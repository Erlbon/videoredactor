"""
Unified metadata model for The Ʌideo Redactor.

Design principle (mirrors the ƎPUB Redactor's calibre:/epub3 dual-write
pattern for Series): each logical field here maps to a format-appropriate
underlying key. MP4 uses mutagen's iTunes-style atoms where a native atom
exists, and a custom freeform atom otherwise. MKV uses mkvpropedit's
flexible Matroska tag system, where we're free to define our own keys.

Content Type is itself a custom field (no real-world standard equivalent)
and drives which type-specific fields are shown/relevant in the GUI's
column filter and bulk-edit panel. It is NOT folded into Description —
kept as its own dedicated key in both formats.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ContentType(str, Enum):
    MOVIE = "Movie"
    TV = "TV"
    MUSIC_VIDEO = "Music Video"
    CLIP = "Clip"
    MISC = "Misc"
    UNSET = ""  # default until the user sets it


@dataclass
class VideoMetadata:
    # --- Universal fields ---
    title: str = ""
    sort_title: str = ""
    description: str = ""
    genre_tags: str = ""          # comma-separated; freeform for now
    release_date: str = ""        # ISO date or bare year, TBD on precision
    language: str = ""
    personal_rating: Optional[int] = None  # 1-5 personal rating; kept — content_rating (PG/TV-MA etc.) dropped for now, unresolved MP4 atom encoding
    comment: str = ""             # catch-all: source URLs, oddball notes, etc.

    # --- Custom field driving GUI filtering ---
    content_type: ContentType = ContentType.UNSET

    # --- Movie-specific ---
    director: str = ""
    cast: str = ""                # comma-separated for now; may become structured later
    writer: str = ""
    studio: str = ""
    collection: str = ""          # franchise/grouping, same spirit as epub Series

    # --- TV-specific ---
    show_title: str = ""          # distinct from `title` (episode title)
    season_number: Optional[int] = None
    episode_number: Optional[int] = None
    network: str = ""
    # NOTE: air date intentionally NOT a separate field -- release_date
    # is reused for TV episodes too (dropped the distinct field to avoid
    # a tag-key collision with no clean resolution; see mkv_backend.py).

    # --- Music video specific ---
    artist: str = ""
    album: str = ""
    composer: str = ""
    # Song lyrics as plain text (LF line ends, up to LYRICS_MAX_BYTES of UTF-8), from Metadata > Look Up > Get Lyrics
    # (LRCLIB) or typed in. MP4: the iTunes lyrics atom (copyright sign + "lyr"); MKV: the LYRICS tag.
    lyrics: str = ""
    # RETIRED field (music videos: the title is the song, as MusicBrainz names it, version included). Old files may
    # still carry a track title; it is READ so nothing is lost, shown read-only in the panel, never written by this
    # app, and can be moved into an empty Title ("Move track title into Title"). Not editable, not compared.
    legacy_track_title: str = field(default="", compare=False)
    # MP4 only: the iTunes media-type atom `stik` as read from the file (6 = Music Video, 9 = Movie, 10 = TV Show),
    # None when the file has none. Read-only; the writer sets 6 together with Content Type = Music Video.
    media_type: Optional[int] = field(default=None, compare=False)
    # True when content_type was filled from stik 6 because the file had no content type of its own (a suggestion
    # the panel flags as "from the file's media type"; it is only saved when the file is saved for another reason).
    content_type_from_media_type: bool = field(default=False, compare=False)

    # --- Scan stamp (core.scan_stamp wire text) ---
    # "STATUS;UTC-time;fingerprint" from the last Check Files scan, kept
    # inside the file (MKV global tag / MP4 freeform atom REDACTOR_CHECK).
    # Not in EDITABLE_FIELDS (the panel never edits it) and compare=False
    # so it doesn't count as a metadata difference; the backends read and
    # write it, VideoFile.record_check sets it.
    scan_stamp: str = field(default="", compare=False)

    # --- Source ids (core.source_tags): which record in which outside database this file was matched to ---
    # Stored inside the file (MKV global tags / MP4 freeform atoms THEPORNDB_ID, STASHDB_ID, TMDB_ID, IMDB_ID,
    # TVDB_ID, SOURCE_URL, METADATA_MATCH). Like scan_stamp: not in EDITABLE_FIELDS (the panel never edits them),
    # compare=False, and only ever written from an accepted lookup result (core.source_tags.stage_sources).
    theporndb_id: str = field(default="", compare=False)
    stashdb_id: str = field(default="", compare=False)
    tmdb_id: str = field(default="", compare=False)
    imdb_id: str = field(default="", compare=False)
    tvdb_id: str = field(default="", compare=False)
    source_url: str = field(default="", compare=False)
    metadata_match: str = field(default="", compare=False)

    # --- Embedded poster ---
    # cover_info: what the file carries now ("JPEG 600x900, 88 KB"; "" = none), read-only, from the file.
    # cover_pending: a poster (JPEG bytes, core.poster) staged by a lookup, embedded by the next Save (an undoable
    # staged change like any other field, so it IS compared).
    cover_info: str = field(default="", compare=False)
    cover_pending: Optional[bytes] = field(default=None, repr=False)

    # --- Read-only / technical (populated from file, never hand-edited) ---
    resolution: str = field(default="", compare=False)
    video_codec: str = field(default="", compare=False)
    audio_codec: str = field(default="", compare=False)
    bitrate: str = field(default="", compare=False)
    duration_seconds: Optional[float] = field(default=None, compare=False)
    frame_rate: str = field(default="", compare=False)
    container: str = field(default="", compare=False)  # 'mp4' or 'mkv'


# The per-file source fields (see above): kept in the file, shown read-only, never in settings.
SOURCE_FIELDS = [
    "theporndb_id", "stashdb_id", "tmdb_id", "imdb_id", "tvdb_id", "source_url", "metadata_match",
]

# Fields considered "editable" (i.e. NOT the read-only technical block).
# Used by the GUI to decide what the bulk-edit panel ever touches.
EDITABLE_FIELDS = [
    # content_type first -- matches UNIVERSAL_FIELDS's ordering (it's
    # the first field to fill in, since it drives which other fields
    # are relevant). This list previously kept the old title-first
    # order even after UNIVERSAL_FIELDS was reordered -- a real,
    # pre-existing inconsistency caught while building the placeholder
    # reference list, which reads from this list and would otherwise
    # have shown a different field order than the panel it's meant to
    # mirror. Also affects VideoFile._verify_write()'s mismatch-check
    # order (core/video_file.py) -- content_type genuinely does sort
    # first now, which it did not before this fix.
    "content_type", "title", "sort_title", "description", "genre_tags",
    "release_date", "language", "personal_rating", "comment",
    "director", "cast", "writer", "studio", "collection",
    "show_title", "season_number", "episode_number", "network",
    "artist", "album", "composer", "lyrics",
]

# The most lyrics text (UTF-8 bytes) a file is asked to carry.
LYRICS_MAX_BYTES = 64 * 1024

# Freeform text fields eligible for the Edit menu's Case
# Conversion and Search/Replace tools (core/text_transforms.py), and
# for Auto-Numbering's "prefix the existing text" mode. Deliberately
# excludes: content_type (a fixed enum, not free text), genre_tags and
# language (controlled-vocab multi-select fields -- changing case or
# doing a substring replace on these could break matching against the
# picker's canonical option list, e.g. turning "Action" into "action"
# would no longer match the Genre picker's own entries), release_date
# (a date format, not free text), and the three int fields below.
TEXT_FIELDS = [
    "title", "sort_title", "description", "comment",
    "director", "cast", "writer", "studio", "collection",
    "show_title", "network",
    "artist", "album", "composer",
]

# Int fields eligible for Auto-Numbering's "write the number directly"
# mode, as opposed to TEXT_FIELDS' "prefix the number onto existing
# text" mode.
NUMERIC_FIELDS = ["season_number", "episode_number", "personal_rating"]

# Which editable fields are relevant to which content type, for the
# column/panel filter. UNSET/no-filter shows everything (handled in the
# GUI layer, not here) -- this map only covers the "filtered" case.
TYPE_SPECIFIC_FIELDS = {
    ContentType.MOVIE: ["director", "cast", "writer", "studio", "collection"],
    # release_date is already universal and doubles as air date for TV --
    # not repeated here to avoid duplicating it in fields_for_content_type().
    ContentType.TV: ["show_title", "season_number", "episode_number", "network"],
    ContentType.MUSIC_VIDEO: ["artist", "album", "composer", "lyrics"],
    ContentType.CLIP: [],
    ContentType.MISC: [],
}

UNIVERSAL_FIELDS = [
    # content_type first -- it's the first field the user actually
    # needs to fill in, since it drives the whole panel/column filter
    # (Movie/TV/Music Video/etc decides which OTHER fields are even
    # relevant), so it shouldn't be buried at the end of the list.
    "content_type", "title", "sort_title", "description", "genre_tags",
    "release_date", "language", "personal_rating", "comment",
]


def fields_for_content_type(content_type: ContentType) -> list[str]:
    """Universal fields + whatever's specific to this content type.

    Returns universal fields only (no crash) for UNSET or any type not
    yet present in TYPE_SPECIFIC_FIELDS, so an unrecognized/blank type
    degrades to 'show the safe common fields' rather than erroring.
    """
    return UNIVERSAL_FIELDS + TYPE_SPECIFIC_FIELDS.get(content_type, [])


def old_track_title_to_show(metadata: VideoMetadata) -> str:
    """The retired track title worth showing read-only: only when the file has one that differs from the title
    (an old value equal to the title is already the title, nothing to show)."""
    old = (metadata.legacy_track_title or "").strip()
    return old if old and old != (metadata.title or "").strip() else ""


def can_move_track_title(metadata: VideoMetadata) -> bool:
    """True for a Music Video with an old track title and an EMPTY Title: the one case where the old value is
    offered as the title. When both exist and differ they are left alone."""
    return (
        metadata.content_type is ContentType.MUSIC_VIDEO
        and bool((metadata.legacy_track_title or "").strip())
        and not (metadata.title or "").strip()
    )


def move_track_title(metadata: VideoMetadata) -> bool:
    """Puts the old track title into the empty Title (staged; nothing is written until the file is saved). The
    old value stays in the file untouched. Returns False when the move does not apply."""
    if not can_move_track_title(metadata):
        return False
    metadata.title = metadata.legacy_track_title.strip()
    return True
