#!/usr/bin/env bash
# Golf_Auswertung im App-LXC aktualisieren bzw. verwalten.
#
#   golf-update                       neuesten Stand holen, installieren, neu starten
#   golf-update --admin <E-Mail>      Admin-Konto anlegen (Passwort wird abgefragt)
#   golf-update --cli <Befehl...>     beliebigen app.cli-Befehl ausführen
#
# Vor einem Update mit Datenbankänderungen im DB-LXC ein Backup ziehen:
#   systemctl start golf-db-backup
set -euo pipefail

APP_DIR=/opt/golf-auswertung
ENV_FILE=/etc/golf-auswertung/golf.env
BRANCH="${BRANCH:-main}"

[[ $EUID -eq 0 ]] || { echo "Bitte als root ausführen." >&2; exit 1; }

run_cli() {
  # Konfiguration laden und als Dienstbenutzer ausführen.
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  cd "$APP_DIR"
  exec runuser -u golf -- "$APP_DIR/.venv/bin/python" -m app.cli "$@"
}

case "${1:-}" in
  --admin)
    [[ -n "${2:-}" ]] || { echo "Aufruf: golf-update --admin <E-Mail>" >&2; exit 2; }
    run_cli create-admin "$2"
    ;;
  --cli)
    shift
    run_cli "$@"
    ;;
  "")
    OLD="$(git -C "$APP_DIR" rev-parse --short HEAD)"
    git -C "$APP_DIR" fetch -q origin "$BRANCH"
    NEW="$(git -C "$APP_DIR" rev-parse --short "origin/$BRANCH")"
    if [[ "$OLD" == "$NEW" ]]; then
      echo "Bereits aktuell ($OLD)."
      exit 0
    fi
    if git -C "$APP_DIR" diff --name-only "$OLD" "origin/$BRANCH" | grep -q '^migrations/'; then
      echo "Achtung: Das Update enthält Datenbank-Migrationen."
      echo "Backup im DB-LXC gemacht? (systemctl start golf-db-backup)  [j/N]"
      read -r answer
      [[ "$answer" =~ ^[jJyY]$ ]] || { echo "Abgebrochen."; exit 1; }
    fi
    git -C "$APP_DIR" reset -q --hard "origin/$BRANCH"
    "$APP_DIR/.venv/bin/pip" install -q "$APP_DIR"
    # Unit-Dateien könnten sich geändert haben.
    install -m 0644 "$APP_DIR"/deploy/app/golf-*.service "$APP_DIR"/deploy/app/golf-*.timer \
      /etc/systemd/system/
    install -m 0755 "$APP_DIR/deploy/app/update.sh" /usr/local/bin/golf-update
    systemctl daemon-reload
    systemctl restart golf-auswertung   # führt Migrationen aus (ExecStartPre)
    for _ in $(seq 1 30); do
      if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
        echo "Aktualisiert: $OLD → $NEW"
        exit 0
      fi
      sleep 1
    done
    journalctl -u golf-auswertung -n 30 --no-pager
    echo "Dienst antwortet nach dem Update nicht." >&2
    echo "Zurück zur alten Version:" >&2
    echo "  git -C $APP_DIR reset --hard $OLD && $APP_DIR/.venv/bin/pip install -q $APP_DIR && systemctl restart golf-auswertung" >&2
    echo "Liefen bereits Migrationen, vorher das Backup im DB-LXC zurückspielen." >&2
    exit 1
    ;;
  *)
    sed -n '2,10p' "$0"
    exit 2
    ;;
esac
