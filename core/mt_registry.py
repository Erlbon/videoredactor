"""
core/mt_registry.py

Which subtitle texts are machine translations. There is no standard way to mark one, and the file name must not
carry an extra token (<name>.en.srt stays <name>.en.srt), so the app remembers a fingerprint of the cue text it
wrote: [machine_translated] <fingerprint> = <engine> in the settings file. A conversion that embeds such a
subtitle gives the track the stream title MT_TRACK_TITLE; a file edited outside the app (different text) is no
longer marked.
"""

from __future__ import annotations

import hashlib

from redactor_common.core.srt import parse_srt

from core.config import get_setting, set_setting

SECTION = "machine_translated"
MT_TRACK_TITLE = "English (machine translation)"
_MAX_ENTRIES_NOTE = 500  # a hard stop against an ever-growing section


def fingerprint(srt_text: str) -> str:
    """Stable for the same cue texts and start times, whatever the line ends, BOM or numbering."""
    cues = parse_srt(srt_text)
    body = "\n".join(f"{round(c.start * 1000)}|{c.text.strip()}" for c in cues)
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:20]


def mark(srt_text: str, engine: str) -> None:
    key = fingerprint(srt_text)
    if key:
        set_setting(SECTION, key, engine or "machine translation")


def engine_for(srt_text: str) -> str:
    """The engine name when `srt_text` is a recorded machine translation, else ""."""
    if not srt_text or not srt_text.strip():
        return ""
    return get_setting(SECTION, fingerprint(srt_text), "")


def track_title(srt_text: str, existing: str = "") -> str:
    """The stream title for an embedded subtitle: MT_TRACK_TITLE (plus an existing "(SDH)"-style label), or
    `existing` unchanged when the text is not a recorded machine translation."""
    if not engine_for(srt_text):
        return existing
    return f"{MT_TRACK_TITLE} {existing}".strip()
