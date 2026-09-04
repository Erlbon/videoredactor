"""
OpenSubtitles REST API client (api.opensubtitles.com, the current v1 API).

Design: hash-match search is ALWAYS tried first (per explicit
instruction) since it's the only method that guarantees sync -- it
fingerprints the exact file via core/opensubtitles_hash.py and asks
"does a subtitle exist for exactly this release." Title-based search is
a fallback for when no hash match exists, and every title-search result
carries an explicit "sync not guaranteed" flag so the GUI can warn
plainly rather than let a title match look as trustworthy as a hash match.

Auth: requires an API key (Api-Key header) for all requests. Login
(username/password -> JWT bearer token) is optional and only needed for
a higher download quota -- v1 supports anonymous (API-key-only) downloads
within OpenSubtitles' free daily limit, so login is treated as optional
here, not required to use the feature at all.

NOTE: not yet runnable/testable in this sandbox -- no network access to
a real OpenSubtitles endpoint. Written against OpenSubtitles' documented
REST API (https://opensubtitles.stoplight.io/docs/opensubtitles-api);
needs a real functional pass once a key + network access are available.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
import urllib.request
import urllib.parse
import json
import os

from core.config import get_setting
from core.opensubtitles_hash import compute_moviehash

BASE_URL = "https://api.opensubtitles.com/api/v1"
USER_AGENT = "TheVideoRedactor v0.1"  # OpenSubtitles requires a descriptive User-Agent


class OpenSubtitlesError(Exception):
    """Raised for missing API key, network failure, or a non-2xx
    response. Message is meant to be shown directly to the user."""


@dataclass
class SubtitleCandidate:
    file_id: int          # used for the download step
    language: str          # ISO 639-1 code, e.g. 'en'
    release_name: str      # e.g. "Movie.Name.2020.1080p.BluRay"
    download_count: int
    hash_matched: bool     # True = fingerprint-verified sync; False = title search, sync NOT guaranteed


def get_api_key() -> Optional[str]:
    key = os.environ.get("OPENSUBTITLES_API_KEY")
    if key:
        return key
    key = get_setting("opensubtitles", "api_key")
    return key or None


def _require_api_key() -> str:
    key = get_api_key()
    if not key:
        raise OpenSubtitlesError(
            "No OpenSubtitles API key configured. Set the OPENSUBTITLES_API_KEY "
            "environment variable, or add one under [opensubtitles] api_key in settings.ini."
        )
    return key


def _get_json(path: str, params: dict, bearer_token: Optional[str] = None) -> dict:
    key = _require_api_key()
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    headers = {"Api-Key": key, "User-Agent": USER_AGENT}
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise OpenSubtitlesError("OpenSubtitles rejected the API key (401 Unauthorized).") from e
        if e.code == 429:
            raise OpenSubtitlesError("OpenSubtitles rate/quota limit hit -- try again later.") from e
        raise OpenSubtitlesError(f"OpenSubtitles request failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise OpenSubtitlesError(f"Could not reach OpenSubtitles: {e.reason}") from e


def _post_json(path: str, payload: dict, bearer_token: Optional[str] = None) -> dict:
    key = _require_api_key()
    url = f"{BASE_URL}{path}"
    headers = {
        "Api-Key": key, "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise OpenSubtitlesError("OpenSubtitles rejected the request (401 Unauthorized).") from e
        if e.code == 429:
            raise OpenSubtitlesError("OpenSubtitles rate/quota limit hit -- try again later.") from e
        raise OpenSubtitlesError(f"OpenSubtitles request failed: HTTP {e.code}") from e
    except urllib.error.URLError as e:
        raise OpenSubtitlesError(f"Could not reach OpenSubtitles: {e.reason}") from e


def search_by_hash(video_path: str, language: str = "en") -> list[SubtitleCandidate]:
    """Search using the file's moviehash -- the guaranteed-sync path.

    Returns an empty list (not an error) if the file is too small to
    hash (see core/opensubtitles_hash.MIN_FILE_SIZE) -- the caller is
    expected to fall through to search_by_title() in that case, same as
    a hash search that simply found no server-side match.
    """
    file_hash = compute_moviehash(video_path)
    if file_hash is None:
        return []

    data = _get_json("/subtitles", {"moviehash": file_hash, "languages": language})
    return _parse_results(data, hash_matched=True)


def search_by_title(query: str, language: str = "en") -> list[SubtitleCandidate]:
    """Fallback title-based search. Every result is flagged
    hash_matched=False -- the GUI must show a sync-not-guaranteed
    warning for these, never present them with the same confidence as
    a hash match.
    """
    data = _get_json("/subtitles", {"query": query, "languages": language})
    return _parse_results(data, hash_matched=False)


def _parse_results(data: dict, hash_matched: bool) -> list[SubtitleCandidate]:
    results = []
    for entry in data.get("data", []):
        attrs = entry.get("attributes", {})
        files = attrs.get("files", [])
        if not files:
            continue
        results.append(SubtitleCandidate(
            file_id=files[0].get("file_id"),
            language=attrs.get("language", ""),
            release_name=attrs.get("release", "") or attrs.get("feature_details", {}).get("title", ""),
            download_count=attrs.get("download_count", 0),
            hash_matched=hash_matched,
        ))
    return results


def download_subtitle_text(file_id: int) -> str:
    """Resolve a file_id to an actual download link, then fetch the
    subtitle's text content. Two-step process per OpenSubtitles' API
    (POST /download returns a short-lived link, not the content itself).
    Uses API-key-only (anonymous) auth -- no login required for the
    free-tier daily download quota.
    """
    link_data = _post_json("/download", {"file_id": file_id})
    download_url = link_data.get("link")
    if not download_url:
        raise OpenSubtitlesError("OpenSubtitles did not return a download link.")

    request = urllib.request.Request(download_url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise OpenSubtitlesError(f"Could not download subtitle file: {e.reason}") from e
