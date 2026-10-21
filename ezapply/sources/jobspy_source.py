"""Job listings from LinkedIn, Indeed, Glassdoor and Google via python-jobspy."""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any, Callable

from ezapply.core.models import Profile, RawItem
from ezapply.core.timeutil import IST
from ezapply.sources.base import extract_emails

# Naukri is deliberately excluded: it answers with 406 / reCAPTCHA-required, and we do not work
# around CAPTCHAs or keep hammering a site that is blocking us (spec §14 risk 1, ruling R14).
SITES = ["linkedin", "indeed", "glassdoor", "google"]
MAX_QUERIES = 3
RESULTS_WANTED = 15
JOBSPY_BUDGET_SECONDS = 360.0
_RATE_LIMIT_HINTS = ("429", "406", "rate limit", "rate-limit", "recaptcha", "captcha")


class _RateLimitWatcher(logging.Handler):
    """JobSpy logs per-site failures instead of raising; spot rate limits from its log lines."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.sites: set[str] = set()

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage().lower()
        site = record.name.rsplit(".", 1)[-1]
        if site in SITES and any(h in msg for h in _RATE_LIMIT_HINTS):
            self.sites.add(site)


def _blank(v: Any) -> bool:
    # None and NaN (NaN != NaN) both count as missing
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN
    except (TypeError, ValueError):  # pd.NA raises on bool()
        return True


def _s(v: Any) -> str:
    return "" if _blank(v) else str(v)


def _posted_at(v: Any) -> datetime | None:
    if _blank(v):
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=IST)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=IST)
    try:
        d = date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=IST)


SCRAPE_TIMEOUT = 90.0


class ScrapeTimeout(Exception):
    pass


def _call_with_timeout(fn: Callable[..., Any], timeout: float, **kwargs: Any) -> Any:
    """Bound one scrape call. jobspy's own requests carry a ~15s timeout and its scrape_jobs uses a
    non-daemon ThreadPoolExecutor, so this guard stops us waiting on a stalled call; the abandoned
    worker is bounded by jobspy's request timeout rather than being killed."""
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["result"] = fn(**kwargs)
        except BaseException as e:  # noqa: BLE001 - re-raised in the caller
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise ScrapeTimeout(f"no response after {timeout:.0f}s")
    if "error" in box:
        raise box["error"]
    return box["result"]


def _default_scrape(**kwargs: Any):
    import jobspy

    return jobspy.scrape_jobs(**kwargs)


class JobSpySource:
    name = "jobspy"

    def __init__(self, scrape: Callable[..., Any] = _default_scrape, sleep: Callable[[float], None] = time.sleep,
                 timeout: float = SCRAPE_TIMEOUT,
                 budget: float = JOBSPY_BUDGET_SECONDS, clock: Callable[[], float] = time.monotonic):
        self.scrape = scrape
        self.timeout = timeout
        self.budget = budget
        self.clock = clock
        self.sleep = sleep
        self.errors: list[str] = []

    def fetch(self, profile: Profile, settings: Any) -> list[RawItem]:
        self.errors = []
        items: list[RawItem] = []
        seen: set[str] = set()
        first = True
        queries = profile.search_queries[:MAX_QUERIES]
        total = len(profile.cities) * len(queries)
        sites = list(SITES)
        watcher = _RateLimitWatcher()
        jlog = logging.getLogger("jobspy")
        jlog.addHandler(watcher)
        start = self.clock()
        done = 0
        try:
            for city in profile.cities:
                for query in queries:
                    if self.clock() - start > self.budget:
                        self.errors.append(f"budget exceeded after {done}/{total} queries")
                        return items
                    if not sites:
                        self.errors.append(f"all sites rate-limited after {done}/{total} queries")
                        return items
                    if not first:
                        self.sleep(3)
                    first = False
                    done += 1
                    try:
                        df = _call_with_timeout(
                            self.scrape, self.timeout,
                            site_name=list(sites),
                            search_term=query,
                            google_search_term=f"{query} jobs in {city} since last week",
                            location=f"{city}, India",
                            country_indeed="India",
                            results_wanted=RESULTS_WANTED,
                            hours_old=settings.max_age_days * 24,
                            linkedin_fetch_description=True,
                        )
                    except Exception as e:  # one failing query must not kill the source
                        self.errors.append(f"{query!r} in {city}: {type(e).__name__}: {e}")
                        continue
                    finally:
                        for site in sorted(watcher.sites & set(sites)):
                            sites.remove(site)
                            self.errors.append(f"{site}: rate-limited, skipped for the rest of this run")
                    for row in df.to_dict("records"):
                        item = self._map(row)
                        if item.url and item.url not in seen:
                            seen.add(item.url)
                            items.append(item)
        finally:
            jlog.removeHandler(watcher)
        return items

    def _map(self, row: dict[str, Any]) -> RawItem:
        description = _s(row.get("description"))
        emails = [e.strip() for e in _s(row.get("emails")).split(",") if e.strip()]
        if not emails:
            emails = extract_emails(description)
        site = _s(row.get("site")).lower()
        return RawItem(
            source=site or self.name,
            external_id=_s(row.get("id")),
            url=_s(row.get("job_url")),
            title=_s(row.get("title")),
            company=_s(row.get("company")),
            location=_s(row.get("location")),
            description=description,
            posted_at=_posted_at(row.get("date_posted")),
            applicant_count=None,
            contact_email=emails[0].lower() if emails else None,
            poster_name=None,
            kind="listing",
        )
