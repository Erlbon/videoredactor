"""The temp files of remux, transcode and repair must never be a name a user's real file could have (2026-10-16 review,
finding 5). They used '<stem>.partial<ext>' / '<stem>.repairing<ext>', and deleted and overwrote (ffmpeg -y) whatever was
there: a real 'Movie.partial.mp4' (or 'Movie.Partial.mp4' on Windows' case-insensitive disk) was destroyed by a remux of
Movie.mkv. Now the temp is the family's hidden '.<stem>.redact-<8 hex><ext>' with a random suffix, ffmpeg runs with -n
(never overwrite) and only files this run created are ever removed. Real ffmpeg, real files."""

import os
import shutil
import subprocess
import unittest.mock as mock

import pytest
from redactor_common.core.temp_names import is_app_temp_name

from core import ffmpeg_backend as fb
from core import file_check as fc
from tests.sample_clip import SAMPLE, SKIP_REASON

pytestmark = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")

KEEPSAKES = {"Movie.partial.mp4": b"A real file the user named Movie.partial", "Movie.Partial.mkv": b"Another real file",
             "Movie.repairing.mp4": b"A real file named Movie.repairing", "Movie.Repairing.mkv": b"Real too"}


def plant(folder):
    for name, data in KEEPSAKES.items():
        (folder / name).write_bytes(data)


def assert_untouched(folder):
    for name, data in KEEPSAKES.items():
        assert (folder / name).read_bytes() == data, f"{name} was changed or removed"


def make_mkv(folder, name="Movie.mkv"):
    out = folder / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-c", "copy", str(out)], check=True, capture_output=True)
    return out


def test_the_temp_name_is_hidden_random_and_a_family_temp_name(tmp_path):
    a, b = fb.partial_path(str(tmp_path / "Movie.mp4")), fb.partial_path(str(tmp_path / "Movie.mp4"))
    assert a != b  # random per call
    for path in (a, b):
        name = os.path.basename(path)
        assert name.startswith(".Movie.redact-") and name.endswith(".mp4") and is_app_temp_name(name)
        assert os.path.dirname(path) == str(tmp_path)
    assert is_app_temp_name(os.path.basename(fc.repair_path(str(tmp_path / "Movie.mkv"))))
    assert fc.repair_path(str(tmp_path / "Movie.mkv")).endswith(".mkv")


def test_the_old_temp_names_are_still_recognised_for_leftovers():
    for name in ("Movie.partial.mp4", "Movie.Partial.mkv", "Movie.repairing.mp4"):
        assert is_app_temp_name(name)
    assert not is_app_temp_name("My.Partial.Cut.mkv")  # a real file is still listed


def test_a_remux_to_mp4_leaves_real_files_called_partial_alone(tmp_path):
    src = make_mkv(tmp_path)
    plant(tmp_path)
    out = tmp_path / "Movie.mp4"
    ok, message = fb.remux_to_mp4(str(src), str(out))
    assert ok, message
    assert out.exists() and not any(n.startswith(".") for n in os.listdir(tmp_path))  # the temp is gone
    assert_untouched(tmp_path)


def test_a_remux_to_mkv_leaves_real_files_called_partial_alone(tmp_path):
    src = tmp_path / "Movie.avi"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-c:v", "mpeg4", "-c:a", "mp3", str(src)], check=True, capture_output=True)
    plant(tmp_path)
    out = tmp_path / "Movie.mkv"
    ok, message = fb.remux_to_mkv(str(src), str(out))
    assert ok, message
    assert_untouched(tmp_path)
    assert not any(n.startswith(".") for n in os.listdir(tmp_path))


def test_a_transcode_leaves_real_files_called_partial_alone(tmp_path):
    src = make_mkv(tmp_path)
    plant(tmp_path)
    out = tmp_path / "Movie.mp4"
    ok, message = fb.transcode_to_mp4(str(src), str(out), crf=40)
    assert ok, message
    assert_untouched(tmp_path)


def test_a_failed_remux_still_leaves_real_files_alone_and_no_temp_behind(tmp_path):
    bad = tmp_path / "Movie.mkv"
    bad.write_bytes(b"not a video")
    plant(tmp_path)
    ok, _ = fb.remux_to_mp4(str(bad), str(tmp_path / "Movie.mp4"))
    assert not ok
    assert_untouched(tmp_path)
    assert sorted(os.listdir(tmp_path)) == sorted([bad.name, *KEEPSAKES])


def test_ffmpeg_is_never_told_to_overwrite_and_only_its_own_temp_is_written(tmp_path):
    seen = []

    class Instant:
        returncode = 0

        def communicate(self, timeout=None):
            return "", ""

    def fake_popen(args, **kwargs):
        seen.append(list(args))
        open(args[-1], "wb").write(b"done")
        return Instant()

    with mock.patch("core.ffmpeg_backend.subprocess.Popen", side_effect=fake_popen):
        ok, _ = fb.remux_to_mp4("in.mkv", str(tmp_path / "x.mp4"))
    assert ok
    (args,) = seen
    assert "-n" in args and "-y" not in args
    assert is_app_temp_name(os.path.basename(args[-1])) and args[-1] != str(tmp_path / "x.mp4")


def test_a_second_remux_attempt_removes_only_its_own_first_try(tmp_path, monkeypatch):
    """The fallback attempts reuse the temp: the file removed between them is the one the first attempt made."""
    calls = []

    def fake_run(args, cancel_event=None, timeout=None):
        calls.append(args[-1])
        if len(calls) == 1:
            assert not os.path.exists(args[-1])
            open(args[-1], "wb").write(b"half")
            return 1, "first attempt failed"
        assert not os.path.exists(args[-1])  # the half-written first try is gone, nothing else was in the way
        open(args[-1], "wb").write(b"whole")
        return 0, ""

    monkeypatch.setattr(fb, "_run_ffmpeg", fake_run)
    plant(tmp_path)
    ok, _ = fb.remux_to_mp4("in.mkv", str(tmp_path / "Movie.mp4"))
    assert ok and (tmp_path / "Movie.mp4").read_bytes() == b"whole"
    assert_untouched(tmp_path)


# --- repair ---------------------------------------------------------------------------------------------------------


def test_a_repair_works_beside_real_files_called_repairing_and_leaves_them_alone(tmp_path):
    clip = tmp_path / "Movie.mp4"
    shutil.copy(SAMPLE, clip)
    # Rewrite it without faststart so the check finds a repairable fault (index at the end).
    late = tmp_path / "late.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", SAMPLE, "-c", "copy", "-movflags", "-faststart", str(late)], check=True, capture_output=True)
    late.replace(clip)
    plant(tmp_path)
    before = fc.quick_check(str(clip))
    assert before.can_repair, before.summary()
    trashed = []
    outcome = fc.repair(str(clip), before, trash=trashed.append, restore=lambda _o, _r: "")
    assert trashed == [str(clip)] and not outcome.check.can_repair
    assert_untouched(tmp_path)
    assert sorted(os.listdir(tmp_path)) == sorted(["Movie.mp4", *KEEPSAKES])  # no repair temp left


def test_a_failed_repair_removes_only_its_own_copy(tmp_path):
    clip = tmp_path / "Movie.mp4"
    shutil.copy(SAMPLE, clip)
    plant(tmp_path)
    before = fc.CheckResult(findings=[fc.Finding("no_duration", fc.REPAIRABLE, "x")])

    def boom(_o, _r):
        raise fc.RepairError("restore failed")

    with pytest.raises(fc.RepairError):
        fc.repair(str(clip), before, trash=lambda p: None, restore=boom)
    assert_untouched(tmp_path)
    assert sorted(os.listdir(tmp_path)) == sorted(["Movie.mp4", *KEEPSAKES])
