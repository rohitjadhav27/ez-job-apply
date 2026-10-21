from html import unescape
from datetime import date, datetime, timezone

from ezapply.core.models import RawItem, Scored
from ezapply.digest.render import DigestItem, DigestMeta, render_digest


def _scored(kind="listing", title="Backend Engineer", pct=92, posted_at=None, **kw):
    item = RawItem(
        source=kw.pop("source", "jobspy"), external_id="1", url="https://example.com/job/1",
        title=title, company="Acme", location="Pune", description="", posted_at=posted_at,
        applicant_count=None, contact_email=None, poster_name=kw.pop("poster_name", None),
        kind=kind, key="k1",
    )
    return Scored(item=item, rule_pct=pct, ai_pct=pct, match_pct=pct,
                  reason=kw.pop("reason", "Strong Python fit"), gap=kw.pop("gap", "No Kafka"),
                  resume_variant=kw.pop("resume_variant", "backend"))


def _meta(**kw):
    base = dict(run_date=date(2025, 10, 21), person_name="Alex",
                source_counts={"jobspy": 42, "ats": 12}, source_errors={},
                ai_status="AI ranking on", threshold=70)
    base.update(kw)
    return DigestMeta(**base)


def test_render_has_both_sections_and_apply_button_only_with_url():
    items = [
        DigestItem(_scored("post", "Hiring SDE"), "https://tool/a/1"),
        DigestItem(_scored("listing", "Job A"), "https://tool/a/2"),
        DigestItem(_scored("listing", "Job B"), None),
    ]
    subject, html, text = render_digest(items, _meta())
    assert subject == "Ez Apply · Tue 21 Oct · 2 matches · 1 hiring posts"
    assert "Hiring posts — send an email" in html and "Top matches" in html
    assert html.index("Hiring posts") < html.index("Top matches")
    assert html.count("Apply with email") == 2
    assert html.count("View &amp; apply") == 2 and html.count("View post") == 1
    assert "https://tool/a/1" in html and "https://tool/a/1" in text
    assert "#1a7f37" in html and "92%" in html


def test_hides_empty_sections():
    _, html, _ = render_digest([DigestItem(_scored("listing"), None)], _meta())
    assert "Hiring posts" not in html and "Top matches" in html


def test_pill_colours_and_day_without_leading_zero():
    items = [DigestItem(_scored(pct=85), None), DigestItem(_scored(pct=75), None)]
    subject, html, _ = render_digest(items, _meta(run_date=date(2025, 10, 1)))
    assert "Wed 1 Oct" in subject
    assert "#0b5cad" in html and "#9a6700" in html


def test_posted_age():
    ist_noon = lambda d: datetime(2025, 10, d, 6, 30, tzinfo=timezone.utc)
    items = [
        DigestItem(_scored(posted_at=ist_noon(21)), None),
        DigestItem(_scored(posted_at=ist_noon(20)), None),
        DigestItem(_scored(posted_at=ist_noon(18)), None),
    ]
    _, html, _ = render_digest(items, _meta())
    assert "today" in html and "1d ago" in html and "3d ago" in html
    _, html2, _ = render_digest([DigestItem(_scored(posted_at=None), None)], _meta())
    assert "ago" not in html2


def test_render_escapes_html_in_job_title():
    items = [DigestItem(_scored(title="<script>x</script>", reason="a <b>b</b>"), None)]
    _, html, text = render_digest(items, _meta())
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "<b>b</b>" not in html
    assert "<script>x</script>" in text  # plain text is not html-escaped


def test_render_no_style_or_svg_tags():
    items = [DigestItem(_scored("post"), "https://t/1"), DigestItem(_scored(), None)]
    _, html, _ = render_digest(items, _meta())
    assert "<style" not in html and "<svg" not in html and "<script" not in html


def test_empty_digest_subject():
    subject, html, text = render_digest([], _meta())
    assert subject == "Ez Apply · Tue 21 Oct · nothing above 70% today"
    assert "Nothing above 70% today — we'll keep looking." in unescape(html)
    assert "Nothing above 70% today" in text
    assert "Job boards 42 · Company careers page 12" in html  # footer still present


def test_footer_lists_failed_sources():
    meta = _meta(source_errors={"linkedin_posts": "apify quota exceeded"}, ai_status="AI off: no key")
    _, html, text = render_digest([DigestItem(_scored(), None)], meta)
    for out in (html, text):
        assert "LinkedIn post" in out and "linkedin_posts" not in out and "apify quota exceeded" in out
        assert "AI off: no key" in out and "≥ 70%" in out


def test_unsafe_urls_are_dropped():
    bad = _scored(title="Evil <job>")
    bad.item.url = "javascript:alert(1)"
    items = [DigestItem(bad, "data:text/html,x"), DigestItem(_scored(title="Fine"), "https://t/ok")]
    _, html, text = render_digest(items, _meta())
    for out in (html, text):
        assert "javascript:" not in out and "data:text" not in out
    assert "Evil &lt;job&gt;" in html and 'href="https://t/ok"' in html
    assert html.count("Apply with email") == 1 and html.count("View &amp; apply") == 1


def test_source_labels():
    items = [DigestItem(_scored(source="linkedin_posts", kind="post"), None),
             DigestItem(_scored(source="ats"), None), DigestItem(_scored(source="naukri"), None),
             DigestItem(_scored(source="weird"), None)]
    _, html, _ = render_digest(items, _meta())
    for label in ("LinkedIn post", "Company careers page", "Naukri", "weird"):
        assert label in html
    assert "Job boards 42" in html


def test_resume_label_shown_only_with_multiple_variants():
    labels = {"backend": "Backend roles", "data": "Data roles"}
    items = [DigestItem(_scored(), None)]
    _, html, text = render_digest(items, _meta(resume_labels=labels))
    assert "Suggested resume: Backend roles" in html and "Resume: Backend roles" in text
    assert "backend<" not in html
    for meta in (_meta(), _meta(resume_labels={"backend": "Backend roles"})):
        _, html, text = render_digest(items, meta)
        assert "Suggested resume" not in html and "Resume:" not in text
