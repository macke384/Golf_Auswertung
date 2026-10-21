import pytest
from pydantic import ValidationError

from app.config import Settings

GOOD_DB = "postgresql+psycopg://golf:richtig-geheim@db/golf"


def test_production_rejects_default_secret():
    with pytest.raises(ValidationError):
        Settings(env="production", database_url=GOOD_DB)


def test_production_rejects_short_or_known_secret():
    for secret in ("changeme", "kurz"):
        with pytest.raises(ValidationError):
            Settings(env="production", session_secret=secret, database_url=GOOD_DB)


def test_production_rejects_default_db_password_and_insecure_cookie():
    with pytest.raises(ValidationError):
        Settings(env="production", session_secret="x" * 40)
    with pytest.raises(ValidationError):
        Settings(env="production", session_secret="x" * 40, database_url=GOOD_DB,
                 cookie_secure=False)


def test_production_ok_with_proper_values():
    s = Settings(env="production", session_secret="x" * 40, database_url=GOOD_DB, cookie_secure=True)
    assert s.cookie_secure
