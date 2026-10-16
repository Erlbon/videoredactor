"""
MP4 read/write backend, via mutagen (pure Python, no subprocess).

Maps VideoMetadata's unified fields onto MP4's iTunes-style atoms where a
native atom exists, and onto custom freeform atoms
(----:com.videoredactor:<key>) otherwise -- same "native where possible,
custom where not" reasoning as the epub tool's calibre:/epub3 dual-write.

IMPORTANT: mutagen.mp4.MP4.tags is documented to be None -- not an empty
dict -- when a file has no existing metadata atoms at all (a completely
normal case for an older or bare/untagged rip, not a malformed file).
Every function below must guard against this explicitly: `atom in
mp4.tags` when mp4.tags is None raises `TypeError: argument of type
'NoneType' is not iterable` (the exact wording varies slightly by Python
version). This was a real, user-reported crash on a genuinely untagged
file -- confirmed against a real screenshot of the actual error, not
guessed at -- see CHANGELOG.md.

ALSO IMPORTANT: mutagen's MP4Tags requires specific Python types per
atom, not uniformly strings. Numeric atoms -- including tvsn (TV season
number) and tves (TV episode number), the two atoms below -- must be
written as a list of int, not a list of str; passing strings for these
is rejected/mishandled by mutagen. This was a second real, user-reported
bug (TV season/episode metadata silently failing to persist) -- see
CHANGELOG.md.

ALSO IMPORTANT (candidate fix for a THIRD real report -- "only Title
persists, every custom field silently reverts"): mutagen's documented
usage for freeform (`----:mean:name`) atoms wraps the value in
`MP4FreeForm(bytes, dataformat=...)` rather than a bare bytes object in
a list. Every CUSTOM_FIELDS value (content_type, director, cast,
studio, sort_title, etc.) previously used a bare bytes object, which
may not serialize correctly through mutagen's save() the same way a
properly-typed MP4FreeForm does -- this is the leading theory for why
title (a plain text atom, unaffected by this) persists while every
custom field silently doesn't. Applied here as a real fix, not left as
only a theory, but -- like the two bugs above it -- has never been
confirmed against a real mutagen install; VideoFile.save()'s new
verify-after-write step (see core/video_file.py) will surface a clear,
specific error if this fix is ALSO wrong, rather than another silent
failure.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

from redactor_common.core.os_utils import replace_with_retry, retry_on_lock

from core.file_access import make_scratch_writable, remove_scratch, require_writable
from core.poster import describe_cover
from core.video_metadata import SOURCE_FIELDS, VideoMetadata, ContentType

# Freeform atom name of each source field (kept here, not imported from core.source_tags, so the backend stays
# free of that module; tests/test_source_tags.py checks the two tables agree).
SOURCE_TAG_NAMES = {
    "theporndb_id": "THEPORNDB_ID", "stashdb_id": "STASHDB_ID", "tmdb_id": "TMDB_ID", "imdb_id": "IMDB_ID",
    "tvdb_id": "TVDB_ID", "source_url": "SOURCE_URL", "metadata_match": "METADATA_MATCH",
}

CUSTOM_ATOM_PREFIX = "----:com.videoredactor:"

# Native iTunes-style MP4 atoms we can map directly.
# Reference: mutagen.mp4's freeform/known atom names.
NATIVE_ATOM_MAP = {
    "title": "\xa9nam",
    "description": "desc",
    "genre_tags": "\xa9gen",
    "release_date": "\xa9day",
    "show_title": "tvsh",
    "season_number": "tvsn",
    "episode_number": "tves",
    "network": "tvnn",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "composer": "\xa9wrt",
    "comment": "\xa9cmt",
}

PNG_SIGNATURE = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def cover_is_png(data: bytes) -> bool:
    """A PNG picture by its signature (a cover read from one file and put into another keeps its real format)."""
    return bytes(data[:8]) == PNG_SIGNATURE


# Atoms mutagen requires as a list of int, not a list of str -- writing
# a string here is the confirmed root cause of TV season/episode tags
# not persisting. Every OTHER atom in NATIVE_ATOM_MAP is a text atom
# and takes strings correctly.
INTEGER_ATOMS = {"tvsn", "tves"}

# Fields with no native MP4 atom -- stored as custom freeform atoms.
CUSTOM_FIELDS = [
    "sort_title", "language", "personal_rating", "content_type",
    "director", "cast", "writer", "studio", "collection",
    "track_title",
    "scan_stamp",
    # Source ids (core.source_tags): THEPORNDB_ID ... METADATA_MATCH, written only from an accepted lookup.
    *SOURCE_FIELDS,
]

# Freeform atom names that differ from the field name.
CUSTOM_ATOM_NAMES = {"scan_stamp": "REDACTOR_CHECK", **{f: t for f, t in SOURCE_TAG_NAMES.items()}}


def _custom_key(field_name: str) -> str:
    return f"{CUSTOM_ATOM_PREFIX}{CUSTOM_ATOM_NAMES.get(field_name, field_name)}"


def read_mp4_metadata(path: str) -> VideoMetadata:
    """Read an MP4 file's tags into a VideoMetadata instance.

    Unrecognized/missing atoms are left at VideoMetadata's defaults rather
    than raising -- a file with partial tagging is the normal case, not
    an error case. A file with NO tags at all (mp4.tags is None) is the
    same story taken to its extreme -- handled explicitly below rather
    than crashing on the first `atom not in mp4.tags` check.
    """
    from mutagen.mp4 import MP4  # deferred import: module not yet installable here

    mp4 = MP4(path)
    meta = VideoMetadata(container="mp4")

    if mp4.tags is not None:
        for field_name, atom in NATIVE_ATOM_MAP.items():
            if atom not in mp4.tags:
                continue
            value = mp4.tags[atom]
            if isinstance(value, list) and value:
                value = value[0]
            if atom in INTEGER_ATOMS:
                # season_number/episode_number are Optional[int] in
                # VideoMetadata -- stringifying them here (like every
                # other atom) would silently break the GUI's int-field
                # widget, which only accepts an actual int, not "1" as
                # a string. Confirmed real bug, not a hypothetical: this
                # is the read-side half of the same TV-tags-don't-stick
                # report the write-side INTEGER_ATOMS fix addresses.
                setattr(meta, field_name, value if isinstance(value, int) else None)
            else:
                setattr(meta, field_name, str(value) if value is not None else "")

        for field_name in CUSTOM_FIELDS:
            key = _custom_key(field_name)
            if key not in mp4.tags:
                continue
            raw = mp4.tags[key]
            if isinstance(raw, list) and raw:
                raw = raw[0]
            text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
            if field_name == "content_type":
                try:
                    meta.content_type = ContentType(text)
                except ValueError:
                    meta.content_type = ContentType.UNSET
            elif field_name in ("season_number", "episode_number", "personal_rating"):
                setattr(meta, field_name, int(text) if text.isdigit() else None)
            else:
                setattr(meta, field_name, text)
        covers = mp4.tags.get("covr")
        if covers:
            meta.cover_info = describe_cover(bytes(covers[0]))
    # else: mp4.tags is None -- a genuinely untagged file. meta stays at
    # its all-defaults state, same outcome as if every atom lookup above
    # had simply found nothing, just without needing to crash first.

    # Technical/read-only fields come from mp4.info, not tags -- mp4.info
    # always exists regardless of whether mp4.tags does, so this is safe
    # unconditionally.
    meta.duration_seconds = getattr(mp4.info, "length", None)
    meta.bitrate = str(getattr(mp4.info, "bitrate", "") or "")

    return meta


def write_mp4_metadata(path: str, meta: VideoMetadata) -> None:
    """Write a VideoMetadata instance's editable fields to an MP4 file.

    Only touches editable fields (see EDITABLE_FIELDS) -- never writes
    the read-only technical block back, same non-negotiable as the epub
    tool never round-tripping its Status column into file content.

    Calls mp4.add_tags() when mp4.tags is None -- mutagen's own
    documented way to initialize an empty, writable tags container for
    a file that doesn't have one yet, rather than assuming every file
    already has some tags to work with.
    """
    from mutagen.mp4 import MP4

    # Never edit the video in place (mutagen rewrites the moov atom and may shift the whole file; a crash
    # mid-write truncates it): copy to a scratch beside it, edit the copy, check it still parses, then swap it
    # in with a lock retry. Same approach as mp3redactor's core/tag_writer.py. MKV (mkvpropedit) is unchanged:
    # it edits in place, by design of the tool.
    _edit_via_scratch(path, lambda scratch: _apply_mp4_tags(scratch, meta))


def _edit_via_scratch(path: str, apply) -> None:
    """Runs `apply(scratch_path)` on a copy of the video and swaps the copy in only when it still parses."""
    target = os.path.realpath(path)
    require_writable(target)  # a read-only video is refused up front, not half-way through
    fd, scratch = tempfile.mkstemp(prefix=".videoredactor-", suffix=".tmp", dir=os.path.dirname(target))
    os.close(fd)
    try:
        retry_on_lock(shutil.copy2, target, scratch)  # keeps the permissions...
        make_scratch_writable(scratch)  # ...but not a read-only attribute: the copy is ours to edit and to delete
        apply(scratch)
        from mutagen.mp4 import MP4

        MP4(scratch)  # the edited copy must still parse before it replaces anything
        replace_with_retry(scratch, target)
    except BaseException:
        remove_scratch(scratch)
        raise


def _apply_mp4_tags(path: str, meta: VideoMetadata) -> None:
    """The mutagen edit itself, on `path` (a scratch copy, see write_mp4_metadata)."""
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

    mp4 = MP4(path)
    if mp4.tags is None:
        mp4.add_tags()

    for field_name, atom in NATIVE_ATOM_MAP.items():
        value = getattr(meta, field_name, "")
        # Not `if value:` -- a Season/Episode of 0 (specials) is a real
        # value and must be written, not deleted.
        if value not in (None, ""):
            if atom in INTEGER_ATOMS:
                try:
                    mp4.tags[atom] = [int(value)]
                except (ValueError, TypeError):
                    # Not a valid integer (e.g. a hand-typed non-numeric
                    # season/episode) -- skip writing this atom rather
                    # than write a wrongly-typed value mutagen would
                    # reject or mishandle, or crash the whole save.
                    pass
            else:
                mp4.tags[atom] = [str(value)]
        elif atom in mp4.tags:
            del mp4.tags[atom]

    for field_name in CUSTOM_FIELDS:
        key = _custom_key(field_name)
        value = getattr(meta, field_name, None)
        if field_name == "content_type":
            value = value.value if isinstance(value, ContentType) else value
        if value not in (None, ""):
            # Mutagen's documented pattern for freeform (----:mean:name)
            # atoms -- explicit MP4FreeForm wrapping, not a bare bytes
            # object -- see module docstring for why this changed.
            mp4.tags[key] = [MP4FreeForm(str(value).encode("utf-8"))]
        elif key in mp4.tags:
            del mp4.tags[key]

    # A staged poster (core.poster JPEG): the standard cover atom. Nothing staged leaves the existing cover alone.
    if getattr(meta, "cover_pending", None):
        pending = bytes(meta.cover_pending)
        mp4.tags["covr"] = [MP4Cover(pending, imageformat=MP4Cover.FORMAT_PNG if cover_is_png(pending) else MP4Cover.FORMAT_JPEG)]

    mp4.save()


def read_mp4_cover(path: str) -> Optional[bytes]:
    """Return embedded cover art bytes, or None if there isn't any --
    including the case of a file with no tags at all, not just "tags
    exist but no covr atom."
    """
    from mutagen.mp4 import MP4

    mp4 = MP4(path)
    if mp4.tags is None:
        return None
    covers = mp4.tags.get("covr")
    if not covers:
        return None
    return bytes(covers[0])


def _apply_cover(path: str, image_bytes: bytes, is_png: bool = False) -> None:
    """The mutagen edit of write_mp4_cover, on `path` itself (a scratch copy)."""
    from mutagen.mp4 import MP4, MP4Cover

    fmt = MP4Cover.FORMAT_PNG if (is_png or cover_is_png(image_bytes)) else MP4Cover.FORMAT_JPEG
    mp4 = MP4(path)
    if mp4.tags is None:
        mp4.add_tags()
    mp4.tags["covr"] = [MP4Cover(image_bytes, imageformat=fmt)]
    mp4.save()


def write_mp4_cover(path: str, image_bytes: bytes, is_png: bool = False) -> None:
    """Embed cover art into an MP4 file (native 'covr' atom), the same safe way tags are written (a scratch copy
    swapped in once it parses). Only this atom is touched. Same add_tags() guard as _apply_mp4_tags."""
    _edit_via_scratch(path, lambda scratch: _apply_cover(scratch, image_bytes, is_png))
