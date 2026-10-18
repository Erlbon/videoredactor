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
    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=lambda args, **k: _Slow(args[-1])):
        ok, message = fn("in.mkv", str(out), cancel_event=cancel)
    assert (ok, message) == (False, "Cancelled")
    assert list(tmp_path.iterdir()) == []


def test_hung_remux_is_killed_after_the_timeout_and_leaves_nothing(tmp_path):
    out = tmp_path / "x.mp4"
    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=lambda args, **k: _Slow(args[-1])),             mock.patch.object(fb, "REMUX_TIMEOUT_SECONDS", 0.5):
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
    from redactor_common.core.temp_names import is_app_temp_name

    assert ok and len(seen) == 1 and os.path.dirname(seen[0]) == str(tmp_path)
    assert is_app_temp_name(os.path.basename(seen[0])) and seen[0] != str(out)  # a hidden family temp name, not "x.partial.mp4"
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
