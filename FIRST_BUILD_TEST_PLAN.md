# First Real Build — Test Plan

Every item below corresponds to a specific `NOTE:` left in the code
during development, marking something written against documentation or
reviewed by eye but never run for real (no PyQt6, no MKVToolNix, and no
network access to TMDB/OpenSubtitles were available in the sandbox this
was built in). This is not a guess at what might be broken — it's the
literal list of things that have never been executed.

**Update:** Stage 0 is confirmed passing on a real Windows run as of
`2026-09-01#13` — the app launches and the MKV-missing-tool warning
correctly fired and was fixed (see CHANGELOG.md). Stages 1+ are still
open.

Work through it in this order: each stage depends on the previous one
actually working, so there's no point testing TMDB import before the
window itself opens.

## Stage 0 — Does it launch at all? ✅ CONFIRMED

- [x] `pip install -r requirements.txt` completes without error
- [x] `python main.py` opens a window
- [ ] Window title shows `The Ʌideo Redactor (v0.1)` (unconfirmed —
      worth a quick visual check, but low risk)
- [ ] Window icon appears in the taskbar (the turned-V glyph)
- [x] Missing-tool warning dialog correctly appeared for MKVToolNix

**If this stage fails:** nothing below can be meaningfully tested yet.
Fix the PyQt6/launch issue first.

## Stage 1 — File loading & the table

- [ ] Open Folder against a real folder containing at least one MP4 and
      one MKV
- [x] MKV files load correctly once MKVToolNix is installed (confirmed
      via the WinError2 bug fix — the underlying read now needs
      re-verification with the tool actually present, since the bug
      report only confirmed the error-message path, not a successful
      MKV read)
- [ ] Both files appear as rows; Status column shows OK for files that
      loaded cleanly
- [ ] Deliberately point it at a folder containing a corrupt/non-video
      file with a `.mp4` extension — confirm it shows LOAD ERROR with a
      tooltip, not a crash
- [ ] Content Type filter dropdown changes visible columns correctly;
      "All (no filter)" shows every field

## Stage 2 — MP4 backend (mutagen) — never run for real

**Update:** as of the entry below, "Status returns to OK" after Save
now means something stronger than it used to -- Save immediately
re-reads the file and verifies the write actually took effect, only
reporting OK if it did. If Status shows SAVE FAILED with a message
naming a specific field and mismatched values, that IS the bug report
now -- copy that exact message, it's much more actionable than "tags
don't stick."

- [ ] Select an MP4 file, type into Title/Description/Genre in the
      bulk-edit panel, click Apply to Selected
- [ ] Status column shows UNSAVED (yellow tint)
- [ ] Save Selected — confirm no exception, Status returns to OK (and
      if it doesn't, read the SAVE FAILED tooltip carefully -- it now
      names the specific field and what was expected vs. what the file
      actually contained)
- [ ] **Close and reopen the file in the app** (or check with a
      separate tool like MediaInfo) to confirm the tags actually
      persisted to the MP4 file, not just the in-memory state -- this
      should now be redundant with what Save's own verification just
      checked, but is still worth confirming independently once
- [ ] Check `core/mp4_backend.py`'s custom-atom fields specifically
      (Sort Title, Director, Cast, Content Type) — these use the
      `----:com.videoredactor:` freeform atom prefix, which is the
      part most likely to have a subtle bug since it was never
      round-tripped through a real MP4 file. **A fix is already
      applied here, but genuinely unverified against real mutagen**:
      the original code used a bare `bytes` object for these atoms;
      mutagen's documented pattern wraps the value in
      `MP4FreeForm(bytes, dataformat=...)` instead, which is what the
      code now does. This is the current leading theory for why only
      Title (a native text atom, unaffected) persisted while every
      custom field silently didn't -- but be aware the test coverage
      for this fix has a real limitation: without real mutagen
      installed, the tests can only confirm the code NOW uses
      `MP4FreeForm` and that this project's own read/write functions
      agree with each other -- they cannot confirm real mutagen
      actually serializes an `MP4FreeForm`-wrapped value any
      differently than a bare-bytes one would have. If custom fields
      STILL don't persist after this, the `MP4FreeForm` theory was
      wrong and the verify-after-write safety net above should at
      least surface the failure clearly rather than silently.

## Stage 3 — MKV backend (mkvpropedit/mkvmerge/mkvextract) — real bugs found and fixed FOUR times

- [x] **First real user report**: the original `--tags global:KEY=value`
      syntax was invalid mkvpropedit usage entirely, and the original
      `mkvmerge -J`-based tag reading couldn't expose custom tag content
      at all. Both rewritten to use a proper Matroska Tags XML file.
- [x] **Second real user report ("no tags are written to MKV files")**:
      the XML-file rewrite that fixed the first bug used
      `--tags all:<file>` as the target selector, changed to
      `--tags global:<file>` (matching `build_tags_xml()`'s
      `<Targets>`-less, whole-file scope). Necessary but not sufficient.
- [x] **Third real user report, confirmed via the app's own
      verify-after-write safety net**: content_type was set to TV, but
      the file read back UNSET, even with the `global` fix applied.
      Split the combined mkvpropedit invocation into two separate
      calls, and made `_verify_write()` report every mismatched field
      together instead of stopping at the first.
- [x] **Fourth real user report -- and the most informative one yet**,
      because it's the one where the diagnostic enrichment from the
      third fix actually paid off: the save_error's `tool output:`
      section showed mkvpropedit's own text, "The file is being
      analyzed. / The changes are written to the file. / Done." --
      mkvpropedit's OWN confirmation that the write genuinely
      succeeded. This flips the entire investigation: the bug was
      never in the write path. It's in the READ path -- specifically,
      `mkvextract`'s stderr was being silently discarded completely
      (never even looked at), and the code was relying on an unverified
      assumption that `mkvextract <file> tags` (no output argument)
      defaults to printing XML on stdout.

      Two changes made in response:
      1. `read_mkv_metadata()` now asks mkvextract to write to an
         explicit temp output file (`mkvextract <file> tags <output-path>`)
         instead of relying on an unconfirmed stdout
         default -- removes that specific ambiguity entirely,
         regardless of which convention mkvextract's tags mode
         actually follows. Falls back to reading stdout if the
         expected output file was never created, in case THIS
         assumption is also wrong.
      2. `read_mkv_metadata()` gained a `diagnostics` parameter that
         captures mkvmerge's and mkvextract's raw stderr (previously
         thrown away entirely), and `VideoFile._verify_write()` now
         includes it in the save_error under `read-back tool output:`
         when present.

      **This is now, again, the most important thing to verify --
      but this time, if it's STILL wrong, the fix itself doesn't
      matter as much as the fact that you'll finally see mkvextract's
      own error text.** Read the full save_error message. If there's
      a `read-back tool output:` section, whatever it says is very
      likely the actual, exact root cause -- copy it verbatim, don't
      paraphrase it. Four rounds of "the write/read appears to
      succeed but isn't" reports have each been narrowed down by
      exactly this kind of raw tool output; this is the first one
      where BOTH the write confirmation and (hopefully) the read
      error text will be visible in the same report.
- [ ] Same edit-and-save cycle as Stage 2, but on an MKV file — confirm
      the fix: Title should write via the native Segment Info property
      (visible as the file's title in most players), everything else
      via the Tags XML mechanism
- [ ] Confirm reading a file back after save shows the same values that
      were saved — this exercises the newly-rewritten `mkvextract`
      explicit-output-file read path
- [ ] Try a field value containing an ampersand, quote, or angle
      bracket (e.g. a comment like `Rock & Roll "classic"`) — this is
      exactly the case the original hand-rolled string approach would
      have corrupted; the new ElementTree-based XML builder should
      handle it correctly (verified in isolation by tests, but worth
      confirming against a real mkvpropedit/mkvextract round-trip too)
- [ ] Confirm mkvpropedit doesn't pop a console window during save
      (the `CREATE_NO_WINDOW` flag was set proactively based on the
      epub tool's v35 fix for Calibre, but never verified against a
      real mkvpropedit invocation)
- [ ] If MKVToolNix is installed somewhere not on PATH, confirm
      Settings → Locate External Tools lets you point at all three
      executables (`mkvpropedit`, `mkvmerge`, `mkvextract` — note
      `mkvextract` is a new addition, added alongside this fix)

## Stage 4 — Thumbnail preview

Already tested for real during development (ffmpeg was available in
the build sandbox) — should just work, but worth a quick sanity check:

- [ ] Selecting a file shows a real video frame, not a placeholder
- [ ] Selecting a corrupt/unreadable file shows "No preview" rather
      than an error dialog

## Stage 5 — TMDB import — never received a real API response, now also batch-capable (never run at all)

**Update:** TMDB import used to be single-file only; it's now batch-capable across a whole selection, added in direct response to a real request. This is entirely new, entirely untested code on top of the existing untested-API-response caveat -- test both together.

- [ ] Get a real TMDB API key, set `TMDB_API_KEY` env var (or add to
      `settings.ini` under `[tmdb] api_key`)
- [ ] **Movie mode, single file** (the original working path): Operations
      → Import Metadata from TMDB (Movie) on one real movie file —
      confirm the search dialog returns real results, fields populate
      correctly, poster downloads and saves as `<filename>-poster.jpg`
- [ ] **Movie mode, multiple files**: select 2-3 different movie files
      at once, run the same action — confirm a SEPARATE search+picker
      dialog appears for EACH file in turn (not one dialog applied to
      all), each showing that file's own filename as the initial
      search guess. Cancel one file's dialog partway through the batch
      — confirm it's skipped and the next file's dialog still appears
      (the loop shouldn't abort on one cancellation)
- [ ] Confirm the end-of-batch status bar message and (if any file
      failed) the summary warning dialog correctly reflect what
      actually happened — imported count, skipped count, failed count
- [ ] **TV mode, single file** (the original working path): confirm the
      **season dropdown auto-loads its episode list on dialog open**
      without needing to manually re-select the first season. This is
      flagged specifically in `gui/tmdb_episode_picker_dialog.py` as an
      untested assumption about Qt's `currentIndexChanged` signal
      firing during `addItem()` calls — if the episode list is empty
      until you manually change the season selection, this is the bug
- [ ] **TV mode, multiple files (the new batch path)**: select several
      episode files belonging to the SAME show, run Import Metadata
      from TMDB (TV Show) — confirm the show search+picker dialog
      appears only ONCE for the whole batch (not once per file), and
      that show-level fields (network, genre, poster) apply to EVERY
      selected file immediately after picking the show
- [ ] Confirm the episode picker then opens once per file, in
      sequence, letting you pick each file's specific episode — cancel
      one file's episode picker partway through and confirm that file
      keeps its show-level-only data while the loop continues to the
      next file's episode picker (not aborting the whole remaining
      batch)
- [ ] Confirm the final status bar message names the matched show and
      reports both the file count and the episode-match count
      correctly

## Stage 5b — TheTVDB import — never received a real API response

Same untested-live caveat as Stage 5, plus one more: TheTVDB requires a
login step (API key → JWT token) before any other endpoint responds,
which this project has never actually exercised against a real server.

- [ ] Get a real TheTVDB API key, set `TVDB_API_KEY` env var (or add to
      `settings.ini` under `[tvdb] api_key`)
- [ ] Import → Import Metadata from TheTVDB (TV Show) on a real
      show file — confirm login succeeds (no "TheTVDB rejected the API
      key" error) and search returns real results
- [ ] Pick a result, confirm show-level fields populate correctly —
      first real test of `get_series_details()`'s JSON parsing
      (genre/network extraction specifically, and whether TheTVDB's
      actual response shape for `originalNetwork`/`genres` matches
      what the code assumes)
- [ ] Confirm the poster downloads correctly — TheTVDB returns a full,
      ready-to-use image URL directly (unlike TMDB's relative-path
      scheme), so this is a simpler path than TMDB's poster download
      but still unverified against a real URL
- [ ] Confirm the season/episode picker populates correctly — unlike
      TMDB's picker, this one fetches ALL episodes once and filters
      client-side rather than calling out per season; if the season
      dropdown is empty or episodes don't appear, check
      `get_series_episodes()`'s JSON parsing first
- [ ] **If the show has more episodes than fit on one API page**,
      confirm the episode list isn't silently truncated — flagged
      explicitly in `core/tvdb_client.py`'s `get_series_episodes()`
      docstring as a known gap (only the first page is fetched; no
      pagination handling exists yet)
- [ ] Cancel the episode picker partway through — same show-level-only
      partial-import behavior as the TMDB picker should apply here too

## Stage 6 — Remux

- [ ] Select an MKV, Operations → Remux Selected to MP4
- [ ] Confirm the "Delete Original?" dialog appears with working
      Yes/No/Yes to All/No to All buttons
- [ ] Confirm the new MP4 is auto-added to the table and loads cleanly
- [ ] Try remuxing when a same-named MP4 already exists — confirm it's
      skipped (not overwritten) and reported in the status message

## Stage 7 — Subtitles — never received a real API response

- [ ] Get a real OpenSubtitles API key, set `OPENSUBTITLES_API_KEY`
- [ ] Import → Import Subtitles from OpenSubtitles on a well-known
      movie file — confirm hash search returns a genuine
      `[EXACT MATCH]` result if one exists for your exact file
- [ ] Try a file unlikely to have a hash match (e.g. something
      self-recorded) — confirm it falls through to the title-search box
      with results tinted and labeled `[sync not guaranteed]`
- [ ] Pick a non-hash-matched result — confirm the extra "sync not
      guaranteed, use anyway?" confirmation appears before it saves
- [ ] Confirm the `.srt` sidecar saves with the correct language code
      in the filename

## Stage 8 — Placeholder reference list + save progress dialog (new, never run)

Both requested directly; neither has any core/ logic to unit-test
(pure GUI wiring, same standing limitation as every other PyQt6-
touching file in this project) beyond the one real bug the placeholder
list build uncovered -- see the note below.

- [ ] Operations → Rename/Export by Pattern (or Import → Import
      Metadata from Filename) on any selection — confirm a reference
      list of every `%field%` code appears alongside the pattern
      field, not hidden behind a scroll or a separate window
- [ ] Confirm the reference list's field order matches the bulk-edit
      panel's own order (content_type first) — this is directly tied
      to a real bug found and fixed while building this: `EDITABLE_FIELDS`
      (`core/video_metadata.py`) had silently kept its old title-first
      order even after the panel itself was reordered to lead with
      content_type, so `_verify_write()`'s mismatch-check order was
      ALSO wrong until this fix landed, not just this new dialog's
      display order. If the reference list doesn't show content_type
      first, that fix didn't take
- [ ] Double-click an entry in the reference list — confirm
      `%field_name%` is inserted into the pattern field at the current
      cursor position (not always at the start or end), and that the
      cursor lands right after the inserted text so typing continues
      naturally
- [ ] Select enough files that a Save Selected/Save All takes a
      noticeable moment — confirm a progress dialog appears (title
      "Saving", current filename shown) rather than the window looking
      frozen. Same `setMinimumDuration` threshold as the folder-loading
      dialog: a quick single-file save should show nothing extra, only
      a batch that's actually slow enough to matter
- [ ] Cancel the save progress dialog partway through — confirm the
      status bar correctly reports how many files were saved before
      cancellation and how many were never attempted, and that already-
      saved files aren't re-attempted or corrupted by the cancellation

## Stage 9 — Settings menu restructure (renamed from Tools, new items, never run)

Pure menu/dialog reorganization, requested directly. No new core/
logic beyond the API key save/load, which is already covered by
existing settings.ini round-trip tests (`tests/test_config.py`) since
`ApiKeysDialog` just calls the same `get_setting`/`set_setting`
functions those tests already exercise.

- [ ] Confirm the menu bar now shows **Settings** where **Tools** used
      to be, and that it contains, in order: Locate External Tools...,
      Add External APIs..., a separator, Add/Remove Columns...,
      Add/Remove Languages..., Add/Remove Genres...
- [ ] Confirm **About** is NOT in this Settings menu at all — it should
      only appear under Help, exactly as before this change (it was
      already a standalone Help action; the thing that got removed was
      a REDUNDANT About tab inside the old Settings dialog wrapper,
      which no longer exists)
- [ ] Settings → Add External APIs... — enter a TMDB key, click Save,
      reopen the dialog, confirm the key you entered is still there
      (this is the first real exercise of `ApiKeysDialog` specifically,
      though the underlying settings.ini read/write it uses is already
      tested)
- [ ] With `TMDB_API_KEY` set as an environment variable, open Add
      External APIs — confirm the priority note appears under that
      field ("...environment variable is currently set and takes
      priority...")
- [ ] Confirm Settings → Add/Remove Columns... / Languages... /
      Genres... each still behave exactly as before (they're the same
      dialogs, just reached directly from the menu now instead of
      through the old tabbed wrapper) — column/panel refresh on
      change, genre/language panel refresh on change

## Stage 10 — Apply button moved to the toolbar (new, never run)

Requested UI relocation: Apply was previously a plain button at the
bottom of the bulk-edit panel; it's now in the main toolbar, directly
beside Save Selected/Save All. Pure GUI wiring, no new core/ logic.

- [ ] Confirm the panel itself no longer has an Apply button anywhere
      in its own layout
- [ ] Confirm the toolbar now reads: Open Folder, (separator), Apply
      to Selected, Save Selected, Save All Changed
- [ ] Select a file, type into a field, click the toolbar's Apply --
      confirm it behaves identically to the old panel button (Status
      shows UNSAVED, the change is staged onto the file's metadata)
- [ ] Click the toolbar Apply button with NOTHING selected, or with a
      selection where nothing's been typed/changed -- confirm it does
      nothing (no files get marked UNSAVED). This exercises a real bug
      fix made alongside the move: `_on_apply_to_selected()` in
      `gui/main_window.py` previously marked every selected file dirty
      unconditionally, even when the incoming `changed_fields` dict was
      completely empty -- meaning clicking Apply with nothing pending
      used to falsely flag files as having unsaved changes. Fixed with
      an early return on an empty dict. Neither this fix nor the button
      relocation itself has an automated test (both are GUI-layer
      behavior in `gui/main_window.py`, unexecutable in this
      development sandbox like every other PyQt6-touching file in this
      project) -- this manual check is the only verification either has
      had so far
- [ ] Clear a previously-set field's text (making it empty but still
      *touched*) and click Apply -- confirm this still correctly
      applies the empty value (clears the field on the selected
      file's metadata), distinguishing "nothing was touched" from "a
      touched field's new value happens to be empty" -- these must NOT
      be treated the same by the fix above

## Stage 11 — Menu restructure (Import/Operations split), renamed commands, OpenSubtitles key, keyboard shortcuts (new, never run)

All requested directly. Pure GUI reorganization plus one new field in
an already-tested settings.ini pattern -- no new core/ logic beyond
`ApiKeysDialog` now also saving `[opensubtitles] api_key`, which uses
the exact same `get_setting`/`set_setting` calls already covered by
`tests/test_config.py`.

- [ ] Confirm the menu bar reads: File, **Import**, **Operations**,
      Settings, Help — in that order
- [ ] Confirm **Import** contains, in order: Import Metadata from TMDB
      (Movie)..., Import Metadata from TMDB (TV Show)..., Import
      Metadata from TheTVDB (TV Show)..., a separator, Import Metadata
      from Filename... (renamed from Parse Filename to Metadata),
      Import Subtitles from OpenSubtitles... (renamed from Fetch
      Subtitles)
- [ ] Confirm **Operations** contains, in order: Remux Selected to
      MP4..., Rename/Export by Pattern... — and nothing else (these
      are the two items moved OUT of the old combined Operations menu)
- [ ] Confirm the dialogs opened by the two renamed commands show
      matching titles in their own title bars (not just the menu
      label) — "Import Metadata from Filename (N file(s))" and "Import
      Subtitles from OpenSubtitles"
- [ ] Settings → Add External APIs... — confirm a third field for
      OpenSubtitles now appears alongside TMDB and TVDB, saves and
      reloads correctly, and shows the environment-variable-priority
      note if `OPENSUBTITLES_API_KEY` is set
- [ ] Confirm every new keyboard shortcut actually works and doesn't
      conflict with anything: Ctrl+Return (Apply), Ctrl+M (TMDB
      Movie), Ctrl+T (TMDB TV), Ctrl+Shift+T (TVDB TV), Ctrl+Shift+F
      (Import Metadata from Filename), Ctrl+Shift+O (Import
      Subtitles), Ctrl+R (Remux), Ctrl+Shift+R (Rename/Export by
      Pattern) — none of these were exercised against a real running
      app, only checked for textual duplication against each other and
      the pre-existing File-menu shortcuts (Ctrl+O, Ctrl+S,
      Ctrl+Shift+S, Quit) during development
- [ ] Specifically confirm Ctrl+Return doesn't do anything unexpected
      while focus is inside a multi-line text field (Description/
      Comment) — it's not a standard Qt text-editing shortcut so it
      shouldn't, but this is exactly the kind of interaction that's
      easy to get wrong in ways static review can't catch

## Stage 12 — Case Conversion, Search/Replace, Auto-Numbering (new, never run)

Three new Operations commands, requested directly. The actual
transformation logic (`core/text_transforms.py`) is thoroughly unit-
tested (33 tests -- case conversion including the colon/dash subtitle
capitalization rule and the apostrophe-mangling bug it deliberately
avoids, search/replace including regex-special-character safety, and
number generation including negative-number padding), but the three
dialogs wiring that logic to the GUI have never run against real
PyQt6, like every other dialog in this project.

- [ ] Operations → Case Conversion... on a selection with a populated
      Title field — confirm the preview table updates live as you
      change the field dropdown or the case mode, and that switching
      to a genuinely different field/mode doesn't leave a stale
      preview from the previous choice
- [ ] Try Title Case specifically on something like "star wars: a new
      hope" — confirm it becomes "Star Wars: A New Hope", not "Star
      Wars: a New Hope" (the colon-aware capitalization fix) — and try
      something with an apostrophe like "don't stop believing" —
      confirm it becomes "Don't Stop Believing", not "Don'T Stop
      Believing" (the str.title() bug this was built to avoid)
- [ ] Confirm Apply only marks a file dirty if its value actually
      changed — a file already in the target case, or with the field
      empty, shouldn't show as UNSAVED afterward
- [ ] Operations → Search/Replace... — confirm the Apply button stays
      disabled until there's both a non-empty Find value AND at least
      one file where the replacement would actually change something
- [ ] Try a Find value containing characters like `.` or `(` — confirm
      they're treated as literal text to match, not regex syntax (this
      is deliberate -- see `core/text_transforms.py`'s module docstring)
- [ ] Toggle Case Sensitive on/off and confirm the preview updates
      correctly both ways
- [ ] Operations → Auto-Numbering... — pick a numeric field (Episode #)
      first, confirm the separator field greys out (numeric fields
      write the number directly, no prefix/separator involved) — then
      pick a text field (Title) and confirm the separator field
      re-enables and the preview shows the number prefixed onto the
      existing title text
- [ ] Confirm the numbering follows the CURRENT selection/table order
      (not re-sorted by filename or anything else) — select a few
      files in a specific order and confirm file #1 in that order gets
      the Start value, not necessarily the alphabetically-first file
- [ ] Try a negative Start value with padding enabled — confirm the
      sign renders correctly (e.g. "-05", not a mangled "0-5")

## What NOT to worry about

Everything in `core/opensubtitles_hash.py`, `core/ffmpeg_backend.py`,
`core/video_file.py`'s thumbnail/sidecar methods, and
`core/external_tools.py` was tested for real during development
(cross-verified against an independent reference implementation, in
the hash algorithm's case) and is unlikely to be where a first-build
bug lives. `core/tvdb_client.py`'s season-grouping logic is similarly
well-tested, but everything else in that file (the actual API calls,
including the login step) is not -- start with Stages 2, 3, 5, 5b, and
7 — those are where the real unknowns are.

