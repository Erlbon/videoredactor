"""LRCLIB synced lyrics as a subtitle source: the query from a video, the find flow, the dialog's Lyrics page
and the Redact step (fake client, no network). The converter and scoring are tested in redactor_common."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.lrclib import LrclibError, Lyrics  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

from core import lyrics_subtitles as ls  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.test_redact_steps import bin_, make_env, only, run_one, stub, work  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])

LRC = "[00:01.00] Words like violence\n[00:05.00] Break the silence\n[00:09.00] Come crashing in\n"


def entry(id_, title="Enjoy the Silence", artist="Depeche Mode", duration=399.0, synced=LRC, album="Violator"):
    return Lyrics(id=id_, track_name=title, artist_name=artist, album_name=album, duration=duration,
                  plain="p", synced=synced)


class FakeClient:
    def __init__(self, signature=None, results=(), error=None):
        self.signature, self.results, self.error = signature, list(results), error
        self.calls = []

    def get_by_signature(self, artist, title, album="", duration=None):
        self.calls.append(("get", artist, title, album, duration))
        if self.error:
            raise self.error
        return self.signature

    def search(self, artist="", title="", query=""):
        self.calls.append(("search", artist, title))
        if self.error:
            raise self.error
        return self.results


def music_video(folder, name="Depeche Mode - Enjoy the Silence.mp4", **meta):
    meta.setdefault("content_type", ContentType.MUSIC_VIDEO)
    meta.setdefault("duration_seconds", 399.5)
    return stub(folder, name, **meta)


# -- query / find ----------------------------------------------------------------------

def test_query_uses_tags_then_file_name(work):
    vf = music_video(work, artist="Tagged Artist", track_title="Tagged Title", album="Alb")
    q = ls.query_for_video(vf)
    assert (q.artist, q.title, q.album, q.duration) == ("Tagged Artist", "Tagged Title", "Alb", 399.5)
    q = ls.query_for_video(music_video(work, "Ленинград - WWW.mp4"))
    assert (q.artist, q.title) == ("Ленинград", "WWW")
    q = ls.query_for_video(music_video(work, "NoSeparator.mp4", title="From Title Tag"))
    assert (q.artist, q.title) == ("", "From Title Tag")


def test_find_lyrics_signature_first_then_search_deduplicated_and_ranked(work):
    hit = entry(1)
    client = FakeClient(signature=hit, results=[entry(2, duration=300.0), entry(1), entry(3, synced="")])
    matches = ls.find_lyrics(ls.query_for_video(music_video(work)), client)
    assert client.calls[0][0] == "get" and client.calls[1][0] == "search"
    assert [m.lyrics.id for m in matches] == [1, 2, 3]
    assert matches[0].score.kind == "exact" and not matches[2].lyrics.has_timing


def test_find_lyrics_without_length_skips_the_signature_call(work):
    client = FakeClient(results=[entry(5)])
    vf = music_video(work, duration_seconds=None)
    matches = ls.find_lyrics(ls.query_for_video(vf), client)
    assert [c[0] for c in client.calls] == ["search"] and matches[0].score.kind == "different"


def test_guess_language_from_the_language_tag(work):
    assert ls.guess_language(music_video(work, language="Russian")) == "ru"
    assert ls.guess_language(music_video(work, language="English; German")) == "und"
    assert ls.guess_language(music_video(work)) == "und"


# -- the dialog ---------------------------------------------------------------------------

def dialog_for(vf, matches, **kw):
    from gui.subtitle_search_dialog import SubtitleSearchDialog
    return SubtitleSearchDialog(str(vf.path), parent=None, video=vf, lyrics_search=lambda query: matches, **kw)


def test_dialog_defaults_to_lyrics_for_a_music_video_and_opensubtitles_otherwise(work, monkeypatch):
    import gui.subtitle_search_dialog as dlg
    monkeypatch.setattr(dlg.SubtitleSearchDialog, "_run_hash_search", lambda self: None)
    music = dialog_for(music_video(work), [])
    assert music.source == "lrclib" and music.source_combo.currentText() == "Lyrics (LRCLIB)"
    film = dialog_for(stub(work, "Film.mp4", content_type=ContentType.MOVIE), [])
    assert film.source == "opensubtitles"
    assert [film.source_combo.itemText(i) for i in range(film.source_combo.count())] == ["OpenSubtitles", "Lyrics (LRCLIB)"]


def test_dialog_lists_results_greys_no_timing_previews_and_applies_offset(work):
    vf = music_video(work)
    matches = ls.find_lyrics(ls.query_for_video(vf), FakeClient(
        signature=entry(1), results=[entry(2, duration=380.0), entry(3, synced="")]))
    d = dialog_for(vf, matches)
    panel = d.lyrics_panel
    assert panel.artist_edit.text() == "Depeche Mode" and panel.title_edit.text() == "Enjoy the Silence"
    assert panel.results_list.count() == 3
    texts = [panel.results_list.item(i).text() for i in range(3)]
    assert "exact" in texts[0] and "synced" in texts[0]
    assert "20 s longer" in texts[1]
    assert "no timing" in texts[2] and not panel.results_list.item(2).flags() & Qt_selectable()
    assert not d.select_button.isEnabled()

    panel.results_list.setCurrentRow(0)
    assert d.select_button.isEnabled()
    assert not panel.warning_label.isVisible() or panel.warning_label.isHidden()  # exact: no warning
    assert panel.preview.toPlainText().startswith("1\n00:00:01,000 --> 00:00:04,950\nWords like violence")
    panel.offset_spin.setValue(2.5)
    assert "00:00:03,500 --> " in panel.preview.toPlainText()
    assert (panel.offset_spin.minimum(), panel.offset_spin.maximum(), panel.offset_spin.singleStep()) == (-30.0, 30.0, 0.5)

    panel.results_list.setCurrentRow(1)
    assert not panel.warning_label.isHidden()
    assert "Sync is not guaranteed" in panel.warning_label.text() and "20 s longer" in panel.warning_label.text()

    d._on_accept()
    assert d.result() and d.source == "lrclib" and d.selected_match.lyrics.id == 2
    assert d.selected_srt.startswith("1\n00:00:03,500 --> ")  # offset applied
    assert d.selected_language == "und"


def test_dialog_cannot_choose_a_no_timing_entry(work):
    vf = music_video(work)
    d = dialog_for(vf, ls.find_lyrics(ls.query_for_video(vf), FakeClient(results=[entry(3, synced="")])))
    d.lyrics_panel.results_list.setCurrentRow(0)
    assert d.lyrics_panel.selected_match is None and not d.select_button.isEnabled()
    d._on_accept()
    assert not d.selected_srt


def test_dialog_language_prefilled_from_the_video_and_editable(work):
    vf = music_video(work, language="Russian")
    d = dialog_for(vf, [])
    assert d.lyrics_panel.language() == "ru"
    d.lyrics_panel.language_combo.setEditText("de")
    assert d.lyrics_panel.language() == "de"
    d.lyrics_panel.language_combo.setEditText("../../evil")
    assert d.lyrics_panel.language() == "und"


def test_dialog_search_uses_the_edited_artist_and_title(work):
    vf = music_video(work)
    seen = []
    from gui.subtitle_search_dialog import SubtitleSearchDialog
    d = SubtitleSearchDialog(str(vf.path), video=vf, lyrics_search=lambda q: seen.append(q) or [])
    d.lyrics_panel.artist_edit.setText("Other Artist")
    d.lyrics_panel.title_edit.setText("Other Title")
    d.lyrics_panel.run_search()
    assert (seen[-1].artist, seen[-1].title, seen[-1].duration) == ("Other Artist", "Other Title", 399.5)
    assert "Nothing found" in d.lyrics_panel.status_label.text()


def Qt_selectable():
    from PyQt6.QtCore import Qt
    return Qt.ItemFlag.ItemIsSelectable


# -- writing goes through the usual sidecar path -----------------------------------------------

def test_main_window_writes_the_sidecar_with_language_and_never_overwrites_unasked(work, monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw
    from PyQt6.QtWidgets import QMessageBox
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    window = mw.MainWindow()
    vf = music_video(work)
    vf.path.write_bytes(b"x")

    class Picked:
        source = "lrclib"
        selected_srt = "1\n00:00:01,000 --> 00:00:02,000\nПривет\n\n"
        selected_language = "ru"
        selected_match = type("M", (), {"score": type("S", (), {"label": "exact"})()})()

    window._save_lyrics_subtitle(vf, Picked)
    out = work / "Depeche Mode - Enjoy the Silence.ru.srt"
    assert out.read_text(encoding="utf-8") == Picked.selected_srt
    # an existing file: the question is asked, "No" keeps it
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))
    Picked.selected_srt = "changed"
    window._save_lyrics_subtitle(vf, Picked)
    assert out.read_text(encoding="utf-8").startswith("1")
    window.close()


# -- Redact ------------------------------------------------------------------------------------

def lyrics_entry(vf, env, monkeypatch, client, use=True, **kw):
    ls.set_client(client)
    try:
        recipe, catalogue = only("subtitles", options={"subtitles": {"use_lrclib": use}}, **kw)
        return run_one(vf, env, recipe, catalogue, finalize=False)
    finally:
        ls.set_client(None)


def test_lrclib_option_is_off_by_default():
    spec = {o.key: o for o in rs.SubtitlesStep.options}["use_lrclib"]
    assert spec.label == "Use LRCLIB lyrics for music videos" and spec.default is False


def test_redact_writes_an_exact_lyrics_match(work, bin_, tmp_path, monkeypatch):
    vf = music_video(work, language="Russian")
    client = FakeClient(signature=entry(1), results=[entry(1)])
    e = lyrics_entry(vf, make_env(bin_, tmp_path), monkeypatch, client)
    assert e.status is FileStatus.CHANGED and "auto-applied" in e.applied[0]
    out = work / "Depeche Mode - Enjoy the Silence.ru.srt"
    assert out.read_text(encoding="utf-8").startswith("1\n00:00:01,000")


def test_redact_close_or_different_match_is_only_a_suggestion(work, bin_, tmp_path, monkeypatch):
    vf = music_video(work)
    client = FakeClient(results=[entry(2, duration=380.0)])
    e = lyrics_entry(vf, make_env(bin_, tmp_path), monkeypatch, client)
    assert e.status is FileStatus.NEEDS_REVIEW and "sync is not guaranteed" in e.review[0].reason
    assert sorted(os.listdir(work)) == ["Depeche Mode - Enjoy the Silence.mp4"]


def test_redact_ignores_lrclib_when_the_option_is_off_or_not_a_music_video(work, bin_, tmp_path, monkeypatch):
    client = FakeClient(signature=entry(1))
    e = lyrics_entry(music_video(work), make_env(bin_, tmp_path), monkeypatch, client, use=False)
    assert client.calls == [] and e.status is FileStatus.UNCHANGED
    film = stub(work, "Film 2020.mp4", content_type=ContentType.MOVIE)
    e = lyrics_entry(film, make_env(bin_, tmp_path), monkeypatch, client)
    assert client.calls == []


def test_redact_lrclib_unavailable_is_a_note_not_a_failure(work, bin_, tmp_path, monkeypatch):
    client = FakeClient(error=LrclibError("Could not reach LRCLIB: no route"))
    e = lyrics_entry(music_video(work), make_env(bin_, tmp_path), monkeypatch, client)
    assert e.failures == [] and any("LRCLIB unavailable" in n for n in e.notes)


def test_redact_leaves_an_existing_subtitle_alone(work, bin_, tmp_path, monkeypatch):
    vf = music_video(work)
    (work / "Depeche Mode - Enjoy the Silence.en.srt").write_text("x")
    client = FakeClient(signature=entry(1))
    e = lyrics_entry(vf, make_env(bin_, tmp_path), monkeypatch, client)
    assert client.calls == [] and e.status is FileStatus.UNCHANGED
