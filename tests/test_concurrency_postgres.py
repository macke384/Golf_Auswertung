"""Parallelität gegen echte PostgreSQL.

Hinweis: Diese Tests erzeugen echte Nebenläufigkeit über Threads und getrennte
DB-Verbindungen. Sie zeigen, dass die DB-Constraints greifen. Ein grüner Lauf
ist dennoch kein Beweis für jedes Timing unter Last — dafür braucht es
zusätzlich Lasttests gegen die laufende Instanz hinter dem Reverse Proxy.
"""
import threading
from concurrent.futures import ThreadPoolExecutor

import anyio
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.imports import service as import_service
from app.models import Import, Scorecard, TournamentEntry
from app import services
from tests.factories import login, make_user, setup_tournament

pytestmark = pytest.mark.postgres


def test_parallel_identical_imports_create_one_row(db, migrated_engine, storage_dir):
    data = ("Name;" + ";".join(str(i) for i in range(1, 19)) + "\nA;" + ";".join(["5"] * 18)).encode()
    n = 8
    barrier = threading.Barrier(n)

    async def chunks():
        yield data[:20]
        yield data[20:]

    def one(_):
        with Session(migrated_engine, expire_on_commit=False) as s:
            barrier.wait()
            res = anyio.run(lambda: import_service.intake(
                s, get_settings(), chunks(), filename="a.csv", user_id=None))
            return res.imp.id, res.duplicate

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(one, range(n)))
    ids = {r[0] for r in results}
    assert len(ids) == 1
    assert sum(1 for r in results if not r[1]) == 1  # genau einer hat neu angelegt
    assert db.scalar(select(func.count()).select_from(Import)) == 1
    files = [p for p in storage_dir.rglob("*") if p.is_file()]
    assert len(files) == 1 and ".tmp" not in str(files[0])


def test_parallel_entry_creation_no_duplicates(client, db, migrated_engine):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    # Teilnahme wieder löschen, um sie parallel neu anzulegen
    db.execute(text("DELETE FROM tournament_entries"))
    db.commit()
    tee_id = s["course"]["tee_ids"][0]
    n = 8
    barrier = threading.Barrier(n)

    def one(_):
        with Session(migrated_engine) as sess:
            barrier.wait()
            try:
                services.create_entry(sess, s["tournament_id"], s["player_ids"][0], tee_id, None)
                sess.commit()
                return "ok"
            except services.DomainError as e:
                return e.status

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(one, range(n)))
    assert results.count("ok") == 1 and results.count(409) == n - 1
    assert db.scalar(select(func.count()).select_from(TournamentEntry)) == 1


def test_parallel_first_scorecard_save_one_card(client, db, migrated_engine):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    n = 8
    barrier = threading.Barrier(n)

    def one(i):
        with Session(migrated_engine) as sess:
            barrier.wait()
            services.save_scorecard(sess, eid, [4 + (i % 3)] * 18)
            sess.commit()
            return "ok"

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(one, range(n)))
    assert results == ["ok"] * n  # serialisiert über FOR UPDATE, keine Fehler
    assert db.scalar(select(func.count()).select_from(Scorecard)) == 1
    holes = db.execute(text("SELECT count(*) FROM hole_scores")).scalar()
    assert holes == 18  # keine gemischten/doppelten Lochwerte


def test_parallel_updates_with_same_version_only_one_wins(client, db, migrated_engine):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    v = client.put(f"/entries/{eid}/scorecard", json={"strokes": [5] * 18}).json()["version"]
    n = 6
    barrier = threading.Barrier(n)

    def one(i):
        with Session(migrated_engine) as sess:
            barrier.wait()
            try:
                services.save_scorecard(sess, eid, [4] * 18, expected_version=v)
                sess.commit()
                return "ok"
            except services.DomainError as e:
                return e.status

    with ThreadPoolExecutor(n) as ex:
        results = list(ex.map(one, range(n)))
    assert results.count("ok") == 1 and results.count(409) == n - 1


def test_db_constraint_blocks_impossible_strokes_even_without_app_checks(db, client):
    """Die DB ist die letzte Verteidigungslinie, falls Code-Prüfungen umgangen werden."""
    from sqlalchemy.exc import IntegrityError

    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    client.put(f"/entries/{eid}/scorecard", json={"strokes": [5] * 18})
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE hole_scores SET strokes = 0 WHERE hole_number = 1"))
        db.commit()
    db.rollback()
