"""Review fixes of 2026-11-28 (cards 6, 8, 9, 21, 34, 35, 38, 41): Join keeps the parts' chapters and an MKV's
attachments, a failing move of part 2 does not escape, the CLI leaves unmarked files out, the lyrics ffmpeg command
copies old subtitle tracks, a cancel is not a failure, undo logs with CRLF or reused paths, an incomplete set of
parts is noted, and a missing or timed-out mkvmerge gives a message instead of an exception."""

import dataclasses
import json
import os
import shutil
import subprocess

import pytest

import core.config as config
from core import container_remux as cr
from core import join_files as jf
from core import library_safety
from core import lyrics_embed as le
from tests.track_language_helpers import ffmpeg, make_mp4

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture
def safety(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    return dataclasses.replace(library_safety.get_safety_settings(), recent_minutes=0, quarantine_root="")


def _probe_json(path, *flags):
    done = subprocess.run(["ffprobe", "-v", "error", *flags, "-of", "json", str(path)], capture_output=True, text=True,
                          check=True)
    return json.loads(done.stdout)


# -- card 38: incomplete sets ----------------------------------------------------------------------------------------------


def test_a_set_that_does_not_start_at_part_one_is_noted(tmp_path):
    group = jf.group_files([str(tmp_path / "Film cd2.mp4"), str(tmp_path / "Film cd3.mp4")])[0]
    assert "part 1 is missing" in group.note


def test_the_of_n_count_is_kept_and_a_short_set_is_noted(tmp_path):
    short = jf.group_files([str(tmp_path / "Film cd1 of 3.mp4"), str(tmp_path / "Film cd2 of 3.mp4")])[0]
    assert "3 parts" in short.note and "only 2" in short.note
    whole = jf.group_files([str(tmp_path / "Film cd1 of 2.mp4"), str(tmp_path / "Film cd2 of 2.mp4")])[0]
    assert whole.note == ""
    disagree = jf.group_files([str(tmp_path / "Film cd1 of 2.mp4"), str(tmp_path / "Film cd2 of 3.mp4")])[0]
    assert "disagree" in disagree.note
    assert jf.part_total("Film cd1 of 3") == 3 and jf.part_total("Film cd1") is None


# -- card 9: the command line leaves unmarked files out ----------------------------------------------------------------------


def test_cli_join_files_ignores_files_without_a_part_marker_unless_told(tmp_path, monkeypatch, capsys):
    from videocli.main import main

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    for n in (1, 2, 3):
        (tmp_path / f"Show S01E0{n}.mp4").write_bytes(b"x")
    assert jf.group_files([str(p) for p in tmp_path.iterdir()], join_unmarked=False) == []
    assert main(["join-files", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["groups"] == 0
    assert main(["join-files", str(tmp_path), "--join-unmarked", "--json"]) in (0, 1)  # empty files: cannot be joined
    assert json.loads(capsys.readouterr().out)["groups"] == 1


def test_cli_join_files_still_groups_marked_parts_without_the_flag(tmp_path):
    files = [str(tmp_path / "Movie cd1.mp4"), str(tmp_path / "Movie cd2.mp4"), str(tmp_path / "Episode.mp4")]
    groups = jf.group_files(files, join_unmarked=False)
    assert [g.name for g in groups] == ["Movie"] and len(groups[0].parts) == 2


# -- card 21: only the new subtitle track is converted --------------------------------------------------------------


def test_the_lyrics_ffmpeg_command_copies_old_subtitle_tracks_and_converts_only_the_new_one():
    args = le.ffmpeg_args("in.mp4", "l.srt", "t.mp4", "eng", drop=[], kept=2, default=False)
    assert "-c:s" in args and args[args.index("-c:s") + 1] == "copy"
    assert args[args.index("-c:s:2") + 1] == "mov_text"
    assert args.index("-c:s") < args.index("-c:s:2")


@needs_ffmpeg
def test_an_existing_mov_text_track_is_not_re_encoded(tmp_path):
    from tests.sample_clip import SAMPLE

    srt = tmp_path / "other.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    clip = tmp_path / "song.mp4"
    ffmpeg("-i", SAMPLE, "-i", str(srt), "-map", "0", "-map", "1", "-c", "copy", "-c:s", "mov_text", str(clip))
    args = le.ffmpeg_args(str(clip), str(srt), str(tmp_path / "out.mp4"), "eng", drop=[], kept=1, default=False)
    args[args.index("-v") + 1] = "info"
    done = subprocess.run(args, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "Stream mapping:" in done.stderr
    assert "mov_text (native) -> mov_text" not in done.stderr  # the old track is copied; only the .srt is converted


# -- card 41: mkvmerge missing or timed out -------------------------------------------------------------------------------


def test_a_timed_out_or_missing_mkvmerge_gives_a_message_not_an_exception(monkeypatch):
    monkeypatch.setattr(le, "get_executable_path", lambda name: name)

    def timeout(args, timeout=None):
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(le, "run_tool", timeout)
    code, text = le._run_mkvmerge(["mkvmerge", "-o", "t.mkv", "s.mkv"])
    assert code is None and "too long" in text

    def missing(args, timeout=None):
        raise FileNotFoundError("mkvmerge")

    monkeypatch.setattr(le, "run_tool", missing)
    code, text = le._run_mkvmerge(["mkvmerge", "-o", "t.mkv", "s.mkv"])
    assert code is None and "could not be run" in text


def test_embedding_into_an_mkv_reports_a_failed_mkvmerge_and_keeps_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(le, "unsupported_reason", lambda path: "")
    monkeypatch.setattr(le, "get_executable_path", lambda name: name)

    def timeout(args, timeout=None):
        raise subprocess.TimeoutExpired(args, timeout)

    monkeypatch.setattr(le, "run_tool", timeout)
    clip = tmp_path / "song.mkv"
    clip.write_bytes(b"original")
    run = library_safety.SafeRun(library_root=str(tmp_path), quarantine_root="", dry_run=False, recent_minutes=0,
                                 trash=lambda p: None)
    result = le.embed_lyrics_track(str(clip), "1\n00:00:00,000 --> 00:00:01,000\nHi\n", "en", run,
                                   mkv_identify=lambda p: {"tracks": []})
    assert not result.ok and "too long" in result.message
    assert clip.read_bytes() == b"original" and sorted(p.name for p in tmp_path.iterdir()) == ["song.mkv"]


# -- card 35: undo with CRLF and with a reused log -----------------------------------------------------------------------


def _remuxed_row(log, tmp_path, content):
    """One remuxed state on disk: the quarantined original and the new file under the original's name; a log row."""
    orig = tmp_path / "a.mp4"
    quarantined = tmp_path / "q" / "a.mp4"
    quarantined.parent.mkdir(exist_ok=True)
    quarantined.write_text(content, encoding="utf-8")
    orig.write_text("new " + content, encoding="utf-8")
    log.append(str(orig), str(quarantined), str(orig), 3, 4, cr.REMUXED)
    return orig, quarantined


def test_undo_reads_a_log_saved_with_crlf(tmp_path):
    log = cr.RemuxLog(str(tmp_path / "log.tsv"))
    orig, quarantined = _remuxed_row(log, tmp_path, "first")
    text = open(log.path, encoding="utf-8", newline="").read()
    with open(log.path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text.replace("\n", "\r\n"))  # what Notepad does
    assert [r["result"] for r in log.rows()] == ["remuxed"]
    lines, problems = cr.undo_remux_log(log.path)
    assert len(lines) == 1 and problems == [] and orig.read_text(encoding="utf-8") == "first"


def test_a_reused_log_is_undone_again_for_a_second_remux_of_the_same_file(tmp_path):
    log = cr.RemuxLog(str(tmp_path / "log.tsv"))
    orig, quarantined = _remuxed_row(log, tmp_path, "first")
    lines, problems = cr.undo_remux_log(log.path)
    assert len(lines) == 1 and not problems and orig.read_text(encoding="utf-8") == "first"
    # remux again into the same log
    shutil.move(str(orig), str(quarantined))
    orig.write_text("new first", encoding="utf-8")
    log.append(str(orig), str(quarantined), str(orig), 3, 4, cr.REMUXED)
    lines, problems = cr.undo_remux_log(log.path)
    assert len(lines) == 1 and not problems and orig.read_text(encoding="utf-8") == "first"
    assert cr.undo_remux_log(log.path) == ([], [])  # and now nothing is left to undo


# -- card 34: a cancel is not a failure ------------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_cancelled_join_is_skipped_not_failed(tmp_path, safety):
    a = make_mp4(tmp_path / "Movie cd1.mp4", audio_tracks=1)
    b = make_mp4(tmp_path / "Movie cd2.mp4", audio_tracks=1)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    result = jf.join_group(plan, dry_run=False, safety=safety, runner=lambda args, **kw: (None, "Cancelled"))
    assert result.status == jf.SKIPPED and "cancelled" in result.message
    assert a.is_file() and b.is_file() and not (tmp_path / "Movie.mp4").exists()


@needs_ffmpeg
def test_a_cancelled_remux_is_skipped_and_not_logged_as_failed(tmp_path, safety):
    path = tmp_path / "x.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest", "-f", "flv",
           str(path))  # an FLV named .mp4: a wrong container
    old = os.path.getmtime(path) - 3600
    os.utime(path, (old, old))
    log = cr.RemuxLog(str(tmp_path / "logs" / "r.tsv"))
    result = cr.remux_file(str(path), dry_run=False, safety=safety, log=log, library_root="",
                           runner=lambda args, **kw: (None, "Cancelled"))
    assert result.status == cr.SKIPPED and "cancelled" in result.message
    assert log.rows() == [] and path.is_file()


# -- card 6: chapters and attachments --------------------------------------------------------------------------------------


def _chapter_file(tmp_path):
    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=One\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=2000\ntitle=Two\n", encoding="utf-8")
    return meta


def _part(path, tmp_path, with_font):
    args = ["-f", "lavfi", "-i", "testsrc2=size=160x120:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
            "-i", str(_chapter_file(tmp_path)), "-map", "0", "-map", "1", "-map_chapters", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
    if with_font:
        font = tmp_path / "font.ttf"
        font.write_bytes(b"not really a font")
        args += ["-attach", str(font), "-metadata:s:t", "mimetype=application/x-truetype-font"]
    ffmpeg(*args, str(path))


@needs_ffmpeg
@pytest.mark.parametrize("extension, with_font", [(".mkv", True), (".mp4", False)])
def test_the_parts_chapters_are_kept_with_each_parts_time_offset(tmp_path, safety, monkeypatch, extension, with_font):
    parts = []
    for n in (1, 2):
        path = tmp_path / f"Movie cd{n}{extension}"
        _part(path, tmp_path, with_font)
        parts.append(path)
    plan = jf.check_group(jf.group_files([str(p) for p in parts])[0])
    assert plan.compatible, plan.reason
    result = jf.join_group(plan, dry_run=False, safety=safety)
    assert result.status == jf.JOINED, result.message
    joined = tmp_path / f"Movie{extension}"
    chapters = _probe_json(joined, "-show_chapters")["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["One", "Two", "One", "Two"]
    starts = [float(c["start_time"]) for c in chapters]
    assert starts == pytest.approx([0, 1, plan.durations[0], plan.durations[0] + 1], abs=0.25)
    if with_font:
        kinds = [s["codec_type"] for s in _probe_json(joined, "-show_streams")["streams"]]
        assert kinds.count("attachment") == 1


@needs_ffmpeg
def test_one_chapter_per_part_still_replaces_the_parts_own_chapters(tmp_path, safety):
    parts = []
    for n in (1, 2):
        path = tmp_path / f"Movie cd{n}.mp4"
        _part(path, tmp_path, False)
        parts.append(path)
    plan = jf.check_group(jf.group_files([str(p) for p in parts])[0])
    jf.join_group(plan, dry_run=False, chapters=True, safety=safety)
    chapters = _probe_json(tmp_path / "Movie.mp4", "-show_chapters")["chapters"]
    assert [c["tags"]["title"] for c in chapters] == ["Part 1", "Part 2"]


def test_chapter_titles_are_escaped_for_ffmetadata():
    text = jf._chapters_text([(0.0, 1.0, "A=B;C#D")])
    bs = chr(92)
    assert f"title=A{bs}=B{bs};C{bs}#D" in text and "START=0" in text and "END=1000" in text


# -- card 8: a part that cannot be moved --------------------------------------------------------------------------------


@needs_ffmpeg
def test_a_part_that_cannot_be_moved_is_noted_and_part_one_keeps_its_undo_row(tmp_path, safety, monkeypatch):
    a = make_mp4(tmp_path / "Movie cd1.mp4", audio_tracks=1)
    b = make_mp4(tmp_path / "Movie cd2.mp4", audio_tracks=1)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    real_move = shutil.move

    def move(src, dst, *args, **kwargs):
        if "cd2" in os.path.basename(str(src)):
            raise PermissionError("open in a player")
        return real_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(library_safety.shutil, "move", move)
    log = cr.RemuxLog(str(tmp_path / "logs" / "j.tsv"))
    result = jf.join_group(plan, dry_run=False, safety=safety, log=log)
    assert result.status == jf.JOINED and "Movie cd2.mp4 could not be moved" in result.message
    assert (tmp_path / "Movie.mp4").is_file() and b.is_file() and not a.exists()
    rows = log.rows()
    assert [os.path.basename(r["original"]) for r in rows] == ["Movie cd1.mp4"]
    monkeypatch.undo()
    lines, problems = jf.undo_join_log(log.path)
    assert len(lines) == 1 and not problems and a.is_file()
