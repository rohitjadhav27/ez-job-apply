from datetime import datetime, timedelta

from ezapply.ranking.filters import parse_experience, passes
from tests.ranking.helpers import NOW, make_item, make_profile, make_settings

S = make_settings()


def test_filter_accepts_good_item():
    assert passes(make_item(), make_profile(), S, NOW) == (True, "")


def test_filter_rejects_other_city():
    assert passes(make_item(location="Delhi"), make_profile(), S, NOW) == (False, "city")


def test_filter_rejects_empty_location():
    assert passes(make_item(location=""), make_profile(), S, NOW)[0] is False


def test_filter_remote_needs_allow_remote():
    item = make_item(location="Remote - India")
    assert passes(item, make_profile(allow_remote=False), S, NOW)[0] is False
    assert passes(item, make_profile(allow_remote=True), S, NOW)[0] is True


def test_filter_post_city_from_text():
    ok = make_item(kind="post", location="", title="Hiring PM", description="Join us in Pune!")
    bad = make_item(kind="post", location="", title="Hiring PM", description="Join us in Delhi")
    assert passes(ok, make_profile(), S, NOW)[0] is True
    assert passes(bad, make_profile(), S, NOW) == (False, "city")


def test_filter_rejects_old():
    old = make_item(posted_at=NOW - timedelta(days=8))
    assert passes(old, make_profile(), S, NOW) == (False, "age")
    assert passes(make_item(posted_at=NOW - timedelta(days=6)), make_profile(), S, NOW)[0]


def test_filter_naive_posted_at_is_ist():
    # 2026-10-20 12:00 UTC == 17:30 IST; naive 17:30 is "now", naive 7 days earlier ok
    naive = datetime(2026, 10, 13, 17, 30)
    assert passes(make_item(posted_at=naive), make_profile(), S, NOW)[0] is True
    naive_old = datetime(2026, 10, 13, 17, 29)
    assert passes(make_item(posted_at=naive_old), make_profile(), S, NOW) == (False, "age")


def test_filter_unknown_posted_at_passes():
    assert passes(make_item(posted_at=None), make_profile(), S, NOW)[0] is True


def test_filter_rejects_exclude_keyword():
    item = make_item(title="Product Intern")
    assert passes(item, make_profile(), S, NOW) == (False, "exclude_keyword")
    # whole word only, title only
    assert passes(make_item(title="Internal Tools PM"), make_profile(), S, NOW)[0] is True
    assert passes(make_item(description="intern supervision"), make_profile(), S, NOW)[0] is True


def test_filter_rejects_blocked_company():
    item = make_item(company="EVIL Corp Pvt Ltd")
    assert passes(item, make_profile(), S, NOW) == (False, "blocked_company")


def test_filter_rejects_far_experience():
    p = make_profile()  # 3-8
    assert passes(make_item(description="11-15 years"), p, S, NOW) == (False, "experience")
    assert passes(make_item(description="8-12 years"), p, S, NOW)[0] is True
    assert passes(make_item(description="0-1 years"), p, S, NOW) == (False, "experience")
    assert passes(make_item(description="2-3 years"), p, S, NOW)[0] is True


def test_parse_experience_patterns():
    assert parse_experience("5+ years") == (5, None)
    assert parse_experience("minimum 4 years of work") == (4, None)
    assert parse_experience("3-5 years") == (3, 5)
    assert parse_experience("3 to 5 yrs") == (3, 5)
    assert parse_experience("no numbers here") == (None, None)


def test_filter_multi_city_location_passes_for_any_profile_city():
    item = make_item(location="Bangalore / Mumbai")
    assert passes(item, make_profile(cities=["Mumbai"]), S, NOW) == (True, "")
    assert passes(item, make_profile(cities=["Pune"]), S, NOW) == (False, "city")


def test_admin_is_not_parsed_as_min_experience():
    assert parse_experience("admin 10 years of records") == (None, None)
    assert parse_experience("min 10 years") == (10, None)
    assert parse_experience("Minimum 5 years") == (5, None)
