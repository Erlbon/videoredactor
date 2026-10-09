# The Ʌideo Redactor

**"Ʌ" is U+0245, LATIN CAPITAL LETTER TURNED V** — the video-tool sibling
of "The ƎPUB Redactor"'s turned-E branding.

A bulk metadata editor for video files (MKV, MP4), built for people
managing a mixed library of movies, TV episodes, music videos, and
oddball clips who want mp3tag-style batch editing instead of clicking
into files one at a time.

## What it does

- **Bulk-edit metadata** across MP4 and MKV files from one panel: title,
  description, genre, cast, director, season/episode numbering, and
  more — with a Content Type filter (Movie / TV / Music Video / Clip /
  Misc) that shows only the fields relevant to what you've got selected.
- **TMDB lookup** — search and match a file against The Movie Database,
  pulling in cast, crew, synopsis, and poster art. Every match is
  confirmed by hand; nothing is auto-applied. This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
- **Season & episode picker** for TV — after matching a show, pick the
  exact season and episode so per-episode titles and air dates land
  correctly, not just show-level metadata.
- **Subtitle fetching** via OpenSubtitles, hash-matched against the
  exact file first (guaranteed sync) with a title-search fallback that's
  clearly flagged as sync-not-guaranteed.
- **Offline IMDb database** (Tools > IMDb Database…) — builds a small
  local database from IMDb's free datasets, which you download
  yourself (datasets.imdbws.com: `title.basics`, and optionally
  `title.ratings`, `title.episode`, `title.akas`). It holds titles,
  years, genres, runtimes, ratings, episode numbers and alternative
  titles — no plot, poster or cast (those stay with TMDB). Look up
  against it under Metadata > Look Up > IMDb (Local Database); Redact
  asks it first and TMDB only for the rest. **IMDb's
  datasets are for personal, non-commercial use only and must not be
  redistributed**; the app never downloads or bundles them, and the
  database it builds is your private copy. Terms:
  https://www.imdb.com/conditions

  Information courtesy of IMDb (https://www.imdb.com). Used with permission.
- **Remux to MP4** for MKV files (fast, lossless container swap, not a
  re-encode), with control over what happens to the original.
- **Convert to MP4 (H.264)** for a real re-encode when a container swap
  alone won't do, and **Import & Convert to MP4** to bring in AVI, MOV,
  WMV, FLV, WebM, MPG, and other video formats this tool doesn't
  otherwise load — both via ffmpeg.
- **Poster art** saved as a sidecar image next to the video, the
  convention Plex/Jellyfin/Kodi already expect.
- **Thumbnail preview** — pulls a real frame from the video so you can
  see what you're tagging without opening a player.

## Command line

The one exe (`videoredactor.exe`, or `python main.py` from source) is also the command line. When its first
argument is a command name, it runs that command and the window never opens; with no command, or with a file
or folder to open, the window starts as usual. `videoredactor --help` lists the commands and
`videoredactor COMMAND --help` lists the options of one.

```
videoredactor info    PATH...  [--fields LIST | --all]
videoredactor set     PATH...  -s FIELD=VALUE ... [--clear FIELD ...] [-n]
videoredactor rename  PATH...  [-p PATTERN] [--zero-pad N] [--ascii] [-n]
videoredactor move    PATH...  -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]
videoredactor check   PATH...  [--repair] [--stamp] [--trash-dir FOLDER] [-n]
videoredactor redact  [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N]
                                [--trash-dir FOLDER] [--list-steps]
```

The command line uses the same code as the window, so the results are the same. It reads the same settings file
(`videoredactor_settings.ini` next to the exe: the saved Redact recipe, the library root, the offline IMDb
database) and the same secret store for the API keys (or the `TMDB_API_KEY`, `TVDB_API_KEY` and
`OPENSUBTITLES_API_KEY` environment variables). FFmpeg is needed for most things and MKVToolNix for MKV files,
exactly as in the window. Not every window function is available from the command line; the commands above are
what is.

### Options every command has

| Option | Meaning |
| --- | --- |
| `PATH...` | One or more video files (`.mp4`, `.m4v`, `.mkv`), folders or wildcards (`D:\Films\Dune*.mkv`). A folder is searched recursively. A file you name is always used; the app's own leftover temporary files are not listed. A path that matches nothing is reported, and if nothing at all matches the command stops with exit code 2. A name containing `[` or `]` is taken literally, a wildcard's matches are filtered by extension like a folder's files, and a folder inside a folder that is a link or junction is not followed. |
| `-R`, `--no-recurse` | For a folder, look only at the files directly in it. |
| `--json` | Print one JSON document on stdout instead of text (see "JSON output"). Nothing else goes to stdout. |
| `-q`, `--quiet` | No progress lines and no warnings on stderr (errors are still shown). |
| `-o FILE`, `--output FILE` | Write the result (the text, or with `--json` the JSON document) to FILE instead of stdout. The file is complete when the program exits. This is the reliable way for a script to read a result. |
| `-n`, `--dry-run` | On the commands that change files (`set`, `rename`, `move`, and `check` with `--repair` or `--stamp`): show what would happen and change nothing. |
| `-h`, `--help` | Help for the program or for one command. |
| `--version` | The version (top level only). |

Progress lines (`[3/20] name.mkv`) go to stderr when more than one file is processed.

### info

`videoredactor info PATH... [--fields LIST | --all]`

Shows each video's container, resolution, codecs, bitrate, frame rate and length, the result of its last check
(if one is recorded in the file), and its tags.

| Option | Meaning |
| --- | --- |
| `--fields LIST` | Comma-separated tag fields to show, e.g. `--fields title,show_title,season`. Default: `content_type, title, show_title, season_number, episode_number, release_date`. |
| `--all` | Show every tag field that has a value. |

Only fields with a value are listed. Exit code 1 if a file could not be read.

### set

`videoredactor set PATH... -s FIELD=VALUE [-s ...] [--clear FIELD ...] [-n]`

Sets or empties tag fields and saves each video in place. Only the tags are written, the video is not
re-encoded, and the save is read back and compared with what was meant to be written (a save that did not stick
is reported as `failed`). Fields and values are checked before any file is touched; a bad one stops the command
with exit code 2.

| Option | Meaning |
| --- | --- |
| `-s FIELD=VALUE`, `--set FIELD=VALUE` | Set a field (repeat for several), e.g. `-s show_title=Dune -s season=1 -s episode=3`. |
| `--clear FIELD` | Empty a field (repeat for several). |
| `-n`, `--dry-run` | Show the old and new value of each field, save nothing. |

The fields are: content_type, title, sort_title, description, genre_tags, release_date, language,
personal_rating, comment, director, cast, writer, studio, collection, show_title, season_number, episode_number,
network, artist, album, track_title, composer. Field names are case-insensitive and the usual spellings work:
`genre`, `year`, `season`, `episode`, `show`, `type`, `rating`.

Checks: `content_type` is one of Movie, TV, Music Video, Clip, Misc (any capitalisation); `season_number`,
`episode_number` and `personal_rating` are whole numbers (the rating 1 to 5); `release_date` is `YYYY`,
`YYYY-MM` or `YYYY-MM-DD`; `language` is a language code (`en`, `eng`, `nb`, `en-GB`).

Each file's result is `changed`, `unchanged` (nothing differed), `planned` (dry run) or `failed`.

### rename

`videoredactor rename PATH... [-p PATTERN] [--zero-pad N] [--ascii] [-n]`

Renames each video from its tags, in its own folder, like Rename / Export / Move > Rename files in place. Its
poster (`<name>-poster.jpg`) and subtitle files (`<name>.srt`, `<name>.<lang>.srt`) are renamed with it. Never
overwrites: a name that is taken gets `(2)`, `(3)`, ... A change of letter case alone (`song` to `Song`) counts as a rename.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | The new name (without the extension), with `%field%` tokens, default `"%show_title% - S%season_number%E%episode_number% - %title%"`. Quote it so the shell leaves the `%` signs alone. |
| `--zero-pad N` | Pad the episode number to N digits (`--zero-pad 2` gives `03`). |
| `--ascii` | ASCII-safe names (é becomes e, æ becomes ae, other symbols are dropped). |
| `-n`, `--dry-run` | Show the new names, rename nothing. |

Tokens are the tag field names above. A video whose pattern would leave a required field empty (a token outside
the pattern's optional `(...)`, `[...]` or `{...}` groups) is `skipped` with the reason, so it never becomes
"Show - S E - ". A video that already has the name is `unchanged`. There is no undo for the command line: preview with `--dry-run`.

### move

`videoredactor move PATH... -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]`

Moves (or copies) each video into a folder tree under a library folder, like Rename / Export / Move > Move into
folders. The pattern may contain `/` to make sub-folders: `"%show_title%/Season %season_number%/%title%"`.
Poster and subtitle files travel with the video. Missing folders are created; nothing is overwritten (a taken
name gets `(2)`); a destination outside the library folder or too long is refused.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | Required. The path under the library folder, with `%field%` tokens. |
| `--root FOLDER` | The library folder. Default: the one saved in the app (Rename / Export / Move window). The folder must exist. |
| `--copy` | Copy instead of move, leaving the originals. |
| `--zero-pad N`, `--ascii` | As for `rename`. |
| `-n`, `--dry-run` | Show where each video would go, change nothing. |

Across volumes a move is a verified copy followed by sending the original to the Recycle Bin. A video whose
pattern would leave a required field empty is `skipped`. There is no undo for a move either: preview with `--dry-run`.

### check

`videoredactor check PATH... [--repair] [--stamp] [--trash-dir FOLDER] [-n]`

The Media > Check Files scan: can the file be opened, does it end early, does it have a seek index, do its audio
tracks have a default and a language. A file is `DAMAGED` (its content is incomplete or unreadable), `REPAIRABLE`
(a lossless remux fixes it), has a `NOTE` (worth knowing, nothing to repair) or is `CHECKED OK`. Nothing is
changed unless you ask.

| Option | Meaning |
| --- | --- |
| `--repair` | Repair the `REPAIRABLE` files with a lossless remux; the original goes to the Recycle Bin (or `--trash-dir`). A `DAMAGED` file is never touched, because repairing it would drop its unreadable end: that stays a decision for Media > Check Files in the app. |
| `--stamp` | Record the result in the file (the same scan stamp the window writes), so a later `info` or the window shows it. |
| `--trash-dir FOLDER` | With `--repair`: move originals into this folder (created if needed) instead of the Recycle Bin. |
| `-n`, `--dry-run` | With `--repair` or `--stamp`: show what would be done, change nothing. |

Exit code 1 when a file is still `DAMAGED` or `REPAIRABLE` after the command, could not be opened, or could not be
checked (FFmpeg's ffprobe is missing).

### redact

`videoredactor redact [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N] [--trash-dir FOLDER] [--list-steps]`

Runs the Redact recipe on the videos, the same steps as Edit > Redact: check and repair, tags from the filename
and from the folder path, lookups on IMDb, TMDB and TheTVDB, subtitles, MKV to MP4 remux, rename, move into
folders. Each changed file is saved in place and its original goes to the Recycle Bin (or `--trash-dir`).
Guesses below the confidence threshold are listed under "needs review" and not applied. There is no
`--dry-run`: use `info` and `check` first, and `--disable` for the steps you do not want.

| Option | Meaning |
| --- | --- |
| `--recipe FILE` | Use this recipe (a JSON file in the format the app stores) instead of the one saved in the app. |
| `--enable STEP` | Turn a step on for this run (repeatable). |
| `--disable STEP` | Turn a step off for this run (repeatable). |
| `--threshold N` | Confidence needed to apply a guess, `0`-`1` or a percentage (`0.9`, `90` or `90%`); a plain number from 1 to 5 such as `1.5` is refused as ambiguous. |
| `--trash-dir FOLDER` | Move originals into this folder (created if needed) instead of the Recycle Bin, for a machine or a task that has none. |
| `--list-steps` | Show the steps and whether the recipe has each on, then stop (no `PATH` needed). |

Steps: `check_repair`, `filename_tags`, `path_tags`, `lookup`, `subtitles`, `remux_mkv_to_mp4`, `rename`,
`move_into_folders`. Without `--recipe` the recipe saved in the app is used (the defaults if none was saved). A
repair is only automatic when a lossless remux fully fixes the file; a damaged file is reported and left alone.
Exit code 1 if any file failed; files that need review are not failures.

### JSON output

`--json` prints one document: `{"results": [...], <summary fields>, "warnings": [...], "errors": [...]}`. It is ASCII-only (a non-ASCII character in a path is a `\uXXXX` escape, which any JSON reader decodes). If a command fails or is interrupted after it started, the document is still printed, with what was done so far and an `error` entry, so a script reading `--output FILE` never finds an empty or half-written file.

| Command | Each entry in `results` | Summary fields |
| --- | --- | --- |
| `info` | `path`, `status`, `check`, `technical` (container, resolution, video_codec, audio_codec, bitrate, frame_rate, duration_seconds), `fields` (name to value) | `files`, `failed` |
| `set` | `path`, `status`, `changes` (field to `{old, new}`), `message` | `files`, `failed`, `dry_run` |
| `rename`, `move` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, and `pattern` or `root` |
| `check` | `path`, `status`, `problem`, `findings` (code, severity, message), `declared_seconds`, `readable_seconds`, `repaired`, `stamped`, `message` | `files`, `problems`, `repair`, `stamp`, `dry_run` |
| `redact` | `file`, `path`, `status`, `applied`, `needs_review` (step, value, confidence, reason), `failures`, `notes`, `skipped`, `not_saved` | `files`, `failed`, `needs_review`, `cancelled`, `confidence_threshold`, `run_notes` |
| `redact --list-steps` | `step`, `label`, `enabled` | `confidence_threshold` |

### Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Done (files that were skipped or unchanged are not failures). |
| 1 | The command ran but some files failed (for `check`: some files have problems). |
| 2 | Bad arguments, an unknown field or step, or no files found. The reason is on stderr. |
| 70 | An internal error (a bug); the traceback is on stderr. |
| 130 | Interrupted with Ctrl+C. |

### Using it from scripts and scheduled tasks (Windows)

`videoredactor.exe` is a windowed program, and Windows shells treat those differently from console programs:
typed by hand in a terminal its output appears there and `>` / `|` redirection works, but an interactive shell
does not wait for it (the prompt can come back before the output), and a script cannot read a windowed
program's output unless it is redirected. So for automation: ask for the result in a file with `--output`, wait
for the process, and read the exit code.

```
:: batch file (cmd waits for the program in a batch file; %errorlevel% is the exit code)
videoredactor.exe check "D:\Films" --json --output "%TEMP%\check.json"
if errorlevel 1 echo some files have problems

:: interactive cmd: start /wait waits and keeps the exit code
start /wait videoredactor.exe redact "D:\Incoming" --quiet --trash-dir "D:\Trash"

# PowerShell: wait with Start-Process, read .ExitCode
$p = Start-Process videoredactor.exe -ArgumentList 'check','D:\Films','--json','-o','C:\Temp\check.json' -Wait -PassThru
$p.ExitCode
(Get-Content C:\Temp\check.json -Raw | ConvertFrom-Json).results | Where-Object problem

# PowerShell: piping to Out-Null also waits
videoredactor.exe check "D:\Incoming" --repair | Out-Null; $LASTEXITCODE
```

Task Scheduler waits for the program and records its exit code as it is. On Linux and macOS there is no such
distinction: the output goes to the terminal and pipes as usual.

### Examples

```
videoredactor info "D:\Films\Dune" --all                                     what is in a folder
videoredactor set "D:\Series\Dune" -s show_title=Dune -s type=TV -n          preview a bulk edit, then run it without -n
videoredactor rename "D:\Series\Dune" -p "%show_title% - S%season_number%E%episode_number% - %title%" --zero-pad 2
videoredactor move "D:\Incoming" -p "%show_title%/Season %season_number%/%title%" --root "D:\Library"
videoredactor check "D:\Films" --repair --stamp --json -o report.json       repair what can be repaired, record the result
videoredactor redact "D:\Incoming" --disable lookup --disable subtitles --trash-dir "D:\Trash"
```

What the commands will not do: overwrite a file, delete anything for good, or ask a question. Everything that
could be a prompt in the window is a flag here or a skipped file in the report.

## Design principles carried over from The ƎPUB Redactor

- **Never write metadata the user didn't confirm.** TMDB and subtitle
  matches always go through a picker — no auto-applying a "confident"
  top result, since a wrong write to an actual file is a worse failure
  than an empty field.
- **Fail loud, not silent.** A file that can't load, save, or match
  shows a clear status and error message instead of being silently
  skipped or endlessly retried.
- **Shell out to the real tools.** Metadata reads/writes go through
  mutagen (MP4) and mkvpropedit/mkvmerge (MKV) rather than reimplementing
  container formats by hand.
- **Typing alone never touches a file.** Edits are staged in memory
  until you explicitly apply and save.

## License

Licensed under the [GNU General Public License v3.0 or later](LICENSE).
The GUI is built on PyQt6, which Riverbank Computing licenses under GPL
v3 (or a paid commercial license) -- this project ships under
GPL-compatible terms to match.
