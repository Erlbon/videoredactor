"""
videocli/cmd_redact.py

  redact PATH...   run the Redact recipe on the video files: the same steps as Edit > Redact in the app
                   (check and repair, tags from the filename and the path, lookups on IMDb / TMDB / TheTVDB,
                   subtitles, MKV to MP4 remux, rename, move), saved in place with each original in the
                   Recycle Bin (or moved to --trash-dir).

The recipe is the one saved in the app (Edit > Edit Redact Recipe) unless --recipe FILE names a JSON recipe;
--enable / --disable / --threshold adjust it for this run only. The API keys come from the TMDB_API_KEY,
TVDB_API_KEY and OPENSUBTITLES_API_KEY environment variables if set, else the app's saved ones; the offline IMDb
database and the library root come from the app's settings. Running the recipe and the report are the shared
ones in redactor_common.cli.commands.
"""

from __future__ import annotations

import argparse

from core import imdb_settings
from core.redact_steps import (
    RedactEnv, VideoCtx, build_catalogue, finalize_file, load_recipe, recipe_from_setting,
)
from redactor_common.cli import CliError, Output, add_common_options
from redactor_common.cli.commands import (
    add_redact_options, build_recipe, list_steps, read_recipe_file, redact_items, trash_to,
)

from videocli.files import collect, load_videos, rename_log


def add_redact_parser(sub) -> None:
    parser = sub.add_parser(
        "redact", help="run the Redact recipe",
        description="Run the Redact recipe on the video files. Each changed file is saved in place and its original goes to "
                    "the Recycle Bin (or --trash-dir). Guesses below the confidence threshold are listed, not applied.",
    )
    parser.add_argument("paths", nargs="*", metavar="PATH", help="video files, folders or wildcards")
    parser.add_argument("-R", "--no-recurse", action="store_true", help="for a folder, look only at the files directly in it")
    add_redact_options(parser)
    add_common_options(parser)
    parser.set_defaults(handler=run_redact)


def run_redact(args: argparse.Namespace, out: Output) -> int:
    env = RedactEnv(
        rename_log=rename_log(), trash=trash_to(args.trash_dir) if args.trash_dir else None,
        imdb_local=imdb_settings.load_database(),
    )
    catalogue = build_catalogue(env=env)
    base = recipe_from_setting(read_recipe_file(args.recipe), catalogue) if args.recipe else load_recipe(catalogue)
    recipe = build_recipe(args, base, catalogue)
    if args.list_steps:
        return list_steps(recipe, catalogue, out)
    if not args.paths:
        raise CliError("give the video files to redact (or --list-steps)")

    videos = load_videos(collect(args.paths, out, recurse=not args.no_recurse))
    return redact_items(
        videos, recipe, catalogue, make_context=lambda v: VideoCtx(v, env), describe=lambda v: v.path.name,
        finalize=finalize_file, finalize_label="Save", path_of=lambda v: str(v.path), out=out,
    )
