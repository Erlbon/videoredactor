"""
core/notes_render.py

Turns what the lookups found (TMDB facts, a Wikipedia introduction) into the text of a series.md or about.md draft. Pure
(no network, no files): the same inputs always give the same note. The writing itself (never overwriting, status: draft,
UTF-8/LF) is redactor_common's core/notes.py; the credit lines and body helpers are its core/note_text.py.

Shape (the contract is Pluteus's docs/notes-format.md; plain Markdown, no [[wiki links]], nothing about what is in the
library: the folders are the structure and Pluteus shows them):

  series.md   front matter (title, years, genres, links, status: draft), a short introduction, fact lines (Network, Created
              by, Status, Seasons, Episodes, Country, Language), "## Cast", "## Seasons", the credit footer
  about.md    (a film) front matter, a short introduction, fact lines (Directed by, Written by, Released, Runtime, Country,
              Language), "## Cast", the credit footer
"""

from __future__ import annotations

from typing import Iterable, Optional

from core.notes_tmdb import TitleDetails
from core.tmdb_client import TMDB_NOTICE, TMDB_URL
from core.version import APP_VERSION
from redactor_common.core.note_text import credit_footer, fact_lines, join_blocks
from redactor_common.core.notes import NoteFrontMatter, render_note

# The shape of the notes this module writes, stamped into every note (with the app version and a hash, see redactor_common
# core/notes.py note_hash) so a later run can tell an old note of ours that nobody edited from one a person edited.
# BUMP it when the shape of a note changes in a way worth refreshing old notes for.
CURRENT_NOTE_FORMAT = 2

TMDB_CREDIT = f"Facts from [TMDB]({TMDB_URL}). {TMDB_NOTICE}"
LOCAL_ONLY_CREDIT = "Built from the folder name only, no online source"


def generator_text() -> str:
    return f"videoredactor {APP_VERSION}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def years_text(details: TitleDetails) -> str:
    """'2008-2013' for an ended series, '2008-' for one still going, '1999' for a film, '' without a date."""
    start = details.year
    if not start.isdigit():
        return ""
    if details.kind == "movie":
        return start
    stop = details.end_date[:4]
    if stop == start:
        return start
    return f"{start}-{stop}" if stop.isdigit() else f"{start}-"


def _minutes(minutes: int) -> str:
    if minutes <= 0:
        return ""
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest:02d} min" if hours else f"{rest} min"


def _list(items: Iterable[str]) -> str:
    return ", ".join(i for i in items if i)


def _cast(details: TitleDetails) -> str:
    lines = [f"- {c.name}" + (f" as {c.character}" if c.character else "") for c in details.cast]
    return "## Cast\n\n" + "\n".join(lines) if lines else ""


def render_series_note(
    details: TitleDetails, *, intro: str = "", links: Iterable[str] = (), footer: Iterable[str] = (),
) -> str:
    """The series.md text. `intro` is the Wikipedia introduction (or '' to use TMDB's overview); `footer` are the source
    sentences (TMDB_CREDIT, a Wikipedia credit)."""
    facts = [
        ("Network", _list(details.networks)), ("Created by", _list(details.creators)), ("Status", details.status),
        ("Seasons", str(len([s for s in details.seasons if s.number > 0])) if details.seasons else ""),
        ("Episodes", str(details.episode_count) if details.episode_count else ""),
        ("Country", _list(details.countries)), ("Original language", details.language),
    ]
    seasons = "\n".join(
        f"- {'Specials' if s.number == 0 else 'Season ' + str(s.number)} ({_plural(s.episodes, 'episode')})"
        for s in details.seasons if s.episodes > 0 and s.number > 0)  # the specials are not a season of the show
    front = NoteFrontMatter(title=details.name, years=years_text(details), genres=[g.lower() for g in details.genres],
                            links=list(links), generator=generator_text(), note_format=CURRENT_NOTE_FORMAT)
    body = join_blocks(
        f"# {details.name}", intro or details.overview, fact_lines(facts), _cast(details),
        ("## Seasons\n\n" + seasons) if seasons else "", credit_footer(list(footer)))
    return render_note(front, body)


def render_movie_note(
    details: TitleDetails, *, intro: str = "", links: Iterable[str] = (), footer: Iterable[str] = (),
) -> str:
    """The about.md text of a film folder."""
    facts = [
        ("Directed by", _list(details.directors)), ("Written by", _list(details.writers)),
        ("Released", details.date), ("Runtime", _minutes(details.runtime)),
        ("Country", _list(details.countries)), ("Original language", details.language),
    ]
    front = NoteFrontMatter(title=details.name, years=years_text(details), genres=[g.lower() for g in details.genres],
                            links=list(links), generator=generator_text(), note_format=CURRENT_NOTE_FORMAT)
    body = join_blocks(
        f"# {details.name}", intro or details.overview, fact_lines(facts), _cast(details), credit_footer(list(footer)))
    return render_note(front, body)


def render_local_note(kind: str, title: str, year: str = "") -> str:
    """The explicit minimal note from the folder name (a person asked for it; nothing was looked up)."""
    front = NoteFrontMatter(title=title, years=year, generator=generator_text(), note_format=CURRENT_NOTE_FORMAT)
    return render_note(front, join_blocks(f"# {title}", credit_footer([LOCAL_ONLY_CREDIT])))
