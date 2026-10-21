"""Session and CSRF helpers."""
from __future__ import annotations

import hmac

from fastapi import Request
from itsdangerous import Signer

from ezapply.core.tokens import read_session
from ezapply.web.runtime import get_runtime

COOKIE = "ezs"
MAX_AGE = 7 * 86400


def current_person(request: Request) -> str | None:
    rt = get_runtime(request)
    value = request.cookies.get(COOKIE)
    if not value:
        return None
    person = read_session(rt.settings.app_secret, value, MAX_AGE)
    return person if person in rt.profiles else None


def csrf_token(secret: str, session_value: str) -> str:
    return Signer(secret, salt="csrf").get_signature(session_value).decode()


def csrf_ok(secret: str, session_value: str, token: str) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(secret, session_value), token)
