import itsdangerous.timed

from ezapply.core.tokens import (
    make_link_token,
    make_session,
    read_link_token,
    read_session,
)


def test_link_token_roundtrip():
    t = make_link_token("s", "alex", "abc", 72)
    assert read_link_token("s", t, 72) == ("alex", "abc")


def test_link_tokens_unique():
    assert make_link_token("s", "r", "a", 1) != make_link_token("s", "r", "a", 1)


def test_tampered_token_rejected():
    t = make_link_token("s", "alex", "abc", 72)
    assert read_link_token("s", t[:-2] + "xx", 72) is None


def test_wrong_secret_rejected():
    t = make_link_token("s", "alex", "abc", 72)
    assert read_link_token("other", t, 72) is None


def test_malformed_token_returns_none():
    assert read_link_token("s", "garbage", 72) is None
    assert read_link_token("s", "", 72) is None


def test_expired_token_rejected(monkeypatch):
    t = make_link_token("s", "alex", "abc", 72)
    real = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real() + 73 * 3600)
    assert read_link_token("s", t, 72) is None


def test_session_roundtrip():
    assert read_session("s", make_session("s", "alex")) == "alex"


def test_session_expired(monkeypatch):
    v = make_session("s", "alex")
    real = itsdangerous.timed.time.time
    monkeypatch.setattr(itsdangerous.timed.time, "time", lambda: real() + 8 * 86400)
    assert read_session("s", v) is None


def test_session_is_not_a_link_token():
    assert read_link_token("s", make_session("s", "alex"), 72) is None
    assert read_session("s", make_link_token("s", "alex", "abc", 72)) is None
