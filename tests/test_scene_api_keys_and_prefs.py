"""Tools > API Keys (the two scene keys, the StashDB address, 'Check the key') and Preferences > Look Up."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core import scene_lookup, stashdb_client, theporndb_client  # noqa: E402
from core.scene_lookup import SceneAuthError  # noqa: E402
from gui.api_keys_dialog import ApiKeysDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def boxes(monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: shown.append(("warning", title, text)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(("info", title, text)))
    return shown


def test_the_dialog_has_both_services_the_address_and_check_buttons():
    dialog = ApiKeysDialog()
    assert {"theporndb", "stashdb"} <= set(dialog.fields) and set(dialog.check_buttons) == {"theporndb", "stashdb"}
    assert dialog.stash_endpoint.text() == scene_lookup.DEFAULT_STASH_ENDPOINT
    assert "hash" in dialog.scene_note.text() and "Nothing else leaves" in dialog.scene_note.text()


def test_check_the_key_uses_the_typed_key_and_reports_ok(monkeypatch, boxes):
    seen = []
    monkeypatch.setattr(theporndb_client, "check_key", lambda key=None: seen.append(key) or "OK: ThePornDB accepted the key.")
    dialog = ApiKeysDialog()
    dialog.fields["theporndb"].edit.setText("typed-token")
    dialog.check_buttons["theporndb"].click()
    assert seen == ["typed-token"] and boxes == [("info", "ThePornDB key", "OK: ThePornDB accepted the key.")]


def test_check_the_key_shows_the_exact_reason_and_the_typed_address(monkeypatch, boxes):
    seen = []

    def check(key=None, endpoint=None):
        seen.append((key, endpoint))
        raise SceneAuthError("box.example.org rejected the API key (HTTP 401 Unauthorized).")

    monkeypatch.setattr(stashdb_client, "check_key", check)
    dialog = ApiKeysDialog()
    dialog.stash_endpoint.setText("https://box.example.org")
    dialog.check_buttons["stashdb"].click()
    assert seen == [(None, "https://box.example.org/graphql")]  # nothing typed: the saved key is used
    assert boxes[0][0] == "warning" and "HTTP 401 Unauthorized" in boxes[0][2]


def test_a_bad_address_is_refused_before_anything_is_saved(monkeypatch, boxes):
    dialog = ApiKeysDialog()
    dialog.stash_endpoint.setText("ftp://nope")
    dialog._on_save()
    assert boxes and "must start with https" in boxes[0][2]
    assert dialog.result() == 0 and scene_lookup.stash_endpoint() == scene_lookup.DEFAULT_STASH_ENDPOINT
    dialog.check_buttons["stashdb"].click()
    assert len(boxes) == 2  # the check refuses it too


def test_saving_stores_the_address_and_the_keys(monkeypatch):
    from core import api_keys

    dialog = ApiKeysDialog()
    dialog.stash_endpoint.setText("https://box.example.org/")
    dialog.fields["stashdb"].edit.setText("stash-secret")
    dialog.fields["theporndb"].edit.setText("tpdb-secret")
    dialog._on_save()
    assert scene_lookup.stash_endpoint() == "https://box.example.org/graphql"
    assert api_keys.get_key("stashdb") == "stash-secret" and api_keys.get_key("theporndb") == "tpdb-secret"
    from core import config

    assert "secret" not in open(config.CONFIG_PATH, encoding="utf-8").read()  # keys are never in the settings file


def test_preferences_has_the_look_up_page_with_md5_off_and_the_privacy_text():
    from core import preferences_backend as pb

    section = next(s for s in pb.build_sections() if s.key == "scene_lookup")
    spec = next(s for s in section.specs if s.key == pb.KEY_SCENE_MD5)
    assert spec.default is False and "whole file" in spec.label
    assert "ONLY a file's hash" in section.description and "no image is downloaded" in section.description
    backend = pb.make_backend()
    assert backend.get(pb.KEY_SCENE_MD5) is None and scene_lookup.md5_enabled() is False
    backend.set_many({pb.KEY_SCENE_MD5: True})
    assert scene_lookup.md5_enabled() is True
