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

No new field (the content type is Porn): the code (SKU) and the duration have no field and are shown in the dialog only.
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


CONTENT_TYPE = ContentType.PORN
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


# A file name that says nothing about the scene (clip1, scene 3, part2, 001, cd1 ...): the owner's clips are
# numbered while the FOLDER holds the movie's name. 'ep' / 'episode' are deliberately not here: TV is handled by the
# season/episode parser, not by this.
GENERIC_NAME_WORDS = ("clip", "scene", "part", "pt", "cd", "disc", "disk", "video", "vid", "file", "track", "movie",
                      "film", "sc")
MIN_NAME_LETTERS = 4  # a cleaned name shorter than this says nothing either

# Folders that are a catch-all place, not the name of a movie: never used for the search.
CATCH_ALL_FOLDERS = ("downloads", "download", "videos", "video", "movies", "movie", "films", "film", "porn", "adult",
                     "xxx", "new folder", "new", "desktop", "documents", "temp", "tmp", "torrents", "incoming",
                     "unsorted", "misc", "stuff", "clips", "scenes", "my videos", "recordings", "inbox")

_GENERIC_WORD_RE = re.compile(r"(?:%s)(?:\d{1,3}|[a-z])?" % "|".join(sorted(GENERIC_NAME_WORDS, key=len, reverse=True)))
_SHORT_CODE_RE = re.compile(r"[a-z]?\d{1,3}")


def _squash(text: str) -> str:
    """Lowercase with every separator removed ('Clip_3' -> 'clip3')."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def is_generic_stem(stem: str) -> bool:
    """True when a file name (without extension) says nothing about the scene: only digits, a generic word (clip,
    scene, part, cd ...) optionally followed by a number or one letter, a letter plus up to three digits, or fewer
    than MIN_NAME_LETTERS characters once separators are removed."""
    cleaned = _squash(stem)
    if len(cleaned) < MIN_NAME_LETTERS:
        return True
    return bool(cleaned.isdigit() or _GENERIC_WORD_RE.fullmatch(cleaned) or _SHORT_CODE_RE.fullmatch(cleaned))


def is_catch_all_folder(name: str) -> bool:
    return " ".join((name or "").lower().replace("_", " ").replace(".", " ").split()) in CATCH_ALL_FOLDERS


def part_marker(stem: str) -> tuple[str, Optional[int]]:
    """(the file name as the part marker, its first number or None): 'clip3' -> ('clip3', 3)."""
    match = re.search(r"\d+", stem or "")
    return (stem or "").strip(), int(match.group()) if match else None


@dataclass
class SearchChoice:
    """What the search box is filled with for one file, and what the folder could offer instead."""
    text: str  # from the file name
    year: str
    generic: bool  # the file name says nothing
    folder_name: str = ""  # the immediate parent folder's own name ('' = no usable folder)
    folder_text: str = ""  # that name cleaned for a search
    folder_year: str = ""
    part: str = ""  # the file name as the part marker ('clip3')
    part_number: Optional[int] = None

    @property
    def has_folder(self) -> bool:
        return bool(self.folder_text)

    @property
    def use_folder(self) -> bool:
        """The folder name is the starting point: the file name is generic and a usable folder exists."""
        return self.generic and self.has_folder


def parent_folder_candidate(path) -> tuple[str, str, str]:
    """(folder name, cleaned search text, year) of the file's IMMEDIATE parent folder, or ('', '', '') when it is a
    drive root, a catch-all folder (Downloads, Videos, New folder ...) or itself says nothing."""
    from pathlib import Path

    parent = Path(path).parent
    name = parent.name
    if not name or is_catch_all_folder(name) or is_generic_stem(name):
        return "", "", ""
    text, year = search_text_for(name)
    if not text or is_generic_stem(text):
        return "", "", ""
    return name, text, year


def search_choice_for(path) -> SearchChoice:
    from pathlib import Path

    stem = Path(path).stem
    text, year = search_text_for(stem)
    generic = is_generic_stem(stem)
    folder_name, folder_text, folder_year = parent_folder_candidate(path)
    part, number = part_marker(stem)
    return SearchChoice(text, year, generic, folder_name, folder_text, folder_year, part, number)


def folder_note(choice: SearchChoice) -> str:
    """The dialog's status sentence for a folder-name search."""
    if not choice.generic:
        return f'Searched for the folder name "{choice.folder_name}".'
    return (f'Searched for the folder name "{choice.folder_name}" because the file name "{choice.part}" says nothing. '
            f"Part: {choice.part}.")


def prefer_part_number(titles: list[str], number: Optional[int]) -> list[int]:
    """The order (indices) for a results list: a result whose title carries the part number ('Scene #03', 'Part 3',
    '#3', a trailing 3) goes first, everything else keeps its order below. Nothing changes without a number or a match."""
    order = list(range(len(titles)))
    if number is None:
        return order
    pattern = re.compile(r"(?:\b(?:scene|sc|part|pt|episode|ep)\s*|#\s*)0*%d\b|(?:^|\s)0*%d$" % (number, number), re.IGNORECASE)
    first = [i for i in order if pattern.search(titles[i] or "")]
    return first + [i for i in order if i not in first]


# --- the search guess: date, studio and title from the file name, the folders and the metadata ---------------------------

# Studios common enough that a folder named after one is read as the studio. Only names the owner's library is likely to
# hold AND that are unambiguous; studios seen in the loaded files' metadata are added at run time (known_studios).
COMMON_STUDIOS = (
    "Evil Angel", "Brazzers", "Bang Bros", "Reality Kings", "Naughty America", "Digital Playground", "Wicked Pictures",
    "Vixen", "Tushy", "Blacked", "Blacked Raw", "Deeper", "Mofos", "Twistys", "Kink", "Hustler", "Jules Jordan",
    "Girlsway", "Zero Tolerance", "Elegant Angel", "Sweetheart Video", "New Sensations", "Pure Taboo", "Team Skeet",
    "Nubile Films", "Falcon Studios", "Raging Stallion", "Lucas Entertainment", "Helix Studios", "Cockyboys",
    "Corbin Fisher", "Randy Blue", "Hot House", "Titan Media", "Treasure Island Media", "NakedSword", "Bel Ami",
)

MIN_PLAUSIBLE_YEAR = 1990
FEW_RESULTS = 5  # a title-only pass with fewer results than this (and no file match) is retried with the studio

_SEPARATORS = re.compile(r"[\s._]+")
_DATE_FULL = re.compile(r"^[\[(]?((?:19|20)\d{2})[-._ ](\d{2})[-._ ](\d{2})[\])]?(?![0-9])")
_DATE_SHORT = re.compile(r"^(\d{2})[-._ ](\d{2})[-._ ](\d{2})(?![0-9])")
_DATE_COMPACT = re.compile(r"^(\d{2})(\d{2})(\d{2})(?![0-9])")
_YEAR_LEAD = re.compile(r"^[\[(]?((?:19|20)\d{2})[\])]?(?=[\s._]|-(?![0-9])|$)")
_SCENE_TAIL = re.compile(r"[\s._-]*\b(?:scene|sc|part|pt)[\s._#-]*0*(\d{1,3})\s*$", re.IGNORECASE)
_XXX = re.compile(r"\bxxx\b", re.IGNORECASE)
_NEW = re.compile(r"\bNEW\b")  # upper case only: 'new' is a title word


def _valid_date(year: int, month: int, day: int) -> str:
    import datetime

    try:
        found = datetime.date(year, month, day)
    except ValueError:
        return ""
    if found.year < MIN_PLAUSIBLE_YEAR or found > datetime.date.today():
        return ""
    return found.isoformat()


def split_leading_date(text: str) -> tuple[str, str, str]:
    """(date 'YYYY-MM-DD' or '', year or '', the rest) of a name that STARTS with a date or a year: 2014-02-17,
    2014.02.17, 14.02.17, 140217 (YY.MM.DD, only when valid and 1990..today), '2014 ' and '(2014)'."""
    text = (text or "").strip()
    match = _DATE_FULL.match(text)
    if match:
        date = _valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if date:
            return date, date[:4], text[match.end():].strip(" ._-")
    for pattern in (_DATE_SHORT, _DATE_COMPACT):
        match = pattern.match(text)
        if match:
            yy = int(match.group(1))
            date = _valid_date((1900 if yy >= 90 else 2000) + yy, int(match.group(2)), int(match.group(3)))
            if date:
                return date, date[:4], text[match.end():].strip(" ._-")
    match = _YEAR_LEAD.match(text)
    if match and MIN_PLAUSIBLE_YEAR <= int(match.group(1)):
        rest = text[match.end():].strip(" ._-")
        if rest:
            return "", match.group(1), rest
    return "", "", text


def _squash_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def studio_prefix(text: str, studios) -> tuple[str, str]:
    """(the studio, the rest) when `text` starts with a known studio (words may be run together: 'EvilAngel.14.02.17')
    and the studio ends at a word boundary; the longest studio wins. ('', text) otherwise."""
    text = (text or "").strip()
    spans = [m for m in re.finditer(r"[^\s._-]+", text)]
    best_studio, best_count, best_len = "", 0, 0
    for studio in studios:
        want = _squash_name(studio)
        if len(want) < 3 or len(want) <= best_len:
            continue
        for count in range(1, min(len(spans), 5) + 1):
            if _squash_name("".join(m.group() for m in spans[:count])) == want:
                best_studio, best_count, best_len = studio, count, len(want)
    if not best_studio:
        return "", text
    return best_studio, text[spans[best_count - 1].end():].strip(" ._-")


def studio_in_name(name: str, studios) -> tuple[str, str]:
    """(studio, rest) when the folder `name` equals a known studio or starts with one ('Evil Angel - Title',
    'Evil Angel Title 1080p'); the rest is '' when the folder IS the studio."""
    found, rest = studio_prefix(name, studios)
    return found, rest


def _clean_title(text: str) -> tuple[str, Optional[int], str]:
    """(title, part number, year in the text) from a name without its date and studio: release tags, XXX / NEW, a
    trailing 'Scene 3' / 'Part 2' marker and a release-group tail are gone."""
    guess = parse_release_name(text)
    title = _XXX.sub(" ", guess.title)
    title = _NEW.sub(" ", title)
    title = re.sub(r"\s+", " ", title).strip(" -_.")
    part = None
    match = _SCENE_TAIL.search(title)
    if match and match.start() > 0:
        part = int(match.group(1))
        title = title[: match.start()].strip(" -_.")
    return title, part, guess.year or ""


def _last_small_number(stem: str) -> Optional[int]:
    found = re.findall(r"(?<![0-9])[0-9]{1,3}(?![0-9])", stem or "")
    return int(found[-1]) if found else None


def known_studios_from(files) -> list[str]:
    """The distinct studios in the loaded files' metadata (a file without one adds nothing)."""
    seen: dict[str, str] = {}
    for vf in files or []:
        studio = (getattr(getattr(vf, "metadata", None), "studio", "") or "").strip()
        if studio:
            seen.setdefault(studio.casefold(), studio)
    return list(seen.values())


@dataclass
class SearchGuess:
    """What the search boxes are filled with for one file, and where each piece came from."""
    text: str = ""
    year: str = ""
    date: str = ""            # the full release date from a leading date in the name ('' = none)
    studio: str = ""
    studio_source: str = ""   # 'metadata', 'file name', 'folder', 'parent folder' ... ('' = no studio)
    performers: list = field(default_factory=list)
    part_number: Optional[int] = None
    title_from_folder: bool = False
    title_source: str = ""    # 'metadata' when the file's own title tag is the search text, else '' (name or folder)
    date_source: str = ""
    source_notes: list = field(default_factory=list)

    def describe(self) -> str:
        """The status sentence: Searched for: "T" - studio from folder: S - date from file name: D"""
        parts = [f'Searched for: "{self.text}"' + (" (title from metadata)" if self.title_source == "metadata" else "")]
        if self.studio:
            parts.append(f"studio from {self.studio_source}: {self.studio}")
        if self.date or self.year:
            parts.append(f"{'date' if self.date else 'year'} from {self.date_source or 'file name'}: {self.date or self.year}")
        parts.extend(self.source_notes)
        return " - ".join(parts)


def guess_search(path, metadata=None, known_studios=(), prefer_folder: Optional[bool] = None) -> SearchGuess:
    """The search guess for one file from its stem, its folders and its existing metadata.

    - A studio the file's metadata already has wins; else a known studio at the START of the file name, then the
      parent folder, then the grandparent folder (equal to, or starting with, a known studio: the studios in
      `known_studios` plus COMMON_STUDIOS).
    - A leading date or year is taken out of the title and kept (the date ranks results, the year filters).
    - A generic file name (23496_01_1080p, clip3) takes the folder's title ('Evil Angel - Strap Some Boyz 2 1080p'
      gives the studio and the title); `prefer_folder` True/False overrides that choice, None decides.
    The text never contains the studio; the dialog adds it only to a second search pass."""
    from pathlib import Path

    path = Path(path)
    meta_studio = (getattr(metadata, "studio", "") or "").strip()
    studios = list(dict.fromkeys(([meta_studio] if meta_studio else []) + list(known_studios or ()) + list(COMMON_STUDIOS)))
    guess = SearchGuess()
    if meta_studio:
        guess.studio, guess.studio_source = meta_studio, "metadata"
    cast = (getattr(metadata, "cast", "") or "").strip()
    if cast:
        guess.performers = [n.strip() for n in cast.split(",") if n.strip()]

    # the file name: studio prefix, then a date, then the title
    stem = path.stem
    rest = stem
    stem_studio, after = studio_prefix(stem, studios)
    if stem_studio:
        rest = after
        if not guess.studio:
            guess.studio, guess.studio_source = stem_studio, "file name"
    date, year, rest = split_leading_date(rest)
    file_text, file_part, file_year = _clean_title(rest)
    file_year = year or file_year
    generic = is_generic_stem(stem) or not file_text or is_generic_stem(file_text)

    # the folders: the immediate parent, then its parent
    folder_title = ""
    folder_date, folder_year = "", ""
    for level, folder in ((0, path.parent), (1, path.parent.parent)):
        name = folder.name
        if not name or is_catch_all_folder(name) or is_generic_stem(name):
            continue
        studio, remainder = studio_in_name(name, studios)
        if studio and not guess.studio:
            guess.studio = studio
            guess.studio_source = "folder" if level == 0 else "parent folder"
        if level == 0:
            use = remainder if studio else name
            if use:
                f_date, f_year, use = split_leading_date(use)
                title, _folder_part, y = _clean_title(use)
                if title and not is_generic_stem(title) and not (studio and not remainder):
                    folder_title, folder_date, folder_year = title, f_date, f_year or y
    use_folder = generic and bool(folder_title) if prefer_folder is None else (prefer_folder and bool(folder_title))
    if use_folder:
        guess.text, guess.title_from_folder = folder_title, True
        if folder_date or folder_year:
            guess.date, guess.year, guess.date_source = folder_date, (folder_date[:4] or folder_year), "folder name"
        else:
            guess.date, guess.year, guess.date_source = date, file_year, "file name"
        guess.part_number = _last_small_number(stem) if generic else file_part
        guess.source_notes.append(f'title from folder "{path.parent.name}"')
    else:
        guess.text = file_text
        guess.date, guess.year = date, file_year
        guess.date_source = "file name"
        guess.part_number = file_part
    # What the file already says about itself beats anything guessed from its name: a title and a release date the
    # user (or an earlier lookup) put in the tags are the search, the file and folder names are only the fallback.
    # prefer_folder=True (the dialog's "Search with the folder name" box ticked by hand) still wins over the title.
    meta_title = (getattr(metadata, "title", "") or "").strip()
    if meta_title and prefer_folder is not True:
        guess.text, guess.title_from_folder, guess.title_source = meta_title, False, "metadata"
        guess.source_notes = [n for n in guess.source_notes if not n.startswith("title from folder")]
    meta_date = (getattr(metadata, "release_date", "") or "").strip()
    if len(meta_date) >= 4 and meta_date[:4].isdigit():
        if len(meta_date) >= 10 and meta_date[4] in "-./" and meta_date[5:7].isdigit() and meta_date[8:10].isdigit():
            guess.date, guess.year = f"{meta_date[:4]}-{meta_date[5:7]}-{meta_date[8:10]}", meta_date[:4]
            guess.date_source = "metadata"
        elif len(meta_date) == 4:
            guess.date, guess.year, guess.date_source = "", meta_date, "metadata"
    return guess


def studio_matches(result_studio: str, result_parent: str, wanted: str) -> bool:
    want = _squash_name(wanted)
    if len(want) < 3:
        return False
    for candidate in (result_studio, result_parent):
        have = _squash_name(candidate)
        if have and (have == want or (min(len(have), len(want)) >= 4 and (have.startswith(want) or want.startswith(have)))):
            return True
    return False


def rank_results(results: list, date: str = "", studio: str = "") -> list:
    """The results in display order, a stable sort: exact file and id matches first, then results whose release date
    equals the guessed `date`, then those whose studio equals the guessed `studio`. Nothing else is reordered."""
    def key(result) -> tuple:
        exact = 0 if (result.is_file_match or result.is_id_match) else 1
        by_date = 0 if date and result.date == date else 1
        by_studio = 0 if studio and studio_matches(result.studio, result.parent_studio, studio) else 1
        return exact, by_date, by_studio

    return sorted(results, key=key)


def rank_marks(result, date: str = "", studio: str = "") -> list[str]:
    marks = []
    if date and result.date == date:
        marks.append("date matches")
    if studio and studio_matches(result.studio, result.parent_studio, studio):
        marks.append("studio matches")
    return marks


def wants_studio_pass(found: list, studio: str, text: str) -> bool:
    """A title-only pass found no file match and few results: retry with the studio in the text (unless it is there)."""
    if not studio or _squash_name(studio) in _squash_name(text):
        return False
    return len(found) < FEW_RESULTS and not any(r.is_file_match or r.is_id_match for r in found)


# --- polite HTTP --------------------------------------------------------------------------------------------------------


# SameSiteRedirects: redactor_common's shared redirect policy (same scheme/host/port only, http/https only, capped);
# the API key travels in a header and urllib would carry it along to wherever the answer pointed.


def make_fetch(timeout: float, max_bytes: int = 8 * 1024 * 1024):
    """A fetch with this app's User-Agent that never follows a redirect to another host."""
    opener = urllib.request.build_opener(SameSiteRedirects())
    return make_default_fetch(USER_AGENT, timeout=timeout, opener=opener, max_bytes=max_bytes)
