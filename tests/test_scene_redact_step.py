"""The Redact step 'Fill from ThePornDB/StashDB (file match only)': off by default, a hash match is ONLY ever a
suggestion for review (whatever the confidence threshold), only the fingerprint is asked, nothing is written."""

import os

import pytest
from redactor_common.core.pipeline import FileStatus

from core import redact_steps as rs
from core import stashdb_client, theporndb_client
from core.scene_lookup import MATCH_FILE, MATCH_TITLE, SceneLookupError, SceneResult
from core.video_file import VideoFile
from core.video_metadata import ContentType, VideoMetadata
from tests.scene_fixtures import KEY
from tests.test_redact_steps import bin_, make_env, only, run_one, work  # noqa: F401  (fixtures and helpers)


def scene(**over) -> SceneResult:
    base = dict(source="ThePornDB", external_id="id-1", title="Test Scene Title", date="2021-11-15",
                studio="Example Studio", parent_studio="Example Network", performers=["Performer One"], tags=["Tag A"],
                description="Synthetic.", page_url="https://theporndb.net/scenes/x", match_kind=MATCH_FILE)
    base.update(over)
    return SceneResult(**base)


@pytest.fixture
def video(work):
    path = work / "clip.mp4"
    path.write_bytes(os.urandom(200 * 1024))  # over 128 KB: it has an OSHASH
    return VideoFile(path=path, metadata=VideoMetadata())


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("THEPORNDB_API_KEY", KEY)
    monkeypatch.setenv("STASHDB_API_KEY", KEY)


def run_step(video, bin_, tmp_path, threshold=0.9, options=None):
    recipe, catalogue = only("scene_lookup", threshold=threshold, options=options)
    return run_one(video, make_env(bin_, tmp_path), recipe, catalogue)


def test_the_step_is_in_the_catalogue_and_off_by_default():
    step = next(s for s in rs.build_catalogue(lambda: []) if s.key == "scene_lookup")
    assert step.default_enabled is False and "file match only" in step.label


def test_no_key_means_no_request_and_a_note(video, bin_, tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda *a: asked.append(a) or [])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: asked.append(a) or [])
    entry = run_step(video, bin_, tmp_path)
    assert asked == [] and entry.status is FileStatus.UNCHANGED
    assert any("no ThePornDB or StashDB key" in n for n in entry.notes)


@pytest.mark.parametrize("threshold", [0.9, 0.5, 0.0])
def test_a_file_match_is_only_ever_a_suggestion_whatever_the_threshold(video, bin_, tmp_path, monkeypatch, keys, threshold):
    asked = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: asked.append(oshash) or [scene()])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: [])
    before = video.path.read_bytes()
    entry = run_step(video, bin_, tmp_path, threshold=threshold)
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == []
    (item,) = entry.review
    assert item.step_key == "scene_lookup" and "file match" in item.reason and "ThePornDB" in item.reason
    assert item.value.fields["title"] == "Test Scene Title" and item.value.fields["studio"] == "Example Studio"
    # nothing was written: the live row and the file are as they were
    assert video.metadata == VideoMetadata() and not video.dirty and video.path.read_bytes() == before
    assert bin_.names() == []  # no original went to the Recycle Bin: there was no save
    # only the 16-character fingerprint was asked, never a path or a name
    assert len(asked) == 1 and len(asked[0]) == 16 and "clip" not in asked[0]


def test_a_title_match_is_never_used(video, bin_, tmp_path, monkeypatch, keys):
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: [scene(match_kind=MATCH_TITLE)])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: [])
    entry = run_step(video, bin_, tmp_path)
    assert entry.status is FileStatus.UNCHANGED and entry.review == []


def test_several_records_with_one_fingerprint_are_left_for_a_person(video, bin_, tmp_path, monkeypatch, keys):
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: [scene(), scene(external_id="id-2")])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: [])
    entry = run_step(video, bin_, tmp_path)
    assert entry.review == [] and any("choose one in Metadata > Look Up" in n for n in entry.notes)


def test_only_empty_fields_are_offered(video, bin_, tmp_path, monkeypatch, keys):
    video.metadata.title = "My Own Title"
    video.metadata.content_type = ContentType.MOVIE
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: [scene()])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: [])
    entry = run_step(video, bin_, tmp_path)
    (item,) = entry.review
    assert "title" not in item.value.fields and item.value.fields["studio"] == "Example Studio"


def test_the_second_service_is_asked_when_the_first_has_nothing(video, bin_, tmp_path, monkeypatch, keys):
    seen = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: seen.append("tp") or [])
    monkeypatch.setattr(stashdb_client, "search_by_hash",
                        lambda oshash, md5="": seen.append(("sd", md5)) or [scene(source="StashDB")])
    entry = run_step(video, bin_, tmp_path)
    assert seen == ["tp", ("sd", "")]  # no MD5: the whole-file hash is off by default
    assert entry.review[0].value.source == "StashDB"


def test_the_options_switch_a_service_off(video, bin_, tmp_path, monkeypatch, keys):
    seen = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda oshash: seen.append("tp") or [])
    monkeypatch.setattr(stashdb_client, "search_by_hash", lambda *a: seen.append("sd") or [])
    run_step(video, bin_, tmp_path, options={"scene_lookup": {"theporndb": False}})
    assert seen == ["sd"]


def test_a_service_error_is_a_note_not_a_failure(video, bin_, tmp_path, monkeypatch, keys):
    def boom(*a):
        raise SceneLookupError("Could not reach the service")

    monkeypatch.setattr(theporndb_client, "search_by_hash", boom)
    monkeypatch.setattr(stashdb_client, "search_by_hash", boom)
    entry = run_step(video, bin_, tmp_path)
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("ThePornDB unavailable" in n for n in entry.notes)


def test_a_file_under_128_kb_is_skipped_with_a_note(work, bin_, tmp_path, monkeypatch, keys):
    small = work / "tiny.mp4"
    small.write_bytes(b"x" * 100)
    asked = []
    monkeypatch.setattr(theporndb_client, "search_by_hash", lambda *a: asked.append(a) or [])
    entry = run_step(VideoFile(path=small, metadata=VideoMetadata()), bin_, tmp_path)
    assert asked == [] and any("too small" in n for n in entry.notes)


def test_the_apply_path_fills_the_working_copy_only(video):
    """The step's own apply path (used if a suggestion is ever applied): content type + the empty fields, on the
    working copy; the live row is untouched."""
    ctx = rs.VideoCtx(video, rs.RedactEnv())
    fill = rs.LookupFill("ThePornDB", "Test Scene Title", {"title": "Test Scene Title", "studio": "Example Studio"},
                         ContentType.MOVIE, "2021")
    lines = rs.SceneLookupStep().apply_suggestion(ctx, rs.StepResult.suggestion(fill, 0.95, "x", review_only=True))
    assert ctx.work.metadata.title == "Test Scene Title" and ctx.work.metadata.content_type is ContentType.MOVIE
    assert any("title" in line for line in lines) and video.metadata.title == ""
