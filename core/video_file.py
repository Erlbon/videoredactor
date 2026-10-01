"""
VideoFile: in-memory representation of one loaded media file.

Analogous to the epub tool's EpubBook -- holds the file's path, its
VideoMetadata, and load/save status. The GUI's file table maps each row
to a VideoFile via Qt.UserRole (not list index), same reasoning as the
epub tool: index-based mapping breaks the moment the table gets sorted
or reordered, UserRole doesn't.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import hashlib
import tempfile

from redactor_common.core.save_errors import describe_save_error
from redactor_common.core.scan_stamp import ScanStamp, make_stamp, parse_stamp

from core.video_metadata import VideoMetadata, EDITABLE_FIELDS, ContentType
from core.mp4_backend import read_mp4_metadata, write_mp4_metadata
from core.mkv_backend import read_mkv_metadata, write_mkv_metadata
from core.ffmpeg_backend import extract_thumbnail, probe_technical_info
from core.external_tools import is_tool_available, MKVTOOLNIX, FFMPEG
from core.opensubtitles_client import clean_language_code
from core.video_fingerprint import video_fingerprint
from core.temp_names import is_app_temp_name
from core.thumb_cache import CACHE_DIR_NAME, is_valid_thumbnail, prune_once_per_session, write_thumbnail_atomically

SUPPORTED_EXTENSIONS = {".mp4", ".m4v", ".mkv"}

# Thumbnails are cached to disk (not held as bytes in memory) since a
# folder load can involve many files -- same reasoning as not holding
# every EPUB's full cover image in memory at once. Keyed by path+mtime
# so a file edited/replaced on disk gets a fresh thumbnail rather than
# serving a stale cached one. The folder is the app's own (never the shared
# temp root); core/thumb_cache.py prunes it (age/size/count) once per session.
THUMBNAIL_CACHE_DIR = Path(tempfile.gettempdir()) / CACHE_DIR_NAME


@dataclass
class VideoFile:
    path: Path
    metadata: VideoMetadata = field(default_factory=VideoMetadata)
    load_error: str = ""   # mirrors EpubBook.load_error -- distinct from save_error
    save_error: str = ""   # mirrors EpubBook.save_error (v42/v43 lesson: track per-file, don't just retry blind)
    dirty: bool = False    # unsaved bulk-edit changes pending
    _thumbnail_path: Optional[Path] = field(default=None, repr=False, compare=False)
    # Media > Check Files... result (core/file_check.CheckResult);
    # None until checked this session. Its persistent record is the scan
    # stamp in metadata.scan_stamp (see record_check / stamp).
    check: Optional[object] = field(default=None, repr=False, compare=False)
    # Whether a stamp read from the file still matches the video: True =
    # its fingerprint differs (or can't be recomputed), False = matches,
    # None = can't be verified (no fingerprint in the stamp, or ffmpeg
    # missing). Set by load() and record_check(); meaningless without a stamp.
    stamp_stale: Optional[bool] = field(default=None, repr=False, compare=False)
    # True while the ONLY unsaved change is a fresh scan stamp (no edits),
    # so a repair right after a scan isn't refused for "unsaved changes".
    # Any other assignment to `dirty` clears it (see __setattr__).
    stamp_only_dirty: bool = field(default=False, repr=False, compare=False)

    def __setattr__(self, name, value):
        if name == "dirty":
            object.__setattr__(self, "stamp_only_dirty", False)
        object.__setattr__(self, name, value)

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    @property
    def is_mkv(self) -> bool:
        return self.extension == ".mkv"

    @property
    def is_mp4(self) -> bool:
        return self.extension in (".mp4", ".m4v")

    def load(self) -> None:
        """Read metadata from disk into self.metadata.

        Checks tool availability BEFORE attempting an MKV read, rather
        than letting a missing mkvmerge surface as a raw, confusing
        FileNotFoundError/WinError2 -- that error text is ambiguous
        (it reads the same whether the video file is missing or the
        external tool is missing), and re-parsing exception text to
        guess which one happened is worse than just checking first.

        Still catches OSError explicitly for the case this WAS meant to
        cover -- a file that's been moved/deleted since being listed
        shouldn't crash the whole load pass -- as well as backend-
        specific failures, recording them in load_error rather than
        raising.
        """
        if self.is_mkv and not is_tool_available(MKVTOOLNIX):
            self.load_error = (
                "MKVToolNix (mkvmerge) not found on PATH -- install it from "
                f"{MKVTOOLNIX.download_url} to read MKV files."
            )
            return

        try:
            if self.is_mkv:
                self.metadata = read_mkv_metadata(str(self.path))
            elif self.is_mp4:
                self.metadata = read_mp4_metadata(str(self.path))
            else:
                self.load_error = f"Unsupported extension: {self.extension}"
                return
            self.load_error = ""
        except OSError as e:
            self.load_error = f"File error: {e}"
            return
        except Exception as e:  # backend-specific parse failures, etc.
            self.load_error = f"Could not read metadata: {e}"
            return

        # Technical fields (resolution, codecs, duration, frame rate,
        # container) via ffprobe -- format-agnostic, so this runs the
        # same way for MP4 and MKV rather than depending on mutagen/
        # mkvmerge separately for this (mutagen in particular doesn't
        # reliably expose video stream info). Failure here is silent
        # and non-fatal: a file with valid tags but no readable
        # technical info (e.g. ffmpeg missing) should still show its
        # tags, just with blank technical columns -- not lose the
        # whole load over a secondary probe.
        if is_tool_available(FFMPEG):
            technical = probe_technical_info(str(self.path))
            for key, value in technical.items():
                setattr(self.metadata, key, value)
            self._refresh_stamp_staleness()

    @property
    def stamp(self) -> Optional[ScanStamp]:
        """The scan stamp recorded in the file (None if none/garbled)."""
        return parse_stamp(self.metadata.scan_stamp)

    def record_check(self, result, fingerprint: str = "") -> bool:
        """Keeps a Check Files result: sets `check`, and stamps the scan
        (status, now, `fingerprint`) into the metadata, marking the file
        dirty so Save writes it like any other tag. A result that isn't a
        real scan (the tool was missing) or a file that failed to load
        gets no stamp. Returns whether a stamp was recorded."""
        self.check = result
        if self.load_error or any(f.code == "no_tool" for f in result.findings):
            return False
        self.metadata.scan_stamp = make_stamp(result.status, fingerprint).to_text()
        self.stamp_stale = False if fingerprint else None
        only_stamp = not self.dirty or self.stamp_only_dirty
        self.dirty = True
        self.stamp_only_dirty = only_stamp
        return True

    def scan_status(self) -> str:
        """The scan result to show: this session's check, else the
        status of a stamp read from the file that still matches the
        video ("" when unscanned, or the stamp is stale)."""
        if self.check is not None:
            return self.check.status
        stamp = self.stamp
        if stamp is None or self.stamp_stale is not False:
            return ""
        return stamp.status

    def _refresh_stamp_staleness(self) -> None:
        """Compares a loaded stamp's fingerprint with the video now."""
        stamp = self.stamp
        self.stamp_stale = None
        if stamp is None or not stamp.fingerprint or not is_tool_available(FFMPEG):
            return
        self.stamp_stale = video_fingerprint(str(self.path)) != stamp.fingerprint

    def stamp_text(self) -> str:
        """The stamp as the Status column shows it ("" without one):
        `<STATUS> · <date time>`, plus "(changed since)" when the video no
        longer matches it, or "(unverified)" when that can't be told."""
        stamp = self.stamp
        if stamp is None:
            return ""
        text = stamp.display()
        if self.stamp_stale:
            return f"{text} (changed since)"
        if self.stamp_stale is None:
            return f"{text} (unverified)"
        return text

    @property
    def size_bytes(self) -> Optional[int]:
        """Live filesystem size, not cached -- unlike thumbnails (which
        are expensive to regenerate) a stat() call is cheap enough to
        just always read fresh rather than cache-and-invalidate. Returns
        None if the file's vanished since being listed, matching the
        same "moved/deleted mid-session" tolerance as load()/get_thumbnail().
        """
        try:
            return self.path.stat().st_size
        except OSError:
            return None

    def save(self) -> None:
        """Write self.metadata back to disk, then read it back and
        verify the write actually took effect.

        Same proactive tool-check as load() -- checks MKVToolNix
        availability before attempting an MKV write rather than letting
        a missing tool surface as an ambiguous subprocess error.

        Only ever writes EDITABLE_FIELDS -- never the read-only technical
        block -- enforced at the backend layer (mp4_backend/mkv_backend),
        not re-checked here.

        The verify-after-write step exists specifically because of a
        real, user-reported symptom this project has now seen twice:
        the write call itself raises no exception and reports success,
        yet the edited fields are silently absent on the next load --
        strongly suggesting a subtle backend-level issue (a mutagen
        atom-type quirk, an mkvpropedit argument-parsing edge case, or
        something not yet identified) that neither backend's own error
        handling catches, because from ITS perspective nothing went
        wrong. Rather than continue guessing at the exact root cause
        without a real mutagen/MKVToolNix environment to test against,
        this makes that entire class of failure impossible to pass as
        a silent "OK": every save immediately re-reads the file and
        compares against what was intended, surfacing a specific,
        actionable save_error the moment they disagree. This roughly
        doubles the I/O cost of every save (a second full read
        immediately after the write) -- an accepted, deliberate
        tradeoff: correctness of the reported status matters more than
        save speed for a metadata editor, and a save that LIES about
        succeeding is worse than one that's merely a bit slower.
        """
        if self.is_mkv and not is_tool_available(MKVTOOLNIX):
            self.save_error = (
                "MKVToolNix (mkvpropedit) not found on PATH -- install it from "
                f"{MKVTOOLNIX.download_url} to save MKV files."
            )
            return

        write_diagnostic = ""
        try:
            if self.is_mkv:
                result = write_mkv_metadata(str(self.path), self.metadata)
                # 1 = finished with warnings (restore_app_metadata accepts
                # it too): the re-read below decides whether it stuck.
                if result.returncode not in (0, 1):
                    self.save_error = result.stderr.strip() or "mkvpropedit failed"
                    return
                # Captured even on success -- a tool can print a warning
                # to stdout/stderr while still exiting 0, and that text
                # is exactly the kind of detail worth surfacing if
                # verification below finds a mismatch, rather than
                # silently discarding it just because the process
                # "succeeded" by exit-code standards.
                write_diagnostic = "\n".join(x for x in (result.stdout, result.stderr) if x).strip()
            elif self.is_mp4:
                write_mp4_metadata(str(self.path), self.metadata)
            else:
                self.save_error = f"Unsupported extension: {self.extension}"
                return
        except OSError as e:
            self.save_error = f"File error: {describe_save_error(e)}"
            return
        except Exception as e:
            self.save_error = f"Could not save metadata: {e}"
            return

        mismatch = self._verify_write(write_diagnostic)
        if mismatch:
            self.save_error = mismatch
            return

        self.save_error = ""
        self.dirty = False

    def _verify_write(self, write_diagnostic: str = "") -> str:
        """Re-read the just-saved file and compare every EDITABLE_FIELDS
        field in self.metadata
        against what's actually on disk now. Reports EVERY mismatch
        found, not just the first -- an earlier version stopped at the
        first disagreement, which (now that content_type sorts first in
        EDITABLE_FIELDS) meant a real report could only ever confirm
        ONE broken field even when the underlying issue affects several
        or all of them, hiding the true scope of the problem from
        whoever's trying to diagnose it next.

        write_diagnostic (the write call's own stdout+stderr, captured
        even when it reported success) is appended to the message when
        present -- a tool can print a warning while still exiting 0,
        and that text is exactly the kind of thing worth seeing when
        trying to figure out why a "successful" write didn't actually
        stick. A real report already confirmed this diagnostic text
        alone was enough to redirect the investigation from "the write
        is broken" to "the write succeeds, so it must be the read" --
        so the SAME treatment is now applied to the read side: for MKV,
        mkvextract's own stderr (previously silently discarded
        entirely) is captured via read_mkv_metadata's diagnostics
        parameter and appended here too, in case a genuine extraction
        failure is the actual culprit and just needed a way to surface.

        An empty field is checked too: it must read back empty (a
        field that was never set and still reads back empty is correct,
        expected agreement, not something to flag).
        """
        read_diagnostics: dict = {}
        try:
            if self.is_mkv:
                reread = read_mkv_metadata(str(self.path), diagnostics=read_diagnostics)
            elif self.is_mp4:
                reread = read_mp4_metadata(str(self.path))
            else:
                return ""
        except Exception as e:
            return f"Save verification failed: could not re-read the file afterward ({e})"

        mismatches = []
        if reread.scan_stamp != self.metadata.scan_stamp:
            mismatches.append(
                f"'scan_stamp' (wrote {self.metadata.scan_stamp!r}, file now reads {reread.scan_stamp!r})"
            )
        for field_name in EDITABLE_FIELDS:
            expected = getattr(self.metadata, field_name, None)
            actual = getattr(reread, field_name, None)
            if expected in (None, ""):
                # An emptied field must be gone from the file too (the
                # writers delete it) -- otherwise the old value comes
                # back on the next load and the save "succeeded" for
                # nothing.
                if actual not in (None, ""):
                    mismatches.append(f"'{field_name}' (cleared, file still reads {actual!r})")
            elif actual != expected:
                mismatches.append(f"'{field_name}' (wrote {expected!r}, file now reads {actual!r})")

        if not mismatches:
            return ""

        message = (
            f"Save appeared to succeed, but {len(mismatches)} field(s) didn't stick: "
            + "; ".join(mismatches)
        )
        if write_diagnostic:
            message += f" | tool output: {write_diagnostic}"
        read_errors = "; ".join(
            f"{key}: {value}" for key, value in read_diagnostics.items()
            if value and key.endswith("_stderr")
            # Only stderr, deliberately -- mkvextract_stdout is just
            # the routine extracted XML content (or empty, in the
            # normal case where mkvextract writes to the explicit
            # output file instead), not an error signal. Surfacing it
            # here would just be noise dressed up as a diagnostic.
        )
        if read_errors:
            message += f" | read-back tool output: {read_errors}"
        return message

    def save_poster_sidecar(self, image_bytes: bytes, overwrite: bool = False) -> Path:
        """Write poster art as a sidecar JPEG next to the video file
        (`<stem>-poster.jpg`), the convention Plex/Jellyfin/Kodi already
        prefer over embedded cover art. Used for BOTH MP4 and MKV in v1
        -- deliberately not also embedding into MP4's native covr atom
        here, even though mp4_backend supports it, so poster-saving has
        one consistent immediate-write behavior across formats rather
        than MP4 writing to the video file immediately while other
        imported fields stay staged until Save.

        Unlike metadata edits, this is NOT staged/dirty -- it writes a
        brand-new sidecar file, which never risks corrupting the actual
        video file, so there's no reason to gate it behind Save.

        Raises FileExistsError (not silently replacing a poster the
        user may have picked by hand) unless `overwrite`; OSError for
        anything else -- the caller reports it.
        """
        out_path = self.path.with_name(f"{self.path.stem}-poster.jpg")
        _write_sidecar(out_path, image_bytes, overwrite)
        return out_path

    def save_subtitle_sidecar(self, subtitle_text: str, language: str = "en", overwrite: bool = False) -> Path:
        """Write a subtitle as a sidecar file (`<stem>.<lang>.srt`), the
        same convention Plex/Jellyfin/Kodi expect for external subtitle
        tracks. Sidecar for BOTH MP4 and MKV -- mkvpropedit cannot add a
        new track to an MKV (it only edits existing tags/attachments;
        adding a track needs an mkvmerge remux), so embedding was
        deliberately dropped in favor of matching MP4's simpler sidecar
        approach, keeping subtitle-saving behavior consistent across
        formats the same way poster-saving is.

        Language code goes in the filename (Plex/Jellyfin/Kodi convention
        for identifying which sidecar is which language) rather than
        needing to be read back out of file content.

        `language` must look like a language code (anything else, e.g.
        a value with a path separator from the server, becomes "und" --
        it's part of a filename). Raises FileExistsError unless
        `overwrite`, OSError for anything else, like save_poster_sidecar.
        """
        out_path = self.path.with_name(f"{self.path.stem}.{clean_language_code(language)}.srt")
        _write_sidecar(out_path, subtitle_text.encode("utf-8"), overwrite)
        return out_path

    def get_thumbnail(self, force_regenerate: bool = False) -> Optional[Path]:
        """Return a cached thumbnail path, generating it via ffmpeg if
        needed. Returns None if extraction fails (e.g. unreadable/corrupt
        video) -- caller (GUI preview) treats that as "no preview
        available," not an error to surface loudly, since a broken
        thumbnail shouldn't block editing the file's metadata.

        Cache key includes mtime so a file replaced/re-encoded on disk
        gets a fresh thumbnail rather than serving a stale cached one.
        """
        if self._thumbnail_path and self._thumbnail_path.exists() and not force_regenerate:
            return self._thumbnail_path

        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            return None

        cache_key = hashlib.sha1(f"{self.path}:{mtime}".encode("utf-8")).hexdigest()
        THUMBNAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        prune_once_per_session(THUMBNAIL_CACHE_DIR)
        out_path = THUMBNAIL_CACHE_DIR / f"{cache_key}.jpg"

        if out_path.exists() and not force_regenerate:
            if is_valid_thumbnail(out_path):
                self._thumbnail_path = out_path
                return out_path
            # Zero-byte/garbled leftover: regenerate below.

        # Extracted into a temp name and moved into place only when valid,
        # so a failed ffmpeg run never leaves a partial JPEG in the cache.
        ok = write_thumbnail_atomically(
            out_path, lambda tmp: extract_thumbnail(str(self.path), tmp)
        )
        if not ok:
            return None
        self._thumbnail_path = out_path
        return out_path


def _write_sidecar(out_path: Path, data: bytes, overwrite: bool) -> None:
    """Writes `data` to `out_path`; without `overwrite`, never replaces
    an existing file (FileExistsError, atomically -- mode "xb")."""
    with open(out_path, "wb" if overwrite else "xb") as handle:
        handle.write(data)


def has_subfolders(folder: Path) -> bool:
    """True if `folder` contains at least one subdirectory. Used to
    decide whether the "include subfolders?" prompt is even worth
    showing -- asking about subfolders when there aren't any would be
    a pointless extra dialog on every single folder open.
    """
    if not folder.is_dir():
        return False
    return any(p.is_dir() for p in folder.iterdir())


def discover_video_files(
    folder: Path,
    recursive: bool = False,
    ignored: Optional[list[Path]] = None,
) -> list[Path]:
    """List supported video files under `folder`. Non-recursive by
    default (direct children only, matching the epub tool's original
    default folder-load behavior) -- pass recursive=True to also walk
    subfolders, which the GUI offers as an explicit prompt rather than
    silently changing behavior based on folder contents.

    The app's own leftover temp/backup files (core.temp_names) are not
    listed; pass a list as `ignored` to collect the ones skipped.
    """
    if not folder.is_dir():
        return []
    if recursive:
        candidates = folder.rglob("*")
    else:
        candidates = folder.iterdir()
    found = []
    for p in candidates:
        if not (p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()):
            continue
        if is_app_temp_name(p.name):
            if ignored is not None:
                ignored.append(p)
            continue
        found.append(p)
    return sorted(found)
