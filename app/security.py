"""Authentifizierung: Passwort-Hashing (Argon2id) und serverseitige Sessions.

- Session-Token: 256 Bit Zufall, im Cookie (HttpOnly, Secure, SameSite=Strict).
- In der DB liegt nur HMAC-SHA256(secret, token): ein DB-Leak erlaubt keinen Login.
- Kein öffentliches Registrieren: Konten legt ein Admin an (Einladung).
- Konto-Sperre nach wiederholten Fehlversuchen.

Was das NICHT löst: geschönte Scores eines angemeldeten Nutzers.
Authentifizierung beweist nur, WER etwas eingetragen hat.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuthSession, User

COOKIE = "golf_session"
ROLES = {"viewer": 0, "editor": 1, "admin": 2}
_ph = PasswordHasher()
_DUMMY_HASH = _ph.hash("timing-equalizer")


def hash_password(pw: str) -> str:
    if len(pw) < 12:
        raise ValueError("Passwort muss mindestens 12 Zeichen haben.")
    return _ph.hash(pw)


def _token_hash(token: str, settings: Settings) -> str:
    return hmac.new(settings.session_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def authenticate(db: Session, settings: Settings, email: str, password: str) -> User | None:
    now = datetime.now(UTC)
    user = db.scalar(select(User).where(User.email == email.strip().lower()).with_for_update())
    if user is None or not user.is_active:
        try:  # gleiche Laufzeit wie bei existierendem Nutzer
            _ph.verify(_DUMMY_HASH, password)
        except VerificationError:
            pass
        return None
    if user.locked_until and user.locked_until > now:
        return None
    try:
        _ph.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        user.failed_logins += 1
        if user.failed_logins >= settings.login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
            user.failed_logins = 0
        db.commit()
        return None
    if _ph.check_needs_rehash(user.password_hash):
        user.password_hash = _ph.hash(password)
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    return user


def create_session(db: Session, settings: Settings, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.execute(delete(AuthSession).where(AuthSession.expires_at < now))
    db.add(
        AuthSession(
            token_hash=_token_hash(token, settings),
            user_id=user.id,
            expires_at=now + timedelta(hours=settings.session_ttl_hours),
        )
    )
    db.commit()
    return token


def end_session(db: Session, settings: Settings, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token, settings)))
        db.commit()


def current_user(
    request: Request, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)
) -> User:
    token = request.cookies.get(COOKIE)
    if not token:
        raise HTTPException(401, "Nicht angemeldet.")
    sess = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token, settings))
    )
    if sess is None or sess.expires_at < datetime.now(UTC) or not sess.user.is_active:
        raise HTTPException(401, "Sitzung abgelaufen.")
    return sess.user


def require(role: str):
    def dep(user: User = Depends(current_user)) -> User:
        if ROLES[user.role] < ROLES[role]:
            raise HTTPException(403, "Keine Berechtigung.")
        return user

    return dep
