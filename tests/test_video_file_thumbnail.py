"""
Tests for VideoFile.get_thumbnail() -- runnable here since it only
depends on core/ffmpeg_backend.py (real in this sandbox), not on
mutagen/MKVToolNix (not installable here). mp4_backend/mkv_backend are
imported by video_file.py but never called by get_thumbnail(), so this
test path doesn't touch either.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.video_file import VideoFile, THUMBNAIL_CACHE_DIR


def _make_test_video(path: str, duration: float = 2.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=320x240:rate=10",
            "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", path,
        ],
        capture_output=True, check=True,
    )


class TestVideoFileThumbnail(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.video_path = Path(self.tmpdir) / "sample.mp4"
        _make_test_video(str(self.video_path))
        # Clean slate for the shared cache dir so tests don't see
        # leftovers from a previous run -- same test-isolation lesson
        # the epub tool's rename_book_file tests hit (v47).
        shutil.rmtree(THUMBNAIL_CACHE_DIR, ignore_errors=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        shutil.rmtree(THUMBNAIL_CACHE_DIR, ignore_errors=True)

    def test_get_thumbnail_generates_and_caches(self):
        vf = VideoFile(path=self.video_path)
        first = vf.get_thumbnail()
        self.assertIsNotNone(first)
        self.assertTrue(first.exists())

        mtime_before = first.stat().st_mtime
        second = vf.get_thumbnail()
        self.assertEqual(first, second)
        # Confirm it's actually the cached file, not a freshly-regenerated
        # one that happens to share a name -- mtime must be unchanged.
        self.assertEqual(mtime_before, second.stat().st_mtime)

    def test_get_thumbnail_force_regenerate(self):
        vf = VideoFile(path=self.video_path)
        first = vf.get_thumbnail()
        mtime_before = first.stat().st_mtime

        import time
        time.sleep(0.05)  # ensure a detectable mtime difference
        second = vf.get_thumbnail(force_regenerate=True)
        self.assertGreater(second.stat().st_mtime, mtime_before)

    def test_get_thumbnail_missing_file_returns_none(self):
        vf = VideoFile(path=Path("/nonexistent/missing.mp4"))
        self.assertIsNone(vf.get_thumbnail())

    def test_two_video_files_get_distinct_cache_entries(self):
        video2_path = Path(self.tmpdir) / "sample2.mp4"
        _make_test_video(str(video2_path))

        vf1 = VideoFile(path=self.video_path)
        vf2 = VideoFile(path=video2_path)
        thumb1 = vf1.get_thumbnail()
        thumb2 = vf2.get_thumbnail()
        self.assertNotEqual(thumb1, thumb2)


if __name__ == "__main__":
    unittest.main()
