"""
core/lyrics_subtitles.py

LRCLIB synced lyrics as a subtitle source for MUSIC VIDEOS (Qt-free). The reusable parts (client, LRC parser,
LRC -> SRT, match scoring) live in redactor_common.core.lrclib; this module adds what is specific to a video:
the artist / title / album / length to search with, the search-first flow (/search, then the exact /get only as a soft extra), and the language tag.

The user always picks explicitly in the dialog. In Redact only an "exact" match (names equal, length within
2 s) is ever written by itself; everything else is a suggestion for the review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

from redactor_common.core import lrclib
from redactor_common.core.lrclib import LrclibCancelled, LrclibClient, LrclibError, Lyrics, Match, rank_matches

from core.subtitle_ingest import ISO_639_2, language_code
from core.version import APP_VERSION

_LOG = logging.getLogger(__name__)
USER_AGENT = f"TheVideoRedactor/{APP_VERSION} (https://github.com/Erlbon)"
SYNC_WARNING = "Sync is not guaranteed: lyrics are timed to the album version."

_client: Optional[LrclibClient] = None
_client_factory: Callable[[], LrclibClient] = lambda: LrclibClient(USER_AGENT)


def get_client() -> LrclibClient:
    """The process-wide client (one throttle for every request to LRCLIB)."""
    global _client
    if _client is None:
        _client = _client_factory()
    return _client


def set_client(client: Optional[LrclibClient]) -> None:
    """Replace (or, with None, reset) the shared client: tests and the offline e2e scripts."""
    global _client
    _client = client


@dataclass
class LyricsQuery:
    artist: str = ""
    title: str = ""
    album: str = ""
    duration: Optional[float] = None


def query_for_video(video) -> LyricsQuery:
    """What to search with, from a VideoFile: its tags (artist, title = the song, album), falling back
    to the file name "Artist - Title"; the length from the probed duration."""
    md = video.metadata
    artist = (md.artist or "").strip()
    title = (md.title or "").strip()
    stem_artist, stem_title = lrclib.split_artist_title(video.path.stem)
    if not title:
        title = stem_title
    if not artist:
        artist = stem_artist
    return LyricsQuery(artist, title, (md.album or "").strip(), md.duration_seconds or None)


def _search_steps(client: LrclibClient, query: LyricsQuery, hooks: dict) -> list[Lyrics]:
    """/search only (the cheap call): artist + title, then the title alone, then the free-text form. The next
    step runs only while the previous one found nothing."""
    results = client.search(query.artist, query.title, **hooks)
    if not results and query.artist:
        results = client.search("", query.title, **hooks)
    if not results:
        results = client.search(query=f"{query.artist} {query.title}".strip(), **hooks)
    return results


def find_lyrics(
    query: LyricsQuery,
    client: Optional[LrclibClient] = None,
    on_status: Optional[Callable[[str], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
    notes: Optional[list[str]] = None,
) -> list[Match]:
    """Entries for the query, best first, scored against the video's length.

    /search comes first (LRCLIB answers it fast even when it is overloaded): artist and title, then the title
    alone, then free text. The exact-signature call /get is the expensive one LRCLIB overloads first (HTTP 503 for
    hours, 2026-10-17), so it is only an optional extra: asked once, without retries, when the search found no exact
    match and artist and length are known; any LrclibError from it is a SOFT failure (a line is appended to `notes`
    and logged, the search results are used). If the SEARCH itself fails (after the client's short retries) /get is
    tried once as a last resort, and the search's error is raised when that gives nothing too.

    `on_status(text)` receives 'LRCLIB is busy (HTTP 503), trying again in N s...' during every wait and
    `should_cancel()` is polled during requests and waits (LrclibCancelled is raised, never swallowed).
    Raises LrclibError."""
    client = client or get_client()
    hooks: dict = {}
    if on_status is not None:
        hooks["on_status"] = on_status
    if should_cancel is not None:
        hooks["should_cancel"] = should_cancel
    if not query.title:
        return []

    results: list[Lyrics] = []
    search_error: Optional[LrclibError] = None
    try:
        results = _search_steps(client, query, hooks)
    except LrclibCancelled:
        raise
    except LrclibError as exc:
        search_error = exc

    wants_get = bool(query.artist and query.duration)
    if wants_get and (search_error is not None or not any(
            m.score.is_exact for m in rank_matches(query.artist, query.title, query.duration, results))):
        try:
            hit = client.get_by_signature(
                query.artist, query.title, query.album, query.duration, retry=False,
                **({"should_cancel": should_cancel} if should_cancel is not None else {}),
            )
        except LrclibCancelled:
            raise
        except LrclibError as exc:
            _LOG.info("LRCLIB exact lookup skipped: %s", exc)
            if notes is not None:
                notes.append(f"The exact-match lookup was skipped: {exc}")
        else:
            if hit is not None:
                results.insert(0, hit)
    if search_error is not None and not results:
        raise search_error
    return rank_matches(query.artist, query.title, query.duration, results)


def srt_for(lyrics: Lyrics, media_duration: Optional[float], offset_seconds: float = 0.0) -> str:
    return lrclib.lrc_to_srt(lyrics.synced, media_duration=media_duration, offset_seconds=offset_seconds)


_ISO1_BY_2 = {}
for _one, _two in ISO_639_2.items():
    if len(_one) == 2 and _two not in _ISO1_BY_2:
        _ISO1_BY_2[_two] = _one


def language_choices() -> list[tuple[str, str]]:
    """(code for the file name, display name): ISO 639-1 like the OpenSubtitles path writes, "und" first."""
    names = [
        ("und", "Unknown (und)"), ("en", "English"), ("ru", "Russian"), ("uk", "Ukrainian"), ("de", "German"),
        ("fr", "French"), ("es", "Spanish"), ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"),
        ("sv", "Swedish"), ("da", "Danish"), ("no", "Norwegian"), ("fi", "Finnish"), ("pl", "Polish"),
        ("cs", "Czech"), ("hu", "Hungarian"), ("tr", "Turkish"), ("el", "Greek"), ("he", "Hebrew"),
        ("ar", "Arabic"), ("ja", "Japanese"), ("ko", "Korean"), ("zh", "Chinese"),
    ]
    return names


def guess_language(video) -> str:
    """A code from the video's Language tag when it names exactly one language, else "und"."""
    raw = (video.metadata.language or "").strip()
    if not raw:
        return "und"
    parts = [p for p in raw.replace(",", ";").split(";") if p.strip()]
    if len(parts) != 1:
        return "und"
    two = language_code(parts[0])
    return _ISO1_BY_2.get(two, "und") if two else "und"


def best_timed(query: LyricsQuery, client: Optional[LrclibClient] = None) -> Optional[Match]:
    """The best entry that has timing (exact before close before different), or None. For the Redact step."""
    for match in find_lyrics(query, client):
        if match.lyrics.has_timing:
            return match
    return None


__all__ = [
    "LrclibCancelled", "LrclibError", "LyricsQuery", "SYNC_WARNING", "USER_AGENT", "find_lyrics", "get_client", "guess_language",
    "language_choices", "query_for_video", "best_timed", "set_client", "srt_for",
]
