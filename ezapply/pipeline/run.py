"""Daily pipeline: fetch -> rank -> draft -> digest -> send, idempotent per IST day."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Callable

from ezapply.core import db
from ezapply.core.config import Settings, load_profiles, load_settings
from ezapply.core.models import Profile, RawItem, Scored
from ezapply.core.timeutil import now_ist
from ezapply.core.tokens import make_link_token
from ezapply.digest.render import DigestItem, DigestMeta, render_digest
from ezapply.digest.send import send_email
from ezapply.drafting.drafter import DraftError, draft_email
from ezapply.llm.client import LLMUnavailable
from ezapply.ranking.combine import finalize
from ezapply.ranking.filters import passes
from ezapply.ranking.judge import judge
from ezapply.ranking.rules import rule_pct
from ezapply.sources.base import SourceSkipped, normalize_item

DEFAULT_OUT_DIR = Path("out")
log = logging.getLogger("ezapply.pipeline")


def _alive(conn, reconnect: Callable | None):
    """Neon drops connections left idle during the multi-minute fetch; reconnect if needed."""
    try:
        conn.execute("select 1")
        conn.rollback()
        return conn
    except Exception:
        if reconnect is None:
            raise
        try:
            conn.close()
        except Exception:
            pass
        return reconnect()


ERROR_CHARS = 200


def _short_errors(messages: list[str]) -> str:
    """First message capped at 200 chars, plus '(+N more)' when there are several."""
    first = messages[0] if messages else ""
    out = first[:ERROR_CHARS] + ("..." if len(first) > ERROR_CHARS else "")
    return out + (f" (+{len(messages) - 1} more)" if len(messages) > 1 else "")


def _resume_labels(profile: Profile) -> dict[str, str]:
    """key -> short label for the variants that can actually be attached."""
    return {k: (v.use_for.strip() or k.replace("_", " ").replace("-", " ").title())
            for k, v in profile.resumes.items() if v.drive_url}


def _fetch(sources, profile: Profile, settings: Settings, errors: list[str],
           source_errors: dict[str, str]) -> tuple[list[RawItem], dict[str, int]]:
    raw: list[RawItem] = []
    counts: dict[str, int] = {}
    for src in sources:
        name = src.name
        log.info("%s: fetch %s start", profile.id, name)
        t0 = time.time()
        try:
            got = list(src.fetch(profile, settings))
        except SourceSkipped as e:
            msg = f"skipped: {e}"
            source_errors[name] = _short_errors([msg])
            errors.append(f"{profile.id}/{name}: {msg}")
            continue
        except Exception as e:  # one broken source must not stop the run
            msg = f"{type(e).__name__}: {e}"
            source_errors[name] = _short_errors([msg])
            errors.append(f"{profile.id}/{name}: {msg}")
            continue
        counts[name] = len(got)
        log.info("%s: fetch %s done: %d items, %d errors, %.0fs", profile.id, name, len(got),
                 len(src.errors), time.time() - t0)
        raw.extend(got)
        if src.errors:
            source_errors[name] = _short_errors(src.errors)
            errors.append(f"{profile.id}/{name}: " + "; ".join(src.errors))
    return raw, counts


def _process_person(profile: Profile, settings: Settings, conn, sources, llm, embedder, now,
                    run_date, errors: list[str], reconnect=None, force: bool = False):
    """Fetch, rank and save matches. Does NOT mark jobs seen: the caller does that once the
    digest is delivered, so a failed send leaves the same items available for the retry."""
    stamp = now()
    source_errors: dict[str, str] = {}
    raw, counts = _fetch(sources, profile, settings, errors, source_errors)
    conn = _alive(conn, reconnect)

    seen: dict[str, RawItem] = {}
    for it in raw:
        n = normalize_item(it)
        seen.setdefault(n.key, n)
    log.info("%s: normalised %d unique of %d raw", profile.id, len(seen), len(raw))
    db.upsert_jobs(conn, list(seen.values()))  # every fetched item lands in jobs (matches reference it)
    known = db.known_keys(conn, profile.id, list(seen))
    if force:
        # re-sending today's digest: items already delivered today count as new again
        known -= db.match_keys(conn, profile.id, run_date)
    fresh = [it for k, it in seen.items() if k not in known]

    log.info("%s: %d new after dedupe", profile.id, len(fresh))
    candidates = [it for it in fresh if passes(it, profile, settings, stamp)[0]]
    log.info("%s: %d passed filters; embedding", profile.id, len(candidates))
    sims = embedder.similarities(profile.resume_text, [f"{i.title}\n{i.description}" for i in candidates])
    scored = [Scored(item=i, rule_pct=rule_pct(i, profile, s, stamp, settings))
              for i, s in zip(candidates, sims)]
    scored.sort(key=lambda s: s.rule_pct, reverse=True)

    to_judge = scored[: settings.judge_top_n]
    if to_judge:
        log.info("%s: judging %d items", profile.id, len(to_judge))
        llm.last_provider = None  # so the digest names the provider used for THIS person
        judge(llm, profile, to_judge)
        log.info("%s: judge done", profile.id)
        ai_available = any(s.ai_pct is not None for s in to_judge)
    else:
        ai_available = True
    final = finalize(scored, settings, ai_available)

    log.info("%s: saving %d matches", profile.id, len(final))
    conn = _alive(conn, reconnect)
    if final:
        db.save_matches(conn, profile.id, run_date, final)

    if not ai_available:
        ai_status = "AI ranking unavailable today — showing rule scores"
    elif to_judge and llm.last_provider:
        ai_status = f"AI ranking: {llm.last_provider}"
    else:
        ai_status = "AI ranking: no new jobs to judge"

    stats = {"fetched": len(raw), "new": len(fresh), "passed_filters": len(candidates),
             "judged": sum(1 for s in to_judge if s.ai_pct is not None), "included": len(final),
             "drafts": 0, "sent": False}
    return final, stats, counts, source_errors, ai_status, conn, list(seen)


def _draft_items(profile: Profile, settings: Settings, conn, llm, final: list[Scored],
                 errors: list[str]) -> tuple[list[DigestItem], int]:
    items, drafts = [], 0
    for s in final:
        url = None
        if s.item.contact_email:
            try:
                draft_id = db.find_open_draft(conn, profile.id, s.item.key)
                if draft_id is None:
                    log.info("%s: drafting for %s", profile.id, s.item.company)
                    subject, body = draft_email(llm, profile, s)
                    draft_id = db.insert_draft(conn, profile.id, s.item.key, s.item.contact_email,
                                               subject, body, s.resume_variant)
                token = make_link_token(settings.app_secret, profile.id, str(draft_id),
                                        settings.link_ttl_hours)
                url = f"{settings.app_base_url.rstrip('/')}/d/{draft_id}?t={token}"
                drafts += 1
            except (LLMUnavailable, DraftError) as e:
                errors.append(f"{profile.id}/draft {s.item.company}: {type(e).__name__}: {e}")
        items.append(DigestItem(scored=s, apply_url=url))
    return items, drafts


def run(settings: Settings, profiles: list[Profile], *, conn, sources, llm, embedder,
        send: Callable = send_email, now: Callable = now_ist, dry_run: bool = False,
        only_person: str | None = None, force: bool = False,
        out_dir: Path = DEFAULT_OUT_DIR, reconnect: Callable | None = None) -> dict:
    run_date = now().date()
    db.start_run(conn, run_date)

    errors: list[str] = []
    stats: dict[str, dict] = {}
    failed = False

    for profile in profiles:
        if only_person and profile.id != only_person:
            continue
        try:
            conn = _alive(conn, reconnect)
            # re-read each time: an overlapping trigger may have delivered this person meanwhile
            sent_already = set((db.get_run(conn, run_date) or {}).get("digest_sent") or [])
            if profile.id in sent_already and not force:
                continue
            pstats, conn, sent_ok = _do_person(profile, settings, conn, sources, llm, embedder, send,
                                               now, run_date, dry_run, force, out_dir, errors, reconnect)
            stats[profile.id] = pstats
            failed = failed or not sent_ok
        except Exception as e:  # one person's failure must not stop the other's digest
            failed = True
            errors.append(f"{profile.id}: {type(e).__name__}: {e}")
            log.exception("%s: failed", profile.id)
            try:
                conn.rollback()
            except Exception:
                pass

    conn = _alive(conn, reconnect)
    db.purge_old(conn)
    existing = db.get_run(conn, run_date) or {}
    db.finish_run(conn, run_date, "failed" if failed else "ok",
                  {**dict(existing.get("stats") or {}), **stats},
                  list(existing.get("errors") or []) + errors)
    return stats


def _do_person(profile, settings, conn, sources, llm, embedder, send, now, run_date, dry_run, force,
               out_dir, errors, reconnect):
    final, pstats, counts, source_errors, ai_status, conn, seen_keys = _process_person(
        profile, settings, conn, sources, llm, embedder, now, run_date, errors, reconnect, force)
    items, pstats["drafts"] = _draft_items(profile, settings, conn, llm, final, errors)
    meta = DigestMeta(run_date=run_date, person_name=profile.name, source_counts=counts,
                      source_errors=source_errors, ai_status=ai_status,
                      threshold=settings.min_match_pct, resume_labels=_resume_labels(profile))
    subject, html, text = render_digest(items, meta)
    log.info("%s: digest rendered (%d items, %d drafts)", profile.id, len(items), pstats["drafts"])

    if dry_run:  # writes files only: nothing is marked sent or seen
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"digest-{profile.id}.html").write_text(html, encoding="utf-8")
        (out_dir / f"digest-{profile.id}.txt").write_text(text, encoding="utf-8")
        return pstats, conn, True
    if not items and not settings.send_empty_digest:
        conn = _alive(conn, reconnect)
        db.mark_seen(conn, profile.id, seen_keys)
        db.mark_digest_sent(conn, run_date, profile.id)
        return pstats, conn, True
    try:
        log.info("%s: sending digest", profile.id)
        send(settings, profile.email, subject, html, text)
    except Exception as e:
        errors.append(f"{profile.id}/send: {type(e).__name__}: {e}")
        return pstats, conn, False  # not marked seen: the retry sends the same items
    pstats["sent"] = True
    conn = _alive(conn, reconnect)
    db.mark_seen(conn, profile.id, seen_keys)
    db.mark_digest_sent(conn, run_date, profile.id)
    return pstats, conn, True


def _build_sources(settings: Settings) -> list:
    import yaml
    from apify_client import ApifyClient

    from ezapply.sources.ats_source import AtsSource, make_http_client
    from ezapply.sources.jobspy_source import JobSpySource
    from ezapply.sources.linkedin_posts import DEFAULT_QUERIES_PATH, LinkedInPostsSource

    sources: list = [JobSpySource(), AtsSource(make_http_client())]
    if settings.apify_token:
        actor = yaml.safe_load(Path(DEFAULT_QUERIES_PATH).read_text(encoding="utf-8"))["actor"]
        sources.append(LinkedInPostsSource(ApifyClient(settings.apify_token), actor))
    return sources


def config_problems(settings: Settings, dry_run: bool) -> list[str]:
    """Misconfiguration that would make a real (non-dry) run send broken links or fail every LLM call."""
    problems = []
    url = settings.app_base_url.strip().lower()
    local = url.startswith(("http://127.0.0.1", "http://localhost"))
    if not url.startswith("https://") and not (dry_run and local):
        problems.append("APP_BASE_URL must start with https:// (http://127.0.0.1 or localhost only with --dry-run)")
    for p in settings.llm_providers:
        if not p.model.strip():
            problems.append(f"LLM_{p.name.upper()}_MODEL is empty but {p.name} has an API key")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ez Apply daily pipeline")
    ap.add_argument("--dry-run", action="store_true", help="write out/digest-<person>.html, send nothing")
    ap.add_argument("--person", help="only this profile id")
    ap.add_argument("--force", action="store_true", help="ignore digest_sent for today")
    args = ap.parse_args(argv)

    from ezapply.llm.client import LLMClient
    from ezapply.ranking.embed import Embedder

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    problems = config_problems(settings, args.dry_run)
    if problems:
        for msg in problems:
            print(f"config error: {msg}", file=sys.stderr)
        return 2
    profiles = load_profiles()
    conn = db.connect(settings.database_url)
    db.apply_migrations(conn)
    embedder = Embedder()
    log.info("warming embedder")
    embedder.similarities("warm up", ["warm up"])
    started = time.time()
    stats = run(settings, profiles, conn=conn, sources=_build_sources(settings),
                llm=LLMClient(settings.llm_providers), embedder=embedder,
                dry_run=args.dry_run, only_person=args.person, force=args.force,
                reconnect=lambda: db.connect(settings.database_url))
    elapsed = time.time() - started
    for person, s in stats.items():
        print(f"{person}: " + " ".join(f"{k}={v}" for k, v in s.items()))
    try:
        conn.close()
    except Exception:
        pass
    conn = db.connect(settings.database_url)
    row = db.get_run(conn, now_ist().date()) or {}
    for e in row.get("errors") or []:
        print(f"error: {e[:300]}")
    print(f"status={row.get('status')} elapsed={elapsed:.0f}s")
    conn.close()
    return 1 if row.get("status") == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
