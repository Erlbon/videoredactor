"""
Regression test for the missing-MKVToolNix error message.

Before the fix, VideoFile.load()/save() on an MKV file when mkvmerge/
mkvpropedit isn't on PATH surfaced a raw, ambiguous exception
(`[WinError 2] The system cannot find the file specified` on Windows,
`[Errno 2] No such file or directory: 'mkvmerge'` on Linux/Mac) that
reads the same whether the VIDEO file is missing or the TOOL is
missing. Fixed by checking core.external_tools.is_tool_available()
proactively before attempting the subprocess call.

This sandbox genuinely lacks MKVToolNix (confirmed via
tests/test_external_tools.py), so this is a real reproduction of the
reported bug, not a simulated one -- no mocking of "tool missing" is
needed here because it's actually true in this environment.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from core.video_file import VideoFile
from core.external_tools import is_tool_available, MKVTOOLNIX


def _make_test_mkv(path: str, duration: float = 1.0) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size=160x120:rate=5",
            "-c:v", "libx264", "-shortest", "-loglevel", "error", path,
        ],
        capture_output=True, check=True,
    )


class TestMissingMkvtoolnixErrorMessage(unittest.TestCase):
    def setUp(self):
        if is_tool_available(MKVTOOLNIX):
            self.skipTest(
                "MKVToolNix IS installed in this environment -- this test "
                "specifically needs it absent to reproduce the original bug. "
                "The fix itself (checking is_tool_available before the "
                "subprocess call) is still exercised by test_external_tools.py "
                "regardless of whether this specific test runs."
            )
        self.tmpdir = tempfile.mkdtemp()
        self.mkv_path = os.path.join(self.tmpdir, "sample.mkv")
        _make_test_mkv(self.mkv_path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_load_gives_clear_tool_missing_message_not_raw_errno(self):
        vf = VideoFile(path=Path(self.mkv_path))
        vf.load()

        self.assertTrue(vf.load_error, "Expected a load_error to be set")
        # The fix's whole point: a human-readable, actionable message --
        # not the raw ambiguous exception text.
        self.assertIn("MKVToolNix", vf.load_error)
        self.assertIn("PATH", vf.load_error)
        self.assertIn(MKVTOOLNIX.download_url, vf.load_error)
        # Explicitly confirm the OLD confusing text is gone.
        self.assertNotIn("WinError", vf.load_error)
        self.assertNotIn("Errno 2", vf.load_error)

    def test_save_gives_clear_tool_missing_message_not_raw_errno(self):
        vf = VideoFile(path=Path(self.mkv_path))
        vf.metadata.title = "Test Title"
        vf.save()

        self.assertTrue(vf.save_error, "Expected a save_error to be set")
        self.assertIn("MKVToolNix", vf.save_error)
        self.assertIn("PATH", vf.save_error)
        self.assertNotIn("WinError", vf.save_error)
        self.assertNotIn("Errno 2", vf.save_error)

    def test_mp4_is_unaffected_by_the_mkv_specific_check(self):
        mp4_path = os.path.join(self.tmpdir, "sample.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=5",
                "-c:v", "libx264", "-shortest", "-loglevel", "error", mp4_path,
            ],
            capture_output=True, check=True,
        )
        vf = VideoFile(path=Path(mp4_path))
        vf.load()
        # Whatever happens to MP4 loading (mutagen may or may not be
        # installed in a given environment), it must NOT be the
        # MKVToolNix message -- that would mean the is_mkv check is
        # wrong and MP4 files are being routed through the MKV guard.
        self.assertNotIn("MKVToolNix", vf.load_error)


if __name__ == "__main__":
    unittest.main()
