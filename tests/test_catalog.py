"""Platzkatalog: Suche, Validierung, Umwandlung, Cache und Fehlerfälle (ohne Netz)."""
import pytest

from app.catalog.handicap_report import Catalog, CatalogError, http_get_json
from tests.catalog_fixtures import AACHEN, INDEX, fake_fetcher

URL = "https://example.invalid/api/v1/courses.php"


def cat(calls=None, **kw):
    return Catalog(URL, fetcher=fake_fetcher(calls), **kw)


def ids(res):
    return [r["id"] for r in res["results"]]


def test_search_umlaut_both_directions():
    c = cat()
    assert ids(c.search("Saarbrücken")) == ["golfclub-saarbruecken-e-v-wallerfangen-gisingen-18"]
    assert ids(c.search("saarbruecken")) == ids(c.search("Saarbr"))
    assert "1-golfclub-fuerth-e-v-fuerth-9" in ids(c.search("Fürth"))
    assert "1-golfclub-fuerth-e-v-fuerth-9" in ids(c.search("fuerth"))


def test_search_multiple_tokens_club_and_ort():
    c = cat()
    assert ids(c.search("golf homburg")) == ["golf-club-homburg-saar-websweiler-hof-e-v-homburg-18"]
    assert ids(c.search("websweiler")) == ["golf-club-homburg-saar-websweiler-hof-e-v-homburg-18"]


def test_search_filters_and_bundesland_aliases():
    c = cat()
    assert ids(c.search("", bundesland="Nordrhein-Westfalen")) == ["aachener-golf-club-1927-e-v-aachen-18"]
    assert ids(c.search("", bundesland="NRW")) == ["aachener-golf-club-1927-e-v-aachen-18"]
    assert ids(c.search("golf", holes=9)) == ["1-golfclub-fuerth-e-v-fuerth-9"]
    assert ids(c.search("golf", only_with_holes=True)) == ["aachener-golf-club-1927-e-v-aachen-18"]
    res = c.search("", bundesland="Saarland")
    assert all(r["bundesland"] == "Saarland" for r in res["results"]) and res["total"] == 2
    assert c.search("lorch")["results"][0]["bundesland"] == "Baden-Württemberg"
    assert "Nordrhein-Westfalen" in c.bundeslaender() and "NRW" not in c.bundeslaender()


def test_search_plz_gives_hint():
    res = cat().search("66123")
    assert res["hint"] and "Postleitzahl" in res["hint"]


def test_invalid_index_entries_skipped():
    res = cat().search("", bundesland="Bayern")
    assert res["total"] == 1  # kaputter Eintrag bricht die Suche nicht


def test_index_is_cached():
    calls = []
    c = cat(calls)
    c.search("aachen")
    c.search("saar")
    assert len(calls) == 1


def test_stale_cache_used_when_source_down():
    state = {"down": False}
    good = fake_fetcher()

    def fetch(url):
        if state["down"]:
            raise CatalogError("Platzkatalog ist nicht erreichbar.", 503)
        return good(url)

    c = Catalog(URL, ttl_seconds=60, fetcher=fetch)
    c.search("aachen")
    c._index.fetched_at -= 3600  # abgelaufen
    state["down"] = True
    res = c.search("aachen")
    assert res["stale"] is True and res["total"] == 1


def test_source_down_without_cache_raises():
    def fetch(url):
        raise CatalogError("Platzkatalog ist nicht erreichbar.", 503)

    with pytest.raises(CatalogError) as e:
        Catalog(URL, fetcher=fetch).search("x")
    assert e.value.status == 503


def test_unknown_format_raises():
    with pytest.raises(CatalogError):
        Catalog(URL, fetcher=lambda u: ["kein", "objekt"]).search("aa")
    with pytest.raises(CatalogError):
        Catalog(URL, fetcher=lambda u: {"courses": [{"bad": 1}]}).search("aa")


def test_course_with_holes_mapped():
    d = cat().course("aachener-golf-club-1927-e-v-aachen-18")
    assert d["suggested_name"] == "Aachener Golf Club 1927 e.V. – 18-Loch-Platz"
    assert d["bundesland"] == "Nordrhein-Westfalen" and d["holes_count"] == 18
    assert len(d["holes"]) == 18 and d["holes"][8] == {"number": 9, "par": 4, "stroke_index": 1}
    genders = [(t["name"], t["gender"]) for t in d["tees"]]
    assert genders == [("Gelb", "m"), ("Rot", "m"), ("Rot", "w")]
    assert d["tees"][0]["course_rating"] == 70.3 and d["tees"][2]["course_rating"] == 72.0
    # Rot Herren: Par 70 ≠ Summe der Loch-Pars 72 → Hinweis
    assert any("weicht" in w for w in d["tees"][1]["warnings"])
    assert d["source_ref"] == "handicap.report:aachener-golf-club-1927-e-v-aachen-18"


def test_course_without_holes_warns():
    d = cat().course("golfclub-saarbruecken-e-v-wallerfangen-gisingen-18")
    assert d["holes"] is None and any("keine Lochdaten" in w for w in d["warnings"])


def test_27_hole_course_becomes_18_with_warning_and_unsicher():
    d = cat().course("golf-club-hetzenhof-e-v-lorch-27")
    assert d["holes_count"] == 18 and any("27-Loch" in w for w in d["warnings"])
    assert any("unsicher" in w for w in d["tees"][0]["warnings"])


def test_course_id_validated_before_request():
    calls = []
    c = cat(calls)
    for bad in ("../etc/passwd", "a b", "x?id=1&evil=2", "", "A" * 5):
        with pytest.raises(CatalogError) as e:
            c.course(bad)
        assert e.value.status == 422
    assert calls == []


def test_course_not_found_and_mismatching_id():
    with pytest.raises(CatalogError) as e:
        cat().course("gibt-es-nicht-18")
    assert e.value.status == 404
    other = dict(AACHEN, id="anderer-platz-18")
    with pytest.raises(CatalogError):
        Catalog(URL, fetcher=lambda u: other).course("aachener-golf-club-1927-e-v-aachen-18")


def test_implausible_values_rejected():
    bad = dict(AACHEN, tees=[{"name": "X", "gender": "M", "cr": 300, "slope": 999, "par": 72}])
    with pytest.raises(CatalogError):
        Catalog(URL, fetcher=lambda u: bad).course("aachener-golf-club-1927-e-v-aachen-18")


def test_duplicate_stroke_index_dropped():
    holes = [dict(h, strokeIndex=1) for h in AACHEN["holes"]]
    d = Catalog(URL, fetcher=lambda u: dict(AACHEN, holes=holes)).course("aachener-golf-club-1927-e-v-aachen-18")
    assert all(h["stroke_index"] is None for h in d["holes"])
    assert any("Loch-HCP" in w for w in d["warnings"])


def test_http_client_handles_unreachable_host():
    # .invalid ist per RFC 2606 nie auflösbar → kontrollierter Fehler statt Exception-Kaskade
    with pytest.raises(CatalogError) as e:
        http_get_json("https://nicht-vorhanden.invalid/x.json")
    assert e.value.status in (502, 503)


def test_index_fixture_shape():
    assert INDEX["courses"][0]["hasHoles"] is True
