"""Replacing an MKV cover must delete the attachment it DETECTED, not the literal name cover.jpg (2026-10-16 review,
finding 2): a file whose cover is cover.png / cover.jpeg / Cover.JPG used to fail with mkvpropedit's 'No attachment
matched' (exit 2) AFTER the title and tags were already written, and every retry failed the same way.

AGAINST THE IN-MEMORY MODEL OF MKVToolNIX (tests/test_embedded_sources_mkv_fake_tools.py), whose --delete-attachment
accepts only the documented selectors (an id or name:<exact name>) and fails with exit 2 when nothing matches. This
proves the logic and the command lines, NOT the real tool: MKVToolNix is not installed on the machine these were
written on."""

import pytest

from core.video_metadata import VideoMetadata
from tests.test_embedded_sources_mkv_fake_tools import JPEG, JPEG2, make, mkv_file  # noqa: F401  (the fixture)

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + b"png-data" * 10


@pytest.mark.parametrize("old_name, mime", [
    ("cover.png", "image/png"), ("cover.jpeg", "image/jpeg"), ("Cover.JPG", "image/jpeg"), ("COVER.webp", "image/webp"),
])
def test_a_poster_replaces_a_cover_that_is_not_called_cover_jpg(mkv_file, old_name, mime):  # noqa: F811
    mkv_file.attachments = [{"file_name": "font.ttf", "content_type": "font/ttf", "data": b"font"},
                            {"file_name": old_name, "content_type": mime, "data": JPEG}]
    vf = make(VideoMetadata(title="New title"))
    vf.metadata.cover_pending = JPEG2
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert mkv_file.title == "New title"
    assert sorted(a["file_name"] for a in mkv_file.attachments) == ["cover.jpg", "font.ttf"]  # one cover, the font untouched
    assert vf.read_cover() == JPEG2
    assert mkv_file.deleted_selectors == ["2"]  # the detected attachment's id, not a name that may not match


def test_a_png_poster_is_stored_as_cover_png_with_the_png_mime_type(mkv_file):  # noqa: F811
    mkv_file.attachments = [{"file_name": "cover.jpg", "content_type": "image/jpeg", "data": JPEG}]
    vf = make()
    vf.metadata.cover_pending = PNG
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert [(a["file_name"], a["content_type"]) for a in mkv_file.attachments] == [("cover.png", "image/png")]
    assert vf.read_cover() == PNG


def test_a_failing_cover_step_leaves_title_and_tags_untouched_so_a_retry_starts_clean(mkv_file):  # noqa: F811
    """The cover is written first: when it is refused nothing else has been applied (it used to come last, leaving the
    title and tags written, a save error, and no way forward)."""
    mkv_file.title = "Old title"
    mkv_file.attachments = [{"file_name": "cover.jpg", "content_type": "image/jpeg", "data": JPEG}]
    original = mkv_file._propedit

    def refuse_attachments(args):
        if any(a.startswith("--delete-attachment") or a == "--add-attachment" for a in args):
            import subprocess

            return subprocess.CompletedProcess(args, 2, "", "Error: the attachment could not be written")
        return original(args)

    mkv_file._propedit = refuse_attachments
    vf = make(VideoMetadata(title="New title", director="A. Director"))
    vf.metadata.cover_pending = JPEG2
    vf.dirty = True
    vf.save()
    assert "attachment could not be written" in vf.save_error and vf.dirty
    assert mkv_file.title == "Old title" and mkv_file.tags_xml == ""  # nothing half-applied
    assert vf.metadata.cover_pending == JPEG2  # still staged for the retry
    mkv_file._propedit = original  # the retry now succeeds in full
    vf.save()
    assert vf.save_error == "" and mkv_file.title == "New title" and vf.read_cover() == JPEG2

