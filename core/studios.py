"""
core/studios.py

The Studios table: which website belongs to which studio (a site such as "Evil Angel" and its address). The website of a
file is DERIVED from its Studio through this table, so the same address is never stored on 500 files; the table is edited
in one place (Tools > Studios) and is filled for the user by applied scene lookups (the host of the studio's own page,
only when that studio has no website yet: the user's own entry always wins).

Stored in settings.ini ([studios] sites), one "studio<US>website" pair per entry, the studio matched case-insensitively.
A website is stored as a bare host name ("evilangel.com"); links are built with https://.
"""

from __future__ import annotations

import urllib.parse

from core.config import get_setting, set_setting

SECTION = "studios"
KEY = "sites"
_PAIR = "\x1e"   # between entries
_FIELD = "\x1f"  # between a studio and its website


def normalize_website(text: str) -> str:
    """'https://www.EvilAngel.com/en/video' -> 'evilangel.com'; '' when there is no usable host."""
    value = (text or "").strip()
    if not value:
        return ""
    parts = urllib.parse.urlsplit(value if "//" in value else "//" + value)
    host = (parts.hostname or "").lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if not host or "." not in host or any(c.isspace() for c in host):
        return ""
    return host


def _key(studio: str) -> str:
    return " ".join((studio or "").casefold().split())


def load_studios() -> dict[str, str]:
    """{studio name as stored: website} in the stored order."""
    raw = get_setting(SECTION, KEY, "")
    out: dict[str, str] = {}
    for entry in raw.split(_PAIR):
        name, _sep, site = entry.partition(_FIELD)
        if name.strip() and site.strip():
            out[name.strip()] = site.strip()
    return out


def save_studios(studios: dict[str, str]) -> None:
    clean = {}
    for name, site in studios.items():
        host = normalize_website(site)
        if name.strip() and host:
            clean[name.strip()] = host
    set_setting(SECTION, KEY, _PAIR.join(f"{n}{_FIELD}{h}" for n, h in clean.items()))


def website_for(studio: str, studios: dict[str, str] | None = None) -> str:
    """The website of `studio` ('' = unknown). A studio with several names ("A, B") uses the first with a website."""
    table = load_studios() if studios is None else studios
    by_key = {_key(n): h for n, h in table.items()}
    for name in (studio or "").replace(";", ",").split(","):
        host = by_key.get(_key(name))
        if host:
            return host
    return ""


def remember_from_lookup(studio: str, page_urls: list[str]) -> bool:
    """After an applied lookup: gives `studio` the host of the first page URL as its website, unless the table already
    has one for it. True when a new entry was stored."""
    name = " ".join((studio or "").split())
    if not name or website_for(name):
        return False
    for url in page_urls or []:
        host = normalize_website(url)
        if host:
            table = load_studios()
            table[name] = host
            save_studios(table)
            return True
    return False
