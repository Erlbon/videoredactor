"""
TMDB attribution: the notice sentence and the official logo.

TMDB's API terms (https://www.themoviedb.org/api-terms-of-use) require this
exact notice and the TMDB logo, shown less prominently than the app's own
marks and unmodified (https://www.themoviedb.org/about/logos-attribution).
The logo is TMDB's own "blue short" SVG, kept as downloaded in
assets/tmdb-logo.svg (source and date: CREDITS.md). It is only ever scaled.
"""

from __future__ import annotations
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from core.app_paths import asset_path
from redactor_common.gui.about_dialog import CreditsDialog

# The wording TMDB prescribes, with "application" as the product word. Kept
# identical to the sentence in CREDITS.md and ABOUT.md (a test checks that).
TMDB_NOTICE = (
    "This application uses TMDB and the TMDB APIs but is not endorsed, "
    "certified, or otherwise approved by TMDB."
)
TMDB_URL = "https://www.themoviedb.org/"

# Small on purpose: TMDB wants its logo less prominent than the app's own.
LOGO_HEIGHT = 14
CREDITS_LOGO_HEIGHT = 20


def logo_path() -> Path:
    # asset_path() resolves through sys._MEIPASS when frozen.
    return asset_path("assets") / "tmdb-logo.svg"


def logo_pixmap(height: int = LOGO_HEIGHT, device_pixel_ratio: float = 1.0) -> QPixmap:
    """The logo rendered `height` px tall (aspect ratio kept); a null pixmap
    if the file is missing or unreadable, so callers just show the text."""
    renderer = QSvgRenderer(str(logo_path()))
    if not renderer.isValid():
        return QPixmap()
    size = renderer.defaultSize()
    if size.height() <= 0:
        return QPixmap()
    width = round(height * size.width() / size.height())
    pixmap = QPixmap(QSize(round(width * device_pixel_ratio), round(height * device_pixel_ratio)))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def _logo_label(height: int, parent: QWidget) -> QLabel | None:
    pixmap = logo_pixmap(height, parent.devicePixelRatioF())
    if pixmap.isNull():
        return None
    label = QLabel(parent)
    label.setPixmap(pixmap)
    label.setToolTip("The Movie Database (TMDB)")
    label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    return label


class TmdbAttributionFooter(QWidget):
    """Small 'logo + notice' strip for the bottom of a TMDB lookup dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.logo_label = _logo_label(LOGO_HEIGHT, self)
        if self.logo_label is not None:
            row.addWidget(self.logo_label, 0, Qt.AlignmentFlag.AlignTop)
        self.notice_label = QLabel(f'Data from <a href="{TMDB_URL}">TMDB</a>. {TMDB_NOTICE}')
        self.notice_label.setWordWrap(True)
        self.notice_label.setOpenExternalLinks(True)
        # Muted, small print: attribution, not a headline.
        self.notice_label.setStyleSheet("color: gray; font-size: 8pt;")
        row.addWidget(self.notice_label, 1)


class AppCreditsDialog(CreditsDialog):
    """The shared Credits dialog plus the TMDB logo under the text. The shared
    dialog renders Markdown through QTextBrowser, which can't reliably draw an
    SVG, so the logo is a separate label placed above the Close button."""

    def __init__(self, credits_path: str, parent=None):
        super().__init__(credits_path, parent)
        self.logo_label = _logo_label(CREDITS_LOGO_HEIGHT, self)
        if self.logo_label is not None:
            layout = self.layout()
            # Last item is the button box; the logo goes just before it.
            layout.insertWidget(layout.count() - 1, self.logo_label, 0, Qt.AlignmentFlag.AlignLeft)
