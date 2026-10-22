"""Beispielantworten im Format von handicap.report (aus echten Antworten abgeleitet, gekürzt)."""

INDEX = {
    "stand": "2026-06-18",
    "version": "v1",
    "count": 7,
    "courses": [
        {"id": "aachener-golf-club-1927-e-v-aachen-18", "club": "Aachener Golf Club 1927 e.V.",
         "ort": "Aachen", "bundesland": "NRW", "holeCount": 18, "hasHoles": True},
        {"id": "golfclub-saarbruecken-e-v-wallerfangen-gisingen-18", "club": "Golfclub Saarbruecken e.V.",
         "ort": "Wallerfangen-Gisingen", "bundesland": "Saarland", "holeCount": 18, "hasHoles": False},
        {"id": "golf-club-homburg-saar-websweiler-hof-e-v-homburg-18",
         "club": "Golf Club Homburg/Saar Websweiler Hof e.V.", "ort": "Homburg", "bundesland": "Saarland",
         "holeCount": 18, "hasHoles": False},
        {"id": "golf-club-bad-saarow-bad-saarow-18", "club": "Golf Club Bad Saarow", "ort": "Bad Saarow",
         "bundesland": "Brandenburg", "holeCount": 18, "hasHoles": False},
        {"id": "1-golfclub-fuerth-e-v-fuerth-9", "club": "1. Golfclub Fürth e.V.", "ort": "Fürth",
         "bundesland": "Bayern", "holeCount": 9, "hasHoles": False},
        {"id": "golf-club-hetzenhof-e-v-lorch-27", "club": "Golf Club Hetzenhof e.V.", "ort": "Lorch",
         "bundesland": "Baden-Wuerttemberg", "holeCount": 27, "hasHoles": False},
        # Kaputter Eintrag: muss übersprungen werden, ohne die Suche zu brechen
        {"id": "../../etc/passwd", "club": "", "holeCount": "x"},
    ],
}

AACHEN = {
    "id": "aachener-golf-club-1927-e-v-aachen-18", "club": "Aachener Golf Club 1927 e.V.",
    "ort": "Aachen", "bundesland": "NRW", "holeCount": 18, "courseName": "18-Loch-Platz",
    "tees": [
        {"name": "Gelb", "gender": "M", "cr": 70.3, "slope": 126, "par": 72, "unsicher": False},
        {"name": "Rot", "gender": "M", "cr": 66.7, "slope": 113, "par": 70, "unsicher": False},
        {"name": "Rot", "gender": "F", "cr": 72, "slope": 123, "par": 72, "unsicher": False},
    ],
    "holes": [
        {"number": n, "par": p, "strokeIndex": si}
        for n, p, si in zip(
            range(1, 19),
            [4, 4, 5, 3, 4, 3, 4, 5, 4, 4, 3, 4, 4, 4, 5, 4, 4, 4],
            [7, 5, 15, 3, 13, 11, 9, 17, 1, 6, 14, 10, 12, 16, 2, 18, 4, 8],
        )
    ],
}

SAARBRUECKEN = {
    "id": "golfclub-saarbruecken-e-v-wallerfangen-gisingen-18", "club": "Golfclub Saarbruecken e.V.",
    "ort": "Wallerfangen-Gisingen", "bundesland": "Saarland", "holeCount": 18, "courseName": "18-Loch",
    "holes": None,
    "tees": [
        {"name": "Gelb", "gender": "M", "cr": 70.4, "slope": 130, "par": 72, "unsicher": False},
        {"name": "Rot", "gender": "F", "cr": 70.9, "slope": 128, "par": 72, "unsicher": False},
    ],
}

HETZENHOF = {
    "id": "golf-club-hetzenhof-e-v-lorch-27", "club": "Golf Club Hetzenhof e.V.", "ort": "Lorch",
    "bundesland": "Baden-Wuerttemberg", "holeCount": 27, "courseName": "27-Hole Course", "holes": None,
    "tees": [{"name": "Gelb", "gender": "M", "cr": 73.1, "slope": 131, "par": 72, "unsicher": True}],
}

DETAILS = {c["id"]: c for c in (AACHEN, SAARBRUECKEN, HETZENHOF)}


def fake_fetcher(calls: list | None = None):
    """Fetcher, der statt HTTP die Beispieldaten liefert."""
    from urllib.parse import parse_qs, urlparse

    from app.catalog.handicap_report import CatalogError

    def fetch(url: str):
        if calls is not None:
            calls.append(url)
        q = parse_qs(urlparse(url).query)
        if "id" not in q:
            return INDEX
        cid = q["id"][0]
        if cid not in DETAILS:
            raise CatalogError("Platz im Katalog nicht gefunden.", 404)
        return DETAILS[cid]

    return fetch
