"""
Tests for VideoFile.save()'s verify-after-write step.

Direct response to a real, twice-reported user symptom: a save reports
success (no exception, mkvpropedit returncode 0) but the written fields
are silently absent on the next load. Rather than continuing to guess
at backend-level root causes without a real mutagen/MKVToolNix
environment to test against, save() now re-reads the file immediately
after writing and compares against what was intended -- these tests
confirm that safety net actually works, in both directions: catching a
genuine silent failure, and NOT producing a false positive on a
genuine success.
"""

import unittest
import unittest.mock as mock
import subprocess
from pathlib import Path

from core.video_file import VideoFile
from core.video_metadata import VideoMetadata, ContentType


class TestSaveVerification(unittest.TestCase):
    def test_silent_write_failure_is_caught_not_reported_as_success(self):
        """Simulates the exact reported bug: mkvpropedit reports
        success (returncode 0), but a re-read shows the custom tags
        never actually persisted -- only title did.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout='{"container":{"properties":{"title":"Below Deck Mediterranean"}}}',
                    stderr="",
                )
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(
                title="Below Deck Mediterranean", content_type=ContentType.TV, season_number=11,
            )
            vf.dirty = True
            vf.save()

        self.assertTrue(vf.save_error, "Expected save_error to be set for a silent write failure")
        self.assertIn("content_type", vf.save_error)
        self.assertTrue(vf.dirty, "dirty must stay True when verification fails -- the edit is still unsaved")

    def test_genuine_successful_save_still_reports_success(self):
        """The other direction: a write that actually DOES persist
        correctly must not be flagged as a false-positive failure.
        Captures what write_mkv_metadata really writes to its temp XML
        file and serves that same content back on the read call --
        a genuine round trip, not two independently-scripted mocks
        that happen to agree by construction.
        """
        persisted = {"xml": None, "title": None}

        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                for i, arg in enumerate(args):
                    if arg == "--set" and i + 1 < len(args) and args[i + 1].startswith("title="):
                        persisted["title"] = args[i + 1][len("title="):]
                    if arg == "--tags" and i + 1 < len(args):
                        target_path = args[i + 1].split(":", 1)[1]
                        with open(target_path, "r", encoding="utf-8") as f:
                            persisted["xml"] = f.read()
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                title = persisted["title"] or ""
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout='{"container":{"properties":{"title":"' + title + '"}}}',
                    stderr="",
                )
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=persisted["xml"] or "", stderr="",
                )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(
                title="Below Deck Mediterranean", content_type=ContentType.TV, season_number=11,
            )
            vf.dirty = True
            vf.save()

        self.assertEqual(vf.save_error, "")
        self.assertFalse(vf.dirty)

    def test_verify_only_checks_fields_that_were_actually_set(self):
        """A field that was never set (stays empty/None) and reads
        back empty must NOT be flagged as a mismatch -- only fields
        the user actually intended to write are checked.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout='{"container":{"properties":{"title":"Only Title"}}}', stderr="",
                )
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            # Only title set -- director/genre_tags/etc left at defaults
            vf.metadata = VideoMetadata(title="Only Title")
            vf.save()

        self.assertEqual(vf.save_error, "")

    def test_verify_error_message_identifies_the_specific_field(self):
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(genre_tags="Action, Comedy")
            vf.save()

        self.assertIn("genre_tags", vf.save_error)
        self.assertIn("Action, Comedy", vf.save_error)

    def test_verify_reports_every_mismatch_not_just_the_first(self):
        """Regression coverage for a real limitation the user's actual
        bug report exposed: content_type sorts first in EDITABLE_FIELDS,
        so an earlier version of _verify_write() that returned on the
        FIRST mismatch could only ever confirm content_type was broken
        -- never revealing whether OTHER fields (genre_tags, season
        number, etc) also silently failed alongside it. Now reports
        every mismatch found in one message.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout='{"container":{"properties":{"title":"Below Deck Mediterranean"}}}',
                    stderr="",
                )
            if "mkvextract" in exe:
                # Nothing except title actually persisted.
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(
                title="Below Deck Mediterranean", content_type=ContentType.TV,
                season_number=11, genre_tags="Reality",
            )
            vf.save()

        self.assertIn("3 field(s)", vf.save_error)
        self.assertIn("content_type", vf.save_error)
        self.assertIn("season_number", vf.save_error)
        self.assertIn("genre_tags", vf.save_error)

    def test_verify_message_includes_raw_tool_output_even_on_nominal_success(self):
        """A tool can print a warning to stdout/stderr while still
        exiting 0 -- that text is now surfaced in the save_error when
        verification finds a mismatch, rather than being silently
        discarded just because the exit code looked fine.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout="", stderr="Warning: unusual tag structure",
                )
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(genre_tags="Action")
            vf.save()

        self.assertIn("Warning: unusual tag structure", vf.save_error)

    def test_write_diagnostic_absent_when_write_reports_nothing(self):
        """The 'tool output:' suffix should not appear at all when
        the write call's stdout/stderr were genuinely empty -- an
        empty diagnostic tag would just be visual noise.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="<Tags></Tags>", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(genre_tags="Action")
            vf.save()

        self.assertNotIn("tool output:", vf.save_error)

    def test_readback_diagnostic_included_when_mkvextract_genuinely_fails(self):
        """The follow-up fix, direct response to a real screenshot: the
        write succeeds (confirmed by mkvpropedit's own stdout, "The
        changes are written to the file"), but a genuine mkvextract
        failure on the READ side means verification still can't find
        the data. The resulting save_error should now include
        mkvextract's own stderr, not just the fact that a mismatch was
        found.
        """
        def fake_run(args, **kwargs):
            exe = args[0]
            if "mkvpropedit" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout="The file is being analyzed.\nThe changes are written to the file.\nDone.",
                    stderr="",
                )
            if "mkvmerge" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=0,
                    stdout='{"container":{"properties":{"title":"Below Deck Mediterranean"}}}',
                    stderr="",
                )
            if "mkvextract" in exe:
                return subprocess.CompletedProcess(
                    args=args, returncode=2, stdout="",
                    stderr='mkvextract: error: unknown mode "tags"',
                )
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="not found")

        with mock.patch("subprocess.run", side_effect=fake_run), \
             mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=Path("/fake/episode.mkv"))
            vf.metadata = VideoMetadata(
                title="Below Deck Mediterranean", content_type=ContentType.TV, season_number=11,
            )
            vf.save()

        self.assertIn("read-back tool output", vf.save_error)
        self.assertIn("unknown mode", vf.save_error)


if __name__ == "__main__":
    unittest.main()
