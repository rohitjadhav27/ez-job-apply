import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from ezapply.core.models import Profile
from ezapply.sources.jobspy_source import JobSpySource

FIXTURE = Path(__file__).parent.parent / "fixtures" / "jobspy_rows.json"


def _profile(cities=("Mumbai",), queries=("product manager",)):
    return Profile(
        id="alex", name="X", email="x@y.z", phone="", cities=list(cities), exp_min=0, exp_max=5,
        target_titles=[], adjacent_titles=[], domain_keywords=[], exclude_keywords=[],
        blocked_companies=[], allow_remote=False, resumes={}, resume_text="", search_queries=list(queries),
    )


SETTINGS = SimpleNamespace(max_age_days=7)


def _df():
    rows = json.loads(FIXTURE.read_text())
    df = pd.DataFrame(rows)
    df.loc[4, "company"] = float("nan")  # real NaN, as jobspy produces
    df.loc[4, "description"] = pd.NA
    df["date_posted"] = pd.to_datetime(df["date_posted"]).dt.date
    return df


def test_jobspy_maps_rows_and_extracts_email():
    calls = []

    def scrape(**kw):
        calls.append(kw)
        return _df()

    src = JobSpySource(scrape=scrape, sleep=lambda s: None)
    items = src.fetch(_profile(), SETTINGS)

    assert len(calls) == 1
    kw = calls[0]
    assert kw["site_name"] == ["linkedin", "indeed", "glassdoor", "google"]
    assert kw["search_term"] == "product manager"
    assert kw["google_search_term"] == "product manager jobs in Mumbai since last week"
    assert kw["location"] == "Mumbai, India"
    assert kw["country_indeed"] == "India"
    assert kw["results_wanted"] == 15
    assert kw["hours_old"] == 168
    assert kw["linkedin_fetch_description"] is True

    assert len(items) == 5
    assert all(i.kind == "listing" for i in items)
    first = items[0]
    assert first.source == "linkedin"  # the row's site, not the aggregate name
    assert first.title == "Senior Product Manager"
    assert first.company == "Acme Labs"
    assert first.location == "Mumbai, Maharashtra, India"
    assert first.url.startswith("https://example.com/linkedin/")
    assert first.external_id == "li-fake0"
    assert first.posted_at.isoformat() == "2026-10-17T00:00:00+05:30"
    assert first.applicant_count is None
    assert first.contact_email is None
    assert first.poster_name is None
    assert first.description == ""  # NaN/None -> ""

    by_company = {i.company: i for i in items}
    assert by_company["Initech Foods"].contact_email == "hr@acme-example.org"  # emails column
    assert by_company["Umbrella Retail"].contact_email == "talent@acme-example.org"  # from description


def test_jobspy_runs_every_query_city_pair_and_sleeps_between():
    calls, sleeps = [], []

    def scrape(**kw):
        calls.append((kw["search_term"], kw["location"]))
        return _df().head(0)

    src = JobSpySource(scrape=scrape, sleep=sleeps.append)
    src.fetch(_profile(cities=("Mumbai", "Pune"), queries=("a", "b")), SETTINGS)
    assert calls == [("a", "Mumbai, India"), ("b", "Mumbai, India"), ("a", "Pune, India"), ("b", "Pune, India")]
    assert sleeps == [3, 3, 3]


def test_jobspy_one_site_error_does_not_stop_others():
    def scrape(**kw):
        if kw["search_term"] == "bad":
            raise RuntimeError("429 too many requests")
        return _df().head(2)

    src = JobSpySource(scrape=scrape, sleep=lambda s: None)
    items = src.fetch(_profile(queries=("bad", "good")), SETTINGS)
    assert len(items) == 2
    assert len(src.errors) == 1 and "bad" in src.errors[0] and "429" in src.errors[0]
    src.fetch(_profile(queries=("good",)), SETTINGS)
    assert src.errors == []  # reset per fetch


def test_jobspy_dedupes_same_url_across_queries():
    src = JobSpySource(scrape=lambda **kw: _df().head(2), sleep=lambda s: None)
    assert len(src.fetch(_profile(queries=("a", "b")), SETTINGS)) == 2


def test_jobspy_nan_and_pd_na_become_empty_strings():
    items = JobSpySource(scrape=lambda **kw: _df(), sleep=lambda s: None).fetch(_profile(), SETTINGS)
    last = items[4]
    assert last.company == "" and last.description == "" and last.contact_email is None


def test_stalled_scrape_times_out_and_is_recorded():
    import threading

    from ezapply.sources.jobspy_source import JobSpySource

    release = threading.Event()

    def blocked(**kw):
        release.wait(30)

    src = JobSpySource(scrape=blocked, sleep=lambda s: None, timeout=0.2)
    profile = type("P", (), {"cities": ["Mumbai"], "search_queries": ["pm"]})()
    settings = type("S", (), {"max_age_days": 7})()
    try:
        assert src.fetch(profile, settings) == []
        assert len(src.errors) == 1 and "ScrapeTimeout" in src.errors[0]
    finally:
        release.set()


def _many_profile(n_queries=5, cities=("Mumbai", "Pune")):
    return type("P", (), {"cities": list(cities), "search_queries": [f"q{i}" for i in range(n_queries)]})()


def test_naukri_never_requested_and_queries_capped_at_three():
    calls = []

    def scrape(**kw):
        calls.append(kw)
        return _df()

    JobSpySource(scrape=scrape, sleep=lambda s: None).fetch(_many_profile(), SETTINGS)
    assert len(calls) == 3 * 2  # 3 phrases x 2 cities
    assert all("naukri" not in c["site_name"] for c in calls)
    assert {c["search_term"] for c in calls} == {"q0", "q1", "q2"}


def test_time_budget_skips_remaining_queries_and_records_it():
    ticks = iter(range(0, 10000, 100))
    calls = []

    def scrape(**kw):
        calls.append(kw)
        return _df()

    src = JobSpySource(scrape=scrape, sleep=lambda s: None, budget=250, clock=lambda: next(ticks))
    src.fetch(_many_profile(), SETTINGS)
    assert len(calls) == 2
    assert src.errors == ["budget exceeded after 2/6 queries"]


def test_rate_limited_site_is_dropped_for_rest_of_run_and_recorded_once():
    import logging

    calls = []

    def scrape(**kw):
        calls.append(list(kw["site_name"]))
        if len(calls) == 1:
            logging.getLogger("jobspy.indeed").error("response status code 429 (blocked)")
        return _df()

    src = JobSpySource(scrape=scrape, sleep=lambda s: None)
    src.fetch(_many_profile(), SETTINGS)
    assert "indeed" in calls[0] and all("indeed" not in c for c in calls[1:])
    assert len(calls) == 6
    assert src.errors == ["indeed: rate-limited, skipped for the rest of this run"]


def test_item_source_is_the_row_site_with_fallback():
    items = JobSpySource(scrape=lambda **kw: _df(), sleep=lambda s: None).fetch(_profile(), SETTINGS)
    assert [i.source for i in items] == ["linkedin", "linkedin", "indeed", "naukri", "glassdoor"]
    src = JobSpySource()
    assert src._map({"job_url": "u", "site": None}).source == "jobspy"
    assert src._map({"job_url": "u", "site": "Indeed"}).source == "indeed"
