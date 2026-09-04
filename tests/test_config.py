"""
Tests for core/config.py. Uses a temp CONFIG_PATH per test (via
monkeypatching the module attribute) rather than touching the real
project's settings.ini.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import core.config as config


class TestConfig(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"

    def tearDown(self):
        config.CONFIG_PATH = self._original_config_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_basic_round_trip(self):
        config.set_setting("tmdb", "api_key", "abc123")
        self.assertEqual(config.get_setting("tmdb", "api_key"), "abc123")

    def test_missing_key_returns_default(self):
        self.assertEqual(config.get_setting("nonexistent", "key", "fallback"), "fallback")

    def test_missing_key_default_is_empty_string(self):
        self.assertEqual(config.get_setting("nonexistent", "key"), "")

    def test_percent_sign_in_value_does_not_crash(self):
        # Regression test: configparser's DEFAULT interpolation treats
        # '%' as special ('%(name)s' substitution syntax), which made
        # saving ANY filename pattern (all of which contain '%field%'
        # placeholders) crash outright with "invalid interpolation
        # syntax" the first time this was tried for real. Fixed by
        # constructing ConfigParser with interpolation=None.
        config.set_setting("misc", "test_value", "100% done")
        self.assertEqual(config.get_setting("misc", "test_value"), "100% done")

    def test_filename_pattern_shaped_value_round_trips(self):
        pattern = "%show_title% - S%season_number%E%episode_number% - %title%"
        config.set_setting("filename_patterns", "history", pattern)
        self.assertEqual(config.get_setting("filename_patterns", "history"), pattern)

    def test_value_with_percent_and_parens_does_not_crash(self):
        # A doubled '%%' has its own special meaning in configparser's
        # interpolation syntax even when escaped correctly elsewhere --
        # confirm interpolation=None makes this a complete non-issue.
        config.set_setting("misc", "test_value", "%(not_a_var)s and 50%% done")
        self.assertEqual(
            config.get_setting("misc", "test_value"), "%(not_a_var)s and 50%% done"
        )

    def test_updating_existing_key_overwrites(self):
        config.set_setting("section", "key", "first")
        config.set_setting("section", "key", "second")
        self.assertEqual(config.get_setting("section", "key"), "second")

    def test_multiple_sections_independent(self):
        config.set_setting("tmdb", "api_key", "tmdb-key")
        config.set_setting("opensubtitles", "api_key", "os-key")
        self.assertEqual(config.get_setting("tmdb", "api_key"), "tmdb-key")
        self.assertEqual(config.get_setting("opensubtitles", "api_key"), "os-key")

    def test_config_file_actually_created_on_disk(self):
        config.set_setting("section", "key", "value")
        self.assertTrue(config.CONFIG_PATH.exists())

    def test_no_file_before_first_write(self):
        self.assertFalse(config.CONFIG_PATH.exists())
        config.get_setting("section", "key", "default")  # a read shouldn't create the file
        self.assertFalse(config.CONFIG_PATH.exists())


if __name__ == "__main__":
    unittest.main()
