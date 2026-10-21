# Ez Apply

A daily job-digest agent. Every morning it searches job boards, company career pages and LinkedIn hiring posts, ranks the results against each person's resume (rules, embeddings, then an LLM judge), and emails each person a digest. Each job in the email has an Apply link that opens a small web tool with a drafted application email to review, edit and open pre-filled in Gmail.

## Features

- Sources: job boards via [JobSpy](https://github.com/Bunsly/JobSpy), company career boards (Greenhouse, Lever, Ashby) and LinkedIn hiring posts via an Apify actor.
- Multi-stage ranking: hard filters (city, experience, excluded keywords), rule score, local embeddings (fastembed), then an LLM judge on the top candidates.
- Free LLMs: Gemini and Groq through OpenAI-compatible endpoints, tried in order, paced for free-tier limits.
- HTML and plain-text digest by email (Gmail SMTP), one per person, de-duplicated across days.
- Application drafts: a tailored email per job (when a contact email is known) and a resume pick from several variants, editable in a FastAPI web tool with magic links and password login.
- Multi-profile: any number of people, each with their own YAML profile and resume.

## Architecture

```
cron-job.org (daily)
      |  POST workflow_dispatch
      v
GitHub Actions: daily.yml  --->  Neon Postgres  <---  Vercel (FastAPI web tool)
  scrape, rank, judge, email        shared state         login, magic links, draft review
```

- The pipeline (`ezapply/pipeline`) runs only in GitHub Actions.
- The web tool (`api/index.py`, `ezapply/web`) runs on Vercel and installs only the base dependencies in `requirements.txt`.
- Pipeline-only dependencies live in the `pipeline` extra in `pyproject.toml`; `uv.lock` is committed.

## Free-tier stack and cost

GitHub Actions, Neon Postgres (free), Vercel (hobby), Gemini and Groq free tiers, Gmail SMTP with an app password, cron-job.org (free), and Apify's monthly free credit for LinkedIn posts. Expected cost is about $0 per month for a couple of profiles with the default budgets.

## Privacy note

Resume text and job descriptions are sent to the LLM providers you configure (Gemini and Groq free tiers by default). Free tiers may use submitted data to improve their models, so do not use this with a resume you are not comfortable sharing with them. Keep secrets in GitHub and Vercel secrets, never in the repository, and do not commit real personal data to a public fork.

## Quick start

1. **Fork** this repository and clone it.
2. Edit or replace the profiles (see [Editing profiles](#editing-profiles)).
3. Follow the steps below: Neon, GitHub Actions secrets, Vercel, then cron-job.org.

## Setup

### 1. Neon

Create a Neon project, copy its connection string (this is `DATABASE_URL`) and apply migrations:

```
DATABASE_URL=... uv run python scripts/migrate.py
```

The daily workflow also runs migrations on every run.

### 2. GitHub Actions

The `schedule:` block in `.github/workflows/daily.yml` is commented out. Configure the secrets below first, then either uncomment it or trigger the workflow from cron-job.org (see below). You can always run it by hand: Actions > Daily digest > Run workflow.

Settings > Secrets and variables > Actions.

Secrets:

| Name | Purpose |
|---|---|
| `DATABASE_URL` | Neon connection string |
| `APP_SECRET` | Signs magic links and sessions (must equal the Vercel value) |
| `APP_BASE_URL` | Public web tool URL used in digest links, e.g. `https://your-app.vercel.app` |
| `LINK_TTL_HOURS` | Optional, magic link lifetime (default 72) |
| `LLM_GEMINI_API_KEY` | Gemini key (OpenAI-compatible endpoint) |
| `LLM_GROQ_API_KEY` | Groq key |
| `LLM_GATEWAY_API_KEY` | Optional, only when using the LLM gateway |
| `APIFY_TOKEN` | LinkedIn post search |
| `SENDER_EMAIL` | Gmail address that sends digests |
| `SENDER_APP_PASSWORD` | Gmail app password for that address |

Variables (all optional unless noted; unset means the app default):

| Name | Default | Purpose |
|---|---|---|
| `MIN_MATCH_PCT` | 75 | Minimum match percent for a job to appear |
| `MAX_AGE_DAYS` | 7 | Ignore older postings |
| `JUDGE_TOP_N` | 30 | Jobs per person sent to the LLM judge |
| `MAX_ITEMS_PER_DIGEST` | 25 | Cap per digest |
| `SEND_EMPTY_DIGEST` | true | Send a digest even with no matches |
| `LLM_PROVIDERS` | `gemini,groq` | Providers in priority order |
| `LLM_GEMINI_MODEL`, `LLM_GROQ_MODEL` | none (required) | Model names |
| `LLM_GEMINI_BASE_URL`, `LLM_GROQ_BASE_URL` | the public endpoints | Override only if they change |
| `LLM_<NAME>_MIN_INTERVAL_SECONDS` | gemini 7, groq 3 | Spacing between calls per provider |
| `LLM_GATEWAY_BASE_URL`, `LLM_GATEWAY_MODEL` | none | Only for the gateway |

### 3. Vercel

Create a project from this repo (`vercel.json` routes everything to `api/index.py`) and set:

| Name | Purpose |
|---|---|
| `DATABASE_URL` | Same Neon connection string |
| `APP_SECRET` | Same value as GitHub |
| `LINK_TTL_HOURS` | Optional, default 72 |
| `PASSWORD_HASH_<ID>` | One per profile (e.g. `PASSWORD_HASH_ALEX`, `PASSWORD_HASH_SAM`): bcrypt hash, from `uv run python scripts/hash_password.py` |
| `COOKIE_SECURE` | Leave unset (secure cookies). `false` only for local http |

## Changing the match threshold

`MIN_MATCH_PCT` is a repository variable: Settings > Secrets and variables > Actions > Variables > `MIN_MATCH_PCT`. Edit it and the next run picks it up; no code change.

## Editing profiles

Each person has `profiles/<id>.yaml` (titles, cities, experience range, keywords, blocked companies, search queries, resume variants) and `profiles/<id>.resume.md`. See [profiles/README.md](profiles/README.md) for every field. `alex` and `sam` are fictional examples; replace them with your own. Put a shareable resume link in each variant's `drive_url`. PDFs in `profiles/` are git-ignored. To generate the resume text from a PDF:

```
uv run python scripts/extract_resumes.py resume.pdf -o profiles/<id>.resume.md
```

Check the spacing of the output, then commit the YAML and `.resume.md` changes. Keep real personal data out of public forks.

## Adding companies

Add entries to `config/companies.yaml` following the existing format; they are scraped on the next run.

## LinkedIn queries and Apify budget

`config/linkedin_queries.yaml` lists post-search queries per person, plus `max_posts_per_person` (30). At $0.002 per post that is about $3.60 per month for two people, under the $5 Apify free credit. Keep to 4 queries per person or fewer; raising `max_posts_per_person` raises the cost proportionally.

## Switching to the LLM gateway

Add `gateway` to `LLM_PROVIDERS` (for example `gateway,gemini,groq`) and set `LLM_GATEWAY_BASE_URL`, `LLM_GATEWAY_MODEL` (variables) and `LLM_GATEWAY_API_KEY` (secret). No code change. A provider with no API key is skipped. The gateway must offer a stateless mode.

## Running locally

```
uv sync --extra pipeline --dev
cp .env.example .env        # fill in values; never commit .env
set -a; . ./.env; set +a    # export .env into the shell (or prefix commands with: uv run --env-file .env)
uv run python -m ezapply.pipeline.run --dry-run            # writes out/digest-<person>.html, sends nothing
uv run python -m ezapply.pipeline.run --dry-run --person alex
uv run pytest -q
```

Other flags: `--person <id>` limits to one profile, `--force` ignores the already-sent-today check. Tests that need Postgres run only when `TEST_DATABASE_URL` is set (use a throwaway Neon branch, since tests truncate tables).

The same two inputs exist on the workflow: Actions > Daily digest > Run workflow > `person` and `force`.

## cron-job.org

GitHub's own schedule is unreliable on timing, so cron-job.org triggers the workflow.

1. Create a fine-grained personal access token limited to this repo with Repository permissions > Actions: Read and write.
2. In cron-job.org create a job:
   - URL: `https://api.github.com/repos/<owner>/<repo>/actions/workflows/daily.yml/dispatches`
   - Method: `POST`
   - Headers: `Accept: application/vnd.github+json`, `Authorization: Bearer <PAT>`, `X-GitHub-Api-Version: 2022-11-28`
   - Body: `{"ref":"main"}`
   - Schedule: 08:00, timezone Asia/Kolkata
3. Use "test run"; a new workflow run should appear. A successful dispatch returns HTTP 204.

If you also enable the backup `schedule:` in the workflow and both fire, the pipeline's per-day sent check prevents duplicate digests.

## Known limits

- Free-tier LLM limits mean calls are paced per provider (see `LLM_<NAME>_MIN_INTERVAL_SECONDS`); a run with many jobs to judge is slow by design, and the job has a 30-minute timeout.
- GitHub scheduled runs can be delayed; the optional backup schedule is best effort.
- Glassdoor blocks GitHub-hosted runner IPs, so it returns little or nothing from Actions. Other JobSpy boards may rate-limit too.
- Naukri needs a CAPTCHA to be solved, so it is skipped.
- Job-board scraping depends on third-party sites and can break when they change.

## License

MIT. See [LICENSE](LICENSE).

Built by [Rohit Jadhav](https://github.com/rohitjadhav27).
