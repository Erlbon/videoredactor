"""
core/collected_from.py

"Collected from": where THIS copy of a video came from (a website, a TV broadcast, a disc), on every Content Type.
Not the same as SOURCE_URL, which is the database record a lookup matched. Qt-free.

Owner's decisions (2026-11-18, decision page questions 6-8):
  6A  A pick list for the kind (COLLECTED_FROM_CHOICES) plus a free detail line (the link, the channel and year,
      the disc's name). Two tags: COLLECTED_FROM and COLLECTED_FROM_DETAIL.
  7B  Every Content Type has it.
  8A  When it is empty and the file carries a web address (yt-dlp's "embed metadata" writes the page's address
      into the Comment), the kind and the link are SUGGESTED: filled in on load, flagged, and written only with
      the file's next save, like the Content Type read from an MP4's media type.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

COLLECTED_FROM_CHOICES = (
    "YouTube", "Vimeo", "Other website", "TV broadcast", "DVD", "Blu-ray", "LaserDisc", "VHS",
    "CD-ROM / Enhanced CD", "Unknown",
)

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_SITES = (
    (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube"),
    (("vimeo.com",), "Vimeo"),
)


def kind_for_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for domains, kind in _SITES:
        if any(host == d or host.endswith("." + d) for d in domains):
            return kind
    return "Other website"


def first_url(text: str) -> Optional[str]:
    match = _URL.search(text or "")
    return match.group(0).rstrip(".,;)]") if match else None


def suggest(metadata) -> bool:
    """Fill an EMPTY Collected from from the first web address in the Comment (then the Description), and flag it
    as a suggestion. Returns True when something was filled. Never touches a file that has a value of its own."""
    if (metadata.collected_from or "").strip() or (metadata.collected_from_detail or "").strip():
        return False
    url = first_url(metadata.comment) or first_url(metadata.description)
    if not url:
        return False
    metadata.collected_from = kind_for_url(url)
    metadata.collected_from_detail = url
    metadata.collected_from_suggested = True
    return True


def drop_suggestion(metadata) -> bool:
    """Undo suggest() while the suggestion is still unconfirmed (the user never kept it): the two fields go empty
    again and the flag clears. For writers that save without the user having seen the panel (a conversion, the
    command line). Returns True when something was dropped."""
    if not getattr(metadata, "collected_from_suggested", False):
        return False
    metadata.collected_from = ""
    metadata.collected_from_detail = ""
    metadata.collected_from_suggested = False
    return True
