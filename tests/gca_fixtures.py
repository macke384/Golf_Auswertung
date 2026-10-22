"""Beispielantworten im Format von golfcourseapi.com (OpenAPI 1.3.0)."""
PARS = [4, 4, 3, 5, 4, 4, 3, 4, 5, 4, 3, 4, 5, 4, 4, 3, 5, 4]
SI_M = [7, 11, 15, 1, 3, 13, 17, 9, 5, 8, 16, 2, 6, 12, 4, 18, 10, 14]
SI_W = [5, 9, 17, 1, 3, 11, 15, 7, 13, 6, 18, 2, 4, 12, 8, 16, 10, 14]

SEARCH = {
    "courses": [
        {"id": "7k2m9qb4", "club_name": "Murray Golf Club", "course_name": "Course No. 1",
         "location": {"address": "124 Golf Course Lane, Murray, KY 42071, USA", "city": "Murray",
                      "state": "KY", "country": "United States"},
         "tees": {"male": 2, "female": 1}},
        {"id": "a3b4c5d6", "club_name": "Golfclub Saarbrücken e.V.", "course_name": "Golfclub Saarbrücken",
         "location": {"address": "Oberlimberger Weg, 66798 Wallerfangen, Germany", "city": "Wallerfangen",
                      "state": "Saarland", "country": "Germany", "latitude": 49.33, "longitude": 6.7},
         "tees": {"male": 2, "female": 1}},
        {"id": "INVALID!", "club_name": "kaputt"},
    ]
}


def _holes(si):
    return [{"par": p, "yardage": 400, "meters": 366, "handicap": s} for p, s in zip(PARS, si)]


COURSE = {
    "id": "a3b4c5d6", "club_name": "Golfclub Saarbrücken e.V.", "course_name": "Golfclub Saarbrücken",
    "location": {"address": "Oberlimberger Weg, 66798 Wallerfangen, Germany", "city": "Wallerfangen",
                 "state": "Saarland", "country": "Germany"},
    "tees": {
        "male": [
            {"tee_name": "Gelb", "course_rating": 70.4, "slope_rating": 130, "total_yards": 6500,
             "total_meters": 5944, "number_of_holes": 18, "par_total": 72, "holes": _holes(SI_M)},
            # Loch-HCP doppelt → muss verworfen werden; par_total weicht ab
            {"tee_name": "Blau", "course_rating": 68.0, "slope_rating": 125, "number_of_holes": 18,
             "par_total": 71, "holes": [dict(h, handicap=1) for h in _holes(SI_M)]},
        ],
        "female": [
            {"tee_name": "Rot", "course_rating": 70.9, "slope_rating": 128, "number_of_holes": 18,
             "par_total": 72, "holes": _holes(SI_W)},
        ],
    },
}

NINE = {
    "id": "n9n9n9n9", "club_name": "Kurzplatz", "course_name": "", "location": {},
    "tees": {"male": [{"tee_name": "Gelb", "course_rating": 31.2, "slope_rating": 108,
                       "number_of_holes": 9, "par_total": 30,
                       "holes": [{"par": 3, "handicap": i * 2 - 1} for i in range(1, 7)]
                       + [{"par": 4, "handicap": i * 2 - 1} for i in range(7, 10)]}]},
}

NO_HOLES = {
    "id": "x1x1x1x1", "club_name": "Ohne Löcher", "course_name": "", "location": {},
    "tees": {"male": [{"tee_name": "Weiss", "course_rating": 72.1, "slope_rating": 132,
                       "number_of_holes": 18, "par_total": 72, "holes": []}]},
}

COURSES = {c["id"]: c for c in (COURSE, NINE, NO_HOLES)}


def fake(calls: list | None = None, fail=None):
    from urllib.parse import parse_qs, urlparse

    from app.catalog.base import CatalogError

    def fetch(url, headers=None):
        if calls is not None:
            calls.append((url, headers))
        if fail:
            raise fail
        u = urlparse(url)
        if u.path == "/v1/search":
            return SEARCH
        cid = u.path.rsplit("/", 1)[-1]
        if cid not in COURSES:
            raise CatalogError("Platz im Katalog nicht gefunden.", 404)
        return COURSES[cid]

    return fetch
