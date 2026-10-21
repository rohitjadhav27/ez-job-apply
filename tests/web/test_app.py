import dataclasses
import re
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import bcrypt
import pytest
from fastapi.testclient import TestClient

from ezapply.core.compose import MAX_COMPOSE_URL
from ezapply.core.config import Settings, load_profiles
from ezapply.core.db import get_draft, insert_draft, record_login_failure, upsert_jobs
from ezapply.core.tokens import make_link_token, make_session
from ezapply.web import app as web
from ezapply.web.runtime import Runtime
from tests.core.test_db import item

pytestmark = pytest.mark.filterwarnings("ignore:Setting per-request cookies:DeprecationWarning")

SECRET = "test-secret-" + "x" * 20
PW = "correct horse"
IOS = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8)"


@pytest.fixture
def app_env(conn):
    import os
    from pathlib import Path
    h = bcrypt.hashpw(PW.encode(), bcrypt.gensalt(rounds=4)).decode()
    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"], app_secret=SECRET,
                        password_hashes={"alex": h, "sam": h})
    profiles = load_profiles(Path(__file__).resolve().parents[2] / "profiles")
    sleeps = []
    web.app.state.runtime = Runtime(settings, profiles, sleep=sleeps.append)
    client = TestClient(web.app, base_url="https://testserver", follow_redirects=False)
    yield client, conn, sleeps
    web.app.state.runtime.close()
    del web.app.state.runtime


def make_draft(conn, person="alex", key="a", body="Hi\n\nResume: https://old"):
    upsert_jobs(conn, [item(key, title="Dev <b>", company="Acme")])
    return insert_draft(conn, person, key, "hr@acme.com", "Subj", body, "default")


def login_cookie(person="alex"):
    return {"ezs": make_session(SECRET, person)}


def csrf_for(client, did):
    html = client.get(f"/drafts/{did}").text
    return re.search(r'name="csrf" value="([^"]+)"', html).group(1)


def test_root_redirects_to_login_without_session(app_env):
    client, *_ = app_env
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"].startswith("/login")


def test_valid_link_sets_cookie_and_redirects_to_draft(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    t = make_link_token(SECRET, "alex", str(did), 72)
    r = client.get(f"/d/{did}?t={t}")
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{did}"
    c = r.headers["set-cookie"]
    assert c.startswith("ezs=") and "HttpOnly" in c and "Secure" in c
    assert "SameSite=lax" in c and "Max-Age=604800" in c
    assert client.get(f"/drafts/{did}").status_code == 200


def test_expired_or_tampered_link_goes_to_login(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    t = make_link_token(SECRET, "alex", str(did), 72)
    bad = client.get(f"/d/{did}?t={t}x")
    assert bad.status_code == 303 and bad.headers["location"] == f"/login?next=/drafts/{did}"
    assert "set-cookie" not in bad.headers
    rt = web.app.state.runtime
    rt.settings = dataclasses.replace(rt.settings, link_ttl_hours=-1)
    exp = client.get(f"/d/{did}?t={t}")
    assert exp.headers["location"] == f"/login?next=/drafts/{did}"
    other = make_draft(conn, key="b")
    wrong = client.get(f"/d/{other}?t={t}")  # token for a different draft
    assert wrong.headers["location"].startswith("/login")


def test_link_with_existing_same_person_session_just_redirects(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    t = make_link_token(SECRET, "alex", str(did), 72)
    r = client.get(f"/d/{did}?t=garbage", cookies=login_cookie("alex"))
    assert r.headers["location"] == f"/drafts/{did}"
    r = client.get(f"/d/{did}?t={t}", cookies=login_cookie("alex"))
    assert r.headers["location"] == f"/drafts/{did}" and "set-cookie" not in r.headers


def test_login_success_sets_session_and_follows_next(app_env):
    client, conn, sleeps = app_env
    r = client.post("/login", data={"person": "sam", "password": PW, "next": "/drafts/abc"})
    assert r.status_code == 303 and r.headers["location"] == "/drafts/abc"
    assert "ezs=" in r.headers["set-cookie"] and sleeps == []
    r = client.post("/login", data={"person": "alex", "password": PW, "next": "//evil.com"})
    assert r.headers["location"] == "/"


def test_login_wrong_password_401_then_429_after_10(app_env):
    client, conn, sleeps = app_env
    for _ in range(10):
        r = client.post("/login", data={"person": "alex", "password": "nope"},
                        headers={"x-forwarded-for": "9.9.9.9, 1.1.1.1"})
        assert r.status_code == 401 and "set-cookie" not in r.headers
    assert sleeps == [1] * 10
    r = client.post("/login", data={"person": "alex", "password": PW},
                    headers={"x-forwarded-for": "9.9.9.9"})
    assert r.status_code == 429
    ok = client.post("/login", data={"person": "alex", "password": PW},
                     headers={"x-forwarded-for": "8.8.8.8"})
    assert ok.status_code == 303


def test_login_unknown_person_or_missing_hash_fails(app_env):
    client, *_ = app_env
    assert client.post("/login", data={"person": "mallory", "password": PW}).status_code == 401
    rt = web.app.state.runtime
    rt.settings = dataclasses.replace(rt.settings, password_hashes={})
    assert client.post("/login", data={"person": "alex", "password": PW}).status_code == 401


def test_login_page_has_person_selector(app_env):
    client, *_ = app_env
    html = client.get("/login").text
    assert 'name="person"' in html and "Alex" in html and "Sam" in html and 'type="password"' in html


def test_cannot_open_other_persons_draft(app_env):
    client, conn, _ = app_env
    did = make_draft(conn, person="sam")
    r = client.get(f"/drafts/{did}", cookies=login_cookie("alex"))
    assert r.status_code == 404
    assert client.get("/drafts/not-a-uuid", cookies=login_cookie("alex")).status_code == 404
    csrf = csrf_for_cookie(client, "alex")
    r = client.post(f"/drafts/{did}", cookies=login_cookie("alex"),
                    data={"csrf": csrf, "to_email": "x@y.z", "subject": "s", "body": "b", "resume": "default"})
    assert r.status_code == 404
    assert get_draft(conn, did, "sam")["subject"] == "Subj"


def csrf_for_cookie(client, person):
    from ezapply.web.auth import csrf_token
    return csrf_token(SECRET, make_session(SECRET, person))


def test_draft_page_content_and_escaping(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    r = client.get(f"/drafts/{did}", cookies=login_cookie("alex"))
    html = r.text
    assert 'value="hr@acme.com"' in html and "Subj" in html
    assert "Dev &lt;b&gt;" in html and "Dev <b>" not in html
    assert "<textarea" in html and 'rows="14"' in html
    assert "all product roles" in html  # resume dropdown label
    assert "Open in Gmail" in html and "Open in Gmail web" in html and "Back to drafts" in html
    assert "https://x/a" in html
    assert re.search(r'id="warn"[^>]*\shidden', html)  # warning block present but hidden


def test_long_body_shows_warning_and_copy_button(app_env):
    client, conn, _ = app_env
    did = make_draft(conn, body="word " * 600)
    html = client.get(f"/drafts/{did}", cookies=login_cookie("alex")).text
    assert "Copy body" in html and str(MAX_COMPOSE_URL) in html
    assert not re.search(r'id="warn"[^>]*\shidden', html)


def test_save_updates_draft(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    c = login_cookie("alex")
    r = client.post(f"/drafts/{did}", cookies=c, data={
        "csrf": csrf_for_cookie(client, "alex"), "to_email": "new@acme.com",
        "subject": "New subj", "body": "New body", "resume": "default"})
    assert r.status_code == 303 and r.headers["location"] == f"/drafts/{did}?saved=1"
    d = get_draft(conn, did, "alex")
    assert (d["to_email"], d["subject"], d["body"], d["status"]) == ("new@acme.com", "New subj", "New body", "edited")


def test_post_without_valid_csrf_is_403(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    c = login_cookie("alex")
    form = {"to_email": "n@a.com", "subject": "s", "body": "b", "resume": "default"}
    assert client.post(f"/drafts/{did}", cookies=c, data=form).status_code == 403
    assert client.post(f"/drafts/{did}", cookies=c, data={**form, "csrf": "bad"}).status_code == 403
    other = csrf_for_cookie(client, "sam")
    assert client.post(f"/drafts/{did}/open", cookies=c, data={**form, "csrf": other}).status_code == 403
    assert client.post("/logout", cookies=c).status_code == 403
    assert get_draft(conn, did, "alex")["status"] == "new"


def _open(client, did, ua=None, body="Hello\n\nResume: https://old", resume="default"):
    headers = {"user-agent": ua} if ua else {}
    return client.post(f"/drafts/{did}/open", cookies=login_cookie("alex"), headers=headers, data={
        "csrf": csrf_for_cookie(client, "alex"), "to_email": "hr@acme.com", "subject": "Hi there",
        "body": body, "resume": resume})


def test_open_redirects_to_gmail_web_with_authuser(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    r = _open(client, did)
    assert r.status_code == 303
    loc = r.headers["location"]
    assert loc.startswith("https://mail.google.com/mail/?authuser=alex.mehta%40example.com&view=cm&fs=1")
    q = parse_qs(urlparse(loc).query)
    assert q["to"] == ["hr@acme.com"] and q["su"] == ["Hi there"]
    assert q["body"][0].startswith("Hello") and "Resume: https://example.com/resumes/alex-mehta" in q["body"][0]
    assert "https://old" not in q["body"][0]
    d = get_draft(conn, did, "alex")
    assert d["status"] == "opened" and d["subject"] == "Hi there"


def test_open_redirects_ios_and_android(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    ios = _open(client, did, IOS).headers["location"]
    assert ios.startswith("googlegmail://co?to=hr%40acme.com&subject=Hi%20there&body=")
    # httpx cannot build a "next request" for a mailto: Location; that is a client limitation only.
    client._build_redirect_request = lambda *a, **k: None
    android = _open(client, did, ANDROID).headers["location"]
    assert android.startswith("mailto:hr@acme.com?subject=Hi%20there&body=")


def test_open_drops_resume_line_when_variant_has_no_url(app_env):
    client, conn, _ = app_env
    did = make_draft(conn, person="sam")
    from dataclasses import replace
    from ezapply.web.auth import csrf_token
    rt = web.app.state.runtime
    sam = rt.profiles["sam"]
    rt.profiles["sam"] = replace(sam, resumes={**sam.resumes, "cv": replace(sam.resumes["cv"], drive_url="")})
    cookie = {"ezs": make_session(SECRET, "sam")}
    r = client.post(f"/drafts/{did}/open", cookies=cookie, data={
        "csrf": csrf_token(SECRET, cookie["ezs"]), "to_email": "hr@acme.com", "subject": "s",
        "body": "Hello\n\nResume: https://old", "resume": "cv"})
    body = parse_qs(urlparse(r.headers["location"]).query)["body"][0]
    assert "Resume" not in body


def test_list_page_newest_first_with_status_and_empty_state(app_env):
    client, conn, _ = app_env
    c = login_cookie("alex")
    assert "No drafts yet" in client.get("/", cookies=c).text
    a = make_draft(conn, key="a")
    b = make_draft(conn, key="b")
    make_draft(conn, person="sam", key="z")
    html = client.get("/", cookies=c).text
    assert html.index(f"/drafts/{b}") < html.index(f"/drafts/{a}")
    assert "Acme" in html and "pill-new" in html and f"/drafts/{a}" in html
    assert "z" not in re.findall(r"/drafts/[^\"]+", html)  # no foreign drafts


def test_logout_clears_cookie(app_env):
    client, *_ = app_env
    r = client.post("/logout", cookies=login_cookie("alex"), data={"csrf": csrf_for_cookie(client, "alex")})
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert 'ezs=""' in r.headers["set-cookie"] or "ezs=;" in r.headers["set-cookie"]
    assert "Max-Age=0" in r.headers["set-cookie"]


def test_security_headers_on_all_responses(app_env):
    client, *_ = app_env
    for r in (client.get("/login"), client.get("/"), client.get("/static/app.css")):
        assert r.headers["x-frame-options"] == "DENY"
        assert r.headers["referrer-policy"] == "no-referrer"
        assert r.headers["x-content-type-options"] == "nosniff"
        csp = r.headers["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self';" in csp
        assert "frame-ancestors 'none'" in csp
        assert "form-action" not in csp  # would block the Gmail redirect chain (see app.py)


def test_static_css_served(app_env):
    client, *_ = app_env
    r = client.get("/static/app.css")
    assert r.status_code == 200 and "prefers-color-scheme: dark" in r.text


def test_cookie_secure_can_be_disabled(app_env):
    client, conn, _ = app_env
    rt = web.app.state.runtime
    rt.settings = dataclasses.replace(rt.settings, cookie_secure=False)
    r = client.post("/login", data={"person": "alex", "password": PW})
    assert "Secure" not in r.headers["set-cookie"]


def test_importing_web_app_pulls_no_pipeline_libs():
    code = (
        "import sys, ezapply.web.app\n"
        "bad = [m for m in ('jobspy','fastembed','openai','apify_client','pandas') if m in sys.modules]\n"
        "bad += [m for m in sys.modules if m.startswith(('ezapply.sources','ezapply.ranking','ezapply.llm',"
        "'ezapply.drafting','ezapply.pipeline','ezapply.digest'))]\n"
        "assert not bad, bad\n"
    )
    env = {k: v for k, v in __import__("os").environ.items() if k not in ("DATABASE_URL", "APP_SECRET")}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr


def test_javascript_job_url_is_not_rendered_as_link(app_env):
    client, conn, _ = app_env
    upsert_jobs(conn, [item("x", title="T", company="C", url="javascript:alert(1)")])
    did = insert_draft(conn, "alex", "x", "a@b.c", "s", "b", "default")
    html = client.get(f"/drafts/{did}", cookies=login_cookie("alex")).text
    assert "javascript:" not in html and "View original job" not in html


def test_no_store_on_pages_but_not_static(app_env):
    client, *_ = app_env
    assert client.get("/login").headers["cache-control"] == "no-store"
    assert "no-store" not in client.get("/static/app.css").headers.get("cache-control", "")


def test_page_uses_external_script_only(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    html = client.get(f"/drafts/{did}", cookies=login_cookie("alex")).text
    assert '<script src="/static/draft.js"' in html and "<script>" not in html
    assert client.get("/static/draft.js").status_code == 200


def test_unknown_resume_value_falls_back_to_current(app_env):
    client, conn, _ = app_env
    did = make_draft(conn)
    client.post(f"/drafts/{did}", cookies=login_cookie("alex"), data={
        "csrf": csrf_for_cookie(client, "alex"), "to_email": "a@b.c", "subject": "s", "body": "b",
        "resume": "../evil"})
    assert get_draft(conn, did, "alex")["resume_variant"] == "default"
