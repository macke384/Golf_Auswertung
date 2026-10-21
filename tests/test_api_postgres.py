"""End-to-end über die HTTP-API gegen echte PostgreSQL."""
import hashlib

import pytest

from tests.factories import course_payload, login, make_user, setup_tournament

pytestmark = pytest.mark.postgres


# --- Auth --------------------------------------------------------------------


def test_requires_login(client):
    assert client.get("/players").status_code == 401


def test_login_logout_and_roles(client, db):
    make_user(db, "viewer@example.org", "viewer")
    login(client, "viewer@example.org")
    assert client.get("/players").status_code == 200
    r = client.post("/players", json={"first_name": "A", "last_name": "B", "gender": "m"})
    assert r.status_code == 403
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/players").status_code == 401


def test_wrong_password_same_message_and_lockout(client, db):
    make_user(db)
    for _ in range(5):
        r = client.post("/auth/login", json={"email": "editor@example.org", "password": "falsch"})
        assert r.status_code == 401 and r.json()["detail"] == "Anmeldung fehlgeschlagen."
    unknown = client.post("/auth/login", json={"email": "nobody@example.org", "password": "x"})
    assert unknown.json()["detail"] == "Anmeldung fehlgeschlagen."
    # Konto ist jetzt gesperrt — auch das richtige Passwort hilft nicht
    r = client.post("/auth/login",
                    json={"email": "editor@example.org", "password": "sehr-geheimes-passwort"})
    assert r.status_code == 401


def test_session_token_not_stored_in_clear(client, db):
    from sqlalchemy import text

    make_user(db)
    login(client)
    token = client.cookies.get("golf_session")
    stored = db.execute(text("SELECT token_hash FROM auth_sessions")).scalar()
    assert token and stored and token != stored


def test_cross_site_write_rejected(client, db):
    make_user(db)
    login(client)
    r = client.post("/players", json={"first_name": "A", "last_name": "B", "gender": "m"},
                    headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403


# --- Validierung / Fehler ------------------------------------------------------


def test_invalid_json_and_unknown_fields(client, db):
    make_user(db)
    login(client)
    r = client.post("/players", content=b"{kaputt", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    r = client.post("/players", json={"first_name": "A", "last_name": "B", "gender": "m",
                                      "is_admin": True})
    assert r.status_code == 422
    r = client.post("/players", json={"first_name": "A", "last_name": "B", "gender": "m",
                                      "handicap_index": "60"})
    assert r.status_code == 422


def test_course_with_inconsistent_par_rejected(client, db):
    make_user(db)
    login(client)
    payload = course_payload()
    payload["tees"][0]["par"] = 70
    r = client.post("/courses", json=payload)
    assert r.status_code == 422 and any("Summe" in e for e in r.json()["errors"])
    # Nichts halb angelegt
    from sqlalchemy import text
    assert db.execute(text("SELECT count(*) FROM courses")).scalar() == 0


def test_duplicate_course_name_conflict(client, db):
    make_user(db)
    login(client)
    assert client.post("/courses", json=course_payload()).status_code == 201
    assert client.post("/courses", json=course_payload()).status_code == 409


# --- Turnier & Scorekarte --------------------------------------------------------


def test_entry_freezes_playing_handicap(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    e = s["entries"][0]
    assert e["playing_handicap"] == 20 and e["handicap_index"] == 18.4
    dup = client.post(f"/tournaments/{s['tournament_id']}/entries",
                      json={"player_id": s["player_ids"][0], "tee_id": s["course"]["tee_ids"][0]})
    assert dup.status_code == 409


def test_scorecard_roundtrip_and_leaderboard(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    from tests.factories import PARS_18

    bogey = [p + 1 for p in PARS_18]
    better = list(bogey)
    better[0] -= 1
    e1, e2 = s["entries"]
    r1 = client.put(f"/entries/{e1['id']}/scorecard", json={"strokes": bogey})
    assert r1.status_code == 200, r1.text
    card = r1.json()
    assert card["gross_strokes"] == 90 and card["net_strokes"] == 70
    assert card["net_stableford"] == 38  # 18×2 + 2 Löcher mit 2 Vorgabeschlägen
    client.put(f"/entries/{e2['id']}/scorecard", json={"strokes": better})
    lb = client.get(f"/tournaments/{s['tournament_id']}/leaderboard").json()
    assert [p["entry_id"] for p in lb["players"]] == [e2["id"], e1["id"]]
    assert lb["teams"][0]["points"] == 38 + 39


def test_scorecard_wrong_length_and_impossible_values(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    assert client.put(f"/entries/{eid}/scorecard", json={"strokes": [4] * 9}).status_code == 422
    assert client.put(f"/entries/{eid}/scorecard", json={"strokes": [0] * 18}).status_code == 422
    assert client.put(f"/entries/{eid}/scorecard", json={"strokes": ["4"] * 18}).status_code == 422


def test_scorecard_optimistic_locking(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    v1 = client.put(f"/entries/{eid}/scorecard", json={"strokes": [5] * 18}).json()["version"]
    ok = client.put(f"/entries/{eid}/scorecard",
                    json={"strokes": [4] * 18, "expected_version": v1})
    assert ok.status_code == 200 and ok.json()["version"] == v1 + 1
    stale = client.put(f"/entries/{eid}/scorecard",
                       json={"strokes": [6] * 18, "expected_version": v1})
    assert stale.status_code == 409


def test_back_nine_tournament(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, holes_count=9, start_hole=10, players=1)
    e = s["entries"][0]
    # 18.4/2 × 128/113 + (35.6 − 36) = 10.02 → 10
    assert e["playing_handicap"] == 10
    card = client.put(f"/entries/{e['id']}/scorecard", json={"strokes": [5] * 9}).json()
    assert [h["number"] for h in card["holes"]] == list(range(10, 19))


# --- Importe ---------------------------------------------------------------------


def _csv(rows: list[tuple[str, list]]) -> bytes:
    lines = ["Name;" + ";".join(str(i) for i in range(1, 19))]
    lines += [f"{n};" + ";".join("-" if v is None else str(v) for v in vals) for n, vals in rows]
    return ("\n".join(lines) + "\n").encode()


def test_import_csv_review_and_apply(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    data = _csv([("Spieler0 Test", [5] * 18), ("Spieler1 Test", [4] * 17 + [None])])
    r = client.post("/imports", content=data, headers={
        "X-Filename": "../../etc/ergebnis.csv",
        "X-Content-SHA256": hashlib.sha256(data).hexdigest(),
    })
    assert r.status_code == 201, r.text
    imp = r.json()
    assert imp["kind"] == "csv" and imp["status"] == "needs_review"
    assert imp["original_name"] == "ergebnis.csv"  # kein Pfad
    assert len(imp["parsed"]["cards"]) == 2

    e1, e2 = s["entries"]
    applied = client.post(f"/imports/{imp['id']}/apply", json={"assignments": [
        {"card": 0, "entry_id": e1["id"]}, {"card": 1, "entry_id": e2["id"]}]})
    assert applied.status_code == 200, applied.text
    card = client.get(f"/entries/{e2['id']}/scorecard").json()
    assert card["source"] == "csv" and card["complete"] is False
    again = client.post(f"/imports/{imp['id']}/apply", json={"assignments": [
        {"card": 0, "entry_id": e1["id"]}]})
    assert again.status_code == 409


def test_import_apply_is_all_or_nothing(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    data = _csv([("A", [5] * 18), ("B", [5] * 18)])
    imp = client.post("/imports", content=data).json()
    e1 = s["entries"][0]
    r = client.post(f"/imports/{imp['id']}/apply", json={"assignments": [
        {"card": 0, "entry_id": e1["id"]}, {"card": 5, "entry_id": s["entries"][1]["id"]}]})
    assert r.status_code == 422
    assert client.get(f"/entries/{e1['id']}/scorecard").status_code == 404
    assert client.get(f"/imports/{imp['id']}").json()["status"] == "needs_review"


def test_import_duplicate_returns_existing(client, db):
    make_user(db)
    login(client)
    data = _csv([("A", [5] * 18)])
    first = client.post("/imports", content=data)
    second = client.post("/imports", content=data)
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["duplicate"] is True and second.json()["id"] == first.json()["id"]


def test_import_checksum_mismatch_and_garbage_leave_no_files(client, db, storage_dir):
    make_user(db)
    login(client)
    r = client.post("/imports", content=b"Name;1\nA;4\n", headers={"X-Content-SHA256": "a" * 64})
    assert r.status_code == 400
    r = client.post("/imports", content=b"\x00\x01\x02binary")
    assert r.status_code == 415
    assert [p for p in storage_dir.rglob("*") if p.is_file()] == []


def test_import_unparseable_csv_stored_as_failed(client, db):
    make_user(db)
    login(client)
    r = client.post("/imports", content=b"irgendwas;ohne;loecher\n1;2;3\n")
    assert r.status_code == 201 and r.json()["status"] == "failed" and r.json()["error"]


def test_imports_require_editor(client, db):
    make_user(db, "viewer@example.org", "viewer")
    login(client, "viewer@example.org")
    assert client.post("/imports", content=b"Name;1\n").status_code == 403


def test_production_hides_docs(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("GOLF_ENV", "production")
    monkeypatch.setenv("GOLF_SESSION_SECRET", "x" * 40)
    monkeypatch.setenv("GOLF_COOKIE_SECURE", "true")
    monkeypatch.setenv("GOLF_DATABASE_URL", "postgresql+psycopg://golf:anders@db/golf")
    get_settings.cache_clear()
    try:
        from app.main import create_app

        app = create_app()
        assert app.openapi_url is None and app.docs_url is None
    finally:
        get_settings.cache_clear()
