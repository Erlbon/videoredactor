"""
MKV read/write backend, via mkvpropedit + mkvmerge + mkvextract
(MKVToolNix CLI).

Same design precedent as the epub tool's Calibre integration: always
shells out to the real CLI tool rather than parsing/writing Matroska's
binary EBML structure ourselves.

Tag storage: the native Segment Info "Title" property (what most
players show as the file's title) is written separately from custom
tags, via mkvpropedit's `--edit info --set title=...`. Every OTHER
editable field is written as a Matroska SimpleTag inside a proper Tags
XML document (built via xml.etree.ElementTree, which guarantees correct
escaping for values containing &, <, >, quotes, etc. -- a hand-rolled
"KEY=value" string would not), passed to mkvpropedit via
`--tags global:path-to-xml`.

CORRECTED TWICE after real user-reported failures, both only catchable
against a real MKVToolNix install this development sandbox has never
had:

1. The original version passed `--tags global:KEY=value` directly on
   the command line, which is not valid mkvpropedit syntax at all --
   `--tags` takes a TARGET:FILENAME pointing at an XML file, never an
   inline key/value pair. Also read tags back via `mkvmerge -J`, which
   doesn't expose custom tag CONTENT at all -- fixed to use
   `mkvextract <file> tags` instead.

2. The XML-file rewrite that fixed #1 used `--tags all:<file>` as the
   target selector. build_tags_xml() below produces tags with NO
   <Targets> element, which per the Matroska spec means those tags are
   scoped as GLOBAL (whole-file) tags -- and mkvpropedit's `--tags`
   target selector needs to match that scope. `global` is the
   documented, standard keyword for exactly this; `all` was used
   without solid justification during the first rewrite and is the
   most likely explanation for a second real report ("no tags are
   written to MKV files at all") that a mocked test suite can't catch
   on its own, since the mocks validate this project's own round-trip
   logic, not real mkvpropedit's actual keyword rules.
"""

from __future__ import annotations
import json
import subprocess
import os
import tempfile
import xml.etree.ElementTree as ET
from typing import Optional

from core.video_metadata import VideoMetadata, ContentType
from core.external_tools import get_executable_path

# Every editable field EXCEPT title maps to a MATROSKA_TAG_KEY of the
# same shape (title is handled separately -- see module docstring).
# Freeform, so we're choosing our own naming convention: upper-snake-case,
# prefixed so they're unambiguous in a file someone might inspect with
# a generic MKV tag viewer.
FIELD_TO_MKV_TAG = {
    "sort_title": "SORT_TITLE",
    "description": "DESCRIPTION",
    "genre_tags": "GENRE",
    "release_date": "DATE_RELEASED",
    "language": "LANGUAGE",
    "personal_rating": "PERSONAL_RATING",
    "comment": "COMMENT",
    "content_type": "VIDEOREDACTOR_CONTENT_TYPE",
    "director": "DIRECTOR",
    "cast": "ACTOR",
    "writer": "WRITTEN_BY",
    "studio": "PRODUCTION_STUDIO",
    "collection": "COLLECTION",
    "show_title": "SHOW_TITLE",
    "season_number": "PART_NUMBER",   # Matroska convention for season within a show
    "episode_number": "EPISODE_NUMBER",
    "network": "NETWORK",
    # release_date (DATE_RELEASED, above) is reused for TV air dates too --
    # no separate air_date field/key.
    "artist": "ARTIST",
    "album": "ALBUM",
    "track_title": "TRACK_TITLE",
    "composer": "COMPOSER",
}

REVERSE_FIELD_MAP = {v: k for k, v in FIELD_TO_MKV_TAG.items()}


def build_tags_xml(field_values: dict[str, str]) -> str:
    """Build a Matroska Tags XML document from {mkv_tag_key: value}.

    Uses ElementTree rather than string formatting specifically so
    values containing &, <, >, or quotes are escaped correctly and
    automatically -- a hand-rolled f-string XML builder would silently
    produce a corrupt (or, worse, mis-parsed) tags file for a
    description or comment containing any of those characters, which
    is a completely realistic thing for either field to contain.

    No <Targets> element -- omitting it defaults the tag's scope to
    the whole file (Matroska's TargetTypeValue 50 / "movie" level) per
    the Matroska spec, matching the "global" file-wide intent this
    project always had for these fields.
    """
    tags_root = ET.Element("Tags")
    tag_elem = ET.SubElement(tags_root, "Tag")
    for name, value in field_values.items():
        simple = ET.SubElement(tag_elem, "Simple")
        ET.SubElement(simple, "Name").text = name
        ET.SubElement(simple, "String").text = str(value)
    return ET.tostring(tags_root, encoding="unicode", xml_declaration=False)


def parse_tags_xml(xml_text: str) -> dict[str, str]:
    """Reverse of build_tags_xml(): parse a Tags XML document (as
    produced by `mkvextract <file> tags`) into {mkv_tag_key: value}.

    Returns an empty dict (not an error) for unparseable/empty input --
    a file with no custom tags yet is the normal case (e.g. never
    edited by this app before), not a malformed-file error. Also
    treats non-string input (e.g. None) as empty rather than raising --
    callers already pass `extract_result.stdout or ""`, but defending
    here too means this function is safe to call directly, not just
    safe via callers remembering the `or ""` guard.
    """
    if not isinstance(xml_text, str) or not xml_text.strip():
        return {}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}

    result: dict[str, str] = {}
    for simple in root.iter("Simple"):
        name_elem = simple.find("Name")
        string_elem = simple.find("String")
        if name_elem is not None and name_elem.text and string_elem is not None:
            result[name_elem.text] = string_elem.text or ""
    return result


def _no_console_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]


def run_mkvpropedit(path: str, args: list[str]) -> subprocess.CompletedProcess:
    """Run mkvpropedit on a file with the given argument list.

    Uses CREATE_NO_WINDOW on Windows to avoid the console-popup issue the
    epub tool hit and fixed (v35) with its own Calibre subprocess calls --
    applying that lesson here proactively rather than waiting to rediscover it.

    CALLER CONTRACT: does not check whether mkvpropedit is actually on
    PATH -- a missing executable raises FileNotFoundError here rather
    than degrading gracefully. video_file.py's save() checks
    is_tool_available(MKVTOOLNIX) before ever calling this, which is
    where that check belongs (once, before the whole save attempt) --
    don't remove that upstream check assuming this function guards
    against it, it doesn't.
    """
    return subprocess.run(
        [get_executable_path("mkvpropedit"), path] + args,
        capture_output=True,
        text=True,
        creationflags=_no_console_flags(),
    )


def read_mkv_metadata(path: str, diagnostics: Optional[dict] = None) -> VideoMetadata:
    """Read an MKV file's metadata.

    Native Segment Info Title comes from `mkvmerge -J` (which DOES
    correctly expose that specific well-known property under
    container.properties.title -- this part of the original code was
    fine). Duration comes from the same JSON dump.

    Custom tags (everything else) come from `mkvextract`, which
    outputs real Tags XML -- `mkvmerge -J` cannot be used for this
    despite superficially having a "tags" concept in its output; that
    JSON only reports whether/how-many tags exist, not their actual
    name/value content.

    mkvextract is asked to write its output to an explicit temp file
    (`mkvextract <file> tags <output-path>`) rather than relying on it
    defaulting to stdout with no output argument given -- that default
    was never confirmed against a real mkvextract binary, and an
    explicit output path removes the ambiguity entirely regardless of
    which convention mkvextract's tags mode actually follows. Falls
    back to reading extract_result.stdout if the expected output file
    wasn't created, in case the explicit-output-file syntax turns out
    to be wrong instead.

    diagnostics, if given a dict, is populated with the raw
    stdout/stderr from both the mkvmerge and mkvextract calls --
    previously mkvextract's stderr in particular was silently
    discarded entirely, which meant a genuine extraction failure (a
    wrong command-line argument, for instance) had no way to ever
    surface to whoever was trying to diagnose why reads were coming
    back empty. VideoFile._verify_write() passes a dict here so a
    save-verification mismatch's error message can include exactly
    what these tools said, not just that they were run.

    CALLER CONTRACT: does not check mkvmerge's/mkvextract's availability
    itself; video_file.py's load() checks is_tool_available(MKVTOOLNIX)
    before calling this.
    """
    if diagnostics is None:
        diagnostics = {}
    meta = VideoMetadata(container="mkv")

    merge_result = subprocess.run(
        [get_executable_path("mkvmerge"), "-J", path],
        capture_output=True, text=True, creationflags=_no_console_flags(),
    )
    diagnostics["mkvmerge_stderr"] = merge_result.stderr or ""
    try:
        info = json.loads(merge_result.stdout) if merge_result.stdout else {}
    except json.JSONDecodeError:
        info = {}
    if not isinstance(info, dict):
        info = {}  # mkvmerge produced valid JSON that wasn't the object shape expected

    # dict.get(key, default) only applies `default` when the key is
    # ABSENT -- if mkvmerge's JSON has a key present with an explicit
    # null value (a real possibility, e.g. for a file with no title
    # set), .get() returns None despite the default, and the next
    # .get() in the chain would then crash with an AttributeError.
    # `or {}` catches both "key absent" and "key present but null"
    # uniformly, everywhere this chain is used below.
    container = info.get("container") or {}
    if not isinstance(container, dict):
        container = {}
    container_props = container.get("properties") or {}
    if not isinstance(container_props, dict):
        container_props = {}

    native_title = container_props.get("title")
    if native_title:
        meta.title = native_title

    duration_ns = container_props.get("duration")
    meta.duration_seconds = (
        duration_ns / 1_000_000_000 if isinstance(duration_ns, (int, float)) else None
    )

    xml_content = ""
    tmp_extract_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp_file:
            tmp_extract_path = tmp_file.name
        extract_result = subprocess.run(
            [get_executable_path("mkvextract"), path, "tags", tmp_extract_path],
            capture_output=True, text=True, creationflags=_no_console_flags(),
        )
        diagnostics["mkvextract_stderr"] = extract_result.stderr or ""
        diagnostics["mkvextract_stdout"] = extract_result.stdout or ""
        if os.path.exists(tmp_extract_path) and os.path.getsize(tmp_extract_path) > 0:
            with open(tmp_extract_path, "r", encoding="utf-8") as f:
                xml_content = f.read()
        else:
            # Explicit-output-file syntax may itself be wrong for this
            # mkvextract version -- fall back to stdout in case it
            # defaults there when given no output argument at all.
            # (This fallback path is now effectively untested against
            # this specific 4-argument invocation; kept only as a
            # last-resort degrade, not trusted as primary.)
            xml_content = extract_result.stdout or ""
    finally:
        if tmp_extract_path and os.path.exists(tmp_extract_path):
            os.remove(tmp_extract_path)

    tag_dict = parse_tags_xml(xml_content)

    for mkv_key, value in tag_dict.items():
        field_name = REVERSE_FIELD_MAP.get(mkv_key)
        if not field_name:
            continue
        if field_name == "content_type":
            try:
                meta.content_type = ContentType(value)
            except ValueError:
                meta.content_type = ContentType.UNSET
        elif field_name in ("season_number", "episode_number", "personal_rating"):
            setattr(meta, field_name, int(value) if str(value).isdigit() else None)
        else:
            setattr(meta, field_name, value)

    return meta


def write_mkv_metadata(path: str, meta: VideoMetadata) -> subprocess.CompletedProcess:
    """Write VideoMetadata's editable fields to an MKV file.

    Title goes through mkvpropedit's native `--edit info --set title=`
    (the real Matroska Segment Info property most players actually
    read/display). Everything else goes through a generated Tags XML
    file passed via `--tags global:...`. `global` (not `all` -- see
    module docstring for the real bug this was) matches
    build_tags_xml()'s Targets-less, whole-file-scoped output.

    Issued as TWO SEPARATE mkvpropedit invocations when both a title
    and custom tags need writing, not combined into one call. This is
    a deliberate change from an earlier version of this function, which
    combined `--edit info --set title=...` and `--tags global:...` into
    a single invocation on the assumption that was safe -- an
    assumption that has never been confirmed against a real
    mkvpropedit binary, and a real user report (custom tags,
    content_type specifically confirmed via the new verify-after-write
    check, silently failing to persist while title kept working)
    matches exactly the shape of bug a bad assumption about combining
    actions could cause. Splitting removes that specific unverified
    assumption from the picture entirely, at the cost of one extra
    process launch when both title and tags are being written -- worth
    it to narrow down what's actually going on. Both results are
    combined into one CompletedProcess-shaped return so callers don't
    need structural changes: returncode is the worse of the two (0 only
    if both succeeded), stdout/stderr are concatenated so nothing
    either call printed is silently lost.

    The generated XML is written to a real temp file (mkvpropedit reads
    --tags as a file path, not stdin) and always cleaned up afterward,
    success or failure, via try/finally.
    """
    title = getattr(meta, "title", None)

    tag_values: dict[str, str] = {}
    for field_name, mkv_tag in FIELD_TO_MKV_TAG.items():
        value = getattr(meta, field_name, None)
        if field_name == "content_type":
            value = value.value if isinstance(value, ContentType) else value
        if value in (None, ""):
            continue
        tag_values[mkv_tag] = str(value)

    if not title and not tag_values:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    results: list[subprocess.CompletedProcess] = []

    if title:
        results.append(run_mkvpropedit(path, ["--edit", "info", "--set", f"title={title}"]))

    if tag_values:
        tmp_xml_path = None
        try:
            xml_content = build_tags_xml(tag_values)
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", delete=False, encoding="utf-8"
            ) as tmp_file:
                tmp_file.write(xml_content)
                tmp_xml_path = tmp_file.name
            results.append(run_mkvpropedit(path, ["--tags", f"global:{tmp_xml_path}"]))
        finally:
            if tmp_xml_path and os.path.exists(tmp_xml_path):
                os.remove(tmp_xml_path)

    combined_returncode = max(r.returncode for r in results)
    combined_stdout = "\n".join(r.stdout for r in results if r.stdout)
    combined_stderr = "\n".join(r.stderr for r in results if r.stderr)
    return subprocess.CompletedProcess(
        args=[], returncode=combined_returncode, stdout=combined_stdout, stderr=combined_stderr,
    )


# NOTE: MKV cover art is sidecar-file only for v1 (a `moviename-poster.jpg`
# next to the file, same convention Plex/Jellyfin/Kodi already prefer over
# embedded art) -- no embedded-attachment extraction/writing here by design.
# Sidecar handling lives at the file-management layer, not per-container,
# since it works identically for MP4 and MKV alike.
