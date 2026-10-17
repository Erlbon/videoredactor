"""
core/clear_plan.py

Clear Fields for videoredactor (Qt-free): the groups, which fields belong to which, what a file would lose, and the staged
clearing itself. The shared planning code is redactor_common.core.clear_fields; this module supplies this app's accessors.

Groups (Edit > Clear Fields, the Clear All Fields button in the side panel, the right-click menu, `videoredactor clear`):

  descriptive  title, sort title, description, genre/tags, release date, language, rating, comment, director, cast,
               writer, studio, collection, show title, season, episode, network, artist, album, composer, lyrics
               (on by default)
  sources      THEPORNDB_ID, STASHDB_ID, TMDB_ID, IMDB_ID, TVDB_ID, SOURCE_URL, METADATA_MATCH (on by default)
  poster       the embedded poster: the MP4 cover atom / the MKV cover attachment (on by default)
  type         Content Type (OFF by default)

NEVER cleared: the Check Files scan stamp, the technical fields, the retired legacy track title (read-only), subtitle and
other sidecar files, and the MP4 media type atom `stik`: it is left untouched even when Content Type is cleared (this app
only ever writes stik 6 for a Music Video; a cleared Content Type then reads as "from the file's media type" again).

Everything is STAGED on the metadata like any other edit (nothing is written until Save, Undo restores it): emptied
fields are removed from the file by the writers (the atoms / tags disappear, no empty strings are written), and the poster
is removed through `VideoMetadata.cover_remove`.
"""

from __future__ import annotations

from redactor_common.core.clear_fields import ClearPlan, FieldGroup, plan_clear

from core.filename_pattern import field_text, set_field_text
from core.video_metadata import EDITABLE_FIELDS, SOURCE_FIELDS, ContentType, VideoMetadata

GROUP_DESCRIPTIVE = "descriptive"
GROUP_SOURCES = "sources"
GROUP_POSTER = "poster"
GROUP_TYPE = "type"
ALL_GROUP_KEYS = [GROUP_DESCRIPTIVE, GROUP_SOURCES, GROUP_POSTER, GROUP_TYPE]
DEFAULT_GROUP_KEYS = [GROUP_DESCRIPTIVE, GROUP_SOURCES, GROUP_POSTER]

POSTER_FIELD = "cover_info"  # the poster as a pseudo-field of the plan
DESCRIPTIVE_FIELDS = [f for f in EDITABLE_FIELDS if f != "content_type"]

GROUPS = [
    FieldGroup(GROUP_DESCRIPTIVE, "Descriptive fields (title, description, genre, cast...)",
               "Title, sort title, description, genre/tags, release date, language, rating, comment, director, cast, "
               "writer, studio, collection, show title, season, episode, network, artist, album, composer, lyrics."),
    FieldGroup(GROUP_SOURCES, "Source IDs and links",
               "The ids and links of the databases the file was matched to (ThePornDB, StashDB, TMDB, IMDb, TheTVDB, "
               "the source link and the match note)."),
    FieldGroup(GROUP_POSTER, "Embedded poster", "The poster inside the file (the cover atom / the cover attachment)."),
    FieldGroup(GROUP_TYPE, "Content type",
               "Movie / TV / Music Video / Clip / Misc. Not cleared by default. The MP4 media type (stik) is never "
               "touched.", default_checked=False),
]
GROUP_FIELDS = {
    GROUP_DESCRIPTIVE: DESCRIPTIVE_FIELDS,
    GROUP_SOURCES: list(SOURCE_FIELDS),
    GROUP_POSTER: [POSTER_FIELD],
    GROUP_TYPE: ["content_type"],
}
GROUP_ALIASES = {
    "descriptive": GROUP_DESCRIPTIVE, "fields": GROUP_DESCRIPTIVE, "sources": GROUP_SOURCES, "source": GROUP_SOURCES,
    "ids": GROUP_SOURCES, "poster": GROUP_POSTER, "cover": GROUP_POSTER, "type": GROUP_TYPE,
    "content_type": GROUP_TYPE,
}


def has_poster(metadata: VideoMetadata) -> bool:
    """The file carries a poster, or one is staged for the next Save (and its removal is not already staged)."""
    return bool(metadata.cover_pending) or (bool(metadata.cover_info) and not metadata.cover_remove)


def file_fields(vf) -> dict[str, str]:
    """{field: current text} of everything Clear Fields could remove from `vf`; the poster is described, not its bytes."""
    values = {name: field_text(vf.metadata, name) for name in DESCRIPTIVE_FIELDS + ["content_type"]}
    values.update({name: (getattr(vf.metadata, name, "") or "") for name in SOURCE_FIELDS})
    if has_poster(vf.metadata):
        values[POSTER_FIELD] = vf.metadata.cover_info or "new poster (staged)"
    return values


def plan_for(files) -> ClearPlan:
    return plan_clear(list(files), GROUPS, file_fields, GROUP_FIELDS)


def stage_clear(vf, group_keys) -> bool:
    """Empties the fields of `group_keys` on `vf`'s metadata (staged; the caller pushes the undo entry first and the
    file is marked as changed). True when anything was cleared."""
    md = vf.metadata
    keys = set(group_keys)
    changed = False
    if GROUP_DESCRIPTIVE in keys:
        for name in DESCRIPTIVE_FIELDS:
            if field_text(md, name):
                set_field_text(md, name, "")
                changed = True
    if GROUP_SOURCES in keys:
        for name in SOURCE_FIELDS:
            if getattr(md, name, ""):
                setattr(md, name, "")
                changed = True
    if GROUP_POSTER in keys and has_poster(md):
        md.cover_pending = None
        if md.cover_info:
            md.cover_remove = True  # the poster the file carries goes at the next Save
        changed = True
    if GROUP_TYPE in keys and md.content_type not in (None, ContentType.UNSET):
        md.content_type = ContentType.UNSET
        md.content_type_from_media_type = False
        changed = True
    if changed:
        vf.dirty = True
    return changed
