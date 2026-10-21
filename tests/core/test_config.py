import pytest

from ezapply.core.config import load_profiles, load_settings

BASE = {"DATABASE_URL": "postgres://x", "APP_SECRET": "s"}


def test_settings_defaults():
    s = load_settings(BASE)
    assert s.min_match_pct == 75 and s.max_age_days == 7 and s.judge_top_n == 30
    assert s.max_items_per_digest == 25 and s.link_ttl_hours == 72 and s.send_empty_digest is True


def test_llm_providers_in_order():
    env = {"DATABASE_URL": "x", "APP_SECRET": "s", "LLM_PROVIDERS": "gemini,groq",
           "LLM_GEMINI_BASE_URL": "g", "LLM_GEMINI_API_KEY": "k1", "LLM_GEMINI_MODEL": "m1",
           "LLM_GROQ_BASE_URL": "q", "LLM_GROQ_API_KEY": "k2", "LLM_GROQ_MODEL": "m2"}
    assert [p.name for p in load_settings(env).llm_providers] == ["gemini", "groq"]


def test_provider_missing_key_is_skipped():
    env = {"DATABASE_URL": "x", "APP_SECRET": "s", "LLM_PROVIDERS": "gemini,groq",
           "LLM_GEMINI_BASE_URL": "g", "LLM_GEMINI_API_KEY": "k1", "LLM_GEMINI_MODEL": "m1"}
    assert [p.name for p in load_settings(env).llm_providers] == ["gemini"]


def test_missing_required_vars_raise_clear_error():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_settings({"APP_SECRET": "s"})
    with pytest.raises(ValueError, match="APP_SECRET"):
        load_settings({"DATABASE_URL": "x"})


def test_optional_fields_and_overrides():
    env = {**BASE, "SEND_EMPTY_DIGEST": "false", "MIN_MATCH_PCT": "80",
           "PASSWORD_HASH_ALEX": "h1", "PASSWORD_HASH_SAM": "h2", "APP_BASE_URL": "https://a"}
    s = load_settings(env)
    assert s.send_empty_digest is False and s.min_match_pct == 80
    assert s.password_hashes == {"alex": "h1", "sam": "h2"}
    assert s.apify_token == "" and s.sender_email == "" and s.app_base_url == "https://a"


def test_load_profiles_reads_both():
    ps = {p.id: p for p in load_profiles()}
    assert ps["alex"].cities == ["Mumbai"]
    assert ps["sam"].cities == ["Mumbai", "Pune", "Bangalore"]
    assert set(ps["sam"].resumes) == {"resume", "cv"}
    assert "Northwind Payments" in ps["alex"].resume_text


def test_provider_min_interval_defaults_and_override():
    env = {"DATABASE_URL": "x", "APP_SECRET": "y", "LLM_PROVIDERS": "gemini,groq,other",
           "LLM_GEMINI_API_KEY": "a", "LLM_GROQ_API_KEY": "b", "LLM_OTHER_API_KEY": "c",
           "LLM_GROQ_MIN_INTERVAL_SECONDS": "5"}
    got = {p.name: p.min_interval_seconds for p in load_settings(env).llm_providers}
    assert got == {"gemini": 7.0, "groq": 5.0, "other": 0.0}


def test_cookie_secure_defaults_true_and_can_be_disabled():
    assert load_settings(BASE).cookie_secure is True
    assert load_settings({**BASE, "COOKIE_SECURE": "false"}).cookie_secure is False
