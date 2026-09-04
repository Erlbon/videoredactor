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
  confirmed by hand; nothing is auto-applied.
- **Season & episode picker** for TV — after matching a show, pick the
  exact season and episode so per-episode titles and air dates land
  correctly, not just show-level metadata.
- **Subtitle fetching** via OpenSubtitles, hash-matched against the
  exact file first (guaranteed sync) with a title-search fallback that's
  clearly flagged as sync-not-guaranteed.
- **Remux to MP4** for MKV files (fast, lossless container swap, not a
  re-encode), with control over what happens to the original.
- **Poster art** saved as a sidecar image next to the video, the
  convention Plex/Jellyfin/Kodi already expect.
- **Thumbnail preview** — pulls a real frame from the video so you can
  see what you're tagging without opening a player.

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
