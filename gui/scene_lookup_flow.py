"""
gui/scene_lookup_flow.py

Metadata > Look Up > ThePornDB... / StashDB...: for each selected file the search dialog (gui/scene_search_dialog.py),
then ONE per-field overwrite review for the whole batch (redactor_common's resolve_overwrite_conflicts), and only then
anything is written: the fields (staged, not saved to disk until Save), and the cover as a sidecar only for a file
whose result was applied with "Save the cover as a sidecar" ticked. Cancel anywhere changes nothing.

The field mapping is core/scene_lookup.scene_fields(); this module only drives the windows.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QMessageBox
from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts

from core.filename_pattern import set_field_text
from core.scene_lookup import CONTENT_TYPE, SceneLookupError, SceneResult, scene_fields
from core.video_metadata import ContentType
from gui.lookup import CANCELLED, run_cancellable_lookup
from gui.scene_search_dialog import SOURCE_FACTORIES, SceneSearchDialog, SceneSource
from gui.tag_panel import FIELD_LABELS

_JPEG_MAGIC = b"\xff\xd8\xff"


def as_jpeg(data: bytes) -> bytes:
    """Cover bytes as a JPEG (the poster sidecar is -poster.jpg): a JPEG is kept as it is, anything else the app can
    read (PNG, WebP) is converted; SceneLookupError for bytes that are not a picture."""
    if data[:3] == _JPEG_MAGIC:
        return data
    image = QImage()
    if not image.loadFromData(data):
        raise SceneLookupError("The cover is not a picture this app can read.")
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.convertToFormat(QImage.Format.Format_RGB32).save(buffer, "JPG", 92):
        raise SceneLookupError("The cover could not be converted to a JPEG.")
    return bytes(buffer.data())


def fields_for(vf, result: SceneResult) -> dict[str, str]:
    """What applying `result` to `vf` would set: the mapped fields, plus Content Type = Movie when it is not set."""
    fields = scene_fields(result, vf.metadata.comment)
    if vf.metadata.content_type is ContentType.UNSET:
        fields["content_type"] = CONTENT_TYPE.value
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

    picks: dict[int, tuple[SceneResult, bool]] = {}
    for index, vf in enumerate(files):
        dialog = dialog_class(source, vf, parent=window, open_api_keys=window._on_add_external_apis)
        if dialog.exec() and dialog.selected_result is not None:
            picks[index] = (dialog.selected_result, dialog.save_cover)
        elif not source.has_key():
            break  # no key and the dialog was closed: asking again for every other file would only repeat that
    if not picks:
        window.status_bar.showMessage(f"{source.name}: nothing chosen, nothing changed")
        return

    changes = {index: fields_for(files[index], result) for index, (result, _cover) in picks.items()}
    changes = {index: fields for index, fields in changes.items() if fields}
    accepted = resolve_overwrite_conflicts(window, files, changes, lambda attr: FIELD_LABELS.get(attr, attr))
    if accepted is None:
        window.status_bar.showMessage(f"{source.name}: review cancelled, nothing changed")
        return

    touched = [files[index] for index in accepted if accepted[index]]
    window._push_undo(f"{source.name} Lookup", touched)
    applied = 0
    for index, values in accepted.items():
        vf = files[index]
        wrote = False
        for attr, value in values.items():
            wrote = set_field_text(vf.metadata, attr, value) or wrote
        if wrote:
            vf.dirty = True
            applied += 1

    covers = _save_covers(window, source, files, picks)
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    parts = [f"{source.name}: {applied} file(s) filled, not yet saved to disk"]
    if covers:
        parts.append(covers)
    window.status_bar.showMessage("; ".join(parts))


def _save_covers(window, source: SceneSource, files: list, picks: dict) -> str:
    """The chosen covers, downloaded one at a time (a progress dialog with Cancel each) and written as sidecars."""
    saved, problems = 0, []
    overwrite_choice: dict = {}
    for index, (result, wants_cover) in picks.items():
        if not (wants_cover and result.cover_url):
            continue
        vf = files[index]
        try:
            data = run_cancellable_lookup(window, source.download_cover, result.cover_url,
                                          label=f"Downloading the cover for {vf.path.name}...")
            if data is CANCELLED:
                problems.append("cover download cancelled")
                break
            data = as_jpeg(data)
        except (SceneLookupError, *source.errors) as exc:
            problems.append(f"{vf.path.name}: cover not saved ({exc})")
            continue
        status, detail = window._save_sidecar(lambda overwrite, v=vf, d=data: v.save_poster_sidecar(d, overwrite),
                                              overwrite_choice)
        if status == "saved":
            saved += 1
        elif status == "error":
            problems.append(f"{vf.path.name}: cover not saved ({detail})")
    parts = []
    if saved:
        parts.append(f"{saved} cover(s) saved")
    parts += problems
    return "; ".join(parts)
