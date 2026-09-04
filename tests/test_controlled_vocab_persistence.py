"""
Tests for core/controlled_vocab.py's get/add/remove option-list
functions -- the persisted, user-editable genre/language lists behind
the new Settings "Add/Remove Genres.../Languages..." dialogs. Uses a
temp CONFIG_PATH (same approach as tests/test_config.py) so these
don't touch the real project's settings.ini.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

import core.config as config
from core.controlled_vocab import (
    get_genre_options, get_language_options,
    add_genre_option, remove_genre_option,
    add_language_option, remove_language_option,
    DEFAULT_GENRE_OPTIONS, DEFAULT_LANGUAGE_OPTIONS,
)


class TestControlledVocabPersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"

    def tearDown(self):
        config.CONFIG_PATH = self._original_config_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_call_seeds_with_defaults(self):
        self.assertEqual(get_genre_options(), DEFAULT_GENRE_OPTIONS)
        self.assertEqual(get_language_options(), DEFAULT_LANGUAGE_OPTIONS)

    def test_add_genre_persists(self):
        add_genre_option("Bollywood")
        self.assertIn("Bollywood", get_genre_options())

    def test_add_genre_survives_a_fresh_read(self):
        add_genre_option("Bollywood")
        # A fresh call simulates a new session reading settings.ini again
        self.assertIn("Bollywood", get_genre_options())

    def test_remove_genre_persists(self):
        remove_genre_option("Horror")
        self.assertNotIn("Horror", get_genre_options())

    def test_remove_genre_does_not_affect_other_genres(self):
        before = set(get_genre_options())
        remove_genre_option("Horror")
        after = set(get_genre_options())
        self.assertEqual(before - after, {"Horror"})

    def test_adding_duplicate_genre_is_a_no_op(self):
        add_genre_option("Bollywood")
        count_after_first = len(get_genre_options())
        add_genre_option("Bollywood")
        count_after_second = len(get_genre_options())
        self.assertEqual(count_after_first, count_after_second)

    def test_removing_nonexistent_genre_is_a_safe_no_op(self):
        before = get_genre_options()
        remove_genre_option("NotARealGenre")
        after = get_genre_options()
        self.assertEqual(before, after)

    def test_blank_genre_name_not_added(self):
        before = get_genre_options()
        add_genre_option("   ")
        after = get_genre_options()
        self.assertEqual(before, after)

    def test_genre_name_is_stripped_before_adding(self):
        add_genre_option("  Bollywood  ")
        self.assertIn("Bollywood", get_genre_options())
        self.assertNotIn("  Bollywood  ", get_genre_options())

    def test_languages_independent_of_genres(self):
        add_language_option("Klingon")
        self.assertIn("Klingon", get_language_options())
        self.assertNotIn("Klingon", get_genre_options())

    def test_remove_language_persists(self):
        remove_language_option("English")
        self.assertNotIn("English", get_language_options())

    def test_default_lists_have_no_duplicates(self):
        self.assertEqual(len(DEFAULT_GENRE_OPTIONS), len(set(DEFAULT_GENRE_OPTIONS)))
        self.assertEqual(len(DEFAULT_LANGUAGE_OPTIONS), len(set(DEFAULT_LANGUAGE_OPTIONS)))

    def test_genre_name_with_special_characters_survives_round_trip(self):
        # Confirms the \x1f delimiter choice doesn't break on genre
        # names containing characters that would corrupt a naive
        # comma-delimited list.
        add_genre_option("Sci-Fi, Fantasy & Horror")
        self.assertIn("Sci-Fi, Fantasy & Horror", get_genre_options())


if __name__ == "__main__":
    unittest.main()
