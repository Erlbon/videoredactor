"""Set Track Language in the real window and its dialog (offscreen only, real MP4 files): the combined multi-file view,
the one-click 'set all unlabelled', the preview line, apply with a failing file in the batch, the Check Files note
going away, pending edits staying pending, the greyed entry for an unsupported file, and the Check Files button."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil  # noqa: E402
import stat  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QDialog, QMessageBox  # noqa: E402

from core import history, track_language as tl  # noqa: E402
from core.file_check import quick_check  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from gui import file_check_dialog as check_flow  # noqa: E402
from gui import track_language_dialog as flow  # noqa: E402
from tests.test_file_check import qapp  # noqa: E402,F401
from tests.track_language_helpers import make_mp4, probe_languages  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)

NEWLINE = chr(10)


def _details(lines):
    return NEWLINE.join(lines)


@pytest.fixture
def window(monkeypatch, tmp_path, qapp):
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


def _load(window, *paths):
    files = []
    for path in paths:
        vf = VideoFile(path=path)
        vf.load()
        files.append(vf)
    window.video_files = files
    window._refresh_table_rows()
    return files


def _entries(*paths):
    return [(str(p), tl.list_tracks(p)) for p in paths]


def test_combined_view_counts_languages_per_track_position(tmp_path, qapp):
    a = make_mp4(tmp_path / "a.mp4", languages=["eng", None])
    b = make_mp4(tmp_path / "b.mp4")
    c = make_mp4(tmp_path / "c.mp4")
    dialog = flow.TrackLanguageDialog(_entries(a, b, c))
    texts = {dialog.table.item(r, 0).text(): dialog.table.item(r, 1).text() for r in range(dialog.table.rowCount())}
    assert texts["Audio 1 (aac) in 3 files"] == "und x 2, eng x 1"
    assert texts["Audio 2 (aac) in 3 files"] == "und x 3"
    assert dialog.table.columnCount() == 3 and not dialog.apply_button.isEnabled()
    assert "Nothing will change" in dialog.preview.text()


def test_one_click_sets_only_unlabelled_and_the_preview_counts(tmp_path, qapp):
    a = make_mp4(tmp_path / "a.mp4", languages=["eng", None])
    b = make_mp4(tmp_path / "b.mp4")
    dialog = flow.TrackLanguageDialog(_entries(a, b))
    dialog.all_combo.setCurrentIndex(dialog.all_combo.findData("nor"))
    dialog.set_all_unlabelled()
    plan = dialog.plan()
    by_name = {os.path.basename(p): sorted(v.values()) for p, v in plan.items()}
    assert by_name == {"a.mp4": ["nor"], "b.mp4": ["nor", "nor"]}      # a's labelled eng track keeps its language
    assert dialog.preview.text() == "3 track(s) in 2 file(s) will change; nothing else is touched."
    assert dialog.apply_button.isEnabled()


def test_subtitle_checkbox_and_hand_picked_row(tmp_path, qapp):
    from tests.track_language_helpers import make_mkv

    path = make_mkv(tmp_path / "m.mkv", tmp_path)
    dialog = flow.TrackLanguageDialog(_entries(path))
    dialog.all_combo.setCurrentIndex(dialog.all_combo.findData("swe"))
    dialog.set_all_unlabelled()
    assert sorted(dialog.plan()[str(path)].values()) == ["swe", "swe"]               # audio only
    dialog.subtitle_box.setChecked(True)
    dialog.set_all_unlabelled()
    assert sorted(dialog.plan()[str(path)].values()) == ["swe", "swe", "swe"]
    dialog.choose("audio", 2, "")
    assert len(dialog.plan()[str(path)]) == 2


def test_apply_runs_a_batch_with_a_failing_file_and_refreshes_everything(window, tmp_path, monkeypatch):
    good1 = make_mp4(tmp_path / "good1.mp4")
    good2 = make_mp4(tmp_path / "good2.mp4")
    locked = make_mp4(tmp_path / "locked.mp4")
    files = _load(window, good1, good2, locked)
    # a Check Files result exists for good1 (the note), and good1 has a pending tag edit
    files[0].record_check(quick_check(str(good1)), "")
    assert [f for f in files[0].check.findings if f.code == "no_language"]
    files[0].metadata.title = "Pending title"
    files[0].dirty = True
    os.chmod(locked, stat.S_IREAD)
    seen = {}
    warnings = []

    def fake_exec(self):
        self.all_combo.setCurrentIndex(self.all_combo.findData("eng"))
        self.set_all_unlabelled()
        seen["preview"] = self.preview.text()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(flow.TrackLanguageDialog, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a[2]))
    try:
        flow.run_set_track_language(window, files, _details)
    finally:
        os.chmod(locked, stat.S_IREAD | stat.S_IWRITE)
    assert seen["preview"] == "6 track(s) in 3 file(s) will change; nothing else is touched."
    assert probe_languages(good1)["audio"] == ["eng", "eng"] and probe_languages(good2)["audio"] == ["eng", "eng"]
    assert probe_languages(locked)["audio"] == ["und", "und"]
    assert window.status_bar.currentMessage().startswith("Set the language of 4 tracks in 2 files, 1 failed")
    assert len(warnings) == 1 and "locked.mp4" in warnings[0] and "read-only" in warnings[0]
    # the Check Files note is gone for the file that had a result, the pending edit is still pending
    assert not [f for f in files[0].check.findings if f.code == "no_language"]
    assert files[0].metadata.title == "Pending title" and files[0].dirty
    assert files[1].check is None  # never checked: not checked now either


def test_nothing_changes_when_the_dialog_is_cancelled(window, tmp_path, monkeypatch):
    path = make_mp4(tmp_path / "a.mp4")
    files = _load(window, path)
    before = path.read_bytes()
    monkeypatch.setattr(flow.TrackLanguageDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    flow.run_set_track_language(window, files, _details)
    assert path.read_bytes() == before and "cancelled" in window.status_bar.currentMessage()


def test_history_entry_and_restore_through_the_journal(window, tmp_path, monkeypatch, journal):
    path = make_mp4(tmp_path / "a.mp4")
    files = _load(window, path)
    history.set_history_enabled(True)
    try:
        monkeypatch.setattr(flow.TrackLanguageDialog, "exec",
                            lambda self: (self.choose("audio", 1, "fre"), QDialog.DialogCode.Accepted)[1])
        flow.run_set_track_language(window, files, _details)
        entry = journal.entries()[0]
        assert entry.changes == {"track a1 language": ("und", "fre")}
        assert history.apply_restore(str(path), journal.restore_values(entry)[0]) == ""
        assert probe_languages(path)["audio"] == ["und", "und"]
    finally:
        history.set_history_enabled(False)


def test_menu_entry_is_greyed_for_unsupported_files_with_a_tooltip(window, tmp_path):
    mp4 = make_mp4(tmp_path / "a.mp4")
    avi = tmp_path / "x.avi"
    avi.write_bytes(b"RIFF....AVI ")
    files = _load(window, mp4)
    action = window.actions_by_key["set_track_language"]
    window._update_track_language_action([])
    assert not action.isEnabled()
    window._update_track_language_action(files)
    assert action.isEnabled()
    odd = VideoFile(path=avi)
    window._update_track_language_action([odd])
    assert not action.isEnabled() and "MP4 and MKV" in action.toolTip()
    window._update_track_language_action([files[0], odd])
    assert action.isEnabled() and "1 of the 2" in action.toolTip()
    enabled, tip = window._track_language_state([odd])
    assert not enabled and "MP4 and MKV" in tip


def test_check_files_dialog_offers_the_button_for_a_no_language_file(tmp_path, qapp):
    path = make_mp4(tmp_path / "a.mp4")
    vf = VideoFile(path=path)
    vf.load()
    vf.check = quick_check(str(path))
    dialog = check_flow.FileCheckResultsDialog([vf])
    assert dialog.language_button is not None and "Set Track Language" in dialog.language_button.text()
    assert "Metadata > Set Track Language" in dialog.table.item(0, 2).text()
    dialog.language_button.click()
    assert dialog.action == "language" and dialog.language_files == [vf]
    labelled = make_mp4(tmp_path / "b.mp4", languages=["eng", "nor"])
    vf2 = VideoFile(path=labelled)
    vf2.load()
    vf2.check = quick_check(str(labelled))
    assert check_flow.FileCheckResultsDialog([vf2]).language_button is None


def test_check_files_runs_the_language_flow(window, tmp_path, monkeypatch):
    path = make_mp4(tmp_path / "a.mp4")
    files = _load(window, path)
    monkeypatch.setattr(check_flow, "ask_check_options", lambda w, n: False)
    monkeypatch.setattr(check_flow.FileCheckResultsDialog, "exec",
                        lambda self: (self.language_button.click(), QDialog.DialogCode.Accepted)[1])
    monkeypatch.setattr(flow.TrackLanguageDialog, "exec",
                        lambda self: (self.all_combo.setCurrentIndex(self.all_combo.findData("eng")),
                                      self.set_all_unlabelled(), QDialog.DialogCode.Accepted)[2])
    check_flow.run_check_and_repair(window, files, _details, deep=False)
    assert probe_languages(path)["audio"] == ["eng", "eng"]
    assert files[0].check is not None and not [f for f in files[0].check.findings if f.code == "no_language"]


# --- "Play this track" (the shared Player, never started here: QMediaPlayer is checked by hand) --------------------


def test_play_buttons_exist_per_audio_row_for_one_file_only(tmp_path, qapp):
    a = make_mp4(tmp_path / "a.mp4")
    b = make_mp4(tmp_path / "b.mp4")
    single = flow.TrackLanguageDialog(_entries(a), can_play=True)
    assert single.table.columnCount() == 4 and len(single.play_buttons) == 2
    heard = []
    single.play_requested.connect(lambda path, n: heard.append((os.path.basename(path), n)))
    single.play_buttons[1].click()
    single.play_buttons[0].click()
    assert heard == [("a.mp4", 1), ("a.mp4", 0)]
    assert single.play_buttons[0].text() == "Play this track"
    combined = flow.TrackLanguageDialog(_entries(a, b), can_play=False)
    assert combined.table.columnCount() == 3 and combined.play_buttons == []
    assert flow.TrackLanguageDialog(_entries(a)).play_buttons == []  # not offered unless the caller can play


def test_open_player_asks_for_the_track_with_ffprobe_labels(tmp_path, monkeypatch, qapp):
    path = make_mp4(tmp_path / "a.mp4", languages=["eng", None])
    calls = []
    monkeypatch.setattr("redactor_common.gui.media_preview.show_media_preview", lambda *a, **k: calls.append((a, k)))
    flow.open_player("WINDOW", str(path), 1, tl.list_tracks(path))
    (args, kwargs), = calls
    assert args == ("WINDOW", str(path)) and kwargs["initial_audio_track"] == 1 and kwargs["video"] and kwargs["autoplay"]
    assert kwargs["track_labels"] == {0: {"language": "eng", "title": "", "codec": "aac"},
                                      1: {"language": "und", "title": "", "codec": "aac"}}


def test_the_flow_wires_play_and_closes_the_player_before_writing(window, tmp_path, monkeypatch):
    path = make_mp4(tmp_path / "a.mp4")
    files = _load(window, path)
    opened, closed = [], []
    monkeypatch.setattr("redactor_common.gui.media_preview.show_media_preview", lambda *a, **k: opened.append(k["initial_audio_track"]))

    class _Open:
        def close(self):
            closed.append(os.path.getsize(path))

    monkeypatch.setattr("redactor_common.gui.media_preview._open_windows", [_Open()])

    def fake_exec(self):
        assert self.windowModality() == flow.Qt.WindowModality.WindowModal  # the player stays usable meanwhile
        self.play_buttons[1].click()
        self.choose("audio", 2, "ger")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(flow.TrackLanguageDialog, "exec", fake_exec)
    before = path.stat().st_size
    flow.run_set_track_language(window, files, _details)
    assert opened == [1] and closed == [before]
    assert probe_languages(path)["audio"] == ["und", "ger"]
