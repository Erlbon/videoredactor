"""The Ʌideo Redactor -- entry point."""

import sys
from PyQt6.QtWidgets import QApplication

from redactor_common.gui.theme import apply_theme

from gui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)  # Fusion + a WCAG-contrast-verified light/dark palette -- see redactor_common/gui/theme.py
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
