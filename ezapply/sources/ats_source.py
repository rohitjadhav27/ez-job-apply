"""Company career boards: Greenhouse, Lever, Ashby public JSON APIs."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

from ezapply.core.models import Profile, RawItem
from ezapply.sources.base import cities_of

DEFAULT_COMPANIES_PATH = Path(__file__).resolve().parents[2] / "config" / "companies.yaml"
USER_AGENT = "ezapply/0.1"
TIMEOUT = 20.0

_URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=false",
}
_TAG_RE = re.compile(r"<[^>]+>")


def make_http_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})


def _strip_html(s: str) -> str:
    text = _TAG_RE.sub(" ", html.unescape(s or ""))
    text = html.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _company_from_slug(slug: str) -> str:
    return slug.replace("-", " ").title()


class AtsSource:
    name = "ats"

    def __init__(self, http: httpx.Client, companies_path: Path = DEFAULT_COMPANIES_PATH):
        self.http = http
        self.companies_path = Path(companies_path)
        self.errors: list[str] = []

    def fetch(self, profile: Profile, settings: Any) -> list[RawItem]:
        self.errors = []
        cfg = yaml.safe_load(self.companies_path.read_text(encoding="utf-8")) or {}
        parsers: dict[str, Callable[..., list[RawItem]]] = {
            "greenhouse": self._greenhouse,
            "lever": self._lever,
            "ashby": self._ashby,
        }
        items: list[RawItem] = []
        for ats, parse in parsers.items():
            for slug in cfg.get(ats) or []:
                try:
                    resp = self.http.get(_URLS[ats].format(slug=slug))
                    resp.raise_for_status()
                    items.extend(parse(slug, resp.json(), profile))
                except Exception as e:  # one failing company must not stop the rest
                    self.errors.append(f"{ats}/{slug}: {type(e).__name__}: {e}")
        return items

    @staticmethod
    def _in_cities(location: str, profile: Profile) -> bool:
        return bool(cities_of(location) & set(profile.cities))

    def _item(self, **kw: Any) -> RawItem:
        return RawItem(source=self.name, applicant_count=None, contact_email=None,
                       poster_name=None, kind="listing", **kw)

    def _greenhouse(self, slug: str, body: dict, profile: Profile) -> list[RawItem]:
        out = []
        for j in body.get("jobs", []):
            loc = (j.get("location") or {}).get("name", "")
            if not self._in_cities(loc, profile):
                continue
            out.append(self._item(
                external_id=str(j.get("id", "")), url=j.get("absolute_url", ""),
                title=j.get("title", ""), company=(j.get("company_name") or "").strip() or _company_from_slug(slug),
                location=loc, description=_strip_html(j.get("content", "")),
                posted_at=_dt(j.get("first_published") or j.get("updated_at")),
            ))
        return out

    def _lever(self, slug: str, body: list, profile: Profile) -> list[RawItem]:
        out = []
        for j in body:
            cats = j.get("categories") or {}
            candidates = [*(cats.get("allLocations") or []), cats.get("location") or ""]
            loc = next((c for c in candidates if self._in_cities(c, profile)), None)
            if loc is None:
                continue
            desc = " ".join(p for p in (j.get("descriptionPlain"), j.get("additionalPlain")) if p)
            out.append(self._item(
                external_id=str(j.get("id", "")), url=j.get("hostedUrl", ""),
                title=j.get("text", ""), company=_company_from_slug(slug),
                location=loc, description=desc, posted_at=_dt(j.get("createdAt")),
            ))
        return out

    def _ashby(self, slug: str, body: dict, profile: Profile) -> list[RawItem]:
        out = []
        for j in body.get("jobs", []):
            candidates = [j.get("location") or "", *[
                s.get("location", "") if isinstance(s, dict) else str(s)
                for s in j.get("secondaryLocations") or []]]
            loc = next((c for c in candidates if self._in_cities(c, profile)), None)
            if loc is None:
                continue
            out.append(self._item(
                external_id=str(j.get("id", "")), url=j.get("jobUrl", ""),
                title=j.get("title", ""), company=_company_from_slug(slug),
                location=loc, description=j.get("descriptionPlain") or _strip_html(j.get("descriptionHtml", "")),
                posted_at=_dt(j.get("publishedAt")),
            ))
        return out
