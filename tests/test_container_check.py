"""Container-vs-extension check (core/container_check.py) and its Check Files integration: hand-made header bytes
and ffprobe format names, no tools needed."""

import json
import subprocess

import pytest

from core import conversion_plan as cp
from core import container_check as cc
from core.file_check import REPAIRABLE, WRONG_CONTAINER_CODE, quick_check

FLV = b"FLV\x01\x05\x00\x00\x00\x09" + b"\x00" * 64
AVI = b"RIFF\x00\x10\x00\x00AVI LIST" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64
MKV = bytes.fromhex("1a45dfa3") + b"\x9f\x42\x86\x81\x01" + b"\x00" * 64
ASF = bytes.fromhex("3026b2758e66cf11a6d900aa0062ce6c") + b"\x00" * 64
OGG = b"OggS\x00\x02" + b"\x00" * 64
PS = bytes.fromhex("000001ba") + b"\x44\x00\x04\x00" + b"\x00" * 64
TS = b"".join(b"\x47" + bytes(187) for _ in range(5))
M2TS = b"".join(bytes(4) + b"\x47" + bytes(187) for _ in range(5))


def named(tmp_path, name, content):
    path = tmp_path / name
    path.write_bytes(content)
    return str(path)


@pytest.mark.parametrize("content,key", [
    (FLV, "flv"), (AVI, "avi"), (MP4, "mp4"), (MKV, "matroska"), (ASF, "asf"), (OGG, "ogg"), (PS, "mpeg"),
    (TS, "mpegts"), (M2TS, "mpegts"), (b"", None), (b"just some text", None),
])
def test_sniff(content, key):
    assert cc.sniff_container(content) == key


@pytest.mark.parametrize("ext", [".mp4", ".m4v", ".mov", ".mkv"])
@pytest.mark.parametrize("content,label", [
    (FLV, "FLV"), (AVI, "AVI"), (ASF, "ASF"), (TS, "MPEG-TS"), (OGG, "Ogg"), (PS, "MPEG-PS"),
])
def test_wrong_by_magic_when_ffprobe_says_nothing(tmp_path, ext, content, label):
    verdict = cc.check_container(named(tmp_path, "movie" + ext, content), "")
    assert verdict is not None and verdict.wrong and verdict.label == label and verdict.source == "magic"
    assert verdict.message == f"wrong container ({ext} file is really {label})"


def test_mkv_that_is_mp4_and_the_reverse(tmp_path):
    assert cc.check_container(named(tmp_path, "a.mkv", MP4), "").wrong
    assert cc.check_container(named(tmp_path, "b.mp4", MKV), "").wrong
    assert cc.check_container(named(tmp_path, "c.avi", MKV), "").wrong
    assert cc.check_container(named(tmp_path, "d.mkv", AVI), "").label == "AVI"


def test_legitimate_files_are_never_flagged(tmp_path):
    for name, content in (("a.mp4", MP4), ("b.m4v", MP4), ("c.mov", MP4), ("d.mkv", MKV), ("e.webm", MKV), ("f.avi", AVI)):
        verdict = cc.check_container(named(tmp_path, name, content), "")
        assert verdict is not None and not verdict.wrong, name


@pytest.mark.parametrize("name,format_name", [
    ("a.mp4", "mov,mp4,m4a,3gp,3g2,mj2"), ("a.m4v", "mov,mp4,m4a,3gp,3g2,mj2"), ("a.mov", "mov,mp4,m4a,3gp,3g2,mj2"),
    ("a.mkv", "matroska,webm"), ("a.webm", "matroska,webm"), ("a.avi", "avi"),
])
def test_ffprobe_format_names_cover_their_extensions(name, format_name):
    assert cc.wrong_container(name, format_name) is None


@pytest.mark.parametrize("name,format_name,label", [
    ("a.mp4", "flv", "FLV"), ("a.mkv", "avi", "AVI"), ("a.mov", "asf", "ASF"), ("a.m4v", "mpegts", "MPEG-TS"),
    ("a.mp4", "ogg", "Ogg"), ("a.mp4", "mpeg", "MPEG-PS"), ("a.mkv", "mov,mp4,m4a,3gp,3g2,mj2", "MP4/MOV"),
    ("a.mp4", "matroska,webm", "Matroska"),
])
def test_ffprobe_format_names_flag_the_wrong_ones(name, format_name, label):
    verdict = cc.wrong_container(name, format_name)
    assert verdict is not None and verdict.label == label and verdict.source == "ffprobe"


@pytest.mark.parametrize("name,format_name", [
    ("a.mp4", ""), ("a.mp4", "h264"), ("a.mp4", "image2"), ("a.mpg", "mpegts"), ("a.txt", "flv"), ("a.mp3", "flv"),
])
def test_unknown_or_unjudged_gives_no_verdict(name, format_name):
    assert cc.wrong_container(name, format_name) is None


def test_ffprobe_wins_over_magic(tmp_path):
    path = named(tmp_path, "a.mp4", FLV)  # bytes say FLV, ffprobe (stub) says MP4: ffprobe is trusted first
    verdict = cc.check_container(path, probe_format=lambda p: "mov,mp4,m4a,3gp,3g2,mj2")
    assert verdict is not None and not verdict.wrong and verdict.source == "ffprobe"


def test_ffprobe_failure_falls_back_to_magic(tmp_path):
    path = named(tmp_path, "a.mp4", FLV)
    verdict = cc.check_container(path, probe_format=lambda p: None)
    assert verdict is not None and verdict.wrong and verdict.source == "magic"


def test_a_missing_file_has_no_verdict(tmp_path):
    assert cc.check_container(str(tmp_path / "nope.mp4"), "") is None


# --- MediaInfo / planner ----------------------------------------------------


def _info(path, format_name, vcodec="h264", acodec="aac"):
    streams = [cp.StreamInfo(0, "video", vcodec, width=1920, height=1080), cp.StreamInfo(1, "audio", acodec),
               cp.StreamInfo(2, "data", "")]
    return cp.MediaInfo(path, 5_000_000_000, 3600.0, streams, format_name=format_name)


def test_parse_ffprobe_keeps_format_name():
    info = cp.parse_ffprobe("a.mp4", {"format": {"format_name": "flv", "duration": "10", "size": "5"}, "streams": []})
    assert info.format_name == "flv"


def test_planner_converts_flv_in_mp4_despite_the_extension():
    plan = cp.plan_conversion(_info("a.mp4", "flv"), is_series=False)
    assert plan.wrong_container == "FLV" and plan.container == ".mp4" and plan.video == cp.V_COPY
    assert plan.does_something and "wrong container" in plan.reasons[0]


def test_planner_leaves_a_real_mp4_alone():
    plan = cp.plan_conversion(_info("a.mp4", "mov,mp4,m4a,3gp,3g2,mj2"), is_series=False)
    assert not plan.wrong_container and not plan.does_something


def test_planner_leaves_a_real_mkv_alone_and_a_hand_made_info_too():
    assert not cp.plan_conversion(_info("a.mkv", "matroska,webm"), is_series=False).does_something
    assert not cp.plan_conversion(_info("a.mp4", ""), is_series=False).does_something


def test_an_mkv_that_is_really_avi_stays_an_mkv():
    plan = cp.plan_conversion(_info("a.mkv", "avi"), is_series=False)
    assert plan.wrong_container == "AVI" and plan.container == ".mkv" and plan.does_something


# --- Check Files finding ----------------------------------------------------


class _Done:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _runner(format_name, duration="60.0"):
    probe = json.dumps({"format": {"duration": duration, "format_name": format_name},
                        "streams": [{"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio",
                                                                         "tags": {"language": "eng"}}]})

    def run(args, timeout=None):
        if args[0] == "ffprobe":
            return _Done(stdout=probe)
        return _Done(stderr="frame=1 time=00:01:00.00")

    return run


def test_check_files_reports_wrong_container_as_its_own_finding(tmp_path):
    path = named(tmp_path, "a.mp4", FLV)
    result = quick_check(path, _runner("flv"))
    codes = {f.code: f for f in result.findings}
    assert WRONG_CONTAINER_CODE in codes
    finding = codes[WRONG_CONTAINER_CODE]
    assert finding.severity == REPAIRABLE
    assert "wrong container (.mp4 file is really FLV)" in finding.message.replace("Wrong", "wrong")
    assert result.status == "WRONG CONTAINER"
    assert result.can_remux_container and not result.can_repair  # the generic repair() is not the fix
    assert "FLV" in result.tooltip()


def test_check_files_does_not_flag_a_real_mp4_with_the_long_format_name(tmp_path):
    path = named(tmp_path, "a.mp4", MP4)
    result = quick_check(path, _runner("mov,mp4,m4a,3gp,3g2,mj2"))
    assert all(f.code != WRONG_CONTAINER_CODE for f in result.findings)
    assert result.status == "CHECKED OK" and not result.can_remux_container


def test_damage_outranks_the_wrong_container_status(tmp_path):
    path = named(tmp_path, "a.mp4", FLV)

    def run(args, timeout=None):
        if args[0] == "ffprobe":
            return _runner("flv")(args)
        return _Done(stderr="[flv] Error while decoding\nframe=1 time=00:00:05.00")

    result = quick_check(path, run)
    assert result.status == "DAMAGED"


def test_unreadable_file_that_looks_like_flv_gets_a_note(tmp_path):
    path = named(tmp_path, "a.mp4", FLV)
    result = quick_check(path, lambda args, timeout=None: _Done(stderr="Invalid data found", returncode=1))
    assert result.status == "DAMAGED" and not result.can_remux_container
    assert any(f.code == "wrong_container_unreadable" for f in result.findings)
