# Credits

The Ʌideo Redactor is built on the work of a number of other projects.

## Shared foundation

- **[redactor_common](https://github.com/erlbon/redactor_common)** — the
  UI/logic package shared with its sibling tools (epub, mp3). See its
  own version line above for which build is vendored here.

## Data

- **IMDb** — the optional offline lookup database is built from IMDb's
  free non-commercial datasets (personal use only; never bundled or
  redistributed). Information courtesy of IMDb (https://www.imdb.com). Used with permission.

## Libraries

- **[PyQt6](https://www.riverbankcomputing.com/software/pyqt/)** — the
  application framework the whole GUI is built on.
- **[Mutagen](https://mutagen.readthedocs.io/)** — reading and writing
  MP4 metadata tags.

## External tools

Not bundled — installed separately, and only used if present on the
system:

- **[FFmpeg](https://ffmpeg.org/)** — thumbnails, remuxing, and format
  probing (`ffmpeg`/`ffprobe`).
- **[MKVToolNix](https://mkvtoolnix.download/)** — Matroska metadata
  editing (`mkvpropedit`, `mkvmerge`).

## APIs

- **[The Movie Database (TMDB)](https://www.themoviedb.org/)** — movie
  metadata lookup. This application uses TMDB and the TMDB APIs but is not endorsed, certified, or otherwise approved by TMDB.
  The TMDB logo shown in the lookup dialogs and under this text is
  TMDB's official "blue short" logo, unmodified (only scaled), from
  https://www.themoviedb.org/about/logos-attribution (downloaded
  2026-10-01 as `assets/tmdb-logo.svg`).
- **[TheTVDB](https://thetvdb.com/)** — TV episode metadata lookup.
  Metadata provided by TheTVDB — please consider contributing missing
  information there, or supporting them directly.
- **[OpenSubtitles](https://www.opensubtitles.com/)** — subtitle
  search and download.
