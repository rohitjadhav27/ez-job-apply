from ezapply.core.models import Scored
from ezapply.ranking.combine import finalize
from tests.ranking.helpers import make_item, make_settings


def sc(rule, ai=None):
    return Scored(make_item(), rule, ai)


def test_finalize_formula():
    s = make_settings(min_match_pct=0)
    a, b, c = sc(60, 90), sc(80), sc(80)
    finalize([a, b], s, True)
    assert (a.match_pct, b.match_pct) == (78, 68)
    finalize([c], s, False)
    assert c.match_pct == 80


def test_finalize_threshold_and_cap():
    s = make_settings(min_match_pct=75, max_items_per_digest=2)
    items = [sc(80), sc(90), sc(76), sc(60), sc(85)]
    out = finalize(items, s, False)
    assert [x.match_pct for x in out] == [90, 85]
    out = finalize([sc(60), sc(75)], make_settings(), False)
    assert [x.match_pct for x in out] == [75]
