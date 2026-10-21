"""Lazily built settings, profiles and DB pool for the web app (no env needed at import time)."""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from ezapply.core.config import Settings, load_profiles, load_settings
from ezapply.core.models import Profile

PROFILES_DIR = Path(__file__).resolve().parents[2] / "profiles"


class Runtime:
    def __init__(self, settings: Settings, profiles: list[Profile],
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.profiles = {p.id: p for p in profiles}
        self.sleep = sleep
        self._pool: ConnectionPool | None = None

    @classmethod
    def from_env(cls) -> "Runtime":
        return cls(load_settings(), load_profiles(PROFILES_DIR))

    @contextmanager
    def conn(self) -> Iterator:
        if self._pool is None:
            # prepare_threshold=None: Neon's PgBouncer pooler rejects server-side prepared statements.
            self._pool = ConnectionPool(
                self.settings.database_url, min_size=0, max_size=3, open=False, check=ConnectionPool.check_connection,
                kwargs={"row_factory": dict_row, "autocommit": False, "prepare_threshold": None},
            )
            self._pool.open()
        with self._pool.connection() as c:
            yield c

    def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            self._pool = None


def get_runtime(request) -> Runtime:
    state = request.app.state
    if getattr(state, "runtime", None) is None:
        state.runtime = Runtime.from_env()
    return state.runtime
