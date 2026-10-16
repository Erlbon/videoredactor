"""
core/lookup_sources.py

From an accepted lookup result to the source fields it writes (core.source_tags.source_values). One place per
service so the dialogs, the Redact steps and the tests agree. Qt-free; nothing here touches a file.

`how` is the METADATA_MATCH word: file (the service knows the file's hash), title (found by a search), manual (the id
was chosen without a search, e.g. it was already in the file).
"""

from __future__ import annotations

from typing import Optional

from redactor_common.core.source_ids import SourceRef, make_ref

from core import scene_lookup
from core.source_tags import source_values

_HOW_FOR_MATCH = {scene_lookup.MATCH_FILE: "file", scene_lookup.MATCH_ID: "manual", scene_lookup.MATCH_TITLE: "title"}


def scene_refs(result: "scene_lookup.SceneResult") -> list[SourceRef]:
    """The reference a ThePornDB / StashDB result names ([] without an id, or for a stash-box that is not stashdb.org:
    its ids are not StashDB's)."""
    service = {"ThePornDB": "theporndb", "StashDB": "stashdb"}.get(result.source, "")
    if not service or not result.external_id or not result.id_trusted:
        return []
    ref = make_ref(service, result.kind if service == "theporndb" else "scene", result.external_id)
    return [ref] if ref is not None else []


def scene_values(result: "scene_lookup.SceneResult", how: Optional[str] = None) -> dict[str, str]:
    """The source fields applying `result` sets."""
    return source_values(scene_refs(result), how or _HOW_FOR_MATCH.get(result.match_kind, "title"), result.page_url)


def tmdb_values(kind: str, tmdb_id, season: Optional[int] = None, episode: Optional[int] = None,
                imdb_id: str = "", tvdb_id=None, how: str = "title") -> dict[str, str]:
    """TMDB: a movie, or a show (with `season` and `episode` set: that episode, the most specific record). The IMDb
    / TheTVDB ids TMDB itself reports for the same record are included (they come from the accepted result)."""
    refs: list[Optional[SourceRef]] = []
    if kind == "movie":
        refs.append(make_ref("tmdb", "movie", str(tmdb_id)))
    elif season is not None and episode is not None:
        refs.append(make_ref("tmdb", "episode", f"{tmdb_id}/{season}/{episode}"))
    else:
        refs.append(make_ref("tmdb", "tv", str(tmdb_id)))
    if imdb_id:
        refs.append(make_ref("imdb", "title", str(imdb_id)))
    if tvdb_id:
        refs.append(make_ref("tvdb", "movie" if kind == "movie" else "series", str(tvdb_id)))
    return source_values([r for r in refs if r is not None], how)


def tvdb_values(series_id, how: str = "title") -> dict[str, str]:
    ref = make_ref("tvdb", "series", str(series_id))
    return source_values([ref] if ref is not None else [], how)


def imdb_values(tconst: str, how: str = "title") -> dict[str, str]:
    ref = make_ref("imdb", "title", str(tconst))
    return source_values([ref] if ref is not None else [], how)
