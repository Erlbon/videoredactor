"""
core/embed_posters.py

Media > Embed Existing Poster Files...: older versions saved a poster as `<name>-poster.jpg` next to the video. Posters
now live INSIDE the video (MP4 cover atom, MKV attachment cover.jpg). This module finds those files, judges them by the
same true-poster rules as a lookup (core.poster.judge_poster), embeds the ones the user ticked and, only after the
embedded poster was read back from the video and matched, sends the poster file to the Recycle Bin (never a permanent
delete). Qt-free apart from core.poster's QImage use in prepare_poster; the dialog is gui/embed_posters_dialog.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

from redactor_common.core.trash import TrashError, move_to_trash

from core.poster import POSTER_KIND, PosterRejected, PosterVerdict, image_size, judge_poster, prepare_poster
from core.sidecars import poster_sidecars

MAX_FILE_BYTES = 30 * 1024 * 1024


@dataclass
class PosterOffer:
    video: object                       # the loaded VideoFile
    poster_path: str
    size: Optional[tuple[int, int]]
    verdict: PosterVerdict
    existing_cover: str = ""            # what the video already carries ("" = none): replacing it is the user's tick
    others: int = 0                     # further poster files of the same video, not touched

    @property
    def default_ticked(self) -> bool:
        """A true poster and nothing to replace; a landscape one or one that would replace a cover starts unticked."""
        return self.verdict.ok and not self.existing_cover

    @property
    def shape_text(self) -> str:
        return f"{self.size[0]}x{self.size[1]}" if self.size else "unreadable"


def judge_file(path: str) -> tuple[Optional[tuple[int, int]], PosterVerdict]:
    """(size, verdict) of a poster file by its header (no full decode). A file that is not a picture is refused."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(MAX_FILE_BYTES)
    except OSError:
        return None, PosterVerdict(False, "the file could not be read")
    size = image_size(head)
    if size is None:
        return None, PosterVerdict(False, "not a picture this app can read")
    return size, judge_poster(size[0], size[1], POSTER_KIND)


def find_offers(videos: list) -> list[PosterOffer]:
    """One offer per video that has a `<stem>-poster.(jpg|jpeg|png)` beside it: the first one that is a true poster
    (else the first), jpg before png."""
    offers = []
    for video in videos:
        if getattr(video, "load_error", ""):
            continue
        paths = poster_sidecars(str(video.path))
        if not paths:
            continue
        judged = [(path, *judge_file(path)) for path in paths]
        best = next((j for j in judged if j[2].ok), judged[0])
        offers.append(PosterOffer(video, best[0], best[1], best[2], getattr(video.metadata, "cover_info", "") or "",
                                  others=len(judged) - 1))
    return offers


@dataclass
class EmbedOutcome:
    offer: PosterOffer
    embedded: bool
    trashed: bool
    message: str


def embed_offer(offer: PosterOffer, trash: Optional[Callable[[str], None]] = None) -> EmbedOutcome:
    """Embeds the offer's poster file in its video, reads it back, and only then moves the file to the Recycle Bin
    (`trash`, default redactor_common's move_to_trash; tests pass a fake bin). A poster that is not a true one is
    embedded only because the user ticked it (the caller passes only ticked offers)."""
    trash = trash or move_to_trash
    name = os.path.basename(offer.poster_path)
    try:
        if os.path.getsize(offer.poster_path) > MAX_FILE_BYTES:
            return EmbedOutcome(offer, False, False, f"{name}: the file is far too large")
        with open(offer.poster_path, "rb") as handle:
            data = handle.read()
        prepared = prepare_poster(data, POSTER_KIND, accept_landscape=not offer.verdict.ok)
    except PosterRejected as exc:
        return EmbedOutcome(offer, False, False, f"{name}: not embedded ({exc})")
    except OSError as exc:
        return EmbedOutcome(offer, False, False, f"{name}: could not be read ({exc})")
    problem = offer.video.embed_cover_now(prepared.jpeg)  # writes only the cover, reads it back and compares
    if problem:
        return EmbedOutcome(offer, False, False, f"{name}: not embedded, the poster file is kept ({problem})")
    try:
        trash(offer.poster_path)
    except (TrashError, OSError) as exc:
        return EmbedOutcome(offer, True, False, f"{name}: embedded, but the file could not go to the Recycle Bin ({exc})")
    return EmbedOutcome(offer, True, True, f"{name}: embedded, the file is in the Recycle Bin")
