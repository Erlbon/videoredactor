"""Media > Find Duplicates... (core/video_duplicates.py; the review
dialog is covered by test_video_duplicates_dialog.py): grouping logic on hand-made hashes, and real ffmpeg-generated
clips -- the same picture re-encoded at another bitrate/resolution must
group together, a different picture must not."""

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

from core import video_duplicates as vd  # noqa: E402
from core.video_duplicates import Candidate, frame_hash, group_duplicates, hamming  # noqa: E402

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)


# --- Grouping logic -----------------------------------------------------------

def test_hamming_counts_differing_bits():
    assert hamming(0b1011, 0b1011) == 0
    assert hamming(0b1011, 0b0100) == 4


def test_groups_need_similar_duration_and_hash():
    a = Candidate("a", 100.0, 0xFFFF)
    b = Candidate("b", 101.0, 0xFFFF ^ 0b11)           # 2 bits off, 1 s longer: same video
    c = Candidate("c", 100.5, 0x0000_0000_FFFF_0000)   # same length, other picture
    d = Candidate("d", 130.0, 0xFFFF)                  # same picture, other length
    e = Candidate("e", None, 0xFFFF)                   # no duration: left out
    f = Candidate("f", 100.2, None)                    # unreadable: left out
    groups = group_duplicates([a, b, c, d, e, f])
    assert [[x.key for x in g] for g in groups] == [["a", "b"]]


def test_threshold_is_configurable_and_groups_chain():
    a = Candidate("a", 10.0, 0)
    b = Candidate("b", 10.0, 0b111111)   # 6 bits from a
    c = Candidate("c", 10.0, 0b111111111111)  # 6 bits from b, 12 from a
    assert [[x.key for x in g] for g in group_duplicates([a, b, c])] == [["a", "b", "c"]]  # A~B, B~C
    assert group_duplicates([a, b, c], threshold=5) == []


def test_only_files_with_a_same_length_neighbour_are_hashed():
    items = [Candidate("a", 100.0), Candidate("b", 101.5), Candidate("lonely", 500.0), Candidate("none", None)]
    seen = []
    vd.find_duplicates(items, lambda c: seen.append(c.key) or 0)
    assert seen == ["a", "b"]


def test_dhash_of_a_gradient():
    row_up = bytes(range(0, 9))        # brighter to the right: no bit set
    row_down = bytes(range(9, 0, -1))  # darker to the right: every bit set
    assert vd.hash_from_pixels(row_up * 8) == 0
    assert vd.hash_from_pixels(row_down * 8) == (1 << 64) - 1


# --- Real clips ---------------------------------------------------------------

def _make(dest: Path, source: str, *, size="320x240", extra=()):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{source}=size={size}:rate=10",
         "-t", "4", "-c:v", "libx264", "-preset", "ultrafast", *extra, str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _reencode(src: Path, dest: Path, *args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-c:v", "libx264", "-preset", "ultrafast", *args, str(dest)],
                   check=True, capture_output=True)
    return dest


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("dups")
    original = _make(d / "original.mp4", "testsrc")
    _reencode(original, d / "lowbitrate.mp4", "-b:v", "30k")
    _reencode(original, d / "small.mkv", "-vf", "scale=160:120")
    _make(d / "other.mp4", "mandelbrot")
    _make(d / "other_too.mp4", "smptebars")
    return d


@needs_ffmpeg
def test_reencoded_copies_group_and_a_different_clip_does_not(clips):
    names = ["original.mp4", "lowbitrate.mp4", "small.mkv", "other.mp4", "other_too.mp4"]
    items = [Candidate(n, 4.0) for n in names]
    groups = vd.find_duplicates(items, lambda c: frame_hash(str(clips / c.key), 4.0))
    assert [[c.key for c in g] for g in groups] == [["original.mp4", "lowbitrate.mp4", "small.mkv"]]


@needs_ffmpeg
def test_frame_falls_back_to_the_first_frame_when_the_seek_misses(clips):
    # A wrong (far too long) duration seeks past the end: no frame, so the first one is used.
    assert frame_hash(str(clips / "original.mp4"), 4000.0) is not None
    assert frame_hash(str(clips / "original.mp4"), None) is not None
    assert frame_hash(str(clips / "nothing.mp4"), 4.0) is None
