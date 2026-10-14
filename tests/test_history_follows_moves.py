"""Tools > History entries follow a renamed, moved or converted file (core/history.record_moves; the RenameLog
on_move hook of redactor_common 2026-10-14-04): File > Rename File / Rename / Export / Move / Undo Last Rename
(through the app's RenameLog), Redact's own rename steps and its new-extension save, the command line's rename
and move, the conversion rules and Media > Remux to MP4. With history off nothing is written. The journal is
the conftest's tmp_path one."""

import os
import shutil
import subprocess
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from redactor_common.core.journal import KIND_REDACT, STATUS_OK  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import conversion_run as cr  # noqa: E402
from core import history  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.library_safety import SafeRun  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


_app = None


@pytest.fixture
def on():
    history.set_history_enabled(True)


def _fake_written(monkeypatch):
    monkeypatch.setattr("core.video_file.write_mp4_metadata", lambda *a, **k: None)
    monkeypatch.setattr(VideoFile, "_verify_write", lambda self, diag="": "")


def _saved(tmp_path, monkeypatch, name="a.mp4") -> Path:
    """A file with one journaled save (old title "Old", new "New")."""
    _fake_written(monkeypatch)
    path = tmp_path / name
    path.write_bytes(b"")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="New"))
    vf.baseline = history.fields_text(VideoMetadata(title="Old"))
    vf.save()
    assert vf.save_error == ""
    return path


# --- the hook --------------------------------------------------------------------------------


def test_record_moves_is_gated_and_never_raises(tmp_path, journal, monkeypatch):
    history.record_move(tmp_path / "a.mp4", tmp_path / "b.mp4")
    assert not os.path.exists(journal.path)  # off: nothing written, no file created
    journal.record_moves([("x", "y")])  # the journal itself is gated too
    assert not os.path.exists(journal.path)

    class Broken:
        def record_moves(self, pairs):
            raise OSError("read-only")

    history.set_history_enabled(True)
    history.set_journal(Broken())
    history.record_move("a", "b")  # does not raise
    assert "read-only" in history.take_problem()


def test_the_apps_rename_log_passes_moves_and_undo_to_the_history(tmp_path, journal, monkeypatch, on):
    import gui.main_window as mw

    log = mw._rename_log_at(str(tmp_path / "log.json"))
    assert log.on_move is history.record_moves
    old = _saved(tmp_path, monkeypatch)
    new = tmp_path / "Renamed.mp4"
    os.rename(old, new)
    log.record("File > Rename File", [(str(old), str(new))])
    [entry] = journal.entries()
    assert entry.path == str(new) and entry.original_path == str(old) and entry.changes == {"title": ("Old", "New")}
    assert journal.entries(path=str(new)) == [entry]

    result = log.undo_last()  # File > Undo Last Rename
    assert result is not None and not result.problems and old.exists()
    [entry] = journal.entries()
    assert entry.path == str(old)


def test_rename_log_with_history_off_writes_nothing(tmp_path, journal):
    import gui.main_window as mw

    log = mw._rename_log_at(str(tmp_path / "log.json"))
    (tmp_path / "a.mp4").write_bytes(b"")
    os.rename(tmp_path / "a.mp4", tmp_path / "b.mp4")
    log.record("x", [(str(tmp_path / "a.mp4"), str(tmp_path / "b.mp4"))])
    log.undo_last()
    assert not os.path.exists(journal.path)


def test_the_window_uses_the_hooked_log_for_rename_by_pattern(tmp_path, journal, monkeypatch, on):
    """The conftest's stand-in log is the app's own (_rename_log_at): every GUI rename path that records in
    _rename_log() (Rename File, Rename by Pattern, Move into Folders, Search/Replace, Redact) is followed."""
    import gui.main_window as mw

    log = mw._rename_log()
    assert log.on_move is history.record_moves


# --- Redact ----------------------------------------------------------------------------------


def _redact_ctx(tmp_path, env):
    path = tmp_path / "show.mp4"
    path.write_bytes(b"MP4")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="Old"))
    ctx = rs.VideoCtx(vf, env)
    ctx.work.metadata.title = "Redacted"
    scratch = ctx.new_scratch(".mkv")  # an earlier step changed the container: the save renames to .mkv
    Path(scratch).write_bytes(b"MKV")
    ctx.adopt(scratch)
    return ctx, path


def _fake_commit(monkeypatch):
    monkeypatch.setattr(VideoFile, "save", lambda self, journal=True: None)
    monkeypatch.setattr(VideoFile, "load", lambda self: None)

    def commit(original, scratch, trash, verify):
        os.replace(scratch, original)
        return types.SimpleNamespace(backup_kept=False, backup="", warning="")

    monkeypatch.setattr(rs, "commit_in_place", commit)


def test_redact_save_with_a_new_extension_is_recorded_where_saved_and_follows_the_rename(
    tmp_path, journal, monkeypatch, on,
):
    _fake_commit(monkeypatch)
    ctx, original = _redact_ctx(tmp_path, rs.RedactEnv(trash=lambda p: None, setting=lambda s, k, d="": d))
    result = ctx.save()
    assert ctx.saved, result
    [entry] = journal.entries()
    assert entry.kind == KIND_REDACT and entry.status == STATUS_OK
    assert entry.original_path == str(original)  # where the save happened
    assert entry.path == str(original.with_suffix(".mkv"))  # followed by the successful rename


def test_redact_save_whose_rename_fails_keeps_the_entry_at_the_saved_path(tmp_path, journal, monkeypatch, on):
    _fake_commit(monkeypatch)

    def refuse(old, new):
        raise OSError("in use")

    monkeypatch.setattr(rs, "rename_no_clobber", refuse)
    ctx, original = _redact_ctx(tmp_path, rs.RedactEnv(trash=lambda p: None, setting=lambda s, k, d="": d))
    ctx.save()
    assert ctx.saved
    [entry] = journal.entries()
    assert entry.path == str(original) == entry.original_path  # NOT the planned .mkv name
    assert original.exists() and not original.with_suffix(".mkv").exists()


def test_redact_rename_step_without_a_rename_log_follows_in_the_history(tmp_path, journal, monkeypatch, on):
    """The command line's Redact has no rename log: the step records the move itself."""
    old = _saved(tmp_path, monkeypatch, "old.mp4")
    vf = VideoFile(path=old, metadata=VideoMetadata(title="Pilot"))
    ctx = rs.VideoCtx(vf, rs.RedactEnv(setting=lambda s, k, d="": d, pattern_history=lambda: ["%title%"]))
    result = rs.RenameStep().execute(ctx)
    assert (tmp_path / "Pilot.mp4").exists(), result
    [entry] = journal.entries()
    assert entry.path == str(tmp_path / "Pilot.mp4") and entry.original_path == str(old)


def test_redact_rename_step_with_the_apps_log_records_once(tmp_path, journal, monkeypatch, on):
    import gui.main_window as mw

    old = _saved(tmp_path, monkeypatch, "old.mp4")
    vf = VideoFile(path=old, metadata=VideoMetadata(title="Pilot"))
    log = mw._rename_log_at(str(tmp_path / "log.json"))
    ctx = rs.VideoCtx(vf, rs.RedactEnv(setting=lambda s, k, d="": d, pattern_history=lambda: ["%title%"], rename_log=log))
    rs.RenameStep().execute(ctx)
    [entry] = journal.entries()
    assert entry.path == str(tmp_path / "Pilot.mp4")
    with open(journal.path, encoding="utf-8") as f:
        assert sum('"t":"moved"' in line for line in f) == 1  # the log's hook only, not twice
    assert log.last_batch().label == "Redact: rename"


# --- conversions -----------------------------------------------------------------------------


def _media(path, size=1000):
    streams = [cp.StreamInfo(0, "video", "h264", width=640, height=480), cp.StreamInfo(1, "audio", "aac")]
    return cp.MediaInfo(path, size, 60.0, streams)


def _old_file(path, text="A" * 1000):
    path.write_text(text, encoding="utf-8")
    past = os.path.getmtime(path) - 3600
    os.utime(path, (past, past))
    return str(path)


def _convert(tmp_path, dry_run=False):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old_file(lib / "ep.avi")
    info = _media(src)
    plan = cp.Plan(container=".mp4", reasons=["legacy container .avi"])

    def runner(args, cancel_event=None, timeout=None):
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 900)
        return 0, ""

    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=dry_run, trash=lambda p: os.remove(p),
                  probe=lambda p: _media(p, 900))
    out = cr.convert_file(run, src, plan, info, cp.ConversionSettings(), runner=runner, probe=lambda p: _media(p, 900))
    return src, out


def test_a_conversion_that_changes_the_extension_takes_the_history_along(tmp_path, journal, on):
    src = str(tmp_path / "lib" / "ep.avi")
    journal.record(src, "save", {"title": ("a", "b")})
    src, out = _convert(tmp_path)
    assert out.converted and out.final_path.endswith("ep.mp4")
    [entry] = journal.entries()
    assert entry.path == out.final_path and entry.original_path == src


def test_a_dry_run_conversion_and_history_off_record_no_move(tmp_path, journal):
    history.set_history_enabled(True)
    journal.record(str(tmp_path / "lib" / "ep.avi"), "save", {"title": ("a", "b")})
    src, out = _convert(tmp_path, dry_run=True)
    assert out.converted
    [entry] = journal.entries()
    assert entry.path == src  # nothing moved

    history.set_history_enabled(False)
    shutil.rmtree(tmp_path / "lib")
    os.remove(journal.path)
    _src, out = _convert(tmp_path)
    assert out.converted and not os.path.exists(journal.path)


def test_remux_with_recycled_original_follows_in_the_history(tmp_path, journal, monkeypatch, on):
    from PyQt6.QtWidgets import QApplication

    import gui.main_window as mw

    global _app
    _app = QApplication.instance() or QApplication([])  # kept referenced: a collected QApplication crashes
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    window = mw.MainWindow()
    try:
        src = tmp_path / "m.mkv"
        src.write_bytes(b"")
        journal.record(str(src), "save", {"title": ("a", "b")})
        window.video_files = [VideoFile(path=src)]
        window._refresh_table_rows()
        window.table.selectAll()
        monkeypatch.setattr(mw.VideoFile, "load", lambda self: None)
        monkeypatch.setattr(window, "_run_transcode_jobs", lambda jobs, s, t, remux=False: {0: (True, "")})
        monkeypatch.setattr(window, "_confirm_remux", lambda jobs: True)
        monkeypatch.setattr(window, "_remux_summary", lambda summary, lines, warn: None)
        monkeypatch.setattr(mw, "move_to_trash", lambda p: None)
        window._on_remux_selected()
    finally:
        window.close()
    [entry] = journal.entries()
    assert entry.path == str(tmp_path / "m.mp4") and entry.original_path == str(src)


# --- command line ----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        pytest.skip("ffmpeg/ffprobe not installed")
    d = tmp_path_factory.mktemp("moves_clip")
    out = d / "clip.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
        "-c:v", "libx264", "-preset", "ultrafast", "-metadata", "title=Pilot", str(out),
    ], check=True, capture_output=True)
    return out


@needs_ffmpeg
def test_cli_rename_and_move_follow_in_the_history(clip, tmp_path, journal, capsys, on):
    from videocli.main import main

    folder = tmp_path / "in"
    folder.mkdir()
    path = folder / "x.mp4"
    shutil.copy2(clip, path)
    journal.record(str(path), "save", {"title": ("a", "Pilot")})
    assert main(["rename", str(path), "-p", "%title%"]) == 0
    renamed = folder / "Pilot.mp4"
    assert renamed.exists()
    [entry] = journal.entries()
    assert entry.path == str(renamed)

    root = tmp_path / "library"
    root.mkdir()
    assert main(["move", str(renamed), "-p", "Shows/%title%", "--root", str(root)]) == 0
    capsys.readouterr()
    moved = root / "Shows" / "Pilot.mp4"
    assert moved.exists()
    [entry] = journal.entries()
    assert os.path.normcase(entry.path) == os.path.normcase(str(moved)) and entry.original_path == str(path)


@needs_ffmpeg
def test_cli_rename_with_history_off_writes_nothing(clip, tmp_path, journal, capsys):
    from videocli.main import main

    path = tmp_path / "x.mp4"
    shutil.copy2(clip, path)
    assert main(["rename", str(path), "-p", "%title%"]) == 0
    capsys.readouterr()
    assert (tmp_path / "Pilot.mp4").exists() and not os.path.exists(journal.path)
