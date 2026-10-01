"""
core/imdb_import.py

Builds a compact, offline SQLite lookup database from IMDb's free
"non-commercial datasets" (the user downloads them from
https://datasets.imdbws.com/ -- this app NEVER downloads or bundles them):
title.basics.tsv.gz (required), and optionally title.ratings, title.episode
and title.akas. Streaming, reading and writing are redactor_common's
core/dump_import.py (header-validated TSV reader, SqliteBuilder, prebuilt
FTS5); what's here is the recipe.

LICENCE. IMDb's datasets are for PERSONAL, NON-COMMERCIAL use only; they
may not be redistributed. See LICENCE_NOTICE (shown in the settings
dialog) and https://www.imdb.com/conditions. The built database is the
user's own private copy.

FORMAT (verified 2026-10-01 from developer.imdb.com/non-commercial-datasets
-- redirects to data.imdb.com -- with no dataset downloaded): every file is
a gzipped, tab-separated, UTF-8 file whose FIRST LINE is a header, missing
values are written as backslash-N. Columns:
    title.basics   tconst titleType primaryTitle originalTitle isAdult
                   startYear endYear runtimeMinutes genres
    title.ratings  tconst averageRating numVotes
    title.episode  tconst parentTconst seasonNumber episodeNumber
    title.akas     titleId ordering title region language types attributes
                   isOriginalTitle
The reader fails loudly (DumpImportError) when a column named here is
missing from a file's header, so a changed or wrong file is never turned
into a silently empty database; extra new columns are ignored.
Checked 2026-10-01 against the first ~1 MB of each real file (none is stored in this repository): headers
exactly as documented, every line has the right number of columns, missing = backslash-N. Seen in the
real data: titleType values short, movie, tvSeries, tvMovie, tvEpisode, tvMiniSeries, tvShort (the full
set also has tvSpecial, video, videoGame); isAdult 0/1; startYear, runtimeMinutes and genres sometimes
missing; endYear mostly missing; tconst "tt" + 7 digits (parentTconst sometimes 8); title.episode has
~16% rows with both numbers missing; title.akas has region missing on original titles (isOriginalTitle=1)
and on some others, language missing on most rows, types imdbDisplay / original / alternative / working /
tv / dvd / video / festival / missing, and historic regions such as XWW, XWG, XEU, SUHH, DDDE, CSHH.

SCHEMA (tconst is the numeric part of "tt0004242"; text columns are ""
and numbers NULL when unknown):
    titles(tconst integer primary key, kind, title, original_title, year,
           end_year, runtime, genres, is_adult, rating, votes, norm)
        every kept NON-episode title (movie, tvSeries, tvMiniSeries, ...)
    episodes(tconst integer primary key, parent, season, episode, title,
             year, runtime, rating, votes)
        episodes of the kept series, with their series' tconst + numbers.
        Kept apart from `titles` so the full-text index and film queries
        don't wade through millions of episodes (IMDb has ~8.5M of them).
    akas(tconst, title, norm, region, language)
        alternative titles of kept titles, only for the chosen regions
        (and original titles), never one that equals the primary/original
        title once normalized.
    titles_fts(norm), akas_fts(norm)  prebuilt FTS5 over NORMALIZED text
        (core/local_db.normalize_words: punctuation, accents and "&" =
        "and" folded exactly like the query side). titles_fts's rowid IS
        the tconst; akas_fts's rowid is akas.rowid.
    redactor_import_info(key, value)
Indexes: akas(tconst), episodes(parent, season, episode).

SIZE AND SPEED (MEASURED on the START of IMDb's files, 2026-10-01: the first ~52k rows of title.basics, ~204k of
ratings, ~227k of episode, ~96k of akas -- 1890s-1950s shorts and films, NOT representative of the whole):
about 115 bytes per kept title and 96 per alternative title, FTS indexes included; ~140k input rows per
second end to end (4 s for the whole 578k-row sample). Episodes could not be measured there (the sample's
episodes belong to series outside it): about 52 bytes each on synthetic rows, so ~60-75 for real, longer
titles. ESTIMATE for the full files (title.basics ~12M rows, episode ~9M, akas ~50M+) with the default
options: ~0.6-0.9M titles (~70-105 MB) + ~5-7M numbered episodes of kept series (~300-525 MB) + ~2-4M
alternative titles (~190-385 MB) = roughly 0.6-1 GB (about 0.3-0.5 GB without episodes); 10-25 minutes; about
1 GB of scratch space (episode links and staged titles) while building. Episodes whose season/episode
numbers IMDb leaves empty (~16% of title.episode's first rows) are not stored: they can't be looked up.

Memory is flat: one line at a time, SqliteBuilder batches, and the
ratings / episode links / kept-title names live in a TEMPORARY on-disk
SQLite file (<dest>.imdb.tmp, deleted at the end or on cancel/failure)
that the passes look up per batch -- never a Python dict of millions of
rows. Passes, in order: ratings, episode links, basics (+ staging of
episode titles), episodes join, akas, full-text indexes.
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from typing import Callable, Iterator, Optional

from redactor_common.core.dump_import import (
    INFO_TABLE,
    DumpImportError,
    ImportCancelled,
    ReadStats,
    SqliteBuilder,
    iter_tsv_records,
    open_dump,
)
from redactor_common.core.local_db import LocalDatabase, LocalDatabaseError, normalize_words

SOURCE_NAME = "IMDb non-commercial datasets"
RECIPE = "imdb-titles/1"
DATASETS_URL = "https://datasets.imdbws.com/"
CONDITIONS_URL = "https://www.imdb.com/conditions"
DOCS_URL = "https://developer.imdb.com/non-commercial-datasets/"
NULL = "\\N"  # backslash + N: how IMDb writes a missing value

LICENCE_NOTICE = (
    "IMDb datasets may be used for personal, non-commercial purposes only. You may hold a local copy for "
    "your own use. They must not be altered, republished, resold or repurposed to create a database for "
    "others, and IMDb may withdraw permission at any time. This app never bundles or downloads the data: "
    "you download it yourself, from IMDb's datasets only."
)
# IMDb requires this exact statement wherever its data is used.
ATTRIBUTION = "Information courtesy of IMDb (https://www.imdb.com). Used with permission."

BASICS_COLUMNS = ["tconst", "titleType", "primaryTitle", "originalTitle", "isAdult", "startYear", "endYear",
                  "runtimeMinutes", "genres"]
RATINGS_COLUMNS = ["tconst", "averageRating", "numVotes"]
EPISODE_COLUMNS = ["tconst", "parentTconst", "seasonNumber", "episodeNumber"]
AKAS_COLUMNS = ["titleId", "ordering", "title", "region", "language", "types", "attributes", "isOriginalTitle"]

# (id as in title.basics' titleType, label, on by default)
TYPE_CHOICES: list[tuple[str, str, bool]] = [
    ("movie", "Movies", True),
    ("tvMovie", "TV movies", True),
    ("tvSeries", "TV series", True),
    ("tvMiniSeries", "TV miniseries", True),
    ("tvEpisode", "TV episodes", True),
    ("tvSpecial", "TV specials", True),
    ("video", "Direct-to-video", True),
    ("short", "Short films", False),
    ("tvShort", "TV shorts", False),
    ("videoGame", "Video games", False),
]
DEFAULT_TYPES = tuple(t for t, _label, on in TYPE_CHOICES if on)

# Regions kept for alternative titles: (IMDb region code, label). XWW is IMDb's "worldwide".
REGION_CHOICES: list[tuple[str, str]] = [
    ("NO", "Norway"), ("DE", "Germany"), ("FR", "France"), ("IT", "Italy"), ("US", "United States"),
    ("GB", "United Kingdom"), ("XWW", "Worldwide (XWW)"), ("SE", "Sweden"), ("DK", "Denmark"),
    ("ES", "Spain"), ("NL", "Netherlands"), ("CA", "Canada"), ("AU", "Australia"),
    # IMDb tags many pre-1990 German release titles with these historic codes (seen in the real sample: XWG on
    # ~6% as many rows as DE, DDDE on ~0.6%). The other historic codes (SUHH, CSHH, XYU, ...) are left out.
    ("XWG", "West Germany (historic)"), ("DDDE", "East Germany (historic)"),
]
DEFAULT_REGIONS = ("NO", "DE", "FR", "IT", "US", "GB", "XWW", "XWG")

DEFAULT_MIN_VOTES = 5

TABLES = {
    "titles": [
        "tconst integer primary key", "kind text", "title text", "original_title text", "year integer",
        "end_year integer", "runtime integer", "genres text", "is_adult integer", "rating real", "votes integer",
        "norm text",
    ],
    "episodes": [
        "tconst integer primary key", "parent integer", "season integer", "episode integer", "title text",
        "year integer", "runtime integer", "rating real", "votes integer",
    ],
    "akas": ["tconst integer", "title text", "norm text", "region text", "language text"],
}
INDEXES = [
    "create index akas_tconst on akas(tconst)",
    "create index episodes_parent on episodes(parent, season, episode)",
]

_BATCH = 4000      # basics rows resolved against the temp tables at a time
_SQL_CHUNK = 500   # keys per "in (...)" query


@dataclass
class BuildOptions:
    """What is kept. `types`: title types (ids from TYPE_CHOICES);
    `min_votes`: films and series with fewer votes are left out (episodes
    never need votes; 0 keeps everything -- a much bigger database);
    `include_episodes`: needed to look up an episode by series + season +
    episode (the biggest part of the file); `include_akas` + `regions`:
    alternative titles for those regions (and original titles)."""

    types: tuple[str, ...] = DEFAULT_TYPES
    skip_adult: bool = True
    min_votes: int = DEFAULT_MIN_VOTES
    include_episodes: bool = True
    include_akas: bool = True
    regions: tuple[str, ...] = DEFAULT_REGIONS
    aka_original: bool = True

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, text: str) -> "BuildOptions":
        """The saved options; anything missing or unreadable falls back to the defaults."""
        base = cls()
        try:
            data = json.loads(text) if text else {}
        except (ValueError, TypeError):
            return base
        if not isinstance(data, dict):
            return base
        known_types = {t for t, _l, _d in TYPE_CHOICES}

        def strings(key: str, default: tuple, allowed: Optional[set] = None) -> tuple:
            value = data.get(key)
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                return default
            return tuple(v for v in value if allowed is None or v in allowed)

        def boolean(key: str, default: bool) -> bool:
            return data[key] if isinstance(data.get(key), bool) else default

        votes = data.get("min_votes")
        return cls(
            types=strings("types", base.types, known_types),
            skip_adult=boolean("skip_adult", base.skip_adult),
            min_votes=votes if isinstance(votes, int) and not isinstance(votes, bool) and 0 <= votes <= 1_000_000
            else base.min_votes,
            include_episodes=boolean("include_episodes", base.include_episodes),
            include_akas=boolean("include_akas", base.include_akas),
            regions=tuple(r.upper() for r in strings("regions", base.regions)),
            aka_original=boolean("aka_original", base.aka_original),
        )

    def describe(self) -> str:
        names = [label for t, label, _d in TYPE_CHOICES if t in self.types]
        parts = [", ".join(names) or "no types"]
        if self.skip_adult:
            parts.append("no adult titles")
        if self.min_votes:
            parts.append(f"films and series with at least {self.min_votes} votes")
        parts.append("with episodes" if self.include_episodes and "tvEpisode" in self.types else "no episodes")
        if self.include_akas:
            parts.append("alternative titles for " + (", ".join(self.regions) or "no regions"))
        return "; ".join(parts)


@dataclass
class ImportSummary:
    titles_seen: int = 0
    titles: int = 0
    episodes: int = 0
    akas: int = 0
    skipped_type: int = 0
    skipped_adult: int = 0
    skipped_votes: int = 0
    bad_lines: int = 0
    rows: dict = field(default_factory=dict)
    sizes: dict = field(default_factory=dict)

    def describe(self) -> str:
        size = self.sizes.get("(file)")
        text = (
            f"{self.titles:,} titles, {self.episodes:,} episodes and {self.akas:,} alternative titles kept out of "
            f"{self.titles_seen:,} titles read ({self.skipped_votes:,} below the vote minimum)."
        )
        if size:
            text += f" Database size {size / (1 << 20):,.0f} MB."
        if self.bad_lines:
            text += f" {self.bad_lines:,} unreadable lines were skipped."
        return text


# --- field helpers -------------------------------------------------------------------------------


def tconst_number(text: Optional[str]) -> Optional[int]:
    """"tt0004242" -> 133093; None for anything else (also "nm..." ids)."""
    if text and text[:2] == "tt" and text[2:].isdigit():
        return int(text[2:])
    return None


def imdb_id(number: int) -> str:
    """4242 -> "tt0004242" (at least 7 digits, as IMDb writes them)."""
    return f"tt{int(number):07d}"


def _int(text: Optional[str]) -> Optional[int]:
    return int(text) if text and text.isdigit() else None


def _text(value: Optional[str]) -> str:
    return " ".join(value.split()) if value else ""


def _float(text: Optional[str]) -> Optional[float]:
    try:
        return float(text) if text else None
    except ValueError:
        return None


def _norm_pair(title: str, original: str) -> str:
    """What the full-text index holds for a title: the normalized title,
    plus the normalized original title when it differs."""
    first, second = normalize_words(title), normalize_words(original)
    return first if not second or second == first else f"{first} {second}"


def _scaled(progress: Optional[Callable[[float], None]], start: float, span: float):
    return (lambda fraction: progress(start + span * fraction)) if progress else None


def _file_date(path: str) -> str:
    try:
        return datetime.datetime.fromtimestamp(os.path.getmtime(path)).date().isoformat()
    except OSError:
        return ""


def _read(path: str, columns: list[str], progress, cancelled, stats: ReadStats) -> Iterator[dict]:
    """The records of one dataset file; fails loudly when a column is missing from its header."""
    with open_dump(path) as dump:
        yield from iter_tsv_records(
            dump, columns, header=True, null=NULL, progress=progress, cancelled=cancelled, stats=stats,
        )


def _chunks(items: list, size: int = _SQL_CHUNK) -> Iterator[list]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# --- the build -----------------------------------------------------------------------------------


def _open_temp(path: str) -> sqlite3.Connection:
    for leftover in (path, path + "-journal"):
        if os.path.exists(leftover):
            os.remove(leftover)
    con = sqlite3.connect(path)
    for pragma in ("journal_mode = OFF", "synchronous = OFF", "cache_size = -131072"):
        con.execute(f"pragma {pragma}")
    con.execute("create table ratings (tconst integer primary key, rating real, votes integer) without rowid")
    con.execute("create table links (tconst integer primary key, parent integer, season integer, "
                "episode integer) without rowid")
    con.execute("create table kept (tconst integer primary key, t1 text, t2 text) without rowid")
    con.execute("create table stage (tconst integer primary key, title text, year integer, runtime integer) "
                "without rowid")
    return con


def _load_ratings(con, path, progress, cancelled, summary) -> None:
    stats = ReadStats()
    batch: list[tuple] = []
    for rec in _read(path, RATINGS_COLUMNS, progress, cancelled, stats):
        number = tconst_number(rec["tconst"])
        votes = _int(rec["numVotes"])
        if number is None or votes is None:
            continue
        batch.append((number, _float(rec["averageRating"]), votes))
        if len(batch) >= 20000:
            con.executemany("insert or replace into ratings values (?, ?, ?)", batch)
            batch.clear()
    con.executemany("insert or replace into ratings values (?, ?, ?)", batch)
    con.commit()
    summary.bad_lines += stats.bad


def _load_links(con, path, progress, cancelled, summary) -> None:
    stats = ReadStats()
    batch: list[tuple] = []
    for rec in _read(path, EPISODE_COLUMNS, progress, cancelled, stats):
        number, parent = tconst_number(rec["tconst"]), tconst_number(rec["parentTconst"])
        if number is None or parent is None:
            continue
        batch.append((number, parent, _int(rec["seasonNumber"]), _int(rec["episodeNumber"])))
        if len(batch) >= 20000:
            con.executemany("insert or replace into links values (?, ?, ?, ?)", batch)
            batch.clear()
    con.executemany("insert or replace into links values (?, ?, ?, ?)", batch)
    con.commit()
    summary.bad_lines += stats.bad


def _ratings_for(con, numbers: list[int]) -> dict[int, tuple]:
    found: dict[int, tuple] = {}
    for chunk in _chunks(numbers):
        marks = ",".join("?" * len(chunk))
        for number, rating, votes in con.execute(
            f"select tconst, rating, votes from ratings where tconst in ({marks})", chunk
        ):
            found[number] = (rating, votes)
    return found


def build_imdb_database(
    basics_path: str,
    dest: str,
    ratings_path: str = "",
    episodes_path: str = "",
    akas_path: str = "",
    options: Optional[BuildOptions] = None,
    progress: Optional[Callable[[float], None]] = None,
    cancelled: Optional[Callable[[], bool]] = None,
) -> ImportSummary:
    """Reads title.basics (and the optional ratings / episode / akas files)
    and writes the lookup database to `dest`, replacing a previous build only
    once this one has succeeded. Raises DumpImportError for a missing file,
    a file whose header lacks the expected columns, or an empty result;
    ImportCancelled when `cancelled()` turns true (nothing is left behind)."""
    options = options or BuildOptions()
    summary = ImportSummary()
    if not basics_path or not os.path.isfile(basics_path):
        raise DumpImportError(f"title.basics file not found: {basics_path or '(not set)'}")
    for label, path in (("title.ratings", ratings_path), ("title.episode", episodes_path), ("title.akas", akas_path)):
        if path and not os.path.isfile(path):
            raise DumpImportError(f"{label} file not found: {path}")
    if not options.types:
        raise DumpImportError("No title types chosen -- tick at least one.")
    wanted = set(options.types)
    want_episodes = options.include_episodes and "tvEpisode" in wanted
    use_episodes = bool(episodes_path) and want_episodes
    use_akas = bool(akas_path) and options.include_akas
    use_ratings = bool(ratings_path)
    if options.min_votes and not use_ratings:
        raise DumpImportError(
            "A minimum number of votes needs the title.ratings file -- add it, or set the minimum to 0."
        )
    regions = {r.upper() for r in options.regions}

    # Progress: each file by its size, then the join, the indexes and the finish.
    sizes = {
        "ratings": os.path.getsize(ratings_path) if use_ratings else 0,
        "episodes": os.path.getsize(episodes_path) if use_episodes else 0,
        "basics": os.path.getsize(basics_path),
        "akas": os.path.getsize(akas_path) if use_akas else 0,
    }
    total = max(sum(sizes.values()), 1)
    reading = 0.88
    spans = {name: reading * size / total for name, size in sizes.items()}
    starts: dict[str, float] = {}
    at = 0.0
    for name in ("ratings", "episodes", "basics", "akas"):
        starts[name], at = at, at + spans[name]
    join_start, fts_start = at, at + 0.04
    temp_path = dest + ".imdb.tmp"
    temp: Optional[sqlite3.Connection] = None

    def check() -> None:
        if cancelled and cancelled():
            raise ImportCancelled()

    try:
        temp = _open_temp(temp_path)
        if use_ratings:
            _load_ratings(temp, ratings_path, _scaled(progress, starts["ratings"], spans["ratings"]), cancelled, summary)
        if use_episodes:
            _load_links(temp, episodes_path, _scaled(progress, starts["episodes"], spans["episodes"]), cancelled, summary)

        with SqliteBuilder(dest, TABLES, INDEXES) as out:
            # -- title.basics -------------------------------------------------------------
            stats = ReadStats()
            titles: list[tuple] = []     # non-episode rows waiting for their rating
            staged: list[tuple] = []     # episode rows

            def flush() -> None:
                if titles:
                    ratings = _ratings_for(temp, [row[0] for row in titles]) if use_ratings else {}
                    kept_rows = []
                    for row in titles:
                        rating, votes = ratings.get(row[0], (None, None))
                        if options.min_votes and (votes or 0) < options.min_votes:
                            summary.skipped_votes += 1
                            continue
                        out.add("titles", row[:9] + (rating, votes, row[9]))
                        kept_rows.append((row[0], normalize_words(row[2]), normalize_words(row[3])))
                    if kept_rows:
                        temp.executemany("insert or ignore into kept values (?, ?, ?)", kept_rows)
                    titles.clear()
                if staged:
                    temp.executemany("insert or replace into stage values (?, ?, ?, ?)", staged)
                    staged.clear()

            for rec in _read(basics_path, BASICS_COLUMNS, _scaled(progress, starts["basics"], spans["basics"]),
                             cancelled, stats):
                summary.titles_seen += 1
                kind = rec["titleType"] or ""
                if kind not in wanted:
                    summary.skipped_type += 1
                    continue
                if options.skip_adult and rec["isAdult"] == "1":
                    summary.skipped_adult += 1
                    continue
                number = tconst_number(rec["tconst"])
                if number is None:
                    stats.bad += 1
                    continue
                title = _text(rec["primaryTitle"])
                if kind == "tvEpisode":
                    if use_episodes:
                        staged.append((number, title, _int(rec["startYear"]), _int(rec["runtimeMinutes"])))
                else:
                    original = _text(rec["originalTitle"])
                    genres = (rec["genres"] or "").strip()
                    titles.append((
                        number, kind, title, original if original != title else "", _int(rec["startYear"]),
                        _int(rec["endYear"]), _int(rec["runtimeMinutes"]), genres,
                        1 if rec["isAdult"] == "1" else 0, _norm_pair(title, original),
                    ))
                if len(titles) + len(staged) >= _BATCH:
                    flush()
            flush()
            temp.commit()
            summary.bad_lines += stats.bad
            kept_count = temp.execute("select count(*) from kept").fetchone()[0]
            if kept_count == 0:
                raise DumpImportError(
                    f"No titles were kept -- none of the {summary.titles_seen:,} title.basics rows is of a chosen "
                    "type (and old enough in votes). Is this the right file, and are the options too strict?"
                )
            if progress:
                progress(join_start)
            check()

            # -- episodes: staged titles + links, only for kept parents -----------------------
            if use_episodes:
                cursor = temp.execute(
                    "select s.tconst, l.parent, l.season, l.episode, s.title, s.year, s.runtime, r.rating, r.votes "
                    "from stage s join links l on l.tconst = s.tconst join kept k on k.tconst = l.parent "
                    "left join ratings r on r.tconst = s.tconst "
                    # IMDb leaves the numbers empty on ~16% of episodes (specials, unsorted): they can't be
                    # looked up by season and episode, so they are not stored.
                    "where l.season is not null and l.episode is not null"
                )
                while True:
                    rows = cursor.fetchmany(20000)
                    if not rows:
                        break
                    out.add_many("episodes", rows)
                    check()

            # -- title.akas -----------------------------------------------------------------
            if use_akas:
                _load_akas(temp, out, akas_path, regions, options, _scaled(progress, starts["akas"], spans["akas"]),
                           cancelled, summary)
            if progress:
                progress(fts_start)
            out.create_fts_index("titles", ["norm"], progress=_scaled(progress, fts_start, 0.04), cancelled=cancelled)
            if use_akas:
                out.create_fts_index("akas", ["norm"], progress=_scaled(progress, fts_start + 0.04, 0.04),
                                     cancelled=cancelled)
            summary.rows = out.finish({
                "source": SOURCE_NAME, "recipe": RECIPE,
                "basics_file": os.path.basename(basics_path), "basics_file_date": _file_date(basics_path),
                "ratings_file": os.path.basename(ratings_path) if ratings_path else "",
                "episodes_file": os.path.basename(episodes_path) if episodes_path else "",
                "akas_file": os.path.basename(akas_path) if akas_path else "",
                "options": options.describe(), "options_json": options.to_json(),
                "titles_seen": summary.titles_seen, "skipped_votes": summary.skipped_votes,
                "bad_lines": summary.bad_lines,
            })
            summary.sizes = dict(out.sizes)
        summary.titles = summary.rows.get("titles", 0)
        summary.episodes = summary.rows.get("episodes", 0)
        summary.akas = summary.rows.get("akas", 0)
        _record_sizes(dest, summary.sizes)
    finally:
        if temp is not None:
            temp.close()
        for leftover in (temp_path, temp_path + "-journal"):
            try:
                os.remove(leftover)
            except OSError:
                pass
    if progress:
        progress(1.0)
    return summary


def _load_akas(temp, out, path, regions, options, progress, cancelled, summary) -> None:
    """Alternative titles of the kept titles, for the chosen regions (and original
    titles), skipping any that equals the title's own primary/original title."""
    stats = ReadStats()
    pending: list[tuple] = []

    def flush() -> None:
        if not pending:
            return
        names: dict[int, tuple] = {}
        for chunk in _chunks(sorted({row[0] for row in pending})):
            marks = ",".join("?" * len(chunk))
            for number, t1, t2 in temp.execute(f"select tconst, t1, t2 from kept where tconst in ({marks})", chunk):
                names[number] = (t1, t2)
        seen: set[tuple[int, str]] = set()
        for number, title, norm, region, language in pending:
            known = names.get(number)
            if known is None or not norm or norm in known or (number, norm) in seen:
                continue
            seen.add((number, norm))
            out.add("akas", (number, title, norm, region, language))
        pending.clear()

    for rec in _read(path, AKAS_COLUMNS, progress, cancelled, stats):
        region = rec["region"] or ""
        original = rec["isOriginalTitle"] == "1"
        if region.upper() not in regions and not (original and options.aka_original):
            continue
        number = tconst_number(rec["titleId"])
        title = _text(rec["title"])
        if number is None or not title:
            continue
        pending.append((number, title, normalize_words(title), region, rec["language"] or ""))
        if len(pending) >= _BATCH:
            flush()
    flush()
    summary.bad_lines += stats.bad


def _record_sizes(dest: str, sizes: dict) -> None:
    """Adds the measured sizes (SqliteBuilder only knows them after the file is in
    place) to the info table; a failure here is harmless."""
    try:
        con = sqlite3.connect(dest)
        try:
            con.executemany(
                f"insert or replace into {INFO_TABLE} values (?, ?)",
                [("size." + name.strip("()"), str(size)) for name, size in sizes.items()],
            )
            con.commit()
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass


class ImdbDatabaseError(LocalDatabaseError):
    """The local IMDb database is missing, unreadable or not the right kind of file."""


def database_info(path: str) -> dict[str, str]:
    """The import-info rows of a database built here. Raises ImdbDatabaseError for a
    missing file, a non-SQLite file, or a database not built by this recipe."""
    db = LocalDatabase(path, ("titles", INFO_TABLE), "an IMDb lookup database built by this app", ImdbDatabaseError)
    try:
        info = dict(db.query(f"select key, value from {INFO_TABLE}"))
    finally:
        db.close()
    if not info.get("recipe", "").startswith("imdb-titles/"):
        raise ImdbDatabaseError("This database wasn't built from IMDb datasets by this app.")
    return info


def describe_database(path: str) -> str:
    """A one-line status for the settings dialog: what it was built from, when, how big."""
    info = database_info(path)
    text = (f"Built from {info.get('basics_file') or 'title.basics'}"
            f"{' (' + info['basics_file_date'] + ')' if info.get('basics_file_date') else ''} on "
            f"{info.get('built', '')[:10] or 'an unknown date'}: {int(info.get('rows.titles', '0') or 0):,} titles")
    episodes = int(info.get("rows.episodes", "0") or 0)
    akas = int(info.get("rows.akas", "0") or 0)
    text += f", {episodes:,} episodes" if episodes else ", no episodes"
    text += f", {akas:,} alternative titles" if akas else ", no alternative titles"
    if info.get("options"):
        text += f". Kept: {info['options']}"
    size = info.get("size.file")
    if size and size.isdigit():
        text += f". {int(size) / (1 << 20):,.0f} MB."
    return text


def main(argv: list[str]) -> int:
    """python -m core.imdb_import title.basics.tsv.gz out.db [title.ratings.tsv.gz [title.episode.tsv.gz [title.akas.tsv.gz]]]"""
    if not 2 <= len(argv) <= 5:
        print("usage: python -m core.imdb_import <title.basics.tsv.gz> <output.db> [ratings [episode [akas]]]",
              file=sys.stderr)
        return 2
    last = [-1]

    def show(fraction: float) -> None:
        percent = int(fraction * 100)
        if percent != last[0]:
            last[0] = percent
            print(f"\r{percent:3d}%", end="", file=sys.stderr, flush=True)

    extra = list(argv[2:]) + [""] * 3
    summary = build_imdb_database(argv[0], argv[1], extra[0], extra[1], extra[2], progress=show)
    print(f"\n{summary.describe()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
