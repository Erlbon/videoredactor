"""The Ʌideo Redactor -- entry point.

Startup (crash logging, taskbar icon ID, theme, message-box width) is
redactor_common.gui.app_bootstrap.run_app(), shared with the other
Redactor apps.
"""

import sys

from core import crash_log
from core.app_paths import asset_path
from core.version import APP_NAME
from redactor_common.gui.app_bootstrap import run_app


def main() -> int:
    from gui.main_window import MainWindow

    return run_app(
        app_name=APP_NAME,
        window_factory=MainWindow,
        crash_log_path=crash_log.log_path(),
        app_user_model_id="Erlbon.VideoRedactor.GUI.1",
        icon_path=asset_path("assets/icon.ico"),
    )


if __name__ == "__main__":
    sys.exit(main())
