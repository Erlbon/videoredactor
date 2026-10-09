"""The Ʌideo Redactor -- entry point.

Startup (crash logging, taskbar icon ID, theme, message-box width) is
redactor_common.gui.app_bootstrap.run_app(), shared with the other
Redactor apps.
"""

import sys

from core import api_keys, crash_log
from core.app_paths import asset_path
from core.version import APP_NAME
from videocli import cli_requested
from redactor_common.gui.app_bootstrap import run_app


def main() -> int:
    if cli_requested(sys.argv):
        # One exe: `videoredactor info ...` is the command line (no window). See videocli/main.py.
        from redactor_common.cli import run
        from videocli.main import main as cli_main

        return run(cli_main, sys.argv[1:])

    from gui.main_window import MainWindow

    # API keys: settings.ini -> secret store, once (no-op when already moved).
    api_keys.migrate_legacy_keys()

    return run_app(
        app_name=APP_NAME,
        window_factory=MainWindow,
        crash_log_path=crash_log.log_path(),
        app_user_model_id="Erlbon.VideoRedactor.GUI.1",
        icon_path=asset_path("assets/icon.ico"),
    )


if __name__ == "__main__":
    sys.exit(main())
