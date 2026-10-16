"""Which sidecar files belong to a video (2026-10-16 review, finding 4). Renaming or moving Movie.mkv used to take
Movie.Extended.srt, which belongs to Movie.Extended.mkv: any word between the stem and .srt was accepted as a
'language'. The middle part of <stem>.<middle>.srt must now be a real language (the subtitle ingest's own table) plus
optional flags (forced / sdh / default ...), and a subtitle whose would-be stem is another video's stem is never taken."""

import os

import pytest
from redactor_common.core.move_plan import PlannedMove

from core import sidecars


def touch(folder, *names):
    for n in names:
        (folder / n).write_bytes(b"x")


def own(folder, video):
    return sorted(os.path.basename(old) for old, _new in sidecars.sidecar_pairs(str(folder / video), str(folder / "Renamed.mkv")))


def test_the_real_sidecar_shapes_belong_to_the_video(tmp_path):
    touch(tmp_path, "Movie.mkv", "Movie.srt", "Movie.en.srt", "Movie.en.forced.srt", "Movie.eng.sdh.srt", "Movie.pt-BR.srt",
          "Movie.English.srt", "Movie-poster.jpg", "Movie-poster.png")
    assert own(tmp_path, "Movie.mkv") == sorted([
        "Movie.srt", "Movie.en.srt", "Movie.en.forced.srt", "Movie.eng.sdh.srt", "Movie.pt-BR.srt", "Movie.English.srt",
        "Movie-poster.jpg", "Movie-poster.png"])


@pytest.mark.parametrize("other_video_present", [True, False])
def test_another_cuts_subtitle_is_never_taken(tmp_path, other_video_present):
    touch(tmp_path, "Movie.mkv", "Movie.srt", "Movie.Extended.srt", "Movie.Extended.en.srt", "Movie.Directors.Cut.srt")
    if other_video_present:
        touch(tmp_path, "Movie.Extended.mkv")
    assert own(tmp_path, "Movie.mkv") == ["Movie.srt"]
    if other_video_present:
        assert own(tmp_path, "Movie.Extended.mkv") == ["Movie.Extended.en.srt", "Movie.Extended.srt"]


def test_a_subtitle_named_like_another_videos_stem_belongs_to_that_video(tmp_path):
    """Movie.en.mkv is a video of its own, so Movie.en.srt and Movie.en.forced.srt are its subtitles, not Movie.mkv's."""
    touch(tmp_path, "Movie.mkv", "Movie.en.mkv", "Movie.srt", "Movie.en.srt", "Movie.en.forced.srt", "Movie.nl.srt")
    assert own(tmp_path, "Movie.mkv") == ["Movie.nl.srt", "Movie.srt"]
    assert own(tmp_path, "Movie.en.mkv") == ["Movie.en.forced.srt", "Movie.en.srt"]


def test_an_importable_container_counts_as_another_video_too(tmp_path):
    touch(tmp_path, "Movie.mkv", "Movie.fr.avi", "Movie.fr.srt")
    assert own(tmp_path, "Movie.mkv") == []


def test_a_longer_name_and_a_number_after_the_stem_are_not_sidecars(tmp_path):
    touch(tmp_path, "Show S01E01.mkv", "Show S01E010.srt", "Show S01E01 Pilot.srt", "Show S01E01.zz-top.srt")
    assert own(tmp_path, "Show S01E01.mkv") == []


def test_move_and_rename_take_only_the_own_sidecars(tmp_path):
    touch(tmp_path, "Movie.mkv", "Movie.en.srt", "Movie.Extended.srt", "Movie.Extended.mkv")
    pairs = sidecars.sidecar_pairs(str(tmp_path / "Movie.mkv"), str(tmp_path / "Other" / "New Name.mkv"))
    assert [(os.path.basename(a), os.path.basename(b)) for a, b in pairs] == [("Movie.en.srt", "New Name.en.srt")]
    move = PlannedMove(None, str(tmp_path / "Movie.mkv"), str(tmp_path / "Other" / "Movie.mkv"), [], "", False, str(tmp_path))
    assert [os.path.basename(m.old_path) for m in sidecars.sidecar_moves(move)] == ["Movie.en.srt"]
    both = sidecars.with_sidecars([move])
    assert [os.path.basename(m.old_path) for m in both] == ["Movie.mkv", "Movie.en.srt"]


def test_the_ingest_and_the_sidecars_agree_on_what_a_subtitle_is(tmp_path):
    """The names the subtitle ingest reads for a video are the names that travel with it (and vice versa)."""
    from core import subtitle_ingest as si

    touch(tmp_path, "Movie.mkv", "Movie.srt", "Movie.en.srt", "Movie.en.sdh.srt", "Movie.Extended.srt")
    video = str(tmp_path / "Movie.mkv")
    ingest = sorted(os.path.basename(s.path) for s in si.find_external_subs(video))
    assert ingest == sorted(os.path.basename(p) for p, _ in sidecars.sidecar_pairs(video, str(tmp_path / "X.mkv")))
