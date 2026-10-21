#!/usr/bin/env bash
# Installiert Golf_Auswertung im App-LXC (Debian 13): App + Caddy + Weboberfläche.
#
#   DATABASE_URL='postgresql+psycopg://golf:...@<DB-IP>:5432/golf?sslmode=require' \
#        DOMAIN=golf.fritz.box ./install.sh
#
# Optional:
#   TLS_MODE=internal   (Standard) eigenes Zertifikat, für Heimnetz/fritz.box
#   TLS_MODE=public     Let's-Encrypt-Zertifikat, DOMAIN muss öffentlich erreichbar sein
#   REPO=https://github.com/macke384/Golf_Auswertung.git   BRANCH=main
#
# Wiederholbar: bestehende Konfiguration und Session-Secret bleiben erhalten.
set -euo pipefail

REPO="${REPO:-https://github.com/macke384/Golf_Auswertung.git}"
BRANCH="${BRANCH:-main}"
DOMAIN="${DOMAIN:-}"
TLS_MODE="${TLS_MODE:-internal}"
APP_DIR=/opt/golf-auswertung
CONF_DIR=/etc/golf-auswertung
DATA_DIR=/var/lib/golf-auswertung
ENV_FILE="${CONF_DIR}/golf.env"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

die() { echo "FEHLER: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Bitte als root ausführen."
[[ -n "$DOMAIN" ]] || die "DOMAIN fehlt (z. B. DOMAIN=golf.fritz.box)."
[[ "$TLS_MODE" == internal || "$TLS_MODE" == public ]] || die "TLS_MODE muss internal oder public sein."
if [[ ! -f "$ENV_FILE" && -z "${DATABASE_URL:-}" ]]; then
  die "DATABASE_URL fehlt (wird vom Datenbank-Installationsskript ausgegeben)."
fi

echo "==> Pakete installieren"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv git caddy tesseract-ocr tesseract-ocr-deu \
  postgresql-client ca-certificates curl

echo "==> Dienstbenutzer und Verzeichnisse"
id golf >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin golf
install -d -m 0755 -o root -g root "$APP_DIR"
install -d -m 0750 -o golf -g golf "$DATA_DIR" "$DATA_DIR/storage"
install -d -m 0750 -o root -g golf "$CONF_DIR"

echo "==> Code holen (${REPO} @ ${BRANCH})"
# Der Code gehört root und ist für den Dienst nur lesbar.
if [[ -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" fetch -q origin "$BRANCH"
  git -C "$APP_DIR" checkout -q "$BRANCH"
  git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$APP_DIR"
fi

echo "==> Python-Umgebung"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"

echo "==> Konfiguration (${ENV_FILE})"
if [[ ! -f "$ENV_FILE" ]]; then
  SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
  umask 027
  cat > "$ENV_FILE" <<EOF
# Golf_Auswertung — nur für root und den Dienst lesbar.
GOLF_ENV=production
GOLF_DATABASE_URL=${DATABASE_URL}
GOLF_SESSION_SECRET=${SECRET}
GOLF_STORAGE_DIR=${DATA_DIR}/storage
GOLF_UPLOAD_MAX_BYTES=15728640
EOF
  chown root:golf "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
else
  echo "    vorhanden, unverändert"
fi

echo "==> Datenbankverbindung prüfen"
DB_URL="$(grep '^GOLF_DATABASE_URL=' "$ENV_FILE" | cut -d= -f2-)"
if ! psql "${DB_URL/postgresql+psycopg/postgresql}" -tAc 'SELECT 1' >/dev/null 2>&1; then
  die "Keine Verbindung zur Datenbank. IP, Passwort und pg_hba im DB-LXC prüfen."
fi

echo "==> systemd-Dienste"
install -m 0644 "$SRC_DIR/golf-auswertung.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/golf-sweep.service" /etc/systemd/system/
install -m 0644 "$SRC_DIR/golf-sweep.timer" /etc/systemd/system/
install -m 0755 "$SRC_DIR/update.sh" /usr/local/bin/golf-update
systemctl daemon-reload
systemctl enable golf-auswertung.service golf-sweep.timer
systemctl restart golf-auswertung.service
systemctl start golf-sweep.timer

echo "==> Caddy (${DOMAIN}, TLS ${TLS_MODE})"
TLS_LINE=""
[[ "$TLS_MODE" == internal ]] && TLS_LINE="tls internal"
sed -e "s|__DOMAIN__|${DOMAIN}|" -e "s|__TLS__|${TLS_LINE}|" "$SRC_DIR/Caddyfile" > /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null
systemctl enable caddy
systemctl reload caddy || systemctl restart caddy

echo "==> Start prüfen"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo
    echo "Golf_Auswertung läuft: https://${DOMAIN}"
    echo
    echo "Ersten Admin anlegen:  golf-update --admin admin@example.org"
    [[ "$TLS_MODE" == internal ]] && echo "Hinweis: Browser zeigen beim ersten Aufruf eine Zertifikatswarnung (eigene CA von Caddy)."
    exit 0
  fi
  sleep 1
done
journalctl -u golf-auswertung -n 30 --no-pager
die "Dienst antwortet nicht. Log siehe oben."
