"""
gui/source_apply.py

The window side of the source ids and the embedded poster: what every lookup does with them once the user has accepted
a result.

  * fetch_prepared_poster(): download (with a progress dialog and Cancel) and prepare a TRUE poster (core.poster) from
    the result's poster candidates; None plus the reason when the service has none ("No poster available from X").
  * SourceStage: the source fields and the prepared poster one file gets.
  * review_changes() / apply_stage(): the stage as rows of the ONE per-field overwrite review (a field the file
    already has with a different value starts unticked) and, once accepted, the staged write. The ids and the poster
    are staged on the metadata like every other field (Save writes them; Undo takes them back); nothing is written to
    a file here, and no sidecar file is ever created.
  * apply_sources(): the whole thing for the flows that have no review of their own (TMDB, TheTVDB, IMDb): a review
    appears only when the file already carries a DIFFERENT value for one of these fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from redactor_common.gui.overwrite_review_dialog import resolve_overwrite_conflicts

from core.poster import PosterRejected, PreparedPoster, no_poster_message, prepare_poster
from core.source_tags import LABELS, SOURCE_FIELDS
from gui.lookup import CANCELLED, run_cancellable_lookup

COVER_ATTR = "cover_info"
COVER_LABEL = "Poster (embedded)"
REVIEW_LABELS = {**LABELS, COVER_ATTR: COVER_LABEL}


def review_label(attr: str, base: Optional[Callable[[str], str]] = None) -> str:
    return REVIEW_LABELS.get(attr) or (base(attr) if base else attr)


@dataclass
class SourceStage:
    """What one file gets from one accepted result."""

    values: dict[str, str] = field(default_factory=dict)   # source field -> tag text
    poster: Optional[PreparedPoster] = None
    note: str = ""                                          # why there is no poster, for the status line

    def rows(self) -> dict[str, str]:
        """The stage as {attribute: text} rows of the review (the poster shows what it would be)."""
        rows = dict(self.values)
        if self.poster is not None:
            rows[COVER_ATTR] = "new poster: " + self.poster.description()
        return rows


def fetch_prepared_poster(window, service: str, download: Callable[[str], bytes], candidates: list[str],
                          accept_landscape: bool = False, label: str = "",
                          kind: str = "poster", errors: tuple = (Exception,)) -> tuple[Optional[PreparedPoster], str]:
    """(poster, "") for the first candidate that is a true poster, else (None, why). At most three candidates are
    tried. Every download runs under the progress dialog with Cancel."""
    why = no_poster_message(service)
    if not candidates:
        return None, why
    reasons: list[str] = []
    for url in candidates[:3]:
        def work(u=url):
            return prepare_poster(download(u), kind, accept_landscape)
        try:
            got = run_cancellable_lookup(window, work, label=label or f"Downloading the poster from {service}...")
        except PosterRejected as exc:
            reasons.append(str(exc))
            continue
        except errors as exc:
            return None, f"{why} ({exc})"
        if got is CANCELLED:
            return None, "poster download cancelled"
        return got, ""
    return None, why + (f" ({reasons[0]})" if reasons else "")


def review_changes(files: list, stages: dict[int, SourceStage]) -> dict[int, dict[str, str]]:
    return {index: stage.rows() for index, stage in stages.items() if stage.rows()}


def apply_stage(vf, stage: SourceStage, accepted: dict[str, str]) -> bool:
    """Stages the accepted rows of `stage` on `vf` (not saved to disk). True when anything changed."""
    changed = False
    for attr in accepted:
        if attr == COVER_ATTR:
            if stage.poster is not None:
                vf.metadata.cover_pending = stage.poster.jpeg
                vf.metadata.cover_remove = False  # a new poster replaces the old one; no removal is needed
                changed = True
        elif attr in SOURCE_FIELDS and attr in stage.values:
            if getattr(vf.metadata, attr, "") != stage.values[attr]:
                setattr(vf.metadata, attr, stage.values[attr])
                changed = True
    if changed:
        vf.dirty = True
    return changed


def apply_sources(window, files: list, stages: dict[int, SourceStage], base_label: Optional[Callable[[str], str]] = None) -> int:
    """Review (only when something differs from what the file has) and stage the sources of several files.
    `stages` is keyed by the file's index in `files`. The caller has pushed the undo entry. Returns how many files
    changed; a cancelled review changes nothing."""
    changes = review_changes(files, stages)
    if not changes:
        return 0
    accepted = resolve_overwrite_conflicts(window, files, changes, lambda attr: review_label(attr, base_label))
    if accepted is None:
        return 0
    return sum(1 for index, rows in accepted.items() if rows and apply_stage(files[index], stages[index], rows))
