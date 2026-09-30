"""API keys in redactor_common's secret store: lookups, migration out of
settings.ini, the unavailable-keyring path, and the API Keys dialog. The
keyring is the in-memory fake from conftest -- never the real one."""

import pytest
from PyQt6.QtWidgets import QApplication, QMessageBox

import core.config as config
from core import api_keys, opensubtitles_client, tmdb_client, tvdb_client
from gui.api_keys_dialog import ApiKeysDialog
from redactor_common.core import secret_store

from conftest import FakeKeyring

SERVICE = secret_store.service_name("videoredactor")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _seed_ini():
    config.set_setting("tmdb", "api_key", "tmdb-old")
    config.set_setting("tvdb", "api_key", "tvdb-old")
    config.set_setting("opensubtitles", "api_key", "os-old")


def test_migration_moves_keys_and_clears_ini(fake_keyring):
    _seed_ini()
    api_keys.migrate_legacy_keys()
    assert fake_keyring.data[(SERVICE, "tmdb_api_key")] == "tmdb-old"
    assert fake_keyring.data[(SERVICE, "tvdb_api_key")] == "tvdb-old"
    assert fake_keyring.data[(SERVICE, "opensubtitles_api_key")] == "os-old"
    for section in ("tmdb", "tvdb", "opensubtitles"):
        assert config.get_setting(section, "api_key") == ""
    assert "old" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert tmdb_client.get_api_key() == "tmdb-old"


def test_unavailable_keyring_keeps_legacy_working(monkeypatch):
    secret_store.set_backend(FakeKeyring(fail_set=True))
    _seed_ini()
    api_keys.migrate_legacy_keys()  # must not raise
    assert config.get_setting("tmdb", "api_key") == "tmdb-old"
    assert tmdb_client.get_api_key() == "tmdb-old"
    assert tvdb_client.get_api_key() == "tvdb-old"
    assert opensubtitles_client.get_api_key() == "os-old"


def test_env_var_wins(monkeypatch, fake_keyring):
    _seed_ini()
    api_keys.migrate_legacy_keys()
    monkeypatch.setenv("TMDB_API_KEY", "from-env")
    assert tmdb_client.get_api_key() == "from-env"
    assert tvdb_client.get_api_key() == "tvdb-old"


def test_each_lookup_gets_its_own_key(fake_keyring):
    fake_keyring.data[(SERVICE, "tmdb_api_key")] = "A"
    fake_keyring.data[(SERVICE, "tvdb_api_key")] = "B"
    fake_keyring.data[(SERVICE, "opensubtitles_api_key")] = "C"
    assert (tmdb_client.get_api_key(), tvdb_client.get_api_key(),
            opensubtitles_client.get_api_key()) == ("A", "B", "C")


def test_no_key_anywhere_is_none_and_error_points_to_dialog():
    assert tmdb_client.get_api_key() is None
    with pytest.raises(tmdb_client.TMDBError, match="API Keys"):
        tmdb_client._require_api_key()
    with pytest.raises(tvdb_client.TVDBError, match="API Keys"):
        tvdb_client._require_api_key()
    with pytest.raises(opensubtitles_client.OpenSubtitlesError, match="API Keys"):
        opensubtitles_client._require_api_key()


def test_dialog_saves_to_store_not_ini(app, fake_keyring):
    dlg = ApiKeysDialog()
    dlg.fields["tmdb"].edit.setText("typed-tmdb")
    dlg.save_button.click()
    assert fake_keyring.data[(SERVICE, "tmdb_api_key")] == "typed-tmdb"
    assert not config.CONFIG_PATH.exists() or "typed-tmdb" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    assert dlg.result() == dlg.DialogCode.Accepted


def test_dialog_replacing_clears_unmigrated_ini_copy(app, fake_keyring):
    _seed_ini()
    dlg = ApiKeysDialog()
    dlg.fields["tvdb"].edit.setText("new-tvdb")
    dlg.save_button.click()
    assert tvdb_client.get_api_key() == "new-tvdb"
    assert config.get_setting("tvdb", "api_key") == ""


def test_dialog_declining_fallback_keeps_dialog_open(app, monkeypatch):
    secret_store.set_backend(FakeKeyring(fail_set=True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)
    dlg = ApiKeysDialog()
    dlg.fields["tmdb"].edit.setText("typed")
    dlg.save_button.click()
    assert dlg.result() != dlg.DialogCode.Accepted
    assert dlg.fields["tmdb"].edit.text() == "typed"
    assert not secret_store.fallback_path("videoredactor").exists()
    assert not secret_store.allow_unencrypted_fallback_enabled()


def test_dialog_accepting_fallback_saves_and_remembers(app, monkeypatch):
    secret_store.set_backend(FakeKeyring(fail_set=True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    dlg = ApiKeysDialog()
    dlg.fields["tmdb"].edit.setText("typed")
    dlg.save_button.click()
    assert dlg.result() == dlg.DialogCode.Accepted
    assert tmdb_client.get_api_key() == "typed"
    assert secret_store.secret_source("videoredactor", "tmdb_api_key") == secret_store.SOURCE_FILE
    assert "typed" not in config.CONFIG_PATH.read_text(encoding="utf-8")
    # remembered across launches: the next startup re-applies it
    secret_store.set_allow_unencrypted_fallback(False)
    api_keys.apply_fallback_preference()
    assert secret_store.allow_unencrypted_fallback_enabled()
