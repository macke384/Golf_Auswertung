"""WHS-Berechnungen (World Handicap System), reine Funktionen ohne DB.

Formeln:
  18 Loch: Course HCP = HI × (SR / 113) + (CR − Par)
   9 Loch: Course HCP = (HI / 2) × (SR9 / 113) + (CR9 − Par9)
  Playing HCP = Course HCP × Allowance
Gerundet wird jeweils auf eine ganze Zahl, x.5 numerisch aufwärts
(also +2.5 Plus-HCP = −2.5 → −2).

Stableford pro Loch: max(0, 2 + Par + Vorgabeschläge − Schläge).
Ein gestrichenes Loch (strokes = None) zählt 0 Punkte.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Sequence


def _round_half_up(x: Decimal) -> int:
    return int((x + Decimal("0.5")).to_integral_value(rounding=ROUND_FLOOR))


@dataclass(frozen=True)
class Hole:
    number: int
    par: int
    stroke_index: int  # Loch-HCP laut Scorekarte


@dataclass(frozen=True)
class TeeRating:
    course_rating: Decimal
    slope_rating: int
    par: int
    holes_count: int  # 9 oder 18 — für welche Lochzahl CR/SR gelten


def course_handicap(handicap_index: Decimal, tee: TeeRating) -> int:
    hi = Decimal(handicap_index)
    if tee.holes_count == 9:
        hi = hi / 2
    elif tee.holes_count != 18:
        raise ValueError("holes_count muss 9 oder 18 sein")
    value = hi * Decimal(tee.slope_rating) / Decimal(113) + (Decimal(tee.course_rating) - tee.par)
    return _round_half_up(value)


def playing_handicap(course_hcp: int, allowance_pct: int = 100) -> int:
    if not 0 < allowance_pct <= 100:
        raise ValueError("allowance_pct muss zwischen 1 und 100 liegen")
    return _round_half_up(Decimal(course_hcp) * Decimal(allowance_pct) / Decimal(100))


def strokes_received(holes: Sequence[Hole], playing_hcp: int) -> dict[int, int]:
    """Verteilt die Vorgabeschläge auf die gespielten Löcher.

    Die Löcher werden nach Stroke Index sortiert und neu durchnummeriert
    (Rang 1..n). Das funktioniert sowohl für 9-Loch-Karten mit SI 1–9 als
    auch für 9 Löcher eines 18-Loch-Platzes mit SI 1–18.
    Plus-Handicaps geben Schläge zurück, beginnend beim leichtesten Loch.
    """
    n = len(holes)
    if n == 0:
        return {}
    ranked = sorted(holes, key=lambda h: h.stroke_index)
    rank = {h.number: i + 1 for i, h in enumerate(ranked)}
    result: dict[int, int] = {}
    if playing_hcp >= 0:
        base, extra = divmod(playing_hcp, n)
        for h in holes:
            result[h.number] = base + (1 if rank[h.number] <= extra else 0)
    else:
        base, extra = divmod(-playing_hcp, n)
        for h in holes:
            # Leichteste Löcher (höchster Rang) geben zuerst zurück.
            result[h.number] = -(base + (1 if rank[h.number] > n - extra else 0))
    return result


def stableford_points(par: int, strokes: int | None, received: int = 0) -> int:
    if strokes is None:
        return 0
    return max(0, 2 + par + received - strokes)


@dataclass(frozen=True)
class HoleResult:
    number: int
    par: int
    strokes: int | None
    received: int
    gross_points: int
    net_points: int


@dataclass(frozen=True)
class RoundResult:
    holes: list[HoleResult]
    playing_hcp: int
    complete: bool  # alle Löcher zu Ende gespielt
    gross_strokes: int | None  # nur bei vollständiger Runde
    net_strokes: int | None
    gross_stableford: int
    net_stableford: int


def evaluate_round(
    holes: Sequence[Hole], strokes: dict[int, int | None], playing_hcp: int
) -> RoundResult:
    numbers = {h.number for h in holes}
    if set(strokes) != numbers:
        raise ValueError(
            f"Scorekarte passt nicht zu den Löchern: erwartet {sorted(numbers)}, erhalten {sorted(strokes)}"
        )
    recv = strokes_received(holes, playing_hcp)
    out: list[HoleResult] = []
    for h in sorted(holes, key=lambda x: x.number):
        s = strokes[h.number]
        out.append(
            HoleResult(
                number=h.number,
                par=h.par,
                strokes=s,
                received=recv[h.number],
                gross_points=stableford_points(h.par, s, 0),
                net_points=stableford_points(h.par, s, recv[h.number]),
            )
        )
    complete = all(r.strokes is not None for r in out)
    gross = sum(r.strokes for r in out) if complete else None  # type: ignore[misc]
    return RoundResult(
        holes=out,
        playing_hcp=playing_hcp,
        complete=complete,
        gross_strokes=gross,
        net_strokes=(gross - playing_hcp) if gross is not None else None,
        gross_stableford=sum(r.gross_points for r in out),
        net_stableford=sum(r.net_points for r in out),
    )
