"""Operations > Find Duplicates... (core/video_duplicates.py and its
dialog): grouping logic on hand-made hashes, and real ffmpeg-generated
clips -- the same picture re-encoded at another bitrate/resolution must
group together, a different picture must not."""

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402

from core import video_duplicates as vd  # noqa: E402
from core.video_duplicates import Candidate, frame_hash, group_duplicates, hamming  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


# --- Grouping logic -----------------------------------------------------------

def test_hamming_counts_differing_bits():
    assert hamming(0b1011, 0b1011) == 0
    assert hamming(0b1011, 0b0100) == 4


def test_groups_need_similar_duration_and_hash():
    a = Candidate("a", 100.0, 0xFFFF)
    b = Candidate("b", 101.0, 0xFFFF ^ 0b11)           # 2 bits off, 1 s longer: same video
    c = Candidate("c", 100.5, 0x0000_0000_FFFF_0000)   # same length, other picture
    d = Candidate("d", 130.0, 0xFFFF)                  # same picture, other length
    e = Candidate("e", None, 0xFFFF)                   # no duration: left out
    f = Candidate("f", 100.2, None)                    # unreadable: left out
    groups = group_duplicates([a, b, c, d, e, f])
    assert [[x.key for x in g] for g in groups] == [["a", "b"]]


def test_threshold_is_configurable_and_groups_chain():
    a = Candidate("a", 10.0, 0)
    b = Candidate("b", 10.0, 0b111111)   # 6 bits from a
    c = Candidate("c", 10.0, 0b111111111111)  # 6 bits from b, 12 from a
    assert [[x.key for x in g] for g in group_duplicates([a, b, c])] == [["a", "b", "c"]]  # A~B, B~C
    assert group_duplicates([a, b, c], threshold=5) == []


def test_only_files_with_a_same_length_neighbour_are_hashed():
    items = [Candidate("a", 100.0), Candidate("b", 101.5), Candidate("lonely", 500.0), Candidate("none", None)]
    seen = []
    vd.find_duplicates(items, lambda c: seen.append(c.key) or 0)
    assert seen == ["a", "b"]


def test_dhash_of_a_gradient():
    row_up = bytes(range(0, 9))        # brighter to the right: no bit set
    row_down = bytes(range(9, 0, -1))  # darker to the right: every bit set
    assert vd.hash_from_pixels(row_up * 8) == 0
    assert vd.hash_from_pixels(row_down * 8) == (1 << 64) - 1


# --- Real clips ---------------------------------------------------------------

def _make(dest: Path, source: str, *, size="320x240", extra=()):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=size={size}:rate=10",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", *extra, str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _reencode(src: Path, dest: Path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "ultrafast", *args, str(dest)],
                   check=True, capture_output=True)
    return dest


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("dups")
    original = _make(d / "original.mp4", "testsrc")
    _reencode(original, d / "lowbitrate.mp4", "-b:v", "30k")
    _reencode(original, d / "small.mkv", "-vf", "scale=160:120")
    _make(d / "other.mp4", "mandelbrot")
    _make(d / "other_too.mp4", "smptebars")
    return d


@needs_ffmpeg
def test_reencoded_copies_group_and_a_different_clip_does_not(clips):
    names = ["original.mp4", "lowbitrate.mp4", "small.mkv", "other.mp4", "other_too.mp4"]
    items = [Candidate(n, 4.0) for n in names]
    groups = vd.find_duplicates(items, lambda c: frame_hash(str(clips / c.key), 4.0))
    assert [[c.key for c in g] for g in groups] == [["original.mp4", "lowbitrate.mp4", "small.mkv"]]


@needs_ffmpeg
def test_frame_falls_back_to_the_first_frame_when_the_seek_misses(clips):
    # A wrong (far too long) duration seeks past the end: no frame, so the first one is used.
    assert frame_hash(str(clips / "original.mp4"), 4000.0) is not None
    assert frame_hash(str(clips / "original.mp4"), None) is not None
    assert frame_hash(str(clips / "nothing.mp4"), 4.0) is None


# --- Dialog and flow ----------------------------------------------------------

def _window(files):
    from PyQt6.QtWidgets import QStatusBar, QWidget

    class Window(QWidget):
        def __init__(self):
            super().__init__()
            self.status_bar = QStatusBar(self)
            self.video_files = files
            self.refreshed = 0
            self.reselected = None

        def _refresh_table_rows(self):
            self.refreshed += 1

        def _reselect_files(self, ids):
            self.reselected = ids

    return Window()


def _video_files(directory, names):
    files = []
    for name in names:
        vf = VideoFile(path=directory / name, metadata=VideoMetadata(duration_seconds=4.0, resolution="320x240", video_codec="h264"))
        files.append(vf)
    return files


@needs_ffmpeg
def test_flow_groups_real_files_and_select_in_list_returns_the_picked_ones(clips, monkeypatch):
    from gui import duplicates_dialog as dd

    files = _video_files(clips, ["original.mp4", "lowbitrate.mp4", "small.mkv", "other.mp4"])
    window = _window(files)
    monkeypatch.setattr(dd.QInputDialog, "getInt", staticmethod(lambda *a, **k: (6, True)))
    monkeypatch.setattr(dd, "get_setting", lambda *a, **k: "6")
    monkeypatch.setattr(dd, "set_setting", lambda *a, **k: None)
    shown = {}

    def fake_exec(dialog):
        shown["groups"] = [
            [dialog.tree.topLevelItem(g).child(c).data(0, dd.FILE_ROLE).path.name
             for c in range(dialog.tree.topLevelItem(g).childCount())]
            for g in range(dialog.tree.topLevelItemCount())
        ]
        group = dialog.tree.topLevelItem(0)
        group.child(1).setSelected(True)
        group.child(2).setSelected(True)
        dialog.select_button.click()
        return QDialog.DialogCode.Accepted if dialog.to_select else QDialog.DialogCode.Rejected

    monkeypatch.setattr(dd.DuplicatesDialog, "exec", fake_exec)
    dd.find_duplicates_flow(window, files)
    assert shown["groups"] == [["original.mp4", "lowbitrate.mp4", "small.mkv"]]
    assert window.reselected == [id(files[1]), id(files[2])]
    assert [vf.path.name for vf in window.video_files] == [vf.path.name for vf in files]  # nothing removed


def test_dialog_actions_reveal_open_and_confirmed_trash(tmp_path, monkeypatch):
    from gui import duplicates_dialog as dd

    files = _video_files(tmp_path, ["a.mp4", "b.mp4", "c.mp4"])
    for vf in files:
        vf.path.write_bytes(b"x")
    dialog = dd.DuplicatesDialog([files], None)
    revealed, opened, trashed = [], [], []
    monkeypatch.setattr(dd, "reveal_in_file_manager", revealed.append)
    monkeypatch.setattr(dd, "open_with_default_app", opened.append)
    monkeypatch.setattr(dd, "move_to_trash", trashed.append)

    group = dialog.tree.topLevelItem(0)
    group.child(0).setSelected(True)
    dialog.reveal_button.click()
    dialog.open_button.click()
    assert revealed == opened == [str(files[0].path)]

    # declined: nothing moves
    monkeypatch.setattr(dd.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dialog.trash_button.click()
    assert trashed == [] and dialog.trashed == []

    monkeypatch.setattr(dd.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dialog.trash_button.click()
    assert trashed == [str(files[0].path)] and dialog.trashed == [files[0]]
    assert dialog.tree.topLevelItemCount() == 1 and dialog.tree.topLevelItem(0).childCount() == 2

    # moving one more leaves a lone file, which is no longer a group
    dialog.tree.topLevelItem(0).child(0).setSelected(True)
    dialog.trash_button.click()
    assert dialog.tree.topLevelItemCount() == 0


def test_flow_with_no_same_length_files_says_so(tmp_path, monkeypatch):
    from gui import duplicates_dialog as dd

    files = _video_files(tmp_path, ["a.mp4", "b.mp4"])
    files[1].metadata.duration_seconds = 900.0
    told = []
    monkeypatch.setattr(dd.QInputDialog, "getInt", staticmethod(lambda *a, **k: (6, True)))
    monkeypatch.setattr(dd, "get_setting", lambda *a, **k: "6")
    monkeypatch.setattr(dd, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(dd.QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    dd.find_duplicates_flow(_window(files), files)
    assert told and "same length" in told[0]
