"""
videocli/cmd_language.py

  set-language PATH... --audio eng [--subtitle nor] [--only-unlabelled] [--dry-run]
      sets the language of the audio / subtitle tracks IN PLACE (MP4: the track's language field is patched, MKV:
      mkvpropedit; nothing is re-encoded or rewritten), the same as Metadata > Set Track Language. Every change is read
      back; the Tools > History journal records the old languages when History is on.
"""

from __future__ import annotations

import argparse

from core import track_language as tl
from core import track_language_codes as codes
from redactor_common.cli import EXIT_OK, EXIT_PARTIAL, CliError, Output, add_common_options
from videocli.files import add_path_arguments, collect


def add_set_language_parser(sub) -> None:
    parser = sub.add_parser(
        "set-language", help="set the language of audio / subtitle tracks (in place)",
        description="Set the language tag of the audio and/or subtitle tracks of MP4 and MKV files in place, without "
                    "re-encoding or rewriting the file. Codes are ISO 639-2/B three-letter codes (eng, nor, swe, ger, "
                    "fre, ...; und = undetermined).",
    )
    add_path_arguments(parser)
    parser.add_argument("--audio", metavar="LANG", help="the language to give the audio tracks, e.g. eng")
    parser.add_argument("--subtitle", metavar="LANG", help="the language to give the subtitle tracks, e.g. nor")
    parser.add_argument("--only-unlabelled", action="store_true",
                        help="change only tracks that have no language yet (und); the others keep theirs")
    parser.add_argument("-n", "--dry-run", action="store_true", help="show what would change, write nothing")
    add_common_options(parser)
    parser.set_defaults(handler=run_set_language)


def _language(text: str, option: str) -> str:
    code = codes.normalize(text or "")
    if len(code) != 3 or not code.isalpha():
        raise CliError(f"{option}: '{text}' is not a three-letter ISO 639-2 language code (eng, nor, swe, ger, ...)")
    return code


def run_set_language(args: argparse.Namespace, out: Output) -> int:
    if not args.audio and not args.subtitle:
        raise CliError("nothing to change: give --audio LANG and/or --subtitle LANG")
    audio = _language(args.audio, "--audio") if args.audio else ""
    subtitle = _language(args.subtitle, "--subtitle") if args.subtitle else ""
    files = collect(args.paths, out, recurse=not args.no_recurse)
    failed = changed_tracks = changed_files = 0
    for index, path in enumerate(files, start=1):
        out.progress(index, len(files), path)
        row = {"path": path, "status": "", "changes": [], "message": ""}
        reason = tl.unsupported_reason(path)
        planned: list[tuple[str, str, str]] = []
        if reason:
            row["status"], row["message"] = "failed", reason
        else:
            try:
                tracks = tl.list_tracks(path)
                plan = tl.plan_changes(tracks, audio, subtitle, args.only_unlabelled)
                by_index = {t.index: t for t in tracks}
                planned = [(by_index[i].key, by_index[i].language, language) for i, language in sorted(plan.items())]
                if not plan:
                    row["status"] = "unchanged"
                elif args.dry_run:
                    row["status"] = "planned"
                else:
                    tl.set_track_languages(path, plan)
                    row["status"] = "changed"
            except tl.TrackLanguageError as exc:
                row["status"], row["message"] = "failed", str(exc)
        row["changes"] = [{"track": key, "old": old, "new": new} for key, old, new in planned]
        if row["status"] == "failed":
            failed += 1
        elif row["status"] in ("changed", "planned"):
            changed_files += 1
            changed_tracks += len(planned)
        out.record(row)
        out.line(f"{row['status']:9} {path}" + (f"  ({row['message']})" if row["message"] else ""))
        for key, old, new in planned:
            out.line(f"          track {key}: {old} -> {new}")
    if args.dry_run:
        out.line("DRY RUN: nothing was changed or written.")
    out.line(f"{'Would set' if args.dry_run else 'Set'} the language of {changed_tracks} track(s) in {changed_files} file(s).")
    out.finish({"files": len(files), "failed": failed, "dry_run": args.dry_run,
                "tracks": changed_tracks, "changed_files": changed_files})
    return EXIT_PARTIAL if failed else EXIT_OK
