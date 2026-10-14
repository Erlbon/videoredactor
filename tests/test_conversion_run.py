"""core/conversion_run: verification rules and the runner, with ffmpeg and ffprobe faked."""

import os
import time

from core import conversion_plan as cp
from core import conversion_run as cr
from core.library_safety import SafeRun

GB = 1073741824
S = cp.ConversionSettings()
GPU = cp.ConversionSettings(encoder="nvenc", hw_decode=True)  # the NVIDIA path with GPU decode/scale


def media(path, *, ext_size=2 * GB, dur=3000.0, h=1080, audio=1, subs=0, sub_codec="subrip", vdur=None, adur=None):
    streams = [cp.StreamInfo(0, "video", "h264", width=h * 16 // 9, height=h, duration=vdur)]
    for _ in range(audio):
        streams.append(cp.StreamInfo(len(streams), "audio", "aac", duration=adur))
    for _ in range(subs):
        streams.append(cp.StreamInfo(len(streams), "subtitle", sub_codec))
    return cp.MediaInfo(path, ext_size, dur, streams)


def scale_plan():
    return cp.Plan(container=".mkv", video=cp.V_NVENC_SCALE, bitrate_driven=True, reasons=["scale"])


def test_good_scale_passes():
    src = media("a.mkv", subs=1)
    out = media("a.mkv", ext_size=GB, h=720, subs=1)
    assert cr.verify_result(src, out, scale_plan(), S, result_size=GB) == ""


def test_height_track_counts_and_subtitles():
    src = media("a.mkv", subs=1, audio=2)
    bad = media("a.mkv", h=1080, audio=1, subs=1)
    problem = cr.verify_result(src, bad, scale_plan(), S, result_size=GB)
    assert "audio tracks" in problem and "height 1080" in problem
    added = media("a.mkv", h=720, audio=2, subs=3)
    assert cr.verify_result(src, added, scale_plan(), S, added_subtitles=2, result_size=GB) == ""  # existing + added
    assert "subtitle tracks: expected 1, got 3" in cr.verify_result(src, added, scale_plan(), S, result_size=GB)


def test_subtitle_count_must_equal_existing_plus_added():
    src = media("a.mkv", subs=1)
    out = media("a.mkv", h=720, subs=1)
    assert "subtitle tracks: expected 3" in cr.verify_result(src, out, scale_plan(), S, added_subtitles=2, result_size=GB)


def test_mp4_target_drops_bitmap_subtitles_from_the_expectation():
    src = media("a.mkv", subs=2, sub_codec="hdmv_pgs_subtitle")
    plan = cp.Plan(container=".mp4", reasons=["x"])
    assert cr.expected_subtitle_count(src, plan) == 0
    assert cr.expected_subtitle_count(src, cp.Plan(container=".mkv", reasons=["x"])) == 2


def test_duration_tolerance_and_stale_avi_header():
    src = media("a.mkv")
    plan = cp.Plan(container=".mkv", reasons=["x"])
    assert cr.verify_result(src, media("a.mkv", dur=3001.4), plan, S) == ""
    assert "duration" in cr.verify_result(src, media("a.mkv", dur=3001.6), plan, S)
    avi = media("a.avi")
    stale = media("a.mkv", dur=3030.0, vdur=3030.0, adur=3030.5)
    assert cr.verify_result(avi, stale, plan, S) == ""  # stale header, streams agree
    assert "duration" in cr.verify_result(avi, media("a.mkv", dur=3030.0, vdur=3030.0, adur=3010.0), plan, S)
    assert "duration" in cr.verify_result(avi, media("a.mkv", dur=3100.0, vdur=3100.0, adur=3100.0), plan, S)  # over 60 s
    assert "duration" in cr.verify_result(src, stale, plan, S)  # not an AVI/OGM source


def test_bitrate_driven_result_must_be_under_85_percent():
    src = media("a.mkv")
    out = media("a.mkv", h=720)
    assert "of the source" in cr.verify_result(src, out, scale_plan(), S, result_size=int(2 * GB * 0.9))
    remux = cp.Plan(container=".mkv", reasons=["x"])  # a plain remux is exempt
    assert cr.verify_result(src, media("a.mkv"), remux, S, result_size=2 * GB) == ""


def test_unreadable_result_fails():
    assert "ffprobe" in cr.verify_result(media("a.mkv"), None, scale_plan(), S)


def _file(path, text="x", age=3600):
    path.write_text(text, encoding="utf-8")
    past = time.time() - age
    os.utime(path, (past, past))
    return str(path)


def test_convert_file_end_to_end_with_cuda_fallback(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.mkv", "A" * 1000)
    info = media(src, ext_size=1000, subs=1)
    plan = scale_plan()
    calls = []

    def runner(args, cancel_event=None, timeout=None):
        calls.append(args)
        if "-hwaccel" in args:
            return 1, "cuda not available"  # first attempt fails, software fallback must follow
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 500)
        return 0, ""

    def probe(path):
        return media(path, ext_size=500, h=720, subs=1) if ".resize" in path else info

    trashed = []
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=trashed.append, probe=probe)
    out = cr.convert_file(run, src, plan, info, GPU, runner=runner, probe=probe)
    assert out.converted and out.final_path == src
    assert len(calls) == 2 and "-hwaccel" in calls[0] and "-hwaccel" not in calls[1]
    assert open(src, encoding="utf-8").read() == "B" * 500
    assert len(trashed) == 1 and open(trashed[0], encoding="utf-8").read() == "A" * 1000
    assert not any(".resize" in n for n in os.listdir(lib))


def test_convert_file_keeps_source_when_result_is_too_big(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.mkv", "A" * 1000)
    info = media(src, ext_size=1000)

    def runner(args, cancel_event=None, timeout=None):
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 950)
        return 0, ""

    probe = lambda p: media(p, ext_size=950, h=720) if ".resize" in p else info  # noqa: E731
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None, probe=probe)
    out = cr.convert_file(run, src, scale_plan(), info, GPU, runner=runner, probe=probe)
    assert not out.converted and "of the source" in out.message
    assert open(src, encoding="utf-8").read() == "A" * 1000 and os.listdir(lib) == ["ep.mkv"]


def test_convert_file_skip_and_dry_run(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.avi", "A" * 100)
    info = media(src, ext_size=100)
    assert not cr.convert_file(SafeRun(str(lib), ""), src, cp.Plan(skip_reason="disc image"), info, S).converted
    ran = []
    plan = cp.Plan(container=".mp4", reasons=["legacy container .avi"])
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=True)
    out = cr.convert_file(run, src, plan, info, S, runner=lambda *a, **k: ran.append(a) or (0, ""))
    assert out.converted and not ran and os.listdir(lib) == ["ep.avi"]  # dry run: nothing run, nothing written


def test_process_file_ingests_srt_converts_mp4_to_mkv_and_removes_the_srt(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "Show.S01E01.mp4", "V" * 1000)
    en = _file(lib / "Show.S01E01.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    nl = _file(lib / "Show.S01E01.nl.srt", "1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
    info = media(src, ext_size=300 * 1048576, h=720)
    seen = {}

    def runner(args, cancel_event=None, timeout=None):
        seen["args"] = args
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("MKV")
        return 0, ""

    def probe(path):
        return media(path, ext_size=3, h=720, subs=2) if ".part" in path else info

    trashed = []
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=trashed.append, probe=probe)
    out = cr.process_file(run, src, S, probe=probe, runner=runner)
    assert out.converted and out.final_path.endswith("Show.S01E01.mkv")
    args = seen["args"]
    assert args.count("-i") == 3 and "language=eng" in args and "language=dut" in args
    assert args[args.index("-disposition:s:0") + 1] == "default"  # first plain English, video had no subtitles
    assert os.path.exists(out.final_path) and not os.path.exists(src)
    assert sorted(os.path.basename(p) for p in trashed) == sorted(["Show.S01E01.redact-orig.mp4", "Show.S01E01.en.srt", "Show.S01E01.nl.srt"])


def test_process_file_skips_a_duplicate_of_an_embedded_track_and_leaves_it(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.mkv", "V" * 100)
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello there my friend\n"
    srt = _file(lib / "ep.en.srt", text)
    info = media(src, ext_size=100, h=720, subs=1)
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None)
    out = cr.process_file(run, src, S, probe=lambda p: info, embedded_texts=lambda i, s: [text], runner=lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ffmpeg run expected")))
    assert not out.converted and os.path.exists(srt) and os.path.exists(src)


def test_process_file_dry_run_lists_actions_and_writes_nothing(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.avi", "V" * 100)
    srt = _file(lib / "ep.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    info = media(src, ext_size=100, h=480)
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=True)
    out = cr.process_file(run, src, S, probe=lambda p: info)
    assert out.converted and sorted(os.listdir(lib)) == ["ep.avi", "ep.en.srt"]
    verbs = [a.verb for a in run.actions]
    assert "convert" in verbs and "recycle" in verbs


def test_dispose_deletes_only_with_the_setting(tmp_path):
    f = _file(tmp_path / "a.srt")
    run = SafeRun(library_root=str(tmp_path), quarantine_root="", dry_run=False, trash=lambda p: None, delete_after_conversion=True)
    assert run.dispose(f, "cleanup", "r") and not os.path.exists(f)
