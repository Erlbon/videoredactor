# Building The Ʌideo Redactor

This must be built ON Windows -- PyInstaller builds for whatever
platform it runs on, and cannot cross-compile a Windows `.exe` from
Linux or macOS. Everything in this repo has been developed and
syntax/logic-checked in a Linux sandbox with no PyQt6, no MKVToolNix,
and no PyPI access, so **this build has not actually been run
end-to-end yet.** Treat the steps below as the documented, reviewed
procedure -- not a verified one -- and expect to debug real issues on
the first attempt.

## Prerequisites (install separately, not bundled)

1. **Python 3.11+** for Windows
2. **ffmpeg** -- https://ffmpeg.org/download.html -- must be on PATH
   (used for thumbnails and remux)
3. **MKVToolNix** -- https://mkvtoolnix.download/downloads.html -- must
   be on PATH (provides `mkvpropedit`, `mkvmerge`, and `mkvextract` --
   all three ship together in a standard MKVToolNix install -- used for
   all MKV
   metadata read/write)

The app checks for these at startup and prompts to install any that are
missing (with a direct download-page button per tool), matching the
epub tool's Calibre/Sigil pattern (v35) -- it does not block launch,
just warns, so features that don't need the missing tool still work.

## Build steps

**Easiest: run `build_exe.bat`** from the project root. It pins itself
to its own folder, checks Python is on PATH, upgrades pip, installs
dependencies, runs PyInstaller, and verifies the output exe actually
exists -- stopping with a clear message on the first failed step
rather than continuing into a broken build. Matches the reliability
pattern of the epub tool's own proven build script.

It deliberately does NOT bump the version number -- run
`python bump_version.py` yourself first if this build should carry a
new version label. Keeping that separate means a test/debug build
doesn't inflate the official version history every time you build.

Or run the steps manually:

```
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m PyInstaller videoredactor.spec --noconfirm
```

(`python -m pip` / `python -m PyInstaller` rather than the bare `pip`
and `pyinstaller` commands -- both route through whichever `python` you
already confirmed works, rather than depending on a possibly-different
Python's `pip`/`pyinstaller` being separately on PATH, which is a real
class of bug this project already hit once.)

The built exe lands at `dist/TheVideoRedactor.exe` -- a single
portable file, not a folder. (The spec's `EXE()` call includes
`a.binaries`/`a.zipfiles`/`a.datas` directly with no separate
`COLLECT()` step, which is PyInstaller's onefile pattern; this was
previously mis-documented here as a one-folder build, which was never
true of the spec as written -- see the changelog.)

## Status as of the last real Windows run

- **Confirmed working:** the app launches successfully with a real
  PyQt6 install and real Windows event loop -- this was previously an
  open question (development happened in a Linux sandbox with no
  PyQt6 at all) and is now resolved.
- **Found and fixed:** MKV files showed a raw, confusing
  `[WinError 2] The system cannot find the file specified` when
  MKVToolNix wasn't on PATH. Root cause was correct (tool genuinely
  missing) but the per-file error text didn't say so clearly. Fixed by
  checking tool availability before the subprocess call instead of
  parsing ambiguous exception text after the fact -- see the
  `2026-09-01#13` changelog entry.

## Known gaps as of this build setup

- **PyQt6 launch itself is confirmed working** (see Status above), but
  most individual dialogs/widgets still haven't been exercised
  end-to-end -- the note in `gui/tmdb_episode_picker_dialog.py` about
  an untested signal-ordering assumption (season dropdown auto-loading
  its episode list) is a good next thing to check specifically.
- **mutagen (MP4 backend) has never been confirmed against a real
  file** -- the MKV backend's first real bug is now fixed (see Status
  above), but MP4 reads/writes are still unverified. ffmpeg-backed
  features (thumbnails, remux, subtitle hashing) were tested for real
  during development.
- **TMDB and OpenSubtitles API clients have never received a real
  response** -- no network path to either service was available during
  development. Written against each service's documented API; the
  first real search/import attempt is the first real test.
- **No app icon embedded via a resource file for the Windows taskbar
  fallback** -- `--icon` on the EXE covers the exe's own icon, but if
  you want the icon to also show correctly in the Alt-Tab switcher
  before the main window paints, verify that separately; not something
  that could be checked without a real Windows+Qt session.

None of this means the code is wrong -- it means it hasn't had its
first real run yet. Budget time for that first run to surface a few
real bugs, the same way any first integration test does.
