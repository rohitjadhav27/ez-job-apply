import re
from datetime import datetime, timedelta

import pytest

from ezapply.core.db import get_run
from ezapply.core.timeutil import IST
from ezapply.drafting.drafter import DraftError
from ezapply.llm.client import LLMUnavailable
from ezapply.pipeline.run import config_problems, run
from ezapply.sources.base import SourceSkipped
from tests.ranking.helpers import make_item, make_profile, make_settings

DAY1 = datetime(2026, 10, 20, 8, 0, tzinfo=IST)


def settings(**kw):
    base = dict(min_match_pct=50, app_base_url="https://app.test", sender_email="s@x.com")
    base.update(kw)
    return make_settings(**base)


class FakeSource:
    def __init__(self, name, items=None, error=None, errors=None):
        self.name, self.items, self.error = name, items or [], error
        self._errors = errors or []
        self.errors = []
        self.calls = []

    def fetch(self, profile, settings):
        self.calls.append(profile.id)
        self.errors = []
        if self.error:
            raise self.error
        self.errors = list(self._errors)
        return list(self.items)


class FakeLLM:
    def __init__(self, down=False, draft_error=None, ai_pct=90):
        self.down, self.draft_error, self.ai_pct = down, draft_error, ai_pct
        self.last_provider = None
        self.draft_calls = 0

    def complete_json(self, system, user, required_keys, temperature=0.2):
        if self.down:
            raise LLMUnavailable("down")
        if required_keys == ["items"]:
            n = len(re.findall(r'<job_post i="(\d+)">', user))
            self.last_provider = "fakeai"
            return {"items": [{"i": i, "shortlist_pct": self.ai_pct, "reason": "good", "gap": "none",
                               "resume_variant": "resume"} for i in range(n)]}
        self.draft_calls += 1
        if self.draft_error:
            raise self.draft_error
        self.last_provider = "fakeai"
        return {"subject": "Application", "body": "Hello,\n\nI would like to apply.\n\nAlex"}


class FakeEmbedder:
    def similarities(self, resume_text, texts):
        return [0.8] * len(texts)


class Outbox:
    def __init__(self, fail_for=()):
        self.sent, self.fail_for = [], fail_for

    def __call__(self, settings, to, subject, html, text):
        if to in self.fail_for:
            raise RuntimeError("smtp down")
        self.sent.append((to, subject, html, text))


PROFILES = [make_profile(id="alex", name="Alex", email="r@x.com"),
            make_profile(id="sam", name="Sam", email="a@x.com")]


def items(**kw):
    return [make_item(external_id="1", title="Product Manager", company="Acme", **kw),
            make_item(external_id="2", title="Senior Product Manager", company="Beta", **kw)]


def go(conn, sources, *, llm=None, send=None, now=DAY1, **kw):
    send = send if send is not None else Outbox()
    stats = run(kw.pop("settings", settings()), kw.pop("profiles", PROFILES), conn=conn, sources=sources,
                llm=llm or FakeLLM(), embedder=FakeEmbedder(), send=send, now=lambda: now, **kw)
    return stats, send


def test_end_to_end_sends_one_digest_per_person(conn):
    stats, send = go(conn, [FakeSource("jobspy", items())])
    assert sorted(t[0] for t in send.sent) == ["a@x.com", "r@x.com"]
    for p in ("alex", "sam"):
        s = stats[p]
        assert (s["fetched"], s["new"], s["passed_filters"], s["judged"], s["included"], s["sent"]) == \
            (2, 2, 2, 2, 2, True)
    assert "AI ranking: fakeai" in send.sent[0][3]
    run_row = get_run(conn, DAY1.date())
    assert sorted(run_row["digest_sent"]) == ["alex", "sam"]
    assert run_row["status"] == "ok"
    n = conn.execute("select count(*) as n from matches").fetchone()["n"]
    assert n == 4


def test_second_run_same_day_sends_nothing(conn):
    src = FakeSource("jobspy", items())
    go(conn, [src])
    stats, send = go(conn, [src])
    assert send.sent == []
    assert len(src.calls) == 2  # not fetched again
    assert stats == {} or all(not v.get("sent") for v in stats.values())


def test_force_resends(conn):
    src = FakeSource("jobspy", items())
    go(conn, [src])
    stats, send = go(conn, [src], force=True)
    assert len(src.calls) == 4
    assert {t[0] for t in send.sent} == {"a@x.com", "r@x.com"}
    # today's already-delivered items are rebuilt into the re-sent digest, not an empty one
    assert stats["alex"]["included"] == 2 and stats["sam"]["included"] == 2
    assert "Senior Product Manager" in send.sent[0][2]


def test_source_failure_reported_and_run_continues(conn):
    good = FakeSource("ats", items())
    bad = FakeSource("jobspy", error=RuntimeError("boom"))
    skipped = FakeSource("linkedin_posts", error=SourceSkipped("quota"))
    partial = FakeSource("other", [], errors=["q1: x", "q2: y"])
    stats, send = go(conn, [bad, skipped, partial, good], only_person="alex")
    assert len(send.sent) == 1
    html = send.sent[0][2]
    assert "boom" in html and "skipped: quota" in html and "q1: x (+1 more)" in html
    errs = get_run(conn, DAY1.date())["errors"]
    assert any("jobspy" in e and "boom" in e for e in errs)
    assert stats["alex"]["included"] == 2


def test_llm_down_uses_rule_scores_and_says_so(conn):
    stats, send = go(conn, [FakeSource("ats", items())], llm=FakeLLM(down=True), only_person="alex")
    assert "AI ranking unavailable today" in send.sent[0][2]
    assert stats["alex"]["judged"] == 0
    row = conn.execute("select rule_pct, ai_pct, match_pct from matches").fetchone()
    assert row["ai_pct"] is None and row["match_pct"] == row["rule_pct"]


def test_seen_job_not_sent_again_next_day(conn):
    src = FakeSource("ats", items())
    go(conn, [src], only_person="alex")
    stats, send = go(conn, [src], now=DAY1 + timedelta(days=1), only_person="alex")
    assert stats["alex"]["fetched"] == 2 and stats["alex"]["new"] == 0
    assert stats["alex"]["included"] == 0
    assert len(send.sent) == 1  # empty digest still sent (send_empty_digest default True)


def test_empty_digest_not_sent_when_disabled_but_marked(conn):
    stats, send = go(conn, [FakeSource("ats", [])], settings=settings(send_empty_digest=False),
                     only_person="alex")
    assert send.sent == [] and stats["alex"]["sent"] is False
    assert get_run(conn, DAY1.date())["digest_sent"] == ["alex"]


def test_draft_created_only_for_items_with_email(conn):
    its = [make_item(external_id="1", title="Product Manager", company="Acme", contact_email="hr@acme.com"),
           make_item(external_id="2", title="Senior Product Manager", company="Beta")]
    stats, send = go(conn, [FakeSource("ats", its)], only_person="alex")
    assert stats["alex"]["drafts"] == 1
    rows = conn.execute("select to_email, person from drafts").fetchall()
    assert [(r["to_email"], r["person"]) for r in rows] == [("hr@acme.com", "alex")]
    html = send.sent[0][2]
    assert len(re.findall(r"https://app\.test/d/[0-9a-f-]{36}\?t=", html)) >= 1
    assert html.count("/d/") == 1 or len(set(re.findall(r"/d/[0-9a-f-]{36}", html))) == 1


@pytest.mark.parametrize("err", [LLMUnavailable("x"), DraftError("bad")])
def test_draft_failure_keeps_item_without_link(conn, err):
    its = [make_item(external_id="1", title="Product Manager", company="Acme", contact_email="hr@acme.com")]
    llm = FakeLLM(draft_error=err)
    stats, send = go(conn, [FakeSource("ats", its)], llm=llm, only_person="alex")
    assert stats["alex"]["included"] == 1 and stats["alex"]["drafts"] == 0
    assert "/d/" not in send.sent[0][2]
    assert any("draft" in e for e in get_run(conn, DAY1.date())["errors"])


def test_send_failure_does_not_block_other_person_and_fails_run(conn):
    stats, send = go(conn, [FakeSource("ats", items())], send=Outbox(fail_for=("r@x.com",)))
    assert [t[0] for t in send.sent] == ["a@x.com"]
    assert stats["alex"]["sent"] is False and stats["sam"]["sent"] is True
    row = get_run(conn, DAY1.date())
    assert row["status"] == "failed" and row["digest_sent"] == ["sam"]
    assert any("smtp down" in e for e in row["errors"])


def test_dry_run_writes_file_and_does_not_mark_sent(conn, tmp_path):
    stats, send = go(conn, [FakeSource("ats", items())], dry_run=True, only_person="alex", out_dir=tmp_path)
    assert send.sent == []
    assert (tmp_path / "digest-alex.html").read_text(encoding="utf-8").startswith("<")
    assert (tmp_path / "digest-alex.txt").exists()
    assert get_run(conn, DAY1.date())["digest_sent"] == []
    assert stats["alex"]["sent"] is False


def test_dropped_connection_during_fetch_reconnects(conn):
    import psycopg

    class Flaky:
        """Delegates to the real connection until `dead`, as if Neon dropped it mid-fetch."""
        dead = False

        def __getattr__(self, name):
            if self.dead:
                def boom(*a, **k):
                    raise psycopg.OperationalError("the connection is lost")
                return boom
            return getattr(conn, name)

    flaky = Flaky()
    reconnects = []

    class Drop(FakeSource):
        def fetch(self, profile, settings):
            flaky.dead = True
            return super().fetch(profile, settings)

    def reconnect():
        reconnects.append(1)
        return conn

    stats, send = go(flaky, [Drop("ats", items())], only_person="alex", reconnect=reconnect)
    assert reconnects == [1] and stats["alex"]["sent"] is True
    assert get_run(conn, DAY1.date())["digest_sent"] == ["alex"]


def test_same_job_for_both_people_is_evaluated_for_both(conn):
    stats, send = go(conn, [FakeSource("ats", items())])
    assert stats["alex"]["new"] == stats["sam"]["new"] == 2
    rows = conn.execute("select person, count(*) as n from matches group by person").fetchall()
    assert {r["person"]: r["n"] for r in rows} == {"alex": 2, "sam": 2}


def test_job_seen_by_one_person_still_new_for_the_other(conn):
    src = FakeSource("ats", items())
    go(conn, [src], only_person="alex")
    stats, send = go(conn, [src], now=DAY1 + timedelta(days=1))
    assert stats["alex"]["new"] == 0 and stats["alex"]["included"] == 0
    assert stats["sam"]["new"] == 2 and stats["sam"]["included"] == 2


def test_failed_send_is_retried_with_the_same_items(conn):
    src = FakeSource("ats", items())
    stats1, send1 = go(conn, [src], send=Outbox(fail_for=("r@x.com",)), only_person="alex")
    assert send1.sent == [] and stats1["alex"]["sent"] is False
    assert get_run(conn, DAY1.date())["status"] == "failed"
    stats2, send2 = go(conn, [src], only_person="alex")
    assert stats2["alex"]["new"] == 2 and stats2["alex"]["included"] == 2
    assert "Senior Product Manager" in send2.sent[0][2]
    # delivered now, so the next day they are no longer new
    stats3, _ = go(conn, [src], now=DAY1 + timedelta(days=1), only_person="alex")
    assert stats3["alex"]["new"] == 0


def test_dry_run_does_not_consume_items(conn, tmp_path):
    src = FakeSource("ats", items())
    go(conn, [src], dry_run=True, only_person="alex", out_dir=tmp_path)
    stats, send = go(conn, [src], only_person="alex")
    assert stats["alex"]["new"] == 2 and stats["alex"]["included"] == 2 and len(send.sent) == 1


def test_one_person_crashing_does_not_stop_the_other(conn):
    class Boom(FakeEmbedder):
        def similarities(self, resume_text, texts):
            if resume_text == "BOOM":
                raise RuntimeError("embed exploded")
            return super().similarities(resume_text, texts)

    profiles = [make_profile(id="alex", name="Alex", email="r@x.com", resume_text="BOOM"),
                make_profile(id="sam", name="Sam", email="a@x.com")]
    outbox = Outbox()
    stats = run(settings(), profiles, conn=conn, sources=[FakeSource("ats", items())], llm=FakeLLM(),
                embedder=Boom(), send=outbox, now=lambda: DAY1)
    assert [t[0] for t in outbox.sent] == ["a@x.com"] and "sam" in stats and "alex" not in stats
    row = get_run(conn, DAY1.date())
    assert row["status"] == "failed" and row["digest_sent"] == ["sam"]
    assert any(e.startswith("alex: RuntimeError: embed exploded") for e in row["errors"])


def test_ai_status_says_no_new_jobs_when_nothing_was_judged(conn):
    src = FakeSource("ats", items())
    go(conn, [src], only_person="alex")
    llm = FakeLLM()
    llm.last_provider = "stale"
    _, send = go(conn, [src], llm=llm, now=DAY1 + timedelta(days=1), only_person="alex")
    assert "no new jobs to judge" in send.sent[0][2] and "stale" not in send.sent[0][2]


def test_app_base_url_trailing_slash_is_stripped(conn):
    its = [make_item(external_id="1", title="Product Manager", company="Acme", contact_email="hr@acme.com")]
    _, send = go(conn, [FakeSource("ats", its)], settings=settings(app_base_url="https://app.test/"),
                 only_person="alex")
    assert "https://app.test/d/" in send.sent[0][2] and "app.test//d" not in send.sent[0][2]


def test_overlapping_trigger_that_already_delivered_is_respected(conn):
    from ezapply.core.db import mark_digest_sent

    class Racer(FakeSource):
        def fetch(self, profile, settings):
            if not self.calls:  # another trigger delivers sam while this run is on alex
                mark_digest_sent(conn, DAY1.date(), "sam")
            return super().fetch(profile, settings)

    stats, send = go(conn, [Racer("ats", items())])
    assert [t[0] for t in send.sent] == ["r@x.com"] and "sam" not in stats


def test_failed_send_retry_does_not_duplicate_drafts_or_call_llm_again(conn):
    its = [make_item(external_id="1", title="Product Manager", company="Acme", contact_email="hr@acme.com")]
    src, llm = FakeSource("ats", its), FakeLLM()
    go(conn, [src], llm=llm, send=Outbox(fail_for=("r@x.com",)), only_person="alex")
    assert llm.draft_calls == 1
    _, send2 = go(conn, [src], llm=llm, only_person="alex")
    assert llm.draft_calls == 1
    rows = conn.execute("select id from drafts").fetchall()
    assert len(rows) == 1
    assert str(rows[0]["id"]) in send2.sent[0][2]


def test_footer_uses_labels_and_shortens_errors(conn):
    long = "x" * 300
    src = FakeSource("jobspy", items(), errors=[long, "second", "third"])
    _, send = go(conn, [src], only_person="alex")
    html = send.sent[0][2]
    assert "Job boards" in html and "x" * 200 in html and "x" * 201 not in html
    assert "(+2 more)" in html


def test_resume_label_shown_only_with_several_usable_variants(conn):
    from ezapply.core.models import ResumeVariant

    _, send = go(conn, [FakeSource("ats", items())], only_person="alex")
    assert "Suggested resume: product roles" in send.sent[0][2]


def test_resume_label_hidden_when_only_one_variant_has_a_url(conn):
    from ezapply.core.models import ResumeVariant

    one = make_profile(id="alex", name="Alex", email="r@x.com",
                       resumes={"resume": ResumeVariant("resume", "u1", "product roles"),
                                "cv": ResumeVariant("cv", "", "research roles")})
    _, send = go(conn, [FakeSource("ats", items())], profiles=[one])
    assert "Suggested resume" not in send.sent[0][2]


def test_config_problems_fail_fast():
    from ezapply.core.config import ProviderConfig

    ok = ProviderConfig("gemini", "http://g", "k", "m")
    assert config_problems(settings(llm_providers=[ok]), dry_run=False) == []
    assert config_problems(settings(app_base_url="http://x.com", llm_providers=[ok]), dry_run=False)
    assert config_problems(settings(app_base_url="", llm_providers=[ok]), dry_run=True)
    assert config_problems(settings(app_base_url="http://127.0.0.1:8000"), dry_run=False)
    assert config_problems(settings(app_base_url="http://localhost:8000"), dry_run=True) == []
    empty = ProviderConfig("groq", "http://q", "k", "")
    msgs = config_problems(settings(llm_providers=[ok, empty]), dry_run=False)
    assert len(msgs) == 1 and "LLM_GROQ_MODEL" in msgs[0]
