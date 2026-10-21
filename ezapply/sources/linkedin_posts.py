"""LinkedIn hiring posts via an Apify post-search actor (no LinkedIn cookies)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml
from apify_client.errors import ApifyApiError

from ezapply.core.models import Profile, RawItem
from ezapply.sources.base import (  # noqa: F401  (extract_emails re-exported)
    SourceSkipped,
    city_of,
    extract_emails,
    extract_emails_with_mailto,
)

MAX_QUERIES = 4
DEFAULT_QUERIES_PATH = Path(__file__).resolve().parents[2] / "config" / "linkedin_queries.yaml"

_QUOTA_HINTS = ("insufficient", "not-enough", "usage", "credit", "limit exceeded", "payment")
_COMPANY_IN_HEADLINE = re.compile(r"(?:\bat\b|@)\s+([^|•·,;()\n]+)", re.IGNORECASE)


def canonical_post_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _is_quota_error(err: ApifyApiError) -> bool:
    if getattr(err, "status_code", None) == 402:
        return True
    text = f"{getattr(err, 'type', '')} {getattr(err, 'message', '')}".lower()
    return any(h in text for h in _QUOTA_HINTS)


def _company_of(author: dict[str, Any]) -> str:
    name = (author.get("name") or "").strip()
    if author.get("type") == "company":
        return name
    m = _COMPANY_IN_HEADLINE.search(author.get("info") or "")
    return m.group(1).strip() if m else ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return ""


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _run_dataset_id(run: Any) -> str:
    ds = getattr(run, "default_dataset_id", None)
    if ds is None and isinstance(run, dict):
        ds = run.get("defaultDatasetId")
    return ds


class LinkedInPostsSource:
    name = "linkedin_posts"

    def __init__(self, apify: Any, actor_id: str, queries_path: Path = DEFAULT_QUERIES_PATH):
        self.apify = apify
        self.actor_id = actor_id
        self.queries_path = Path(queries_path)
        self.errors: list[str] = []

    def _config(self) -> dict[str, Any]:
        return yaml.safe_load(self.queries_path.read_text(encoding="utf-8")) or {}

    def fetch(self, profile: Profile, settings: Any) -> list[RawItem]:
        self.errors = []
        cfg = self._config()
        queries = (cfg.get(profile.id) or [])[:MAX_QUERIES]
        if not queries:
            return []
        cap = int(cfg.get("max_posts_per_person", 30))
        run_input = {
            "searchQueries": list(queries),
            "maxPosts": max(1, cap // len(queries)),
            "postedLimit": "24h",
            "sortBy": "date",
        }
        try:
            run = self.apify.actor(self.actor_id).call(run_input=run_input, run_timeout=timedelta(seconds=300),
                                                   wait_duration=timedelta(seconds=330), logger=None)
            self._check_run(run)
            raw = list(self.apify.dataset(_run_dataset_id(run)).iterate_items())
        except ApifyApiError as e:
            if _is_quota_error(e):
                raise SourceSkipped(f"quota: {getattr(e, 'message', e)}") from e
            raise

        items: list[RawItem] = []
        seen: set[str] = set()
        for r in raw:
            item = self._map(r)
            if item is None or item.url in seen:
                continue
            seen.add(item.url)
            items.append(item)
        return items

    @staticmethod
    def _check_run(run: Any) -> None:
        status = str(getattr(getattr(run, "status", None), "value", getattr(run, "status", None)))
        if run is None or status != "SUCCEEDED":
            msg = str(getattr(run, "status_message", "") or "").lower()
            if any(h in msg for h in _QUOTA_HINTS):
                raise SourceSkipped(f"quota: apify run {status}: {msg}")
            raise RuntimeError(f"apify run {status}" if run is not None else "apify run returned no run (None)")

    def _map(self, r: dict[str, Any]) -> RawItem | None:
        url = r.get("linkedinUrl") or ""
        if not url:
            return None
        content = r.get("content") or ""
        author = r.get("author") or {}
        emails = extract_emails_with_mailto(content, r.get("contentAttributes"))
        return RawItem(
            source=self.name,
            external_id=str(r.get("id") or r.get("entityId") or ""),
            url=canonical_post_url(url),
            title=_first_line(content),
            company=_company_of(author),
            location=city_of(content) or "",
            description=content,
            posted_at=_parse_dt((r.get("postedAt") or {}).get("date")),
            applicant_count=None,
            contact_email=emails[0] if emails else None,
            poster_name=author.get("name") or None,
            kind="post",
        )
