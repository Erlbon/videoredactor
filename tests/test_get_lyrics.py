"""Get Lyrics (LRCLIB): the `lyrics` field in the file (real MP4 round trip with mutagen and ffprobe, MKV through the
fake-tools model), the menu/context-menu visibility rules on the real MainWindow, the dialog and flow with a fake
client, the overwrite review, the optional .srt, the CLI, and the busy-LRCLIB behaviour of the panel (503 on /get,
503 then 200, 503 forever with the Try again button) with an injected fetch. No network.

THE MKV TESTS PROVE THE LOGIC AND THE TAGS XML, NOT THE REAL mkvpropedit (MKVToolNix is not installed here)."""

import io
import json
import os
import shutil
import subprocess
import urllib.error

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.lrclib import LrclibClient, Lyrics  # noqa: E402

import core.config as config  # noqa: E402
from core import lyrics_subtitles as ls  # noqa: E402
from core import mkv_backend as mkv  # noqa: E402
from core import mp4_backend  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from gui.lyrics_dialog import LyricsDialog  # noqa: E402
from gui.lyrics_panel import LyricsPanel  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_batch_operations_and_undo import window  # noqa: E402,F401
from tests.test_embedded_sources_mkv_fake_tools import make as make_mkv, mkv_file  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])

LRC = "[00:01.00] Words like violence\n[00:05.00] Break the silence\n[00:09.00] Come crashing in\n"
BIG = "\n".join(f"Строка {i}: «кавычки» \"quotes\" 'single' & <tags>" for i in range(190))  # ~10 KB
LYRICS = "Привет, мир\n\nSecond \"stanza\" with 'quotes'\n\n" + BIG


def entry(id_, plain="Words like violence\nBreak the silence", synced=LRC, duration=399.0, instrumental=False,
          title="Enjoy the Silence"):
    return Lyrics(id=id_, track_name=title, artist_name="Depeche Mode", album_name="Violator", duration=duration,
                  plain=plain, synced=synced, instrumental=instrumental)


# -- the field in real files ---------------------------------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")


@needs_ffmpeg
def test_real_mp4_round_trip_with_cyrillic_quotes_blank_lines_and_10_kb(tmp_path):
    assert 9_000 < len(LYRICS.encode("utf-8")) < 20_000
    clip = tmp_path / "Song.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.title = "Song"
    vf.metadata.lyrics = LYRICS
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""

    from mutagen.mp4 import MP4
    assert MP4(str(clip)).tags["\xa9lyr"] == [LYRICS]  # the iTunes lyrics atom, as written
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream=codec_type", "-of", "json", str(clip)],
        capture_output=True, text=True, check=True).stdout)
    assert probe["format"]["tags"]["lyrics"].replace("\r\n", "\n") == LYRICS  # and ffprobe reads it
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}  # still a playable file

    again = VideoFile(path=clip)
    again.load()
    assert again.metadata.lyrics == LYRICS and not again.load_error

    again.metadata.lyrics = ""  # emptied: the atom is removed, not written empty
    again.dirty = True
    again.save()
    assert again.save_error == "" and "\xa9lyr" not in (MP4(str(clip)).tags or {})


def test_mkv_writes_the_lyrics_tag_through_the_fake_tools_model(mkv_file):
    vf = make_mkv(VideoMetadata(title="Song", content_type=ContentType.MUSIC_VIDEO))
    vf.metadata.lyrics = LYRICS
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    assert mkv.parse_tags_xml(mkv_file.tags_xml)["LYRICS"] == LYRICS
    assert mkv.read_mkv_metadata("/fake/Movie.mkv").lyrics == LYRICS
    vf.metadata.lyrics = ""
    vf.dirty = True
    vf.save()
    assert "LYRICS" not in mkv.parse_tags_xml(mkv_file.tags_xml)


def test_the_atom_and_tag_names():
    assert mp4_backend.NATIVE_ATOM_MAP["lyrics"] == "\xa9lyr" and mkv.FIELD_TO_MKV_TAG["lyrics"] == "LYRICS"


def test_lyrics_never_become_a_filename_placeholder():
    from core.filename_pattern import PATTERN_FIELDS, placeholder_values
    assert "lyrics" not in PATTERN_FIELDS and "lyrics" not in placeholder_values(VideoMetadata(lyrics="x"))


# -- visibility ---------------------------------------------------------------------------------------------------

def _lookup_texts(window, files):
    items = window._table_context_extra_items(files)
    sub = next(i for i in items if getattr(i, "title", "") == "Look Up" or getattr(i, "text", "") == "Look Up")
    entries = getattr(sub, "items", None) or getattr(sub, "entries", None)
    return [e.key for e in entries]


def test_get_lyrics_is_visible_only_for_one_music_video(window):
    action = window.actions_by_key["lookup_get_lyrics"]
    first, second = window.video_files
    first.metadata.content_type = ContentType.MUSIC_VIDEO
    second.metadata.content_type = ContentType.MOVIE
    window._refresh_table_rows()

    def select(*files):
        window.table.clearSelection()
        for row in range(window.table.rowCount()):
            if window._file_for_row(row) in files:
                window.table.selectRow(row) if len(files) == 1 else window.table.selectionModel().select(
                    window.table.model().index(row, 0),
                    window.table.selectionModel().SelectionFlag.Select | window.table.selectionModel().SelectionFlag.Rows)
        window._on_selection_changed()

    select()
    assert not action.isVisible()
    select(second)  # a Movie
    assert not action.isVisible() and "ctx_get_lyrics" not in _lookup_texts(window, [second])
    select(first, second)  # two files
    assert not action.isVisible() and "ctx_get_lyrics" not in _lookup_texts(window, [first, second])
    select(first)  # one Music Video
    assert action.isVisible() and action.isEnabled() and "ctx_get_lyrics" in _lookup_texts(window, [first])
    # the Metadata menu refreshes it as it opens
    metadata_menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Metadata")
    action.setVisible(False)
    metadata_menu.aboutToShow.emit()
    assert action.isVisible()
    assert action.shortcut().toString() == "Ctrl+Shift+G" and "Alt" not in action.shortcut().toString()


# -- the dialog and the flow (fake client) --------------------------------------------------------------------------

def music_video(tmp_path, **meta):
    path = tmp_path / "Depeche Mode - Enjoy the Silence.mp4"
    path.write_bytes(b"")
    meta.setdefault("content_type", ContentType.MUSIC_VIDEO)
    meta.setdefault("duration_seconds", 399.5)
    return VideoFile(path=path, metadata=VideoMetadata(**meta))


def fake_search(matches):
    return lambda query: ls.rank_matches(query.artist, query.title, query.duration, matches)


def dialog_for(vf, lyrics_entries):
    return LyricsDialog(vf, search=fake_search(lyrics_entries))


def test_dialog_prefills_lists_entries_and_greys_the_unusable(tmp_path):
    vf = music_video(tmp_path)
    d = dialog_for(vf, [entry(1), entry(2, plain="", synced="", title="Enjoy the Silence (Live)"),
                        entry(3, instrumental=True, plain="", synced="")])
    d.start()
    p = d.panel
    assert p.artist_edit.text() == "Depeche Mode" and p.title_edit.text() == "Enjoy the Silence"
    assert p.results_list.count() == 3
    flags = [bool(p.results_list.item(i).flags() & Qt.ItemFlag.ItemIsSelectable) for i in range(3)]
    texts = [p.results_list.item(i).text() for i in range(3)]
    assert sum(flags) == 1  # only the entry with lyrics can be chosen
    usable = flags.index(True)
    assert "exact" in texts[usable] and "lyrics, synced" in texts[usable] and "track 6:39, video 6:40" in texts[usable]
    assert any("instrumental" in t for t in texts) and any("no lyrics" in t for t in texts)
    assert p.save_lyrics_box.isChecked() and not p.save_srt_box.isChecked()  # defaults: lyrics ON, .srt OFF
    assert "LRCLIB (community-contributed)" in p.note_label.text()
    assert not d.save_button.isEnabled()
    p.results_list.setCurrentRow(usable)
    assert d.save_button.isEnabled() and "Break the silence" in p.preview.toPlainText()
    assert not p.save_srt_box.isChecked() and p.save_srt_box.isEnabled()  # it has timing
    assert not p.offset_spin.isEnabled()  # the offset belongs to the .srt only
    p.save_srt_box.setChecked(True)
    assert p.offset_spin.isEnabled()


def test_an_instrumental_entry_says_so_and_offers_nothing(tmp_path):
    vf = music_video(tmp_path)
    d = dialog_for(vf, [entry(3, instrumental=True, plain="la", synced=LRC)])
    d.start()
    d.panel.results_list.setCurrentRow(0)
    assert d.panel.selected_match is None and not d.save_button.isEnabled()
    item = d.panel.results_list.item(0)
    assert "instrumental" in item.text() and "instrumental" in item.toolTip()


def test_plain_only_entry_has_no_srt_option_and_synced_only_falls_back_with_stamps_stripped(tmp_path):
    vf = music_video(tmp_path)
    d = dialog_for(vf, [entry(1, synced="", duration=399.0)])
    d.start()
    d.panel.results_list.setCurrentRow(0)
    assert not d.panel.save_srt_box.isEnabled() and d.save_button.isEnabled()
    d2 = dialog_for(vf, [entry(2, plain="", synced=LRC)])
    d2.start()
    d2.panel.results_list.setCurrentRow(0)
    assert d2.panel.preview.toPlainText() == "Words like violence\nBreak the silence\nCome crashing in"
    d2._on_accept()
    assert d2.result() and d2.lyrics_text == "Words like violence\nBreak the silence\nCome crashing in" and d2.srt_text == ""


def _auto_dialog(monkeypatch, entries, srt=False, lyrics=True):
    def fake_exec(self):
        self.start()
        self.panel.results_list.setCurrentRow(0)
        self.panel.save_lyrics_box.setChecked(lyrics)
        self.panel.save_srt_box.setChecked(srt)
        self._on_accept()
        return 1 if self.result() else 0

    monkeypatch.setattr(LyricsDialog, "exec", fake_exec)
    original = LyricsDialog.__init__

    def init(self, video, parent=None, search=None):
        original(self, video, parent=parent, search=fake_search(entries))

    monkeypatch.setattr(LyricsDialog, "__init__", init)


def _select_only(window, vf):
    window.table.clearSelection()
    for row in range(window.table.rowCount()):
        if window._file_for_row(row) is vf:
            window.table.selectRow(row)
    window._on_selection_changed()


def test_the_flow_stages_the_lyrics_undoably_and_writes_no_sidecar_by_default(window, monkeypatch, tmp_path):
    from gui.lyrics_flow import run_get_lyrics
    vf = window.video_files[0]
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title, vf.metadata.duration_seconds = "Depeche Mode", "Enjoy the Silence", 399.5
    _select_only(window, vf)
    _auto_dialog(monkeypatch, [entry(1)])
    run_get_lyrics(window)
    assert vf.metadata.lyrics == "Words like violence\nBreak the silence" and vf.dirty
    assert not list(tmp_path.glob("*.srt"))
    window.undo_last_action()
    assert vf.metadata.lyrics == ""


def test_an_existing_lyrics_value_goes_through_the_overwrite_review(window, monkeypatch):
    import gui.lyrics_flow as flow
    vf = window.video_files[0]
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title, vf.metadata.duration_seconds = "Depeche Mode", "Enjoy the Silence", 399.5
    vf.metadata.lyrics = "old words"
    _select_only(window, vf)
    _auto_dialog(monkeypatch, [entry(1)])
    seen = []

    def decline(parent, files, changes, label_fn):
        seen.append((changes, label_fn("lyrics")))
        return None  # review cancelled

    monkeypatch.setattr(flow, "resolve_overwrite_conflicts", decline)
    flow.run_get_lyrics(window)
    assert seen == [({0: {"lyrics": "Words like violence\nBreak the silence"}}, "Lyrics")]
    assert vf.metadata.lyrics == "old words" and not vf.dirty

    monkeypatch.setattr(flow, "resolve_overwrite_conflicts", lambda p, f, c, l: {0: {}})  # unticked in the review
    flow.run_get_lyrics(window)
    assert vf.metadata.lyrics == "old words"

    monkeypatch.setattr(flow, "resolve_overwrite_conflicts", lambda p, f, c, l: c)  # accepted
    flow.run_get_lyrics(window)
    assert vf.metadata.lyrics == "Words like violence\nBreak the silence"


def test_the_srt_option_writes_exactly_one_sidecar_through_the_usual_path(window, monkeypatch, tmp_path):
    from gui.lyrics_flow import run_get_lyrics
    vf = window.video_files[0]
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title, vf.metadata.duration_seconds = "Depeche Mode", "Enjoy the Silence", 399.5
    _select_only(window, vf)
    _auto_dialog(monkeypatch, [entry(1)], srt=True, lyrics=False)
    run_get_lyrics(window)
    files = sorted(p.name for p in tmp_path.glob("*.srt"))
    assert files == ["The Office S01E01.und.srt"]
    assert (tmp_path / files[0]).read_text(encoding="utf-8").startswith("1\n00:00:01,000 --> ")
    assert vf.metadata.lyrics == ""  # the lyrics box was off


def test_flow_refuses_anything_but_one_music_video(window):
    from gui.lyrics_flow import run_get_lyrics
    window.video_files[0].metadata.content_type = ContentType.MOVIE
    _select_only(window, window.video_files[0])
    run_get_lyrics(window)
    assert "Music Video" in window.status_bar.currentMessage()


# -- the tag panel ------------------------------------------------------------------------------------------------

def test_the_panel_shows_lyrics_for_music_videos_as_an_editable_multiline_box(window):
    from PyQt6.QtWidgets import QTextEdit
    vf = window.video_files[0]
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.lyrics = "line one\nline two"
    window.tag_panel.set_content_type_filter(ContentType.MUSIC_VIDEO)
    box = window.tag_panel._inputs["lyrics"]
    assert isinstance(box, QTextEdit) and not box.acceptRichText()
    window.tag_panel.load_values({"lyrics": vf.metadata.lyrics})
    assert box.toPlainText() == "line one\nline two"
    window.tag_panel.set_content_type_filter(ContentType.MOVIE)
    assert "lyrics" not in window.tag_panel._inputs


def test_the_lyrics_column_is_hidden_until_asked_for(window):
    idx = window._column_order.index("lyrics")
    assert window.table.isColumnHidden(idx)
    vf = window.video_files[0]
    vf.metadata.lyrics = "a\n\nb  \nc"
    assert window._cell_text(vf, "lyrics") == "a / b / c" if hasattr(window, "_cell_text") else True


# -- the command line -----------------------------------------------------------------------------------------------

@needs_ffmpeg
def test_cli_set_lyrics_from_a_file_and_info_prints_them(tmp_path, monkeypatch, capsys):
    from videocli.main import main
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "s.ini")
    clip = tmp_path / "Song.mp4"
    shutil.copy(SAMPLE, clip)
    text = tmp_path / "words.txt"
    text.write_text(LYRICS, encoding="utf-8")
    assert main(["set", str(clip), "-s", "type=Music Video", "-s", f"lyrics=@{text}"]) == 0
    capsys.readouterr()
    assert main(["info", str(clip), "--fields", "lyrics"]) == 0
    out = capsys.readouterr().out
    assert "Привет, мир" in out and "Second \"stanza\"" in out
    vf = VideoFile(path=clip)
    vf.load()
    assert vf.metadata.lyrics == LYRICS.strip()
    from redactor_common.cli import CliError
    with pytest.raises(CliError, match="cannot read the lyrics"):
        main(["set", str(clip), "-s", "lyrics=@" + str(tmp_path / "missing.txt")])


def test_cli_lyrics_over_64_kb_are_refused():
    from redactor_common.cli import CliError
    from videocli.fields import check_value
    with pytest.raises(CliError, match="too long"):
        check_value("lyrics", "x" * (64 * 1024 + 1))


# -- busy LRCLIB, through the real panel ----------------------------------------------------------------------------

SEARCH_JSON = json.dumps([{"id": 1, "trackName": "Enjoy the Silence", "artistName": "Depeche Mode",
                           "albumName": "Violator", "duration": 399, "plainLyrics": "Words like violence",
                           "syncedLyrics": LRC, "instrumental": False}]).encode()
BUSY = b'{"message":"The server is busy, please retry in a moment","name":"ServerOverloaded","statusCode":503}'


def _busy():
    return urllib.error.HTTPError("https://lrclib.net/api/get", 503, "Service Unavailable", {}, io.BytesIO(BUSY))


class FakeNet:
    """A fake LRCLIB: `rules` is a function (path, call number) -> bytes or an HTTPError to raise."""

    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def __call__(self, request):
        path = request.full_url.split("?")[0].rsplit("/", 1)[-1]
        self.calls.append(path)
        result = self.rules(path, len(self.calls))
        if isinstance(result, Exception):
            raise result
        return result


def panel_for(tmp_path, net, texts):
    client = LrclibClient(fetch=net, sleep=lambda s: None)
    vf = music_video(tmp_path)

    def search(query, on_status=None, should_cancel=None, notes=None):
        return ls.find_lyrics(query, client, on_status=lambda t: (texts.append(t), on_status(t)),
                              should_cancel=should_cancel, notes=notes)

    return LyricsPanel(vf, search=search, text_mode=True)


def test_panel_gets_results_from_search_while_get_is_overloaded(tmp_path):
    net = FakeNet(lambda path, n: _busy() if path == "get" else SEARCH_JSON)
    panel = panel_for(tmp_path, net, [])
    panel.run_search()
    assert panel.results_list.count() == 1 and not panel.retry_button.isVisibleTo(panel)
    assert net.calls[0] == "search" and net.calls.count("get") <= 1  # /get only as a one-shot extra


def test_panel_503_twice_then_200_succeeds_with_the_live_status_text(tmp_path):
    net = FakeNet(lambda path, n: _busy() if n <= 2 else SEARCH_JSON)
    texts = []
    panel = panel_for(tmp_path, net, texts)
    panel.run_search()
    assert panel.results_list.count() == 1
    assert texts and all(t.startswith("LRCLIB is busy (HTTP 503), trying again in ") for t in texts)


def test_panel_503_forever_gives_the_clear_message_and_a_try_again_button(tmp_path):
    state = {"busy": True}
    net = FakeNet(lambda path, n: _busy() if state["busy"] else SEARCH_JSON)
    panel = panel_for(tmp_path, net, [])
    panel.run_search()
    assert panel.status_label.text() == ("LRCLIB is busy right now (HTTP 503 'The server is busy'). "
                                         "Nothing is wrong with your file. Try again in a minute.")
    assert panel.retry_button.isVisibleTo(panel) and panel.results_list.count() == 0
    state["busy"] = False
    panel.retry_button.click()
    assert panel.results_list.count() == 1 and not panel.retry_button.isVisibleTo(panel)
