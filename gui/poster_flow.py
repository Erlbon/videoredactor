"""
gui/poster_flow.py

Metadata > Get Poster (and right-click > Get Poster): fetches the TRUE POSTER (core/poster.py) for the selected files from
the source ids they already carry (core/poster_fetch.py: TMDB / TheTVDB / IMDb for films and TV, ThePornDB / StashDB for
scenes), without searching. The posters are staged on the files like every lookup's (Save embeds them, Undo takes them
back); a file that already has a poster is left alone unless "Replace" is ticked, and then the usual per-field review
asks, with the new poster unticked.
"""

from __future__ import annotations

import threading
from typing import Optional

from PyQt6.QtWidgets import QCheckBox, QMessageBox
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background
from redactor_common.gui.progress import run_with_progress

from core import poster_fetch
from gui.source_apply import SourceStage, apply_sources

TITLE = "Get Poster"


def has_poster(vf) -> bool:
    return bool(vf.metadata.cover_info) or vf.metadata.cover_pending is not None


def plan_files(files: list, replace: bool) -> tuple[list, list[str]]:
    """(the files a poster is fetched for, one report line per file that is skipped and why)."""
    todo, lines = [], []
    for vf in files:
        name = vf.path.name
        if vf.load_error:
            lines.append(f"{name}: skipped, it failed to load")
        elif not poster_fetch.services_for(vf.metadata):
            lines.append(f"{name}: skipped, it carries no source id (run Metadata > Look Up first)")
        elif has_poster(vf) and not replace:
            lines.append(f"{name}: skipped, it already has a poster (tick Replace to fetch another)")
        else:
            todo.append(vf)
    return todo, lines


def ask_options(window, count: int) -> Optional[tuple[bool, bool]]:
    """(replace existing posters, accept a landscape cover) or None on Cancel."""
    box = QMessageBox(window)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(TITLE)
    box.setText(
        f"Get the poster for {count} file(s) from the source ids they carry?\n\nOnly a true poster is used (the "
        "service's own poster, portrait, big enough); never a frame from the video. Nothing is written to the files "
        "until you save, and Undo takes it back."
    )
    replace = QCheckBox("Replace a poster the file already has (asks per file)")
    landscape = QCheckBox("Accept a landscape cover when the record has no poster")
    layout = box.layout()
    layout.addWidget(replace, layout.rowCount(), 0, 1, layout.columnCount())
    layout.addWidget(landscape, layout.rowCount(), 0, 1, layout.columnCount())
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    if box.exec() != QMessageBox.StandardButton.Ok:
        return None
    return replace.isChecked(), landscape.isChecked()


def fetch_posters(window, files: list, accept_landscape: bool):
    """Downloads a poster per file under a cancellable progress dialog; returns ({index: outcome}, cancelled)."""
    cancel = threading.Event()
    outcomes: dict[int, poster_fetch.PosterOutcome] = {}
    cancelled = False

    def step(item, _index: int) -> None:
        nonlocal cancelled
        position, vf = item
        if cancel.is_set():
            cancelled = True
            return
        try:
            outcomes[position] = call_in_background(
                poster_fetch.fetch_poster, vf.metadata, accept_landscape=accept_landscape, cancel_event=cancel)
        except BackgroundCancelled:
            cancelled = True

    finished = run_with_progress(
        window, list(enumerate(files)), step, "Getting posters...", threshold=1, cancellable=True,
        label_for=lambda item: f"Getting the poster for {item[1].path.name}", on_cancel=cancel.set,
    )
    return outcomes, cancelled or not finished


def run_get_poster(window, files: list, options: Optional[tuple[bool, bool]] = None) -> None:
    """The whole flow for `files` (the selected ones). `options` (replace, landscape) skip the question (tests, a
    caller that already asked)."""
    if not files:
        QMessageBox.information(window, TITLE, "Select the files you want a poster for.")
        return
    candidates, _lines = plan_files(files, replace=True)
    if not candidates:
        QMessageBox.information(
            window, TITLE,
            "None of the selected files carries a source id (TMDB, TheTVDB, IMDb, ThePornDB or StashDB), so there is "
            "no record to take a poster from. Run Metadata > Look Up first.")
        return
    if options is None:
        options = ask_options(window, len(candidates))
        if options is None:
            return
    replace, landscape = options
    todo, lines = plan_files(files, replace)
    if not todo:
        window.status_bar.showMessage("Get Poster: nothing to do")
        QMessageBox.information(window, TITLE, "\n".join(lines))
        return
    outcomes, cancelled = fetch_posters(window, todo, landscape)
    stages: dict[int, SourceStage] = {}
    for position, vf in enumerate(todo):
        outcome = outcomes.get(position)
        if outcome is None:
            continue
        if outcome.found:
            stages[position] = SourceStage(poster=outcome.poster)
            lines.append(f"{vf.path.name}: poster from {outcome.service}, {outcome.poster.description()}")
        else:
            lines.append(f"{vf.path.name}: no poster ({outcome.why_not()})")
    changed = 0
    if stages:
        window._push_undo(TITLE, [todo[i] for i in stages])
        changed = apply_sources(window, todo, stages)
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    summary = f"Get Poster: {changed} poster(s) staged, not yet saved to disk"
    skipped = len(files) - changed
    if skipped:
        summary += f", {skipped} file(s) without a new poster"
    if cancelled:
        summary += ", cancelled"
    window.status_bar.showMessage(summary)
    box = QMessageBox(window)
    box.setIcon(QMessageBox.Icon.Information if changed else QMessageBox.Icon.Warning)
    box.setWindowTitle(TITLE)
    box.setText(summary + ".")
    box.setDetailedText("\n".join(lines))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()
