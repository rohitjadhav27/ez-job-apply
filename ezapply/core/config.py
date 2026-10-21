"""Settings from env and profiles from YAML. Imports no pipeline-only libraries."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import yaml

from ezapply.core.models import Profile, ResumeVariant

PASSWORD_HASH_PREFIX = "PASSWORD_HASH_"


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str
    min_interval_seconds: float = 0.0


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_secret: str
    min_match_pct: int = 75
    max_age_days: int = 7
    judge_top_n: int = 30
    max_items_per_digest: int = 25
    send_empty_digest: bool = True
    link_ttl_hours: int = 72
    apify_token: str = ""
    sender_email: str = ""
    sender_app_password: str = ""
    app_base_url: str = ""
    llm_providers: list[ProviderConfig] = field(default_factory=list)
    password_hashes: dict[str, str] = field(default_factory=dict)
    cookie_secure: bool = True


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key, "").strip()
    return int(raw) if raw else default


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"Missing required environment variable: {key}")
    return value


# Free-tier per-minute limits: space calls out per provider (override with LLM_<NAME>_MIN_INTERVAL_SECONDS).
DEFAULT_MIN_INTERVALS = {"gemini": 7.0, "groq": 3.0}


def _providers(env: Mapping[str, str]) -> list[ProviderConfig]:
    names = [n.strip() for n in env.get("LLM_PROVIDERS", "gemini,groq").split(",") if n.strip()]
    out = []
    for name in names:
        up = name.upper()
        key = env.get(f"LLM_{up}_API_KEY", "").strip()
        if not key:
            continue
        out.append(ProviderConfig(
            name=name,
            base_url=env.get(f"LLM_{up}_BASE_URL", "").strip(),
            api_key=key,
            model=env.get(f"LLM_{up}_MODEL", "").strip(),
            min_interval_seconds=float(
                env.get(f"LLM_{up}_MIN_INTERVAL_SECONDS", "").strip() or DEFAULT_MIN_INTERVALS.get(name, 0.0)),
        ))
    return out


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    hashes = {}
    for key, value in env.items():
        if key.startswith(PASSWORD_HASH_PREFIX) and value.strip():
            hashes[key[len(PASSWORD_HASH_PREFIX):].lower()] = value.strip()
    return Settings(
        database_url=_required(env, "DATABASE_URL"),
        app_secret=_required(env, "APP_SECRET"),
        min_match_pct=_int(env, "MIN_MATCH_PCT", 75),
        max_age_days=_int(env, "MAX_AGE_DAYS", 7),
        judge_top_n=_int(env, "JUDGE_TOP_N", 30),
        max_items_per_digest=_int(env, "MAX_ITEMS_PER_DIGEST", 25),
        send_empty_digest=_bool(env.get("SEND_EMPTY_DIGEST"), True),
        link_ttl_hours=_int(env, "LINK_TTL_HOURS", 72),
        apify_token=env.get("APIFY_TOKEN", "").strip(),
        sender_email=env.get("SENDER_EMAIL", "").strip(),
        sender_app_password=env.get("SENDER_APP_PASSWORD", "").strip(),
        app_base_url=env.get("APP_BASE_URL", "").strip(),
        llm_providers=_providers(env),
        password_hashes=hashes,
        cookie_secure=_bool(env.get("COOKIE_SECURE"), True),
    )


def load_profiles(dir: Path = Path("profiles")) -> list[Profile]:
    dir = Path(dir)
    profiles = []
    for path in sorted(dir.glob("*.yaml")):
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        text_file = d.get("resume_text_file")
        resume_text = ""
        if text_file:
            tp = Path(text_file)
            if not tp.is_absolute() and not tp.exists():
                tp = dir / tp.name
            resume_text = tp.read_text(encoding="utf-8") if tp.exists() else ""
        exp = d.get("experience_years", {})
        resumes = {
            k: ResumeVariant(key=k, drive_url=v.get("drive_url", "") or "", use_for=v.get("use_for", ""))
            for k, v in (d.get("resumes") or {}).items()
        }
        profiles.append(Profile(
            id=d["id"], name=d["name"], email=d["email"], phone=d.get("phone", ""),
            cities=list(d.get("cities", [])),
            exp_min=int(exp.get("min", 0)), exp_max=int(exp.get("max", 99)),
            target_titles=list(d.get("target_titles", [])),
            adjacent_titles=list(d.get("adjacent_titles", [])),
            domain_keywords=list(d.get("domain_keywords", [])),
            exclude_keywords=list(d.get("exclude_keywords", [])),
            blocked_companies=list(d.get("blocked_companies", [])),
            allow_remote=bool(d.get("allow_remote", False)),
            resumes=resumes, resume_text=resume_text,
            search_queries=list(d.get("search_queries", [])),
        ))
    return profiles
