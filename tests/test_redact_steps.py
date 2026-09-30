"""The Redact button's steps (core/redact_steps.py) on redactor_common's
pipeline engine. Steps that only read names and metadata run on empty
files with mocked lookups; the ones that rewrite a video use clips
generated with ffmpeg (skipped when it isn't installed). MKVToolNix is not
needed: MKV files are only ever the SOURCE of a remux here. The Recycle Bin
is a fake folder."""

import os
import shutil
import subprocess
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from redactor_common.core.pipeline import FileStatus, Recipe, run_recipe_on_item  # noqa: E402
from redactor_common.core.rename_log import RenameLog  # noqa: E402
from redactor_common.core.scan_stamp import parse_stamp  # noqa: E402
from redactor_common.gui.redact_dialog import run_redact  # noqa: E402

from core import opensubtitles_client as osc  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core import tmdb_client, tvdb_client  # noqa: E402
from core.file_check import CheckResult, Finding, NOTE, mp4_index_at_end  # noqa: E402
from core.mp4_backend import read_mp4_metadata  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402

_app = QApplication.instance() or QApplication([])

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg/ffprobe not installed"
)

SOURCES = [
    "-f", "lavfi", "-i", "testsrc=duration=12:size=160x120:rate=10",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=12",
]
ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    d = tmp_path_factory.mktemp("redact_clips")
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", "-movflags", "+faststart", str(d / "good.mp4"))
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", str(d / "late_index.mp4"))
    _ffmpeg(*SOURCES, *ENCODE, "-metadata:s:a:0", "language=eng", str(d / "good.mkv"))
    return d


class Bin:
    """A fake Recycle Bin: moves the file into a folder."""

    def __init__(self, folder: Path):
        self.folder = folder
        folder.mkdir(exist_ok=True)

    def __call__(self, path: str) -> None:
        shutil.move(path, str(self.folder / f"{len(self.names()) + 1}-{os.path.basename(path)}"))

    def names(self) -> list[str]:
        return sorted(os.listdir(self.folder))


@pytest.fixture
def bin_(tmp_path):
    return Bin(tmp_path / "bin")


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "videos"
    folder.mkdir()
    return folder


def make_env(bin_, tmp_path, history=(), settings=None):
    settings = dict(settings or {})
    return rs.RedactEnv(
        trash=bin_,
        rename_log=RenameLog(str(tmp_path / "rename_log.json")),
        pattern_history=lambda: list(history),
        setting=lambda section, key, default="": settings.get((section, key), default),
    )


def only(*keys, options=None, threshold=0.9):
    """A recipe with just these steps on, plus their options."""
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in keys for s in catalogue}
    recipe.options = {k: dict(v) for k, v in (options or {}).items()}
    recipe.confidence_threshold = threshold
    return recipe, catalogue


def run_one(video, env, recipe, catalogue, finalize=True):
    resolved = recipe.resolve(catalogue)
    return run_recipe_on_item(
        video, resolved, recipe.confidence_threshold, lambda v: rs.VideoCtx(v, env),
        describe=lambda v: v.path.name, finalize=rs.finalize_file if finalize else None, finalize_label="Save",
    )


def stub(folder, name, **meta):
    """A loaded-looking VideoFile on an empty file (enough for steps that
    only read names and metadata)."""
    path = folder / name
    path.write_bytes(b"")
    return VideoFile(path=path, metadata=VideoMetadata(**meta))


def leftovers(folder):
    return [n for n in os.listdir(folder) if n.startswith(".") and ".redact-" in n]


# --- the recipe ------------------------------------------------------------------


def test_default_recipe_enabled_flags_and_pinned_last_steps():
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    assert recipe.enabled == {
        "check_repair": True, "filename_tags": True, "path_tags": True, "lookup": True, "subtitles": False,
        "remux_mkv_to_mp4": False, "rename": False, "move_into_folders": False,
    }
    assert recipe.order[-2:] == ["rename", "move_into_folders"]
    assert [s.key for s, _o in recipe.resolve(catalogue)] == ["check_repair", "filename_tags", "path_tags", "lookup"]
    # Rename starts on only once a rename pattern exists.
    assert Recipe.default_for(rs.build_catalogue(lambda: ["%title%"])).enabled["rename"] is True


def test_recipe_round_trips_through_the_settings_file_as_one_line():
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    recipe.enabled["subtitles"] = True
    recipe.options["move_into_folders"]["pattern"] = "%show_title%/%title%"
    recipe.options["subtitles"]["language"] = "nb"
    recipe.confidence_threshold = 0.8
    text = rs.recipe_to_setting(recipe)
    assert "\n" not in text
    rs.save_recipe(recipe)  # through core.config (the conftest points it at tmp_path)
    loaded = rs.load_recipe(catalogue)
    assert loaded.to_dict() == recipe.to_dict()
    assert loaded.options["move_into_folders"]["pattern"] == "%show_title%/%title%"
    assert rs.recipe_from_setting("not json {", catalogue).to_dict() == Recipe.default_for(catalogue).to_dict()
    assert rs.recipe_from_setting("", catalogue).to_dict() == Recipe.default_for(catalogue).to_dict()


def test_a_recipe_without_secrets():
    catalogue = rs.build_catalogue(lambda: [])
    text = rs.recipe_to_setting(Recipe.default_for(catalogue)).lower()
    assert "api_key" not in text and "password" not in text


# --- skipping ----------------------------------------------------------------------


@pytest.mark.parametrize("prepare, expected", [
    (lambda vf: setattr(vf, "load_error", "Could not read metadata: boom"), "could not be read"),
    (lambda vf: setattr(vf, "dirty", True), "unsaved edits"),
])
def test_unreadable_and_edited_files_are_skipped_with_a_note(work, bin_, tmp_path, prepare, expected):
    vf = stub(work, "a.mp4", title="x")
    prepare(vf)
    recipe, catalogue = only("check_repair", "filename_tags", "lookup")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.SKIPPED and expected in entry.skips[0]
    assert bin_.names() == [] and leftovers(work) == []


def test_a_file_whose_only_unsaved_change_is_a_scan_stamp_is_not_skipped(work, bin_, tmp_path):
    vf = stub(work, "a.mp4")
    vf.dirty, vf.stamp_only_dirty = True, True
    ctx = rs.VideoCtx(vf, make_env(bin_, tmp_path))
    assert ctx.skip_reason == ""


def test_skipped_files_show_up_in_the_report_text(work, bin_, tmp_path):
    vf = stub(work, "a.mp4")
    vf.load_error = "broken"
    recipe, catalogue = only("check_repair")
    report = run_redact(
        None, [vf], recipe, catalogue, lambda v: rs.VideoCtx(v, make_env(bin_, tmp_path)),
        describe=lambda v: v.path.name, show_results=False, finalize=rs.finalize_file, finalize_label="Save",
    )
    text = report.to_text()
    assert "SKIPPED" in text and "a.mp4" in text and "broken" in text
    assert report.count(FileStatus.SKIPPED) == 1


# --- check and repair ---------------------------------------------------------------


def test_a_tool_error_is_never_stamped_and_never_repaired(work, bin_, tmp_path, monkeypatch):
    vf = stub(work, "a.mp4")
    monkeypatch.setattr(rs, "_run", lambda *a, **k: None)  # ffprobe/ffmpeg missing or hung
    monkeypatch.setattr(rs, "build_repaired_copy", lambda *a, **k: pytest.fail("repaired after a tool error"))
    recipe, catalogue = only("check_repair")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.UNCHANGED
    assert any("not checked" in n for n in entry.notes)
    assert vf.metadata.scan_stamp == "" and not vf.dirty and vf.check is None
    assert bin_.names() == [] and leftovers(work) == []


def test_a_no_tool_finding_is_never_stamped(work, bin_, tmp_path, monkeypatch):
    vf = stub(work, "a.mp4")
    monkeypatch.setattr(rs, "quick_check", lambda *a, **k: CheckResult(
        findings=[Finding("no_tool", NOTE, "Not checked: ffprobe isn't available")], openable=False))
    recipe, catalogue = only("check_repair")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.UNCHANGED and vf.metadata.scan_stamp == ""


@needs_ffmpeg
def test_a_repairable_file_is_repaired_stamped_and_saved_in_place(clips, work, bin_, tmp_path):
    path = work / "late.mp4"
    shutil.copy2(clips / "late_index.mp4", path)
    assert mp4_index_at_end(str(path))
    vf = VideoFile(path=path)
    vf.load()
    recipe, catalogue = only("check_repair")
    report = run_redact(
        None, [vf], recipe, catalogue, lambda v: rs.VideoCtx(v, make_env(bin_, tmp_path)),
        describe=lambda v: v.path.name, show_results=False, finalize=rs.finalize_file, finalize_label="Save",
    )
    entry = report.entries[0]
    assert entry.status is FileStatus.CHANGED, report.to_text()
    assert any("repaired losslessly" in a for a in entry.applied)
    assert any(a.startswith("Save: saved in place") for a in entry.applied)
    assert not mp4_index_at_end(str(path))  # the index moved to the front
    assert len(bin_.names()) == 1 and "late.redact-orig.mp4" in bin_.names()[0]  # the original, in the bin
    assert leftovers(work) == [] and sorted(os.listdir(work)) == ["late.mp4"]
    stamp = parse_stamp(read_mp4_metadata(str(path)).scan_stamp)  # written inside the file
    assert stamp is not None and stamp.status == "CHECKED OK" and stamp.fingerprint
    assert vf.dirty is False and vf.scan_status() == "CHECKED OK" and vf.stamp_stale is False


@needs_ffmpeg
def test_repair_off_only_reports_and_stamps(clips, work, bin_, tmp_path):
    path = work / "late.mp4"
    shutil.copy2(clips / "late_index.mp4", path)
    vf = VideoFile(path=path)
    vf.load()
    recipe, catalogue = only("check_repair", options={"check_repair": {"repair": False}})
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.CHANGED and mp4_index_at_end(str(path))
    assert any("repair is off" in n for n in entry.notes)
    assert parse_stamp(read_mp4_metadata(str(path)).scan_stamp).status == "REPAIRABLE"


@needs_ffmpeg
def test_a_damaged_file_is_reported_not_repaired(clips, work, bin_, tmp_path):
    vf = stub(work, "cut.mp4")
    data = (clips / "good.mp4").read_bytes()
    vf.path.write_bytes(data[: int(len(data) * 0.55)])  # faststart: the index is there, the media is cut
    recipe, catalogue = only("check_repair")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue, finalize=False)
    assert any(n.startswith("check_repair") or "DAMAGED" in n for n in entry.notes)
    assert any("not repaired automatically" in n for n in entry.notes)
    assert bin_.names() == []


@needs_ffmpeg
def test_a_second_run_changes_nothing_and_does_not_touch_the_file(clips, work, bin_, tmp_path):
    path = work / "good.mp4"
    shutil.copy2(clips / "good.mp4", path)
    vf = VideoFile(path=path)
    vf.load()
    recipe, catalogue = only("check_repair")
    env = make_env(bin_, tmp_path)
    assert run_one(vf, env, recipe, catalogue).status is FileStatus.CHANGED  # first stamp
    before = path.read_bytes()
    second = run_one(vf, env, recipe, catalogue)
    assert second.status is FileStatus.UNCHANGED and path.read_bytes() == before
    assert len(bin_.names()) == 1  # only the first run's original


# --- filename tags ---------------------------------------------------------------------


PATTERN = "%show_title% S%season_number%E%episode_number% %title%"


def test_filename_pattern_match_fills_only_empty_fields(work, bin_, tmp_path):
    vf = stub(work, "The Office S02E05 Halloween.mp4", title="Kept Title")
    recipe, catalogue = only("filename_tags")
    env = make_env(bin_, tmp_path, history=[PATTERN])
    resolved = recipe.resolve(catalogue)
    entry = run_recipe_on_item(vf, resolved, 0.9, lambda v: rs.VideoCtx(v, env), lambda v: v.path.name)
    assert entry.status is FileStatus.CHANGED and entry.review == []
    assert any("auto-applied at 95%" in a for a in entry.applied)
    # A working copy was filled; the live row is only updated by the save.
    ctx = rs.VideoCtx(vf, env)
    step = resolved[0][0]
    ctx.step_options = {"pattern": ""}
    result = step.run(ctx)
    assert result.value.fields == {"show_title": "The Office", "season_number": "2", "episode_number": "5"}
    step.apply_suggestion(ctx, result)
    md = ctx.work.metadata
    assert (md.show_title, md.season_number, md.episode_number, md.title) == ("The Office", 2, 5, "Kept Title")
    assert vf.metadata.show_title == ""  # live row untouched until the save


def test_release_name_guess_goes_to_needs_review_not_applied(work, bin_, tmp_path):
    vf = stub(work, "Some.Show.S02E05.1080p.WEB.mp4")
    recipe, catalogue = only("filename_tags")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)  # no saved pattern
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == []
    assert entry.review[0].confidence < 0.9 and "release-name" in entry.review[0].reason
    assert "season_number='2'" in str(entry.review[0].value)
    assert bin_.names() == []


def test_a_lowered_threshold_applies_the_release_name_guess(work, bin_, tmp_path):
    vf = stub(work, "Some.Show.S02E05.1080p.WEB.mp4")
    recipe, catalogue = only("filename_tags", threshold=0.5)
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue, finalize=False)
    assert entry.status is FileStatus.CHANGED and entry.review == []


@needs_ffmpeg
def test_filled_tags_are_written_verified_and_the_original_trashed(clips, work, bin_, tmp_path):
    path = work / "The Office S02E05 Halloween.mp4"
    shutil.copy2(clips / "good.mp4", path)
    vf = VideoFile(path=path)
    vf.load()
    recipe, catalogue = only("filename_tags")
    entry = run_one(vf, make_env(bin_, tmp_path, history=[PATTERN]), recipe, catalogue)
    assert entry.status is FileStatus.CHANGED, (entry.failures, entry.notes)
    md = read_mp4_metadata(str(path))
    assert (md.show_title, md.season_number, md.episode_number, md.title) == ("The Office", 2, 5, "Halloween")
    assert len(bin_.names()) == 1 and leftovers(work) == []
    assert vf.metadata.show_title == "The Office" and not vf.dirty  # the live row was reloaded from disk


@needs_ffmpeg
def test_a_save_that_fails_verification_leaves_the_original_untouched(clips, work, bin_, tmp_path, monkeypatch):
    path = work / "The Office S02E05 Halloween.mp4"
    shutil.copy2(clips / "good.mp4", path)
    before = path.read_bytes()
    vf = VideoFile(path=path)
    vf.load()
    monkeypatch.setattr(rs, "verify_remux", lambda *a: "1 audio track(s) missing")
    recipe, catalogue = only("filename_tags")
    entry = run_one(vf, make_env(bin_, tmp_path, history=[PATTERN]), recipe, catalogue)
    assert entry.status is FileStatus.FAILED
    assert "NOT SAVED" in entry.failures[0] and "audio track" in entry.failures[0]
    assert path.read_bytes() == before and bin_.names() == [] and leftovers(work) == []
    assert vf.metadata.show_title == ""  # the live row did not take the unsaved guess


# --- folder path tags ------------------------------------------------------------------


def path_env(bin_, tmp_path, library, history=()):
    settings = {("rename", "library_root"): str(library)} if library else None
    return make_env(bin_, tmp_path, history=history, settings=settings)


def path_run(video, env, threshold=0.9):
    recipe, catalogue = only("path_tags", threshold=threshold)
    return run_one(video, env, recipe, catalogue, finalize=False)


def test_path_tags_order_and_recipe_round_trip():
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    keys = [s.key for s, _o in recipe.resolve(catalogue)]
    assert keys.index("filename_tags") + 1 == keys.index("path_tags") == keys.index("lookup") - 1
    recipe.options["path_tags"] = {"pattern": "%show_title%/%title%"}
    recipe.enabled["path_tags"] = False
    back = rs.recipe_from_setting(rs.recipe_to_setting(recipe), catalogue)
    assert back.enabled["path_tags"] is False and back.options["path_tags"] == {"pattern": "%show_title%/%title%"}
    # A recipe saved before the step existed gets it, on, after the filename step.
    old = Recipe.from_json('{"order":["check_repair","filename_tags","lookup"],"enabled":{"lookup":false}}')
    assert [s.key for s, _o in old.resolve(catalogue)] == ["check_repair", "filename_tags", "path_tags"]


def test_path_tags_fill_a_season_folder_path_and_strip_the_zero(work, bin_, tmp_path):
    library = tmp_path / "library"
    folder = library / "The Office" / "Season 02"
    folder.mkdir(parents=True)
    vf = stub(folder, "Halloween.mp4")
    recipe, catalogue = only("path_tags")
    env = path_env(bin_, tmp_path, library)
    step = recipe.resolve(catalogue)[0][0]
    ctx = rs.VideoCtx(vf, env)
    ctx.step_options = {"pattern": ""}
    result = step.run(ctx)
    assert result.value.fields == {"show_title": "The Office", "season_number": "2", "title": "Halloween"}
    assert result.confidence >= 0.9
    step.apply_suggestion(ctx, result)
    md = ctx.work.metadata
    assert (md.show_title, md.season_number, md.title) == ("The Office", 2, "Halloween")
    assert vf.metadata.show_title == ""  # the live row waits for the save
    entry = path_run(vf, env)
    assert entry.status is FileStatus.CHANGED and entry.review == []


def test_path_tags_season_zero_and_specials(work, bin_, tmp_path):
    library = tmp_path / "library"
    (library / "Show" / "Season 0").mkdir(parents=True)
    (library / "Show" / "Specials").mkdir(parents=True)
    env = path_env(bin_, tmp_path, library)
    season0 = stub(library / "Show" / "Season 0", "Pilot.mp4")
    assert path_run(season0, env).status is FileStatus.CHANGED  # "Season 0" is season 0, like TMDB's specials
    specials = stub(library / "Show" / "Specials", "Pilot.mp4")
    entry = path_run(specials, env)
    # 'Specials' does not match 'Season %season_number%': reviewed, not applied, season stays empty.
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == []
    assert entry.review[0].confidence < 0.9 and "Season %season_number%" in entry.review[0].reason
    assert "season_number" not in entry.review[0].value.fields
    assert path_run(specials, env, threshold=0.5).status is FileStatus.CHANGED


def test_path_tags_fill_only_empty_fields(work, bin_, tmp_path):
    library = tmp_path / "library"
    folder = library / "The Office" / "Season 02"
    folder.mkdir(parents=True)
    vf = stub(folder, "Halloween.mp4", show_title="Kept Show", title="Kept Title", season_number=7)
    entry = path_run(vf, path_env(bin_, tmp_path, library))
    assert entry.status is FileStatus.UNCHANGED and entry.applied == [] and entry.review == []
    vf = stub(folder, "Other.mp4", show_title="Kept Show")
    recipe, catalogue = only("path_tags")
    ctx = rs.VideoCtx(vf, path_env(bin_, tmp_path, library))
    ctx.step_options = {"pattern": ""}
    result = recipe.resolve(catalogue)[0][0].run(ctx)
    assert result.value.fields == {"season_number": "2", "title": "Other"}


def test_path_tags_without_a_root_or_outside_it_do_nothing(work, bin_, tmp_path):
    library = tmp_path / "library"
    (library / "Show" / "Season 1").mkdir(parents=True)
    vf = stub(work, "a.mp4")
    entry = path_run(vf, path_env(bin_, tmp_path, None))
    assert entry.status is FileStatus.UNCHANGED and any("no library root" in n for n in entry.notes)
    entry = path_run(vf, path_env(bin_, tmp_path, library))  # work/ is not under library/
    assert entry.status is FileStatus.UNCHANGED and entry.notes == []


def test_path_tags_pattern_comes_from_the_option_then_the_history(work, bin_, tmp_path):
    library = tmp_path / "library"
    (library / "Show").mkdir(parents=True)
    vf = stub(library / "Show", "Pilot.mp4")
    # A filename pattern in the history is never used as a path pattern.
    env = path_env(bin_, tmp_path, library, history=["%title%", "%show_title%/%title%"])
    entry = path_run(vf, env)  # a bare %show_title% folder is only 87% sure: reviewed
    assert entry.status is FileStatus.NEEDS_REVIEW and "'%show_title%/%title%'" in entry.review[0].reason
    assert rs.latest_path_pattern(["%title%", "%show_title%/%title%", "a/b"]) == "%show_title%/%title%"
    assert rs.latest_path_pattern(["%title%"]) == ""
    recipe, catalogue = only("path_tags", options={"path_tags": {"pattern": "%title%"}})
    entry = run_one(vf, env, recipe, catalogue, finalize=False)
    assert any("not a path pattern" in n for n in entry.notes)


# --- lookup ------------------------------------------------------------------------------


def fake_tmdb(monkeypatch, movies=(), shows=(), episodes=None):
    monkeypatch.setattr(tmdb_client, "search_movies", lambda title, year=None: list(movies))
    monkeypatch.setattr(tmdb_client, "search_tv", lambda title: list(shows))
    monkeypatch.setattr(tmdb_client, "get_movie_details", lambda i: {
        "title": "Inception", "description": "Dreams.", "genre_tags": "Sci-Fi", "release_date": "2010-07-16",
        "language": "en", "director": "Nolan", "cast": "DiCaprio", "studio": "WB", "_poster_path": "/x.jpg",
    })
    monkeypatch.setattr(tmdb_client, "get_tv_show_details", lambda i: {
        "show_title": "The Office", "description": "Show.", "genre_tags": "Comedy",
        "release_date": "2005-03-24", "network": "NBC", "_poster_path": None,
    })

    def episode(i, season, number):
        if episodes is None:
            raise tmdb_client.TMDBError("episode not found")
        return dict(episodes, season_number=season, episode_number=number)

    monkeypatch.setattr(tmdb_client, "get_tv_episode_details", episode)


def movie(title, year, tmdb_id=1):
    return tmdb_client.MovieCandidate(tmdb_id, title, f"{year}-01-01", "", None)


def show(name, year, tmdb_id=7):
    return tmdb_client.TVCandidate(tmdb_id, name, f"{year}-01-01", "", None)


def lookup_entry(vf, env, **kw):
    recipe, catalogue = only("lookup", **kw)
    return run_one(vf, env, recipe, catalogue, finalize=False)


def test_a_movie_with_exact_title_and_year_is_applied(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    vf = stub(work, "Inception.2010.1080p.BluRay.mp4", title="")
    entry = lookup_entry(vf, make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED and entry.review == []
    text = "\n".join(entry.applied)
    assert "auto-applied at 95%" in text and "director = 'Nolan'" in text and "content_type = 'Movie'" in text


def test_a_movie_title_match_without_year_goes_to_review(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    vf = stub(work, "Inception.mp4")  # parse_release_name: unknown kind (no year)
    entry = lookup_entry(vf, make_env(bin_, tmp_path))
    assert entry.status is FileStatus.UNCHANGED  # not even a movie guess: nothing searched
    vf = stub(work, "Inception 1080p.mp4")
    assert lookup_entry(vf, make_env(bin_, tmp_path)).status is FileStatus.UNCHANGED


def test_a_movie_with_the_wrong_year_or_a_different_title_needs_review(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2011)])
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == []
    assert "not the year 2010" in entry.review[0].reason and entry.review[0].confidence < 0.9
    fake_tmdb(monkeypatch, movies=[movie("Interception", 2010)])
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.review[0].confidence <= 0.4


def test_two_movies_matching_title_and_year_are_ambiguous(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010, 1), movie("Inception", 2010, 2)])
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW


def test_lookup_fills_only_empty_fields(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, movies=[movie("Inception", 2010)])
    vf = stub(work, "Inception.2010.mp4", director="Mine", content_type=ContentType.CLIP)
    env = make_env(bin_, tmp_path)
    recipe, catalogue = only("lookup")
    ctx = rs.VideoCtx(vf, env)
    step = rs.LookupStep()
    result = step.run(ctx)
    step.apply_suggestion(ctx, result)
    md = ctx.work.metadata
    assert md.director == "Mine" and md.content_type is ContentType.CLIP and md.studio == "WB"


def test_a_complete_file_makes_no_network_call(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setattr(tmdb_client, "search_movies", lambda *a: pytest.fail("searched"))
    vf = stub(work, "Inception.2010.mp4", title="t", description="d", genre_tags="g", release_date="r",
              language="en", director="d", cast="c", studio="s")
    assert lookup_entry(vf, make_env(bin_, tmp_path)).status is FileStatus.UNCHANGED


def test_no_api_key_is_nothing_with_a_note(work, bin_, tmp_path):
    # No mocking: the conftest's keyring is empty, so the real client refuses.
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("No TMDB API key" in n for n in entry.notes)


def test_offline_is_nothing_with_a_note(work, bin_, tmp_path, monkeypatch):
    def offline(*a, **k):
        raise tmdb_client.TMDBError("Couldn't reach TMDB (timed out).")

    monkeypatch.setattr(tmdb_client, "search_movies", offline)
    entry = lookup_entry(stub(work, "Inception.2010.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("unavailable" in n and "timed out" in n for n in entry.notes)


def test_a_tv_show_title_match_needs_review_unless_the_year_agrees(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, shows=[show("The Office", 2005)], episodes={"title": "Halloween", "description": "Ep."})
    entry = lookup_entry(stub(work, "The Office S02E05.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW and entry.applied == []
    assert "title matches" in entry.review[0].reason
    entry = lookup_entry(stub(work, "The Office (2005) S02E05.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED and entry.review == []
    text = "\n".join(entry.applied)
    assert "title = 'Halloween'" in text and "network = 'NBC'" in text and "content_type = 'TV'" in text
    # The episode's own description wins over the show's.
    assert "description = 'Ep.'" in text


def test_a_missing_episode_is_noted_and_the_show_level_fields_still_fill(work, bin_, tmp_path, monkeypatch):
    fake_tmdb(monkeypatch, shows=[show("The Office", 2005)], episodes=None)
    entry = lookup_entry(stub(work, "The Office (2005) S09E99.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED
    assert any("no S09E99" in n for n in entry.notes)


def test_thetvdb_is_the_fallback_when_tmdb_fails(work, bin_, tmp_path, monkeypatch):
    def no_key(*a, **k):
        raise tmdb_client.TMDBError("No TMDB API key configured.")

    monkeypatch.setattr(tmdb_client, "search_tv", no_key)
    monkeypatch.setattr(tvdb_client, "search_series", lambda title: [
        tvdb_client.SeriesCandidate(9, "The Office", "2005-03-24", "", None)])
    monkeypatch.setattr(tvdb_client, "get_series_details", lambda i: {
        "show_title": "The Office", "description": "", "genre_tags": "Comedy", "release_date": "",
        "network": "NBC", "_poster_path": None})
    monkeypatch.setattr(tvdb_client, "get_episode_details", lambda i, s, e: {
        "title": "Halloween", "description": "", "release_date": "2005-10-25", "season_number": s,
        "episode_number": e})
    entry = lookup_entry(stub(work, "The Office (2005) S02E05.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED and "TheTVDB" in "\n".join(entry.applied)
    assert any("No TMDB API key" in n for n in entry.notes)


# --- subtitles ----------------------------------------------------------------------------


def candidate(hash_matched):
    return osc.SubtitleCandidate(file_id=5, language="en", release_name="Some.Movie", download_count=3,
                                 hash_matched=hash_matched)


def subtitle_entry(vf, env, **kw):
    recipe, catalogue = only("subtitles", **kw)
    return run_one(vf, env, recipe, catalogue, finalize=False)


def test_subtitles_are_off_by_default():
    assert rs.SubtitlesStep().default_enabled is False
    assert Recipe.default_for(rs.build_catalogue(lambda: [])).enabled["subtitles"] is False


def test_a_hash_matched_subtitle_is_downloaded_beside_the_video(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setattr(osc, "search_by_hash", lambda path, lang: [candidate(True)])
    monkeypatch.setattr(osc, "download_subtitle_text", lambda file_id: "1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    vf = stub(work, "Some Movie 2020.mp4")
    entry = subtitle_entry(vf, make_env(bin_, tmp_path))
    assert entry.status is FileStatus.CHANGED and "auto-applied at 95%" in entry.applied[0]
    assert (work / "Some Movie 2020.en.srt").read_text().startswith("1")


def test_a_title_matched_subtitle_needs_review_and_writes_nothing(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setattr(osc, "search_by_hash", lambda path, lang: [])
    monkeypatch.setattr(osc, "search_by_title", lambda query, lang: [candidate(False)])
    monkeypatch.setattr(osc, "download_subtitle_text", lambda file_id: pytest.fail("downloaded"))
    vf = stub(work, "Some Movie 2020.mp4")
    entry = subtitle_entry(vf, make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW and "sync is not guaranteed" in entry.review[0].reason
    assert sorted(os.listdir(work)) == ["Some Movie 2020.mp4"]


def test_an_existing_subtitle_is_left_alone(work, bin_, tmp_path, monkeypatch):
    monkeypatch.setattr(osc, "search_by_hash", lambda *a: pytest.fail("searched"))
    vf = stub(work, "Some Movie 2020.mp4")
    (work / "Some Movie 2020.nb.srt").write_text("x")
    assert subtitle_entry(vf, make_env(bin_, tmp_path)).status is FileStatus.UNCHANGED


def test_subtitles_without_a_key_are_nothing_with_a_note(work, bin_, tmp_path):
    entry = subtitle_entry(stub(work, "Some Movie 2020.mp4"), make_env(bin_, tmp_path))
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("unavailable" in n for n in entry.notes)


# --- remux ---------------------------------------------------------------------------------


@needs_ffmpeg
def test_remux_turns_an_mkv_into_a_verified_mp4_with_the_tags(clips, work, bin_, tmp_path):
    path = work / "Film.mkv"
    shutil.copy2(clips / "good.mkv", path)
    vf = VideoFile(path=path, metadata=VideoMetadata(title="Film", director="Me"))  # as if loaded
    recipe, catalogue = only("remux_mkv_to_mp4")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.CHANGED, (entry.failures, entry.notes)
    assert not path.exists() and (work / "Film.mp4").exists()
    assert vf.path == work / "Film.mp4" and not vf.load_error
    md = read_mp4_metadata(str(work / "Film.mp4"))
    assert md.title == "Film" and md.director == "Me"
    assert "Film.redact-orig.mkv" in bin_.names()[0] and leftovers(work) == []


@needs_ffmpeg
def test_remux_never_proceeds_when_verification_finds_a_missing_track(clips, work, bin_, tmp_path, monkeypatch):
    path = work / "Film.mkv"
    shutil.copy2(clips / "good.mkv", path)
    before = path.read_bytes()
    vf = VideoFile(path=path, metadata=VideoMetadata(title="Film"))
    monkeypatch.setattr(rs, "verify_remux", lambda *a: "1 subtitle track(s) missing")
    recipe, catalogue = only("remux_mkv_to_mp4")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.FAILED and "subtitle track" in entry.failures[0]
    assert path.read_bytes() == before and sorted(os.listdir(work)) == ["Film.mkv"] and bin_.names() == []


@needs_ffmpeg
def test_remux_leaves_a_file_alone_when_the_mp4_name_is_taken(clips, work, bin_, tmp_path):
    path = work / "Film.mkv"
    shutil.copy2(clips / "good.mkv", path)
    (work / "Film.mp4").write_bytes(b"other")
    vf = VideoFile(path=path, metadata=VideoMetadata(title="Film"))
    recipe, catalogue = only("remux_mkv_to_mp4")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.UNCHANGED and any("already exists" in n for n in entry.notes)
    assert path.exists() and bin_.names() == []


def test_remux_ignores_an_mp4(work, bin_, tmp_path):
    recipe, catalogue = only("remux_mkv_to_mp4")
    assert run_one(stub(work, "a.mp4"), make_env(bin_, tmp_path), recipe, catalogue).status is FileStatus.UNCHANGED


# --- rename and move -------------------------------------------------------------------------


def with_sidecars(work, stem):
    for suffix in ("-poster.jpg", ".en.srt", ".srt"):
        (work / f"{stem}{suffix}").write_text("x")
    (work / f"{stem}0.en.srt").write_text("another video's")  # must stay put


def test_rename_renames_the_video_and_its_sidecars_and_logs_it(work, bin_, tmp_path):
    vf = stub(work, "a.mp4", show_title="Show", title="Pilot")
    with_sidecars(work, "a")
    env = make_env(bin_, tmp_path)
    recipe, catalogue = only("rename", options={"rename": {"pattern": "%show_title% - %title%"}})
    entry = run_one(vf, env, recipe, catalogue)
    assert entry.status is FileStatus.CHANGED and "+3 sidecar" in entry.applied[0]
    assert vf.path == work / "Show - Pilot.mp4"
    assert sorted(os.listdir(work)) == sorted([
        "Show - Pilot.mp4", "Show - Pilot-poster.jpg", "Show - Pilot.en.srt", "Show - Pilot.srt", "a0.en.srt",
    ])
    batch = env.rename_log.last_batch()
    assert batch.label == "Redact: rename" and len(batch.renames) == 4
    env.rename_log.undo_last()  # one undo restores the video and its sidecars together
    assert (work / "a.mp4").exists() and (work / "a-poster.jpg").exists()


def test_rename_uses_the_most_recent_saved_pattern_and_leaves_a_hole_alone(work, bin_, tmp_path):
    recipe, catalogue = only("rename")
    vf = stub(work, "a.mp4", show_title="Show", title="Pilot")
    entry = run_one(vf, make_env(bin_, tmp_path, history=["%show_title% - %title%"]), recipe, catalogue)
    assert vf.path.name == "Show - Pilot.mp4" and entry.status is FileStatus.CHANGED
    vf = stub(work, "b.mp4", title="Pilot")  # %show_title% is empty: no "- Pilot" files
    entry = run_one(vf, make_env(bin_, tmp_path, history=["%show_title% - %title%"]), recipe, catalogue)
    assert vf.path.name == "b.mp4" and any("empty %show_title%" in n for n in entry.notes)
    vf = stub(work, "c.mp4", title="Pilot")  # an optional group may be empty
    run_one(vf, make_env(bin_, tmp_path, history=["%title%[ - %show_title%]"]), recipe, catalogue)
    assert vf.path.name == "Pilot.mp4"


def test_rename_without_any_pattern_is_nothing_with_a_note(work, bin_, tmp_path):
    recipe, catalogue = only("rename")
    entry = run_one(stub(work, "a.mp4", title="x"), make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.UNCHANGED and any("no rename pattern" in n for n in entry.notes)


def test_rename_applies_the_saved_zero_padding(work, bin_, tmp_path):
    vf = stub(work, "a.mp4", show_title="Show", episode_number=3)
    env = make_env(bin_, tmp_path, settings={("rename", "zero_pad", "0"): "1", ("rename", "zero_pad_width", "2"): "3"})
    env.setting = lambda s, k, d="": {"zero_pad": "1", "zero_pad_width": "3"}.get(k, d)
    recipe, catalogue = only("rename", options={"rename": {"pattern": "%show_title% E%episode_number%"}})
    run_one(vf, env, recipe, catalogue)
    assert vf.path.name == "Show E003.mp4"


def move_recipe(pattern=None):
    options = {"move_into_folders": {"pattern": pattern}} if pattern else None
    return only("move_into_folders", options=options)


def test_move_files_into_folders_with_sidecars_and_undoes_as_one_batch(work, bin_, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    vf = stub(work, "a.mp4", show_title="The Office", season_number=2, title="Halloween")
    with_sidecars(work, "a")
    env = make_env(bin_, tmp_path, settings={("rename", "library_root"): str(library)})
    recipe, catalogue = move_recipe()
    entry = run_one(vf, env, recipe, catalogue)
    assert entry.status is FileStatus.CHANGED, (entry.failures, entry.notes)
    folder = library / "The Office" / "Season 2"
    assert vf.path == folder / "Halloween.mp4"
    assert sorted(os.listdir(folder)) == ["Halloween-poster.jpg", "Halloween.en.srt", "Halloween.mp4", "Halloween.srt"]
    assert sorted(os.listdir(work)) == ["a0.en.srt"]
    batch = env.rename_log.last_batch()
    assert batch.root == str(library) and len(batch.renames) == 4
    assert [os.path.basename(d) for d in batch.created_dirs] == ["The Office", "Season 2"]
    result = env.rename_log.undo_last()
    assert len(result.restored) == 4 and (work / "a.mp4").exists() and (work / "a.srt").exists()


def test_move_never_overwrites_it_numbers_the_name(work, bin_, tmp_path):
    library = tmp_path / "library"
    (library / "Show" / "Season 1").mkdir(parents=True)
    (library / "Show" / "Season 1" / "Pilot.mp4").write_bytes(b"existing")
    vf = stub(work, "a.mp4", show_title="Show", season_number=1, title="Pilot")
    env = make_env(bin_, tmp_path, settings={("rename", "library_root"): str(library)})
    recipe, catalogue = move_recipe()
    run_one(vf, env, recipe, catalogue)
    assert vf.path.name == "Pilot (2).mp4"
    assert (library / "Show" / "Season 1" / "Pilot.mp4").read_bytes() == b"existing"


def test_move_needs_a_library_root_and_filled_fields(work, bin_, tmp_path):
    recipe, catalogue = move_recipe()
    vf = stub(work, "a.mp4", show_title="Show", season_number=1, title="Pilot")
    entry = run_one(vf, make_env(bin_, tmp_path), recipe, catalogue)
    assert entry.status is FileStatus.UNCHANGED and any("no library root" in n for n in entry.notes)
    missing_root = make_env(bin_, tmp_path, settings={("rename", "library_root"): str(tmp_path / "nope")})
    entry = run_one(vf, missing_root, recipe, catalogue)
    assert any("library root folder doesn't exist" in n for n in entry.notes) and vf.path.name == "a.mp4"
    library = tmp_path / "lib"
    library.mkdir()
    env = make_env(bin_, tmp_path, settings={("rename", "library_root"): str(library)})
    movie_file = stub(work, "m.mp4", title="A Movie")  # no show/season: not filed under "Unknown"
    entry = run_one(movie_file, env, recipe, catalogue)
    assert any("empty %show_title%" in n for n in entry.notes) and os.listdir(library) == []


def test_rename_then_move_run_in_that_order_after_the_save(work, bin_, tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    vf = stub(work, "a.mp4", show_title="Show", season_number=1, title="Pilot")
    env = make_env(bin_, tmp_path, settings={("rename", "library_root"): str(library)})
    catalogue = rs.build_catalogue(lambda: [])
    recipe = Recipe.default_for(catalogue)
    recipe.enabled = {s.key: s.key in ("rename", "move_into_folders") for s in catalogue}
    recipe.order = ["move_into_folders", "rename"] + [k for k in recipe.order if k not in ("rename", "move_into_folders")]
    recipe.options = {"rename": {"pattern": "%show_title% S%season_number% %title%"},
                      "move_into_folders": {"pattern": "%show_title%/%title%"}}
    order = [s.key for s, _o in recipe.resolve(catalogue)]
    assert order == ["move_into_folders", "rename"]  # hand-edited order: the editor/engine only pin "last" vs normal
    recipe.order = [k for k in rs.Recipe.default_for(catalogue).order]
    entry = run_one(vf, env, recipe, catalogue)
    assert [a.split(":")[0] for a in entry.applied] == ["Rename by pattern", "Move into folders under the library root"]
    assert vf.path == library / "Show" / "Pilot.mp4"  # the move's own pattern has the last word on the name


# --- the whole button ---------------------------------------------------------------------------


@needs_ffmpeg
def test_end_to_end_run_redact_with_a_fake_bin(clips, work, bin_, tmp_path):
    good = work / "The Office S02E05 Halloween.mp4"
    late = work / "late.mp4"
    shutil.copy2(clips / "good.mp4", good)
    shutil.copy2(clips / "late_index.mp4", late)
    unsaved = work / "unsaved.mp4"
    shutil.copy2(clips / "good.mp4", unsaved)
    files = []
    for p in (good, late, unsaved):
        vf = VideoFile(path=p)
        vf.load()
        files.append(vf)
    files[2].dirty = True
    env = make_env(bin_, tmp_path, history=[PATTERN])
    catalogue = rs.build_catalogue(env.pattern_history)
    recipe = Recipe.default_for(catalogue)
    recipe.enabled["lookup"] = False  # no network in tests
    report = run_redact(
        None, files, recipe, catalogue, lambda v: rs.VideoCtx(v, env), describe=lambda v: v.path.name,
        show_results=False, finalize=rs.finalize_file, finalize_label="Save",
    )
    assert report.count(FileStatus.CHANGED) == 2 and report.count(FileStatus.SKIPPED) == 1, report.to_text()
    by_name = {e.file: e for e in report.entries}
    assert "unsaved edits" in by_name["unsaved.mp4"].skips[0]
    md = read_mp4_metadata(str(good))
    assert (md.show_title, md.title) == ("The Office", "Halloween")
    assert parse_stamp(md.scan_stamp).status == "CHECKED OK"
    assert not mp4_index_at_end(str(late))
    assert len(bin_.names()) == 2 and leftovers(work) == []
    assert files[2].dirty  # the skipped file keeps its unsaved edits, untouched
    text = report.to_text()
    assert "CHANGES" in text and "Save: saved in place" in text and "SKIPPED" in text
