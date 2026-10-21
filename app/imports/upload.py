"""Streaming-Upload ohne Storage-Leaks.

Ablauf:
  1. Der Request-Body wird in 64-KB-Blöcken in eine Temp-Datei geschrieben
     (nie komplett im RAM), dabei SHA-256 berechnet und das Größenlimit
     geprüft. Bei jedem Fehler — Limit, Abbruch, Disconnect — wird die
     Temp-Datei sofort gelöscht.
  2. Der Dateityp wird aus den ersten Bytes bestimmt (Magic Bytes), nicht
     aus Content-Type oder Dateiname des Clients.
  3. Erst nach erfolgreicher Prüfung wird die Datei atomar (os.replace) unter
     ihrem Hash abgelegt. Gleicher Inhalt → gleicher Pfad, daher sind
     parallele identische Uploads unkritisch.
  4. Bleibt nach einem Absturz trotzdem etwas liegen (Temp-Datei oder Datei
     ohne DB-Eintrag), räumt sweep_orphans() es nach einer Karenzzeit weg.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path

import anyio

from app.config import Settings


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class StagedUpload:
    tmp_path: Path
    sha256: str
    size: int
    kind: str  # csv | pdf | photo


def _too_big(settings: Settings) -> str:
    return f"Datei zu groß (max. {settings.upload_max_bytes / (1024 * 1024):.1f} MB)."


def detect_kind(head: bytes) -> str:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "photo"
    if b"\x00" in head:
        raise UploadError(415, "Dateityp wird nicht unterstützt (erlaubt: CSV, PDF, JPEG, PNG).")
    try:
        head.decode("utf-8")
        return "csv"
    except UnicodeDecodeError as e:
        # Am Blockende kann ein UTF-8-Mehrbytezeichen abgeschnitten sein.
        if e.reason == "unexpected end of data":
            return "csv"
    try:
        head.decode("cp1252")  # Excel-Exporte unter Windows
        return "csv"
    except UnicodeDecodeError:
        pass
    raise UploadError(415, "Dateityp wird nicht unterstützt (erlaubt: CSV, PDF, JPEG, PNG).")


def _storage_path(settings: Settings, sha256: str) -> Path:
    return settings.storage_dir / sha256[:2] / sha256


async def receive_stream(
    chunks: AsyncIterator[bytes],
    settings: Settings,
    expected_sha256: str | None = None,
    declared_length: int | None = None,
) -> StagedUpload:
    if declared_length is not None and declared_length > settings.upload_max_bytes:
        raise UploadError(413, _too_big(settings))
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=settings.tmp_dir, prefix="up-")
    tmp = Path(name)
    h = hashlib.sha256()
    size = 0
    head = b""
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > settings.upload_max_bytes:
                    raise UploadError(
                        413, _too_big(settings)
                    )
                if len(head) < 4096:
                    head += chunk[: 4096 - len(head)]
                h.update(chunk)
                await anyio.to_thread.run_sync(f.write, chunk)
            await anyio.to_thread.run_sync(f.flush)
            await anyio.to_thread.run_sync(os.fsync, f.fileno())
        if size == 0:
            raise UploadError(400, "Leere Datei.")
        if declared_length is not None and size != declared_length:
            raise UploadError(400, "Upload unvollständig.")
        digest = h.hexdigest()
        if expected_sha256 is not None and expected_sha256.lower() != digest:
            raise UploadError(400, "Prüfsumme stimmt nicht — Datei beim Übertragen beschädigt.")
        return StagedUpload(tmp, digest, size, detect_kind(head))
    except BaseException:
        # Auch bei Client-Abbruch (CancelledError) keine Temp-Datei zurücklassen.
        tmp.unlink(missing_ok=True)
        raise


def commit(staged: StagedUpload, settings: Settings) -> Path:
    final = _storage_path(settings, staged.sha256)
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged.tmp_path, final)
    return final


def discard(staged: StagedUpload) -> None:
    staged.tmp_path.unlink(missing_ok=True)


def stored_file(settings: Settings, sha256: str) -> Path:
    return _storage_path(settings, sha256)


def sweep_orphans(settings: Settings, referenced: Iterable[str], now: float | None = None) -> int:
    """Löscht verwaiste Temp-Dateien und gespeicherte Dateien ohne DB-Eintrag.

    Nur Dateien älter als orphan_grace_seconds, damit laufende Uploads nicht
    getroffen werden. Gibt die Anzahl gelöschter Dateien zurück.
    """
    now = time.time() if now is None else now
    cutoff = now - settings.orphan_grace_seconds
    keep = set(referenced)
    removed = 0
    root = settings.storage_dir
    if not root.exists():
        return 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            if p.stat().st_mtime > cutoff:
                continue
            in_tmp = p.parent == settings.tmp_dir
            if in_tmp or p.name not in keep:
                p.unlink()
                removed += 1
        except FileNotFoundError:
            continue
    return removed
