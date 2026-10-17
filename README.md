# The Ʌideo Redactor (videoredactor)

A PyQt6 desktop tool for bulk-editing the tags of MP4 and MKV files, mp3tag-style, and for getting a video
library into MP4/MKV with ffmpeg. Part of the Redactor family; shared code comes from
[redactor_common](https://github.com/Erlbon/redactor_common), pinned by tag in `requirements.txt`.

- Edit tags of many files at once from the side panel. Filter the file list from the funnel in any column header (Content Type, Status, Filename, Year...): tick the values to show, or type a 'contains' text; filtered columns turn blue.
- Look up metadata on TMDB, TheTVDB or an offline IMDb database you build yourself; every match is confirmed by hand. The record's ids are kept inside the file (tags `TMDB_ID`, `IMDB_ID`, ... `METADATA_MATCH`) so the next lookup skips the search, and a true poster (portrait, from the source's poster pictures) is embedded in the video instead of saved beside it. Media > Embed Existing Poster Files moves old `-poster.jpg` files in.
- Translate a foreign subtitle to English, side by side (Media > Translate Subtitles to English; offline engine of your own: a LibreTranslate server or the argos-translate command, nothing bundled). The original stays, the translation is a second file <name>.en.srt, editable before it is written and never replacing an existing file. Media > Compare Subtitles shows any two subtitle tracks of a video side by side.
- Optional scene metadata from ThePornDB and StashDB (your own key under Tools > API Keys): the dialog asks for the exact file by its OSHASH fingerprint first (a file match is highlighted, a title match is flagged), then you pick, review each field and optionally embed the poster in the file. Only the hash and/or the search text leave your computer. `videoredactor lookup-check theporndb|stashdb` tests a key.
- Fetch subtitles from OpenSubtitles (hash match first; a title match is flagged as sync-not-guaranteed), or, for music videos, time-synced lyrics from LRCLIB (free, no key) turned into an .srt with an adjustable offset. The Redact subtitle step can use LRCLIB too (option off by default; it only saves a same-artist, same-title, same-length match, everything else is a suggestion).
- Music videos: the title is the song; the track title field is gone, old values are kept and can be moved into the title. MP4 music videos also carry the iTunes media type (`stik` 6; MKV has no equivalent and keeps the custom content-type tag only).
- Check files for damage and repair them losslessly (an optional **Deep scan** reads the whole file for zero-filled blocks inside the video, the pieces a torrent never delivered, and **Cut Out Damaged Parts** removes just those parts, losslessly, when the file can't be fetched again); a file whose **container does not match its extension** (an .mp4 that is really an FLV: players report file errors, the content is intact) is flagged WRONG CONTAINER and fixed by **Remux to Real Container**, a lossless, verified remux that moves the original to a quarantine folder and writes an undo log (`videoredactor fix-container` / `undo-remux` on the command line); a note about audio/subtitle tracks without a language is fixed by **Metadata > Set Track Language**; find duplicates; rename and move files by tag patterns.
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

## The Bitrate column's colours

Every file's Bitrate cell is tinted by its **video bits per pixel per frame** (the video stream's own bit rate, else the file's rate minus the audio), compared as H.264: HEVC counts for about 40 percent less (HEVC 0.6, AV1 0.5, VP9 0.65, older codecs more). **Yellow** (poor) is below 0.05 bpp, **green** is fine, **orange** is above the conversion limit of Media > Convert to MP4/MKV (Rules) for the file's resolution (the same function the rules use, so the table and the rules never disagree; movies are coloured too, but the rules skip them unless "apply to movies" is on, and the tooltip says so). No colour when the resolution, frame rate or bit rate is unknown (the tooltip says which). **Yellow means "check it", not "bad"**: animation and low-motion content compresses better. A failed file keeps its red tint. Hover a cell for the numbers ("1080p HEVC, 2.1 Mb/s video (0.04 bpp, HEVC-equivalent 0.07). Poor below 1.5 Mb/s for HEVC; good from about 3 Mb/s."). The two thresholds (poor 0.05, good 0.10, 0.01 to 0.5, poor below good) are in Tools > Preferences > Conversion > Table colours and in Export / Import Settings.

| H.264, 24 fps | 480p | 720p | 1080p | 4K |
| --- | --- | --- | --- | --- |
| poor below | about 0.5 Mb/s | about 1.1 | about 2.5 | about 10 |
| good from about | about 1 Mb/s | about 2.2 | about 5 | about 20 |

## Menus

| Menu | Items |
| --- | --- |
| File | Open Files (Ctrl+O), Open Folder (Ctrl+Shift+O), Include Subfolders (also a checkbox on the toolbar next to Open Folder), Import and Convert; Save All (Ctrl+Shift+A); Rename File (F2), Undo Last Rename, Rename / Export / Move (Ctrl+E); Export Settings, Import Settings; Remove from List (Del), Clear List; Exit |
| Edit | Undo (Ctrl+Z), Redo (Ctrl+Y); Apply to N Selected (Ctrl+Return); Redact (Ctrl+Shift+E), Edit Redact Recipe; Search and Replace (Ctrl+H), Change Case, Auto-Number, Clear Fields (empties descriptive fields, source ids and the embedded poster of the selected files; staged and undoable until Save; the content type only if ticked) |
| View | Show Metadata Panel; Zoom In / Out / Reset (Ctrl++, Ctrl+-, Ctrl+0); Clear All Filters; Refresh List (F5, Ctrl+R); Command Palette (Ctrl+K) |
| Metadata | Parse Filename (Ctrl+I); Look Up: TMDB (Movie) (Ctrl+M), TMDB (TV Show) (Ctrl+T), TheTVDB (TV Show) (Ctrl+Shift+T), IMDb (Local Database), ThePornDB, StashDB, Subtitles (OpenSubtitles, Lyrics) (Ctrl+Shift+L), Get Lyrics (LRCLIB) (Ctrl+Shift+G, one Music Video only); Number Episodes; **Set Track Language** (audio/subtitle language tags of MP4 and MKV, written in place without re-encoding; `videoredactor set-language` on the command line; a "Play this track" button listens to a track first, and the Player has an audio-track selector, A key) |
| Media | Play (Ctrl+Shift+P, also a toolbar button and a large button under the side-panel thumbnail), Remux to MP4 (Ctrl+Shift+M), Convert to MP4 (H.264) (Ctrl+Shift+C), Convert to MP4/MKV (Rules); Check Files (with the optional Deep scan; Cut Out Damaged Parts for files it finds damaged; Remux to Real Container for files with a wrong container), Find Duplicates |
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
| Fill from ThePornDB/StashDB (file match only; always a suggestion for review, never written by itself) | off |
| Download a missing subtitle (OpenSubtitles, LRCLIB lyrics) | off |
| Translate non-English subtitles of music videos to English (needs a translation engine; the result is only a suggestion) | off |
| Remux MKV to MP4 (lossless) | off |
| Convert to MP4/MKV and ingest subtitles | off |
| Rename by pattern | off |
| Move into folders under the library root | off |

Rename and Move run last, after the save. When the convert step is on, every Redact run asks "Dry run the
conversion step" in its confirm question (ticked by default: the report then lists what would happen and nothing
is converted). On the command line, `redact --convert-dry-run` does the same; without it the conversion is real.

## Change history (Tools > History)

Opt-in, off by default: turn it on in Tools > Preferences > History (or say Yes when Tools > History asks), and
switch it off there again; "Delete History..." moves what was recorded to the Recycle Bin (after asking). While it is on, every tag change the
app makes to a file (Save, Save All, Redact, a Restore, and the command line's `set`, which follows the same
setting) is recorded with the old and new text of each changed field in `videoredactor_journal.jsonl`, next to
`videoredactor_settings.ini`. Tools > History lists them, newest first, and "Restore Old Values..." writes the old
values back with the app's own save (write, re-read, compare), so an unwanted save can be undone after the app was
closed. Covers and other images are not kept; a file replaced by a conversion is in the Recycle Bin or quarantine
folder instead. The newest 50,000 changes from the last 90 days are kept. A renamed, moved or converted file takes
its history with it (Rename File, Rename / Export / Move, Undo Last Rename, Redact's rename and move steps, the
command line's `rename` and `move`, Remux to MP4 when the original is recycled, the conversion rules). Export /
Import Settings carries the on/off switch; importing a bundle with history on turns recording on.

## Preferences (Tools > Preferences)

| Page | What it holds |
| --- | --- |
| Filenames | ASCII-safe names, zero-padding of numbers, Auto-Number padding |
| Transcode | CRF, audio bitrate and threads of Media > Convert to MP4 (H.264) only |
| Duplicates | The starting sensitivity of Find Duplicates |
| History | The opt-in switch of Tools > History (off by default) and Delete History... (to the Recycle Bin, after asking; acts at once) |
| Tools / Paths | ffmpeg and MKVToolNix locations, the IMDb database and dataset files (saved as you type) |
| Conversion | The conversion rules: bitrate and size limits for series episodes (and optionally movies), scaling, the HEVC encoder (Automatic: NVIDIA hevc_nvenc only when a test encode works here, else libx265 CRF 27 in software), NVENC quality, libx265 quality, NVIDIA GPU decode/scale (off by default), the size ratio a re-encode must beat, when to use MKV (default: 4 or more subtitle tracks), disc images (default: left alone, never converted), subtitle languages to ingest |
| Library safety | Quarantine folder and undo log folder (optional; without one, replaced files go to the Recycle Bin), delete-after-conversion (off by default, cannot be undone), the recent-file guard, and the duration tolerance a conversion result must meet |

Every page is written on OK or Apply and dropped on Cancel, except Tools / Paths.

## Command line

The same exe is also a command line (`videoredactor info|set|rename|move|check|redact ...`); see the
"Command line" section of [ABOUT.md](ABOUT.md).

## Licence

GPL v3 or later; see [LICENSE](LICENSE) and [CREDITS.md](CREDITS.md).
