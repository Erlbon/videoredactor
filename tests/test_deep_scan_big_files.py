"""The deep scan counts bytes for its progress bar; a file over 2 GB overflowed QProgressDialog's 32-bit range and the
owner got the 'Something went wrong' box (OverflowError: argument 4 overflowed, 2026-10-17)."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402

from PyQt6.QtWidgets import QApplication  # noqa: E402

import gui.file_check_dialog as fcd  # noqa: E402
from core.video_file import VideoFile  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_a_deep_scan_of_a_four_gigabyte_file_does_not_overflow_the_progress_dialog(monkeypatch, tmp_path):
    video = VideoFile(path=Path(tmp_path / "Big.mp4"))
    monkeypatch.setattr(fcd, "_file_size", lambda vf: 4_500_000_000)  # what the stat of a 4.5 GB file says
    seen = []

    def fake_check(path, deep, on_bytes, cancelled):
        on_bytes(2_250_000_000, 4_500_000_000)
        on_bytes(4_500_000_000, 4_500_000_000)
        seen.append(path)
        raise OSError("stop here: the progress calls above are the point")

    monkeypatch.setattr(fcd, "_check_and_fingerprint", fake_check)
    errors, checked = [], []
    fcd._deep_check_files(None, [video], errors, checked)  # raised OverflowError before the fix
    assert seen and checked == [] and "stop here" in errors[0]
