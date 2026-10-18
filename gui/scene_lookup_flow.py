"""
gui/scene_lookup_flow.py

Metadata > Look Up > ThePornDB... / StashDB...: for each selected file the search dialog (gui/scene_search_dialog.py),
then ONE per-field overwrite review for the whole batch (redactor_common's resolve_overwrite_conflicts), and only then
anything is staged: the fields, the source ids (THEPORNDB_ID / STASHDB_ID, SOURCE_URL, METADATA_MATCH) and, for a file
whose result was applied with "Save the poster in the file" ticked, the poster. All of it is staged on the file and
written by Save, inside the file; no sidecar file is created. Cancel anywhere changes nothing.

The field mapping is core/scene_lookup.scene_fields(); this module only drives the windows.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtWidgets import QMessageBox
from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts

from core.filename_pattern import set_field_text
from core.lookup_sources import scene_values
from core import studios
from core.controlled_vocab import remember_porn_tags
from core.scene_lookup import CONTENT_TYPE, SceneLookupError, SceneResult, guess_search, scene_fields
from core.video_metadata import ContentType
from gui.scene_search_dialog import SOURCE_FACTORIES, SceneSearchDialog, SceneSource
from gui.lookup_file_header import file_context
from gui.source_apply import SourceStage, apply_stage, fetch_prepared_poster, review_label
from gui.tag_panel import FIELD_LABELS


def fields_for(vf, result: SceneResult) -> dict[str, str]:
    """What applying `result` to `vf` would set: the mapped fields, plus Content Type = Porn when it is not set."""
    fields = scene_fields(result, vf.metadata.comment)
    if vf.metadata.content_type is ContentType.UNSET:
        fields["content_type"] = CONTENT_TYPE.value
    # The Scene # (episode number) comes from the file name (s02, Scene 3) when the file has none: the services do not
    # reliably number their scenes.
    if vf.metadata.episode_number is None:
        number = guess_search(vf.path, vf.metadata).scene_number
        if number:
            fields["episode_number"] = str(number)
    return fields


def run_scene_lookup(window, key: str, source_factory: Optional[Callable[[], SceneSource]] = None,
                     dialog_class=SceneSearchDialog) -> None:
    """The whole flow for the selected files of `window` (a MainWindow); `key` is "theporndb" or "stashdb"."""
    selected = window._selected_video_files()
    if not selected:
        QMessageBox.information(window, "No Files Selected", "Select at least one file first.")
        return
    files = [vf for vf in selected if not vf.load_error]
    if not files:
        QMessageBox.warning(window, "Cannot Look Up", "All selected files failed to load -- fix that first.")
        return
    source = (source_factory or SOURCE_FACTORIES[key])()

    picks: dict[int, tuple[SceneResult, bool, bool]] = {}
    for index, vf in enumerate(files):
        dialog = dialog_class(source, vf, parent=window, open_api_keys=window._on_add_external_apis,
                              context=file_context(vf, index + 1, len(files)))
        accepted = dialog.exec()
        if getattr(dialog, "stop_requested", False):
            break  # Stop Here: this file and the rest are not touched (the picks so far still go to the review)
        if accepted and dialog.selected_result is not None:
            picks[index] = (dialog.selected_result, dialog.save_cover, dialog.accept_landscape)
        elif not source.has_key():
            break  # no key and the dialog was closed: asking again for every other file would only repeat that
    if not picks:
        window.status_bar.showMessage(f"{source.name}: nothing chosen, nothing changed")
        return

    # The posters are fetched only for a result applied with the box ticked, before the review so that it can show
    # what the new poster is. A picture that is not a true poster is not embedded (the status line says so).
    stages: dict[int, SourceStage] = {}
    problems: list[str] = []
    for index, (result, wants_poster, landscape) in picks.items():
        vf = files[index]
        stage = SourceStage(values=scene_values(result))
        if wants_poster:
            poster, why = fetch_prepared_poster(
                window, source.name, source.download_cover, result.poster_urls, accept_landscape=landscape,
                label=f"Downloading the poster for {vf.path.name}...", errors=(SceneLookupError, *source.errors),
            )
            stage.poster, stage.note = poster, why
            if why:
                problems.append(f"{vf.path.name}: {why}")
        stages[index] = stage

    changes = {index: {**fields_for(files[index], result), **stages[index].rows()} for index, (result, _p, _l) in picks.items()}
    changes = {index: fields for index, fields in changes.items() if fields}
    accepted = resolve_overwrite_conflicts(window, files, changes, lambda attr: review_label(attr, lambda a: FIELD_LABELS.get(a, a)))
    if accepted is None:
        window.status_bar.showMessage(f"{source.name}: review cancelled, nothing changed")
        return

    touched = [files[index] for index in accepted if accepted[index]]
    window._push_undo(f"{source.name} Lookup", touched)
    applied = posters = 0
    for index, values in accepted.items():
        vf = files[index]
        wrote = apply_stage(vf, stages[index], values)
        posters += int("cover_info" in values and stages[index].poster is not None)
        for attr, value in values.items():
            if attr in stages[index].rows():
                continue  # a source id or the poster: staged by apply_stage
            wrote = set_field_text(vf.metadata, attr, value) or wrote
        if wrote:
            vf.dirty = True
            applied += 1
        # What the lookup taught the app: its tags join the Porn tag list, its studio gets a website (the user's own
        # entry always wins).
        result = picks[index][0]
        if "genre_tags" in values:
            remember_porn_tags(values["genre_tags"])
        if "studio" in values:
            studios.remember_from_lookup(values["studio"], list(result.urls))

    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    parts = [f"{source.name}: {applied} file(s) filled, not yet saved to disk"]
    if posters:
        parts.append(f"{posters} poster(s) will be embedded on Save")
    parts += problems
    window.status_bar.showMessage("; ".join(parts))
