"""Postgres repository. Base deps only (psycopg, stdlib): imported by the web app.

Every function commits its own work.
"""
from __future__ import annotations

import functools
from datetime import date
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json

from ezapply.core.models import RawItem, Scored

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def connect(url: str) -> psycopg.Connection:
    # prepare_threshold=None: Neon's pooler (PgBouncer, transaction mode) rejects server-side prepared statements.
    return psycopg.connect(url, row_factory=dict_row, autocommit=False, prepare_threshold=None)


def _tx(fn):
    """Commit on success; roll back and re-raise on failure, so the connection stays usable."""
    @functools.wraps(fn)
    def wrapper(conn, *args, **kwargs):
        try:
            result = fn(conn, *args, **kwargs)
            conn.commit()
            return result
        except BaseException:
            conn.rollback()
            raise
    return wrapper

MIGRATION_LOCK_KEY = 727001


def apply_migrations(conn: psycopg.Connection) -> None:
    # One transaction per migration; a transaction-scoped advisory lock serialises concurrent
    # runners and (unlike a session lock) is safe behind PgBouncer. It releases on commit/rollback.
    try:
        conn.execute(
            "create table if not exists schema_migrations ("
            "name text primary key, applied_at timestamptz default now())"
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        try:
            conn.execute("select pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_KEY,))
            done = conn.execute("select 1 from schema_migrations where name=%s", (path.name,)).fetchone()
            if not done:
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (name) values (%s)", (path.name,))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


@_tx
def known_keys(conn: psycopg.Connection, person: str, keys: list[str]) -> set[str]:
    """Keys this person has already been evaluated on within the last 30 days."""
    if not keys:
        return set()
    rows = conn.execute(
        "select job_id from seen where person = %s and job_id = any(%s) "
        "and first_seen_at > now() - interval '30 days'",
        (person, list(keys)),
    ).fetchall()
    return {r["job_id"] for r in rows}


@_tx
def match_keys(conn: psycopg.Connection, person: str, run_date: date) -> set[str]:
    rows = conn.execute("select job_id from matches where person=%s and run_date=%s",
                        (person, run_date)).fetchall()
    return {r["job_id"] for r in rows}


@_tx
def mark_seen(conn: psycopg.Connection, person: str, keys: list[str]) -> None:
    if not keys:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "insert into seen (person, job_id) values (%s, %s) on conflict do nothing",
            [(person, k) for k in keys],
        )


@_tx
def upsert_jobs(conn: psycopg.Connection, items: list[RawItem]) -> None:
    sql = """
        insert into jobs (id, kind, source, url, title, company, location, description,
                          posted_at, applicant_count, contact_email, poster_name)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (id) do update set
          kind=excluded.kind, source=excluded.source, url=excluded.url, title=excluded.title,
          company=excluded.company, location=excluded.location, description=excluded.description,
          posted_at=excluded.posted_at, applicant_count=excluded.applicant_count,
          contact_email=excluded.contact_email, poster_name=excluded.poster_name
    """
    with conn.cursor() as cur:
        cur.executemany(sql, [
            (i.key, i.kind, i.source, i.url, i.title, i.company, i.location, i.description,
             i.posted_at, i.applicant_count, i.contact_email, i.poster_name)
            for i in items
        ])


@_tx
def save_matches(conn: psycopg.Connection, person: str, run_date: date, scored: list[Scored]) -> None:
    sql = """
        insert into matches (job_id, person, rule_pct, ai_pct, match_pct, reason, gap, resume_variant, run_date)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict (job_id, person) do update set
          rule_pct=excluded.rule_pct, ai_pct=excluded.ai_pct, match_pct=excluded.match_pct,
          reason=excluded.reason, gap=excluded.gap, resume_variant=excluded.resume_variant,
          run_date=excluded.run_date
    """
    with conn.cursor() as cur:
        cur.executemany(sql, [
            (s.item.key, person, s.rule_pct, s.ai_pct, s.match_pct, s.reason, s.gap,
             s.resume_variant, run_date)
            for s in scored
        ])


@_tx
def insert_draft(conn: psycopg.Connection, person: str, job_id: str, to_email: str,
                 subject: str, body: str, resume_variant: str) -> UUID:
    row = conn.execute(
        "insert into drafts (person, job_id, to_email, subject, body, resume_variant) "
        "values (%s,%s,%s,%s,%s,%s) returning id",
        (person, job_id, to_email, subject, body, resume_variant),
    ).fetchone()
    return row["id"]


@_tx
def find_open_draft(conn: psycopg.Connection, person: str, job_id: str) -> UUID | None:
    """Existing not-yet-sent draft for this person and job (so a retry reuses it)."""
    row = conn.execute(
        "select id from drafts where person = %s and job_id = %s and status in ('new','edited') "
        "order by created_at desc limit 1",
        (person, job_id),
    ).fetchone()
    return row["id"] if row else None


_DRAFT_SELECT = """
    select d.*, j.title, j.company, j.url
    from drafts d join jobs j on j.id = d.job_id
"""


@_tx
def get_draft(conn: psycopg.Connection, draft_id: UUID, person: str) -> dict | None:
    row = conn.execute(_DRAFT_SELECT + " where d.id = %s and d.person = %s", (draft_id, person)).fetchone()
    return row


@_tx
def list_drafts(conn: psycopg.Connection, person: str, limit: int = 100) -> list[dict]:
    rows = conn.execute(
        _DRAFT_SELECT + " where d.person = %s order by d.created_at desc limit %s", (person, limit)
    ).fetchall()
    return rows


@_tx
def update_draft(conn: psycopg.Connection, draft_id: UUID, person: str, *, to_email: str,
                 subject: str, body: str, resume_variant: str, status: str) -> bool:
    cur = conn.execute(
        "update drafts set to_email=%s, subject=%s, body=%s, resume_variant=%s, status=%s, "
        "updated_at=now() where id=%s and person=%s",
        (to_email, subject, body, resume_variant, status, draft_id, person),
    )
    return cur.rowcount == 1


@_tx
def get_run(conn: psycopg.Connection, run_date: date) -> dict | None:
    row = conn.execute("select * from runs where run_date = %s", (run_date,)).fetchone()
    return row


@_tx
def start_run(conn: psycopg.Connection, run_date: date) -> None:
    conn.execute(
        "insert into runs (run_date, started_at, status) values (%s, now(), 'running') "
        "on conflict (run_date) do nothing",
        (run_date,),
    )


@_tx
def finish_run(conn: psycopg.Connection, run_date: date, status: str, stats: dict, errors: list) -> None:
    cur = conn.execute(
        "update runs set finished_at=now(), status=%s, stats=%s, errors=%s where run_date=%s",
        (status, Json(stats), Json(errors), run_date),
    )
    if cur.rowcount != 1:
        raise LookupError(f"no runs row for {run_date}; call start_run first")


@_tx
def mark_digest_sent(conn: psycopg.Connection, run_date: date, person: str) -> None:
    cur = conn.execute(
        "update runs set digest_sent = case when digest_sent @> to_jsonb(%s::text) "
        "then digest_sent else digest_sent || to_jsonb(%s::text) end where run_date=%s",
        (person, person, run_date),
    )
    if cur.rowcount != 1:
        raise LookupError(f"no runs row for {run_date}; call start_run first")


@_tx
def record_login_failure(conn: psycopg.Connection, ip: str) -> None:
    conn.execute("insert into login_failures (ip) values (%s)", (ip,))


@_tx
def recent_login_failures(conn: psycopg.Connection, ip: str, minutes: int = 15) -> int:
    row = conn.execute(
        "select count(*) as n from login_failures where ip=%s and at > now() - make_interval(mins => %s)",
        (ip, minutes),
    ).fetchone()
    return row["n"]


@_tx
def purge_old(conn: psycopg.Connection, days: int = 60) -> None:
    """Delete jobs (and, via cascade, their matches/drafts) older than `days`,
    keeping any job referenced by a draft younger than `days`."""
    conn.execute(
        "delete from jobs where first_seen_at < now() - make_interval(days => %s) "
        "and not exists (select 1 from drafts d where d.job_id = jobs.id "
        "and d.created_at >= now() - make_interval(days => %s))",
        (days, days),
    )
    conn.execute("delete from login_failures where at < now() - interval '1 day'")
