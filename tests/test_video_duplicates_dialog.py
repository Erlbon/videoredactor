"""Media > Find Duplicates... wired to redactor_common's shared review
dialog (gui/duplicates_dialog.py): the groups the app hands over with
their tier and reason, "Not duplicates" remembered across dialog
instances by the JSON store, trashed files dropped from the app's list,
and Select These in the List reselecting. Frame hashing and
fingerprinting are faked by file name, so nothing here needs ffmpeg and
the tests behave the same on Windows and Linux."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QStatusBar, QWidget  # noqa: E402
from redactor_common.core.duplicates import (  # noqa: E402
    TIER_IDENTICAL,
    TIER_POSSIBLE,
    TIER_STRONG,
    JsonDismissStore,
)
from redactor_common.gui import duplicates_dialog as shared  # noqa: E402

from core.video_duplicates import Candidate  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from gui import duplicates_dialog as dd  # noqa: E402

_app = QApplication.instance() or QApplication([])

# Frame hash per file name: a/b share one picture, c is 3 bits away, d is another picture.
HASHES = {"a.mp4": 0, "b.mp4": 0, "c.mp4": 0b111, "d.mp4": (1 << 64) - 1, "e.mp4": 0}


class Window(QWidget):
    def __init__(self, files):
        super().__init__()
        self.status_bar = QStatusBar(self)
        self.video_files = files
        self.refreshed = 0
        self.reselected = None

    def _refresh_table_rows(self):
        self.refreshed += 1

    def _reselect_files(self, ids):
        self.reselected = ids


def _files(directory, names, duration=4.0):
    files = []
    for name in names:
        path = directory / name
        path.write_bytes(name.encode())
        files.append(VideoFile(path=path, metadata=VideoMetadata(
            duration_seconds=duration, resolution="320x240", video_codec="h264")))
    return files


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Fake hashing/fingerprints by name, a JSON dismiss store in tmp_path,
    and the threshold prompt answered with 6."""
    monkeypatch.setattr(dd, "frame_hash", lambda path, duration: HASHES[os.path.basename(path)])
    monkeypatch.setattr(dd, "video_fingerprint", lambda path: "v-" + os.path.basename(path))
    monkeypatch.setattr(dd, "content_fingerprint", lambda path: "c-" + os.path.basename(path))
    store_path = tmp_path / "dismissed.json"
    monkeypatch.setattr(dd, "dismiss_store", lambda: JsonDismissStore(str(store_path)))
    monkeypatch.setattr(dd.QInputDialog, "getInt", staticmethod(lambda *a, **k: (6, True)))
    monkeypatch.setattr(dd, "get_setting", lambda *a, **k: "6")
    monkeypatch.setattr(dd, "set_setting", lambda *a, **k: None)
    return tmp_path, store_path


def _member_names(dialog):
    return [
        [dialog.tree.topLevelItem(g).child(c).text(0) for c in range(dialog.tree.topLevelItem(g).childCount())]
        for g in range(dialog.tree.topLevelItemCount())
    ]


def _run(monkeypatch, window, files, act):
    """Runs the flow with the shared dialog's exec() replaced by `act(dialog)`."""
    seen = {}

    def fake_exec(dialog):
        seen["dialog"] = dialog
        act(dialog)
        return 0

    monkeypatch.setattr(shared.DuplicatesDialog, "exec", fake_exec)
    dd.find_duplicates_flow(window, files)
    return seen.get("dialog")


# --- find_groups: tiers, reasons, fingerprints ----------------------------------

def test_tier_mapping():
    same = [Candidate("a", 4.0, 5), Candidate("b", 4.0, 5)]
    near = [Candidate("a", 4.0, 0), Candidate("b", 4.0, 0b111)]
    assert dd.tier_for(same, identical=False)[0] == TIER_STRONG
    assert dd.tier_for(same, identical=True) == (TIER_IDENTICAL, "same file contents")
    tier, reason = dd.tier_for(near, identical=False)
    assert tier == TIER_POSSIBLE and "up to 3 of 64 bits" in reason and "2 s" in reason


def test_groups_carry_tier_reason_fingerprints_and_progress(env):
    files = _files(env[0], ["a.mp4", "b.mp4", "c.mp4", "d.mp4"])
    candidates = [Candidate(vf, 4.0) for vf in files]
    calls, unreadable = [], []
    groups = dd.find_groups(candidates, 6, lambda *a: calls.append(a), lambda: False, unreadable)
    assert len(groups) == 1 and unreadable == []
    group = groups[0]
    assert [m.path for m in group.members] == [str(vf.path) for vf in files[:3]]
    assert [m.fingerprint for m in group.members] == ["v-a.mp4", "v-b.mp4", "v-c.mp4"]
    assert group.tier == TIER_POSSIBLE   # c is 3 bits off
    assert group.members[0].fields["name"] == "a.mp4" and group.members[0].fields["resolution"] == "320x240"
    assert any("Reading a frame of a.mp4" in c[2] for c in calls)
    assert calls[-1][0] <= calls[-1][1]


def test_identical_contents_make_an_identical_group(env, monkeypatch):
    monkeypatch.setattr(dd, "content_fingerprint", lambda path: "same")
    files = _files(env[0], ["a.mp4", "b.mp4"])
    groups = dd.find_groups([Candidate(vf, 4.0) for vf in files], 6, lambda *a: None, lambda: False, [])
    assert [g.tier for g in groups] == [TIER_IDENTICAL]


def test_unreadable_files_drop_out_and_are_named(env, monkeypatch):
    monkeypatch.setitem(HASHES, "e.mp4", None)
    files = _files(env[0], ["a.mp4", "b.mp4", "e.mp4"])
    unreadable = []
    groups = dd.find_groups([Candidate(vf, 4.0) for vf in files], 6, lambda *a: None, lambda: False, unreadable)
    assert unreadable == ["e.mp4"] and [len(g.members) for g in groups] == [2]


def test_cancel_returns_nothing(env):
    files = _files(env[0], ["a.mp4", "b.mp4"])
    assert dd.find_groups([Candidate(vf, 4.0) for vf in files], 6, lambda *a: None, lambda: True, []) == []


# --- The flow with the shared dialog -------------------------------------------

def test_flow_shows_groups_and_select_in_list_reselects(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "b.mp4", "c.mp4", "d.mp4"])
    window = Window(files)
    texts = {}

    def act(dialog):
        group = dialog.tree.topLevelItem(0)
        texts["group"] = group.text(0)
        texts["members"] = _member_names(dialog)
        texts["intro"] = dialog.summary_label.text()
        assert dialog.tree.selectedItems() == []   # nothing preselected
        group.child(1).setSelected(True)
        group.child(2).setSelected(True)
        dialog.select_button.click()

    _run(monkeypatch, window, files, act)
    assert texts["members"] == [["a.mp4", "b.mp4", "c.mp4"]]
    assert texts["group"].startswith("Possible match: similar frame hash") and "(3 files)" in texts["group"]
    assert "nearly the same length" in texts["intro"] and "not always mistakes" in texts["intro"]
    assert window.reselected == [id(files[1]), id(files[2])]
    assert window.video_files == files   # nothing removed


def test_not_duplicates_persists_across_dialog_instances(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "b.mp4"])
    window = Window(files)

    def dismiss(dialog):
        dialog.tree.topLevelItem(0).child(0).setSelected(True)
        dialog.dismiss_button.click()
        assert dialog.tree.topLevelItemCount() == 0   # hidden now

    _run(monkeypatch, window, files, dismiss)
    assert JsonDismissStore(str(env[1])).count() == 1   # written to the file

    # A new dialog (a new run, a new store object) keeps the group hidden ...
    def hidden(dialog):
        assert dialog.tree.topLevelItemCount() == 0
        dialog.show_hidden_check.setChecked(True)
        assert _member_names(dialog) == [["a.mp4", "b.mp4"]]

    assert _run(monkeypatch, window, files, hidden) is not None

    # ... also after the files are renamed, because the identity is the video fingerprint.
    renamed = _files(env[0], ["a.mp4", "b.mp4"])
    for vf in renamed:
        vf.path = vf.path.with_name("moved-" + vf.path.name)
    monkeypatch.setattr(dd, "video_fingerprint", lambda path: "v-" + os.path.basename(path).replace("moved-", ""))
    monkeypatch.setattr(dd, "frame_hash", lambda path, duration: HASHES[os.path.basename(path).replace("moved-", "")])

    def still_hidden(dialog):
        assert dialog.tree.topLevelItemCount() == 0

    assert _run(monkeypatch, Window(renamed), renamed, still_hidden) is not None


def test_trashed_files_leave_the_app_list(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "b.mp4", "e.mp4"])
    window = Window(files)
    trashed_paths = []

    def act(dialog):
        dialog._trash = trashed_paths.append   # never touch a real Recycle Bin
        monkeypatch.setattr(shared.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
        dialog.tree.topLevelItem(0).child(2).setSelected(True)
        dialog.trash_button.click()

    dialog = _run(monkeypatch, window, files, act)
    assert trashed_paths == [str(files[2].path)]
    assert dialog.trashed == [files[2]]
    assert window.video_files == files[:2] and window.refreshed == 1
    assert "Moved 1 file(s)" in window.status_bar.currentMessage()


def test_trash_confirm_lists_full_paths(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "b.mp4"])
    asked = []

    def act(dialog):
        dialog._trash = lambda path: None
        monkeypatch.setattr(shared.QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.StandardButton.No)
        dialog.tree.topLevelItem(0).child(0).setSelected(True)
        dialog.trash_button.click()

    _run(monkeypatch, Window(files), files, act)
    assert asked and str(files[0].path) in asked[0]


def test_nothing_found_says_so(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "d.mp4"])
    told = []
    monkeypatch.setattr(shared.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    window = Window(files)
    dd.find_duplicates_flow(window, files)
    assert told == ["No duplicates found."]
    assert "No duplicates found among 2 file(s)" in window.status_bar.currentMessage()


def test_flow_with_no_same_length_files_says_so(env, monkeypatch):
    files = _files(env[0], ["a.mp4", "b.mp4"])
    files[1].metadata.duration_seconds = 900.0
    told = []
    monkeypatch.setattr(dd.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    dd.find_duplicates_flow(Window(files), files)
    assert told and "same length" in told[0]


def test_default_dismiss_store_lives_in_the_settings_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(dd, "base_dir", lambda: tmp_path)
    store = dd.dismiss_store()
    store.dismiss(["x", "y"])
    assert (tmp_path / "videoredactor_duplicates_dismissed.json").exists()
