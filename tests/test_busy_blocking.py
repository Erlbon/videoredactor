"""2026-10-16 independent review, 'shared progress dialogs and threading', as it shows in videoredactor:
(1) the subtitle translation's counted dialog stays up (modal) after Cancel until the engine has really stopped;
(2) run_lookup / run_cancellable_lookup (which disable their owner themselves) keep working with redactor_common's
own input block: the previous enabled state comes back, a click during the call starts nothing."""

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt, QTimer  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication, QProgressDialog, QPushButton, QVBoxLayout, QWidget  # noqa: E402

from core import translation_settings as ts  # noqa: E402
from tests.test_subtitle_translation import (  # noqa: E402,F401
    FakeTranslator, RU, drive_dialog, scratch, window, write_srt,
)
from tests.test_redact_steps import work  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])


class SlowTranslator(FakeTranslator):
    def translate_text(self, text, source, target):
        time.sleep(0.8)
        return super().translate_text(text, source, target)


def test_subtitle_translation_cancel_keeps_the_dialog_up_until_the_engine_stopped(window, work, monkeypatch):
    ts.set_translator_factory(lambda: SlowTranslator())
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    seen = {}

    def use(d):
        def cancel():
            box = next(b for b in d.findChildren(QProgressDialog) if b.isVisible())
            box.findChild(QPushButton).click()  # the real Cancel button

        def probe():
            boxes = d.findChildren(QProgressDialog)
            seen["visible"] = any(b.isVisible() for b in boxes)
            seen["text"] = boxes[0].labelText() if boxes else ""
            seen["blocked"] = not d.isEnabled()

        QTimer.singleShot(200, cancel)
        QTimer.singleShot(500, probe)  # the engine is still inside its first 0.8 s call
        d.run_translate()
        seen["enabled_after"] = d.isEnabled()

    try:
        drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    finally:
        ts.set_translator_factory(None)
    assert seen["visible"], "the dialog was hidden while the engine still ran"
    assert seen["text"].startswith("Cancelling") and seen["blocked"]
    assert seen["enabled_after"]


def _owner():
    owner = QWidget()
    layout = QVBoxLayout(owner)
    button = QPushButton("Go")
    layout.addWidget(button)
    owner.button = button
    owner.show()
    return owner


def test_run_lookup_restores_the_previous_enabled_state_and_blocks_clicks():
    from gui.lookup import run_cancellable_lookup, run_lookup

    for runner in (run_lookup, run_cancellable_lookup):
        owner = _owner()
        calls = []

        def slow():
            calls.append(1)
            time.sleep(0.6)
            return "ok"

        owner.button.clicked.connect(lambda: runner(owner, slow))
        QTimer.singleShot(100, lambda: QTest.mouseClick(owner.button, Qt.MouseButton.LeftButton))
        QTest.mouseClick(owner.button, Qt.MouseButton.LeftButton)
        assert calls == [1] and owner.isEnabled() and QApplication.overrideCursor() is None
        owner.setEnabled(False)  # an owner the caller had disabled stays disabled
        assert runner(owner, lambda: 3) == 3 and not owner.isEnabled()
        owner.close()
