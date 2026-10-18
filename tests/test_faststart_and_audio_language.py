"""MP4 files get their index (moov) FIRST (-movflags +faststart) from every conversion and remux path, and the optional
"Tag untagged audio as" setting writes a language onto audio tracks that have none (existing tags and everything else
stay as they are). Unit tests on the ffmpeg arguments, plus real small files made by ffmpeg."""

import json
import shutil
import subprocess

import pytest

import core.config as config
from core import conversion_plan as cp
from core import ffmpeg_backend as fb
from tests.track_language_helpers import make_mp4, top_level_boxes

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


def _audio(*languages, default_first=False):
    return [cp.StreamInfo(1 + i, "audio", "aac", language=lang, default=(default_first and i == 0))
            for i, lang in enumerate(languages)]


def _info(audio):
    return cp.MediaInfo(path="in.mkv", size_bytes=1000, duration=10.0,
                        streams=[cp.StreamInfo(0, "video", "h264", width=640, height=360)] + audio)


def _plan(container):
    plan = cp.Plan()
    plan.container = container
    plan.video = cp.V_COPY
    return plan


# -- +faststart in the arguments ----------------------------------------------------------------------------------------


def test_an_mp4_target_gets_faststart_and_an_mkv_target_does_not():
    mp4_args = cp.build_ffmpeg_args(_info(_audio("eng")), _plan(".mp4"), "in.mkv", "out.mp4")
    mkv_args = cp.build_ffmpeg_args(_info(_audio("eng")), _plan(".mkv"), "in.avi", "out.mkv")
    assert mp4_args[mp4_args.index("-movflags") + 1] == "+faststart" and mp4_args[-1] == "out.mp4"
    assert "-movflags" not in mkv_args


def test_remux_to_mp4_adds_faststart_to_every_attempt_and_remux_to_mkv_does_not(monkeypatch, tmp_path):
    commands = []

    def fake_run(args, cancel_event=None, timeout=None):
        commands.append(list(args))
        return 1, "refused"  # every attempt fails, so all of them are tried

    monkeypatch.setattr(fb, "_run_ffmpeg", fake_run)
    fb.remux_to_mp4("in.mkv", str(tmp_path / "out.mp4"))
    assert len(commands) == len(fb._REMUX_ATTEMPTS)
    assert all(c[c.index("-movflags") + 1] == "+faststart" for c in commands)
    commands.clear()
    monkeypatch.setattr(fb, "_run_ffmpeg", lambda args, cancel_event=None, timeout=None: commands.append(list(args)) or (1, "x"))
    fb.remux_to_mkv("in.mp4", str(tmp_path / "out.mkv"))
    assert commands and "-movflags" not in commands[0]


# -- the language arguments -----------------------------------------------------------------------------------------------


def test_untagged_audio_gets_the_language_and_the_first_track_becomes_default():
    args = cp.audio_language_args(_audio("und", ""), "eng")
    assert args == ["-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=eng",
                    "-disposition:a", "0", "-disposition:a:0", "default"]


@pytest.mark.parametrize("untagged", ["und", "UND", "unk", "mis", "", None])
def test_every_empty_language_form_counts_as_untagged(untagged):
    assert cp.audio_language_args(_audio(untagged), "nor") == ["-metadata:s:a:0", "language=nor"]


def test_an_existing_language_is_never_overwritten_and_only_the_gaps_are_filled():
    assert cp.audio_language_args(_audio("eng", "und"), "nor", ) == [
        "-metadata:s:a:1", "language=nor", "-disposition:a", "0", "-disposition:a:0", "default"]
    assert cp.audio_language_args(_audio("eng", "fra"), "nor") == []  # nothing is untagged: nothing changes


def test_an_empty_setting_changes_nothing():
    assert cp.audio_language_args(_audio("und", "und"), "") == []
    assert cp.audio_language_args(_audio("und"), "   ") == []


def test_the_default_flag_is_only_set_when_no_track_has_it():
    assert "-disposition:a:0" not in cp.audio_language_args(_audio("und", "und", default_first=True), "eng")
    assert "-disposition:a:0" not in cp.audio_language_args(_audio("und"), "eng")  # a single track needs none


def test_a_conversion_carries_the_language_arguments():
    settings = cp.ConversionSettings(default_audio_language="eng")
    args = cp.build_ffmpeg_args(_info(_audio("und", "nor")), _plan(".mkv"), "in.avi", "out.mkv", settings)
    assert "-metadata:s:a:0" in args and args[args.index("-metadata:s:a:0") + 1] == "language=eng"
    assert "-metadata:s:a:1" not in args  # the existing nor stays
    off = cp.build_ffmpeg_args(_info(_audio("und")), _plan(".mkv"), "in.avi", "out.mkv")
    assert "-metadata:s:a:0" not in off


def test_the_setting_is_saved_normalised_and_a_damaged_value_turns_it_off(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    assert cp.get_conversion_settings().default_audio_language == ""  # off by default
    settings = cp.get_conversion_settings()
    settings.default_audio_language = " ENG "
    cp.set_conversion_settings(settings)
    assert cp.get_conversion_settings().default_audio_language == "eng"
    config.set_setting("conversion", "default_audio_language", "english!")
    assert cp.get_conversion_settings().default_audio_language == ""


# -- real files -----------------------------------------------------------------------------------------------------------


def _atoms(path):
    return [name for name, _start, _end in top_level_boxes(path.read_bytes()) if name in ("moov", "mdat")]


def _languages(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index:stream_tags=language",
                           "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return [(s.get("tags") or {}).get("language", "und") for s in json.loads(done.stdout)["streams"]]


@needs_ffmpeg
def test_remux_to_mp4_puts_the_index_before_the_media_data(tmp_path):
    src = make_mp4(tmp_path / "late.mp4", audio_tracks=1)
    assert _atoms(src) == ["mdat", "moov"]  # the premise: ffmpeg's default leaves the index at the end
    out = tmp_path / "early.mp4"
    ok, message = fb.remux_to_mp4(str(src), str(out))
    assert ok, message
    assert _atoms(out) == ["moov", "mdat"]


@needs_ffmpeg
def test_remux_with_a_language_tags_the_untagged_track_and_leaves_an_existing_one(tmp_path):
    src = make_mp4(tmp_path / "two.mp4", audio_tracks=2, languages=[None, "fra"])
    out = tmp_path / "tagged.mp4"
    ok, message = fb.remux_to_mp4(str(src), str(out), audio_language="eng")
    assert ok, message
    assert _languages(out) == ["eng", "fra"]
    assert _atoms(out) == ["moov", "mdat"]


@needs_ffmpeg
def test_remux_without_the_setting_leaves_the_languages_alone(tmp_path):
    src = make_mp4(tmp_path / "two.mp4", audio_tracks=2, languages=[None, None])
    out = tmp_path / "same.mp4"
    ok, message = fb.remux_to_mp4(str(src), str(out))
    assert ok, message
    assert _languages(out) == ["und", "und"]


@needs_ffmpeg
def test_the_untagged_audio_of_two_tracks_gets_a_default_flag(tmp_path):
    src = make_mp4(tmp_path / "two.mp4", audio_tracks=2)
    out = tmp_path / "tagged.mp4"
    ok, message = fb.remux_to_mp4(str(src), str(out), audio_language="nor")
    assert ok, message
    done = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream_disposition=default",
                           "-of", "json", str(out)], capture_output=True, text=True, check=True)
    defaults = [s["disposition"]["default"] for s in json.loads(done.stdout)["streams"]]
    assert defaults.count(1) == 1 and defaults[0] == 1
