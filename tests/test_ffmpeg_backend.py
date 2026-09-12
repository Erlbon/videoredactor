"""
Tests for core/ffmpeg_backend.py.

Unlike mp4_backend/mkv_backend (untestable here -- no mutagen/MKVToolNix
in this sandbox), ffmpeg IS available, so this module gets real
functional tests against an actual generated video file, not just
syntax checks.
"""

import subprocess
import os
import tempfile
import threading
import unittest
import unittest.mock as mock
from pathlib import Path

from core.ffmpeg_backend import (
    get_duration_seconds, extract_thumbnail, remux_to_mp4, transcode_to_mp4,
)


def _make_test_video(path: str, duration: float = 3.0) -> None:
    """Generate a small synthetic test video via ffmpeg's lavfi testsrc,
    so tests don't depend on a real-world sample file being present."""
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", path,
        ],
        capture_output=True, check=True,
    )


class TestFfmpegBackend(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.video_path = os.path.join(self.tmpdir, "sample.mp4")
        _make_test_video(self.video_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_duration_seconds_returns_correct_value(self):
        duration = get_duration_seconds(self.video_path)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, 3.0, delta=0.2)

    def test_get_duration_seconds_missing_file_returns_none(self):
        self.assertIsNone(get_duration_seconds("/nonexistent/path.mp4"))

    def test_extract_thumbnail_creates_valid_jpeg(self):
        out_path = os.path.join(self.tmpdir, "thumb.jpg")
        ok = extract_thumbnail(self.video_path, out_path)
        self.assertTrue(ok)
        self.assertTrue(Path(out_path).exists())
        self.assertGreater(Path(out_path).stat().st_size, 0)
        # JPEG magic bytes
        with open(out_path, "rb") as f:
            self.assertEqual(f.read(2), b"\xff\xd8")

    def test_extract_thumbnail_explicit_timestamp(self):
        out_path = os.path.join(self.tmpdir, "thumb_ts.jpg")
        ok = extract_thumbnail(self.video_path, out_path, timestamp_seconds=1.5)
        self.assertTrue(ok)
        self.assertTrue(Path(out_path).exists())

    def test_extract_thumbnail_missing_file_returns_false(self):
        out_path = os.path.join(self.tmpdir, "thumb_fail.jpg")
        ok = extract_thumbnail("/nonexistent/path.mp4", out_path)
        self.assertFalse(ok)
        self.assertFalse(Path(out_path).exists())

    def test_remux_to_mp4_succeeds_and_preserves_playability(self):
        out_path = os.path.join(self.tmpdir, "remuxed.mp4")
        ok, stderr = remux_to_mp4(self.video_path, out_path)
        self.assertTrue(ok, msg=stderr)
        self.assertTrue(Path(out_path).exists())
        # Verify the remuxed output is itself a valid, probeable video
        remuxed_duration = get_duration_seconds(out_path)
        self.assertIsNotNone(remuxed_duration)
        self.assertAlmostEqual(remuxed_duration, 3.0, delta=0.2)

    def test_remux_missing_file_fails_with_stderr(self):
        out_path = os.path.join(self.tmpdir, "remux_fail.mp4")
        ok, stderr = remux_to_mp4("/nonexistent/path.mp4", out_path)
        self.assertFalse(ok)
        self.assertTrue(stderr.strip())

    def test_transcode_to_mp4_succeeds_and_preserves_playability(self):
        out_path = os.path.join(self.tmpdir, "transcoded.mp4")
        ok, stderr = transcode_to_mp4(self.video_path, out_path, crf=30, audio_bitrate="96k")
        self.assertTrue(ok, msg=stderr)
        self.assertTrue(Path(out_path).exists())
        # Verify the transcoded output is itself a valid, probeable video
        # -- a real re-encode, so this exercises libx264/aac actually
        # being invoked correctly, not just that ffmpeg exited 0.
        duration = get_duration_seconds(out_path)
        self.assertIsNotNone(duration)
        self.assertAlmostEqual(duration, 3.0, delta=0.3)


class TestTranscodeToMp4WithFakeProcess(unittest.TestCase):
    """transcode_to_mp4 tests that don't need a real ffmpeg encode --
    either the input is deliberately missing (ffmpeg never actually
    runs long enough to matter) or subprocess.Popen itself is mocked
    (argument-passing and cancellation). Split out from
    TestFfmpegBackend so these don't depend on that class's setUp
    generating a real synthetic video file via a real ffmpeg call.
    """

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_transcode_missing_file_fails_with_stderr(self):
        out_path = os.path.join(self.tmpdir, "transcode_fail.mp4")
        ok, stderr = transcode_to_mp4("/nonexistent/path.mp4", out_path)
        self.assertFalse(ok)
        self.assertTrue(stderr.strip())

    def test_transcode_includes_threads_flag_when_given(self):
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return _FakeInstantProcess()

        with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=fake_popen):
            transcode_to_mp4("input.mp4", os.path.join(self.tmpdir, "x.mp4"), threads=4)
        self.assertIn("-threads", captured["args"])
        self.assertIn("4", captured["args"])

    def test_transcode_omits_threads_flag_when_not_given(self):
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            return _FakeInstantProcess()

        with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=fake_popen):
            transcode_to_mp4("input.mp4", os.path.join(self.tmpdir, "x.mp4"), threads=None)
        self.assertNotIn("-threads", captured["args"])

    def test_transcode_cancel_event_stops_encode_without_waiting_for_it(self):
        # Uses a fake Popen that never finishes on its own, rather than
        # racing a real (fast, tiny) encode against cancellation timing
        # -- this exercises transcode_to_mp4's cancel-and-kill path
        # deterministically instead of relying on the real encode being
        # slow enough to interrupt in time.
        cancel_event = threading.Event()
        cancel_event.set()  # already cancelled before the first poll check
        fake_process = _FakeSlowProcess()

        with mock.patch("core.ffmpeg_backend.subprocess.Popen", return_value=fake_process):
            ok, message = transcode_to_mp4(
                "input.mp4", os.path.join(self.tmpdir, "cancelled.mp4"),
                cancel_event=cancel_event,
            )
        self.assertFalse(ok)
        self.assertEqual(message, "Cancelled")
        self.assertTrue(fake_process.killed)


class _FakeInstantProcess:
    """Stands in for a Popen whose ffmpeg process has already finished
    by the first communicate() call -- lets threads/crf argument-passing
    tests run without actually invoking ffmpeg."""
    returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def kill(self):
        pass


class _FakeSlowProcess:
    """Stands in for a Popen that never finishes on its own -- used to
    test transcode_to_mp4's cancellation path without depending on a
    real encode being slow enough to actually interrupt mid-run."""

    def __init__(self):
        self.killed = False
        self.returncode = None

    def communicate(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=timeout)
        self.returncode = -9
        return "", "killed"

    def kill(self):
        self.killed = True


if __name__ == "__main__":
    unittest.main()
