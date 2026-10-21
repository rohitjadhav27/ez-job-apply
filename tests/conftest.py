import os
from pathlib import Path

import pytest


def _load_env_file() -> None:
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_env_file()

APP_TABLES = "drafts, matches, seen, jobs, runs, login_failures"


@pytest.fixture
def conn():
    url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    prod = os.environ.get("PROD_DATABASE_URL", "").strip()
    if prod and url == prod:
        pytest.skip("REFUSING to truncate: TEST_DATABASE_URL equals PROD_DATABASE_URL")
    from ezapply.core.db import apply_migrations, connect

    c = connect(url)
    apply_migrations(c)
    c.execute(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE")
    c.commit()
    yield c
    c.rollback()
    c.close()
