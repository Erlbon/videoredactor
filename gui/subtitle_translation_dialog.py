"""
gui/subtitle_translation_dialog.py

Subtitle translation, side by side. One dialog, two modes:

  MODE_TRANSLATE  "Subtitle translation": pick a non-English subtitle of the video (a sidecar file or an embedded
                  text track), confirm its language, Translate (a worker thread under a progress dialog with
                  Cancel), fix the English cells by hand if the machine got something wrong, then Write English
                  Subtitle: <name>.en.srt beside the video through the app's usual subtitle writer. It never
                  replaces a file; if <name>.en.srt exists it offers another English name or cancel. The
                  original subtitle is never touched. The text is remembered as a machine translation.
  MODE_COMPARE    "Compare subtitles": any two subtitle tracks of the video side by side (read only), rows
                  aligned by time overlap so tracks with different cue counts still line up.

Nothing is translated or written unless the user presses the buttons. The worker thread never touches a widget.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout,
)
from redactor_common.core.srt import Cue, align_cues, clock_text, parse_srt
from redactor_common.core.translate import TranslateError, Translator
from redactor_common.gui.progress import run_work_with_progress
from redactor_common.gui.table_columns import make_columns_resizable

from core import lyrics_subtitles, subtitle_translation as st, translation_settings

MODE_TRANSLATE = "translate"
MODE_COMPARE = "compare"
MT_STATUS = "Machine translation (engine {engine}) - check before relying on it."
COL_TIME, COL_ORIGINAL, COL_ENGLISH = 0, 1, 2


class SubtitleTranslationDialog(QDialog):
    """Usage: SubtitleTranslationDialog(video, mode=MODE_TRANSLATE, parent=window).exec(), `video` a VideoFile.
    `translator` overrides the configured engine (tests); `preselect` = a sidecar path to start on."""

    def __init__(self, video, mode: str = MODE_TRANSLATE, parent=None, translator: Optional[Translator] = None,
                 preselect: str = "", sources: Optional[list] = None):
        super().__init__(parent)
        self.video = video
        self.mode = mode
        self._translator = translator
        self.target = translation_settings.target_language()
        self.sources = sources if sources is not None else st.list_sources(str(video.path))
        self.written_path = None
        self.result = None           # TranslateResult of the last Translate
        self._source_cues: list[Cue] = []
        self._texts: dict[int, str] = {}
        self._loading = False

        translate = mode == MODE_TRANSLATE
        self.setWindowTitle("Subtitle translation" if translate else "Compare subtitles")
        self.resize(900, 600)
        layout = QVBoxLayout(self)

        self.video_label = QLabel(video.path.name)
        self.video_label.setStyleSheet("font-weight: bold;")
        layout.addWidget(self.video_label)

        top = QHBoxLayout()
        self.source_combo = QComboBox()
        self.source_combo.setMinimumWidth(280)
        self.language_combo = QComboBox()
        self.language_combo.setEditable(True)
        self.language_combo.setMaximumWidth(150)
        self.language_note = QLabel("")
        self.second_combo = QComboBox()
        self.second_combo.setMinimumWidth(280)
        if translate:
            candidates = st.translatable(self.sources)
            for source in candidates:
                self.source_combo.addItem(source.label, source)
            top.addWidget(QLabel("Subtitle:"))
            top.addWidget(self.source_combo, 1)
            top.addWidget(QLabel("Language:"))
            for code, name in lyrics_subtitles.language_choices():
                if code != "en":
                    self.language_combo.addItem(f"{name} [{code}]" if code != "und" else name, code)
            top.addWidget(self.language_combo)
            self.second_combo.hide()
        else:
            for source in self.sources:
                self.source_combo.addItem(source.label, source)
                self.second_combo.addItem(source.label, source)
            top.addWidget(QLabel("Left:"))
            top.addWidget(self.source_combo, 1)
            top.addWidget(QLabel("Right:"))
            top.addWidget(self.second_combo, 1)
            self.language_combo.hide()
            if self.second_combo.count() > 1:
                self.second_combo.setCurrentIndex(1)
        layout.addLayout(top)
        layout.addWidget(self.language_note)

        self.table = QTableWidget(0, 3)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self.translate_button = QPushButton("Translate")
        self.write_button = QPushButton("Write English Subtitle")
        self.close_button = QPushButton("Close")
        if translate:
            buttons.addWidget(self.translate_button)
            buttons.addWidget(self.write_button)
        else:
            self.translate_button.hide()
            self.write_button.hide()
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

        self.translate_button.clicked.connect(self.run_translate)
        self.write_button.clicked.connect(self.write_english)
        self.close_button.clicked.connect(self.reject)
        self.write_button.setEnabled(False)
        self.translate_button.setEnabled(self.source_combo.count() > 0)

        if translate:
            self._setup_translate(preselect)
        else:
            self.source_combo.currentIndexChanged.connect(self._load_compare)
            self.second_combo.currentIndexChanged.connect(self._load_compare)
            self._load_compare()

    # -- translate mode -----------------------------------------------------------------------

    def translator(self) -> Translator:
        if self._translator is None:
            self._translator = translation_settings.make_translator()
        return self._translator

    def _setup_translate(self, preselect: str) -> None:
        self.table.setHorizontalHeaderLabels(["Time", "Original", "English (editable)"])
        if self.source_combo.count() == 0:
            self.status_label.setText(
                "This video has no subtitle in another language to translate"
                + (" (it already has English subtitles)." if st.has_english(self.sources) else ".")
            )
            self._fit_columns()
            return
        if preselect:
            for i in range(self.source_combo.count()):
                if self.source_combo.itemData(i).path == preselect:
                    self.source_combo.setCurrentIndex(i)
        self.source_combo.currentIndexChanged.connect(self._load_source)
        self._load_source()

    def current_source(self):
        return self.source_combo.currentData()

    def source_language(self) -> str:
        """The language code in the box (a listed entry, or what was typed)."""
        text = self.language_combo.currentText().strip()
        index = self.language_combo.findText(text)
        if index >= 0:
            return str(self.language_combo.itemData(index) or "")
        if "[" in text and text.endswith("]"):
            text = text[text.rindex("[") + 1:-1]
        return text.lower() if text.lower() != "unknown (und)" else ""

    def _set_language(self, code: str) -> None:
        index = self.language_combo.findData(code) if code else self.language_combo.findData("und")
        if index >= 0:
            self.language_combo.setCurrentIndex(index)
        elif code:
            self.language_combo.setEditText(code)

    def _load_source(self) -> None:
        source = self.current_source()
        self._loading = True
        try:
            self.write_button.setEnabled(False)
            self.result = None
            try:
                text = st.read_text(source)
            except Exception as exc:  # noqa: BLE001 -- shown to the user, not raised
                self._source_cues = []
                self.status_label.setText(f"Could not read that subtitle: {exc}")
                self._fill_translate_rows()
                return
            self._source_cues = parse_srt(text)
            code, guessed = st.source_language(source, text)
            self._set_language(code)
            if guessed:
                self.language_note.setText(
                    "The subtitle has no language tag; the language was guessed from its text"
                    + (f" ({code})" if code else " (could not tell)") + ". Please confirm it before translating."
                )
            else:
                self.language_note.setText("")
            self._fill_translate_rows()
            self.status_label.setText(f"{len(self._source_cues)} cues. Press Translate.")
        finally:
            self._loading = False

    def _fill_translate_rows(self, translated: Optional[list[Cue]] = None) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._source_cues))
        for row, cue in enumerate(self._source_cues):
            self._set_cell(row, COL_TIME, clock_text(cue.start), editable=False)
            self._set_cell(row, COL_ORIGINAL, cue.text, editable=False)
            self._set_cell(row, COL_ENGLISH, translated[row].text if translated else "", editable=True)
        self.table.blockSignals(False)
        self.table.resizeRowsToContents()
        self._fit_columns()

    def _set_cell(self, row: int, col: int, text: str, editable: bool) -> None:
        item = QTableWidgetItem(text)
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)
        self.table.setItem(row, col, item)

    def _fit_columns(self) -> None:
        make_columns_resizable(self.table, wide_column=None)
        total = max(self.table.viewport().width(), 600)
        header = self.table.horizontalHeader()
        header.resizeSection(COL_TIME, 70)
        half = (total - 70) // 2
        header.resizeSection(COL_ORIGINAL, max(half, 200))

    def run_translate(self) -> None:
        if not self._source_cues:
            return
        source_lang = self.source_language()
        if not source_lang:
            QMessageBox.information(self, "Language Needed", "Choose the subtitle's language first.")
            return
        translator = self.translator()
        cues = list(self._source_cues)
        target = self.target

        def work(progress, cancel):
            return st.translate_all(cues, source_lang, target, translator, progress=progress, cancel=cancel)

        self.status_label.setText("Translating...")
        self.translate_button.setEnabled(False)
        try:
            result = run_work_with_progress(self, "Translating subtitles...", work, len(cues))
        except TranslateError as exc:
            result = None
            self.status_label.setText(f"Translation failed: {exc}")
        finally:
            self.translate_button.setEnabled(True)
        if result is None:
            return
        self.result = result
        self._fill_translate_rows(result.cues)
        notes = [MT_STATUS.format(engine=result.engine or translator.name)]
        if result.cancelled:
            notes.insert(0, f"Cancelled: {result.translated} of {len(cues)} cues translated; the rest are empty.")
        if result.error:
            notes.insert(0, f"The engine stopped: {result.error}")
        notes.extend(result.warnings)
        self.status_label.setText(" ".join(notes))
        for row in result.failed:
            item = self.table.item(row, COL_ENGLISH)
            if item is not None:
                item.setText("")  # untranslated: leave it for the user to type, never the foreign text
        self.write_button.setEnabled(result.translated > 0)

    def english_cues(self) -> list[Cue]:
        """The cues as they are in the table now (edits included): original timing, the English cell's text.
        Rows whose English cell is empty are left out."""
        out = []
        for row, cue in enumerate(self._source_cues):
            item = self.table.item(row, COL_ENGLISH)
            text = (item.text() if item is not None else "").strip()
            if text:
                out.append(Cue(cue.start, cue.end, text))
        return out

    def write_english(self) -> Optional[str]:
        cues = self.english_cues()
        if not cues:
            return None
        engine = (self.result.engine if self.result else "") or self.translator().name
        source_lang = self.source_language()
        tag = "en"
        while True:
            try:
                path = st.write_translation(self.video, cues, engine, source_lang, tag=tag)
                break
            except FileExistsError:
                alt = st.free_english_alternative(self.video.path)
                if alt is None or tag != "en":
                    QMessageBox.warning(
                        self, "Not Written",
                        "English subtitle files for this video already exist; nothing was written.",
                    )
                    return None
                name = f"{self.video.path.stem}.{alt}.srt"
                reply = QMessageBox.question(
                    self, "English Subtitle Exists",
                    f"{self.video.path.stem}.en.srt already exists and is never replaced.\n\n"
                    f"Save the translation as {name} instead?",
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return None
                tag = alt
            except OSError as exc:
                QMessageBox.warning(self, "Not Written", f"Couldn't write the subtitle file: {exc}")
                return None
        self.written_path = path
        self.status_label.setText(f"Wrote {path.name} (machine translation, engine {engine}). The original is unchanged.")
        return str(path)

    # -- compare mode ------------------------------------------------------------------------------

    def _load_compare(self) -> None:
        left, right = self.source_combo.currentData(), self.second_combo.currentData()
        self.table.setHorizontalHeaderLabels(
            ["Time", left.label if left else "Left", right.label if right else "Right"]
        )
        if left is None or right is None:
            self.table.setRowCount(0)
            self.status_label.setText("This video has fewer than two readable subtitle tracks to compare.")
            self._fit_columns()
            return
        try:
            a = parse_srt(st.read_text(left))
            b = parse_srt(st.read_text(right))
        except Exception as exc:  # noqa: BLE001
            self.table.setRowCount(0)
            self.status_label.setText(f"Could not read a subtitle: {exc}")
            return
        rows = align_cues(a, b)
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._set_cell(i, COL_TIME, clock_text(row.start), editable=False)
            self._set_cell(i, 1, row.a_text, editable=False)
            self._set_cell(i, 2, row.b_text, editable=False)
        self.table.resizeRowsToContents()
        self._fit_columns()
        self.status_label.setText(
            f"{len(a)} cues on the left, {len(b)} on the right, {len(rows)} rows aligned by time. Read only."
        )
