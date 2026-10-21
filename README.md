# Golf_Auswertung

Auswertung von Golf-Scorekarten für Clubmannschaften: Brutto/Netto, Stableford,
Einzel- und Mannschaftswertung. Selbst gehostet, vollständig unter eigener Kontrolle.

**Stand:** Backend (API, Datenbank, WHS-Berechnung, Import-Pipeline) und Weboberfläche für den
Rechner. Die Erfassung auf dem Handy direkt auf dem Platz folgt später.

## Weboberfläche

Unter der Adresse des Servers (z. B. `https://192.168.178.24`) nach der Anmeldung:

| Bereich | Funktion |
|---|---|
| Plätze | Platz mit Abschlägen anlegen, umbenennen, löschen; Abschläge hinzufügen (Par und optional Loch-HCP von bestehendem Abschlag übernehmen), bearbeiten, löschen. Abschläge mit Meldungen: Änderung nur nach Rückfrage, Spielvorgaben werden neu berechnet; bestätigte Scorekarten sperren Änderungen |
| Einzelrunden | Private Runden/Training eines Spielers (18 Loch, 9 Loch vorne/hinten, Notiz), Spielvorgabe mit 100 % Handicap-Anteil; gleiche Scorekarte und Auswertung wie im Turnier |
| Spieler | Anlegen, bearbeiten, suchen; HCP-Index in deutscher Schreibweise (`18,4`, `+1,2`). Spielerseite mit persönlicher Auswertung: alle Runden (Turniere und Einzelrunden), bestes/Ø Brutto, beste/Ø Stableford netto, getrennt nach 18 und 9 Loch |
| Mannschaften | Anlegen, umbenennen, löschen (nicht, solange in Turniermeldungen eingetragen), Spieler per Häkchen zuordnen |
| Turniere | Anlegen, bearbeiten, löschen (18 Loch, 9 Loch vorne/hinten, Handicap-Anteil); Spieler einzeln oder als ganze Mannschaft melden, Mannschaft je Meldung ändern. Handicap-Anteil-Änderung berechnet Spielvorgaben neu (nach Rückfrage); Platz/Runde nur ohne Meldungen; bestätigte Scorekarten sperren ergebnisrelevante Änderungen und das Löschen |
| Scorekarte | Loch-Raster mit Live-Auswertung (Vorgabeschläge, Punkte je Loch, Brutto/Netto, Stableford); Cursor springt automatisch weiter, `-` = gestrichen; Status Entwurf/Eingereicht/Bestätigt |
| Rangliste | Stableford/Zählspiel, brutto/netto, Mannschaftswertung mit „beste N“ |
| Importe | CSV/PDF/Foto hochladen, Original neben den erkannten Werten prüfen, automatische Namenszuordnung, korrigieren, übernehmen |
| Nutzer | (nur Admin) Konten anlegen, Rollen, sperren, Passwort setzen |

Die Oberfläche ist reines HTML/CSS/JavaScript ohne Build-Schritt (`app/web/`) und wird von der App
selbst ausgeliefert. Alle Berechnungen passieren auf dem Server; die Live-Anzeige nutzt einen
Vorschau-Endpunkt, der nichts speichert. Eine strikte Content-Security-Policy erlaubt nur eigene
Skripte; Daten werden ausschließlich als Text ins DOM eingesetzt.

## Aufbau

```
app/
  config.py            Konfiguration, Prüfung der Secrets beim Start
  models.py            Datenmodell (PostgreSQL), Eindeutigkeit per DB-Constraint
  schemas.py           Eingabevalidierung (Typen, Bereiche, unbekannte Felder verboten)
  scoring/whs.py       Course/Playing HCP, Schlagverteilung, Stableford  (reine Funktionen)
  scoring/plausibility.py  Fehler vs. Auffälligkeiten bei Platz- und Scoredaten
  scoring/leaderboard.py   Ranglisten mit Stechen, Mannschaftswertung
  imports/upload.py    Streaming-Upload, SHA-256, Magic Bytes, Temp-Cleanup, Sweep
  imports/parsers.py   CSV, PDF, Foto (OCR) → einheitliches Kartenformat
  imports/service.py   Annahme: Duplikaterkennung, Ablage, DB-Eintrag
  services.py          Fachlogik mit DB: Teilnahmen, Scorekarten, Importe, Ranglisten
  security.py          Argon2-Passwörter, serverseitige Sessions, Rollen
  main.py              HTTP-API und kontrollierte Fehlerbehandlung
migrations/            Alembic
tests/                 Unit-Tests + Integrationstests gegen echte PostgreSQL
```

## Fachliche Regeln

| Thema | Umsetzung |
|---|---|
| Course HCP 18 Loch | HI × SR/113 + (CR − Par), gerundet (x.5 aufwärts) |
| Course HCP 9 Loch | (HI/2) × SR/113 + (CR − Par) |
| 9 Loch auf 18-Loch-Abschlag | **Vereinfachung:** CR/2, gleiches SR. Separate 9-Loch-Ratings sind ein offener Punkt |
| Playing HCP | Course HCP × Allowance des Turniers |
| Vorgabeschläge | Nach Loch-HCP verteilt; bei 9 Löchern nach Rang innerhalb der gespielten Löcher. Plus-HCP gibt an den leichtesten Löchern zurück |
| Stableford | max(0, 2 + Par + Vorgabe − Schläge); gestrichenes Loch = 0 Punkte |
| Eingefrorene Werte | HI und Playing HCP werden bei der Turniermeldung gespeichert |
| Stechen | Letzte 9/6/3/1 (18 Loch) bzw. 6/3/1 (9 Loch) in Spielreihenfolge |
| Mannschaft | Summe der besten N Netto-Stableford-Ergebnisse (N pro Abfrage wählbar) |
| Einzelrunde | Technisch ein Turnier vom Typ `single` mit genau einer Meldung und 100 % Allowance – gleiche Berechnung, gleiche Absicherung |

## Import

`POST /imports` mit dem Dateiinhalt als Body (kein Multipart, damit echt gestreamt wird).

- Typ wird an den ersten Bytes erkannt — Dateiname und Content-Type des Clients werden ignoriert.
- Optional `X-Content-SHA256`: der Server lehnt ab, wenn der Inhalt unterwegs beschädigt wurde.
- Gleiche Datei erneut (auch parallel) → `200` mit dem bestehenden Import, kein Duplikat.
- Ergebnis landet immer als `needs_review`. Erst `POST /imports/{id}/apply` mit einer
  Zuordnung Karte → Teilnahme schreibt Scorekarten (alles oder nichts).
- Bestätigte Scorekarten werden von Importen nie überschrieben.

**CSV-Format:** Kopfzeile mit `Name` (optional `DGV`) und Lochspalten `1`..`18` oder `L1`/`Loch 1`,
Trennzeichen `;` `,` oder Tab, UTF-8 oder Windows-1252. `-`, `x` oder leer = gestrichen.

**PDF:** Tabellen werden wie CSV gelesen, sonst Textzeilen „Name + Zahlen“ mit Prüfung über Out/In/Total.
Die tatsächlichen Exportformate (z. B. aus der Clubsoftware) sind **noch nicht getestet** —
dafür werden echte Beispieldateien gebraucht.

**Foto:** OCR mit tesseract. Erkennt nur Zeilen, deren Summe aufgeht; alles ist `confidence: low`
und muss geprüft werden. Bei echten Handyfotos (schräg, handschriftlich) ist die Trefferquote
erfahrungsgemäß gering — das ist eine Eingabehilfe, kein automatischer Import.

## Sicherheit — was welche Maßnahme löst

| Maßnahme | Löst | Löst nicht |
|---|---|---|
| Login, Rollen (viewer/editor/admin) | Wer darf lesen/schreiben | Geschönte Scores eines berechtigten Nutzers |
| Session-Token nur als HMAC in der DB | DB-Leak ermöglicht keinen Login | Gestohlenes Cookie |
| SameSite=Strict + Sec-Fetch-Site-Prüfung | CSRF | XSS in einer künftigen Oberfläche |
| SHA-256 beim Upload | Beschädigung auf dem Transportweg, Duplikate | Manipulation durch den Absender |
| Plausibilitätsprüfung | Tippfehler, OCR-Fehler, kaputte Importe | Bewusst plausibel geschönte Scores |
| Status `confirmed` (Gegenzeichnung) | Nachträgliches Überschreiben durch Importe | — fachlich die eigentliche „Anti-Cheat“-Stelle |
| DB-Constraints | Duplikate und unmögliche Werte auch bei Bugs/Parallelität | — |

Weitere Punkte: Argon2id, Kontosperre nach 5 Fehlversuchen, keine offene Registrierung
(Admin legt Konten an), Start verweigert in Production bei Beispiel-Secrets,
API-Doku in Production abgeschaltet, Fehlermeldungen ohne interne Details.

## Tests

```bash
pip install -e ".[dev]"
pytest                                   # Unit-Tests, ohne Datenbank
export TEST_DATABASE_URL=postgresql+psycopg://golf:golf@127.0.0.1:5432/golf_test
pytest                                   # zusätzlich Integration/Concurrency gegen PostgreSQL
```

Die Postgres-Tests migrieren eine leere DB hoch, wieder herunter und erneut hoch,
und prüfen u. a. parallele identische Uploads, parallele Meldungen und Scorekarten-Speicherungen.

**Was grüne Tests NICHT beweisen** (muss separat geprüft werden):
- Verhalten hinter Caddy (HTTPS, Body-Limit, Forwarded-Header) und die Installationsskripte auf echten LXC — nur im echten Deployment prüfbar.
- Lastverhalten mit vielen gleichzeitigen Nutzern — braucht einen Lasttest gegen die laufende Instanz.
- OCR-Qualität bei echten Scorekartenfotos und Parsing echter PDF-Exporte — braucht Beispieldateien.
- Datei-Sweep: Zwischen Prüfung und Löschen gibt es ein sehr kleines Zeitfenster, in dem ein gleichzeitig
  neu hochgeladener identischer Inhalt getroffen werden könnte. Der Sweep läuft daher nur beim Start
  bzw. per `python -m app.cli sweep` zu ruhigen Zeiten.

## Betrieb

Empfohlen: **zwei LXC auf Proxmox ohne Docker** (App + Datenbank getrennt).
Anleitung und Skripte: [deploy/README.md](deploy/README.md).

Alternativ für andere Umgebungen: `docker compose up -d` (siehe `docker-compose.yml`, `.env.example`).

## Bekannte Grenzen / nächste Schritte

- Scorekarten-Erfassung auf dem Handy
- Separate 9-Loch-Course/Slope-Ratings
- Löschen von Spielern
- Automatisierte Browser-Tests der Oberfläche in der CI (bisher manuell mit Playwright geprüft)
- Import-Verarbeitung (OCR) als Hintergrundjob statt im Request
- DGV-Anbindung als spätere Import-Schnittstelle


## Lizenz

MIT — siehe [LICENSE](LICENSE).
