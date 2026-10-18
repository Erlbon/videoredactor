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
import html
from enum import Enum
from typing import Optional

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea, QFormLayout, QLineEdit,
    QTextEdit, QSpinBox, QComboBox, QLabel, QPushButton, QStyle, QToolButton, QCheckBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from core.video_metadata import ContentType, PANEL_LABELS_BY_TYPE, fields_for_content_type, UNIVERSAL_FIELDS
from core.controlled_vocab import (
    get_genre_options, get_language_options, get_porn_tag_options, parse_multi_value, serialize_multi_value,
)
from core.table_settings import is_column_visible, sanitize_hidden_fields
from core.config import get_setting, set_setting
from redactor_common.core.field_order import decode_order, encode_order, merge_field_order
from redactor_common.gui.field_order import FieldOrderController, FieldRow, reorder_form_rows
from gui.multi_select_combo import MultiSelectComboBox
from redactor_common.gui.collapsible_splitter import CollapseToggleButton
from redactor_common.gui.image_pane import ImagePanelSplitter, ImagePreviewBox

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
    "episode_number": "Episode / Scene #",
    "network": "Network",
    "artist": "Artist",
    "album": "Album",
    "composer": "Composer",
    "lyrics": "Lyrics",
}

# Fields long enough to warrant a multi-line text box rather than a single line.
MULTILINE_FIELDS = {"description", "comment", "lyrics"}

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


def load_field_order() -> list[str]:
    """The user's side-panel field order, by field key ([] = the default order)."""
    return decode_order(get_setting("panel", "field_order", ""))


def save_field_order(order: list[str]) -> None:
    set_setting("panel", "field_order", encode_order(order))


class TagPanel(QWidget):
    """Emits fieldsChanged(dict) with only the fields the user actually
    touched -- mirrors the epub tool's <multiple values> handling: fields
    left untouched across a multi-selection are never overwritten with a
    blank on Apply.
    """

    apply_requested = pyqtSignal(dict)  # {field_name: value}
    move_track_title_requested = pyqtSignal()  # "Move track title into Title" for the selected music video
    clear_fields_requested = pyqtSignal()  # the "Clear All Fields..." button (MainWindow opens the Clear Fields dialog)
    collapseToggleRequested = pyqtSignal()  # the panel doesn't control its own width -- MainWindow does
    fields_mode_changed = pyqtSignal()  # "Show all fields" was toggled: MainWindow re-runs the selection refresh

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._inputs: dict[str, QWidget] = {}
        self._labels: dict[str, QLabel] = {}
        self._touched: set[str] = set()
        self._current_filter: Optional[ContentType] = None
        self._current_extra: frozenset = frozenset()  # fields shown although the type does not use them (they hold a value)
        self._show_all = False
        self._last_files: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        # Sits at the very top regardless of how narrow the panel gets,
        # so the toggle stays reachable even when minimized -- MainWindow
        # collapses to a slim strip, not to zero width, for exactly this
        # reason (see redactor_common's SplitterPaneCollapser).
        toggle_row = QHBoxLayout()
        self.show_all_box = QCheckBox("Show all fields")
        self.show_all_box.setToolTip("Off: the panel shows only the fields that belong to the selected files' Content "
                                     "Type (plus any other field that holds a value). On: every field.")
        self.show_all_box.toggled.connect(self._on_show_all_toggled)
        toggle_row.addWidget(self.show_all_box)
        toggle_row.addStretch(1)
        self.collapse_toggle_btn = CollapseToggleButton()
        self.collapse_toggle_btn.clicked.connect(self.collapseToggleRequested.emit)
        toggle_row.addWidget(self.collapse_toggle_btn)
        outer.addLayout(toggle_row)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.form_container = QWidget()
        self.form_layout = QFormLayout(self.form_container)
        # The form, then the collapsed read-only "Sources" section (the source ids kept in the file).
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.addWidget(self.form_container)
        # Read-only notes under the form: the retired track title kept in an old file, and a content type that came
        # from the MP4 media type. Both hidden unless they apply to the one selected file.
        self.website_label = QLabel()  # the selection's website, derived from its Studio (read-only; Tools > Studios)
        self.website_label.setTextFormat(Qt.TextFormat.RichText)
        self.website_label.setOpenExternalLinks(True)
        self.website_label.setVisible(False)
        scroll_layout.addWidget(self.website_label)
        self.media_type_note = QLabel("Content Type: from the file's media type (Music Video). It is saved only "
                                      "when you save this file.")
        self.media_type_note.setWordWrap(True)
        self.media_type_note.setVisible(False)
        scroll_layout.addWidget(self.media_type_note)
        self.old_track_title_label = QLabel()
        self.old_track_title_label.setWordWrap(True)
        self.old_track_title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.old_track_title_label.setVisible(False)
        scroll_layout.addWidget(self.old_track_title_label)
        self.move_track_title_button = QPushButton("Move track title into Title")
        self.move_track_title_button.setToolTip(
            "This music video has an old track title and no title. The title is the song: this puts the old "
            "value into Title (not saved until you save the file). The old value stays in the file."
        )
        self.move_track_title_button.setVisible(False)
        self.move_track_title_button.clicked.connect(self.move_track_title_requested.emit)
        scroll_layout.addWidget(self.move_track_title_button)
        self.sources_toggle = QToolButton()
        self.sources_toggle.setCheckable(True)
        self.sources_toggle.setChecked(False)
        self.sources_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.sources_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.sources_toggle.setText("Sources")
        self.sources_toggle.setToolTip("Which records of which databases this file was matched to (read-only; they "
                                       "are set by a lookup and kept inside the file).")
        self.sources_toggle.toggled.connect(self._on_sources_toggled)
        scroll_layout.addWidget(self.sources_toggle)
        self.sources_label = QLabel()
        self.sources_label.setTextFormat(Qt.TextFormat.RichText)
        self.sources_label.setOpenExternalLinks(True)
        self.sources_label.setWordWrap(True)
        self.sources_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.sources_label.setVisible(False)
        scroll_layout.addWidget(self.sources_label)
        scroll_layout.addStretch(1)
        self.scroll_area.setWidget(scroll_content)

        # redactor_common's ImagePreviewBox + ImagePanelSplitter: the
        # resizable image area every Redactor side panel shares -- drag
        # the divider to give the thumbnail more (or less) room; either
        # pane can be dragged all the way closed.
        self.preview_box = ImagePreviewBox(
            "Preview", placeholder="No preview",
            minimum_size=(PREVIEW_WIDTH // 2, PREVIEW_HEIGHT // 2),
        )
        self.preview_label = self.preview_box.image_label
        self.preview_label.setStyleSheet("background-color: #222; color: #888;")  # a "screen" look for video frames
        # A large Play button directly under the thumbnail (same action as Media > Play: MainWindow wires it).
        self.play_button = QPushButton(" Play")
        self.play_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.play_button.setMinimumHeight(40)
        self.play_button.setToolTip("Play the selected file (Ctrl+Shift+P)")
        self.play_button.setEnabled(False)
        preview_column = QWidget()
        preview_layout = QVBoxLayout(preview_column)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(self.preview_box, 1)
        preview_layout.addWidget(self.play_button)
        self._vertical_splitter = ImagePanelSplitter(self.scroll_area, preview_column, initial_sizes=(500, 300))
        outer.addWidget(self._vertical_splitter, 1)
        self.clear_fields_button = QPushButton("Clear All Fields...")
        self.clear_fields_button.setToolTip(
            "Empty fields, source ids and the poster of the selected files (you choose which; nothing is written until "
            "you Save, and Undo takes it back)")
        self.clear_fields_button.clicked.connect(self.clear_fields_requested.emit)
        outer.addWidget(self.clear_fields_button)

        # Drag a field by its grip (redactor_common's field_order): the rows are only permuted, never rebuilt, so
        # values, the "mixed" state and the touched markers stay with their field. The order is saved by field key
        # ([panel] field_order in settings.ini) and applies to every Content Type filter (a filtered panel shows
        # the relevant fields in the saved relative order).
        self._field_order = FieldOrderController(
            self.form_container,
            get_rows=lambda: [FieldRow(k, [self._labels[k], w]) for k, w in self._inputs.items()],
            apply_order=lambda order: reorder_form_rows(
                self.form_layout, {k: (self._labels[k], w) for k, w in self._inputs.items()}, order),
            default_order=lambda: list(FIELD_LABELS.keys()),
            load_order=load_field_order, save_order=save_field_order,
        )
        self.set_content_type_filter(None)  # builds the initial (all-fields) form

    def reset_field_order(self) -> None:
        """Back to the default field order (also: right-click a grip or the panel > Reset Field Order)."""
        self._field_order.reset()

    def set_website(self, host: str) -> None:
        """The website derived from the selection's Studio ("" hides the line)."""
        if host:
            safe = html.escape(host, quote=True)
            self.website_label.setText(f'<b>Website:</b> <a href="https://{safe}">{safe}</a>')
        self.website_label.setVisible(bool(host))

    def set_old_track_title(self, files: list) -> None:
        """The retired track title of the one selected file, read-only (only when it differs from the Title, so
        nothing in an old file is silently lost), the "Move track title into Title" button (a Music Video with an
        empty Title) and the note for a content type read from the MP4 media type."""
        from core.video_metadata import can_move_track_title, old_track_title_to_show

        md = files[0].metadata if len(files) == 1 else None
        old = old_track_title_to_show(md) if md is not None else ""
        self.old_track_title_label.setText(f"Old track title (retired field, kept in the file, read-only): {old}")
        self.old_track_title_label.setVisible(bool(old))
        self.move_track_title_button.setVisible(md is not None and can_move_track_title(md))
        self.media_type_note.setVisible(
            md is not None and md.content_type_from_media_type and md.content_type is ContentType.MUSIC_VIDEO
        )

    def _on_sources_toggled(self, expanded: bool) -> None:
        self.sources_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self.sources_label.setVisible(expanded)

    def set_sources(self, files: list) -> None:
        """The Sources section for the selection: one file's ids as clickable links (built by redactor_common's
        build_link, from validated ids only), a note for several files or none. Read-only; stays collapsed or open as
        the user left it."""
        from core.source_tags import describe

        if len(files) != 1:
            self.sources_toggle.setText("Sources")
            self.sources_label.setText("Select one file to see its sources." if files else "")
            return
        rows = describe(files[0].metadata)
        self.sources_toggle.setText(f"Sources ({len([r for r in rows if r[2]])})" if rows else "Sources")
        if not rows:
            self.sources_label.setText("<i>This file has no source ids. A lookup (Metadata > Look Up) adds them.</i>")
            return
        lines = []
        for label, text, link in rows:
            value = (f'<a href="{html.escape(link, quote=True)}">{html.escape(text)}</a>' if link else html.escape(text))
            lines.append(f"<b>{html.escape(label)}:</b> {value}")
        self.sources_label.setText("<br>".join(lines))

    def set_preview_caption(self, caption: str) -> None:
        """'Poster' (the picture embedded in the file) or 'Frame preview' (a frame ffmpeg grabbed; never written)."""
        self.preview_box.setTitle(caption)

    def set_preview_image(self, path: Optional[str]) -> None:
        """Show a thumbnail, or a 'No preview'/'Multiple files selected'
        placeholder when path is None. AspectRatioImageLabel keeps
        aspect ratio itself, rescaling as the preview pane is resized
        (via the vertical splitter) rather than being locked to a fixed
        box the way this used to be.
        """
        if path is None:
            self.set_preview_caption("Preview")
            self.preview_box.show_message("No preview")
            return
        self.preview_box.show_pixmap(QPixmap(path), "Preview unavailable")

    def set_preview_loading(self) -> None:
        """Placeholder while a thumbnail is generated in the background
        (see MainWindow._update_preview)."""
        self.preview_box.show_message("Loading preview\u2026")

    def set_preview_qimage(self, image: Optional[QImage]) -> None:
        """Shows an already-decoded thumbnail (decoded off the main
        thread by redactor_common's AsyncPreviewLoader); a null or None
        image shows "Preview unavailable"."""
        self.preview_box.show_image(image, "Preview unavailable")

    def set_collapsed_indicator(self, collapsed: bool) -> None:
        """Updates the panel's own toggle button to reflect whether it's
        currently minimized. MainWindow owns the actual collapsed/
        expanded state (via the splitter, since resizing is its job,
        not this widget's) and calls this after any change -- the
        toggle button click, or the user dragging the splitter handle
        by hand."""
        self.collapse_toggle_btn.set_collapsed(collapsed)

    def _on_show_all_toggled(self, checked: bool) -> None:
        self._show_all = checked
        self.show_fields_for(self._last_files)
        self.fields_mode_changed.emit()

    def show_fields_for(self, files: list) -> bool:
        """Dynamic field visibility: one Content Type across the whole selection shows that type's fields (the
        universal ones plus its own); a mixed or unset selection, or "Show all fields", shows everything. A field the
        type does not use but that holds a value in a selected file stays visible, so nothing stray is hidden.
        Rebuilds the form only when the set of fields changes; returns True then (the caller reloads the values)."""
        self._last_files = list(files)
        content_type: Optional[ContentType] = None
        extra: set[str] = set()
        if files and not self._show_all:
            types = {f.metadata.content_type for f in files}
            if len(types) == 1 and ContentType.UNSET not in types:
                content_type = next(iter(types))
                own = set(fields_for_content_type(content_type))
                for f in files:
                    for name in FIELD_LABELS:
                        if name not in own and getattr(f.metadata, name, None) not in (None, "", 0):
                            extra.add(name)
        key = (content_type, frozenset(extra))
        if key == (self._current_filter, self._current_extra):
            return False
        self.set_content_type_filter(content_type, frozenset(extra))
        return True

    def set_content_type_filter(self, content_type: Optional[ContentType], extra: frozenset = frozenset()) -> None:
        """Rebuild the form for the given filter. None = show all editable fields. `extra` = further fields to show
        although the type does not use them.

        Also filters out any field the user has hidden via Settings'
        Add/Remove Columns -- hiding a column and hiding its panel
        field are the same "I don't use this field" signal, per
        explicit request ("I want to remove all the fields I am not
        going to use, to get more screen estate"), so both read from
        the SAME persisted hidden-columns state rather than being two
        independent settings that could drift out of sync.
        """
        self._current_filter = content_type
        self._current_extra = frozenset(extra)

        # Clear existing rows
        while self.form_layout.rowCount():
            self.form_layout.removeRow(0)
        self._inputs.clear()
        self._labels.clear()
        self._touched.clear()

        if content_type is None:
            field_names = list(FIELD_LABELS.keys())
        else:
            field_names = fields_for_content_type(content_type)
            field_names += [f for f in FIELD_LABELS if f in extra and f not in field_names]

        hidden = self._get_hidden_fields()
        # The Lyrics COLUMN starts hidden (a whole song per cell), so its hidden state is not the panel's: the
        # field is part of every Music Video's panel.
        field_names = [f for f in field_names if f == "lyrics" or is_column_visible(f, hidden)]
        # The user's saved order (by key), whatever the filter shows: relative order kept, a field the saved order
        # has not seen goes where the default order puts it.
        saved_order = merge_field_order(load_field_order(), list(FIELD_LABELS.keys()))
        field_names.sort(key=lambda f: saved_order.index(f) if f in saved_order else len(saved_order))

        for field_name in field_names:
            label = PANEL_LABELS_BY_TYPE.get(content_type, {}).get(field_name) or FIELD_LABELS.get(field_name, field_name)
            widget = self._make_input_widget(field_name)
            self._inputs[field_name] = widget
            self._labels[field_name] = QLabel(label)
            self.form_layout.addRow(self._labels[field_name], widget)
        self._field_order.refresh()  # grips for the rebuilt rows

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
        self.set_content_type_filter(self._current_filter, self._current_extra)

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
            getter = get_porn_tag_options if (field_name == "genre_tags" and self._current_filter is ContentType.PORN)                 else MULTISELECT_FIELD_GETTERS[field_name]
            multi = MultiSelectComboBox(getter())
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
            text_edit.setMaximumHeight(240 if field_name == "lyrics" else 80)
            if field_name == "lyrics":
                text_edit.setMinimumHeight(120)
                text_edit.setAcceptRichText(False)
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
        Save All directly (without a separate Apply click) would
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
                # A str-Enum (the content type) must be looked up by its plain text: findData() does not match the
                # enum object itself, so the box would silently stay blank.
                plain = value.value if isinstance(value, Enum) else value
                idx = widget.findData(plain if plain is not None else "")
                widget.setCurrentIndex(idx if idx >= 0 else 0)
            elif isinstance(widget, QSpinBox):
                widget.setValue(value if isinstance(value, int) else 0)
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(value if isinstance(value, str) else "")
            elif isinstance(widget, QLineEdit):
                widget.setText(value if isinstance(value, str) else "")
        self._touched.clear()  # loading values must never count as "touched"
