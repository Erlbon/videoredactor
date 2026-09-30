"""Shared test setup: isolate tool lookup from whatever happens to be
installed on the machine running the tests. The well-known install
folder tier (core.external_tools._install_dirs) would otherwise find a
real MKVToolNix under Program Files and break tests that deliberately
simulate it being absent."""

import pytest


@pytest.fixture(autouse=True)
def _no_install_dir_tool_lookup(monkeypatch):
    import core.external_tools as external_tools

    monkeypatch.setattr(external_tools, "_install_dirs", lambda: [])


@pytest.fixture(autouse=True)
def _isolated_rename_log(monkeypatch, tmp_path):
    """File > Undo Last Rename's log (redactor_common's RenameLog) lives
    next to the settings -- the project folder when running from source.
    Tests that rename files point it at a temporary folder instead."""
    import gui.main_window as main_window
    from redactor_common.core.rename_log import RenameLog

    log = RenameLog(str(tmp_path / "rename_log.json"))
    monkeypatch.setattr(main_window, "_rename_log", lambda: log)


class FakeKeyring:
    """In-memory stand-in for the OS credential store (keyring's API)."""

    def __init__(self, fail_set=False):
        self.data = {}
        self.fail_set = fail_set

    def get_password(self, service, name):
        return self.data.get((service, name))

    def set_password(self, service, name, value):
        if self.fail_set:
            raise OSError("no credential store")
        self.data[(service, name)] = value

    def delete_password(self, service, name):
        if (service, name) not in self.data:
            raise type("PasswordDeleteError", (Exception,), {})("not found")
        del self.data[(service, name)]


@pytest.fixture(autouse=True)
def fake_keyring(tmp_path, monkeypatch):
    """No test may touch the real Windows Credential Manager (or the real
    settings.ini / env keys): in-memory keyring, fallback file and settings
    in tmp_path, no API key env vars."""
    import core.config as config
    from redactor_common.core import secret_store

    backend = FakeKeyring()
    secret_store.set_backend(backend)
    secret_store.set_fallback_dir(tmp_path / "secrets")
    secret_store.set_allow_unencrypted_fallback(False)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    for var in ("TMDB_API_KEY", "TVDB_API_KEY", "OPENSUBTITLES_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    yield backend
    secret_store.set_backend(None)
    secret_store.set_fallback_dir(None)
    secret_store.set_allow_unencrypted_fallback(False)
