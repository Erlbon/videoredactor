"""
gui/lyrics_flow.py

Metadata > Look Up > Get Lyrics (LRCLIB): the whole flow for the ONE selected music video of a MainWindow.

  1. the Get Lyrics dialog (gui/lyrics_dialog.py): search LRCLIB, pick an entry, choose what to save;
  2. the lyrics are staged on the file's `lyrics` field through the same per-field overwrite review as every other
     lookup (a lyrics value the file already has is replaced only when its box is still ticked there); Undo takes
     them back and Save All writes them inside the file (MP4 iTunes lyrics atom, MKV LYRICS tag);
  3. with "Also save the timed lyrics as a subtitle file (.srt)" ticked, one sidecar <stem>.<lang>.srt is written
     through the usual sidecar path (an existing file is replaced only when the user says so).

Cancel anywhere (the dialog, the review) changes nothing.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtWidgets import QMessageBox
from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts

from core.filename_pattern import set_field_text
from core.video_metadata import ContentType
from gui.lyrics_dialog import LyricsDialog
from gui.tag_panel import FIELD_LABELS

ONLY_MUSIC_VIDEO = "Get Lyrics works on one Music Video: select exactly one file whose Content Type is Music Video."


def lyrics_target(window):
    """The one selected, loaded Music Video, or None (the menu entry is hidden in every other case)."""
    files = window._selected_video_files()
    if len(files) != 1 or files[0].load_error:
        return None
    return files[0] if files[0].metadata.content_type is ContentType.MUSIC_VIDEO else None


def run_get_lyrics(window, dialog_class=LyricsDialog, search=None) -> None:
    vf = lyrics_target(window)
    if vf is None:
        window.status_bar.showMessage(ONLY_MUSIC_VIDEO)
        return
    dialog = dialog_class(vf, parent=window, search=search) if search is not None else dialog_class(vf, parent=window)
    if not dialog.exec():
        window.status_bar.showMessage("Get Lyrics: nothing chosen, nothing changed")
        return

    text, srt = dialog.lyrics_text, dialog.srt_text
    staged = False
    if text:
        changes = {0: {"lyrics": text}}
        accepted = resolve_overwrite_conflicts(window, [vf], changes, lambda attr: FIELD_LABELS.get(attr, attr))
        if accepted is None:
            window.status_bar.showMessage("Get Lyrics: review cancelled, nothing changed")
            return
        values = accepted.get(0, {})
        if "lyrics" in values:
            window._push_undo("Get Lyrics", [vf])
            if set_field_text(vf.metadata, "lyrics", values["lyrics"]):
                vf.dirty = True
                staged = True

    parts: list[str] = []
    if staged:
        parts.append("lyrics from LRCLIB set, not yet saved to disk")
    elif text:
        parts.append("kept the lyrics the file already has")
    if srt:
        saved = _save_srt(window, vf, srt, dialog.srt_language)
        if saved:
            parts.append(f"saved the timed lyrics to {saved}")
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    window.status_bar.showMessage("Get Lyrics: " + ("; ".join(parts) if parts else "nothing changed"))


def _save_srt(window, vf, srt: str, language: str) -> Optional[str]:
    status, detail = window._save_sidecar(
        lambda overwrite: vf.save_subtitle_sidecar(srt, language=language, overwrite=overwrite), {}
    )
    if status == "error":
        QMessageBox.warning(window, "Subtitle Not Saved", f"Couldn't write the subtitle file: {detail}")
        return None
    if status == "kept":
        return None
    return detail.name
