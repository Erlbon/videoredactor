# The Ʌideo Redactor (videoredactor)

A PyQt6 desktop tool for bulk-editing the tags of MP4 and MKV files, mp3tag-style, and for getting a video
library into MP4/MKV with ffmpeg. Part of the Redactor family; shared code comes from
[redactor_common](https://github.com/Erlbon/redactor_common), pinned by tag in `requirements.txt`.

- Edit tags of many files at once from the side panel, filtered by Content Type (Movie, TV, Music Video, Clip, Misc).
- Look up metadata on TMDB, TheTVDB or an offline IMDb database you build yourself; every match is confirmed by hand.
- Fetch subtitles from OpenSubtitles (hash match first; a title match is flagged as sync-not-guaranteed).
- Check files for damage and repair them losslessly; find duplicates; rename and move files by tag patterns.
- Redact: one click runs a saved recipe of steps on the selected (or all loaded) files.
- Convert other formats (AVI, WMV, MOV, FLV, MPG, WebM...) to MP4 or MKV with conversion rules that copy streams
  where they can, verify every result and keep the original in the Recycle Bin or a quarantine folder.

More detail, the command line, and the licences of the data sources: [ABOUT.md](ABOUT.md). History:
[CHANGELOG.md](CHANGELOG.md).

## Install, run, build

- External tools, installed separately: **ffmpeg/ffprobe** (thumbnails, probing, remux, conversion) and
  **MKVToolNix** (`mkvpropedit`, `mkvmerge`, `mkvextract`, for MKV tags). The app finds them on PATH, in a bundled
  `tools/` folder, in their default install folder, or where Tools > External Tools points; a missing tool is
  reported at launch but does not block it.
- From source: `pip install -r requirements.txt`, then `python main.py`.
- Tests: `python -m pytest -q`.
- Windows exe: `build_exe.bat` (PyInstaller, `videoredactor.spec`); see [BUILD.md](BUILD.md). It must be built on
  Windows. `python bump_version.py` sets the version first.

## Menus

| Menu | Items |
| --- | --- |
| File | Open Files (Ctrl+O), Open Folder (Ctrl+Shift+O), Include Subfolders, Import and Convert; Save All (Ctrl+Shift+A); Rename File (F2), Undo Last Rename, Rename / Export / Move (Ctrl+E); Export Settings, Import Settings; Remove from List (Del), Clear List; Exit |
| Edit | Undo (Ctrl+Z), Redo (Ctrl+Y); Apply to N Selected (Ctrl+Return); Redact (Ctrl+Shift+E), Edit Redact Recipe; Search and Replace (Ctrl+H), Change Case, Auto-Number |
| View | Show Metadata Panel; Zoom In / Out / Reset (Ctrl++, Ctrl+-, Ctrl+0); Refresh List (F5, Ctrl+R); Command Palette (Ctrl+K) |
| Metadata | Parse Filename (Ctrl+I); Look Up: TMDB (Movie) (Ctrl+M), TMDB (TV Show) (Ctrl+T), TheTVDB (TV Show) (Ctrl+Shift+T), IMDb (Local Database), Subtitles (OpenSubtitles) (Ctrl+Shift+L); Number Episodes |
| Media | Remux to MP4 (Ctrl+Shift+M), Convert to MP4 (H.264) (Ctrl+Shift+C), Convert to MP4/MKV (Rules); Check Files, Find Duplicates |
| Tools | Preferences (Ctrl+,); API Keys, External Tools, IMDb Database; Columns, Genres, Languages; History |
| Help | Changelog, Credits, About |

Open Folder loads MP4 and MKV files (`.mp4`, `.m4v`, `.mkv`). When the folder also holds other video formats it
offers, in one dialog with a "Dry run first" checkbox, to convert them with the conversion rules. Media > Remux to
MP4 is a lossless MKV-to-MP4 container swap; Media > Convert to MP4 (H.264) is a real H.264/AAC re-encode that
never deletes the original; Media > Convert to MP4/MKV (Rules) and File > Import and Convert use the conversion
rules. Anything that takes more than a moment (loading, probing, saving, conversions, lookups, renames) runs under
a progress dialog.

## Redact steps

Edit > Edit Redact Recipe chooses the steps and their order; Edit > Redact runs them. Each changed file is saved
in place and its original goes to the Recycle Bin.

| Step | Default |
| --- | --- |
| Check and repair (lossless) | on |
| Fill empty tags from the filename | on |
| Fill empty tags from the folder path | on |
| Look up metadata (IMDb, TMDB, TheTVDB) | on |
| Download a missing subtitle (OpenSubtitles) | off |
| Remux MKV to MP4 (lossless) | off |
| Convert to MP4/MKV and ingest subtitles | off |
| Rename by pattern | off |
| Move into folders under the library root | off |

Rename and Move run last, after the save. When the convert step is on, every Redact run asks "Dry run the
conversion step" in its confirm question (ticked by default: the report then lists what would happen and nothing
is converted). On the command line, `redact --convert-dry-run` does the same; without it the conversion is real.

## Change history (Tools > History)

Opt-in, off by default: turn it on in Tools > Preferences > History (or say Yes when Tools > History asks), and
switch it off there again; "Delete History..." removes what was recorded. While it is on, every tag change the
app makes to a file (Save, Save All, Redact, a Restore, and the command line's `set`, which follows the same
setting) is recorded with the old and new text of each changed field in `videoredactor_journal.jsonl`, next to
`videoredactor_settings.ini`. Tools > History lists them, newest first, and "Restore Old Values..." writes the old
values back with the app's own save (write, re-read, compare), so an unwanted save can be undone after the app was
closed. Covers and other images are not kept; a file replaced by a conversion is in the Recycle Bin or quarantine
folder instead. The newest 50,000 changes from the last 90 days are kept.

## Preferences (Tools > Preferences)

| Page | What it holds |
| --- | --- |
| Filenames | ASCII-safe names, zero-padding of numbers, Auto-Number padding |
| Transcode | CRF, audio bitrate and threads of Media > Convert to MP4 (H.264) only |
| Duplicates | The starting sensitivity of Find Duplicates |
| Tools / Paths | ffmpeg and MKVToolNix locations, the IMDb database and dataset files (saved as you type) |
| Conversion | The conversion rules: bitrate and size limits for series episodes (and optionally movies), scaling, NVENC quality, the size ratio a re-encode must beat, when to use MKV (default: 4 or more subtitle tracks), disc images (default: left alone, never converted), subtitle languages to ingest |
| Library safety | Quarantine folder and undo log folder (optional; without one, replaced files go to the Recycle Bin), delete-after-conversion (off by default, cannot be undone), the recent-file guard, and the duration tolerance a conversion result must meet |

Every page is written on OK or Apply and dropped on Cancel, except Tools / Paths.

## Command line

The same exe is also a command line (`videoredactor info|set|rename|move|check|redact ...`); see the
"Command line" section of [ABOUT.md](ABOUT.md).

## Licence

GPL v3 or later; see [LICENSE](LICENSE) and [CREDITS.md](CREDITS.md).
