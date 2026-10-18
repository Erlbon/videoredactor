"""
gui/embed_posters_dialog.py

Media > Embed Existing Poster Files...: the review list of the `<name>-poster.jpg` files beside the selected videos
(all loaded videos when none is selected): video, poster file, size, shape, a "true poster?" verdict by the same rules a
lookup uses (core.poster), and what cover the video already has. A true poster that would replace nothing starts
ticked; a landscape one, or one that would replace an existing cover, starts UNTICKED (ticking is the explicit OK).
"Embed" then works through the ticked ones on a worker thread under a progress dialog with Cancel, and only after an
embed was read back from the video does it send that poster file to the Recycle Bin (reversible, never a permanent
delete).
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)
from redactor_common.gui.background_call import BackgroundCancelled, call_in_background
from redactor_common.gui.progress import run_with_progress
from redactor_common.gui.sortable_table import NaturalTableWidgetItem, enable_sorting
from redactor_common.gui.table_columns import make_columns_resizable

from core.embed_posters import EmbedOutcome, PosterOffer, embed_offer, find_offers
from core.poster import MAX_BYTES, MAX_WIDTH

TITLE = "Embed Existing Poster Files"
# What embedding does to the picture: the same limits a looked-up poster gets (core.poster.prepare_poster).
SCALING_SENTENCE = (f"The poster is scaled to at most {MAX_WIDTH} px and {MAX_BYTES // 1024} KB; "
                    "the original file goes to the Recycle Bin.")
HEADERS = ["Embed", "Video", "Poster file", "Size", "True poster?", "Cover now"]
COL_TICK, COL_VIDEO, COL_FILE, COL_SIZE, COL_VERDICT, COL_COVER = range(6)


class TickItem(QTableWidgetItem):
    """The checkbox cell: sorts by its state (ticked first when descending)."""

    def __lt__(self, other) -> bool:
        return self.checkState().value < other.checkState().value


class EmbedPostersDialog(QDialog):
    """The review list. `ticked_offers()` after exec() == Accepted are the ones to embed."""

    def __init__(self, offers: list[PosterOffer], parent=None):
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.resize(980, 420)
        self.offers = offers
        layout = QVBoxLayout(self)
        note = QLabel(
            "Posters are now kept inside the video file. These poster files sit next to their videos from earlier "
            "versions. Ticked ones are embedded; each poster file then goes to the Recycle Bin, but only after the "
            "embedded poster was read back from the video. A landscape picture is not a poster and starts unticked; "
            "so does a poster that would replace a cover the video already has. "
            f"{SCALING_SENTENCE}"
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(len(offers), len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        import os

        for row, offer in enumerate(offers):
            tick = TickItem()
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            tick.setCheckState(Qt.CheckState.Checked if offer.default_ticked else Qt.CheckState.Unchecked)
            tick.setData(Qt.ItemDataRole.UserRole, row)  # the offer's index: survives sorting
            self.table.setItem(row, COL_TICK, tick)
            verdict = "Yes" if offer.verdict.ok else f"No: {offer.verdict.reason}"
            cells = {
                COL_VIDEO: offer.video.path.name,
                COL_FILE: os.path.basename(offer.poster_path) + (f" (+{offer.others} more, not touched)" if offer.others else ""),
                COL_SIZE: offer.shape_text,
                COL_VERDICT: verdict,
                COL_COVER: offer.existing_cover or "none",
            }
            for col, text in cells.items():
                item = NaturalTableWidgetItem(text)
                item.setToolTip(text)
                item.setData(Qt.ItemDataRole.UserRole, row)
                self.table.setItem(row, col, item)
        enable_sorting(self.table)  # after the fill; the rows stay in order until a header is clicked
        layout.addWidget(self.table, 1)
        make_columns_resizable(self.table, wide_column=COL_VIDEO)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.embed_button = QPushButton("Embed")
        self.embed_button.clicked.connect(self._on_embed)
        buttons.addWidget(self.embed_button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def ticked_offers(self) -> list[PosterOffer]:
        out = []
        for row in range(self.table.rowCount()):  # visual rows: the offer comes from the item, not the row number
            tick = self.table.item(row, COL_TICK)
            if tick.checkState() == Qt.CheckState.Checked:
                out.append(self.offers[tick.data(Qt.ItemDataRole.UserRole)])
        return sorted(out, key=self.offers.index)

    def _on_embed(self) -> None:
        if not self.ticked_offers():
            QMessageBox.information(self, TITLE, "Tick at least one poster file first.")
            return
        self.accept()


def embed_ticked(window, offers: list[PosterOffer], trash: Optional[Callable[[str], None]] = None) -> list[EmbedOutcome]:
    """Embeds `offers` one at a time on a worker thread (progress dialog with Cancel); the outcomes of those that ran."""
    outcomes: list[EmbedOutcome] = []

    def step(offer: PosterOffer, _index: int) -> None:
        try:
            outcomes.append(call_in_background(embed_offer, offer, trash))
        except BackgroundCancelled:
            return
        except Exception as exc:  # noqa: BLE001 -- one video's trouble must not stop the rest
            outcomes.append(EmbedOutcome(offer, False, False, f"{offer.video.path.name}: {exc}"))

    run_with_progress(window, offers, step, "Embedding posters...", threshold=1, cancellable=True,
                      label_for=lambda o: f"Embedding the poster in {o.video.path.name}")
    return outcomes


def run_embed_posters(window, files: list, error_details: Callable[[list[str]], str],
                      trash: Optional[Callable[[str], None]] = None, dialog_class=EmbedPostersDialog) -> list[EmbedOutcome]:
    """The whole Media > Embed Existing Poster Files... flow for `files` (a MainWindow's selection or all rows)."""
    if not files:
        QMessageBox.information(window, TITLE, "Open some video files first (File > Open Files or Open Folder).")
        return []
    offers = run_with_progress_scan(window, files)
    if not offers:
        QMessageBox.information(window, TITLE, "No <name>-poster.jpg (or .png) files were found next to these videos.")
        return []
    dialog = dialog_class(offers, window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return []
    outcomes = embed_ticked(window, dialog.ticked_offers(), trash)
    window._refresh_table_rows()
    if window._selected_video_files():
        window._on_selection_changed()
    done = sum(1 for o in outcomes if o.embedded)
    binned = sum(1 for o in outcomes if o.trashed)
    problems = [o.message for o in outcomes if not o.embedded or not o.trashed]
    window.status_bar.showMessage(f"Embedded {done} poster(s); {binned} poster file(s) moved to the Recycle Bin")
    if problems:
        QMessageBox.warning(window, "Some posters were not embedded", error_details(problems))
    return outcomes


def run_with_progress_scan(window, files: list) -> list[PosterOffer]:
    """find_offers() with a progress dialog (it reads each poster file's header)."""
    found: list[PosterOffer] = []

    def step(vf, _index: int) -> None:
        found.extend(find_offers([vf]))

    run_with_progress(window, files, step, "Looking for poster files...", threshold=20, cancellable=False,
                      label_for=lambda vf: f"Looking next to {vf.path.name}")
    return found
