"""URL safety helper (stdlib only)."""
from __future__ import annotations

from urllib.parse import urlparse


def safe_url(u: str | None) -> str | None:
    """Return the stripped URL only if it is http(s) with a host; otherwise None (blocks javascript: etc.)."""
    if not u:
        return None
    try:
        parsed = urlparse(u.strip())
    except ValueError:
        return None
    return u.strip() if parsed.scheme in ("http", "https") and parsed.netloc else None
