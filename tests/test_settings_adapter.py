"""Export/Import Settings: the settings.ini adapter (core/settings_adapter.py)
and its File-menu wiring. Secrets must never reach a bundle."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

import core.config as config  # noqa: E402
import gui.main_window as mw  # noqa: E402
from core import api_keys  # noqa: E402
from core.redact_steps import RECIPE_KEY, RECIPE_SECTION  # noqa: E402
from core.settings_adapter import APP_SLUG, SECTIONS, VideoSettingsAdapter  # noqa: E402
from redactor_common.core import secret_store  # noqa: E402
from redactor_common.core import settings_bundle as sb  # noqa: E402
from tests.test_batch_operations_and_undo import window  # noqa: E402,F401

RECIPE = {"order": ["a", "b"], "enabled": {"a": True, "b": False}, "options": {"a": {"pattern": "%title%"}},
          "confidence_threshold": 0.8}
SEP = "\x1f"


@pytest.fixture
def ini(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    return config.CONFIG_PATH


def _seed():
    config.set_setting(RECIPE_SECTION, RECIPE_KEY, json.dumps(RECIPE))
    config.set_setting("filename_patterns", "history", SEP.join(["%title%", "%show_title%, %title%"]))
    config.set_setting("rename", "move_pattern", "%show_title%/%title%")
    config.set_setting("table", "column_order", "filename,title,genre_tags")
    config.set_setting("table", "hidden_columns", "network,studio")
    config.set_setting("table", "column_widths", json.dumps({"title": 222}))
    config.set_setting("rename", "zero_pad", "1")
    config.set_setting("rename", "zero_pad_width", "3")
    config.set_setting("rename", "ascii_only", "1")
    config.set_setting("vocabulary", "genres", SEP.join(["Noir", "Comedy"]))
    config.set_setting("transcode", "crf", "19")
    config.set_setting("duplicates", "threshold", "9")
    config.set_setting("tools", "ffmpeg", "C:/tools/ffmpeg.exe")
    config.set_setting("general", "last_folder", "D:/Videos")


def _export(adapter, include=None):
    include = set(include) if include is not None else sb.default_selection(adapter)
    return json.loads(sb.dump_bundle(sb.build_bundle(adapter, include)))


def test_round_trip_export_change_import_restores(ini):
    _seed()
    adapter = VideoSettingsAdapter()
    everything = {s.key for s in adapter.sections()}
    text = sb.dump_bundle(sb.build_bundle(adapter, everything))
    before = {k: adapter.read_section(k) for k in everything}
    # Change a value in most sections.
    config.set_setting("table", "hidden_columns", "")
    config.set_setting("rename", "zero_pad", "0")
    config.set_setting("vocabulary", "genres", "X")
    config.set_setting("transcode", "crf", "30")
    config.set_setting("tools", "ffmpeg", "")
    config.set_setting(RECIPE_SECTION, RECIPE_KEY, "{}")
    config.set_setting("filename_patterns", "history", "zzz")
    config.set_setting("general", "last_folder", "E:/Other")
    assert {k: adapter.read_section(k) for k in everything} != before
    bundle = sb.parse_bundle(text, APP_SLUG)
    result = sb.apply_bundle(adapter, bundle, everything)
    assert not result.failed
    assert {k: adapter.read_section(k) for k in everything} == before
    assert sb.diff_bundle(adapter, bundle) == []


def test_values_are_normalised_to_json_types(ini):
    _seed()
    adapter = VideoSettingsAdapter()
    assert adapter.read_section("defaults") == {
        "zero_pad": True, "zero_pad_width": 3, "ascii_only": True, "auto_number_padding": 2}
    assert adapter.read_section("columns") == {
        "order": ["filename", "title", "genre_tags"], "hidden": ["network", "studio"], "widths": {"title": 222}}
    assert adapter.read_section("patterns")["history"] == ["%title%", "%show_title%, %title%"]
    assert adapter.read_section("redact")["recipe"] == RECIPE
    media = adapter.read_section("media")
    assert media["crf"] == 19 and media["duplicate_threshold"] == 9
    json.dumps({k: adapter.read_section(k) for k in SECTIONS})  # all JSON-able


def test_unset_and_corrupt_values_read_as_defaults(ini):
    config.set_setting("transcode", "crf", "banana")
    config.set_setting("table", "column_widths", "{not json")
    config.set_setting(RECIPE_SECTION, RECIPE_KEY, "garbage")
    adapter = VideoSettingsAdapter()
    assert adapter.read_section("media") == {
        "crf": 23, "audio_bitrate": "128k", "threads": 0, "duplicate_threshold": 6}
    assert adapter.read_section("columns")["widths"] == {}
    assert adapter.read_section("redact") == {"recipe": ""}
    assert adapter.read_section("defaults")["zero_pad_width"] == 2
    assert "Drama" in adapter.read_section("vocabulary")["genres"]
    assert "genres" not in ini.read_text(encoding="utf-8")  # reading seeds nothing


def test_machine_specific_sections_are_excluded_by_default(ini):
    _seed()
    adapter = VideoSettingsAdapter()
    portable = {s.key for s in adapter.sections() if s.portable}
    assert sb.default_selection(adapter) == portable
    assert {"tools", "folders"}.isdisjoint(portable)
    text = json.dumps(_export(adapter))
    assert "ffmpeg.exe" not in text and "D:/Videos" not in text
    opted_in = json.dumps(_export(adapter, {"tools", "folders"}))
    assert "ffmpeg.exe" in opted_in and "D:/Videos" in opted_in


def test_no_api_key_or_consent_flag_is_ever_in_a_bundle(ini, fake_keyring):
    service = secret_store.service_name(api_keys.APP)
    fake_keyring.data[(service, "tmdb_api_key")] = "SECRET-TMDB-123"
    fake_keyring.data[(service, "tvdb_api_key")] = "SECRET-TVDB-456"
    fake_keyring.data[(service, "opensubtitles_api_key")] = "SECRET-OS-789"
    config.set_setting("secrets", "allow_unencrypted_fallback", "1")
    config.set_setting("tmdb", "api_key", "SECRET-LEGACY-000")
    _seed()
    adapter = VideoSettingsAdapter()
    everything = {s.key for s in adapter.sections()}
    text = sb.dump_bundle(sb.build_bundle(adapter, everything))
    for needle in ("SECRET", "api_key", "allow_unencrypted_fallback", "secrets"):
        assert needle not in text
    # No key in any section even looks like a credential, or lives in a secret ini section.
    for _label, _portable, keys in SECTIONS.values():
        assert not [k.name for k in keys if sb.looks_secret(k.name)]
        assert all(k.ini_section not in ("secrets", "tmdb", "tvdb", "opensubtitles") for k in keys)
    # A hostile file can't write them either.
    hostile = sb.Bundle(app=APP_SLUG, sections={
        "media": sb.BundleSection("x", {"crf": 20, "api_key": "evil", "allow_unencrypted_fallback": "1"}),
        "secrets": sb.BundleSection("x", {"allow_unencrypted_fallback": "0"}),
        "tmdb": sb.BundleSection("x", {"api_key": "evil"}),
    })
    sb.apply_bundle(adapter, hostile, {"media", "secrets", "tmdb"})
    assert config.get_setting("transcode", "crf") == "20"
    assert config.get_setting("secrets", "allow_unencrypted_fallback") == "1"  # untouched
    assert config.get_setting("tmdb", "api_key") == "SECRET-LEGACY-000"
    assert "evil" not in ini.read_text(encoding="utf-8")


def test_unknown_keys_are_ignored_and_other_keys_untouched(ini):
    config.set_setting("general", "unrelated", "keep")
    config.set_setting("transcode", "crf", "21")
    adapter = VideoSettingsAdapter()
    adapter.write_section("media", {"crf": 25, "surprise": "1"})
    assert config.get_setting("transcode", "crf") == "25"
    assert config.get_setting("general", "unrelated") == "keep"
    assert not config.load_config().has_option("transcode", "surprise")
    assert set(adapter.read_section("media")) == {"crf", "audio_bitrate", "threads", "duplicate_threshold"}


def test_invalid_values_are_rejected_but_valid_ones_still_apply(ini):
    adapter = VideoSettingsAdapter()
    with pytest.raises(ValueError, match="crf"):
        adapter.write_section("media", {"crf": 999, "threads": 4, "audio_bitrate": "lots"})
    assert config.get_setting("transcode", "threads") == "4"
    assert config.get_setting("transcode", "crf") == ""
    with pytest.raises(ValueError):
        adapter.write_section("defaults", {"zero_pad": "yes", "ascii_only": True})
    assert config.get_setting("rename", "ascii_only") == "1" and config.get_setting("rename", "zero_pad") == ""
    with pytest.raises(ValueError):
        adapter.write_section("redact", {"recipe": {"unrelated": 1}})


def test_hidden_columns_always_keep_the_filename_column(ini):
    VideoSettingsAdapter().write_section("columns", {"hidden": ["filename", "network"]})
    assert VideoSettingsAdapter().read_section("columns")["hidden"] == ["network"]


def test_another_apps_file_is_rejected():
    text = json.dumps({"format": "redactor-settings", "version": 1, "app": "cbzredactor", "sections": {}})
    with pytest.raises(sb.SettingsBundleError):
        sb.parse_bundle(text, APP_SLUG)


def test_a_section_is_saved_in_one_atomic_write(ini, monkeypatch):
    saved = []
    real = config.save_config
    monkeypatch.setattr(config, "save_config", lambda parser: (saved.append(1), real(parser)))
    VideoSettingsAdapter().write_section("defaults", {"zero_pad": True, "zero_pad_width": 4})
    assert len(saved) == 1


def test_bundle_header_names_the_app_and_version(ini):
    from core.version import APP_VERSION

    bundle = _export(VideoSettingsAdapter())
    assert bundle["app"] == "videoredactor" and bundle["app_version"] == APP_VERSION


# --- menu wiring -------------------------------------------------------------------


def test_file_menu_has_enabled_export_and_import(window):
    reg = window.actions_by_key
    for key, text in (("export_settings", "Export Settings…"), ("import_settings", "Import Settings…")):
        assert reg[key].isEnabled() and reg[key].text().replace("&", "") == text
    file_menu = next(a.menu() for a in window.menuBar().actions() if a.text().replace("&", "") == "File")
    shape = [a.text().replace("&", "") for a in file_menu.actions() if not a.isSeparator()]
    assert (shape.index("Rename / Export / Move…") < shape.index("Export Settings…")
            < shape.index("Import Settings…") < shape.index("Remove from List"))


def test_menu_actions_open_the_shared_dialogs(window, monkeypatch):
    calls = []
    monkeypatch.setattr(mw, "export_settings", lambda parent, adapter: calls.append(("export", adapter, None)))
    monkeypatch.setattr(mw, "import_settings",
                        lambda parent, adapter, on_applied=None: calls.append(("import", adapter, on_applied)))
    window.actions_by_key["export_settings"].trigger()
    window.actions_by_key["import_settings"].trigger()
    assert [c[0] for c in calls] == ["export", "import"]
    assert all(isinstance(c[1], VideoSettingsAdapter) and c[1].app_slug == APP_SLUG for c in calls)
    assert callable(calls[1][2]) and callable(calls[0][1].redetect_tools)


def test_import_refreshes_columns_and_panel_live(window, monkeypatch):
    window._rebuild_table_columns(None)
    assert not window.table.isColumnHidden(window._column_order.index("network"))
    VideoSettingsAdapter().write_section("columns", {"hidden": ["network"]})
    refreshed = []
    monkeypatch.setattr(window.tag_panel, "refresh_fields", lambda: refreshed.append(1))
    window._after_settings_import(sb.ApplyResult(applied=["columns"]))
    assert window.table.isColumnHidden(window._column_order.index("network"))
    assert refreshed
    window._after_settings_import(sb.ApplyResult(applied=["patterns"]))  # nothing to refresh, no error


def test_redetect_checks_again_and_prompts_only_if_missing(window, monkeypatch):
    monkeypatch.setattr(mw, "missing_tools", lambda: [])
    window._redetect_tools()
    assert "detected" in window.status_bar.currentMessage()
    prompted = []
    monkeypatch.setattr(mw, "missing_tools", lambda: ["x"])
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: prompted.append(1))
    window._redetect_tools()
    assert prompted == [1]
