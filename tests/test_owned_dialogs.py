"""Every dialog class of the app has the shared OwnedDialog base: a dialog made straight from QDialog stays in memory, with all its widgets, for
as long as the main window lives (found 2026-10-24 by soaking the app: hundreds of megabytes after a few hundred dialog opens)."""
import importlib
import inspect
import os
import pkgutil

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtWidgets import QDialog  # noqa: E402

import gui as gui_package  # noqa: E402
from redactor_common.gui.owned_dialog import OwnedDialog  # noqa: E402


def test_every_dialog_class_of_the_app_has_the_shared_base():
    plain = []
    for info in pkgutil.iter_modules(gui_package.__path__):
        try:
            module = importlib.import_module(f"gui.{info.name}")
        except ImportError:                                  # an optional dependency is not installed here
            continue
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and issubclass(cls, QDialog) and not issubclass(cls, OwnedDialog):
                plain.append(f"{module.__name__}.{name}")
    assert plain == []
