"""Eingabe-/Ausgabeschemas. Erste Validierungsstufe (Typen, Längen, Bereiche).

Fachliche Prüfungen (Summe der Pars, Plausibilität der Scores) folgen in
app.scoring.plausibility.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Gender = Literal["m", "w", "d"]
HI = Annotated[Decimal, Field(ge=Decimal("-10.0"), le=Decimal("54.0"), decimal_places=1)]
Strokes = Annotated[int, Field(ge=1, le=20, strict=True)] | None


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginIn(Strict):
    email: Annotated[str, StringConstraints(max_length=254)]
    password: Annotated[str, StringConstraints(max_length=1024)]


class UserIn(Strict):
    email: EmailStr
    password: Annotated[str, StringConstraints(min_length=12, max_length=1024)]
    role: Literal["admin", "editor", "viewer"] = "viewer"


class PlayerIn(Strict):
    first_name: Name
    last_name: Name
    gender: Gender
    dgv_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] | None = None
    handicap_index: HI | None = None


class TeamIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    player_ids: list[int] = Field(default_factory=list, max_length=200)


class HoleIn(Strict):
    number: Annotated[int, Field(ge=1, le=18)]
    par: Annotated[int, Field(ge=3, le=6)]
    stroke_index: Annotated[int, Field(ge=1, le=18)]
    length_m: Annotated[int, Field(ge=30, le=800)] | None = None


class TeeIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    gender: Gender
    holes_count: Literal[9, 18]
    course_rating: Annotated[Decimal, Field(ge=20, le=90, decimal_places=1)]
    slope_rating: Annotated[int, Field(ge=55, le=155)]
    par: Annotated[int, Field(ge=27, le=80)]
    holes: list[HoleIn] = Field(min_length=9, max_length=18)


class CourseIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    tees: list[TeeIn] = Field(min_length=1, max_length=12)


class TournamentIn(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
    played_on: date
    course_id: int
    holes_count: Literal[9, 18] = 18
    start_hole: Literal[1, 10] = 1
    allowance_pct: Annotated[int, Field(ge=1, le=100)] = 100


class EntryIn(Strict):
    player_id: int
    tee_id: int
    team_id: int | None = None
    handicap_index: HI | None = None  # sonst aktueller Index des Spielers


class ScorecardIn(Strict):
    strokes: list[Strokes] = Field(min_length=9, max_length=18)
    expected_version: int | None = None  # None = neu anlegen oder bewusst überschreiben
    status: Literal["draft", "submitted", "confirmed"] = "draft"


class Assignment(Strict):
    card: Annotated[int, Field(ge=0)]
    entry_id: int
    strokes: list[Strokes] | None = Field(default=None, min_length=9, max_length=18)


class ApplyImportIn(Strict):
    assignments: list[Assignment] = Field(min_length=1, max_length=500)


class PlayerPatch(Strict):
    first_name: Name | None = None
    last_name: Name | None = None
    gender: Gender | None = None
    dgv_id: Annotated[str, StringConstraints(strip_whitespace=True, max_length=32)] | None = None
    handicap_index: HI | None = None


class TeamMembersIn(Strict):
    player_ids: list[int] = Field(max_length=200)


class PreviewIn(Strict):
    strokes: list[Strokes] = Field(min_length=9, max_length=18)


class UserPatch(Strict):
    role: Literal["admin", "editor", "viewer"] | None = None
    is_active: bool | None = None
    password: Annotated[str, StringConstraints(min_length=12, max_length=1024)] | None = None


class CourseRename(Strict):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=150)]
