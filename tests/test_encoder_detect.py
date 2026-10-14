"""The HEVC encoder choice for the conversion rules' bitrate re-encodes (core/encoder_detect.py,
ConversionSettings.encoder / hw_decode / x265_crf): NVIDIA is no longer assumed. Every probe is injected, so no
test needs a GPU or ffmpeg."""

import os
import subprocess
import time

import pytest

from core import conversion_plan as cp
from core import conversion_run as cr
from core import encoder_detect as ed
from core.library_safety import SafeRun

GB = 1073741824


@pytest.fixture(autouse=True)
def _fresh_cache():
    ed.clear_cache()
    yield
    ed.clear_cache()


def _done(code=0, out=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr="")


LISTING = " V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)\n V....D libx265 ...\n"


def fake_ffmpeg(listed=True, encodes=True, calls=None):
    def run(args, timeout):
        if calls is not None:
            calls.append(list(args))
        if "-encoders" in args:
            return _done(0, LISTING if listed else " V....D libx265 libx265 H.265 / HEVC\n")
        assert args == ed.TEST_ARGS
        return _done(0 if encodes else 1)

    return run


# --- detection -------------------------------------------------------------------------------


def test_probe_needs_listed_and_a_working_test_encode():
    assert ed.probe_nvenc(fake_ffmpeg()) is True
    assert ed.probe_nvenc(fake_ffmpeg(listed=False)) is False
    assert ed.probe_nvenc(fake_ffmpeg(encodes=False)) is False  # listed, but no NVIDIA card
    assert ed.probe_nvenc(lambda args, timeout: None) is False  # ffmpeg missing / timed out


def test_the_test_encode_is_one_second_of_testsrc_into_null():
    assert ed.TEST_ARGS == [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=320x240:rate=25",
        "-c:v", "hevc_nvenc", "-f", "null", "-",
    ]
    assert ed.LIST_ARGS == ["ffmpeg", "-hide_banner", "-encoders"]


def test_detection_is_cached_and_rechecked_when_the_ffmpeg_path_changes():
    calls = []
    path = {"value": "C:/tools/ffmpeg.exe"}
    run = fake_ffmpeg(calls=calls)
    assert ed.nvenc_available(run=run, ffmpeg_path=lambda: path["value"]) is True
    assert ed.nvenc_available(run=run, ffmpeg_path=lambda: path["value"]) is True
    assert len(calls) == 2  # list + test encode, once
    path["value"] = "D:/other/ffmpeg.exe"
    no_gpu = fake_ffmpeg(encodes=False, calls=calls)
    assert ed.nvenc_available(run=no_gpu, ffmpeg_path=lambda: path["value"]) is False
    assert len(calls) == 4  # a new ffmpeg: checked again
    ed.clear_cache()
    assert ed.nvenc_available(run=run, ffmpeg_path=lambda: path["value"]) is True and len(calls) == 6


def test_resolve_encoder():
    yes, no = (lambda: True), (lambda: False)
    assert ed.resolve_encoder(cp.ConversionSettings(), available=no) == "x265"
    assert ed.resolve_encoder(cp.ConversionSettings(), available=yes) == "nvenc"
    assert ed.resolve_encoder(None, available=no) == "x265"  # the default is auto

    def never():
        raise AssertionError("a fixed choice must not probe")

    assert ed.resolve_encoder(cp.ConversionSettings(encoder="nvenc"), available=never) == "nvenc"
    assert ed.resolve_encoder(cp.ConversionSettings(encoder="x265"), available=never) == "x265"


# --- the conversion uses it --------------------------------------------------------------------


def _media(path, size=2 * GB, h=1080):
    streams = [cp.StreamInfo(0, "video", "h264", width=h * 16 // 9, height=h), cp.StreamInfo(1, "audio", "aac")]
    return cp.MediaInfo(path, size, 3000.0, streams)


def _scale_run(tmp_path, settings, available):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "ep.mkv"
    src.write_text("A" * 1000, encoding="utf-8")
    past = time.time() - 3600
    os.utime(src, (past, past))
    info = _media(str(src), size=1000)
    plan = cp.Plan(container=".mkv", video=cp.V_NVENC_SCALE, bitrate_driven=True, reasons=["scale"])
    calls = []

    def runner(args, cancel_event=None, timeout=None):
        calls.append(args)
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("B" * 500)
        return 0, ""

    def probe(path):
        return _media(path, size=500, h=720) if ".resize" in path else info

    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None, probe=probe)
    out = cr.convert_file(
        run, str(src), plan, info, settings, runner=runner, probe=probe,
        resolve_encoder=lambda s: ed.resolve_encoder(s, available=available),
    )
    return out, calls


def test_auto_without_a_working_nvenc_encodes_with_libx265(tmp_path):
    out, calls = _scale_run(tmp_path, cp.ConversionSettings(), lambda: False)
    assert out.converted
    [args] = calls
    assert args[args.index("-c:v") + 1] == "libx265" and args[args.index("-crf") + 1] == "27"
    assert "-hwaccel" not in args and "scale=-2:720" in args and "hevc_nvenc" not in args


def test_auto_with_a_working_nvenc_encodes_with_it_on_software_scaling(tmp_path):
    out, calls = _scale_run(tmp_path, cp.ConversionSettings(), lambda: True)
    assert out.converted
    [args] = calls  # hw_decode is off by default: one attempt, no CUDA
    assert args[args.index("-c:v") + 1] == "hevc_nvenc" and "-hwaccel" not in args and "scale=-2:720" in args


def test_hw_decode_on_with_nvenc_tries_cuda_first(tmp_path):
    out, calls = _scale_run(tmp_path, cp.ConversionSettings(hw_decode=True), lambda: True)
    assert out.converted and len(calls) == 1
    assert calls[0][calls[0].index("-hwaccel") + 1] == "cuda" and "scale_cuda=-2:720" in calls[0]


def test_hw_decode_on_but_no_nvenc_never_uses_cuda(tmp_path):
    out, calls = _scale_run(tmp_path, cp.ConversionSettings(hw_decode=True), lambda: False)
    assert out.converted and len(calls) == 1 and "-hwaccel" not in calls[0]


def test_a_remux_never_probes_the_encoder(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "ep.avi"
    src.write_text("A" * 100, encoding="utf-8")
    past = time.time() - 3600
    os.utime(src, (past, past))
    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None,
                  probe=lambda p: _media(p, size=100))

    def never(_settings):
        raise AssertionError("a stream copy needs no encoder")

    seen = []

    def fake_copy(args, cancel_event=None, timeout=None):
        seen.append(args)
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("C" * 100)
        return 0, ""

    out = cr.convert_file(
        run, str(src), cp.Plan(container=".mp4", reasons=["legacy"]), _media(str(src), size=100),
        cp.ConversionSettings(), runner=fake_copy, probe=lambda p: _media(p, size=100), resolve_encoder=never,
    )
    assert out.converted, out.message
    assert seen[0][seen[0].index("-c:v") + 1] == "copy"


# --- Preferences > Conversion ----------------------------------------------------------------


def test_preferences_conversion_page_shows_and_saves_the_encoder_settings():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from gui.video_preferences import ENCODER_HELP, HW_DECODE_HELP, HW_DECODE_LABEL, X265_CRF_HELP, ConversionPage

    app = QApplication.instance() or QApplication([])  # noqa: F841 -- kept referenced
    page = ConversionPage()
    assert page.encoder.currentData() == "auto" and page.hw_decode.isChecked() is False
    assert page.x265_crf.value() == 27
    assert page.hw_decode.text() == HW_DECODE_LABEL == "Use the NVIDIA GPU to decode and scale"
    assert "needs an NVIDIA card" in page.hw_decode_help.text() == HW_DECODE_HELP
    assert "test encode" in page.encoder_help.text() == ENCODER_HELP
    assert page.x265_crf_help.text() == X265_CRF_HELP
    page.encoder.setCurrentIndex(page.encoder.findData("x265"))
    page.x265_crf.setValue(24)
    page.hw_decode.setChecked(True)
    page.save()
    s = cp.get_conversion_settings()
    assert s.encoder == "x265" and s.x265_crf == 24 and s.hw_decode is True
    assert ConversionPage().encoder.currentData() == "x265"
