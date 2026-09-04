"""
Tests for core/external_tools.py. Fully runnable here -- shutil.which()
needs no network or GUI, and this sandbox conveniently has a real mixed
environment to test against: ffmpeg genuinely installed, MKVToolNix
genuinely not, which exercises both the "found" and "missing" paths for
real rather than needing to fake either.
"""

import unittest
from pathlib import Path

from core.external_tools import (
    ToolInfo, is_tool_available, missing_tools, FFMPEG, MKVTOOLNIX,
    get_executable_path, get_tool_override, set_tool_override,
    is_executable_available,
)


class TestExternalTools(unittest.TestCase):
    def test_ffmpeg_detected_as_available(self):
        # This sandbox has ffmpeg installed -- a real positive case,
        # not a mock.
        self.assertTrue(is_tool_available(FFMPEG))

    def test_fake_tool_detected_as_unavailable(self):
        fake = ToolInfo(
            name="Fake", executables=["definitely_not_a_real_binary_xyz123"],
            download_url="", used_for="test",
        )
        self.assertFalse(is_tool_available(fake))

    def test_partial_install_counts_as_unavailable(self):
        # One real executable (ffmpeg) + one fake one -- the whole tool
        # must be flagged missing, not silently treated as OK because
        # part of it happens to be present.
        partial = ToolInfo(
            name="Partial", executables=["ffmpeg", "definitely_not_a_real_binary_xyz123"],
            download_url="", used_for="test",
        )
        self.assertFalse(is_tool_available(partial))

    def test_missing_tools_includes_a_genuinely_absent_tool(self):
        # MKVToolNix is genuinely not installed in this sandbox --
        # confirms missing_tools() surfaces a real gap, not just that
        # the function runs without error.
        names = [t.name for t in missing_tools()]
        self.assertIn("MKVToolNix", names)

    def test_missing_tools_excludes_a_genuinely_present_tool(self):
        names = [t.name for t in missing_tools()]
        self.assertNotIn("ffmpeg", names)


class TestToolOverrides(unittest.TestCase):
    """Uses a temp CONFIG_PATH (same approach as test_config.py) so
    these don't touch the real project's settings.ini.
    """

    def setUp(self):
        import shutil, tempfile
        import core.config as config
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"
        self.real_ffmpeg = shutil.which("ffmpeg")  # genuinely exists in this sandbox

    def tearDown(self):
        import shutil
        import core.config as config
        config.CONFIG_PATH = self._original_config_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_override_resolves_to_bare_command_name(self):
        self.assertEqual(get_executable_path("mkvpropedit"), "mkvpropedit")

    def test_override_takes_priority_in_resolved_path(self):
        set_tool_override("mkvpropedit", "/some/custom/path/mkvpropedit.exe")
        self.assertEqual(get_executable_path("mkvpropedit"), "/some/custom/path/mkvpropedit.exe")

    def test_stale_override_reports_unavailable_not_a_false_positive(self):
        set_tool_override("mkvpropedit", "/nonexistent/fake/mkvpropedit.exe")
        self.assertFalse(is_executable_available("mkvpropedit"))

    def test_override_to_a_real_existing_file_reports_available(self):
        set_tool_override("mkvpropedit", self.real_ffmpeg)  # any real file works for this check
        self.assertTrue(is_executable_available("mkvpropedit"))

    def test_clearing_override_falls_back_to_path_lookup(self):
        set_tool_override("mkvpropedit", "/some/path")
        set_tool_override("mkvpropedit", "")  # clear
        self.assertEqual(get_tool_override("mkvpropedit"), "")
        self.assertEqual(get_executable_path("mkvpropedit"), "mkvpropedit")

    def test_unknown_executable_name_rejected(self):
        with self.assertRaises(ValueError):
            set_tool_override("not_a_real_executable", "/some/path")

    def test_valid_override_makes_previously_missing_tool_available(self):
        # End-to-end: MKVToolNix is genuinely absent in this sandbox --
        # confirm a correct override for ALL of its executables actually
        # resolves the whole ToolInfo as available. Iterates
        # MKVTOOLNIX.executables directly (rather than hardcoding a
        # count) specifically so this test can't silently go stale
        # again the way it did when mkvextract was added as a third
        # required executable -- it did, and this test failed exactly
        # as it should have until updated to match.
        self.assertFalse(is_tool_available(MKVTOOLNIX))
        for exe in MKVTOOLNIX.executables:
            set_tool_override(exe, self.real_ffmpeg)
        self.assertTrue(is_tool_available(MKVTOOLNIX))

    def test_override_on_only_one_of_two_executables_still_reports_missing(self):
        # Partial override -- same "partial install" principle as
        # test_partial_install_counts_as_unavailable, but via overrides
        # instead of PATH.
        set_tool_override("mkvpropedit", self.real_ffmpeg)
        # mkvmerge left unconfigured AND genuinely absent from PATH here
        self.assertFalse(is_tool_available(MKVTOOLNIX))


class TestBundledToolsDir(unittest.TestCase):
    """New capability, promoted from the mp3 project's tool_locator.py
    pattern: a copy in tools/ next to the exe should be found and used
    even with no override configured and nothing on PATH -- this
    project had no such tier before."""

    def setUp(self):
        import shutil, tempfile
        import core.config as config
        import core.external_tools as external_tools
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"

        self.tools_dir = Path(self.tmpdir) / "tools"
        self.tools_dir.mkdir()
        self.bundled_exe = self.tools_dir / "mkvpropedit.exe"
        self.bundled_exe.write_bytes(b"")
        self._original_bundled_dir_fn = external_tools._bundled_tools_dir
        external_tools._bundled_tools_dir = lambda: self.tools_dir

    def tearDown(self):
        import shutil
        import core.config as config
        import core.external_tools as external_tools
        config.CONFIG_PATH = self._original_config_path
        external_tools._bundled_tools_dir = self._original_bundled_dir_fn
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_bundled_copy_found_with_no_override_and_not_on_path(self):
        # mkvpropedit is genuinely not on PATH in this sandbox (per the
        # module docstring's own note that MKVToolNix isn't installed
        # here) -- so this only passes if the bundled tools/ tier is
        # genuinely being consulted, not falling through to a PATH hit.
        self.assertEqual(get_executable_path("mkvpropedit"), str(self.bundled_exe))
        self.assertTrue(is_executable_available("mkvpropedit"))

    def test_override_still_wins_over_bundled_copy(self):
        set_tool_override("mkvpropedit", str(self.bundled_exe))
        # different, but also real/existing -- confirms override takes
        # priority even when a bundled copy is also present
        other_real_file = Path(self.tmpdir) / "settings.ini"
        other_real_file.write_bytes(b"")
        set_tool_override("mkvpropedit", str(other_real_file))
        self.assertEqual(get_executable_path("mkvpropedit"), str(other_real_file))


if __name__ == "__main__":
    unittest.main()
