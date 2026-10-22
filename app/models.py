"""Datenmodell.

Grundsätze:
- Eindeutigkeit wird in der Datenbank erzwungen (UNIQUE/CHECK), nicht nur im
  Code. Nur so sind parallele Requests und doppelte Importe sicher.
- Par/Loch-HCP hängen am Abschlag (tee_holes), weil sie sich je nach
  Abschlag/Geschlecht unterscheiden können.
- Handicap-Index und Playing HCP werden pro Turnierteilnahme eingefroren,
  damit alte Ergebnisse sich nicht ändern, wenn sich das Handicap ändert.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="viewer")
    is_active: Mapped[bool] = mapped_column(default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("role IN ('admin','editor','viewer')", name="ck_users_role"),
        CheckConstraint("email = lower(email)", name="ck_users_email_lower"),
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    # Nur der Hash des Tokens wird gespeichert; ein DB-Leak gibt keine Sessions preis.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship()


class Player(Base):
    __tablename__ = "players"
    id: Mapped[int] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str] = mapped_column(String(1))
    dgv_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    handicap_index: Mapped[Decimal | None] = mapped_column(Numeric(3, 1))
    __table_args__ = (
        CheckConstraint("gender IN ('m','w','d')", name="ck_players_gender"),
        CheckConstraint("handicap_index BETWEEN -10.0 AND 54.0", name="ck_players_hi"),
    )


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)


class TeamMember(Base):
    __tablename__ = "team_members"
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id", ondelete="CASCADE"), primary_key=True)


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150), unique=True)
    # Herkunft bei Import aus einem Katalog, z. B. "handicap.report:<id>" (verhindert Doppelimport)
    source_ref: Mapped[str | None] = mapped_column(String(200), unique=True)
    tees: Mapped[list["Tee"]] = relationship(back_populates="course", cascade="all, delete-orphan")


class Tee(Base):
    __tablename__ = "tees"
    id: Mapped[int] = mapped_column(primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(40))  # z. B. "Gelb"
    gender: Mapped[str] = mapped_column(String(1))
    holes_count: Mapped[int] = mapped_column(SmallInteger)
    course_rating: Mapped[Decimal] = mapped_column(Numeric(4, 1))
    slope_rating: Mapped[int] = mapped_column(SmallInteger)
    par: Mapped[int] = mapped_column(SmallInteger)
    course: Mapped[Course] = relationship(back_populates="tees")
    holes: Mapped[list["TeeHole"]] = relationship(
        cascade="all, delete-orphan", order_by="TeeHole.number"
    )
    __table_args__ = (
        UniqueConstraint("course_id", "name", "gender", name="uq_tees_course_name_gender"),
        CheckConstraint("holes_count IN (9,18)", name="ck_tees_holes"),
        CheckConstraint("slope_rating BETWEEN 55 AND 155", name="ck_tees_slope"),
        CheckConstraint("gender IN ('m','w','d')", name="ck_tees_gender"),
    )


class TeeHole(Base):
    __tablename__ = "tee_holes"
    tee_id: Mapped[int] = mapped_column(ForeignKey("tees.id", ondelete="CASCADE"), primary_key=True)
    number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    par: Mapped[int] = mapped_column(SmallInteger)
    stroke_index: Mapped[int] = mapped_column(SmallInteger)
    length_m: Mapped[int | None] = mapped_column(SmallInteger)
    __table_args__ = (
        UniqueConstraint("tee_id", "stroke_index", name="uq_tee_holes_si"),
        CheckConstraint("number BETWEEN 1 AND 18", name="ck_tee_holes_number"),
        CheckConstraint("par BETWEEN 3 AND 6", name="ck_tee_holes_par"),
        CheckConstraint("stroke_index BETWEEN 1 AND 18", name="ck_tee_holes_si"),
    )


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    played_on: Mapped[date] = mapped_column(Date)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id"))
    holes_count: Mapped[int] = mapped_column(SmallInteger)
    start_hole: Mapped[int] = mapped_column(SmallInteger, default=1)  # 1 oder 10 bei 9 Loch
    allowance_pct: Mapped[int] = mapped_column(SmallInteger, default=100)
    # "tournament" = Turnier/Spieltag; "single" = Einzelrunde eines Spielers
    # (privat/Training), technisch ein Turnier mit genau einer Meldung.
    kind: Mapped[str] = mapped_column(String(12), default="tournament", server_default="tournament")
    note: Mapped[str | None] = mapped_column(String(300))
    __table_args__ = (
        # Turniernamen sind pro Tag eindeutig; Einzelrunden nicht (gleicher Spieler, zwei Runden).
        Index("uq_tournaments_name_date", "name", "played_on", unique=True,
              postgresql_where=text("kind = 'tournament'")),
        CheckConstraint("kind IN ('tournament','single')", name="ck_tournaments_kind"),
        CheckConstraint("holes_count IN (9,18)", name="ck_tournaments_holes"),
        CheckConstraint("start_hole IN (1,10)", name="ck_tournaments_start"),
        CheckConstraint("holes_count = 9 OR start_hole = 1", name="ck_tournaments_start_18"),
        CheckConstraint("allowance_pct BETWEEN 1 AND 100", name="ck_tournaments_allowance"),
    )


class TournamentEntry(Base):
    """Teilnahme eines Spielers an einem Turnier (optional für eine Mannschaft)."""

    __tablename__ = "tournament_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id", ondelete="CASCADE"))
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"))
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))
    tee_id: Mapped[int] = mapped_column(ForeignKey("tees.id"))
    handicap_index: Mapped[Decimal] = mapped_column(Numeric(3, 1))  # eingefroren
    playing_handicap: Mapped[int] = mapped_column(SmallInteger)  # eingefroren
    __table_args__ = (UniqueConstraint("tournament_id", "player_id", name="uq_entries_player"),)


class Scorecard(Base):
    __tablename__ = "scorecards"
    id: Mapped[int] = mapped_column(primary_key=True)
    entry_id: Mapped[int] = mapped_column(
        ForeignKey("tournament_entries.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[str] = mapped_column(String(16), default="draft")
    source: Mapped[str] = mapped_column(String(16), default="manual")
    import_id: Mapped[int | None] = mapped_column(ForeignKey("imports.id", ondelete="SET NULL"))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    # Optimistisches Locking: parallele Bearbeitung überschreibt nichts still.
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    scores: Mapped[list["HoleScore"]] = relationship(
        cascade="all, delete-orphan", order_by="HoleScore.hole_number"
    )
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (
        CheckConstraint("status IN ('draft','submitted','confirmed')", name="ck_scorecards_status"),
        CheckConstraint("source IN ('manual','csv','pdf','photo','api')", name="ck_scorecards_source"),
    )


class HoleScore(Base):
    __tablename__ = "hole_scores"
    scorecard_id: Mapped[int] = mapped_column(
        ForeignKey("scorecards.id", ondelete="CASCADE"), primary_key=True
    )
    hole_number: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    strokes: Mapped[int | None] = mapped_column(SmallInteger)  # NULL = gestrichen
    __table_args__ = (
        CheckConstraint("hole_number BETWEEN 1 AND 18", name="ck_hole_scores_number"),
        CheckConstraint("strokes IS NULL OR strokes BETWEEN 1 AND 20", name="ck_hole_scores_strokes"),
    )


class Import(Base):
    """Eine hochgeladene Datei (CSV/PDF/Foto) und ihr Parse-Ergebnis.

    Importe werden nie direkt übernommen: sie landen als 'needs_review' und
    werden erst nach Prüfung durch einen Editor auf Scorekarten angewendet.
    """

    __tablename__ = "imports"
    id: Mapped[int] = mapped_column(primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(8))
    size_bytes: Mapped[int] = mapped_column(Integer)
    original_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="needs_review")
    parsed: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("kind IN ('csv','pdf','photo')", name="ck_imports_kind"),
        CheckConstraint(
            "status IN ('needs_review','failed','applied','rejected')", name="ck_imports_status"
        ),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="ck_imports_sha256"),
    )


class CatalogCache(Base):
    """Zwischengespeicherte Antworten externer Platzkataloge.

    Grund für die Tabelle: golfcourseapi.com erlaubt im Free-Tarif nur ~35
    Anfragen pro Tag. Ein Speicher, der Neustarts übersteht, spart Kontingent.
    """

    __tablename__ = "catalog_cache"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApiUsage(Base):
    """Verbrauchte Anfragen je externer Quelle und Tag (UTC) – für Kontingentgrenzen."""

    __tablename__ = "api_usage"
    source: Mapped[str] = mapped_column(String(30), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (CheckConstraint("count >= 0", name="ck_api_usage_count"),)
