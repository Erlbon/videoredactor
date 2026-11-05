"""redact -n / --dry-run (redactor_common 2026-11-05-01): only the steps marked dry_run_safe run, nothing is saved.

The CLI test runs every dry-run-safe step on a copy of the sample clip and checks the folder byte for byte; the
lookups are faked and the network is blocked, so nothing leaves the machine."""

from __future__ import annotations

import json
import os
import shutil
import socket
from types import SimpleNamespace

import pytest

from core import redact_steps
from core.redact_steps import build_catalogue
from tests.sample_clip import SAMPLE, SKIP_REASON
from videocli.main import main

# The steps that only read (files, ffprobe, the web) and change the in-memory working copy. Every other step writes
# beside the video (check_repair's repair, subtitles, translate_subtitles, remux_mkv_to_mp4) or runs after the save
# (convert_library, rename, move_into_folders). Changing this set needs the audit in core/redact_steps.py redone.
DRY_RUN_SAFE = {"filename_tags", "path_tags", "lookup", "scene_lookup", "get_poster", "track_title_to_title"}


def test_the_dry_run_safe_steps_are_pinned():
    catalogue = build_catalogue()
    assert {s.key for s in catalogue if s.dry_run_safe} == DRY_RUN_SAFE
    assert not [s.key for s in catalogue if s.dry_run_safe and s.position == "after_save"]


def _snapshot(folder):
    """Every entry under `folder`: relative path -> (bytes, mtime_ns); folders map to None."""
    found = {}
    for root, dirs, files in os.walk(folder):
        for name in dirs:
            found[os.path.relpath(os.path.join(root, name), folder)] = None
        for name in files:
            path = os.path.join(root, name)
            with open(path, "rb") as handle:
                found[os.path.relpath(path, folder)] = (handle.read(), os.stat(path).st_mtime_ns)
    return found


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise AssertionError("a dry-run test tried to reach the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def fake_tmdb(monkeypatch):
    """TMDB answers for 'Show' S01E02 without a request."""
    tmdb = redact_steps.tmdb_client
    monkeypatch.setattr(tmdb, "search_tv", lambda title, year=None: [SimpleNamespace(name="Show", year="", tmdb_id=42)])
    monkeypatch.setattr(tmdb, "get_tv_show_details", lambda ident: {"show_title": "Show", "network": "Fake Network"})
    monkeypatch.setattr(
        tmdb, "get_tv_episode_details", lambda ident, season, episode: {"title": "The Second One", "description": "Plot."},
    )


@pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip available")
def test_redact_dry_run_with_every_safe_step_writes_nothing(tmp_path, capsys, no_network, fake_tmdb):
    library = tmp_path / "library"
    library.mkdir()
    video = library / "Show.S01E02.mp4"
    shutil.copyfile(SAMPLE, video)
    before = _snapshot(library)

    steps = [s.key for s in build_catalogue()]
    argv = ["redact", "-n", str(video), "--threshold", "0.5", "--trash-dir", str(tmp_path / "trash"), "--json"]
    for key in steps:
        argv += ["--enable" if key in DRY_RUN_SAFE else "--disable", key]
    code = main(argv)
    document = json.loads(capsys.readouterr().out)

    assert code == 0, document
    assert document["dry_run"] is True
    entry = document["results"][0]
    # The guesses were applied to the in-memory copy (a real run would save them) ...
    assert entry["applied"], entry
    assert "Fake Network" in json.dumps(entry)
    # ... and nothing on disk changed: same names, same bytes, same times; no trash folder was made.
    assert _snapshot(library) == before
    assert not (tmp_path / "trash").exists()


@pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip available")
def test_redact_dry_run_leaves_the_writing_steps_out_and_says_so(tmp_path, capsys, no_network):
    video = tmp_path / "Show.S01E02.mp4"
    shutil.copyfile(SAMPLE, video)
    before = _snapshot(tmp_path)
    steps = [s.key for s in build_catalogue()]
    argv = ["redact", "--dry-run", str(video), "--json", "--disable", "lookup"]
    for key in ("check_repair", "remux_mkv_to_mp4", "convert_library", "rename"):
        argv += ["--enable", key]
    assert set(steps) >= {"check_repair", "remux_mkv_to_mp4", "convert_library", "rename"}
    main(argv)
    document = json.loads(capsys.readouterr().out)
    assert document["dry_run"] is True
    notes = " ".join(document["run_notes"])
    assert "Dry run: not run" in notes and "Rename by pattern" in notes and "Check and repair" in notes
    assert _snapshot(tmp_path) == before
