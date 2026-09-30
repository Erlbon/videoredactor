"""Redact's pattern trail: each pattern option offers the app's pattern
history, follows a fallback while empty, previews itself, and a recipe's
saved pattern is kept as saved (first-save pinning) instead of silently
following later Rename/Export changes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog, QLabel  # noqa: E402
from redactor_common.core.pipeline import Recipe  # noqa: E402
from redactor_common.gui.redact_dialog import RecipeEditorDialog  # noqa: E402

from core import redact_steps as rs  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

KEYS = ("filename_tags", "rename", "move_into_folders", "path_tags")


def env_with(history, settings=None):
    settings = dict(settings or {})
    return rs.RedactEnv(
        pattern_history=lambda: list(history),
        setting=lambda section, key, default="": settings.get((section, key), default),
    )


def spec_of(catalogue, key):
    step = next(s for s in catalogue if s.key == key)
    return step.options[0]


def test_every_pattern_option_carries_the_trail_callables():
    env = env_with(["%title%", "%show_title%/%title%"], {("rename", "move_pattern"): "%show_title%/%title%"})
    catalogue = rs.build_catalogue(env=env)
    for key in KEYS:
        spec = spec_of(catalogue, key)
        assert spec.suggestions and spec.fallback and spec.fallback_label and spec.preview
    # History newest first: filename patterns, then path patterns; path options reversed.
    assert spec_of(catalogue, "rename").suggestions() == ["%title%", "%show_title%/%title%"]
    assert spec_of(catalogue, "path_tags").suggestions() == ["%show_title%/%title%", "%title%"]
    assert spec_of(catalogue, "rename").fallback() == "%title%"
    assert spec_of(catalogue, "path_tags").fallback() == "%show_title%/%title%"
    assert spec_of(catalogue, "move_into_folders").fallback() == "%show_title%/%title%"


def test_fallbacks_without_history_and_the_preview_sample():
    catalogue = rs.build_catalogue(env=env_with([]))
    assert spec_of(catalogue, "rename").fallback() == ""
    assert spec_of(catalogue, "path_tags").fallback() == rs.DEFAULT_PATH_PATTERN
    assert spec_of(catalogue, "move_into_folders").fallback() == rs.DEFAULT_MOVE_PATTERN
    rename = spec_of(catalogue, "rename")
    assert rename.preview("%show_title% - S%season_number%E%episode_number%") == "Show - S1E1"
    assert spec_of(catalogue, "move_into_folders").preview(rs.DEFAULT_MOVE_PATTERN) == "Show/Season 1/Episode"
    assert rename.preview("") == ""


def test_preview_uses_the_first_video_and_never_raises():
    def boom():
        raise RuntimeError("no video")

    values = {"show_title": "Lost", "title": "Pilot"}
    catalogue = rs.build_catalogue(env=env_with([]), sample=lambda: values)
    assert spec_of(catalogue, "rename").preview("%show_title% - %title%") == "Lost - Pilot"
    catalogue = rs.build_catalogue(env=env_with([]), sample=boom)
    assert spec_of(catalogue, "rename").preview("%title%") == "Episode"


def test_step_resolution_stored_wins_and_empty_follows_the_fallback(tmp_path):
    env = env_with(["%title%"])
    step = rs.RenameStep(env=env)
    ctx = rs.VideoCtx(VideoFile(path=tmp_path / "a.mp4", metadata=VideoMetadata()), env)
    ctx.step_options = {"pattern": ""}
    assert step.effective_pattern(ctx) == "%title%"  # follows the latest filename pattern
    ctx.step_options = {"pattern": "%show_title% - %title%"}
    assert step.effective_pattern(ctx) == "%show_title% - %title%"
    # Resolved against THIS run's history, not the catalogue's.
    ctx.env = env_with(["%release_date% %title%"])
    ctx.step_options = {"pattern": ""}
    assert step.effective_pattern(ctx) == "%release_date% %title%"


def test_first_save_pinning_fills_empty_patterns_only():
    env = env_with(["%title%", "%show_title%/%title%"], {("rename", "move_pattern"): "%show_title%/Season %season_number%/%title%"})
    catalogue = rs.build_catalogue(env=env)
    recipe = Recipe.default_for(catalogue)
    recipe.options["rename"]["pattern"] = "%show_title% - %title%"
    pinned = rs.pin_patterns(recipe, catalogue)
    assert pinned.options["rename"]["pattern"] == "%show_title% - %title%"  # kept as typed
    assert pinned.options["filename_tags"]["pattern"] == "%title%"
    assert pinned.options["path_tags"]["pattern"] == "%show_title%/%title%"
    assert pinned.options["move_into_folders"]["pattern"] == "%show_title%/Season %season_number%/%title%"
    assert recipe.options["filename_tags"]["pattern"] == ""  # the input is not mutated
    # Nothing to pin (no filename history): stays empty, following.
    empty = rs.build_catalogue(env=env_with([]))
    assert rs.pin_patterns(Recipe.default_for(empty), empty).options["rename"]["pattern"] == ""


def test_a_pinned_recipe_ignores_later_rename_export_changes(tmp_path):
    history = ["%title%"]
    env = env_with(history)
    catalogue = rs.build_catalogue(env=env)
    pinned = rs.pin_patterns(Recipe.default_for(catalogue), catalogue)
    # The user later uses a different Rename/Export pattern.
    history.insert(0, "%show_title% - %title%")
    resolved = dict((s.key, o) for s, o in pinned.resolve(catalogue))
    assert resolved["rename"]["pattern"] == "%title%"
    step = next(s for s in catalogue if s.key == "rename")
    ctx = rs.VideoCtx(VideoFile(path=tmp_path / "a.mp4", metadata=VideoMetadata()), env)
    ctx.step_options = resolved["rename"]
    assert step.effective_pattern(ctx) == "%title%"
    # An unpinned (empty) recipe would have followed.
    ctx.step_options = {"pattern": ""}
    assert step.effective_pattern(ctx) == "%show_title% - %title%"


def test_an_old_recipe_json_loads_unchanged():
    catalogue = rs.build_catalogue(env=env_with(["%title%"]))
    text = (
        '{"order":["check_repair","rename"],"enabled":{"check_repair":true,"rename":true},'
        '"options":{"rename":{"pattern":"%show_title% - %title%"},"path_tags":{"pattern":""}},'
        '"confidence_threshold":0.8}'
    )
    recipe = rs.recipe_from_setting(text, catalogue)
    assert recipe.options["rename"]["pattern"] == "%show_title% - %title%"
    assert recipe.options["path_tags"]["pattern"] == ""
    assert recipe.confidence_threshold == 0.8
    assert rs.recipe_to_setting(recipe) == text


def test_the_editor_shows_the_trail_caption_and_suggestions():
    env = env_with(["%title%", "%show_title%/%title%"])
    catalogue = rs.build_catalogue(env=env)
    recipe = Recipe.default_for(catalogue)
    recipe.options["rename"]["pattern"] = "%show_title% - %title%"
    dialog = RecipeEditorDialog(catalogue, recipe)
    for row in range(dialog.list.count()):
        if dialog.list.item(row).data(Qt.ItemDataRole.UserRole) == "rename":
            dialog.list.setCurrentRow(row)
    texts = [w.text() for w in dialog.findChildren(QLabel) if w.objectName() in ("pattern_caption", "pattern_preview")]
    assert any("In effect: %show_title% - %title%" in t and "set in this recipe" in t for t in texts)
    assert any(t.startswith("Preview") and "Show - Episode" in t for t in texts)
    combo = dialog.findChildren(QComboBox)[0]
    assert [combo.itemText(i) for i in range(combo.count())][:2] == ["%title%", "%show_title%/%title%"]
    # A followed (empty) value says what it follows.
    recipe.options["rename"]["pattern"] = ""
    dialog2 = RecipeEditorDialog(catalogue, recipe)
    for row in range(dialog2.list.count()):
        if dialog2.list.item(row).data(Qt.ItemDataRole.UserRole) == "rename":
            dialog2.list.setCurrentRow(row)
    texts = [w.text() for w in dialog2.findChildren(QLabel) if w.objectName() == "pattern_caption"]
    assert any("follows: the latest filename pattern" in t for t in texts)


def test_first_open_pins_and_ok_stores_it_then_later_history_changes_do_not_steer(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    window = mw.MainWindow()
    try:
        config.set_setting("rename", "move_pattern", "%show_title%/%title%")
        history = ["%title%"]
        monkeypatch.setattr(rs, "load_pattern_history", lambda: list(history))
        monkeypatch.setattr(mw, "build_catalogue", lambda sample=None: rs.build_catalogue(lambda: list(history), sample=sample))
        monkeypatch.setattr(mw.RecipeEditorDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
        assert not rs.recipe_is_saved()
        window._on_edit_redact_recipe()
        assert rs.recipe_is_saved()
        stored = rs.load_recipe(rs.build_catalogue(lambda: list(history)))
        assert stored.options["rename"]["pattern"] == "%title%"
        assert stored.options["move_into_folders"]["pattern"] == "%show_title%/%title%"
        history.insert(0, "%show_title% - %title%")
        window._on_edit_redact_recipe()  # an already-saved recipe is never re-pinned
        stored = rs.load_recipe(rs.build_catalogue(lambda: list(history)))
        assert stored.options["rename"]["pattern"] == "%title%"
    finally:
        for vf in window.video_files:
            vf.dirty = False
        window.close()
