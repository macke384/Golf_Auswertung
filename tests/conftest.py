"""Test-Setup.

Reine Unit-Tests (WHS, Parser, Upload) laufen ohne Datenbank.
Tests mit @pytest.mark.postgres brauchen eine echte, leere PostgreSQL-DB:

  export TEST_DATABASE_URL=postgresql+psycopg://golf:golf@127.0.0.1:5433/golf_test

Ohne diese Variable werden sie übersprungen — bewusst kein SQLite-Ersatz,
weil Constraints, Sperren und Parallelität sich dort anders verhalten.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
_STORAGE = Path(tempfile.mkdtemp(prefix="golf-test-storage-"))

os.environ.setdefault("GOLF_ENV", "test")
os.environ["GOLF_COOKIE_SECURE"] = "false"  # TestClient spricht http://
os.environ["GOLF_STORAGE_DIR"] = str(_STORAGE)
if TEST_DB:
    os.environ["GOLF_DATABASE_URL"] = TEST_DB


def pytest_collection_modifyitems(config, items):
    if TEST_DB:
        return
    skip = pytest.mark.skip(reason="TEST_DATABASE_URL nicht gesetzt (echte PostgreSQL nötig)")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def migrated_engine():
    """Migriert eine leere DB von Grund auf (testet zugleich die Migrationen)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text

    eng = create_engine(TEST_DB)
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    cfg.attributes["database_url"] = TEST_DB
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")  # Downgrade muss sauber durchlaufen
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


TABLES = (
    "hole_scores, scorecards, imports, tournament_entries, tournaments, tee_holes, tees, "
    "courses, team_members, teams, players, auth_sessions, users, catalog_cache, api_usage"
)


@pytest.fixture
def db(migrated_engine):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with migrated_engine.begin() as c:
        c.execute(text(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE"))
    for p in _STORAGE.rglob("*"):
        if p.is_file():
            p.unlink()
    with Session(migrated_engine, expire_on_commit=False) as s:
        yield s


@pytest.fixture
def client(db):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(), raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def storage_dir() -> Path:
    return _STORAGE
