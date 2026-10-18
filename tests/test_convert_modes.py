"""Media > Convert: the three modes (automatic rules, lossless remux, re-encode to H.264) through the one safe engine
(core/convert_modes.py), the shared conversion policy laid over the safety settings, and the Convert dialog (offscreen,
real small files made by ffmpeg)."""

import dataclasses
import os
import shutil
import subprocess

import pytest

import core.config as config
from core import conversion_plan as cp
from core import convert_modes as cm
from core import library_safety
from core.redact_steps import make_safe_run
from redactor_common.core import conversion_policy as policy_mod
from tests.track_language_helpers import make_mkv, make_mp4

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture
def safety(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    return dataclasses.replace(library_safety.get_safety_settings(), recent_minutes=0)


def _run(safety, path, dry_run=False):
    return make_safe_run(config.get_setting, safety, dry_run=dry_run, path=str(path))


def _info(ext=".mkv", video="h264", audio=("aac",), subs=()):
    streams = [cp.StreamInfo(0, "video", video, width=640, height=360)]
    streams += [cp.StreamInfo(1 + i, "audio", codec) for i, codec in enumerate(audio)]
    streams += [cp.StreamInfo(10 + i, "subtitle", codec) for i, codec in enumerate(subs)]
    return cp.MediaInfo(path="x" + ext, size_bytes=1000, duration=10.0, streams=streams)


# -- the plans (no ffmpeg) ----------------------------------------------------------------------------------------------


def test_the_remux_plan_is_lossless_and_says_why_it_cannot_be():
    assert cm.plan_remux(_info()).container == ".mp4" and cm.plan_remux(_info()).video == cp.V_COPY
    assert "already an MP4" in cm.plan_remux(_info(".mp4")).skip_reason
    assert "audio (dts)" in cm.plan_remux(_info(audio=("dts",))).skip_reason
    assert "subtitles (hdmv_pgs_subtitle)" in cm.plan_remux(_info(subs=("hdmv_pgs_subtitle",))).skip_reason
    assert "video (vp8)" in cm.plan_remux(_info(video="vp8")).skip_reason
    assert "no video" in cm.plan_remux(cp.MediaInfo(path="a.mkv", size_bytes=1, duration=1, streams=[])).skip_reason


def test_the_h264_plan_carries_the_transcode_settings():
    from core.transcode_settings import TranscodeSettings

    plan = cm.plan_h264(_info(".avi"), TranscodeSettings(crf=20, audio_bitrate="160k", threads=4))
    assert (plan.container, plan.video, plan.crf, plan.audio_bitrate, plan.threads) == (".mp4", cp.V_X264, 20, "160k", 4)
    args = cp.build_ffmpeg_args(_info(".avi"), plan, "in.avi", "out.mp4")
    assert args[args.index("-c:v") + 1] == "libx264" and args[args.index("-crf") + 1] == "20"
    assert args[args.index("-b:a") + 1] == "160k" and args[args.index("-threads") + 1] == "4"
    assert "+faststart" in args


def test_the_result_takes_a_new_name_only_when_the_original_stays_an_mp4_and_is_kept(tmp_path):
    plan = cm.plan_h264(_info(".mp4"), __import__("core.transcode_settings", fromlist=["x"]).TranscodeSettings())
    src = str(tmp_path / "a.mp4")
    assert cm.output_path(src, plan, keep=True) == str(tmp_path / "a_h264.mp4")
    assert cm.output_path(src, plan, keep=False) == src
    assert cm.output_path(str(tmp_path / "a.mkv"), plan, keep=True) == str(tmp_path / "a.mp4")


# -- the shared policy over the safety settings --------------------------------------------------------------------------


def test_an_unsaved_policy_is_what_the_app_did_before(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    assert library_safety.get_safety_settings().originals == policy_mod.ORIGINALS_RECYCLE  # no quarantine folder before
    library_safety.set_safety_settings(dataclasses.replace(library_safety.get_safety_settings(), quarantine_root="D:/q"))
    settings = library_safety.get_safety_settings()
    assert settings.originals == policy_mod.ORIGINALS_QUARANTINE and settings.quarantine_root == "D:/q"


def test_a_saved_policy_overrides_the_old_values(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    library_safety.save_policy(policy_mod.ConversionPolicy(
        on_open=policy_mod.ON_OPEN_AUTO, originals=policy_mod.ORIGINALS_QUARANTINE, quarantine_folder="",
        recent_minutes=0, dry_run_first=False))
    s = library_safety.get_safety_settings()
    assert s.quarantine_beside and not s.quarantine_root and s.on_open == policy_mod.ON_OPEN_AUTO
    assert s.recent_minutes == 0 and s.dry_run is False and not s.keep_originals
    library_safety.save_policy(policy_mod.ConversionPolicy(originals=policy_mod.ORIGINALS_KEEP))
    assert library_safety.get_safety_settings().keep_originals
    library_safety.save_policy(policy_mod.ConversionPolicy(originals=policy_mod.ORIGINALS_RECYCLE, quarantine_folder="D:/q"))
    s = library_safety.get_safety_settings()
    assert s.quarantine_root == "" and not s.quarantine_beside  # the Recycle Bin: the folder is not used


# -- real conversions ---------------------------------------------------------------------------------------------------


def _probe_streams(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name", "-of", "csv=p=0",
                           str(path)], capture_output=True, text=True, check=True)
    return sorted(line.strip() for line in done.stdout.splitlines() if line.strip())


@needs_ffmpeg
def test_remux_keeps_the_original_when_asked_and_copies_every_stream(tmp_path, safety):
    src = make_mkv(tmp_path / "m.mkv", tmp_path, audio_tracks=2, subtitle=False)
    outcome = cm.convert_with_mode(_run(safety, src), str(src), cm.MODE_REMUX, cp.ConversionSettings(), keep=True)
    assert outcome.converted, outcome.message
    assert src.is_file() and (tmp_path / "m.mp4").is_file()  # the original stays where it is
    assert _probe_streams(tmp_path / "m.mp4") == _probe_streams(src)


@needs_ffmpeg
def test_remux_without_keep_moves_the_original_beside_the_file_with_an_undo_log(tmp_path, safety):
    src = make_mkv(tmp_path / "m.mkv", tmp_path, audio_tracks=1, subtitle=False)
    safety = dataclasses.replace(safety, originals=policy_mod.ORIGINALS_QUARANTINE, quarantine_root="")
    outcome = cm.convert_with_mode(_run(safety, src), str(src), cm.MODE_REMUX, cp.ConversionSettings(), keep=False)
    assert outcome.converted, outcome.message
    assert not src.exists() and (tmp_path / "m.mp4").is_file()
    kept = [p for p in tmp_path.rglob("m.mkv") if "remuxed originals" in str(p)]
    assert len(kept) == 1  # moved, never deleted
    assert list(tmp_path.rglob("undo *.tsv"))  # the undo log


@needs_ffmpeg
def test_a_dry_run_writes_and_moves_nothing(tmp_path, safety):
    src = make_mkv(tmp_path / "m.mkv", tmp_path, audio_tracks=1, subtitle=False)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    outcome = cm.convert_with_mode(_run(safety, src, dry_run=True), str(src), cm.MODE_REMUX, cp.ConversionSettings(), keep=False)
    assert outcome.converted  # "would convert"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


@needs_ffmpeg
def test_a_file_that_cannot_be_remuxed_is_skipped_with_the_reason(tmp_path, safety):
    src = make_mp4(tmp_path / "a.mp4", audio_tracks=1)
    outcome = cm.convert_with_mode(_run(safety, src), str(src), cm.MODE_REMUX, cp.ConversionSettings(), keep=True)
    assert not outcome.converted and "already an MP4" in outcome.message


@needs_ffmpeg
def test_h264_of_an_mp4_keeps_the_original_and_names_the_new_file_h264(tmp_path, safety):
    src = make_mp4(tmp_path / "a.mp4", audio_tracks=1)
    outcome = cm.convert_with_mode(_run(safety, src), str(src), cm.MODE_H264, cp.ConversionSettings(), keep=True)
    assert outcome.converted, outcome.message
    assert src.is_file() and (tmp_path / "a_h264.mp4").is_file()
    assert "h264" in _probe_streams(tmp_path / "a_h264.mp4")[1] + _probe_streams(tmp_path / "a_h264.mp4")[0]


@needs_ffmpeg
def test_h264_without_keep_replaces_the_file_and_quarantines_the_original(tmp_path, safety):
    src = make_mp4(tmp_path / "a.mp4", audio_tracks=1)
    safety = dataclasses.replace(safety, originals=policy_mod.ORIGINALS_QUARANTINE, quarantine_root="")
    outcome = cm.convert_with_mode(_run(safety, src), str(src), cm.MODE_H264, cp.ConversionSettings(), keep=False)
    assert outcome.converted, outcome.message
    assert src.is_file() and not (tmp_path / "a_h264.mp4").exists()  # the result took the original's name
    assert [p for p in tmp_path.rglob("a.mp4") if "converted originals" in str(p)]


# -- the dialog ---------------------------------------------------------------------------------------------------------


def test_the_dialog_keeps_the_original_for_a_reencode_unless_the_user_decides(qapp):
    from gui.convert_dialog import ConvertDialog

    safety = dataclasses.replace(library_safety.SafetySettings(), originals=policy_mod.ORIGINALS_QUARANTINE)
    dialog = ConvertDialog(["a.mkv"], safety=safety)
    assert dialog.mode == cm.MODE_RULES and not dialog.keep
    dialog._buttons[cm.MODE_H264].setChecked(True)
    assert dialog.mode == cm.MODE_H264 and dialog.keep  # a lossy re-encode keeps it by default
    dialog._keep.click()  # the user unticks it: from now on the mode no longer decides
    dialog._buttons[cm.MODE_REMUX].setChecked(True)
    dialog._buttons[cm.MODE_H264].setChecked(True)
    assert not dialog.keep


def test_the_dialog_says_where_the_original_goes_and_starts_as_a_dry_run(qapp):
    from gui.convert_dialog import ConvertDialog, originals_sentence

    s = library_safety.SafetySettings(originals=policy_mod.ORIGINALS_QUARANTINE, quarantine_root="D:/q", dry_run=True)
    assert "D:/q" in originals_sentence(s)
    assert "Recycle Bin" in originals_sentence(dataclasses.replace(s, originals=policy_mod.ORIGINALS_RECYCLE, quarantine_root=""))
    assert "beside the file" in originals_sentence(dataclasses.replace(s, quarantine_root=""))
    dialog = ConvertDialog(["a.avi", "b.avi", "c.avi", "d.avi"], safety=s)
    assert dialog.dry_run and "Recycle" not in dialog._fate.text() and "D:/q" in dialog._fate.text()


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


# -- the on-open policy (Open Folder's offer) -----------------------------------------------------------------------------


@pytest.fixture
def window(monkeypatch, tmp_path, qapp):
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def _offer(window, monkeypatch, on_open, others):
    import gui.main_window as mw

    library_safety.save_policy(policy_mod.ConversionPolicy(on_open=on_open))
    monkeypatch.setattr(mw, "discover_convertible_files", lambda folder, recursive=False: others)
    asked, converted = [], []
    monkeypatch.setattr(mw, "offer_conversion", lambda *a, **k: asked.append(1) or mw.CONVERT_YES)
    monkeypatch.setattr(window, "_convert_paths", lambda paths, dry_run=None, **kw: converted.append((paths, dry_run)))
    window._offer_import_of_convertible(__import__("pathlib").Path("D:/x"), False, 0)
    return asked, converted


def test_policy_ask_shows_the_offer(window, monkeypatch):
    asked, converted = _offer(window, monkeypatch, policy_mod.ON_OPEN_ASK, ["a.avi"])
    assert asked == [1] and converted[0][0] == ["a.avi"]


def test_policy_auto_converts_without_asking_and_never_does_nothing(window, monkeypatch):
    asked, converted = _offer(window, monkeypatch, policy_mod.ON_OPEN_AUTO, ["a.avi", "b.avi"])
    assert asked == [] and converted == [(["a.avi", "b.avi"], False)]  # a real run, no question
    asked, converted = _offer(window, monkeypatch, policy_mod.ON_OPEN_NEVER, ["a.avi"])
    assert asked == [] and converted == []


def test_the_conversion_page_saves_the_shared_policy(qapp, monkeypatch, tmp_path):
    from gui.video_preferences import ConversionPage, SafetyPage

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    page = ConversionPage()
    page.policy_widget._recycle.setChecked(True)
    page.policy_widget._on_open[policy_mod.ON_OPEN_AUTO].setChecked(True)
    page.policy_widget._recent.setValue(3)
    page.save()
    SafetyPage().save()  # the dialog saves both pages; the safety page must not undo what the policy page chose
    s = library_safety.get_safety_settings()
    assert (s.originals, s.on_open, s.recent_minutes, s.quarantine_root) == (policy_mod.ORIGINALS_RECYCLE, policy_mod.ON_OPEN_AUTO, 3, "")
