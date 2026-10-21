"""API-Endpunkte, die die Weboberfläche nutzt."""
import pytest

from tests.factories import PARS_18, login, make_user, setup_tournament

pytestmark = pytest.mark.postgres


def test_index_and_static_served_with_csp(client):
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200


def test_lists_and_tournament_detail(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    assert client.get("/courses").json()[0]["tees"][0]["par"] == 72
    teams = client.get("/teams").json()
    assert len(teams[0]["members"]) == 2
    t = client.get("/tournaments").json()[0]
    assert t["entries"] == 2 and t["course"] == "GC Saar"
    eid = s["entries"][0]["id"]
    client.put(f"/entries/{eid}/scorecard", json={"strokes": [p + 1 for p in PARS_18]})
    d = client.get(f"/tournaments/{s['tournament_id']}").json()
    with_card = [e for e in d["entries"] if e["scorecard"]]
    assert len(with_card) == 1 and with_card[0]["scorecard"]["net_stableford"] == 38


def test_entry_holes_and_preview_do_not_save(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=1)
    eid = s["entries"][0]["id"]
    info = client.get(f"/entries/{eid}").json()
    assert len(info["holes"]) == 18 and sum(h["received"] for h in info["holes"]) == 20
    p = client.post(f"/entries/{eid}/scorecard/preview", json={"strokes": [5] * 18}).json()
    assert p["errors"] == [] and p["result"]["gross_strokes"] == 90
    bad = client.post(f"/entries/{eid}/scorecard/preview",
                      json={"strokes": [5] * 17 + [None]}).json()
    assert bad["result"]["complete"] is False
    assert client.get(f"/entries/{eid}/scorecard").status_code == 404


def test_patch_player_and_team_members(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    pid = s["player_ids"][0]
    assert client.patch(f"/players/{pid}", json={"handicap_index": "12.3"}).status_code == 200
    assert client.patch(f"/players/{pid}", json={"handicap_index": "99"}).status_code == 422
    players = {p["id"]: p for p in client.get("/players").json()}
    assert players[pid]["handicap_index"] == 12.3
    r = client.put(f"/teams/{s['team_id']}/members", json={"player_ids": [pid]})
    assert r.status_code == 200
    assert [m["id"] for m in client.get("/teams").json()[0]["members"]] == [pid]


def test_delete_entry_blocked_when_confirmed(client, db):
    make_user(db)
    login(client)
    s = setup_tournament(client, players=2)
    e1, e2 = (e["id"] for e in s["entries"])
    client.put(f"/entries/{e1}/scorecard", json={"strokes": [5] * 18, "status": "confirmed"})
    assert client.delete(f"/entries/{e1}").status_code == 409
    client.put(f"/entries/{e2}/scorecard", json={"strokes": [5] * 18})
    assert client.delete(f"/entries/{e2}").status_code == 204
    assert client.get(f"/entries/{e2}/scorecard").status_code == 404


def test_import_file_served_sandboxed(client, db):
    make_user(db)
    login(client)
    data = ("Name;" + ";".join(str(i) for i in range(1, 19)) + "\nA;" + ";".join(["5"] * 18)).encode()
    imp = client.post("/imports", content=data).json()
    r = client.get(f"/imports/{imp['id']}/file")
    assert r.status_code == 200 and r.content == data
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert client.get("/imports").json()[0]["cards"] == 1


def test_user_admin(client, db):
    make_user(db, "admin@example.org", "admin")
    login(client, "admin@example.org")
    me = client.get("/users").json()[0]
    assert client.patch(f"/users/{me['id']}", json={"role": "viewer"}).status_code == 409
    new = client.post("/users", json={"email": "Neu@Example.org", "password": "x" * 12}).json()
    assert new["email"] == "neu@example.org"
    assert client.patch(f"/users/{new['id']}", json={"is_active": False}).status_code == 200


def test_editor_cannot_manage_users(client, db):
    make_user(db)
    login(client)
    assert client.get("/users").status_code == 403
