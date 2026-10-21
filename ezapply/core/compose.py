"""Gmail / mailto compose URL builders (stdlib only)."""
from __future__ import annotations

from urllib.parse import quote

MAX_COMPOSE_URL = 1900
_RESUME_PREFIX = "Resume:"


def _q(value: str) -> str:
    return quote(value, safe="")


def gmail_web_url(account: str, to: str, subject: str, body: str) -> str:
    return (
        "https://mail.google.com/mail/?"
        f"authuser={_q(account)}&view=cm&fs=1&to={_q(to)}&su={_q(subject)}&body={_q(body)}"
    )


def gmail_ios_url(to: str, subject: str, body: str) -> str:
    return f"googlegmail://co?to={_q(to)}&subject={_q(subject)}&body={_q(body)}"


def mailto_url(to: str, subject: str, body: str) -> str:
    return f"mailto:{quote(to, safe='@')}?subject={_q(subject)}&body={_q(body)}"


def compose_body(body: str, resume_url: str) -> str:
    lines = body.split("\n")
    idx = next((i for i, l in enumerate(lines) if l.startswith(_RESUME_PREFIX)), None)
    if resume_url:
        line = f"{_RESUME_PREFIX} {resume_url}"
        if idx is None:
            return f"{body}\n\n{line}"
        lines[idx] = line
        return "\n".join(lines)
    if idx is None:
        return body
    del lines[idx]
    # avoid leaving a double blank gap where the line was
    if 0 < idx < len(lines) and lines[idx] == "" and lines[idx - 1] == "":
        del lines[idx]
    return "\n".join(lines).rstrip("\n") if idx >= len(lines) else "\n".join(lines)
