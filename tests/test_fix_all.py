"""Check Files > Fix All: one click runs every step that applies to the checked files, in order (remux, cut, repair,
then the track language), from each file's state at that moment (offscreen, real MP4 files)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QMessageBox  # noqa: E402

from core.file_check import quick_check  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from gui import file_check_dialog as flow  # noqa: E402
from tests.test_file_check import qapp  # noqa: E402,F401
from tests.test_track_language_gui import window  # noqa: E402,F401
from tests.track_language_helpers import make_mp4  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


def _checked(window, path):
    vf = VideoFile(path=path)
    vf.load()
    vf.check = quick_check(str(path))
    window.video_files = [vf]
    window._refresh_table_rows()
    return vf


def _faststart_off(tmp_path):
    """An MP4 with its index (moov) at the END and audio without a language: needs a repair AND a language."""
    return make_mp4(tmp_path / "late.mp4", languages=[None, None])


def test_the_dialog_offers_fix_all_only_when_two_kinds_of_fix_apply(window, tmp_path):
    vf = _checked(window, _faststart_off(tmp_path))
    fixes = flow.eligible_fixes([vf])
    both = bool(fixes["repair"]) and bool(fixes["language"])
    dialog = flow.FileCheckResultsDialog([vf], window)
    assert (dialog.fix_all_button is not None) == both
    if not both:
        pytest.skip("this ffmpeg wrote a file that is not flagged for both fixes")


def test_fix_all_repairs_then_asks_for_the_language_of_the_repaired_file(window, tmp_path, monkeypatch):
    vf = _checked(window, _faststart_off(tmp_path))
    fixes = flow.eligible_fixes([vf])
    if not (fixes["repair"] and fixes["language"]):
        pytest.skip("this ffmpeg wrote a file that is not flagged for both fixes")
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    asked = []
    import gui.track_language_dialog as language_dialog
    monkeypatch.setattr(language_dialog, "run_set_track_language", lambda w, files, details: asked.append(list(files)))
    flow._run_fix_all(window, [vf], lambda lines: "\n".join(lines))
    assert not flow.eligible_fixes([vf])["repair"]  # the index is at the front now
    assert asked == [[vf]]  # the language step ran last, for the file as it is after the repair


def test_fix_all_with_nothing_to_do_says_so(window, tmp_path, monkeypatch):
    vf = _checked(window, make_mp4(tmp_path / "ok.mp4", languages=["eng", "eng"]))
    monkeypatch.setattr(flow, "eligible_fixes", lambda files: {step: [] for step in flow.FIX_ORDER})
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append((title, text)))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: pytest.fail("asked although nothing applies"))
    flow._run_fix_all(window, [vf], lambda lines: "\n".join(lines))
    assert shown and shown[0][0] == "Fix All"


def test_a_cut_that_would_lose_over_a_quarter_stays_out_of_fix_all(monkeypatch):
    class Plan:
        heavy = True

    class Check:
        can_remux_container = False
        can_cut = True
        can_repair = False
        findings = []

    class File:
        check = Check()
        path = __import__("pathlib").Path("x.mp4")

    monkeypatch.setattr(flow, "plan_cut", lambda check: Plan())
    fixes, notes = flow._fix_all_plan([File()])
    assert fixes["cut"] == [] and notes and "25%" in notes[0]
