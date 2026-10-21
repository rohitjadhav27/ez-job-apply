create table seen (
  person text not null,
  job_id text not null references jobs(id) on delete cascade,
  first_seen_at timestamptz not null default now(),
  primary key (person, job_id)
);
create index seen_person_first_seen_idx on seen (person, first_seen_at);
