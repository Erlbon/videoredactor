"""
core/transcode_settings.py

Persisted defaults for the H.264/AAC transcode feature (see
core/ffmpeg_backend.transcode_to_mp4): CRF, audio bitrate, and thread
count. Edited once via gui/tool_settings_dialog.py's Transcode Defaults
section and reused on every "Convert Selected to MP4" run -- these are
encode-quality/hardware knobs a user sets to match their own preference
once, not something worth re-asking per batch (unlike, say, the TMDB
search dialog's per-search Year field).
"""

from __future__ import annotations
from dataclasses import dataclass

from core.config import get_setting, set_setting

_SECTION = "transcode"
_CRF_KEY = "crf"
_AUDIO_BITRATE_KEY = "audio_bitrate"
_THREADS_KEY = "threads"

DEFAULT_CRF = 23
DEFAULT_AUDIO_BITRATE = "128k"
# 0 means "no -threads flag at all" -- let ffmpeg pick its own default,
# rather than this project inventing a fake "0 threads" meaning.
DEFAULT_THREADS = 0


@dataclass
class TranscodeSettings:
    crf: int = DEFAULT_CRF
    audio_bitrate: str = DEFAULT_AUDIO_BITRATE
    threads: int = DEFAULT_THREADS  # 0 = let ffmpeg decide


def get_transcode_settings() -> TranscodeSettings:
    """Reads settings.ini, tolerating a corrupted/hand-edited CRF or
    thread count by falling back to the default rather than raising --
    same "a bad settings.ini shouldn't crash a normal run" reasoning as
    the rest of this app's settings readers.
    """
    try:
        crf = int(get_setting(_SECTION, _CRF_KEY, str(DEFAULT_CRF)))
    except ValueError:
        crf = DEFAULT_CRF
    try:
        threads = int(get_setting(_SECTION, _THREADS_KEY, str(DEFAULT_THREADS)))
    except ValueError:
        threads = DEFAULT_THREADS
    audio_bitrate = get_setting(_SECTION, _AUDIO_BITRATE_KEY, DEFAULT_AUDIO_BITRATE) or DEFAULT_AUDIO_BITRATE
    return TranscodeSettings(crf=crf, audio_bitrate=audio_bitrate, threads=threads)


def set_transcode_settings(settings: TranscodeSettings) -> None:
    set_setting(_SECTION, _CRF_KEY, str(settings.crf))
    set_setting(_SECTION, _AUDIO_BITRATE_KEY, settings.audio_bitrate)
    set_setting(_SECTION, _THREADS_KEY, str(settings.threads))
