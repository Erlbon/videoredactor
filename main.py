"""The Ʌideo Redactor -- entry point."""

import sys
from PyQt6.QtWidgets import QApplication

from redactor_common.gui.qmessagebox_style import apply_message_box_style
from redactor_common.gui.theme import apply_theme

from gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)  # Fusion + a WCAG-contrast-verified light/dark palette -- see redactor_common/gui/theme.py
    # A long unwrappable line (a path, raw ffmpeg/mkvtoolnix stderr) could
    # otherwise stretch a QMessageBox across the whole screen -- this was
    # the one Redactor app missing this fix entirely.
    apply_message_box_style(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
