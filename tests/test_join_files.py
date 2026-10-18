"""Join Files (core/join_files.py): grouping by name and part number, the can-join-without-re-encoding check, the lossless
concat with verification, tags and poster from the first part, originals moved (never deleted) with an undo log
(offscreen, real small MP4 files made by ffmpeg)."""

import dataclasses
import json
import os
import shutil
import subprocess

import pytest

import core.config as config
from core import join_files as jf
from core import library_safety
from core.video_file import VideoFile
from tests.track_language_helpers import ffmpeg, make_mp4

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


# -- names and groups (no ffmpeg) -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("stem, base, number", [
    ("Strap Some Boyz 2 cd1", "Strap Some Boyz 2", 1),
    ("Strap Some Boyz 2 - CD 2", "Strap Some Boyz 2", 2),
    ("Title (disc1)", "Title", 1),
    ("Title [part 2]", "Title", 2),
    ("Title.pt3", "Title", 3),
    ("Title cd1 of 2", "Title", 1),
    ("Title", "Title", None),
    ("cd1", "cd1", None),
    ("Discovery", "Discovery", None),
])
def test_split_part_name(stem, base, number):
    assert jf.split_part_name(stem) == (base, number)


def test_parts_are_grouped_by_folder_and_name_and_ordered_by_number(tmp_path):
    a = tmp_path / "Movie cd2.mp4"
    b = tmp_path / "Movie cd1.mp4"
    other = tmp_path / "Other cd1.mp4"
    other2 = tmp_path / "Other cd2.mp4"
    groups = jf.group_files([str(a), str(b), str(other), str(other2)])
    assert [g.name for g in groups] == ["Movie", "Other"]
    assert [os.path.basename(p) for p in groups[0].parts] == ["Movie cd1.mp4", "Movie cd2.mp4"]
    assert groups[0].output_path == str(tmp_path / "Movie.mp4") and not groups[0].note


def test_a_missing_repeated_or_single_part_is_flagged(tmp_path):
    gap = jf.group_files([str(tmp_path / "A cd1.mp4"), str(tmp_path / "A cd3.mp4")])[0]
    assert "part 2 is missing" in gap.note
    repeat = jf.group_files([str(tmp_path / "B cd1.mp4"), str(tmp_path / "B part1.mp4")])[0]
    assert "same part number" in repeat.note
    single = jf.group_files([str(tmp_path / "C cd1.mp4")])[0]
    assert "only one part" in single.note


def test_a_hand_picked_selection_without_markers_is_one_group_in_name_order(tmp_path):
    groups = jf.group_files([str(tmp_path / "Title B.mp4"), str(tmp_path / "Title A.mp4")])
    assert len(groups) == 1 and groups[0].name == "Title"
    assert [os.path.basename(p) for p in groups[0].parts] == ["Title A.mp4", "Title B.mp4"]


def test_the_concat_list_escapes_quotes_and_backslashes():
    assert jf._concat_quote("C:\\a\\it's.mp4") == "file 'C:/a/it'\\''s.mp4'"


# -- real files -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def safety(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    return dataclasses.replace(library_safety.get_safety_settings(), recent_minutes=0, quarantine_root="")


def _duration(path):
    done = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True)
    return float(json.loads(done.stdout)["format"]["duration"])


def _parts(tmp_path, count=2, size="160x120"):
    paths = []
    for n in range(1, count + 1):
        path = tmp_path / f"Movie cd{n}.mp4"
        make_mp4(path, audio_tracks=1)
        paths.append(path)
    return paths


@needs_ffmpeg
def test_two_matching_parts_are_joined_losslessly_and_the_originals_are_kept(tmp_path, safety):
    a, b = _parts(tmp_path)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    assert plan.compatible and plan.total_seconds == pytest.approx(4, abs=0.3) and "h264" in plan.summary
    results, log_path = jf.join_groups([plan.group], dry_run=False, safety=safety, plans=[plan])
    assert [r.status for r in results] == [jf.JOINED]
    joined = tmp_path / "Movie.mp4"
    assert joined.is_file() and _duration(joined) == pytest.approx(4, abs=0.4)
    assert not a.exists() and not b.exists()  # moved, not left behind ...
    kept = [p for p in tmp_path.rglob("*.mp4") if "joined originals" in str(p)]
    assert sorted(p.name for p in kept) == ["Movie cd1.mp4", "Movie cd2.mp4"]  # ... and not deleted
    assert log_path and os.path.isfile(log_path)


@needs_ffmpeg
def test_undo_puts_the_parts_back_and_sets_the_joined_file_aside(tmp_path, safety):
    a, b = _parts(tmp_path)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    _results, log_path = jf.join_groups([plan.group], dry_run=False, safety=safety, plans=[plan])
    lines, problems = jf.undo_join_log(log_path)
    assert not problems and len(lines) == 2
    assert a.is_file() and b.is_file() and not (tmp_path / "Movie.mp4").exists()
    aside = [p for p in tmp_path.rglob("Movie.mp4") if "undone join" in str(p)]
    assert len(aside) == 1  # moved aside, never deleted
    assert jf.undo_join_log(log_path) == ([], [])  # a second undo does nothing


@needs_ffmpeg
def test_a_dry_run_writes_nothing(tmp_path, safety):
    a, b = _parts(tmp_path)
    before = sorted(p.name for p in tmp_path.rglob("*"))
    results, log_path = jf.join_groups(jf.group_files([str(a), str(b)]), dry_run=True, safety=safety)
    assert results[0].status == jf.WOULD and log_path == ""
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


@needs_ffmpeg
def test_parts_that_differ_are_not_joined_and_say_why(tmp_path, safety):
    a = tmp_path / "Movie cd1.mp4"
    b = tmp_path / "Movie cd2.mp4"
    make_mp4(a, audio_tracks=1)
    ffmpeg("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=2", "-f", "lavfi", "-i", "sine=duration=2",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(b))
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    assert not plan.compatible and "differs" in plan.reason and "re-encode" in plan.reason
    result = jf.join_group(plan, dry_run=False, safety=safety)
    assert result.status == jf.SKIPPED and a.is_file() and b.is_file()


@needs_ffmpeg
def test_tags_come_from_the_first_part_and_the_scene_number_is_cleared(tmp_path, safety):
    a, b = _parts(tmp_path)
    vf = VideoFile(path=a)
    vf.load()
    vf.metadata.title, vf.metadata.studio, vf.metadata.episode_number = "The Whole Movie", "Evil Angel", 1
    vf.save()
    assert not vf.save_error
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    jf.join_groups([plan.group], dry_run=False, safety=safety, plans=[plan])
    joined = VideoFile(path=tmp_path / "Movie.mp4")
    joined.load()
    assert (joined.metadata.title, joined.metadata.studio) == ("The Whole Movie", "Evil Angel")
    assert joined.metadata.episode_number is None


@needs_ffmpeg
def test_one_chapter_per_part_when_asked(tmp_path, safety):
    a, b = _parts(tmp_path)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    jf.join_groups([plan.group], dry_run=False, chapters=True, safety=safety, plans=[plan])
    done = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(tmp_path / "Movie.mp4")],
                          capture_output=True, text=True, check=True)
    titles = [c["tags"]["title"] for c in json.loads(done.stdout)["chapters"]]
    assert titles == ["Part 1", "Part 2"]


@needs_ffmpeg
def test_a_part_changed_in_the_last_minutes_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    busy = dataclasses.replace(library_safety.get_safety_settings(), recent_minutes=60, quarantine_root="")
    a, b = _parts(tmp_path)
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    result = jf.join_group(plan, dry_run=False, safety=busy)
    assert result.status == jf.SKIPPED and "modified in the last" in result.message
    assert a.is_file() and b.is_file() and not (tmp_path / "Movie.mp4").exists()


@needs_ffmpeg
def test_an_existing_target_is_never_overwritten(tmp_path, safety):
    a, b = _parts(tmp_path)
    (tmp_path / "Movie.mp4").write_bytes(b"precious")
    plan = jf.check_group(jf.group_files([str(a), str(b)])[0])
    assert not plan.compatible and "already exists" in plan.reason
    assert (tmp_path / "Movie.mp4").read_bytes() == b"precious"


@needs_ffmpeg
def test_cli_join_files_dry_run_apply_and_undo(tmp_path, monkeypatch, capsys):
    import time

    from videocli.main import main

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    a, b = _parts(tmp_path)
    old = time.time() - 3600
    for part in (a, b):
        os.utime(part, (old, old))  # not "modified in the last 10 minutes"
    log_path = str(tmp_path / "cli.tsv")
    assert main(["join-files", str(tmp_path), "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] and dry["counts"] == {"would join": 1} and not (tmp_path / "Movie.mp4").exists()
    assert main(["join-files", str(tmp_path), "--apply", "--log", log_path, "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["counts"] == {"joined": 1} and (tmp_path / "Movie.mp4").is_file() and not a.exists()
    assert main(["undo-join", log_path]) == 0
    assert "Put back 2 part(s)" in capsys.readouterr().out and a.is_file() and b.is_file()
