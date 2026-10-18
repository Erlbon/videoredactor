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
  more — and filter the file list from the funnel in any column header
  (Content Type, Status, Filename, Year and more).
- **TMDB lookup** — search and match a file against The Movie Database,
  pulling in cast, crew, synopsis, and poster art. Every match is
  confirmed by hand; nothing is auto-applied. This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
- **Season & episode picker** for TV — after matching a show, pick the
  exact season and episode so per-episode titles and air dates land
  correctly, not just show-level metadata.
- **Subtitle translation** (Media > Translate Subtitles to English…,
  Media > Compare Subtitles…) — translates a non-English subtitle to
  English with an engine you run yourself (a LibreTranslate server or
  the argos-translate command; nothing is built in). Original and
  translation are two files (<name>.ru.srt, <name>.en.srt), shown side
  by side with the English cells editable; the translation is marked
  as machine translated (embedding gives the track the title
  "English (machine translation)"). Set up under Tools > Preferences >
  Translation.
- **Subtitle fetching** via OpenSubtitles, hash-matched against the
  exact file first (guaranteed sync) with a title-search fallback that's
  clearly flagged as sync-not-guaranteed. For music videos the same
  dialog can fetch time-synced lyrics from LRCLIB and write them as an
  .srt (with an offset control).
- **Music videos: the title is the song** (as MusicBrainz names it, version included). The old separate
  track title field is gone: old values are kept in the file and can be moved into the title (side panel
  button, or the Redact step "Use the track title as the title"). An MP4 music video also gets the iTunes
  media type (`stik` 6); MKV keeps only the custom Content Type tag.
- **ThePornDB and StashDB lookups** (Metadata > Look Up > ThePornDB… / StashDB…) — optional scene
  metadata sources for files you own: title, date, studio (and its parent), performers as credited,
  tags, description, director and the cover. The dialog first asks the service for the exact file by its
  fingerprint (the OSHASH, the same 64-bit hash as OpenSubtitles; the MD5 only if Tools > Preferences >
  Look Up allows it, as it reads the whole file) and highlights a **file match**; anything found by title is
  flagged "match by title: check it". You pick one result, then the usual per-field review shows what
  would change before anything is written. A poster is embedded in the video (on Save) only when you tick
  "Save the poster in the file". Both need your own key (Tools > API Keys, with a "Check the key"
  button; the StashDB address is a setting, so other stash-box servers work). **Privacy:** only a file hash
  and/or the search text you see leave this computer, together with your key to that service; no file name,
  path or other tag, and no image is downloaded unless you ask. Data courtesy of
  [ThePornDB](https://theporndb.net) and [StashDB](https://stashdb.org); their data is user-contributed,
  check a match before relying on it. Not included yet: perceptual-hash (PHASH) matching.
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
- **Convert** (Media > Convert, Ctrl+Shift+C; also right-click > Fix and Convert) is the one conversion entry. Its dialog
  picks the mode: **Automatic** (the conversion rules), **Lossless remux to MP4** (fast, no re-encode; a file with
  something MP4 cannot hold is skipped with the reason) or **Re-encode to H.264** (a real H.264/AAC re-encode at the
  Preferences > Transcode quality). All three go through the same verified engine, the same dry run, and the same
  policy for the original (Preferences > Conversion: a quarantine folder with an undo log, the Recycle Bin, or keep it
  where it is); a re-encode keeps the original by default.
- **Conversion rules** (Media > Convert in Automatic mode, File > Import
  and Convert, the Open Folder offer and the Redact convert step) bring
  AVI, MOV, WMV, FLV, WebM, MPG and other formats into MP4 or MKV,
  copying streams wherever a container can hold them — see "Library
  safety and conversion" below.
- **Metadata > Get Poster** fetches the poster of the selected files from the source ids they ALREADY carry (TMDB,
  TheTVDB, IMDb through TMDB, ThePornDB, StashDB; a TV episode gets its series' poster), without searching, under
  the same true-poster rule below. A file that already has a poster is left alone unless you tick Replace (then the
  usual review asks, with the new poster unticked). Staged like a lookup: Save embeds it, Undo takes it back. The
  Redact step `get_poster` (off by default) does the same for files without a poster.
- **Poster art embedded in the video** (MP4: the cover atom; MKV: an attachment named `cover.jpg`), which
  Jellyfin, Plex and Kodi read from the file. A lookup (TMDB, TheTVDB, ThePornDB, StashDB) embeds one only
  when you tick "Save the poster in the file", and only a TRUE poster: the source's own poster picture,
  portrait (height at least 1.3 times the width, about 2:3), at least 400 x 600, scaled down to at most
  1000 pixels wide and kept under 400 KB as a JPEG. A wide picture, a background or a video frame is never
  used (an off-by-default box "Accept a landscape cover when there is no poster" allows a wide one for that
  lookup); with no poster the file gets none and the dialog says "No poster available from <service>". The
  side panel shows the embedded poster (captioned "Poster"), else a frame preview ("Frame preview": never
  written to the file). The app creates no poster file next to the video any more: **Media > Embed Existing
  Poster Files...** moves old `<name>-poster.jpg` files into their videos (reviewed first, landscape ones
  unticked; each file goes to the Recycle Bin only after the embedded poster was read back).
- **Source ids kept in the file**: every lookup you apply records which record it came from, as tags inside
  the video (`THEPORNDB_ID`, `STASHDB_ID`, `TMDB_ID`, `IMDB_ID`, `TVDB_ID`, `SOURCE_URL`, `METADATA_MATCH` =
  `service;date;file|title|manual`), shown read-only in the side panel's collapsed **Sources** section (links
  to the records), in an optional **Sources** column and in `info --fields` / `--all`. A file that already
  has an id uses it directly next time ("File has an ID" at the top of the lookup dialog, and in the Redact
  lookup steps); an id is replaced only through the per-field review. Settings export never includes them.
- **Thumbnail preview** — pulls a real frame from the video so you can
  see what you're tagging without opening a player.
- **Change history** (Tools > History…) — opt-in, off by default: turn it
  on in Tools > Preferences > History (or answer Yes when Tools > History
  asks) and off again there, where "Delete History…" moves what was
  recorded to the Recycle Bin (after asking). A file that is renamed,
  moved (File > Rename File, Rename / Export / Move, Undo Last Rename,
  Redact's rename and move steps, the command line's `rename` and `move`)
  or converted to a new name or extension (Media > Convert, the conversion
  rules, the Redact convert step) takes its
  history with it, unless the original is kept: then the history stays with the original. Export / Import Settings carries the
  on/off switch: importing a bundle with history on turns recording on. While it is on, every tag change the app makes to a file
  (Save, Save All, Redact, a Restore, the command line's `set`, which
  follows the same setting: off = nothing recorded) is recorded with the old and new value of each changed field in
  `videoredactor_journal.jsonl`, next to `videoredactor_settings.ini`.
  "Restore Old Values…" writes the old values back, so an unwanted save
  can be undone even after the app was closed. A note written by Tools >
  Create Notes (created, or refreshed from an older version) is recorded too
  and "Undo Note…" puts it back (the note goes to the Recycle Bin, or to the
  Library safety backup folder on a network drive; a refresh gets its older
  text back; only while the note is unchanged). Covers and other images
  are not kept there; a file replaced by a conversion is in the Recycle
  Bin or the quarantine folder instead. The newest 50,000 changes from the
  last 90 days are kept.

## Library safety and conversion

The conversion rules (Tools > Preferences > Conversion) decide what happens to a file: a legacy container (AVI,
WMV, MOV, FLV, MPG, WebM, OGM, ...) becomes MP4 or MKV by stream copy; only video that no container can hold
(RealVideo, Flash, odd WMV) is re-encoded; a series episode over the bitrate or size limits is re-encoded to HEVC
and scaled to 720p when taller (movies only if you turn that on).

The HEVC encoder ("Encoder for the re-encodes" in Preferences > Conversion) is no longer assumed to be NVIDIA's.
"Automatic" (the default) uses hevc_nvenc only when ffmpeg lists it AND a one-second test encode with it works on
this computer (checked once per run of the app, and again when the ffmpeg path changes); otherwise libx265 in
software (`-preset medium -crf 27`, about the size and quality of NVENC cq 30; slower, but it works on any
computer, and the 85 percent size check still protects the source). "NVIDIA GPU always" and "Software always"
skip the check. "Use the NVIDIA GPU to decode and scale" (off by default) adds `-hwaccel cuda` and `scale_cuda`,
only with the NVIDIA encoder, and falls back to software scaling if that fails; with it off scaling is the
software `scale=-2:720`. A series episode is a file tagged TV,
or an untagged file whose name reads as an episode (`S01E02`); movies, clips and other tagged files are not.
External `.srt` files beside the video are muxed in (an MP4 then becomes an MKV) and removed afterwards. A file
with 4 or more subtitle tracks (the default of "MKV when a file has at least") becomes an MKV, not an MP4. Disc
images (ISO, BIN, IMG, VOB) are never converted under the default "Leave alone", and not listed either; "list them"
names each one in the report for a manual decision, and only the third choice stream-copies a single `.vob` to
MKV. These rules are separate from Preferences > Transcode, which
only holds the settings of Media > Convert in its re-encode to H.264 mode.

Every conversion is written to a temporary `.redact-part` / `.redact-resize` file beside the source and checked before it
replaces anything: the same number of video and audio tracks, the expected number of subtitle tracks, the target
height when scaling, a bitrate-driven re-encode smaller than the set share of the source, and a length within the duration
tolerance (Preferences > Library safety: the larger of the seconds and the percentage you set; an AVI/OGM whose
header length is stale may be up to 60 s off when the result's own streams agree). A result that fails is deleted
and the original stays as it was. A file modified in the last few minutes (setting) is never touched.

What happens to an original that a verified result replaced (Tools > Preferences > Library safety):

- **Recycle Bin** (default, no quarantine folder set). On a network share or removable drive, where there is no
  Recycle Bin, the original is kept beside the result as `<name>.redact-orig.<ext>`.
- **Quarantine folder** (if set): moved there, under a dated folder per kind of change that mirrors its path in
  the library, and logged line by line in a TSV undo log.
- **Deleted for good** when "Delete the original after a verified conversion" is on (off by default). The ingested
  `.srt` files are then deleted too. This cannot be undone.

Dry run: Media > Convert, Import and Convert and the Open Folder offer each ask "Dry run first"
(ticked by default) in their one dialog. Edit > Redact asks too, per run: when the recipe's "Convert to MP4/MKV
and ingest subtitles" step is on, its confirm question has "Dry run the conversion step (list what would happen,
change nothing)", ticked by default (there is no saved dry-run setting any more, so an old choice can never
decide silently). A dry run reports "DRY RUN, nothing changed: would convert ..." for every file
and touches nothing.

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
videoredactor notes   PATH...  [--kind KIND] [--write] [--include-uncertain] [--update] [--no-wikipedia] [-n]
videoredactor lookup-check  theporndb|stashdb  [--endpoint URL]
videoredactor fix-container PATH... [--apply] [--log FILE]
videoredactor undo-remux LOG.tsv
videoredactor set-language PATH... --audio eng [--subtitle nor] [--only-unlabelled] [--dry-run]
```

The command line uses the same code as the window, so the results are the same. It reads the same settings file
(`videoredactor_settings.ini` next to the exe: the saved Redact recipe, the library root, the offline IMDb
database) and the same secret store for the API keys (or the `TMDB_API_KEY`, `TVDB_API_KEY` and
`OPENSUBTITLES_API_KEY` environment variables; `THEPORNDB_API_KEY` and `STASHDB_API_KEY` for the two scene sources). FFmpeg is needed for most things and MKVToolNix for MKV files,
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

Only fields with a value are listed. In text mode each line also shows the container, resolution, codecs,
frame rate, length and the check result; `--json` has the same in `technical` and `check`. A file named in full
is always read, even if its name looks like one of the app's own temp files (those are left out only when a
folder or wildcard finds them). Exit code 1 if a file could not be read.

### set

`videoredactor set PATH... -s FIELD=VALUE [-s ...] [--clear FIELD ...] [-n]`

Sets or empties tag fields and saves each video in place. Only the tags are written, the video is not
re-encoded, and the save is read back and compared with what was meant to be written (a save that did not stick
is reported as `failed`). Fields and values are checked before any file is touched; a bad one stops the command
with exit code 2. When the change history is on (Tools > Preferences > History, the same setting the window
uses), each change is recorded there and can be restored in Tools > History; when it is off (the default) the
command line records nothing either. The same holds for `redact`.

| Option | Meaning |
| --- | --- |
| `-s FIELD=VALUE`, `--set FIELD=VALUE` | Set a field (repeat for several), e.g. `-s show_title=Dune -s season=1 -s episode=3`. |
| `--clear FIELD` | Empty a field (repeat for several). |
| `-n`, `--dry-run` | Show the old and new value of each field, save nothing. |

The fields are: content_type, title, sort_title, description, genre_tags, release_date, language,
personal_rating, comment, director, cast, writer, studio, collection, show_title, season_number, episode_number,
network, artist, album, composer, lyrics. Field names are case-insensitive and the usual spellings work:
`genre`, `year`, `season`, `episode`, `show`, `type`, `rating`.

Checks: `content_type` is one of Movie, TV, Music Video, Clip, Misc (any capitalisation); `season_number`,
`episode_number` and `personal_rating` are whole numbers written with the digits 0-9 (the rating 1 to 5, the
others up to 99999; `007` is stored as `7`); `release_date` is a real date, `YYYY`, `YYYY-MM` or `YYYY-MM-DD`
(`2026-02-30` is refused); `language` is a language code (`en`, `eng`, `nb`, `en-GB`). A value with a control
character in it (or, outside the description-type fields, a line break or tab) is refused, since no tag format
can store it. `lyrics` (a music video's song text, up to 64 KB, many lines) takes its text from a file:
`-s lyrics=@song.txt` (UTF-8; `@-` reads standard input); a value that does not start with `@` is used as it is.
`info --fields lyrics` prints them. If a field is given both `-s` and `--clear`, `--clear` wins whatever the order.

Each file's result is `changed`, `unchanged` (nothing differed), `planned` (dry run) or `failed`.

### clear

`videoredactor clear PATH... [--groups LIST] [-n] --yes`

Empties groups of fields on each video and saves it in place (the tags only; the video is not re-encoded), with the same plan as Edit > Clear Fields in the window. Without `--yes` (and without `--dry-run`) it refuses to run: there is no question to answer on a command line, and a cleared file cannot be undone once saved. A file that is read-only is reported as `failed`; the save is read back and compared.

| Option | Meaning |
| --- | --- |
| `--groups LIST` | Comma-separated groups: `descriptive` (title, description, genre, cast, season, episode, lyrics...), `sources` (TMDB, IMDb, TheTVDB, ThePornDB and StashDB ids, the source link and the match note), `poster` (the embedded cover), `type` (the content type: only cleared when named). Default `descriptive,sources,poster`. |
| `-n`, `--dry-run` | Show what would be cleared, write nothing. |
| `--yes` | Really clear and save (required unless `--dry-run`). |

The scan stamp, the technical fields, the retired track title, subtitle files and the MP4 media type atom are never touched.

### rename

`videoredactor rename PATH... [-p PATTERN] [--zero-pad N] [--ascii] [-n]`

Renames each video from its tags, in its own folder, like Rename / Export / Move > Rename files in place. Its
existing poster file (`<name>-poster.jpg`, no longer created by the app) and subtitle files (`<name>.srt`, `<name>.<lang>.srt`) are renamed with it. Never
overwrites: a name that is taken gets `(2)`, `(3)`, ... A change of letter case alone (`song` to `Song`) counts as a rename.

| Option | Meaning |
| --- | --- |
| `-p PATTERN`, `--pattern PATTERN` | The new name (without the extension), with `%field%` tokens, default `"%show_title% - S%season_number%E%episode_number% - %title%"`. Quote it so the shell leaves the `%` signs alone. |
| `--zero-pad N` | Pad the episode number to N digits (`--zero-pad 2` gives `03`). Default: the choice saved in the app's Rename window (on, with its width, or off); `--zero-pad 0` turns it off. |
| `--ascii` | ASCII-safe names (é becomes e, æ becomes ae, other symbols are dropped). Also on when the app's Rename window has it saved. |
| `-n`, `--dry-run` | Show the new names, rename nothing. |

Tokens are the tag field names above; a token that is not a field name (a typo such as `%tittle%`) is refused
with exit code 2 instead of silently rendering as nothing. A rename pattern cannot contain `/` or `\` (the
`move` command makes folders). In a batch file write `%%` for each `%`, for example `-p "%%show_title%% - %%title%%"`
(cmd expands a single `%name%` itself, and a pattern that then reads nothing makes the file `skipped`). A video whose pattern would leave a required field empty (a token outside
the pattern's optional `(...)`, `[...]` or `{...}` groups) is `skipped` with the reason, so it never becomes
"Show - S E - ". A video that already has the name is `unchanged`. There is no undo for the command line: preview with `--dry-run`.

### move

`videoredactor move PATH... -p PATTERN [--root FOLDER] [--copy] [--zero-pad N] [--ascii] [-n]`

Moves (or copies) each video into a folder tree under a library folder, like Rename / Export / Move > Move into
folders. The pattern may contain `/` to make sub-folders: `"%show_title%/Season %season_number%/%title%"`.
Existing poster files and subtitle files travel with the video. Missing folders are created; nothing is overwritten (a taken
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

`videoredactor redact [PATH...] [--recipe FILE] [--enable STEP] [--disable STEP] [--threshold N] [--trash-dir FOLDER] [--convert-dry-run] [--list-steps]`

Runs the Redact recipe on the videos, the same steps as Edit > Redact: check and repair, tags from the filename
and from the folder path, lookups on IMDb, TMDB and TheTVDB, subtitles, MKV to MP4 remux, convert to MP4/MKV and
ingest subtitles, rename, move into folders. Each changed file is saved in place and its original goes to the
Recycle Bin (or `--trash-dir`). The convert step (`convert_library`, off unless the recipe or `--enable` turns it
on) converts for real unless `--convert-dry-run` is given; it follows the app's Library safety settings for the
quarantine folder and delete-after-conversion. The old saved dry-run setting is not read any more.
Guesses below the confidence threshold are listed under "needs review" and not applied. There is no
`--dry-run` for the whole recipe: use `info` and `check` first, and `--disable` for the steps you do not want.

| Option | Meaning |
| --- | --- |
| `--convert-dry-run` | The convert step only lists what it would convert ("DRY RUN, nothing changed: would convert ...") and changes nothing; the other steps still run and save. Without it the conversion is real. |
| `--recipe FILE` | Use this recipe (a JSON file in the format the app stores) instead of the one saved in the app. |
| `--enable STEP` | Turn a step on for this run (repeatable). |
| `--disable STEP` | Turn a step off for this run (repeatable). |
| `--threshold N` | Confidence needed to apply a guess: a fraction `0`-`1` (`0.9`, also `1`), or a percentage with at least two digits (`90`, `90%`, `100`); a number above 1 and below 5 such as `1.5` is refused as ambiguous. |
| `--trash-dir FOLDER` | Move originals into this folder (created if needed) instead of the Recycle Bin, for a machine or a task that has none. |
| `--list-steps` | Show the steps and whether the recipe has each on, then stop (no `PATH` needed). |

Steps: `check_repair`, `filename_tags`, `path_tags`, `lookup`, `scene_lookup` (off by default: asks ThePornDB and
StashDB about the file's own fingerprint and only ever produces a "needs review" suggestion, whatever the
threshold), `get_poster` (off by default: a file that carries a source id and has no poster gets the true poster
of that record from TMDB, TheTVDB, IMDb (through TMDB), ThePornDB or StashDB, without searching; applied as an
id match, so at the default threshold it needs no review), `track_title_to_title` (off by default: a Music Video with an old track title and an empty title gets
it as the title, only ever as a "needs review" suggestion), `subtitles`, `translate_subtitles`, `remux_mkv_to_mp4`, `convert_library`,
`rename`, `move_into_folders`. Without `--recipe` the recipe saved in the app is used (the defaults if none was saved). A
repair is only automatic when a lossless remux fully fixes the file; a damaged file is reported and left alone.
Exit code 1 if any file failed; files that need review are not failures. Rename and Move run last, after the
file is saved, and also on a file that had nothing to save. A `--recipe` file that is not valid JSON or not a
recipe is refused (exit code 2) rather than replaced by the default recipe.

### notes

`videoredactor notes PATH... [--kind KIND] [--update] [--update-legacy] [--no-cache] [--no-wikipedia] [--write] [--include-uncertain] [--show-drafts]`

Drafts the Markdown notes the Pluteus media server shows beside a folder, the same as Tools > Create Notes: `series.md` in a
series folder (episodes named `S01E02`, directly or in Season folders) and `about.md` in a film folder, from TMDB (your
own key under Tools > API Keys, or `TMDB_API_KEY`) and, when Wikidata links the title's IMDb id to an article, a Wikipedia
introduction. A report unless `--write` is given: every title folder is listed with how sure the match is and what would
be written. `--write` writes the drafts (`status: draft`) of the confident matches only: TMDB has exactly one title with
the folder's name (and year), and for a series the seasons and episode numbers in the folder fit it. A folder that already
has `artist.md`, `author.md`, `series.md`, `album.md` or `about.md` (any letter case) is never touched, and a note is never
overwritten. Uncertain and unmatched folders are listed with the reasons and the candidates and not written.

| Option | Meaning |
| --- | --- |
| `--kind KIND` | Which notes: `series` (`series.md`), `movie` (`about.md` of a film folder) or `both` (the default); several can be joined by commas. |
| `--update` | Also list the notes made by an older version of this program (stamped, an older format, nobody edited them) and, with `--write`, replace only those; the old note goes to the Recycle Bin. A note a person edited, or one this program did not write, is never touched. |
| `--update-legacy` | With `--update --write`: also replace the notes of the first versions (no stamp, so it cannot be known whether they were edited; still the Recycle Bin, still never an edited or foreign note). |
| `--no-cache` | Do not use the disk cache of earlier answers: everything is asked again. |
| `--no-wikipedia` | No Wikipedia introduction in the notes (fewer requests). |
| `--write` | Write the drafts of the confident matches (the default is a report only). |
| `--include-uncertain` | With `--write`: also write an uncertain match whose top candidate fits the name and year on its own and has no equal; marked "from an uncertain match" in the report. |
| `--show-drafts` | Print each draft note (text) or include it (`--json`). |

Exit code 1 if a lookup failed (no key, no network); uncertain folders are not failures.

### lookup-check

`videoredactor lookup-check theporndb|stashdb [--endpoint URL]`

Makes one small authenticated request to the service with the saved key (or `THEPORNDB_API_KEY` /
`STASHDB_API_KEY`) and prints `OK: ...` or `FAILED: <the exact reason>` (no key, HTTP 401, server unreachable).
Exit code 0 when the key was accepted, 1 otherwise. Nothing about any file is sent. `--endpoint` tries another
stash-box server (StashDB only) for this run; the saved address is under Tools > API Keys.

| Option | Meaning |
| --- | --- |
| `--endpoint URL` | StashDB only: check another stash-box server's GraphQL address for this run. |

### fix-container

`videoredactor fix-container PATH... [--apply] [--log FILE]`

Finds files whose container is not what their extension says (an `.mp4` that is really an FLV: players report file
errors, the content is intact) and remuxes them losslessly into a real MP4/MKV, the same fix as Media > Check Files >
Remux to Real Container. It is a **dry run unless you add `--apply`**: it lists what would be remuxed and writes
nothing. A real run converts to a temp name, verifies it (real container, length, same tracks, a full decode with
zero errors), then swaps; the original is moved (never deleted) to a `remuxed originals <date>` folder (the quarantine
folder of Preferences > Library safety when one is set, else beside the file) and a UTF-8 TSV row is written. Files
modified in the last 10 minutes are skipped, an existing target is never overwritten, a file that would need a
re-encode is skipped. Exit code 1 when a file failed.

| Option | Meaning |
| --- | --- |
| `--apply` | Really remux. Without it nothing is changed or written. |
| `--log FILE` | With `--apply`: the TSV undo log to write (default: beside the quarantine folder). |

### undo-remux

`videoredactor undo-remux LOG.tsv`

Reads the log of a `fix-container --apply` (or the window's remux) and puts each original back; the remuxed copy is
moved to an `undone remux <date>` folder beside it, never deleted. An original whose name is taken again is left in
quarantine and reported. Undoing twice does nothing. Exit code 1 when something could not be put back.

### join-files

`videoredactor join-files PATH... [--apply] [--chapters] [--log FILE]`

Joins the parts of a movie that is split over several files (`Title cd1.mp4`, `Title cd2.mp4`; also `disc1`, `part 2`,
`pt1`) into one file, **without re-encoding**: the same as Media > Join Files. The files are grouped by folder and name
and ordered by part number; a hand-picked set of files with no part markers is one group in name order. A group is
joined only when its parts match (same container, video codec, size, pixel format and frame rate, the same audio tracks
and subtitle codecs); a group that differs, has a missing or repeated part number, or whose target name exists is
reported with the reason and left alone. It is a **dry run unless you add `--apply`**. A real run joins to a temp name,
verifies it (same tracks, a length equal to the parts' together, a full decode with zero errors, the poster and tags
read back), then swaps it in; the tags and poster come from the first part and the Scene # is cleared. Every part is
moved (never deleted) to a `joined originals <date>` folder (the quarantine folder of Preferences > Library safety when
one is set, else beside the file) and a UTF-8 TSV row is written per part. Parts modified in the last 10 minutes are
skipped. Exit code 1 when a group failed or could not be joined. Use `-R` / `--no-recurse` to look only at the files
directly in a folder.

| Option | Meaning |
| --- | --- |
| `--apply` | Really join. Without it nothing is changed or written. |
| `--chapters` | Add one chapter per part (Part 1, Part 2, ...) at the joins. |
| `--log FILE` | With `--apply`: the TSV undo log to write (default: beside the quarantine folder). |

### undo-join

`videoredactor undo-join LOG.tsv`

Reads the log of a `join-files --apply` (or the window's join) and puts each part back; the joined file is moved to an
`undone join <date>` folder beside it, never deleted. A part whose name is taken again is left in quarantine and
reported. Undoing twice does nothing. Exit code 1 when something could not be put back.

### set-language

`videoredactor set-language PATH... --audio LANG [--subtitle LANG] [--only-unlabelled] [--dry-run]`

Sets the language of the audio and/or subtitle tracks of MP4 and MKV files **in place**: nothing is re-encoded or
rewritten (an MP4's track language field is patched, an MKV is edited with mkvpropedit), every change is read back, and
the old languages go into Tools > History when History is on. It is the command-line side of Metadata > Set Track
Language and the fix for Check Files' "audio track(s) without a language". Codes are ISO 639-2/B three-letter codes
(`eng`, `nor`, `swe`, `ger`, `fre`, ...; `und` = undetermined). A read-only file is refused and listed; a failing file
does not stop the others. Exit code 1 when a file failed.

| Option | Meaning |
| --- | --- |
| `--audio LANG` | The language to give the audio tracks. |
| `--subtitle LANG` | The language to give the subtitle tracks. |
| `--only-unlabelled` | Change only the tracks that have no language yet (und); the others keep theirs. |
| `--dry-run` | List what would change; nothing is written. |

### JSON output

`--json` prints one document: `{"results": [...], <summary fields>, "warnings": [...], "errors": [...]}`. It is ASCII-only (a non-ASCII character in a path is a `\uXXXX` escape, which any JSON reader decodes). If a command fails or is interrupted after it started, the document is still printed, with what was done so far and an `error` entry, so a script reading `--output FILE` never finds an empty or half-written file.

| Command | Each entry in `results` | Summary fields |
| --- | --- | --- |
| `info` | `path`, `status`, `check`, `technical` (container, resolution, video_codec, audio_codec, bitrate, frame_rate, duration_seconds), `fields` (name to value) | `files`, `failed` |
| `set` | `path`, `status`, `changes` (field to `{old, new}`), `message` | `files`, `failed`, `dry_run` |
| `set-language` | `path`, `status`, `changes` (track, old, new), `message` | `files`, `failed`, `dry_run`, `tracks`, `changed_files` |
| `rename`, `move` | `path`, `status`, `new_path`, `message` | `files`, `failed`, `dry_run`, and `pattern` or `root` |
| `check` | `path`, `status`, `problem`, `findings` (code, severity, message), `declared_seconds`, `readable_seconds`, `repaired`, `stamped`, `message` | `files`, `problems`, `repair`, `stamp`, `dry_run` |
| `redact` | `file`, `path`, `status`, `applied`, `needs_review` (step, value, confidence, reason), `failures`, `notes`, `skipped`, `not_saved` | `files`, `failed`, `needs_review`, `cancelled`, `confidence_threshold`, `run_notes` |
| `lookup-check` | `service`, `ok`, `message` | `service`, `ok` |
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

What the commands will not do: overwrite a file or ask a question. Everything that could be a prompt in the
window is a flag here or a skipped file in the report. They do not delete anything for good either, with one
exception: `redact` with the convert step on and "Delete the original after a verified conversion" turned on in
Preferences > Library safety deletes each converted original (and the ingested `.srt` files), exactly as the
window does.

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
