"""Review 2026-11-28, agent A1: keep-the-original beats delete-after, same-name results, attachments of the plan
(audio per stream, no upscale, size ratio), a tool override that is not a program, cancellable decode and subtitle
extraction, and a subtitle whose extra words start with a language name."""

import os
import subprocess
import sys
import threading
import time

import pytest

from core import conversion_plan as cp
from core import conversion_run as cr
from core import convert_modes as cm
from core import ffmpeg_backend as fb
from core import library_safety as ls
from core import subtitle_ingest as si

GB = 1073741824
MB = 1048576
GOOD = ({"video": 1, "audio": 1, "subtitle": 0}, 100.0)


def _old(path, text="x", age=3600):
    path.write_text(text, encoding="utf-8")
    t = time.time() - age
    os.utime(path, (t, t))
    return str(path)


def _writer(s, t):
    with open(t, "w", encoding="utf-8") as f:
        f.write("new")
    return True, ""


def _run(lib, **kw):
    return ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None,
                      probe=lambda p: GOOD, **kw)


# --- cards 1 and 2 -----------------------------------------------------------------------------------------------


def test_card1_explicit_keep_beats_delete_after_conversion(tmp_path):
    src = _old(tmp_path / "a.avi", "orig")
    run = _run(tmp_path, delete_after_conversion=True, keep_originals=True, keep_strict=True)
    assert run.convert(src, str(tmp_path / "a.mkv"), _writer, ls.KIND_REMUXED, "r")
    assert open(src, encoding="utf-8").read() == "orig"  # not deleted
    assert (tmp_path / "a.mkv").is_file()


def test_card1_convert_with_mode_turns_delete_after_into_a_keep(tmp_path, monkeypatch):
    src = _old(tmp_path / "a.mkv", "orig")
    run = _run(tmp_path, delete_after_conversion=True)
    info = cp.MediaInfo(src, 10, 10.0, [cp.StreamInfo(0, "video", "h264", width=640, height=360),
                                        cp.StreamInfo(1, "audio", "aac")])
    monkeypatch.setattr(cr, "read_source_cover", lambda *a, **k: (None, ""))
    monkeypatch.setattr(cr, "read_source_tags", lambda *a, **k: None)
    monkeypatch.setattr(cr, "convert_file", lambda r, source, plan, i, conv, final="", **k: (
        r.convert(source, final, _writer, ls.KIND_REMUXED, "r"), cr.ConversionOutcome(True, final_path=final))[1])
    outcome = cm.convert_with_mode(run, src, cm.MODE_REMUX, cp.ConversionSettings(), keep=True, probe=lambda p: info)
    assert outcome.converted
    assert open(src, encoding="utf-8").read() == "orig" and (tmp_path / "a.mp4").is_file()


def test_card2_keep_refuses_a_result_with_the_originals_name(tmp_path):
    src = _old(tmp_path / "Show.mp4", "orig")
    run = _run(tmp_path, keep_originals=True, keep_strict=True)
    assert not run.convert(src, src, _writer, ls.KIND_RESIZED, "r")
    assert open(src, encoding="utf-8").read() == "orig" and sorted(os.listdir(tmp_path)) == ["Show.mp4"]
    assert "same name" in run.actions[-1].reason


def test_without_keep_a_same_name_result_still_replaces_the_original(tmp_path):
    src = _old(tmp_path / "Show.mp4", "orig")
    run = _run(tmp_path)
    assert run.convert(src, src, _writer, ls.KIND_RESIZED, "r")
    assert open(src, encoding="utf-8").read() == "new"


# --- card 7 ------------------------------------------------------------------------------------------------------


def test_card7_only_the_odd_audio_track_is_reencoded():
    streams = [cp.StreamInfo(0, "video", "h264", width=1280, height=720),
               cp.StreamInfo(1, "audio", "wavpack", bit_rate=500_000), cp.StreamInfo(2, "audio", "ac3", bit_rate=640_000)]
    m = cp.MediaInfo("F:/Video/Show/ep.mkv", 300 * MB, 3000.0, streams)
    plan = cp.plan_conversion(m, extra_subtitles=1)
    assert plan.audio_reencode and plan.audio_reencode_indices == [1]
    args = cp.build_ffmpeg_args(m, plan, "in.mkv", "out.mkv")
    assert args[args.index("-c:a:0") + 1] == "aac" and args[args.index("-c:a:1") + 1] == "copy"
    assert "-c:a" not in args


# --- card 20 and 37 -----------------------------------------------------------------------------------------------


def test_card20_the_ratio_is_clamped_on_read():
    big = cp.ConversionSettings(output_max_ratio=1.2)
    assert not cp.output_size_ok(100, 110, big)  # a larger result never replaces the source
    assert cp.output_size_ok(100, 90, big)
    nan = cp.ConversionSettings(output_max_ratio=float("nan"))
    assert cp.output_size_ok(100, 80, nan) and not cp.output_size_ok(100, 90, nan)


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_card20_the_preference_box_stops_at_100(qapp, tmp_path, monkeypatch):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    from gui.video_preferences import ConversionPage  # noqa: PLC0415

    page = ConversionPage()
    assert page.ratio.maximum() == 100 and page.ratio.minimum() == 1


def test_card37_a_wide_but_short_series_video_is_not_scaled_up():
    s = cp.ConversionSettings()
    rule = cp.bitrate_rule(696, 1920, 2 * GB, 12.0, s, is_series=True, kind=cp.KIND_SERIES)
    assert not rule.scale
    tall = cp.bitrate_rule(1080, 1920, 2 * GB, 12.0, s, is_series=True, kind=cp.KIND_SERIES)
    assert tall.scale and tall.target_height == s.scale_height


# --- card 36 ------------------------------------------------------------------------------------------------------


def test_card36_a_tool_that_is_not_a_program_returns_none(monkeypatch):
    def boom(*a, **k):
        raise PermissionError(13, "access denied")

    monkeypatch.setattr(fb, "run_tool", boom)
    assert fb._run(["ffprobe", "-version"]) is None
    monkeypatch.setattr(fb, "popen_tool", boom)
    code, message = fb._run_ffmpeg(["ffmpeg", "-version"])
    assert code is None and message


# --- card 61 ------------------------------------------------------------------------------------------------------


def _fake_ffmpeg(monkeypatch, tmp_path, body):
    script = tmp_path / "fake.py"
    script.write_text(body, encoding="utf-8")
    monkeypatch.setattr(fb, "get_executable_path", lambda name: sys.executable)
    return str(script)


def test_card61_decode_check_can_be_cancelled(monkeypatch, tmp_path):
    script = _fake_ffmpeg(monkeypatch, tmp_path, "import time\ntime.sleep(60)\n")
    real = fb._run_ffmpeg
    monkeypatch.setattr(fb, "_run_ffmpeg", lambda args, **k: real(["ffmpeg", script], **k))
    cancel = threading.Event()
    threading.Timer(1.0, cancel.set).start()
    started = time.time()
    problem = cr.decode_problem("whatever.mkv", cancel_event=cancel)
    assert time.time() - started < 15 and "did not finish" in problem and "Cancelled" in problem


def test_card61_the_decode_ffmpeg_is_known_to_the_close_time_kill(monkeypatch, tmp_path):
    script = _fake_ffmpeg(monkeypatch, tmp_path, "import time\ntime.sleep(60)\n")
    real = fb._run_ffmpeg
    monkeypatch.setattr(fb, "_run_ffmpeg", lambda args, **k: real(["ffmpeg", script], **k))
    result = {}
    t = threading.Thread(target=lambda: result.setdefault("p", cr.decode_problem("x.mkv")))
    t.start()
    deadline = time.time() + 10
    while not fb._live_processes and time.time() < deadline:
        time.sleep(0.1)
    assert fb.kill_running_ffmpeg() == 1
    t.join(15)
    assert not t.is_alive() and "did not finish" in result["p"]


def test_card61_subtitle_extraction_reads_stdout_through_the_cancellable_path(monkeypatch, tmp_path):
    script = _fake_ffmpeg(monkeypatch, tmp_path, "print('1\\n00:00:01,000 --> 00:00:02,000\\nhello')\n")
    real = fb._run_ffmpeg
    monkeypatch.setattr(fb, "_run_ffmpeg", lambda args, **k: real(["ffmpeg", script], **k))
    m = cp.MediaInfo("x.mkv", 1, 1.0, [cp.StreamInfo(0, "video", "h264"), cp.StreamInfo(1, "subtitle", "subrip")])
    texts = cr.embedded_subtitle_texts(m, "x.mkv", cancel_event=threading.Event())
    assert len(texts) == 1 and "hello" in texts[0]


# --- card 72 ------------------------------------------------------------------------------------------------------


def test_card72_another_videos_subtitle_is_not_adopted():
    video = "F:/Video/Show/Show S01E01.mkv"
    assert si.parse_subtitle_name(video, "F:/Video/Show/Show S01E01 - French Kiss.en.srt") is None
    assert si.parse_subtitle_name(video, "F:/Video/Show/Show S01E01 - French.srt").language == "fre"
    assert si.parse_subtitle_name(video, "F:/Video/Show/Show S01E01.en.forced.srt").language == "eng"


# --- card 5 -------------------------------------------------------------------------------------------------------


def _mkv_with_font():
    streams = [cp.StreamInfo(0, "video", "h264", width=1280, height=720), cp.StreamInfo(1, "audio", "aac"),
               cp.StreamInfo(2, "subtitle", "ass"),
               cp.StreamInfo(3, "attachment", "ttf", filename="arial.ttf"),
               cp.StreamInfo(4, "attachment", "mjpeg", filename="cover.jpg")]
    return cp.MediaInfo("F:/Video/Show/ep.mkv", 300 * MB, 3000.0, streams)


def test_card5_an_mkv_result_keeps_the_fonts_but_not_the_poster_twice():
    m = _mkv_with_font()
    plan = cp.plan_conversion(m, extra_subtitles=1)
    args = cp.build_ffmpeg_args(m, plan, "in.mkv", "out.mkv")
    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert "0:3" in maps and "0:4" not in maps  # the font yes; the poster is restored separately


def test_card5_verify_result_notices_a_lost_attachment():
    m = _mkv_with_font()
    plan = cp.plan_conversion(m, extra_subtitles=1)
    lost = cp.MediaInfo("out.mkv", 1, 3000.0, [s for s in m.streams if s.kind != "attachment"])
    assert "attachments" in cr.verify_result(m, lost, plan, cp.ConversionSettings(), added_subtitles=1)
    kept = cp.MediaInfo("out.mkv", 1, 3000.0, [s for s in m.streams if s.filename != "arial.ttf"] + [
        cp.StreamInfo(5, "attachment", "ttf", filename="arial.ttf")])
    assert "attachments" not in cr.verify_result(m, kept, plan, cp.ConversionSettings(), added_subtitles=1)


def test_card5_the_lossless_mp4_remux_refuses_ass_and_fonts():
    assert "ASS" in cm.plan_remux(cp.MediaInfo("a.mkv", 1, 1.0, [cp.StreamInfo(0, "video", "h264"),
                                                                 cp.StreamInfo(1, "subtitle", "ass")])).skip_reason
    assert "arial.ttf" in cm.plan_remux(_mkv_with_font_no_ass()).skip_reason


def _mkv_with_font_no_ass():
    return cp.MediaInfo("a.mkv", 1, 1.0, [cp.StreamInfo(0, "video", "h264"),
                                          cp.StreamInfo(1, "attachment", "ttf", filename="arial.ttf")])
