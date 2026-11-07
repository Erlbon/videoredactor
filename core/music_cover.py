"""
core/music_cover.py

Metadata > Look Up > Get Cover: candidate covers for ONE music video, searched by its artist and song title (the tags,
else the file name "Artist - Title", the same query Get Lyrics uses). The cover of a music video is the song's release
artwork, square (core/poster.py ALBUM_KIND), so the sources are the music ones:

  iTunes            the iTunes Search API (no key): the song's album/single artwork at 1000 x 1000
  MusicBrainz       recordings by artist + title, one cover per release group from the Cover Art Archive (no key;
                    MusicBrainz' one-request-a-second rule through redactor_common's shared throttle)
  Discogs           releases by artist + track, their cover image (only with a Discogs token, Tools > API Keys;
                    without one the source is skipped and said so)

Every candidate's picture is downloaded here (the dialog shows them all side by side, and the one chosen is not fetched
twice); a picture that is not a square cover big enough is left out with a note. Nothing is written: the caller stages
the chosen one (core/poster.prepare_poster with ALBUM_KIND). Qt-free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import quote, urlencode

from redactor_common.core.lookup_client import LookupError as FetchError, fetch_bytes, fetch_json, make_default_fetch
from redactor_common.core.lookups import discogs, musicbrainz

from core.poster import ALBUM_KIND, image_size, judge_poster
from core.version import APP_VERSION

# No "#" in it: MusicBrainz answers 503 to a User-Agent with the version as written ("2026-11-06#02").
USER_AGENT = f"videoredactor/{APP_VERSION.replace('#', '.')} ( https://github.com/Erlbon/videoredactor )"
musicbrainz.configure(USER_AGENT)
discogs.configure(USER_AGENT, secret_app="videoredactor")

ITUNES_SEARCH = "https://itunes.apple.com/search"
ITUNES_SIZE = "1000x1000bb"
PER_SOURCE = 4  # candidates kept from each source
SOURCES = ("itunes", "musicbrainz", "discogs")
SOURCE_NAMES = {"itunes": "iTunes", "musicbrainz": "MusicBrainz", "discogs": "Discogs"}

_fetch = make_default_fetch(USER_AGENT, timeout=20.0)


class CoverSearchError(Exception):
    """One source failed; the message says why in plain words."""


@dataclass
class CoverCandidate:
    source: str          # "iTunes", "MusicBrainz", "Discogs"
    release: str         # "Violator (1990)"
    url: str
    data: bytes = b""
    size: tuple[int, int] = (0, 0)

    def label(self) -> str:
        shape = f"{self.size[0]}x{self.size[1]}" if self.size[0] else "size unknown"
        return f"{self.source}: {self.release}\n{shape}"


@dataclass
class CoverResults:
    candidates: list[CoverCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # one line per source that gave nothing, and why


def _year(text: str) -> str:
    return (text or "")[:4] if (text or "")[:4].isdigit() else ""


def _release_text(title: str, year: str) -> str:
    title = (title or "").strip() or "unknown release"
    return f"{title} ({year})" if year else title


# --- the sources (each returns candidates without pictures) -----------------------------------------------------------


def search_itunes(artist: str, title: str, fetch: Callable = None) -> list[CoverCandidate]:
    term = f"{artist} {title}".strip()
    url = f"{ITUNES_SEARCH}?{urlencode({'term': term, 'entity': 'song', 'limit': 15}, quote_via=quote)}"
    data = fetch_json(url, fetch or _fetch, error_cls=CoverSearchError, source_name="iTunes") or {}
    out, seen = [], set()
    want_artist = artist.casefold().strip()
    for item in data.get("results") or []:
        if not isinstance(item, dict):
            continue
        art = str(item.get("artworkUrl100") or "")
        if not art or "100x100bb" not in art:
            continue
        if want_artist and want_artist not in str(item.get("artistName") or "").casefold():
            continue  # a cover version or a namesake: not this artist's release
        album = str(item.get("collectionName") or "")
        if album.casefold() in seen:
            continue
        seen.add(album.casefold())
        out.append(CoverCandidate("iTunes", _release_text(album, _year(str(item.get("releaseDate") or ""))),
                                  art.replace("100x100bb", ITUNES_SIZE)))
    return out


class _MusicBrainzBusy(CoverSearchError):
    pass


MB_BUSY = "MusicBrainz is busy (rate limit) -- try again in a moment."
MB_RETRY_WAITS = (2.0, 4.0)


def _mb_get(url: str, fetch: Callable, sleep: Callable[[float], None]) -> dict:
    """One MusicBrainz search; its rate limit (HTTP 503) is waited out twice before it is reported."""
    for wait in MB_RETRY_WAITS + (None,):
        try:
            return fetch_json(url, fetch, error_cls=CoverSearchError, source_name="MusicBrainz",
                              status_messages={503: MB_BUSY}, status_errors={503: _MusicBrainzBusy}) or {}
        except _MusicBrainzBusy:
            if wait is None:
                raise CoverSearchError(MB_BUSY) from None
            sleep(wait)
    return {}


def search_musicbrainz(artist: str, title: str, fetch: Callable = None, sleep: Callable[[float], None] = time.sleep) -> list[CoverCandidate]:
    lucene = f"recording:{musicbrainz._lucene(title)}"
    if artist:
        lucene += f" AND artist:{musicbrainz._lucene(artist)}"
    # A famous song has hundreds of recordings, most of them live bootlegs: the studio releases first, then anything.
    data = {}
    for query in (lucene + _MB_STUDIO_FILTER, lucene):
        url = f"{musicbrainz.API_BASE}/recording?{urlencode({'query': query, 'limit': 25, 'fmt': 'json'}, quote_via=quote)}"
        data = _mb_get(url, fetch or musicbrainz._default_fetch, sleep)
        if data.get("recordings"):
            break
    ranked, seen = [], set()
    for recording in data.get("recordings") or []:
        for release in (recording or {}).get("releases") or []:
            group = (release or {}).get("release-group") or {}
            group_id = group.get("id")
            if not group_id or group_id in seen:
                continue
            seen.add(group_id)
            date = release.get("date") or ""
            candidate = CoverCandidate(
                "MusicBrainz", _release_text(release.get("title") or group.get("title") or "", _year(date)),
                f"{musicbrainz.COVER_ART_BASE}/release-group/{group_id}/front-500")
            ranked.append(((_mb_rank(release, group), date or "9999"), candidate))  # undated last
    return [candidate for _key, candidate in sorted(ranked, key=lambda pair: pair[0])]


_MB_TYPE_ORDER = {"single": 0, "album": 1, "ep": 2}
_MB_STUDIO_FILTER = (" AND status:official AND (primarytype:single OR primarytype:album OR primarytype:ep)"
                     " AND NOT secondarytype:live AND NOT secondarytype:compilation")


def _mb_rank(release: dict, group: dict) -> int:
    """Official singles first, then albums and EPs; live recordings, compilations, bootlegs and the rest after."""
    rank = _MB_TYPE_ORDER.get(str(group.get("primary-type") or "").casefold(), 3)
    if group.get("secondary-types"):
        rank += 4  # Live, Compilation, Remix, DJ-mix...
    if str(release.get("status") or "Official").casefold() != "official":
        rank += 8
    return rank


def search_discogs(artist: str, title: str, client: Optional[discogs.DiscogsClient] = None) -> list[CoverCandidate]:
    if client is None:
        token = discogs.load_token()
        if not token:
            raise CoverSearchError("no Discogs token (Tools > API Keys)")
        client = discogs.DiscogsClient(token)
    params = {"type": "release", "per_page": 15}
    if artist:
        params["artist"] = artist
    params["track"] = title
    try:
        data = client.get_json("database/search", **params)
    except discogs.DiscogsError as exc:
        raise CoverSearchError(str(exc)) from None
    out, seen = [], set()
    for item in data.get("results") or []:
        image = str((item or {}).get("cover_image") or "")
        if not image or image.endswith("spacer.gif"):
            continue
        name = str(item.get("title") or "")
        key = (item.get("master_id") or name.casefold())
        if key in seen:
            continue
        seen.add(key)
        out.append(CoverCandidate("Discogs", _release_text(name, str(item.get("year") or "")), image))
    return out


# --- the whole search ---------------------------------------------------------------------------------------------------


def _download(candidate: CoverCandidate, discogs_client) -> bytes:
    if candidate.source == "Discogs" and discogs_client is not None:
        return discogs_client.get_image(candidate.url) or b""
    return fetch_bytes(candidate.url, _fetch, error_cls=CoverSearchError, what="the cover")


def find_covers(
    artist: str,
    title: str,
    *,
    searches: Optional[dict[str, Callable[[str, str], list[CoverCandidate]]]] = None,
    download: Optional[Callable[[CoverCandidate], bytes]] = None,
    should_cancel: Callable[[], bool] = lambda: False,
    per_source: int = PER_SOURCE,
) -> CoverResults:
    """Candidates from every source, with their pictures; a source that fails or finds nothing gets a note."""
    results = CoverResults()
    if not title.strip():
        results.notes.append("no song title to search with (set the Title, or name the file 'Artist - Title')")
        return results
    discogs_client = None
    if searches is None:
        token = discogs.load_token()
        discogs_client = discogs.DiscogsClient(token) if token else None
        searches = {
            "iTunes": search_itunes,
            "MusicBrainz": search_musicbrainz,
            "Discogs": lambda a, t: search_discogs(a, t, discogs_client) if discogs_client else _no_token(),
        }
    get = download or (lambda c: _download(c, discogs_client))
    for name, search in searches.items():
        if should_cancel():
            break
        try:
            found = search(artist, title)
        except (CoverSearchError, FetchError, OSError) as exc:
            results.notes.append(f"{name}: {exc}")
            continue
        kept, rejected = 0, []
        for candidate in found:
            if kept >= per_source or should_cancel():
                break
            try:
                candidate.data = get(candidate)
            except (CoverSearchError, FetchError, OSError, discogs.DiscogsError):
                candidate.data = b""
            size = image_size(candidate.data) if candidate.data else None
            if not size:
                continue  # Cover Art Archive has no front cover for many releases: a 404, not worth a note each
            verdict = judge_poster(size[0], size[1], ALBUM_KIND)
            if not verdict.ok:
                rejected.append(verdict.reason)
                continue
            candidate.size = size
            results.candidates.append(candidate)
            kept += 1
        if not kept:
            results.notes.append(f"{name}: " + (f"no usable cover ({rejected[0]})" if rejected else "nothing found"))
    return results


def _no_token() -> list[CoverCandidate]:
    raise CoverSearchError(discogs.TOKEN_HELP)
