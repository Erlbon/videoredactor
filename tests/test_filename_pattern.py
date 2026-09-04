"""Tests for core/filename_pattern.py -- pure string/regex logic, fully runnable."""

import unittest
from pathlib import Path

from core.video_metadata import VideoMetadata, ContentType
from core.filename_pattern import (
    render_filename, parse_filename, validate_filename_stem,
    sanitize_filename_stem, load_pattern_history, save_pattern_to_history,
)


class TestRenderFilename(unittest.TestCase):
    def test_basic_placeholder_substitution(self):
        meta = VideoMetadata(title="The Matrix")
        self.assertEqual(render_filename(meta, "%title%"), "The Matrix")

    def test_multiple_placeholders(self):
        meta = VideoMetadata(show_title="Breaking Bad", season_number=1, episode_number=5, title="Gray Matter")
        result = render_filename(meta, "%show_title% - S%season_number%E%episode_number% - %title%")
        self.assertEqual(result, "Breaking Bad - S1E5 - Gray Matter")

    def test_illegal_characters_stripped(self):
        meta = VideoMetadata(title="Who: What? *Question*")
        result = render_filename(meta, "%title%")
        self.assertNotIn(":", result)
        self.assertNotIn("?", result)
        self.assertNotIn("*", result)

    def test_empty_field_produces_no_placeholder_text(self):
        meta = VideoMetadata(title="")
        result = render_filename(meta, "[%title%]")
        self.assertEqual(result, "[]")

    def test_unrecognized_placeholder_left_literal(self):
        meta = VideoMetadata(title="Test")
        result = render_filename(meta, "%title% - %not_a_real_field%")
        self.assertIn("%not_a_real_field%", result)

    def test_content_type_enum_renders_as_string_value(self):
        meta = VideoMetadata(content_type=ContentType.MOVIE)
        self.assertEqual(render_filename(meta, "%content_type%"), "Movie")

    def test_all_empty_pattern_falls_back_to_untitled(self):
        meta = VideoMetadata(title="")
        self.assertEqual(render_filename(meta, "%title%"), "untitled")


class TestSanitizeFilenameStem(unittest.TestCase):
    def test_collapses_whitespace_runs(self):
        self.assertEqual(sanitize_filename_stem("a    b"), "a b")

    def test_strips_trailing_space_and_dot(self):
        self.assertEqual(sanitize_filename_stem("Name. "), "Name")

    def test_empty_input_becomes_untitled(self):
        self.assertEqual(sanitize_filename_stem(""), "untitled")

    def test_length_capped(self):
        long_name = "a" * 500
        self.assertLessEqual(len(sanitize_filename_stem(long_name)), 200)


class TestParseFilename(unittest.TestCase):
    def test_basic_round_trip(self):
        meta = VideoMetadata(title="The Matrix")
        pattern = "%title%"
        rendered = render_filename(meta, pattern)
        parsed = parse_filename(rendered, pattern)
        self.assertEqual(parsed["title"], "The Matrix")

    def test_multi_field_round_trip(self):
        meta = VideoMetadata(show_title="Breaking Bad", season_number=1, episode_number=5, title="Gray Matter")
        pattern = "%show_title% - S%season_number%E%episode_number% - %title%"
        rendered = render_filename(meta, pattern)
        parsed = parse_filename(rendered, pattern)
        self.assertEqual(parsed["show_title"], "Breaking Bad")
        self.assertEqual(parsed["season_number"], "1")
        self.assertEqual(parsed["episode_number"], "5")
        self.assertEqual(parsed["title"], "Gray Matter")

    def test_double_digit_season_and_episode_boundary(self):
        # Non-greedy capture between S and E must still grab the full
        # multi-digit season number, not stop at the first digit.
        pattern = "S%season_number%E%episode_number%"
        result = parse_filename("S10E25", pattern)
        self.assertEqual(result["season_number"], "10")
        self.assertEqual(result["episode_number"], "25")

    def test_flexible_whitespace_matches_extra_spaces(self):
        pattern = "%title% - %show_title%"
        # Double spaces around the separator, unlike the single-space pattern
        result = parse_filename("Title  -  Show", pattern)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Title")
        self.assertEqual(result["show_title"], "Show")

    def test_non_matching_filename_returns_none(self):
        pattern = "%show_title% - S%season_number%E%episode_number%"
        self.assertIsNone(parse_filename("completely different format", pattern))

    def test_unrecognized_placeholder_treated_as_literal(self):
        # %not_a_real_field% isn't in EDITABLE_FIELDS, so it should be
        # matched as literal text, not captured.
        pattern = "%title% %not_a_real_field%"
        result = parse_filename("Movie %not_a_real_field%", pattern)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "Movie")
        self.assertNotIn("not_a_real_field", result)

    def test_duplicate_placeholder_in_pattern_does_not_crash(self):
        # Python's re module rejects duplicate named groups -- must
        # degrade to None, not raise.
        pattern = "%title% %title%"
        result = parse_filename("A B", pattern)
        self.assertIsNone(result)

    def test_extracted_values_are_stripped(self):
        pattern = "[%title%]"
        result = parse_filename("[ Padded Title ]", pattern)
        self.assertEqual(result["title"], "Padded Title")


class TestValidateFilenameStem(unittest.TestCase):
    def test_valid_name_returns_none(self):
        self.assertIsNone(validate_filename_stem("Normal Filename"))

    def test_empty_string_invalid(self):
        self.assertIsNotNone(validate_filename_stem(""))

    def test_whitespace_only_invalid(self):
        self.assertIsNotNone(validate_filename_stem("   "))

    def test_trailing_dot_invalid(self):
        self.assertIsNotNone(validate_filename_stem("Name."))

    def test_trailing_space_invalid(self):
        self.assertIsNotNone(validate_filename_stem("Name "))

    def test_illegal_character_invalid(self):
        result = validate_filename_stem("Who: What?")
        self.assertIsNotNone(result)
        self.assertIn(":", result)

    def test_reserved_windows_name_invalid(self):
        self.assertIsNotNone(validate_filename_stem("CON"))

    def test_reserved_name_case_insensitive(self):
        self.assertIsNotNone(validate_filename_stem("con"))

    def test_reserved_name_with_extension_still_caught(self):
        # 'CON.txt' -- the reserved-name check applies to the part
        # before the first dot, matching Windows' own actual restriction.
        self.assertIsNotNone(validate_filename_stem("CON.mp4"))

    def test_name_containing_reserved_word_is_fine(self):
        # 'CONTACT' is not reserved just because it starts with 'CON'
        self.assertIsNone(validate_filename_stem("CONTACT"))

    def test_too_long_invalid(self):
        self.assertIsNotNone(validate_filename_stem("a" * 300))


class TestPatternHistory(unittest.TestCase):
    """Uses a temp CONFIG_PATH, same approach as tests/test_config.py,
    since this shares the same underlying settings.ini storage.
    """

    def setUp(self):
        import shutil, tempfile
        import core.config as config
        self.tmpdir = tempfile.mkdtemp()
        self._original_config_path = config.CONFIG_PATH
        config.CONFIG_PATH = Path(self.tmpdir) / "settings.ini"

    def tearDown(self):
        import shutil
        import core.config as config
        config.CONFIG_PATH = self._original_config_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_empty_history_initially(self):
        self.assertEqual(load_pattern_history(), [])

    def test_save_and_load_single_pattern(self):
        save_pattern_to_history("%title%")
        self.assertEqual(load_pattern_history(), ["%title%"])

    def test_comma_containing_pattern_survives_intact(self):
        # The exact case a comma-delimited history list would corrupt --
        # this is why the delimiter is \x1f, not a comma.
        pattern = "%title%, %release_date%"
        save_pattern_to_history(pattern)
        self.assertIn(pattern, load_pattern_history())

    def test_percent_placeholder_pattern_does_not_crash(self):
        # Regression coverage at this module's own level too, not just
        # test_config.py's -- every real pattern this function will
        # ever be asked to save contains '%field%' placeholders.
        pattern = "%show_title% - S%season_number%E%episode_number%"
        save_pattern_to_history(pattern)  # must not raise
        self.assertIn(pattern, load_pattern_history())

    def test_reusing_pattern_moves_to_front_not_duplicated(self):
        save_pattern_to_history("%title%")
        save_pattern_to_history("%show_title%")
        save_pattern_to_history("%title%")
        history = load_pattern_history()
        self.assertEqual(history[0], "%title%")
        self.assertEqual(history.count("%title%"), 1)

    def test_history_capped_at_max(self):
        for i in range(15):
            save_pattern_to_history(f"%title%_{i}")
        history = load_pattern_history()
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0], "%title%_14")  # most recent first


if __name__ == "__main__":
    unittest.main()
