"""Stage 2 LLM shortlist-probability judge."""
from __future__ import annotations

from ezapply.core.models import Profile, Scored
from ezapply.llm.client import LLMClient, LLMUnavailable
from ezapply.llm.untrusted import SYSTEM_RULE, untrusted, wrap_job_post

BATCH = 5
RESUME_CHARS = 6000
ITEM_CHARS = 1000

_SYSTEM = """You are a strict recruiter screening candidates. For each job, estimate the probability (0-100) that this candidate gets shortlisted. Be strict: a mediocre fit should score low.
{rule}
Answer ONLY with JSON of this exact shape, one entry per job, using the job's index i:
{{"items":[{{"i":0,"shortlist_pct":0,"reason":"<=20 words","gap":"<=20 words","resume_variant":"<key>"}}]}}
resume_variant must be exactly one of these keys:
{variants}"""


def _usable(profile: Profile) -> dict:
    """Resume variants that can actually be attached (non-empty Drive URL)."""
    return {k: v for k, v in profile.resumes.items() if v.drive_url}


def _system(profile: Profile) -> str:
    variants = "\n".join(f"- {k}: {v.use_for}" for k, v in _usable(profile).items())
    return _SYSTEM.format(variants=variants, rule=SYSTEM_RULE)


def _user(profile: Profile, batch: list[Scored]) -> str:
    parts = [f"CANDIDATE RESUME:\n{profile.resume_text[:RESUME_CHARS]}\n\nJOBS:"]
    for i, s in enumerate(batch):
        it = s.item
        body = (f"Title: {it.title[:ITEM_CHARS]}\nCompany: {it.company[:ITEM_CHARS]}\n"
                f"Location: {it.location[:ITEM_CHARS]}\nDescription: {it.description[:ITEM_CHARS]}")
        parts.append("\n" + wrap_job_post(untrusted(body), i))
    return "\n".join(parts)


def _variant(raw, profile: Profile) -> str:
    keys = list(_usable(profile))
    v = str(raw).strip().lower()
    for k in keys:
        if k.lower() == v:
            return k
    return keys[0] if keys else ""


def judge(llm: LLMClient, profile: Profile, items: list[Scored]) -> list[Scored]:
    system = _system(profile)
    for start in range(0, len(items), BATCH):
        batch = items[start:start + BATCH]
        try:
            data = llm.complete_json(system, _user(profile, batch), ["items"])
        except LLMUnavailable:
            continue
        rows = data.get("items")
        if not isinstance(rows, list):
            continue
        for row in rows:
            try:
                i = int(row["i"])
                pct = round(float(row["shortlist_pct"]))
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not 0 <= i < len(batch):
                continue
            s = batch[i]
            s.ai_pct = max(0, min(100, pct))
            s.reason = str(row.get("reason", ""))
            s.gap = str(row.get("gap", ""))
            s.resume_variant = _variant(row.get("resume_variant", ""), profile)
    return items
