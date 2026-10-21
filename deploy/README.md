# Betrieb auf Proxmox: zwei LXC ohne Docker

```
                 Heimnetz / Internet
                         │ 443
             ┌───────────▼────────────┐
             │ golf-app (LXC)         │
             │  Caddy  → HTTPS        │
             │  uvicorn 127.0.0.1:8000│
             │  Upload-Ablage         │
             └───────────┬────────────┘
                         │ 5432, TLS, nur von dieser IP
             ┌───────────▼────────────┐
             │ golf-db (LXC)          │
             │  PostgreSQL            │
             │  nächtliches pg_dump   │
             └────────────────────────┘
```

Warum so: Weboberfläche und API laufen unter einer Adresse (einfache, sichere
Cookies, kein CORS). Die Datenbank liegt getrennt, damit sie eigene Backups und
Updates bekommt und App-Updates die Daten nicht gefährden.

## 1. Container anlegen

Zwei **unprivilegierte** LXC aus der Vorlage **Debian 13**:

| | golf-db | golf-app |
|---|---|---|
| CPU / RAM | 1 Kern / 1 GB | 2 Kerne / 2 GB (OCR braucht etwas Rechenzeit) |
| Disk | 8 GB + Backups | 8 GB + Uploads |
| Netzwerk | feste IP | feste IP |

Feste IPs am besten in der FRITZ!Box reservieren oder statisch im LXC setzen.
Optional: Proxmox-Firewall für golf-db so einstellen, dass Port 5432 nur von golf-app erreichbar ist
(pg_hba lässt ohnehin nur diese IP zu — die Firewall ist die zweite Linie).

## 2. Datenbank-LXC

```bash
apt-get update && apt-get install -y git
git clone https://github.com/macke384/Golf_Auswertung.git /root/golf
/root/golf/deploy/db/install.sh <IP-von-golf-app>
```

Das Skript gibt am Ende die **Datenbank-URL** aus. Sie wird nur einmal angezeigt — kopieren.

## 3. App-LXC

```bash
apt-get update && apt-get install -y git
git clone https://github.com/macke384/Golf_Auswertung.git /root/golf
DATABASE_URL='postgresql+psycopg://golf:…@<IP-von-golf-db>:5432/golf?sslmode=require' \
DOMAIN=golf.fritz.box \
/root/golf/deploy/app/install.sh

golf-update --admin deine@adresse.de      # ersten Admin anlegen
```

- `TLS_MODE=internal` (Standard): Caddy erzeugt ein eigenes Zertifikat. Der Browser warnt beim
  ersten Aufruf; für das Heimnetz ist das in Ordnung. Damit `golf.fritz.box` aufgelöst wird,
  den Hostnamen des LXC auf `golf` setzen (die FRITZ!Box vergibt dann `golf.fritz.box`).
- `TLS_MODE=public`: echtes Let's-Encrypt-Zertifikat. Voraussetzung: öffentliche Domain,
  die auf deinen Anschluss zeigt, und Portfreigabe 80/443 auf golf-app.

## Alltag

| Aufgabe | Befehl | Wo |
|---|---|---|
| Update | `golf-update` | golf-app |
| Admin anlegen | `golf-update --admin <E-Mail>` | golf-app |
| Status / Log | `systemctl status golf-auswertung` · `journalctl -u golf-auswertung -f` | golf-app |
| Backup sofort | `systemctl start golf-db-backup` | golf-db |
| Backups ansehen | `ls -lh /var/backups/golf` | golf-db |
| Backup zurückspielen | siehe unten | golf-db |

`golf-update` fragt nach, wenn ein Update Datenbank-Migrationen enthält — dann vorher ein Backup ziehen.

### Backup zurückspielen

```bash
# golf-app:  systemctl stop golf-auswertung
# golf-db:
sudo -u postgres pg_restore --clean --if-exists -d golf /var/backups/golf/golf_<DATUM>.dump
# golf-app:  systemctl start golf-auswertung
```

### Backups auf die Synology

Ein unprivilegierter LXC kann keine Netzlaufwerke selbst einbinden. Bewährter Weg:

1. NFS- oder SMB-Freigabe der Synology auf dem **Proxmox-Host** einbinden, z. B. `/mnt/nas-backup`.
2. Als Mountpoint in golf-db durchreichen:
   `pct set <ID> -mp0 /mnt/nas-backup/golf,mp=/var/backups/golf`
3. Rechte: im unprivilegierten LXC ist `postgres` auf dem Host UID `100000 + <UID im LXC>`
   (`id -u postgres` im LXC). Das Zielverzeichnis auf dem Host dieser UID geben.

Alternativ die Proxmox-eigene Sicherung (vzdump) beider LXC auf die Synology einrichten —
zusätzlich zu pg_dump, nicht stattdessen: pg_dump ist garantiert konsistent und lässt sich
einzeln zurückspielen.

## Was die Skripte absichern

- Die App lauscht nur auf 127.0.0.1; nach außen spricht ausschließlich Caddy.
- Der Dienst läuft als eigener Benutzer `golf` mit systemd-Härtung und darf nur in
  `/var/lib/golf-auswertung` schreiben. Code und Konfiguration gehören root.
- `/etc/golf-auswertung/golf.env` (DB-Passwort, Session-Secret) ist nur für root und den Dienst lesbar.
- Datenbankzugriff nur vom App-LXC, nur verschlüsselt (TLS), nur mit Passwort (SCRAM).
- Migrationen laufen vor jedem Start; schlägt eine fehl, startet die alte Version nicht halb.

## Geprüft und nicht geprüft

Geprüft: Skripte mit shellcheck, systemd-Units mit `systemd-analyze verify`, Passwortvergabe
und Backup-Skript gegen eine echte PostgreSQL, Installation des Pakets in einer frischen Python-Umgebung.

**Nicht geprüft:** ein vollständiger Durchlauf auf echten Debian-13-LXC unter Proxmox.
Beim ersten Einrichten bitte die Ausgaben im Blick behalten und Fehler melden.
