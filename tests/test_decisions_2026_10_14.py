"""Owner decisions of 2026-10-14: no Ctrl+S alias (V1), "Open" wording (V2), the Redact convert step's dry run
asked per run (V3), one end-of-run summary for Convert to MP4 (H.264) (V4), and the two conversion defaults
made explicit (V6). Dialog exec()s are patched so the real handlers run headless."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QCheckBox, QMessageBox  # noqa: E402
from redactor_common.core.pipeline import Recipe  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import conversion_run as cr  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])


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


def _files(window, tmp_path, *names):
    files = []
    for name in names:
        (tmp_path / name).write_bytes(b"")
        files.append(VideoFile(path=tmp_path / name))
    window.video_files = files
    window._refresh_table_rows()
    return files


# --- V1: no Ctrl+S / Ctrl+Shift+S aliases ------------------------------------------------------------------------


def test_v1_ctrl_s_is_bound_to_nothing(window):
    from PyQt6.QtGui import QAction

    keys = {k.toString() for a in window.findChildren(QAction) for k in a.shortcuts()}
    assert "Ctrl+S" not in keys and "Ctrl+Shift+S" not in keys and "F8" not in keys
    assert [k.toString() for k in window.actions_by_key["save_all"].shortcuts()] == ["Ctrl+Shift+A"]


# --- V2: the menu's own words ------------------------------------------------------------------------------------


def test_v2_empty_list_messages_say_open_files(window, monkeypatch):
    said = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: said.append(text))
    window._on_check_files()
    window._on_find_duplicates()
    window._on_redact()
    window._batch_targets("rename")
    assert len(said) == 4
    for text in said:
        assert "Open Files" in text and "Load" not in text, text


def test_v2_import_and_convert_picker_title(window, monkeypatch):
    titles = []
    monkeypatch.setattr(window, "_pick_videos_to_convert", lambda title, exts: titles.append(title) or [])
    monkeypatch.setattr(window, "_convert_paths", lambda paths, dry_run=None: None)
    window._on_import_and_convert()
    assert titles == ["Import and Convert"]


# --- V3: the convert step's dry run, per run ---------------------------------------------------------------------


def _ctx(env, path):
    ctx = rs.VideoCtx.__new__(rs.VideoCtx)
    ctx.env, ctx.start_mtime, ctx.skip_reason, ctx.original = env, None, "", str(path)
    ctx.video = type("V", (), {"path": path, "metadata": type("M", (), {"content_type": rs.ContentType.TV})()})()
    ctx.bg = lambda fn, *a, **k: fn(*a, **k)
    return ctx


@pytest.mark.parametrize("saved,choice", [("0", True), ("1", False)])
def test_v3_the_run_choice_beats_the_saved_setting(tmp_path, monkeypatch, saved, choice):
    seen = []
    monkeypatch.setattr(
        rs.conversion_run, "process_file",
        lambda run, p, conv, **kw: seen.append(run.dry_run) or cr.ConversionOutcome(False, message="nothing to do"),
    )
    settings = {("safety", "dry_run"): saved}
    env = rs.RedactEnv(trash=lambda p: None, setting=lambda s, k, d="": settings.get((s, k), d), convert_dry_run=choice)
    path = tmp_path / "a.avi"
    path.write_bytes(b"")
    rs.ConvertStep().execute(_ctx(env, path))
    assert seen == [choice]


def _convert_recipe_on(monkeypatch, on: bool):
    import gui.main_window as mw

    def fake_load(catalogue):
        recipe = Recipe.default_for(list(catalogue))
        recipe.enabled["convert_library"] = on
        return recipe

    monkeypatch.setattr(mw, "load_recipe", fake_load)


@pytest.mark.parametrize("select", [True, False])
@pytest.mark.parametrize("untick", [False, True])
def test_v3_redact_confirm_asks_the_dry_run_ticked_by_default(window, monkeypatch, tmp_path, select, untick):
    files = _files(window, tmp_path, "a.mp4", "b.mp4")
    if select:
        window.table.selectAll()
    _convert_recipe_on(monkeypatch, True)
    boxes = []

    def fake_exec(self):
        box = self.checkBox()
        boxes.append((self.windowTitle(), box.text() if box else None, box.isChecked() if box else None))
        if untick:
            box.setChecked(False)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("the old question has no checkbox"))
    targets, dry = window._redact_targets()
    assert len(boxes) == 1
    title, label, checked = boxes[0]
    assert title == ("Redact" if select else "Redact all files?")
    assert label == "Dry run the conversion step (list what would happen, change nothing)" and checked is True
    assert targets == files and dry is (not untick)


def test_v3_redact_passes_the_choice_to_the_step(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    _files(window, tmp_path, "a.mp4")
    window.table.selectAll()
    _convert_recipe_on(monkeypatch, True)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Yes)
    envs = []
    monkeypatch.setattr(mw, "run_redact_dialog", lambda *a, make_context, **k: envs.append(make_context) or None)
    real_ctx = mw.VideoCtx
    monkeypatch.setattr(mw, "VideoCtx", lambda vf, env: envs.append(env) or real_ctx(vf, env))
    window._on_redact()
    envs[0](window.video_files[0])
    assert envs[1].convert_dry_run is True


def test_v3_no_extra_question_when_the_convert_step_is_off(window, monkeypatch, tmp_path):
    files = _files(window, tmp_path, "a.mp4")
    window.table.selectAll()
    _convert_recipe_on(monkeypatch, False)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: pytest.fail("no confirm expected"))
    assert window._redact_targets() == (files, None)


def test_v3_preferences_have_no_redact_dry_run_option():
    from gui.video_preferences import SafetyPage

    page = SafetyPage()
    texts = [box.text() for box in page.findChildren(QCheckBox)]
    assert not any("dry run" in t.lower() for t in texts), texts


def test_v3_cli_convert_dry_run_flag(monkeypatch, tmp_path, capsys):
    import core.config as config
    from videocli import cmd_redact
    from videocli.main import main

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    envs = []
    real_env = cmd_redact.RedactEnv
    monkeypatch.setattr(cmd_redact, "RedactEnv", lambda **kw: envs.append(real_env(**kw)) or envs[-1])
    monkeypatch.setattr(cmd_redact, "collect", lambda paths, out, recurse=True: [])
    monkeypatch.setattr(cmd_redact, "load_videos", lambda paths, out: [])
    monkeypatch.setattr(cmd_redact, "redact_items", lambda *a, **k: 0)
    main(["redact", str(tmp_path / "x.mp4"), "--convert-dry-run"])
    main(["redact", str(tmp_path / "x.mp4")])
    capsys.readouterr()
    assert [e.convert_dry_run for e in envs] == [True, False]


# --- V4: one summary at the end of Convert to MP4 (H.264) --------------------------------------------------------


# --- V6: the two conversion defaults ------------------------------------------------------------------------------


def test_v6_conversion_defaults_are_pinned_and_explained(monkeypatch, tmp_path):
    import core.config as config
    from gui.video_preferences import DISC_HELP, MANY_SUBS_HELP, ConversionPage

    defaults = cp.ConversionSettings()
    assert defaults.many_subtitle_tracks == 4
    assert defaults.disc_images == cp.DISC_LEAVE == "leave"
    for ext in (".iso", ".bin", ".img", ".vob"):  # never converted, and not listed, under the default
        plan = cp.plan_conversion(cp.MediaInfo(f"disc{ext}", 1, 1.0, []), defaults)
        assert plan.skip_reason and not plan.needs_review
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    page = ConversionPage()
    assert page.many_subs_help.text() == MANY_SUBS_HELP and "Default: 4" in MANY_SUBS_HELP
    assert page.disc_help.text() == DISC_HELP and "Default: Leave alone" in DISC_HELP and "never converted" in DISC_HELP
