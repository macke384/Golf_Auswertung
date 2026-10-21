"""Einzelrunden, persönliche Auswertung, Turniere und Mannschaften bearbeiten/löschen."""
import pytest

from tests.factories import PARS_18, course_payload, login, make_user, setup_tournament

pytestmark = pytest.mark.postgres


def _player(client, hi="18.4"):
    return client.post("/players", json={"first_name": "Eva", "last_name": "Einzel", "gender": "w",
                                         "handicap_index": hi}).json()["id"]


def test_single_round_full_flow(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    pid = _player(client)
    r = client.post("/rounds", json={"player_id": pid, "tee_id": c["tee_ids"][0],
                                     "played_on": "2026-10-20", "note": "Training"})
    assert r.status_code == 201, r.text
    rnd = r.json()
    assert rnd["playing_handicap"] == 20  # 100 % Allowance
    card = client.put(f"/entries/{rnd['entry_id']}/scorecard", json={"strokes": [p + 1 for p in PARS_18]})
    assert card.json()["gross_strokes"] == 90
    # Erscheint nicht in der Turnierliste, aber in den Runden
    assert client.get("/tournaments").json() == []
    rounds = client.get("/rounds").json()
    assert len(rounds) == 1 and rounds[0]["note"] == "Training" and rounds[0]["player"] == "Eva Einzel"
    # Zweiter Spieler kann nicht gemeldet werden
    other = _player(client)
    dup = client.post(f"/tournaments/{rnd['tournament_id']}/entries",
                      json={"player_id": other, "tee_id": c["tee_ids"][0]})
    assert dup.status_code == 409
    # Zwei Einzelrunden am selben Tag sind erlaubt
    again = client.post("/rounds", json={"player_id": pid, "tee_id": c["tee_ids"][0],
                                         "played_on": "2026-10-20"})
    assert again.status_code == 201


def test_single_round_nine_holes_back(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    pid = _player(client)
    rnd = client.post("/rounds", json={"player_id": pid, "tee_id": c["tee_ids"][0],
                                       "played_on": "2026-10-20", "holes_count": 9,
                                       "start_hole": 10}).json()
    info = client.get(f"/entries/{rnd['entry_id']}").json()
    assert [h["number"] for h in info["holes"]] == list(range(10, 19))
    assert info["kind"] == "single"


def test_delete_single_round_entry_removes_round(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    rnd = client.post("/rounds", json={"player_id": _player(client), "tee_id": c["tee_ids"][0],
                                       "played_on": "2026-10-20"}).json()
    assert client.delete(f"/entries/{rnd['entry_id']}").status_code == 204
    assert client.get("/rounds").json() == []
    assert client.get(f"/tournaments/{rnd['tournament_id']}").status_code == 404


def test_player_rounds_and_stats(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    pid = s["player_ids"][0]
    client.put(f"/entries/{s['entries'][0]['id']}/scorecard", json={"strokes": [p + 1 for p in PARS_18]})
    rnd = client.post("/rounds", json={"player_id": pid, "tee_id": s["course"]["tee_ids"][0],
                                       "played_on": "2026-11-01"}).json()
    client.put(f"/entries/{rnd['entry_id']}/scorecard", json={"strokes": PARS_18})
    res = client.get(f"/players/{pid}/rounds").json()
    assert [r["kind"] for r in res["rounds"]] == ["single", "tournament"]  # neueste zuerst
    st = res["stats"]["18"]
    assert st["rounds"] == 2 and st["best_gross"] == 72 and st["avg_gross"] == 81.0
    assert st["best_net_stableford"] == 56


def test_update_tournament_name_date_and_allowance(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    tid = s["tournament_id"]
    assert client.patch(f"/tournaments/{tid}", json={"name": "Spieltag 1", "played_on": "2026-05-11"}).status_code == 200
    r = client.patch(f"/tournaments/{tid}", json={"allowance_pct": 95})
    assert r.status_code == 409 and r.json()["code"] == "tournament_in_use"
    r = client.patch(f"/tournaments/{tid}?force=true", json={"allowance_pct": 95})
    assert r.status_code == 200 and r.json()["recalculated"] == 1
    d = client.get(f"/tournaments/{tid}").json()
    assert d["name"] == "Spieltag 1" and d["allowance_pct"] == 95 and d["entries"][0]["playing_hcp"] == 19


def test_tournament_structure_change_blocked_with_entries(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    r = client.patch(f"/tournaments/{s['tournament_id']}?force=true", json={"holes_count": 9})
    assert r.status_code == 409


def test_tournament_structure_change_without_entries(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    tid = client.post("/tournaments", json={"name": "X", "played_on": "2026-05-01",
                                            "course_id": c["id"]}).json()["id"]
    r = client.patch(f"/tournaments/{tid}", json={"holes_count": 9, "start_hole": 10})
    assert r.status_code == 200
    d = client.get(f"/tournaments/{tid}").json()
    assert d["holes_count"] == 9 and d["start_hole"] == 10


def test_duplicate_tournament_name_on_rename(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    client.post("/tournaments", json={"name": "A", "played_on": "2026-05-01", "course_id": c["id"]})
    tid = client.post("/tournaments", json={"name": "B", "played_on": "2026-05-01", "course_id": c["id"]}).json()["id"]
    assert client.patch(f"/tournaments/{tid}", json={"name": "A"}).status_code == 409


def test_delete_tournament(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    e1, e2 = (e["id"] for e in s["entries"])
    client.put(f"/entries/{e1}/scorecard", json={"strokes": PARS_18, "status": "confirmed"})
    assert client.delete(f"/tournaments/{s['tournament_id']}").status_code == 409
    client.put(f"/entries/{e1}/scorecard", json={"strokes": PARS_18, "status": "draft"})
    assert client.delete(f"/tournaments/{s['tournament_id']}").status_code == 204
    assert client.get(f"/entries/{e2}").status_code == 404
    from sqlalchemy import text
    assert db.execute(text("SELECT count(*) FROM hole_scores")).scalar() == 0


def test_team_rename_delete_and_entry_team_change(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    tid = s["team_id"]
    assert client.patch(f"/teams/{tid}", json={"name": "Herren I"}).status_code == 200
    assert client.get("/teams").json()[0]["name"] == "Herren I"
    assert client.delete(f"/teams/{tid}").status_code == 409
    eid = s["entries"][0]["id"]
    assert client.patch(f"/entries/{eid}", json={"team_id": None}).status_code == 200
    assert client.delete(f"/teams/{tid}").status_code == 204
    assert client.get("/teams").json() == []
