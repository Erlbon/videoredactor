"""
Tests for core/opensubtitles_hash.py.

Runnable for real in this sandbox -- pure file I/O, no network/API key
needed. The main correctness test cross-checks against an independently
written reference implementation (not just re-running the same code
path), since a hash algorithm with a subtle transcription bug would
otherwise silently "pass" by comparing itself to itself.
"""

import os
import struct
import shutil
import tempfile
import unittest
import subprocess

from core.opensubtitles_hash import compute_moviehash, MIN_FILE_SIZE


def _reference_hash(path: str) -> str:
    """Independent reimplementation of the same public algorithm, used
    only to cross-check core/opensubtitles_hash.py's output -- written
    separately (different loop structure, single-read-then-slice instead
    of read-in-a-loop) so a bug in one implementation is unlikely to be
    mirrored in the other.
    """
    filesize = os.path.getsize(path)
    hash_val = filesize
    with open(path, "rb") as f:
        data_start = f.read(65536)
        f.seek(max(0, filesize - 65536))
        data_end = f.read(65536)
    for chunk in (data_start, data_end):
        for i in range(0, 65536, 8):
            val = struct.unpack("<q", chunk[i:i + 8])[0]
            hash_val = (hash_val + val) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % hash_val


class TestOpenSubtitlesHash(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _make_video(self, name: str, duration: int = 10) -> str:
        path = os.path.join(self.tmpdir, name)
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=640x480:rate=24",
                "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
                "-c:v", "libx264", "-b:v", "500k", "-c:a", "aac", "-shortest", path,
            ],
            capture_output=True, check=True,
        )
        return path

    def test_matches_independent_reference_implementation(self):
        video = self._make_video("sample.mp4")
        self.assertEqual(compute_moviehash(video), _reference_hash(video))

    def test_hash_is_16_hex_chars(self):
        video = self._make_video("sample2.mp4")
        h = compute_moviehash(video)
        self.assertEqual(len(h), 16)
        int(h, 16)  # raises ValueError if not valid hex

    def test_stable_across_repeated_calls(self):
        video = self._make_video("sample3.mp4")
        self.assertEqual(compute_moviehash(video), compute_moviehash(video))

    def test_different_content_gives_different_hash(self):
        path_a = os.path.join(self.tmpdir, "a.bin")
        path_b = os.path.join(self.tmpdir, "b.bin")
        with open(path_a, "wb") as f:
            f.write(os.urandom(200_000))
        with open(path_b, "wb") as f:
            f.write(os.urandom(200_000))
        self.assertNotEqual(compute_moviehash(path_a), compute_moviehash(path_b))

    def test_file_below_minimum_size_returns_none(self):
        path = os.path.join(self.tmpdir, "tiny.bin")
        with open(path, "wb") as f:
            f.write(b"x" * (MIN_FILE_SIZE - 1))
        self.assertIsNone(compute_moviehash(path))

    def test_file_exactly_at_minimum_size_succeeds(self):
        path = os.path.join(self.tmpdir, "boundary.bin")
        with open(path, "wb") as f:
            f.write(b"x" * MIN_FILE_SIZE)
        self.assertIsNotNone(compute_moviehash(path))


if __name__ == "__main__":
    unittest.main()
