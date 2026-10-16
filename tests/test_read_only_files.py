"""A read-only video through the real save paths (2026-10-16 review, finding 3): a clear 'read-only' message BEFORE anything
is copied, the original untouched (and still read-only), and no full-size scratch copy left in the folder. shutil.copy2
copies the read-only bit, so the scratch copy of a read-only file used to be read-only too, the save failed on it and the
cleanup `os.remove` was refused and swallowed: a hidden .videoredactor-*.tmp the size of the movie stayed behind."""

import os
import shutil
import stat
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402

from core import file_access, mp4_backend  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402
from tests.test_poster import solid  # noqa: E402
from tests.test_redact_steps import Bin, PATTERN, make_env, only, run_one  # noqa: E402

pytestmark = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def ro_clip(tmp_path):
    path = tmp_path / "Show.S01E01.mp4"
    shutil.copy(SAMPLE, path)
    os.chmod(path, stat.S_IREAD)
    yield path
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def folder_of(path: Path) -> list[str]:
    return sorted(os.listdir(path.parent))


def test_saving_a_read_only_mp4_says_so_and_leaves_no_temp_file(ro_clip):
    before = ro_clip.read_bytes()
    vf = VideoFile(path=ro_clip)
    vf.load()
    vf.metadata.title = "Pilot"
    vf.dirty = True
    vf.save()
    assert "read-only" in vf.save_error and ro_clip.name in vf.save_error
    assert "Permission denied" not in vf.save_error and ".videoredactor-" not in vf.save_error
    assert vf.dirty  # still unsaved
    assert folder_of(ro_clip) == [ro_clip.name]  # no hidden full-size copy
    assert ro_clip.read_bytes() == before and not os.access(ro_clip, os.W_OK)  # untouched, still read-only


def test_embedding_a_poster_into_a_read_only_mp4_says_so_and_leaves_no_temp_file(ro_clip):
    vf = VideoFile(path=ro_clip)
    vf.load()
    problem = vf.embed_cover_now(solid(600, 900, "JPG"))
    assert "read-only" in problem
    assert folder_of(ro_clip) == [ro_clip.name]


def test_a_scratch_copy_that_ends_up_read_only_is_removed_after_a_failed_edit(tmp_path):
    """Whatever the reason a scratch copy is read-only, the cleanup must succeed (it used to be refused and swallowed)."""
    clip = tmp_path / "Movie.mp4"
    shutil.copy(SAMPLE, clip)

    def apply(scratch):
        os.chmod(scratch, stat.S_IREAD)
        raise RuntimeError("the edit failed")

    with pytest.raises(RuntimeError):
        mp4_backend._edit_via_scratch(str(clip), apply)
    assert folder_of(clip) == [clip.name]


def test_a_scratch_copy_is_made_writable_right_after_copying(tmp_path):
    """copy2 hands the scratch copy the source's read-only bit (here: the source turned read-only after the up-front
    check, as a race would); the edit of the copy still has to work."""
    clip = tmp_path / "Movie.mp4"
    shutil.copy(SAMPLE, clip)
    real_copy2 = shutil.copy2

    def copy_then_read_only(src, dst, *a, **k):
        out = real_copy2(src, dst, *a, **k)
        os.chmod(dst, stat.S_IREAD)
        return out

    with mock.patch.object(mp4_backend.shutil, "copy2", side_effect=copy_then_read_only):
        mp4_backend.write_mp4_metadata(str(clip), VideoMetadata(title="Edited"))
    assert mp4_backend.read_mp4_metadata(str(clip)).title == "Edited"
    assert folder_of(clip) == [clip.name]


def test_remove_scratch_removes_a_read_only_file_and_reports_a_missing_one(tmp_path):
    f = tmp_path / "x.tmp"
    f.write_bytes(b"data")
    os.chmod(f, stat.S_IREAD)
    assert file_access.remove_scratch(str(f)) and not f.exists()
    assert file_access.remove_scratch(str(f))  # already gone is fine


def test_a_writable_files_permissions_are_not_touched(tmp_path):
    f = tmp_path / "x.mp4"
    f.write_bytes(b"data")
    mode = os.stat(f).st_mode
    file_access.make_scratch_writable(str(f))
    assert os.stat(f).st_mode == mode


def test_redact_refuses_a_read_only_original_before_making_a_working_copy(tmp_path):
    work = tmp_path / "videos"
    work.mkdir()
    path = work / "The Office S02E05 Halloween.mp4"
    shutil.copy(SAMPLE, path)
    os.chmod(path, stat.S_IREAD)
    try:
        before = path.read_bytes()
        bin_ = Bin(tmp_path / "bin")
        vf = VideoFile(path=path)
        vf.load()
        recipe, catalogue = only("filename_tags")
        entry = run_one(vf, make_env(bin_, tmp_path, history=[PATTERN]), recipe, catalogue)
        assert entry.status is FileStatus.FAILED
        assert "NOT SAVED" in entry.failures[0] and "read-only" in entry.failures[0]
        assert path.read_bytes() == before and bin_.names() == []
        assert os.listdir(work) == [path.name]  # no hidden scratch of any kind
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_a_read_only_mkv_is_refused_before_any_tool_runs(tmp_path):
    """Against the in-memory model of MKVToolNix (not the real tool): no mkvpropedit call is made at all."""
    from tests.test_embedded_sources_mkv_fake_tools import FakeMkv

    path = tmp_path / "Movie.mkv"
    path.write_bytes(b"not really a matroska file")
    os.chmod(path, stat.S_IREAD)
    fake = FakeMkv()
    try:
        with mock.patch("subprocess.run", side_effect=fake.run), mock.patch("core.video_file.is_tool_available", return_value=True):
            vf = VideoFile(path=path, metadata=VideoMetadata(title="New"))
            vf.dirty = True
            vf.save()
            problem = vf.embed_cover_now(solid(600, 900, "JPG"))
        assert "read-only" in vf.save_error and "read-only" in problem
        assert fake.propedit_calls == [] and os.listdir(tmp_path) == [path.name]
    finally:
        os.chmod(path, stat.S_IREAD | stat.S_IWRITE)
