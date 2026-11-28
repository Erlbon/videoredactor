"""
Review 2026-11-28, fix agent B: cards 3, 4, 15, 16, 17, 31, 39, 40, 65, 69, 70.

MP4 parts run on a tiny generated clip (skipped without ffmpeg); MKV parts need no MKVToolNix: they test the XML
functions and drive the backend with a mocked run_tool that answers with real `mkvmerge -J` shaped JSON.
"""

import json
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

from core import collected_from, conversion_run, embed_posters, mkv_backend
from core.mkv_backend import build_tags_xml, parse_tags_xml, plan_global_tags
from core.mp4_backend import read_mp4_metadata, write_mp4_cover, write_mp4_metadata
from core.video_file import VideoFile
from core.video_fingerprint import video_fingerprint
from core.video_metadata import VideoMetadata

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def _clip(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x48:rate=5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )
    return path


@pytest.fixture
def clip(tmp_path):
    return _clip(tmp_path / "clip.mp4")


JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300") + b"\x00" * 200 + b"\xff\xd9"


# --- card 3: multi-valued MP4 atoms ---------------------------------------------------------------------------


@needs_ffmpeg
def test_mp4_multi_valued_tags_survive_a_save_that_does_not_touch_them(clip):
    from mutagen.mp4 import MP4

    mp4 = MP4(str(clip))
    mp4.add_tags() if mp4.tags is None else None
    mp4.tags["\xa9gen"] = ["Rock", "Pop"]
    mp4.tags["\xa9ART"] = ["A", "B"]
    mp4.save()

    meta = read_mp4_metadata(str(clip))
    assert meta.genre_tags == "Rock; Pop" and meta.artist == "A; B"
    meta.title = "New title"
    write_mp4_metadata(str(clip), meta)

    after = MP4(str(clip))
    assert list(after.tags["\xa9gen"]) == ["Rock", "Pop"]
    assert list(after.tags["\xa9ART"]) == ["A", "B"]
    assert after.tags["\xa9nam"] == ["New title"]


@needs_ffmpeg
def test_mp4_edited_multi_valued_tag_is_written_as_several_values_and_a_single_one_stays_single(clip):
    from mutagen.mp4 import MP4

    mp4 = MP4(str(clip))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags["\xa9gen"] = ["Rock", "Pop"]
    mp4.tags["\xa9alb"] = ["One album"]
    mp4.save()
    meta = read_mp4_metadata(str(clip))
    meta.genre_tags = "Jazz; Blues; Soul"
    meta.album = "Other; album"  # a single-valued atom is never split
    write_mp4_metadata(str(clip), meta)
    after = MP4(str(clip))
    assert list(after.tags["\xa9gen"]) == ["Jazz", "Blues", "Soul"]
    assert list(after.tags["\xa9alb"]) == ["Other; album"]


# --- card 15: the creation date -------------------------------------------------------------------------------


@needs_ffmpeg
@pytest.mark.skipif(sys.platform != "win32", reason="creation time is a Windows notion")
def test_mp4_tag_save_keeps_the_creation_date(clip):
    from redactor_common.core.os_utils import _set_creation_time_windows

    old = 1_500_000_000.0  # July 2017
    _set_creation_time_windows(str(clip), old)
    before = clip.stat().st_ctime
    assert abs(before - old) < 2
    meta = read_mp4_metadata(str(clip))
    meta.title = "Retitled"
    write_mp4_metadata(str(clip), meta)
    assert abs(clip.stat().st_ctime - old) < 2
    write_mp4_cover(str(clip), JPEG)
    assert abs(clip.stat().st_ctime - old) < 2


# --- card 39: odd digits and bytes ------------------------------------------------------------------------------


@needs_ffmpeg
def test_mp4_with_a_superscript_digit_rating_and_non_utf8_freeform_still_loads(clip):
    from mutagen.mp4 import MP4, MP4FreeForm

    mp4 = MP4(str(clip))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags["----:com.videoredactor:personal_rating"] = [MP4FreeForm("²".encode("utf-8"))]
    mp4.tags["----:com.videoredactor:language"] = [MP4FreeForm(b"\xff\xfeen")]
    mp4.save()
    meta = read_mp4_metadata(str(clip))
    assert meta.personal_rating is None
    assert meta.language.endswith("en")


def test_mkv_number_tags_ignore_non_ascii_digits(monkeypatch):
    xml = build_tags_xml({"PART_NUMBER": "²", "EPISODE_NUMBER": "7", "PERSONAL_RATING": "٣"})
    meta = _read_mkv_with(monkeypatch, tags_xml=xml)
    assert meta.season_number is None and meta.episode_number == 7 and meta.personal_rating is None


# --- card 4: MKV repeated tags and targeted tags ---------------------------------------------------------------

TAGS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Tags>
  <Tag><Targets/>
    <Simple><Name>ACTOR</Name><String>Alice</String></Simple>
    <Simple><Name>ACTOR</Name><String>Bob</String></Simple>
    <Simple><Name>GENRE</Name><String>Drama</String></Simple>
    <Simple><Name>GENRE</Name><String>Crime</String></Simple>
  </Tag>
  <Tag><Targets><TagTrackUID>123</TagTrackUID></Targets>
    <Simple><Name>DESCRIPTION</Name><String>track note</String></Simple>
  </Tag>
</Tags>"""


def test_parse_tags_xml_joins_repeats_and_ignores_track_tags():
    parsed = parse_tags_xml(TAGS_XML)
    assert parsed == {"ACTOR": "Alice; Bob", "GENRE": "Drama; Crime"}


def test_a_save_with_no_edit_keeps_repeated_tags_and_the_track_tag():
    parsed = parse_tags_xml(TAGS_XML)
    planned = plan_global_tags(TAGS_XML, dict(parsed))
    from xml.etree import ElementTree as ET

    root = ET.fromstring(planned)
    names = [(s.find("Name").text, s.find("String").text) for t in root.findall("Tag") for s in t.findall("Simple")]
    assert ("ACTOR", "Alice") in names and ("ACTOR", "Bob") in names
    assert ("GENRE", "Drama") in names and ("GENRE", "Crime") in names
    # The track's own tag is not copied into the global one (mkvpropedit's `global` leaves it where it is).
    assert "DESCRIPTION" not in [n[0] for n in names]
    assert len([n for n in names if n[0] == "ACTOR"]) == 2


def test_an_edited_cast_is_written_as_repeated_tags():
    xml = build_tags_xml({"ACTOR": "Alice; Bob; Cy", "COMMENT": "a; b"})
    from xml.etree import ElementTree as ET

    root = ET.fromstring(xml)
    names = [(s.find("Name").text, s.find("String").text) for s in root.iter("Simple")]
    assert names == [("ACTOR", "Alice"), ("ACTOR", "Bob"), ("ACTOR", "Cy"), ("COMMENT", "a; b")]


# --- card 40: CR and control characters --------------------------------------------------------------------------


def test_mkv_tag_text_is_made_xml_safe_and_reads_back_the_same():
    text = "line one\r\nline two\rthree\x0bfour\x00"
    xml = build_tags_xml({"DESCRIPTION": text})  # would raise or differ before the fix
    parsed = parse_tags_xml(xml)
    assert parsed["DESCRIPTION"] == mkv_backend.clean_tag_text(text) == "line one\nline two\nthreefour"


def test_mkv_verify_accepts_text_that_came_back_cleaned(monkeypatch, tmp_path):
    vf = VideoFile(path=tmp_path / "x.mkv", metadata=VideoMetadata(title="T", description="a\r\nb\x0bc"))
    reread = VideoMetadata(container="mkv", title="T", description="a\nbc")
    monkeypatch.setattr("core.video_file.read_mkv_metadata", lambda path, diagnostics=None: reread)
    assert vf._verify_write() == ""


# --- card 65, 70, 69: MKV cover and save steps (mocked tools) ------------------------------------------------------


def _done(stdout="", returncode=0):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _info(size=1000, name="cover.jpg"):
    return json.dumps({"container": {"properties": {}},
                       "attachments": [{"id": 3, "file_name": name, "size": size, "content_type": "image/jpeg"}]})


def _read_mkv_with(monkeypatch, tags_xml="", info="{}"):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if "-J" in args:
            return _done(info)
        if "tags" in args:
            Path(args[-1]).write_text(tags_xml, encoding="utf-8")
            return _done()
        return _done()

    monkeypatch.setattr(mkv_backend, "run_tool", fake)
    monkeypatch.setattr(mkv_backend, "get_executable_path", lambda name: name)
    return mkv_backend.read_mkv_metadata("x.mkv")


def test_a_huge_mkv_cover_is_not_extracted(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        return _done(_info(size=mkv_backend.MAX_INPUT_BYTES + 1))

    monkeypatch.setattr(mkv_backend, "run_tool", fake)
    monkeypatch.setattr(mkv_backend, "get_executable_path", lambda name: name)
    assert mkv_backend.read_mkv_cover("x.mkv") is None
    assert not any("attachments" in a for a in calls)  # mkvextract never ran


def test_a_failed_identify_does_not_add_a_second_cover(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        return _done("", returncode=2)  # mkvmerge -J failed

    monkeypatch.setattr(mkv_backend, "run_tool", fake)
    monkeypatch.setattr(mkv_backend, "get_executable_path", lambda name: name)
    with pytest.raises(RuntimeError):
        mkv_backend.write_mkv_cover("x.mkv", JPEG)
    with pytest.raises(RuntimeError):
        mkv_backend.remove_mkv_cover("x.mkv")
    assert not any("mkvpropedit" in a[0] for a in calls)  # nothing was edited


def test_a_good_identify_still_replaces_the_old_cover_by_id(monkeypatch):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        return _done(_info()) if "-J" in args else _done()

    monkeypatch.setattr(mkv_backend, "run_tool", fake)
    monkeypatch.setattr(mkv_backend, "get_executable_path", lambda name: name)
    assert mkv_backend.write_mkv_cover("x.mkv", JPEG).returncode == 0
    edit = [a for a in calls if a[0] == "mkvpropedit"][0]
    assert edit[edit.index("--delete-attachment") + 1] == "3"


def test_mkv_save_stops_after_a_failed_title_and_the_baseline_follows_the_file(monkeypatch, tmp_path):
    calls = []

    def fake(args, **kwargs):
        calls.append(args)
        if args[0] == "mkvpropedit" and "--tags" in args:
            return _done("", returncode=2)
        if "-J" in args:
            return _done(json.dumps({"container": {"properties": {"title": "New"}}}))
        return _done()

    monkeypatch.setattr(mkv_backend, "run_tool", fake)
    monkeypatch.setattr(mkv_backend, "get_executable_path", lambda name: name)
    monkeypatch.setattr("core.video_file.is_tool_available", lambda tool: True)
    monkeypatch.setattr("core.video_file.require_writable", lambda p: None)
    monkeypatch.setattr("core.video_file.history.journal_begin", lambda *a, **k: None)
    monkeypatch.setattr("core.video_file.history.journal_finish", lambda *a, **k: None)
    path = tmp_path / "x.mkv"
    path.write_bytes(b"x")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="New", comment="c"))
    vf.baseline = {"title": "Old"}
    vf.save()
    assert vf.save_error  # the tags call failed after the title call succeeded
    # The title is on disk now: the baseline is read from the file again, not left as "Old".
    assert vf.baseline.get("title") == "New" if isinstance(vf.baseline, dict) else "New" in str(vf.baseline)


# --- card 31: the fingerprint ignores a cover ------------------------------------------------------------------


@needs_ffmpeg
def test_adding_a_cover_does_not_change_the_fingerprint(clip):
    before = video_fingerprint(str(clip))
    assert before.startswith("v2-")
    write_mp4_cover(str(clip), _real_jpeg(clip.parent))
    assert video_fingerprint(str(clip)) == before


def _real_jpeg(folder: Path) -> bytes:
    out = folder / "p.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=32x48:rate=1:duration=1",
                    "-frames:v", "1", str(out)], check=True, capture_output=True)
    return out.read_bytes()


def test_a_stamp_from_an_older_fingerprint_reads_unverified(tmp_path, monkeypatch):
    from redactor_common.core.scan_stamp import make_stamp

    monkeypatch.setattr("core.video_file.is_tool_available", lambda tool: True)
    monkeypatch.setattr("core.video_file.video_fingerprint", lambda p: "v2-zzz")
    vf = VideoFile(path=tmp_path / "x.mp4", metadata=VideoMetadata(scan_stamp=make_stamp("CHECKED OK", "v1-abc").to_text()))
    vf._refresh_stamp_staleness()
    assert vf.stamp_stale is None
    vf.metadata.scan_stamp = make_stamp("CHECKED OK", "v2-abc").to_text()
    vf._refresh_stamp_staleness()
    assert vf.stamp_stale is True


# --- card 16: a suggested "Collected from" is not written behind the user's back --------------------------------


@needs_ffmpeg
def test_source_tags_for_a_conversion_drop_the_suggested_collected_from(clip, monkeypatch):
    from mutagen.mp4 import MP4

    mp4 = MP4(str(clip))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags["\xa9cmt"] = ["https://youtu.be/abc123"]
    mp4.save()
    vf = VideoFile(path=clip)
    vf.load()
    assert vf.metadata.collected_from == "YouTube" and vf.metadata.collected_from_suggested  # the window's behaviour
    tags = conversion_run.read_source_tags(str(clip))
    assert tags.collected_from == "" and tags.collected_from_detail == "" and not tags.collected_from_suggested
    assert tags.comment == "https://youtu.be/abc123"


@needs_ffmpeg
def test_cli_load_for_write_drops_the_suggestion_but_a_kept_value_stays(clip):
    from mutagen.mp4 import MP4, MP4FreeForm
    from videocli.files import load_videos

    mp4 = MP4(str(clip))
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags["\xa9cmt"] = ["https://youtu.be/abc123"]
    mp4.save()
    [shown] = load_videos([str(clip)])
    assert shown.metadata.collected_from == "YouTube"
    [video] = load_videos([str(clip)], for_write=True)
    video.metadata.title = "X"
    video.dirty = True
    video.save()
    assert video.save_error == ""
    after = MP4(str(clip))
    assert not [k for k in after.tags if "collected_from" in k]
    mp4 = MP4(str(clip))
    mp4.tags["----:com.videoredactor:collected_from"] = [MP4FreeForm(b"DVD")]
    mp4.save()
    [kept] = load_videos([str(clip)], for_write=True)
    assert kept.metadata.collected_from == "DVD"


def test_drop_suggestion_only_touches_a_flagged_suggestion():
    meta = VideoMetadata(collected_from="DVD")
    assert not collected_from.drop_suggestion(meta) and meta.collected_from == "DVD"


# --- card 17: a staged poster change blocks the sidecar embed ------------------------------------------------------


def test_embed_offer_skips_a_video_with_a_staged_cover_change(tmp_path):
    poster = tmp_path / "v-poster.jpg"
    poster.write_bytes(JPEG)
    binned = []
    for staged in ({"cover_pending": b"x"}, {"cover_remove": True}):
        meta = VideoMetadata()
        for key, value in staged.items():
            setattr(meta, key, value)
        video = types.SimpleNamespace(path=tmp_path / "v.mp4", metadata=meta,
                                      embed_cover_now=lambda jpeg: pytest.fail("must not embed"))
        offer = embed_posters.PosterOffer(video, str(poster), (2, 3), embed_posters.PosterVerdict(True, ""))
        outcome = embed_posters.embed_offer(offer, trash=binned.append)
        assert not outcome.embedded and not outcome.trashed and "unsaved poster change" in outcome.message
    assert binned == [] and poster.exists()
