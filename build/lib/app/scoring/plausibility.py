"""Plausibilitätsprüfung für Platzdaten und Scorekarten.

Zwei Stufen:
- errors:   Daten sind technisch/fachlich unmöglich → werden abgelehnt.
- warnings: Daten sind möglich, aber auffällig → werden gespeichert und
            zur Prüfung markiert (z. B. Hole-in-one auf Par 5).

Wichtig: Diese Prüfung erkennt Tippfehler, OCR-Fehler und kaputte Importe.
Sie ist KEIN Schutz gegen bewusst geschönte, aber plausible Scores — das
kann nur die Gegenzeichnung durch einen Zähler/Spielleiter leisten.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Sequence

from app.scoring.whs import Hole, RoundResult

HI_MIN, HI_MAX = Decimal("-10.0"), Decimal("54.0")
SLOPE_MIN, SLOPE_MAX = 55, 155
CR_RANGE = {18: (Decimal("50"), Decimal("85")), 9: (Decimal("25"), Decimal("43"))}
PAR_HOLE_MIN, PAR_HOLE_MAX = 3, 6
STROKES_MIN, STROKES_MAX = 1, 20


@dataclass
class Check:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def check_handicap_index(hi: Decimal) -> Check:
    c = Check()
    if not HI_MIN <= Decimal(hi) <= HI_MAX:
        c.errors.append(f"Handicap-Index {hi} liegt außerhalb von {HI_MIN}..{HI_MAX}.")
    return c


def check_tee(
    holes: Sequence[Hole], course_rating: Decimal, slope_rating: int, par: int, holes_count: int
) -> Check:
    c = Check()
    if holes_count not in (9, 18):
        c.errors.append("Ein Abschlag hat 9 oder 18 Löcher.")
        return c
    numbers = sorted(h.number for h in holes)
    if numbers != list(range(1, holes_count + 1)):
        c.errors.append(f"Lochnummern müssen 1..{holes_count} lückenlos sein, erhalten: {numbers}.")
    for h in holes:
        if not PAR_HOLE_MIN <= h.par <= PAR_HOLE_MAX:
            c.errors.append(f"Loch {h.number}: Par {h.par} ist ungültig.")
        if not 1 <= h.stroke_index <= 18:
            c.errors.append(f"Loch {h.number}: Loch-HCP {h.stroke_index} muss zwischen 1 und 18 liegen.")
    sis = [h.stroke_index for h in holes]
    if len(set(sis)) != len(sis):
        c.errors.append("Loch-HCP (Stroke Index) ist doppelt vergeben.")
    if sum(h.par for h in holes) != par:
        c.errors.append(f"Summe der Loch-Pars ({sum(h.par for h in holes)}) ≠ Platz-Par ({par}).")
    lo, hi = CR_RANGE[holes_count]
    if not lo <= Decimal(course_rating) <= hi:
        c.errors.append(f"Course Rating {course_rating} ist für {holes_count} Loch unplausibel.")
    if not SLOPE_MIN <= slope_rating <= SLOPE_MAX:
        c.errors.append(f"Slope Rating {slope_rating} muss zwischen {SLOPE_MIN} und {SLOPE_MAX} liegen.")
    if not c.errors and abs(Decimal(course_rating) - par) > Decimal(8) * holes_count / 18:
        c.warnings.append("Course Rating weicht stark vom Par ab — bitte Werte prüfen.")
    return c


def check_strokes(holes: Sequence[Hole], strokes: dict[int, int | None]) -> Check:
    """Prüft die Rohwerte einer Scorekarte, bevor gerechnet wird."""
    c = Check()
    expected = {h.number for h in holes}
    if set(strokes) != expected:
        missing = sorted(expected - set(strokes))
        extra = sorted(set(strokes) - expected)
        if missing:
            c.errors.append(f"Fehlende Löcher: {missing}.")
        if extra:
            c.errors.append(f"Löcher gehören nicht zur Runde: {extra}.")
        return c
    par = {h.number: h.par for h in holes}
    for n, s in sorted(strokes.items()):
        if s is None:
            continue
        if isinstance(s, bool) or not isinstance(s, int):
            c.errors.append(f"Loch {n}: Schlagzahl muss eine ganze Zahl sein.")
            continue
        if not STROKES_MIN <= s <= STROKES_MAX:
            c.errors.append(f"Loch {n}: {s} Schläge ist unmöglich ({STROKES_MIN}..{STROKES_MAX}).")
            continue
        if s == 1 and par[n] >= 4:
            c.warnings.append(f"Loch {n}: Hole-in-one auf Par {par[n]} — bitte bestätigen.")
        if s >= par[n] + 7:
            c.warnings.append(f"Loch {n}: {s} Schläge auf Par {par[n]} — Tippfehler?")
    played = [s for s in strokes.values() if s is not None]
    if len(played) >= 6 and len(set(played)) == 1:
        c.warnings.append("Alle Löcher haben denselben Wert — möglicher Erfassungs-/OCR-Fehler.")
    if strokes and not played:
        c.warnings.append("Keine einzige Schlagzahl erfasst.")
    return c


def check_result(result: RoundResult) -> Check:
    """Auffälligkeiten im ausgewerteten Ergebnis."""
    c = Check()
    n = len(result.holes)
    # 36 Netto-Punkte (18 Loch) = Spielen der Vorgabe. Deutlich mehr ist selten.
    if result.net_stableford > 2 * n + n // 2:  # > 45 bei 18, > 22 bei 9 Loch
        c.warnings.append(
            f"{result.net_stableford} Netto-Punkte sind sehr hoch — Handicap und Scores prüfen."
        )
    return c
