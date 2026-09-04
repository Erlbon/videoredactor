"""Tests for core/controlled_vocab.py -- pure functions, fully runnable."""

import unittest

from core.controlled_vocab import (
    parse_multi_value, serialize_multi_value,
    DEFAULT_GENRE_OPTIONS, DEFAULT_LANGUAGE_OPTIONS,
)


class TestControlledVocab(unittest.TestCase):
    def test_parse_multi_value_basic(self):
        self.assertEqual(parse_multi_value("Action, Comedy"), {"Action", "Comedy"})

    def test_parse_multi_value_empty_string(self):
        self.assertEqual(parse_multi_value(""), set())

    def test_parse_multi_value_none(self):
        self.assertEqual(parse_multi_value(None), set())

    def test_parse_multi_value_strips_whitespace(self):
        self.assertEqual(parse_multi_value("  Action ,  Comedy  "), {"Action", "Comedy"})

    def test_parse_multi_value_ignores_empty_entries(self):
        # Trailing/double commas shouldn't produce a phantom empty-string entry
        self.assertEqual(parse_multi_value("Action, , Comedy,"), {"Action", "Comedy"})

    def test_serialize_uses_canonical_order_not_input_order(self):
        selected = parse_multi_value("Drama, Action, Comedy")
        result = serialize_multi_value(selected, DEFAULT_GENRE_OPTIONS)
        self.assertEqual(result, "Action, Comedy, Drama")

    def test_serialize_is_stable_regardless_of_set_iteration_order(self):
        # Sets have no guaranteed iteration order -- confirm two
        # differently-constructed sets with the same members serialize
        # identically.
        a = serialize_multi_value({"Comedy", "Action"}, DEFAULT_GENRE_OPTIONS)
        b = serialize_multi_value({"Action", "Comedy"}, DEFAULT_GENRE_OPTIONS)
        self.assertEqual(a, b)

    def test_serialize_preserves_unknown_values(self):
        selected = {"Action", "SomeWeirdGenreFromTMDB"}
        result = serialize_multi_value(selected, DEFAULT_GENRE_OPTIONS)
        self.assertIn("Action", result)
        self.assertIn("SomeWeirdGenreFromTMDB", result)

    def test_round_trip_preserves_all_values(self):
        original = "Action, Comedy, Drama"
        parsed = parse_multi_value(original)
        result = serialize_multi_value(parsed, DEFAULT_GENRE_OPTIONS)
        self.assertEqual(parse_multi_value(result), parsed)

    def test_empty_selection_serializes_to_empty_string(self):
        self.assertEqual(serialize_multi_value(set(), DEFAULT_GENRE_OPTIONS), "")

    def test_genre_and_language_options_have_no_duplicates(self):
        self.assertEqual(len(DEFAULT_GENRE_OPTIONS), len(set(DEFAULT_GENRE_OPTIONS)))
        self.assertEqual(len(DEFAULT_LANGUAGE_OPTIONS), len(set(DEFAULT_LANGUAGE_OPTIONS)))


if __name__ == "__main__":
    unittest.main()
