import httpx
import respx

from ezapply.core.models import Profile
from ezapply.sources.ats_source import AtsSource


def _profile(cities=("Mumbai",)):
    return Profile(
        id="alex", name="X", email="x@y.z", phone="", cities=list(cities), exp_min=0, exp_max=5,
        target_titles=[], adjacent_titles=[], domain_keywords=[], exclude_keywords=[],
        blocked_companies=[], allow_remote=False, resumes={}, resume_text="", search_queries=[],
    )


GH = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"
LV = "https://api.lever.co/v0/postings/globex?mode=json"
AS = "https://api.ashbyhq.com/posting-api/job-board/initech?includeCompensation=false"

GH_BODY = {
    "jobs": [
        {"id": 11, "title": "Product Manager", "company_name": "Acme Labs",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
         "location": {"name": "Mumbai, India"}, "updated_at": "2026-10-18T10:00:00-04:00",
         "first_published": "2026-10-10T10:00:00-04:00",
         "content": "&lt;p&gt;Own the &lt;b&gt;roadmap&lt;/b&gt;&lt;/p&gt;"},
        {"id": 12, "title": "Product Manager", "company_name": "Acme Labs",
         "absolute_url": "https://boards.greenhouse.io/acme/jobs/12",
         "location": {"name": "London, UK"}, "updated_at": "2026-10-18T10:00:00-04:00", "content": ""},
    ]
}
LV_BODY = [
    {"id": "abc", "text": "Growth Marketer", "hostedUrl": "https://jobs.lever.co/globex/abc",
     "categories": {"location": "Bengaluru", "allLocations": ["Bengaluru"]},
     "createdAt": 1760000000000, "descriptionPlain": "Grow things"},
    {"id": "def", "text": "Designer", "hostedUrl": "https://jobs.lever.co/globex/def",
     "categories": {"location": "Berlin"}, "createdAt": 1760000000000, "descriptionPlain": ""},
]
AS_BODY = {
    "jobs": [
        {"id": "x1", "title": "Chief of Staff", "location": "Pune", "jobUrl": "https://jobs.ashbyhq.com/initech/x1",
         "publishedAt": "2026-10-15T08:00:00.000+00:00", "descriptionPlain": "Support the CEO"},
        {"id": "x2", "title": "Engineer", "location": "Remote - US", "jobUrl": "https://jobs.ashbyhq.com/initech/x2",
         "publishedAt": "2026-10-15T08:00:00.000+00:00", "descriptionPlain": ""},
    ]
}


def _cfg(tmp_path, body):
    p = tmp_path / "companies.yaml"
    p.write_text(body)
    return p


@respx.mock
def test_ats_filters_to_profile_cities(tmp_path):
    respx.get(GH).mock(return_value=httpx.Response(200, json=GH_BODY))
    src = AtsSource(httpx.Client(), _cfg(tmp_path, "greenhouse: [acme]\nlever: []\nashby: []\n"))
    items = src.fetch(_profile(), None)
    assert len(items) == 1
    it = items[0]
    assert (it.source, it.kind, it.external_id) == ("ats", "listing", "11")
    assert it.title == "Product Manager" and it.company == "Acme Labs"
    assert it.location == "Mumbai, India"
    assert it.url == "https://boards.greenhouse.io/acme/jobs/11"
    assert it.description == "Own the roadmap"
    assert it.posted_at is not None and it.posted_at.tzinfo is not None
    assert it.applicant_count is None and it.contact_email is None and it.poster_name is None


@respx.mock
def test_ats_lever_and_ashby(tmp_path):
    respx.get(LV).mock(return_value=httpx.Response(200, json=LV_BODY))
    respx.get(AS).mock(return_value=httpx.Response(200, json=AS_BODY))
    src = AtsSource(httpx.Client(), _cfg(tmp_path, "greenhouse: []\nlever: [globex]\nashby: [initech]\n"))
    items = src.fetch(_profile(cities=("Bangalore", "Pune")), None)
    assert sorted(i.title for i in items) == ["Chief of Staff", "Growth Marketer"]
    lever = next(i for i in items if i.title == "Growth Marketer")
    assert lever.company == "Globex" and lever.url == "https://jobs.lever.co/globex/abc"
    assert lever.posted_at.year == 2025
    ashby = next(i for i in items if i.title == "Chief of Staff")
    assert ashby.company == "Initech" and ashby.external_id == "x1"
    assert ashby.description == "Support the CEO"


@respx.mock
def test_ats_failing_company_is_skipped(tmp_path):
    respx.get(GH).mock(return_value=httpx.Response(404))
    respx.get(LV).mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get(AS).mock(return_value=httpx.Response(200, json=AS_BODY))
    src = AtsSource(httpx.Client(), _cfg(tmp_path, "greenhouse: [acme]\nlever: [globex]\nashby: [initech]\n"))
    items = src.fetch(_profile(cities=("Pune",)), None)
    assert [i.title for i in items] == ["Chief of Staff"]
    assert len(src.errors) == 2


def test_strip_html_handles_double_escaped_entities():
    from ezapply.sources.ats_source import _strip_html

    raw = "&lt;p&gt;R&amp;amp;D&amp;nbsp;team&amp;rsquo;s&lt;/p&gt;"
    assert _strip_html(raw) == "R&D team\u2019s"


def test_ats_in_cities_accepts_any_listed_city():
    assert AtsSource._in_cities("Bangalore / Mumbai", _profile(("Mumbai",))) is True
    assert AtsSource._in_cities("Delhi / Chennai", _profile(("Mumbai",))) is False
