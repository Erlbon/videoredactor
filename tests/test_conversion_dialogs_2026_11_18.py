"""The Convert dialog's per-run choices, the dry-run table and Preferences > Conversion's new fields (decisions of
2026-11-18), built offscreen."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import convert_modes as cm  # noqa: E402
from core import convert_preview as pv  # noqa: E402
from core.library_safety import SafetySettings  # noqa: E402

app = QApplication.instance() or QApplication([])


def test_convert_dialog_options_default_off_and_follow_the_mode():
    from gui.convert_dialog import ConvertDialog

    dialog = ConvertDialog(["D:/a.mkv"], safety=SafetySettings())
    assert dialog.options == cp.RunOptions()
    dialog._mv_1080.setChecked(True)
    dialog._lenient.setChecked(True)
    assert dialog.options == cp.RunOptions(lenient=True, music_video_1080=True)
    dialog._buttons[cm.MODE_REMUX].setChecked(True)
    assert not dialog._mv_1080.isEnabled() and dialog.options == cp.RunOptions(lenient=True)


def test_dry_run_table_ticks_only_files_that_change():
    from gui.convert_preview_dialog import ConvertPreviewDialog

    rows = [
        pv.PreviewRow("D:/a.mkv", pv.ROUTE_SHRINK, "1080p", "h264", 7.9, "\u2192 720p HEVC, .mkv", True),
        pv.PreviewRow("D:/b.mp4", pv.ROUTE_SKIP, "1080p", "hevc", 3.1, "movies are exempt", False),
        pv.PreviewRow("D:/c.avi", pv.ROUTE_REMUX, "576p", "mpeg4", 1.6, "\u2192 .mp4, copy", True),
    ]
    dialog = ConvertPreviewDialog(rows, "DRY RUN", "report")
    assert dialog._checked() == ["D:/a.mkv", "D:/c.avi"]
    assert dialog.convert_button.text() == "Convert the ticked files (2)"
    dialog.only_changes.setChecked(True)
    hidden = [dialog.table.item(r, 0).text() for r in range(3) if dialog.table.isRowHidden(r)]
    assert hidden == ["b.mp4"]
    first = next(r for r in range(3) if dialog.table.item(r, 0).text() == "a.mkv")
    dialog.table.item(first, 0).setCheckState(Qt.CheckState.Unchecked)
    dialog._convert()
    assert dialog.ticked_paths == ["D:/c.avi"]


def test_preferences_page_saves_the_new_fields(monkeypatch):
    from gui import video_preferences as vp

    stored = {}
    monkeypatch.setattr(vp.conversion_plan, "get_conversion_settings", lambda *a, **k: cp.ConversionSettings())
    monkeypatch.setattr(vp.conversion_plan, "set_conversion_settings", lambda s, *a, **k: stored.update(s=s))
    monkeypatch.setattr(vp.library_safety, "save_policy", lambda *a, **k: None)
    monkeypatch.setattr(vp.bitrate_bands, "set_band_settings", lambda *a, **k: None)
    page = vp.ConversionPage()
    page.x265_preset.setCurrentIndex(page.x265_preset.findData("slow"))
    page.encode_jobs.setValue(3)
    page.max_height_movie.setCurrentIndex(page.max_height_movie.findData(1080))
    page.mv_h264.setValue(20.0)
    page.save()
    s = stored["s"]
    assert (s.x265_preset, s.encode_jobs, s.remux_jobs, s.max_height_movie, s.max_height_other) == ("slow", 3, 4, 1080, 0)
    assert s.mv_4k_mbps_h264 == 20.0 and s.mv_4k_mbps_hevc == 35.0
