"""Clearing a field must persist (review finding M4) and Season/Episode 0
must survive (M5): MKV tag deletion without destroying other tools' tags,
MP4 round trip against a real file, and save verification of empty
fields."""

import subprocess
import unittest.mock as mock
import xml.etree.ElementTree as ET

import pytest

from core import mkv_backend as mkv
from core.mp4_backend import read_mp4_metadata, write_mp4_metadata
from core.video_file import VideoFile
from core.video_metadata import ContentType, VideoMetadata


def _names(xml_text):
    return sorted(n.text for n in ET.fromstring(xml_text).iter("Name"))


FOREIGN = (
    "<Tags>"
    "<Tag><Simple><Name>ENCODER_SETTINGS</Name><String>x264</String></Simple>"
    "<Simple><Name>DIRECTOR</Name><String>Old Director</String></Simple></Tag>"
    "<Tag><Targets><TrackUID>123</TrackUID></Targets><Simple><Name>BPS</Name><String>1</String></Simple></Tag>"
    "</Tags>"
)


def test_plan_keeps_other_tools_tags_and_replaces_ours():
    planned = mkv.plan_global_tags(FOREIGN, {"COMMENT": "hi"})
    assert _names(planned) == ["COMMENT", "ENCODER_SETTINGS"]  # DIRECTOR gone; track tag is left to mkvpropedit


def test_plan_clears_our_tag_but_keeps_foreign_ones():
    planned = mkv.plan_global_tags(FOREIGN, {})
    assert _names(planned) == ["ENCODER_SETTINGS"]


def test_plan_deletes_all_global_tags_when_only_ours_were_there():
    xml = "<Tags><Tag><Simple><Name>DIRECTOR</Name><String>x</String></Simple></Tag></Tags>"
    assert mkv.plan_global_tags(xml, {}) == ""


def test_plan_does_nothing_when_nothing_to_set_or_clear():
    assert mkv.plan_global_tags("", {}) is None
    assert mkv.plan_global_tags(FOREIGN.replace("DIRECTOR", "OTHER"), {}) is None


def _fake_tool(extracted_xml, title="Old Title"):
    calls = []

    def fake_run(args, **kwargs):
        exe = args[0]
        if "mkvpropedit" in exe:
            tags = next((a for a in args if a.startswith("global:")), None)
            written = None
            if tags and tags != "global:":
                with open(tags.split(":", 1)[1], encoding="utf-8") as handle:
                    written = handle.read()
            calls.append((args[2:], written))
            return subprocess.CompletedProcess(args, 0, "", "")
        if "mkvmerge" in exe:
            return subprocess.CompletedProcess(args, 0, '{"container": {"properties": {"title": "%s"}}}' % title, "")
        if "mkvextract" in exe:
            return subprocess.CompletedProcess(args, 0, extracted_xml, "")
        return subprocess.CompletedProcess(args, 1, "", "not found")

    return fake_run, calls


def test_clearing_everything_deletes_the_title_and_our_tags_but_not_foreign_ones():
    fake, calls = _fake_tool(FOREIGN)
    with mock.patch("subprocess.run", side_effect=fake):
        result = mkv.write_mkv_metadata("/fake/a.mkv", VideoMetadata())  # every field empty
    assert result.returncode == 0
    assert calls[0][0] == ["--edit", "info", "--delete", "title"]
    args, written = calls[1]
    assert args[0] == "--tags" and _names(written) == ["ENCODER_SETTINGS"]


def test_clearing_the_last_tag_writes_an_empty_filename():
    xml = "<Tags><Tag><Simple><Name>DIRECTOR</Name><String>x</String></Simple></Tag></Tags>"
    fake, calls = _fake_tool(xml, title="")
    with mock.patch("subprocess.run", side_effect=fake):
        mkv.write_mkv_metadata("/fake/a.mkv", VideoMetadata())
    assert calls == [(["--tags", "global:"], None)]  # no title to delete, tags removed


def test_an_untagged_untitled_file_is_not_touched_when_everything_is_empty():
    fake, calls = _fake_tool("", title="")
    with mock.patch("subprocess.run", side_effect=fake):
        assert mkv.write_mkv_metadata("/fake/a.mkv", VideoMetadata()).returncode == 0
    assert calls == []


def test_unreadable_tags_are_not_cleared_but_values_are_still_written():
    def fake(args, **kwargs):
        if "mkvextract" in args[0]:
            return subprocess.CompletedProcess(args, 2, "", "error")
        if "mkvmerge" in args[0]:
            return subprocess.CompletedProcess(args, 0, "{}", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    with mock.patch("subprocess.run", side_effect=fake) as run:
        mkv.write_mkv_metadata("/fake/a.mkv", VideoMetadata())
        assert not any("mkvpropedit" in c.args[0][0] for c in run.call_args_list)


@pytest.fixture(scope="module")
def mp4_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("mp4") / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
         "-c:v", "libx264", "-preset", "ultrafast", str(path)],
        check=True, capture_output=True,
    )
    return path


def test_mp4_season_and_episode_zero_are_written_not_deleted(mp4_file, tmp_path):
    copy = tmp_path / "s0.mp4"
    copy.write_bytes(mp4_file.read_bytes())
    write_mp4_metadata(str(copy), VideoMetadata(show_title="Show", season_number=0, episode_number=0))
    meta = read_mp4_metadata(str(copy))
    assert (meta.season_number, meta.episode_number) == (0, 0)


def test_mp4_clearing_fields_persists_and_verifies(mp4_file, tmp_path):
    vf = VideoFile(path=tmp_path / "c.mp4")
    vf.path.write_bytes(mp4_file.read_bytes())
    vf.metadata = VideoMetadata(title="T", director="D", season_number=0, content_type=ContentType.TV)
    vf.save()
    assert vf.save_error == ""
    vf.metadata = VideoMetadata(title="", director="", season_number=None, content_type=ContentType.UNSET)
    vf.save()
    assert vf.save_error == ""
    meta = read_mp4_metadata(str(vf.path))
    assert (meta.title, meta.director, meta.season_number, meta.content_type) == ("", "", None, ContentType.UNSET)


def test_verification_flags_a_cleared_field_that_is_still_in_the_file(tmp_path):
    vf = VideoFile(path=tmp_path / "x.mkv")
    vf.metadata = VideoMetadata(title="", director="")
    stale = VideoMetadata(title="Old", director="")
    with mock.patch("core.video_file.read_mkv_metadata", return_value=stale):
        message = vf._verify_write()
    assert "'title' (cleared, file still reads 'Old')" in message


@pytest.mark.parametrize("code, ok", [(0, True), (1, True), (2, False)])
def test_mkvpropedit_warning_exit_is_not_a_failed_save(tmp_path, code, ok):
    vf = VideoFile(path=tmp_path / "w.mkv")
    vf.metadata = VideoMetadata(title="T")
    done = subprocess.CompletedProcess([], code, "", "warning: something")
    with mock.patch("core.video_file.is_tool_available", return_value=True), \
            mock.patch("core.video_file.write_mkv_metadata", return_value=done), \
            mock.patch("core.video_file.read_mkv_metadata", return_value=VideoMetadata(title="T")):
        vf.save()
    assert (vf.save_error == "") is ok
