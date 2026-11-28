"""Regression tests for the 2026-11-28 review cards fixed by agent D (cards 18, 23, 25-30, 32, 42, 44-47, 49-53, 56,
64, 66, 67). Each test failed before its fix."""

import argparse
import datetime
import io
import json
import types

import pytest

from core import config


@pytest.fixture
def ini(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "videoredactor_settings.ini")
    return config.CONFIG_PATH


# -- card 18, 28, 52: videocli/cmd_tags.py --------------------------------------------------------------------------


def test_card18_clear_track_title_is_refused_not_a_title_wipe():
    from redactor_common.cli import CliError
    from videocli import cmd_tags

    args = argparse.Namespace(assignments=[], clear=["track_title"])
    with pytest.raises(CliError, match="retired"):
        cmd_tags.run_set(args, None)
    args = argparse.Namespace(assignments=[], clear=["Track Title"])
    with pytest.raises(CliError, match="retired"):
        cmd_tags.run_set(args, None)


def test_card28_lyrics_from_stdin_are_read_as_utf8_bytes(monkeypatch):
    from videocli import cmd_tags

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO("Blåbær\n".encode("utf-8")), encoding="cp1252"))
    assert cmd_tags._read_lyrics_source("-") == "Blåbær\n"


def test_card28_missing_stdin_gives_a_clear_message(monkeypatch):
    from redactor_common.cli import CliError
    from videocli import cmd_tags

    monkeypatch.setattr("sys.stdin", None)
    with pytest.raises(CliError, match="standard input"):
        cmd_tags._read_lyrics_source("-")


class _Out:
    def __init__(self):
        self.rows = []

    def progress(self, *a):
        pass

    def record(self, row):
        self.rows.append(row)

    def line(self, text):
        pass

    def finish(self, summary):
        pass


def _music_video_from_media_type():
    from core.video_metadata import ContentType, VideoMetadata

    meta = VideoMetadata()
    meta.content_type = ContentType.MUSIC_VIDEO
    meta.content_type_from_media_type = True
    saved = []
    video = types.SimpleNamespace(path="clip.mp4", metadata=meta, dirty=False, save_error="", saved=saved)
    video.save = lambda: saved.append(True)
    return video


def _run_set(monkeypatch, video, assignments, clear):
    from videocli import cmd_tags

    monkeypatch.setattr(cmd_tags, "collect", lambda *a, **k: ["clip.mp4"])
    monkeypatch.setattr(cmd_tags, "load_videos", lambda files, out, **kw: [video])
    monkeypatch.setattr(cmd_tags, "skip_reason", lambda v: "")
    out = _Out()
    args = argparse.Namespace(assignments=assignments, clear=clear, paths=["x"], no_recurse=False, dry_run=False)
    cmd_tags.run_set(args, out)
    return out.rows[0]


def test_card52_setting_music_video_on_a_media_type_only_file_writes_it(monkeypatch):
    video = _music_video_from_media_type()
    row = _run_set(monkeypatch, video, ["content_type=Music Video"], [])
    assert row["status"] == "changed" and video.saved


def test_card52_clearing_it_says_the_media_type_remains(monkeypatch):
    video = _music_video_from_media_type()
    row = _run_set(monkeypatch, video, [], ["content_type"])
    assert row["status"] == "unchanged" and "media type" in row["message"] and not video.saved


# -- card 51: CLI rename / move accept %track_title% -----------------------------------------------------------------


def test_card51_rename_and_move_modernize_the_retired_token(monkeypatch, ini):
    from videocli import cmd_files

    seen = {}
    monkeypatch.setattr(cmd_files, "load_videos", lambda files, out, **kw: [])
    monkeypatch.setattr(cmd_files, "collect", lambda *a, **k: [])
    monkeypatch.setattr(cmd_files.commands, "rename_items", lambda videos, **kw: seen.update(rename=kw["pattern"]) or 0)
    monkeypatch.setattr(cmd_files.commands, "move_items", lambda videos, **kw: seen.update(move=kw["pattern"]) or 0)
    monkeypatch.setattr(cmd_files.commands, "finish_run", lambda *a, **k: 0)
    monkeypatch.setattr(cmd_files, "_zero_pad", lambda args: False)
    base = dict(paths=["x"], no_recurse=False, dry_run=False, root="R", copy=False, ascii=False)
    cmd_files.run_rename(argparse.Namespace(pattern="%artist% - %track_title%", **base), None)
    cmd_files.run_move(argparse.Namespace(pattern="%artist%/%track_title%", **base), None)
    assert seen == {"rename": "%artist% - %title%", "move": "%artist%/%title%"}


# -- card 53: stamp save failure is a problem ------------------------------------------------------------------------


def test_card53_a_stamp_that_could_not_be_saved_is_a_problem(monkeypatch):
    from videocli import cmd_check

    monkeypatch.setattr(cmd_check, "video_fingerprint", lambda path: "")
    row = {"message": "", "problem": False, "stamped": False}
    args = argparse.Namespace(stamp=True, dry_run=False)
    cmd_check._maybe_stamp(_StampVideo(), types.SimpleNamespace(), args, row)
    assert row["problem"] is True and not row["stamped"] and "could not be recorded" in row["message"]


class _StampVideo:
    save_error = "disk is read-only"
    path = "x.mkv"

    def record_check(self, result, fingerprint):
        return True

    def save(self):
        pass


# -- card 56: notes obeys the saved Wikipedia setting ----------------------------------------------------------------


def test_card56_cli_notes_obeys_the_saved_wikipedia_setting(tmp_path, capsys, ini, monkeypatch):
    from core import notes_settings
    from tests.notes_helpers import fake_fetchers, make_movie
    from videocli import cmd_notes
    from videocli.main import main

    monkeypatch.setattr(cmd_notes, "make_cache", lambda args: None)
    asked = []
    monkeypatch.setattr(cmd_notes, "make_fetchers", lambda wiki: asked.append(wiki) or fake_fetchers(wikipedia=wiki))
    make_movie(tmp_path)
    notes_settings.set_wikipedia_enabled(False)
    main(["notes", str(tmp_path), "--json"])
    capsys.readouterr()
    assert asked == [False]


# -- card 30: no ffmpeg read pass -> not stamped --------------------------------------------------------------------


def test_card30_quick_check_without_the_ffmpeg_read_pass_is_not_a_real_scan(tmp_path):
    from core import file_check

    video = tmp_path / "a.mp4"
    video.write_bytes(b"\x00" * 64)
    probe = types.SimpleNamespace(
        returncode=0, stderr="", stdout=json.dumps({"format": {"duration": "10.0", "format_name": "mov,mp4"}}))

    def run(cmd, **kwargs):
        return probe if cmd[0] == "ffprobe" else None  # ffmpeg missing / timed out

    result = file_check.quick_check(str(video), run=run)
    assert any(f.code == "no_tool" for f in result.findings)


# -- card 26: deep scan run ending at a chunk edge --------------------------------------------------------------------


def test_card26_zero_run_ending_exactly_at_a_chunk_edge_is_found(tmp_path):
    from core import deep_scan

    path = tmp_path / "f.bin"
    path.write_bytes(b"\x01" * 4 + b"\x00" * 8 + b"\x01" * 4)
    assert deep_scan.find_zero_runs(str(path), [(0, 16)], min_run=4, chunk_bytes=4) == [(4, 12)]
    # the same run in the middle of a chunk was always found
    path.write_bytes(b"\x01" * 3 + b"\x00" * 8 + b"\x01" * 5)
    assert deep_scan.find_zero_runs(str(path), [(0, 16)], min_run=4, chunk_bytes=4) == [(3, 11)]


# -- cards 25, 29: track language ----------------------------------------------------------------------------------------


def test_card25_an_mp4_track_stored_as_a_T_code_can_be_changed(monkeypatch, tmp_path):
    from core import ffmpeg_backend, track_language as tl

    stream = types.SimpleNamespace(attached_pic=False, kind="audio", index=1, language="deu")
    monkeypatch.setattr(ffmpeg_backend, "probe_media", lambda path: types.SimpleNamespace(streams=[stream]))
    packed = tl.encode_packed("deu")
    monkeypatch.setattr(tl, "read_mdhd_list", lambda path: [tl._Mdhd("soun", 100, packed, 0)])
    patches = tl._mp4_patches(str(tmp_path / "x.mp4"), [], {1: "eng"})
    assert patches == [(100, packed, tl.encode_packed("eng"))]


def test_card29_mkv_subtitle_tracks_keep_their_ietf_tag_in_step(monkeypatch):
    from core import track_language as tl
    from redactor_common.core import subprocess_utils

    data = {"tracks": [
        {"type": "video", "properties": {}},
        {"type": "subtitles", "properties": {"language_ietf": "en"}},
    ]}
    monkeypatch.setattr(subprocess_utils, "run_tool", lambda *a, **k: types.SimpleNamespace(stdout=json.dumps(data)))
    assert tl._mkv_ietf_keys("x.mkv") == {"s1"}


# -- cards 23, 27: TheTVDB ---------------------------------------------------------------------------------------------------


def test_card23_the_v4_host_is_used():
    from core import tvdb_client

    assert tvdb_client.BASE_URL == "https://api4.thetvdb.com/v4"


def test_card27_all_episode_pages_are_read(monkeypatch):
    from core import tvdb_client

    pages = {
        0: {"data": {"episodes": [{"seasonNumber": 1, "number": 1, "name": "A"}]}, "links": {"next": "x?page=1"}},
        1: {"data": {"episodes": [{"seasonNumber": 25, "number": 10, "name": "B"}]}, "links": {"next": None}},
    }
    asked = []

    def fake(path, params=None, _retrying=False):
        asked.append(params["page"])
        return pages[params["page"]]

    monkeypatch.setattr(tvdb_client, "_get_json", fake)
    episodes = tvdb_client.get_series_episodes(7)
    assert [(e.season_number, e.episode_number) for e in episodes] == [(1, 1), (25, 10)] and asked == [0, 1]
    assert tvdb_client.get_episode_details(7, 25, 10)["title"] == "B"


# -- card 32: season 0 -----------------------------------------------------------------------------------------------------------


def test_card32_first_number_keeps_zero():
    from core.redact_steps import _first_number

    assert _first_number(0, 1) == 0 and _first_number(None, 5) == 5 and _first_number(None, None) is None


# -- card 42: damaged settings file ------------------------------------------------------------------------------------------


def test_card42_a_damaged_ini_is_set_aside_and_defaults_are_used(ini):
    ini.write_text("[table]\nhidden_columns = a\nhidden_columns = b\n", encoding="utf-8")
    assert config.get_setting("table", "hidden_columns", "dflt") == "dflt"
    assert not ini.exists() and ini.with_name(ini.name + ".bad").exists()
    config.set_setting("table", "hidden_columns", "c")  # the app can write a fresh file
    assert config.get_setting("table", "hidden_columns") == "c"


def test_card42_bad_utf8_and_a_line_without_equals_too(ini):
    ini.write_bytes(b"[a]\nk = \xff\xfe\xfa\n")
    assert config.get_setting("a", "k", "d") == "d"
    ini.write_text("[a]\njust a line\n", encoding="utf-8")
    assert config.get_setting("a", "k", "d") == "d"


def test_card42_the_main_window_says_so_once_in_its_status_bar(ini, monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    import gui.main_window as mw

    _app = QApplication.instance() or QApplication([])  # noqa: F841
    monkeypatch.setattr(config, "SETTINGS_RESET_MESSAGE", "")
    ini.write_text("[table]\nhidden_columns = a\nhidden_columns = b\n", encoding="utf-8")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    window = mw.MainWindow()
    try:
        assert ".bad" in window.status_bar.currentMessage()
        assert config.SETTINGS_RESET_MESSAGE == ""  # shown once
    finally:
        window.close()


# -- card 44: non-finite numbers --------------------------------------------------------------------------------------------------


def test_card44_inf_in_a_number_field_is_refused_not_a_crash():
    from core.filename_pattern import set_field_text
    from core.video_metadata import VideoMetadata

    meta = VideoMetadata()
    assert set_field_text(meta, "episode_number", "1e999") is False
    assert set_field_text(meta, "episode_number", "inf") is False
    assert set_field_text(meta, "episode_number", "4") is True


@pytest.mark.parametrize("module,scene", [
    ("stashdb_client", {"id": "1", "title": "T", "duration": float("inf")}),
    ("theporndb_client", {"id": "1", "title": "T", "duration": float("inf")}),
])
def test_card44_a_scene_duration_of_infinity_is_dropped(module, scene):
    import importlib

    mod = importlib.import_module(f"core.{module}")
    result = mod.parse_scene(scene, "title", "scene") if module == "theporndb_client" else mod.parse_scene(scene, "title")
    assert result is None or result.duration is None


# -- card 45, 47: studios ------------------------------------------------------------------------------------------------------------


def test_card45_a_website_with_a_stray_bracket_is_not_a_crash():
    from core import studios

    assert studios.normalize_website("http://[x/y") == ""
    assert studios.normalize_website("//[abc") == ""
    assert studios.normalize_website("https://www.example.com/a") == "example.com"


def test_card47_a_studio_name_with_a_comma_matches_whole():
    from core import studios

    table = {"Brazzers, Inc.": "brazzers.com", "Other": "other.com"}
    assert studios.website_for("Brazzers, Inc.", table) == "brazzers.com"
    assert studios.website_for("Unknown, Other", table) == "other.com"  # several names still work


def test_card47_the_vocabulary_editor_refuses_a_comma(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QMessageBox

    from gui.vocabulary_editor_dialog import VocabularyEditorDialog

    app = QApplication.instance() or QApplication([])  # noqa: F841
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[1]))
    added = []
    import inspect

    params = list(inspect.signature(VocabularyEditorDialog.__init__).parameters)
    kwargs = {}
    for name in params[1:]:
        if name == "title":
            kwargs[name] = "T"
        elif "get" in name:
            kwargs[name] = lambda: list(added)
        elif "add" in name:
            kwargs[name] = added.append
        elif "remove" in name:
            kwargs[name] = lambda n: None
    dialog = VocabularyEditorDialog(**kwargs)
    dialog.new_item_edit.setText("Rock, Pop")
    dialog._on_add()
    assert added == [] and shown == ["No Commas"]


# -- card 46: emptied vocabulary stays empty -------------------------------------------------------------------------------------------


def test_card46_removing_the_last_entry_does_not_bring_the_defaults_back(ini):
    from core import controlled_vocab as cv

    assert cv.get_genre_options() == cv.DEFAULT_GENRE_OPTIONS  # first call seeds
    for name in list(cv.get_genre_options()):
        cv.remove_genre_option(name)
    assert cv.get_genre_options() == []


# -- card 49: scene name noise ---------------------------------------------------------------------------------------------------------------


def test_card49_real_title_words_survive_as_noise_words():
    from core.scene_name_parser import parse_scene_name

    assert parse_scene_name("Internal Affairs 1080p").title == "Internal Affairs"
    assert parse_scene_name("Behind the Scenes").title == "Behind the Scenes"
    assert parse_scene_name("Some Title 1080p Complete").title == "Some Title"  # in the release tail they still go


# -- card 50: scene dates -----------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("given,expected", [
    ("2010-00-00", "2010"), ("0000-00-00", ""), ("2021-13-45", "2021"), ("2021-02-31", "2021-02"),
    ("2021-11-15T10:00:00", "2021-11-15"), ("2021-11", "2021-11"), ("1799-01-01", ""), ("2020", "2020"),
])
def test_card50_clean_date_validates(given, expected):
    from core.scene_lookup import clean_date

    assert clean_date(given) == expected


# -- card 64: Import Settings ------------------------------------------------------------------------------------------------------------------


def test_card64_imported_tool_paths_must_be_named_like_the_tool(ini, tmp_path):
    from core.settings_adapter import VideoSettingsAdapter

    adapter = VideoSettingsAdapter()
    evil = tmp_path / "evil.exe"
    evil.write_bytes(b"")
    with pytest.raises(ValueError):
        adapter.write_section("tools", {"ffmpeg": str(evil)})
    assert config.get_setting("tools", "ffmpeg", "") == ""
    good = tmp_path / "ffmpeg.exe"
    good.write_bytes(b"")
    adapter.write_section("tools", {"ffmpeg": str(good)})
    assert config.get_setting("tools", "ffmpeg", "") == str(good)
    adapter.write_section("tools", {"ffprobe": str(tmp_path / "ffprobe.exe")})  # not on this computer: kept as is
    assert config.get_setting("tools", "ffprobe", "") == ""


def test_card64_the_stashdb_address_is_not_in_a_default_import():
    from core.settings_adapter import VideoSettingsAdapter

    adapter = VideoSettingsAdapter()
    portable = {s.key for s in adapter.sections() if s.portable}
    assert "stashdb_address" not in portable and "scene_lookup" in portable
    assert "stashdb_endpoint" not in adapter.read_section("scene_lookup")


# -- cards 66, 67: OpenSubtitles ---------------------------------------------------------------------------------------------------------------------


def _hash_video(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(bytes(range(256)) * 600)  # above the minimum size to hash
    return str(video)


def test_card66_a_hash_search_asks_for_hash_matches_only_and_trusts_the_flag(tmp_path, monkeypatch, ini):
    from core import opensubtitles_client as osc

    seen = []
    entries = [
        {"attributes": {"moviehash_match": True, "language": "en", "release": "Exact", "download_count": 1,
                        "files": [{"file_id": 1}]}},
        {"attributes": {"moviehash_match": False, "language": "en", "release": "Title only", "download_count": 9,
                        "files": [{"file_id": 2}]}},
        {"attributes": {"language": "en", "release": "No flag", "files": [{"file_id": 3}]}},
    ]

    def fake(request):
        seen.append(request.full_url)
        return json.dumps({"data": entries}).encode()

    monkeypatch.setattr(osc, "_fetch", fake)
    monkeypatch.setattr(osc, "_require_api_key", lambda: "k")
    found = osc.search_by_hash(_hash_video(tmp_path), "en")
    assert "moviehash_match=only" in seen[0]
    assert [c.release_name for c in found] == ["Exact"] and all(c.hash_matched for c in found)


def test_card67_a_downloaded_legacy_encoded_subtitle_is_decoded_not_damaged(monkeypatch):
    from core import opensubtitles_client as osc

    monkeypatch.setattr(osc, "_require_api_key", lambda: "k")
    monkeypatch.setattr(osc, "_fetch", lambda r: json.dumps({"link": "https://dl"}).encode())
    srt = "1\n00:00:01,000 --> 00:00:02,000\nBlåbær og rømme\n"
    monkeypatch.setattr(osc, "_download_fetch", lambda r: srt.encode("cp1252"))
    assert osc.download_subtitle_text(5, "nb") == srt
    ru = "1\n00:00:01,000 --> 00:00:02,000\nПривет, мир\n"
    monkeypatch.setattr(osc, "_download_fetch", lambda r: ru.encode("cp1251"))
    assert osc.download_subtitle_text(5, "ru") == ru
    monkeypatch.setattr(osc, "_download_fetch", lambda r: srt.encode("utf-8-sig"))
    assert osc.download_subtitle_text(5) == srt
