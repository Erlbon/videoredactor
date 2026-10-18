"""
core/theporndb_client.py

ThePornDB (https://theporndb.net) REST API client: a scene / movie / JAV metadata source, looked up by the file's
OSHASH ("file match") or by title text ("title match"). Used like the TMDB and TheTVDB clients: the dialogs and the
Redact step call it, nothing is applied here.

Verified against the service's own OpenAPI document (https://api.theporndb.net/openapi.json, v3.24.757, fetched
without a key on 2026-10-16):
  - base https://api.theporndb.net, header "Authorization: Bearer <token>" (a Sanctum token from the profile's
    API Tokens page); JSON; an unknown or missing token answers 401 {"message": "Unauthenticated."}.
  - GET /scenes (also /movies, /jav): the list/search, answer {"data": [SceneResource...], "meta": {...}}. Search
    parameters used here: q (text), year (integer), per_page (1-100), page. (`parse` exists too: "parse a string into a
    searchable phrase"; not used, a plain q is predictable.)
  - GET /scenes/hash/{hash}?type=OSHASH|PHASH (also /movies/hash, /jav/hash): ONE SceneResource under "data"; 404 when
    the service has no scene with that hash. The hash types are OSHASH and PHASH only: there is no MD5 here.
  - GET /scenes/{identifier}: one SceneResource (identifier = slug, id or uuid).
  - GET /auth/user: the token's user ({"id", "name"}): the cheap authenticated request the key check uses.
  - SceneResource: id (uuid), _id, title, type, slug, external_id, description, date (YYYY-MM-DD), url (the studio's
    page), image, back_image, poster_image, poster, background{full,large,medium,small}, posters{...}, duration,
    sku, site{name, parent{...}, network{...}}, performers[{name, ...}], tags[{name}], directors[{name}], hashes[...].
    Parsing is defensive anyway: every field may be missing or null.
Not documented anywhere reachable without an account: a rate limit (third-party tools mention 120-240 requests a
minute; this client keeps at least one second between requests and retries a 429 twice) and terms of use. The
site's own privacy policy page is the only legal page that can be read without logging in.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from core import api_keys
from core.scene_lookup import (
    MATCH_FILE, MATCH_TITLE, SceneAuthError, SceneLookupError, SceneRateLimited, SceneResult, clean_date, clean_names,
    clean_text, clean_url, make_fetch, valid_oshash,
)
from redactor_common.core.lookup_client import Throttle, build_request, fetch_bytes, fetch_json, throttled
from core.scene_lookup import USER_AGENT

SERVICE = "ThePornDB"
BASE_URL = "https://api.theporndb.net"
SITE_URL = "https://theporndb.net"
KEY_ID = "theporndb"
MIN_GAP_SECONDS = 1.0
RETRY_429 = (2.0, 5.0)
MAX_COVER_BYTES = 20 * 1024 * 1024

# kind -> (path segment, label). All three answer with the same SceneResource.
KINDS = {"scene": ("scenes", "Scene"), "movie": ("movies", "Movie"), "jav": ("jav", "JAV")}

_throttle = Throttle(MIN_GAP_SECONDS)
# Injectable for tests (replace _fetch / _cover_fetch / _sleep); see redactor_common.core.lookup_client.
_fetch = throttled(make_fetch(15), _throttle)
_cover_fetch = make_fetch(20, MAX_COVER_BYTES)
_sleep = time.sleep


class ThePornDBError(SceneLookupError):
    """A failed ThePornDB request: missing key, rejected key, network down, a bad answer. The text is for the user."""


class ThePornDBAuthError(ThePornDBError, SceneAuthError):
    pass


class ThePornDBRateLimited(ThePornDBError, SceneRateLimited):
    pass


def get_api_key() -> Optional[str]:
    return api_keys.get_key(KEY_ID)


def _require_api_key(override: Optional[str] = None) -> str:
    key = override or get_api_key()
    if not key:
        raise ThePornDBAuthError(
            "No ThePornDB API key is set. Create a token at https://theporndb.net/user/api-tokens and enter it under "
            "Tools > API Keys... (or set the THEPORNDB_API_KEY environment variable)."
        )
    return key


def _get_json(path: str, params: Optional[dict] = None, ignore_404: bool = False,
              key: Optional[str] = None) -> Optional[dict]:
    key = _require_api_key(key)
    request = build_request(
        f"{BASE_URL}{path}", params,
        {"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": USER_AGENT},
    )
    data = fetch_json(
        request, _fetch, ThePornDBError, SERVICE, ignore_404=ignore_404,
        status_messages={
            401: "ThePornDB rejected the API key (HTTP 401 Unauthorized). Check the token under Tools > API Keys.",
            403: "ThePornDB refused the request (HTTP 403 Forbidden).",
            429: "ThePornDB's rate limit was hit (HTTP 429 Too Many Requests): wait a minute and try again.",
        },
        status_errors={401: ThePornDBAuthError, 429: ThePornDBRateLimited},
        retry_429=RETRY_429, sleep=_sleep, secrets=(key,),
        unreachable_message="Could not reach ThePornDB: {detail} (check your internet connection).",
    )
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ThePornDBError("Received an unexpected answer from ThePornDB.")
    return data


# --- parsing ------------------------------------------------------------------------------------------------------


def _name_of(node: Any) -> str:
    return clean_text(node.get("name"), 300, single_line=True) if isinstance(node, dict) else ""


def _names(nodes: Any) -> list[str]:
    return clean_names([_name_of(n) for n in nodes] if isinstance(nodes, list) else [])


def _poster_urls(scene: dict) -> list[str]:
    """The poster-type pictures of the record, best first: the `posters` sizes (the API's poster type), then the plain
    `poster` and `poster_image` fields. NEVER `background`, `image` or `back_image`: those are scene stills. The
    picture's real shape is checked after the download (core.poster), because a studio can put a still in `poster`."""
    candidates: list[Any] = []
    sizes = scene.get("posters")
    if isinstance(sizes, dict):
        candidates += [sizes.get("large"), sizes.get("full"), sizes.get("medium")]
    candidates += [scene.get("poster"), scene.get("poster_image")]
    urls: list[str] = []
    for candidate in candidates:
        url = clean_url(candidate)
        if url and url not in urls:
            urls.append(url)
    return urls


def _landscape_urls(scene: dict) -> list[str]:
    """The wide pictures (background, scene image): used only when the user ticks "Accept a landscape cover"."""
    candidates: list[Any] = []
    sizes = scene.get("background")
    if isinstance(sizes, dict):
        candidates += [sizes.get("large"), sizes.get("full"), sizes.get("medium")]
    candidates += [scene.get("image")]
    return [u for u in dict.fromkeys(clean_url(c) for c in candidates) if u]


def _scene_performers(scenes: Any) -> list[str]:
    """A movie record often lists its performers only on the scenes inside it: their distinct names, in order."""
    names: list[str] = []
    for item in scenes if isinstance(scenes, list) else []:
        if isinstance(item, dict):
            names.extend(n for n in _names(item.get("performers")) if n not in names)
    return names


def parse_scene(scene: Any, match_kind: str = MATCH_TITLE, kind: str = "scene") -> Optional[SceneResult]:
    """A SceneResource (dict) as a SceneResult; None for anything that is not a record with an id or a title."""
    if not isinstance(scene, dict):
        return None
    ident = clean_text(scene.get("id"), 80, single_line=True) or clean_text(str(scene.get("_id") or ""), 40)
    title = clean_text(scene.get("title"), 500, single_line=True)
    if not ident and not title:
        return None
    site = scene.get("site") if isinstance(scene.get("site"), dict) else {}
    parent = site.get("parent") if isinstance(site.get("parent"), dict) else site.get("network")
    studio = _name_of(site)
    segment = KINDS.get(kind, KINDS["scene"])[0]
    slug = clean_text(scene.get("slug"), 200, single_line=True)
    page = f"{SITE_URL}/{segment}/{slug or ident}" if (slug or ident) else ""
    duration = scene.get("duration")
    return SceneResult(
        source=SERVICE,
        external_id=ident,
        title=title,
        date=clean_date(scene.get("date")),
        studio=studio,
        parent_studio=_name_of(parent) if _name_of(parent) != studio else "",
        performers=_names(scene.get("performers")) or _scene_performers(scene.get("scenes")),
        tags=_names(scene.get("tags")),
        description=clean_text(scene.get("description")),
        director=", ".join(_names(scene.get("directors"))),
        urls=[u for u in (clean_url(scene.get("url")),) if u],
        page_url=page,
        cover_url=(_poster_urls(scene) or [""])[0],
        poster_urls=_poster_urls(scene),
        landscape_urls=_landscape_urls(scene),
        kind=kind,
        code=clean_text(scene.get("sku"), 100, single_line=True),
        duration=int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0 else None,
        match_kind=match_kind,
    )


def _results(data: Optional[dict], match_kind: str, kind: str) -> list[SceneResult]:
    node = (data or {}).get("data")
    items = node if isinstance(node, list) else [node] if isinstance(node, dict) else []
    return [r for r in (parse_scene(item, match_kind, kind) for item in items) if r is not None]


# --- the lookups ----------------------------------------------------------------------------------------------------


def search_by_hash(oshash: str, kinds: tuple[str, ...] = ("scene", "movie", "jav")) -> list[SceneResult]:
    """The records with this OSHASH ("file match"), trying scenes, then movies, then JAV, and stopping at the first
    kind that has one. Empty for an invalid hash or no match (a 404 is the normal "not in the database")."""
    if not valid_oshash(oshash):
        return []
    for kind in kinds:
        segment = KINDS[kind][0]
        data = _get_json(f"/{segment}/hash/{oshash.lower()}", {"type": "OSHASH"}, ignore_404=True)
        found = _results(data, MATCH_FILE, kind)
        if found:
            return found
    return []


def search_by_title(text: str, year: Optional[str] = None, kind: str = "scene", per_page: int = 25) -> list[SceneResult]:
    """The records matching `text` ("title match"), best first as the service ranks them. `year` narrows it."""
    query = " ".join((text or "").split())[:200]
    if not query:
        return []
    params: dict[str, Any] = {"q": query, "per_page": per_page}
    if year and str(year).strip().isdigit():
        params["year"] = int(str(year).strip())
    data = _get_json(f"/{KINDS[kind][0]}", params)
    return _results(data, MATCH_TITLE, kind)


# --- the structured search ladder (2026-10-18) ----------------------------------------------------------------------------
#
# GET /scenes (also /movies, /jav) takes FILTERS next to the free text `q` (OpenAPI v3.24.757, read 2026-10-18):
#   title (string)                   "The title to search by"
#   date (string YYYY-MM-DD) + date_operation (enum "=", ">", ">=", "<", "<=")
#   year (integer)
#   site (string, a site name) + site_operation (enum Network, Parent, Site, Site/Network, Site/Parent,
#                                   Site/Parent/Network); we send "Site/Parent/Network": the studio may be the site, its
#                                   parent studio or its network
#   performers: style deepObject, schema type object, description "The performer names to filter by", example
#                                   {"84060": "Mia Malkova"}: one query pair per entry, performers[<key>]=<name>. We have
#                                   names but no ids, so the key is the position: performers[0]=Mia Malkova&performers[1]=...
#   performer_and (boolean)          "filter by all performers or any": left out (any), a wrong guessed name must not empty
#                                   the answer.
# Filters are ANDed by the service, so each rung of the ladder relaxes the one before it and the ladder stops at the
# first rung that answers. UNPROVEN against the live service (no key was available): the dialog says which rung answered.

LADDER_STEPS = 4
SITE_OPERATION = "Site/Parent/Network"
MAX_PERFORMERS = 4
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def performer_params(names) -> dict[str, str]:
    """The deepObject encoding of performer names: {'performers[0]': 'Mia Malkova', ...} (urlencode makes the brackets
    %5B / %5D, which is what a deepObject query is)."""
    clean = []
    for name in names or ():
        text = " ".join(str(name).split())[:100]
        if text and text not in clean:
            clean.append(text)
    return {f"performers[{i}]": name for i, name in enumerate(clean[:MAX_PERFORMERS])}


def ladder_steps(fields: dict) -> list[tuple[int, str, dict]]:
    """The rungs that apply to these guessed fields, as (step number of LADDER_STEPS, label, query parameters), most
    specific first. fields: title, text (the plain q text, default the title), date, year, studio, performers.
    A rung is only built when it has something to filter by: an empty filter is never sent."""
    title = " ".join(str(fields.get("title") or "").split())[:200]
    text = " ".join(str(fields.get("text") or fields.get("title") or "").split())[:200]
    date = str(fields.get("date") or "").strip()
    date = date if _ISO_DATE.fullmatch(date) else ""
    year = str(fields.get("year") or "").strip()
    year = year if year.isdigit() else ""
    studio = " ".join(str(fields.get("studio") or "").split())[:100]
    performers = performer_params(fields.get("performers"))
    steps: list[tuple[int, str, dict]] = []
    if title and date:
        steps.append((1, f"title + date: {date}", {"title": title, "date": date, "date_operation": "="}))
    if title and (studio or performers):
        params: dict = {"title": title}
        label = ["title"]
        if studio:
            params.update({"site": studio, "site_operation": SITE_OPERATION})
            label.append(f"studio: {studio}")
        if performers:
            params.update(performers)
            label.append("performers")
        steps.append((2, " + ".join(label), params))
    elif not title and performers:
        params = dict(performers)
        label = ["performers"]
        if studio:
            params.update({"site": studio, "site_operation": SITE_OPERATION})
            label.append(f"studio: {studio}")
        steps.append((2, " + ".join(label), params))
    if title:
        params = {"title": title}
        if year:
            params["year"] = int(year)
        steps.append((3, f"title + year: {year}" if year else "title only", params))
    if text:
        params = {"q": text}
        if year:
            params["year"] = int(year)
        steps.append((4, "plain text search", params))
    unique: list[tuple[int, str, dict]] = []
    for step in steps:
        if all(step[2] != other[2] for other in unique):
            unique.append(step)
    return unique


def search_structured(fields: dict, kind: str = "scene", per_page: int = 25, trace: Optional[list] = None,
                      plain_only: bool = False) -> tuple[list[SceneResult], str]:
    """Runs the ladder (one request per rung, the client's throttle keeps the gap) and stops at the first rung with
    results: returns (results, the rung's label); ([], "") when none answered. `plain_only` runs only the old q rung.
    Every rung tried is appended to `trace` as (step, LADDER_STEPS, label, number of results) so the dialog can say
    what was tried. An auth / network / rate-limit error propagates (ThePornDBError) and ends the ladder."""
    segment = KINDS[kind][0]
    for step, label, params in ladder_steps(fields):
        if plain_only and step != 4:
            continue
        data = _get_json(f"/{segment}", {**params, "per_page": per_page})
        found = _results(data, MATCH_TITLE, kind)
        if trace is not None:
            trace.append((step, LADDER_STEPS, label, len(found)))
        if found:
            return found, label
    return [], ""


def get(identifier: str, kind: str = "scene") -> SceneResult:
    """One record by slug, id or uuid; ThePornDBError when there is none."""
    ident = clean_text(identifier, 200, single_line=True)
    if not ident or "/" in ident:
        raise ThePornDBError("That is not a ThePornDB identifier.")
    data = _get_json(f"/{KINDS[kind][0]}/{ident}", ignore_404=True)
    found = _results(data, MATCH_TITLE, kind)
    if not found:
        raise ThePornDBError(f"ThePornDB has no {KINDS[kind][1].lower()} {ident}.")
    return found[0]


def check_key(key: Optional[str] = None) -> str:
    """One cheap authenticated request (GET /auth/user), with `key` (typed but not saved yet) or the stored one.
    Returns the OK line; raises ThePornDBError with the exact reason (no key, HTTP 401, unreachable...)."""
    data = _get_json("/auth/user", key=key) or {}
    user = data.get("data") if isinstance(data.get("data"), dict) else data
    name = clean_text(user.get("name"), 100, single_line=True) if isinstance(user, dict) else ""
    return f"OK: ThePornDB accepted the key" + (f" (account: {name})" if name else "") + "."


def download_cover(url: str) -> bytes:
    """The image bytes at a cover URL taken from a result. Called only when the user applied the result and asked for
    the cover. No key is sent: the images are on ThePornDB's public CDN."""
    clean = clean_url(url)
    if not clean:
        raise ThePornDBError("The result has no usable cover address.")
    return fetch_bytes(
        clean, _cover_fetch, ThePornDBError, what="the cover", retry_429=RETRY_429, sleep=_sleep,
        unreachable_message="Could not download the cover: {detail}.",
    )
