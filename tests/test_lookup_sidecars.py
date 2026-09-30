"""Lookups off the GUI thread (M6), sidecar write safety (M7), no-clobber
rename/export (M11) and the small config/User-Agent fixes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox, QWidget  # noqa: E402

from core import opensubtitles_client as osc  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])


# --- M6 --------------------------------------------------------------------

def test_run_lookup_runs_off_the_gui_thread_disables_the_owner_and_reraises():
    from gui.lookup import run_lookup

    owner = QWidget()
    seen = {}

    def work():
        seen["thread"] = threading.current_thread()
        seen["enabled"] = owner.isEnabled()
        return 42

    assert run_lookup(owner, work) == 42
    assert seen["thread"] is not threading.main_thread()
    assert owner.isEnabled()  # restored afterwards

    def boom():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        run_lookup(owner, boom)
    assert owner.isEnabled()


def test_the_search_dialogs_do_their_network_call_in_the_background(monkeypatch):
    from gui import tmdb_search_dialog as tmdb, tvdb_search_dialog as tvdb

    threads = []

    def fake_search(*args, **kwargs):
        threads.append(threading.current_thread())
        return []

    monkeypatch.setattr(tmdb, "search_movies", fake_search)
    monkeypatch.setattr(tvdb, "search_series", fake_search)
    tmdb.TMDBSearchDialog(mode="movie", initial_query="Alien")
    tvdb.TVDBSearchDialog(initial_query="Alien")
    assert len(threads) == 2 and all(t is not threading.main_thread() for t in threads)


def test_subtitle_dialog_survives_an_unreadable_file(tmp_path, monkeypatch):
    from gui.subtitle_search_dialog import SubtitleSearchDialog

    # compute_moviehash raises OSError for a file that can't be read
    # (here: one that vanished) -- it used to escape the constructor.
    with_warning = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: with_warning.append(a))
    dialog = SubtitleSearchDialog(str(tmp_path / "gone.mkv"))
    assert with_warning
    assert "failed" in dialog.status_label.text().lower()


# --- M7 --------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("en", "en"), ("pt-BR", "pt-BR"), ("../../etc/passwd", "und"), ("e/n", "und"),
    ("", "und"), (None, "und"), ("x", "und"), ("toolonglang", "und"),
])
def test_language_code_is_validated_before_it_reaches_a_filename(raw, expected):
    assert osc.clean_language_code(raw) == expected


def test_server_language_is_cleaned_when_parsing_results():
    data = {"data": [{"attributes": {"language": "../x", "files": [{"file_id": 1}], "release": "r"}}]}
    assert osc._parse_results(data, hash_matched=True)[0].language == "und"


def test_subtitle_sidecar_cannot_escape_the_folder_and_does_not_overwrite(tmp_path):
    vf = VideoFile(path=tmp_path / "m.mkv", metadata=VideoMetadata())
    out = vf.save_subtitle_sidecar("one", language="../../evil")
    assert out == tmp_path / "m.und.srt" and out.read_text() == "one"
    with pytest.raises(FileExistsError):
        vf.save_subtitle_sidecar("two", language="../../evil")
    assert out.read_text() == "one"
    vf.save_subtitle_sidecar("two", language="zz", overwrite=True)
    assert vf.save_subtitle_sidecar("three", language="zz", overwrite=True).read_text() == "three"


def test_poster_sidecar_does_not_silently_overwrite(tmp_path):
    vf = VideoFile(path=tmp_path / "m.mkv", metadata=VideoMetadata())
    poster = vf.save_poster_sidecar(b"a")
    with pytest.raises(FileExistsError):
        vf.save_poster_sidecar(b"b")
    assert poster.read_bytes() == b"a"
    assert vf.save_poster_sidecar(b"b", overwrite=True).read_bytes() == b"b"


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    for vf in w.video_files:
        vf.dirty = False
    w.close()


def test_save_sidecar_reports_errors_and_asks_before_replacing(window, monkeypatch, tmp_path):
    vf = VideoFile(path=tmp_path / "m.mkv", metadata=VideoMetadata())

    status, detail = window._save_sidecar(lambda overwrite: vf.save_poster_sidecar(b"a", overwrite), {})
    assert status == "saved"

    asked = []

    def answer(button):
        def fake(parent, title, text, *args):
            asked.append(text)
            return button
        return fake

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.StandardButton.No))
    assert window._save_sidecar(lambda o: vf.save_poster_sidecar(b"b", o), {})[0] == "kept"
    assert detail.read_bytes() == b"a" and "m-poster.jpg" in asked[0]

    monkeypatch.setattr(QMessageBox, "question", answer(QMessageBox.StandardButton.YesToAll))
    choice = {}
    assert window._save_sidecar(lambda o: vf.save_poster_sidecar(b"b", o), choice)[0] == "saved"
    assert detail.read_bytes() == b"b" and choice == {"all": True}
    assert window._save_sidecar(lambda o: vf.save_poster_sidecar(b"c", o), choice)[0] == "saved"  # no new question
    assert len(asked) == 2

    def unwritable(_overwrite):
        raise PermissionError("read-only folder")

    status, detail = window._save_sidecar(unwritable, {})
    assert status == "error" and "read-only folder" in detail


# --- M11 -------------------------------------------------------------------

def test_export_copy_refuses_to_overwrite_and_leaves_no_temp(tmp_path):
    import gui.main_window as mw

    src, dst = tmp_path / "a.mkv", tmp_path / "b.mkv"
    src.write_bytes(b"source")
    dst.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        mw._copy_no_clobber(str(src), str(dst))
    assert dst.read_bytes() == b"precious" and sorted(p.name for p in tmp_path.iterdir()) == ["a.mkv", "b.mkv"]
    mw._copy_no_clobber(str(src), str(tmp_path / "c.mkv"))
    assert (tmp_path / "c.mkv").read_bytes() == b"source"


def test_rename_by_pattern_uses_the_no_clobber_rename(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    existing = tmp_path / "taken.mkv"
    existing.write_bytes(b"precious")
    vf = VideoFile(path=tmp_path / "a.mkv", metadata=VideoMetadata())
    vf.path.write_bytes(b"mine")
    window.video_files = [vf]
    window._refresh_table_rows()

    monkeypatch.setattr(mw.RenamePatternDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(mw.RenamePatternDialog, "planned_renames",
                        lambda self: [(vf, str(vf.path), str(existing))])
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a))
    window._on_rename_by_pattern()
    assert existing.read_bytes() == b"precious" and vf.path.name == "a.mkv" and warned


def test_rename_no_clobber_is_what_main_window_uses_on_posix(monkeypatch, tmp_path):
    # On POSIX os.rename silently replaces; the helper's link-based
    # path must be what runs, and it must refuse an existing target.
    from redactor_common.core.os_utils import rename_no_clobber
    import gui.main_window as mw

    assert mw.rename_no_clobber is rename_no_clobber
    (tmp_path / "x").write_bytes(b"1")
    (tmp_path / "y").write_bytes(b"2")
    with pytest.raises(FileExistsError):
        rename_no_clobber(str(tmp_path / "x"), str(tmp_path / "y"))
    assert (tmp_path / "y").read_bytes() == b"2"


# --- Low -------------------------------------------------------------------

def test_user_agent_carries_the_real_version():
    from core.version import APP_VERSION

    assert osc.USER_AGENT == f"TheVideoRedactor v{APP_VERSION}"
    assert "v0.1" not in osc.USER_AGENT


def test_save_config_is_atomic(tmp_path, monkeypatch):
    import configparser

    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    config.set_setting("a", "k", "original")

    class Exploding(configparser.ConfigParser):
        def write(self, fp, *args, **kwargs):
            fp.write("[a]\nk = half")
            raise OSError("disk full")

    parser = Exploding(interpolation=None)
    with pytest.raises(OSError):
        config.save_config(parser)
    assert config.get_setting("a", "k") == "original"  # the old file survived intact
    assert [p.name for p in tmp_path.iterdir()] == ["settings.ini"]  # and no temp is left


def test_failed_tvdb_fetch_changes_nothing_and_adds_no_undo_entry(window, monkeypatch, tmp_path):
    import gui.main_window as mw
    from core.tvdb_client import SeriesCandidate, TVDBError
    from core.video_metadata import ContentType

    vf = VideoFile(path=tmp_path / "Show S01E01.mkv", metadata=VideoMetadata())
    vf.path.write_bytes(b"")
    window.video_files = [vf]
    window._refresh_table_rows()
    window.table.selectAll()

    class FakeDialog:
        selected_candidate = SeriesCandidate(tvdb_id=1, name="Show", first_air_time="2000-01-01", overview="", image_url=None)

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return 1

    def failing(_id):
        raise TVDBError("offline")

    monkeypatch.setattr(mw, "TVDBSearchDialog", FakeDialog)
    monkeypatch.setattr(mw, "get_series_details", failing)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    before = len(window.undo_manager._undo) if hasattr(window.undo_manager, "_undo") else None
    window._on_import_tvdb()
    assert vf.metadata.content_type == ContentType.UNSET and not vf.dirty
    if before is not None:
        assert len(window.undo_manager._undo) == before
