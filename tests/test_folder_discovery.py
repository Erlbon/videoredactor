"""
Tests for core/video_file.py's discover_video_files()/has_subfolders().
Fully runnable here -- pure filesystem operations, no external tools.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from core.video_file import discover_video_files, has_subfolders


class TestDiscoverVideoFiles(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _touch(self, relative_path: str) -> Path:
        path = self.tmpdir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
        return path

    def test_non_recursive_finds_only_top_level_files(self):
        self._touch("top1.mp4")
        self._touch("top2.mkv")
        self._touch("subfolder/nested.mp4")

        result = discover_video_files(self.tmpdir, recursive=False)
        names = {p.name for p in result}
        self.assertEqual(names, {"top1.mp4", "top2.mkv"})

    def test_recursive_finds_nested_files_too(self):
        self._touch("top1.mp4")
        self._touch("subfolder/nested1.mp4")
        self._touch("subfolder/deeper/nested2.mkv")

        result = discover_video_files(self.tmpdir, recursive=True)
        names = {p.name for p in result}
        self.assertEqual(names, {"top1.mp4", "nested1.mp4", "nested2.mkv"})

    def test_recursive_is_opt_in_default_stays_non_recursive(self):
        self._touch("top1.mp4")
        self._touch("subfolder/nested.mp4")

        # No recursive= argument at all -- must match recursive=False
        result = discover_video_files(self.tmpdir)
        names = {p.name for p in result}
        self.assertEqual(names, {"top1.mp4"})

    def test_non_video_files_excluded_in_both_modes(self):
        self._touch("video.mp4")
        self._touch("notes.txt")
        self._touch("subfolder/video2.mkv")
        self._touch("subfolder/readme.md")

        non_recursive_names = {p.name for p in discover_video_files(self.tmpdir, recursive=False)}
        recursive_names = {p.name for p in discover_video_files(self.tmpdir, recursive=True)}
        self.assertEqual(non_recursive_names, {"video.mp4"})
        self.assertEqual(recursive_names, {"video.mp4", "video2.mkv"})

    def test_empty_folder_returns_empty_list(self):
        self.assertEqual(discover_video_files(self.tmpdir, recursive=True), [])

    def test_nonexistent_folder_returns_empty_list_not_error(self):
        fake_path = self.tmpdir / "does_not_exist"
        self.assertEqual(discover_video_files(fake_path, recursive=True), [])

    def test_results_sorted(self):
        self._touch("zebra.mp4")
        self._touch("apple.mp4")
        self._touch("subfolder/mango.mkv")

        result = discover_video_files(self.tmpdir, recursive=True)
        names = [p.name for p in result]
        self.assertEqual(names, sorted(names))


class TestHasSubfolders(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_flat_folder_has_no_subfolders(self):
        (self.tmpdir / "file.mp4").write_bytes(b"fake")
        self.assertFalse(has_subfolders(self.tmpdir))

    def test_folder_with_subfolder_detected(self):
        (self.tmpdir / "subfolder").mkdir()
        self.assertTrue(has_subfolders(self.tmpdir))

    def test_empty_folder_has_no_subfolders(self):
        self.assertFalse(has_subfolders(self.tmpdir))

    def test_subfolder_detected_even_if_empty_itself(self):
        (self.tmpdir / "empty_subfolder").mkdir()
        self.assertTrue(has_subfolders(self.tmpdir))

    def test_nonexistent_folder_returns_false_not_error(self):
        fake_path = self.tmpdir / "does_not_exist"
        self.assertFalse(has_subfolders(fake_path))

    def test_files_only_do_not_count_as_subfolders(self):
        (self.tmpdir / "a.mp4").write_bytes(b"fake")
        (self.tmpdir / "b.mkv").write_bytes(b"fake")
        self.assertFalse(has_subfolders(self.tmpdir))


if __name__ == "__main__":
    unittest.main()
