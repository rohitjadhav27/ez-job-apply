import pytest
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

from ezapply.core.db import (
    apply_migrations, finish_run, find_open_draft, get_draft, get_run, insert_draft, known_keys, mark_seen,
    list_drafts, mark_digest_sent, purge_old, recent_login_failures,
    record_login_failure, save_matches, start_run, update_draft, upsert_jobs,
)
from ezapply.core.db import MIGRATIONS_DIR
from ezapply.core.models import RawItem, Scored

D = date(2026, 10, 20)


def item(key="k1", **kw):
    base = dict(source="linkedin", external_id=key, url=f"https://x/{key}", title="Engineer",
                company="Acme", location="Pune", description="desc", posted_at=None,
                applicant_count=None, contact_email=None, poster_name=None,
                kind="listing", key=key)
    base.update(kw)
    return RawItem(**base)


def age_job(conn, key, days):
    conn.execute("UPDATE jobs SET first_seen_at = now() - make_interval(days => %s) WHERE id=%s", (days, key))
    conn.commit()


def test_migrations_idempotent(conn):
    apply_migrations(conn)
    apply_migrations(conn)
    n = conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n == len(list(MIGRATIONS_DIR.glob("*.sql")))


def test_upsert_jobs_is_idempotent_and_updates(conn):
    upsert_jobs(conn, [item("a", title="Old")])
    upsert_jobs(conn, [item("a", title="New", applicant_count=5, posted_at=datetime(2026, 10, 1, tzinfo=timezone.utc))])
    rows = conn.execute("SELECT * FROM jobs").fetchall()
    assert len(rows) == 1 and rows[0]["title"] == "New" and rows[0]["applicant_count"] == 5


def age_seen(conn, person, key, days):
    conn.execute("update seen set first_seen_at = now() - make_interval(days => %s) "
                 "where person=%s and job_id=%s", (days, person, key))
    conn.commit()


def test_known_keys_is_per_person_and_only_recent(conn):
    upsert_jobs(conn, [item("fresh"), item("old"), item("other")])
    mark_seen(conn, "alex", ["fresh", "old"])
    mark_seen(conn, "sam", ["other"])
    age_seen(conn, "alex", "old", 31)
    assert known_keys(conn, "alex", ["fresh", "old", "other", "never"]) == {"fresh"}
    assert known_keys(conn, "sam", ["fresh", "other"]) == {"other"}
    assert known_keys(conn, "alex", []) == set()


def test_mark_seen_is_idempotent_and_keeps_first_seen(conn):
    upsert_jobs(conn, [item("a")])
    mark_seen(conn, "alex", ["a"])
    age_seen(conn, "alex", "a", 31)
    mark_seen(conn, "alex", ["a", "a"])
    assert known_keys(conn, "alex", ["a"]) == set()
    mark_seen(conn, "alex", [])


def test_seen_rows_cascade_with_job_purge(conn):
    upsert_jobs(conn, [item("a")])
    mark_seen(conn, "alex", ["a"])
    age_job(conn, "a", 61)
    purge_old(conn)
    assert conn.execute("select count(*) as n from seen").fetchone()["n"] == 0


def test_save_matches_upserts(conn):
    upsert_jobs(conn, [item("a")])
    save_matches(conn, "alex", D, [Scored(item("a"), rule_pct=60, ai_pct=80, match_pct=75, reason="r", gap="g", resume_variant="v")])
    save_matches(conn, "alex", D, [Scored(item("a"), rule_pct=60, match_pct=90)])
    rows = conn.execute("SELECT * FROM matches").fetchall()
    assert len(rows) == 1
    assert rows[0]["job_id"] == "a" and rows[0]["match_pct"] == 90 and rows[0]["ai_pct"] is None
    assert rows[0]["run_date"] == D


def _draft(conn, person="alex", key="a"):
    upsert_jobs(conn, [item(key, title="Dev", company="Acme")])
    return insert_draft(conn, person, key, "hr@acme.com", "Subj", "Body", "v1")


def test_insert_and_get_draft_joins_job(conn):
    did = _draft(conn)
    d = get_draft(conn, did, "alex")
    assert d["to_email"] == "hr@acme.com" and d["status"] == "new"
    assert d["title"] == "Dev" and d["company"] == "Acme" and d["url"] == "https://x/a"
    assert [x["id"] for x in list_drafts(conn, "alex")] == [did]
    assert list_drafts(conn, "sam") == []


def test_get_draft_wrong_person_returns_none(conn):
    did = _draft(conn)
    assert get_draft(conn, did, "sam") is None


def test_update_draft_sets_status_and_updated_at(conn):
    did = _draft(conn)
    before = get_draft(conn, did, "alex")
    assert before["updated_at"] is None
    ok = update_draft(conn, did, "alex", to_email="x@y.com", subject="S2", body="B2", resume_variant="v2", status="edited")
    assert ok is True
    after = get_draft(conn, did, "alex")
    assert (after["to_email"], after["subject"], after["body"], after["resume_variant"], after["status"]) == ("x@y.com", "S2", "B2", "v2", "edited")
    assert after["updated_at"] is not None
    assert update_draft(conn, did, "sam", to_email="z", subject="z", body="z", resume_variant="z", status="opened") is False
    assert get_draft(conn, did, "alex")["subject"] == "S2"


def test_run_lifecycle(conn):
    assert get_run(conn, D) is None
    start_run(conn, D)
    start_run(conn, D)  # idempotent
    assert get_run(conn, D)["status"] == "running"
    finish_run(conn, D, "ok", {"jobs": 3}, ["e1"])
    r = get_run(conn, D)
    assert r["status"] == "ok" and r["stats"] == {"jobs": 3} and r["errors"] == ["e1"] and r["finished_at"] is not None


def test_mark_digest_sent_accumulates_people(conn):
    start_run(conn, D)
    mark_digest_sent(conn, D, "alex")
    mark_digest_sent(conn, D, "sam")
    mark_digest_sent(conn, D, "sam")
    sent = get_run(conn, D)["digest_sent"]
    assert set(sent) == {"alex", "sam"} and len(sent) == 2


def test_login_failures_window(conn):
    record_login_failure(conn, "1.1.1.1")
    record_login_failure(conn, "1.1.1.1")
    record_login_failure(conn, "2.2.2.2")
    conn.execute("UPDATE login_failures SET at = now() - interval '20 minutes' WHERE ip='2.2.2.2'")
    conn.commit()
    assert recent_login_failures(conn, "1.1.1.1") == 2
    assert recent_login_failures(conn, "2.2.2.2") == 0
    assert recent_login_failures(conn, "2.2.2.2", minutes=30) == 1


def test_purge_old_keeps_drafted_jobs(conn):
    upsert_jobs(conn, [item("stale"), item("drafted"), item("drafted_old"), item("fresh")])
    for k in ("stale", "drafted", "drafted_old"):
        age_job(conn, k, 90)
    save_matches(conn, "alex", D, [Scored(item("stale"), rule_pct=1), Scored(item("fresh"), rule_pct=1)])
    insert_draft(conn, "alex", "drafted", "a@b.c", "s", "b", "v")
    old = insert_draft(conn, "alex", "drafted_old", "a@b.c", "s", "b", "v")
    conn.execute("UPDATE drafts SET created_at = now() - interval '61 days' WHERE id=%s", (old,))
    conn.commit()
    purge_old(conn, days=60)
    ids = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    assert ids == {"drafted", "fresh"}
    assert {r["job_id"] for r in conn.execute("SELECT job_id FROM matches").fetchall()} == {"fresh"}
    assert get_draft(conn, old, "alex") is None


def test_failed_call_rolls_back_and_connection_stays_usable(conn):
    with pytest.raises(Exception) as ei:
        insert_draft(conn, "alex", "missing-job", "a@b.c", "s", "b", "v")
    assert "InFailedSqlTransaction" not in type(ei.value).__name__
    assert conn.info.transaction_status.name == "IDLE"
    assert list_drafts(conn, "alex") == []


def test_run_updates_require_existing_run(conn):
    with pytest.raises(LookupError):
        mark_digest_sent(conn, D, "alex")
    with pytest.raises(LookupError):
        finish_run(conn, D, "ok", {}, [])
    assert get_run(conn, D) is None


def test_find_open_draft_only_new_or_edited_per_person(conn):
    did = _draft(conn)
    assert find_open_draft(conn, "alex", "a") == did
    assert find_open_draft(conn, "sam", "a") is None
    assert find_open_draft(conn, "alex", "zzz") is None
    update_draft(conn, did, "alex", to_email="x@y.com", subject="S", body="B", resume_variant="v", status="edited")
    assert find_open_draft(conn, "alex", "a") == did
    update_draft(conn, did, "alex", to_email="x@y.com", subject="S", body="B", resume_variant="v", status="sent")
    assert find_open_draft(conn, "alex", "a") is None
