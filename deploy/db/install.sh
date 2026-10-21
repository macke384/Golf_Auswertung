#!/usr/bin/env bash
# Installiert PostgreSQL im Datenbank-LXC (Debian 13) für Golf_Auswertung.
#
#   ./install.sh <IP-des-App-LXC>      (als root)
#
# - Datenbank "golf" mit Benutzer "golf" und zufälligem Passwort
# - Zugriff ausschließlich von der angegebenen App-IP (scram-sha-256)
# - Nächtliches Backup per pg_dump (systemd-Timer), 14 Tage Aufbewahrung
#
# Das Skript ist wiederholbar: ein zweiter Lauf ändert das Passwort NICHT,
# außer mit RESET_PASSWORD=1.
set -euo pipefail

APP_IP="${1:-}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/golf}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DB_NAME=golf
DB_USER=golf

die() { echo "FEHLER: $*" >&2; exit 1; }
# Debian-LXC-Vorlagen enthalten kein sudo; runuser gehört zum Grundsystem.
as_pg() { runuser -u postgres -- "$@"; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
[[ -n "$APP_IP" ]] || die "Aufruf: $0 <IP-des-App-LXC>"
python3 -c "import ipaddress,sys; ipaddress.ip_address(sys.argv[1])" "$APP_IP" 2>/dev/null \
  || die "'$APP_IP' ist keine gültige IP-Adresse."

echo "==> Pakete installieren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q postgresql

PG_VERSION="$(find /etc/postgresql -mindepth 1 -maxdepth 1 -printf "%f\n" | sort -V | tail -1)"
PG_CONF_DIR="/etc/postgresql/${PG_VERSION}/main"
echo "    PostgreSQL ${PG_VERSION}"

echo "==> Netzwerkzugriff konfigurieren"
LOCAL_IP="$(hostname -I | awk '{print $1}')"
cat > "${PG_CONF_DIR}/conf.d/golf.conf" <<EOF
# Verwaltet von Golf_Auswertung deploy/db/install.sh
listen_addresses = 'localhost,${LOCAL_IP}'
password_encryption = scram-sha-256
# Debian liefert ein selbstsigniertes Zertifikat (ssl-cert); pg_hba erzwingt TLS.
ssl = on
max_connections = 100
EOF

HBA="${PG_CONF_DIR}/pg_hba.conf"
MARK="# golf-auswertung"
sed -i "/${MARK}\$/d" "$HBA"
echo "hostssl ${DB_NAME}    ${DB_USER}    ${APP_IP}/32    scram-sha-256    ${MARK}" >> "$HBA"

systemctl enable --now postgresql
systemctl restart postgresql

echo "==> Datenbank und Benutzer anlegen"
PW_NEW=""
# Passwort über stdin statt Kommandozeile, damit es nicht in der Prozessliste erscheint.
# token_urlsafe enthält nur [A-Za-z0-9_-], also keine Anführungszeichen.
set_password() {
  local verb="$1"
  PW_NEW="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  as_pg psql -q -v ON_ERROR_STOP=1 <<SQL
\set pw '${PW_NEW}'
${verb} ROLE ${DB_USER} LOGIN PASSWORD :'pw';
SQL
}
if [[ "$(as_pg psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'")" != 1 ]]; then
  set_password CREATE
elif [[ "${RESET_PASSWORD:-0}" == "1" ]]; then
  set_password ALTER
fi
if [[ "$(as_pg psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'")" != 1 ]]; then
  # Immer UTF-8, unabhängig von der Spracheinstellung des Containers
  # (ohne LANG legt Debian den Cluster mit SQL_ASCII an).
  as_pg createdb -O "${DB_USER}" -E UTF8 --locale=C.UTF-8 -T template0 "${DB_NAME}"
fi
ENC="$(as_pg psql -tAc "SELECT pg_encoding_to_char(encoding) FROM pg_database WHERE datname='${DB_NAME}'")"
if [[ "$ENC" != UTF8 ]]; then
  die "Datenbank '${DB_NAME}' hat Kodierung ${ENC} statt UTF8. Löschen mit: runuser -u postgres -- dropdb ${DB_NAME} — dann Skript erneut starten."
fi
# Niemand außer dem Eigentümer darf sich verbinden oder Objekte anlegen.
as_pg psql -q -d "${DB_NAME}" -c "REVOKE ALL ON DATABASE ${DB_NAME} FROM PUBLIC;" \
  -c "REVOKE CREATE ON SCHEMA public FROM PUBLIC;"

echo "==> Backup einrichten (${BACKUP_DIR}, ${RETENTION_DAYS} Tage)"
install -d -m 0750 -o postgres -g postgres "$BACKUP_DIR"
cat > /usr/local/bin/golf-db-backup <<EOF
#!/usr/bin/env bash
# Nächtliches Backup der Golf_Auswertung-Datenbank. Erzeugt von install.sh.
set -euo pipefail
DIR="${BACKUP_DIR}"
STAMP="\$(date +%Y-%m-%d_%H%M)"
TMP="\${DIR}/.golf_\${STAMP}.dump.partial"
OUT="\${DIR}/golf_\${STAMP}.dump"
trap 'rm -f "\$TMP"' EXIT
pg_dump -Fc -d ${DB_NAME} -f "\$TMP"
pg_restore --list "\$TMP" > /dev/null   # Dump lesbar?
mv "\$TMP" "\$OUT"
find "\$DIR" -maxdepth 1 -name 'golf_*.dump' -mtime +${RETENTION_DAYS} -delete
echo "Backup erstellt: \$OUT (\$(du -h "\$OUT" | cut -f1))"
EOF
chmod 0755 /usr/local/bin/golf-db-backup

install -m 0644 "$(dirname "$0")/golf-db-backup.service" /etc/systemd/system/
install -m 0644 "$(dirname "$0")/golf-db-backup.timer" /etc/systemd/system/
sed -i "s|^ReadWritePaths=.*|ReadWritePaths=${BACKUP_DIR}|" /etc/systemd/system/golf-db-backup.service
systemctl daemon-reload
systemctl enable --now golf-db-backup.timer

echo
echo "Fertig. Datenbank-LXC: ${LOCAL_IP}"
if [[ -n "$PW_NEW" ]]; then
  echo
  echo "Datenbank-URL für den App-LXC (nur jetzt sichtbar, sicher notieren):"
  echo "  postgresql+psycopg://${DB_USER}:${PW_NEW}@${LOCAL_IP}:5432/${DB_NAME}?sslmode=require"
else
  echo "Benutzer '${DB_USER}' existierte bereits — Passwort unverändert (RESET_PASSWORD=1 zum Neusetzen)."
fi
echo
echo "Backup testen: systemctl start golf-db-backup && journalctl -u golf-db-backup -n 5"
