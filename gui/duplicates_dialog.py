"""
gui/duplicates_dialog.py

Media > Find Duplicates... (core/video_duplicates.py): hashes one
frame of every loaded file that has a same-length neighbour, groups the
files whose pictures match, and hands the groups to redactor_common's
shared review dialog (gui/duplicates_dialog.py there). This module is
only the video side of it: the threshold prompt, the finding (on the
worker thread, under the shared cancellable progress dialog), the tier
and reason of each group, and what the app does afterwards (drop
trashed files from the list, select the picked ones).

Review only: nothing is changed unless the user picks an action in the
shared dialog. Per the family policy duplicates are not errors, so the
dialog selects nothing and the user can mark a group "Not duplicates";
that is remembered in videoredactor_duplicates_dismissed.json by the
members' video fingerprints (core/video_fingerprint.py), which survive a
tag edit, a rename or a move. The frame hashes themselves live only for
this run.
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import QInputDialog, QMessageBox
from redactor_common.core.duplicates import (
    TIER_IDENTICAL,
    TIER_POSSIBLE,
    TIER_STRONG,
    DuplicateGroup,
    DuplicateMember,
    JsonDismissStore,
)
from redactor_common.core.scan_stamp import content_fingerprint
from redactor_common.gui.duplicates_dialog import run_find_duplicates

from core.app_paths import base_dir
from core.config import get_setting, set_setting
from core.format_helpers import format_duration, format_file_size
from core.video_duplicates import (
    DURATION_TOLERANCE,
    HAMMING_THRESHOLD,
    Candidate,
    candidates_needing_hash,
    frame_hash,
    group_duplicates,
    hamming,
)
from core.video_file import VideoFile
from core.video_fingerprint import video_fingerprint

TITLE = "Find Duplicates"
DISMISSED_FILE = "videoredactor_duplicates_dismissed.json"
COLUMNS = [
    ("name", "File"), ("folder", "Folder"), ("duration", "Duration"),
    ("resolution", "Resolution"), ("size", "Size"), ("codec", "Video codec"),
]
INTRO_TEXT = "Files in a group have nearly the same length and a matching picture."


def dismiss_store() -> JsonDismissStore:
    """The "not duplicates" decisions, next to the app's other settings."""
    return JsonDismissStore(str(base_dir() / DISMISSED_FILE))


def _member(vf: VideoFile, fingerprint: str) -> DuplicateMember:
    meta = vf.metadata
    return DuplicateMember(
        vf, str(vf.path),
        {
            "name": vf.path.name, "folder": str(vf.path.parent),
            "duration": format_duration(meta.duration_seconds), "resolution": meta.resolution,
            "size": format_file_size(vf.size_bytes), "codec": meta.video_codec,
        },
        fingerprint,
    )


def _max_distance(group: list[Candidate]) -> int:
    """The largest frame-hash distance between any two members (a chained
    group can have far-apart ends)."""
    hashes = [c.hash for c in group if c.hash is not None]
    return max((hamming(a, b) for i, a in enumerate(hashes) for b in hashes[i + 1:]), default=0)


def tier_for(group: list[Candidate], identical: bool) -> tuple[str, str]:
    """(tier, reason) of a frame-hash group. Byte-identical files are
    Identical; every member on the very same frame hash is a Strong
    match; any looser match (up to the user's threshold) is Possible."""
    window = f"lengths within {DURATION_TOLERANCE:g} s"
    if identical:
        return TIER_IDENTICAL, "same file contents"
    distance = _max_distance(group)
    if distance == 0:
        return TIER_STRONG, f"matching frame hash, {window}"
    return TIER_POSSIBLE, f"similar frame hash (up to {distance} of 64 bits apart), {window}"


def find_groups(
    candidates: list[Candidate],
    threshold: int,
    progress: Callable[..., None],
    cancelled: Callable[[], bool],
    unreadable: list[str],
) -> list[DuplicateGroup]:
    """The shared dialog's find_fn body, on the worker thread: hash a
    frame of every file with a same-length neighbour, group by hash, then
    fingerprint the members of the groups found (their dismissal identity)
    and check each group for byte-identical files. Unreadable files are
    named in `unreadable`; [] when cancelled."""
    needed = candidates_needing_hash(candidates)
    total = len(needed)
    for done, candidate in enumerate(needed):
        if cancelled():
            return []
        vf: VideoFile = candidate.key  # type: ignore[assignment]
        progress(done, total, f"Reading a frame of {vf.path.name}")
        try:
            candidate.hash = frame_hash(str(vf.path), candidate.duration)
        except Exception:  # an unreadable file just drops out of the comparison
            candidate.hash = None
        if candidate.hash is None:
            unreadable.append(vf.path.name)

    found = group_duplicates(candidates, threshold)
    total += sum(len(g) for g in found)
    done = len(needed)
    groups: list[DuplicateGroup] = []
    for group in found:
        members = []
        contents = set()
        for candidate in group:
            if cancelled():
                return []
            vf = candidate.key  # type: ignore[assignment]
            progress(done, total, f"Fingerprinting {vf.path.name}")
            done += 1
            members.append(_member(vf, video_fingerprint(str(vf.path))))
            contents.add(content_fingerprint(str(vf.path)))
        identical = len(contents) == 1 and "" not in contents
        tier, reason = tier_for(group, identical)
        groups.append(DuplicateGroup(f"frame:{members[0].path}", tier, reason, members))
    return groups


def find_duplicates_flow(window, files: list[VideoFile]) -> None:
    """The whole Media > Find Duplicates... flow. `window` is the main
    window (status bar, file list, row refresh, selection)."""
    threshold, ok = QInputDialog.getInt(
        window, TITLE,
        "Two videos match when their picture hashes differ in at most this many of 64 bits\n"
        "(lower = stricter; 6 is a good start):",
        int(get_setting("duplicates", "threshold", str(HAMMING_THRESHOLD)) or HAMMING_THRESHOLD), 0, 32,
    )
    if not ok:
        return
    set_setting("duplicates", "threshold", str(threshold))

    candidates = [Candidate(vf, vf.metadata.duration_seconds) for vf in files if not vf.load_error]
    needed = candidates_needing_hash(candidates)
    if not needed:
        QMessageBox.information(window, TITLE, "No two loaded files have the same length, so there are no duplicates to find.")
        return

    unreadable: list[str] = []
    state = {"cancelled": False, "finished": False}

    def find_fn(_items, progress, cancelled):
        groups = find_groups(candidates, threshold, progress, cancelled, unreadable)
        state["cancelled"] = cancelled()
        state["finished"] = True
        return groups

    def on_trashed(trashed: list) -> None:
        gone = {id(vf) for vf in trashed}
        window.video_files = [vf for vf in window.video_files if id(vf) not in gone]
        window._refresh_table_rows()
        window.status_bar.showMessage(f"Moved {len(trashed)} file(s) to the Recycle Bin")

    def on_select_in_list(picked: list) -> None:
        # The dialog already leaves out files it moved to the Recycle Bin.
        window._reselect_files([id(vf) for vf in picked])

    dialog = run_find_duplicates(
        window, [c.key for c in needed], find_fn, COLUMNS,
        title=TITLE, dismiss_store=dismiss_store(), on_select_in_list=on_select_in_list,
        on_trashed=on_trashed, intro_text=INTRO_TEXT, progress_label="Comparing videos...",
        none_found_message="No duplicates found.",
    )
    note = f" ({len(unreadable)} file(s) couldn't be read)" if unreadable else ""
    if dialog is None:
        if state["cancelled"]:
            window.status_bar.showMessage("Find Duplicates cancelled")
        elif state["finished"]:
            window.status_bar.showMessage(f"No duplicates found among {len(needed)} file(s) of matching length{note}")
        return
    if not dialog.trashed:
        window.status_bar.showMessage(f"Reviewed groups of probable duplicates{note}")
