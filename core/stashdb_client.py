"""
core/stashdb_client.py

StashDB (https://stashdb.org) and any other stash-box instance: a GraphQL scene-metadata source, looked up by the
file's OSHASH ("file match"; MD5 too when Preferences allows it) or by title text ("title match"). The endpoint is a
setting (core/scene_lookup.stash_endpoint(), default https://stashdb.org/graphql); the key is the one stored under
Tools > API Keys for that instance.

Verified against the open-source schema (github.com/stashapp/stash-box, graphql/schema/schema.graphql and
types/scene.graphql, master, fetched without a key on 2026-10-16), where it differs from what one might expect:
  - POST <endpoint>, header "ApiKey: <key>", body {"query", "variables"}. A missing key answers HTTP 200 with
    {"errors": [{"message": "not authorized"}], "data": {"me": null}}; an invalid key answers HTTP 401 with an empty
    body (both observed against stashdb.org with plain unauthenticated requests).
  - findScenesBySceneFingerprints(fingerprints: [[FingerprintQueryInput!]!]!): [[Scene]!]!  (one inner list per file;
    FingerprintQueryInput {hash: FingerprintHash!, algorithm: MD5 | OSHASH | PHASH}).
  - searchScene(term, limit) is DEPRECATED: searchScenes(term, limit, page, per_page): QueryScenesResultType
    {count, scenes} replaces it (this client uses searchScenes and falls back to searchScene on an older instance).
  - Scene.date is DEPRECATED in favour of release_date (this client asks for release_date and falls back to date on an
    older instance). URL.type is deprecated too (the `site` field replaced it): only `url` is requested.
  - Scene { id, title, details, release_date, production_date, urls{url}, studio{name, parent{name}},
    tags{name}, images{url,width,height}, performers{as, performer{name, disambiguation}}, duration, director, code }.
  - me: User {name}: the cheap authenticated request the key check uses.
Not documented anywhere reachable without an account: a rate limit and terms of use (the guidelines site, which is
reachable, says nothing about either). This client keeps at least one second between requests and retries a 429 twice.
"""

from __future__ import annotations

import time
import urllib.parse
from typing import Any, Optional

from core import api_keys
from core.scene_lookup import (
    MATCH_FILE, MATCH_TITLE, USER_AGENT, SceneAuthError, SceneLookupError, SceneRateLimited, SceneResult, clean_date,
    clean_names, clean_text, clean_url, make_fetch, page_base, stash_endpoint, valid_oshash,
)
from redactor_common.core.lookup_client import (
    Throttle, build_request, fetch_bytes, fetch_json, redact_text, throttled,
)

SERVICE = "StashDB"
KEY_ID = "stashdb"
MIN_GAP_SECONDS = 1.0
RETRY_429 = (2.0, 5.0)
MAX_COVER_BYTES = 20 * 1024 * 1024
RESULT_LIMIT = 25

_throttle = Throttle(MIN_GAP_SECONDS)
# Injectable for tests (replace _fetch / _cover_fetch / _sleep); see redactor_common.core.lookup_client.
_fetch = throttled(make_fetch(20), _throttle)
_cover_fetch = make_fetch(20, MAX_COVER_BYTES)
_sleep = time.sleep


class StashDBError(SceneLookupError):
    """A failed stash-box request: missing key, rejected key, network down, a GraphQL error. The text is for the user."""


class StashDBAuthError(StashDBError, SceneAuthError):
    pass


class StashDBRateLimited(StashDBError, SceneRateLimited):
    pass


class StashSchemaError(StashDBError):
    """The server does not know a field or query this client asked for (an older stash-box); callers may retry with
    the older name."""


def get_api_key() -> Optional[str]:
    return api_keys.get_key(KEY_ID)


def _require_api_key(override: Optional[str] = None) -> str:
    key = override or get_api_key()
    if not key:
        raise StashDBAuthError(
            "No StashDB API key is set. Register at https://stashdb.org/register, copy the API key from your profile "
            "and enter it under Tools > API Keys... (or set the STASHDB_API_KEY environment variable)."
        )
    return key


_SCENE_FIELDS = """
    id title details __DATE__ urls { url }
    studio { name parent { name } }
    performers { as performer { name disambiguation } }
    tags { name }
    images { url width height }
    duration director code
"""


def _scene_fields(date_field: str = "release_date") -> str:
    return _SCENE_FIELDS.replace("__DATE__", date_field)


def _first_error(errors: Any, key: str) -> str:
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return redact_text(clean_text(errors[0].get("message"), 300, single_line=True), (key,))
    return ""


def _post(query: str, variables: Optional[dict] = None, key: Optional[str] = None,
          endpoint: Optional[str] = None) -> dict:
    """One GraphQL request; the `data` object of the answer. GraphQL errors become StashDBError with the server's own
    message (the key scrubbed), a missing key StashDBAuthError."""
    key = _require_api_key(key)
    endpoint = endpoint or stash_endpoint()
    request = build_request(
        endpoint, headers={"ApiKey": key, "Accept": "application/json", "User-Agent": USER_AGENT},
        json_body={"query": query, "variables": variables or {}},
    )
    host = urllib.parse.urlsplit(endpoint).netloc
    body = fetch_json(
        request, _fetch, StashDBError, SERVICE,
        status_messages={
            401: f"{host} rejected the API key (HTTP 401 Unauthorized). Check it under Tools > API Keys.",
            403: f"{host} refused the request (HTTP 403 Forbidden): the account may lack read access.",
            429: f"{host} rate limit was hit (HTTP 429 Too Many Requests): wait a minute and try again.",
        },
        status_errors={401: StashDBAuthError, 429: StashDBRateLimited},
        retry_429=RETRY_429, sleep=_sleep, secrets=(key,),
        unreachable_message=f"Could not reach {host}: {{detail}} (check your internet connection and the StashDB address).",
    )
    if not isinstance(body, dict):
        raise StashDBError(f"Received an unexpected answer from {host}.")
    data = body.get("data")
    errors = body.get("errors")
    if errors and (not isinstance(data, dict) or all(value is None for value in data.values())):
        message = _first_error(errors, key) or "the server reported an error"
        lowered = message.lower()
        if "not authorized" in lowered or "unauthorized" in lowered or "unauthenticated" in lowered:
            raise StashDBAuthError(
                f"{host} did not authorize the request ({message}): the API key is missing, not accepted, or the "
                "account has no read access."
            )
        if "cannot query field" in lowered or "unknown argument" in lowered or "unknown field" in lowered:
            raise StashSchemaError(f"{host} does not understand the query ({message}).")
        raise StashDBError(f"{host} reported an error: {message}")
    return data if isinstance(data, dict) else {}


def _run_with_fallbacks(builders: list[tuple[str, dict]]) -> dict:
    """The first (query, variables) pair the server understands: an older stash-box lacks release_date / searchScenes."""
    last: Optional[StashSchemaError] = None
    for query, variables in builders:
        try:
            return _post(query, variables)
        except StashSchemaError as exc:
            last = exc
    raise last if last is not None else StashDBError("No query to run.")


# --- parsing ------------------------------------------------------------------------------------------------------


def _credited(entry: Any) -> str:
    """A performer as credited on the scene: the "as" credit when there is one, else the performer's name."""
    if not isinstance(entry, dict):
        return ""
    alias = clean_text(entry.get("as"), 200, single_line=True)
    performer = entry.get("performer") if isinstance(entry.get("performer"), dict) else {}
    return alias or clean_text(performer.get("name"), 200, single_line=True)


def _pick_image(images: Any) -> str:
    """The cover: the first image the record lists (the service puts the primary one first)."""
    for image in images if isinstance(images, list) else []:
        url = clean_url(image.get("url")) if isinstance(image, dict) else ""
        if url:
            return url
    return ""


def parse_scene(scene: Any, match_kind: str = MATCH_TITLE, endpoint: str = "") -> Optional[SceneResult]:
    """A Scene (dict) as a SceneResult; None for anything that is not a record with an id or a title."""
    if not isinstance(scene, dict):
        return None
    ident = clean_text(scene.get("id"), 80, single_line=True)
    title = clean_text(scene.get("title"), 500, single_line=True)
    if not ident and not title:
        return None
    studio = scene.get("studio") if isinstance(scene.get("studio"), dict) else {}
    parent = studio.get("parent") if isinstance(studio.get("parent"), dict) else {}
    tags = scene.get("tags")
    urls = scene.get("urls")
    duration = scene.get("duration")
    base = page_base(endpoint or stash_endpoint())
    return SceneResult(
        source=SERVICE,
        external_id=ident,
        title=title,
        date=clean_date(scene.get("release_date") or scene.get("date")),
        studio=clean_text(studio.get("name"), 200, single_line=True),
        parent_studio=clean_text(parent.get("name"), 200, single_line=True),
        performers=clean_names([_credited(p) for p in scene.get("performers")] if isinstance(scene.get("performers"), list) else []),
        tags=clean_names([t.get("name") if isinstance(t, dict) else "" for t in tags] if isinstance(tags, list) else []),
        description=clean_text(scene.get("details")),
        director=clean_text(scene.get("director"), 300, single_line=True),
        urls=[u for u in (clean_url(x.get("url")) if isinstance(x, dict) else "" for x in (urls if isinstance(urls, list) else [])) if u],
        page_url=f"{base}/scenes/{ident}" if ident else "",
        cover_url=_pick_image(scene.get("images")),
        code=clean_text(scene.get("code"), 100, single_line=True),
        duration=int(duration) if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0 else None,
        match_kind=match_kind,
    )


def _dedupe(results: list[SceneResult]) -> list[SceneResult]:
    seen: set[str] = set()
    unique = []
    for result in results:
        key = result.external_id or result.title
        if key not in seen:
            seen.add(key)
            unique.append(result)
    return unique


# --- the lookups ----------------------------------------------------------------------------------------------------


def search_by_hash(oshash: str, md5: str = "") -> list[SceneResult]:
    """The scenes whose fingerprints include this OSHASH (or MD5) ("file match"). Empty for an invalid hash or no match."""
    fingerprints = []
    if valid_oshash(oshash):
        fingerprints.append({"hash": oshash.lower(), "algorithm": "OSHASH"})
    if md5 and len(md5) == 32 and all(c in "0123456789abcdefABCDEF" for c in md5):
        fingerprints.append({"hash": md5.lower(), "algorithm": "MD5"})
    if not fingerprints:
        return []
    endpoint = stash_endpoint()

    def build(date_field: str) -> tuple[str, dict]:
        return (
            "query ($fp: [[FingerprintQueryInput!]!]!) { findScenesBySceneFingerprints(fingerprints: $fp) { "
            + _scene_fields(date_field) + " } }",
            {"fp": [fingerprints]},  # one file, all its fingerprints
        )

    data = _run_with_fallbacks([build("release_date"), build("date")])
    groups = data.get("findScenesBySceneFingerprints")
    found: list[SceneResult] = []
    for group in groups if isinstance(groups, list) else []:
        for scene in group if isinstance(group, list) else []:
            result = parse_scene(scene, MATCH_FILE, endpoint)
            if result is not None:
                found.append(result)
    return _dedupe(found)


def search_by_title(text: str, year: Optional[str] = None, limit: int = RESULT_LIMIT) -> list[SceneResult]:
    """The scenes matching `text` ("title match"). The search has no year filter: results of that year come first."""
    term = " ".join((text or "").split())[:200]
    if not term:
        return []
    endpoint = stash_endpoint()

    def new(date_field: str) -> tuple[str, dict]:
        return (
            "query ($term: String!, $limit: Int) { searchScenes(term: $term, per_page: $limit) { scenes { "
            + _scene_fields(date_field) + " } } }",
            {"term": term, "limit": limit},
        )

    def old(date_field: str) -> tuple[str, dict]:
        return (
            "query ($term: String!, $limit: Int) { searchScene(term: $term, limit: $limit) { "
            + _scene_fields(date_field) + " } }",
            {"term": term, "limit": limit},
        )

    data = _run_with_fallbacks([new("release_date"), new("date"), old("release_date"), old("date")])
    if isinstance(data.get("searchScenes"), dict):
        scenes = data["searchScenes"].get("scenes")
    else:
        scenes = data.get("searchScene")
    results = [r for r in (parse_scene(s, MATCH_TITLE, endpoint) for s in (scenes if isinstance(scenes, list) else [])) if r]
    results = _dedupe(results)
    wanted = (year or "").strip()
    if wanted:
        results.sort(key=lambda r: r.year != wanted)  # stable: the service's order inside each group
    return results


def get(scene_id: str) -> SceneResult:
    """One scene by its id; StashDBError when there is none."""
    ident = clean_text(scene_id, 80, single_line=True)
    if not ident:
        raise StashDBError("That is not a StashDB scene id.")
    endpoint = stash_endpoint()

    def build(date_field: str) -> tuple[str, dict]:
        return "query ($id: ID!) { findScene(id: $id) { " + _scene_fields(date_field) + " } }", {"id": ident}

    data = _run_with_fallbacks([build("release_date"), build("date")])
    result = parse_scene(data.get("findScene"), MATCH_TITLE, endpoint)
    if result is None:
        raise StashDBError(f"StashDB has no scene {ident}.")
    return result


def check_key(key: Optional[str] = None, endpoint: Optional[str] = None) -> str:
    """One cheap authenticated request (me { name }), with `key` / `endpoint` as typed (not saved yet) or the stored
    ones. Returns the OK line; raises StashDBError with the exact reason."""
    endpoint = endpoint or stash_endpoint()
    host = urllib.parse.urlsplit(endpoint).netloc
    data = _post("{ me { name } }", key=key, endpoint=endpoint)
    me = data.get("me")
    if not isinstance(me, dict):
        raise StashDBAuthError(f"{host} did not recognise the key.")
    name = clean_text(me.get("name"), 100, single_line=True)
    return f"OK: {host} accepted the key" + (f" (account: {name})" if name else "") + "."


def download_cover(url: str) -> bytes:
    """The image bytes at a cover URL taken from a result. Called only when the user applied the result and asked for
    the cover. The key is sent only when the image is on the endpoint's own host."""
    clean = clean_url(url)
    if not clean:
        raise StashDBError("The result has no usable cover address.")
    request: Any = clean
    if urllib.parse.urlsplit(clean).netloc.lower() == urllib.parse.urlsplit(stash_endpoint()).netloc.lower():
        key = get_api_key()
        if key:
            request = build_request(clean, headers={"ApiKey": key, "User-Agent": USER_AGENT})
    secrets = tuple(k for k in (get_api_key(),) if k)
    return fetch_bytes(
        request, _cover_fetch, StashDBError, what="the cover", retry_429=RETRY_429, sleep=_sleep, secrets=secrets,
        unreachable_message="Could not download the cover: {detail}.",
    )
