"""The wrong-container fix, end to end with REAL ffmpeg/ffprobe: a real FLV named .mp4 is flagged by the check, remuxed
losslessly into a real MP4, the original is quarantined (never deleted), a TSV row is written, undo restores it, a fresh
file is skipped, an existing target is never overwritten, a dry run writes nothing."""

import json
import os
import shutil
import subprocess
import time

import pytest

from core import container_check, container_remux, library_safety
from core import conversion_plan as cp
from core.ffmpeg_backend import probe_media
from core.file_check import quick_check
from videocli.main import main

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)

SAFETY = library_safety.SafetySettings(dry_run=False)  # no quarantine folder set: the dated folder beside the file


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


def _sources(seconds=3):
    return ["-f", "lavfi", "-i", f"testsrc2=size=160x120:rate=10:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]


def _format_name(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "default=nw=1:nk=1", path],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _decode_errors(path):
    done = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"], capture_output=True, text=True)
    return done.returncode, done.stderr.strip()


def _old(path, minutes=60):
    past = time.time() - minutes * 60
    os.utime(path, (past, past))
    return path


@pytest.fixture(scope="module")
def flv_bytes(tmp_path_factory):
    folder = tmp_path_factory.mktemp("flv-source")
    path = folder / "source.flv"
    _ffmpeg(*_sources(), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            "-f", "flv", str(path))
    assert _format_name(str(path)) == "flv"
    return path.read_bytes()


def _flv_named(tmp_path, flv_bytes, name="23496_01_1080p.mp4"):
    path = tmp_path / name
    path.write_bytes(flv_bytes)
    return _old(str(path))


def test_the_check_flags_a_real_flv_named_mp4(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes)
    assert _format_name(path) == "flv"
    result = quick_check(path)
    assert result.status == "WRONG CONTAINER" and result.can_remux_container
    assert any("really FLV" in f.message for f in result.findings)
    verdict = container_check.check_container(path)  # the ffprobe path, not only the stubs
    assert verdict.wrong and verdict.label == "FLV" and verdict.source == "ffprobe"


def test_a_real_mp4_is_not_flagged(tmp_path):
    path = tmp_path / "real.mp4"
    _ffmpeg(*_sources(2), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            "-metadata:s:a:0", "language=eng", "-movflags", "+faststart", str(path))
    assert "mp4" in _format_name(str(path))
    _old(str(path))
    result = quick_check(str(path))
    assert result.status == "CHECKED OK"
    assert container_remux.remux_file(str(path), dry_run=False, safety=SAFETY, library_root="").status == container_remux.RIGHT


def test_dry_run_reports_and_writes_nothing(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes)
    before = {p.name: (p.stat().st_size, p.stat().st_mtime) for p in tmp_path.iterdir()}
    results, log = container_remux.remux_files([path], dry_run=True, safety=SAFETY, library_root="")
    assert [r.status for r in results] == [container_remux.WOULD] and log == ""
    assert {p.name: (p.stat().st_size, p.stat().st_mtime) for p in tmp_path.iterdir()} == before
    assert _format_name(path) == "flv"


def test_remux_end_to_end_quarantine_log_and_undo(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes)
    original_size = os.path.getsize(path)
    source_info = probe_media(path)
    log_path = str(tmp_path / "logs" / "remux.tsv")

    results, written = container_remux.remux_files([path], dry_run=False, safety=SAFETY, log_path=log_path, library_root="")
    assert [r.status for r in results] == [container_remux.REMUXED], results[0].message
    assert written == log_path

    # a real MP4 under the same name, content intact, decodes with zero errors
    assert os.path.isfile(path) and "mp4" in _format_name(path)
    assert _decode_errors(path) == (0, "")
    after = probe_media(path)
    assert abs(after.duration - source_info.duration) <= 1.5
    assert len(after.of("video")) == len(source_info.of("video")) and len(after.of("audio")) == len(source_info.of("audio"))
    assert [s.codec for s in after.of("video")] == ["h264"] and [s.codec for s in after.of("audio")] == ["aac"]
    assert quick_check(path).status in ("CHECKED OK", "NOTE")  # NOTE: the FLV audio has no language tag
    assert container_check.check_container(path).wrong is False

    # the original is quarantined, never deleted, and nothing else is left behind
    quarantined = results[0].quarantined
    assert os.path.dirname(quarantined) == str(tmp_path / f"remuxed originals {library_safety.date.today().isoformat()}")
    assert os.path.getsize(quarantined) == original_size and _format_name(quarantined) == "flv"
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["23496_01_1080p.mp4"]

    # one TSV row, UTF-8, with all columns
    rows = container_remux.RemuxLog(log_path).rows()
    assert len(rows) == 1
    row = rows[0]
    assert (row["original"], row["quarantined"], row["new"], row["result"]) == (path, quarantined, path, "remuxed")
    assert int(row["original_bytes"]) == original_size and int(row["new_bytes"]) == os.path.getsize(path)
    text = open(log_path, encoding="utf-8").read().split("\n")
    assert text[0].split("\t") == list(container_remux.LOG_COLUMNS)

    # undo puts the FLV back under its name and keeps the remuxed copy (moved, not deleted)
    lines, problems = container_remux.undo_remux_log(log_path)
    assert problems == [] and len(lines) == 1
    assert _format_name(path) == "flv" and os.path.getsize(path) == original_size
    assert not os.path.exists(quarantined)
    undone = [os.path.join(d, f) for d, _s, fs in os.walk(tmp_path) for f in fs if "undone remux" in d]
    assert len(undone) == 1 and "mp4" in _format_name(undone[0])
    again, more = container_remux.undo_remux_log(log_path)  # undoing twice does nothing
    assert again == [] and more == []


def test_a_fresh_file_is_skipped(tmp_path, flv_bytes):
    path = tmp_path / "fresh.mp4"
    path.write_bytes(flv_bytes)  # modified just now
    result = container_remux.remux_file(str(path), dry_run=False, safety=SAFETY, library_root="")
    assert result.status == container_remux.SKIPPED and "last 10 minutes" in result.message
    assert _format_name(str(path)) == "flv" and [p.name for p in tmp_path.iterdir()] == ["fresh.mp4"]
    assert container_remux.remux_file(str(path), dry_run=True, safety=SAFETY, library_root="").status == container_remux.SKIPPED


def test_an_existing_target_is_never_overwritten(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes, "clip.m4v")  # an .m4v that is really an FLV: the fix is clip.mp4
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"precious")
    result = container_remux.remux_file(path, dry_run=False, safety=SAFETY, library_root="")
    assert result.status == container_remux.SKIPPED and "already exists" in result.message
    assert target.read_bytes() == b"precious" and _format_name(path) == "flv"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["clip.m4v", "clip.mp4"]


def test_a_different_name_target_and_undo(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes, "clip.m4v")
    log_path = str(tmp_path / "remux.tsv")
    results, _ = container_remux.remux_files([path], dry_run=False, safety=SAFETY, log_path=log_path, library_root="")
    assert results[0].status == container_remux.REMUXED, results[0].message
    new = str(tmp_path / "clip.mp4")
    assert results[0].new_path == new and "mp4" in _format_name(new) and not os.path.exists(path)
    lines, problems = container_remux.undo_remux_log(log_path)
    assert problems == [] and _format_name(path) == "flv" and not os.path.exists(new)


def test_undo_refuses_when_the_original_name_is_taken(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes, "clip.m4v")
    log_path = str(tmp_path / "remux.tsv")
    container_remux.remux_files([path], dry_run=False, safety=SAFETY, log_path=log_path, library_root="")
    open(path, "wb").write(b"someone else's file")
    lines, problems = container_remux.undo_remux_log(log_path)
    assert lines == [] and problems and "exists now" in problems[0]
    assert open(path, "rb").read() == b"someone else's file"


def test_a_mkv_that_is_really_mp4_becomes_a_real_mkv(tmp_path):
    real = tmp_path / "src.mp4"
    _ffmpeg(*_sources(2), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(real))
    path = tmp_path / "movie.mkv"
    shutil.move(str(real), str(path))
    _old(str(path))
    assert quick_check(str(path)).status == "WRONG CONTAINER"
    result = container_remux.remux_file(str(path), dry_run=False, safety=SAFETY, library_root="")
    assert result.status == container_remux.REMUXED, result.message
    assert "matroska" in _format_name(str(path)) and _decode_errors(str(path)) == (0, "")


def test_an_avi_in_an_mkv_is_fixed_and_a_reencode_is_refused(tmp_path):
    path = tmp_path / "old.mkv"
    _ffmpeg(*_sources(2), "-c:v", "mpeg4", "-c:a", "libmp3lame", "-shortest", "-f", "avi", str(path))
    _old(str(path))
    assert container_check.check_container(str(path)).label == "AVI"
    assert container_remux.remux_file(str(path), dry_run=False, safety=SAFETY, library_root="").status == container_remux.REMUXED
    assert "matroska" in _format_name(str(path))
    # video no container can hold would need a re-encode: skipped, untouched
    odd = tmp_path / "flash.mp4"
    _ffmpeg(*_sources(2), "-c:v", "flv1", "-c:a", "libmp3lame", "-shortest", "-f", "flv", str(odd))
    _old(str(odd))
    result = container_remux.remux_file(str(odd), dry_run=False, safety=SAFETY, library_root="")
    assert result.status == container_remux.SKIPPED and "re-encode" in result.message
    assert _format_name(str(odd)) == "flv"


def test_a_failed_verification_keeps_the_original(tmp_path, flv_bytes):
    path = _flv_named(tmp_path, flv_bytes)
    result = container_remux.remux_file(
        path, dry_run=False, safety=SAFETY, library_root="", decode_check=lambda p: "the result does not decode cleanly: x",
    )
    assert result.status == container_remux.FAILED and "does not decode" in result.message
    assert _format_name(path) == "flv"
    assert [p.name for p in tmp_path.iterdir()] == ["23496_01_1080p.mp4"]  # no temp file, no quarantine folder content


def test_a_configured_quarantine_folder_is_used(tmp_path, flv_bytes):
    folder = tmp_path / "lib"
    folder.mkdir()
    path = _flv_named(folder, flv_bytes)
    quarantine = tmp_path / "quarantine"
    safety = library_safety.SafetySettings(quarantine_root=str(quarantine), dry_run=False)
    result = container_remux.remux_file(path, dry_run=False, safety=safety, library_root=str(folder))
    assert result.status == container_remux.REMUXED
    assert result.quarantined.startswith(str(quarantine)) and _format_name(result.quarantined) == "flv"


def test_the_planner_path_converts_it_too(tmp_path, flv_bytes):
    """Import & Convert / Redact go through conversion_run.process_file: the same file is converted there."""
    from core import conversion_run

    path = _flv_named(tmp_path, flv_bytes)
    run = library_safety.SafeRun(library_root=str(tmp_path), quarantine_root=str(tmp_path), dry_run=False)
    outcome = conversion_run.process_file(run, path, cp.ConversionSettings(), is_series=False, carry_tags=False)
    assert outcome.converted, outcome.message
    assert "mp4" in _format_name(path)


def test_cli_fix_container_dry_run_apply_and_undo(tmp_path, flv_bytes, capsys):
    path = _flv_named(tmp_path, flv_bytes)
    log_path = str(tmp_path / "cli.tsv")
    assert main(["fix-container", str(tmp_path), "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] and dry["counts"] == {"would remux": 1} and _format_name(path) == "flv"
    assert main(["fix-container", str(tmp_path), "--apply", "--log", log_path, "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["counts"] == {"remuxed": 1} and "mp4" in _format_name(path)
    assert main(["undo-remux", log_path]) == 0
    assert "Undid 1 remux" in capsys.readouterr().out and _format_name(path) == "flv"
