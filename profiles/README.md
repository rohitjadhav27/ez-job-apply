# Profiles

Each person who receives a digest has two files here:

- `<id>.yaml` - search preferences and resume links
- `<id>.resume.md` - plain text of the resume, read by the ranker (embeddings and LLM judge)

`alex` and `sam` are fictional examples. Replace them with your own.

## Adding your profile

1. Copy `alex.yaml` to `<id>.yaml` (lowercase id, letters and digits only, e.g. `priya`).
2. Edit the fields:

| Field | Meaning |
|---|---|
| `id` | Must match the file name. Used in URLs, the DB and the env var name |
| `name`, `email`, `phone` | Shown in digests and drafts; `email` is where the digest is sent |
| `cities` | Cities to search and match |
| `allow_remote` | Include remote roles |
| `experience_years` | `{min, max}` years of experience asked for in a posting |
| `target_titles` | Strong title matches |
| `adjacent_titles` | Weaker (partial credit) title matches |
| `domain_keywords` | Words that boost a role's score |
| `exclude_keywords` | Roles containing these are dropped |
| `blocked_companies` | Companies never shown |
| `resumes` | One or more variants: `drive_url` (share link) and `use_for` (hint for the LLM to pick a variant) |
| `resume_text_file` | Path to the resume text, e.g. `profiles/<id>.resume.md` |
| `search_queries` | Queries sent to job boards |

3. Create `profiles/<id>.resume.md`. Paste the text of your resume (or generate it from a PDF with
   `uv run python scripts/extract_resumes.py <resume.pdf> -o profiles/<id>.resume.md`; check spacing afterwards).
   With several variants, use one `## Heading` section per variant, as in `sam.resume.md`.
4. Upload each resume PDF somewhere shareable (e.g. Google Drive, "anyone with the link") and set `drive_url`.
5. Add queries for your id in `config/linkedin_queries.yaml` (optional).
6. Set a password for the web tool: run `uv run python scripts/hash_password.py` and store the hash in the
   `PASSWORD_HASH_<ID>` environment variable (for example `PASSWORD_HASH_PRIYA`) on Vercel and locally.

Do not commit real resumes or personal data to a public repository.
