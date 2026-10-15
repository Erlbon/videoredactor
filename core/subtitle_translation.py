"""
core/subtitle_translation.py

What the subtitle translation needs from a video (Qt-free): the subtitle tracks that exist (sidecar .srt files
beside it and embedded text tracks), their text and language, and the one writer of the English sidecar.

  list_sources(video_path)   SubtitleSource per track: a sidecar from subtitle_ingest.find_external_subs, an
                             embedded text track from ffprobe (bitmap tracks cannot be translated and are left out)
  read_text(source)          the SRT text of a track (a sidecar decoded like the ingest does; an embedded track
                             through ffmpeg)
  source_language(source, text)  the track's language as an ISO 639-1 code: its tag, else a guess from the text
                             (`guessed` tells the dialog to ask the user to confirm)
  write_translation(...)     <stem>.en.srt through VideoFile.save_subtitle_sidecar: never replaces a file; the
                             text is remembered as a machine translation (core/mt_registry) and recorded in the
                             History journal (opt-in, like all of it)
  translate_all(...)         the cues through the configured engine (a worker-thread function)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from redactor_common.core.srt import Cue, format_srt, parse_srt
from redactor_common.core.translate import TranslateResult, Translator, detect_language, translate_cues

from core import conversion_plan as cp
from core import history, mt_registry, subtitle_ingest as si
from core.subtitle_ingest import ISO_639_2

KIND_SIDECAR = "sidecar"
KIND_EMBEDDED = "embedded"
ENGLISH_ALTERNATIVE_TAGS = ("eng", "en-US", "en-GB")

_ISO1_BY_2: dict[str, str] = {}
for _one, _two in ISO_639_2.items():
    if len(_one) == 2 and _two not in _ISO1_BY_2:
        _ISO1_BY_2[_two] = _one


def iso1(code: str) -> str:
    """"rus" / "ru" / "Russian" -> "ru"; "" when it is not a language (or "und")."""
    two = si.language_code(code) if code else ""
    return _ISO1_BY_2.get(two, "")


@dataclass
class SubtitleSource:
    label: str
    kind: str                # KIND_SIDECAR / KIND_EMBEDDED
    language: str            # ISO 639-1 ("" = not known)
    path: str = ""           # the sidecar file, or the video for an embedded track
    stream_index: int = -1   # ffprobe stream index (embedded only)

    @property
    def is_english(self) -> bool:
        return self.language == "en"


def list_sources(video_path: str, probe: Optional[Callable] = None, listdir=os.listdir) -> list[SubtitleSource]:
    """Every subtitle track of the video that can be read as text: sidecars first (by name), then embedded."""
    sources: list[SubtitleSource] = []
    for ext in si.find_external_subs(video_path, listdir=listdir):
        language = iso1(ext.language)
        name = os.path.basename(ext.path)
        sources.append(SubtitleSource(f"{name} (file)", KIND_SIDECAR, language, ext.path))
    if probe is None:
        from core.ffmpeg_backend import probe_media as probe  # noqa: PLC0415
    info = probe(video_path)
    if info is not None:
        for track in info.of("subtitle"):
            if track.codec not in cp.TEXT_SUBTITLES:
                continue
            language = iso1(track.language)
            title = f" {track.title}" if track.title else ""
            sources.append(SubtitleSource(
                f"Embedded track {track.index}: {language or track.language or 'und'}{title}",
                KIND_EMBEDDED, language, video_path, track.index,
            ))
    return sources


def read_text(source: SubtitleSource) -> str:
    """The track as SRT text; raises OSError / SubtitleDecodeError / RuntimeError with a message for the user."""
    if source.kind == KIND_SIDECAR:
        with open(source.path, "rb") as f:
            return si.decode_srt(f.read(), si.language_code(source.language) or "und")
    from core.ffmpeg_backend import _run  # noqa: PLC0415

    result = _run(["ffmpeg", "-v", "error", "-i", source.path, "-map", f"0:{source.stream_index}", "-f", "srt", "-"],
                  timeout=300)
    if result is None or result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError("ffmpeg could not read that subtitle track.")
    return result.stdout


def source_language(source: SubtitleSource, text: str) -> tuple[str, bool]:
    """(ISO 639-1 code, guessed): the track's own tag when it has one, else a guess from the text."""
    if source.language:
        return source.language, False
    sample = " ".join(c.text for c in parse_srt(text)[:80])
    return detect_language(sample), True


def translatable(sources: list[SubtitleSource]) -> list[SubtitleSource]:
    """The tracks worth translating to English: every one that is not (known to be) English."""
    return [s for s in sources if not s.is_english]


def has_english(sources: list[SubtitleSource]) -> bool:
    return any(s.is_english for s in sources)


def translate_all(cues: list[Cue], source_lang: str, target_lang: str, translator: Translator,
                  progress=None, cancel=None) -> TranslateResult:
    """translate_cues with the app's defaults: for the worker-thread runner (no widgets in here)."""
    return translate_cues(cues, source_lang, target_lang, translator, progress=progress, cancel=cancel)


# -- writing ----------------------------------------------------------------------------------------

def english_target(video_path) -> Path:
    p = Path(video_path)
    return p.with_name(f"{p.stem}.en.srt")


def free_english_alternative(video_path) -> Optional[str]:
    """The first language tag in ENGLISH_ALTERNATIVE_TAGS whose sidecar does not exist yet ("eng": <name>.eng.srt,
    still one English track to the ingest), or None when all are taken."""
    p = Path(video_path)
    for tag in ENGLISH_ALTERNATIVE_TAGS:
        if not p.with_name(f"{p.stem}.{tag}.srt").exists():
            return tag
    return None


def write_translation(video, cues: list[Cue], engine_name: str, source_lang: str, tag: str = "en") -> Path:
    """Writes the translated cues as <stem>.<tag>.srt beside `video` (a VideoFile) and never replaces a file:
    FileExistsError when it exists (the caller offers free_english_alternative()). The text is recorded as a
    machine translation and, when History is on, in the journal. The original subtitle is never touched."""
    text = format_srt(cues)
    path = video.save_subtitle_sidecar(text, language=tag, overwrite=False)
    mt_registry.mark(text, engine_name)
    note = f"machine translation ({engine_name}) from {source_lang or 'unknown'}"
    entry = history.journal_begin(
        video.path, "save", {"subtitle file": ""}, {"subtitle file": f"{path.name} ({note})"}, note=note
    )
    history.journal_finish(entry, True)
    return path
