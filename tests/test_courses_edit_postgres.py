"""Plätze und Abschläge bearbeiten/löschen."""
import copy

import pytest

from tests.factories import PARS_18, course_payload, login, make_user, setup_tournament

pytestmark = pytest.mark.postgres


def tee_payload(**over):
    t = copy.deepcopy(course_payload()["tees"][0])
    t.update(over)
    return t


def test_rename_and_delete_unused_course(client, db):
    make_user(db)
    login(client)
    cid = client.post("/courses", json=course_payload()).json()["id"]
    assert client.patch(f"/courses/{cid}", json={"name": "Neuer Name"}).status_code == 200
    assert client.get(f"/courses/{cid}").json()["name"] == "Neuer Name"
    assert client.delete(f"/courses/{cid}").status_code == 204
    assert client.get(f"/courses/{cid}").status_code == 404


def test_course_with_tournament_cannot_be_deleted(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    r = client.delete(f"/courses/{s['course']['id']}")
    assert r.status_code == 409 and "Turnier" in r.json()["detail"]


def test_edit_unused_tee_including_swapped_stroke_index(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    tid = c["tee_ids"][0]
    t = tee_payload(name="Gelb neu", course_rating="70.8")
    # Loch-HCP von Loch 1 und 2 tauschen: darf nicht am Unique-Constraint scheitern
    t["holes"][0]["stroke_index"], t["holes"][1]["stroke_index"] = (
        t["holes"][1]["stroke_index"], t["holes"][0]["stroke_index"])
    r = client.put(f"/tees/{tid}", json=t)
    assert r.status_code == 200, r.text
    tee = client.get(f"/courses/{c['id']}").json()["tees"][0]
    assert tee["name"] == "Gelb neu" and tee["course_rating"] == 70.8
    assert tee["holes"][0]["stroke_index"] == 11


def test_edit_invalid_tee_rejected(client, db):
    make_user(db)
    login(client)
    c = client.post("/courses", json=course_payload()).json()
    r = client.put(f"/tees/{c['tee_ids'][0]}", json=tee_payload(par=70))
    assert r.status_code == 422


def test_edit_used_tee_needs_confirmation_and_recalculates(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    tid = s["course"]["tee_ids"][0]
    assert s["entries"][0]["playing_handicap"] == 20
    changed = tee_payload(slope_rating=140)
    r = client.put(f"/tees/{tid}", json=changed)
    assert r.status_code == 409 and r.json()["code"] == "tee_in_use"
    r = client.put(f"/tees/{tid}?force=true", json=changed)
    assert r.status_code == 200 and r.json()["recalculated"] == 1
    # 18.4 × 140/113 − 0.8 = 22.0
    d = client.get(f"/tournaments/{s['tournament_id']}").json()
    assert d["entries"][0]["playing_hcp"] == 22


def test_rename_used_tee_needs_no_confirmation(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    r = client.put(f"/tees/{s['course']['tee_ids'][0]}", json=tee_payload(name="Gelb (Herren)"))
    assert r.status_code == 200 and r.json()["recalculated"] == 0


def test_confirmed_scorecard_blocks_tee_edit(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    client.put(f"/entries/{eid}/scorecard", json={"strokes": PARS_18, "status": "confirmed"})
    r = client.put(f"/tees/{s['course']['tee_ids'][0]}?force=true", json=tee_payload(slope_rating=140))
    assert r.status_code == 409 and "bestätigten" in r.json()["detail"]


def test_delete_tee(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    cid = s["course"]["id"]
    used = s["course"]["tee_ids"][0]
    assert client.delete(f"/tees/{used}").status_code == 409
    extra = client.post(f"/courses/{cid}/tees", json=tee_payload(name="Rot", gender="w")).json()["id"]
    assert client.get(f"/courses/{cid}").json()["tees"][0]["entries"] == 1
    assert client.delete(f"/tees/{extra}").status_code == 204
    assert len(client.get(f"/courses/{cid}").json()["tees"]) == 1


def test_viewer_cannot_edit(client, db):
    make_user(db, "viewer@example.org", "viewer")
    login(client, "viewer@example.org")
    assert client.delete("/courses/1").status_code == 403
    assert client.put("/tees/1", json=tee_payload()).status_code == 403
