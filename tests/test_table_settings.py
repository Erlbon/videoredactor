"""Tests for core/table_settings.py -- pure functions, fully runnable."""

import unittest

from core.table_settings import merge_column_order, is_column_visible, sanitize_hidden_fields


class TestMergeColumnOrder(unittest.TestCase):
    def test_basic_reorder(self):
        current = ["filename", "status", "title", "genre_tags"]
        persisted = ["title", "filename"]
        self.assertEqual(
            merge_column_order(persisted, current),
            ["title", "filename", "status", "genre_tags"],
        )

    def test_new_field_appended_in_original_relative_order(self):
        current = ["filename", "status", "title", "director", "cast"]
        persisted = ["title", "filename"]
        result = merge_column_order(persisted, current)
        # title/filename per preference, then director/cast in their
        # original relative order (director before cast, matching
        # `current`'s order) -- not shuffled or alphabetized.
        self.assertEqual(result, ["title", "filename", "status", "director", "cast"])

    def test_stale_persisted_field_silently_dropped(self):
        current = ["filename", "title"]
        persisted = ["title", "director", "filename"]  # director no longer present
        self.assertEqual(merge_column_order(persisted, current), ["title", "filename"])

    def test_empty_persisted_order_keeps_original_order(self):
        current = ["filename", "status", "title"]
        self.assertEqual(merge_column_order([], current), current)

    def test_no_fields_lost_or_duplicated(self):
        current = ["filename", "status", "title", "genre_tags", "language", "director"]
        persisted = ["director", "genre_tags", "nonexistent_field"]
        result = merge_column_order(persisted, current)
        self.assertEqual(sorted(result), sorted(current))
        self.assertEqual(len(result), len(set(result)))


class TestColumnVisibility(unittest.TestCase):
    def test_filename_always_visible_even_if_in_hidden_set(self):
        self.assertTrue(is_column_visible("filename", {"filename", "title"}))

    def test_hidden_field_is_not_visible(self):
        self.assertFalse(is_column_visible("title", {"title"}))

    def test_unhidden_field_is_visible(self):
        self.assertTrue(is_column_visible("genre_tags", {"title"}))

    def test_empty_hidden_set_means_everything_visible(self):
        self.assertTrue(is_column_visible("title", set()))


class TestSanitizeHiddenFields(unittest.TestCase):
    def test_removes_filename(self):
        self.assertEqual(
            sanitize_hidden_fields({"filename", "title", "status"}),
            {"title", "status"},
        )

    def test_no_op_when_filename_not_present(self):
        self.assertEqual(sanitize_hidden_fields({"title", "status"}), {"title", "status"})

    def test_empty_set_stays_empty(self):
        self.assertEqual(sanitize_hidden_fields(set()), set())


class TestPanelFieldFiltering(unittest.TestCase):
    """Regression coverage for the "hiding a column should also hide it
    from the bulk-edit panel" feature -- gui/tag_panel.py's
    set_content_type_filter() filters its field list through
    is_column_visible() the exact same way the file table already
    does, using the SAME [table] hidden_columns setting rather than a
    separate panel-specific one. TagPanel itself can't be tested here
    (needs PyQt6), but the actual filtering logic it calls is pure
    core.table_settings/core.video_metadata code -- these tests
    exercise that combination directly, mirroring tag_panel.py's own
    usage exactly.
    """

    def test_hidden_field_excluded_from_content_type_field_list(self):
        from core.video_metadata import fields_for_content_type, ContentType

        hidden = {"composer"}
        field_names = fields_for_content_type(ContentType.MUSIC_VIDEO)
        self.assertIn("composer", field_names)  # sanity check it was there to begin with

        filtered = [f for f in field_names if is_column_visible(f, hidden)]
        self.assertNotIn("composer", filtered)

    def test_unhidden_fields_all_survive_filtering(self):
        from core.video_metadata import fields_for_content_type, ContentType

        hidden = {"composer"}
        field_names = fields_for_content_type(ContentType.MUSIC_VIDEO)
        filtered = [f for f in field_names if is_column_visible(f, hidden)]

        expected_remaining = [f for f in field_names if f != "composer"]
        self.assertEqual(filtered, expected_remaining)

    def test_empty_hidden_set_leaves_field_list_unchanged(self):
        from core.video_metadata import fields_for_content_type, ContentType

        field_names = fields_for_content_type(ContentType.MOVIE)
        filtered = [f for f in field_names if is_column_visible(f, set())]
        self.assertEqual(filtered, field_names)

    def test_multiple_hidden_fields_all_excluded(self):
        from core.video_metadata import fields_for_content_type, ContentType

        hidden = {"director", "writer", "studio"}
        field_names = fields_for_content_type(ContentType.MOVIE)
        filtered = [f for f in field_names if is_column_visible(f, hidden)]

        for f in hidden:
            self.assertNotIn(f, filtered)

    def test_hiding_content_type_itself_is_respected(self):
        # content_type is not in PROTECTED_COLUMNS (only filename is) --
        # confirm the panel would genuinely let a user hide it too if
        # they choose to, no special-casing beyond what already exists.
        from core.video_metadata import UNIVERSAL_FIELDS

        hidden = {"content_type"}
        filtered = [f for f in UNIVERSAL_FIELDS if is_column_visible(f, hidden)]
        self.assertNotIn("content_type", filtered)


if __name__ == "__main__":
    unittest.main()
