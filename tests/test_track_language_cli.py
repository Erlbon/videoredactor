"""`videoredactor set-language`, with real MP4 files: a dry run writes nothing, a real run sets and reads back,
--only-unlabelled keeps labelled tracks, a failing file does not stop the batch, usage errors."""

import json
import os
import shutil
import stat

import pytest

from core import track_language as tl
from redactor_common.cli import CliError
from tests.track_language_helpers import make_mp4, probe_languages
from videocli.main import main

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


def test_dry_run_prints_the_plan_and_writes_nothing(tmp_path, capsys):
    path = make_mp4(tmp_path / "a.mp4")
    before = path.read_bytes()
    assert main(["set-language", str(path), "--audio", "eng", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "planned" in out and "track a1: und -> eng" in out and "DRY RUN" in out
    assert path.read_bytes() == before


def test_real_run_sets_audio_and_leaves_subtitles_alone(tmp_path, capsys):
    path = make_mp4(tmp_path / "a.mp4")
    assert main(["set-language", str(path), "--audio", "nor"]) == 0
    assert probe_languages(path)["audio"] == ["nor", "nor"]
    assert "Set the language of 2 track(s) in 1 file(s)" in capsys.readouterr().out


def test_only_unlabelled_keeps_a_labelled_track(tmp_path):
    path = make_mp4(tmp_path / "a.mp4", languages=["swe", None])
    assert main(["set-language", str(path), "--audio", "eng", "--only-unlabelled"]) == 0
    assert probe_languages(path)["audio"] == ["swe", "eng"]


def test_a_failing_file_is_listed_and_the_rest_still_run(tmp_path, capsys):
    locked = make_mp4(tmp_path / "locked.mp4")
    fine = make_mp4(tmp_path / "fine.mp4")
    os.chmod(locked, stat.S_IREAD)
    try:
        assert main(["set-language", str(locked), str(fine), "--audio", "eng", "--json"]) == 1
    finally:
        os.chmod(locked, stat.S_IREAD | stat.S_IWRITE)
    rows = {os.path.basename(r["path"]): r for r in json.loads(capsys.readouterr().out)["results"]}
    assert rows["locked.mp4"]["status"] == "failed" and "read-only" in rows["locked.mp4"]["message"]
    assert rows["fine.mp4"]["status"] == "changed"
    assert probe_languages(fine)["audio"] == ["eng", "eng"] and probe_languages(locked)["audio"] == ["und", "und"]


def test_usage_errors(tmp_path):
    path = make_mp4(tmp_path / "a.mp4")
    with pytest.raises(CliError, match="nothing to change"):
        main(["set-language", str(path)])
    with pytest.raises(CliError, match="three-letter"):
        main(["set-language", str(path), "--audio", "english"])
