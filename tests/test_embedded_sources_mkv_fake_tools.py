"""The MKV side of the source-id tags and the embedded poster, against an IN-MEMORY MODEL of MKVToolNix (mkvmerge -J,
mkvextract tags/attachments, mkvpropedit), patched in as subprocess.run, like tests/test_clearing_fields.py.

THIS PROVES THE LOGIC AND THE COMMAND LINES AS DOCUMENTED, NOT THE REAL TOOL: MKVToolNix is not installed on the machine
these tests were written on, so `mkvpropedit --delete-attachment name:cover.jpg --attachment-name cover.jpg
--attachment-mime-type image/jpeg --add-attachment <file>` has not been run against a real mkvpropedit. The syntax is
taken from the mkvpropedit manual (https://mkvtoolnix.download/doc/mkvpropedit.html)."""

import json
import os
import subprocess
import unittest.mock as mock
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from core import mkv_backend as mkv
from core.video_file import VideoFile
from core.video_metadata import VideoMetadata

JPEG = b"\xff\xd8\xff\xe0" + b"fake-jpeg-bytes" * 20
JPEG2 = b"\xff\xd8\xff\xe0" + b"other-jpeg-data" * 30


class FakeMkv:
    """One fake file: its title, its global tags (Tags XML text) and its attachments."""

    def __init__(self, title="", tags_xml="", attachments=None):
        self.title, self.tags_xml = title, tags_xml
        self.attachments = list(attachments or [])  # dicts: file_name, content_type, data
        self.propedit_calls: list[list[str]] = []
        self.next_id = 1

    # --- the three tools -------------------------------------------------------------------------------------------

    def run(self, args, **kwargs):
        exe = os.path.basename(str(args[0])).lower()
        if "mkvmerge" in exe:
            info = {"container": {"properties": {"title": self.title, "duration": 5_000_000_000}},
                    "attachments": [{"id": i + 1, "file_name": a["file_name"], "content_type": a["content_type"],
                                     "size": len(a["data"])} for i, a in enumerate(self.attachments)]}
            return subprocess.CompletedProcess(args, 0, json.dumps(info), "")
        if "mkvextract" in exe:
            mode, target = args[2], args[3]
            if mode == "tags":
                Path(target).write_text(self.tags_xml, encoding="utf-8")
            else:
                ident, out = target.split(":", 1)
                Path(out).write_bytes(self.attachments[int(ident) - 1]["data"])
            return subprocess.CompletedProcess(args, 0, "", "")
        if "mkvpropedit" in exe:
            return self._propedit(args)
        return subprocess.CompletedProcess(args, 1, "", "unknown tool")

    def _propedit(self, args):
        rest = list(args[2:])
        self.propedit_calls.append(rest)
        name = mime = None
        i = 0
        deletes, adds = [], []
        while i < len(rest):
            a = rest[i]
            if a == "--edit":
                i += 2
                continue
            if a == "--set":
                self.title = rest[i + 1].split("=", 1)[1]
                i += 2
                continue
            if a == "--delete":
                self.title = ""
                i += 2
                continue
            if a == "--tags":
                target = rest[i + 1].split(":", 1)[1]
                self.tags_xml = Path(target).read_text(encoding="utf-8") if target else ""
                i += 2
                continue
            if a == "--delete-attachment":
                deletes.append(rest[i + 1])
                i += 2
                continue
            if a == "--attachment-name":
                name = rest[i + 1]
                i += 2
                continue
            if a == "--attachment-mime-type":
                mime = rest[i + 1]
                i += 2
                continue
            if a == "--add-attachment":
                path = rest[i + 1]
                adds.append({"file_name": name or os.path.basename(path), "content_type": mime or "application/octet-stream",
                             "data": Path(path).read_bytes()})
                name = mime = None
                i += 2
                continue
            return subprocess.CompletedProcess(args, 2, "", f"Error: unknown option {a}")
        for selector in deletes:
            assert selector.startswith("name:")
            self.attachments = [x for x in self.attachments if x["file_name"] != selector[5:]]
        self.attachments.extend(adds)
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def mkv_file():
    fake = FakeMkv()
    with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("core.video_file.is_tool_available", return_value=True):
        yield fake


def make(meta=None) -> VideoFile:
    return VideoFile(path=Path("/fake/Movie.mkv"), metadata=meta or VideoMetadata())


def test_ids_are_written_as_global_simple_tags_with_the_documented_names(mkv_file):
    vf = make(VideoMetadata(title="Movie"))
    vf.metadata.tmdb_id, vf.metadata.imdb_id = "movie:603", "tt0133093"
    vf.metadata.theporndb_id = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    vf.metadata.source_url, vf.metadata.metadata_match = "https://www.themoviedb.org/movie/603", "tmdb;2026-10-16;title"
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    tags = mkv.parse_tags_xml(mkv_file.tags_xml)
    assert tags["TMDB_ID"] == "movie:603" and tags["IMDB_ID"] == "tt0133093"
    assert tags["THEPORNDB_ID"].startswith("d284e2a0") and tags["METADATA_MATCH"] == "tmdb;2026-10-16;title"
    assert tags["SOURCE_URL"] == "https://www.themoviedb.org/movie/603"
    assert "STASHDB_ID" not in tags and "TVDB_ID" not in tags
    reread = mkv.read_mkv_metadata("/fake/Movie.mkv")
    assert (reread.tmdb_id, reread.imdb_id, reread.metadata_match) == ("movie:603", "tt0133093", "tmdb;2026-10-16;title")


def test_other_tools_tags_are_kept_when_ids_are_written(mkv_file):
    mkv_file.tags_xml = ("<Tags><Tag><Simple><Name>ENCODER_SETTINGS</Name><String>x264</String></Simple></Tag></Tags>")
    vf = make()
    vf.metadata.stashdb_id = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"
    vf.dirty = True
    vf.save()
    tags = mkv.parse_tags_xml(mkv_file.tags_xml)
    assert tags["ENCODER_SETTINGS"] == "x264" and "STASHDB_ID" in tags


def test_the_poster_becomes_the_cover_jpg_attachment_and_is_read_back(mkv_file):
    vf = make()
    vf.metadata.cover_pending = JPEG
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and vf.metadata.cover_pending is None
    assert [(a["file_name"], a["content_type"]) for a in mkv_file.attachments] == [("cover.jpg", "image/jpeg")]
    assert vf.read_cover() == JPEG and vf.metadata.cover_info.startswith("JPEG, ")
    command = mkv_file.propedit_calls[-1]
    assert command[-4:-2] == ["--attachment-mime-type", "image/jpeg"] or "--add-attachment" in command
    assert command[command.index("--attachment-name") + 1] == "cover.jpg"
    assert command.index("--attachment-name") < command.index("--add-attachment")  # options come before the action


def test_a_new_poster_replaces_the_old_cover_in_the_same_call(mkv_file):
    mkv_file.attachments = [{"file_name": "cover.jpg", "content_type": "image/jpeg", "data": JPEG},
                            {"file_name": "font.ttf", "content_type": "font/ttf", "data": b"font"}]
    vf = make()
    vf.metadata.cover_pending = JPEG2
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert sorted(a["file_name"] for a in mkv_file.attachments) == ["cover.jpg", "font.ttf"]  # one cover, the font untouched
    assert vf.read_cover() == JPEG2
    command = mkv_file.propedit_calls[-1]
    assert command[:2] == ["--delete-attachment", "name:cover.jpg"]


def test_a_save_without_a_staged_poster_does_not_touch_attachments(mkv_file):
    mkv_file.attachments = [{"file_name": "cover.jpg", "content_type": "image/jpeg", "data": JPEG}]
    vf = make(VideoMetadata(title="T"))
    vf.dirty = True
    vf.save()
    assert vf.read_cover() == JPEG and all("--add-attachment" not in c for c in mkv_file.propedit_calls)


def test_other_cover_names_are_read_but_landscape_variants_are_ignored(mkv_file):
    mkv_file.attachments = [{"file_name": "cover_land.jpg", "content_type": "image/jpeg", "data": b"wide"}]
    assert mkv.read_mkv_cover("/fake/Movie.mkv") is None
    mkv_file.attachments.append({"file_name": "Cover.PNG", "content_type": "image/png", "data": b"\x89PNG-data"})
    assert mkv.read_mkv_cover("/fake/Movie.mkv") == b"\x89PNG-data"
    assert mkv.read_mkv_metadata("/fake/Movie.mkv").cover_info.startswith("PNG, ")


def test_a_cover_that_did_not_stick_is_reported_by_the_save_verification(mkv_file):
    original = mkv_file._propedit

    def drop_attachments(args):
        result = original(args)
        mkv_file.attachments = []  # the tool "succeeded" but the file has no cover afterwards
        return result

    mkv_file._propedit = drop_attachments
    vf = make()
    vf.metadata.cover_pending = JPEG
    vf.dirty = True
    vf.save()
    assert "cover" in vf.save_error and vf.dirty and vf.metadata.cover_pending == JPEG  # still staged for a retry


def test_embed_cover_now_writes_and_reads_back(mkv_file):
    vf = make(VideoMetadata(title="Unsaved"))
    assert vf.embed_cover_now(JPEG) == ""
    assert vf.read_cover() == JPEG and mkv_file.title == ""  # only the cover was written


def test_embed_cover_now_without_the_tool_says_so():
    with mock.patch("core.video_file.is_tool_available", return_value=False):
        assert "MKVToolNix" in make().embed_cover_now(JPEG)


def test_a_failing_mkvpropedit_is_an_error_not_a_silent_success(mkv_file):
    def fail(args):
        return subprocess.CompletedProcess(args, 2, "", "Error: could not open the file")

    mkv_file._propedit = fail
    problem = make().embed_cover_now(JPEG)
    assert "could not open" in problem
