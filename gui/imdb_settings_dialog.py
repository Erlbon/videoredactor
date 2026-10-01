"""
gui/imdb_settings_dialog.py

Tools > IMDb Database... -- points the app at an offline IMDb lookup
database and builds it. IMDb's datasets are free for PERSONAL,
NON-COMMERCIAL use only and may not be redistributed, so the app never
downloads or bundles them: the user fetches title.basics.tsv.gz (and,
optionally, title.ratings / title.episode / title.akas) from
datasets.imdbws.com, picks the files here, chooses what to keep, and
Build Database... converts them (core/imdb_import.py) behind a
cancellable progress dialog.

redactor_common's LocalDatabaseSettingsDialog supplies the database path,
Check File and the Build button; this adds the licence notice, the four
file pickers, the build options, a free-disk-space note and a status line
(what the file was built from). Paths and options are remembered in
settings.ini (core/imdb_settings.py).
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)
from redactor_common.core.dump_import import DumpImportError
from redactor_common.core.local_db import forget_cached
from redactor_common.gui.dump_import_runner import run_dump_import
from redactor_common.gui.local_db_settings_dialog import LocalDatabaseSettingsDialog

from core import imdb_settings
from core.imdb_import import (
    CONDITIONS_URL,
    DATASETS_URL,
    DOCS_URL,
    ATTRIBUTION,
    LICENCE_NOTICE,
    REGION_CHOICES,
    TYPE_CHOICES,
    BuildOptions,
    ImdbDatabaseError,
    build_imdb_database,
    describe_database,
)

DATASET_FILTER = "IMDb dataset (*.tsv.gz *.tsv *.gz);;All files (*)"
# An ESTIMATE: per-row sizes were measured on the first ~50k titles / ~95k alternative titles of IMDb's
# files (about 115 bytes per title, 96 per alternative title; episodes not measurable there) and scaled to
# the full files' published row counts: a default build comes to roughly 0.6-1 GB and uses about 1 GB of
# scratch space while it runs.
FREE_SPACE_WARNING_BYTES = 3 * (1 << 30)

PICKERS = [
    ("basics", "title.basics:", "Required -- path to title.basics.tsv.gz"),
    ("ratings", "title.ratings:", "Recommended -- ratings and vote counts (needed for the vote minimum)"),
    ("episodes", "title.episode:", "Optional -- season and episode numbers (needed to look up episodes)"),
    ("akas", "title.akas:", "Optional -- alternative titles (a film's Italian, German, French title ...); the biggest file"),
]

INSTRUCTIONS = (
    "Look up films, series and episodes offline in <b>IMDb</b>'s free datasets: title, year, genres, runtime and "
    "rating, with no network and no rate limits. The datasets have <b>no plot, poster or cast</b> -- those still "
    f"come from TMDB.<br><br><b>Building it:</b><ol><li>From <a href=\"{DATASETS_URL}\">datasets.imdbws.com</a> "
    "download <code>title.basics.tsv.gz</code> (about 200 MB) and, as you like, <code>title.ratings.tsv.gz</code>, "
    "<code>title.episode.tsv.gz</code> and <code>title.akas.tsv.gz</code> (the biggest). Keep them compressed. "
    f"<a href=\"{DOCS_URL}\">What the files hold</a>.</li>"
    "<li>Choose them below, pick what to keep, and click <b>Build Database...</b>. Reading them takes a while "
    "(tens of minutes for the full files); Cancel is safe, and an existing database is replaced only when the new "
    "one is complete.</li></ol>"
)


def licence_html() -> str:
    return (f"<b>Licence:</b> {LICENCE_NOTICE} See IMDb's <a href=\"{CONDITIONS_URL}\">conditions of use</a> "
            f"and the <a href=\"{DOCS_URL}\">datasets page</a>.")


class ImdbSettingsDialog(LocalDatabaseSettingsDialog):
    def __init__(self, parent=None):
        super().__init__(
            title="IMDb Database",
            instructions_html=INSTRUCTIONS,
            path=imdb_settings.load_database(),
            check=describe_database,
            save=self._save_all,
            error_types=(ImdbDatabaseError,),
            file_filter="SQLite database (*.db *.sqlite *.sqlite3);;All files (*)",
            build=lambda dialog: self._build_database(),
            build_label="Build Database…",
            parent=parent,
        )
        self.setMinimumWidth(680)
        self.path_edit.setPlaceholderText(imdb_settings.default_database_path())
        options = imdb_settings.load_options()
        sources = imdb_settings.load_sources()

        # The licence notice: prominent, at the very top, before anything can be built.
        self.licence_label = QLabel(licence_html())
        self.licence_label.setWordWrap(True)
        self.licence_label.setTextFormat(Qt.TextFormat.RichText)
        self.licence_label.setOpenExternalLinks(True)
        self.licence_label.setStyleSheet("QLabel { border: 2px solid #c0392b; border-radius: 4px; padding: 8px; }")

        self.attribution_label = QLabel(ATTRIBUTION)
        self.attribution_label.setWordWrap(True)
        self.attribution_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        box = QGroupBox("Build from IMDb datasets")
        layout = QVBoxLayout(box)
        self.source_edits: dict[str, QLineEdit] = {}
        for kind, label, placeholder in PICKERS:
            self.source_edits[kind] = self._picker(layout, kind, label, sources.get(kind, ""), placeholder)

        layout.addWidget(QLabel("Keep these title types:"))
        grid = QGridLayout()
        self.type_boxes: dict[str, QCheckBox] = {}
        for index, (type_id, label, _default) in enumerate(TYPE_CHOICES):
            type_box = QCheckBox(label)
            type_box.setChecked(type_id in options.types)
            self.type_boxes[type_id] = type_box
            grid.addWidget(type_box, index // 4, index % 4)
        layout.addLayout(grid)

        self.skip_adult = QCheckBox("Skip adult titles")
        self.skip_adult.setChecked(options.skip_adult)
        layout.addWidget(self.skip_adult)

        votes_row = QHBoxLayout()
        votes_row.addWidget(QLabel("Minimum votes for films and series:"))
        self.min_votes = QSpinBox()
        self.min_votes.setRange(0, 1_000_000)
        self.min_votes.setValue(options.min_votes)
        self.min_votes.setToolTip(
            "Titles with fewer IMDb votes are left out (episodes never need votes). Needs the ratings file.\n"
            "5 keeps the database much smaller and loses almost only obscure entries; 0 keeps everything."
        )
        votes_row.addWidget(self.min_votes)
        votes_row.addWidget(QLabel("(0 keeps everything; a bigger number is a smaller database)"), 1)
        layout.addLayout(votes_row)

        self.include_episodes = QCheckBox("Include episodes (needed to look up an episode; the biggest part)")
        self.include_episodes.setChecked(options.include_episodes)
        layout.addWidget(self.include_episodes)

        self.include_akas = QCheckBox("Include alternative titles (finds films by their Norwegian, Italian... titles)")
        self.include_akas.setChecked(options.include_akas)
        self.include_akas.toggled.connect(self._sync_aka_boxes)
        layout.addWidget(self.include_akas)
        region_grid = QGridLayout()
        self.region_boxes: dict[str, QCheckBox] = {}
        for index, (code, label) in enumerate(REGION_CHOICES):
            region_box = QCheckBox(label if "(" in label else f"{label} ({code})")
            region_box.setToolTip(f"IMDb region code {code}")
            region_box.setChecked(code in options.regions)
            self.region_boxes[code] = region_box
            region_grid.addWidget(region_box, index // 4, index % 4)
        layout.addLayout(region_grid)
        self.aka_original = QCheckBox("Also keep original-language titles")
        self.aka_original.setChecked(options.aka_original)
        layout.addWidget(self.aka_original)
        self._sync_aka_boxes()

        self.space_label = QLabel()
        self.space_label.setWordWrap(True)
        layout.addWidget(self.space_label)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        # Licence first, then the status line and the build box, above the Check File / Build row.
        self.layout().insertWidget(0, self.licence_label)
        self.layout().insertWidget(1, self.attribution_label)
        self.layout().insertWidget(4, box)
        self.layout().insertWidget(4, self.status_label)
        self.path_edit.textChanged.connect(self._refresh_status)
        self._refresh_status()

    # -- widgets -------------------------------------------------------------------------

    def _picker(self, layout, kind: str, label: str, value: str, placeholder: str) -> QLineEdit:
        row = QHBoxLayout()
        row.addWidget(QLabel(label))
        edit = QLineEdit(value)
        edit.setPlaceholderText(placeholder)
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda: self._browse_dataset(edit, label))
        row.addWidget(edit, 1)
        row.addWidget(browse)
        layout.addLayout(row)
        return edit

    def _browse_dataset(self, edit: QLineEdit, label: str) -> None:
        start = edit.text() or next(
            (os.path.dirname(e.text()) for e in self.source_edits.values() if e.text()), ""
        ) or os.path.dirname(self.path_edit.text() or "")
        path, _ = QFileDialog.getOpenFileName(self, f"Choose {label.rstrip(':')}", start, DATASET_FILTER)
        if path:
            edit.setText(path)

    def _sync_aka_boxes(self) -> None:
        enabled = self.include_akas.isChecked()
        for region_box in self.region_boxes.values():
            region_box.setEnabled(enabled)
        self.aka_original.setEnabled(enabled)

    def _browse(self) -> None:
        """The database may not exist yet, so this is a Save dialog that doesn't
        ask to overwrite (picking an existing file just selects it)."""
        path, _ = QFileDialog.getSaveFileName(
            self, "Choose where the database is (or will be) stored",
            self.path_edit.text().strip() or imdb_settings.default_database_path(),
            self._file_filter, options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.path_edit.setText(path)

    def free_space_text(self) -> str:
        """How much room the database's drive has, with a warning when that is less
        than a default build needs; "" when it can't be told."""
        folder = os.path.dirname(os.path.abspath(self.path_edit.text().strip() or imdb_settings.default_database_path()))
        while folder and not os.path.isdir(folder):
            parent = os.path.dirname(folder)
            if parent == folder:
                return ""
            folder = parent
        try:
            free = shutil.disk_usage(folder).free
        except OSError:
            return ""
        text = f"Free space where the database goes: {free / (1 << 30):,.1f} GB."
        if free < FREE_SPACE_WARNING_BYTES:
            text += (" That may not be enough: with the default options the finished database is estimated at "
                     "0.6-1 GB and the build needs about 1 GB more in scratch space. Choose another location or keep less.")
        return text

    def _refresh_status(self) -> None:
        path = self.path_edit.text().strip()
        if not path:
            self.status_label.setText("No database yet.")
        elif not os.path.isfile(path):
            self.status_label.setText("Not built yet -- the file doesn't exist.")
        else:
            ok, message = self.check_result()
            self.status_label.setText(message if ok else f"Problem: {message}")
        self.space_label.setText(self.free_space_text())

    # -- options / build -----------------------------------------------------------------

    def sources(self) -> dict[str, str]:
        return {kind: edit.text().strip() for kind, edit in self.source_edits.items()}

    def build_options(self) -> BuildOptions:
        return BuildOptions(
            types=tuple(type_id for type_id, box in self.type_boxes.items() if box.isChecked()),
            skip_adult=self.skip_adult.isChecked(),
            min_votes=self.min_votes.value(),
            include_episodes=self.include_episodes.isChecked(),
            include_akas=self.include_akas.isChecked(),
            regions=tuple(code for code, box in self.region_boxes.items() if box.isChecked()),
            aka_original=self.aka_original.isChecked(),
        )

    def _save_all(self, path: str) -> None:
        imdb_settings.save_database(path)
        imdb_settings.save_sources(self.sources())
        imdb_settings.save_options(self.build_options())

    def _build_database(self) -> Optional[str]:
        """Converts the chosen datasets; returns the new database's path (None if
        abandoned, cancelled or failed)."""
        sources = self.sources()
        if not sources["basics"]:
            QMessageBox.information(self, "Build Database", "Choose the title.basics file first.")
            return None
        options = self.build_options()
        if not options.types:
            QMessageBox.information(self, "Build Database", "Tick at least one title type.")
            return None
        if options.min_votes and not sources["ratings"]:
            QMessageBox.information(
                self, "Build Database",
                "The vote minimum needs the title.ratings file. Choose it, or set the minimum to 0.",
            )
            return None
        notes = []
        if options.include_episodes and "tvEpisode" in options.types and not sources["episodes"]:
            notes.append("Without title.episode no episodes are kept.")
        if options.include_akas and not sources["akas"]:
            notes.append("Without title.akas there are no alternative titles.")
        dest = self.path_edit.text().strip() or imdb_settings.default_database_path()
        replacing = (
            "\n\nThe existing database is replaced only when the new one is complete." if os.path.exists(dest) else ""
        )
        extra = ("\n\n" + " ".join(notes)) if notes else ""
        answer = QMessageBox.question(
            self, "Build Database",
            f"Build {os.path.basename(dest)} from the IMDb dataset file(s)?\n\nIt reads the whole files, which can take "
            f"tens of minutes, and needs a few GB of free disk space while it runs.\n\nKeeping: {options.describe()}."
            f"{extra}{replacing}\n\nReminder: {LICENCE_NOTICE}\n\n{ATTRIBUTION}",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return None
        self._save_all(dest)  # remember the sources even if the build is cancelled
        forget_cached(dest)  # a lookup may still hold the old build open
        try:
            summary = run_dump_import(
                self, "Build IMDb Database", "Reading the IMDb datasets…",
                lambda progress, cancelled: build_imdb_database(
                    sources["basics"], dest, sources["ratings"], sources["episodes"], sources["akas"],
                    options, progress, cancelled,
                ),
            )
        except DumpImportError as exc:
            QMessageBox.warning(self, "Build IMDb Database", str(exc))
            return None
        if summary is None:
            return None
        QMessageBox.information(self, "Build IMDb Database", summary.describe())
        self.path_edit.setText(dest)
        self._refresh_status()
        return dest
