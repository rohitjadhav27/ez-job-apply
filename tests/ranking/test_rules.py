from datetime import timedelta

import pytest

from ezapply.ranking import rules
from tests.ranking.helpers import NOW, make_item, make_profile, make_settings

P = make_profile()


def test_title_fit_levels():
    assert rules.title_fit("Senior Product Manager", P) == 100
    assert rules.title_fit("Business Analyst", P) == 60
    assert rules.title_fit("Plumber", P) == 20


def test_seniority_fit():
    assert rules.seniority_fit((5, 6), P) == 100
    assert rules.seniority_fit((8, 12), P) == 100
    assert rules.seniority_fit((None, None), P) == 60
    assert rules.seniority_fit((10, None), P) == 60   # 2 years outside
    assert rules.seniority_fit((None, 1), P) == 60    # 2 years below
    assert rules.seniority_fit((20, None), P) == 0


def test_freshness():
    assert rules.freshness(NOW - timedelta(hours=24), NOW, 7) == 100
    assert rules.freshness(NOW, NOW, 7) == 100
    assert rules.freshness(NOW - timedelta(days=7), NOW, 7) == 0
    assert rules.freshness(NOW - timedelta(days=4), NOW, 7) == 50
    assert rules.freshness(None, NOW, 7) == 50


def test_city_pref():
    assert rules.city_pref(make_item(location="Mumbai"), P) == 100
    assert rules.city_pref(make_item(location="Pune"), P) == 70
    assert rules.city_pref(make_item(location="Bengaluru"), P) == 40
    assert rules.city_pref(make_item(location="Remote"), P) == 40
    assert rules.city_pref(make_item(location="Pune"), make_profile(cities=["Mumbai"])) == 100


def test_competition():
    assert rules.competition(make_item(contact_email="a@b.com", applicant_count=900)) == 100
    assert rules.competition(make_item(applicant_count=10)) == 100
    assert rules.competition(make_item(applicant_count=None)) == 50
    assert rules.competition(make_item(applicant_count=100)) == 80
    assert rules.competition(make_item(applicant_count=600)) == 0


def test_skills_fit():
    item = make_item(title="PM", description="fintech")
    # sim 0.55 -> 0.5 clamp; keywords 1 of 2 -> 0.5 => 0.7*50 + 0.3*50 = 50
    assert rules.skills_fit(0.55, item, P) == 50
    assert rules.skills_fit(0.9, item, P) == 85   # 70 + 15
    assert rules.skills_fit(0.1, item, P) == 15   # 0 + 15
    nokw = make_profile(domain_keywords=[])
    assert rules.skills_fit(0.8, item, nokw) == 70


def test_rule_pct_weights_sum():
    best = make_item(title="Product Manager", location="Mumbai", posted_at=NOW,
                     contact_email="a@b.com", description="fintech payments 4-6 years")
    assert rules.rule_pct(best, P, 0.8, NOW, make_settings()) == 100
    worst = make_item(title="Plumber", location="Bangalore",
                      posted_at=NOW - timedelta(days=7), applicant_count=600,
                      description="x 30+ years")
    # title 20*0.3=6, skills 0, seniority 0, fresh 0, city 40*.05=2, comp 0 => 8
    assert rules.rule_pct(worst, make_profile(cities=["Mumbai", "Pune", "Bangalore"],
                                              domain_keywords=["zzz"]),
                          0.0, NOW, make_settings()) == 8


def test_city_pref_uses_best_matching_city():
    assert rules.city_pref(make_item(location="Bangalore / Mumbai"), P) == 100
    assert rules.city_pref(make_item(location="Bengaluru or Pune"), P) == 70
