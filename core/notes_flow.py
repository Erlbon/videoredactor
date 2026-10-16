"""
core/notes_flow.py

The engine behind Tools > Create Notes: Markdown folder notes (series.md in a series folder, about.md in a film folder: the
contract is Pluteus's docs/notes-format.md) drafted from what the folder holds and what TMDB and Wikipedia say. Qt-free;
the command line (videocli/cmd_notes.py) and the review dialog are thin layers over it.

  scan(paths, include_subfolders, kinds)       core.notes_local: the folders that should get a note
  build_proposals(targets, engine, ...)        one Proposal per target (network); never writes
  write_proposal(proposal, dry_run=...)        redactor_common's write_note: never overwrites, status: draft
  NotesEngine.rebuild(proposal, candidate)     the proposal built again from the candidate a person picked

Judgement: a proposal is 'confident' (the only kind a batch run writes) when TMDB has exactly ONE title with the folder's
name (and year, when the folder has one) and, for a series, the seasons and episode numbers in the folder fit that title.
'uncertain' keeps the candidate list for a person to choose; 'none' means nothing was found. The Wikipedia introduction is
used only when the article is the one Wikidata links to the title's IMDb id: never a guess from a name.

Every network function takes an injectable `fetch`, so the tests run on recorded replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core import history
from core.notes_local import (
    ALL_KINDS, KIND_MOVIE, KIND_SERIES, LocalMovie, LocalSeries, ScanResult, Skipped, Target, read_movie, read_series,
    scan, strip_country_tag,
)
from core.notes_render import (
    TMDB_CREDIT, render_local_note, render_movie_note, render_series_note,
)
from core.notes_tmdb import Tmdb, TitleDetails, TitleHit, make_fetch as make_tmdb_fetch
from core.tmdb_client import TMDBError
from core.version import APP_VERSION
from redactor_common.core import note_text
from redactor_common.core import wikipedia as wp
from redactor_common.core.notes import NoteWriteResult
from redactor_common.core.notes_cache import CachedFetch, RequestStats, ResponseCache
from redactor_common.core.notes_model import KIND_NOTE, STATUS_SKIPPED, Candidate, Confidence, NotesSummary  # noqa: F401
from redactor_common.core.notes_model import Proposal as _Proposal
from redactor_common.core.notes_model import build_each, text_sha
from redactor_common.core.notes_model import summarize as _summarize
from redactor_common.core.notes_model import undo_note_entry as _undo_note_entry
from redactor_common.core.notes_model import write_proposal as _write_proposal
from redactor_common.core.notes_phases import PhaseSet, PhaseTimer

__all__ = [
    "ALL_KINDS", "Candidate", "Confidence", "Fetchers", "KIND_MOVIE", "KIND_SERIES", "NotesEngine", "NotesSummary", "Proposal",
    "ScanResult", "Skipped", "Target", "build_proposals", "live_fetchers", "scan", "summarize", "undo_note_entry",
    "write_proposal", "STATUS_SKIPPED", "PHASES",
]

LEVEL_LOCAL_NAME = "local-name"  # rebuild(level=...): the explicit minimal note from the folder name (never automatic)
MAX_CANDIDATES = 4
YEAR_SLACK = 1  # a film's release year may differ by one between countries: plausible, never confident
WIKI_USER_AGENT = f"videoredactor/{APP_VERSION} ( https://github.com/Erlbon )"

TMDB, WIKIPEDIA, SCAN, WRITE = "tmdb", "wikipedia", "scan", "write"
PHASES = PhaseSet({SCAN: "scan", TMDB: "TMDB", WIKIPEDIA: "Wikipedia", WRITE: "writing"},
                  {SCAN: "folders", TMDB: "requests", WIKIPEDIA: "requests", WRITE: "notes"})

Progress = Callable[[int, int, str], None]
_FATAL = re.compile(r"^(No TMDB API key|TMDB rejected)")


@dataclass
class Fetchers:
    """The network, injectable. `tmdb` and `wiki` are `fetch(request) -> bytes` callables (None: the real one); `wikipedia`
    False leaves Wikipedia out of the run."""

    tmdb: Optional[Callable] = None
    wiki: Optional[Callable] = None
    wikipedia: bool = True
    key: Optional[str] = None  # the TMDB key (None: the user's own, from the API Keys store)


def live_fetchers() -> Fetchers:
    return Fetchers()


@dataclass
class Proposal(_Proposal):
    """The shared draft of one note (redactor_common.core.notes_model) plus what only a video folder has."""

    local: Optional[LocalSeries | LocalMovie] = None
    tmdb_id: int = 0  # the title the draft is built from (0: none)

    def to_dict(self, with_text: bool = True) -> dict:
        out = super().to_dict(with_text)
        if self.tmdb_id:
            out["tmdb_id"] = self.tmdb_id
        return out


def summarize(proposals: list[Proposal], results: Optional[dict[str, NoteWriteResult]] = None,
              cancelled: bool = False) -> NotesSummary:
    return _summarize(proposals, results, cancelled)


# ---- judging a match -------------------------------------------------------------------------------------------------

def _fold(text: str) -> str:
    folded = wp.fold(text)
    return folded[4:] if folded.startswith("the ") else folded  # 'The Wire' and 'Wire' are the same search


def _names_match(title: str, hit: TitleHit) -> bool:
    wanted = _fold(title)
    return bool(wanted) and any(_fold(n) == wanted for n in hit.names)


def _year_gap(year: str, hit: TitleHit) -> Optional[int]:
    if not year.isdigit() or not hit.year.isdigit():
        return None
    return abs(int(year) - int(hit.year))


def structure(local: LocalSeries, details: TitleDetails) -> tuple[list[str], list[str]]:
    """(matched, mismatched): do the seasons and episode numbers found in the folder fit this TMDB series? Every local
    season must exist at TMDB and no local episode number may be past the season's episode count (a few more is allowed:
    TMDB can lag behind a running series). `matched` say what fits ('season 1: 7 of 7 episodes'), `mismatched` what does not;
    the folder fits when there is nothing mismatched."""
    known = {s.number: s.episodes for s in details.seasons}
    matched: list[str] = []
    mismatched: list[str] = []
    for season, episodes in sorted(local.episodes.items()):
        count = known.get(season)
        if count is None:
            mismatched.append(f"season {season} is not at TMDB")
            continue
        top = max(episodes) if episodes else 0
        if top > count + 3:
            mismatched.append(f"season {season} has an episode {top} but TMDB has {count}")
        else:
            matched.append(f"season {season}: {len(local.seasons.get(season, []))} of {count} episodes")
    return matched, mismatched


DATE_FIT_SHARE = 0.8  # this share of the dated episodes in the folder must have exactly that air date at TMDB
MIN_DATED_EPISODES = 2  # fewer dates than this say nothing


@dataclass
class Evidence:
    """How well one TMDB series fits the folder beyond its name: the air dates written in the file names against TMDB's, and
    whether each season holds exactly as many episodes as TMDB says (point 1 and 2 of telling two series of one name apart)."""

    counts_exact: int = 0  # local seasons whose number of episodes is TMDB's number
    counts_total: int = 0
    dates_match: int = 0  # dated local episodes whose air date is TMDB's
    dates_total: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def date_fit(self) -> bool:
        return self.dates_total >= MIN_DATED_EPISODES and self.dates_match / self.dates_total >= DATE_FIT_SHARE

    @property
    def count_fit(self) -> bool:
        return self.counts_total > 0 and self.counts_exact == self.counts_total


def evidence(local: LocalSeries, details: TitleDetails, season_dates: Callable[[int], dict[int, str]]) -> Evidence:
    """The evidence of `details` against the folder. `season_dates(season)` gives TMDB's {episode: air date} of a season (only
    asked for the seasons whose file names carry dates)."""
    known = {s.number: s.episodes for s in details.seasons}
    out = Evidence()
    for season, files in sorted(local.seasons.items()):
        count = known.get(season)
        if count is None:
            continue
        out.counts_total += 1
        if len(files) == count:
            out.counts_exact += 1
            out.notes.append(f"season {season}: exactly {count} episodes")
        dated = local.dates.get(season, {})
        if dated:
            tmdb = season_dates(season)
            matched = sum(1 for episode, date in dated.items() if tmdb.get(episode) == date)
            out.dates_total += len(dated)
            out.dates_match += matched
            out.notes.append(f"season {season}: {matched} of {len(dated)} air dates match")
    return out


# ---- the engine ------------------------------------------------------------------------------------------------------

class NotesEngine:
    """Builds proposals. One engine per run: it caches what it fetched, so rebuilding after a person's pick does not ask
    again, and keeps the request statistics and phase times of the run."""

    def __init__(self, fetchers: Optional[Fetchers] = None, cache: Optional[ResponseCache] = None):
        self.fetchers = fetchers or Fetchers()
        self.stats = RequestStats()
        self.phases = PhaseTimer(PHASES)
        self._tmdb_fetch = CachedFetch(self.fetchers.tmdb or make_tmdb_fetch(), "tmdb", cache, self.stats, self.phases, TMDB,
                                       secret_params=("api_key",))
        self._wiki = CachedFetch(self.fetchers.wiki or _default_wiki_fetch(), "wiki", cache, self.stats, self.phases, WIKIPEDIA)
        self.tmdb = Tmdb(self._tmdb_fetch, self.fetchers.key)
        self._details: dict[tuple[str, int], Optional[TitleDetails]] = {}
        self._season_dates: dict[tuple[int, int], dict[int, str]] = {}
        self._evidence: dict[int, Evidence] = {}
        self._fatal = ""  # a problem every later folder would hit too (no key, a rejected key): said once

    def close(self) -> None:
        """Nothing to release today (the cache belongs to the caller); kept so callers need not care."""

    # -- building ----------------------------------------------------------------------------------------------

    def build_proposal(self, target: Target) -> Proposal:
        return self._build(target)

    def rebuild(self, proposal: Proposal, candidate: Optional[Candidate] = None, level: str = "") -> Proposal:
        """The proposal again: from the candidate a person picked (`candidate`), or the explicit minimal note from the
        folder name (`level` = LEVEL_LOCAL_NAME). With neither, a fresh lookup."""
        target = proposal.target
        if level == LEVEL_LOCAL_NAME:
            return self.local_note(proposal)
        built = self._build(target, force=candidate)  # type: ignore[arg-type]
        built.old_state = getattr(target, "update", None)
        return built

    def can_choose(self, proposal: Proposal, candidate: Candidate) -> bool:
        return bool(candidate.id)

    def best_guess(self, proposal: Proposal) -> Optional[Candidate]:
        """The candidate worth writing a draft from when the match is uncertain (--include-uncertain): the top one, only
        when its name AND year fit (score 0.8) and nothing else scores as high. None otherwise."""
        ranked = sorted(proposal.candidates, key=lambda c: -c.score)
        if not ranked or ranked[0].score < 0.8:
            return None
        return ranked[0] if len(ranked) == 1 or ranked[1].score < ranked[0].score else None

    def local_note(self, proposal: Proposal) -> Proposal:
        """The explicit minimal note from the folder name (nothing looked up; a person asked for it on the review page)."""
        target = proposal.target
        local = proposal.local or (read_series(target.folder) if target.kind == KIND_SERIES else read_movie(target.folder))  # type: ignore[attr-defined]
        title = local.name if isinstance(local, LocalSeries) else local.title
        out = Proposal(target, Confidence.CONFIDENT, render_local_note(target.kind, title, local.year), title,  # type: ignore[attr-defined]
                       ["a minimal note from the folder name, as you asked"], ["the folder name"], [], [], local=local)
        out.old_state = getattr(target, "update", None)
        return out

    def _timed_details(self, kind: str, tmdb_id: int) -> Optional[TitleDetails]:
        key = (kind, tmdb_id)
        if key not in self._details:
            self._details[key] = self.tmdb.details(kind, tmdb_id)
        return self._details[key]

    def _dates_of(self, tmdb_id: int, season: int) -> dict[int, str]:
        key = (tmdb_id, season)
        if key not in self._season_dates:
            self._season_dates[key] = self.tmdb.season_air_dates(tmdb_id, season)
        return self._season_dates[key]

    def _evidence_of(self, local: LocalSeries, details: TitleDetails) -> Evidence:
        """The air-date and episode-count evidence of one TMDB series against the folder (asked once per series)."""
        if details.id not in self._evidence:
            self._evidence[details.id] = evidence(local, details, lambda season: self._dates_of(details.id, season))
        return self._evidence[details.id]

    def _tell_apart(self, proposal: Proposal, local: LocalSeries, pool: list[TitleHit]) -> Optional[TitleDetails]:
        """Several series have the folder's name: the one whose episodes' AIR DATES (written in the file names) are TMDB's
        wins; failing that, the one whose seasons hold EXACTLY as many episodes as the folder. Only when it is the only one
        that fits and every other candidate was looked at too (a candidate that could not be checked might fit as well)."""
        checked: list[tuple[TitleHit, TitleDetails, Evidence]] = []
        for hit in pool:
            details = self._timed_details("tv", hit.id)
            if details is None:
                return None
            matched, mismatched = structure(local, details)
            if mismatched:
                continue  # its seasons are not the folder's: it is out of the running
            checked.append((hit, details, self._evidence_of(local, details)))
        for fits, how in ((lambda e: e.date_fit, "the air dates in the file names match"),
                          (lambda e: e.count_fit, "every season has exactly the folder's number of episodes")):
            winners = [(hit, details, ev) for hit, details, ev in checked if fits(ev)]
            if len(winners) == 1:
                hit, details, ev = winners[0]
                proposal.reasons.append(f"{len(pool)} titles are called '{details.name}', but only the one from {details.year or hit.year} "
                                        f"fits the folder: {how} (" + "; ".join(ev.notes) + ")")
                return details
        return None

    def _build(self, target: Target, force: Optional[Candidate] = None) -> Proposal:
        series = target.kind == KIND_SERIES
        local: LocalSeries | LocalMovie = read_series(target.folder) if series else read_movie(target.folder)
        proposal = Proposal(target, local=local, old_state=target.update)
        proposal.title = local.name if isinstance(local, LocalSeries) else local.title
        if self._fatal:
            proposal.problems.append(self._fatal)
            return proposal
        kind = "tv" if series else "movie"
        try:
            if force is not None:
                return self._from_candidate(proposal, kind, force)
            return self._from_search(proposal, kind)
        except TMDBError as exc:
            message = str(exc)
            if _FATAL.match(message):
                self._fatal = message
            proposal.problems.append(message)
            return proposal

    def _from_search(self, proposal: Proposal, kind: str) -> Proposal:
        local = proposal.local
        assert local is not None
        title = strip_country_tag(local.name if isinstance(local, LocalSeries) else local.title)
        year = local.year
        hits = self.tmdb.search(kind, title, year)
        if not hits and year:  # the folder's year may be wrong, or TMDB's date different: look again without it
            hits = self.tmdb.search(kind, title)
            if hits:
                proposal.reasons.append(f"nothing at TMDB for {title} ({year}); searched without the year")
        if not hits:
            proposal.reasons.append(f"TMDB has no {'series' if kind == 'tv' else 'film'} called '{title}'")
            return proposal
        exact = [h for h in hits if _names_match(title, h)]
        strong = [h for h in exact if (_year_gap(year, h) in (None, 0))]
        candidates = self._candidates(proposal, kind, hits, title, year, with_evidence=len(strong or exact) > 1)
        proposal.candidates = candidates
        if len(strong) == 1:
            hit = strong[0]
            details = self._timed_details(kind, hit.id)
            if details is None:
                proposal.reasons.append("TMDB did not answer for the title it found")
                return proposal
            if isinstance(local, LocalSeries):
                matched, mismatched = structure(local, details)
                if mismatched:
                    proposal.reasons.append(f"'{details.name}' ({details.year}) is the only title with that name, but the folder "
                                            f"does not fit it: " + "; ".join(mismatched))
                    proposal.confidence = Confidence.UNCERTAIN
                    return proposal
                proposal.reasons.append(f"the only TMDB series called '{details.name}'" + (f" from {year}" if year else "")
                                        + "; " + "; ".join(matched))
            else:
                proposal.reasons.append(f"the only TMDB film called '{details.name}'" + (f" from {year}" if year else ""))
            return self._finish(proposal, details, Confidence.CONFIDENT)
        if isinstance(local, LocalSeries) and len(strong or exact) > 1:
            winner = self._tell_apart(proposal, local, strong or exact)
            if winner is not None:
                return self._finish(proposal, winner, Confidence.CONFIDENT)
        if len(exact) > 1 or len(strong) > 1:
            proposal.reasons.append(f"{len(strong) or len(exact)} titles are called '{title}'"
                                    + ("" if year else " and the folder has no year") + ": pick the right one")
        else:
            near = [h for h in exact if _year_gap(year, h) is not None and _year_gap(year, h) <= YEAR_SLACK]
            proposal.reasons.append(
                f"'{near[0].name}' is a year off ({near[0].year} against {year}): check it" if near
                else f"no TMDB title is exactly '{title}'" + (f" from {year}" if year else "") + ": the closest are listed")
        proposal.confidence = Confidence.UNCERTAIN
        return proposal

    def _candidates(self, proposal: Proposal, kind: str, hits: list[TitleHit], title: str, year: str,
                    with_evidence: bool = False) -> list[Candidate]:
        local = proposal.local
        out: list[Candidate] = []
        for hit in hits[:MAX_CANDIDATES]:
            score = (0.5 if _names_match(title, hit) else 0.0) + (0.3 if _year_gap(year, hit) == 0 else 0.0)
            found: list[str] = []
            if isinstance(local, LocalSeries) and score >= 0.5:
                details = self._timed_details(kind, hit.id)
                if details is not None:
                    found, mismatched = structure(local, details)
                    score += 0.2 if not mismatched else 0.0
                    if not mismatched and with_evidence:  # air dates cost a request per season: only when titles compete
                        ev = self._evidence_of(local, details)
                        score += (0.3 if ev.date_fit else 0.0) + (0.1 if ev.count_fit else 0.0)
                        found = [n for n in ev.notes if "air dates" in n] + found
            blurb = hit.overview[:90] + ("..." if len(hit.overview) > 90 else "")
            out.append(Candidate("tmdb", str(hit.id), hit.name, ", ".join(x for x in (hit.year, blurb) if x), found, score))
        return sorted(out, key=lambda c: -c.score)

    def _from_candidate(self, proposal: Proposal, kind: str, candidate: Candidate) -> Proposal:
        details = self._timed_details(kind, int(candidate.id))
        if details is None:
            proposal.problems.append(f"TMDB has no title {candidate.id} any more")
            return proposal
        proposal.reasons.append("chosen by you from the candidates")
        return self._finish(proposal, details, Confidence.CONFIDENT)

    def _finish(self, proposal: Proposal, details: TitleDetails, confidence: Confidence) -> Proposal:
        proposal.confidence, proposal.tmdb_id, proposal.title = confidence, details.id, details.name
        proposal.sources = ["TMDB"]
        links = [details.url]
        footer = [TMDB_CREDIT]
        intro = ""
        page = self._wikipedia(proposal, details)
        if page is not None:
            intro = wp.trim_extract(page.extract)
            links.append(page.url or f"https://en.wikipedia.org/wiki/{page.title.replace(' ', '_')}")
            footer.append(note_text.wikipedia_credit(page.title))
            proposal.sources.append("Wikipedia")
        if details.imdb_id:
            links.append(f"https://www.imdb.com/title/{details.imdb_id}/")
        render = render_series_note if details.kind == "tv" else render_movie_note
        proposal.note_text = render(details, intro=intro, links=links, footer=footer)
        return proposal

    def _wikipedia(self, proposal: Proposal, details: TitleDetails) -> Optional[wp.Page]:
        """The article Wikidata links to the title's IMDb id, or None (and why, in the reasons)."""
        if not self.fetchers.wikipedia:
            proposal.reasons.append("Wikipedia is switched off for this run")
            return None
        if not details.imdb_id:
            proposal.reasons.append("TMDB has no IMDb id for this title, so no Wikipedia article can be verified")
            return None
        try:
            qid = wp.qid_by_statement("P345", details.imdb_id, self._wiki)
            title = wp.title_from_wikidata(qid, self._wiki) if qid else ""
            page = wp.usable(wp.get_summary(title, self._wiki)) if title else None
        except wp.WikipediaError as exc:
            proposal.problems.append(str(exc))
            return None
        if page is None:
            proposal.reasons.append("no Wikipedia article is linked to this title's IMDb id")
            return None
        page.via = "wikidata (IMDb id)"
        proposal.reasons.append(f"Wikipedia: {page.title}, found through the title's IMDb id")
        return page


def _default_wiki_fetch() -> Callable:
    return _WIKI_FETCH


_WIKI_FETCH = wp.make_fetch(WIKI_USER_AGENT)


# ---- building and writing --------------------------------------------------------------------------------------------

def build_proposals(
    targets: list[Target], fetchers: Optional[Fetchers] = None, progress: Optional[Progress] = None,
    cancel: Optional[Callable[[], bool]] = None, on_proposal: Optional[Callable[[Proposal], None]] = None,
    engine: Optional[NotesEngine] = None, cache: Optional[ResponseCache] = None,
) -> list[Proposal]:
    """One Proposal per target, in order (the shared build_each). Network only; this function writes no file."""
    engine = engine or NotesEngine(fetchers, cache)  # a caller that keeps the engine can rebuild with its caches
    return build_each(targets, engine.build_proposal, progress, cancel, on_proposal)  # type: ignore[return-value]


def write_proposal(proposal: Proposal, dry_run: bool = False, allow_uncertain: bool = False) -> NoteWriteResult:
    """Writes the draft of a CONFIDENT proposal (the shared write_proposal: an uncertain / none one is refused with status
    'skipped' unless a person chose it, an existing note is never overwritten). With the opt-in history on, a written note
    is recorded so it can be undone (undo_note_entry)."""
    return _write_proposal(proposal, dry_run, allow_uncertain, record=record_note_created)


def record_note_created(path: str, text: str, replaced: bool = False) -> None:
    """Journal entry for a created note when the opt-in history is on (otherwise nothing is touched). The entry keeps the
    SHA-256 of what was written so an undo can tell whether the person edited it since."""
    if not history.history_enabled():
        return
    try:
        before = "an older note, now in the Recycle Bin" if replaced else ""
        history.get_journal().record(path, KIND_NOTE, {"note": (before, Path(path).name)}, note=f"sha256:{text_sha(text)}")
    except Exception as exc:  # noqa: BLE001 -- the note is already written; a journal failure never undoes it
        history._report(exc)


def undo_note_entry(entry, journal=None) -> str:
    """Undoes a journal entry of a created note (the shared undo: Recycle Bin, only if it still holds exactly what was
    written). Returns '' or a plain-language problem."""
    return _undo_note_entry(entry, journal if journal is not None else history.get_journal())
