"""
videocli/cmd_notes.py

  notes PATH...   draft the Markdown folder notes (series.md in a series folder, about.md in a film folder) that Pluteus
                  shows beside the folder, from what the folders hold and what TMDB and Wikipedia say. Needs the TMDB key
                  saved in the app (Tools > API Keys) or TMDB_API_KEY.

A REPORT by default: every title folder is looked up and listed with how sure the match is and what would be written, and
nothing is written. --write writes the drafts (status: draft in the front matter) of the CONFIDENT matches only: TMDB has
exactly one title with the folder's name (and year), and for a series the seasons and episode numbers in the folder fit it.
Uncertain and unmatched folders are listed with the reasons and the candidates, never written: they are for a person to
decide (Tools > Create Notes in the app has the review page). A folder that already has artist.md / author.md / series.md /
album.md / about.md, in any letter case, is never touched; a note is never overwritten. The engine is core/notes_flow.py;
the layout rule is core/notes_local.py.
"""

from __future__ import annotations

import argparse
import logging
import time

from core import notes_settings
from core.notes_flow import (
    Confidence, Fetchers, NotesEngine, NotesSummary, Proposal, build_proposals, live_fetchers, scan, summarize, write_proposal,
)
from core.notes_local import ALL_KINDS, KIND_MOVIE, KIND_SERIES
from redactor_common.cli import EXIT_INTERRUPTED, EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options
from redactor_common.core.notes import STATE_LEGACY
from redactor_common.core.notes_timing import lookup_sentence

KIND_WORDS = ("both", KIND_SERIES, KIND_MOVIE, "all")


def kind_argument(text: str) -> tuple:
    """--kind: one word (series, movie, both) or several joined by commas (series,movie)."""
    words = [w.strip().lower() for w in text.replace(";", ",").split(",") if w.strip()]
    bad = [w for w in words if w not in KIND_WORDS]
    if not words or bad:
        raise argparse.ArgumentTypeError(f"invalid kind {', '.join(bad) or text!r}: use {', '.join(KIND_WORDS)}")
    return notes_settings.parse_kinds(words)


def make_cache(args: argparse.Namespace):
    """The disk cache of the lookups, or None for --no-cache / the setting off. The tests replace this one function."""
    if args.no_cache:
        return None
    return notes_settings.default_cache()


def make_fetchers(use_wikipedia: bool) -> Fetchers:
    """The real services; the tests replace this one function with recorded fixtures."""
    fetchers = live_fetchers()
    fetchers.wikipedia = use_wikipedia
    return fetchers


def add_notes_parser(sub) -> None:
    parser = sub.add_parser(
        "notes", help="draft series.md / about.md folder notes",
        description="Draft the Markdown notes Pluteus shows beside a folder (series.md for a series, about.md for a film) from "
                    "TMDB and Wikipedia, checked against the seasons and episodes really in the folder. A report unless "
                    "--write is given; --write writes only the confident matches (status: draft) and never overwrites an "
                    "existing note.",
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="a library, series or film folder (or video files)")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="look only at the folders named, not the folders below them")
    parser.add_argument("--kind", type=kind_argument, default=ALL_KINDS, metavar="KIND",
                        help="which notes: series (series.md), movie (about.md of a film folder), both (the default)")
    parser.add_argument("--update", action="store_true",
                        help="also list the notes made by an older version of this app (stamped, an older format, nobody "
                             "edited them) and, with --write, replace ONLY those (the old note goes to the Recycle Bin). "
                             "Notes edited by a person or not made by this app are never touched")
    parser.add_argument("--update-legacy", action="store_true",
                        help="with --update --write: ALSO replace the notes of the first versions (no stamp, so it cannot be "
                             "known whether they were edited; still the Recycle Bin, still never an edited or foreign note)")
    parser.add_argument("--no-cache", action="store_true", help="do not use the disk cache of earlier answers (everything is asked again)")
    parser.add_argument("--no-wikipedia", action="store_true", help="no Wikipedia introduction in the notes (fewer requests)")
    parser.add_argument("--write", action="store_true", help="write the drafts of the confident matches (default: report only)")
    parser.add_argument("--include-uncertain", action="store_true",
                        help="with --write: ALSO write the uncertain matches whose top candidate fits name AND year and has no "
                             "equal (drafts, marked 'from an uncertain match' in the report; never over an existing note, "
                             "never for nothing found)")
    parser.add_argument("-n", "--dry-run", action="store_true", help="never write, even with --write: show what would be written")
    parser.add_argument("--show-drafts", action="store_true", help="print each draft note (text mode) / include it (JSON)")
    add_common_options(parser)
    parser.set_defaults(handler=run_notes)


def _row(proposal: Proposal, status: str, message: str, show_draft: bool) -> dict:
    row = proposal.to_dict(with_text=show_draft)
    row.update({"status": status, "message": message, "path": proposal.path})
    return row


def _print(out: Output, row: dict) -> None:
    extra = f"  ({row['message']})" if row["message"] else ""
    out.line(f"{row['status']:<11} {row['kind']:<7} {row['folder']}{extra}")
    if row["status"] in ("review", "no-match"):
        for reason in row["reasons"][:4]:
            out.line(f"             - {reason}")
        for cand in row["candidates"][:3]:
            found = f", {len(cand['hits'])} season(s) fit" if cand["hits"] else ""
            out.line(f"             candidate: {cand['source']} {cand['name']} [{cand['detail']}]{found}")
    for problem in row["problems"][:3]:
        out.line(f"             ! {problem}")
    if row.get("draft"):
        out.line("")
        out.line(row["draft"].rstrip("\n"))
        out.line("")


def run_notes(args: argparse.Namespace, out: Output) -> int:
    update = args.update or args.update_legacy
    scan_started = time.perf_counter()
    found = scan(args.paths, include_subfolders=not args.no_recurse, kinds=args.kind, include_outdated=update)
    scan_seconds = time.perf_counter() - scan_started
    for argument in found.missing:
        out.error(f"nothing found for {argument}")
    if not found.targets and not found.skipped:
        raise CliError("no series or film folders found")
    dry = args.dry_run or not args.write
    for skipped in found.skipped:
        row = {"status": "left-alone" if skipped.state else "has-note", "kind": skipped.kind, "folder": str(skipped.folder),
               "message": skipped.reason, "reasons": [], "candidates": [], "problems": [], "confidence": "", "path": "",
               "sources": []}
        out.record(row)
        _print(out, row)

    results = {}
    proposals: list[Proposal] = []
    guessed = 0  # notes written (or that would be) from an uncertain match
    failed = 0
    cancelled = False
    engine = NotesEngine(make_fetchers(not args.no_wikipedia), cache=make_cache(args))
    engine.phases.add("scan", scan_seconds, count=len(found.targets))
    started = time.perf_counter()

    def handle(proposal: Proposal) -> None:
        nonlocal failed, guessed
        guess = None
        unwritten = ""
        if args.include_uncertain and proposal.confidence == Confidence.UNCERTAIN:
            guess = engine.best_guess(proposal)
            if guess is None:
                unwritten = "no candidate fits name and year on its own"
            else:
                reasons = list(proposal.reasons)
                proposal = engine.rebuild(proposal, candidate=guess)
                proposal.reasons = [f"best guess from an UNCERTAIN match: {guess.name} ({guess.source})", *reasons[:3]]
        proposals.append(proposal)
        if guess is not None and not proposal.writable:
            guess = None  # the rebuild did not give a note: it is reported like any other uncertain one
        legacy_held = proposal.is_update and proposal.old_state.kind == STATE_LEGACY and not args.update_legacy
        if proposal.writable and legacy_held and not dry:
            status = "legacy"
            message = ("made by the first versions (no stamp), so it cannot be known whether it was edited: "
                       "not replaced (add --update-legacy to replace it)")
        elif proposal.writable:
            with engine.phases.phase("write", count=0 if dry or legacy_held else 1):
                result = write_proposal(proposal, dry_run=dry or legacy_held)
            results[proposal.path] = result
            verb = "update" if proposal.is_update else "write"
            if result.ok:
                if dry:
                    status = f"would-{verb}"
                    message = ", ".join(proposal.sources) + (f"  [{proposal.old_state.kind}]" if proposal.is_update else "")
                    if legacy_held:
                        message += "  (not replaced without --update-legacy)"
                else:
                    status, message = ("updated" if proposal.is_update else "written"), proposal.path
                    if proposal.is_update:
                        message += "  (the old note is in the Recycle Bin)"
                if guess is not None:
                    guessed += 1
                    message += f"  [from an uncertain match: {guess.name} ({guess.source}) - review it]"
            elif result.status in ("exists", "changed"):
                status, message = "has-note", result.message
            else:
                status, message = "failed", result.message
                failed += 1
        elif proposal.confidence == Confidence.NONE:
            status = "no-match"
            message = "; ".join(proposal.reasons[:1])
            if proposal.problems:
                failed += 1
        else:
            status, message = "review", "uncertain: not written" + (f" ({unwritten})" if unwritten else "")
        out.record(_row(proposal, status, message, args.show_drafts))
        out.records[-1]["from_uncertain"] = guess is not None
        _print(out, out.records[-1])

    def progress(done: int, total: int, label: str) -> None:
        if label:
            out.progress(done + 1, total, label)

    try:
        build_proposals(found.targets, progress=progress, on_proposal=handle, engine=engine)
    except KeyboardInterrupt:
        cancelled = True
        out.warn("interrupted: the folders not yet looked at were left alone")

    summary: NotesSummary = summarize(proposals, results, cancelled=cancelled)
    summary_dict = summary.to_dict()
    summary_dict.update({"has_note": len(found.skipped), "dry_run": dry, "failed": failed, "from_uncertain": guessed})
    out.line("")
    out.line(
        f"{summary.total} folder(s) looked up: {summary.confident} confident, {summary.uncertain} uncertain, "
        f"{summary.none} not found; {summary.written} written, {summary.would_write} would be written, "
        f"{len(found.skipped)} already had a note.")
    if guessed:
        out.line(f"{guessed} note(s) {'would be ' if dry else 'were '}written from an UNCERTAIN match (best guess, still "
                 "status: draft): look at them before you trust them.")
    if summary.uncertain + summary.none > 0:
        out.line("Uncertain and unmatched folders were not written; review them (the candidates are listed above).")
    for problem in summary.problems[:3]:
        out.line(f"! {problem}")
    if update:
        left = [x for x in found.skipped if x.state in ("edited", "foreign")]
        current = [x for x in found.skipped if x.state == "current"]
        out.line(f"{len(left)} notes were left alone: edited by a person or not ours."
                 + (f" {len(current)} are already current." if current else ""))
        summary_dict.update({"left_alone": len(left), "already_current": len(current), "updates": summary.updates})
    elapsed = time.perf_counter() - started
    sentence = lookup_sentence(len(proposals), elapsed, engine.stats.requests, engine.stats.from_cache)
    out.line(sentence)
    if out.json_mode:
        out.info(sentence)  # stderr: the JSON document on stdout stays clean
    summary_dict.update({"requests": engine.stats.requests, "from_cache": engine.stats.from_cache,
                         "elapsed_seconds": round(elapsed, 2), "phases": engine.phases.to_dict()})
    if engine.phases.time_line():
        out.line(engine.phases.time_line())
        if out.json_mode:
            out.info(engine.phases.time_line())
    logging.getLogger("videoredactor.notes").debug("%s | %s", sentence, engine.phases.detail())
    out.finish(summary_dict)
    if cancelled:
        return EXIT_INTERRUPTED
    return EXIT_PARTIAL if failed else EXIT_OK
