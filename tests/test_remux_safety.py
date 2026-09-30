"""Remux / convert safety (review findings H1-H3, M8-M10): a remux keeps
every track or keeps the original, runs off the GUI thread, never leaves
a partial output behind, one failing job doesn't abort the batch, and two
inputs can't claim the same output name. Real ffmpeg for the stream
checks (like tests/test_ffmpeg_backend.py; CI installs ffmpeg)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import subprocess  # noqa: E402
import threading  # noqa: E402
import unittest.mock as mock  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import ffmpeg_backend as fb  # noqa: E402
from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def multi_track_mkv(tmp_path_factory):
    """Video + two audio tracks + an SRT subtitle track."""
    d = tmp_path_factory.mktemp("multi")
    (d / "sub.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    path = d / "multi.mkv"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-i", str(d / "sub.srt"),
        "-map", "0", "-map", "1", "-map", "2", "-map", "3",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "srt", str(path),
    )
    return path


def test_remux_keeps_every_audio_track(multi_track_mkv, tmp_path):
    out = tmp_path / "multi.mp4"
    ok, message = fb.remux_to_mp4(str(multi_track_mkv), str(out))
    assert ok, message
    counts, _duration = fb._stream_summary(str(out))
    assert counts["audio"] == 2  # a bare `-c copy` kept only one
    assert not list(tmp_path.glob("*.partial*"))


def test_verify_remux_reports_what_the_fallback_dropped(multi_track_mkv, tmp_path):
    # Subtitles survive a remux only when MP4 accepts the codec; if the
    # fallback had to drop them, the verdict must say so (and the caller
    # then keeps the original).
    out = tmp_path / "audio_only.mp4"
    _ffmpeg("-i", str(multi_track_mkv), "-map", "0:V", "-map", "0:a", "-c", "copy", str(out))
    assert "subtitle track(s) missing" in fb.verify_remux(str(multi_track_mkv), str(out))
    full = tmp_path / "full.mp4"
    _ffmpeg("-i", str(multi_track_mkv), "-map", "0:V", "-map", "0:a", "-map", "0:s", "-c", "copy",
            "-c:s", "mov_text", str(full))
    assert fb.verify_remux(str(multi_track_mkv), str(full)) == ""


def test_verify_remux_reports_a_missing_audio_track_and_a_short_result():
    summaries = {"a.mkv": ({"video": 1, "audio": 2}, 100.0), "a.mp4": ({"video": 1, "audio": 1}, 60.0)}
    with mock.patch.object(fb, "_stream_summary", side_effect=lambda p: summaries[p]):
        verdict = fb.verify_remux("a.mkv", "a.mp4")
    assert "1 audio track(s) missing" in verdict and "shorter" in verdict
    with mock.patch.object(fb, "_stream_summary", return_value=None):
        assert "couldn't compare" in fb.verify_remux("a.mkv", "a.mp4")


def test_failed_remux_leaves_no_output_or_partial(tmp_path):
    bad = tmp_path / "bad.mkv"
    bad.write_bytes(b"not a video")
    out = tmp_path / "bad.mp4"
    ok, _message = fb.remux_to_mp4(str(bad), str(out))
    assert not ok
    assert list(tmp_path.iterdir()) == [bad]


class _Slow:
    """A Popen that writes a partial output, then never finishes on its own."""
    returncode = None

    def __init__(self, temp):
        Path(temp).write_bytes(b"half a file")
        self.killed = False

    def communicate(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        self.returncode = -9
        return "", ""

    def kill(self):
        self.killed = True


@pytest.mark.parametrize("fn", [fb.remux_to_mp4, fb.transcode_to_mp4])
def test_cancel_removes_the_partial_file(fn, tmp_path):
    out = tmp_path / "x.mp4"
    cancel = threading.Event()
    cancel.set()
    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=lambda *a, **k: _Slow(fb.partial_path(str(out)))):
        ok, message = fn("in.mkv", str(out), cancel_event=cancel)
    assert (ok, message) == (False, "Cancelled")
    assert list(tmp_path.iterdir()) == []


def test_hung_remux_is_killed_after_the_timeout_and_leaves_nothing(tmp_path):
    out = tmp_path / "x.mp4"
    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=lambda *a, **k: _Slow(fb.partial_path(str(out)))),             mock.patch.object(fb, "REMUX_TIMEOUT_SECONDS", 0.5):
        ok, message = fb.remux_to_mp4("in.mkv", str(out))
    assert ok is False and "ffmpeg" in message and list(tmp_path.iterdir()) == []


def test_ffmpeg_writes_to_a_temp_name_and_renames_on_success(tmp_path):
    out = tmp_path / "x.mp4"
    seen = []

    class Instant:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(args, **kwargs):
        seen.append(args[-1])
        Path(args[-1]).write_bytes(b"done")
        return Instant()

    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=fake_popen):
        ok, _ = fb.remux_to_mp4("in.mkv", str(out))
    assert ok and seen == [str(tmp_path / "x.partial.mp4")]
    assert out.read_bytes() == b"done" and [p.name for p in tmp_path.iterdir()] == ["x.mp4"]


def test_a_failed_transcode_removes_its_partial_output(tmp_path):
    out = tmp_path / "x.mp4"

    class Failing:
        returncode = 1

        def communicate(self, timeout=None):
            return "", "boom"

    def fake_popen(args, **kwargs):
        Path(args[-1]).write_bytes(b"half")
        return Failing()

    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=fake_popen):
        ok, message = fb.transcode_to_mp4("in.mkv", str(out))
    assert (ok, message) == (False, "boom") and list(tmp_path.iterdir()) == []


def test_worker_survives_a_job_that_raises():
    import gui.main_window as mw

    jobs = [(Path("a.mkv"), Path("a.mp4")), (Path("b.mkv"), Path("b.mp4"))]
    worker = mw._TranscodeWorker(jobs, None, remux=True)
    finished = []
    worker.file_finished.connect(lambda i, ok, msg: finished.append((i, ok, msg)))

    def fake_remux(src, dst, cancel_event=None):
        if src == "a.mkv":
            raise PermissionError("denied")
        return True, ""

    with mock.patch.object(mw, "remux_to_mp4", fake_remux), mock.patch.object(mw, "verify_remux", lambda s, d: ""):
        worker.run()
    assert finished == [(0, False, "denied"), (1, True, "")]


def test_claim_output_refuses_a_duplicate_destination_in_one_batch(tmp_path):
    import gui.main_window as mw

    claimed: set[str] = set()
    assert mw._claim_output(tmp_path / "a.mp4", claimed)
    assert not mw._claim_output(tmp_path / "a.mp4", claimed)  # a.avi and a.mov both -> a.mp4
    (tmp_path / "b.mp4").write_bytes(b"")
    assert not mw._claim_output(tmp_path / "b.mp4", claimed)  # already on disk


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


def _remux_window(window, monkeypatch, tmp_path, verdict):
    import gui.main_window as mw

    src = tmp_path / "m.mkv"
    src.write_bytes(b"")
    vf = VideoFile(path=src)
    window.video_files = [vf]
    window._refresh_table_rows()
    window.table.selectAll()
    monkeypatch.setattr(mw.VideoFile, "load", lambda self: None)
    seen = {}

    def fake_run(jobs, settings, title, remux=False):
        seen["remux"] = remux
        return {0: (True, verdict)}

    monkeypatch.setattr(window, "_run_transcode_jobs", fake_run)
    asked = []
    monkeypatch.setattr(window, "_confirm_delete_original", lambda vf, out: asked.append(vf) or True)
    trashed = []
    monkeypatch.setattr(mw, "move_to_trash", lambda p: trashed.append(p))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    window._on_remux_selected()
    return seen, asked, trashed, src


def test_remux_verified_original_goes_to_the_recycle_bin_not_unlinked(window, monkeypatch, tmp_path):
    seen, asked, trashed, src = _remux_window(window, monkeypatch, tmp_path, verdict="")
    assert seen["remux"] is True  # runs on the worker, not inline
    assert trashed == [str(src)] and src.exists()  # (the fake trash doesn't remove it)


def test_remux_that_lost_a_track_keeps_the_original_and_does_not_offer_deletion(window, monkeypatch, tmp_path):
    _seen, asked, trashed, _src = _remux_window(window, monkeypatch, tmp_path, verdict="1 audio track(s) missing")
    assert asked == [] and trashed == []
