from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class ResumeVariant:
    key: str
    drive_url: str
    use_for: str


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    email: str
    phone: str
    cities: list[str]
    exp_min: int
    exp_max: int
    target_titles: list[str]
    adjacent_titles: list[str]
    domain_keywords: list[str]
    exclude_keywords: list[str]
    blocked_companies: list[str]
    allow_remote: bool
    resumes: dict[str, ResumeVariant]
    resume_text: str
    search_queries: list[str]


@dataclass
class RawItem:
    source: str
    external_id: str
    url: str
    title: str
    company: str
    location: str
    description: str
    posted_at: datetime | None
    applicant_count: int | None
    contact_email: str | None
    poster_name: str | None
    kind: Literal["listing", "post"]
    key: str = ""


@dataclass
class Scored:
    item: RawItem
    rule_pct: int
    ai_pct: int | None = None
    match_pct: int = 0
    reason: str = ""
    gap: str = ""
    resume_variant: str = ""
