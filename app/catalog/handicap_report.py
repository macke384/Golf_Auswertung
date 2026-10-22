"""Platzkatalog von handicap.report (https://handicap.report/api/v1/).

Die externe API ist eine fremde, nicht vertrauenswürdige Datenquelle:
- Abruf nur serverseitig, feste Basis-URL aus der Konfiguration (kein SSRF),
  Platz-IDs werden gegen ein striktes Muster geprüft.
- Timeout und Größenlimit; jede Antwort wird gegen ein Schema validiert.
- Ungültige Einträge im Index werden übersprungen statt die Suche zu brechen.
- Index und Details werden zwischengespeichert; ist die Quelle nicht
  erreichbar, wird ein vorhandener (auch abgelaufener) Stand weiterverwendet.

Bekannte Eigenheiten der Quelle (Stand 2026-06):
- Keine PLZ, Adresse oder Koordinaten – Suche nur über Club, Ort, Bundesland.
- Umlaute teils umschrieben ("Saarbruecken") → Suche normalisiert beide Seiten.
- Lochdaten (Par, Stroke Index) fehlen bei den meisten Plätzen (holes = null).
- 27-Loch-Anlagen: Ratings gelten offenbar für eine 18-Loch-Kombination.
- Tees mit "unsicher": true sind ungeprüfte Werte.
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

COURSE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,159}$")
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT_S = 10
SOURCE = "handicap.report"


class CatalogError(Exception):
    """Katalog nicht erreichbar oder Antwort unbrauchbar."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


# --- Schemas der externen Antworten (nur die Felder, die wir nutzen) ----------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class IndexEntry(_Lenient):
    id: str = Field(pattern=COURSE_ID_RE.pattern)
    club: str = Field(min_length=1, max_length=200)
    ort: str = Field(default="", max_length=120)
    bundesland: str = Field(default="", max_length=60)
    holeCount: int = Field(ge=1, le=36)
    hasHoles: bool = False


class ExtTee(_Lenient):
    name: str = Field(min_length=1, max_length=60)
    gender: str = Field(max_length=2)
    cr: float = Field(ge=15, le=90)
    slope: int = Field(ge=55, le=155)
    par: int = Field(ge=20, le=80)
    unsicher: bool = False


class ExtHole(_Lenient):
    number: int = Field(ge=1, le=27)
    par: int = Field(ge=3, le=6)
    strokeIndex: int | None = Field(default=None, ge=1, le=27)


class ExtCourse(_Lenient):
    id: str = Field(pattern=COURSE_ID_RE.pattern)
    club: str = Field(min_length=1, max_length=200)
    ort: str = Field(default="", max_length=120)
    bundesland: str = Field(default="", max_length=60)
    holeCount: int = Field(ge=1, le=36)
    courseName: str | None = Field(default=None, max_length=120)
    tees: list[ExtTee] = Field(default_factory=list, max_length=40)
    holes: list[ExtHole] | None = Field(default=None, max_length=36)


# --- Abruf --------------------------------------------------------------------

Fetcher = Callable[[str], Any]


def http_get_json(url: str) -> Any:
    """GET mit Timeout und Größenlimit; liefert geparstes JSON oder CatalogError."""
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Golf_Auswertung/0.1"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (feste URL)
            if resp.status != 200:
                raise CatalogError(f"Platzkatalog antwortet mit Status {resp.status}.")
            data = resp.read(MAX_BYTES + 1)
    except CatalogError:
        raise
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise CatalogError("Platz im Katalog nicht gefunden.", 404) from e
        raise CatalogError(f"Platzkatalog antwortet mit Status {e.code}.") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise CatalogError("Platzkatalog ist nicht erreichbar.", 503) from e
    if len(data) > MAX_BYTES:
        raise CatalogError("Antwort des Platzkatalogs ist zu groß.")
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CatalogError("Antwort des Platzkatalogs ist kein gültiges JSON.") from e


# --- Normalisierung für die Suche ------------------------------------------------


def norm(text: str) -> str:
    t = text.lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


BUNDESLAENDER = [
    "Baden-Württemberg", "Bayern", "Berlin", "Brandenburg", "Bremen", "Hamburg", "Hessen",
    "Mecklenburg-Vorpommern", "Niedersachsen", "Nordrhein-Westfalen", "Rheinland-Pfalz",
    "Saarland", "Sachsen", "Sachsen-Anhalt", "Schleswig-Holstein", "Thüringen",
]
_BL_BY_NORM = {norm(b): b for b in BUNDESLAENDER} | {"nrw": "Nordrhein-Westfalen", "bw": "Baden-Württemberg"}


def canon_bundesland(raw: str) -> str:
    """'NRW', 'Baden-Wuerttemberg' … → offizieller Name; Unbekanntes bleibt unverändert."""
    return _BL_BY_NORM.get(norm(raw or ""), (raw or "").strip())


# --- Katalog mit Cache -----------------------------------------------------------


@dataclass
class _Cached:
    value: Any
    fetched_at: float


@dataclass
class Catalog:
    base_url: str
    ttl_seconds: int = 3600
    fetcher: Fetcher = http_get_json
    _index: _Cached | None = None
    _details: dict[str, _Cached] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # Index ---------------------------------------------------------------------

    def index(self) -> tuple[list[IndexEntry], dict]:
        now = time.time()
        with self._lock:
            cached = self._index
        if cached and now - cached.fetched_at < self.ttl_seconds:
            return cached.value["entries"], {"stand": cached.value["stand"], "stale": False}
        try:
            raw = self.fetcher(self.base_url)
            entries, stand, skipped = self._parse_index(raw)
        except CatalogError:
            if cached:  # lieber alter Stand als gar keiner
                return cached.value["entries"], {"stand": cached.value["stand"], "stale": True}
            raise
        with self._lock:
            self._index = _Cached({"entries": entries, "stand": stand, "skipped": skipped}, now)
        return entries, {"stand": stand, "stale": False}

    @staticmethod
    def _parse_index(raw: Any) -> tuple[list[IndexEntry], str | None, int]:
        if not isinstance(raw, dict) or not isinstance(raw.get("courses"), list):
            raise CatalogError("Platzkatalog liefert ein unbekanntes Format.")
        entries, skipped = [], 0
        for item in raw["courses"][:5000]:
            try:
                entries.append(IndexEntry.model_validate(item))
            except ValidationError:
                skipped += 1
        if not entries:
            raise CatalogError("Platzkatalog enthält keine lesbaren Plätze.")
        stand = raw.get("stand") if isinstance(raw.get("stand"), str) else None
        return entries, (stand or "")[:20] or None, skipped

    def search(
        self,
        q: str = "",
        bundesland: str | None = None,
        holes: Literal[9, 18] | None = None,
        only_with_holes: bool = False,
        limit: int = 50,
    ) -> dict:
        entries, meta = self.index()
        tokens = norm(q).split()
        hint = None
        if any(t.isdigit() and len(t) == 5 for t in tokens):
            hint = "Der Katalog enthält keine Postleitzahlen. Bitte nach Ort oder Clubnamen suchen."
            tokens = [t for t in tokens if not (t.isdigit() and len(t) == 5)]
        bl = canon_bundesland(bundesland) if bundesland else None
        results = []
        for e in entries:
            land = canon_bundesland(e.bundesland)
            if bl and land != bl:
                continue
            if holes == 9 and e.holeCount != 9:
                continue
            if holes == 18 and e.holeCount < 18:
                continue
            if only_with_holes and not e.hasHoles:
                continue
            hay = norm(f"{e.club} {e.ort} {land} {e.bundesland}")
            if all(t in hay for t in tokens):
                # Treffer am Wortanfang von Club/Ort zuerst
                words = hay.split()
                score = sum(1 for t in tokens if any(w.startswith(t) for w in words))
                results.append((-score, norm(e.club), e))
        results.sort(key=lambda x: (x[0], x[1]))
        return {
            "stand": meta["stand"], "stale": meta["stale"], "hint": hint,
            "total": len(results),
            "results": [e.model_dump() | {"bundesland": canon_bundesland(e.bundesland)}
                        for _, _, e in results[:limit]],
        }

    def bundeslaender(self) -> list[str]:
        entries, _ = self.index()
        return sorted({canon_bundesland(e.bundesland) for e in entries if e.bundesland})

    # Details -------------------------------------------------------------------

    def course(self, course_id: str) -> dict:
        if not COURSE_ID_RE.match(course_id or ""):
            raise CatalogError("Ungültige Platz-ID.", 422)
        now = time.time()
        with self._lock:
            cached = self._details.get(course_id)
        if cached and now - cached.fetched_at < self.ttl_seconds:
            return cached.value
        url = f"{self.base_url}?{urllib.parse.urlencode({'id': course_id})}"
        try:
            raw = self.fetcher(url)
            if not isinstance(raw, dict):
                raise CatalogError("Platzkatalog liefert ein unbekanntes Format.")
            try:
                ext = ExtCourse.model_validate(raw)
            except ValidationError as e:
                raise CatalogError("Platzdaten im Katalog sind unvollständig oder unplausibel.") from e
            if ext.id != course_id:
                raise CatalogError("Platzkatalog liefert einen anderen Platz als angefragt.")
            value = to_import(ext)
        except CatalogError:
            if cached:
                return cached.value | {"stale": True}
            raise
        with self._lock:
            if len(self._details) > 500:
                self._details.clear()
            self._details[course_id] = _Cached(value, now)
        return value


# --- Umwandlung in unser Format ---------------------------------------------------

GENDER_MAP = {"M": "m", "F": "w", "W": "w", "D": "d"}


def to_import(ext: ExtCourse) -> dict:
    """Externe Platzdaten → Vorschlag für 'Neuer Platz' inkl. Hinweisen.

    Es wird nichts automatisch gespeichert; der Nutzer prüft und ergänzt.
    """
    warnings: list[str] = []
    holes_count = ext.holeCount
    if holes_count == 27:
        holes_count = 18
        warnings.append("27-Loch-Anlage: Die Werte gelten vermutlich für eine 18-Loch-Kombination. "
                        "Bitte Kombination und Lochdaten von der Scorekarte prüfen.")
    elif holes_count not in (9, 18):
        warnings.append(f"Ungewöhnliche Lochzahl {holes_count} – bitte prüfen.")
        holes_count = 18 if holes_count > 9 else 9

    holes = None
    if ext.holes:
        hs = sorted(ext.holes, key=lambda h: h.number)
        numbers = [h.number for h in hs]
        sis = [h.strokeIndex for h in hs]
        if numbers != list(range(1, holes_count + 1)):
            warnings.append("Lochdaten der Quelle sind unvollständig und wurden nicht übernommen.")
        else:
            if any(s is None for s in sis) or len(set(sis)) != len(sis):
                warnings.append("Loch-HCP fehlt oder ist in der Quelle doppelt – bitte ergänzen.")
                sis = [None] * len(hs)
            holes = [{"number": h.number, "par": h.par, "stroke_index": s} for h, s in zip(hs, sis)]
    else:
        warnings.append("Die Quelle enthält keine Lochdaten (Par und Loch-HCP je Loch). "
                        "Bitte von der Scorekarte ergänzen.")

    hole_par_sum = sum(h["par"] for h in holes) if holes else None
    tees = []
    for t in ext.tees:
        tw: list[str] = []
        gender = GENDER_MAP.get(t.gender.upper())
        if gender is None:
            tw.append(f"Unbekanntes Geschlecht '{t.gender}' – bitte wählen.")
            gender = "m"
        if t.unsicher:
            tw.append("In der Quelle als unsicher markiert – Werte mit der Scorekarte abgleichen.")
        if holes_count == 9 and t.par > 45:
            tw.append("Par/CR sehen nach 18-Loch-Werten aus (9 Loch zweimal gespielt?).")
        if hole_par_sum is not None and hole_par_sum != t.par:
            tw.append(f"Par des Abschlags ({t.par}) weicht von der Summe der Loch-Pars "
                      f"({hole_par_sum}) ab – Loch-Pars für diesen Abschlag anpassen.")
        tees.append({
            "name": t.name[:40], "gender": gender, "holes_count": holes_count,
            "course_rating": round(t.cr, 1), "slope_rating": t.slope, "par": t.par,
            "unsicher": t.unsicher, "warnings": tw,
        })
    if not tees:
        warnings.append("Die Quelle enthält keine Abschläge.")

    name = ext.club if not ext.courseName else f"{ext.club} – {ext.courseName}"
    return {
        "source": SOURCE,
        "source_ref": f"{SOURCE}:{ext.id}",
        "id": ext.id,
        "club": ext.club, "ort": ext.ort, "bundesland": canon_bundesland(ext.bundesland),
        "course_name": ext.courseName,
        "suggested_name": name[:150],
        "holes_count": holes_count,
        "holes": holes,
        "tees": tees,
        "warnings": warnings,
        "stale": False,
    }
