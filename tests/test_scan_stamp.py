"""Check Files scan stamps kept inside the file (MKV global tag / MP4
freeform atom REDACTOR_CHECK), the stream-level video fingerprint they
carry, and how the Status column shows them. Clips are generated with
ffmpeg (skipped when it isn't installed); MKVToolNix is simulated the
way the other tests do."""

import os
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.scan_stamp import make_stamp, parse_stamp  # noqa: E402

from core import mkv_backend as mkv  # noqa: E402
from core.file_check import CheckResult, Finding, NOTE  # noqa: E402
from core.mp4_backend import read_mp4_metadata, write_mp4_metadata  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_fingerprint import video_fingerprint  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


def _clip(dest: Path, source="testsrc=duration=4:size=160x120:rate=10", extra=()):
    _ffmpeg("-f", "lavfi", "-i", source, "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", *extra, str(dest))
    return dest


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("stamp")
    _clip(d / "a.mp4")
    _ffmpeg("-i", str(d / "a.mp4"), "-c", "copy", str(d / "a.mkv"))
    return d


# --- MKV: through the mkv backend with a simulated MKVToolNix -----------------

FOREIGN = "<Tags><Tag><Simple><Name>ENCODER_SETTINGS</Name><String>x</String></Simple></Tag></Tags>"


def _mkv_tool(state):
    """A tiny in-memory mkvpropedit/mkvextract/mkvmerge."""
    def fake_run(args, **kwargs):
        exe = args[0]
        if "mkvpropedit" in exe:
            target = next((a for a in args if a.startswith("global:")), None)
            if target is not None:
                path = target.split(":", 1)[1]
                state["xml"] = open(path, encoding="utf-8").read() if path else ""
            return subprocess.CompletedProcess(args, 0, "", "")
        if "mkvmerge" in exe:
            return subprocess.CompletedProcess(args, 0, '{"container": {"properties": {}}}', "")
        if "mkvextract" in exe:
            with open(args[-1], "w", encoding="utf-8") as handle:
                handle.write(state["xml"])
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "not found")
    return fake_run


def test_mkv_stamp_round_trips_as_redactor_check_and_keeps_foreign_tags():
    stamp = make_stamp("CHECKED OK", "v1-abc").to_text()
    state = {"xml": FOREIGN}
    with mock.patch("subprocess.run", side_effect=_mkv_tool(state)):
        done = mkv.write_mkv_metadata("/fake/a.mkv", VideoMetadata(scan_stamp=stamp))
        assert done.returncode == 0
        meta = mkv.read_mkv_metadata("/fake/a.mkv")
    names = mkv.parse_tags_xml(state["xml"])
    assert names["REDACTOR_CHECK"] == stamp
    assert names["ENCODER_SETTINGS"] == "x"  # another tool's tag survives
    assert meta.scan_stamp == stamp
    assert parse_stamp(meta.scan_stamp).fingerprint == "v1-abc"


def test_mkv_garbled_stamp_reads_as_no_stamp():
    xml = "<Tags><Tag><Simple><Name>REDACTOR_CHECK</Name><String>not a stamp</String></Simple></Tag></Tags>"
    with mock.patch("subprocess.run", side_effect=_mkv_tool({"xml": xml})):
        meta = mkv.read_mkv_metadata("/fake/a.mkv")
    vf = VideoFile(path=Path("/fake/a.mkv"), metadata=meta)
    assert vf.stamp is None and vf.stamp_text() == "" and vf.scan_status() == ""


# --- MP4: real mutagen --------------------------------------------------------

@needs_ffmpeg
def test_mp4_stamp_round_trips_and_garbage_is_ignored(clips, tmp_path):
    copy = tmp_path / "s.mp4"
    copy.write_bytes((clips / "a.mp4").read_bytes())
    stamp = make_stamp("DAMAGED", "v1-abc").to_text()
    write_mp4_metadata(str(copy), VideoMetadata(title="T", scan_stamp=stamp))
    meta = read_mp4_metadata(str(copy))
    assert meta.scan_stamp == stamp and meta.title == "T"

    write_mp4_metadata(str(copy), VideoMetadata(title="T", scan_stamp="garbage;;;"))
    assert parse_stamp(read_mp4_metadata(str(copy)).scan_stamp) is None

    write_mp4_metadata(str(copy), VideoMetadata(title="T"))  # empty = removed
    assert read_mp4_metadata(str(copy)).scan_stamp == ""


@needs_ffmpeg
def test_mp4_save_verifies_the_stamp_and_clears_dirty(clips, tmp_path):
    vf = VideoFile(path=tmp_path / "v.mp4")
    vf.path.write_bytes((clips / "a.mp4").read_bytes())
    vf.load()
    assert vf.record_check(CheckResult(), video_fingerprint(str(vf.path)))
    assert vf.dirty
    vf.save()
    assert vf.save_error == "" and not vf.dirty

    again = VideoFile(path=vf.path)
    again.load()
    assert not again.dirty
    assert again.stamp.status == "CHECKED OK"
    assert again.stamp_stale is False
    assert again.stamp_text().startswith("CHECKED OK · ")
    assert "changed since" not in again.stamp_text()


# --- Fingerprint --------------------------------------------------------------

@needs_ffmpeg
def test_fingerprint_is_stable_across_tag_writes_and_copy_remux(clips, tmp_path):
    original = video_fingerprint(str(clips / "a.mp4"))
    assert original.startswith("v1-") and ";" not in original

    tagged = tmp_path / "tagged.mp4"
    tagged.write_bytes((clips / "a.mp4").read_bytes())
    write_mp4_metadata(str(tagged), VideoMetadata(title="New title", comment="x" * 500, scan_stamp="OK;2026-01-01T00:00:00Z"))
    assert tagged.read_bytes() != (clips / "a.mp4").read_bytes()  # the bytes did change
    assert video_fingerprint(str(tagged)) == original

    remuxed = tmp_path / "remux.mkv"
    _ffmpeg("-i", str(clips / "a.mp4"), "-map", "0", "-c", "copy", "-metadata", "title=other", str(remuxed))
    assert video_fingerprint(str(remuxed)) == video_fingerprint(str(clips / "a.mkv")) == original


@needs_ffmpeg
def test_fingerprint_changes_on_truncation_and_reencode(clips, tmp_path):
    original = video_fingerprint(str(clips / "a.mp4"))
    cut = tmp_path / "cut.mp4"
    _ffmpeg("-i", str(clips / "a.mp4"), "-c", "copy", "-t", "2", str(cut))
    reencoded = tmp_path / "re.mp4"
    _ffmpeg("-i", str(clips / "a.mp4"), "-c:v", "libx264", "-crf", "35", "-preset", "ultrafast", "-c:a", "copy", str(reencoded))
    scaled = tmp_path / "scaled.mp4"
    _ffmpeg("-i", str(clips / "a.mp4"), "-vf", "scale=80:60", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(scaled))
    silent = tmp_path / "silent.mp4"
    _ffmpeg("-i", str(clips / "a.mp4"), "-an", "-c", "copy", str(silent))
    prints = {video_fingerprint(str(p)) for p in (cut, reencoded, scaled, silent)}
    assert original not in prints and "" not in prints and len(prints) == 4


def test_fingerprint_of_an_unreadable_file_is_empty(tmp_path):
    (tmp_path / "junk.mp4").write_bytes(b"not a video")
    assert video_fingerprint(str(tmp_path / "junk.mp4")) == ""
    assert video_fingerprint(str(tmp_path / "missing.mp4")) == ""


# --- Stamp state and display --------------------------------------------------

def test_record_check_stamps_and_marks_dirty_but_not_for_a_missing_tool_or_load_error():
    vf = VideoFile(path=Path("/fake/a.mp4"))
    assert vf.record_check(CheckResult(), "v1-abc")
    assert vf.dirty and vf.stamp.status == "CHECKED OK" and vf.stamp.fingerprint == "v1-abc"

    missing = CheckResult(openable=False, findings=[Finding("no_tool", NOTE, "Not checked: ffprobe isn't available")])
    clean = VideoFile(path=Path("/fake/b.mp4"))
    assert not clean.record_check(missing)
    assert not clean.dirty and clean.metadata.scan_stamp == "" and clean.check is missing

    broken = VideoFile(path=Path("/fake/c.mp4"), load_error="Could not read metadata")
    assert not broken.record_check(CheckResult())
    assert not broken.dirty and broken.metadata.scan_stamp == ""


def _window(monkeypatch, tmp_path):
    import core.config as config
    import gui.main_window as mw

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    return mw.MainWindow()


def test_status_column_shows_the_stamp_and_flags_a_stale_one(monkeypatch, tmp_path):
    w = _window(monkeypatch, tmp_path)
    vf = VideoFile(path=tmp_path / "a.mp4")
    assert w._display_value(vf, "status") == "OK"  # never scanned

    vf.metadata.scan_stamp = make_stamp("CHECKED OK", "v1-abc").to_text()
    vf.stamp_stale = False
    shown = w._display_value(vf, "status")
    assert shown == vf.stamp.display() and shown.startswith("CHECKED OK · ")

    vf.stamp_stale = True
    assert w._display_value(vf, "status") == f"{vf.stamp.display()} (changed since)"
    assert vf.scan_status() == ""  # a stale stamp is never treated as a current result

    vf.stamp_stale = None
    assert w._display_value(vf, "status").endswith("(unverified)")

    vf.metadata.scan_stamp = make_stamp("DAMAGED", "v1-abc").to_text()
    vf.stamp_stale = False
    assert vf.scan_status() == "DAMAGED"
    assert w._row_colors(vf) is not None  # a current DAMAGED stamp colours the row like a fresh check
    vf.stamp_stale = True
    assert w._row_colors(vf) is None

    vf.dirty = True
    vf.check = CheckResult()
    assert w._display_value(vf, "status") == "UNSAVED · CHECKED OK"
    vf.dirty = False
    w.close()


def test_undo_keeps_a_stamp_recorded_after_the_edit(monkeypatch, tmp_path):
    import gui.main_window as mw

    vf = VideoFile(path=tmp_path / "a.mp4", metadata=VideoMetadata(title="before"))
    snapshot = mw.MainWindow._snapshot(vf)
    vf.metadata.title = "after"
    vf.record_check(CheckResult(), "v1-abc")
    stamp = vf.metadata.scan_stamp
    vf.dirty = False
    mw.MainWindow._restore(vf, snapshot)
    assert vf.metadata.title == "before" and vf.metadata.scan_stamp == stamp and vf.dirty


def test_a_scan_alone_does_not_block_repair_but_a_real_edit_does():
    vf = VideoFile(path=Path("/fake/a.mp4"))
    vf.record_check(CheckResult(), "v1-abc")
    assert vf.dirty and vf.stamp_only_dirty
    vf.dirty = True  # some edit marks it dirty the ordinary way
    assert not vf.stamp_only_dirty
    vf.record_check(CheckResult(), "v1-abc")  # a rescan must not hide the edit
    assert vf.dirty and not vf.stamp_only_dirty
