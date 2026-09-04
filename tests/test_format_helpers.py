"""Tests for core/format_helpers.py -- pure functions, fully runnable."""

import unittest

from core.format_helpers import format_duration, format_file_size


class TestFormatDuration(unittest.TestCase):
    def test_under_an_hour_omits_hours_component(self):
        self.assertEqual(format_duration(303), "5:03")

    def test_over_an_hour_includes_hours(self):
        self.assertEqual(format_duration(5425), "1:30:25")

    def test_none_returns_empty_string(self):
        self.assertEqual(format_duration(None), "")

    def test_negative_returns_empty_string(self):
        self.assertEqual(format_duration(-5), "")

    def test_zero_is_not_empty(self):
        # Zero-length is a real (if unusual) value, distinct from "unknown"
        self.assertEqual(format_duration(0), "0:00")

    def test_seconds_are_zero_padded(self):
        self.assertEqual(format_duration(65), "1:05")

    def test_minutes_zero_padded_when_hours_present(self):
        self.assertEqual(format_duration(3665), "1:01:05")

    def test_fractional_seconds_truncated_not_rounded(self):
        self.assertEqual(format_duration(59.9), "0:59")


class TestFormatFileSize(unittest.TestCase):
    def test_bytes_no_decimal(self):
        self.assertEqual(format_file_size(500), "500 B")

    def test_kilobytes(self):
        self.assertEqual(format_file_size(2048), "2.0 KB")

    def test_megabytes(self):
        self.assertEqual(format_file_size(1258291), "1.2 MB")

    def test_gigabytes(self):
        self.assertEqual(format_file_size(1610612736), "1.5 GB")

    def test_none_returns_empty_string(self):
        self.assertEqual(format_file_size(None), "")

    def test_negative_returns_empty_string(self):
        self.assertEqual(format_file_size(-100), "")

    def test_zero_bytes(self):
        self.assertEqual(format_file_size(0), "0 B")

    def test_boundary_exactly_1024_bytes_rolls_to_kb(self):
        self.assertEqual(format_file_size(1024), "1.0 KB")

    def test_boundary_just_under_1024_stays_bytes(self):
        self.assertEqual(format_file_size(1023), "1023 B")


if __name__ == "__main__":
    unittest.main()
