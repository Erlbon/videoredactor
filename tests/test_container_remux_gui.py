"""Media > Check Files with a wrong-container file, in the real MainWindow (offscreen only): the status column and
tooltip, the results dialog's 'Remux to Real Container' button, the confirmation (file list, 'Dry run (report only)'
ticked by default), a dry run that changes nothing, then the real remux with the original quarantined."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402
from PyQt6.QtWidgets import QCheckBox, QDialog, QMessageBox  # noqa: E402

from core import container_remux  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from gui import file_check_dialog as flow  # noqa: E402
from tests.test_file_check import qapp  # noqa: E402,F401

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


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


def _format_name(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", str(path)],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_check_files_offers_and_runs_the_remux(window, tmp_path, monkeypatch):
    folder = tmp_path / "lib"
    folder.mkdir()
    path = folder / "23496_01_1080p.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", "-f", "flv", str(path)], check=True, capture_output=True)
    old = time.time() - 3600
    os.utime(path, (old, old))
    vf = VideoFile(path=path)
    vf.load()  # an FLV is no MP4: it may well fail to load, and must still be checkable
    window.video_files = [vf]
    window._refresh_table_rows()

    shown = {}
    clicks = []
    state = {"real": False}

    def results_exec(self):
        shown["status"] = self.table.item(0, 1).text()
        shown["finding"] = self.table.item(0, 2).text()
        shown["tooltip"] = self.table.item(0, 2).toolTip()
        shown["button"] = self.remux_button.text() if self.remux_button else None
        shown["repairable"] = list(self.repairable)
        self.remux_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(flow.FileCheckResultsDialog, "exec", results_exec)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.setdefault("warning", a))
    monkeypatch.setattr(flow, "ask_check_options", lambda w, n: False)

    def box_exec(self):
        dry = self.checkBox()
        clicks.append((self.windowTitle(), self.text(), dry.isChecked() if isinstance(dry, QCheckBox) else None))
        if self.windowTitle() == flow.TITLE and isinstance(dry, QCheckBox):
            if state["real"]:  # the second run: really do it
                dry.setChecked(False)
            for button in self.buttons():
                if button.text() == "Remux":
                    button.click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", box_exec)

    # first run: the confirmation's dry-run box is ticked by default and is left ticked
    flow.run_check_and_repair(window, [vf], lambda lines: "\n".join(lines), deep=False)
    assert shown["status"] == "WRONG CONTAINER" and "really FLV" in shown["finding"] and "FLV" in shown["tooltip"]
    assert shown["button"] == f"{flow.REMUX_BUTTON} (1)..." and shown["repairable"] == []
    confirm = clicks[0]
    assert confirm[2] is True and str(path) in confirm[1] and "never deleted" in confirm[1]
    assert _format_name(path) == "flv" and sorted(p.name for p in folder.iterdir()) == [path.name]  # dry run: nothing written
    assert clicks[1][0] == "Dry run" and window.status_bar.currentMessage().startswith("Dry run:")

    # second run: dry run unticked, really remuxed
    vf.check = None
    clicks.clear()
    state["real"] = True
    flow.run_check_and_repair(window, [vf], lambda lines: "\n".join(lines), deep=False)
    assert "mp4" in _format_name(path)
    quarantine = [p for p in folder.iterdir() if p.is_dir()]
    assert len(quarantine) == 1 and quarantine[0].name.startswith("remuxed originals ")
    assert [p.name for p in quarantine[0].iterdir() if p.is_file() and p.suffix == ".mp4"] == [path.name]
    assert _format_name(quarantine[0] / path.name) == "flv"
    assert [r for r in container_remux.RemuxLog(str(next(quarantine[0].glob("remux log *.tsv")))).rows()][0]["result"] == "remuxed"
