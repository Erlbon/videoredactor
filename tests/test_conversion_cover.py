"""A conversion must carry the embedded poster over (2026-10-16 review, finding 1). Since 'Embed Existing Poster Files'
moves the sidecar to the Recycle Bin, the embedded picture is the ONLY copy: a conversion that drops it loses it.

Real flows: a real MP4 (the sample clip) with a real cover goes through the real process_file with real ffmpeg; the
result is read back with the app's reader AND ffprobe (attached_pic). The MKV direction runs against the in-memory
model of MKVToolNix of tests/test_embedded_sources_mkv_fake_tools.py: that proves the logic and the command lines, NOT the
real tool (MKVToolNix is not installed on the machine these were written on)."""

import json
import os
import shutil
import subprocess
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import conversion_plan as cp  # noqa: E402
from core import conversion_run as cr  # noqa: E402
from core.library_safety import SafeRun  # noqa: E402
from core.mp4_backend import read_mp4_cover, write_mp4_cover  # noqa: E402
from core.poster import prepare_poster  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_poster import solid  # noqa: E402

pytestmark = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")

_app = QApplication.instance() or QApplication([])

# A bitrate re-encode of a tiny clip with the software encoder: the one conversion of a real MP4 that needs no
# MKVToolNix (min size 0, limit below zero so the clip counts as 'too fat', the size-ratio rule relaxed for a 2 s clip).
REENCODE = cp.ConversionSettings(
    encoder="x265", min_size_mb=0, reencode_video_mbps=-1.0, reencode_video_mbps_small=-1.0, output_max_ratio=50.0,
)


def attached_pics(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type:stream_disposition=attached_pic", "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return [s for s in json.loads(out)["streams"] if s.get("disposition", {}).get("attached_pic")]


def make_clip(folder: Path, name="Show.S01E01.mp4", cover=True) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    clip = folder / name
    shutil.copy(SAMPLE, clip)
    if cover:
        write_mp4_cover(str(clip), prepare_poster(solid(600, 900)).jpeg)
    return clip


def real_run(lib: Path, trashed: list) -> SafeRun:
    return SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, recent_minutes=0, trash=trashed.append)


def test_a_reencoded_mp4_keeps_its_embedded_poster(tmp_path):
    clip = make_clip(tmp_path / "lib")
    cover = read_mp4_cover(str(clip))
    assert cover and len(attached_pics(clip)) == 1
    trashed = []
    out = cr.process_file(real_run(tmp_path / "lib", trashed), str(clip), REENCODE, carry_tags=False)
    assert out.converted, out.message
    assert out.final_path == str(clip)
    assert read_mp4_cover(out.final_path) == cover  # the app's reader
    assert len(attached_pics(out.final_path)) == 1  # ffprobe
    vf = VideoFile(path=Path(out.final_path))
    vf.load()
    assert vf.metadata.cover_info.startswith("JPEG 600x900")
    assert len(trashed) == 1  # the original went to the (fake) bin only after the verified swap


def test_the_poster_survives_when_the_tags_are_carried_too(tmp_path):
    clip = make_clip(tmp_path / "lib")
    cover = read_mp4_cover(str(clip))
    vf = VideoFile(path=clip)
    vf.load()
    vf.metadata.title, vf.metadata.director = "Pilot", "A. Director"
    vf.dirty = True
    vf.save()
    assert vf.save_error == ""
    out = cr.process_file(real_run(tmp_path / "lib", []), str(clip), REENCODE)
    assert out.converted, out.message
    again = VideoFile(path=Path(out.final_path))
    again.load()
    assert again.metadata.title == "Pilot" and again.metadata.director == "A. Director"
    assert read_mp4_cover(out.final_path) == cover


def test_a_png_poster_keeps_its_format(tmp_path):
    clip = make_clip(tmp_path / "lib", cover=False)
    png = solid(300, 450, "PNG")
    write_mp4_cover(str(clip), png, is_png=True)
    out = cr.process_file(real_run(tmp_path / "lib", []), str(clip), REENCODE, carry_tags=False)
    assert out.converted, out.message
    assert read_mp4_cover(out.final_path) == png
    from mutagen.mp4 import MP4, MP4Cover

    assert MP4(out.final_path).tags["covr"][0].imageformat == MP4Cover.FORMAT_PNG


def test_a_file_without_a_poster_gets_none_invented(tmp_path):
    clip = make_clip(tmp_path / "lib", cover=False)
    out = cr.process_file(real_run(tmp_path / "lib", []), str(clip), REENCODE, carry_tags=False)
    assert out.converted, out.message
    assert read_mp4_cover(out.final_path) is None and attached_pics(out.final_path) == []


def test_when_the_poster_cannot_be_restored_the_original_is_kept(tmp_path):
    clip = make_clip(tmp_path / "lib")
    before = clip.read_bytes()
    trashed = []
    with mock.patch.object(VideoFile, "embed_cover_now", return_value="disk full"):
        out = cr.process_file(real_run(tmp_path / "lib", trashed), str(clip), REENCODE, carry_tags=False)
    assert not out.converted and "poster" in out.message and "disk full" in out.message
    assert clip.read_bytes() == before and trashed == []
    assert sorted(os.listdir(tmp_path / "lib")) == [clip.name]  # no temp file left behind


def test_a_poster_that_is_unreadable_but_known_to_exist_stops_the_conversion(tmp_path):
    clip = make_clip(tmp_path / "lib")
    before = clip.read_bytes()
    with mock.patch.object(VideoFile, "read_cover", side_effect=OSError("locked")):
        out = cr.process_file(real_run(tmp_path / "lib", []), str(clip), REENCODE, carry_tags=False)
    assert not out.converted and "poster" in out.message
    assert clip.read_bytes() == before


def test_a_dry_run_does_not_read_or_touch_the_poster(tmp_path):
    clip = make_clip(tmp_path / "lib")
    before = clip.read_bytes()
    run = SafeRun(library_root=str(tmp_path / "lib"), quarantine_root="", dry_run=True, recent_minutes=0)
    with mock.patch.object(VideoFile, "read_cover", side_effect=AssertionError("dry run must not read")):
        out = cr.process_file(run, str(clip), REENCODE, carry_tags=False)
    assert out.converted and clip.read_bytes() == before


# --- MP4 -> MKV against the model of MKVToolNix ---------------------------------------------------------------------

from tests.test_embedded_sources_mkv_fake_tools import FakeMkv  # noqa: E402


def test_an_mp4_that_becomes_an_mkv_keeps_its_poster_as_the_cover_attachment(tmp_path):
    lib = tmp_path / "lib"
    clip = make_clip(lib)
    cover = read_mp4_cover(str(clip))
    (lib / "Show.S01E01.en.srt").write_text("1\n00:00:00,100 --> 00:00:01,000\nHello there my friend\n", encoding="utf-8")
    fake = FakeMkv()
    real_run_ = subprocess.run

    def dispatch(args, **kwargs):
        if any(name in os.path.basename(str(args[0])).lower() for name in ("mkvmerge", "mkvextract", "mkvpropedit")):
            return fake.run(args, **kwargs)
        return real_run_(args, **kwargs)

    with mock.patch("subprocess.run", side_effect=dispatch), mock.patch("core.video_file.is_tool_available", return_value=True):
        out = cr.process_file(real_run(lib, []), str(clip), cp.ConversionSettings(), carry_tags=False)
    assert out.converted, out.message
    assert out.final_path.endswith(".mkv") and not clip.exists()
    assert [(a["file_name"], a["content_type"]) for a in fake.attachments] == [("cover.jpg", "image/jpeg")]
    assert fake.attachments[0]["data"] == cover


# --- an MKV source ------------------------------------------------------------------------------------------------


def make_mkv(folder: Path, with_cover: bool) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / "Show.S01E02.mkv"
    args = ["ffmpeg", "-v", "error", "-y", "-i", SAMPLE]
    if with_cover:
        jpg = folder / "cover.jpg"
        jpg.write_bytes(solid(600, 900, "JPG"))
        args += ["-attach", str(jpg), "-metadata:s:t:0", "mimetype=image/jpeg"]
    args += ["-c", "copy", str(out)]
    subprocess.run(args, check=True, capture_output=True)
    if with_cover:
        jpg.unlink()
    return out


def test_an_mkv_with_a_cover_is_not_converted_when_the_cover_cannot_be_read(tmp_path):
    lib = tmp_path / "lib"
    clip = make_mkv(lib, with_cover=True)
    before = clip.read_bytes()
    with mock.patch("core.conversion_run.is_tool_available", return_value=False):
        out = cr.process_file(real_run(lib, []), str(clip), REENCODE, carry_tags=False)
    assert not out.converted and "poster" in out.message and "MKVToolNix" in out.message
    assert clip.read_bytes() == before and os.listdir(lib) == [clip.name]


def test_an_mkv_without_a_cover_still_converts_without_mkvtoolnix(tmp_path):
    lib = tmp_path / "lib"
    clip = make_mkv(lib, with_cover=False)
    with mock.patch("core.conversion_run.is_tool_available", return_value=False):
        out = cr.process_file(real_run(lib, []), str(clip), REENCODE, carry_tags=False)
    assert out.converted, out.message
