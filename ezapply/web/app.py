"""Draft review web tool. Imports only base deps and ezapply.core / ezapply.web."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

import bcrypt
import jinja2
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ezapply.core import compose, db
from ezapply.core.urls import safe_url
from ezapply.core.tokens import make_session, read_link_token
from ezapply.web.auth import COOKIE, MAX_AGE, csrf_ok, csrf_token, current_person
from ezapply.web.runtime import get_runtime

HERE = Path(__file__).resolve().parent
# Starlette's default autoescape keys off .html/.xml suffixes, which ".html.j2" does not match: force it on.
templates = Jinja2Templates(env=jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(HERE / "templates")), autoescape=True))
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

MAX_LOGIN_FAILURES = 10
# No form-action directive on purpose: Chrome applies it to every hop of a POST redirect chain, and the
# Gmail hand-off redirects mail.google.com -> accounts.google.com (sign-in) and to googlegmail:/mailto:,
# so an allowlist blocks the hand-off (verified in the browser). Framing is blocked by frame-ancestors.
CSP = ("default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "base-uri 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = CSP
    if not request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store"
    return response


# ---- helpers -------------------------------------------------------------

def _uuid(value: str) -> UUID | None:
    try:
        return UUID(value)
    except ValueError:
        return None


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _safe_next(value: str) -> str:
    return value if value.startswith("/") and not value.startswith("//") and "\\" not in value else "/"


def _set_session(response: Response, rt, person: str) -> None:
    response.set_cookie(COOKIE, make_session(rt.settings.app_secret, person), max_age=MAX_AGE,
                        httponly=True, secure=rt.settings.cookie_secure, samesite="lax", path="/")


def _client_ip(request: Request) -> str:
    # Relies on Vercel overwriting X-Forwarded-For with the real client IP; behind any other proxy
    # the first entry is client-controlled and the per-IP limit could be sidestepped.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd.strip():
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _csrf(request: Request, rt) -> str:
    return csrf_token(rt.settings.app_secret, request.cookies.get(COOKIE, ""))


def _csrf_valid(request: Request, rt, token: str) -> bool:
    return csrf_ok(rt.settings.app_secret, request.cookies.get(COOKIE, ""), token)


def _render(request: Request, name: str, status: int = 200, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx, status_code=status)


def _resume_options(profile, selected: str) -> list[dict]:
    return [
        {"key": k, "url": v.drive_url, "label": v.use_for or k, "selected": k == selected}
        for k, v in profile.resumes.items() if v.drive_url
    ]


def _resume_url(profile, key: str) -> str:
    v = profile.resumes.get(key)
    return v.drive_url if v else ""


def _web_url_len(profile, to: str, subject: str, body: str, resume_key: str) -> int:
    return len(compose.gmail_web_url(profile.email, to, subject,
                                     compose.compose_body(body, _resume_url(profile, resume_key))))


# ---- auth ----------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    rt = get_runtime(request)
    return _render(request, "login.html.j2", people=list(rt.profiles.values()), next=_safe_next(next), error="")


@app.post("/login")
def login(request: Request, person: str = Form(""), password: str = Form(""), next: str = Form("/")):
    rt = get_runtime(request)
    ip = _client_ip(request)
    people = list(rt.profiles.values())
    nxt = _safe_next(next)
    with rt.conn() as conn:
        if db.recent_login_failures(conn, ip) >= MAX_LOGIN_FAILURES:
            return _render(request, "login.html.j2", 429, people=people, next=nxt,
                           error="Too many attempts. Try again in a few minutes.")
        pw_hash = rt.settings.password_hashes.get(person, "")
        ok = False
        if person in rt.profiles and pw_hash:
            try:
                ok = bcrypt.checkpw(password.encode(), pw_hash.encode())
            except ValueError:
                ok = False
        if not ok:
            db.record_login_failure(conn, ip)
    if not ok:
        rt.sleep(1)
        return _render(request, "login.html.j2", 401, people=people, next=nxt, error="Wrong person or password.")
    response = _redirect(nxt)
    _set_session(response, rt, person)
    return response


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    rt = get_runtime(request)
    if not _csrf_valid(request, rt, csrf):
        return Response("Forbidden", status_code=403)
    response = _redirect("/login")
    response.delete_cookie(COOKIE, path="/", secure=rt.settings.cookie_secure, httponly=True, samesite="lax")
    return response


@app.get("/d/{draft_id}")
def token_exchange(request: Request, draft_id: str, t: str = ""):
    rt = get_runtime(request)
    target = f"/drafts/{draft_id}"
    session = current_person(request)
    parsed = read_link_token(rt.settings.app_secret, t, rt.settings.link_ttl_hours) if t else None
    if parsed and parsed[1] == draft_id and parsed[0] in rt.profiles:
        if session == parsed[0]:
            return _redirect(target)
        response = _redirect(target)
        _set_session(response, rt, parsed[0])
        return response
    if session:
        return _redirect(target)
    return _redirect(f"/login?next={target}")


# ---- pages ---------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    person = current_person(request)
    if not person:
        return _redirect("/login")
    rt = get_runtime(request)
    with rt.conn() as conn:
        drafts = db.list_drafts(conn, person)
    return _render(request, "list.html.j2", profile=rt.profiles[person], drafts=drafts, csrf=_csrf(request, rt))


def _draft_page(request: Request, rt, person: str, d: dict) -> HTMLResponse:
    profile = rt.profiles[person]
    body = d["body"] or ""
    url_len = _web_url_len(profile, d["to_email"] or "", d["subject"] or "", body, d["resume_variant"] or "")
    return _render(
        request, "draft.html.j2", profile=profile, d=d, csrf=_csrf(request, rt),
        options=_resume_options(profile, d["resume_variant"] or ""),
        url_len=url_len, max_len=compose.MAX_COMPOSE_URL, too_long=url_len > compose.MAX_COMPOSE_URL,
        web_url=compose.gmail_web_url(profile.email, d["to_email"] or "", d["subject"] or "",
                                      compose.compose_body(body, _resume_url(profile, d["resume_variant"] or ""))),
        saved=request.query_params.get("saved") == "1",
        job_url=safe_url(d.get("url")),
    )


@app.get("/drafts/{draft_id}", response_class=HTMLResponse)
def draft_page(request: Request, draft_id: str):
    person = current_person(request)
    if not person:
        return _redirect(f"/login?next=/drafts/{draft_id}")
    rt = get_runtime(request)
    did = _uuid(draft_id)
    d = None
    if did:
        with rt.conn() as conn:
            d = db.get_draft(conn, did, person)
    if not d:
        return _render(request, "notfound.html.j2", 404)
    return _draft_page(request, rt, person, d)


def _save(request: Request, draft_id: str, csrf: str, to_email: str, subject: str, body: str,
          resume: str, status: str):
    """Returns (error_response | None, person, did, profile, fields)."""
    person = current_person(request)
    if not person:
        return _redirect(f"/login?next=/drafts/{draft_id}"), None, None, None, None
    rt = get_runtime(request)
    if not _csrf_valid(request, rt, csrf):
        return Response("Forbidden", status_code=403), None, None, None, None
    did = _uuid(draft_id)
    saved = False
    profile = rt.profiles[person]
    if resume and resume not in profile.resumes:  # "" = explicit "No resume link"
        current = None
        if did:
            with rt.conn() as conn:
                current = db.get_draft(conn, did, person)
        cur = (current or {}).get("resume_variant") or ""
        resume = cur if cur in profile.resumes else next(iter(profile.resumes), "")
    if did:
        with rt.conn() as conn:
            saved = db.update_draft(conn, did, person, to_email=to_email.strip(), subject=subject, body=body,
                                    resume_variant=resume, status=status)
    if not saved:
        return _render(request, "notfound.html.j2", 404), None, None, None, None
    return None, person, did, profile, resume


@app.post("/drafts/{draft_id}")
def save_draft(request: Request, draft_id: str, csrf: str = Form(""), to_email: str = Form(""),
               subject: str = Form(""), body: str = Form(""), resume: str = Form("")):
    err, *_ = _save(request, draft_id, csrf, to_email, subject, body, resume, "edited")
    return err or _redirect(f"/drafts/{draft_id}?saved=1")


@app.post("/drafts/{draft_id}/open")
def open_draft(request: Request, draft_id: str, csrf: str = Form(""), to_email: str = Form(""),
               subject: str = Form(""), body: str = Form(""), resume: str = Form("")):
    err, person, did, profile, resume = _save(request, draft_id, csrf, to_email, subject, body, resume, "opened")
    if err:
        return err
    to = to_email.strip()
    out_body = compose.compose_body(body, _resume_url(profile, resume))
    ua = request.headers.get("user-agent", "")
    if re.search(r"iPhone|iPad", ua):
        url = compose.gmail_ios_url(to, subject, out_body)
    elif "Android" in ua:
        url = compose.mailto_url(to, subject, out_body)
    else:
        url = compose.gmail_web_url(profile.email, to, subject, out_body)
    return _redirect(url)
