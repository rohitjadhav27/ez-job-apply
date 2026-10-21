import httpx
import openai
import pytest

from ezapply.core.config import ProviderConfig
from ezapply.llm.client import LLMClient, LLMUnavailable, extract_json

GEMINI = ProviderConfig("gemini", "http://g", "k1", "gm")
GROQ = ProviderConfig("groq", "http://q", "k2", "qm")


def _req():
    return httpx.Request("POST", "http://x")


def api_error():
    return openai.APIConnectionError(request=_req())


def rate_limit():
    return openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=_req()), body=None)


class FakeOpenAI:
    """Scripted replies per provider name; an Exception instance is raised, a str is returned."""

    def __init__(self, script, log, name, **kwargs):
        self.script, self.log, self.name, self.kwargs = script, log, name, kwargs
        self.chat = self
        self.completions = self

    def create(self, **kw):
        self.log.append((self.name, kw))
        item = self.script[self.name].pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, tuple):  # ("raw", response object) returned as-is
            return item[1]
        msg = type("M", (), {"content": item})()
        choice = type("C", (), {"message": msg})()
        return type("R", (), {"choices": [choice]})()


def build(script, providers=(GEMINI, GROQ)):
    log, sleeps, made = [], [], []

    def make_client(**kwargs):
        made.append(kwargs)
        name = {"k1": "gemini", "k2": "groq"}[kwargs["api_key"]]
        return FakeOpenAI(script, log, name, **kwargs)

    client = LLMClient(list(providers), sleep=sleeps.append, make_client=make_client)
    return client, log, sleeps, made


def test_first_provider_success():
    c, log, sleeps, made = build({"gemini": ['{"ok": true}'], "groq": []})
    assert c.complete_json("sys", "usr", ["ok"]) == {"ok": True}
    assert c.last_provider == "gemini"
    assert sleeps == []
    name, kw = log[0]
    assert kw["model"] == "gm"
    assert kw["response_format"] == {"type": "json_object"}
    assert kw["temperature"] == 0.2
    assert kw["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "usr"}]
    assert made[0]["base_url"] == "http://g"
    assert made[0]["max_retries"] == 0
    assert made[0]["timeout"] == 60
    assert made[0]["default_headers"] == {"User-Agent": "ezapply/0.1"}


def test_falls_back_after_three_failures():
    c, log, sleeps, _ = build({"gemini": [api_error()] * 3, "groq": ['{"ok": 1}']})
    assert c.complete_json("s", "u", ["ok"]) == {"ok": 1}
    assert c.last_provider == "groq"
    assert sleeps == [2, 4]
    assert [n for n, _ in log] == ["gemini"] * 3 + ["groq"]


def rate_limit_after(seconds):
    return openai.RateLimitError(
        "slow down", response=httpx.Response(429, request=_req(), headers={"retry-after": str(seconds)}),
        body=None)


def test_429_waits_default_then_retries_same_provider():
    c, log, sleeps, _ = build({"gemini": [rate_limit(), '{"a": 1}'], "groq": []})
    assert c.complete_json("s", "u", ["a"]) == {"a": 1}
    assert sleeps == [20.0]
    assert [n for n, _ in log] == ["gemini", "gemini"]


def test_429_honours_retry_after_capped_at_60():
    c, _, sleeps, _ = build({"gemini": [rate_limit_after(5), rate_limit_after(500), '{"a": 1}'], "groq": []})
    c.complete_json("s", "u", ["a"])
    assert sleeps == [5.0, 60.0]


def test_429_after_two_waits_moves_to_next_provider_without_exhausting():
    c, log, sleeps, _ = build({"gemini": [rate_limit()] * 3 + ['{"a": 2}'], "groq": ['{"a": 1}', '{"a": 3}']})
    assert c.complete_json("s", "u", ["a"]) == {"a": 1}
    assert sleeps == [20.0, 20.0]
    assert c.last_provider == "groq"
    # gemini is not exhausted: next call tries it again and succeeds
    assert c.complete_json("s", "u", ["a"]) == {"a": 2}
    assert c.last_provider == "gemini"


def test_provider_exhausted_after_three_consecutive_rate_limited_calls():
    c, log, _, _ = build({"gemini": [rate_limit()] * 9, "groq": ['{"a": 1}'] * 4})
    for _ in range(4):
        c.complete_json("s", "u", ["a"])
    assert [n for n, _ in log].count("gemini") == 9  # 3 calls x 3 requests; 4th call skips gemini


def test_success_resets_rate_limit_streak():
    r = rate_limit
    gem = [r(), r(), r()] + [r(), r(), '{"a": 0}'] + [r(), r(), r()] + [r(), r(), r()] + ['{"a": 9}']
    c, log, _, _ = build({"gemini": gem, "groq": ['{"a": 1}'] * 4})
    for _ in range(4):
        c.complete_json("s", "u", ["a"])
    c.complete_json("s", "u", ["a"])
    assert c.last_provider == "gemini"  # without the reset, 3 failed calls would have exhausted it


def test_min_interval_paces_calls_per_provider():
    now = [100.0]
    slow = ProviderConfig("gemini", "http://g", "k1", "gm", min_interval_seconds=7)
    log, sleeps = [], []
    script = {"gemini": ['{"a": 1}', '{"a": 2}', '{"a": 3}']}

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    c = LLMClient([slow], sleep=sleep, clock=lambda: now[0],
                  make_client=lambda **kw: FakeOpenAI(script, log, "gemini", **kw))
    c.complete_json("s", "u", ["a"])
    now[0] += 2
    c.complete_json("s", "u", ["a"])
    now[0] += 10
    c.complete_json("s", "u", ["a"])
    assert sleeps == [5.0]


def test_extracts_json_from_fenced_reply():
    assert extract_json('Sure!\n```json\n{"a":1}\n```') == {"a": 1}


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('blah {"a": {"b": "}"}} trailing') == {"a": {"b": "}"}}
    assert extract_json("[1, 2]") is None
    assert extract_json("no json here") is None
    assert extract_json("") is None


def test_invalid_json_is_retried_on_same_provider():
    c, log, sleeps, _ = build({"gemini": ["not json", '{"ok": 1}'], "groq": []})
    assert c.complete_json("s", "u", ["ok"]) == {"ok": 1}
    assert sleeps == [2]


def test_missing_key_triggers_next_provider():
    c, log, sleeps, _ = build({"gemini": ['{"x": 1}'] * 3, "groq": ['{"ok": 1}']})
    assert c.complete_json("s", "u", ["ok"]) == {"ok": 1}
    assert c.last_provider == "groq"
    assert sleeps == [2, 4]


def test_all_fail_raises():
    c, _, _, _ = build({"gemini": [api_error()] * 3, "groq": [rate_limit()] * 3})
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u", ["ok"])


def test_all_exhausted_raises_on_next_call_without_requests():
    c, log, _, _ = build({"gemini": [rate_limit()] * 9, "groq": [rate_limit()] * 9})
    for _ in range(3):
        with pytest.raises(LLMUnavailable):
            c.complete_json("s", "u", ["ok"])
    assert len(log) == 18
    with pytest.raises(LLMUnavailable):
        c.complete_json("s", "u", ["ok"])
    assert len(log) == 18


def _resp(choices):
    return type("R", (), {"choices": choices})()


def test_empty_or_missing_choices_is_a_failed_attempt_not_a_crash():
    no_msg = _resp([type("C", (), {"message": None})()])
    script = {"gemini": [("raw", _resp([])), ("raw", _resp(None)), ("raw", no_msg)], "groq": ['{"ok": 1}']}
    c, log, _, _ = build(script)
    assert c.complete_json("s", "u", ["ok"]) == {"ok": 1}
    assert c.last_provider == "groq"
    assert [n for n, _ in log] == ["gemini"] * 3 + ["groq"]
