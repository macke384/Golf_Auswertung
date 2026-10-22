"""Katalog-Endpunkte und Übernahme mit Herkunftsangabe (gegen PostgreSQL, ohne Netz)."""
import pytest

from tests.catalog_fixtures import fake_fetcher
from tests.factories import login, make_user

pytestmark = pytest.mark.postgres


@pytest.fixture
def cclient(client):
    client.app.state.catalog.fetcher = fake_fetcher()
    return client


def test_search_requires_editor_and_input(cclient, db):
    assert cclient.get("/course-catalog/handicap-report/search?q=aachen").status_code == 401
    make_user(db, "viewer@example.org", "viewer")
    login(cclient, "viewer@example.org")
    assert cclient.get("/course-catalog/handicap-report/search?q=aachen").status_code == 403


def test_search_and_detail(cclient, db):
    make_user(db)
    login(cclient)
    assert cclient.get("/course-catalog/handicap-report/search?q=a").status_code == 422
    r = cclient.get("/course-catalog/handicap-report/search", params={"q": "Saarbrücken"})
    assert r.status_code == 200 and r.json()["results"][0]["imported_course_id"] is None
    d = cclient.get("/course-catalog/handicap-report/courses/aachener-golf-club-1927-e-v-aachen-18").json()
    assert len(d["tees"]) == 3 and d["imported_course_id"] is None
    assert cclient.get("/course-catalog/handicap-report/courses/gibt-es-nicht").status_code == 404
    assert cclient.get("/course-catalog/handicap-report/courses/UPPER").status_code == 422
    assert "Saarland" in cclient.get("/course-catalog/handicap-report/bundeslaender").json()


def test_import_marks_source_and_prevents_duplicate(cclient, db):
    make_user(db)
    login(cclient)
    d = cclient.get("/course-catalog/handicap-report/courses/aachener-golf-club-1927-e-v-aachen-18").json()
    gelb = d["tees"][0]
    payload = {
        "name": d["suggested_name"], "source_ref": d["source_ref"],
        "tees": [{"name": gelb["name"], "gender": gelb["gender"], "holes_count": 18,
                  "course_rating": str(gelb["course_rating"]), "slope_rating": gelb["slope_rating"],
                  "par": gelb["par"], "holes": d["holes"]}],
    }
    r = cclient.post("/courses", json=payload)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert cclient.get(f"/courses/{cid}").json()["source_ref"] == d["source_ref"]
    again = cclient.post("/courses", json=payload | {"name": "Anderer Name"})
    assert again.status_code == 409 and "bereits übernommen" in again.json()["detail"]
    s = cclient.get("/course-catalog/handicap-report/search", params={"q": "aachen"}).json()
    assert s["results"][0]["imported_course_id"] == cid
    det = cclient.get("/course-catalog/handicap-report/courses/aachener-golf-club-1927-e-v-aachen-18").json()
    assert det["imported_course_id"] == cid


def test_invalid_source_ref_rejected(cclient, db):
    make_user(db)
    login(cclient)
    from tests.factories import course_payload

    r = cclient.post("/courses", json=course_payload() | {"source_ref": "http://evil/x"})
    assert r.status_code == 422


def test_catalog_unreachable_gives_503(cclient, db):
    from app.catalog.handicap_report import CatalogError

    def down(url):
        raise CatalogError("Platzkatalog ist nicht erreichbar.", 503)

    cclient.app.state.catalog.fetcher = down
    make_user(db)
    login(cclient)
    r = cclient.get("/course-catalog/handicap-report/search?q=aachen")
    assert r.status_code == 503 and "nicht erreichbar" in r.json()["detail"]
