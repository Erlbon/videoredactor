"""Tidy-up pass 2026-10-13#10: progress dialogs for reloads, lookups and rename loops; the conversion rules'
series test and duration tolerance; Preferences pages that obey Cancel; status-bar messages instead of silent
no-ops; menu texts that name the real place."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import conversion_run as cr  # noqa: E402
from core import library_safety as ls  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402

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
    w.close()


def _record_progress(monkeypatch):
    import gui.main_window as mw

    calls = []
    real = mw.run_with_progress

    def spy(parent, items, step, label, **kw):
        items = list(items)
        calls.append((label, len(items), kw.get("cancellable", True)))
        return real(parent, items, step, label, **kw)

    monkeypatch.setattr(mw, "run_with_progress", spy)
    return calls


# --- T5: what counts as a series episode --------------------------------------------------------------------


@pytest.mark.parametrize("content_type, stem, expected", [
    (ContentType.TV, "anything", True),
    (ContentType.MOVIE, "Show.S01E02", False),
    (ContentType.CLIP, "Show.S01E02", False),
    (ContentType.MISC, "holiday", False),
    (ContentType.UNSET, "Show.S01E02.720p", True),
    (ContentType.UNSET, "holiday clip", False),
    (None, "Show.S01E02", True),
    (None, "some film 2019", False),
])
def test_series_is_tv_or_an_untagged_episode_name(content_type, stem, expected):
    assert rs.conversion_is_series(content_type, stem) is expected


def test_convert_step_passes_the_new_series_rule(monkeypatch, tmp_path):
    seen = {}

    def fake_process(run, path, conv, **kw):
        seen["is_series"] = kw["is_series"]
        return cr.ConversionOutcome(False, message="nothing to do")

    monkeypatch.setattr(rs.conversion_run, "process_file", fake_process)
    path = tmp_path / "holiday clip.mp4"
    path.write_bytes(b"x")
    vf = VideoFile(path=path)  # content type unset, name is not an episode
    env = rs.RedactEnv(setting=lambda s, k, d="": d, background=lambda fn, *a, **k: fn(*a, **k))
    rs.ConvertStep().execute(rs.VideoCtx(vf, env))
    assert seen["is_series"] is False  # it used to be True: everything not tagged Movie was a "series"


# --- T4: the Library safety tolerance reaches the rules engine ----------------------------------------------


def test_verify_result_uses_the_given_tolerance():
    src = cp.MediaInfo("a.mkv", 100, 3000.0, [cp.StreamInfo(0, "video", "h264", width=1280, height=720),
                                               cp.StreamInfo(1, "audio", "aac")])
    out = cp.MediaInfo("a.mkv", 100, 3005.0, list(src.streams))
    plan = cp.Plan(container=".mkv", reasons=["x"])
    assert "duration" in cr.verify_result(src, out, plan, cp.ConversionSettings())  # 1.5 s by default
    assert cr.verify_result(src, out, plan, cp.ConversionSettings(), tolerance_seconds=6) == ""
    assert cr.verify_result(src, out, plan, cp.ConversionSettings(), tolerance_seconds=1, tolerance_fraction=0.002) == ""


def test_convert_file_verifies_with_the_runs_tolerance(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "ep.avi"
    src.write_text("A" * 100, encoding="utf-8")
    past = os.path.getmtime(src) - 3600
    os.utime(src, (past, past))
    streams = [cp.StreamInfo(0, "video", "h264", width=640, height=480), cp.StreamInfo(1, "audio", "aac")]
    info = cp.MediaInfo(str(src), 100, 600.0, streams)

    def runner(args, cancel_event=None, timeout=None):
        Path(args[-1]).write_text("B", encoding="utf-8")
        return 0, ""

    def probe(path):
        return cp.MediaInfo(path, 1, 603.0, streams) if ".redact-part" in path else info  # 3 s off

    plan = cp.Plan(container=".mp4", reasons=["legacy"])
    strict = ls.SafeRun(str(lib), "", dry_run=False, trash=lambda p: None, tolerance_seconds=1, tolerance_fraction=0)
    assert not cr.convert_file(strict, str(src), plan, info, cp.ConversionSettings(), runner=runner, probe=probe).converted
    loose = ls.SafeRun(str(lib), "", dry_run=False, trash=lambda p: None, tolerance_seconds=5, tolerance_fraction=0)
    assert cr.convert_file(loose, str(src), plan, info, cp.ConversionSettings(), runner=runner, probe=probe).converted


# --- T6: Conversion and Library safety obey Cancel ------------------------------------------------------------


def test_safety_page_writes_only_on_ok_or_apply(monkeypatch):
    from gui import video_preferences as vp

    stored = ls.SafetySettings(delete_after_conversion=False)
    writes = []
    monkeypatch.setattr(vp.library_safety, "get_safety_settings", lambda get=None: stored)
    monkeypatch.setattr(vp.library_safety, "set_safety_settings", lambda s, put=None: writes.append(s))
    monkeypatch.setattr(vp.conversion_plan, "set_conversion_settings", lambda s, put=None: None)

    dialog = vp.VideoPreferencesDialog()
    dialog.safety_page.delete_after.setChecked(True)  # the dangerous one
    dialog.safety_page.tol_seconds.setValue(9)
    assert writes == []  # nothing saved by the click itself
    dialog.reject()  # Cancel
    assert writes == []

    dialog = vp.VideoPreferencesDialog()
    dialog.safety_page.delete_after.setChecked(True)
    dialog.apply()
    assert len(writes) == 1 and writes[0].delete_after_conversion is True


# --- T3: texts name the real place --------------------------------------------------------------------------


def test_no_text_points_to_tools_external_tools_for_the_h264_defaults():
    import gui.main_window as mw
    from core.preferences_backend import build_sections

    source = Path(mw.__file__).read_text(encoding="utf-8")
    assert "Adjust these defaults via \"\n" not in source
    assert "Tools > Preferences > Transcode" in source
    transcode = next(s for s in build_sections() if s.key == "transcode")
    assert "Conversion page" in transcode.description


def test_conversion_page_says_what_the_transcode_page_is_for():
    from PyQt6.QtWidgets import QLabel
    from gui.video_preferences import ConversionPage, SafetyPage

    conversion, safety = ConversionPage(), SafetyPage()  # kept alive while their labels are read
    texts = " ".join(label.text() for label in conversion.findChildren(QLabel))
    assert "Transcode page" in texts and "Convert to MP4 (H.264)" in texts
    assert "Conversion page" in " ".join(label.text() for label in safety.findChildren(QLabel))


# --- T7: silent no-ops say why ------------------------------------------------------------------------------


def test_f2_with_no_or_several_rows_selected_says_why(window, tmp_path):
    window.rename_selected_file()
    assert "select" in window.status_bar.currentMessage().lower()
    window.video_files = [VideoFile(path=tmp_path / "a.mp4"), VideoFile(path=tmp_path / "b.mp4")]
    window._refresh_table_rows()
    window.table.selectAll()
    window.rename_selected_file()
    assert "2 are selected" in window.status_bar.currentMessage()


def test_apply_and_refresh_with_nothing_to_do_say_why(window):
    window.actions_by_key["refresh_list"].trigger()
    assert "list is empty" in window.status_bar.currentMessage()
    window._on_apply_menu()
    assert "Nothing to apply" in window.status_bar.currentMessage()


# --- P1: re-reading results is under a progress dialog ------------------------------------------------------


def test_h264_convert_reloads_results_under_progress(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    files = []
    for name in ("a.avi", "b.avi"):
        (tmp_path / name).write_bytes(b"")
        files.append(VideoFile(path=tmp_path / name))
    window.video_files = files
    window._refresh_table_rows()
    window.table.selectAll()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(window, "_run_transcode_jobs", lambda jobs, settings, title: {i: (True, "") for i in range(len(jobs))})
    monkeypatch.setattr(mw.VideoFile, "load", lambda self: None)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: QMessageBox.StandardButton.Ok)  # the end summary
    calls = _record_progress(monkeypatch)
    window._on_convert_to_mp4()
    assert ("Reading the converted files...", 2, False) in calls
    assert sorted(vf.path.name for vf in window.video_files) == ["a.avi", "a.mp4", "b.avi", "b.mp4"]


# --- P2: lookups and rename loops show progress -------------------------------------------------------------


def test_run_lookup_goes_through_run_busy(monkeypatch):
    import gui.lookup as lookup

    seen = {}

    def fake_busy(parent, label, fn, *args, **kwargs):
        seen.update(parent=parent, label=label)
        return fn(*args, **kwargs)

    monkeypatch.setattr(lookup, "run_busy", fake_busy)
    owner = QWidget()
    assert lookup.run_lookup(owner, lambda x, year=None: (x, year), 1, year=2) == (1, 2)
    assert seen["parent"] is owner and seen["label"] == lookup.LOOKUP_LABEL


def test_search_replace_filename_renames_under_progress(window, monkeypatch, tmp_path):
    import gui.main_window as mw

    files = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_bytes(b"")
        files.append(VideoFile(path=tmp_path / name))
    window.video_files = files
    window._refresh_table_rows()

    class FakeDialog:
        DialogCode = mw.SearchReplaceDialog.DialogCode

        def __init__(self, *a, **k):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def result_field_key(self):
            return mw.FILENAME_FIELD_KEY

        def accepted_changes(self):
            return {0: "x1", 1: "x2", 2: "x3"}

    monkeypatch.setattr(mw, "SearchReplaceDialog", FakeDialog)
    calls = _record_progress(monkeypatch)
    window._on_search_replace()
    assert ("Renaming files...", 3, True) in calls
    assert sorted(p.name for p in tmp_path.iterdir() if p.suffix == ".mp4") == ["x1.mp4", "x2.mp4", "x3.mp4"]
