"""Tools > Preferences: the shared dialog over this app's ini, with the
existing storage keys (so Export/Import Settings keeps working)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QAction, QKeySequence  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

import core.config as config  # noqa: E402
from core import imdb_settings  # noqa: E402
from core.preferences_backend import INI_LOCATIONS, bitrate_ok, build_sections, make_backend  # noqa: E402
from core.settings_adapter import SECTIONS  # noqa: E402
from gui.video_preferences import VideoPreferencesDialog  # noqa: E402
from tests.test_batch_operations_and_undo import window  # noqa: E402,F401


@pytest.fixture(autouse=True)
def _ini(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def test_every_storage_key_is_one_the_settings_bundle_already_exports():
    bundle = {(k.ini_section, k.ini_key) for _l, _p, keys in SECTIONS.values() for k in keys}
    assert set(INI_LOCATIONS.values()) <= bundle


def test_pages_and_defaults():
    dlg = VideoPreferencesDialog()
    assert dlg.page_titles() == ["Filenames", "Transcode", "Duplicates", "Tools / Paths"]
    assert dlg.value("zero_pad_width") == 2 and dlg.value("crf") == 23
    assert dlg.value("audio_bitrate") == "128k" and dlg.value("threads") == 0
    assert dlg.value("duplicate_threshold") == 6
    assert dlg.value("zero_pad_numbers") is False and dlg.value("ascii_filenames") is False


def test_ranges_match_the_settings_bundle():
    specs = {s.key: s for sec in build_sections() for s in sec.specs}
    assert (specs["crf"].minimum, specs["crf"].maximum) == (0, 51)
    assert (specs["threads"].minimum, specs["threads"].maximum) == (0, 256)
    assert (specs["duplicate_threshold"].minimum, specs["duplicate_threshold"].maximum) == (0, 32)
    assert (specs["zero_pad_width"].minimum, specs["zero_pad_width"].maximum) == (1, 9)
    assert (specs["auto_number_padding"].minimum, specs["auto_number_padding"].maximum) == (1, 9)


def test_ok_writes_changed_values_under_the_existing_ini_keys():
    dlg = VideoPreferencesDialog()
    dlg.set_value("ascii_filenames", True)
    dlg.set_value("zero_pad_numbers", True)
    dlg.set_value("zero_pad_width", 3)
    dlg.set_value("auto_number_padding", 4)
    dlg.set_value("crf", 18)
    dlg.set_value("audio_bitrate", " 192k ")
    dlg.set_value("threads", 200)
    dlg.set_value("duplicate_threshold", 10)
    dlg.accept()
    get = config.get_setting
    assert get("rename", "ascii_only") == "1" and get("rename", "zero_pad") == "1"
    assert get("rename", "zero_pad_width") == "3" and get("auto_numbering", "padding") == "4"
    assert get("transcode", "crf") == "18" and get("transcode", "audio_bitrate") == "192k"
    assert get("transcode", "threads") == "200" and get("duplicates", "threshold") == "10"
    # ... and the transcode reader the converter uses sees them.
    from core.transcode_settings import get_transcode_settings
    s = get_transcode_settings()
    assert (s.crf, s.audio_bitrate, s.threads) == (18, "192k", 200)


def test_cancel_writes_nothing():
    dlg = VideoPreferencesDialog()
    dlg.set_value("crf", 30)
    dlg.reject()
    assert config.get_setting("transcode", "crf", "") == ""


def test_reads_existing_ini_values_and_survives_garbage():
    config.set_setting("rename", "zero_pad", "1")
    config.set_setting("rename", "zero_pad_width", "4")
    config.set_setting("transcode", "crf", "99")  # out of range
    config.set_setting("duplicates", "threshold", "abc")
    dlg = VideoPreferencesDialog()
    assert dlg.value("zero_pad_numbers") is True and dlg.value("zero_pad_width") == 4
    assert dlg.value("crf") == 23 and dlg.value("duplicate_threshold") == 6


def test_backend_saves_everything_in_one_write(monkeypatch):
    saves = []
    real = config.save_config
    monkeypatch.setattr(config, "save_config", lambda p: (saves.append(1), real(p)))
    make_backend().set_many({"crf": 20, "threads": 4, "ascii_filenames": False})
    assert len(saves) == 1
    assert config.get_setting("rename", "ascii_only") == "0"


@pytest.mark.parametrize("text,ok", [("128k", True), ("192K", True), ("1m", True), ("256", True),
                                      ("", False), ("12345k", False), ("k", False), ("128 k", False),
                                      ("128kb", False)])
def test_bitrate_rule(text, ok):
    assert bitrate_ok(text) is ok


def test_bad_bitrate_blocks_ok_and_apply_and_writes_nothing(monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    dlg = VideoPreferencesDialog()
    dlg.set_value("crf", 30)
    dlg.set_value("audio_bitrate", "fast")
    dlg.accept()
    dlg.apply()
    assert dlg.result() != dlg.DialogCode.Accepted
    assert len(warned) == 2 and "audio bitrate" in warned[0]
    assert config.get_setting("transcode", "crf", "") == ""


def test_paths_page_saves_tool_and_imdb_paths():
    dlg = VideoPreferencesDialog()
    from gui.video_preferences import ImdbPathsGroup
    group = dlg.findChild(ImdbPathsGroup)
    group.path_edit.setText("D:/db/imdb.db")
    group.source_edits["akas"].setText("D:/dump/title.akas.tsv.gz")
    group._save()
    assert imdb_settings.load_database() == "D:/db/imdb.db"
    assert imdb_settings.load_sources()["akas"] == "D:/dump/title.akas.tsv.gz"
    from gui.tool_settings_dialog import ToolPathsWidget
    tools = dlg.findChild(ToolPathsWidget)
    tools.path_edits["ffmpeg"].setText("C:/x/ffmpeg.exe")
    tools.path_edits["ffmpeg"].editingFinished.emit()
    from core.external_tools import get_tool_override
    assert get_tool_override("ffmpeg") == "C:/x/ffmpeg.exe"


def test_menu_entry_is_ctrl_comma_with_the_preferences_role(window):
    action = window.actions_by_key["preferences"]
    assert action.shortcut() == QKeySequence("Ctrl+,")
    assert action.menuRole() == QAction.MenuRole.PreferencesRole
    tools = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "Tools")
    assert tools.actions()[0] is action


def test_menu_entry_opens_the_dialog(window, monkeypatch):
    opened = []
    monkeypatch.setattr(VideoPreferencesDialog, "exec", lambda self: opened.append(self) or 0)
    window.actions_by_key["preferences"].trigger()
    assert len(opened) == 1
