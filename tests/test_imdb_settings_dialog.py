"""Tools > IMDb Database...: the settings dialog (offscreen), its settings storage and the
Export/Import Settings entries. Synthetic fixtures only."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402

import core.config as config  # noqa: E402
from core import imdb_import, imdb_settings  # noqa: E402
from core.imdb_import import BuildOptions  # noqa: E402
from core.settings_adapter import APP_SLUG, VideoSettingsAdapter  # noqa: E402
from gui import imdb_settings_dialog as dlg  # noqa: E402
from tests.imdb_fixture import build, make_dataset  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def test_licence_notice_is_prominent_and_complete():
    dialog = dlg.ImdbSettingsDialog()
    try:
        text = dialog.licence_label.text()
        assert "personal, non-commercial purposes only" in text and "local copy for your own use" in text
        assert "republished, resold or repurposed" in text and "withdraw permission at any time" in text
        assert "https://developer.imdb.com/non-commercial-datasets/" in text
        assert "https://www.imdb.com/conditions" in text
        assert dialog.layout().indexOf(dialog.licence_label) == 0  # the very first thing in the dialog
        assert "never bundles or downloads" in text
    finally:
        dialog.close()


def test_defaults_are_the_documented_options():
    dialog = dlg.ImdbSettingsDialog()
    try:
        options = dialog.build_options()
        assert options == BuildOptions()
        assert set(options.types) == {"movie", "tvMovie", "tvSeries", "tvMiniSeries", "tvEpisode", "tvSpecial", "video"}
        assert options.skip_adult and options.min_votes == 5 and options.include_episodes and options.include_akas
        assert set(options.regions) == {"NO", "DE", "FR", "IT", "US", "GB", "XWW"}
        assert set(dialog.source_edits) == {"basics", "ratings", "episodes", "akas"}
        assert dialog.status_label.text() == "No database yet."
    finally:
        dialog.close()


def test_save_remembers_paths_and_options():
    dialog = dlg.ImdbSettingsDialog()
    try:
        dialog.path_edit.setText("D:/db/imdb.db")
        dialog.source_edits["basics"].setText("D:/dump/title.basics.tsv.gz")
        dialog.type_boxes["short"].setChecked(True)
        dialog.min_votes.setValue(25)
        dialog.region_boxes["SE"].setChecked(True)
        dialog.accept()
    finally:
        dialog.close()
    assert imdb_settings.load_database() == "D:/db/imdb.db"
    assert imdb_settings.load_sources()["basics"] == "D:/dump/title.basics.tsv.gz"
    options = imdb_settings.load_options()
    assert "short" in options.types and options.min_votes == 25 and "SE" in options.regions
    again = dlg.ImdbSettingsDialog()
    try:
        assert again.min_votes.value() == 25 and again.type_boxes["short"].isChecked()
    finally:
        again.close()


def test_build_runs_and_shows_status(tmp_path, monkeypatch):
    files = make_dataset(tmp_path / "dump")
    dest = str(tmp_path / "imdb.db")
    shown = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: pytest.fail(f"warned: {a[2]}"))
    dialog = dlg.ImdbSettingsDialog()
    try:
        dialog.path_edit.setText(dest)
        for kind, path in files.items():
            dialog.source_edits[kind].setText(path)
        assert dialog._build_database() == dest
        assert os.path.isfile(dest)
        assert "7 titles" in dialog.status_label.text()
        assert shown and "7 titles" in shown[-1]
    finally:
        dialog.close()
    assert imdb_settings.load_sources()["basics"] == files["basics"]  # remembered even before Save


def test_build_without_basics_or_with_votes_but_no_ratings_asks_first(tmp_path, monkeypatch):
    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("must not ask"))
    dialog = dlg.ImdbSettingsDialog()
    try:
        assert dialog._build_database() is None and "title.basics" in told[-1]
        dialog.source_edits["basics"].setText(make_dataset(tmp_path / "d")["basics"])
        assert dialog._build_database() is None and "title.ratings" in told[-1]
    finally:
        dialog.close()


def test_build_reports_a_bad_file_without_creating_a_database(tmp_path, monkeypatch):
    from tests.imdb_fixture import BASICS, BASICS_HEADER, write_tsv

    files = make_dataset(tmp_path / "d")
    write_tsv(files["basics"], [h.replace("titleType", "kind") for h in BASICS_HEADER], BASICS)
    warned = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dialog = dlg.ImdbSettingsDialog()
    try:
        dialog.path_edit.setText(str(tmp_path / "x.db"))
        for kind, path in files.items():
            dialog.source_edits[kind].setText(path)
        assert dialog._build_database() is None
        assert warned and "titleType" in warned[-1]
        assert not os.path.exists(tmp_path / "x.db")
    finally:
        dialog.close()


def test_check_file_status_for_a_foreign_database(tmp_path):
    dest, _s, _f = build(tmp_path)
    dialog = dlg.ImdbSettingsDialog()
    try:
        dialog.path_edit.setText(dest)
        assert "Built from title.basics.tsv.gz" in dialog.status_label.text()
        other = tmp_path / "other.db"
        import sqlite3
        con = sqlite3.connect(other)
        con.execute("create table x (y)")
        con.commit()
        con.close()
        dialog.path_edit.setText(str(other))
        assert dialog.status_label.text().startswith("Problem:")
    finally:
        dialog.close()


def test_free_space_warning_appears_when_the_drive_is_nearly_full(monkeypatch, tmp_path):
    import collections
    usage = collections.namedtuple("usage", "total used free")
    monkeypatch.setattr(dlg.shutil, "disk_usage", lambda p: usage(100, 99, 1 << 28))
    dialog = dlg.ImdbSettingsDialog()
    try:
        dialog.path_edit.setText(str(tmp_path / "imdb.db"))
        assert "may not be enough" in dialog.free_space_text()
    finally:
        dialog.close()
    monkeypatch.setattr(dlg.shutil, "disk_usage", lambda p: usage(100, 1, 50 << 30))
    dialog = dlg.ImdbSettingsDialog()
    try:
        assert "may not be enough" not in dialog.free_space_text()
    finally:
        dialog.close()


# --- Export / Import Settings -----------------------------------------------------


def test_paths_are_machine_specific_and_options_portable(tmp_path):
    existing = tmp_path / "imdb.db"
    existing.write_bytes(b"x")
    imdb_settings.save_database(str(existing))
    imdb_settings.save_options(BuildOptions(min_votes=50))
    adapter = VideoSettingsAdapter()
    sections = {s.key: s.portable for s in adapter.sections()}
    assert sections["imdb"] is False and sections["imdb_options"] is True
    default = json.dumps(json.loads(sb.dump_bundle(sb.build_bundle(adapter, sb.default_selection(adapter)))))
    assert str(existing).replace("\\", "\\\\") not in default and '"min_votes":50' in default.replace(" ", "")


def test_imported_paths_only_count_when_the_file_exists_here(tmp_path):
    here = tmp_path / "here.db"
    here.write_bytes(b"x")
    imdb_settings.save_database(str(here))
    adapter = VideoSettingsAdapter()
    adapter.write_section("imdb", {"database": "Z:/nowhere/imdb.db", "basics_file": ""})
    assert imdb_settings.load_database() == str(here)  # kept
    other = tmp_path / "other.db"
    other.write_bytes(b"x")
    adapter.write_section("imdb", {"database": str(other)})
    assert imdb_settings.load_database() == str(other)


def test_options_import_is_normalised(tmp_path):
    adapter = VideoSettingsAdapter()
    adapter.write_section("imdb_options", {"options": {"min_votes": 12, "types": ["movie", "bogus"], "x": 1}})
    options = imdb_settings.load_options()
    assert options.min_votes == 12 and options.types == ("movie",)
    with pytest.raises(ValueError):
        adapter.write_section("imdb_options", {"options": "nonsense"})
    assert config.get_setting("imdb", "options")  # unchanged by the rejected value


def test_round_trip_through_a_bundle(tmp_path):
    here = tmp_path / "here.db"
    here.write_bytes(b"x")
    imdb_settings.save_database(str(here))
    imdb_settings.save_options(BuildOptions(min_votes=9))
    adapter = VideoSettingsAdapter()
    everything = {s.key for s in adapter.sections()}
    text = sb.dump_bundle(sb.build_bundle(adapter, everything))
    imdb_settings.save_options(BuildOptions(min_votes=1))
    result = sb.apply_bundle(adapter, sb.parse_bundle(text, APP_SLUG), everything)
    assert not result.failed
    assert imdb_settings.load_options().min_votes == 9 and imdb_settings.load_database() == str(here)


def test_the_importer_still_has_no_network_code():
    source = open(imdb_import.__file__, encoding="utf-8").read()
    for word in ("urllib", "requests", "http.client", "urlopen", "socket"):
        assert word not in source


# --- IMDb's required attribution ----------------------------------------------------

ATTRIBUTION = "Information courtesy of IMDb (https://www.imdb.com). Used with permission."


def test_the_attribution_sentence_is_exact_and_shown_wherever_imdb_data_is(imdb_db=None):
    from gui.tmdb_search_dialog import SearchSource, TMDBSearchDialog

    assert imdb_import.ATTRIBUTION == ATTRIBUTION
    settings = dlg.ImdbSettingsDialog()
    try:
        assert settings.attribution_label.text() == ATTRIBUTION
        order = [settings.layout().itemAt(i).widget() for i in range(3)]
        assert order[0] is settings.licence_label and order[1] is settings.attribution_label  # right under the box
    finally:
        settings.close()
    import gui.main_window as mw_module
    source = SearchSource("IMDb (Local Database)", lambda q, year=None: [], lambda q, year=None: [], (),
                          note="IMDb has no plot.\n\n" + imdb_import.ATTRIBUTION)
    lookup = TMDBSearchDialog("movie", "", source=source)
    try:
        from PyQt6.QtWidgets import QLabel
        assert any(ATTRIBUTION in label.text() for label in lookup.findChildren(QLabel))
    finally:
        lookup.close()
    text = open(os.path.join(os.path.dirname(mw_module.__file__), "..", "ABOUT.md"), encoding="utf-8").read()
    assert ATTRIBUTION in " ".join(text.split())
    credits = open(os.path.join(os.path.dirname(mw_module.__file__), "..", "CREDITS.md"), encoding="utf-8").read()
    assert ATTRIBUTION in " ".join(credits.split())
    source_text = open(mw_module.__file__, encoding="utf-8").read()
    assert "imdb_import.ATTRIBUTION" in source_text  # the real lookup dialog's note carries it
