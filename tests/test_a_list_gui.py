"""A6/A7/A8/A12 (2026-10-13 review): the rules-engine conversion action, the Open Folder / Import route, Cancel
reaching ffmpeg, closing the app killing ffmpeg, and honest "what happens to the original" texts."""

import os
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import conversion_run as cr  # noqa: E402
from core import ffmpeg_backend as fb  # noqa: E402
from core import library_safety as ls  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.library_safety import SafeRun  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])


# --- A8: Cancel and closing the app reach ffmpeg --------------------------------------------------------------


def test_a8_process_file_hands_the_cancel_event_to_the_ffmpeg_runner(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "ep.avi"
    src.write_bytes(b"V" * 100)
    past = time.time() - 3600
    os.utime(src, (past, past))
    info = cp.MediaInfo(str(src), 100, 10.0, [cp.StreamInfo(0, "video", "h264", width=640, height=480)])
    seen = {}

    def runner(args, cancel_event=None, timeout=None):
        seen["event"] = cancel_event
        return None, "Cancelled"

    event = threading.Event()
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None)
    out = cr.process_file(run, str(src), cp.ConversionSettings(), probe=lambda p: info, runner=runner, cancel_event=event)
    assert seen["event"] is event
    assert not out.converted and src.exists() and os.listdir(lib) == ["ep.avi"]  # no .redact-part left behind


def test_a8_kill_running_ffmpeg_stops_a_live_run(monkeypatch):
    monkeypatch.setattr(fb, "get_executable_path", lambda name: sys.executable)
    result = {}

    def run():
        result["r"] = fb._run_ffmpeg(["ffmpeg", "-c", "import time; time.sleep(30)"], timeout=60)

    t = threading.Thread(target=run)
    t.start()
    deadline = time.time() + 10
    while fb.running_ffmpeg_count() == 0 and time.time() < deadline:
        time.sleep(0.05)
    assert fb.running_ffmpeg_count() == 1
    assert fb.kill_running_ffmpeg() == 1
    t.join(10)
    assert result["r"] == (None, "Cancelled")
    assert fb.running_ffmpeg_count() == 0


def test_a8_redact_env_carries_a_cancel_event_and_the_step_passes_it(tmp_path, monkeypatch):
    event = threading.Event()
    seen = {}
    monkeypatch.setattr(rs.conversion_run, "process_file", lambda run, p, conv, **kw: seen.update(kw) or cr.ConversionOutcome(False, message="nothing to do"))
    env = rs.RedactEnv(trash=lambda p: None, setting=lambda s, k, d="": d, cancel_event=event)
    path = tmp_path / "a.avi"
    path.write_bytes(b"")
    ctx = rs.VideoCtx.__new__(rs.VideoCtx)
    ctx.env, ctx.start_mtime, ctx.skip_reason, ctx.original = env, None, "", str(path)
    ctx.video = type("V", (), {"path": path, "metadata": type("M", (), {"content_type": rs.ContentType.TV})()})()
    ctx.bg = lambda fn, *a, **k: fn(*a, **k)
    rs.ConvertStep().execute(ctx)
    assert seen["cancel_event"] is event


# --- A7: RemuxStep and ConvertStep in one recipe -----------------------------------------------------------------


def test_a7_remux_step_refuses_when_the_convert_step_is_on(tmp_path):
    from redactor_common.core.pipeline import Recipe, begin_run, run_recipe_on_item

    path = tmp_path / "Film.mkv"
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="Film"))
    env = rs.RedactEnv(trash=lambda p: None, setting=lambda s, k, d="": {("safety", "dry_run"): "1"}.get((s, k), d))
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in ("remux_mkv_to_mp4", "convert_library") for s in catalogue}
    resolved = recipe.resolve(catalogue)
    run = begin_run([vf], resolved, env)
    entry = run_recipe_on_item(vf, resolved, 0.9, lambda v: rs.VideoCtx(v, env), describe=lambda v: v.path.name, run=run)
    assert entry.status is not FileStatus.CHANGED and path.exists()
    assert any("not remuxed" in n and "Convert to MP4/MKV" in n for n in entry.notes), entry.notes


# --- A12: honest texts ---------------------------------------------------------------------------------------------


def test_a12_original_fate_text_follows_the_settings():
    s = ls.SafetySettings(dry_run=False)
    assert "Recycle Bin" in rs.original_fate_text(s, False) and "convert" not in rs.original_fate_text(s, False).lower()
    assert "DRY RUN" in rs.original_fate_text(ls.SafetySettings(dry_run=True), True)
    assert "DELETED" in rs.original_fate_text(ls.SafetySettings(dry_run=False, delete_after_conversion=True), True)
    quarantine = rs.original_fate_text(ls.SafetySettings(dry_run=False, quarantine_root="D:/q"), True)
    assert "D:/q" in quarantine and "quarantine" in quarantine


def test_a6_dry_run_line_is_explicit():
    actions = [ls.Action("convert", "D:/v/a.avi", "D:/v/a.mkv", "legacy container .avi"), ls.Action("recycle", "D:/v/a.en.srt", "Recycle Bin", "ingested")]
    line = rs.dry_run_line(actions)
    assert line.startswith("DRY RUN") and "would convert a.avi -> a.mkv" in line and "would recycle a.en.srt" in line


# --- A6/A7: the GUI action -----------------------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def _old_file(path: Path, data: bytes = b"V" * 100) -> str:
    path.write_bytes(data)
    past = time.time() - 3600
    os.utime(path, (past, past))
    return str(path)


def _fake_engine(monkeypatch, src: str, written: list):
    """process_file with a fake probe and ffmpeg runner: the AVI becomes an MP4 by writing a small file."""
    import gui.main_window as mw

    info = cp.MediaInfo(src, 100, 10.0, [cp.StreamInfo(0, "video", "h264", width=640, height=480), cp.StreamInfo(1, "audio", "aac")])

    def runner(args, cancel_event=None, timeout=None):
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("MP4")
        written.append(args[-1])
        return 0, ""

    def probe(path):
        out = cp.MediaInfo(path, 3, 10.0, [cp.StreamInfo(0, "video", "h264", width=640, height=480), cp.StreamInfo(1, "audio", "aac")])
        return out if ".redact-part" in path else info

    real = cr.process_file
    monkeypatch.setattr(mw.conversion_run, "process_file", lambda run, p, conv, **kw: real(run, p, conv, probe=probe, runner=runner, **kw))


def test_a6_media_action_defaults_to_a_dry_run_and_reports_it(window, monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old_file(lib / "Show.S01E01.avi")
    written = []
    _fake_engine(monkeypatch, src, written)
    boxes = []

    def fake_exec(self):
        boxes.append((self.windowTitle(), self.text(), self.detailedText(), self.checkBox()))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    window._convert_paths([src])
    assert boxes[0][3] is not None and boxes[0][3].isChecked()  # "Dry run first" shown and on by default
    assert written == [] and os.listdir(lib) == ["Show.S01E01.avi"]
    assert "DRY RUN" in boxes[1][1] and "DRY RUN" in boxes[1][2] and "would convert Show.S01E01.avi -> Show.S01E01.mp4" in boxes[1][2]


def test_a6_media_action_real_run_converts_and_adds_the_row(window, monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old_file(lib / "Show.S01E01.avi")
    written = []
    _fake_engine(monkeypatch, src, written)
    trashed = []
    monkeypatch.setattr(ls.SafeRun, "_trash", lambda self, p: trashed.append(p) or os.remove(p))
    monkeypatch.setattr(VideoFile, "load", lambda self: None)
    boxes = []

    def fake_exec(self):
        if self.checkBox() is not None:
            self.checkBox().setChecked(False)  # the user unticks "Dry run first"
        boxes.append((self.text(), self.detailedText()))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    window._convert_paths([src])
    assert len(written) == 1 and os.listdir(lib) == ["Show.S01E01.mp4"]
    assert [os.path.basename(p) for p in trashed] == ["Show.S01E01.redact-orig.avi"]
    assert [vf.path.name for vf in window.video_files] == ["Show.S01E01.mp4"]  # not loaded before: added
    assert "1 of 1 file(s) converted" in boxes[1][0]


def test_a7_open_folder_offer_and_import_go_through_the_rules_engine(window, monkeypatch):
    seen = {}
    monkeypatch.setattr(window, "_convert_paths", lambda paths: seen.setdefault("paths", paths))
    window._import_paths(["D:/x/a.avi"])
    assert seen["paths"] == ["D:/x/a.avi"]
    monkeypatch.setattr(window, "_pick_videos_to_convert", lambda title, exts: ["D:/x/b.wmv"])
    seen.clear()
    window._on_import_and_convert()
    assert seen["paths"] == ["D:/x/b.wmv"]


def test_a8_cancel_in_the_progress_dialog_sets_the_event_and_stops(window, monkeypatch, tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    a = _old_file(lib / "a.avi")
    b = _old_file(lib / "b.avi")
    import gui.main_window as mw
    from PyQt6.QtWidgets import QProgressDialog

    calls = []

    def fake_process(run, path, conv, **kw):
        calls.append(path)
        dialog = window.findChild(QProgressDialog)
        if dialog is not None:
            dialog.canceled.emit()  # Cancel clicked while the first file is "encoding" (queued to the GUI thread)
        assert kw["cancel_event"].wait(5)  # what _run_ffmpeg's poll loop sees within half a second
        return cr.ConversionOutcome(False, message="Cancelled")

    monkeypatch.setattr(mw.conversion_run, "process_file", fake_process)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    window._convert_paths([a, b])
    assert calls == [a]  # the second file never started


def test_a8_closing_the_window_kills_ffmpeg(window, monkeypatch):
    import gui.main_window as mw

    killed = []
    monkeypatch.setattr(mw, "kill_running_ffmpeg", lambda: killed.append(1) or 0)
    event = threading.Event()
    window._active_cancel = event
    window.close()
    assert killed == [1] and event.is_set()
