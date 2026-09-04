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
import unittest
from pathlib import Path

from core.ffmpeg_backend import get_duration_seconds, extract_thumbnail, remux_to_mp4


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


if __name__ == "__main__":
    unittest.main()
