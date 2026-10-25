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
  notes_cache / notes_wikipedia   [notes] cache / wikipedia (Tools > Create Notes; core/notes_settings.py)
  scene_md5            [scene_lookup] md5 (ThePornDB / StashDB lookups; core/scene_lookup.py)

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

from redactor_common.core import selfupdate

from core import app_paths, config, history, notes_settings, scene_lookup
from core.transcode_settings import DEFAULT_AUDIO_BITRATE, DEFAULT_CRF, DEFAULT_THREADS
from core.video_duplicates import HAMMING_THRESHOLD

KEY_CRF = "crf"
KEY_AUDIO_BITRATE = "audio_bitrate"
KEY_THREADS = "threads"
KEY_DUPLICATE_THRESHOLD = "duplicate_threshold"
KEY_HISTORY_ENABLED = "history_enabled"
KEY_NOTES_CACHE = "notes_cache"
KEY_NOTES_WIKIPEDIA = "notes_wikipedia"
KEY_SCENE_MD5 = "scene_md5"
KEY_TRANSLATION_ENGINE = "translation_engine"
KEY_TRANSLATION_URL = "translation_url"
KEY_TRANSLATION_COMMAND = "translation_command"
KEY_TRANSLATION_TARGET = "translation_target"

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
    KEY_NOTES_CACHE: (notes_settings.SECTION, "cache"),
    KEY_NOTES_WIKIPEDIA: (notes_settings.SECTION, "wikipedia"),
    KEY_SCENE_MD5: (scene_lookup.SECTION, "md5"),
    KEY_TRANSLATION_ENGINE: ("translation", "engine"),
    KEY_TRANSLATION_URL: ("translation", "url"),
    KEY_TRANSLATION_COMMAND: ("tools", "argos-translate"),
    KEY_TRANSLATION_TARGET: ("translation", "target"),
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


def notes_section(actions: tuple[PrefAction, ...] = ()) -> PrefSection:
    """Preferences > Create Notes: the lookup cache and the Wikipedia introduction (Tools > Create Notes)."""
    return PrefSection("notes", "Create Notes", (
        PrefSpec(KEY_NOTES_CACHE, "Keep the answers of earlier lookups on disk (faster the next time)", "bool", True,
                 help="TMDB and Wikipedia answers, kept for a month (a title that was not found, for a day), so a second "
                      "run or a run that was cancelled does not ask again. Your TMDB key is never stored in it."),
        PrefSpec(KEY_NOTES_WIKIPEDIA, "Add a Wikipedia introduction to the notes", "bool", True,
                 help="Only the article that Wikidata links to the title's IMDb id is used, never one found by name. "
                      "Off: the notes are made from TMDB alone (fewer requests)."),
    ), f"Tools > Create Notes drafts series.md / about.md for the Pluteus media server from TMDB (your own key under "
       f"Tools > API Keys) and Wikipedia. The cache file is {notes_settings.cache_path()}.", actions=actions)


SCENE_PRIVACY_TEXT = (
    "Metadata > Look Up > ThePornDB and StashDB send ONLY a file's hash (the OSHASH, which reads 128 KB of the file) "
    "and/or the search text shown in the dialog, together with your own key to that service. No file name, path or "
    "other tag leaves this computer, and no image is downloaded unless you apply a result and tick Save the poster "
    "in the file. The services see your key and your IP address. The StashDB address and the keys are under "
    "Tools > API Keys."
)


def scene_lookup_section() -> PrefSection:
    """Preferences > Look Up: the one option of the ThePornDB / StashDB lookups (and what leaves the machine)."""
    return PrefSection("scene_lookup", "Look Up (ThePornDB, StashDB)", (
        PrefSpec(KEY_SCENE_MD5, "Also look files up by their MD5 (StashDB; reads the whole file)", "bool", False,
                 help="Off by default: the MD5 needs every byte of the file (minutes for a large video, with a progress "
                      "window and Cancel). The OSHASH, which reads only 128 KB, is always used."),
    ), SCENE_PRIVACY_TEXT)


def translation_section(actions: tuple[PrefAction, ...] = ()) -> PrefSection:
    """Preferences > Translation: which engine translates subtitles (Media > Translate Subtitles to English).
    Nothing is bundled and nothing translates by itself."""
    from redactor_common.core.translate import DEFAULT_URL

    return PrefSection("translation", "Translation", (
        PrefSpec(KEY_TRANSLATION_ENGINE, "Engine", "choice", "libretranslate",
                 choices=(("libretranslate", "LibreTranslate server"), ("argos", "argos-translate command")),
                 help="LibreTranslate: your own server (for example the Docker image libretranslate/libretranslate), "
                      "offline models, any compatible server works. argos-translate: the argostranslate command "
                      "line with its offline models. Neither is part of this program."),
        PrefSpec(KEY_TRANSLATION_URL, "Server address", "str", DEFAULT_URL,
                 help="Used by the LibreTranslate engine. An API key, if the server wants one, goes under "
                      "Tools > API Keys (LibreTranslate)."),
        PrefSpec(KEY_TRANSLATION_COMMAND, "argos-translate command", "path", "",
                 help="Used by the argos-translate engine. Blank finds it on PATH. The same setting as the "
                      "argos-translate row under Tools > External Tools."),
        PrefSpec(KEY_TRANSLATION_TARGET, "Translate to", "str", "en",
                 help="The language code translations are made in (English: en)."),
    ), "Machine translation is optional and off until you start it: Media > Translate Subtitles to English... "
       "or the Redact step. A translation is always written as a separate file and marked as machine "
       "translated; the original subtitle is never changed.", actions=actions)


def build_sections(history_actions: tuple[PrefAction, ...] = (),
                   translation_actions: tuple[PrefAction, ...] = (),
                   notes_actions: tuple[PrefAction, ...] = ()) -> list[PrefSection]:
    """The shared dialog's pages. `history_actions`: the History page's buttons (the dialog passes its Qt
    Delete History... callback; this module stays Qt-free)."""
    return [
        filenames_section(),
        PrefSection("transcode", "Transcode", (
            PrefSpec(KEY_CRF, "Video quality (CRF)", "int", DEFAULT_CRF,
                     help="Used by Media > Convert, re-encode to H.264. Lower means higher quality and a "
                          "larger file; 18 to 28 is the usual range.",
                     minimum=0, maximum=51),
            PrefSpec(KEY_AUDIO_BITRATE, "Audio bitrate", "str", DEFAULT_AUDIO_BITRATE,
                     help='A number with an optional k or m, for example "128k" or "192k".'),
            PrefSpec(KEY_THREADS, "Threads", "int", DEFAULT_THREADS,
                     help="How many CPU threads ffmpeg may use. 0 lets ffmpeg decide.",
                     minimum=0, maximum=256),
        ), "Defaults for Media > Convert in its 'Re-encode to H.264' mode, the plain H.264/AAC re-encode, only. The "
           "conversion rules (Media > Convert in Automatic mode, Import and Convert, the Redact convert step) have "
           "their own limits on the Conversion page."),
        PrefSection("duplicates", "Duplicates", (
            PrefSpec(KEY_DUPLICATE_THRESHOLD, "Duplicate sensitivity (bits apart)", "int",
                     HAMMING_THRESHOLD,
                     help="How different two videos' frame fingerprints may be and still count "
                          "as possible duplicates, from 0 (identical only) to 32. Higher finds "
                          "more, with more false matches.",
                     minimum=0, maximum=32),
        ), "Used by Media > Find Duplicates as its starting value."),
        translation_section(translation_actions),
        notes_section(notes_actions),
        scene_lookup_section(),
        history_section(history_actions),
        selfupdate.updates_section(),
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
    """The ini, plus the Updates page's switch, which lives in videoredactor_updates.json."""
    return selfupdate.with_update_settings(CallbackBackend(_get, _set),
                                           selfupdate.UpdateSettings(app_paths.updates_json_path()))
