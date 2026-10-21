"""Fachlogik mit Datenbankzugriff: Teilnahmen, Scorekarten, Importe, Ranglisten.

Die API-Schicht ruft nur diese Funktionen auf; hier liegen Transaktionen
und die Verbindung zwischen Datenmodell und den reinen Berechnungen.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.models import (
    HoleScore,
    Import,
    Player,
    Scorecard,
    Tee,
    TeeHole,
    Tournament,
    TournamentEntry,
)
from app.scoring import leaderboard, plausibility, whs


class DomainError(Exception):
    """Fachlicher Fehler, der dem Nutzer verständlich gemeldet werden kann."""

    def __init__(self, status: int, message: str, details: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or []


# --- Platz / Runde -----------------------------------------------------------


def round_holes(t: Tournament, tee: Tee) -> list[whs.Hole]:
    """Die Löcher, die in diesem Turnier auf diesem Abschlag gespielt werden."""
    if t.holes_count == 18:
        if tee.holes_count != 18:
            raise DomainError(422, "Ein 18-Loch-Turnier braucht einen 18-Loch-Abschlag.")
        numbers = range(1, 19)
    elif tee.holes_count == 9:
        if t.start_hole != 1:
            raise DomainError(422, "Ein 9-Loch-Platz hat keine Löcher 10–18.")
        numbers = range(1, 10)
    else:
        numbers = range(t.start_hole, t.start_hole + 9)
    by_no = {h.number: h for h in tee.holes}
    return [whs.Hole(n, by_no[n].par, by_no[n].stroke_index) for n in numbers]


def round_rating(t: Tournament, tee: Tee, holes: list[whs.Hole]) -> whs.TeeRating:
    if t.holes_count == tee.holes_count:
        return whs.TeeRating(tee.course_rating, tee.slope_rating, tee.par, tee.holes_count)
    # 9 Loch auf einem 18-Loch-Abschlag. Vereinfachung: halbes CR, gleiches SR.
    # TODO: separate 9-Loch-Ratings (vorne/hinten) erfassen, sobald verfügbar.
    return whs.TeeRating(
        (Decimal(tee.course_rating) / 2).quantize(Decimal("0.1")),
        tee.slope_rating,
        sum(h.par for h in holes),
        9,
    )


def create_tee(db: Session, course_id: int, data: dict) -> Tee:
    holes = [whs.Hole(h["number"], h["par"], h["stroke_index"]) for h in data["holes"]]
    check = plausibility.check_tee(
        holes, data["course_rating"], data["slope_rating"], data["par"], data["holes_count"]
    )
    if not check.ok:
        raise DomainError(422, "Abschlagsdaten sind ungültig.", check.errors)
    tee = Tee(
        course_id=course_id,
        name=data["name"],
        gender=data["gender"],
        holes_count=data["holes_count"],
        course_rating=data["course_rating"],
        slope_rating=data["slope_rating"],
        par=data["par"],
        holes=[
            TeeHole(number=h["number"], par=h["par"], stroke_index=h["stroke_index"], length_m=h.get("length_m"))
            for h in data["holes"]
        ],
    )
    db.add(tee)
    db.flush()
    return tee


# --- Teilnahmen --------------------------------------------------------------


def create_entry(
    db: Session, tournament_id: int, player_id: int, tee_id: int, team_id: int | None,
    handicap_index: Decimal | None = None,
) -> TournamentEntry:
    t = db.get(Tournament, tournament_id)
    player = db.get(Player, player_id)
    tee = db.scalar(select(Tee).options(selectinload(Tee.holes)).where(Tee.id == tee_id))
    if t is None or player is None or tee is None:
        raise DomainError(404, "Turnier, Spieler oder Abschlag nicht gefunden.")
    if tee.course_id != t.course_id:
        raise DomainError(422, "Der Abschlag gehört nicht zum Platz des Turniers.")
    hi = handicap_index if handicap_index is not None else player.handicap_index
    if hi is None:
        raise DomainError(422, "Für den Spieler ist kein Handicap-Index hinterlegt.")
    c = plausibility.check_handicap_index(hi)
    if not c.ok:
        raise DomainError(422, "Ungültiger Handicap-Index.", c.errors)
    holes = round_holes(t, tee)
    ch = whs.course_handicap(Decimal(hi), round_rating(t, tee, holes))
    entry = TournamentEntry(
        tournament_id=t.id, player_id=player.id, team_id=team_id, tee_id=tee.id,
        handicap_index=hi, playing_handicap=whs.playing_handicap(ch, t.allowance_pct),
    )
    try:
        with db.begin_nested():  # Savepoint: äußere Transaktion bleibt nutzbar
            db.add(entry)
            db.flush()
    except IntegrityError:
        raise DomainError(409, "Der Spieler ist bereits für dieses Turnier gemeldet.")
    return entry


@dataclass
class EntryContext:
    entry: TournamentEntry
    tournament: Tournament
    tee: Tee
    holes: list[whs.Hole]


def load_entry(db: Session, entry_id: int, for_update: bool = False) -> EntryContext:
    q = select(TournamentEntry).where(TournamentEntry.id == entry_id)
    if for_update:
        # Serialisiert parallele Erstanlagen einer Scorekarte für dieselbe Teilnahme.
        q = q.with_for_update()
    entry = db.scalar(q)
    if entry is None:
        raise DomainError(404, "Teilnahme nicht gefunden.")
    t = db.get(Tournament, entry.tournament_id)
    tee = db.scalar(select(Tee).options(selectinload(Tee.holes)).where(Tee.id == entry.tee_id))
    return EntryContext(entry, t, tee, round_holes(t, tee))  # type: ignore[arg-type]


# --- Scorekarten -------------------------------------------------------------


def strokes_from_list(holes: list[whs.Hole], values: list[int | None]) -> dict[int, int | None]:
    if len(values) != len(holes):
        raise DomainError(
            422, f"Die Runde hat {len(holes)} Löcher, die Scorekarte {len(values)} Werte."
        )
    # Reihenfolge der Werte = Spielreihenfolge der Löcher (z. B. 10..18).
    return {h.number: v for h, v in zip(holes, values)}


def save_scorecard(
    db: Session,
    entry_id: int,
    values: list[int | None],
    *,
    source: str = "manual",
    expected_version: int | None = None,
    import_id: int | None = None,
    status: str = "draft",
) -> Scorecard:
    """Legt eine Scorekarte an oder ersetzt sie vollständig (idempotent).

    expected_version schützt vor dem stillen Überschreiben paralleler
    Änderungen: Weicht die Version ab, gibt es 409 statt Datenverlust.
    Der Aufrufer committet.
    """
    ctx = load_entry(db, entry_id, for_update=True)
    strokes = strokes_from_list(ctx.holes, values)
    check = plausibility.check_strokes(ctx.holes, strokes)
    if not check.ok:
        raise DomainError(422, "Scorekarte enthält unmögliche Werte.", check.errors)
    result = whs.evaluate_round(ctx.holes, strokes, ctx.entry.playing_handicap)
    warnings = check.warnings + plausibility.check_result(result).warnings

    card = db.scalar(
        select(Scorecard).options(selectinload(Scorecard.scores)).where(Scorecard.entry_id == entry_id)
    )
    if card is None:
        if expected_version not in (None, 0):
            raise DomainError(409, "Scorekarte existiert noch nicht.")
        card = Scorecard(entry_id=entry_id)
        db.add(card)
    else:
        if expected_version is not None and expected_version != card.version:
            raise DomainError(
                409, "Die Scorekarte wurde zwischenzeitlich geändert. Bitte neu laden."
            )
        if card.status == "confirmed" and source != "manual":
            raise DomainError(409, "Bestätigte Scorekarten werden durch Importe nicht überschrieben.")
    card.source = source
    card.import_id = import_id
    card.status = status
    card.warnings = warnings
    card.scores = [HoleScore(hole_number=n, strokes=s) for n, s in strokes.items()]
    card.updated_at = func.now()  # erzwingt UPDATE → Version wird erhöht
    db.flush()
    return card


def scorecard_result(ctx: EntryContext, card: Scorecard) -> whs.RoundResult:
    strokes = {s.hole_number: s.strokes for s in card.scores}
    return whs.evaluate_round(ctx.holes, strokes, ctx.entry.playing_handicap)


def tournament_leaderboard(db: Session, tournament_id: int, metric: str, team_best_n: int | None):
    t = db.get(Tournament, tournament_id)
    if t is None:
        raise DomainError(404, "Turnier nicht gefunden.")
    rows_q = (
        select(TournamentEntry, Player, Scorecard)
        .join(Player, Player.id == TournamentEntry.player_id)
        .join(Scorecard, Scorecard.entry_id == TournamentEntry.id)
        .where(TournamentEntry.tournament_id == tournament_id)
        .options(selectinload(Scorecard.scores))
    )
    tees: dict[int, Tee] = {}
    rows = []
    for entry, player, card in db.execute(rows_q).all():
        if entry.tee_id not in tees:
            tees[entry.tee_id] = db.scalar(
                select(Tee).options(selectinload(Tee.holes)).where(Tee.id == entry.tee_id)
            )
        holes = round_holes(t, tees[entry.tee_id])
        strokes = {s.hole_number: s.strokes for s in card.scores}
        res = whs.evaluate_round(holes, strokes, entry.playing_handicap)
        rows.append(
            leaderboard.Row(entry.id, f"{player.first_name} {player.last_name}", entry.team_id, res)
        )
    return {
        "tournament_id": t.id,
        "metric": metric,
        "players": leaderboard.rank(rows, metric),  # type: ignore[arg-type]
        "teams": leaderboard.team_ranking(rows, team_best_n),
    }


# --- Importe -----------------------------------------------------------------


def apply_import(db: Session, import_id: int, assignments: list[dict]) -> list[Scorecard]:
    """Überträgt geprüfte Import-Karten auf Teilnahmen — alles oder nichts.

    assignments: [{"card": <Index in parsed.cards>, "entry_id": int,
                   "strokes": optional korrigierte Werte}]
    """
    imp = db.scalar(select(Import).where(Import.id == import_id).with_for_update())
    if imp is None:
        raise DomainError(404, "Import nicht gefunden.")
    if imp.status != "needs_review":
        raise DomainError(409, f"Import ist bereits '{imp.status}'.")
    cards = (imp.parsed or {}).get("cards", [])
    seen_entries: set[int] = set()
    out = []
    for a in assignments:
        idx = a["card"]
        if not 0 <= idx < len(cards):
            raise DomainError(422, f"Karte {idx} existiert im Import nicht.")
        if a["entry_id"] in seen_entries:
            raise DomainError(422, "Eine Teilnahme wurde mehrfach zugeordnet.")
        seen_entries.add(a["entry_id"])
        values = a.get("strokes") or cards[idx]["strokes"]
        out.append(
            save_scorecard(db, a["entry_id"], values, source=imp.kind, import_id=imp.id)
        )
    imp.status = "applied"
    db.flush()
    return out
