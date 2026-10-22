"""golfcourseapi.com: Kontingent, Cache, Parallelität, Umwandlung (gegen PostgreSQL, ohne Netz)."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.catalog.base import CatalogError
from app.catalog.golfcourseapi import GolfCourseAPI
from tests import gca_fixtures as fx
from tests.factories import login, make_user

pytestmark = pytest.mark.postgres
KEY = "TEST-KEY-NICHT-ECHT"


@pytest.fixture
def gca(db, migrated_engine):
    calls = []
    api = GolfCourseAPI("https://api.example.invalid", KEY, 5,
                        sessionmaker(bind=migrated_engine), fetcher=fx.fake(calls))
    api.calls = calls
    return api


def test_search_parses_sorts_germany_first_and_sends_bearer(gca):
    res = gca.search("Saarbrücken")
    assert [r["id"] for r in res["results"]] == ["a3b4c5d6", "7k2m9qb4"]  # ungültiger Eintrag fehlt
    assert res["results"][0]["city"] == "Wallerfangen"
    url, headers = gca.calls[0]
    assert url.startswith("https://api.example.invalid/v1/search?search_query=Saarbr")
    assert headers == {"Authorization": f"Bearer {KEY}"}
    assert KEY not in str(res)


def test_search_too_short_costs_nothing(gca):
    with pytest.raises(CatalogError) as e:
        gca.search("ab")
    assert e.value.status == 422 and gca.calls == [] and gca.usage()["used"] == 0


def test_cache_saves_quota_and_survives_new_client(gca, migrated_engine):
    gca.search("Saarbrücken")
    gca.search("  saarbrücken ")  # gleicher Suchbegriff, anders geschrieben
    assert len(gca.calls) == 1 and gca.usage()["used"] == 1
    fresh_calls = []
    fresh = GolfCourseAPI("https://api.example.invalid", KEY, 5, sessionmaker(bind=migrated_engine),
                          fetcher=fx.fake(fresh_calls))
    assert fresh.search("saarbrücken")["cached"] is True and fresh_calls == []  # wie nach Neustart


def test_own_daily_limit_stops_requests(gca):
    for q in ("aaa", "bbb", "ccc", "ddd", "eee"):
        gca.search(q)
    with pytest.raises(CatalogError) as e:
        gca.search("fff")
    assert e.value.status == 429 and len(gca.calls) == 5
    assert gca.usage() == {"used": 5, "limit": 5, "remaining": 0}
    assert gca.search("aaa")["cached"] is True  # Bekanntes geht weiter


def test_provider_429_marks_day_exhausted(gca):
    gca.fetcher = fx.fake(gca.calls, fail=CatalogError("Das Tageskontingent ist aufgebraucht.", 429))
    with pytest.raises(CatalogError):
        gca.search("aaa")
    assert gca.usage()["remaining"] == 0
    with pytest.raises(CatalogError):
        gca.search("bbb")
    assert len(gca.calls) == 1  # zweite Suche hat gar nicht mehr angefragt


def test_stale_cache_on_outage(gca, db):
    gca.search("saarbruecken")
    db.execute(text("UPDATE catalog_cache SET fetched_at = now() - interval '30 days'"))
    db.commit()
    gca.fetcher = fx.fake(gca.calls, fail=CatalogError("Platzkatalog ist nicht erreichbar.", 503))
    res = gca.search("saarbruecken")
    assert res["stale"] is True and res["results"]


def test_no_key_never_requests(db, migrated_engine):
    calls = []
    api = GolfCourseAPI("https://api.example.invalid", "", 5, sessionmaker(bind=migrated_engine),
                        fetcher=fx.fake(calls))
    with pytest.raises(CatalogError) as e:
        api.search("saarbruecken")
    assert e.value.status == 503 and "GOLF_GOLFCOURSEAPI_KEY" in e.value.message and calls == []


def test_parallel_identical_searches_cost_one_request(gca):
    slow = fx.fake(gca.calls)

    def slow_fetch(url, headers=None):
        time.sleep(0.2)
        return slow(url, headers)

    gca.fetcher = slow_fetch
    barrier = threading.Barrier(8)

    def one(_):
        barrier.wait()
        return gca.search("Saarbrücken")["total"]

    with ThreadPoolExecutor(8) as ex:
        assert list(ex.map(one, range(8))) == [2] * 8
    assert len(gca.calls) == 1 and gca.usage()["used"] == 1


def test_parallel_different_searches_respect_limit(db, migrated_engine):
    calls = []
    api = GolfCourseAPI("https://api.example.invalid", KEY, 3, sessionmaker(bind=migrated_engine),
                        fetcher=fx.fake(calls))
    barrier = threading.Barrier(10)

    def one(i):
        barrier.wait()
        try:
            api.search(f"suche{i}")
            return "ok"
        except CatalogError as e:
            return e.status

    with ThreadPoolExecutor(10) as ex:
        results = list(ex.map(one, range(10)))
    assert results.count("ok") == 3 and results.count(429) == 7 and len(calls) == 3


def test_course_mapping_per_tee(gca):
    d = gca.course("A3B4C5D6")  # Groß-/Kleinschreibung egal
    assert d["source_ref"] == "golfcourseapi:a3b4c5d6" and d["holes"] is None
    assert d["suggested_name"] == "Golfclub Saarbrücken e.V. – Golfclub Saarbrücken"
    gelb, blau, rot = d["tees"]
    assert (gelb["name"], gelb["gender"], rot["gender"]) == ("Gelb", "m", "w")
    assert gelb["holes"][3] == {"number": 4, "par": 5, "stroke_index": 1, "length_m": 366}
    assert rot["holes"][3]["stroke_index"] == 1 and rot["holes"][0]["stroke_index"] == 5
    assert all(h["stroke_index"] is None for h in blau["holes"])
    assert any("doppelt" in w for w in blau["warnings"])
    assert blau["par"] == 72 and any("Summe" in w for w in blau["warnings"])
    assert gelb["warnings"] == [] and gelb["length_m"] == 5944


def test_course_nine_holes_and_missing_holes(gca):
    nine = gca.course("n9n9n9n9")["tees"][0]
    assert nine["holes_count"] == 9 and nine["par"] == 30 and nine["holes"][8]["stroke_index"] == 17
    no = gca.course("x1x1x1x1")["tees"][0]
    assert no["holes"] is None and any("Keine Lochdaten" in w for w in no["warnings"])


def test_course_id_validation_and_not_found(gca):
    for bad in ("../../x", "1234567", "ilou1234", "abcdefghi"):
        with pytest.raises(CatalogError) as e:
            gca.course(bad)
        assert e.value.status == 422
    with pytest.raises(CatalogError) as e:
        gca.course("zzzzzzzz")
    assert e.value.status == 404
    assert len(gca.calls) == 1  # nur der 404-Abruf


def test_course_wrapped_in_course_key_and_mismatch(gca):
    gca.fetcher = lambda url, headers=None: {"course": fx.COURSE}
    assert gca.course("a3b4c5d6")["club"] == "Golfclub Saarbrücken e.V."
    gca.fetcher = lambda url, headers=None: dict(fx.COURSE, id="b3b4c5d6")
    with pytest.raises(CatalogError):
        gca.course("a3b4c5d7")


# --- über die HTTP-API ---------------------------------------------------------


@pytest.fixture
def gclient(client, gca):
    client.app.state.golfcourseapi = gca
    return client


def test_api_sources_search_detail_import(gclient, db):
    make_user(db)
    login(gclient)
    src = {s["id"]: s for s in gclient.get("/course-catalog/sources").json()}
    assert src["golfcourseapi"]["configured"] is True and src["golfcourseapi"]["usage"]["limit"] == 5
    assert KEY not in gclient.get("/course-catalog/sources").text
    r = gclient.get("/course-catalog/golfcourseapi/search", params={"q": "Saarbrücken"})
    assert r.status_code == 200 and r.json()["results"][0]["imported_course_id"] is None
    d = gclient.get("/course-catalog/golfcourseapi/courses/a3b4c5d6").json()
    gelb = d["tees"][0]
    payload = {"name": d["suggested_name"], "source_ref": d["source_ref"], "tees": [{
        "name": gelb["name"], "gender": gelb["gender"], "holes_count": 18,
        "course_rating": str(gelb["course_rating"]), "slope_rating": gelb["slope_rating"],
        "par": gelb["par"], "holes": [{k: h[k] for k in ("number", "par", "stroke_index", "length_m")}
                                      for h in gelb["holes"]]}]}
    created = gclient.post("/courses", json=payload)
    assert created.status_code == 201, created.text
    d2 = gclient.get("/course-catalog/golfcourseapi/courses/a3b4c5d6").json()
    assert d2["imported_course_id"] == created.json()["id"] and d2["cached"] is True
    assert gclient.get("/course-catalog/golfcourseapi/courses/abc").status_code == 422


def test_api_quota_message(gclient, db, gca):
    gca.daily_limit = 0
    make_user(db)
    login(gclient)
    r = gclient.get("/course-catalog/golfcourseapi/search", params={"q": "Saarbrücken"})
    assert r.status_code == 429 and "Tageslimit" in r.json()["detail"]
