"""
core/redact_steps.py

The steps behind the "Redact" button (redactor_common's pipeline engine,
core/pipeline.py): run a recipe on each selected/loaded video with no
operator input and leave a corrected file IN PLACE, the original in the
Recycle Bin. Qt-free; gui/main_window.py wires it to the menu/toolbar.

Every step wraps code the app already has (file_check, ffmpeg_backend,
the TMDB/TheTVDB/OpenSubtitles clients, the filename parser, the rename
pattern) without the dialogs. How one file flows:

  1. VideoCtx makes a WORKING copy of the metadata (what steps edit);
     the live row and the file on disk are not touched until the save.
     A file that failed to load or has unsaved edits is SKIPPED (the
     first step says so; the engine stops the file there).
  2. Steps run. Measurements and fixes are APPLIED; guesses (filename
     parse, TMDB/TheTVDB match, subtitles) are SUGGESTIONs with a
     confidence, applied by the engine only at or above the recipe's
     threshold and listed as "Needs review" otherwise. Steps that change
     the video itself (lossless repair, MKV -> MP4 remux) write a
     SCRATCH copy beside the original; later steps work on that copy.
  3. finalize_file() (the engine's finalize hook, labelled "Save") writes
     the working metadata onto the scratch copy with the app's own
     writer (VideoFile.save: write, re-read, compare), verifies the
     streams and duration against the original, and commit_in_place()
     swaps it in, the original going to the Recycle Bin. Then the live
     row is reloaded from disk.
  4. Rename and Move into folders (position "after_save") run after the save,
     on the finished file; sidecar files (-poster.jpg, .lang.srt) travel
     with the video.

The check never acts on a failed tool: a scan where ffprobe/ffmpeg was
missing or stopped is never stamped and never triggers a repair. A
repair is only automatic when a lossless remux fully fixes the file
(REPAIRABLE); a DAMAGED file (whose unreadable end a repair would drop)
is reported and left for Media > Check Files.
"""

from __future__ import annotations

import copy
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from redactor_common.core.move_plan import execute_move, plan_moves, render_relative_path
from redactor_common.core.os_utils import rename_no_clobber
from redactor_common.core.path_parser import is_path_pattern, parse_path_detailed
from redactor_common.core.pipeline import (
    CommitError,
    FileReport,
    OptionSpec,
    Recipe,
    Step,
    StepResult,
    StepStatus,
    commit_in_place,
    effective_option_source,
)
from redactor_common.core.journal import KIND_REDACT
from redactor_common.core.local_db import year_gap
from redactor_common.core.rename_pattern import render_filename, unique_path, zero_pad_numeric_value
from redactor_common.core.trash import move_to_trash

from core import controlled_vocab, imdb_import, imdb_local, lyrics_subtitles, opensubtitles_client, scene_lookup, stashdb_client
from core import poster_fetch, theporndb_client, tmdb_client, tvdb_client
from core import subtitle_translation, translation_settings
from core import conversion_plan, conversion_run, file_access, history, library_safety
from core.config import get_setting, set_setting
from core.ffmpeg_backend import _run, remux_to_mp4, verify_remux
from core.file_check import RepairError, build_repaired_copy, quick_check
from core.filename_pattern import (
    PARSE_NUMERIC_FIELDS,
    PARSE_STRIP_ZEROS_FIELDS,
    VALID_FIELD_KEYS,
    field_text,
    load_pattern_history,
    modernize_pattern,
    parse_filename,
    placeholder_values,
    set_field_text,
)
from core.release_name_parser import parse_release_name
from core.sidecars import sidecar_moves, sidecar_pairs, sidecar_suffixes
from core.video_file import VideoFile
from core.video_fingerprint import video_fingerprint
from core.video_metadata import SOURCE_FIELDS, ContentType, can_move_track_title, move_track_title
from core.lookup_sources import imdb_values, scene_values, tmdb_values, tvdb_values
from core.source_tags import ref_in, stage_sources

RECIPE_SECTION = "redact"
RECIPE_KEY = "recipe"

# Confidence of a filename parse that matched the saved pattern exactly,
# of the release-name heuristic (never auto-applied at the default 0.9).
PATTERN_CONFIDENCE = 0.95
RELEASE_NAME_CONFIDENCE = 0.6
# Lookup matches: exact title and year / exact title only / nothing exact.
LOOKUP_ID = 0.97  # an exact IMDb id found in the filename or Comment
LOOKUP_EXACT = 0.95
LOOKUP_TITLE_ONLY = 0.8
LOOKUP_AMBIGUOUS = 0.6
LOOKUP_CLOSEST = 0.4
# ThePornDB / StashDB: a file's fingerprint matched. Shown with this confidence, but the step's suggestion is
# review_only: whatever the recipe's threshold, it is never applied by itself.
SCENE_FILE_CONFIDENCE = 0.95
# OpenSubtitles: a hash match syncs by construction; a text match may not.
SUBTITLE_HASH_CONFIDENCE = 0.95
SUBTITLE_TEXT_CONFIDENCE = 0.6

# The fields a lookup could still fill; when all are set there is nothing
# to gain from a network round trip.
_MOVIE_FIELDS = ("title", "description", "genre_tags", "release_date", "language", "director", "cast", "studio")
_TV_FIELDS = ("show_title", "title", "description", "genre_tags", "network", "release_date")

DEFAULT_MOVE_PATTERN = "%show_title%/Season %season_number%/%title%"
DEFAULT_PATH_PATTERN = DEFAULT_MOVE_PATTERN


def _direct(fn: Callable, *args: Any, **kwargs: Any) -> Any:
    return fn(*args, **kwargs)


def latest_file_pattern(history: list[str]) -> str:
    """The most recent saved pattern that names a FILE: the Rename dialog's
    "Move into folders" patterns (which contain a slash or backslash) share
    the same history but make no sense for renaming or parsing a filename."""
    return next((p for p in history if p and "/" not in p and "\\" not in p), "")


def latest_path_pattern(history: list[str]) -> str:
    """The most recent saved PATH pattern (contains a slash or backslash),
    the counterpart of latest_file_pattern: what "Move into folders" and
    a path-mode Import Metadata from Filename leave in the shared history."""
    return next((p for p in history if p and is_path_pattern(p)), "")


# --- run environment ---------------------------------------------------------


@dataclass
class RedactEnv:
    """What the steps of one Redact run share, all injectable for tests:
    the trash function (None = the Recycle Bin), the rename log
    (anything with .record(label, pairs, ...)), `background` (how a slow
    call -- ffmpeg, a request -- is run: directly by default. The GUI leaves
    it at the default because redactor_common's run_redact already runs each
    file on a worker thread; its call_in_background helper only works ON the
    GUI thread, so passing it here would break), and where the saved rename
    patterns and settings come from."""

    trash: Callable[[str], None] | None = None
    rename_log: Any = None
    background: Callable[..., Any] = _direct
    pattern_history: Callable[[], list[str]] = load_pattern_history
    setting: Callable[[str, str, str], str] = get_setting
    imdb_local: str = ""  # path of the offline IMDb database ("" = none set up: the lookup is online only)
    # Set by the progress dialog's Cancel (run_redact_dialog(on_cancel=cancel_event.set)) and by the app
    # closing: the step that runs ffmpeg hands it to _run_ffmpeg, which kills the encode within half a second.
    cancel_event: Any = None
    # The convert step's dry-run choice for THIS run only: the GUI's Redact confirm asks it per run (ticked by
    # default) and the command line passes --dry-run. None falls back to the old Library safety setting
    # (only callers that never ask, such as tests, still get that).
    convert_dry_run: bool | None = None


# --- per-file context --------------------------------------------------------


class VideoCtx:
    """make_context() result for one file. Never raises: a file that must
    not be touched gets `skip_reason`, which the first step that runs
    turns into StepResult.skipped."""

    def __init__(self, video: VideoFile, env: RedactEnv):
        self.video = video
        self.env = env
        self.step_options: dict = {}
        self.skip_reason = ""
        if video.load_error:
            self.skip_reason = f"the file could not be read ({video.load_error})"
        elif video.dirty and not video.stamp_only_dirty:
            self.skip_reason = "it has unsaved edits (save or undo them first)"
        elif not os.path.isfile(video.path):
            self.skip_reason = "the file is missing"
        # Work through a symlink rather than replacing it.
        self.original = os.path.realpath(video.path)
        self.work = VideoFile(path=Path(self.original), metadata=copy.deepcopy(video.metadata))
        self.work.check = video.check
        self.work.stamp_stale = video.stamp_stale
        # The ORIGINAL file's tags as text, for the one Tools > History entry this file's save records (the
        # working copy's own save onto the scratch file is not journaled). A file Redact may touch has no
        # unsaved edits, so its metadata is what is on disk when it was never loaded with a baseline.
        self.baseline = video.baseline if video.baseline is not None else history.fields_text(video.metadata)
        try:
            self.start_mtime: float | None = os.path.getmtime(self.original)  # before Redact's own save touches it
        except OSError:
            self.start_mtime = None
        self.current = self.original  # the newest content: the original, or a scratch copy
        self.content_changed = False
        self.scratches: list[str] = []
        self.saved = False
        self.save_failed = False
        self.verify_problem = ""

    def bg(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        return self.env.background(fn, *args, **kwargs)

    def new_scratch(self, ext: str) -> str:
        """An unused hidden name beside the original (same volume, so the
        final swap is an atomic rename), keeping the extension the tools
        pick their muxer by. Not created."""
        folder, name = os.path.split(self.original)
        stem = os.path.splitext(name)[0]
        path = os.path.join(folder, f".{stem}.redact-{uuid.uuid4().hex[:8]}{ext}")
        self.scratches.append(path)
        return path

    def adopt(self, path: str) -> None:
        """`path` (a finished scratch copy) is the file's content from now on."""
        old = self.current
        self.current = path
        self.content_changed = True
        if old != self.original and old != path:
            _remove_quietly(old)

    def needs_write(self) -> bool:
        live, work = self.video, self.work
        return (
            self.content_changed
            or live.dirty  # a scan stamp recorded earlier and never saved
            or work.metadata != live.metadata
            or work.metadata.scan_stamp != live.metadata.scan_stamp
            or any(getattr(work.metadata, f) != getattr(live.metadata, f) for f in SOURCE_FIELDS)
        )

    def close(self) -> None:
        """Engine hook, after every file: no scratch copy is left behind
        (the commit already moved the one that was used)."""
        for path in self.scratches:
            _remove_quietly(path)
            _remove_quietly(path + ".partial")

    # -- the save ----------------------------------------------------------------

    def save(self) -> StepResult:
        """Working metadata -> scratch copy -> verify -> commit_in_place ->
        reload the live row. Every failure leaves the original untouched."""
        live, work = self.video, self.work
        original_ext = os.path.splitext(self.original)[1]
        ext = os.path.splitext(self.current)[1]
        final = self.original if ext.lower() == original_ext.lower() else os.path.splitext(self.original)[0] + ext
        if final != self.original and os.path.lexists(final):
            self.save_failed = True
            return StepResult.failed(f"NOT SAVED, the original is untouched: {os.path.basename(final)} already exists")
        if file_access.is_read_only(self.original):  # before any working copy exists: nothing to clean up
            self.save_failed = True
            return StepResult.failed(f"NOT SAVED, the original is untouched: {file_access.read_only_message(self.original)}")
        try:
            if self.current == self.original:
                scratch = self.new_scratch(original_ext)
                self.bg(shutil.copy2, self.original, scratch)  # keeps the permissions
                file_access.make_scratch_writable(scratch)  # ...but not a read-only attribute
            else:
                scratch = self.current
        except OSError as exc:
            self.save_failed = True
            return StepResult.failed(f"NOT SAVED, couldn't make a working copy: {exc}")

        # The app's own writer: writes the tags, re-reads the file and
        # compares every field (and the scan stamp) with what it meant to write.
        work.path = Path(scratch)
        work.save_error = ""
        self.bg(work.save, journal=False)
        if work.save_error:
            self.save_failed = True
            return StepResult.failed(f"NOT SAVED, the original is untouched: {work.save_error}")

        def verify(path: str) -> bool:
            self.verify_problem = self.bg(verify_remux, self.original, path)
            return not self.verify_problem

        # Tools > History: one "redact" entry per file, old values from the original, new from the working
        # copy; announced before the commit, finished after. Never fails the save (core.history). It is
        # recorded at the path where the save happens (the original's); a new extension's rename below is a
        # move the entry follows only once it succeeded.
        entry = history.journal_begin(
            self.original, KIND_REDACT, self.baseline, history.fields_text(work.metadata),
        )
        try:
            result = self.bg(commit_in_place, self.original, scratch, self.env.trash or move_to_trash, verify)
        except CommitError as exc:
            self.save_failed = True
            detail = f"{exc}" + (f" ({self.verify_problem})" if self.verify_problem else "")
            history.journal_finish(entry, False, detail)
            return StepResult.failed(f"NOT SAVED, the original is untouched: {detail}")
        self.saved = True

        final_path = self.original
        rename_problem = ""
        if final != self.original:
            try:
                rename_no_clobber(self.original, final)
                final_path = final
                history.record_move(self.original, final)
            except OSError as exc:
                rename_problem = (
                    f"saved, but couldn't rename it to {os.path.basename(final)} ({exc}); "
                    f"it is an {ext.lstrip('.').upper()} file still named {os.path.basename(self.original)}"
                )
        history.journal_finish(entry, True, rename_problem)
        if final_path != self.original:
            live.path = Path(final_path)
        live.dirty = False
        live.save_error = ""
        check = work.check
        self.bg(live.load)  # what is on disk now: technical details, the stamp's staleness
        live.check = check
        live._thumbnail_path = None

        if rename_problem:
            return StepResult.failed(rename_problem)
        notes = []
        if result.backup_kept:
            text = f"saved; the original is kept at {result.backup}"
            notes.append(result.warning)
        else:
            text = "saved in place; the original is in the Recycle Bin"
        if live.load_error:
            notes.append(f"saved, but re-reading the file failed: {live.load_error}")
        return _with_note(StepResult.applied(text), "; ".join(notes))


def finalize_file(ctx: VideoCtx, _report: FileReport) -> StepResult | None:
    """The engine's finalize hook (label "Save"): writes the file once, if
    anything changed."""
    if ctx.skip_reason:
        return StepResult.skipped(ctx.skip_reason)
    if not ctx.needs_write():
        return None
    return ctx.save()


# --- helpers -----------------------------------------------------------------


def _remove_quietly(path: str) -> None:
    file_access.remove_scratch(path)  # retries once past a read-only attribute


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", " ", (text or "").casefold()).strip()


def _with_note(result: StepResult, note: str) -> StepResult:
    result.note = note
    return result


def _is_empty(ctx: VideoCtx, field_name: str) -> bool:
    return not field_text(ctx.work.metadata, field_name)


def _set_sources(ctx: VideoCtx, values: dict[str, str]) -> list[str]:
    """Writes the source ids of an applied lookup into the working metadata: only the ones that are EMPTY (an id
    already in the file is replaced only through Metadata > Look Up's per-field review, never here)."""
    lines = []
    for key, value in values.items():
        if not getattr(ctx.work.metadata, key, "") and stage_sources(ctx.work.metadata, {key: value}):
            lines.append(f"{key} = {value!r}")
    if lines:
        ctx.work.dirty = True
    return lines


def _set_fields(ctx: VideoCtx, fields: dict[str, Any]) -> list[str]:
    """Writes `fields` into the working metadata (empties only are
    chosen by the caller); returns "field = value" lines."""
    lines = []
    for key, value in fields.items():
        if set_field_text(ctx.work.metadata, key, str(value)):
            lines.append(f"{key} = {value!r}")
    if lines:
        ctx.work.dirty = True
    return lines


class VideoStep(Step):
    """Base class: a file that must not be touched is reported as skipped
    by whichever step runs first."""

    def run(self, ctx: VideoCtx) -> StepResult:
        if ctx.skip_reason:
            return StepResult.skipped(ctx.skip_reason)
        return self.execute(ctx)

    def execute(self, ctx: VideoCtx) -> StepResult:
        raise NotImplementedError


# --- pattern trail (redactor_common's OptionSpec suggestions/fallback/preview) ------
#
# A saved recipe keeps the pattern it was saved with; an EMPTY stored value
# follows the fallback below. The editor shows what is in effect, where it
# came from and the recent patterns; the steps resolve through the same
# effective_option_source so what the editor shows is what runs.

SAMPLE_VALUES = {
    "show_title": "Show", "season_number": "1", "episode_number": "1", "title": "Episode", "release_date": "2020",
}


def _pad_episode(values: dict[str, str], setting: Callable[[str, str, str], str]) -> dict[str, str]:
    """`values` with the Rename dialog's saved zero-padding applied to the
    episode number (a copy)."""
    values = dict(values)
    if setting("rename", "zero_pad", "0") == "1":
        try:
            width = int(setting("rename", "zero_pad_width", "2") or 2)
        except ValueError:
            width = 2
        values["episode_number"] = zero_pad_numeric_value(values.get("episode_number", ""), width)
    return values


def _history(env: "RedactEnv") -> list[str]:
    try:
        return [p for p in env.pattern_history() if isinstance(p, str) and p]
    except Exception:  # noqa: BLE001 -- a broken history must not break the editor
        return []


def _pattern_suggestions(env: "RedactEnv", kind: str) -> list[str]:
    """The pattern history, newest first, the kind this option takes first
    (file options: filename patterns, then path patterns; path options the
    reverse), de-duplicated. The move option also offers the pattern last
    used in Rename/Export's Move into folders."""
    history = _history(env)
    if kind == "move":
        history.insert(0, env.setting("rename", "move_pattern", "").strip())
    files = [p for p in history if p and not is_path_pattern(p)]
    paths = [p for p in history if p and is_path_pattern(p)]
    ordered = files + paths if kind == "file" else paths + files
    return list(dict.fromkeys(ordered))


def _pattern_fallback(env: "RedactEnv", kind: str) -> str:
    history = _history(env)
    if kind == "file":
        return latest_file_pattern(history)
    if kind == "path":
        return latest_path_pattern(history) or DEFAULT_PATH_PATTERN
    return env.setting("rename", "move_pattern", "").strip() or DEFAULT_MOVE_PATTERN


_FALLBACK_LABELS = {
    "file": "the latest filename pattern",
    "path": "the latest path pattern (else the default)",
    "move": "the last Rename/Export Move into folders pattern (else the default)",
}


def _pattern_preview(
    env: "RedactEnv", kind: str, pattern: str, sample: Callable[[], dict[str, str] | None] | None
) -> str:
    """`pattern` rendered on the first loaded video's values (else a built-in
    Show/Season/Episode sample) with the renderers Rename and Move use.
    Cheap and never raises."""
    try:
        values = None
        try:
            values = sample() if sample else None
        except Exception:  # noqa: BLE001
            values = None
        values = _pad_episode(values or SAMPLE_VALUES, env.setting)
        if not pattern.strip():
            return ""
        if is_path_pattern(pattern):
            return "/".join(render_relative_path(values, pattern))
        return render_filename(values, pattern)
    except Exception:  # noqa: BLE001
        return ""


def pattern_trail_spec(
    base: OptionSpec, kind: str, env: "RedactEnv", sample: Callable[[], dict[str, str] | None] | None = None
) -> OptionSpec:
    """`base` with the pattern-trail callables bound to `env`. kind is
    "file" (filename patterns), "path" (path patterns) or "move"."""
    return replace(
        base,
        suggestions=lambda: _pattern_suggestions(env, kind),
        fallback=lambda: _pattern_fallback(env, kind),
        fallback_label=_FALLBACK_LABELS[kind],
        preview=lambda p: _pattern_preview(env, kind, p, sample),
    )


class PatternStep(VideoStep):
    """A step with one "pattern" option shown as a pattern trail.
    `trail_kind` is "file", "path" or "move"."""

    trail_kind = "file"

    def __init__(self, *args: Any, env: "RedactEnv | None" = None, sample: Callable | None = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.options = tuple(
            pattern_trail_spec(o, self.trail_kind, env or RedactEnv(), sample) if o.key == "pattern" else o
            for o in type(self).options
        )

    def effective_pattern(self, ctx: "VideoCtx") -> str:
        """The pattern to use: the stored one, else the fallback (resolved
        against THIS run's settings/history)."""
        spec = pattern_trail_spec(type(self).options[0], self.trail_kind, ctx.env)
        value, _source = effective_option_source(spec, self.options_for(ctx)["pattern"].strip())
        return value.strip()


# --- step 1: check and repair --------------------------------------------------


class CheckRepairStep(VideoStep):
    key = "check_repair"
    label = "Check and repair (lossless)"
    description = (
        "Runs the quick health check (Media > Check Files) and stamps the result inside the file. "
        "With 'repair' on, a file a lossless remux fully fixes (missing seek index or duration, MP4 index "
        "at the end, default audio track) is rewritten on a scratch copy, verified, and swapped in. A "
        "DAMAGED file is only reported -- repairing it would drop its unreadable end; use Check Files for "
        "that. If ffprobe/ffmpeg is missing or stops responding, nothing is stamped and nothing is repaired."
    )
    options = (
        OptionSpec("repair", "Repair what a lossless remux can fix", "bool", True),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        tool_failed: list[str] = []

        def run(args, timeout=None):
            done = _run(args) if timeout is None else _run(args, timeout=timeout)
            if done is None:
                tool_failed.append(args[0])
            return done

        check = ctx.bg(quick_check, ctx.current, run)
        if tool_failed or any(f.code == "no_tool" for f in check.findings):
            return StepResult.nothing(
                note="not checked: ffprobe/ffmpeg is missing or stopped responding (nothing stamped or repaired)"
            )
        status, summary = check.status, check.summary()

        if status == "REPAIRABLE" and self.options_for(ctx)["repair"]:
            return self._repair(ctx, check)

        fingerprint = ctx.bg(video_fingerprint, ctx.current) if check.openable else ""
        stamp = ctx.work.stamp
        current = stamp is not None and ctx.work.stamp_stale is False and stamp.status == status
        stamped = False
        if not current:
            stamped = ctx.work.record_check(check, fingerprint)
        else:
            ctx.work.check = check
        note = ""
        if status != "CHECKED OK":
            note = f"{status}: {summary}"
            if check.can_repair and status != "REPAIRABLE":
                note += " -- not repaired automatically (a repair would drop what can't be read); use Check Files"
            elif status == "REPAIRABLE":
                note += " -- repair is off in the recipe"
            elif status == "WRONG CONTAINER":
                note += " -- the Convert step fixes it with a lossless remux, or Media > Check Files > Remux"
        if stamped:
            what = "checked OK" if status == "CHECKED OK" else status
            return _with_note(StepResult.applied(f"{what} (stamped)"), note)
        return StepResult.nothing(note=note)

    def _repair(self, ctx: VideoCtx, check) -> StepResult:
        target = ctx.new_scratch(os.path.splitext(ctx.current)[1])
        try:
            outcome = ctx.bg(build_repaired_copy, ctx.current, check, target)
        except RepairError as exc:
            return StepResult.failed(f"not repaired, the file is untouched: {exc}")
        ctx.adopt(target)
        fingerprint = ctx.bg(video_fingerprint, target)
        ctx.work.record_check(outcome.check, fingerprint)
        result = StepResult.applied(f"repaired losslessly ({check.summary()}); now {outcome.check.status} (stamped)")
        return _with_note(result, outcome.note)


# --- step 2: tags from the filename ------------------------------------------------


@dataclass
class FieldFill:
    """What a guess would write: field key -> new text."""

    fields: dict[str, str]

    def __str__(self) -> str:
        return ", ".join(f"{key}={value!r}" for key, value in self.fields.items())


class FilenameTagsStep(PatternStep):
    key = "filename_tags"
    label = "Fill empty tags from the filename"
    description = (
        "Parses the filename with your most recent saved pattern (Import Metadata from Filename / "
        "Rename by Pattern) and fills fields that are EMPTY; existing values are never replaced. A name "
        "that matches the pattern exactly is applied; without a matching pattern, season/episode/title/year "
        "read from release-name tags (S01E02, 2010, 1080p...) are only a guess and go to Needs review."
    )
    options = (
        OptionSpec(
            "pattern", "Pattern (empty: the most recently used)", "str", "", max_length=300,
            tooltip="A %field% pattern such as %show_title% - S%season_number%E%episode_number% - %title%.",
        ),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        stem = ctx.video.path.stem
        pattern = self.effective_pattern(ctx)
        if pattern:
            parsed = parse_filename(stem, pattern)
            if parsed:
                fills = {k: v for k, v in parsed.items() if v and _is_empty(ctx, k)}
                if not fills:
                    return StepResult.nothing()
                return StepResult.suggestion(
                    FieldFill(fills), PATTERN_CONFIDENCE, f"the filename matches the pattern {pattern!r} exactly"
                )
        guess = parse_release_name(stem)
        fills: dict[str, str] = {}
        if guess.kind == "tv":
            fills = {"show_title": guess.title, "season_number": str(guess.season), "episode_number": str(guess.episode)}
        elif guess.kind == "movie":
            fills = {"title": guess.title, "release_date": guess.year or ""}
        fills = {k: v for k, v in fills.items() if v and _is_empty(ctx, k)}
        if not fills:
            return StepResult.nothing(
                note="" if pattern else "no saved filename pattern, and no release-name tags to read"
            )
        reason = "release-name tags in the filename"
        reason += " (no saved pattern matched)" if pattern else " (no saved pattern)"
        return StepResult.suggestion(FieldFill(fills), RELEASE_NAME_CONFIDENCE, reason)

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        return _set_fields(ctx, result.value.fields)


class PathTagsStep(PatternStep):
    key = "path_tags"
    trail_kind = "path"
    label = "Fill empty tags from the folder path"
    description = (
        "Reads the folders the file sits in, under the library root chosen in Rename/Export by Pattern > "
        "Move into folders, with a path pattern (the most recent saved one, else %show_title%/Season "
        "%season_number%/%title%) and fills fields that are EMPTY; existing values are never replaced. A "
        "path that matches every segment is applied; a partial match (a 'Specials' folder instead of "
        "'Season N', a file outside a show folder) goes to Needs review with the missing segments named. "
        "Does nothing without a library root or for a file outside it. 'Season 02' gives season 2."
    )
    options = (
        OptionSpec(
            "pattern", "Path pattern (empty: the most recent saved one)", "str", "", max_length=300,
            tooltip=f"A %field% pattern with '/', e.g. {DEFAULT_PATH_PATTERN}",
        ),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        root = ctx.env.setting("rename", "library_root", "").strip()
        if not root:
            return StepResult.nothing(note="folder path not read: no library root (choose one in Rename/Export by Pattern > Move into folders)")
        path = str(ctx.video.path)
        if not _is_under(path, root):
            return StepResult.nothing()
        pattern = self.effective_pattern(ctx)
        if not is_path_pattern(pattern):
            return StepResult.nothing(note=f"folder path not read: {pattern!r} has no '/' (not a path pattern)")
        parsed = parse_path_detailed(
            path, pattern, root, set(VALID_FIELD_KEYS), set(PARSE_NUMERIC_FIELDS),
            strip_leading_zeros_fields=set(PARSE_STRIP_ZEROS_FIELDS),
        )
        if not parsed.matched:
            return StepResult.nothing(note=f"folder path not read: the file name doesn't match the pattern {pattern!r}")
        fills = {k: v for k, v in parsed.values.items() if v and _is_empty(ctx, k)}
        if not fills:
            return StepResult.nothing()
        reason = f"the folder path matches the pattern {pattern!r}"
        if parsed.missing_segments:
            reason += "; no match for " + ", ".join(repr(m) for m in parsed.missing_segments)
        return StepResult.suggestion(FieldFill(fills), parsed.confidence, reason)

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        return _set_fields(ctx, result.value.fields)


def _is_under(path: str, root: str) -> bool:
    """True when `path` is inside `root` (case-insensitive on Windows)."""
    norm_root = os.path.normcase(os.path.abspath(root))
    try:
        return os.path.commonpath([os.path.normcase(os.path.abspath(path)), norm_root]) == norm_root
    except ValueError:  # different drives
        return False


# --- step 3: local IMDb database, then TMDB / TheTVDB lookup ---------------------------


@dataclass
class LookupFill:
    source: str  # "TMDB" / "TheTVDB" / "IMDb (local database)" / "IMDb (local database) + TMDB"
    label: str  # the matched title
    fields: dict[str, Any]
    content_type: ContentType
    year: str = ""  # the matched title's (first) year, to cross-check a local match against an online one
    sources: dict = field(default_factory=dict)  # source id fields this match sets when they are empty (core.lookup_sources)

    def __str__(self) -> str:
        shown = ", ".join(f"{key}={str(value)[:40]!r}" for key, value in self.fields.items())
        return f"{self.source} '{self.label}': {shown}"


@dataclass
class LocalFound:
    """What the local IMDb database found: the fields it can fill, how sure that is, and why."""

    fill: LookupFill
    confidence: float
    reason: str


def _tv_guess(stem: str):
    """(title, season, episode, year) read from a filename: the year is
    split off the title ("Show (2019) S01E02")."""
    guess = parse_release_name(stem)
    title, year = guess.title, ""
    m = re.search(r"\b((?:19|20)\d{2})$", title)
    if m and title != m.group(1):
        title, year = title[: m.start()].strip(" -_"), m.group(1)
    return guess, title, year


def conversion_is_series(content_type, stem: str) -> bool:
    """Whether the conversion rules treat a file as a series episode (the bitrate/720p re-encode rules apply to
    series only, unless Preferences > Conversion says movies too). A file tagged TV is one; a file whose content
    type is not set is one when its NAME parses as an episode (a season AND an episode number); anything tagged
    otherwise (Movie, Music Video, Clip, Misc) is not."""
    if content_type == ContentType.TV:
        return True
    if content_type in (None, "", ContentType.UNSET):
        guess = _tv_guess(stem)[0]
        return guess.season is not None and guess.episode is not None
    return False


class LookupStep(VideoStep):
    key = "lookup"
    label = "Look up metadata (IMDb, TMDB, TheTVDB)"
    description = (
        "Finds the movie or show named by the filename and fills fields that are EMPTY. With a local IMDb "
        "database set up (Tools > IMDb Database) that is asked FIRST, offline: title, year, genres and, for TV, "
        "the episode's title and numbers (an IMDb id like tt1234567 in the filename or Comment is used exactly); "
        "TMDB (TheTVDB as the fallback for TV) is then asked only for what is still empty, such as the plot, and "
        "when it can't be reached the IMDb fields are still filled and the report says so. A movie whose title AND "
        "year match exactly is applied; a title-only match, or a TV show whose title matches but whose year can't "
        "be confirmed, goes to Needs review. The online part needs network access and an API key (Tools > API Keys); "
        "without either only the local database is used, or nothing happens, and the report says why."
    )
    options = (
        OptionSpec("movies", "Look up movies (IMDb local, TMDB)", "bool", True),
        OptionSpec("tv", "Look up TV shows (IMDb local, TMDB, then TheTVDB)", "bool", True),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        opts = self.options_for(ctx)
        stem = ctx.video.path.stem
        guess = parse_release_name(stem)
        notes: list[str] = []
        db = self._open_local(ctx, notes)
        outcome = self._by_imdb_id(ctx, db, stem, opts, notes) if db is not None else None
        if outcome is None:
            if guess.kind == "movie" and opts["movies"]:
                if not any(_is_empty(ctx, f) for f in _MOVIE_FIELDS):
                    outcome = StepResult.nothing()
                else:
                    year = guess.year or ctx.work.metadata.release_date[:4]
                    outcome = self._movie(ctx, guess.title, year, db, notes)
            elif guess.kind == "tv" and opts["tv"]:
                if not any(_is_empty(ctx, f) for f in _TV_FIELDS):
                    outcome = StepResult.nothing()
                else:
                    outcome = self._tv(ctx, stem, db, notes)
            else:
                outcome = StepResult.nothing()
        if notes:
            outcome.note = "; ".join(n for n in (outcome.note, *notes) if n)
        return outcome

    # -- the local IMDb database -----------------------------------------------------

    @staticmethod
    def _open_local(ctx: VideoCtx, notes: list[str]):
        """The opened local database, or None when none is set up (silently) or it
        can't be opened (a note: the online lookup carries on)."""
        path = ctx.env.imdb_local
        if not path:
            return None
        try:
            return imdb_local.open_database(path)
        except imdb_import.ImdbDatabaseError as exc:
            notes.append(f"the local IMDb database is unavailable ({exc})")
            return None

    def _by_imdb_id(self, ctx: VideoCtx, db, stem: str, opts: dict, notes: list[str]) -> StepResult | None:
        """A title id written in the filename or the Comment field identifies the
        title exactly (97%). None when there is no id, it isn't in the database, or
        the kind of title is switched off in the step's options."""
        held = ref_in(ctx.work.metadata, "imdb", ("title",))
        wanted = held.id if held is not None else imdb_local.find_imdb_id(stem, ctx.work.metadata.comment)
        if not wanted:
            return None
        try:
            found = imdb_local.title_by_id(db, wanted)
        except imdb_import.ImdbDatabaseError as exc:
            notes.append(f"IMDb (local database) lookup failed ({exc})")
            return None
        if found is None:
            notes.append(f"IMDb (local database) doesn't have {wanted}")
            return None
        reason = f"exact IMDb id {wanted}" + (" (the id in the file)" if held is not None else "")
        if isinstance(found, imdb_local.ImdbMovieCandidate):
            if not opts["movies"]:
                return None
            fill = LookupFill(imdb_local.SERVICE_NAME, f"{found.title} ({found.year})",
                              self._only_empty(ctx, imdb_local.movie_fields(found)), ContentType.MOVIE, found.year,
                              imdb_values(wanted, "manual"))
            local = LocalFound(fill, LOOKUP_ID, reason)
            return self._movie_with_local(ctx, local, found.title, found.year, notes)
        if not opts["tv"]:
            return None
        if isinstance(found, imdb_local.ImdbTVCandidate):
            series, episode = found, None
            guess, _t, _y = _tv_guess(stem)
            season = ctx.work.metadata.season_number or guess.season
            number = ctx.work.metadata.episode_number or guess.episode
            if season and number:
                episode = imdb_local.find_episode(db, found.tconst, season, number)
        else:  # an episode id: its series and numbers come with it
            series, episode = imdb_local.series_by_id(db, found.series), found
            if series is None:
                notes.append(f"IMDb (local database) has {wanted} but not its series")
                return None
        fields = self._tv_fields(series, episode)
        fill = LookupFill(imdb_local.SERVICE_NAME, f"{series.name} ({series.year})",
                          self._only_empty(ctx, fields), ContentType.TV, series.year,
                          imdb_values(episode.imdb_id if episode is not None and episode.imdb_id else wanted, "manual"))
        local = LocalFound(fill, LOOKUP_ID, reason)
        return self._tv_with_local(
            ctx, local, series.name, series.year, episode.season if episode else None,
            episode.episode_number if episode else None, notes,
        )

    @staticmethod
    def _only_empty(ctx: VideoCtx, fields: dict) -> dict:
        return {k: v for k, v in fields.items() if _is_empty(ctx, k)}

    @staticmethod
    def _tv_fields(series, episode) -> dict:
        # Episode fields first: more specific than the show's.
        fields = imdb_local.episode_fields(episode) if episode is not None else {}
        for key, value in imdb_local.show_fields(series).items():
            fields.setdefault(key, value)
        return fields

    def _movie_local(self, ctx: VideoCtx, db, title: str, year: str, notes: list[str]) -> LocalFound | None:
        try:
            candidates = imdb_local.search_movies(db, title, year or None)
        except imdb_import.ImdbDatabaseError as exc:
            notes.append(f"IMDb (local database) lookup failed ({exc})")
            return None
        if not candidates:
            notes.append(f"IMDb (local database) found no film for '{title}'")
            return None
        exact = [c for c in candidates if c.exact]
        with_year = [c for c in exact if year and c.year == year]
        if len(with_year) == 1:
            pick, confidence, reason = with_year[0], LOOKUP_EXACT, "title and year match exactly"
        elif len(with_year) > 1:
            pick, confidence = with_year[0], LOOKUP_AMBIGUOUS
            reason = f"{len(with_year)} films match the title and year"
        elif exact and year:
            pick, confidence, reason = exact[0], LOOKUP_AMBIGUOUS, f"the title matches but not the year {year}"
        elif len(exact) == 1:
            pick, confidence, reason = exact[0], LOOKUP_TITLE_ONLY, "the title matches, but the filename has no year"
        elif exact:
            pick, confidence = exact[0], LOOKUP_AMBIGUOUS
            reason = f"{len(exact)} films match the title and there is no year to tell them apart"
        else:
            pick, confidence, reason = candidates[0], LOOKUP_CLOSEST, "closest search result; the title differs"
        if pick.alias and pick.exact:
            reason += f" (as '{pick.alias}')"
        fill = LookupFill(imdb_local.SERVICE_NAME, f"{pick.title} ({pick.year})",
                          self._only_empty(ctx, imdb_local.movie_fields(pick)), ContentType.MOVIE, pick.year,
                          imdb_values(pick.imdb_id, "title"))
        return LocalFound(fill, confidence, f"{reason} (IMDb {pick.imdb_id})")

    def _tv_local(self, ctx: VideoCtx, db, title: str, year: str, season, episode, notes: list[str]) -> LocalFound | None:
        try:
            candidates = imdb_local.search_series(db, title, year or None)
        except imdb_import.ImdbDatabaseError as exc:
            notes.append(f"IMDb (local database) lookup failed ({exc})")
            return None
        if not candidates:
            notes.append(f"IMDb (local database) found no show for '{title}'")
            return None
        exact = [c for c in candidates if c.exact]
        with_year = [c for c in exact if year and c.year == year]
        if len(with_year) == 1:
            pick, confidence, reason = with_year[0], LOOKUP_EXACT, "title and first-air year match exactly"
        elif len(exact) == 1 and year:
            pick, confidence = exact[0], LOOKUP_AMBIGUOUS
            reason = f"the title matches but its first-air year is {exact[0].year or 'unknown'}, not {year}"
        elif len(exact) == 1:
            pick, confidence, reason = exact[0], LOOKUP_TITLE_ONLY, "the title matches, but the filename has no year to confirm it"
        elif exact:
            pick, confidence = (with_year or exact)[0], LOOKUP_AMBIGUOUS
            reason = f"{len(with_year or exact)} shows match the title"
        else:
            pick, confidence, reason = candidates[0], LOOKUP_CLOSEST, "closest search result; the title differs"
        found_episode = None
        if season and episode:
            try:
                found_episode = imdb_local.find_episode(db, pick.tconst, season, episode)
            except imdb_import.ImdbDatabaseError:
                found_episode = None
            if found_episode is None:
                notes.append(f"IMDb (local database): no S{season:02d}E{episode:02d} for '{pick.name}'")
            elif confidence == LOOKUP_TITLE_ONLY and len(exact) == 1:
                # The one show of that title, and it has exactly this season and episode.
                confidence, reason = LOOKUP_EXACT, f"{reason}; S{season:02d}E{episode:02d} exists in it"
        fill = LookupFill(imdb_local.SERVICE_NAME, f"{pick.name} ({pick.year})",
                          self._only_empty(ctx, self._tv_fields(pick, found_episode)), ContentType.TV, pick.year,
                          imdb_values(found_episode.imdb_id if found_episode is not None and found_episode.imdb_id else pick.imdb_id,
                                      "title"))
        return LocalFound(fill, confidence, f"{reason} (IMDb {pick.imdb_id})")

    def _combine(self, ctx: VideoCtx, local: LocalFound, online: StepResult | None, notes: list[str]) -> StepResult:
        """The local fields, plus whatever the online lookup found for fields the local
        database left empty. The online match must agree on the year; the combined
        confidence is the lower of the two (never above either source's own rule)."""
        fill, confidence, reason = local.fill, local.confidence, local.reason
        if online is not None:
            if online.status is StepStatus.SUGGESTION:
                other: LookupFill = online.value
                if fill.year and other.year and year_gap(fill.year, other.year) > 1:
                    notes.append(
                        f"{other.source} matched '{other.label}', which is a different year than the IMDb match "
                        f"'{fill.label}', so only the IMDb fields were used"
                    )
                else:
                    fields = dict(fill.fields)
                    for key, value in other.fields.items():
                        if key == "release_date" and key in fields and str(value).startswith(str(fields[key])[:4]):
                            fields[key] = value  # TMDB's full date beats IMDb's bare year
                        else:
                            fields.setdefault(key, value)
                    sources = dict(fill.sources)
                    for key, value in other.sources.items():
                        sources.setdefault(key, value)
                    fill = LookupFill(f"{fill.source} + {other.source}", fill.label, fields, fill.content_type, fill.year, sources)
                    confidence = min(confidence, online.confidence)
                    reason = f"{reason}; {other.source}: {online.reason}"
            elif online.note:
                notes.append(online.note)
        if not fill.fields and ctx.work.metadata.content_type is not ContentType.UNSET:
            return StepResult.nothing()
        return StepResult.suggestion(fill, confidence, reason)

    # -- movies ------------------------------------------------------------------

    def _movie(self, ctx: VideoCtx, title: str, year: str, db, notes: list[str]) -> StepResult:
        local = self._movie_local(ctx, db, title, year, notes) if db is not None else None
        return self._movie_with_local(ctx, local, title, year, notes)

    def _movie_with_local(self, ctx: VideoCtx, local: LocalFound | None, title: str, year: str,
                          notes: list[str]) -> StepResult:
        if local is None:
            return self._movie_online(ctx, title, year)
        have = set(local.fill.fields)
        remaining = [f for f in _MOVIE_FIELDS if _is_empty(ctx, f) and f not in have]
        online = self._movie_online(ctx, title, year, skip=have - {"release_date"}) if remaining else None
        return self._combine(ctx, local, online, notes)

    def _movie_online(self, ctx: VideoCtx, title: str, year: str, skip: set[str] | frozenset = frozenset()) -> StepResult:
        held = ref_in(ctx.work.metadata, "tmdb", ("movie",))
        if held is not None:  # the file already names its TMDB record: no search
            try:
                details = ctx.bg(tmdb_client.get_movie_details, int(held.id))
            except tmdb_client.TMDBError as exc:
                return StepResult.nothing(note=f"TMDB details unavailable: {exc}")
            fields = {k: v for k, v in self._empty_fields(ctx, details).items() if k not in skip}
            if not fields and ctx.work.metadata.content_type is not ContentType.UNSET:
                return StepResult.nothing()
            label_year = (details.get("release_date") or "")[:4]
            fill = LookupFill("TMDB", f"{details.get('title', '')} ({label_year})", fields, ContentType.MOVIE, label_year,
                              tmdb_values("movie", held.id, imdb_id=details.get("_imdb_id", ""), how="manual"))
            return StepResult.suggestion(fill, LOOKUP_ID, f"the TMDB id in the file ({held.id})")
        try:
            candidates = ctx.bg(tmdb_client.search_movies, title, year or None)
        except tmdb_client.TMDBError as exc:
            return StepResult.nothing(note=f"TMDB lookup unavailable: {exc}")
        if not candidates:
            return StepResult.nothing(note=f"TMDB found no movie for '{title}'")
        exact = [c for c in candidates if _norm(c.title) == _norm(title)]
        with_year = [c for c in exact if year and c.year == year]
        if len(with_year) == 1:
            pick, confidence, reason = with_year[0], LOOKUP_EXACT, "title and year match exactly"
        elif len(with_year) > 1:
            pick, confidence = with_year[0], LOOKUP_AMBIGUOUS
            reason = f"{len(with_year)} movies match the title and year"
        elif exact and year:
            pick, confidence, reason = exact[0], LOOKUP_AMBIGUOUS, f"the title matches but not the year {year}"
        elif exact:
            pick, confidence, reason = exact[0], LOOKUP_TITLE_ONLY, "the title matches, but the filename has no year"
        else:
            pick, confidence = candidates[0], LOOKUP_CLOSEST
            reason = "closest search result; the title differs"
        try:
            details = ctx.bg(tmdb_client.get_movie_details, pick.tmdb_id)
        except tmdb_client.TMDBError as exc:
            return StepResult.nothing(note=f"TMDB details unavailable: {exc}")
        fields = {k: v for k, v in self._empty_fields(ctx, details).items() if k not in skip}
        if not fields and ctx.work.metadata.content_type is not ContentType.UNSET:
            return StepResult.nothing()
        fill = LookupFill("TMDB", f"{pick.title} ({pick.year})", fields, ContentType.MOVIE, pick.year,
                          tmdb_values("movie", pick.tmdb_id, imdb_id=details.get("_imdb_id", ""), how="title"))
        return StepResult.suggestion(fill, confidence, f"{reason} (TMDB id {pick.tmdb_id})")

    # -- TV -----------------------------------------------------------------------

    def _tv(self, ctx: VideoCtx, stem: str, db=None, notes: list[str] | None = None) -> StepResult:
        notes = notes if notes is not None else []
        guess, title, year = _tv_guess(stem)
        season = ctx.work.metadata.season_number or guess.season
        episode = ctx.work.metadata.episode_number or guess.episode
        local = self._tv_local(ctx, db, title, year, season, episode, notes) if db is not None else None
        return self._tv_with_local(ctx, local, title, year, season, episode, notes)

    def _tv_with_local(self, ctx: VideoCtx, local: LocalFound | None, title: str, year: str, season, episode,
                       notes: list[str]) -> StepResult:
        online_notes: list[str] = []
        if local is None:
            outcome = self._tv_online(ctx, title, year, season, episode, online_notes)
            if outcome is None:
                return StepResult.nothing(note="; ".join(online_notes))
            return _with_note(outcome, "; ".join(online_notes))
        have = set(local.fill.fields)
        remaining = [f for f in _TV_FIELDS if _is_empty(ctx, f) and f not in have]
        online = None
        if remaining:
            online = self._tv_online(ctx, title, year, season, episode, online_notes, skip=have - {"release_date"})
        notes.extend(online_notes)
        return self._combine(ctx, local, online, notes)

    def _tv_online(self, ctx, title, year, season, episode, notes, skip=frozenset()) -> StepResult | None:
        order = ["TMDB", "TheTVDB"]
        if ref_in(ctx.work.metadata, "tvdb", ("series",)) is not None and ref_in(ctx.work.metadata, "tmdb", ("tv", "season", "episode")) is None:
            order.reverse()  # the file names its TheTVDB record only: ask that one first
        for source in order:
            outcome = self._tv_from(ctx, source, title, year, season, episode, notes, skip)
            if outcome is not None:
                return outcome
        return None

    def _tv_from(self, ctx, source, title, year, season, episode, notes, skip=frozenset()) -> StepResult | None:
        tmdb = source == "TMDB"
        client, error = (tmdb_client, tmdb_client.TMDBError) if tmdb else (tvdb_client, tvdb_client.TVDBError)
        held = ref_in(ctx.work.metadata, "tmdb", ("tv", "season", "episode")) if tmdb else ref_in(ctx.work.metadata, "tvdb", ("series",))
        if held is not None:  # the file already names the show: no search, no guessing
            return self._tv_by_id(ctx, source, int(held.id.split("/")[0]), season, episode, notes, skip)
        try:
            candidates = ctx.bg(client.search_tv if tmdb else client.search_series, title, year or None)
        except error as exc:
            notes.append(f"{source} lookup unavailable: {exc}")
            return None
        if not candidates:
            notes.append(f"{source} found no show for '{title}'")
            return None
        exact = [c for c in candidates if _norm(c.name) == _norm(title)]
        with_year = [c for c in exact if year and c.year == year]
        if len(with_year) == 1:
            pick, confidence, reason = with_year[0], LOOKUP_EXACT, "title and first-air year match exactly"
        elif len(exact) == 1 and year:
            pick, confidence = exact[0], LOOKUP_AMBIGUOUS
            reason = f"the title matches but its first-air year is {exact[0].year or 'unknown'}, not {year}"
        elif len(exact) == 1:
            pick, confidence, reason = exact[0], LOOKUP_TITLE_ONLY, "the title matches, but the filename has no year to confirm it"
        elif exact:
            pick, confidence = (with_year or exact)[0], LOOKUP_AMBIGUOUS
            reason = f"{len(with_year or exact)} shows match the title"
        else:
            pick, confidence, reason = candidates[0], LOOKUP_CLOSEST, "closest search result; the title differs"
        ident = pick.tmdb_id if tmdb else pick.tvdb_id
        try:
            show = ctx.bg(tmdb_client.get_tv_show_details if tmdb else tvdb_client.get_series_details, ident)
        except error as exc:
            notes.append(f"{source} details unavailable: {exc}")
            return None
        episode_details: dict = {}
        if season and episode:
            try:
                episode_details = ctx.bg(
                    tmdb_client.get_tv_episode_details if tmdb else tvdb_client.get_episode_details,
                    ident, season, episode,
                )
            except error:
                notes.append(f"{source}: no S{season:02d}E{episode:02d} for '{pick.name}'")
        # Episode fields first: more specific than the show's (title,
        # description and air date of THIS episode).
        fields = self._empty_fields(ctx, episode_details)
        for key, value in self._empty_fields(ctx, show).items():
            fields.setdefault(key, value)
        fields = {k: v for k, v in fields.items() if k not in skip}
        if not fields and ctx.work.metadata.content_type is not ContentType.UNSET:
            return StepResult.nothing()
        found_episode = bool(episode_details)
        sources = (tmdb_values("tv", ident, season if found_episode else None, episode if found_episode else None,
                               imdb_id=show.get("_imdb_id", ""), tvdb_id=show.get("_tvdb_id", ""), how="title")
                   if tmdb else tvdb_values(ident, "title"))
        fill = LookupFill(source, f"{pick.name} ({pick.year})", fields, ContentType.TV, pick.year, sources)
        return StepResult.suggestion(fill, confidence, f"{reason} ({source} id {ident})")

    def _tv_by_id(self, ctx, source, ident: int, season, episode, notes, skip=frozenset()) -> StepResult | None:
        """The show the file's own TMDB_ID / TVDB_ID names (a certain match, but still a suggestion)."""
        tmdb = source == "TMDB"
        client, error = (tmdb_client, tmdb_client.TMDBError) if tmdb else (tvdb_client, tvdb_client.TVDBError)
        try:
            show = ctx.bg(tmdb_client.get_tv_show_details if tmdb else tvdb_client.get_series_details, ident)
        except error as exc:
            notes.append(f"{source} details unavailable: {exc}")
            return None
        episode_details: dict = {}
        if season and episode:
            try:
                episode_details = ctx.bg(
                    tmdb_client.get_tv_episode_details if tmdb else tvdb_client.get_episode_details, ident, season, episode,
                )
            except error:
                notes.append(f"{source}: no S{season:02d}E{episode:02d} in the show the file's id names")
        fields = self._empty_fields(ctx, episode_details)
        for key, value in self._empty_fields(ctx, show).items():
            fields.setdefault(key, value)
        fields = {k: v for k, v in fields.items() if k not in skip}
        if not fields and ctx.work.metadata.content_type is not ContentType.UNSET:
            return StepResult.nothing()
        found_episode = bool(episode_details)
        sources = (tmdb_values("tv", ident, season if found_episode else None, episode if found_episode else None,
                               imdb_id=show.get("_imdb_id", ""), tvdb_id=show.get("_tvdb_id", ""), how="manual")
                   if tmdb else tvdb_values(ident, "manual"))
        name = show.get("show_title", "") or f"{source} {ident}"
        year = (show.get("release_date") or "")[:4]
        fill = LookupFill(source, f"{name} ({year})", fields, ContentType.TV, year, sources)
        return StepResult.suggestion(fill, LOOKUP_ID, f"the {source} id in the file ({ident})")

    @staticmethod
    def _empty_fields(ctx: VideoCtx, details: dict) -> dict[str, Any]:
        return {
            key: value for key, value in details.items()
            if not key.startswith("_") and value not in ("", None) and _is_empty(ctx, key)
        }

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        fill: LookupFill = result.value
        lines = []
        if ctx.work.metadata.content_type is ContentType.UNSET:
            ctx.work.metadata.content_type = fill.content_type
            ctx.work.dirty = True
            lines.append(f"content_type = {fill.content_type.value!r}")
        lines += _set_fields(ctx, fill.fields)
        lines += _set_sources(ctx, fill.sources)
        return [f"from {fill.source} '{fill.label}'"] + lines


# --- step 3b: ThePornDB / StashDB, file match only -------------------------------------------------------------------


class SceneLookupStep(VideoStep):
    key = "scene_lookup"
    label = "Fill from ThePornDB/StashDB (file match only)"
    description = (
        "Asks ThePornDB and StashDB (the ones you have a key for, Tools > API Keys) whether they know THIS file, by "
        "its fingerprint (the OSHASH: only that hash leaves the computer, never the name; the MD5 only when "
        "Preferences > Look Up allows it). A file match fills the fields that are still EMPTY (title, date, "
        "studio, performers, tags, description) as a SUGGESTION: it always goes to Needs review, whatever the "
        "confidence threshold is, and nothing is written until you have looked at it (Metadata > Look Up shows the "
        "same record with a per-field review). A title match is never used here. Off by default."
    )
    default_enabled = False
    options = (
        OptionSpec("theporndb", "Ask ThePornDB", "bool", True),
        OptionSpec("stashdb", "Ask StashDB", "bool", True),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        opts = self.options_for(ctx)
        clients = [(name, client) for name, client, wanted in (
            ("ThePornDB", theporndb_client, opts["theporndb"]), ("StashDB", stashdb_client, opts["stashdb"]),
        ) if wanted and client.get_api_key()]
        if not clients:
            return StepResult.nothing(note="no ThePornDB or StashDB key is set (Tools > API Keys)")
        if not any(_is_empty(ctx, f) for f in scene_lookup.FILL_FIELDS):
            return StepResult.nothing()
        try:
            hashes = ctx.bg(scene_lookup.file_hashes, ctx.original, scene_lookup.md5_enabled(), ctx.env.cancel_event)
        except OSError as exc:
            return StepResult.nothing(note=f"the file could not be hashed ({exc})")
        if not hashes.oshash:
            return StepResult.nothing(note="the file is under 128 KB, too small for a fingerprint match")
        notes: list[str] = []
        for name, client in clients:
            held = ref_in(ctx.work.metadata, "stashdb" if client is stashdb_client else "theporndb",
                          ("scene",) if client is stashdb_client else ("scene", "movie", "jav"))
            try:
                if held is not None:  # the file already names its record: ask for exactly that, no hash search
                    one = ctx.bg(client.get, held.id) if client is stashdb_client else ctx.bg(client.get, held.id, held.kind)
                    one.match_kind = scene_lookup.MATCH_ID
                    found = [one]
                elif client is stashdb_client:
                    found = ctx.bg(client.search_by_hash, hashes.oshash, hashes.md5)
                else:
                    found = ctx.bg(client.search_by_hash, hashes.oshash)
            except scene_lookup.SceneLookupError as exc:
                notes.append(f"{name} unavailable: {exc}")
                continue
            found = [r for r in found if r.is_file_match or r.is_id_match]
            if not found:
                notes.append(f"{name} has no record with this file's fingerprint")
                continue
            if len(found) > 1:
                notes.append(f"{name}: {len(found)} records share this fingerprint; choose one in Metadata > Look Up")
                continue
            result = found[0]
            fields = {k: v for k, v in scene_lookup.scene_fields(result, ctx.work.metadata.comment).items()
                      if _is_empty(ctx, k)}
            if not fields and ctx.work.metadata.content_type is not ContentType.UNSET:
                return StepResult.nothing(note=f"{name} matched the file, but every field it could fill is already set")
            fill = LookupFill(name, result.title or "(untitled)", fields, scene_lookup.CONTENT_TYPE, result.year,
                              scene_values(result))
            why = (f"the {name} id in the file ({result.external_id})" if result.is_id_match
                   else f"file match: {name} has this file's fingerprint (id {result.external_id})")
            suggestion = StepResult.suggestion(
                fill, SCENE_FILE_CONFIDENCE, why,
                review_only=True,
            )
            return _with_note(suggestion, "; ".join(notes))
        return StepResult.nothing(note="; ".join(notes))

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        fill: LookupFill = result.value
        lines = []
        if ctx.work.metadata.content_type is ContentType.UNSET:
            ctx.work.metadata.content_type = fill.content_type
            ctx.work.dirty = True
            lines.append(f"content_type = {fill.content_type.value!r}")
        lines += _set_fields(ctx, fill.fields)
        lines += _set_sources(ctx, fill.sources)
        controlled_vocab.remember_porn_tags(fill.fields.get("genre_tags", ""))  # its tags join the Porn tag list
        return [f"from {fill.source} '{fill.label}'"] + lines


# --- the poster, from the ids the file already carries ---


@dataclass
class PosterChoice:
    service: str
    jpeg: bytes
    description: str

    def __str__(self) -> str:
        return f"poster from {self.service} ({self.description})"


class PosterStep(VideoStep):
    key = "get_poster"
    label = "Get the poster from the file's source ids"
    description = (
        "A file that carries a source id (TMDB, TheTVDB, IMDb, ThePornDB or StashDB, put there by a lookup) and has no "
        "poster yet gets the poster of THAT record, without searching: only a true poster is used (the service's own "
        "poster, portrait, big enough; never a frame from the video). A film or TV episode asks TMDB, TheTVDB or IMDb "
        "(through TMDB), a scene asks ThePornDB or StashDB. A file that already has a poster is left alone. Runs after "
        "the lookup steps, so an id they just found is used. Saved inside the file with the other changes. Off by default."
    )
    default_enabled = False

    def execute(self, ctx: VideoCtx) -> StepResult:
        meta = ctx.work.metadata
        if meta.cover_info or meta.cover_pending is not None:
            return StepResult.nothing()
        if not poster_fetch.services_for(meta):
            return StepResult.nothing()
        outcome = ctx.bg(poster_fetch.fetch_poster, meta, cancel_event=ctx.env.cancel_event)
        if not outcome.found:
            return StepResult.nothing(note=f"no poster: {outcome.why_not()}")
        return StepResult.suggestion(
            PosterChoice(outcome.service, outcome.poster.jpeg, outcome.poster.description()), LOOKUP_ID,
            f"the {outcome.service} id in the file",
        )

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        choice: PosterChoice = result.value
        ctx.work.metadata.cover_pending = choice.jpeg
        ctx.work.metadata.cover_remove = False
        ctx.work.dirty = True
        return [f"poster = {choice.description} (from {choice.service})"]


# --- music videos: the retired track title ---


@dataclass
class TrackTitleMove:
    title: str

    def __str__(self) -> str:
        return f"title={self.title!r}"


class TrackTitleStep(VideoStep):
    key = "track_title_to_title"
    label = "Use the track title as the title"
    description = (
        "Music videos: the title is the song and the old separate 'track title' field is gone. A Music Video "
        "that still has an old track title in the file and an EMPTY title gets that value as its title, as a "
        "SUGGESTION: it always goes to Needs review. When both exist and differ nothing is changed. The old "
        "value stays in the file. Off by default."
    )
    default_enabled = False

    def execute(self, ctx: VideoCtx) -> StepResult:
        if not can_move_track_title(ctx.work.metadata):
            return StepResult.nothing()
        title = ctx.work.metadata.legacy_track_title.strip()
        return StepResult.suggestion(
            TrackTitleMove(title), 0.9, "the file has an old track title and no title", review_only=True,
        )

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> list[str]:
        if move_track_title(ctx.work.metadata):
            ctx.work.dirty = True
            return [f"title = {ctx.work.metadata.title!r} (from the old track title)"]
        return []


# --- step 4: subtitles ----------------------------------------------------------------


@dataclass
class SubtitleChoice:
    candidate: Any  # opensubtitles_client.SubtitleCandidate

    def __str__(self) -> str:
        c = self.candidate
        return f"{c.language} subtitle '{c.release_name}'" + ("" if c.hash_matched else " (sync not guaranteed)")


@dataclass
class LyricsChoice:
    """An LRCLIB result for a music video: the SRT text is already built (no offset)."""
    lyrics: Any  # redactor_common.core.lrclib.Lyrics
    match: Any   # redactor_common.core.lrclib.MatchScore
    srt_text: str
    language: str

    def __str__(self) -> str:
        l = self.lyrics
        return f"{self.language} lyrics '{l.artist_name} - {l.track_name}' from LRCLIB ({self.match.label})"


class SubtitlesStep(VideoStep):
    key = "subtitles"
    label = "Download a missing subtitle (OpenSubtitles, LRCLIB lyrics)"
    description = (
        "For a video with no subtitle file beside it, looks one up on OpenSubtitles and saves it as "
        "<name>.<lang>.srt. A match by the video's own hash is applied (it syncs by construction); a match "
        "by title may not be in sync and goes to Needs review. Off by default: it needs an API key and "
        "counts against your daily download quota. Music videos can use time-synced lyrics from LRCLIB instead "
        "(option below, off by default): only a match with the same artist, title and length is saved, the rest "
        "go to Needs review."
    )
    default_enabled = False
    options = (
        OptionSpec("language", "Language code", "str", "en", max_length=8),
        OptionSpec(
            "use_lrclib", "Use LRCLIB lyrics for music videos", "bool", False,
            tooltip="For a Music Video, look for time-synced lyrics on LRCLIB (no key needed) before OpenSubtitles. "
            "Only a match with the same artist and title and a length within 2 s is saved by itself; every other "
            "match is only a suggestion in the review. The lyrics' language is unknown, so a single Language tag "
            "on the video is used, else 'und'.",
        ),
    )

    def _lyrics_result(self, ctx: VideoCtx) -> StepResult | None:
        """The LRCLIB suggestion for a music video, None when it found nothing usable (OpenSubtitles may go on)."""
        query = lyrics_subtitles.query_for_video(ctx.work)
        match = ctx.bg(lyrics_subtitles.best_timed, query)
        if match is None:
            return None
        srt = lyrics_subtitles.srt_for(match.lyrics, query.duration)
        if not srt:
            return None
        choice = LyricsChoice(match.lyrics, match.score, srt, lyrics_subtitles.guess_language(ctx.work))
        if match.score.is_exact:
            return StepResult.suggestion(choice, SUBTITLE_HASH_CONFIDENCE, "LRCLIB lyrics: same artist, title and length")
        return StepResult.suggestion(
            choice, SUBTITLE_TEXT_CONFIDENCE, f"LRCLIB lyrics, {match.score.label}: sync is not guaranteed"
        )

    def execute(self, ctx: VideoCtx) -> StepResult:
        if any(s.lower().endswith(".srt") for s in sidecar_suffixes(str(ctx.video.path))):
            return StepResult.nothing()
        if (
            self.options_for(ctx).get("use_lrclib")
            and ctx.work.metadata.content_type is ContentType.MUSIC_VIDEO
        ):
            try:
                found_lyrics = self._lyrics_result(ctx)
            except lyrics_subtitles.LrclibError as exc:
                found_lyrics = None
                lyrics_note = f"LRCLIB unavailable: {exc}"
            else:
                lyrics_note = ""
            if found_lyrics is not None:
                return found_lyrics
            if opensubtitles_client.get_api_key() is None:
                return StepResult.nothing(note=lyrics_note or "no synced lyrics found on LRCLIB")
        language = opensubtitles_client.clean_language_code(self.options_for(ctx)["language"].strip() or "en")
        try:
            found = ctx.bg(opensubtitles_client.search_by_hash, ctx.original, language)
            hashed = bool(found)
            if not found:
                guess = parse_release_name(ctx.video.path.stem)
                query = guess.title
                if guess.kind == "tv" and guess.season and guess.episode:
                    query += f" S{guess.season:02d}E{guess.episode:02d}"
                elif guess.year:
                    query += f" {guess.year}"
                found = ctx.bg(opensubtitles_client.search_by_title, query, language)
        except opensubtitles_client.OpenSubtitlesError as exc:
            return StepResult.nothing(note=f"OpenSubtitles unavailable: {exc}")
        if not found:
            return StepResult.nothing(note=f"no {language} subtitle found")
        best = max(found, key=lambda c: c.download_count)
        if hashed:
            return StepResult.suggestion(
                SubtitleChoice(best), SUBTITLE_HASH_CONFIDENCE, "matched by the video's own hash: in sync"
            )
        return StepResult.suggestion(
            SubtitleChoice(best), SUBTITLE_TEXT_CONFIDENCE, "matched by title: sync is not guaranteed"
        )

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> str:
        if isinstance(result.value, LyricsChoice):
            choice = result.value
            try:
                path = ctx.video.save_subtitle_sidecar(choice.srt_text, language=choice.language, overwrite=False)
            except FileExistsError as exc:
                return f"kept the existing {os.path.basename(exc.filename or 'subtitle file')}"
            return f"saved {path.name} (LRCLIB lyrics)"
        candidate = result.value.candidate
        text = ctx.bg(opensubtitles_client.download_subtitle_text, candidate.file_id)
        try:
            path = ctx.video.save_subtitle_sidecar(text, language=candidate.language, overwrite=False)
        except FileExistsError as exc:
            return f"kept the existing {os.path.basename(exc.filename or 'subtitle file')}"
        return f"saved {path.name}"


@dataclass
class TranslationChoice:
    """A machine translation of a foreign subtitle, ready to be written as <name>.en.srt after review."""
    cues: Any          # list[redactor_common.core.srt.Cue], English text, original timing
    source_lang: str
    engine: str
    source_name: str

    def __str__(self) -> str:
        return f"English machine translation of {self.source_name} ({self.source_lang}, {self.engine})"


# A translation is only ever a suggestion: this stays far below any sensible auto-apply threshold.
TRANSLATION_CONFIDENCE = 0.3


class SubtitleTranslateStep(VideoStep):
    key = "translate_subtitles"
    label = "Translate non-English subtitles of music videos to English"
    description = (
        "For a Music Video with a non-English subtitle file beside it and no English one, translates that subtitle "
        "to English with the engine set under Tools > Preferences > Translation (your own LibreTranslate server or "
        "the argos-translate command; nothing is built into this program). The result is a machine translation: "
        "it is never written by itself, it goes to Needs review, and when approved it is saved as <name>.en.srt "
        "next to the original (which is not touched) and marked as machine translated. Off by default."
    )
    default_enabled = False

    def execute(self, ctx: VideoCtx) -> StepResult:
        if ctx.work.metadata.content_type is not ContentType.MUSIC_VIDEO:
            return StepResult.nothing()
        externals = [
            (e, subtitle_translation.iso1(e.language))
            for e in subtitle_translation.si.find_external_subs(str(ctx.video.path))
        ]
        if not externals or any(lang == "en" for _e, lang in externals):
            return StepResult.nothing()
        ext, language = externals[0]
        source = subtitle_translation.SubtitleSource(
            os.path.basename(ext.path), subtitle_translation.KIND_SIDECAR, language, ext.path
        )
        try:
            text = subtitle_translation.read_text(source)
        except Exception as exc:  # noqa: BLE001 -- a note, never a failure of the whole recipe
            return StepResult.nothing(note=f"not translated: {os.path.basename(ext.path)} could not be read ({exc})")
        language, _guessed = subtitle_translation.source_language(source, text)
        if not language:
            return StepResult.nothing(
                note=f"not translated: the language of {os.path.basename(ext.path)} is unknown "
                "(translate it from Media > Translate Subtitles to English and confirm the language)"
            )
        cues = subtitle_translation.parse_srt(text)
        if not cues:
            return StepResult.nothing()
        translator = translation_settings.make_translator()
        result = ctx.bg(
            subtitle_translation.translate_all, cues, language, translation_settings.target_language(), translator
        )
        if result.error or result.cancelled or result.failed or not result.translated:
            reason = result.error or "some cues could not be translated"
            return StepResult.nothing(note=f"translation not used: {reason}")
        return StepResult.suggestion(
            TranslationChoice(result.cues, language, result.engine or translator.name, source.label),
            TRANSLATION_CONFIDENCE, "machine translation: always check it before keeping it",
        )

    def apply_suggestion(self, ctx: VideoCtx, result: StepResult) -> str:
        choice = result.value
        try:
            path = subtitle_translation.write_translation(ctx.video, choice.cues, choice.engine, choice.source_lang)
        except FileExistsError as exc:
            return f"kept the existing {os.path.basename(exc.filename or 'subtitle file')}"
        return f"saved {path.name} (machine translation, {choice.engine})"


# --- step 5: MKV -> MP4 remux ------------------------------------------------------------


class RemuxStep(VideoStep):
    key = "remux_mkv_to_mp4"
    label = "Remux MKV to MP4 (lossless)"
    description = (
        "Repackages an MKV as MP4 without re-encoding (as Media > Remux to MP4). Changes the container, so "
        "it is off by default. The result is verified: if any video/audio/subtitle track or attachment "
        "would be lost, or it is shorter, nothing is changed. The app's tags are written onto the MP4. "
        "Not run while 'Convert to MP4/MKV and ingest subtitles' is on in the same recipe: that step never "
        "moves an MKV to MP4 (the two would fight), so turn one of them off."
    )
    default_enabled = False

    def execute(self, ctx: VideoCtx) -> StepResult:
        if os.path.splitext(ctx.current)[1].lower() != ".mkv":
            return StepResult.nothing()
        if getattr(ctx, "batch", {}).get(ConvertStep.key):
            return StepResult.nothing(
                note="not remuxed: 'Convert to MP4/MKV and ingest subtitles' is on in this recipe and keeps MKV files "
                "as MKV; turn one of the two steps off"
            )
        if os.path.islink(ctx.video.path):
            return StepResult.nothing(note="not remuxed: the file is a symbolic link")
        final = os.path.splitext(ctx.original)[0] + ".mp4"
        if os.path.lexists(final):
            return StepResult.nothing(note=f"not remuxed: {os.path.basename(final)} already exists")
        target = ctx.new_scratch(".mp4")
        audio_language = conversion_plan.get_conversion_settings(ctx.env.setting).default_audio_language
        ok, message = ctx.bg(remux_to_mp4, ctx.current, target, audio_language=audio_language)
        if not ok:
            _remove_quietly(target)
            return StepResult.failed(f"not remuxed, the file is untouched: {(message or 'remux failed').strip().splitlines()[-1]}")
        problem = ctx.bg(verify_remux, ctx.current, target)
        if problem:
            _remove_quietly(target)
            return StepResult.failed(f"not remuxed, the file is untouched: {problem}")
        ctx.adopt(target)
        ctx.work.metadata.container = "mp4"
        return StepResult.applied("remuxed to MP4 (every track kept; verified)")


# --- steps 7 and 8: rename and move (after the save) ------------------------------------


def _pattern_values(ctx: VideoCtx) -> dict[str, str]:
    """The live file's placeholder values with the Rename dialog's saved
    zero-padding applied to the episode number."""
    return _pad_episode(placeholder_values(ctx.video.metadata), ctx.env.setting)


def _ascii_only(ctx: VideoCtx) -> bool:
    return ctx.env.setting("rename", "ascii_only", "0") == "1"


_GROUP_RE = re.compile(r"\[[^\[\]]*\]|\([^()]*\)|\{[^{}]*\}")


def empty_required_tokens(pattern: str, values: dict[str, str]) -> list[str]:
    """Placeholders outside the pattern's optional (...)/[...]/{...}
    groups whose value is empty -- renaming with them would leave a hole
    ("Show - S E - ")."""
    required = pattern
    while True:
        stripped = _GROUP_RE.sub("", required)
        if stripped == required:
            break
        required = stripped
    return [t for t in re.findall(r"%(\w+)%", required) if t in values and not values[t].strip()]


def _log_moves(ctx: "VideoCtx", label: str, pairs: list[tuple[str, str]], **kwargs: Any) -> None:
    """Records a Redact rename/move: in the run's rename log (File > Undo Last Rename), whose on_move hook
    passes it on to Tools > History; without a rename log (the command line) straight to Tools > History."""
    if not pairs:
        return
    if ctx.env.rename_log is not None:
        ctx.env.rename_log.record(label, pairs, **kwargs)
    else:
        history.record_moves(pairs)


class RenameStep(PatternStep):
    key = "rename"
    label = "Rename by pattern"
    description = (
        "Renames the finished file with a %field% pattern (empty: the one you last used in Rename/Export by "
        "Pattern, with its zero-pad and ASCII choices), never overwriting anything; logged for File > Undo "
        "Last Rename. Sidecar files (poster, subtitles) are renamed with it. Always runs last, after the "
        "file is saved. A file whose pattern would leave a required field empty is left as it is. Off by "
        "default: tick it in Edit > Edit Redact Recipe to turn it on."
    )
    position = "after_save"
    run_when_unchanged = True  # a file with nothing to save is still renamed/moved
    default_enabled = False  # like Move: never switched on just because a pattern exists
    options = (
        OptionSpec("pattern", "Pattern (empty: the most recently used)", "str", "", max_length=300),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        pattern = self.effective_pattern(ctx)
        if not pattern:
            return StepResult.nothing(note="not renamed: no rename pattern saved yet (use Rename/Export by Pattern once)")
        values = _pattern_values(ctx)
        missing = empty_required_tokens(pattern, values)
        if missing:
            return StepResult.nothing(note="not renamed: empty " + ", ".join(f"%{m}%" for m in missing))
        old_path = str(ctx.video.path)
        stem, ext = os.path.splitext(os.path.basename(old_path))
        new_stem = render_filename(values, pattern, fallback=stem, ascii_only=_ascii_only(ctx))
        new_path = unique_path(os.path.dirname(old_path), new_stem, ext, set(), own_path=old_path)
        if os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(os.path.abspath(old_path)):
            return StepResult.nothing()
        sidecars = sidecar_pairs(old_path, new_path)
        try:
            rename_no_clobber(old_path, new_path)
        except OSError as exc:
            return StepResult.failed(f"couldn't rename to {os.path.basename(new_path)!r}: {exc}")
        ctx.video.path = Path(new_path)
        pairs = [(old_path, new_path)]
        problems = []
        for old, new in sidecars:
            try:
                rename_no_clobber(old, new)
                pairs.append((old, new))
            except OSError as exc:
                problems.append(f"{os.path.basename(old)} not renamed ({exc})")
        _log_moves(ctx, "Redact: rename", pairs)
        result = StepResult.applied(f"renamed to {os.path.basename(new_path)!r}" + (f" (+{len(pairs) - 1} sidecar file(s))" if len(pairs) > 1 else ""))
        return _with_note(result, "; ".join(problems))


class MoveIntoFoldersStep(PatternStep):
    key = "move_into_folders"
    trail_kind = "move"
    label = "Move into folders under the library root"
    description = (
        "Moves the finished file into a folder tree under the library root chosen in Rename/Export by "
        "Pattern > Move into folders (the pattern may contain '/': e.g. %show_title%/Season "
        "%season_number%/%title%, the default when none was used before). Missing folders are created, nothing is overwritten, and the move is "
        "logged for Undo Last Rename. Sidecar files move with the video. Off by default; needs a library root."
    )
    position = "after_save"
    run_when_unchanged = True  # a file with nothing to save is still renamed/moved
    default_enabled = False
    options = (
        OptionSpec(
            "pattern", "Folder/file pattern (empty: the last one used)", "str", "", max_length=300,
            tooltip=f"Empty: the pattern last used in Rename/Export by Pattern > Move into folders, else {DEFAULT_MOVE_PATTERN}",
        ),
    )

    def execute(self, ctx: VideoCtx) -> StepResult:
        root = ctx.env.setting("rename", "library_root", "").strip()
        if not root:
            return StepResult.nothing(note="not moved: no library root (choose one in Rename/Export by Pattern > Move into folders)")
        pattern = self.effective_pattern(ctx)
        values = _pattern_values(ctx)
        missing = empty_required_tokens(pattern.replace("/", " ").replace("\\", " "), values)
        if missing:
            return StepResult.nothing(note="not moved: empty " + ", ".join(f"%{m}%" for m in missing))
        old_path = str(ctx.video.path)
        planned = plan_moves(
            [ctx.video], root, pattern, lambda _v: values, lambda _v: old_path, ascii_only=_ascii_only(ctx),
        )[0]
        if planned.blocking:
            return StepResult.nothing(note=f"not moved: {planned.warning}")
        if planned.is_noop:
            return StepResult.nothing()
        sidecars = sidecar_moves(planned)
        trash = ctx.env.trash or move_to_trash
        try:
            moved = execute_move(planned.old_path, planned.new_path, copy=False, trash=trash)
        except Exception as exc:  # noqa: BLE001 -- one file's problem is its own failure
            return StepResult.failed(f"couldn't move to {planned.relative_path()}: {exc}")
        ctx.video.path = Path(moved.new_path)
        created = list(moved.created_dirs)
        pairs: list[tuple[str, str]] = [] if moved.original_kept else [(planned.old_path, moved.new_path)]
        trashed = [(planned.old_path, moved.new_path)] if moved.original_trashed else []
        problems = [moved.warning] if moved.warning else []
        for sidecar in sidecars:
            try:
                done = execute_move(sidecar.old_path, sidecar.new_path, copy=False, trash=trash)
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{os.path.basename(sidecar.old_path)} not moved ({exc})")
                continue
            created += done.created_dirs
            if not done.original_kept:
                pairs.append((sidecar.old_path, done.new_path))
            if done.original_trashed:
                trashed.append((sidecar.old_path, done.new_path))
            if done.warning:
                problems.append(done.warning)
        _log_moves(
            ctx, "Redact: move into folders", pairs, created_dirs=created, trashed=trashed, root=planned.root,
        )
        text = f"moved to {planned.relative_path()}"
        if sidecars:
            text += f" (+{len(sidecars)} sidecar file(s))"
        return _with_note(StepResult.applied(text), "; ".join(problems))


# --- step 9: convert to MP4/MKV and ingest subtitles (after the save) --------------------


def dry_run_line(actions: list) -> str:
    """The explicit report line for a dry run: "DRY RUN: would convert a.avi -> a.mkv (legacy container .avi);
    would recycle a.en.srt (...)". Never a quiet 'nothing'."""
    parts = []
    for a in actions:
        if a.verb == "skip":
            parts.append(f"would skip {os.path.basename(a.path)} ({a.detail}; {a.reason})")
        else:
            detail = os.path.basename(a.detail) if a.verb == "convert" else a.detail
            parts.append(f"would {a.verb} {os.path.basename(a.path)} -> {detail} ({a.reason})")
    return "DRY RUN, nothing changed: " + ("; ".join(parts) if parts else "nothing to do")


def make_safe_run(
    setting: Callable[[str, str, str], str],
    safety: "library_safety.SafetySettings | None" = None,
    *,
    dry_run: bool | None = None,
    trash: Callable[[str], None] | None = None,
    path: str = "",
) -> "library_safety.SafeRun":
    """The SafeRun for a conversion, from the Library safety settings (with the undo log beside the quarantine
    folder when one is set). `dry_run` overrides the persistent setting: Media > Convert to MP4/MKV (rules)
    and the Redact confirm both ask per run (RedactEnv.convert_dry_run); None falls back to the setting."""
    safety = safety or library_safety.get_safety_settings(setting)
    quarantine = safety.quarantine_root
    library_root = setting("rename", "library_root", "").strip()
    if safety.quarantine_beside and path:
        # The shared policy's "quarantine, no folder set": a dated folder beside the file being converted.
        quarantine = library_root = os.path.dirname(os.path.abspath(path))
    log = None
    if quarantine:
        log_folder = safety.undo_log_folder or os.path.join(quarantine, "_undo logs")
        log = library_safety.UndoLog(os.path.join(log_folder, f"undo {library_safety.stamp_now()[:10]}.tsv"))
    return library_safety.SafeRun(
        library_root=library_root, quarantine_root=quarantine, log=log,
        dry_run=safety.dry_run if dry_run is None else dry_run, recent_minutes=safety.recent_minutes, trash=trash,
        delete_after_conversion=safety.delete_after_conversion, keep_originals=safety.keep_originals,
        tolerance_seconds=safety.duration_tolerance_seconds, tolerance_fraction=safety.duration_tolerance_fraction,
    )


def original_fate_text(
    safety: "library_safety.SafetySettings", convert_enabled: bool, dry_run: bool | None = None,
) -> str:
    """What really happens to an original in this Redact run, for the confirm question and the results header:
    a plain save always puts the original in the Recycle Bin; a conversion (ConvertStep) follows this run's
    dry-run choice (`dry_run`; None = the old setting) and the Library safety settings (quarantine folder /
    delete-after-conversion)."""
    text = "Each changed file is saved in place; its original goes to the Recycle Bin (restore it from there to undo)."
    if not convert_enabled:
        return text
    if safety.dry_run if dry_run is None else dry_run:
        return text + " Conversions run as a DRY RUN this time: listed, not done."
    if safety.delete_after_conversion:
        return text + " A converted original is DELETED, not recycled (delete-after-conversion is on)."
    if safety.quarantine_beside:
        return text + " A converted original is moved to a dated 'originals' folder beside the file (with an undo log)."
    if safety.quarantine_root:
        return text + f" A converted original is moved to the quarantine folder {safety.quarantine_root}."
    return text + " A converted original goes to the Recycle Bin too (on a network or removable drive it is kept beside the result instead)."


class ConvertStep(VideoStep):
    key = "convert_library"
    label = "Convert to MP4/MKV and ingest subtitles"
    description = (
        "Leaves the finished file ready for a media server: a legacy container (AVI, WMV, MOV...) becomes MP4 or "
        "MKV by stream copy (only video no container can hold is re-encoded); series episodes over the bitrate "
        "limits are re-encoded and scaled to 720p; external .srt files beside the video are muxed in (an MP4 "
        "becomes an MKV) and removed afterwards. Every result is verified before the swap and the original goes "
        "to the Recycle Bin or the quarantine folder (or is deleted, if you turned that on). Disc images are "
        "left alone. A file whose container does not match its extension (an .mp4 that is really an FLV) is converted "
        "despite its name, by a lossless remux. An MKV stays an MKV (so 'Remux MKV to MP4' is not run while this step is on). All limits "
        "are in Preferences > Conversion. Every Redact run with this step on asks 'Dry run the conversion step' "
        "in its confirm question (ticked by default): the report then shows a 'DRY RUN: would ...' line per file "
        "and nothing is converted; untick it to convert for real. On the command line, redact --convert-dry-run "
        "does the same. Media > Convert to MP4/MKV (rules) runs the same rules with its own per-run dry-run "
        "choice. Off by default."
    )
    position = "after_save"
    run_when_unchanged = True
    default_enabled = False

    def prepare(self, items, env) -> bool:
        """Marks the run so RemuxStep can see this step is on (ctx.batch[self.key])."""
        return True

    def execute(self, ctx: VideoCtx) -> StepResult:
        setting = ctx.env.setting
        safety = library_safety.get_safety_settings(setting)
        conv = conversion_plan.get_conversion_settings(setting)
        path = str(ctx.video.path)
        run = make_safe_run(setting, safety, dry_run=ctx.env.convert_dry_run, trash=ctx.env.trash, path=path)
        if ctx.start_mtime is not None and os.path.normcase(path) == os.path.normcase(ctx.original):
            run.known_mtime[path] = ctx.start_mtime  # Redact's own save must not make the file look "in use"
        is_series = conversion_is_series(ctx.video.metadata.content_type, ctx.video.path.stem)
        try:
            outcome = ctx.bg(
                conversion_run.process_file, run, path, conv, is_series=is_series, cancel_event=ctx.env.cancel_event,
                carry_tags=False,  # this step rewrites the tags itself, from its working copy, just below
            )
        except library_safety.SafetyError as exc:
            return StepResult.failed(f"not converted, the file is untouched: {exc}")
        skipped = [a for a in run.actions if a.verb == "skip"]
        extra = "; ".join(outcome.notes)
        if not outcome.converted:
            note = skipped[0].detail if skipped else outcome.message
            if outcome.message == "Cancelled" or any(a.detail.endswith("Cancelled)") for a in skipped):
                return StepResult.failed("not converted: cancelled (the file is untouched)")
            return StepResult.nothing(note="; ".join(n for n in (note, extra) if n))
        if run.dry_run:
            return StepResult.nothing(note="; ".join(n for n in (dry_run_line(run.actions), extra) if n))
        # The tags the app had written are carried by ffmpeg only where the container allows; write them again.
        keep = copy.deepcopy(ctx.video.metadata)
        ctx.video.path = Path(outcome.final_path)
        ctx.video.metadata = keep
        ctx.video.save_error = ""
        # Not journaled in Tools > History: the tags are carried over unchanged, the original is in the Recycle
        # Bin / quarantine.
        ctx.bg(ctx.video.save, journal=False)
        problem = ctx.video.save_error
        ctx.bg(ctx.video.load)
        ctx.video.dirty = False
        done = [f"{a.verb}: {os.path.basename(a.path)}" for a in run.actions if a.done]
        result = StepResult.applied(f"converted to {os.path.basename(outcome.final_path)} ({'; '.join(done)})")
        return _with_note(result, f"converted, but the tags could not be rewritten: {problem}" if problem else "")


# --- catalogue and recipe ----------------------------------------------------------------


def build_catalogue(
    pattern_history: Callable[[], list[str]] = load_pattern_history,
    env: RedactEnv | None = None,
    sample: Callable[[], dict[str, str] | None] | None = None,
) -> list[Step]:
    """The steps, in default run order ("last" steps pinned after the
    rest by the engine). Rename and Move start OFF (renaming files is
    never switched on behind the user's back); a saved recipe keeps
    whatever it saved. `env` supplies the history/settings the pattern trail
    reads (default: the app's own); `sample` returns placeholder values
    for the pattern previews (None: a built-in Show/Season/Episode)."""
    if env is None:
        env = RedactEnv(pattern_history=pattern_history)
    return [
        CheckRepairStep(),
        FilenameTagsStep(env=env, sample=sample),
        PathTagsStep(env=env, sample=sample),
        LookupStep(),
        SceneLookupStep(),
        PosterStep(),
        TrackTitleStep(),
        SubtitlesStep(),
        SubtitleTranslateStep(),
        RemuxStep(),
        ConvertStep(),
        RenameStep(env=env, sample=sample),
        MoveIntoFoldersStep(env=env, sample=sample),
    ]


def pin_patterns(recipe: Recipe, catalogue: list[Step]) -> Recipe:
    """First-save pinning: `recipe` with every EMPTY pattern option set to
    the pattern currently in effect, so saving it keeps today's Rename/Export
    patterns even if those change later. Non-empty values are untouched;
    a pattern with nothing to pin (no fallback value) stays empty."""
    pinned = copy.deepcopy(recipe)
    for step in catalogue:
        for spec in step.options:
            if spec.kind != "str" or spec.fallback is None:
                continue
            stored = pinned.options.setdefault(step.key, {})
            if str(stored.get(spec.key, spec.default) or "").strip():
                continue
            value, _source = effective_option_source(spec, "")
            if value.strip():
                stored[spec.key] = value.strip()
    return pinned


def recipe_is_saved() -> bool:
    """True once a recipe has been stored in the settings file."""
    return bool(get_setting(RECIPE_SECTION, RECIPE_KEY, "").strip())


def recipe_to_setting(recipe: Recipe) -> str:
    """One-line JSON for the ini value (configparser can't hold newlines
    in a value). Holds step keys, flags, patterns and the threshold --
    no secrets."""
    import json

    return json.dumps(recipe.to_dict(), separators=(",", ":"))


def recipe_from_setting(text: str, catalogue: list[Step]) -> Recipe:
    """The saved recipe; nothing saved (or unreadable) means the defaults."""
    recipe = Recipe.from_json(text)
    for stored in recipe.options.values():
        if isinstance(stored, dict) and isinstance(stored.get("pattern"), str):
            stored["pattern"] = modernize_pattern(stored["pattern"])  # an old %track_title% means the title now
    if not (recipe.order or recipe.enabled or recipe.options):
        return Recipe.default_for(catalogue)  # nothing saved, or unreadable
    return recipe


def load_recipe(catalogue: list[Step]) -> Recipe:
    return recipe_from_setting(get_setting(RECIPE_SECTION, RECIPE_KEY, ""), catalogue)


def save_recipe(recipe: Recipe) -> None:
    set_setting(RECIPE_SECTION, RECIPE_KEY, recipe_to_setting(recipe))
