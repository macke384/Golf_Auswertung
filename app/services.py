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

    def __init__(self, status: int, message: str, details: list[str] | None = None,
                 code: str | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.details = details or []
        self.code = code  # maschinenlesbar, z. B. "tee_in_use" für Rückfragen in der Oberfläche


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


# --- Lesemodelle für die Oberfläche ----------------------------------------------


def result_dict(r: whs.RoundResult) -> dict:
    return {
        "playing_hcp": r.playing_hcp,
        "complete": r.complete,
        "gross_strokes": r.gross_strokes,
        "net_strokes": r.net_strokes,
        "gross_stableford": r.gross_stableford,
        "net_stableford": r.net_stableford,
        "holes": [h.__dict__ for h in r.holes],
    }


def preview_scorecard(db: Session, entry_id: int, values: list[int | None]) -> dict:
    """Rechnet eine Scorekarte aus, ohne zu speichern (Live-Anzeige beim Erfassen).

    Unmögliche Werte führen hier nicht zu einem Fehlerstatus, sondern werden
    als errors zurückgegeben, damit die Oberfläche sie neben dem Feld zeigt.
    """
    ctx = load_entry(db, entry_id)
    strokes = strokes_from_list(ctx.holes, values)
    check = plausibility.check_strokes(ctx.holes, strokes)
    if not check.ok:
        return {"errors": check.errors, "warnings": check.warnings, "result": None}
    r = whs.evaluate_round(ctx.holes, strokes, ctx.entry.playing_handicap)
    return {
        "errors": [],
        "warnings": check.warnings + plausibility.check_result(r).warnings,
        "result": result_dict(r),
    }


def entry_holes(db: Session, entry_id: int) -> dict:
    """Lochdaten und Vorgabeschläge einer Teilnahme, auch ohne Scorekarte."""
    ctx = load_entry(db, entry_id)
    recv = whs.strokes_received(ctx.holes, ctx.entry.playing_handicap)
    player = db.get(Player, ctx.entry.player_id)
    return {
        "entry_id": ctx.entry.id,
        "tournament_id": ctx.tournament.id,
        "tournament": ctx.tournament.name,
        "player": f"{player.first_name} {player.last_name}",  # type: ignore[union-attr]
        "tee": ctx.tee.name,
        "handicap_index": ctx.entry.handicap_index,
        "playing_hcp": ctx.entry.playing_handicap,
        "holes": [
            {"number": h.number, "par": h.par, "stroke_index": h.stroke_index,
             "received": recv[h.number]}
            for h in ctx.holes
        ],
    }


def tournament_detail(db: Session, tournament_id: int) -> dict:
    from app.models import Course, Team

    t = db.get(Tournament, tournament_id)
    if t is None:
        raise DomainError(404, "Turnier nicht gefunden.")
    course = db.get(Course, t.course_id)
    q = (
        select(TournamentEntry, Player, Tee, Team, Scorecard)
        .join(Player, Player.id == TournamentEntry.player_id)
        .join(Tee, Tee.id == TournamentEntry.tee_id)
        .outerjoin(Team, Team.id == TournamentEntry.team_id)
        .outerjoin(Scorecard, Scorecard.entry_id == TournamentEntry.id)
        .where(TournamentEntry.tournament_id == tournament_id)
        .order_by(Player.last_name, Player.first_name)
        .options(selectinload(Scorecard.scores), selectinload(Tee.holes))
    )
    entries = []
    for e, p, tee, team, card in db.execute(q).all():
        summary = None
        if card is not None:
            strokes = {s.hole_number: s.strokes for s in card.scores}
            r = whs.evaluate_round(round_holes(t, tee), strokes, e.playing_handicap)
            summary = {
                "status": card.status, "version": card.version, "source": card.source,
                "warnings": len(card.warnings or []), "complete": r.complete,
                "gross_strokes": r.gross_strokes, "net_stableford": r.net_stableford,
                "gross_stableford": r.gross_stableford,
            }
        entries.append({
            "id": e.id, "player_id": p.id, "player": f"{p.first_name} {p.last_name}",
            "team_id": e.team_id, "team": team.name if team else None,
            "tee_id": tee.id, "tee": f"{tee.name} ({tee.gender})",
            "handicap_index": e.handicap_index, "playing_hcp": e.playing_handicap,
            "scorecard": summary,
        })
    return {
        "id": t.id, "name": t.name, "played_on": t.played_on, "course_id": t.course_id,
        "course": course.name if course else None, "holes_count": t.holes_count,
        "start_hole": t.start_hole, "allowance_pct": t.allowance_pct, "entries": entries,
    }


def delete_entry(db: Session, entry_id: int) -> None:
    entry = db.scalar(select(TournamentEntry).where(TournamentEntry.id == entry_id).with_for_update())
    if entry is None:
        raise DomainError(404, "Teilnahme nicht gefunden.")
    card = db.scalar(select(Scorecard).where(Scorecard.entry_id == entry_id))
    if card is not None and card.status == "confirmed":
        raise DomainError(409, "Teilnahmen mit bestätigter Scorekarte können nicht gelöscht werden.")
    db.delete(entry)
    db.flush()


# --- Plätze bearbeiten / löschen ---------------------------------------------


def tee_usage(db: Session, tee_ids: list[int]) -> dict[int, int]:
    if not tee_ids:
        return {}
    rows = db.execute(
        select(TournamentEntry.tee_id, func.count())
        .where(TournamentEntry.tee_id.in_(tee_ids))
        .group_by(TournamentEntry.tee_id)
    ).all()
    return dict(rows)  # type: ignore[arg-type]


def update_tee(db: Session, tee_id: int, data: dict, force: bool = False) -> dict:
    """Ändert einen Abschlag vollständig.

    Wird der Abschlag schon in Meldungen verwendet, ist eine ausdrückliche
    Bestätigung (force) nötig; danach werden die eingefrorenen Spielvorgaben
    dieser Meldungen mit ihrem eingefrorenen HCP-Index neu berechnet.
    Bestätigte Scorekarten blockieren jede Änderung.
    """
    tee = db.scalar(
        select(Tee).options(selectinload(Tee.holes)).where(Tee.id == tee_id).with_for_update()
    )
    if tee is None:
        raise DomainError(404, "Abschlag nicht gefunden.")
    holes = [whs.Hole(h["number"], h["par"], h["stroke_index"]) for h in data["holes"]]
    check = plausibility.check_tee(
        holes, data["course_rating"], data["slope_rating"], data["par"], data["holes_count"]
    )
    if not check.ok:
        raise DomainError(422, "Abschlagsdaten sind ungültig.", check.errors)

    entries = db.scalars(
        select(TournamentEntry).where(TournamentEntry.tee_id == tee_id).with_for_update()
    ).all()
    rating_changed = (
        tee.holes_count != data["holes_count"]
        or Decimal(tee.course_rating) != Decimal(str(data["course_rating"]))
        or tee.slope_rating != data["slope_rating"]
        or tee.par != data["par"]
        or [(h.number, h.par, h.stroke_index) for h in tee.holes]
        != [(h["number"], h["par"], h["stroke_index"]) for h in sorted(data["holes"], key=lambda x: x["number"])]
    )
    if entries and rating_changed:
        confirmed = db.scalar(
            select(func.count()).select_from(Scorecard)
            .where(Scorecard.entry_id.in_([e.id for e in entries]), Scorecard.status == "confirmed")
        )
        if confirmed:
            raise DomainError(
                409,
                f"Der Abschlag gehört zu {confirmed} bestätigten Scorekarte(n) und kann nicht mehr "
                "geändert werden. Lege bei Bedarf einen neuen Abschlag an.",
            )
        if tee.holes_count != data["holes_count"]:
            raise DomainError(409, "Die Lochzahl kann nicht geändert werden, solange Spieler gemeldet sind.")
        if not force:
            raise DomainError(
                409,
                f"Der Abschlag wird in {len(entries)} Meldung(en) verwendet. Die Spielvorgaben "
                "dieser Meldungen werden neu berechnet.",
                code="tee_in_use",
            )

    tee.name = data["name"]
    tee.gender = data["gender"]
    tee.holes_count = data["holes_count"]
    tee.course_rating = data["course_rating"]
    tee.slope_rating = data["slope_rating"]
    tee.par = data["par"]
    # Löcher erst löschen, dann neu anlegen: sonst kollidieren Primärschlüssel
    # und der eindeutige Loch-HCP beim Tauschen von Werten.
    tee.holes.clear()
    db.flush()
    tee.holes.extend(
        TeeHole(number=h["number"], par=h["par"], stroke_index=h["stroke_index"], length_m=h.get("length_m"))
        for h in data["holes"]
    )
    try:
        db.flush()
    except IntegrityError:
        raise DomainError(409, "Es gibt auf diesem Platz schon einen Abschlag mit diesem Namen und Geschlecht.")

    recalculated = 0
    if rating_changed:
        for e in entries:
            t = db.get(Tournament, e.tournament_id)
            rh = round_holes(t, tee)  # type: ignore[arg-type]
            ch = whs.course_handicap(Decimal(e.handicap_index), round_rating(t, tee, rh))  # type: ignore[arg-type]
            new_ph = whs.playing_handicap(ch, t.allowance_pct)  # type: ignore[union-attr]
            if new_ph != e.playing_handicap:
                e.playing_handicap = new_ph
                recalculated += 1
        db.flush()
    return {"id": tee.id, "entries": len(entries), "recalculated": recalculated}


def delete_tee(db: Session, tee_id: int) -> None:
    tee = db.scalar(select(Tee).where(Tee.id == tee_id).with_for_update())
    if tee is None:
        raise DomainError(404, "Abschlag nicht gefunden.")
    used = tee_usage(db, [tee_id]).get(tee_id, 0)
    if used:
        raise DomainError(409, f"Der Abschlag wird in {used} Meldung(en) verwendet und kann nicht gelöscht werden.")
    db.delete(tee)
    try:
        db.flush()
    except IntegrityError:  # parallel gemeldet
        raise DomainError(409, "Der Abschlag wird inzwischen verwendet.")


def delete_course(db: Session, course_id: int) -> None:
    from app.models import Course

    course = db.scalar(select(Course).where(Course.id == course_id).with_for_update())
    if course is None:
        raise DomainError(404, "Platz nicht gefunden.")
    n = db.scalar(select(func.count()).select_from(Tournament).where(Tournament.course_id == course_id))
    if n:
        raise DomainError(409, f"Auf dem Platz gibt es {n} Turnier(e). Erst die Turniere löschen.")
    db.delete(course)
    try:
        db.flush()
    except IntegrityError:
        raise DomainError(409, "Der Platz wird inzwischen verwendet.")
