"""pytest used to hang at exit, never printing its summary, when two
tests using the `window` fixture failed after selecting a row. Selecting
starts the thumbnail preview; the failed test's traceback keeps its
window alive, the next test's event processing starts that window's
debounced ffmpeg load, and pytest's final gc deleted the window while
the load ran -- deadlocking redactor_common's AsyncPreviewLoader
(fixed there 2026-09-29-04). The same deadlock hung the real app on
quit while a thumbnail was loading. Replayed in a child pytest, since
the failure is a hang."""

import os
import subprocess
import sys
from pathlib import Path

_TWO_FAILING_TESTS = '''
import time
from core.video_file import VideoFile
from tests.test_batch_operations_and_undo import _app, window  # noqa: F401


def _slow_thumbnail(self, force_regenerate=False):
    time.sleep(0.5)  # still running when pytest's final gc deletes the window
    return None


def _select_first_row(window, monkeypatch):
    monkeypatch.setattr(VideoFile, "get_thumbnail", _slow_thumbnail)
    window.table.selectRow(0)
    _app.processEvents()
    window._preview_loader.wait_for_done(0)  # start the load now, not after the debounce


def test_one(window, monkeypatch):
    _select_first_row(window, monkeypatch)
    assert window is None


def test_two(window, monkeypatch):
    _select_first_row(window, monkeypatch)
    assert window is None
'''


def test_two_failing_window_tests_still_report(tmp_path):
    repo = Path(__file__).resolve().parent.parent
    probe = tmp_path / "test_probe.py"
    probe.write_text(_TWO_FAILING_TESTS, encoding="utf-8")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(repo), env.get("PYTHONPATH")) if p)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider",
         "--rootdir", str(tmp_path)],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60,
    )
    assert "2 failed" in result.stdout, result.stdout + result.stderr
    assert result.returncode == 1
