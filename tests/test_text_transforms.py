"""Tests for core/text_transforms.py -- pure functions, fully runnable."""

import unittest

from core.text_transforms import (
    apply_case_conversion, apply_search_replace,
    generate_auto_number, apply_auto_number_to_text_field,
)


class TestCaseConversion(unittest.TestCase):
    def test_upper(self):
        self.assertEqual(apply_case_conversion("the lord of the rings", "upper"), "THE LORD OF THE RINGS")

    def test_lower(self):
        self.assertEqual(apply_case_conversion("THE LORD OF THE RINGS", "lower"), "the lord of the rings")

    def test_sentence(self):
        self.assertEqual(apply_case_conversion("THE LORD OF THE RINGS", "sentence"), "The lord of the rings")

    def test_title_basic(self):
        self.assertEqual(apply_case_conversion("the lord of the rings", "title"), "The Lord of the Rings")

    def test_title_first_word_always_capitalized_even_if_small_word(self):
        self.assertEqual(apply_case_conversion("a tale of two cities", "title"), "A Tale of Two Cities")

    def test_title_last_word_always_capitalized_even_if_small_word(self):
        self.assertEqual(apply_case_conversion("what is love for", "title"), "What Is Love For")

    def test_title_does_not_mangle_apostrophes(self):
        # The specific, well-known Python str.title() bug this avoids:
        # "don't stop believing".title() == "Don'T Stop Believing"
        self.assertEqual(apply_case_conversion("don't stop believing", "title"), "Don't Stop Believing")

    def test_title_capitalizes_word_after_colon(self):
        self.assertEqual(apply_case_conversion("star wars: a new hope", "title"), "Star Wars: A New Hope")

    def test_title_capitalizes_word_after_colon_no_space_variant(self):
        self.assertEqual(apply_case_conversion("mission: impossible", "title"), "Mission: Impossible")

    def test_title_capitalizes_word_after_dash(self):
        self.assertEqual(apply_case_conversion("movie - the sequel", "title"), "Movie - The Sequel")

    def test_title_preserves_multiple_spaces(self):
        self.assertEqual(apply_case_conversion("hello  world", "title"), "Hello  World")

    def test_title_word_with_no_alphabetic_characters(self):
        self.assertEqual(apply_case_conversion("vol. 1 - 123", "title"), "Vol. 1 - 123")

    def test_empty_string_unchanged(self):
        self.assertEqual(apply_case_conversion("", "title"), "")

    def test_whitespace_only_unchanged(self):
        self.assertEqual(apply_case_conversion("   ", "upper"), "   ")

    def test_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            apply_case_conversion("test", "not_a_real_mode")

    def test_sentence_case_preserves_leading_whitespace(self):
        self.assertEqual(apply_case_conversion("  hello world", "sentence"), "  Hello world")


class TestSearchReplace(unittest.TestCase):
    def test_basic_replace(self):
        self.assertEqual(apply_search_replace("Episode 1: Pilot", "Episode", "Ep."), "Ep. 1: Pilot")

    def test_case_sensitive_by_default_does_not_match_different_case(self):
        self.assertEqual(
            apply_search_replace("EPISODE 1: Pilot", "episode", "Ep.", case_sensitive=True),
            "EPISODE 1: Pilot",
        )

    def test_case_insensitive_matches_different_case(self):
        self.assertEqual(
            apply_search_replace("EPISODE 1: Pilot", "episode", "Ep.", case_sensitive=False),
            "Ep. 1: Pilot",
        )

    def test_regex_special_characters_treated_as_literal(self):
        self.assertEqual(apply_search_replace("a.b.c", ".", "_"), "a_b_c")

    def test_parentheses_treated_as_literal(self):
        self.assertEqual(apply_search_replace("cost (was 10)", "(", "["), "cost [was 10)")

    def test_empty_search_returns_unchanged(self):
        self.assertEqual(apply_search_replace("hello", "", "X"), "hello")

    def test_replace_all_occurrences(self):
        self.assertEqual(apply_search_replace("aaa", "a", "b"), "bbb")

    def test_case_insensitive_replace_all_occurrences(self):
        self.assertEqual(apply_search_replace("AaAaA", "a", "x", case_sensitive=False), "xxxxx")

    def test_search_not_found_returns_unchanged(self):
        self.assertEqual(apply_search_replace("hello world", "xyz", "abc"), "hello world")


class TestAutoNumbering(unittest.TestCase):
    def test_basic_sequence_with_padding(self):
        self.assertEqual(generate_auto_number(0, start=1, increment=1, padding=2), "01")
        self.assertEqual(generate_auto_number(1, start=1, increment=1, padding=2), "02")
        self.assertEqual(generate_auto_number(9, start=1, increment=1, padding=2), "10")

    def test_no_padding(self):
        self.assertEqual(generate_auto_number(0, start=5, increment=10, padding=0), "5")
        self.assertEqual(generate_auto_number(2, start=5, increment=10, padding=0), "25")

    def test_negative_start_with_padding_keeps_sign_correct(self):
        # str(-5).zfill(3) == "-05", not the wrong "0-5"
        self.assertEqual(generate_auto_number(0, start=-5, increment=1, padding=3), "-05")

    def test_custom_increment(self):
        self.assertEqual(generate_auto_number(3, start=0, increment=5, padding=0), "15")

    def test_padding_wider_than_needed(self):
        self.assertEqual(generate_auto_number(0, start=1, increment=1, padding=5), "00001")

    def test_apply_to_text_field_with_existing_value(self):
        self.assertEqual(apply_auto_number_to_text_field("Pilot", "01", " - "), "01 - Pilot")

    def test_apply_to_text_field_with_empty_value_no_dangling_separator(self):
        self.assertEqual(apply_auto_number_to_text_field("", "01", " - "), "01")

    def test_apply_to_text_field_custom_separator(self):
        self.assertEqual(apply_auto_number_to_text_field("Pilot", "01", ". "), "01. Pilot")


if __name__ == "__main__":
    unittest.main()
