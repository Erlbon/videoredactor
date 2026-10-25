"""Help > Check for Updates and Preferences > Updates are wired to redactor_common's self-updater; the daily check
is off by default."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

import gui.main_window as mw  # noqa: E402
from core import preferences_backend  # noqa: E402
from redactor_common.core import selfupdate  # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)


def test_help_menu_check_for_updates_is_wired(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)  # a modal box
    calls = []
    monkeypatch.setattr(mw.UpdateController, "check_now", lambda self: calls.append(self))
    window = mw.MainWindow()
    help_menu = next(a.menu() for a in window.menuBar().actions() if a.menu() and a.text().replace("&", "") == "Help")
    action = next(a for a in help_menu.actions() if a.text().replace("&", "") == "Check for Updates…")
    assert action.isEnabled()
    action.trigger()
    assert calls == [window.updates]
    assert window.updates.repo.lower() == "erlbon/videoredactor"
    assert not window.updates.settings.enabled and window.updates.start_auto_check() is False


def test_preferences_updates_switch_is_saved_in_its_own_file():
    assert any(s.key == selfupdate.SECTION_UPDATES for s in preferences_backend.build_sections())
    backend = preferences_backend.make_backend()
    assert backend.get(selfupdate.KEY_CHECK_FOR_UPDATES) is False
    backend.set_many({selfupdate.KEY_CHECK_FOR_UPDATES: True})
    assert selfupdate.UpdateSettings(mw.app_paths.updates_json_path()).enabled
