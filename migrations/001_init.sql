create table jobs (
  id text primary key,
  kind text, source text, url text, title text, company text, location text,
  description text, posted_at timestamptz, applicant_count int,
  contact_email text, poster_name text,
  first_seen_at timestamptz not null default now()
);

create table matches (
  job_id text not null references jobs(id) on delete cascade,
  person text not null,
  rule_pct int, ai_pct int, match_pct int,
  reason text, gap text, resume_variant text,
  run_date date,
  primary key (job_id, person)
);
create index matches_person_run_date_idx on matches (person, run_date);

create table drafts (
  id uuid primary key default gen_random_uuid(),
  person text not null,
  job_id text not null references jobs(id) on delete cascade,
  to_email text, subject text, body text, resume_variant text,
  status text not null default 'new',
  created_at timestamptz not null default now(),
  updated_at timestamptz
);
create index drafts_person_created_idx on drafts (person, created_at desc);

create table runs (
  run_date date primary key,
  started_at timestamptz, finished_at timestamptz,
  status text, stats jsonb, errors jsonb,
  digest_sent jsonb not null default '[]'
);

create table login_failures (
  ip text not null,
  at timestamptz not null default now()
);
create index login_failures_ip_at_idx on login_failures (ip, at);
