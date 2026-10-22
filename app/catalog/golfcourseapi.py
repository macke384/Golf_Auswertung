"""Platzkatalog von golfcourseapi.com (https://api.golfcourseapi.com, OpenAPI 1.3.0).

Genutzt werden nur GET /v1/search und GET /v1/courses/{id}. Vorteil gegenüber
handicap.report: Par und Loch-HCP je Abschlag, Adresse und Land.

Kontingent: Der Free-Tarif erlaubt nur wenige Anfragen pro Tag (~35). Daher:
- Jede Anfrage wird vorher in api_usage gezählt (atomar in PostgreSQL); ist das
  eigene Tageslimit erreicht, wird nicht mehr angefragt.
- Antworten liegen in catalog_cache (Suche 7 Tage, Platz 30 Tage) und
  überstehen Neustarts. Bei Ausfall oder erschöpftem Kontingent wird ein
  abgelaufener Eintrag weiterverwendet und als "stale" markiert.
- Parallele identische Anfragen im selben Prozess warten aufeinander, damit
  nur eine davon Kontingent verbraucht.

Der API-Schlüssel kommt ausschließlich aus der Server-Konfiguration und wird
weder geloggt noch an die Oberfläche gegeben.
"""
from __future__ import annotations

import logging
import re
import threading
import urllib.parse
from datetime import UTC, date, datetime, timedelta
from typing import Any, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.catalog.base import AuthRejected, CatalogError, Fetcher, http_get_json
from app.models import ApiUsage, CatalogCache

log = logging.getLogger(__name__)
SOURCE = "golfcourseapi"
ID_RE = re.compile(r"^[0-9abcdefghjkmnpqrstvwxyz]{8}$")
SEARCH_TTL = timedelta(days=7)
COURSE_TTL = timedelta(days=30)


# --- Schemas der externen Antworten ------------------------------------------


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Location(_Lenient):
    address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, max_length=120)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class Summary(_Lenient):
    id: str = Field(pattern=ID_RE.pattern)
    club_name: str = Field(default="", max_length=200)
    course_name: str = Field(default="", max_length=200)
    location: Location = Field(default_factory=Location)
    tees: dict[str, int] = Field(default_factory=dict)


class Hole(_Lenient):
    par: int | None = Field(default=None, ge=0, le=7)
    handicap: int | None = Field(default=None, ge=0, le=36)
    yardage: int | None = Field(default=None, ge=0, le=1000)
    meters: int | None = Field(default=None, ge=0, le=1000)


class TeeBox(_Lenient):
    tee_name: str = Field(default="", max_length=60)
    course_rating: float | None = Field(default=None, ge=0, le=90)
    slope_rating: int | None = Field(default=None, ge=0, le=200)
    number_of_holes: int | None = Field(default=None, ge=1, le=36)
    par_total: int | None = Field(default=None, ge=0, le=100)
    total_meters: int | None = None
    total_yards: int | None = None
    holes: list[Hole] = Field(default_factory=list, max_length=36)


class Course(_Lenient):
    id: str = Field(pattern=ID_RE.pattern)
    club_name: str = Field(default="", max_length=200)
    course_name: str = Field(default="", max_length=200)
    location: Location = Field(default_factory=Location)
    tees: dict[str, list[TeeBox]] = Field(default_factory=dict)


def clean_key(raw: str) -> str:
    """Entfernt Leerraum, Zeilenenden (\\r von Windows/Mac-Editoren) und umschließende Anführungszeichen."""
    k = (raw or "").strip().strip("\r\n").strip()
    if len(k) >= 2 and k[0] == k[-1] and k[0] in "'\"":
        k = k[1:-1].strip()
    return k


class OwnQuotaReached(CatalogError):
    """Unser eigenes Tageslimit ist erreicht (es wurde nicht angefragt)."""


# --- Client --------------------------------------------------------------------


class GolfCourseAPI:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        daily_limit: int,
        sessionmaker: Callable[[], Session],
        fetcher: Fetcher = http_get_json,
    ):
        self.base_url = base_url.rstrip("/")
        self._api_key = clean_key(api_key)
        # Laut Spezifikation: "Bearer" bevorzugt, "Key" (Legacy) weiter unterstützt.
        # Wir beginnen mit "Key" (funktioniert bei alten und neuen Servern) und
        # merken uns das Verfahren, das zuletzt akzeptiert wurde.
        self._schemes = ["Key", "Bearer"]
        self.daily_limit = daily_limit
        self.sessionmaker = sessionmaker
        self.fetcher = fetcher
        self._key_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    # Kontingent -----------------------------------------------------------------

    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()

    def usage(self) -> dict:
        with self.sessionmaker() as db:
            used = db.scalar(
                select(ApiUsage.count).where(ApiUsage.source == SOURCE, ApiUsage.day == self._today())
            ) or 0
        return {"used": used, "limit": self.daily_limit, "remaining": max(0, self.daily_limit - used)}

    def _reserve(self) -> int:
        """Zählt eine Anfrage atomar vor dem Abruf. Gibt den neuen Stand zurück.

        UPSERT mit Bedingung: bei erreichtem Limit wird nichts geändert und
        keine Zeile zurückgegeben – sicher auch bei parallelen Requests.
        """
        with self.sessionmaker() as db:
            stmt = (
                insert(ApiUsage)
                .values(source=SOURCE, day=self._today(), count=1)
                .on_conflict_do_update(
                    index_elements=[ApiUsage.source, ApiUsage.day],
                    set_={"count": ApiUsage.count + 1},
                    where=ApiUsage.count < self.daily_limit,
                )
                .returning(ApiUsage.count)
            )
            n = db.scalar(stmt) if self.daily_limit > 0 else None
            db.commit()
        if n is None:
            raise OwnQuotaReached(
                f"Das eigene Tageslimit von {self.daily_limit} Anfragen an golfcourseapi.com ist "
                "erreicht. Bereits gesuchte Plätze sind weiter verfügbar; neue Suchen ab morgen.", 429)
        return n

    def _mark_exhausted(self) -> None:
        with self.sessionmaker() as db:
            db.execute(text(
                "INSERT INTO api_usage (source, day, count) VALUES (:s, :d, :n) "
                "ON CONFLICT (source, day) DO UPDATE SET count = GREATEST(api_usage.count, :n)"
            ), {"s": SOURCE, "d": self._today(), "n": self.daily_limit})
            db.commit()

    # Cache ------------------------------------------------------------------------

    def _cache_get(self, key: str) -> tuple[Any, datetime] | None:
        with self.sessionmaker() as db:
            row = db.get(CatalogCache, (SOURCE, key))
            return (row.payload, row.fetched_at) if row else None

    def _cache_put(self, key: str, payload: Any) -> None:
        with self.sessionmaker() as db:
            stmt = insert(CatalogCache).values(source=SOURCE, key=key, payload=payload,
                                               fetched_at=datetime.now(UTC))
            db.execute(stmt.on_conflict_do_update(
                index_elements=[CatalogCache.source, CatalogCache.key],
                set_={"payload": stmt.excluded.payload, "fetched_at": stmt.excluded.fetched_at},
            ))
            db.commit()

    def _lock_for(self, key: str) -> threading.Lock:
        with self._locks_guard:
            if len(self._key_locks) > 1000:
                self._key_locks.clear()
            return self._key_locks.setdefault(key, threading.Lock())

    def _cached_fetch(self, key: str, path: str, ttl: timedelta, parse: Callable[[Any], Any]) -> tuple[Any, dict]:
        """Liefert (Ergebnis, meta). Holt nur bei fehlendem/abgelaufenem Cache."""
        with self._lock_for(key):
            hit = self._cache_get(key)
            now = datetime.now(UTC)
            if hit and now - hit[1] < ttl:
                return hit[0], {"cached": True, "stale": False, "fetched_at": hit[1].isoformat()}
            if not self._api_key:
                if hit:
                    return hit[0], {"cached": True, "stale": True, "fetched_at": hit[1].isoformat()}
                raise CatalogError("Für golfcourseapi.com ist kein API-Schlüssel eingerichtet "
                                   "(GOLF_GOLFCOURSEAPI_KEY).", 503)
            try:
                raw = self._authorized_get(f"{self.base_url}{path}")
                value = parse(raw)
            except CatalogError as e:
                if e.status == 429 and not isinstance(e, OwnQuotaReached):
                    self._mark_exhausted()  # Anbieter meldet Limit → heute nicht mehr fragen
                if hit and e.status != 404:
                    return hit[0], {"cached": True, "stale": True, "fetched_at": hit[1].isoformat(),
                                    "notice": e.message}
                raise
            self._cache_put(key, value)
            return value, {"cached": False, "stale": False, "fetched_at": now.isoformat()}

    def _authorized_get(self, url: str) -> Any:
        """Abruf mit Authentifizierung; bei 401 einmal das andere Verfahren versuchen.

        Jeder Versuch wird vorher gezählt (lieber zu vorsichtig mit dem Kontingent).
        """
        last_err: CatalogError | None = None
        for scheme in list(self._schemes):
            self._reserve()
            try:
                raw = self.fetcher(url, headers={"Authorization": f"{scheme} {self._api_key}"})
            except AuthRejected as e:
                log.warning("golfcourseapi.com lehnt Schlüssel ab (Verfahren %s, Länge %d)",
                            scheme, len(self._api_key))
                last_err = e
                continue
            if self._schemes[0] != scheme:
                self._schemes = [scheme] + [x for x in self._schemes if x != scheme]
            return raw
        raise CatalogError(
            "golfcourseapi.com lehnt den API-Schlüssel ab. Bitte den Schlüssel in "
            "/etc/golf-auswertung/golf.env (GOLF_GOLFCOURSEAPI_KEY) prüfen und den Dienst neu starten.",
            last_err.status if last_err else 502)

    # Öffentliche Methoden ------------------------------------------------------

    def search(self, q: str) -> dict:
        query = " ".join(q.split())[:100]
        if len(query) < 3:
            raise CatalogError("Bitte mindestens 3 Zeichen eingeben (jede Suche kostet Kontingent).", 422)
        key = "search:" + query.lower()

        def parse(raw: Any) -> list[dict]:
            if not isinstance(raw, dict) or not isinstance(raw.get("courses"), list):
                raise CatalogError("golfcourseapi.com liefert ein unbekanntes Format.")
            out = []
            for item in raw["courses"][:200]:
                try:
                    s = Summary.model_validate(item)
                except ValidationError:
                    continue
                out.append({
                    "id": s.id, "club": s.club_name, "course_name": s.course_name,
                    "city": s.location.city, "state": s.location.state, "country": s.location.country,
                    "address": s.location.address,
                    "tees_male": s.tees.get("male", 0), "tees_female": s.tees.get("female", 0),
                })
            return out

        results, meta = self._cached_fetch(
            key, f"/v1/search?{urllib.parse.urlencode({'search_query': query})}", SEARCH_TTL, parse)
        # Deutsche Plätze zuerst, sonst Reihenfolge der API (Relevanz)
        results = sorted(results, key=lambda r: 0 if (r.get("country") or "").lower() in ("germany", "deutschland") else 1)
        return {"results": results, "total": len(results), **meta, "usage": self.usage()}

    def course(self, course_id: str) -> dict:
        cid = (course_id or "").lower()
        if not ID_RE.match(cid):
            raise CatalogError("Ungültige Platz-ID.", 422)

        def parse(raw: Any) -> dict:
            if not isinstance(raw, dict):
                raise CatalogError("golfcourseapi.com liefert ein unbekanntes Format.")
            # Laut Spezifikation das Objekt direkt; manche APIs verpacken es in {"course": …}
            body = raw.get("course") if isinstance(raw.get("course"), dict) else raw
            try:
                c = Course.model_validate(body)
            except ValidationError:
                raise CatalogError("Platzdaten von golfcourseapi.com sind unvollständig oder unplausibel.") from None
            if c.id != cid:
                raise CatalogError("golfcourseapi.com liefert einen anderen Platz als angefragt.")
            return to_import(c)

        value, meta = self._cached_fetch("course:" + cid, f"/v1/courses/{cid}", COURSE_TTL, parse)
        return value | {"stale": meta["stale"], "cached": meta["cached"], "notice": meta.get("notice"),
                        "usage": self.usage()}


# --- Umwandlung in unser Format ---------------------------------------------------

GENDERS = {"male": "m", "female": "w"}


def _tee_holes(tee: TeeBox, warnings: list[str]) -> list[dict] | None:
    n = len(tee.holes)
    if n == 0:
        warnings.append("Keine Lochdaten für diesen Abschlag – bitte von der Scorekarte ergänzen.")
        return None
    if n not in (9, 18):
        warnings.append(f"{n} Löcher in der Quelle – Lochdaten nicht übernommen.")
        return None
    if tee.number_of_holes and tee.number_of_holes != n:
        warnings.append(f"Quelle nennt {tee.number_of_holes} Löcher, liefert aber {n} – bitte prüfen.")
    pars = [h.par if h.par and 3 <= h.par <= 6 else None for h in tee.holes]
    sis = [h.handicap or None for h in tee.holes]
    if any(p is None for p in pars):
        warnings.append("Par fehlt oder ist ungültig bei einzelnen Löchern – bitte ergänzen.")
    valid_si = [s for s in sis if s is not None]
    if len(valid_si) != n or len(set(valid_si)) != n or any(s > 18 for s in valid_si):
        if valid_si:
            warnings.append("Loch-HCP in der Quelle unvollständig oder doppelt – bitte von der Scorekarte ergänzen.")
        else:
            warnings.append("Kein Loch-HCP in der Quelle – bitte von der Scorekarte ergänzen.")
        sis = [None] * n
    def length(h: Hole) -> int | None:
        m = h.meters or (round(h.yardage * 0.9144) if h.yardage else None)
        return m if m and 30 <= m <= 800 else None

    return [{"number": i + 1, "par": p, "stroke_index": s, "length_m": length(h)}
            for i, (h, p, s) in enumerate(zip(tee.holes, pars, sis))]


def to_import(c: Course) -> dict:
    warnings: list[str] = []
    tees = []
    for key, gender in GENDERS.items():
        for t in c.tees.get(key, [])[:20]:
            tw: list[str] = []
            holes = _tee_holes(t, tw)
            n = len(holes) if holes else (t.number_of_holes if t.number_of_holes in (9, 18) else 18)
            if t.number_of_holes == 27 or len(t.holes) == 27:
                tw.append("27 Löcher in der Quelle – bitte die gespielte 18-Loch-Kombination prüfen.")
            par = t.par_total
            if holes and all(h["par"] for h in holes):
                s = sum(h["par"] for h in holes)
                if par and par != s:
                    tw.append(f"Par gesamt ({par}) ≠ Summe der Loch-Pars ({s}) – Summe übernommen.")
                par = s
            if not t.course_rating or not t.slope_rating:
                tw.append("Course Rating oder Slope fehlt – bitte ergänzen.")
            elif not 55 <= t.slope_rating <= 155:
                tw.append(f"Slope {t.slope_rating} liegt außerhalb 55–155 – bitte prüfen.")
            if n == 9 and t.course_rating and t.course_rating > 45:
                tw.append("Course Rating sieht nach 18-Loch-Wert aus – bitte 9-Loch-Rating eintragen.")
            tees.append({
                "name": (t.tee_name or "Abschlag")[:40], "gender": gender, "holes_count": n,
                "course_rating": round(t.course_rating, 1) if t.course_rating else None,
                "slope_rating": t.slope_rating or None, "par": par,
                "unsicher": False, "warnings": tw, "holes": holes,
                "length_m": t.total_meters or (round(t.total_yards * 0.9144) if t.total_yards else None),
            })
    if not tees:
        warnings.append("Die Quelle enthält keine Abschläge.")
    names = [x for x in (c.club_name.strip(), c.course_name.strip()) if x]
    if len(names) == 2 and names[0].lower() == names[1].lower():
        names = names[:1]
    loc = c.location
    return {
        "source": SOURCE,
        "source_ref": f"{SOURCE}:{c.id}",
        "id": c.id,
        "club": c.club_name or c.course_name,
        "course_name": c.course_name or None,
        "ort": loc.city or "",
        "bundesland": loc.state or "",
        "country": loc.country or "",
        "address": loc.address or "",
        "suggested_name": " – ".join(names)[:150] or c.id,
        "holes_count": max((t["holes_count"] for t in tees), default=18),
        "holes": None,  # Lochdaten liegen hier je Abschlag vor
        "tees": tees,
        "warnings": warnings,
    }
