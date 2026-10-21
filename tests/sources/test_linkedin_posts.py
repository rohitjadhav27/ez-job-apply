from datetime import timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from apify_client.errors import ApifyApiError

from ezapply.core.models import Profile
from ezapply.sources.base import SourceSkipped
from ezapply.sources.linkedin_posts import (
    LinkedInPostsSource,
    canonical_post_url,
    extract_emails,
)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "apify_posts.json"


def _profile(pid="alex"):
    return Profile(
        id=pid, name="X", email="x@y.z", phone="", cities=["Mumbai"], exp_min=0, exp_max=5,
        target_titles=[], adjacent_titles=[], domain_keywords=[], exclude_keywords=[],
        blocked_companies=[], allow_remote=False, resumes={}, resume_text="", search_queries=[],
    )


class FakeApify:
    """Mimics the bits of ApifyClient the source uses."""

    def __init__(self, items, error=None, run=None):
        self.items = items
        self.error = error
        self.run = run or SimpleNamespace(default_dataset_id="ds1", status="SUCCEEDED", status_message="")
        self.calls = []

    def actor(self, actor_id):
        outer = self

        class _Actor:
            def call(self, run_input=None, **kw):
                outer.calls.append((actor_id, run_input))
                outer.call_kwargs = kw
                if outer.error:
                    raise outer.error
                return outer.run

        return _Actor()

    def dataset(self, ds_id):
        assert ds_id == "ds1"
        return SimpleNamespace(iterate_items=lambda: iter(self.items))


def _queries(tmp_path, body):
    p = tmp_path / "q.yaml"
    p.write_text(body)
    return p


QUERIES = """
actor: harvestapi/linkedin-post-search
max_posts_per_person: 30
alex:
  - q one
  - q two
sam:
  - q three
"""


def test_extract_emails():
    assert extract_emails("mail cv to HR@Acme.in or hr@acme.in; logo@2x.png") == ["hr@acme.in"]


def test_extract_emails_ignores_example_com_and_trailing_dot():
    assert extract_emails("a@example.com, b@corp.co.in.") == ["b@corp.co.in"]


def test_canonical_post_url_drops_query_fragment_and_slash():
    u = "https://www.linkedin.com/posts/x_y-activity-1-AbCd/?utm_source=share#frag"
    assert canonical_post_url(u) == "https://www.linkedin.com/posts/x_y-activity-1-AbCd"


def test_linkedin_posts_maps_fixture(tmp_path):
    items = json.loads(FIXTURE.read_text())
    apify = FakeApify(items)
    src = LinkedInPostsSource(apify, "harvestapi/linkedin-post-search", _queries(tmp_path, QUERIES))
    out = src.fetch(_profile("alex"), settings=None)

    # one actor run per person, with all that person's queries and the budget cap
    assert len(apify.calls) == 1
    actor_id, run_input = apify.calls[0]
    assert actor_id == "harvestapi/linkedin-post-search"
    assert run_input["searchQueries"] == ["q one", "q two"]
    assert run_input["maxPosts"] == 15
    assert run_input["postedLimit"] == "24h"
    assert run_input["sortBy"] == "date"

    assert len(out) == 3
    person_post = next(i for i in out if i.poster_name == "Jane Recruiter")
    assert person_post.kind == "post"
    assert person_post.source == "linkedin_posts"
    assert person_post.external_id == "7000000000000000001"
    assert person_post.url == "https://www.linkedin.com/posts/jane-recruiter-1a2b3c_hiring-activity-7000000000000000001-AbCd"
    assert person_post.company == "Acme Labs"  # parsed from "Talent Partner at Acme Labs"
    assert person_post.contact_email == "jane.recruiter@acme-example.org"
    assert person_post.title == person_post.description.splitlines()[0][:120]
    assert len(person_post.title) <= 120
    assert person_post.posted_at is not None and person_post.posted_at.tzinfo is not None
    assert person_post.applicant_count is None

    company_post = next(i for i in out if i.poster_name == "Acme Labs")
    assert company_post.company == "Acme Labs"  # company-type author

    no_email = next(i for i in out if i.poster_name == "Sam Hiring")
    assert no_email.contact_email is None
    assert no_email.company == ""


def test_linkedin_posts_dedupes_urls_and_skips_person_without_queries(tmp_path):
    items = json.loads(FIXTURE.read_text())
    dup = dict(items[0], linkedinUrl=items[0]["linkedinUrl"].split("?")[0] + "/?x=1")
    apify = FakeApify(items + [dup])
    src = LinkedInPostsSource(apify, "a/b", _queries(tmp_path, QUERIES))
    assert len(src.fetch(_profile("alex"), None)) == 3
    assert src.fetch(_profile("nobody"), None) == []
    assert len(apify.calls) == 1


def test_queries_capped_at_four_and_total_posts_within_budget(tmp_path):
    body = "actor: a/b\nmax_posts_per_person: 30\nalex: [a, b, c, d, e, f, g, h]\n"
    apify = FakeApify([])
    LinkedInPostsSource(apify, "a/b", _queries(tmp_path, body)).fetch(_profile("alex"), None)
    run_input = apify.calls[0][1]
    assert run_input["searchQueries"] == ["a", "b", "c", "d"]
    assert len(run_input["searchQueries"]) * run_input["maxPosts"] <= 30


def test_actor_call_has_timeout(tmp_path):
    apify = FakeApify([])
    LinkedInPostsSource(apify, "a/b", _queries(tmp_path, QUERIES)).fetch(_profile("alex"), None)
    assert apify.call_kwargs["run_timeout"] == timedelta(seconds=300)


def test_failed_run_status_raises_runtime_error(tmp_path):
    run = SimpleNamespace(default_dataset_id="ds1", status="TIMED-OUT", status_message="")
    src = LinkedInPostsSource(FakeApify([], run=run), "a/b", _queries(tmp_path, QUERIES))
    with pytest.raises(RuntimeError, match="TIMED-OUT"):
        src.fetch(_profile("alex"), None)


def test_none_run_raises_runtime_error(tmp_path):
    src = LinkedInPostsSource(FakeApify([]), "a/b", _queries(tmp_path, QUERIES))
    src.apify.run = None
    # FakeApify falls back to a default run when None is passed, so set it directly
    with pytest.raises(RuntimeError):
        src.fetch(_profile("alex"), None)


def test_failed_run_with_credit_message_is_skipped(tmp_path):
    run = SimpleNamespace(default_dataset_id="ds1", status="FAILED", status_message="Insufficient credit to run")
    src = LinkedInPostsSource(FakeApify([], run=run), "a/b", _queries(tmp_path, QUERIES))
    with pytest.raises(SourceSkipped, match="quota"):
        src.fetch(_profile("alex"), None)


def test_errors_attribute_reset_per_fetch(tmp_path):
    src = LinkedInPostsSource(FakeApify([]), "a/b", _queries(tmp_path, QUERIES))
    src.errors.append("stale")
    src.fetch(_profile("alex"), None)
    assert src.errors == []


def test_extract_emails_trims_run_on_and_bad_local_parts():
    assert extract_emails("write to hr@acme.in.Thanks for reading") == ["hr@acme.in"]
    assert extract_emails(".bad@acme.in %x@acme.in ok@acme.co.in") == ["ok@acme.co.in"]


def test_mailto_link_in_content_attributes_is_used(tmp_path):
    item = json.loads(FIXTURE.read_text())[2]
    item["contentAttributes"] = [{"type": "TEXT_LINK", "textLink": "mailto:Jobs@Corp.in?subject=hi"}]
    src = LinkedInPostsSource(FakeApify([item]), "a/b", _queries(tmp_path, QUERIES))
    assert src.fetch(_profile("alex"), None)[0].contact_email == "jobs@corp.in"


def _api_error(status, body):
    resp = httpx.Response(status, json=body, request=httpx.Request("POST", "https://api.apify.com/v2/x"))
    return ApifyApiError(resp, 1)


def test_apify_credit_error_raises_source_skipped(tmp_path):
    err = _api_error(402, {"error": {"type": "not-enough-usage-to-run-paid-actor", "message": "Insufficient credit"}})
    src = LinkedInPostsSource(FakeApify([], error=err), "a/b", _queries(tmp_path, QUERIES))
    with pytest.raises(SourceSkipped, match="quota"):
        src.fetch(_profile("alex"), None)


def test_apify_usage_limit_message_raises_source_skipped(tmp_path):
    err = _api_error(403, {"error": {"type": "platform-feature-disabled", "message": "Monthly usage hard limit exceeded"}})
    src = LinkedInPostsSource(FakeApify([], error=err), "a/b", _queries(tmp_path, QUERIES))
    with pytest.raises(SourceSkipped, match="quota"):
        src.fetch(_profile("alex"), None)


def test_other_apify_errors_propagate(tmp_path):
    err = _api_error(400, {"error": {"type": "invalid-input", "message": "bad input"}})
    src = LinkedInPostsSource(FakeApify([], error=err), "a/b", _queries(tmp_path, QUERIES))
    with pytest.raises(ApifyApiError):
        src.fetch(_profile("alex"), None)
