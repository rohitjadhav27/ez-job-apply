"""Signed, expiring link tokens and session cookies (stdlib + itsdangerous only)."""
from __future__ import annotations

import secrets

from itsdangerous import BadData, URLSafeTimedSerializer

_LINK_SALT = "draft-link"
_SESSION_SALT = "session"


def make_link_token(secret: str, person: str, draft_id: str, ttl_hours: int) -> str:
    # ttl_hours is enforced at read time; accepted here for a symmetric API.
    s = URLSafeTimedSerializer(secret, salt=_LINK_SALT)
    return s.dumps({"p": person, "d": draft_id, "n": secrets.token_urlsafe(8)})


def read_link_token(secret: str, token: str, ttl_hours: int) -> tuple[str, str] | None:
    s = URLSafeTimedSerializer(secret, salt=_LINK_SALT)
    try:
        data = s.loads(token, max_age=ttl_hours * 3600)
        return str(data["p"]), str(data["d"])
    except (BadData, KeyError, TypeError):
        return None


def make_session(secret: str, person: str) -> str:
    return URLSafeTimedSerializer(secret, salt=_SESSION_SALT).dumps({"p": person})


def read_session(secret: str, value: str, max_age_s: int = 7 * 86400) -> str | None:
    s = URLSafeTimedSerializer(secret, salt=_SESSION_SALT)
    try:
        return str(s.loads(value, max_age=max_age_s)["p"])
    except (BadData, KeyError, TypeError):
        return None
