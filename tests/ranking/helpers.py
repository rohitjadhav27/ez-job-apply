from datetime import datetime, timezone

from ezapply.core.config import Settings
from ezapply.core.models import Profile, RawItem, ResumeVariant

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


def make_settings(**kw) -> Settings:
    return Settings(database_url="x", app_secret="y", **kw)


def make_profile(**kw) -> Profile:
    base = dict(
        id="alex", name="Alex", email="r@x.com", phone="1",
        cities=["Mumbai", "Pune", "Bangalore"], exp_min=3, exp_max=8,
        target_titles=["Product Manager"], adjacent_titles=["Business Analyst"],
        domain_keywords=["fintech", "payments"], exclude_keywords=["intern"],
        blocked_companies=["Evil Corp"], allow_remote=False,
        resumes={"resume": ResumeVariant("resume", "u1", "product roles"),
                 "cv": ResumeVariant("cv", "u2", "research roles")},
        resume_text="resume body", search_queries=[],
    )
    base.update(kw)
    return Profile(**base)


def make_item(**kw) -> RawItem:
    base = dict(
        source="s", external_id="1", url="http://x/1", title="Product Manager",
        company="Acme", location="Mumbai, India", description="Build fintech payments.",
        posted_at=NOW, applicant_count=None, contact_email=None, poster_name=None,
        kind="listing",
    )
    base.update(kw)
    return RawItem(**base)
