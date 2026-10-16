"""
core/source_tags.py

The source-id tags kept INSIDE a video file: which record of which outside database it was matched to. Qt-free.
The vocabulary (services, kinds, id shapes, links) is redactor_common.core.source_ids; this module maps it onto the
file's tag fields (core.video_metadata.SOURCE_FIELDS) and builds what a lookup writes.

Tag names, written as they are (MKV: SimpleTag in the global tags; MP4: freeform atom ----:com.videoredactor:<NAME>):

    THEPORNDB_ID    the ThePornDB id: a bare UUID (or slug) for a scene, "movie:<id>" / "jav:<id>" for the others
    STASHDB_ID      the StashDB scene UUID
    TMDB_ID         "<kind>:<id>": movie:603, tv:1396, season:1396/1, episode:1396/1/2
    IMDB_ID         the bare title id, tt0133093
    TVDB_ID         "<kind>:<id>": series:73244, movie:12, episode:9876
    SOURCE_URL      the link to the record the last accepted lookup used (built by redactor_common's build_link)
    METADATA_MATCH  "service;YYYY-MM-DD;how" -- which service filled the fields, on what day, and how the record was
                    found: file (the service knows this file's hash), title (a search by title or name) or
                    manual (the id was typed, pasted or already in the file)

The id of a field's default kind is stored bare (IMDb title, ThePornDB/StashDB scene); every other is "kind:id", so
the value alone says what it is. Nothing here writes to a file: stage_sources() only fills the metadata object, and
only from a SourceRef a lookup produced (SourceRef validates on construction, so a made-up id cannot get in).
"""

from __future__ import annotations

import datetime
import re
from typing import Iterable, Optional

from redactor_common.core.source_ids import SourceRef, build_link, format_label, make_ref, normalise_id, parse_link

from core.video_metadata import SOURCE_FIELDS, VideoMetadata

# field -> (service, default kind or "" for none, the kinds a video file can carry)
ID_FIELDS = {
    "theporndb_id": ("theporndb", "scene", ("scene", "movie", "jav")),
    "stashdb_id": ("stashdb", "scene", ("scene",)),
    "tmdb_id": ("tmdb", "", ("movie", "tv", "season", "episode")),
    "imdb_id": ("imdb", "title", ("title",)),
    "tvdb_id": ("tvdb", "", ("series", "movie", "episode")),
}
TAG_NAMES = {
    "theporndb_id": "THEPORNDB_ID", "stashdb_id": "STASHDB_ID", "tmdb_id": "TMDB_ID", "imdb_id": "IMDB_ID",
    "tvdb_id": "TVDB_ID", "source_url": "SOURCE_URL", "metadata_match": "METADATA_MATCH",
}
FIELD_FOR_SERVICE = {service: field for field, (service, _default, _kinds) in ID_FIELDS.items()}
LABELS = {
    "theporndb_id": "ThePornDB ID", "stashdb_id": "StashDB ID", "tmdb_id": "TMDB ID", "imdb_id": "IMDb ID",
    "tvdb_id": "TheTVDB ID", "source_url": "Source link", "metadata_match": "Matched by",
}
HOW = ("file", "title", "manual")
HOW_TEXT = {"file": "the file's hash", "title": "a search by title", "manual": "the id (chosen, typed or already in the file)"}


def is_source_field(name: str) -> bool:
    return name in SOURCE_FIELDS


# --- one id <-> its tag text ---------------------------------------------------------------------------------------


def encode_ref(ref: SourceRef) -> Optional[tuple[str, str]]:
    """(field, tag text) for a reference a video file can carry, else None (a performer or a book is not one)."""
    field = FIELD_FOR_SERVICE.get(ref.service)
    if field is None or ref.kind not in ID_FIELDS[field][2]:
        return None
    return field, (ref.id if ref.kind == ID_FIELDS[field][1] else f"{ref.kind}:{ref.id}")


def decode_field(field: str, text: str) -> Optional[SourceRef]:
    """The SourceRef a field's tag text names; None for empty or invalid text (never raises)."""
    if field not in ID_FIELDS or not isinstance(text, str) or not text.strip():
        return None
    service, default, kinds = ID_FIELDS[field]
    text = text.strip()
    kind, ident = default, text
    head, sep, rest = text.partition(":")
    if sep and head.lower() in kinds:
        kind, ident = head.lower(), rest
    if not kind:
        return None
    if field == "imdb_id" and re.fullmatch(r"(?i)nm\d+", ident):
        return None  # a person's id is not a title's
    ref = make_ref(service, kind, ident)
    return ref if ref is not None and ref.kind in kinds else None


def refs_in(metadata: VideoMetadata) -> list[SourceRef]:
    """Every valid id the file carries, in field order."""
    out = []
    for field in ID_FIELDS:
        ref = decode_field(field, getattr(metadata, field, ""))
        if ref is not None:
            out.append(ref)
    return out


def ref_in(metadata: VideoMetadata, service: str, kinds: Iterable[str] = ()) -> Optional[SourceRef]:
    """The file's id for `service` (restricted to `kinds` when given), or None."""
    field = FIELD_FOR_SERVICE.get(service)
    ref = decode_field(field, getattr(metadata, field, "")) if field else None
    if ref is not None and kinds and ref.kind not in tuple(kinds):
        return None
    return ref


# --- METADATA_MATCH --------------------------------------------------------------------------------------------------


def make_match(service: str, how: str, today: Optional[datetime.date] = None) -> str:
    if how not in HOW:
        raise ValueError(f"how must be one of {HOW}")
    if not re.fullmatch(r"[a-z]+", service or ""):
        raise ValueError("service must be a plain service name")
    return f"{service};{(today or datetime.date.today()).isoformat()};{how}"


def parse_match(text: str) -> Optional[tuple[str, str, str]]:
    """(service, 'YYYY-MM-DD', how) or None."""
    parts = (text or "").strip().split(";")
    if len(parts) != 3 or not re.fullmatch(r"[a-z]+", parts[0]) or parts[2] not in HOW:
        return None
    try:
        datetime.date.fromisoformat(parts[1])
    except ValueError:
        return None
    return parts[0], parts[1], parts[2]


# --- what a lookup writes --------------------------------------------------------------------------------------------


def source_values(refs: Iterable[SourceRef], how: str, page_url: str = "", today: Optional[datetime.date] = None) -> dict[str, str]:
    """The source fields an accepted lookup sets, as tag text: one id field per service (the FIRST reference of a
    service wins: callers list the most specific one first), SOURCE_URL (the link of the first reference, or
    `page_url` when it is an allowlisted link to the same record) and METADATA_MATCH for the first reference's
    service. Empty when no reference can be stored."""
    values: dict[str, str] = {}
    first: Optional[SourceRef] = None
    for ref in refs:
        encoded = encode_ref(ref)
        if encoded is None or encoded[0] in values:
            continue
        values[encoded[0]] = encoded[1]
        first = first or ref
    if first is None:
        return {}
    url = ""
    page = parse_link(page_url) if page_url else None
    if page is not None and page == first:
        url = page_url.strip()
    values["source_url"] = url or build_link(first)
    values["metadata_match"] = make_match(first.service, how, today)
    return values


def stage_sources(metadata: VideoMetadata, values: dict[str, str]) -> bool:
    """Writes source_values() into the metadata object (staged, like any field); only valid values are taken.
    Returns whether anything changed."""
    changed = False
    for field, text in values.items():
        if field not in SOURCE_FIELDS:
            continue
        if field in ID_FIELDS and decode_field(field, text) is None:
            continue
        if field == "metadata_match" and parse_match(text) is None:
            continue
        if field == "source_url" and parse_link(text) is None:
            continue
        if getattr(metadata, field, "") != text:
            setattr(metadata, field, text)
            changed = True
    return changed


# --- showing them ----------------------------------------------------------------------------------------------------


def link_for(ref: SourceRef) -> str:
    return build_link(ref)


def describe(metadata: VideoMetadata) -> list[tuple[str, str, str]]:
    """(label, text, link) rows for the Sources section: one per id the file carries (link = the record's page),
    then the source link and the match line. An invalid raw value is shown as it is, without a link."""
    rows: list[tuple[str, str, str]] = []
    for field in ID_FIELDS:
        raw = (getattr(metadata, field, "") or "").strip()
        if not raw:
            continue
        ref = decode_field(field, raw)
        rows.append((LABELS[field], raw, build_link(ref) if ref is not None else ""))
    if metadata.source_url.strip():
        url = metadata.source_url.strip()
        rows.append((LABELS["source_url"], url, url if parse_link(url) is not None else ""))
    if metadata.metadata_match.strip():
        match = parse_match(metadata.metadata_match)
        text = metadata.metadata_match.strip()
        if match is not None:
            service, date, how = match
            text = f"{format_label(service) if service in ('tmdb', 'imdb', 'tvdb') else service.capitalize()}, {date}, by {HOW_TEXT[how]}"
        rows.append((LABELS["metadata_match"], text, ""))
    return rows


def has_sources(metadata: VideoMetadata) -> bool:
    return any((getattr(metadata, f, "") or "").strip() for f in SOURCE_FIELDS)


def sources_summary(metadata: VideoMetadata) -> str:
    """One line for a table column: 'TMDB movie:603, IMDb tt0133093'."""
    parts = []
    for field in ID_FIELDS:
        raw = (getattr(metadata, field, "") or "").strip()
        if raw:
            parts.append(f"{format_label(ID_FIELDS[field][0])} {raw}")
    return ", ".join(parts)


__all__ = [
    "ID_FIELDS", "TAG_NAMES", "LABELS", "HOW", "FIELD_FOR_SERVICE", "SOURCE_FIELDS", "is_source_field", "encode_ref",
    "decode_field", "refs_in", "ref_in", "make_match", "parse_match", "source_values", "stage_sources", "link_for",
    "describe", "has_sources", "sources_summary", "normalise_id",
]
