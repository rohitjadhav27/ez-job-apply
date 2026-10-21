"""Apply pending SQL migrations to DATABASE_URL."""
import os
import sys

from ezapply.core.db import apply_migrations, connect

if __name__ == "__main__":
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("DATABASE_URL is not set")
    conn = connect(url)
    try:
        apply_migrations(conn)
    finally:
        conn.close()
