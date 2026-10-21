"""Vercel entrypoint: exposes the ASGI app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ezapply.web.app import app  # noqa: E402,F401
