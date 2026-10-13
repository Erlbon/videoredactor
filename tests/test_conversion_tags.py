"""The app's tags survive a conversion: ffmpeg renames them on the way (an MP4 'show' becomes 'SHOW', not the
app's 'SHOW_TITLE'), so conversion_run.process_file reads them before the swap and writes them onto the new file."""

import os
import time
from pathlib import Path

import pytest

from core import conversion_plan as cp
from core import conversion_run as cr
from core import library_safety as ls
from core.video_metadata import ContentType, VideoMetadata


def _old(path, text="x"):
    path.write_text(text, encoding="utf-8")
    past = time.time() - 3600
    os.utime(path, (past, past))
    return str(path)


def _info(path, subs=0):
    streams = [cp.StreamInfo(0, "video", "h264", width=640, height=480), cp.StreamInfo(1, "audio", "aac")]
    streams += [cp.StreamInfo(2 + i, "subtitle", "subrip") for i in range(subs)]
    return cp.MediaInfo(path, 100, 100.0, streams)


@pytest.fixture
def job(tmp_path):
    """An MP4 with an English .srt beside it: the rules turn it into an MKV."""
    lib = tmp_path / "lib"
    lib.mkdir()
    src = _old(lib / "Show.S01E01.mp4", "V" * 100)
    _old(lib / "Show.S01E01.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")

    def runner(args, cancel_event=None, timeout=None):
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("MKV")
        return 0, ""

    def probe(path):
        return _info(path, subs=1) if ".part" in path else _info(path)

    def make_run(dry_run=False):
        return ls.SafeRun(library_root=str(lib), quarantine_root="", dry_run=dry_run, trash=lambda p: None, probe=probe)

    return src, runner, probe, make_run


def _tags():
    return VideoMetadata(title="Pilot", show_title="Slow Horses", season_number=1, episode_number=1, content_type=ContentType.TV)


def test_tags_read_before_the_swap_are_written_onto_the_converted_file(job, monkeypatch):
    src, runner, probe, make_run = job
    order = []
    monkeypatch.setattr(cr, "read_source_tags", lambda path: order.append(("read", os.path.exists(path))) or _tags())
    monkeypatch.setattr(cr, "write_tags", lambda final, md: order.append(("write", final, md.show_title)) or "")
    out = cr.process_file(make_run(), src, cp.ConversionSettings(), probe=probe, runner=runner)
    assert out.converted and out.final_path.endswith("Show.S01E01.mkv")
    assert order == [("read", True), ("write", out.final_path, "Slow Horses")]  # read while the source still existed
    assert not out.notes


def test_a_tag_problem_is_reported_but_the_conversion_stands(job, monkeypatch):
    src, runner, probe, make_run = job
    monkeypatch.setattr(cr, "read_source_tags", lambda path: _tags())
    monkeypatch.setattr(cr, "write_tags", lambda final, md: "MKVToolNix (mkvpropedit) not found")
    out = cr.process_file(make_run(), src, cp.ConversionSettings(), probe=probe, runner=runner)
    assert out.converted and os.path.exists(out.final_path)
    assert any("tags could not be rewritten" in n and "mkvpropedit" in n for n in out.notes)


def test_tags_handed_in_are_used_and_the_disk_is_not_read(job, monkeypatch):
    src, runner, probe, make_run = job
    monkeypatch.setattr(cr, "read_source_tags", lambda path: pytest.fail("a loaded row hands its tags over"))
    written = []
    monkeypatch.setattr(cr, "write_tags", lambda final, md: written.append(md.title) or "")
    cr.process_file(make_run(), src, cp.ConversionSettings(), probe=probe, runner=runner, tags=_tags())
    assert written == ["Pilot"]


def test_redact_step_mode_and_dry_run_touch_no_tags(job, monkeypatch):
    src, runner, probe, make_run = job
    monkeypatch.setattr(cr, "read_source_tags", lambda path: pytest.fail("not read"))
    monkeypatch.setattr(cr, "write_tags", lambda final, md: pytest.fail("not written"))
    cr.process_file(make_run(), src, cp.ConversionSettings(), probe=probe, runner=runner, carry_tags=False)
    assert os.path.exists(os.path.splitext(src)[0] + ".mkv")
    # a second file, dry run: nothing is read or written either
    lib = Path(src).parent
    src2 = _old(lib / "Other.S01E02.mp4", "V" * 100)
    _old(lib / "Other.S01E02.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    out = cr.process_file(make_run(dry_run=True), src2, cp.ConversionSettings(), probe=probe, runner=runner)
    assert out.converted and os.path.exists(src2)


def test_read_source_tags_skips_other_containers_and_clears_the_scan_stamp(tmp_path, monkeypatch):
    avi = tmp_path / "a.avi"
    avi.write_text("x")
    assert cr.read_source_tags(str(avi)) is None  # an AVI carries none of the app's tags

    class FakeVideoFile:
        def __init__(self, path, metadata=None):
            self.path, self.metadata, self.load_error, self.save_error = path, metadata, "", ""

        def load(self):
            self.metadata = _tags()
            self.metadata.scan_stamp = "OK;2026-10-01;abc"

        def save(self):
            pass

    monkeypatch.setattr(cr, "VideoFile", FakeVideoFile)
    tags = cr.read_source_tags(str(tmp_path / "a.mp4"))
    assert tags.show_title == "Slow Horses" and tags.scan_stamp == ""  # the stamp described the old file's bytes
    assert cr.write_tags("x.mkv", tags) == ""


def test_write_tags_reports_the_writers_error(monkeypatch):
    class BrokenVideoFile:
        def __init__(self, path, metadata=None):
            self.save_error = ""

        def save(self):
            self.save_error = "tags did not stick"

    monkeypatch.setattr(cr, "VideoFile", BrokenVideoFile)
    assert cr.write_tags("x.mkv", _tags()) == "tags did not stick"
