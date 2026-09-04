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

from core.video_metadata import VideoMetadata, EDITABLE_FIELDS, ContentType
from core.mp4_backend import read_mp4_metadata, write_mp4_metadata
from core.mkv_backend import read_mkv_metadata, write_mkv_metadata
from core.ffmpeg_backend import extract_thumbnail, probe_technical_info
from core.external_tools import is_tool_available, MKVTOOLNIX, FFMPEG

SUPPORTED_EXTENSIONS = {".mp4", ".m4v", ".mkv"}

# Thumbnails are cached to disk (not held as bytes in memory) since a
# folder load can involve many files -- same reasoning as not holding
# every EPUB's full cover image in memory at once. Keyed by path+mtime
# so a file edited/replaced on disk gets a fresh thumbnail rather than
# serving a stale cached one.
THUMBNAIL_CACHE_DIR = Path(tempfile.gettempdir()) / "videoredactor_thumbnails"


@dataclass
class VideoFile:
    path: Path
    metadata: VideoMetadata = field(default_factory=VideoMetadata)
    load_error: str = ""   # mirrors EpubBook.load_error -- distinct from save_error
    save_error: str = ""   # mirrors EpubBook.save_error (v42/v43 lesson: track per-file, don't just retry blind)
    dirty: bool = False    # unsaved bulk-edit changes pending
    _thumbnail_path: Optional[Path] = field(default=None, repr=False, compare=False)

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
                if result.returncode != 0:
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
            self.save_error = f"File error: {e}"
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
        field that currently holds a non-empty value in self.metadata
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

        Only checks fields that are currently non-empty in
        self.metadata -- a field that was never set and still reads
        back empty is correct, expected agreement, not something to
        flag.
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
        for field_name in EDITABLE_FIELDS:
            expected = getattr(self.metadata, field_name, None)
            if expected in (None, ""):
                continue
            actual = getattr(reread, field_name, None)
            if actual != expected:
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

    def save_poster_sidecar(self, image_bytes: bytes) -> Path:
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
        """
        out_path = self.path.with_name(f"{self.path.stem}-poster.jpg")
        out_path.write_bytes(image_bytes)
        return out_path

    def save_subtitle_sidecar(self, subtitle_text: str, language: str = "en") -> Path:
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
        """
        out_path = self.path.with_name(f"{self.path.stem}.{language}.srt")
        out_path.write_text(subtitle_text, encoding="utf-8")
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
        out_path = THUMBNAIL_CACHE_DIR / f"{cache_key}.jpg"

        if out_path.exists() and not force_regenerate:
            self._thumbnail_path = out_path
            return out_path

        ok = extract_thumbnail(str(self.path), str(out_path))
        if not ok:
            return None
        self._thumbnail_path = out_path
        return out_path


def has_subfolders(folder: Path) -> bool:
    """True if `folder` contains at least one subdirectory. Used to
    decide whether the "include subfolders?" prompt is even worth
    showing -- asking about subfolders when there aren't any would be
    a pointless extra dialog on every single folder open.
    """
    if not folder.is_dir():
        return False
    return any(p.is_dir() for p in folder.iterdir())


def discover_video_files(folder: Path, recursive: bool = False) -> list[Path]:
    """List supported video files under `folder`. Non-recursive by
    default (direct children only, matching the epub tool's original
    default folder-load behavior) -- pass recursive=True to also walk
    subfolders, which the GUI offers as an explicit prompt rather than
    silently changing behavior based on folder contents.
    """
    if not folder.is_dir():
        return []
    if recursive:
        candidates = folder.rglob("*")
    else:
        candidates = folder.iterdir()
    return sorted(
        p for p in candidates
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
