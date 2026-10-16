"""
core/notes_local.py

What a video library folder IS, for Tools > Create Notes: which folders get a note, and what they hold. Qt-free, no
network. The note contract is Pluteus's (docs/notes-format.md, docs/library-layout.md); the shared writer is
redactor_common's core/notes.py.

  scan(paths, include_subfolders, kinds, include_outdated)   the folders that should get a note, and the ones left alone
  read_series(folder) / read_movie(folder)                   what the folder holds (seasons and episode counts, the title and year)

Two kinds of folder get a note:

  series   a folder holding episodes (S01E02 in the file names), directly or in Season folders      -> series.md
  movie    a folder holding one film (at most MAX_MOVIE_FILES videos: the film, a part 2, a trailer)  -> about.md

Anything else (a folder of many loose films, 'Movies', 'Downloads') gets no note and is walked into. A Season folder
chosen on its own stands for the series above it. A folder that already has a note of ANY of Pluteus's names is left
alone and listed in `skipped` (the writer never overwrites).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from core.release_name_parser import parse_release_name
from core.video_file import SUPPORTED_EXTENSIONS
from redactor_common.core.notes import NoteState, existing_note, note_status
from redactor_common.core.temp_names import is_app_temp_name

KIND_SERIES = "series"
KIND_MOVIE = "movie"
ALL_KINDS = (KIND_SERIES, KIND_MOVIE)
NOTE_KIND_OF = {KIND_SERIES: "series", KIND_MOVIE: "about"}  # the note file's kind
MAX_MOVIE_FILES = 4  # the film, its parts, a trailer: more than that is a folder of films, not one film

_SEASON_FOLDER = re.compile(r"^(?:season|series|saison|sesong|staffel|s)\s*[-_. ]?\s*(\d{1,2})\b|^specials?$", re.IGNORECASE)
_AIR_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01]))(?!\d)")  # 'Currahee (2001-09-09)'
_YEAR_SUFFIX = re.compile(r"\s*[(\[]\s*((?:19|20)\d{2})\s*[)\]]\s*$")
_SERIES_WORDS = re.compile(r"\s*[(\[]\s*(?:us|uk|au|ca|nz|[a-z]{2,3}\s+series|tv series)\s*[)\]]\s*$", re.IGNORECASE)
# Folders that are a place to keep video in, not a title.
GENERIC_FOLDER_NAMES = frozenset({
    "video", "videos", "movies", "movie", "films", "film", "tv", "tv shows", "tv series", "series", "shows", "serier",
    "filmer", "downloads", "incoming", "new", "media", "library", "misc", "clips", "music videos", "documentaries",
    "anime", "cartoons", "kids", "unsorted",
})


@dataclass
class Target:
    folder: Path
    kind: str
    update: Optional[NoteState] = None  # an older note of ours that may be refreshed (scan(include_outdated=True))

    @property
    def note_kind(self) -> str:
        return NOTE_KIND_OF[self.kind]


@dataclass
class Skipped:
    folder: Path
    kind: str
    reason: str
    state: str = ""  # the existing note's state ('edited', 'current', ...) when that is why


@dataclass
class ScanResult:
    targets: list[Target] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # named paths that do not exist


@dataclass
class LocalSeries:
    """What a series folder holds: `seasons` maps a season number (0 = specials) to its episode files."""

    folder: Path
    name: str  # the title to look up, cleaned of year and tags
    year: str = ""
    seasons: dict[int, list[str]] = field(default_factory=dict)  # season -> episode file names, sorted
    episodes: dict[int, list[int]] = field(default_factory=dict)  # season -> episode numbers found in the names
    dates: dict[int, dict[int, str]] = field(default_factory=dict)  # season -> {episode: 'YYYY-MM-DD' written in the file name}

    @property
    def episode_count(self) -> int:
        return sum(len(v) for v in self.seasons.values())


@dataclass
class LocalMovie:
    folder: Path
    title: str
    year: str = ""
    files: list[str] = field(default_factory=list)


# ---- names -----------------------------------------------------------------------------------------------------------

def title_and_year(folder_name: str) -> tuple[str, str]:
    """('Breaking Bad', '2008') from 'Breaking Bad (2008)', 'Breaking.Bad.2008.1080p' or '[2008] Breaking Bad'; the year
    is '' when the name has none. Country tags ('(US)', '(UK)') are kept in the title: they tell remakes apart."""
    name = (folder_name or "").strip()
    match = _YEAR_SUFFIX.search(name)
    if match:
        return name[: match.start()].strip(), match.group(1)
    guess = parse_release_name(name)
    if guess.kind == "movie" and guess.year:
        return guess.title, guess.year
    if guess.kind == "unknown" and not re.search(r"[._]", name):
        return name, ""  # a plain title ('The Office (US)'): the parser would only take its brackets away
    return (guess.title or name), ""


def is_season_folder(name: str) -> bool:
    return bool(_SEASON_FOLDER.match((name or "").strip()))


def is_generic_name(name: str) -> bool:
    return (name or "").strip().casefold() in GENERIC_FOLDER_NAMES


def strip_country_tag(title: str) -> str:
    """'The Office (US)' -> 'The Office': what TMDB is searched for (the tag is only used to pick among the hits)."""
    return _SERIES_WORDS.sub("", title or "").strip()


# ---- reading a folder ------------------------------------------------------------------------------------------------

def _videos(folder: Path) -> list[Path]:
    try:
        return sorted((Path(e.path) for e in os.scandir(folder)
                       if e.is_file() and os.path.splitext(e.name)[1].lower() in SUPPORTED_EXTENSIONS
                       and not is_app_temp_name(e.name)), key=lambda p: p.name.casefold())
    except OSError:
        return []


def _subfolders(folder: Path) -> list[Path]:
    try:
        return sorted((Path(e.path) for e in os.scandir(folder) if e.is_dir() and not e.name.startswith(".")),
                      key=lambda p: p.name.casefold())
    except OSError:
        return []


def _episode_files(folder: Path) -> list[tuple[Path, Optional[int], Optional[int]]]:
    """(file, season, episode) of the episode-looking videos directly in `folder`."""
    found = []
    for video in _videos(folder):
        guess = parse_release_name(video.stem)
        if guess.kind == "tv":
            found.append((video, guess.season, guess.episode))
    return found


def read_series(folder: Path) -> LocalSeries:
    """The seasons of a series folder: episodes named S01E02 directly in it, and the ones in its Season folders (the
    season number from the file name, else from the folder's name)."""
    name, year = title_and_year(folder.name)
    series = LocalSeries(folder, name, year)

    def add(files, fallback_season):
        for video, season, episode in files:
            number = season if season is not None else fallback_season
            if number is None:
                continue
            series.seasons.setdefault(number, []).append(video.name)
            if episode is not None:
                series.episodes.setdefault(number, []).append(episode)
                date = _AIR_DATE.search(video.stem)
                if date:
                    series.dates.setdefault(number, {}).setdefault(episode, date.group(1))
                for extra in parse_release_name(video.stem).extra_episodes:
                    series.episodes[number].append(extra)

    add(_episode_files(folder), None)
    for child in _subfolders(folder):
        match = _SEASON_FOLDER.match(child.name.strip())
        if match:
            fallback = int(match.group(1)) if match.group(1) else 0
            add(_episode_files(child), fallback)
    for season in series.seasons:
        series.seasons[season].sort(key=str.casefold)
        series.episodes[season] = sorted(set(series.episodes.get(season, [])))
    return series


def read_movie(folder: Path) -> LocalMovie:
    title, year = title_and_year(folder.name)
    videos = _videos(folder)
    if not year and videos:  # 'Movie Name/Movie.Name.2010.1080p.mkv': the file says what the folder does not
        guess = parse_release_name(videos[0].stem)
        if guess.kind == "movie" and guess.year:
            year = guess.year
    return LocalMovie(folder, title, year, [v.name for v in videos])


def classify(folder: Path) -> str:
    """'series', 'movie' or '' (not a title folder) for one folder, from what is directly in it and in its Season
    folders. A Season folder itself is never a title ('')."""
    if is_season_folder(folder.name) or is_generic_name(folder.name):
        return ""
    if _episode_files(folder):
        return KIND_SERIES
    for child in _subfolders(folder):
        if is_season_folder(child.name) and _episode_files(child):
            return KIND_SERIES
    videos = _videos(folder)
    if 1 <= len(videos) <= MAX_MOVIE_FILES:
        return KIND_MOVIE
    return ""


# ---- the scan --------------------------------------------------------------------------------------------------------

def _resolve_roots(paths: Iterable[str | os.PathLike]) -> tuple[list[Path], list[str]]:
    roots: list[Path] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = Path(raw)
        if path.is_file():
            path = path.parent  # a chosen file stands for its folder
        if not path.is_dir():
            missing.append(str(raw))
            continue
        if is_season_folder(path.name) and not is_generic_name(path.parent.name) and _episode_files(path):
            path = path.parent  # a Season folder stands for its series
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            roots.append(path)
    return roots, missing


def scan(
    paths: Iterable[str | os.PathLike], include_subfolders: bool = True, kinds: Iterable[str] = ALL_KINDS,
    include_outdated: bool = False,
) -> ScanResult:
    """The title folders under `paths` (folders, or files standing for their folder) that have no note yet.

    A folder with an artist.md / author.md / series.md / album.md / about.md of ANY letter case is skipped (`skipped`, with
    the file's name): Pluteus shows the first of those, so a new one would hide the person's own. With
    `include_subfolders` False only the named folders themselves are looked at. `include_outdated` (default OFF): a folder
    whose existing note is OUR note of the right kind in the state 'outdated-untouched' or 'legacy' becomes a target with
    `update` set, to be refreshed on request; 'edited', 'foreign' and 'current' notes are never targets."""
    from core.notes_render import CURRENT_NOTE_FORMAT

    wanted = set(kinds)
    result = ScanResult()
    roots, result.missing = _resolve_roots(paths)
    seen: set[str] = set()

    def consider(folder: Path, kind: str) -> None:
        key = os.path.normcase(str(folder))
        if key in seen or kind not in wanted:
            return
        seen.add(key)
        found = existing_note(folder)
        if found:
            if include_outdated and Path(found).name.lower() == f"{NOTE_KIND_OF[kind]}.md":
                state = note_status(found, CURRENT_NOTE_FORMAT)
                if state.replaceable:
                    result.targets.append(Target(folder, kind, update=state))
                    return
                result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name} ({state.kind})", state.kind))
                return
            result.skipped.append(Skipped(folder, kind, f"already has {Path(found).name}"))
            return
        result.targets.append(Target(folder, kind))

    def walk(folder: Path, is_root: bool) -> None:
        kind = classify(folder)
        if kind:
            consider(folder, kind)
            return  # a title folder holds its own seasons / parts: nothing below it is a title of its own
        if include_subfolders:
            for child in _subfolders(folder):
                walk(child, False)

    for root in roots:
        walk(root, True)
    return result


def count_targets(folder: str, include_subfolders: bool = True) -> tuple[int, int]:
    """(series, movies) the scan would find under `folder` (the scope dialog's counts; no note is looked at)."""
    series = movies = 0

    def walk(path: Path, is_root: bool) -> None:
        nonlocal series, movies
        kind = classify(path)
        if kind == KIND_SERIES:
            series += 1
        elif kind == KIND_MOVIE:
            movies += 1
        elif include_subfolders:
            for child in _subfolders(path):
                walk(child, False)

    root = Path(folder)
    if root.is_dir():
        walk(root, True)
    return series, movies
