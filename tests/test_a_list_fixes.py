"""Regression tests for the 2026-10-13 cross-app review (the "A list"): conversion plan, ffmpeg subtitle
arguments, the safety swap, subtitle decoding and the MP4 scratch-copy write."""

import os
import shutil
import subprocess
import time

import pytest

from core import conversion_plan as cp
from core import conversion_run as cr
from core import library_safety as ls
from core import subtitle_ingest as si

GB = 1073741824
MB = 1048576
GOOD = ({"video": 1, "audio": 1, "subtitle": 0}, 100.0)


def info(ext=".mkv", size=2 * GB, dur=3000.0, vcodec="h264", w=1920, h=1080, audio=("aac",), subs=()):
    streams = [cp.StreamInfo(0, "video", vcodec, width=w, height=h)]
    for codec in audio:
        streams.append(cp.StreamInfo(len(streams), "audio", codec, bit_rate=160_000))
    for codec in subs:
        streams.append(cp.StreamInfo(len(streams), "subtitle", codec))
    return cp.MediaInfo("F:/Video/Show/ep" + ext, size, dur, streams)


def _old(path, text="x", age=3600):
    path.write_text(text, encoding="utf-8")
    past = time.time() - age
    os.utime(path, (past, past))
    return str(path)


# --- A1: an MKV with external subtitles to add is work ---------------------------------------------------------


def test_a1_mkv_with_external_subtitles_does_something():
    p = cp.plan_conversion(info(".mkv", size=300 * MB, h=720, w=1280), extra_subtitles=1)
    assert p.container == ".mkv" and p.does_something
    assert any("external subtitle" in r for r in p.reasons)
    assert not cp.plan_conversion(info(".mkv", size=300 * MB, h=720, w=1280)).does_something


def test_a1_process_file_ingests_srt_into_an_mkv(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old(lib / "ep.mkv", "V" * 100)
    _old(lib / "ep.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    media = info(".mkv", size=100, h=480, w=640)
    media.path = src
    ran = []

    def runner(args, cancel_event=None, timeout=None):
        ran.append(args)
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("MKV")
        return 0, ""

    def probe(path):
        out = info(".mkv", size=3, h=480, w=640, subs=("subrip",))
        out.path = path
        return out if ".redact-part" in path else media

    trashed = []
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=trashed.append, probe=probe)
    out = cr.process_file(run, src, cp.ConversionSettings(), probe=probe, runner=runner)
    assert out.converted and ran and "language=eng" in ran[0]
    assert sorted(os.path.basename(p) for p in trashed) == ["ep.en.srt", "ep.redact-orig.mkv"]


def test_a1_no_sidecar_work_when_the_plan_skips(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old(lib / "disc.iso", "V" * 100)
    _old(lib / "disc.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    media = info(".iso", size=100)
    media.path = src
    loads = []
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=True)
    out = cr.process_file(run, src, cp.ConversionSettings(), probe=lambda p: media, reader=lambda p: loads.append(p) or b"x")
    assert not out.converted and loads == []  # the .srt was never read


def test_a1_embedded_extraction_only_runs_with_external_candidates(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old(lib / "ep.avi", "V" * 100)
    media = info(".avi", size=100, h=480, w=640, subs=("subrip",))
    media.path = src
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=True)
    out = cr.process_file(
        run, src, cp.ConversionSettings(), probe=lambda p: media,
        embedded_texts=lambda i, s: (_ for _ in ()).throw(AssertionError("no ffmpeg extraction expected")),
    )
    assert out.converted  # dry run of the legacy container conversion, no extraction ran


# --- A2: MKV target with mov_text source subtitles -------------------------------------------------------------


def test_a2_mov_text_becomes_srt_for_an_mkv_target_per_stream():
    m = info(".mp4", size=300 * MB, h=720, w=1280, subs=("mov_text", "subrip"))
    a = cp.build_ffmpeg_args(m, cp.Plan(container=".mkv", reasons=["x"]), "in.mp4", "out.mkv")
    assert "-c:s" not in a  # no global copy
    assert a[a.index("-c:s:0") + 1] == "srt" and a[a.index("-c:s:1") + 1] == "copy"
    assert cp.mkv_subtitle_codec("ass") == "copy" and cp.mkv_subtitle_codec("hdmv_pgs_subtitle") == "copy"
    assert cp.mkv_subtitle_codec("mov_text") == "srt" and cp.mkv_subtitle_codec("text") == "srt"


def _ffmpeg():
    return shutil.which("ffmpeg")


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not on PATH")
def test_a2_real_ffmpeg_mp4_with_mov_text_to_mkv(tmp_path):
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    src = str(tmp_path / "in.mp4")
    make = [_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1", "-f", "lavfi", "-i",
            "sine=frequency=440:duration=1", "-i", str(srt), "-map", "0:0", "-map", "1:0", "-map", "2:0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "mov_text", "-shortest", src]
    subprocess.run(make, check=True, timeout=120, stdin=subprocess.DEVNULL)
    m = cp.MediaInfo(src, os.path.getsize(src), 1.0, [
        cp.StreamInfo(0, "video", "h264", width=64, height=64), cp.StreamInfo(1, "audio", "aac"),
        cp.StreamInfo(2, "subtitle", "mov_text"),
    ])
    out = str(tmp_path / "out.mkv")
    args = cp.build_ffmpeg_args(m, cp.Plan(container=".mkv", reasons=["x"]), src, out)
    args[0] = _ffmpeg()
    done = subprocess.run(args, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    assert os.path.getsize(out) > 0
    probe = subprocess.run([os.path.join(os.path.dirname(_ffmpeg()), "ffprobe"), "-v", "error", "-select_streams", "s",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", out],
                           capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert "subrip" in probe.stdout


# --- A3: delete-after-conversion with a new extension renames first ------------------------------------------


def test_a3_delete_mode_keeps_the_original_when_the_result_cannot_take_its_name(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    lib.mkdir()
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, delete_after_conversion=True, probe=lambda p: GOOD)
    src = _old(lib / "a.avi", "orig")
    final = str(lib / "a.mkv")

    def writer(s, t):
        with open(t, "w", encoding="utf-8") as f:
            f.write("new")
        return True, ""

    import redactor_common.core.pipeline as pipeline

    def broken_rename(src_, dst):
        raise PermissionError(13, "locked")

    monkeypatch.setattr(pipeline, "_rename_no_clobber", broken_rename)
    with pytest.raises(ls.SafetyError):
        run.convert(src, final, writer, ls.KIND_REMUXED, "r")
    assert open(src, encoding="utf-8").read() == "orig"  # the original was never removed


def test_a3_delete_mode_happy_path_removes_the_original_only_after_the_rename(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, delete_after_conversion=True, probe=lambda p: GOOD)
    src = _old(lib / "a.avi", "orig")
    final = str(lib / "a.mkv")

    def writer(s, t):
        with open(t, "w", encoding="utf-8") as f:
            f.write("new")
        return True, ""

    assert run.convert(src, final, writer, ls.KIND_REMUXED, "r")
    assert not os.path.exists(src) and open(final, encoding="utf-8").read() == "new"
    assert sorted(os.listdir(lib)) == ["a.mkv"]


# --- A4: the swap is commit_in_place (lock retry) ---------------------------------------------------------------


def test_a4_swap_rides_out_a_transient_lock(tmp_path, monkeypatch):
    import redactor_common.core.os_utils as ou

    lib = tmp_path / "lib"
    lib.mkdir()
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None, probe=lambda p: GOOD)
    src = _old(lib / "a.mkv", "big")
    real_replace = os.replace
    failures = {"n": 2}

    def flaky_replace(a, b):
        if failures["n"] > 0:
            failures["n"] -= 1
            raise PermissionError(13, "held by a scanner")
        real_replace(a, b)

    monkeypatch.setattr(ou.os, "replace", flaky_replace)
    monkeypatch.setattr(ou.time, "sleep", lambda s: None)

    def writer(s, t):
        with open(t, "w", encoding="utf-8") as f:
            f.write("small")
        return True, ""

    assert run.convert(src, src, writer, ls.KIND_RESIZED, "r", marker=ls.RESIZE_MARKER)
    assert open(src, encoding="utf-8").read() == "small" and failures["n"] == 0


# --- A5: bitmap subtitles force MKV ------------------------------------------------------------------------------


def test_a5_bitmap_subtitles_force_mkv_with_a_reason():
    p = cp.plan_conversion(info(".avi", size=300 * MB, h=480, w=640, subs=("hdmv_pgs_subtitle",)))
    assert p.container == ".mkv" and p.does_something
    assert any("bitmap subtitle" in r for r in p.reasons)
    assert cr.expected_subtitle_count(info(".avi", subs=("dvd_subtitle",)), p) == 1  # nothing is dropped
    plain = cp.plan_conversion(info(".avi", size=300 * MB, h=480, w=640, subs=("subrip",)))
    assert plain.container == ".mp4"


# --- A10: UTF-16 .srt files --------------------------------------------------------------------------------------


def test_a10_utf16_bom_decodes_and_utf32_too():
    text = "1\n00:00:01,000 --> 00:00:02,000\nCaf\u00e9\n"
    assert si.decode_srt(text.encode("utf-16")) == text  # BOM FF FE
    assert si.decode_srt(b"\xfe\xff" + text.encode("utf-16-be")) == text
    assert si.decode_srt(text.encode("utf-32")) == text


def test_a10_utf16_without_bom_is_refused_not_garbled():
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello\n"
    with pytest.raises(si.SubtitleDecodeError):
        si.decode_srt(text.encode("utf-16-le"))  # no BOM: cp1252 would give H\x00e\x00...


def test_a10_an_unreadable_srt_is_left_alone_and_noted(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old(lib / "ep.mp4", "V" * 100)
    bad = lib / "ep.en.srt"
    bad.write_bytes("1\n00:00:01,000 --> 00:00:02,000\nHello\n".encode("utf-16-le"))
    media = info(".mp4", size=100, h=480, w=640)
    media.path = src
    run = ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None)
    out = cr.process_file(run, src, cp.ConversionSettings(), probe=lambda p: media, runner=lambda *a, **k: (0, ""))
    assert not out.converted and bad.exists()
    assert out.notes and "ep.en.srt" in out.notes[0] and "not ingested" in out.notes[0]


# --- A9: MP4 save goes through a scratch copy --------------------------------------------------------------------


def _tiny_mp4(path):
    """A minimal MP4 mutagen accepts (ftyp + moov with an empty udta)."""
    import struct

    def box(kind, payload=b""):
        return struct.pack(">I", 8 + len(payload)) + kind + payload

    mvhd = box(b"mvhd", bytes(100))
    moov = box(b"moov", mvhd + box(b"udta"))
    path.write_bytes(box(b"ftyp", b"isom" + bytes(4) + b"isom") + moov + box(b"mdat", bytes(64)))


def test_a9_mp4_write_uses_a_scratch_copy_and_keeps_the_original_on_failure(tmp_path, monkeypatch):
    pytest.importorskip("mutagen")
    from core import mp4_backend
    from core.video_metadata import VideoMetadata

    target = tmp_path / "v.mp4"
    _tiny_mp4(target)
    before = target.read_bytes()
    seen = {}

    def exploding(path, meta):
        seen["path"] = path
        raise RuntimeError("boom mid-write")

    monkeypatch.setattr(mp4_backend, "_apply_mp4_tags", exploding)
    with pytest.raises(RuntimeError):
        mp4_backend.write_mp4_metadata(str(target), VideoMetadata(title="T"))
    assert os.path.normcase(seen["path"]) != os.path.normcase(str(target))  # the edit hit the copy, not the file
    assert target.read_bytes() == before and os.listdir(tmp_path) == ["v.mp4"]  # untouched, no scratch left


@pytest.mark.skipif(not _ffmpeg(), reason="ffmpeg not on PATH")
def test_a9_mp4_write_round_trip_leaves_no_scratch(tmp_path):
    pytest.importorskip("mutagen")
    from core import mp4_backend
    from core.video_metadata import VideoMetadata

    target = tmp_path / "v.mp4"
    subprocess.run([_ffmpeg(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
                    "-c:v", "libx264", "-preset", "ultrafast", str(target)], check=True, timeout=120, stdin=subprocess.DEVNULL)
    mp4_backend.write_mp4_metadata(str(target), VideoMetadata(title="Written"))
    assert os.listdir(tmp_path) == ["v.mp4"]
    assert mp4_backend.read_mp4_metadata(str(target)).title == "Written"
