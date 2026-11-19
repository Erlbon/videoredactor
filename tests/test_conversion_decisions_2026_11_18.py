"""The conversion decisions of 2026-11-18 (decision page questions 9-18, plus the settled music-video rule and the
subtitle fix from his conversion prompt), with ffmpeg and ffprobe faked."""

import os
import threading
import time

from core import conversion_plan as cp
from core import conversion_run as cr
from core import convert_preview as pv
from core.library_safety import SafeRun
from core.redact_steps import conversion_kind
from core.video_metadata import ContentType

GB = 1073741824
MB = 1048576


def info(ext=".mkv", size=2 * GB, dur=3000.0, vcodec="h264", w=1920, h=1080, audio=("aac",), subs=()):
    streams = [cp.StreamInfo(0, "video", vcodec, width=w, height=h)]
    for codec in audio:
        streams.append(cp.StreamInfo(len(streams), "audio", codec, bit_rate=160_000))
    for codec in subs:
        streams.append(cp.StreamInfo(len(streams), "subtitle", codec))
    return cp.MediaInfo("F:/Video/x" + ext, size, dur, streams)


def _file(path, text="x", age=3600):
    path.write_text(text, encoding="utf-8")
    past = time.time() - age
    os.utime(path, (past, past))
    return str(path)


# --- subtitle streams without a codec name (his prompt's rule) ---------------------------------------------------


def test_unnamed_subtitle_streams_are_not_mapped_or_counted():
    m = info(".mkv", subs=("subrip", "", "hdmv_pgs_subtitle"))
    plan = cp.Plan(container=".mkv", reasons=["x"])
    args = cp.build_ffmpeg_args(m, plan, "in.mkv", "out.mkv")
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert "0:3" not in maps and "0:2" in maps and "0:4" in maps  # stream 1 is the audio
    assert "-c:s:2" not in args  # two subtitle streams carried, so positions 0 and 1 only
    assert cr.expected_subtitle_count(m, plan) == 2
    assert len(cp.mappable_subtitles(m)) == 2


def test_unnamed_subtitles_do_not_count_towards_many_tracks():
    m = info(".mp4", size=300 * MB, h=720, w=1280, subs=("mov_text", "", "", ""))
    assert cp.plan_conversion(m).container == ".mp4"  # 1 real track, not 4


# --- 15A x265 preset ---------------------------------------------------------------------------------------------


def test_one_x265_preset_for_both_uses():
    s = cp.ConversionSettings(x265_preset="slow")
    legacy = info(".rmvb", size=300 * MB, h=480, w=640, vcodec="rv40")
    args = cp.build_ffmpeg_args(legacy, cp.plan_conversion(legacy, s), "in.rmvb", "out.mp4", s)
    assert args[args.index("-preset") + 1] == "slow"
    shrink = cp.Plan(container=".mkv", video=cp.V_NVENC_SCALE, bitrate_driven=True, reasons=["x"])
    args = cp.build_ffmpeg_args(info(), shrink, "in.mkv", "out.mkv", s, encoder="x265")
    assert args[args.index("-preset") + 1] == "slow"


def test_settings_validate_the_new_fields():
    values = {"x265_preset": "warp", "remux_jobs": "0", "encode_jobs": "99", "max_height_movie": "1000",
              "max_height_other": "1080"}
    s = cp.get_conversion_settings(lambda section, key, default="": values.get(key, ""))
    assert s.x265_preset == "medium" and s.remux_jobs == 4 and s.encode_jobs == 2
    assert s.max_height_movie == 0 and s.max_height_other == 1080


# --- 17A highest resolution kept, per content type ---------------------------------------------------------------


def test_kind_from_content_type_and_name():
    assert conversion_kind(ContentType.TV, "anything") == cp.KIND_SERIES
    assert conversion_kind(None, "Show.S01E02") == cp.KIND_SERIES
    assert conversion_kind(ContentType.MOVIE, "Film") == cp.KIND_MOVIE
    assert conversion_kind(ContentType.MUSIC_VIDEO, "Song") == cp.KIND_MUSIC_VIDEO
    assert conversion_kind(ContentType.CLIP, "c") == cp.KIND_OTHER
    assert conversion_kind(None, "holiday") == cp.KIND_OTHER


def test_the_ceiling_only_matters_when_the_bitrate_rule_fires():
    s = cp.ConversionSettings(apply_to_movies=True, max_height_movie=1080, max_height_other=720)
    fat_4k = info(size=8 * GB, h=2160, w=3840)
    plan = cp.plan_conversion(fat_4k, s, kind=cp.KIND_MOVIE)
    assert plan.scales and plan.target_height == 1080 and "scale to 1080p" in plan.reasons[-1]
    fat_1080 = cp.plan_conversion(info(size=4 * GB), s, kind=cp.KIND_MOVIE)
    assert fat_1080.video == cp.V_NVENC and "already at most 1080p" in fat_1080.reasons[-1]
    lean = info(size=900 * MB, dur=3000.0)  # 1080p at about 2.3 Mb/s: under 1 GB and 4 Mb/s
    assert not cp.plan_conversion(lean, s, kind=cp.KIND_OTHER).does_something
    other = cp.plan_conversion(info(size=4 * GB), s, kind=cp.KIND_OTHER)
    assert other.target_height == 720
    args = cp.build_ffmpeg_args(fat_4k, plan, "in.mkv", "out.mkv", s, encoder="x265")
    assert args[args.index("-vf") + 1] == "scale=-2:1080"


def test_series_keep_scaling_to_the_series_height():
    plan = cp.plan_conversion(info(size=3 * GB), kind=cp.KIND_SERIES)
    assert plan.scales and plan.scale_to(cp.ConversionSettings()) == 720


# --- the settled music-video rule ----------------------------------------------------------------------------------


def test_music_videos_keep_4k_below_their_own_limits():
    s = cp.ConversionSettings()
    lean = info(size=int(15.5e6 * 240 / 8), dur=240.0, h=2160, w=3840)  # 15.5 Mb/s, about 465 MB
    assert not cp.plan_conversion(lean, s, kind=cp.KIND_MUSIC_VIDEO).does_something
    fat = info(size=int(30e6 * 240 / 8), dur=240.0, h=2160, w=3840)  # 30 Mb/s H.264: over 25
    plan = cp.plan_conversion(fat, s, kind=cp.KIND_MUSIC_VIDEO)
    assert plan.video == cp.V_NVENC and not plan.scales and "4K kept" in plan.reasons[-1]
    hevc = info(size=int(30e6 * 240 / 8), dur=240.0, h=2160, w=3840, vcodec="hevc")  # 30 Mb/s HEVC: under 35
    assert not cp.plan_conversion(hevc, s, kind=cp.KIND_MUSIC_VIDEO).does_something
    hd = info(size=3 * GB, dur=240.0)  # a fat 1080p music video: left alone, no forced 720p
    assert not cp.plan_conversion(hd, s, kind=cp.KIND_MUSIC_VIDEO).does_something


def test_music_videos_to_1080p_is_a_per_run_choice():
    lean = info(size=int(15.5e6 * 240 / 8), dur=240.0, h=2160, w=3840)
    plan = cp.plan_conversion(lean, kind=cp.KIND_MUSIC_VIDEO, options=cp.RunOptions(music_video_1080=True))
    assert plan.scales and plan.target_height == 1080
    hd = info(size=500 * MB, dur=240.0)
    assert not cp.plan_conversion(hd, kind=cp.KIND_MUSIC_VIDEO, options=cp.RunOptions(music_video_1080=True)).does_something


# --- 10A fallback chain --------------------------------------------------------------------------------------------


def test_shrink_attempts_follow_his_chain():
    gpu = cp.ConversionSettings(encoder="nvenc", hw_decode=True)
    scale = cp.Plan(container=".mkv", video=cp.V_NVENC_SCALE, reasons=["x"])
    assert cr.shrink_attempts(scale, gpu, cp.ENCODER_NVENC) == [(True, False), (True, True), (False, False), (False, True)]
    assert cr.shrink_attempts(scale, cp.ConversionSettings(), cp.ENCODER_NVENC) == [(False, False), (False, True)]
    aac_already = cp.Plan(container=".mkv", video=cp.V_NVENC, audio_reencode=True, reasons=["x"])
    assert cr.shrink_attempts(aac_already, gpu, cp.ENCODER_NVENC) == [(False, False)]
    remux = cp.Plan(container=".mp4", reasons=["x"])
    assert cr.shrink_attempts(remux, gpu, cp.ENCODER_NVENC) == [(False, False)]


def test_audio_fallback_is_aac_192k(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "ep.mkv", "A" * 1000)
    source = info(".mkv", size=1000)
    source.path = src
    plan = cp.Plan(container=".mkv", video=cp.V_NVENC, bitrate_driven=True, reasons=["x"])
    calls = []

    def runner(args, cancel_event=None, timeout=None):
        calls.append(args)
        if args[args.index("-c:a") + 1] == "copy":
            return 1, "Could not write header: audio"
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 500)
        return 0, ""

    def probe(path):
        out = info(".mkv", size=500)
        out.path = path
        return out if ".redact-resize" in path else source

    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: os.remove(p), probe=probe)
    out = cr.convert_file(run, src, plan, source, cp.ConversionSettings(encoder="x265"), runner=runner, probe=probe)
    assert out.converted and len(calls) == 2
    assert calls[1][calls[1].index("-b:a") + 1] == "192k"
    assert out.notes == ["audio re-encoded to AAC 192k (copying it failed)"]


# --- 9A lenient mode -------------------------------------------------------------------------------------------------


def test_lenient_args():
    args = cp.build_ffmpeg_args(info(), cp.Plan(container=".mkv", reasons=["x"]), "in.mkv", "out.mkv", lenient=True)
    assert "-err_detect" in args and args[args.index("-fflags") + 1] == "+genpts+discardcorrupt"
    assert args[args.index("-max_error_rate") + 1] == "1.0"
    assert args.index("-err_detect") < args.index("-i")


def _lenient_case(tmp_path, result_duration):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _file(lib / "cap.avi", "A" * 1000)
    source = info(".avi", size=1000, dur=1000.0, vcodec="mpeg4", audio=("mp3",))
    source.path = src
    plan = cp.plan_conversion(source)

    def runner(args, cancel_event=None, timeout=None):
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 900)
        return 0, ""

    def probe(path):
        out = info(".mp4", size=900, dur=result_duration, vcodec="mpeg4", audio=("mp3",))
        out.path = path
        return out

    trashed = []
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=trashed.append, probe=probe)
    out = cr.convert_file(run, src, plan, source, cp.ConversionSettings(), runner=runner, probe=probe,
                          options=cp.RunOptions(lenient=True))
    return lib, src, out, trashed


def test_a_partial_result_is_kept_beside_the_untouched_original(tmp_path):
    lib, src, out, trashed = _lenient_case(tmp_path, 700.0)  # 70%
    assert not out.converted and out.partial_path.endswith("cap.partial.mp4")
    assert "PARTIAL" in out.message and "70%" in out.message
    assert sorted(os.listdir(lib)) == ["cap.avi", "cap.partial.mp4"]
    assert open(src, encoding="utf-8").read() == "A" * 1000 and trashed == []


def test_too_short_a_lenient_result_is_thrown_away(tmp_path):
    lib, _src, out, _trashed = _lenient_case(tmp_path, 500.0)  # 50%: under 60
    assert not out.converted and not out.partial_path and "duration" in out.message
    assert os.listdir(lib) == ["cap.avi"]


def test_a_full_length_lenient_result_replaces_the_original(tmp_path):
    lib, _src, out, trashed = _lenient_case(tmp_path, 1000.0)
    assert out.converted and not out.partial_path and "cap.mp4" in os.listdir(lib) and len(trashed) == 1


# --- 11B job limits ----------------------------------------------------------------------------------------------------


def test_job_limits_cap_encodes_and_remuxes_separately():
    limits = cr.JobLimits(remux=3, encode=1)
    encode = cp.Plan(video=cp.V_NVENC, container=".mkv", reasons=["x"])
    remux = cp.Plan(container=".mp4", reasons=["x"])
    assert limits.slot(encode) is limits.encode and limits.slot(remux) is limits.remux
    assert limits.encode.acquire(blocking=False) and not limits.encode.acquire(blocking=False)
    assert all(limits.remux.acquire(blocking=False) for _ in range(3)) and not limits.remux.acquire(blocking=False)


def test_convert_file_holds_a_slot_while_ffmpeg_runs(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    limits = cr.JobLimits(remux=1, encode=1)
    seen = []

    def runner(args, cancel_event=None, timeout=None):
        seen.append(limits.remux.acquire(blocking=False))  # the slot is taken: False
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B")
        return 0, ""

    src = _file(lib / "a.avi")
    source = info(".avi", size=1, vcodec="mpeg4", audio=("mp3",))
    source.path = src

    def probe(path):
        out = info(".mp4", size=1, vcodec="mpeg4", audio=("mp3",))
        out.path = path
        return out

    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None, probe=probe)
    cr.convert_file(run, src, cp.plan_conversion(source), source, cp.ConversionSettings(), runner=runner, probe=probe,
                    limits=limits)
    assert seen == [False] and limits.remux.acquire(blocking=False)  # released afterwards


# --- 14B sizes -------------------------------------------------------------------------------------------------------


def test_size_texts():
    a = cr.ConversionOutcome(True, source_bytes=3 * GB, result_bytes=GB)
    b = cr.ConversionOutcome(True, source_bytes=GB, result_bytes=GB // 2)
    assert cr.size_text(a) == "3.0 GB \u2192 1.0 GB"
    assert cr.size_summary([a, b, cr.ConversionOutcome(False)]) == "4.0 GB \u2192 1.5 GB, saved 2.5 GB (62%)"
    assert cr.size_summary([cr.ConversionOutcome(True, source_bytes=GB // 2, result_bytes=GB)]).endswith("grew by 0.50 GB")
    assert cr.size_summary([]) == ""


# --- 16A unknown legacy video ------------------------------------------------------------------------------------------


def test_unknown_legacy_video_is_reencoded_with_aac():
    m = info(".wmv", size=300 * MB, h=480, w=640, vcodec="vp6f", audio=("wmav2",))
    plan = cp.plan_conversion(m)
    assert plan.video == cp.V_X265
    args = cp.build_ffmpeg_args(m, plan, "in.wmv", "out.mkv")
    assert args[args.index("-c:a") + 1] == "aac" and "160k" in args


# --- 12B / 13A preview ------------------------------------------------------------------------------------------------


def test_preview_rows_and_routes():
    s = cp.ConversionSettings()
    m = info(size=3 * GB)
    plan = cp.plan_conversion(m, s)
    row = pv.preview_row("F:/x.mkv", cr.ConversionOutcome(True, media=m, plan=plan), s)
    assert row.route == pv.ROUTE_SHRINK and row.resolution == "1080p" and row.codec == "h264"
    assert row.action.startswith("\u2192 720p HEVC") and row.changes and row.mbps and row.mbps > 7
    skip = pv.preview_row("F:/y.mkv", cr.ConversionOutcome(False, message="left alone", media=m, plan=cp.Plan(skip_reason="no")), s)
    assert skip.route == pv.ROUTE_SKIP and not skip.changes


def test_would_convert_column():
    s = cp.ConversionSettings()
    fat = 3 * GB
    assert pv.would_convert(".mkv", "h264", 1080, 1920, fat, 8.0, cp.KIND_SERIES, s) == "\u2192 720p"
    assert pv.would_convert(".mkv", "h264", 720, 1280, fat, 5.0, cp.KIND_SERIES, s) == pv.WOULD_REENCODE
    assert pv.would_convert(".avi", "mpeg4", 480, 640, 300 * MB, 1.0, cp.KIND_SERIES, s) == pv.WOULD_REMUX
    assert pv.would_convert(".mkv", "h264", 1080, 1920, fat, 8.0, cp.KIND_MOVIE, s) == pv.WOULD_NO
    assert pv.would_convert(".rm", "rv40", 480, 640, 300 * MB, 1.0, cp.KIND_SERIES, s) == pv.WOULD_REENCODE
    assert pv.would_convert(".iso", "", 0, 0, fat, None, cp.KIND_SERIES, s) == pv.WOULD_NO
