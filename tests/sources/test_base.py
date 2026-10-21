from dataclasses import replace

from ezapply.core.models import RawItem
from ezapply.sources.base import cities_of, city_of, item_key, normalize_item, normalize_text


def make_item(**kw) -> RawItem:
    fields = dict(
        source="linkedin",
        external_id="1",
        url="https://example.com/job/1",
        title="Senior Product Manager",
        company="Razorpay",
        location="Bangalore",
        description="desc",
        posted_at=None,
        applicant_count=None,
        contact_email=None,
        poster_name=None,
        kind="listing",
    )
    fields.update(kw)
    return RawItem(**fields)


def test_same_job_two_sources_one_key():
    a = make_item(company="Razorpay Software Pvt Ltd", location="Bengaluru, Karnataka")
    b = make_item(source="naukri", company="Razorpay Software", title="Senior Product Manager ", location="Bangalore")
    assert item_key(a) == item_key(b)


def test_different_city_different_key():
    assert item_key(make_item(location="Pune")) != item_key(make_item(location="Mumbai"))


def test_post_key_uses_url():
    a = make_item(kind="post", url="https://l.in/p/1", company="X", title="A")
    b = make_item(kind="post", url="https://l.in/p/1", company="Y", title="B")
    c = make_item(kind="post", url="https://l.in/p/2", company="X", title="A")
    assert item_key(a) == item_key(b)
    assert item_key(a) != item_key(c)


def test_city_aliases():
    assert city_of("Navi Mumbai, Maharashtra") == "Mumbai"
    assert city_of("Bengaluru, Karnataka") == "Bangalore"
    assert city_of("BLR") == "Bangalore"
    assert city_of("Thane") == "Mumbai"
    assert city_of("Pune (Hybrid)") == "Pune"
    assert city_of("Delhi") is None


def test_normalize_text_suffixes():
    assert normalize_text("Acme Technologies Pvt. Ltd.") == "acme"
    assert normalize_text("Incredible  Inc") == "incredible"
    assert normalize_text("Senior PM!") == "senior pm"


def test_normalize_strips_nul_and_truncates():
    it = normalize_item(make_item(title="a\x00b", company="c\x00d", description="a\x00b" + "x" * 9000))
    assert "\x00" not in it.description and len(it.description) == 8000
    assert it.title == "ab" and it.company == "cd"
    assert it.key


def test_cities_of_returns_all_matches():
    assert cities_of("Bangalore / Mumbai") == {"Bangalore", "Mumbai"}
    assert cities_of("Bengaluru, Pune (Hybrid)") == {"Bangalore", "Pune"}
    assert cities_of("Delhi") == set()
    assert city_of("Bangalore / Mumbai") == "Bangalore"  # item_key determinism unchanged


def test_normalize_text_strips_india_suffix_but_not_air_india():
    assert normalize_text("Equifax India") == normalize_text("Equifax")
    assert normalize_text("Equifax India Pvt Ltd") == "equifax"
    assert normalize_text("Equifax (India) Private Limited") == "equifax"
    assert normalize_text("Air India") == "air india"
    assert normalize_text("India") == "india"
