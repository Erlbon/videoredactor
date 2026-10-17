"""
core/track_language.py

Metadata > Set Track Language: read and set the LANGUAGE tag of a file's audio / subtitle (and video) tracks IN PLACE,
without re-encoding or rewriting the file (Check Files reports "N audio track(s) without a language" as a note; this
is what fixes it).

  list_tracks(path)                 the tracks (index, kind, position, codec, language, title), read with the app's own
                                    ffprobe reader (ffmpeg_backend.probe_media)
  set_track_languages(path, {i: l}) writes the languages, verifies them, journals them (Tools > History)
  plan_changes(...)                 which tracks a "set all unlabelled audio tracks to ..." would change

How it is written:
  * MP4 / M4V: the 2 bytes of the packed ISO-639-2 language of that track's `mdhd` atom (moov > trak > mdia > mdhd) are
    patched in the real file. The atoms are parsed and checked first (box sizes, version 0 or 1, the handler type of
    every track against what ffprobe says, the old language against what ffprobe says); nothing else in the file is
    touched: not `mdat`, not any other atom, not an `elng` atom. The file's size is the same afterwards. If the read-back
    does not show exactly the intended languages the old bytes are written back and the problem is reported.
  * MKV: `mkvpropedit --edit track:a1 --set language=xxx` (the track selector of its Nth audio / subtitle / video
    track), plus `language-ietf` when the track has one, so both agree. MKVToolNix does the in-place edit.
  * Anything else is refused.

A read-only file is refused before anything is touched (file_access.require_writable). Qt-free; runs on a worker thread.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from core import track_language_codes as codes
from core.external_tools import MKVTOOLNIX, get_executable_path, is_tool_available
from core.file_access import ReadOnlyFileError, read_only_message, require_writable

SUPPORTED_EXTENSIONS = frozenset({".mp4", ".m4v", ".mkv"})
MP4_EXTENSIONS = frozenset({".mp4", ".m4v"})
KINDS = ("video", "audio", "subtitle")
KIND_LETTER = {"video": "v", "audio": "a", "subtitle": "s"}
LETTER_KIND = {letter: kind for kind, letter in KIND_LETTER.items()}
FIELD_PREFIX = "track "
FIELD_SUFFIX = " language"
_MAX_MOOV_BYTES = 512 * 1024 * 1024


class TrackLanguageError(Exception):
    """Complete, user-facing reason a file was not changed (or was put back)."""


@dataclass(frozen=True)
class TrackInfo:
    index: int        # ffprobe's stream index
    kind: str         # video / audio / subtitle
    position: int     # 1-based position among the tracks of its kind
    codec: str
    language: str     # ISO 639-2/B code, 'und' when the file has none
    title: str = ""

    @property
    def key(self) -> str:
        """'a1' = the first audio track (also mkvpropedit's track selector)."""
        return f"{KIND_LETTER[self.kind]}{self.position}"

    @property
    def unlabelled(self) -> bool:
        return self.language == codes.UNDETERMINED


@dataclass
class TrackChange:
    key: str
    index: int
    old: str
    new: str


@dataclass
class SetResult:
    path: str
    changes: list[TrackChange] = field(default_factory=list)
    note: str = ""


def is_supported(path) -> bool:
    return Path(os.fspath(path)).suffix.lower() in SUPPORTED_EXTENSIONS


def unsupported_reason(path) -> str:
    """"" when Set Track Language works on this file; else why not (the tooltip of the greyed entry)."""
    suffix = Path(os.fspath(path)).suffix.lower()
    if suffix in SUPPORTED_EXTENSIONS:
        return ""
    return f"Set Track Language works on MP4 and MKV files only ({suffix or 'no extension'} is not one)"


def field_name(key: str) -> str:
    """The Tools > History field of a track: 'track a1 language'."""
    return f"{FIELD_PREFIX}{key}{FIELD_SUFFIX}"


def key_of_field(name: str) -> str:
    """'a1' for 'track a1 language'; "" for any other field name."""
    if name.startswith(FIELD_PREFIX) and name.endswith(FIELD_SUFFIX):
        key = name[len(FIELD_PREFIX):-len(FIELD_SUFFIX)]
        if len(key) >= 2 and key[0] in LETTER_KIND and key[1:].isdigit():
            return key
    return ""


def list_tracks(path) -> list[TrackInfo]:
    """The video, audio and subtitle tracks of a file in file order (a poster stored as a video stream is not a
    track). Raises TrackLanguageError when the file cannot be read."""
    from core.ffmpeg_backend import probe_media  # noqa: PLC0415

    info = probe_media(os.fspath(path))
    if info is None:
        raise TrackLanguageError("the file could not be read with ffprobe (is ffmpeg installed, is the file damaged?)")
    counts = {kind: 0 for kind in KINDS}
    tracks = []
    for stream in info.streams:
        if stream.kind not in KINDS or stream.attached_pic:
            continue
        counts[stream.kind] += 1
        tracks.append(TrackInfo(
            index=stream.index, kind=stream.kind, position=counts[stream.kind], codec=stream.codec,
            language=codes.normalize(stream.language), title=stream.title,
        ))
    return tracks


def plan_changes(
    tracks: list[TrackInfo], audio: str = "", subtitle: str = "", only_unlabelled: bool = True,
) -> dict[int, str]:
    """{stream index: language} for 'set the audio tracks to `audio`, the subtitle tracks to `subtitle`' ("" = leave
    that kind alone); with only_unlabelled just the tracks that have no language yet. Tracks already at the wanted
    language are left out."""
    wanted = {"audio": audio, "subtitle": subtitle}
    plan = {}
    for track in tracks:
        language = wanted.get(track.kind, "")
        if not language or track.language == language or (only_unlabelled and not track.unlabelled):
            continue
        plan[track.index] = language
    return plan


# ---------------------------------------------------------------------------
# MP4: the mdhd language field
# ---------------------------------------------------------------------------

@dataclass
class _Mdhd:
    handler: str
    offset: int       # absolute file offset of the 2 language bytes
    packed: int       # the 16-bit value now there
    version: int


def decode_packed(packed: int) -> Optional[str]:
    """The three letters of a packed ISO-639-2 value, 'und' for the undetermined values, None for a Macintosh language
    code (below 0x400: not a packed value, ffprobe maps them through a table of its own)."""
    if packed == 0x7FFF:
        return codes.UNDETERMINED
    if packed < 0x400:
        return None
    letters = [((packed >> shift) & 0x1F) + 0x60 for shift in (10, 5, 0)]
    if not all(0x61 <= c <= 0x7A for c in letters):
        return codes.UNDETERMINED
    return "".join(chr(c) for c in letters)


def encode_packed(language: str, keep_top_bit_of: int = 0) -> int:
    """The 15-bit packed value of a three-letter code (char - 0x60 in three 5-bit groups); the pad bit of the old
    value is kept."""
    if len(language) != 3 or not all("a" <= c <= "z" for c in language):
        raise TrackLanguageError(f"'{language}' is not a three-letter ISO 639-2 language code")
    value = 0
    for char in language:
        value = (value << 5) | (ord(char) - 0x60)
    return (keep_top_bit_of & 0x8000) | value


def _box(data: bytes, pos: int, end: int, base: int) -> tuple[str, int, int]:
    """(type, payload start, box end) of the box at `pos`; raises TrackLanguageError when its size is impossible."""
    if pos + 8 > end:
        raise TrackLanguageError("the MP4's atoms are damaged (a box header is cut off)")
    size, kind = struct.unpack_from(">I4s", data, pos)
    header = 8
    if size == 1:
        if pos + 16 > end:
            raise TrackLanguageError("the MP4's atoms are damaged (a 64-bit box header is cut off)")
        size = struct.unpack_from(">Q", data, pos + 8)[0]
        header = 16
    elif size == 0:
        size = end - pos
    if size < header or pos + size > end:
        raise TrackLanguageError(
            f"the MP4's atoms are damaged ('{kind.decode('latin-1')}' at byte {base + pos} has an impossible size)")
    return kind.decode("latin-1"), pos + header, pos + size


def _children(data: bytes, start: int, end: int, base: int):
    pos = start
    while pos + 8 <= end:
        kind, payload, box_end = _box(data, pos, end, base)
        yield kind, payload, box_end
        pos = box_end


def read_mdhd_list(path: str) -> list[_Mdhd]:
    """moov > trak > mdia > (mdhd, hdlr) of every track in file order, with the absolute offset of each language field.
    Raises TrackLanguageError when the layout is not one this code is sure about."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        pos = 0
        moov_at = moov_end = -1
        while pos + 8 <= size:
            handle.seek(pos)
            head = handle.read(16)
            if len(head) < 8:
                break
            box_size, kind = struct.unpack_from(">I4s", head, 0)
            header = 8
            if box_size == 1:
                if len(head) < 16:
                    raise TrackLanguageError("the MP4's atoms are damaged (a 64-bit box header is cut off)")
                box_size = struct.unpack_from(">Q", head, 8)[0]
                header = 16
            elif box_size == 0:
                box_size = size - pos
            if box_size < header or pos + box_size > size:
                raise TrackLanguageError(
                    f"the MP4's atoms are damaged ('{kind.decode('latin-1')}' at byte {pos} has an impossible size)")
            if kind == b"moov":
                moov_at, moov_end = pos + header, pos + box_size
                break
            pos += box_size
        if moov_at < 0:
            raise TrackLanguageError("the MP4 has no moov atom (no track list), so no language can be set")
        if moov_end - moov_at > _MAX_MOOV_BYTES:
            raise TrackLanguageError("the MP4's moov atom is unreasonably large")
        handle.seek(moov_at)
        moov = handle.read(moov_end - moov_at)
    found: list[_Mdhd] = []
    for kind, payload, trak_end in _children(moov, 0, len(moov), moov_at):
        if kind != "trak":
            continue
        mdia = next(((p, e) for k, p, e in _children(moov, payload, trak_end, moov_at) if k == "mdia"), None)
        if mdia is None:
            raise TrackLanguageError("a track of the MP4 has no mdia atom")
        mdhd = hdlr = None
        for k, p, e in _children(moov, mdia[0], mdia[1], moov_at):
            if k == "mdhd" and mdhd is None:
                mdhd = (p, e)
            elif k == "hdlr" and hdlr is None:
                hdlr = (p, e)
        if mdhd is None:
            raise TrackLanguageError("a track of the MP4 has no mdhd atom")
        p, e = mdhd
        version = moov[p]
        if version not in (0, 1):
            raise TrackLanguageError(f"an mdhd atom has the unknown version {version}")
        lang_at = p + (20 if version == 0 else 32)
        if lang_at + 2 > e:
            raise TrackLanguageError("an mdhd atom is too short for its version")
        handler = ""
        if hdlr is not None and hdlr[0] + 12 <= hdlr[1]:
            handler = moov[hdlr[0] + 8:hdlr[0] + 12].decode("latin-1")
        found.append(_Mdhd(handler, moov_at + lang_at, struct.unpack_from(">H", moov, lang_at)[0], version))
    return found


_HANDLER_KINDS = {
    "soun": "audio", "vide": "video", "sbtl": "subtitle", "subt": "subtitle", "text": "subtitle", "clcp": "subtitle",
}


def _mp4_patches(path: str, tracks: list[TrackInfo], new: dict[int, str]) -> list[tuple[int, int, int]]:
    """[(absolute offset, old 16-bit value, new 16-bit value)] for the tracks in `new`; every check is done here,
    before the file is written."""
    from core.ffmpeg_backend import probe_media  # noqa: PLC0415

    info = probe_media(path)
    if info is None:
        raise TrackLanguageError("the file could not be read with ffprobe")
    streams = [s for s in info.streams if not s.attached_pic and s.kind != "attachment"]
    mdhds = read_mdhd_list(path)
    if len(streams) != len(mdhds):
        raise TrackLanguageError(
            f"ffprobe sees {len(streams)} streams but the MP4 has {len(mdhds)} tracks, so the tracks cannot be matched "
            f"safely; nothing was changed")
    by_index = {}
    for stream, mdhd in zip(streams, mdhds):
        expected = _HANDLER_KINDS.get(mdhd.handler)
        if expected and stream.kind in KINDS and expected != stream.kind:
            raise TrackLanguageError(
                f"track {stream.index} is {stream.kind} for ffprobe but '{mdhd.handler}' in the file, so the tracks "
                f"cannot be matched safely; nothing was changed")
        decoded = decode_packed(mdhd.packed)
        if decoded is not None and decoded != codes.normalize(stream.language):
            raise TrackLanguageError(
                f"track {stream.index}: the file's language field reads '{decoded}' but ffprobe says "
                f"'{codes.normalize(stream.language)}', so the tracks cannot be matched safely; nothing was changed")
        by_index[stream.index] = mdhd
    patches = []
    for index, language in new.items():
        mdhd = by_index.get(index)
        if mdhd is None:
            raise TrackLanguageError(f"track {index} cannot be matched to an atom of the MP4")
        patches.append((mdhd.offset, mdhd.packed, encode_packed(language, mdhd.packed)))
    return patches


def _write_bytes(path: str, patches: list[tuple[int, int, int]], use_new: bool) -> None:
    with open(path, "r+b") as handle:
        for offset, old, new in patches:
            handle.seek(offset)
            handle.write(struct.pack(">H", new if use_new else old))
        handle.flush()
        os.fsync(handle.fileno())


# ---------------------------------------------------------------------------
# MKV: mkvpropedit
# ---------------------------------------------------------------------------

def _mkv_ietf_keys(path: str) -> set[str]:
    """The track selectors ('a1') of the tracks that carry a language-ietf property (mkvmerge -J)."""
    import json  # noqa: PLC0415

    from redactor_common.core.subprocess_utils import run_tool  # noqa: PLC0415

    try:
        done = run_tool([get_executable_path("mkvmerge"), "-J", path], timeout=120)
        data = json.loads(done.stdout)
    except Exception:  # noqa: BLE001 -- unknown: the legacy language alone is written
        return set()
    keys, counts = set(), {}
    for track in data.get("tracks") or []:
        kind = track.get("type")
        if kind not in KINDS:
            continue
        counts[kind] = counts.get(kind, 0) + 1
        if (track.get("properties") or {}).get("language_ietf"):
            keys.add(f"{KIND_LETTER[kind]}{counts[kind]}")
    return keys


def _mkv_args(path: str, changes: list[TrackChange], use_new: bool) -> list[str]:
    ietf = _mkv_ietf_keys(path)
    args: list[str] = []
    for change in changes:
        language = change.new if use_new else change.old
        args += ["--edit", f"track:{change.key}", "--set", f"language={language}"]
        if change.key in ietf:
            args += ["--set", f"language-ietf={codes.ietf_of(language)}"]
    return args


def _run_mkvpropedit(path: str, args: list[str]) -> None:
    from core.mkv_backend import run_mkvpropedit  # noqa: PLC0415

    done = run_mkvpropedit(path, args)
    if done.returncode not in (0, 1):  # mkvpropedit: 1 = finished with warnings
        detail = (done.stderr or done.stdout or "").strip()[:300]
        raise TrackLanguageError(f"mkvpropedit failed (exit code {done.returncode}): {detail}")


# ---------------------------------------------------------------------------
# The public write
# ---------------------------------------------------------------------------

def _check_language(language: str) -> str:
    language = codes.normalize(language)
    if len(language) != 3 or not language.isalpha() or not language.islower():
        raise TrackLanguageError(f"'{language}' is not a three-letter ISO 639-2 language code")
    return language


def _expected(tracks: list[TrackInfo], changes: list[TrackChange]) -> dict[int, str]:
    expected = {t.index: t.language for t in tracks}
    expected.update({c.index: c.new for c in changes})
    return expected


def _verify(path: str, tracks: list[TrackInfo], changes: list[TrackChange], size_before: Optional[int]) -> str:
    """"" when the file reads back with exactly the intended languages (and, for MP4, the same size); else why not."""
    try:
        after = list_tracks(path)
    except TrackLanguageError as exc:
        return f"the file no longer reads back ({exc})"
    if [(t.index, t.kind) for t in after] != [(t.index, t.kind) for t in tracks]:
        return "the track list is different after the change"
    expected = _expected(tracks, changes)
    wrong = [f"{t.kind} {t.position}: '{t.language}' instead of '{expected[t.index]}'" for t in after
             if t.language != expected[t.index]]
    if wrong:
        return "the read-back does not match: " + "; ".join(wrong)
    if size_before is not None and os.path.getsize(path) != size_before:
        return "the file size changed"
    return ""


def _write_note(path: str, exc: OSError) -> str:
    return (f"{os.path.basename(path)} could not be written ({exc.strerror or exc}). If it is on a read-only share, "
            f"in use by another program (a player) or on a path that cannot be written, close it there or copy it "
            f"somewhere writable.")


def set_track_languages(path, languages: dict[int, str], journal: bool = True) -> SetResult:
    """Sets the language of the tracks (stream index -> three-letter code) in place and verifies it. Returns what
    changed (empty when every track already had its language: the file is not touched). Raises TrackLanguageError
    with the exact reason; the file is then unchanged (a failed write is put back)."""
    path = os.fspath(path)
    reason = unsupported_reason(path)
    if reason:
        raise TrackLanguageError(reason)
    if not os.path.isfile(path):
        raise TrackLanguageError(f"{os.path.basename(path)} is not there")
    try:
        require_writable(path)
    except ReadOnlyFileError as exc:
        raise TrackLanguageError(str(exc)) from exc
    suffix = Path(path).suffix.lower()
    mkv = suffix == ".mkv"
    if mkv and not is_tool_available(MKVTOOLNIX):
        raise TrackLanguageError("MKVToolNix is not installed (Tools > External Tools), and it writes the language of an MKV track")
    tracks = list_tracks(path)
    by_index = {t.index: t for t in tracks}
    changes: list[TrackChange] = []
    for index, language in sorted(languages.items()):
        track = by_index.get(index)
        if track is None:
            raise TrackLanguageError(f"the file has no track {index}")
        language = _check_language(language)
        if language != track.language:
            changes.append(TrackChange(track.key, index, track.language, language))
    result = SetResult(path)
    if not changes:
        return result
    size_before = None if mkv else os.path.getsize(path)

    entry = None
    if journal:
        from core import history  # noqa: PLC0415
        from redactor_common.core.journal import KIND_SAVE  # noqa: PLC0415

        entry = history.journal_begin(
            path, KIND_SAVE, {field_name(c.key): c.old for c in changes}, {field_name(c.key): c.new for c in changes},
            note="Set Track Language",
        )
    problem = ""
    try:
        problem = _apply(path, mkv, tracks, changes, size_before)
    finally:
        if journal:
            from core import history  # noqa: PLC0415

            history.journal_finish(entry, not problem, problem)
    if problem:
        raise TrackLanguageError(problem)
    result.changes = changes
    return result


def _apply(path: str, mkv: bool, tracks: list[TrackInfo], changes: list[TrackChange], size_before: Optional[int]) -> str:
    """The write and its read-back; "" or the problem (the old languages are put back when the write did not verify)."""
    if mkv:
        try:
            _run_mkvpropedit(path, _mkv_args(path, changes, True))
        except TrackLanguageError as exc:
            return f"{exc}; the file was not verified, check it with Check Files"
        except OSError as exc:
            return _write_note(path, exc)
        problem = _verify(path, tracks, changes, None)
        if problem:
            try:
                _run_mkvpropedit(path, _mkv_args(path, changes, False))
                undone = " The old languages were written back."
            except (TrackLanguageError, OSError):
                undone = " Putting the old languages back also failed: check the file."
            return f"{os.path.basename(path)}: {problem}.{undone}"
        return ""
    try:
        patches = _mp4_patches(path, tracks, {c.index: c.new for c in changes})
    except TrackLanguageError as exc:
        return str(exc)
    except OSError as exc:
        return _write_note(path, exc)
    try:
        _write_bytes(path, patches, True)
    except OSError as exc:
        try:
            _write_bytes(path, patches, False)
        except OSError:
            pass
        return _write_note(path, exc)
    problem = _verify(path, tracks, changes, size_before)
    if problem:
        try:
            _write_bytes(path, patches, False)
            undone = " The old languages were written back."
        except OSError:
            undone = " Putting the old bytes back also failed: check the file."
        return f"{os.path.basename(path)}: {problem}.{undone}"
    return ""


def apply_field_values(path, values: dict[str, str]) -> str:
    """Tools > History's Restore for 'track a1 language' fields: {field: old text} back onto the file. "" or the
    problem. Not journaled here (the History dialog records the restore itself)."""
    try:
        tracks = list_tracks(path)
        by_key = {t.key: t for t in tracks}
        wanted = {}
        for name, language in values.items():
            key = key_of_field(name)
            if key not in by_key:
                return f"the file has no track '{key or name}' any more"
            wanted[by_key[key].index] = language
        set_track_languages(path, wanted, journal=False)
    except TrackLanguageError as exc:
        return str(exc)
    return ""
