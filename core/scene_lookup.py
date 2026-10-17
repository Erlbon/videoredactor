"""
core/scene_lookup.py

What the two scene-database sources (ThePornDB, StashDB) share: the result type, the ONE place that maps a result onto
the app's metadata fields, the two settings, the file hashes, the polite HTTP pieces and the search text taken from a
file name. The clients themselves are core/theporndb_client.py and core/stashdb_client.py. Qt-free.

What leaves the machine: a file hash (OSHASH; MD5 only when Preferences says so) and/or the search text, plus the API
key in a header to the service that issued it. Nothing else: no path, no file name unless the search text is one, no
other tag. No image is ever fetched by a search: a cover is downloaded only after the user applied a result AND ticked
"Save the cover as a sidecar".

Field mapping (result -> VideoMetadata), see scene_fields():

    title                    -> title
    release date             -> release_date            (as the service gives it: a full date or a bare year)
    description              -> description
    studio / site            -> studio
    parent studio / network  -> collection              (only when the studio has one)
    performers as credited   -> cast                    (comma separated; StashDB's "as" credit, else the name)
    tags                     -> genre_tags              (comma separated)
    director(s)              -> director
    the service's page       -> comment                 (one line "<Service>: <url>" appended to what is there)
    content type             -> Movie, only when it is not set yet (the one existing type that shows Cast and Studio)

No new field and no new ContentType: the code (SKU) and the duration have no field and are shown in the dialog only.
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from core import config
from core.opensubtitles_hash import compute_moviehash
from core.release_name_parser import parse_release_name
from core.version import APP_VERSION
from core.video_metadata import ContentType
from redactor_common.core.lookup_client import SameSiteRedirects, make_default_fetch

# The services ask for a descriptive User-Agent; this one names the app, its version and its author.
USER_AGENT = f"TheVideoRedactor/{APP_VERSION} (https://github.com/Erlbon)"

SECTION = "scene_lookup"
DEFAULT_STASH_ENDPOINT = "https://stashdb.org/graphql"

MATCH_FILE = "file match"
MATCH_TITLE = "title match"
MATCH_ID = "id match"  # the file already carries the service's id (THEPORNDB_ID / STASHDB_ID)
TITLE_WARNING = "match by title: check it"

# Where to get a key (shown when none is set).
KEY_HELP = {
    "theporndb": ("ThePornDB", "https://theporndb.net/user/api-tokens",
                  "Register at theporndb.net, then create a token under your profile > API Tokens."),
    "stashdb": ("StashDB", "https://stashdb.org/register",
                "Register at stashdb.org (it asks for an invite key, see guidelines.stashdb.org), then copy the API "
                "key from your profile."),
}

_MAX_TEXT = 8000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HEX16 = re.compile(r"[0-9a-fA-F]{16}")


class SceneLookupError(Exception):
    """Base of both services' errors; the message is meant to be shown as it is."""


class SceneAuthError(SceneLookupError):
    """The key is missing or the service did not accept it."""


class SceneRateLimited(SceneLookupError):
    """HTTP 429 after the retries."""


@dataclass
class SceneResult:
    source: str                      # "ThePornDB" / "StashDB"
    external_id: str                 # the service's id (a UUID); empty when it sent none
    title: str = ""
    date: str = ""                   # "YYYY-MM-DD", "YYYY-MM" or "YYYY"; "" when unknown
    studio: str = ""
    parent_studio: str = ""
    performers: list[str] = field(default_factory=list)   # as credited
    tags: list[str] = field(default_factory=list)
    description: str = ""
    director: str = ""
    urls: list[str] = field(default_factory=list)          # the studio's own pages for it
    page_url: str = ""                                     # the page on the service
    cover_url: str = ""                                    # the first poster candidate ("" = the record has no poster)
    poster_urls: list[str] = field(default_factory=list)   # poster-type pictures only (never a background or a scene still)
    landscape_urls: list[str] = field(default_factory=list)  # wide pictures: used only when the user accepts a landscape cover
    kind: str = "scene"                                    # ThePornDB: scene / movie / jav (StashDB: scene)
    id_trusted: bool = True                                # False for a stash-box that is not stashdb.org: its id is not a StashDB id
    code: str = ""
    duration: Optional[int] = None                         # seconds
    match_kind: str = MATCH_TITLE

    @property
    def year(self) -> str:
        return self.date[:4] if re.match(r"\d{4}", self.date or "") else ""

    @property
    def is_file_match(self) -> bool:
        return self.match_kind == MATCH_FILE

    @property
    def is_id_match(self) -> bool:
        return self.match_kind == MATCH_ID

    def summary(self) -> str:
        who = ", ".join(self.performers[:3]) + (" ..." if len(self.performers) > 3 else "")
        return " | ".join(p for p in (self.title or "(untitled)", self.studio, self.date, who) if p)


# --- cleaning what the services send -------------------------------------------------------------------------------


def clean_text(value: object, limit: int = _MAX_TEXT, single_line: bool = False) -> str:
    """`value` as text a tag can hold: a string only (anything else is empty), no control characters, trimmed, cut at
    `limit`. A single-line value has its line breaks turned into spaces."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL.sub("", value).replace("\r\n", "\n").replace("\r", "\n")
    if single_line:
        text = " ".join(text.split())
    return text.strip()[:limit]


def clean_date(value: object) -> str:
    """A date as "YYYY-MM-DD" / "YYYY-MM" / "YYYY": the leading date of whatever the service sent ("2021-11-15T..."),
    else empty."""
    text = clean_text(value, 40, single_line=True)
    match = re.match(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", text)
    if not match:
        return ""
    year, month, day = match.groups()
    return "-".join(p for p in (year, month, day) if p)


def clean_names(values: object, limit: int = 200) -> list[str]:
    """Names as a list of single-line texts without duplicates, in order. A name never holds a comma (the fields are
    comma separated lists)."""
    result: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else []:
        name = clean_text(value, 200, single_line=True).replace(",", " ")
        name = " ".join(name.split())
        if name and name not in result:
            result.append(name)
        if len(result) >= limit:
            break
    return result


def clean_url(value: object) -> str:
    """An http(s) URL as text, else empty (a service must not make the app show a file: or javascript: link)."""
    text = clean_text(value, 1000, single_line=True)
    return text if urllib.parse.urlsplit(text).scheme.lower() in ("http", "https") else ""


# --- the mapping onto the app's fields (the one place) ----------------------------------------------------------------


def comment_line(result: SceneResult) -> str:
    """The one line the comment gets: the service and the page of the record, or its id when there is no page."""
    where = result.page_url or (f"id {result.external_id}" if result.external_id else "")
    return f"{result.source}: {where}" if where else ""


def scene_fields(result: SceneResult, existing_comment: str = "") -> dict[str, str]:
    """The fields `result` can fill, as text, EMPTY values left out. The comment is `existing_comment` plus the
    service line (not repeated when it is already there). Never includes content_type: the callers set it."""
    line = comment_line(result)
    existing = (existing_comment or "").strip()
    if not line or line in existing:
        comment = ""
    else:
        comment = f"{existing}\n{line}" if existing else line
    fields = {
        "title": clean_text(result.title, 500, single_line=True),
        "release_date": clean_date(result.date),
        "description": clean_text(result.description),
        "studio": clean_text(result.studio, 200, single_line=True),
        "collection": clean_text(result.parent_studio, 200, single_line=True) if result.studio else "",
        "cast": ", ".join(clean_names(result.performers)),
        "genre_tags": ", ".join(clean_names(result.tags)),
        "director": clean_text(result.director, 300, single_line=True),
        "comment": comment,
    }
    return {key: value for key, value in fields.items() if value}


CONTENT_TYPE = ContentType.MOVIE
# What a lookup could still fill (the Redact step does nothing when all of these are set).
FILL_FIELDS = ("title", "release_date", "description", "studio", "cast", "genre_tags", "director")


# --- settings ---------------------------------------------------------------------------------------------------------


def normalize_endpoint(text: str) -> str:
    """A stash-box endpoint as the URL to POST to: http or https with a host; a bare site address gets "/graphql".
    ValueError (with the reason) for anything else, so a typo is never saved."""
    value = (text or "").strip()
    parts = urllib.parse.urlsplit(value)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("The StashDB address must start with https:// (or http://) and name a server, for example "
                         f"{DEFAULT_STASH_ENDPOINT}")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("The StashDB address must not hold a user name, password or query: the key goes in the key field.")
    path = parts.path.rstrip("/") or "/graphql"
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc, path, "", ""))


def stash_endpoint() -> str:
    """The configured stash-box endpoint (default StashDB); an unreadable setting means the default."""
    try:
        return normalize_endpoint(config.get_setting(SECTION, "stashdb_endpoint", "") or DEFAULT_STASH_ENDPOINT)
    except Exception:  # noqa: BLE001 -- a hand-edited bad value must never send the key anywhere odd
        return DEFAULT_STASH_ENDPOINT


def set_stash_endpoint(text: str) -> str:
    """Stores the normalised endpoint (the default is stored as "nothing"); returns it. ValueError when invalid."""
    value = normalize_endpoint(text)
    if value == DEFAULT_STASH_ENDPOINT:
        config.remove_setting(SECTION, "stashdb_endpoint")
    else:
        config.set_setting(SECTION, "stashdb_endpoint", value)
    return value


def md5_enabled() -> bool:
    """Preferences > Look Up: also use the MD5 of the whole file (reads all of it). Default OFF."""
    return config.get_setting(SECTION, "md5", "") == "1"


def set_md5_enabled(enabled: bool) -> None:
    if enabled:
        config.set_setting(SECTION, "md5", "1")
    else:
        config.remove_setting(SECTION, "md5")


def page_base(endpoint: str) -> str:
    """The site of a stash-box endpoint ("https://stashdb.org/graphql" -> "https://stashdb.org")."""
    parts = urllib.parse.urlsplit(endpoint)
    path = parts.path
    if path.endswith("/graphql"):
        path = path[: -len("/graphql")]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path.rstrip("/"), "", ""))


# --- file hashes -------------------------------------------------------------------------------------------------------


@dataclass
class FileHashes:
    oshash: str = ""   # 16 lowercase hex characters, "" when the file is under 128 KB
    md5: str = ""      # 32 lowercase hex characters, only when asked for


def valid_oshash(text: str) -> bool:
    return bool(_HEX16.fullmatch(text or ""))


def compute_md5(path: str, cancel: Optional[threading.Event] = None,
                progress: Optional[Callable[[int, int], None]] = None) -> str:
    """The MD5 of the WHOLE file ("" when `cancel` was set meanwhile). Reads every byte: only on request."""
    total = os.path.getsize(path)
    digest = hashlib.md5()  # noqa: S324 -- an identifier the services use, not security
    done = 0
    with open(path, "rb") as handle:
        while True:
            if cancel is not None and cancel.is_set():
                return ""
            block = handle.read(4 * 1024 * 1024)
            if not block:
                break
            digest.update(block)
            done += len(block)
            if progress is not None:
                progress(done, total)
    return digest.hexdigest()


def file_hashes(path: str, with_md5: bool = False, cancel: Optional[threading.Event] = None) -> FileHashes:
    """The hashes of one file. The OSHASH is the OpenSubtitles moviehash (both services use that algorithm: 64-bit
    sum of the size and the first and last 64 KB, 16 hex characters). The MD5 is computed only when `with_md5`."""
    hashes = FileHashes(oshash=compute_moviehash(path) or "")
    if with_md5:
        hashes.md5 = compute_md5(path, cancel)
    return hashes


# --- the search text from a file name -----------------------------------------------------------------------------------


def search_text_for(stem: str) -> tuple[str, str]:
    """(text, year) to prefill the search box from a file name: the cleaned title and the year when the name has one."""
    guess = parse_release_name(stem)
    return guess.title.strip(), guess.year or ""


# --- polite HTTP --------------------------------------------------------------------------------------------------------


# SameSiteRedirects: redactor_common's shared redirect policy (same scheme/host/port only, http/https only, capped);
# the API key travels in a header and urllib would carry it along to wherever the answer pointed.


def make_fetch(timeout: float, max_bytes: int = 8 * 1024 * 1024):
    """A fetch with this app's User-Agent that never follows a redirect to another host."""
    opener = urllib.request.build_opener(SameSiteRedirects())
    return make_default_fetch(USER_AGENT, timeout=timeout, opener=opener, max_bytes=max_bytes)
