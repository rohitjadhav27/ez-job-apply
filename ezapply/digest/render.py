from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from ezapply.core.urls import safe_url
from ezapply.core.models import Scored
from ezapply.core.timeutil import IST

_TEMPLATES = Path(__file__).parent / "templates"
_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=select_autoescape(["html", "xml", "j2"], default_for_string=True),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)
# Plain text must not be HTML-escaped.
_TEXT_ENV = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    autoescape=False,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)

GREEN, BLUE, AMBER = "#1a7f37", "#0b5cad", "#9a6700"


@dataclass
class DigestItem:
    scored: Scored
    apply_url: str | None


@dataclass
class DigestMeta:
    run_date: date
    person_name: str
    source_counts: dict[str, int]
    source_errors: dict[str, str]
    ai_status: str
    threshold: int
    resume_labels: dict[str, str] = field(default_factory=dict)


_SOURCE_LABELS = {
    "linkedin": "LinkedIn", "indeed": "Indeed", "naukri": "Naukri",
    "glassdoor": "Glassdoor", "google": "Google Jobs", "jobspy": "Job boards",
    "ats": "Company careers page", "linkedin_posts": "LinkedIn post",
}


def _label(source: str) -> str:
    return _SOURCE_LABELS.get(source, source)


def _pill_colour(pct: int) -> str:
    return GREEN if pct >= 90 else BLUE if pct >= 80 else AMBER


def _age(posted_at: datetime | None, run_date: date) -> str:
    if posted_at is None:
        return ""
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=IST)
    days = (run_date - posted_at.astimezone(IST).date()).days
    return "today" if days <= 0 else f"{days}d ago"


def _short_date(d: date) -> str:
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _view(di: DigestItem, run_date: date, labels: dict[str, str]) -> dict:
    s, it = di.scored, di.scored.item
    is_post = it.kind == "post"
    meta_parts = [
        it.poster_name if is_post else None,
        it.company,
        None if is_post else it.location,
        _age(it.posted_at, run_date),
        _label(it.source),
        f"{it.applicant_count} applicants" if it.applicant_count else None,
    ]
    return {
        "title": it.title,
        "url": safe_url(it.url),
        "meta": [p for p in meta_parts if p],
        "pct": s.match_pct,
        "colour": _pill_colour(s.match_pct),
        "reason": s.reason,
        "gap": s.gap,
        "resume": labels.get(s.resume_variant, "") if len(labels) > 1 else "",
        "apply_url": safe_url(di.apply_url),
        "view_label": "View post" if is_post else "View & apply",
    }


def render_digest(items: list[DigestItem], meta: DigestMeta) -> tuple[str, str, str]:
    posts = [_view(i, meta.run_date, meta.resume_labels) for i in items if i.scored.item.kind == "post"]
    listings = [_view(i, meta.run_date, meta.resume_labels) for i in items if i.scored.item.kind != "post"]
    date_str = _short_date(meta.run_date)
    if items:
        subject = f"Ez Apply · {date_str} · {len(listings)} matches · {len(posts)} hiring posts"
        summary = f"{len(listings)} matches and {len(posts)} hiring posts for you today."
    else:
        subject = f"Ez Apply · {date_str} · nothing above {meta.threshold}% today"
        summary = f"Nothing above {meta.threshold}% today — we'll keep looking."
    ctx = {
        "meta": meta,
        "date_str": date_str,
        "summary": summary,
        "posts": posts,
        "listings": listings,
        "empty": not items,
        "source_errors": [(_label(k), v) for k, v in meta.source_errors.items()],
        "counts": " · ".join(f"{_label(k)} {v}" for k, v in meta.source_counts.items()),
    }
    html = _ENV.get_template("digest.html.j2").render(**ctx)
    text = _TEXT_ENV.get_template("digest.txt.j2").render(**ctx)
    return subject, html, text
