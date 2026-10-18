"""
core/poster_fetch.py

Get a TRUE POSTER (core/poster.py: the source's own poster-type picture, portrait, big enough) for a file from the source
ids it ALREADY carries (core/source_tags.py: TMDB_ID, TVDB_ID, IMDB_ID, THEPORNDB_ID, STASHDB_ID), without searching for
anything: the id names the record, the record names its poster. Used by Metadata > Get Poster and the Redact step "Get the
poster from the file's source ids".

  films     TMDB movie id, else the IMDb id through TMDB's /find
  TV        TMDB tv / season / episode id (the SERIES poster), else the TheTVDB series id, else the IMDb id through TMDB
  scenes    ThePornDB id (scene, movie, jav), else the StashDB scene id

The order follows the file's content type (a Porn file asks the scene services first, everything else TMDB first) and every
source the file has an id for is tried until one gives a poster; a source without an API key, without a poster, or with a
picture that is not a true poster is skipped and said so in the note. Nothing is written here: the caller stages the
prepared poster (VideoMetadata.cover_pending). Qt-free except core.poster's decoding (QImage), like the lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from core import api_keys, stashdb_client, theporndb_client, tmdb_client, tvdb_client
from core.poster import PosterRejected, PreparedPoster, prepare_poster
from core.scene_lookup import SceneLookupError
from core.source_tags import ref_in
from core.video_metadata import ContentType, VideoMetadata

SCENE_SERVICES = ("theporndb", "stashdb")
FILM_SERVICES = ("tmdb", "tvdb", "imdb")
SERVICE_NAMES = {"theporndb": "ThePornDB", "stashdb": "StashDB", "tmdb": "TMDB", "tvdb": "TheTVDB", "imdb": "IMDb (through TMDB)"}


@dataclass
class PosterOutcome:
    poster: Optional[PreparedPoster] = None
    service: str = ""                      # the service that gave it
    notes: list[str] = field(default_factory=list)   # one line per source tried that gave none, and why

    @property
    def found(self) -> bool:
        return self.poster is not None

    def why_not(self) -> str:
        return "; ".join(self.notes) if self.notes else "the file carries no source id to take a poster from"


def services_for(metadata: VideoMetadata) -> list[str]:
    """The services this file has an id for, in the order they are tried (by content type)."""
    order = SCENE_SERVICES + FILM_SERVICES if metadata.content_type is ContentType.PORN else FILM_SERVICES + SCENE_SERVICES
    out = []
    for service in order:
        if service == "imdb":
            if ref_in(metadata, "imdb") is not None and ref_in(metadata, "tmdb") is None:
                out.append(service)  # an IMDb id is only a way into TMDB when the file has no TMDB id of its own
        elif ref_in(metadata, service) is not None:
            out.append(service)
    return out


def _candidates(service: str, metadata: VideoMetadata) -> tuple[list[str], Callable[[str], bytes]]:
    """(the poster addresses of the record the file's id names, how to download one). Raises the service's own error."""
    if service in SCENE_SERVICES:
        client = theporndb_client if service == "theporndb" else stashdb_client
        ref = ref_in(metadata, service)
        result = client.get(ref.id, ref.kind) if service == "theporndb" else client.get(ref.id)
        return list(result.poster_urls), client.download_cover
    if service == "tmdb":
        ref = ref_in(metadata, "tmdb")
        ident = ref.id.split("/")[0]
        if not ident.isdigit():
            return [], tmdb_client.download_poster
        details = tmdb_client.get_movie_details(int(ident)) if ref.kind == "movie" else tmdb_client.get_tv_show_details(int(ident))
        path = details.get("_poster_path") or ""
        return ([path] if path else []), tmdb_client.download_poster
    if service == "tvdb":
        ref = ref_in(metadata, "tvdb", ("series",))
        if ref is None or not ref.id.isdigit():
            return [], tvdb_client.download_image
        url = tvdb_client.get_series_details(int(ref.id)).get("_poster_path") or ""
        return ([url] if url else []), tvdb_client.download_image
    ref = ref_in(metadata, "imdb")  # imdb: through TMDB's /find
    path = tmdb_client.find_poster_by_imdb(ref.id)
    return ([path] if path else []), tmdb_client.download_poster


def _key_name(service: str) -> str:
    return "tmdb" if service == "imdb" else service


def fetch_poster(
    metadata: VideoMetadata, *, accept_landscape: bool = False, cancel_event=None,
) -> PosterOutcome:
    """The first true poster any of the file's ids gives, or an outcome whose notes say why none did."""
    outcome = PosterOutcome()
    services = services_for(metadata)
    if not services:
        return outcome
    for service in services:
        if cancel_event is not None and cancel_event.is_set():
            outcome.notes.append("cancelled")
            break
        name = SERVICE_NAMES[service]
        if not api_keys.get_key(_key_name(service)):
            outcome.notes.append(f"{name}: no API key set (Tools > API Keys)")
            continue
        try:
            urls, download = _candidates(service, metadata)
        except (SceneLookupError, tmdb_client.TMDBError, tvdb_client.TVDBError, OSError) as exc:
            outcome.notes.append(f"{name}: {exc}")
            continue
        if not urls:
            outcome.notes.append(f"{name}: the record has no poster")
            continue
        reasons: list[str] = []
        for url in urls[:3]:
            try:
                outcome.poster = prepare_poster(download(url), "poster", accept_landscape)
            except PosterRejected as exc:
                reasons.append(str(exc))
                continue
            except (SceneLookupError, tmdb_client.TMDBError, tvdb_client.TVDBError, OSError) as exc:
                reasons.append(str(exc))
                break
            outcome.service = name
            return outcome
        outcome.notes.append(f"{name}: " + (reasons[0] if reasons else "no usable poster"))
    return outcome
