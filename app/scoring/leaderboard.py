"""Ranglisten aus ausgewerteten Runden (reine Funktionen).

Gleichstand bei Stableford wird per Stechen (Countback) aufgelöst:
18 Loch: letzte 9, 6, 3, letztes Loch; 9 Loch: letzte 6, 3, letztes Loch —
jeweils in Spielreihenfolge. Bleibt es gleich, teilen sich die Spieler den Platz.
Vereinfachung: Stechen nutzt die Netto-Punkte pro Loch, nicht anteilige
Vorgaben wie beim Zählspiel-Stechen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.scoring.whs import RoundResult

Metric = Literal["net_stableford", "gross_stableford", "net_strokes", "gross_strokes"]


@dataclass(frozen=True)
class Row:
    entry_id: int
    player: str
    team_id: int | None
    result: RoundResult


def _segments(n: int) -> list[int]:
    return [9, 6, 3, 1] if n == 18 else [6, 3, 1]


def _sort_key(row: Row, metric: Metric) -> tuple:
    r = row.result
    pts_field = "net_points" if metric.startswith("net") else "gross_points"
    pts = [getattr(h, pts_field) for h in r.holes]  # holes liegen in Spielreihenfolge
    countback = tuple(sum(pts[-k:]) for k in _segments(len(pts)))
    if metric.endswith("stableford"):
        value = getattr(r, metric)
        return (-value, *(-c for c in countback))
    strokes = getattr(r, metric)
    # Unvollständige Runden (gestrichene Löcher) gehören nicht in die Zählspielwertung.
    return (0 if strokes is not None else 1, strokes or 0, *(-c for c in countback))


def rank(rows: list[Row], metric: Metric = "net_stableford") -> list[dict]:
    ordered = sorted(rows, key=lambda r: _sort_key(r, metric))
    out, prev_key, place = [], None, 0
    for i, row in enumerate(ordered, start=1):
        key = _sort_key(row, metric)
        if key != prev_key:
            place, prev_key = i, key
        value = getattr(row.result, metric)
        out.append(
            {
                "place": place if value is not None else None,
                "entry_id": row.entry_id,
                "player": row.player,
                "team_id": row.team_id,
                "value": value,
                "playing_hcp": row.result.playing_hcp,
            }
        )
    return out


def team_ranking(rows: list[Row], best_n: int | None = None) -> list[dict]:
    """Mannschaftswertung: Summe der besten N Netto-Stableford-Ergebnisse je Team."""
    by_team: dict[int, list[int]] = {}
    for r in rows:
        if r.team_id is not None:
            by_team.setdefault(r.team_id, []).append(r.result.net_stableford)
    teams = []
    for team_id, scores in by_team.items():
        counted = sorted(scores, reverse=True)[: best_n or len(scores)]
        teams.append({"team_id": team_id, "points": sum(counted), "counted": len(counted)})
    teams.sort(key=lambda t: -t["points"])
    place, prev = 0, None
    for i, t in enumerate(teams, start=1):
        if t["points"] != prev:
            place, prev = i, t["points"]
        t["place"] = place
    return teams
