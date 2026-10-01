"""
Thumbnail cache housekeeping (core/thumb_cache.py) with injected dir/clock,
and VideoFile.get_thumbnail's atomic write. No real ffmpeg: extraction is mocked.
"""

import hashlib
import os
from pathlib import Path

import pytest

import core.thumb_cache as tc
import core.video_file as vfmod
from core.video_file import VideoFile

DAY = 86400
NOW = 1_000_000_000.0
JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100


def _name(i: int) -> str:
    return hashlib.sha1(str(i).encode()).hexdigest() + ".jpg"


@pytest.fixture
def cache(tmp_path):
    d = tmp_path / tc.CACHE_DIR_NAME
    d.mkdir()
    return d


def _make(d: Path, name: str, age_days: float = 0, size: int = 100) -> Path:
    p = d / name
    p.write_bytes(b"\xff\xd8" + b"x" * (size - 2))
    t = NOW - age_days * DAY
    os.utime(p, (t, t))
    return p


def test_old_files_pruned_fresh_kept(cache):
    old = _make(cache, _name(1), age_days=20)
    fresh = _make(cache, _name(2), age_days=1)
    assert tc.prune_thumbnail_cache(cache, now=NOW) == 1
    assert not old.exists() and fresh.exists()


def test_age_limit_is_configurable(cache):
    f = _make(cache, _name(1), age_days=3)
    assert tc.prune_thumbnail_cache(cache, now=NOW, max_age_days=2) == 1
    assert not f.exists()


def test_size_cap_removes_oldest_first(cache):
    files = [_make(cache, _name(i), age_days=10 - i, size=1000) for i in range(5)]  # 0 oldest
    removed = tc.prune_thumbnail_cache(cache, now=NOW, max_bytes=2500)
    assert removed == 3
    assert [f.exists() for f in files] == [False, False, False, True, True]


def test_count_cap_removes_oldest_first(cache):
    files = [_make(cache, _name(i), age_days=10 - i) for i in range(6)]
    assert tc.prune_thumbnail_cache(cache, now=NOW, max_files=4) == 2
    assert [f.exists() for f in files] == [False, False, True, True, True, True]


def test_foreign_files_and_folders_untouched(cache):
    foreign = [
        _make(cache, "notes.txt", age_days=100),
        _make(cache, "photo.jpg", age_days=100),
        _make(cache, "x.jpeg", age_days=100),
        _make(cache, _name(1)[:-4] + ".png", age_days=100),
    ]
    sub = cache / "sub"
    sub.mkdir()
    inner = _make(sub, _name(2), age_days=100)
    tc.prune_thumbnail_cache(cache, now=NOW, max_files=0, max_bytes=0)
    assert all(f.exists() for f in foreign)
    assert inner.exists()


def test_refuses_a_directory_that_is_not_the_apps_own(tmp_path):
    other = tmp_path / "Documents"
    other.mkdir()
    f = _make(other, _name(1), age_days=100)
    assert tc.prune_thumbnail_cache(other, now=NOW) == 0
    assert f.exists()
    assert tc.prune_thumbnail_cache(tmp_path / "missing" / tc.CACHE_DIR_NAME, now=NOW) == 0


def test_stale_partial_copy_removed_but_recent_one_kept(cache):
    key = _name(1)[:-4]
    stale = _make(cache, f"{key}.0a0b0c0d.partial.jpg", age_days=0.1)  # 2.4 h
    live = cache / f"{key}.1a1b1c1d.partial.jpg"
    live.write_bytes(b"")
    os.utime(live, (NOW - 60, NOW - 60))
    tc.prune_thumbnail_cache(cache, now=NOW)
    assert not stale.exists() and live.exists()


def test_time_budget_stops_pruning(cache):
    for i in range(5):
        _make(cache, _name(i), age_days=30)
    ticks = iter(range(0, 1000))
    removed = tc.prune_thumbnail_cache(cache, now=NOW, clock=lambda: next(ticks), time_budget=2.5)
    assert 0 < removed < 5


def test_prune_once_per_session(cache, monkeypatch):
    calls = []
    monkeypatch.setattr(tc, "prune_thumbnail_cache", lambda d, **kw: calls.append(d))
    monkeypatch.setattr(tc, "_pruned_dirs", set())
    tc.prune_once_per_session(cache)
    tc.prune_once_per_session(cache)
    assert len(calls) == 1


@pytest.mark.parametrize("content,expected", [(b"", False), (b"\xff", False), (b"GIF89a", False), (JPEG, True)])
def test_is_valid_thumbnail(tmp_path, content, expected):
    p = tmp_path / "t.jpg"
    p.write_bytes(content)
    assert tc.is_valid_thumbnail(p) is expected


def test_is_valid_thumbnail_missing_file(tmp_path):
    assert tc.is_valid_thumbnail(tmp_path / "nope.jpg") is False


# --- VideoFile.get_thumbnail ----------------------------------------------

@pytest.fixture
def video(tmp_path, monkeypatch):
    d = tmp_path / tc.CACHE_DIR_NAME
    monkeypatch.setattr(vfmod, "THUMBNAIL_CACHE_DIR", d)
    monkeypatch.setattr(tc, "_pruned_dirs", set())
    p = tmp_path / "a.mp4"
    p.write_bytes(b"fake")
    return VideoFile(path=p), d


def test_failing_ffmpeg_leaves_nothing_in_the_cache(video, monkeypatch):
    vf, d = video

    def failing(src, out):
        Path(out).write_bytes(b"\xff\xd8 half a frame")  # ffmpeg died after writing some
        return False

    monkeypatch.setattr(vfmod, "extract_thumbnail", failing)
    assert vf.get_thumbnail() is None
    assert list(d.iterdir()) == []


def test_zero_byte_output_is_not_cached(video, monkeypatch):
    vf, d = video

    def empty(src, out):
        Path(out).write_bytes(b"")
        return True

    monkeypatch.setattr(vfmod, "extract_thumbnail", empty)
    assert vf.get_thumbnail() is None
    assert list(d.iterdir()) == []


def test_success_is_cached_and_reused(video, monkeypatch):
    vf, d = video
    calls = []

    def ok(src, out):
        calls.append(out)
        Path(out).write_bytes(JPEG)
        return True

    monkeypatch.setattr(vfmod, "extract_thumbnail", ok)
    first = vf.get_thumbnail()
    assert first is not None and first.read_bytes() == JPEG
    assert [p.name for p in d.iterdir()] == [first.name]  # no temp left behind
    vf2 = VideoFile(path=vf.path)
    assert vf2.get_thumbnail() == first
    assert len(calls) == 1


def test_zero_byte_cached_thumbnail_is_regenerated(video, monkeypatch):
    vf, d = video

    def ok(src, out):
        Path(out).write_bytes(JPEG)
        return True

    monkeypatch.setattr(vfmod, "extract_thumbnail", ok)
    good = vf.get_thumbnail()
    good.write_bytes(b"")  # corrupted on disk
    vf2 = VideoFile(path=vf.path)
    again = vf2.get_thumbnail()
    assert again == good and again.read_bytes() == JPEG


def test_failed_regeneration_keeps_previous_good_thumbnail(video, monkeypatch):
    vf, d = video
    monkeypatch.setattr(vfmod, "extract_thumbnail", lambda s, o: (Path(o).write_bytes(JPEG), True)[1])
    good = vf.get_thumbnail()
    monkeypatch.setattr(vfmod, "extract_thumbnail", lambda s, o: False)
    assert vf.get_thumbnail(force_regenerate=True) is None
    assert good.read_bytes() == JPEG


def test_first_use_prunes_the_cache(video, monkeypatch):
    vf, d = video
    d.mkdir()
    stale = d / _name(7)
    stale.write_bytes(JPEG)
    os.utime(stale, (1, 1))
    monkeypatch.setattr(vfmod, "extract_thumbnail", lambda s, o: (Path(o).write_bytes(JPEG), True)[1])
    vf.get_thumbnail()
    assert not stale.exists()
