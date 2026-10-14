"""
core/preferences_backend.py

What the shared Preferences dialog (redactor_common) edits in this app, and
where each value lives in videoredactor_settings.ini. Qt-free.

The storage keys are the ones the app and Export/Import Settings
(core/settings_adapter.py) already use -- the dialog only gives them a second
way in, it never moves them:

  ascii_filenames      [rename] ascii_only
  zero_pad_numbers     [rename] zero_pad
  zero_pad_width       [rename] zero_pad_width
  auto_number_padding  [auto_numbering] padding
  crf / audio_bitrate / threads   [transcode]
  duplicate_threshold  [duplicates] threshold
  history_enabled      [history] enabled (Tools > History, opt-in; core/history.py)

There is no default-language setting in this app (a video's language is read
from its own tracks), so the shared language section is not offered.
"""

from __future__ import annotations

import re

from redactor_common.core.preferences import (
    KEY_ASCII_FILENAMES,
    KEY_AUTO_NUMBER_PADDING,
    KEY_ZERO_PAD_NUMBERS,
    KEY_ZERO_PAD_WIDTH,
    CallbackBackend,
    PrefAction,
    PrefSection,
    PrefSpec,
    filenames_section,
)

from core import config, history
from core.transcode_settings import DEFAULT_AUDIO_BITRATE, DEFAULT_CRF, DEFAULT_THREADS
from core.video_duplicates import HAMMING_THRESHOLD

KEY_CRF = "crf"
KEY_AUDIO_BITRATE = "audio_bitrate"
KEY_THREADS = "threads"
KEY_DUPLICATE_THRESHOLD = "duplicate_threshold"
KEY_HISTORY_ENABLED = "history_enabled"

# preference key -> (ini section, ini key)
INI_LOCATIONS: dict[str, tuple[str, str]] = {
    KEY_ASCII_FILENAMES: ("rename", "ascii_only"),
    KEY_ZERO_PAD_NUMBERS: ("rename", "zero_pad"),
    KEY_ZERO_PAD_WIDTH: ("rename", "zero_pad_width"),
    KEY_AUTO_NUMBER_PADDING: ("auto_numbering", "padding"),
    KEY_CRF: ("transcode", "crf"),
    KEY_AUDIO_BITRATE: ("transcode", "audio_bitrate"),
    KEY_THREADS: ("transcode", "threads"),
    KEY_DUPLICATE_THRESHOLD: ("duplicates", "threshold"),
    KEY_HISTORY_ENABLED: (history.SETTINGS_SECTION, history.SETTINGS_KEY),
}

_BITRATE = re.compile(r"\d{1,4}[kKmM]?")


def bitrate_ok(text: str) -> bool:
    """An ffmpeg -b:a value: up to four digits, optionally k or m."""
    return _BITRATE.fullmatch(text) is not None


HISTORY_CHECKBOX = "Keep a history of tag changes (lets you undo a Save or Redact later)"


def history_help_text(path: str) -> str:
    return (
        "What is kept: the old and new text of every tag field a Save, Save All, Redact or the command line "
        "changes; never covers or other images. A renamed, moved or converted file takes its history with it. "
        "How much: the last 90 days, at most 50,000 changes. "
        f"Where: {path}. Tools > History shows it and can write old values back. You can switch it off again "
        "at any time; what was recorded stays until you delete it. Off by default."
    )


def history_section(actions: tuple[PrefAction, ...] = ()) -> PrefSection:
    """Preferences > History: the opt-in switch ([history] enabled, off by default) and, from the dialog, the
    Delete History... action button."""
    return PrefSection("history", "History", (
        PrefSpec(KEY_HISTORY_ENABLED, HISTORY_CHECKBOX, "bool", False,
                 help="Off by default: while it is off nothing is written."),
    ), history_help_text(history.journal_path()), actions=actions)


def build_sections(history_actions: tuple[PrefAction, ...] = ()) -> list[PrefSection]:
    """The shared dialog's pages. `history_actions`: the History page's buttons (the dialog passes its Qt
    Delete History... callback; this module stays Qt-free)."""
    return [
        filenames_section(),
        PrefSection("transcode", "Transcode", (
            PrefSpec(KEY_CRF, "Video quality (CRF)", "int", DEFAULT_CRF,
                     help="Used by Convert to MP4 (H.264). Lower means higher quality and a "
                          "larger file; 18 to 28 is the usual range.",
                     minimum=0, maximum=51),
            PrefSpec(KEY_AUDIO_BITRATE, "Audio bitrate", "str", DEFAULT_AUDIO_BITRATE,
                     help='A number with an optional k or m, for example "128k" or "192k".'),
            PrefSpec(KEY_THREADS, "Threads", "int", DEFAULT_THREADS,
                     help="How many CPU threads ffmpeg may use. 0 lets ffmpeg decide.",
                     minimum=0, maximum=256),
        ), "Defaults for Media > Convert to MP4 (H.264), the plain H.264/AAC re-encode, only. The conversion "
           "rules (Media > Convert to MP4/MKV (Rules), Import and Convert, the Redact convert step) have their "
           "own limits on the Conversion page."),
        PrefSection("duplicates", "Duplicates", (
            PrefSpec(KEY_DUPLICATE_THRESHOLD, "Duplicate sensitivity (bits apart)", "int",
                     HAMMING_THRESHOLD,
                     help="How different two videos' frame fingerprints may be and still count "
                          "as possible duplicates, from 0 (identical only) to 32. Higher finds "
                          "more, with more false matches.",
                     minimum=0, maximum=32),
        ), "Used by Media > Find Duplicates as its starting value."),
        history_section(history_actions),
    ]


def _get(key: str):
    section, name = INI_LOCATIONS[key]
    raw = config.get_setting(section, name, "")
    return raw if raw != "" else None  # None -> the spec's default


def _store(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def _set(values: dict) -> None:
    """One atomic save for everything the dialog changed."""
    parser = config.load_config()
    for key, value in values.items():
        section, name = INI_LOCATIONS[key]
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, name, _store(value))
    config.save_config(parser)


def make_backend() -> CallbackBackend:
    return CallbackBackend(_get, _set)
