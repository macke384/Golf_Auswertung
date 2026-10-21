"""Verwaltung per Kommandozeile.

  python -m app.cli create-admin admin@example.org   (Passwort wird abgefragt)
  python -m app.cli sweep                            (verwaiste Upload-Dateien löschen)
"""
from __future__ import annotations

import getpass
import sys

from app.config import get_settings
from app.db import get_sessionmaker
from app.imports.service import sweep
from app.models import User
from app.security import hash_password


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[0] == "create-admin":
        pw = getpass.getpass("Passwort (mind. 12 Zeichen): ")
        if pw != getpass.getpass("Wiederholen: "):
            print("Passwörter stimmen nicht überein.", file=sys.stderr)
            return 1
        with get_sessionmaker()() as db:
            db.add(User(email=argv[1].strip().lower(), password_hash=hash_password(pw), role="admin"))
            db.commit()
        print("Admin angelegt.")
        return 0
    if argv[:1] == ["sweep"]:
        with get_sessionmaker()() as db:
            print(f"{sweep(db, get_settings())} Dateien entfernt.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
