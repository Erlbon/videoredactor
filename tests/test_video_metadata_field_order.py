"""
Tests for core/video_metadata.py's field lists and ordering.

The EDITABLE_FIELDS-ordering test here is regression coverage for a
real bug: when content_type was moved to the front of UNIVERSAL_FIELDS
(so the bulk-edit panel shows it first, as the field users need to
fill in before anything else makes sense), EDITABLE_FIELDS -- a
SEPARATE list used by VideoFile._verify_write()'s mismatch-check order
and now also gui/placeholder_reference.py's display order -- was never
updated to match, silently keeping the old title-first order. Found
while building the placeholder reference list, whose whole point is to
mirror the panel's own field order; the code comment claiming it
already did so turned out to be describing something that wasn't true
yet.
"""

import unittest

from core.video_metadata import (
    UNIVERSAL_FIELDS, EDITABLE_FIELDS, TYPE_SPECIFIC_FIELDS,
    ContentType, fields_for_content_type,
)


class TestFieldListConsistency(unittest.TestCase):
    def test_editable_fields_starts_with_content_type(self):
        self.assertEqual(EDITABLE_FIELDS[0], "content_type")

    def test_editable_fields_first_entry_matches_universal_fields_first_entry(self):
        # The actual regression: these two lists' first entries used to
        # disagree (UNIVERSAL_FIELDS led with content_type,
        # EDITABLE_FIELDS still led with title) even though both are
        # meant to reflect the same "content_type is filled in first"
        # design decision.
        self.assertEqual(EDITABLE_FIELDS[0], UNIVERSAL_FIELDS[0])

    def test_editable_fields_contains_every_universal_field(self):
        for field in UNIVERSAL_FIELDS:
            self.assertIn(field, EDITABLE_FIELDS)

    def test_editable_fields_contains_every_type_specific_field(self):
        for fields in TYPE_SPECIFIC_FIELDS.values():
            for field in fields:
                self.assertIn(field, EDITABLE_FIELDS)

    def test_editable_fields_has_no_duplicates(self):
        self.assertEqual(len(EDITABLE_FIELDS), len(set(EDITABLE_FIELDS)))

    def test_editable_fields_count_unchanged_by_reorder(self):
        # The reorder fix must not have accidentally dropped or
        # duplicated a field -- same 22 fields as before, just reordered.
        self.assertEqual(len(EDITABLE_FIELDS), 22)

    def test_fields_for_content_type_still_leads_with_content_type(self):
        # fields_for_content_type() prepends UNIVERSAL_FIELDS, so this
        # should hold regardless of which content type is asked for.
        for ct in (ContentType.MOVIE, ContentType.TV, ContentType.MUSIC_VIDEO):
            self.assertEqual(fields_for_content_type(ct)[0], "content_type")


if __name__ == "__main__":
    unittest.main()
