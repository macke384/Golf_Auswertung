"""HTTP-API. Dünne Schicht: Eingaben validieren, Services aufrufen, Fehler übersetzen."""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from app import schemas, security, services
from app.config import Settings, get_settings
from app.db import get_db, get_sessionmaker
from app.imports import service as import_service
from app.imports.upload import UploadError
from app.models import Course, Import, Player, Scorecard, Team, TeamMember, Tee, Tournament, User

log = logging.getLogger("golf")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    try:  # Aufräumen nach Absturz/Neustart
        with get_sessionmaker()() as db:
            import_service.sweep(db, settings)
    except OperationalError:
        log.warning("Datenbank beim Start nicht erreichbar; Aufräumen übersprungen.")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    prod = settings.env == "production"
    app = FastAPI(
        title="Golf_Auswertung",
        lifespan=lifespan,
        # In Production keine öffentliche API-Doku (keine unnötige Preisgabe).
        docs_url=None if prod else "/docs",
        redoc_url=None,
        openapi_url=None if prod else "/openapi.json",
    )

    # --- Middleware ----------------------------------------------------------

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # CSRF-Schutz zusätzlich zu SameSite=Strict: schreibende Requests nur
        # von derselben Origin (moderne Browser senden Sec-Fetch-Site).
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            site = request.headers.get("sec-fetch-site")
            if site is not None and site not in ("same-origin", "none"):
                return JSONResponse({"detail": "Fremde Herkunft abgelehnt."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    # --- Fehlerbehandlung: kontrolliert, ohne interne Details --------------------

    @app.exception_handler(services.DomainError)
    async def _domain(_: Request, e: services.DomainError):
        return JSONResponse({"detail": e.message, "errors": e.details}, status_code=e.status)

    @app.exception_handler(UploadError)
    async def _upload(_: Request, e: UploadError):
        return JSONResponse({"detail": e.message}, status_code=e.status)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, e: RequestValidationError):
        errors = [
            {"field": ".".join(str(p) for p in err["loc"][1:]), "message": err["msg"]}
            for err in e.errors()
        ]
        return JSONResponse({"detail": "Ungültige Eingabe.", "errors": errors}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, e: IntegrityError):
        log.info("Integrity conflict: %s", e.orig.__class__.__name__ if e.orig else e)
        return JSONResponse(
            {"detail": "Konflikt: Datensatz existiert bereits oder verweist auf Unbekanntes."},
            status_code=409,
        )

    @app.exception_handler(StaleDataError)
    async def _stale(_: Request, e: StaleDataError):
        return JSONResponse(
            {"detail": "Daten wurden zwischenzeitlich geändert. Bitte neu laden."}, status_code=409
        )

    @app.exception_handler(OperationalError)
    async def _db_down(_: Request, e: OperationalError):
        log.error("Datenbankfehler: %s", e.__class__.__name__)
        return JSONResponse(
            {"detail": "Datenbank vorübergehend nicht erreichbar."},
            status_code=503,
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        ref = uuid.uuid4().hex[:8]
        log.exception("Unerwarteter Fehler [%s]", ref)
        return JSONResponse({"detail": f"Interner Fehler (Referenz {ref})."}, status_code=500)

    # --- Health --------------------------------------------------------------

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # --- Auth ----------------------------------------------------------------

    @app.post("/auth/login")
    def login(
        body: schemas.LoginIn,
        response: Response,
        db: Session = Depends(get_db),
        s: Settings = Depends(get_settings),
    ):
        user = security.authenticate(db, s, body.email, body.password)
        if user is None:
            # Gleiche Meldung für "unbekannt", "falsch" und "gesperrt".
            raise HTTPException(401, "Anmeldung fehlgeschlagen.")
        token = security.create_session(db, s, user)
        response.set_cookie(
            security.COOKIE, token, httponly=True, secure=s.cookie_secure, samesite="strict",
            max_age=s.session_ttl_hours * 3600, path="/",
        )
        return {"email": user.email, "role": user.role}

    @app.post("/auth/logout", status_code=204)
    def logout(request: Request, response: Response, db: Session = Depends(get_db),
               s: Settings = Depends(get_settings)):
        security.end_session(db, s, request.cookies.get(security.COOKIE))
        response.delete_cookie(security.COOKIE, path="/")

    @app.get("/auth/me")
    def me(user: User = Depends(security.current_user)):
        return {"email": user.email, "role": user.role}

    @app.post("/users", status_code=201)
    def create_user(body: schemas.UserIn, db: Session = Depends(get_db),
                    _: User = Depends(security.require("admin"))):
        u = User(email=body.email.lower(), password_hash=security.hash_password(body.password),
                 role=body.role)
        db.add(u)
        db.commit()
        return {"id": u.id, "email": u.email, "role": u.role}

    # --- Stammdaten ------------------------------------------------------------

    editor = Depends(security.require("editor"))
    viewer = Depends(security.require("viewer"))

    @app.post("/players", status_code=201)
    def create_player(body: schemas.PlayerIn, db: Session = Depends(get_db), _: User = editor):
        p = Player(**body.model_dump())
        db.add(p)
        db.commit()
        return {"id": p.id}

    @app.get("/players")
    def list_players(db: Session = Depends(get_db), _: User = viewer):
        rows = db.scalars(select(Player).order_by(Player.last_name, Player.first_name))
        return [
            {"id": p.id, "first_name": p.first_name, "last_name": p.last_name, "gender": p.gender,
             "dgv_id": p.dgv_id, "handicap_index": p.handicap_index}
            for p in rows
        ]

    @app.post("/teams", status_code=201)
    def create_team(body: schemas.TeamIn, db: Session = Depends(get_db), _: User = editor):
        t = Team(name=body.name)
        db.add(t)
        db.flush()
        for pid in set(body.player_ids):
            db.add(TeamMember(team_id=t.id, player_id=pid))
        db.commit()
        return {"id": t.id}

    @app.post("/courses", status_code=201)
    def create_course(body: schemas.CourseIn, db: Session = Depends(get_db), _: User = editor):
        c = Course(name=body.name)
        db.add(c)
        db.flush()
        tee_ids = [services.create_tee(db, c.id, t.model_dump()).id for t in body.tees]
        db.commit()
        return {"id": c.id, "tee_ids": tee_ids}

    @app.get("/courses/{course_id}")
    def get_course(course_id: int, db: Session = Depends(get_db), _: User = viewer):
        c = db.scalar(
            select(Course).options(selectinload(Course.tees).selectinload(Tee.holes))
            .where(Course.id == course_id)
        )
        if c is None:
            raise HTTPException(404, "Platz nicht gefunden.")
        return {
            "id": c.id, "name": c.name,
            "tees": [
                {"id": t.id, "name": t.name, "gender": t.gender, "holes_count": t.holes_count,
                 "course_rating": t.course_rating, "slope_rating": t.slope_rating, "par": t.par,
                 "holes": [{"number": h.number, "par": h.par, "stroke_index": h.stroke_index,
                            "length_m": h.length_m} for h in t.holes]}
                for t in c.tees
            ],
        }

    @app.post("/tournaments", status_code=201)
    def create_tournament(body: schemas.TournamentIn, db: Session = Depends(get_db),
                          _: User = editor):
        if body.holes_count == 18 and body.start_hole != 1:
            raise HTTPException(422, "18-Loch-Turniere beginnen an Loch 1.")
        t = Tournament(**body.model_dump())
        db.add(t)
        db.commit()
        return {"id": t.id}

    @app.post("/tournaments/{tournament_id}/entries", status_code=201)
    def create_entry(tournament_id: int, body: schemas.EntryIn, db: Session = Depends(get_db),
                     _: User = editor):
        e = services.create_entry(db, tournament_id, body.player_id, body.tee_id, body.team_id,
                                  body.handicap_index)
        db.commit()
        return {"id": e.id, "handicap_index": e.handicap_index,
                "playing_handicap": e.playing_handicap}

    # --- Scorekarten -----------------------------------------------------------

    def _card_out(db: Session, entry_id: int, card: Scorecard) -> dict:
        ctx = services.load_entry(db, entry_id)
        r = services.scorecard_result(ctx, card)
        return {
            "id": card.id, "entry_id": entry_id, "version": card.version, "status": card.status,
            "source": card.source, "warnings": card.warnings, "playing_hcp": r.playing_hcp,
            "complete": r.complete, "gross_strokes": r.gross_strokes, "net_strokes": r.net_strokes,
            "gross_stableford": r.gross_stableford, "net_stableford": r.net_stableford,
            "holes": [h.__dict__ for h in r.holes],
        }

    @app.put("/entries/{entry_id}/scorecard")
    def put_scorecard(entry_id: int, body: schemas.ScorecardIn, db: Session = Depends(get_db),
                      _: User = editor):
        card = services.save_scorecard(db, entry_id, body.strokes,
                                       expected_version=body.expected_version, status=body.status)
        db.commit()
        return _card_out(db, entry_id, card)

    @app.get("/entries/{entry_id}/scorecard")
    def get_scorecard(entry_id: int, db: Session = Depends(get_db), _: User = viewer):
        card = db.scalar(select(Scorecard).options(selectinload(Scorecard.scores))
                         .where(Scorecard.entry_id == entry_id))
        if card is None:
            raise HTTPException(404, "Keine Scorekarte vorhanden.")
        return _card_out(db, entry_id, card)

    @app.get("/tournaments/{tournament_id}/leaderboard")
    def get_leaderboard(
        tournament_id: int,
        metric: Literal["net_stableford", "gross_stableford", "net_strokes", "gross_strokes"]
        = "net_stableford",
        team_best_n: int | None = Query(default=None, ge=1, le=50),
        db: Session = Depends(get_db),
        _: User = viewer,
    ):
        return services.tournament_leaderboard(db, tournament_id, metric, team_best_n)

    # --- Importe ---------------------------------------------------------------

    @app.post("/imports", status_code=201)
    async def upload_import(
        request: Request,
        response: Response,
        x_filename: str | None = Header(default=None, max_length=255),
        x_content_sha256: str | None = Header(default=None),
        content_length: int | None = Header(default=None),
        db: Session = Depends(get_db),
        s: Settings = Depends(get_settings),
        user: User = editor,
    ):
        """Roh-Upload: Body = Dateiinhalt. Typ wird serverseitig erkannt.

        X-Content-SHA256 (optional): Prüfsumme vom Client, erkennt Beschädigung
        auf dem Transportweg. Gleiche Datei erneut → 200 mit bestehendem Import.
        """
        if x_content_sha256 is not None and not SHA256_RE.match(x_content_sha256):
            raise HTTPException(400, "X-Content-SHA256 ist keine gültige SHA-256-Prüfsumme.")
        res = await import_service.intake(
            db, s, request.stream(), filename=x_filename, user_id=user.id,
            expected_sha256=x_content_sha256, declared_length=content_length,
        )
        if res.duplicate:
            response.status_code = 200
        return _import_out(res.imp) | {"duplicate": res.duplicate}

    def _import_out(i: Import) -> dict:
        return {"id": i.id, "sha256": i.sha256, "kind": i.kind, "status": i.status,
                "original_name": i.original_name, "error": i.error, "parsed": i.parsed}

    @app.get("/imports/{import_id}")
    def get_import(import_id: int, db: Session = Depends(get_db), _: User = editor):
        i = db.get(Import, import_id)
        if i is None:
            raise HTTPException(404, "Import nicht gefunden.")
        return _import_out(i)

    @app.post("/imports/{import_id}/apply")
    def apply_import(import_id: int, body: schemas.ApplyImportIn, db: Session = Depends(get_db),
                     _: User = editor):
        cards = services.apply_import(db, import_id, [a.model_dump() for a in body.assignments])
        db.commit()
        return {"applied": [{"scorecard_id": c.id, "entry_id": c.entry_id, "warnings": c.warnings}
                            for c in cards]}

    @app.post("/imports/{import_id}/reject", status_code=204)
    def reject_import(import_id: int, db: Session = Depends(get_db), _: User = editor):
        i = db.scalar(select(Import).where(Import.id == import_id).with_for_update())
        if i is None:
            raise HTTPException(404, "Import nicht gefunden.")
        if i.status == "applied":
            raise HTTPException(409, "Bereits übernommene Importe können nicht verworfen werden.")
        i.status = "rejected"
        db.commit()

    return app


app = create_app()
