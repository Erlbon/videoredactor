"""
TagPanel: the bulk-edit panel (left side of the main window).

Shape follows the epub tool's tag_panel: a resizable/collapsible panel
that edits whichever fields are relevant to the current view. Here,
"relevant" is driven by the Content Type filter (see main_window.py) --
when a filter is active, only that content type's fields are shown;
with no filter, ALL editable fields are shown (per user's explicit
"if no filter is selected, all columns should be shown" instruction).

Values aren't written to any file until Apply/Save -- typing alone only
stages changes locally, same non-negotiable the epub tool had to
specifically fix once (v23) after it initially leaked through.
"""

from __future__ import annotations
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QFormLayout, QLineEdit,
    QTextEdit, QSpinBox, QComboBox, QLabel,
)
from PyQt6.QtCore import pyqtSignal, Qt
from PyQt6.QtGui import QPixmap

from core.video_metadata import ContentType, fields_for_content_type, UNIVERSAL_FIELDS
from core.controlled_vocab import (
    get_genre_options, get_language_options, parse_multi_value, serialize_multi_value,
)
from core.table_settings import is_column_visible, sanitize_hidden_fields
from core.config import get_setting
from gui.multi_select_combo import MultiSelectComboBox

PREVIEW_WIDTH = 240
PREVIEW_HEIGHT = 135  # 16:9 -- matches typical video aspect ratio

# Human-readable labels for each field key.
FIELD_LABELS = {
    # content_type first -- matches UNIVERSAL_FIELDS's ordering in
    # core/video_metadata.py: it's the first field to fill in, since it
    # drives which other fields are relevant.
    "content_type": "Content Type",
    "title": "Title",
    "sort_title": "Sort Title",
    "description": "Description",
    "genre_tags": "Genre / Tags",
    "release_date": "Release Date",
    "language": "Language",
    "personal_rating": "Personal Rating (1-5)",
    "comment": "Comment",
    "director": "Director",
    "cast": "Cast",
    "writer": "Writer",
    "studio": "Studio",
    "collection": "Collection",
    "show_title": "Show Title",
    "season_number": "Season #",
    "episode_number": "Episode #",
    "network": "Network",
    "artist": "Artist",
    "album": "Album",
    "track_title": "Track Title",
    "composer": "Composer",
}

# Fields long enough to warrant a multi-line text box rather than a single line.
MULTILINE_FIELDS = {"description", "comment"}

# Fields that are small integers -> spin box, not free text (avoids
# users typing "Season Two" into a field that needs to write a number).
INT_FIELDS = {"season_number", "episode_number", "personal_rating"}

# Controlled-vocabulary multi-select fields, per explicit request --
# replaces freeform typing with a fixed pick-list (a file can genuinely
# have more than one genre, or more than one audio language track).
# Maps to GETTER FUNCTIONS, not static lists -- the option lists are
# now user-editable via Settings (Add/Remove Genres.../Languages...),
# so a widget built from a snapshot taken once at import time would go
# stale the moment the user edits either list. Calling the getter fresh
# every time a widget is built (or the panel is refreshed) is what
# makes "remove a genre in Settings" actually take the genre out of
# this picker too, rather than just out of some list nobody's reading
# from anymore.
MULTISELECT_FIELD_GETTERS = {
    "genre_tags": get_genre_options,
    "language": get_language_options,
}


class TagPanel(QWidget):
    """Emits fieldsChanged(dict) with only the fields the user actually
    touched -- mirrors the epub tool's <multiple values> handling: fields
    left untouched across a multi-selection are never overwritten with a
    blank on Apply.
    """

    apply_requested = pyqtSignal(dict)  # {field_name: value}

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._inputs: dict[str, QWidget] = {}
        self._touched: set[str] = set()
        self._current_filter: Optional[ContentType] = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        # Preview thumbnail, above the field form -- same "cover preview
        # above the fields" arrangement as the epub tool's cover/fields
        # splitter, minus the splitter itself for now (fixed size; a
        # draggable version can follow later if it's actually wanted).
        self.preview_label = QLabel()
        self.preview_label.setFixedSize(PREVIEW_WIDTH, PREVIEW_HEIGHT)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: #222; color: #888;")
        self.preview_label.setText("No preview")
        outer.addWidget(self.preview_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        outer.addWidget(self.scroll_area)

        self.form_container = QWidget()
        self.form_layout = QFormLayout(self.form_container)
        self.scroll_area.setWidget(self.form_container)

        self.set_content_type_filter(None)  # builds the initial (all-fields) form

    def set_preview_image(self, path: Optional[str]) -> None:
        """Show a thumbnail, or a 'No preview'/'Multiple files selected'
        placeholder when path is None. Scaling keeps aspect ratio rather
        than stretching/distorting a 16:9 frame to fit a fixed box.
        """
        if path is None:
            self.preview_label.setPixmap(QPixmap())  # clears any prior image
            self.preview_label.setText("No preview")
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText("Preview unavailable")
            return

        scaled = pixmap.scaled(
            PREVIEW_WIDTH, PREVIEW_HEIGHT,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.preview_label.setText("")
        self.preview_label.setPixmap(scaled)

    def set_content_type_filter(self, content_type: Optional[ContentType]) -> None:
        """Rebuild the form for the given filter. None = show all editable fields.

        Also filters out any field the user has hidden via Settings'
        Add/Remove Columns -- hiding a column and hiding its panel
        field are the same "I don't use this field" signal, per
        explicit request ("I want to remove all the fields I am not
        going to use, to get more screen estate"), so both read from
        the SAME persisted hidden-columns state rather than being two
        independent settings that could drift out of sync.
        """
        self._current_filter = content_type

        # Clear existing rows
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._inputs.clear()
        self._touched.clear()

        if content_type is None:
            field_names = list(FIELD_LABELS.keys())
        else:
            field_names = fields_for_content_type(content_type)

        hidden = self._get_hidden_fields()
        field_names = [f for f in field_names if is_column_visible(f, hidden)]

        for field_name in field_names:
            label = FIELD_LABELS.get(field_name, field_name)
            widget = self._make_input_widget(field_name)
            self._inputs[field_name] = widget
            self.form_layout.addRow(QLabel(label), widget)

    def _get_hidden_fields(self) -> set[str]:
        """Reads the exact same [table] hidden_columns setting.ini key
        main_window.py's own _load_hidden_fields() reads -- deliberately
        the same key, not a separate panel-specific one, so a column
        hidden via the table's header right-click menu or the Settings
        Add/Remove Columns dialog takes the matching panel field out
        immediately too, with no separate setting to keep in sync.
        """
        raw = get_setting("table", "hidden_columns", "")
        return sanitize_hidden_fields({f for f in raw.split(",") if f} if raw else set())

    def refresh_fields(self) -> None:
        """Rebuild the form so it picks up CURRENT state -- both the
        genre_tags/language MultiSelectComboBox option lists (after a
        Settings Add/Remove Genres.../Languages... change) and which
        fields are hidden (after a Settings Add/Remove Columns change,
        or a column hidden via the table header's right-click menu).
        Call this after any of those change, so the panel reflects it
        immediately rather than only on the next filter change or file
        selection.

        Applies any pending (typed but not yet Applied) changes FIRST,
        via the same apply_pending_changes() Save already uses. A full
        rebuild necessarily discards the panel's current widget state
        -- without this, an in-progress edit to some OTHER field
        sitting in the panel while the user happens to visit Settings
        would be lost outright rather than merely staying pending.

        Just re-runs the existing full rebuild with the same filter
        already active -- _make_input_widget already reads live from
        core.controlled_vocab's getters, and set_content_type_filter
        already reads live from the hidden-fields setting, so a
        straightforward rebuild is sufficient; no separate fast path
        is needed for either case.
        """
        self.apply_pending_changes()
        self.set_content_type_filter(self._current_filter)

    def _make_input_widget(self, field_name: str) -> QWidget:
        if field_name == "content_type":
            combo = QComboBox()
            combo.addItem("", "")  # blank = leave unset/mixed
            for ct in ContentType:
                if ct != ContentType.UNSET:
                    combo.addItem(ct.value, ct.value)
            combo.currentIndexChanged.connect(lambda _, f=field_name: self._mark_touched(f))
            return combo

        if field_name in MULTISELECT_FIELD_GETTERS:
            multi = MultiSelectComboBox(MULTISELECT_FIELD_GETTERS[field_name]())
            multi.selectionChanged.connect(lambda f=field_name: self._mark_touched(f))
            return multi

        if field_name in INT_FIELDS:
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setSpecialValueText(" ")  # 0 displays as blank = "not set"
            spin.valueChanged.connect(lambda _, f=field_name: self._mark_touched(f))
            return spin

        if field_name in MULTILINE_FIELDS:
            text_edit = QTextEdit()
            text_edit.setMaximumHeight(80)
            text_edit.textChanged.connect(lambda f=field_name: self._mark_touched(f))
            return text_edit

        line_edit = QLineEdit()
        line_edit.textChanged.connect(lambda _, f=field_name: self._mark_touched(f))
        return line_edit

    def _mark_touched(self, field_name: str) -> None:
        self._touched.add(field_name)

    def has_pending_changes(self) -> bool:
        """True if the user has typed/changed something in the panel
        that hasn't been Applied yet. Exposed so callers (e.g. Save)
        can check before deciding whether an implicit Apply is needed.
        """
        return bool(self._touched)

    def apply_pending_changes(self) -> None:
        """Programmatically do what clicking Apply does -- exposed so
        Save can trigger it first. "Hit Save" should also mean "I meant
        to Apply first": without this, typing into a field and hitting
        Save/Ctrl+S directly (without a separate Apply click) would
        silently save the file's OLD metadata, since VideoFile.save()
        always writes whatever's currently in vf.metadata, and typing
        alone never touches that -- only Apply does. No-op if nothing's
        actually pending, so this is always safe to call unconditionally
        before a save.
        """
        if not self._touched:
            return
        self._on_apply_clicked()

    def _on_apply_clicked(self) -> None:
        """Collect only touched fields' current values and emit them.

        Untouched fields are deliberately omitted -- this is what makes
        "load 5 files, only edit Genre, Apply" leave the other 4 fields'
        existing per-file values alone rather than blanking them.
        """
        values: dict[str, object] = {}
        for field_name in self._touched:
            widget = self._inputs.get(field_name)
            if widget is None:
                continue
            values[field_name] = self._read_widget_value(field_name, widget)
        self.apply_requested.emit(values)
        self._touched.clear()

    def _read_widget_value(self, field_name: str, widget: QWidget) -> object:
        if isinstance(widget, MultiSelectComboBox):
            # Checked before the generic QComboBox branch below --
            # MultiSelectComboBox IS a QComboBox subclass, so isinstance
            # would match the wrong branch first otherwise.
            selected = set(widget.checked_items())
            return serialize_multi_value(selected, widget.options)
        if isinstance(widget, QComboBox):
            return widget.currentData()
        if isinstance(widget, QSpinBox):
            value = widget.value()
            return value if value > 0 else None
        if isinstance(widget, QTextEdit):
            return widget.toPlainText()
        if isinstance(widget, QLineEdit):
            return widget.text()
        return None

    def load_values(self, metadata_dict: dict[str, object]) -> None:
        """Populate the panel from a single file's (or a multi-selection's
        merged) field values. A None value for a field means "mixed values
        across selection" and should display as blank, same spirit as the
        epub tool's <multiple values> placeholder -- exact widget-level
        placeholder rendering left for the functional-testing pass.
        """
        self._touched.clear()
        for field_name, widget in self._inputs.items():
            value = metadata_dict.get(field_name)
            if isinstance(widget, MultiSelectComboBox):
                # Checked before the generic QComboBox branch below --
                # same subclass-ordering reason as _read_widget_value.
                selected = parse_multi_value(value if isinstance(value, str) else "")
                widget.set_checked_items(selected)
            elif isinstance(widget, QComboBox):
                idx = widget.findData(value if value is not None else "")
                widget.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value if isinstance(value, int) else 0)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(value if isinstance(value, str) else "")
            elif isinstance(widget, QLineEdit):
                widget.setText(value if isinstance(value, str) else "")
        self._touched.clear()  # loading values must never count as "touched"
