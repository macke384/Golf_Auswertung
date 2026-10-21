from __future__ import annotations

from datetime import date

from app.security import hash_password

PARS_18 = [4, 4, 3, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 5, 4]
SI_18 = [7, 11, 15, 1, 3, 13, 17, 9, 5, 8, 16, 2, 6, 12, 4, 18, 10, 14]
PASSWORD = "sehr-geheimes-passwort"


def course_payload(name: str = "GC Saar") -> dict:
    return {
        "name": name,
        "tees": [
            {
                "name": "Gelb", "gender": "m", "holes_count": 18, "course_rating": "71.2",
                "slope_rating": 128, "par": 72,
                "holes": [{"number": i + 1, "par": PARS_18[i], "stroke_index": SI_18[i]}
                          for i in range(18)],
            }
        ],
    }


def make_user(db, email="editor@example.org", role="editor"):
    from app.models import User

    u = User(email=email, password_hash=hash_password(PASSWORD), role=role)
    db.add(u)
    db.commit()
    return u


def login(client, email="editor@example.org"):
    r = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text


def setup_tournament(client, holes_count=18, start_hole=1, players=2, team=True):
    """Legt Platz, Turnier, Spieler (+Team) und Teilnahmen über die API an."""
    course = client.post("/courses", json=course_payload()).json()
    tid = client.post("/tournaments", json={
        "name": "Mannschaftsspiel", "played_on": str(date(2026, 5, 10)), "course_id": course["id"],
        "holes_count": holes_count, "start_hole": start_hole, "allowance_pct": 100,
    }).json()["id"]
    pids = [
        client.post("/players", json={"first_name": f"Spieler{i}", "last_name": "Test",
                                      "gender": "m", "handicap_index": "18.4"}).json()["id"]
        for i in range(players)
    ]
    team_id = client.post("/teams", json={"name": "Herren 1", "player_ids": pids}).json()["id"] \
        if team else None
    entries = [
        client.post(f"/tournaments/{tid}/entries",
                    json={"player_id": pid, "tee_id": course["tee_ids"][0], "team_id": team_id}).json()
        for pid in pids
    ]
    return {"course": course, "tournament_id": tid, "player_ids": pids, "team_id": team_id,
            "entries": entries}
