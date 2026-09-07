"""
Tests that VideoFile.save() explains a real OSError clearly rather than
surfacing a raw exception string -- specifically, that it routes
through redactor_common.core.save_errors.describe_save_error(), which
recognizes Windows' classic MAX_PATH (260-char) limit and explains that
retrying the same save can't help without moving the file first.

A cross-repo redactor_common-adoption review found this project had no
path-too-long protection anywhere, unlike epub (where the module this
was generalized from originated).
"""

import unittest
import unittest.mock as mock
from pathlib import Path

from core.video_file import VideoFile
from core.video_metadata import VideoMetadata, ContentType


class TestSaveErrorMessages(unittest.TestCase):
    def test_path_too_long_oserror_gets_a_clear_explanation(self):
        class _FakeWinError(OSError):
            winerror = 206  # ERROR_FILENAME_EXCED_RANGE

        def _raise(*_args, **_kwargs):
            raise _FakeWinError("[WinError 206] The filename or extension is too long")

        with mock.patch("core.video_file.write_mp4_metadata", side_effect=_raise):
            vf = VideoFile(path=Path("/fake/episode.mp4"))
            vf.metadata = VideoMetadata(title="Something", content_type=ContentType.MOVIE)
            vf.dirty = True
            vf.save()

        self.assertIn("260-character limit", vf.save_error)
        self.assertIn("shorten the folder path", vf.save_error)

    def test_an_ordinary_oserror_still_falls_back_to_its_own_message(self):
        def _raise(*_args, **_kwargs):
            raise OSError("disk is full")

        with mock.patch("core.video_file.write_mp4_metadata", side_effect=_raise):
            vf = VideoFile(path=Path("/fake/episode.mp4"))
            vf.metadata = VideoMetadata(title="Something", content_type=ContentType.MOVIE)
            vf.dirty = True
            vf.save()

        self.assertIn("disk is full", vf.save_error)


if __name__ == "__main__":
    unittest.main()
