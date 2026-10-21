"""Stage 1 local rule score."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from rapidfuzz import fuzz

from ezapply.core.models import Profile, RawItem
from ezapply.ranking.filters import aware, parse_experience
from ezapply.sources.base import cities_of, normalize_text

if TYPE_CHECKING:
    from ezapply.core.config import Settings

WEIGHTS = {"title": 30, "skills": 30, "seniority": 20, "freshness": 10, "city": 5, "competition": 5}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def title_fit(title: str, profile: Profile) -> int:
    t = normalize_text(title)
    if any(fuzz.token_set_ratio(t, normalize_text(x)) >= 90 for x in profile.target_titles):
        return 100
    if any(fuzz.token_set_ratio(t, normalize_text(x)) >= 90 for x in profile.adjacent_titles):
        return 60
    return 20


def seniority_fit(exp: tuple[int | None, int | None], profile: Profile) -> int:
    lo, hi = exp
    if lo is None and hi is None:
        return 60
    outside = 0
    if lo is not None and lo > profile.exp_max:
        outside = lo - profile.exp_max
    elif hi is not None and hi < profile.exp_min:
        outside = profile.exp_min - hi
    return max(0, 100 - 20 * outside)


def freshness(posted_at: datetime | None, now: datetime, max_age_days: int) -> int:
    if posted_at is None:
        return 50
    hours = (now - aware(posted_at)).total_seconds() / 3600
    if hours <= 24:
        return 100
    span = max_age_days * 24 - 24
    if span <= 0:
        return 0
    return round(100 * _clamp(1 - (hours - 24) / span))


def city_pref(item: RawItem, profile: Profile) -> int:
    if len(profile.cities) <= 1:
        return 100
    idxs = [profile.cities.index(c) for c in cities_of(item.location) if c in profile.cities]
    if idxs:
        return (100, 70, 40)[min(min(idxs), 2)]
    return 40


def competition(item: RawItem) -> int:
    if item.contact_email:
        return 100
    n = item.applicant_count
    if n is None:
        return 50
    if n < 25:
        return 100
    return round(max(0, 100 - n / 5))


def skills_fit(similarity: float, item: RawItem, profile: Profile) -> int:
    kws = profile.domain_keywords
    text = f"{item.title} {item.description}".lower()
    ratio = sum(1 for k in kws if k.lower() in text) / len(kws) if kws else 0.0
    sim = _clamp((similarity - 0.3) / 0.5)
    return round(0.7 * sim * 100 + 0.3 * ratio * 100)


def rule_pct(item: RawItem, profile: Profile, similarity: float, now: datetime,
             settings: "Settings") -> int:
    exp = parse_experience(f"{item.title} {item.description}")
    factors = {
        "title": title_fit(item.title, profile),
        "skills": skills_fit(similarity, item, profile),
        "seniority": seniority_fit(exp, profile),
        "freshness": freshness(item.posted_at, now, settings.max_age_days),
        "city": city_pref(item, profile),
        "competition": competition(item),
    }
    return round(sum(WEIGHTS[k] * v for k, v in factors.items()) / 100)
