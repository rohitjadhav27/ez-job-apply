"""Final match percentage, threshold, ordering, cap."""
from __future__ import annotations

from typing import TYPE_CHECKING

from ezapply.core.models import Scored

if TYPE_CHECKING:
    from ezapply.core.config import Settings


def finalize(scored: list[Scored], settings: "Settings", ai_available: bool) -> list[Scored]:
    for s in scored:
        if not ai_available:
            s.match_pct = s.rule_pct
        elif s.ai_pct is not None:
            s.match_pct = round(0.6 * s.ai_pct + 0.4 * s.rule_pct)
        else:
            s.match_pct = round(s.rule_pct * 0.85)
    kept = [s for s in scored if s.match_pct >= settings.min_match_pct]
    kept.sort(key=lambda s: s.match_pct, reverse=True)
    return kept[: settings.max_items_per_digest]
