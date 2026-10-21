"""Stage 0 hard filters."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from ezapply.core.models import Profile, RawItem
from ezapply.sources.base import cities_of, normalize_text

if TYPE_CHECKING:
    from ezapply.core.config import Settings

IST = ZoneInfo("Asia/Kolkata")

_RANGE = re.compile(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b", re.I)
_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)\b", re.I)
_MIN = re.compile(r"(?:\bminimum|\bmin\.?|at least)\s+(?:of\s+)?(\d{1,2})\s*(?:years?|yrs?)\b", re.I)


def parse_experience(text: str) -> tuple[int | None, int | None]:
    """First experience statement found in text, as (min, max)."""
    found = []
    for pat, kind in ((_RANGE, "range"), (_PLUS, "min"), (_MIN, "min")):
        m = pat.search(text)
        if m:
            found.append((m.start(), kind, m))
    if not found:
        return (None, None)
    _, kind, m = min(found, key=lambda f: f[0])
    if kind == "range":
        return (int(m.group(1)), int(m.group(2)))
    return (int(m.group(1)), None)


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=IST) if dt.tzinfo is None else dt


def _has_word(text: str, phrase: str) -> bool:
    phrase = phrase.strip().lower()
    return bool(phrase) and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text.lower()) is not None


def _city_ok(item: RawItem, profile: Profile) -> bool:
    text = f"{item.title} {item.description}" if item.kind == "post" else item.location
    if cities_of(text) & set(profile.cities):
        return True
    return profile.allow_remote and "remote" in item.location.lower()


def passes(item: RawItem, profile: Profile, settings: "Settings",
           now: datetime) -> tuple[bool, str]:
    if not _city_ok(item, profile):
        return False, "city"
    if item.posted_at is not None:
        if now - aware(item.posted_at) > timedelta(days=settings.max_age_days):
            return False, "age"
    if any(_has_word(item.title, k) for k in profile.exclude_keywords):
        return False, "exclude_keyword"
    company = normalize_text(item.company)
    for blocked in profile.blocked_companies:
        b = normalize_text(blocked)
        if b and re.search(rf"(?<!\w){re.escape(b)}(?!\w)", company):
            return False, "blocked_company"
    lo, hi = parse_experience(f"{item.title} {item.description}")
    if lo is not None and lo > profile.exp_max + 2:
        return False, "experience"
    if hi is not None and hi < profile.exp_min - 1:
        return False, "experience"
    return True, ""
