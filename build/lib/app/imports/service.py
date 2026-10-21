"""Import-Annahme: Stream → Temp → Parsen → Ablage → DB-Eintrag.

Garantien:
- Fehler vor der Ablage (Limit, Abbruch, Parserfehler) → Temp-Datei weg.
- Identische Datei zweimal (auch parallel) → genau ein DB-Eintrag; der zweite
  Upload bekommt den bestehenden Import zurück (duplicate=True).
- DB-Fehler nach der Ablage → Datei bleibt ohne Referenz liegen und wird von
  sweep_orphans() nach der Karenzzeit gelöscht. Kein dauerhafter Leak.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import anyio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.imports import upload
from app.imports.parsers import PARSERS, ParseError
from app.models import Import

log = logging.getLogger(__name__)


@dataclass
class IntakeResult:
    imp: Import
    duplicate: bool


def _sanitize_name(name: str | None) -> str:
    if not name:
        return "upload"
    # Nur der Dateiname, keine Pfade oder Steuerzeichen.
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    clean = "".join(ch for ch in base if ch.isprintable())
    return clean[:255] or "upload"


async def intake(
    db: Session,
    settings: Settings,
    chunks: AsyncIterator[bytes],
    *,
    filename: str | None,
    user_id: int | None,
    expected_sha256: str | None = None,
    declared_length: int | None = None,
) -> IntakeResult:
    staged = await upload.receive_stream(chunks, settings, expected_sha256, declared_length)
    try:
        existing = db.scalar(select(Import).where(Import.sha256 == staged.sha256))
        if existing is not None:
            return IntakeResult(existing, True)
        try:
            parsed = await anyio.to_thread.run_sync(PARSERS[staged.kind], staged.tmp_path)
            status, error = "needs_review", None
        except ParseError as e:
            parsed, status, error = None, "failed", str(e)
        upload.commit(staged, settings)
    finally:
        upload.discard(staged)  # no-op, wenn bereits verschoben

    imp = Import(
        sha256=staged.sha256,
        kind=staged.kind,
        size_bytes=staged.size,
        original_name=_sanitize_name(filename),
        status=status,
        parsed=parsed,
        error=error,
        uploaded_by=user_id,
    )
    db.add(imp)
    try:
        db.commit()
    except IntegrityError:
        # Paralleler Upload derselben Datei hat gewonnen. Die abgelegte Datei
        # hat denselben Inhalt und gehört jetzt zu dessen Eintrag.
        db.rollback()
        winner = db.scalar(select(Import).where(Import.sha256 == staged.sha256))
        if winner is None:  # theoretisch: Gewinner wurde inzwischen gelöscht
            raise
        return IntakeResult(winner, True)
    return IntakeResult(imp, False)


def sweep(db: Session, settings: Settings) -> int:
    referenced = set(db.scalars(select(Import.sha256)))
    removed = upload.sweep_orphans(settings, referenced)
    if removed:
        log.info("Verwaiste Upload-Dateien entfernt: %d", removed)
    return removed
