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
    if t.kind == "single":
        has_entry = db.scalar(select(func.count()).select_from(TournamentEntry)
                              .where(TournamentEntry.tournament_id == t.id))
        if has_entry:
            raise DomainError(409, "Eine Einzelrunde hat genau einen Spieler.")
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
        "kind": ctx.tournament.kind,
        "player_id": ctx.entry.player_id,
        "played_on": ctx.tournament.played_on,
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
        "kind": t.kind, "note": t.note,
    }


def delete_entry(db: Session, entry_id: int) -> None:
    entry = db.scalar(select(TournamentEntry).where(TournamentEntry.id == entry_id).with_for_update())
    if entry is None:
        raise DomainError(404, "Teilnahme nicht gefunden.")
    card = db.scalar(select(Scorecard).where(Scorecard.entry_id == entry_id))
    if card is not None and card.status == "confirmed":
        raise DomainError(409, "Teilnahmen mit bestätigter Scorekarte können nicht gelöscht werden.")
    t = db.get(Tournament, entry.tournament_id)
    if t is not None and t.kind == "single":
        db.delete(t)  # Einzelrunde ohne Spieler ergibt keinen Sinn; Meldung fällt per CASCADE mit
    else:
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


# --- Einzelrunden und persönliche Auswertung ------------------------------------


def create_single_round(db: Session, data: dict) -> TournamentEntry:
    """Legt eine Einzelrunde an: ein Turnier vom Typ 'single' mit genau einer Meldung.

    Allowance ist 100 % (WHS für Privatrunden). Alles in einer Transaktion.
    """
    from app.models import Course

    player = db.get(Player, data["player_id"])
    tee = db.get(Tee, data["tee_id"])
    if player is None or tee is None:
        raise DomainError(404, "Spieler oder Abschlag nicht gefunden.")
    course = db.get(Course, tee.course_id)
    holes_count = data["holes_count"]
    start_hole = data["start_hole"] if holes_count == 9 else 1
    t = Tournament(
        name=f"Einzelrunde {player.first_name} {player.last_name}"[:150],
        played_on=data["played_on"], course_id=tee.course_id, holes_count=holes_count,
        start_hole=start_hole, allowance_pct=100, kind="single", note=data.get("note") or None,
    )
    db.add(t)
    db.flush()
    if course is None:
        raise DomainError(404, "Platz nicht gefunden.")
    return create_entry(db, t.id, player.id, tee.id, None, data.get("handicap_index"))


def _round_row(t: Tournament, e: TournamentEntry, tee: Tee, course_name: str, card) -> dict:
    row = {
        "entry_id": e.id, "tournament_id": t.id, "kind": t.kind, "name": t.name, "note": t.note,
        "played_on": t.played_on, "course": course_name, "tee": f"{tee.name} ({tee.gender})",
        "holes_count": t.holes_count, "start_hole": t.start_hole,
        "handicap_index": e.handicap_index, "playing_hcp": e.playing_handicap,
        "scorecard": None,
    }
    if card is not None:
        strokes = {sc.hole_number: sc.strokes for sc in card.scores}
        r = whs.evaluate_round(round_holes(t, tee), strokes, e.playing_handicap)
        row["scorecard"] = {
            "status": card.status, "complete": r.complete,
            "gross_strokes": r.gross_strokes, "net_strokes": r.net_strokes,
            "gross_stableford": r.gross_stableford, "net_stableford": r.net_stableford,
        }
    return row


def player_rounds(db: Session, player_id: int) -> dict:
    """Alle Runden eines Spielers (Turniere und Einzelrunden) mit Kennzahlen."""
    from app.models import Course

    player = db.get(Player, player_id)
    if player is None:
        raise DomainError(404, "Spieler nicht gefunden.")
    q = (
        select(TournamentEntry, Tournament, Tee, Course.name, Scorecard)
        .join(Tournament, Tournament.id == TournamentEntry.tournament_id)
        .join(Tee, Tee.id == TournamentEntry.tee_id)
        .join(Course, Course.id == Tournament.course_id)
        .outerjoin(Scorecard, Scorecard.entry_id == TournamentEntry.id)
        .where(TournamentEntry.player_id == player_id)
        .order_by(Tournament.played_on.desc(), Tournament.id.desc())
        .options(selectinload(Tee.holes), selectinload(Scorecard.scores))
    )
    rounds = [_round_row(t, e, tee, cname, card) for e, t, tee, cname, card in db.execute(q).all()]

    def stats(holes: int) -> dict:
        done = [r["scorecard"] for r in rounds
                if r["holes_count"] == holes and r["scorecard"] and r["scorecard"]["complete"]]
        any_card = [r["scorecard"] for r in rounds if r["holes_count"] == holes and r["scorecard"]]
        avg = (lambda xs: round(sum(xs) / len(xs), 1) if xs else None)
        return {
            "rounds": len(any_card),
            "complete": len(done),
            "best_gross": min((c["gross_strokes"] for c in done), default=None),
            "avg_gross": avg([c["gross_strokes"] for c in done]),
            "best_net_stableford": max((c["net_stableford"] for c in any_card), default=None),
            "avg_net_stableford": avg([c["net_stableford"] for c in any_card]),
        }

    return {
        "player": {"id": player.id, "first_name": player.first_name, "last_name": player.last_name,
                   "gender": player.gender, "handicap_index": player.handicap_index,
                   "dgv_id": player.dgv_id},
        "stats": {"18": stats(18), "9": stats(9)},
        "rounds": rounds,
    }


def single_rounds(db: Session) -> list[dict]:
    from app.models import Course

    q = (
        select(TournamentEntry, Tournament, Tee, Course.name, Scorecard, Player)
        .join(Tournament, Tournament.id == TournamentEntry.tournament_id)
        .join(Tee, Tee.id == TournamentEntry.tee_id)
        .join(Course, Course.id == Tournament.course_id)
        .join(Player, Player.id == TournamentEntry.player_id)
        .outerjoin(Scorecard, Scorecard.entry_id == TournamentEntry.id)
        .where(Tournament.kind == "single")
        .order_by(Tournament.played_on.desc(), Tournament.id.desc())
        .limit(500)
        .options(selectinload(Tee.holes), selectinload(Scorecard.scores))
    )
    out = []
    for e, t, tee, cname, card, p in db.execute(q).all():
        row = _round_row(t, e, tee, cname, card)
        row["player_id"] = p.id
        row["player"] = f"{p.first_name} {p.last_name}"
        out.append(row)
    return out


# --- Turniere bearbeiten / löschen ---------------------------------------------


def _confirmed_cards(db: Session, tournament_id: int) -> int:
    return db.scalar(
        select(func.count()).select_from(Scorecard)
        .join(TournamentEntry, TournamentEntry.id == Scorecard.entry_id)
        .where(TournamentEntry.tournament_id == tournament_id, Scorecard.status == "confirmed")
    ) or 0


def update_tournament(db: Session, tournament_id: int, data: dict, force: bool = False) -> dict:
    """Ändert Turnierdaten.

    Name, Datum, Notiz: jederzeit. Handicap-Anteil: bei Meldungen nur nach
    Bestätigung (Spielvorgaben werden neu berechnet). Platz und Runde (Lochzahl,
    Startloch): nur ohne Meldungen. Bestätigte Scorekarten sperren alles, was
    Ergebnisse verändert.
    """
    t = db.scalar(select(Tournament).where(Tournament.id == tournament_id).with_for_update())
    if t is None:
        raise DomainError(404, "Turnier nicht gefunden.")
    entries = db.scalars(
        select(TournamentEntry).where(TournamentEntry.tournament_id == t.id).with_for_update()
    ).all()
    holes_count = data.get("holes_count", t.holes_count)
    start_hole = data.get("start_hole", t.start_hole) if holes_count == 9 else 1
    course_id = data.get("course_id", t.course_id)
    structure_changed = (holes_count, start_hole, course_id) != (t.holes_count, t.start_hole, t.course_id)
    allowance = data.get("allowance_pct", t.allowance_pct)
    allowance_changed = allowance != t.allowance_pct

    if entries and (structure_changed or allowance_changed):
        if _confirmed_cards(db, t.id):
            raise DomainError(409, "Das Turnier hat bestätigte Scorekarten. Handicap-Anteil, Platz und "
                                   "Runde können nicht mehr geändert werden.")
        if structure_changed:
            raise DomainError(409, "Platz, Lochzahl und Startloch können nur ohne Meldungen geändert "
                                   "werden. Erst die Meldungen entfernen.")
        if not force:
            raise DomainError(409, f"Es gibt {len(entries)} Meldung(en). Die Spielvorgaben werden mit "
                                   "dem neuen Handicap-Anteil neu berechnet.", code="tournament_in_use")

    if t.kind == "tournament" and "name" in data:
        t.name = data["name"]
    if "played_on" in data:
        t.played_on = data["played_on"]
    if "note" in data:
        t.note = data["note"] or None
    t.course_id, t.holes_count, t.start_hole = course_id, holes_count, start_hole
    t.allowance_pct = allowance
    try:
        db.flush()
    except IntegrityError:
        raise DomainError(409, "Es gibt an diesem Tag schon ein Turnier mit diesem Namen.")

    recalculated = 0
    if allowance_changed:
        for e in entries:
            tee = db.scalar(select(Tee).options(selectinload(Tee.holes)).where(Tee.id == e.tee_id))
            rh = round_holes(t, tee)  # type: ignore[arg-type]
            ch = whs.course_handicap(Decimal(e.handicap_index), round_rating(t, tee, rh))  # type: ignore[arg-type]
            ph = whs.playing_handicap(ch, t.allowance_pct)
            if ph != e.playing_handicap:
                e.playing_handicap = ph
                recalculated += 1
        db.flush()
    return {"id": t.id, "recalculated": recalculated}


def delete_tournament(db: Session, tournament_id: int) -> None:
    t = db.scalar(select(Tournament).where(Tournament.id == tournament_id).with_for_update())
    if t is None:
        raise DomainError(404, "Turnier nicht gefunden.")
    if _confirmed_cards(db, t.id):
        raise DomainError(409, "Das Turnier hat bestätigte Scorekarten und kann nicht gelöscht werden. "
                               "Erst den Status der Scorekarten zurücksetzen.")
    # Meldungen und Scorekarten werden per ON DELETE CASCADE mitgelöscht.
    db.delete(t)
    db.flush()


# --- Mannschaften bearbeiten / löschen -----------------------------------------


def delete_team(db: Session, team_id: int) -> None:
    from app.models import Team

    team = db.scalar(select(Team).where(Team.id == team_id).with_for_update())
    if team is None:
        raise DomainError(404, "Mannschaft nicht gefunden.")
    used = db.scalar(select(func.count()).select_from(TournamentEntry)
                     .where(TournamentEntry.team_id == team_id))
    if used:
        raise DomainError(409, f"Die Mannschaft ist in {used} Turniermeldung(en) eingetragen und "
                               "kann nicht gelöscht werden, sonst ändern sich alte Mannschaftswertungen.")
    db.delete(team)
    try:
        db.flush()
    except IntegrityError:
        raise DomainError(409, "Die Mannschaft wird inzwischen verwendet.")


def update_entry(db: Session, entry_id: int, data: dict) -> dict:
    """Mannschaftszuordnung einer Meldung ändern (z. B. falsch gemeldet)."""
    e = db.scalar(select(TournamentEntry).where(TournamentEntry.id == entry_id).with_for_update())
    if e is None:
        raise DomainError(404, "Meldung nicht gefunden.")
    if "team_id" in data:
        e.team_id = data["team_id"]
    db.flush()
    return {"id": e.id}
