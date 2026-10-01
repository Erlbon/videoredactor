"""TMDB attribution: TMDB's required notice sentence and official logo shown in
Credits, the TMDB lookup dialogs and the docs; old wording gone; logo bundled."""

import ast
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QTextBrowser  # noqa: E402

from gui import tmdb_attribution as attr  # noqa: E402
from gui import tmdb_episode_picker_dialog as picker  # noqa: E402
from gui.tmdb_search_dialog import SearchSource, TMDBSearchDialog  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = (
    "This application uses TMDB and the TMDB APIs but is not endorsed, "
    "certified, or otherwise approved by TMDB."
)


@pytest.fixture(scope="module", autouse=True)
def _app():
    return QApplication.instance() or QApplication([])


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_code_constant_is_the_official_sentence():
    assert attr.TMDB_NOTICE == OFFICIAL


@pytest.mark.parametrize("name", ["CREDITS.md", "ABOUT.md"])
def test_docs_carry_the_official_sentence(name):
    assert OFFICIAL in _flat((ROOT / name).read_text(encoding="utf-8"))


def test_old_wording_is_gone_everywhere():
    old = re.compile(r"uses the TMDB API but is not endorsed|endorsed or certified by TMDB", re.I)
    skip = {".git", "build", "dist", "build-linux", "dist-linux", "__pycache__", ".claude"}
    for path in ROOT.rglob("*"):
        if path.is_dir() or skip & set(path.relative_to(ROOT).parts):
            continue
        if path.suffix not in {".py", ".md", ".txt", ".spec", ".bat", ".ini"}:
            continue
        # The changelog may quote the old wording when describing the change.
        # release_notes.md is generated from the changelog by the release workflow.
        if path.name in ("test_tmdb_attribution.py", "CHANGELOG.md", "release_notes.md"):
            continue
        assert not old.search(path.read_text(encoding="utf-8", errors="ignore")), path


def test_logo_file_exists_and_renders_through_the_resource_helper():
    path = attr.logo_path()
    assert path.is_file() and path.name == "tmdb-logo.svg"
    pixmap = attr.logo_pixmap(attr.LOGO_HEIGHT)
    assert not pixmap.isNull() and pixmap.height() == attr.LOGO_HEIGHT
    assert pixmap.width() > pixmap.height()  # horizontal logo, aspect kept
    assert isinstance(pixmap, QPixmap)


def test_logo_is_listed_in_the_pyinstaller_spec_datas():
    tree = ast.parse((ROOT / "videoredactor.spec").read_text(encoding="utf-8"))
    datas = []
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "datas":
            datas = ast.literal_eval(node.value)
    assert ("assets/tmdb-logo.svg", "assets") in datas
    # Every bundled source file really exists (a typo would fail the build).
    for src, _dest in datas:
        assert (ROOT / src).exists(), src


def test_credits_dialog_shows_the_sentence_and_a_small_logo():
    dialog = attr.AppCreditsDialog(str(ROOT / "CREDITS.md"))
    try:
        assert OFFICIAL in _flat(dialog.findChild(QTextBrowser).toPlainText())
        assert dialog.logo_label is not None and not dialog.logo_label.pixmap().isNull()
        assert dialog.logo_label.pixmap().height() <= 50
    finally:
        dialog.close()


def test_tmdb_search_dialog_has_notice_and_logo():
    dialog = TMDBSearchDialog(mode="movie")
    try:
        footer = dialog.attribution
        assert OFFICIAL in footer.notice_label.text()
        assert footer.logo_label is not None and not footer.logo_label.pixmap().isNull()
    finally:
        dialog.close()


def test_other_search_sources_do_not_show_the_tmdb_notice():
    source = SearchSource(name="IMDb", movies=lambda q, year=None: [], tv=lambda q, year=None: [])
    dialog = TMDBSearchDialog(mode="movie", source=source)
    try:
        assert not hasattr(dialog, "attribution")
        assert not any(OFFICIAL in lbl.text() for lbl in dialog.findChildren(QLabel))
    finally:
        dialog.close()


def test_episode_picker_has_notice_and_logo(monkeypatch):
    monkeypatch.setattr(picker, "run_lookup", lambda *a, **k: [])
    monkeypatch.setattr(picker.QMessageBox, "information", lambda *a, **k: None)
    dialog = picker.TVEpisodePickerDialog(1)
    try:
        assert OFFICIAL in dialog.attribution.notice_label.text()
        assert dialog.attribution.logo_label is not None
    finally:
        dialog.close()


def test_no_endorsement_is_claimed():
    # The notice only ever says "not endorsed"; nothing says the app is.
    for name in ("CREDITS.md", "ABOUT.md"):
        text = _flat((ROOT / name).read_text(encoding="utf-8"))
        for m in re.finditer(r"[^.]*TMDB[^.]*endorse[^.]*\.", text):
            assert "not endorsed" in m.group(0)
