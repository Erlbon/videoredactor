"""
gui/notes_dialog.py

Tools > Create Notes...: the questions and the run for the folder notes (core/notes_flow.py is the engine). The question
"which folder, and what to create?" is redactor_common's NotesScopeDialog, the progress and the review page are
redactor_common's run_create_notes / NotesReviewDialog; this module only says what is video's own: which folders a choice
stands for, what a folder would get, how long a scope takes, and the TMDB key check.

  scope_question(parent, ...)     the 'which folder?' dialog with the video words; returns (folder, kinds, outdated)
  create_notes(parent, paths, ...)  scan, look up (progress + Cancel), open the review page
  title_folders(files)            the series or film folder each selected file belongs to
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Iterable, Optional

from PyQt6.QtWidgets import QMessageBox, QWidget

from core import notes_settings
from core.notes_flow import Fetchers, NotesEngine, build_proposals, live_fetchers, scan, write_proposal
from core.notes_local import (
    ALL_KINDS, KIND_MOVIE, KIND_SERIES, classify, count_targets, is_season_folder, title_and_year,
)
from core.tmdb_client import get_api_key
from redactor_common.core.notes import existing_note
from redactor_common.core.notes_scope import ScopeChoice, is_root, merge_choices, opened_caption, opened_folder
from redactor_common.core.notes_timing import format_duration
from redactor_common.gui.notes_review_dialog import NotesReviewDialog, open_in_system
from redactor_common.gui.notes_run import run_create_notes
from redactor_common.gui.notes_scope_dialog import ask_scope

TITLE = "Create Notes"
KIND_CHECKS = (
    (KIND_SERIES, "Series notes (series.md in a series folder)"),
    (KIND_MOVIE, "Film notes (about.md in a film folder)"),
)
SECONDS_PER_TITLE = 1.7  # measured shape: 2 TMDB requests (~0.3 s) and 3 Wikipedia requests (kept to one per 0.35 s)
HINT_MIN_FOLDERS = 100  # the scope dialog warns above this many folders
TMDB_KEY_MESSAGE = ("Create Notes looks the titles up at TMDB, which needs your own (free) API key.\n\n"
                    "Add it under Tools > API Keys, then try again.")


# ---- what a folder is -------------------------------------------------------------------------------------------------

def title_folders(files: Iterable[str | Path]) -> list[Path]:
    """The series or film folder each file belongs to (a file in a Season folder belongs to the series above it), each
    once, in order."""
    found: list[Path] = []
    for file in files:
        folder = Path(file).parent
        if is_season_folder(folder.name):
            folder = folder.parent
        if folder not in found:
            found.append(folder)
    return found


def scope_candidates(loaded: Iterable[str], selected: Iterable[str], last_directory: str, notes_last_folder: str):
    """The ordered (path, caption, default) candidates of the question: the folder you opened, the series / film folder of
    the selected files (ticked by default), one level up, the folder used last time."""
    loaded, selected = [str(p) for p in loaded], [str(p) for p in selected]
    opened, count = opened_folder(loaded, last_directory)
    candidates = [(opened, opened_caption(count), False)]
    for index, folder in enumerate(title_folders(selected)[:2]):
        candidates.append((str(folder), "the series or film of the selected files", index == 0))
    if opened and not is_root(opened):
        above = str(Path(opened).parent)
        if not is_root(above):
            candidates.append((above, "one level up: also the other folders next to it", False))
    candidates.append((notes_last_folder, "the folder you used last time", False))
    return candidates


def folder_info(path: str) -> tuple[str, str, tuple[int, int]]:
    """(kind, existing note, (series, films) below) of a folder you might choose: what it is to the scan ('series', 'movie'
    or '' for a folder that holds titles or nothing), the name of the note it already has, and how many titles are below
    it when it is none itself. Only the folder structure is read."""
    folder = Path(path)
    kind = classify(folder)
    note = existing_note(folder)
    return kind, Path(note).name if note else "", (count_targets(path, True) if not kind else (0, 0))


def folder_caption(info: tuple[str, str, tuple[int, int]], folder_name: str, kinds: str) -> str:
    """What the folder would get, in a few words: 'would get a series note: Dark'."""
    kind, existing, below = info
    wanted = notes_settings.parse_kinds(kinds)
    if not kind:
        series, movies = below
        if series or movies:
            return f"a library folder: the {series} series and {movies} film folders below get their own notes"
        return "nothing to write a note for here (no series or film folders)"
    noun = "a series note" if kind == KIND_SERIES else "a film note"
    if kind not in wanted:
        return f"a {'series' if kind == KIND_SERIES else 'film'} folder ({'series' if kind == KIND_SERIES else 'film'} notes are not ticked in What to create)"
    if existing:
        return f"already has {existing}: left alone"
    return f"would get {noun}: {title_and_year(folder_name)[0]}"


def count_for_hint(folder: str, include_subfolders: bool) -> tuple[int, int]:
    return count_targets(folder, include_subfolders)


def scope_hint(counts: Optional[tuple[int, int]], kinds: str) -> str:
    """The one-line time hint of a big scope (only a hint, never blocking); '' when there is nothing to warn about."""
    if not counts:
        return ""
    series, movies = counts
    wanted = notes_settings.parse_kinds(kinds)
    folders = (series if KIND_SERIES in wanted else 0) + (movies if KIND_MOVIE in wanted else 0)
    if folders <= HINT_MIN_FOLDERS:
        return ""
    return (f"About {folders} folders: roughly {format_duration(folders * SECONDS_PER_TITLE)} "
            "(a rough guess: a title that is not found takes longer). Answers are kept, so a second run is quick.")


def kinds_sentence(kinds: str) -> str:
    wanted = notes_settings.parse_kinds(kinds)
    if wanted == (KIND_SERIES,):
        return "Looked up: series notes only."
    if wanted == (KIND_MOVIE,):
        return "Looked up: film notes only."
    return "Looked up: series and film notes."


def left_alone_sentence(found) -> str:
    """'N notes were left alone: edited by a person or not ours.' (and how many are already current)."""
    left = sum(1 for x in found.skipped if x.state in ("edited", "foreign"))
    current = sum(1 for x in found.skipped if x.state == "current")
    return (f"{left} notes were left alone: edited by a person or not ours." + (f" {current} are already current." if current else ""))


# ---- the question and the run -----------------------------------------------------------------------------------------

def scope_question(
    parent: QWidget, choices: list[ScopeChoice], include_subfolders: bool,
    on_include_changed: Optional[Callable[[bool], None]] = None, start: str = "", kinds: str = "",
) -> tuple[str, str, bool]:
    """(folder, kinds, include_outdated); folder is "" when the person cancelled."""
    return ask_scope(
        parent, choices, include_subfolders, kind_checks=KIND_CHECKS, kinds=kinds or notes_settings.kinds_text(ALL_KINDS),
        parse_kinds=notes_settings.parse_kinds, kinds_text=notes_settings.kinds_text, describe=folder_info,
        caption_of=folder_caption, count=count_for_hint, hint=scope_hint, on_include_changed=on_include_changed, start=start,
        title="Create Notes: which folder?", heading="Create notes: which folder?",
        browse_title="Create Notes: choose a library, series or film folder")


def create_notes(
    parent: QWidget, paths: list, include_subfolders: bool, make_fetchers: Callable[[], Fetchers] = live_fetchers,
    size_text: str = "", on_size: Optional[Callable[[str], None]] = None, open_path: Callable[[str], None] = open_in_system,
    kinds: tuple = ALL_KINDS, cache=None, include_outdated: bool = False,
) -> Optional[NotesReviewDialog]:
    """Scan `paths`, look every folder up under a cancellable progress dialog, then open the review page. Returns the
    (closed) dialog, or None when there was nothing to review."""
    from core import history

    fetchers = make_fetchers()
    fetchers.wikipedia = fetchers.wikipedia and notes_settings.wikipedia_enabled()
    if fetchers.key is None and not get_api_key():
        QMessageBox.information(parent, TITLE, TMDB_KEY_MESSAGE)
        return None
    engine = NotesEngine(fetchers, cache=cache)
    return run_create_notes(
        parent, title=TITLE, scan=lambda: scan(paths, include_subfolders, kinds=kinds, include_outdated=include_outdated),
        engine=engine, build=build_proposals, write_fn=write_proposal,
        label_for=lambda target: f"{'series' if target.kind == KIND_SERIES else 'film'}: {target.folder.name}",
        nothing_found="No series or film folders without a note were found.", intro_notes=[kinds_sentence(",".join(kinds))],
        owner_kinds=(KIND_SERIES, KIND_MOVIE), size_text=size_text, on_size=on_size, open_path=open_path,
        include_outdated=include_outdated, left_alone=left_alone_sentence,
        history_note="Recorded in Tools > History (it is switched on)." if history.history_enabled() else "")
