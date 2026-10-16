"""Source-id tags and the embedded poster against REAL MP4 files (copies of a small sample clip), with the real mutagen
backend and ffprobe: the freeform atoms and the cover atom are written by VideoFile.save(), read back by mutagen AND by
ffprobe, and the file still probes. Skipped when the sample clip or ffprobe is missing."""

import json
import os
import shutil
import subprocess

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import mp4_backend  # noqa: E402
from core.poster import prepare_poster  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.test_poster import solid  # noqa: E402

from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402

FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")

_app = QApplication.instance() or QApplication([])
U = "d284e2a0-789d-4e6c-beb9-6e61c24e8b84"


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height:stream_disposition=attached_pic",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)["streams"]


@pytest.fixture
def clip(tmp_path):
    path = tmp_path / "Show.S01E01.mp4"
    shutil.copy(SAMPLE, path)
    return path


def loaded(path) -> VideoFile:
    vf = VideoFile(path=path)
    vf.load()
    assert not vf.load_error
    return vf


def test_ids_and_poster_round_trip_through_save(clip, tmp_path):
    folder_before = sorted(os.listdir(tmp_path))
    vf = loaded(clip)
    assert vf.metadata.cover_info == "" and vf.metadata.tmdb_id == ""
    poster = prepare_poster(solid(600, 900, "PNG"))
    vf.metadata.title = "Pilot"
    vf.metadata.tmdb_id, vf.metadata.imdb_id = "episode:1396/1/1", "tt0959621"
    vf.metadata.source_url = "https://www.themoviedb.org/tv/1396/season/1/episode/1"
    vf.metadata.metadata_match = "tmdb;2026-10-16;title"
    vf.metadata.cover_pending = poster.jpeg
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and not vf.dirty
    assert vf.metadata.cover_pending is None and vf.metadata.cover_info.startswith("JPEG 600x900")

    # 1. mutagen: the atoms exactly as documented
    from mutagen.mp4 import MP4

    tags = MP4(str(clip)).tags
    assert bytes(tags["----:com.videoredactor:TMDB_ID"][0]) == b"episode:1396/1/1"
    assert bytes(tags["----:com.videoredactor:IMDB_ID"][0]) == b"tt0959621"
    assert bytes(tags["----:com.videoredactor:SOURCE_URL"][0]).startswith(b"https://www.themoviedb.org/")
    assert bytes(tags["----:com.videoredactor:METADATA_MATCH"][0]) == b"tmdb;2026-10-16;title"
    assert "----:com.videoredactor:THEPORNDB_ID" not in tags  # nothing was invented for the others
    assert bytes(tags["covr"][0]) == poster.jpeg and tags["covr"][0].imageformat == tags["covr"][0].FORMAT_JPEG

    # 2. ffprobe: the cover is an attached picture, the video and audio streams are intact
    streams = probe(clip)
    attached = [s for s in streams if s["disposition"]["attached_pic"] == 1]
    assert len(attached) == 1 and attached[0]["codec_name"] == "mjpeg" and (attached[0]["width"], attached[0]["height"]) == (600, 900)
    real = [s for s in streams if s["disposition"]["attached_pic"] == 0]
    assert [s["codec_type"] for s in real] == ["video", "audio"] and (real[0]["width"], real[0]["height"]) == (320, 240)

    # 3. a fresh load sees the same, and the folder holds the video only (no sidecar, no leftover scratch)
    again = loaded(clip)
    assert (again.metadata.tmdb_id, again.metadata.imdb_id, again.metadata.metadata_match) == (
        "episode:1396/1/1", "tt0959621", "tmdb;2026-10-16;title")
    assert again.read_cover() == poster.jpeg and again.metadata.title == "Pilot"
    assert sorted(os.listdir(tmp_path)) == folder_before


def test_a_save_without_a_staged_poster_leaves_the_existing_cover_alone(clip):
    vf = loaded(clip)
    first = prepare_poster(solid(600, 900, "PNG", color=0x225588))
    vf.metadata.cover_pending = first.jpeg
    vf.dirty = True
    vf.save()
    again = loaded(clip)
    again.metadata.comment = "edited later"
    again.dirty = True
    again.save()
    assert again.save_error == "" and loaded(clip).read_cover() == first.jpeg


def test_a_new_poster_replaces_the_old_one_and_there_is_only_one_cover(clip):
    vf = loaded(clip)
    old = prepare_poster(solid(600, 900, "PNG", color=0x111111))
    new = prepare_poster(solid(700, 1000, "PNG", color=0xEEEEEE))
    for poster in (old, new):
        vf.metadata.cover_pending = poster.jpeg
        vf.dirty = True
        vf.save()
        assert vf.save_error == ""
    assert vf.read_cover() == new.jpeg
    assert sum(1 for s in probe(clip) if s["disposition"]["attached_pic"] == 1) == 1


def test_clearing_an_id_removes_its_atom(clip):
    vf = loaded(clip)
    vf.metadata.stashdb_id = U
    vf.dirty = True
    vf.save()
    from mutagen.mp4 import MP4

    assert "----:com.videoredactor:STASHDB_ID" in MP4(str(clip)).tags
    vf.metadata.stashdb_id = ""
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and "----:com.videoredactor:STASHDB_ID" not in MP4(str(clip)).tags


def test_embed_cover_now_touches_only_the_cover(clip):
    vf = loaded(clip)
    vf.metadata.title = "Unsaved edit"      # staged, never written by embed_cover_now
    vf.dirty = True
    poster = prepare_poster(solid(600, 900, "PNG"))
    assert vf.embed_cover_now(poster.jpeg) == ""
    on_disk = loaded(clip)
    assert on_disk.read_cover() == poster.jpeg and on_disk.metadata.title != "Unsaved edit"
    assert vf.metadata.cover_info.startswith("JPEG 600x900") and vf.dirty  # the staged edit is still pending


def test_a_failed_embed_reports_and_leaves_the_file_intact(clip, monkeypatch):
    vf = loaded(clip)
    before = clip.read_bytes()

    def boom(path, data, is_png=False):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("core.video_file.write_mp4_cover", boom)
    problem = vf.embed_cover_now(b"\xff\xd8\xff" + b"0" * 50)
    assert problem.startswith("File error") and clip.read_bytes() == before


def test_the_cover_is_what_the_panel_preview_shows(clip):
    vf = loaded(clip)
    assert vf.get_preview() is not None and vf.preview_kind == "Frame preview"  # no cover: the frame
    vf.metadata.cover_pending = prepare_poster(solid(600, 900, "PNG")).jpeg
    vf.dirty = True
    vf.save()
    path = vf.get_preview()
    assert vf.preview_kind == "Poster" and path.read_bytes() == vf.read_cover()


def test_an_untagged_clip_reads_with_no_sources(clip):
    meta = mp4_backend.read_mp4_metadata(str(clip))
    assert (meta.tmdb_id, meta.source_url, meta.metadata_match, meta.cover_info) == ("", "", "", "")


def test_unicode_survives_in_the_tags(clip):
    vf = loaded(clip)
    vf.metadata.source_url = "https://en.wikipedia.org/wiki/Z%C3%BCrich"   # an allowlisted link with percent escapes
    vf.metadata.content_type = ContentType.MOVIE
    vf.dirty = True
    vf.save()
    assert vf.save_error == "" and loaded(clip).metadata.source_url == vf.metadata.source_url
