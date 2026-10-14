"""The operations journal (Tools > History, core/history.py): every tag change the app makes to a file is
recorded with old and new values and can be restored. Real tiny MP4s made with ffmpeg (skipped without it);
the MKV path uses fakes (MKVToolNix is not needed). The journal is the conftest's tmp_path one."""

import os
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from redactor_common.core.journal import KIND_REDACT, KIND_SAVE, STATUS_FAILED, STATUS_OK  # noqa: E402

from core import history  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.filename_pattern import field_text  # noqa: E402
from core.mp4_backend import read_mp4_metadata  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, EDITABLE_FIELDS, VideoMetadata  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    d = tmp_path_factory.mktemp("journal_clip")
    out = d / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
        "-metadata:s:a:0", "language=eng", "-movflags", "+faststart", str(out),
    ], check=True, capture_output=True)
    return out


def loaded(clip, tmp_path, name="show.mp4", **tags):
    path = tmp_path / name
    shutil.copy2(clip, path)
    if tags:
        vf = VideoFile(path=path)
        vf.load()
        for key, value in tags.items():
            setattr(vf.metadata, key, value)
        vf.save(journal=False)
    vf = VideoFile(path=path)
    vf.load()
    return vf


# --- J1: fields_text ------------------------------------------------------------------------


def test_fields_text_is_the_editable_fields_only():
    md = VideoMetadata(title="Pilot", content_type=ContentType.TV, season_number=1, resolution="1920x1080",
                       scan_stamp="CHECKED OK;2026-10-14T00:00:00Z;abc")
    text = history.fields_text(md)
    assert list(text) == EDITABLE_FIELDS
    assert text["title"] == "Pilot" and text["content_type"] == "TV" and text["season_number"] == "1"
    assert text["episode_number"] == ""
    assert "scan_stamp" not in text and "resolution" not in text


def test_get_journal_defaults_next_to_the_settings(monkeypatch, tmp_path):
    from core import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "cfg" / "videoredactor_settings.ini")
    history.set_journal(None)
    j = history.get_journal()
    assert j.path == str(tmp_path / "cfg" / "videoredactor_journal.jsonl")
    assert history.get_journal() is j
    assert isinstance(j, history.GatedJournal)


@pytest.fixture
def on():
    """History is opt-in: these tests switch it on first."""
    history.set_history_enabled(True)


# --- O1: opt-in ------------------------------------------------------------------------------


def test_history_is_off_by_default_and_tolerates_garbage():
    from core import config

    assert history.history_enabled() is False
    config.set_setting("history", "enabled", "maybe?")
    assert history.history_enabled() is False
    history.set_history_enabled(True)
    assert history.history_enabled() is True
    history.set_history_enabled(False)
    assert history.history_enabled() is False


def _fake_written(monkeypatch):
    monkeypatch.setattr("core.video_file.write_mp4_metadata", lambda *a, **k: None)
    monkeypatch.setattr(VideoFile, "_verify_write", lambda self, diag="": "")


def _changed(tmp_path, name="a.mp4"):
    path = tmp_path / name
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="New"))
    vf.baseline = history.fields_text(VideoMetadata(title="Old"))
    return vf


def test_off_writes_nothing_and_creates_no_file(tmp_path, journal, monkeypatch):
    _fake_written(monkeypatch)
    vf = _changed(tmp_path)
    vf.save()
    assert vf.save_error == ""
    assert not os.path.exists(journal.path)
    # the shared dialog's own restore lines are gated too
    assert journal.record(str(vf.path), "restore", {"title": ("a", "b")}) is None
    journal.mark_restored("x", "y")
    assert not os.path.exists(journal.path)


def test_turning_it_on_records_and_off_stops_again(tmp_path, journal, monkeypatch):
    _fake_written(monkeypatch)
    history.set_history_enabled(True)
    _changed(tmp_path, "a.mp4").save()
    assert len(journal.entries()) == 1
    history.set_history_enabled(False)
    _changed(tmp_path, "b.mp4").save()
    assert len(journal.entries()) == 1


def test_off_save_all_writes_nothing(tmp_path, journal, monkeypatch):
    from PyQt6.QtWidgets import QApplication

    import gui.main_window as mw

    QApplication.instance() or QApplication([])
    _fake_written(monkeypatch)
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    files = [_changed(tmp_path, "a.mp4"), _changed(tmp_path, "b.mp4")]
    w.video_files = files
    w._save_files(files)
    assert all(not vf.save_error for vf in files)
    assert not os.path.exists(journal.path)
    history.set_history_enabled(True)
    files = [_changed(tmp_path, "c.mp4"), _changed(tmp_path, "d.mp4"), _changed(tmp_path, "e.mp4")]
    w.video_files = files
    w._save_files(files)
    assert len(journal.entries()) == 3
    w.close()


@needs_ffmpeg
def test_off_redact_and_cli_set_write_nothing(clip, tmp_path, journal, capsys):
    from videocli.main import main

    vf = loaded(clip, tmp_path, title="Old Title")
    ctx = rs.VideoCtx(vf, rs.RedactEnv(trash=Bin(tmp_path / "bin"), setting=lambda s, k, d="": d))
    ctx.work.metadata.title = "Redacted"
    rs.finalize_file(ctx, None)
    assert ctx.saved
    assert main(["set", str(vf.path), "-s", "title=After"]) == 0
    capsys.readouterr()
    assert not os.path.exists(journal.path)


def test_delete_history_recycles_the_file(tmp_path, journal, monkeypatch):
    _fake_written(monkeypatch)
    history.set_history_enabled(True)
    _changed(tmp_path).save()
    assert history.journal_exists()
    binned = Bin(tmp_path / "bin")
    assert history.delete_history(trash=binned) == ""
    assert not history.journal_exists() and journal.entries() == []
    assert (tmp_path / "bin" / os.path.basename(journal.path)).exists()  # in the "Recycle Bin", not unlinked
    assert history.delete_history(trash=binned) == ""  # nothing there: fine

    _changed(tmp_path, "b.mp4").save()

    def refuse(path):
        raise OSError("no Recycle Bin on this drive")

    assert "Recycle Bin" in history.delete_history(trash=refuse) and history.journal_exists()


# --- J2/J3/J6: plain save, real MP4 -------------------------------------------------------------


@needs_ffmpeg
def test_save_records_old_and_new_and_restore_brings_the_tags_back(clip, tmp_path, journal, on):
    vf = loaded(clip, tmp_path, title="Old Title", comment="keep me")
    assert vf.baseline["title"] == "Old Title"
    vf.metadata.title = "New Title"
    vf.metadata.season_number = 3
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert vf.baseline["title"] == "New Title"  # J2: the baseline follows a successful save

    entries = journal.entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == KIND_SAVE and entry.status == STATUS_OK and entry.path == str(vf.path)
    assert entry.changes == {"title": ("Old Title", "New Title"), "season_number": ("", "3")}

    values, skipped = journal.restore_values(entry)
    assert history.apply_restore(str(vf.path), values) == ""
    back = read_mp4_metadata(str(vf.path))
    assert back.title == "Old Title" and back.season_number is None and back.comment == "keep me"
    assert len(journal.entries()) == 1  # apply_restore journals nothing itself


@needs_ffmpeg
def test_a_save_with_no_real_change_records_nothing(clip, tmp_path, journal, on):
    vf = loaded(clip, tmp_path, title="Same")
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and journal.entries() == []


@needs_ffmpeg
def test_a_save_that_only_writes_a_scan_stamp_records_nothing(clip, tmp_path, journal, on):
    vf = loaded(clip, tmp_path, title="Same")
    vf.metadata.scan_stamp = "CHECKED OK;2026-10-14T00:00:00Z;"
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and journal.entries() == []


@needs_ffmpeg
def test_cli_set_is_journaled(clip, tmp_path, journal, capsys, on):
    from videocli.main import main

    vf = loaded(clip, tmp_path, title="Before")
    assert main(["set", str(vf.path), "-s", "title=After"]) == 0
    capsys.readouterr()
    [entry] = journal.entries()
    assert entry.kind == KIND_SAVE and entry.changes == {"title": ("Before", "After")}


def test_apply_restore_reports_an_unreadable_file(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    problem = history.apply_restore(str(path), {"title": "x"})
    assert problem.startswith("the file could not be read")


# --- failures --------------------------------------------------------------------------------


def test_a_failed_save_records_failed(tmp_path, journal, monkeypatch, on):
    path = tmp_path / "a.mp4"
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="New"))
    vf.baseline = history.fields_text(VideoMetadata(title="Old"))

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("core.video_file.write_mp4_metadata", boom)
    vf.save()
    assert vf.save_error
    [entry] = journal.entries()
    assert entry.status == STATUS_FAILED and "disk full" in entry.error
    assert entry.changes == {"title": ("Old", "New")}
    assert vf.baseline["title"] == "Old"  # unchanged: the file was not written


def test_a_broken_journal_never_fails_the_save_and_is_reported_once(tmp_path, monkeypatch, on):
    class Broken:
        def begin(self, *a, **k):
            raise OSError("journal is read-only")

        def finish(self, *a, **k):
            raise OSError("journal is read-only")

    history.set_journal(Broken())
    monkeypatch.setattr("core.video_file.write_mp4_metadata", lambda *a, **k: None)
    monkeypatch.setattr(VideoFile, "_verify_write", lambda self, diag="": "")
    for title in ("One", "Two"):
        path = tmp_path / f"{title}.mp4"
        path.write_bytes(b"")
        vf = VideoFile(path=path, metadata=VideoMetadata(title=title))
        vf.baseline = history.fields_text(VideoMetadata())
        vf.save()
        assert vf.save_error == ""
    problem = history.take_problem()
    assert "read-only" in problem and "History" in problem
    assert history.take_problem() == ""


def test_a_never_loaded_file_and_journal_false_record_nothing(tmp_path, journal, monkeypatch, on):
    monkeypatch.setattr("core.video_file.write_mp4_metadata", lambda *a, **k: None)
    monkeypatch.setattr(VideoFile, "_verify_write", lambda self, diag="": "")
    path = tmp_path / "a.mp4"
    path.write_bytes(b"")
    VideoFile(path=path, metadata=VideoMetadata(title="x")).save()  # no baseline (conversion re-write)
    vf = VideoFile(path=path, metadata=VideoMetadata(title="y"))
    vf.baseline = history.fields_text(VideoMetadata())
    vf.save(journal=False)
    assert journal.entries() == []


# --- MKV path, with fakes -------------------------------------------------------------------


def test_mkv_save_is_journaled(tmp_path, journal, on):
    persisted = {"xml": None, "title": None}

    def fake_run(args, **kwargs):
        exe = args[0]
        if "mkvpropedit" in exe:
            for i, arg in enumerate(args):
                if arg == "--set" and i + 1 < len(args) and args[i + 1].startswith("title="):
                    persisted["title"] = args[i + 1][len("title="):]
                if arg == "--tags" and i + 1 < len(args):
                    with open(args[i + 1].split(":", 1)[1], encoding="utf-8") as f:
                        persisted["xml"] = f.read()
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        if "mkvmerge" in exe:
            title = persisted["title"] or ""
            return subprocess.CompletedProcess(
                args=args, returncode=0, stdout='{"container":{"properties":{"title":"' + title + '"}}}', stderr="",
            )
        if "mkvextract" in exe:
            return subprocess.CompletedProcess(args=args, returncode=0, stdout=persisted["xml"] or "", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

    path = tmp_path / "episode.mkv"
    path.write_bytes(b"")
    with mock.patch("subprocess.run", side_effect=fake_run), \
            mock.patch("core.video_file.is_tool_available", return_value=True):
        vf = VideoFile(path=path, metadata=VideoMetadata(title="Pilot", content_type=ContentType.TV, season_number=1))
        vf.baseline = history.fields_text(VideoMetadata(title="pilot"))
        vf.dirty = True
        vf.save()
    assert vf.save_error == ""
    [entry] = journal.entries()
    assert entry.kind == KIND_SAVE and entry.status == STATUS_OK
    assert entry.changes == {
        "content_type": ("", "TV"), "title": ("pilot", "Pilot"), "season_number": ("", "1"),
    }


# --- J4: Redact ------------------------------------------------------------------------------


class Bin:
    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(exist_ok=True)

    def __call__(self, path: str) -> None:
        shutil.move(path, str(self.folder / os.path.basename(path)))


@needs_ffmpeg
def test_a_redact_save_records_one_redact_entry(clip, tmp_path, journal, on):
    folder = tmp_path / "videos"
    folder.mkdir()
    vf = loaded(clip, folder, title="Old Title")
    ctx = rs.VideoCtx(vf, rs.RedactEnv(trash=Bin(tmp_path / "bin"), setting=lambda s, k, d="": d))
    ctx.work.metadata.title = "Redacted Title"
    ctx.work.metadata.show_title = "Show"
    result = rs.finalize_file(ctx, None)
    assert ctx.saved, result
    assert field_text(read_mp4_metadata(str(vf.path)), "title") == "Redacted Title"
    [entry] = journal.entries()  # the scratch-copy save is not journaled, only this one
    assert entry.kind == KIND_REDACT and entry.status == STATUS_OK
    assert entry.path == os.path.realpath(vf.path)
    assert entry.changes == {"title": ("Old Title", "Redacted Title"), "show_title": ("", "Show")}
    assert vf.baseline["title"] == "Redacted Title"  # the live row was re-read


@needs_ffmpeg
def test_a_redact_with_nothing_to_change_records_nothing(clip, tmp_path, journal, on):
    vf = loaded(clip, tmp_path, title="Same")
    ctx = rs.VideoCtx(vf, rs.RedactEnv(trash=Bin(tmp_path / "bin"), setting=lambda s, k, d="": d))
    assert rs.finalize_file(ctx, None) is None
    assert journal.entries() == []


# --- J5: Tools > History ---------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch):
    from PyQt6.QtWidgets import QApplication

    import gui.main_window as mw

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def _fake_dialog(monkeypatch, seen, on_exec=None):
    import gui.main_window as mw

    class FakeDialog:
        def __init__(self, journal, apply_restore, parent, title):
            seen["journal"], seen["title"] = journal, title
            self.apply_restore = apply_restore

        def exec(self):
            if on_exec:
                on_exec(self)
            return 0

    monkeypatch.setattr(mw, "HistoryDialog", FakeDialog)


def _answer(monkeypatch, button):
    import gui.main_window as mw

    asked = []
    monkeypatch.setattr(mw.QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), button)[1])
    return asked


def test_tools_history_asks_when_off_and_nothing_recorded(window, monkeypatch):
    import gui.main_window as mw

    seen = {}
    _fake_dialog(monkeypatch, seen)
    asked = _answer(monkeypatch, mw.QMessageBox.StandardButton.No)
    window.actions_by_key["history"].trigger()
    assert asked and asked[0].startswith("History is off. Turn it on?")
    assert seen == {} and history.history_enabled() is False  # No: nothing happens

    asked = _answer(monkeypatch, mw.QMessageBox.StandardButton.Yes)
    window.actions_by_key["history"].trigger()
    assert history.history_enabled() is True and seen["title"] == "History"


def test_tools_history_off_with_a_journal_opens_without_asking(window, monkeypatch, journal, tmp_path):
    _fake_written(monkeypatch)
    history.set_history_enabled(True)
    _changed(tmp_path).save()
    history.set_history_enabled(False)
    seen = {}
    _fake_dialog(monkeypatch, seen)
    asked = _answer(monkeypatch, None)
    window.actions_by_key["history"].trigger()
    assert asked == [] and seen["title"] == "History (recording is off)"


def test_history_dialog_restores_and_reloads_loaded_rows(window, monkeypatch, tmp_path, on):
    import gui.main_window as mw

    w = window
    path = tmp_path / "a.mp4"
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="stale"))
    vf.dirty = True
    w.video_files = [vf]
    w._refresh_table_rows()

    seen = {}

    def restore(dialog):  # what the dialog's Restore does (on a worker thread in real use)
        assert dialog.apply_restore(str(path), {"title": "old"}) == ""

    _fake_dialog(monkeypatch, seen, restore)
    monkeypatch.setattr(mw.history, "apply_restore", lambda p, values: "")
    reloaded = []
    monkeypatch.setattr(VideoFile, "load", lambda self: reloaded.append(self.path))
    w.actions_by_key["history"].trigger()
    assert seen["journal"] is history.get_journal() and seen["title"] == "History"
    assert reloaded == [path] and vf.dirty is False


# --- O2: Preferences > History ---------------------------------------------------------------


def _prefs():
    from PyQt6.QtWidgets import QApplication

    from gui.video_preferences import VideoPreferencesDialog

    QApplication.instance() or QApplication([])
    return VideoPreferencesDialog()


def test_preferences_checkbox_is_off_round_trips_and_cancel_changes_nothing():
    from core.preferences_backend import KEY_HISTORY_ENABLED, build_sections
    from gui.video_preferences import HISTORY_CHECKBOX

    [section] = [s for s in build_sections() if s.key == "history"]
    [spec] = section.specs
    assert spec.label == HISTORY_CHECKBOX and spec.default is False
    assert history.journal_path() in section.description and "90 days" in section.description
    assert "covers" in section.description and "takes its history with it" in section.description

    dialog = _prefs()
    assert "History" in dialog.page_titles()
    assert dialog.value(KEY_HISTORY_ENABLED) is False
    dialog.set_value(KEY_HISTORY_ENABLED, True)
    dialog.reject()  # Cancel
    assert history.history_enabled() is False

    dialog = _prefs()
    dialog.set_value(KEY_HISTORY_ENABLED, True)
    dialog.apply()
    assert history.history_enabled() is True
    dialog = _prefs()
    assert dialog.value(KEY_HISTORY_ENABLED) is True
    dialog.set_value(KEY_HISTORY_ENABLED, False)
    dialog.apply()
    assert history.history_enabled() is False


def _delete_button(dialog):
    from gui.video_preferences import DELETE_HISTORY_LABEL

    [button] = [b for b in dialog.action_buttons if b.text() == DELETE_HISTORY_LABEL]
    return button


def test_preferences_delete_history_is_a_pref_action_that_asks_and_recycles(monkeypatch, tmp_path, journal):
    import redactor_common.core.trash as trash_module

    import gui.video_preferences as vp

    _fake_written(monkeypatch)
    history.set_history_enabled(True)
    _changed(tmp_path).save()
    recycled = []
    monkeypatch.setattr(trash_module, "move_to_trash", lambda p: (recycled.append(p), os.remove(p)))
    infos = []
    monkeypatch.setattr(vp.QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    dialog = _prefs()
    button = _delete_button(dialog)
    assert button.text() == "Delete History..."
    monkeypatch.setattr(vp.QMessageBox, "question", lambda *a, **k: vp.QMessageBox.StandardButton.Cancel)
    button.click()
    assert history.journal_exists() and recycled == []
    asked = []
    monkeypatch.setattr(
        vp.QMessageBox, "question", lambda *a, **k: (asked.append(a[2]), vp.QMessageBox.StandardButton.Yes)[1],
    )
    button.click()
    assert not history.journal_exists() and recycled == [journal.path]
    assert "Recycle Bin" in asked[0] and "Recycle Bin" in infos[-1]
    button.click()  # nothing left: says so, asks nothing
    assert len(asked) == 1 and "no history" in infos[-1]
    assert history.history_enabled() is True  # deleting does not switch recording off
