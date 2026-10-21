# Ez Apply — Design Spec

Date: 2026-10-20
Status: Draft for review

## 1. Purpose

A personal, zero-cost tool for two job seekers (Alex and Sam) that:

1. Finds new, relevant job listings and LinkedIn "hiring — email your CV" posts every morning.
2. Ranks them by estimated probability of being shortlisted, as a percentage.
3. Emails each person their own daily digest containing only items at or above a configurable threshold.
4. For items with a contact email, prepares a tailored application email that the person reviews and edits in a small web tool, then opens in Gmail, fully pre-filled, to send from their own account.

**Success looks like:** every morning by ~08:00 IST each person has an email with the best new matches; any "Apply with email" item takes under a minute from click to sent.

### Non-goals (v1)

- Automatic form submission on job sites or ATS portals.
- Logging into anyone's LinkedIn or scraping with personal accounts.
- Sending email from the tool itself (applications are sent by the person from Gmail).
- Cover letters or tailored resumes for listings without a contact email.
- Any paid service. Everything runs on free tiers.

## 2. People and profiles

Each person has a YAML profile at `profiles/<id>.yaml` (committed; resume PDFs are not).

| | Alex (`alex`) | Sam (`sam`) |
|---|---|---|
| Digest sent to | alex.mehta@example.com | sam.kapoor@example.com |
| Target roles | Product Manager, Senior PM, Fintech/Payments PM, Technical PM (API/platform), AI PM, Product Owner | Marketing Manager, Brand Manager, Growth/Digital Marketing Manager, Founder's Office, Chief of Staff, Client/Account Manager |
| Experience band | 3–8 years | 4–10 years |
| Cities | Mumbai | Mumbai, Pune, Bangalore |
| Domain bonus keywords | fintech, payments, banking, wealth, cross-border, API, AI | D2C, consumer, brand, startup, agency, e-commerce |
| Resume variants | `default` | `resume` (marketing/brand), `cv` (founder's office/CoS/account mgmt) |

Profile schema (illustrative):

```yaml
id: sam
name: Sam Kapoor
email: sam.kapoor@example.com
cities: [Mumbai, Pune, Bangalore]   # order = preference
experience_years: {min: 4, max: 10}
target_titles: [...]                # exact/near matches
adjacent_titles: [...]              # partial credit
domain_keywords: [...]
exclude_keywords: [intern, sales executive, BPO, telecaller]
blocked_companies: []
resumes:
  resume: {drive_url: "https://example.com/resumes/...", use_for: "marketing, brand, growth"}
  cv:     {drive_url: "https://example.com/resumes/...", use_for: "founder's office, chief of staff, account management"}
resume_text_file: profiles/sam.resume.md   # plain-text resume used for matching and drafting
search_queries: ["marketing manager", "brand manager", "founder's office", "chief of staff", ...]
```

Resume text is extracted once from the PDFs into `profiles/<id>.resume.md` (committed, since it is needed in CI). Resume PDFs are shared as public share links (e.g. Google Drive) and referenced by URL only.

## 3. Architecture

```
cron-job.org (08:00 IST)
   │  POST GitHub API: workflow_dispatch
   ▼
GitHub Actions job  ──────────────►  Neon Postgres  ◄──────────  Vercel web tool (FastAPI)
  fetch → filter → score → judge        jobs, matches,              login, draft list,
  → draft → digest                      drafts, runs                edit, "Open in Gmail"
   │
   └── Gmail SMTP (shared sender account) → one digest email per person
                                              │
                                              └── "Apply with email" link (signed token) → web tool
```

Two deployables in one repo, one language (Python 3.12):

- **Pipeline** (`ezapply/pipeline/…`): runs in GitHub Actions, started on demand by cron-job.org calling `POST /repos/{owner}/{repo}/actions/workflows/daily.yml/dispatches` with a fine-grained PAT (scope: this repo, Actions: write). The workflow also keeps a `schedule:` trigger at 08:30 IST as a backup; the run is idempotent per day (see §8).
- **Web tool** (`ezapply/web/…`): FastAPI app deployed to Vercel (Python runtime).
- **Shared** (`ezapply/core/…`): DB models, token signing, Gmail compose-URL builder, config.

### Repository layout

```
ezapply/
  core/        config.py, db.py, models.py, tokens.py, compose.py
  sources/     base.py, jobspy_source.py, ats_source.py, linkedin_posts.py
  ranking/     filters.py, rules.py, embed.py, judge.py, combine.py
  llm/         client.py (OpenAI-compatible provider chain)
  drafting/    drafter.py
  digest/      render.py, send.py, templates/digest.html.j2
  pipeline/    run.py (entry point: python -m ezapply.pipeline.run)
  web/         app.py, templates/, static/
api/index.py   Vercel entry → ezapply.web.app
profiles/      alex.yaml, sam.yaml, *.resume.md  (PDFs gitignored)
config/        companies.yaml (ATS boards to watch)
migrations/    SQL files
tests/
.github/workflows/daily.yml
vercel.json
```

## 4. Sources

Each source implements `fetch(profile) -> list[RawItem]` and is isolated: an exception in one source is logged to the run record and does not stop the others.

| Source | What | How | Notes |
|---|---|---|---|
| `jobspy` | Listings from LinkedIn, Indeed, Naukri, Glassdoor, Google Jobs | `python-jobspy` library, per (query × city), `hours_old=168` | Unauthenticated public search, so no account risk. Rate-limit-friendly: small result counts, sleeps between calls. |
| `ats` | Company career boards | Public JSON APIs of Greenhouse, Lever, Ashby for companies listed in `config/companies.yaml` | Curated list, starting with India fintech, D2C and startup companies. |
| `linkedin_posts` | "We're hiring, email your CV to …" posts | Apify LinkedIn post-search actor (free $5/month credit), keyword queries per person, last 24–48h | Actor chosen at implementation time (popular, no-cookie variant). Emails extracted by regex; posts without an email are kept as "DM / view post" items. If Apify credit runs out, the source reports "skipped: quota" in the digest footer. |

`RawItem` fields: `source, external_id, url, title, company, location, description, posted_at, applicant_count?, contact_email?, poster_name?, kind ('listing'|'post')`.

**Dedupe:** `item_key = sha1(normalize(company) | normalize(title) | normalize(city))`; for posts, `sha1(post_url)`. An item already present in `jobs` is not re-scored or re-sent (unless re-posted after 30 days).

## 5. Ranking

All scores are percentages (0–100).

### Stage 0 — hard filters (drop)

- Location not in the person's cities (remote-India listings allowed only if `allow_remote: true` in the profile; default false).
- Posted more than `MAX_AGE_DAYS` (default 7) ago.
- Already seen (dedupe).
- Title or description contains an `exclude_keywords` term, or company is in `blocked_companies`.
- Stated experience minimum exceeds profile max + 2, or stated maximum is below profile min − 1.

### Stage 1 — rule score (local, free)

| Factor | Weight | Computation |
|---|---|---|
| Title fit | 30 | 100 for a target-title match (normalized, fuzzy ≥ 90), 60 for an adjacent title, 20 otherwise |
| Skills/domain fit | 30 | 70% cosine similarity of JD vs resume text (`fastembed`, `BAAI/bge-small-en-v1.5`, CPU), rescaled to 0–100; 30% domain-keyword hit ratio |
| Seniority fit | 20 | 100 if the JD's experience range overlaps the profile band; linear decay outside; 60 if unknown |
| Freshness | 10 | 100 if posted ≤ 24h, linear down to 0 at `MAX_AGE_DAYS` |
| City preference | 5 | 100 for the first city, 70 for the second, 40 for the third; 100 if only one city |
| Competition | 5 | 100 if a contact email is present or applicants < 25; 50 if unknown; down to 0 at 500+ applicants |

`rule_pct = Σ weight × factor / 100`

### Stage 2 — AI judge (top `JUDGE_TOP_N`, default 30, per person, by rule_pct)

One LLM call per item (batched 5 per call to save quota) with resume text + JD. It returns strict JSON:

```json
{"shortlist_pct": 0-100, "reason": "≤ 20 words", "gap": "≤ 20 words", "resume_variant": "resume|cv|default"}
```

### Final

`match_pct = round(0.6 × shortlist_pct + 0.4 × rule_pct)`

- Items not judged (outside the top N) use `match_pct = rule_pct × 0.85`, so they rarely clear the threshold unjudged.
- If all LLM providers fail for the run, `match_pct = rule_pct` and the digest shows "AI ranking unavailable today".
- **Inclusion:** `match_pct ≥ MIN_MATCH_PCT` (env, default `75`, same for both people). Sorted descending; capped at `MAX_ITEMS_PER_DIGEST` (default 25).

## 6. LLM layer

`llm/client.py` exposes one function, `complete_json(messages, schema) -> dict`, backed by an ordered provider chain. Every provider is an **OpenAI-compatible chat-completions endpoint**:

```
LLM_PROVIDERS=gemini,groq                 # order = priority
LLM_GEMINI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_GEMINI_API_KEY=...
LLM_GEMINI_MODEL=<current free-tier Flash model, confirmed at implementation>
LLM_GROQ_BASE_URL=https://api.groq.com/openai/v1
LLM_GROQ_API_KEY=...
LLM_GROQ_MODEL=<current Groq Llama model, confirmed at implementation>
```

- On a 429, 5xx, timeout or invalid JSON (after one repair retry), the chain moves to the next provider. A provider that returns 429 is skipped for the rest of the run.
- **Moving to an LLM gateway later:** add `gateway` to `LLM_PROVIDERS` with its base URL, key and model. No code change. The gateway must offer a stateless mode (no memory distillation) for this app's key; that is the gateway's responsibility, not Ez Apply's.
- Requests use `temperature=0.2` for judging and `0.6` for drafting.

## 7. Drafting

A draft is created for every **included** item that has a `contact_email`, whether a post or a listing.

- Input: resume text, chosen resume variant, item text (JD or post), poster name.
- Output JSON: `{subject, body}`. Body: plain text, 120–180 words, greeting by poster name when known, 2–3 specific matches between resume and requirement, a single call to action, signature with name and phone, and a line `Resume: <drive_url>`.
- The body is plain text because Gmail compose URLs accept only plain text; Gmail turns the URL into a link when the email is sent.
- Stored in `drafts` with status `new`.

## 8. Data model (Neon Postgres)

```sql
jobs(
  id text primary key,              -- item_key
  kind text, source text, url text, title text, company text, location text,
  description text, posted_at timestamptz, applicant_count int,
  contact_email text, poster_name text, first_seen_at timestamptz default now()
)
matches(
  job_id text references jobs, person text, rule_pct int, ai_pct int, match_pct int,
  reason text, gap text, resume_variant text, run_date date,
  primary key (job_id, person)
)
drafts(
  id uuid primary key, person text, job_id text references jobs,
  to_email text, subject text, body text, resume_variant text,
  status text default 'new',        -- new | edited | opened
  created_at timestamptz default now(), updated_at timestamptz
)
runs(
  run_date date primary key, started_at timestamptz, finished_at timestamptz,
  status text, stats jsonb, errors jsonb, digest_sent jsonb
)
```

**Idempotency:** a run for a `run_date` (IST) whose `digest_sent` already contains a person is skipped for that person. Running the workflow twice therefore never double-sends.

**Retention:** jobs and matches older than 60 days are deleted at the end of each run (keeps Neon well inside its free limits).

## 9. Digest email

- Sent via Gmail SMTP (`smtp.gmail.com:465`) from one shared sender account (`SENDER_EMAIL`, `SENDER_APP_PASSWORD`), one email per person, to that person's address.
- HTML (table layout, inline CSS, readable in Gmail web and mobile) plus a plain-text alternative.
- Subject: `Ez Apply · Tue 21 Oct · 14 matches · 3 hiring posts`.

Sections:

1. **Hiring posts — send an email:** a card per post with poster, company, age, match %, reason, and buttons `✉ Apply with email` (only with an email) and `View post`.
2. **Top matches:** a card per listing with title, company, city, posted age, source, applicants, **match %**, why, gap, suggested resume, `View & apply` (the original listing URL) and, when an email exists, `✉ Apply with email`.
3. **Footer:** counts per source, failed or skipped sources, AI status, threshold in use.

If nothing clears the threshold, the email says so in one line (it can be turned off with `SEND_EMPTY_DIGEST=false`).

## 10. Web tool (Vercel)

### Access

- **Magic link:** every "Apply with email" link is `https://<app>/d/<draft_id>?t=<token>`. The token is `itsdangerous` URL-safe signed with `APP_SECRET` and carries `{person, draft_id, nonce, exp}`. Expiry is `LINK_TTL_HOURS` (default 72). A tampered or expired token is rejected.
- A valid token sets a signed, `HttpOnly`, `Secure`, `SameSite=Lax` session cookie for that person (7 days) and redirects to the draft.
- **Password login:** with no valid token or session, `/login` asks for a password. Each person has their own (`PASSWORD_HASH_ALEX`, `PASSWORD_HASH_SAM`, bcrypt). A session only ever sees its own person's drafts. Failed logins are slowed (1 s delay) and limited to 10 per IP per 15 min (counter in Postgres).
- Known limitation: anyone the digest email is forwarded to can use its links until they expire.

### Pages

| Route | Purpose |
|---|---|
| `GET /login`, `POST /login`, `POST /logout` | Password auth |
| `GET /d/{id}?t=…` | Token exchange, then redirect to `/drafts/{id}` |
| `GET /` | The person's drafts, newest first (status, company, role, match %) |
| `GET /drafts/{id}` | Editable form: To, Subject, Body, resume variant (dropdown; changes the `Resume:` line), link to the original listing or post |
| `POST /drafts/{id}` | Save edits (status → `edited`) |
| `POST /drafts/{id}/open` | Save, set status → `opened`, redirect to Gmail compose |

### Gmail hand-off (`core/compose.py`)

- Desktop: `https://mail.google.com/mail/?authuser=<person_email>&view=cm&fs=1&to=…&su=…&body=…` (URL-encoded). `authuser` makes sure the right Gmail account opens.
- iOS: `googlegmail://co?to=…&subject=…&body=…`. Android: `mailto:` (opens the default mail app, normally Gmail). The page chooses by user agent and always shows a secondary "Open in Gmail web" link.
- If the encoded URL would exceed 1,900 characters, the page warns and offers "Copy body" as a fallback.

## 11. Configuration (env)

| Var | Default | Used by |
|---|---|---|
| `DATABASE_URL` | — | both |
| `MIN_MATCH_PCT` | 75 | pipeline |
| `MAX_AGE_DAYS` | 7 | pipeline |
| `JUDGE_TOP_N` | 30 | pipeline |
| `MAX_ITEMS_PER_DIGEST` | 25 | pipeline |
| `SEND_EMPTY_DIGEST` | true | pipeline |
| `LLM_PROVIDERS`, `LLM_<NAME>_{BASE_URL,API_KEY,MODEL}` | gemini,groq | pipeline |
| `APIFY_TOKEN` | — | pipeline |
| `SENDER_EMAIL`, `SENDER_APP_PASSWORD` | — | pipeline |
| `APP_BASE_URL` | — | pipeline (links), web |
| `APP_SECRET` | — | both (token signing) |
| `LINK_TTL_HOURS` | 72 | both |
| `PASSWORD_HASH_ALEX`, `PASSWORD_HASH_SAM` | — | web |

Pipeline secrets live in GitHub Actions secrets; web secrets live in Vercel env vars. `.env.example` documents all of them.

## 12. Error handling

- A source failure is recorded in `runs.errors` and shown in the digest footer; the run continues.
- An LLM provider fails over down the chain; if all fail, the run falls back to rule scores (§5).
- Drafting failure for an item: the item is still shown, without the Apply-with-email button.
- SMTP failure: retried 3× with backoff; if it still fails the workflow exits non-zero, so GitHub emails the repo owner.
- A DB failure at start aborts the run (non-zero exit).

## 13. Testing

- **Unit (pytest):** filters, each rule factor, the combine formula and threshold, dedupe keys, token sign/verify/expiry/tamper, compose-URL building and length guard, LLM chain failover (fake providers), JSON repair.
- **Source adapters:** parsing tested against recorded fixture responses; no live network in tests.
- **Digest:** render to HTML with fixture data; snapshot test plus a manual check in Gmail.
- **Web:** FastAPI `TestClient`: login, token exchange, cross-person access denied, edit/save, open redirect.
- **Dry run:** `python -m ezapply.pipeline.run --dry-run --person alex` fetches and ranks live but writes the digest to `out/digest-<person>.html` and sends nothing.

## 14. Risks and spikes (do first)

1. **JobSpy against Naukri and LinkedIn from GitHub runner IPs:** may be rate-limited or blocked. Spike: one dry run from Actions. Mitigation: fewer queries, add Google Jobs, rely more on ATS boards.
2. **Apify LinkedIn post actor quality and cost per run within $5/month:** spike with 2 queries, then measure.
3. **Gmail compose URL with `authuser` + long bodies, and `googlegmail://` on iOS:** quick manual test.
4. **FastAPI on Vercel Python runtime cold start with a Neon connection:** use Neon's pooled connection string and a small `psycopg` pool.

## 15. Future (not v1)

- Switch to the LLM gateway (config only).
- Auto-apply for simple ATS forms (Greenhouse/Lever) with human confirmation.
- Feedback loop: "not relevant" button in the tool that adjusts profile keywords.
