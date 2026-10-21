from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from ezapply.core.models import Profile, RawItem

if TYPE_CHECKING:
    from ezapply.core.config import Settings

MAX_DESCRIPTION = 8000

_SUFFIXES = ("pvt ltd", "private limited", "ltd", "inc", "llp", "technologies")

_CITY_ALIASES = (
    ("navi mumbai", "Mumbai"),
    ("bengaluru", "Bangalore"),
    ("bangalore", "Bangalore"),
    ("blr", "Bangalore"),
    ("mumbai", "Mumbai"),
    ("bombay", "Mumbai"),
    ("thane", "Mumbai"),
    ("pune", "Pune"),
)


class Source(Protocol):
    name: str
    errors: list[str]

    def fetch(self, profile: Profile, settings: Settings) -> list[RawItem]: ...


def normalize_text(s: str) -> str:
    t = re.sub(r"[^\w\s]", " ", s.lower())
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed:
        changed = False
        for suf in _SUFFIXES:
            if t == suf:
                continue
            if t.endswith(" " + suf):
                t = t[: -len(suf)].rstrip()
                changed = True
        # trailing "India" is a regional suffix ("Equifax India"), but not in "Air India"
        head = t[: -len(" india")] if t.endswith(" india") else ""
        if head and head != "air":
            t = head
            changed = True
    return t


def city_of(location: str) -> str | None:
    loc = normalize_text(location)
    for alias, city in _CITY_ALIASES:
        if re.search(rf"\b{re.escape(alias)}\b", loc):
            return city
    return None


def cities_of(text: str) -> set[str]:
    """ALL canonical cities whose aliases appear in text (whole-word, case-insensitive)."""
    loc = normalize_text(text)
    return {city for alias, city in _CITY_ALIASES if re.search(rf"\b{re.escape(alias)}\b", loc)}


def _sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def item_key(item: RawItem) -> str:
    if item.kind == "post":
        return _sha1(item.url)
    where = city_of(item.location) or normalize_text(item.location)
    return _sha1(f"{normalize_text(item.company)}|{normalize_text(item.title)}|{where}")


def _clean(s: str) -> str:
    return s.replace("\x00", "")


def normalize_item(item: RawItem) -> RawItem:
    out = replace(
        item,
        title=_clean(item.title),
        company=_clean(item.company),
        description=_clean(item.description)[:MAX_DESCRIPTION],
    )
    out.key = item_key(out)
    return out


class SourceSkipped(Exception):
    """Raised by a source that cannot run (e.g. quota exhausted); reported as 'skipped'."""


_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_IMAGE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico"}
_MAILTO_RE = re.compile(r"mailto:([^\s?]+)", re.IGNORECASE)


def extract_emails(text: str) -> list[str]:
    """Lower-cased, de-duplicated emails; skips example.com and image-file look-alikes."""
    found: list[str] = []
    for m in _EMAIL_RE.findall(text or ""):
        email = m.lower().rstrip(".")
        local, domain = email.rsplit("@", 1)
        labels = domain.split(".")
        # trim a sentence run-on like "hr@acme.in.Thanks": drop trailing label when the
        # original had a capitalised word glued after a dot
        raw_labels = m.rstrip(".").rsplit("@", 1)[1].split(".")
        while len(labels) > 2 and raw_labels[-1][:1].isupper() and len(labels[-1]) > 3 and labels[-1] not in _COMMON_TLDS:
            labels.pop()
            raw_labels.pop()
        domain = ".".join(labels)
        email = f"{local}@{domain}"
        if domain == "example.com" or domain.endswith(".example.com"):
            continue
        if labels[-1] in _IMAGE_TLDS:
            continue
        if email not in found:
            found.append(email)
    return found


_COMMON_TLDS = {"com", "org", "net", "edu", "gov", "info", "co", "in", "io", "ai"}


def extract_emails_with_mailto(text: str, link_attrs: list | None = None) -> list[str]:
    """Emails from text plus any mailto: links in LinkedIn contentAttributes."""
    extra = []
    for a in link_attrs or []:
        m = _MAILTO_RE.search(str((a or {}).get("textLink") or ""))
        if m:
            extra.append(m.group(1))
    return extract_emails((text or "") + " " + " ".join(extra))
