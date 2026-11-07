"""Get Lyrics' "embed the timed lyrics as a subtitle track" (core/lyrics_embed.py, gui/lyrics_flow.py).

The MP4 path runs for real (ffmpeg + ffprobe + mutagen on a generated clip): the track is there with its language and
name, the tags and the poster survive, a second embed replaces the Lyrics track instead of adding one, and the original
goes to the (test) Recycle Bin. The MKV path runs for real only where MKVToolNix is installed; elsewhere its mkvmerge
command is checked, and the verified swap is driven with a runner that writes the file mkvmerge would. No network."""

import json
import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import library_safety  # noqa: E402
from core import lyrics_embed as le  # noqa: E402
from core.external_tools import MKVTOOLNIX, is_tool_available  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402

_app = QApplication.instance() or QApplication([])

SRT = "1\n00:00:00,200 --> 00:00:01,000\nWords like violence\n\n2\n00:00:01,000 --> 00:00:01,900\nBreak the silence\n"
needs_ffmpeg = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")


def _run(tmp_path, binned):
    def trash(path):
        binned.append(path)
        os.makedirs(tmp_path / "bin", exist_ok=True)
        shutil.move(path, tmp_path / "bin" / f"{len(binned)}-{os.path.basename(path)}")
    return library_safety.SafeRun(library_root=str(tmp_path), quarantine_root="", dry_run=False, recent_minutes=0,
                                  trash=trash)


def _streams(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name:stream_disposition=default:stream_tags",
                          "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return json.loads(out)["streams"]


def _subs(path):
    return [s for s in _streams(path) if s["codec_type"] == "subtitle"]


# -- pure parts --------------------------------------------------------------------------------------------------------


def test_the_mkvmerge_command_copies_everything_and_adds_the_named_track():
    args = le.mkvmerge_args("in.mkv", "l.srt", "in.redact-part.mkv", "eng", drop=[], default=True)
    assert args == ["mkvmerge", "-o", "in.redact-part.mkv", "in.mkv", "--language", "0:eng", "--track-name", "0:Lyrics",
                    "--default-track-flag", "0:yes", "--sub-charset", "0:UTF-8", "l.srt"]
    again = le.mkvmerge_args("in.mkv", "l.srt", "t.mkv", "nor", drop=[3, 5], default=False)
    assert again[3:5] == ["--subtitle-tracks", "!3,5"] and "0:no" in again


def test_an_earlier_lyrics_track_is_found_by_name_in_both_containers():
    mkv = {"tracks": [{"id": 0, "type": "video"}, {"id": 2, "type": "subtitles", "properties": {"track_name": "English"}},
                      {"id": 3, "type": "subtitles", "properties": {"track_name": "lyrics"}}]}
    assert [(t.track_id, le._is_lyrics(t)) for t in le.mkv_subtitle_tracks(mkv)] == [(2, False), (3, True)]
    mp4 = [{"codec_type": "video"}, {"codec_type": "subtitle", "tags": {"handler_name": "SubtitleHandler"}},
           {"codec_type": "subtitle", "tags": {"handler_name": "Lyrics"}}]
    assert [(t.track_id, le._is_lyrics(t)) for t in le.mp4_subtitle_tracks(mp4)] == [(0, False), (1, True)]


def test_language_and_unsupported_files():
    assert le.track_language("en") == "eng" and le.track_language("und") == "und" and le.track_language("zz") == "und"
    assert "only MP4 and MKV" in le.unsupported_reason("clip.avi")


def test_nothing_happens_without_timed_lyrics(tmp_path):
    result = le.embed_lyrics_track(str(tmp_path / "a.mp4"), "  ", "en", _run(tmp_path, []))
    assert not result.ok and "no timed lyrics" in result.message


# -- MP4, for real -------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_mp4_gets_the_track_keeps_tags_and_poster_and_a_second_embed_replaces_it(tmp_path):
    clip = tmp_path / "Depeche Mode - Enjoy the Silence.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title, vf.metadata.lyrics = "Depeche Mode", "Enjoy the Silence", "Words"
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    image = QImage(400, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 85)
    jpeg = bytes(buffer.data())
    assert vf.embed_cover_now(jpeg) == ""
    before = _streams(clip)

    binned: list = []
    result = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, binned))
    assert result.ok, result.message
    subs = _subs(clip)
    assert len(subs) == 1 and subs[0]["codec_name"] == "mov_text"
    assert subs[0]["tags"]["language"] == "eng" and subs[0]["tags"]["handler_name"] == "Lyrics"
    assert subs[0]["disposition"]["default"] == 1  # the only subtitle track
    assert [s["codec_type"] for s in _streams(clip) if s["codec_type"] != "subtitle"] == [s["codec_type"] for s in before]
    again = VideoFile(path=clip)
    again.load()
    assert (again.metadata.artist, again.metadata.title, again.metadata.lyrics) == ("Depeche Mode", "Enjoy the Silence", "Words")
    assert again.read_cover() == jpeg
    assert len(binned) == 1 and binned[0].endswith(".redact-orig.mp4")  # the original (set aside) went to the bin
    assert not list(tmp_path.glob("*redact-part*")) and not list(tmp_path.glob("*redact-orig*"))
    cue = subprocess.run(["ffmpeg", "-v", "error", "-i", str(clip), "-map", "0:s:0", "-f", "srt", "-"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    assert "Words like violence" in cue and "Break the silence" in cue

    second = le.embed_lyrics_track(str(clip), SRT.replace("violence", "silence!"), "nb", _run(tmp_path, binned))
    assert second.ok and second.replaced == 1, second.message
    subs = _subs(clip)
    assert len(subs) == 1 and subs[0]["tags"]["language"] == "nor"


@needs_ffmpeg
def test_mp4_with_another_subtitle_keeps_it_and_the_lyrics_track_is_not_default(tmp_path):
    clip = tmp_path / "song.mp4"
    srt = tmp_path / "other.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-i", str(srt), "-map", "0", "-map", "1", "-c", "copy",
                    "-c:s", "mov_text", "-metadata:s:s:0", "language=ger", str(clip)], check=True)
    result = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, []))
    assert result.ok, result.message
    subs = _subs(clip)
    assert [s["tags"]["language"] for s in subs] == ["ger", "eng"]
    assert subs[1]["disposition"]["default"] == 0


@needs_ffmpeg
def test_a_failed_mux_leaves_the_original_untouched(tmp_path):
    clip = tmp_path / "song.mp4"
    shutil.copy(SAMPLE, clip)
    data = clip.read_bytes()
    result = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, []),
                                   ffmpeg_runner=lambda args, **kw: (1, "Invalid argument"))
    assert not result.ok and "Invalid argument" in result.message
    assert clip.read_bytes() == data and sorted(p.name for p in tmp_path.iterdir()) == ["song.mp4"]


# -- MKV ------------------------------------------------------------------------------------------------------------


@needs_ffmpeg
def test_mkv_swap_is_verified_with_a_runner_that_writes_what_mkvmerge_would(tmp_path, monkeypatch):
    clip = tmp_path / "song.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-c", "copy", str(clip)], check=True)
    monkeypatch.setattr(le, "unsupported_reason", lambda path: "")
    seen = []

    def fake_mkvmerge(args):
        seen.append(args)
        temp, source, srt = args[2], args[3], args[-1]
        done = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", source, "-i", srt, "-map", "0", "-map", "1",
                               "-c", "copy", "-metadata:s:s:0", "title=Lyrics", temp])
        return done.returncode, ""

    result = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, []), mkv_identify=lambda p: {"tracks": []},
                                   mkv_runner=fake_mkvmerge)
    assert result.ok, result.message
    assert seen and seen[0][:4] == ["mkvmerge", "-o", str(tmp_path / "song.redact-part.mkv"), str(clip)]
    assert len(_subs(clip)) == 1

    bad = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, []), mkv_identify=lambda p: {"tracks": []},
                                mkv_runner=lambda args: (shutil.copy(args[3], args[2]), (0, ""))[1])
    assert not bad.ok and "subtitle tracks" in bad.message  # no track was added: verification refuses the swap
    assert len(_subs(clip)) == 1


def test_the_mkvmerge_runner_resolves_the_tool_and_returns_its_exit_code(monkeypatch):
    """Runs without MKVToolNix: the real runner's own code (imports, path lookup), only the process is faked."""
    seen = []

    def fake_run_tool(args, timeout=None):
        seen.append(args)
        return subprocess.CompletedProcess(args, 1, stdout="Warning: something\n", stderr="")

    monkeypatch.setattr(le, "run_tool", fake_run_tool)
    monkeypatch.setattr(le, "get_executable_path", lambda name: f"C:/tools/{name}.exe")
    assert le._run_mkvmerge(["mkvmerge", "-o", "t.mkv", "s.mkv"]) == (1, "Warning: something\n")
    assert seen == [["C:/tools/mkvmerge.exe", "-o", "t.mkv", "s.mkv"]]


def _cue_text(path, index=0):
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:s:{index}", "-f", "srt", "-"],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


@pytest.mark.skipif(not is_tool_available(MKVTOOLNIX) or bool(SKIP_REASON), reason="MKVToolNix not installed")
def test_mkv_for_real_with_mkvmerge_keeps_tags_poster_and_other_subtitles(tmp_path):
    clip = tmp_path / "Depeche Mode - Enjoy the Silence.mkv"
    other = tmp_path / "other.srt"
    other.write_text("1\n00:00:00,000 --> 00:00:01,000\nHallo\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-i", str(other), "-map", "0", "-map", "1", "-c", "copy",
                    "-metadata:s:s:0", "language=ger", str(clip)], check=True)
    vf = VideoFile(path=clip)
    vf.load()
    assert not vf.load_error
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title, vf.metadata.lyrics = "Depeche Mode", "Enjoy the Silence", "Words"
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    from PyQt6.QtCore import QBuffer, QIODevice
    from PyQt6.QtGui import QColor, QImage
    image = QImage(400, 400, QImage.Format.Format_RGB32)
    image.fill(QColor("teal"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 85)
    jpeg = bytes(buffer.data())
    assert vf.embed_cover_now(jpeg) == ""

    binned: list = []
    result = le.embed_lyrics_track(str(clip), SRT, "en", _run(tmp_path, binned))
    assert result.ok, result.message
    subs = _subs(clip)
    assert [(s["tags"].get("language"), s["tags"].get("title")) for s in subs] == [("ger", None), ("eng", "Lyrics")]
    assert subs[1]["disposition"]["default"] == 0  # the video already had a subtitle track
    assert "Words like violence" in _cue_text(clip, 1) and "Hallo" in _cue_text(clip, 0)
    again = VideoFile(path=clip)
    again.load()
    assert (again.metadata.artist, again.metadata.title, again.metadata.lyrics) == ("Depeche Mode", "Enjoy the Silence", "Words")
    assert again.metadata.content_type is ContentType.MUSIC_VIDEO
    assert again.read_cover() == jpeg
    assert len(binned) == 1 and not list(tmp_path.glob("*redact-*"))

    second = le.embed_lyrics_track(str(clip), SRT.replace("violence", "thunder"), "nb", _run(tmp_path, binned))
    assert second.ok and second.replaced == 1, second.message
    subs = _subs(clip)
    assert [(s["tags"].get("language"), s["tags"].get("title")) for s in subs] == [("ger", None), ("nor", "Lyrics")]
    assert "Words like thunder" in _cue_text(clip, 1)
    assert VideoFile(path=clip).read_cover() == jpeg


# -- the dialog and the flow on the real window --------------------------------------------------------------------


@needs_ffmpeg
def test_the_dialog_box_and_the_flow_embed_into_the_selected_music_video(tmp_path, monkeypatch):
    from redactor_common.core.lrclib import Lyrics

    import gui.lyrics_flow as flow
    from tests.test_get_lyrics import LRC, dialog_for

    clip = tmp_path / "Depeche Mode - Enjoy the Silence.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    entry = Lyrics(id=1, track_name="Enjoy the Silence", artist_name="Depeche Mode", album_name="Violator",
                   duration=vf.metadata.duration_seconds or 2.0, plain="Words", synced=LRC.replace("00:05", "00:01.5")
                   .replace("00:09", "00:01.8"), instrumental=False)
    dialog = dialog_for(vf, [entry])
    dialog.start()
    assert not dialog.panel.embed_box.isEnabled()  # nothing selected yet
    dialog.panel.results_list.setCurrentRow(0)
    assert dialog.panel.embed_box.isEnabled()
    dialog.panel.save_lyrics_box.setChecked(False)
    dialog.panel.embed_box.setChecked(True)
    assert dialog.panel.language_combo.isEnabled() and dialog.save_button.isEnabled()
    dialog._on_accept()
    assert dialog.embed_track and not dialog.save_srt and dialog.srt_text

    from types import SimpleNamespace

    from PyQt6.QtWidgets import QWidget

    import core.redact_steps as rs

    messages: list = []

    class Window(QWidget):
        status_bar = SimpleNamespace(showMessage=messages.append)

        def _selected_video_files(self):
            return [vf]

        def _refresh_table_rows(self):
            pass

        def _on_selection_changed(self):
            pass

    binned: list = []
    monkeypatch.setattr(rs, "make_safe_run", lambda *a, **k: _run(tmp_path, binned))
    chosen = SimpleNamespace(exec=lambda: 1, lyrics_text="", srt_text=dialog.srt_text, save_srt=False,
                             embed_track=True, srt_language=dialog.srt_language)
    window = Window()
    flow.run_get_lyrics(window, dialog_class=lambda video, parent=None: chosen)
    subs = _subs(clip)
    assert len(subs) == 1 and subs[0]["tags"]["handler_name"] == "Lyrics", messages
    assert messages[-1].startswith("Get Lyrics: embedded the lyrics as a subtitle track"), messages
    assert len(binned) == 1 and not list(tmp_path.glob("*.srt"))  # no sidecar: only the track was asked for
    _app.processEvents()
    assert not [w for w in QApplication.topLevelWidgets() if w.isVisible() and w.inherits("QProgressDialog")]
