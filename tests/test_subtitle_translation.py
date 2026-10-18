"""Subtitle translation: the core helpers, the machine-translation marker, the side-by-side dialog (translate,
edit, write, never overwrite) and Compare on the real MainWindow with scratch settings and a fake engine, the
Preferences page and the Redact step. The engines themselves are tested in redactor_common."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.core.srt import format_srt, parse_srt  # noqa: E402
from redactor_common.core.pipeline import FileStatus  # noqa: E402
from redactor_common.core.translate import TranslateError, TranslateUnavailable, Translator  # noqa: E402

from core import mt_registry, translation_settings as ts  # noqa: E402
from core import subtitle_translation as st  # noqa: E402
from core import redact_steps as rs  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType, VideoMetadata  # noqa: E402
from tests.test_redact_steps import bin_, make_env, only, run_one, stub, work  # noqa: E402,F401

_app = QApplication.instance() or QApplication([])

RU = (
    "1\n00:00:01,000 --> 00:00:03,000\nКогда переехал — не помню\n\n"
    "2\n00:00:04,000 --> 00:00:06,500\nНаверное, был я бухой\n\n"
    "3\n00:00:08,000 --> 00:00:10,000\nМой адрес — ни дом, и ни улица\n"
)
EN = (
    "1\n00:00:01,100 --> 00:00:06,000\nI do not remember when I moved. I must have been drunk.\n\n"
    "2\n00:00:08,000 --> 00:00:10,000\nMy address is neither house nor street.\n\n"
    "3\n00:00:20,000 --> 00:00:22,000\nAn English-only cue.\n"
)


class FakeTranslator(Translator):
    name = "FakeEngine"
    DICT = {
        "Когда переехал — не помню": "I do not remember when I moved",
        "Наверное, был я бухой": "I must have been drunk",
        "Мой адрес — ни дом, и ни улица": "My address is neither house nor street",
    }

    def __init__(self):
        self.calls = []

    def translate_text(self, text, source, target):
        self.calls.append((source, target, text))
        return "\n".join(self.DICT.get(line, "?" + line) for line in text.split("\n"))

    def check(self, source="", target="en"):
        return "fake ok"


@pytest.fixture
def fake_engine():
    fake = FakeTranslator()
    ts.set_translator_factory(lambda: fake)
    yield fake
    ts.set_translator_factory(None)


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    import core.config as config
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    return tmp_path


def music_video(folder, name="Leningrad - WWW.mp4", **meta):
    meta.setdefault("content_type", ContentType.MUSIC_VIDEO)
    return stub(folder, name, **meta)


def write_srt(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


# -- core -------------------------------------------------------------------------------------

def test_iso1_and_sources(work):
    vf = music_video(work)
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    write_srt(work, "Leningrad - WWW.en.srt", EN)
    write_srt(work, "Leningrad - WWW.srt", RU)
    sources = st.list_sources(str(vf.path), probe=lambda p: None)
    assert [(s.label, s.language) for s in sources] == [
        ("Leningrad - WWW.en.srt (file)", "en"), ("Leningrad - WWW.ru.srt (file)", "ru"), ("Leningrad - WWW.srt (file)", "")]
    assert [s.language for s in st.translatable(sources)] == ["ru", ""]
    assert st.has_english(sources)
    assert st.iso1("rus") == "ru" and st.iso1("und") == "" and st.iso1("English") == "en"


def test_source_language_tag_or_guess(work):
    vf = music_video(work)
    write_srt(work, "Leningrad - WWW.srt", RU)
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    by_name = {os.path.basename(s.path): s for s in st.list_sources(str(vf.path), probe=lambda p: None)}
    assert st.source_language(by_name["Leningrad - WWW.ru.srt"], RU) == ("ru", False)
    assert st.source_language(by_name["Leningrad - WWW.srt"], RU) == ("ru", True)


def test_write_translation_is_en_srt_marks_mt_and_never_overwrites(work, scratch):
    vf = music_video(work)
    cues = parse_srt(RU)
    for c in cues:
        c.text = "English " + c.text[:3]
    path = st.write_translation(vf, cues, "FakeEngine", "ru")
    assert path.name == "Leningrad - WWW.en.srt"
    text = path.read_text(encoding="utf-8")
    assert [(c.start, c.end) for c in parse_srt(text)] == [(c.start, c.end) for c in cues]
    assert mt_registry.engine_for(text) == "FakeEngine"
    assert mt_registry.track_title(text, "") == "English (machine translation)"
    assert mt_registry.track_title(text, "(SDH)") == "English (machine translation) (SDH)"
    with pytest.raises(FileExistsError):
        st.write_translation(vf, cues, "FakeEngine", "ru")
    assert path.read_text(encoding="utf-8") == text
    assert st.free_english_alternative(vf.path) == "eng"


def test_mt_marker_survives_crlf_and_ignores_other_texts(scratch):
    mt_registry.mark(EN, "X")
    assert mt_registry.engine_for(EN.replace("\n", "\r\n")) == "X"
    assert mt_registry.engine_for("﻿" + EN) == "X"
    assert mt_registry.engine_for(RU) == "" and mt_registry.track_title(RU, "(1)") == "(1)"
    assert mt_registry.engine_for("") == ""


def test_conversion_names_a_machine_translated_track(tmp_path, scratch):
    """The real ingest path (process_file, ffmpeg faked as in test_conversion_run): the machine-translated English
    subtitle becomes a track titled "English (machine translation)", the original Russian one keeps no such title."""
    from core import conversion_run as cr
    from core.library_safety import SafeRun
    from tests.test_conversion_run import S, media

    lib = tmp_path / "lib"
    lib.mkdir()
    src = lib / "Song.mp4"
    src.write_bytes(b"V" * 1000)
    ru = write_srt(lib, "Song.ru.srt", RU)
    en = lib / "Song.en.srt"
    vf = VideoFile(path=src, metadata=VideoMetadata())
    st.write_translation(vf, parse_srt(EN), "FakeEngine", "ru")
    assert en.exists() and ru.exists()
    info = media(str(src), ext_size=300 * 1048576, h=720)
    seen = {}

    def runner(args, cancel_event=None, timeout=None):
        seen["args"] = args
        with open(args[-1], "w", encoding="utf-8") as f:
            f.write("MKV")
        return 0, ""

    def probe(path):
        return media(path, ext_size=3, h=720, subs=2) if ".redact-part" in path else info

    run = SafeRun(library_root=str(lib), quarantine_root="", dry_run=False, trash=lambda p: None, probe=probe)
    old = __import__("time").time() - 3600  # the recent-file guard leaves files younger than a few minutes alone
    os.utime(src, (old, old))
    out = cr.process_file(run, str(src), S, probe=probe, runner=runner, carry_tags=False)
    assert out.converted, out.message
    args = seen["args"]
    titles = [args[i + 1] for i, a in enumerate(args) if a.startswith("-metadata:s:s") and args[i + 1].startswith("title=")]
    assert titles == ["title=English (machine translation)"]
    assert "language=eng" in args and "language=rus" in args


def test_engine_selection(scratch, monkeypatch):
    from redactor_common.core.translate import ArgosCliTranslator, LibreTranslateTranslator
    assert ts.engine() == "libretranslate" and ts.server_url() == "http://localhost:5000" and ts.target_language() == "en"
    assert isinstance(ts.make_translator(), LibreTranslateTranslator)
    import core.config as config
    config.set_setting("translation", "engine", "argos")
    config.set_setting("translation", "url", "http://box:5000")
    assert isinstance(ts.make_translator(), ArgosCliTranslator) and ts.server_url() == "http://box:5000"
    config.set_setting("translation", "engine", "garbage")
    assert ts.engine() == "libretranslate"


def test_check_engine_libretranslate_and_argos_missing(scratch):
    from redactor_common.core.translate import ArgosCliTranslator
    assert ts.check_engine(FakeTranslator()) == "fake ok"
    with pytest.raises(TranslateUnavailable, match="could not be run"):
        ts.check_engine(ArgosCliTranslator("definitely-not-a-command-xyz"))


def test_preferences_page_and_defaults(scratch):
    from gui.video_preferences import VideoPreferencesDialog
    dlg = VideoPreferencesDialog()
    assert "Translation" in dlg.page_titles()
    assert dlg.value("translation_engine") == "libretranslate" and dlg.value("translation_target") == "en"
    assert dlg.value("translation_url") == "http://localhost:5000"


# -- the dialog on the real MainWindow ------------------------------------------------------------------

@pytest.fixture
def window(scratch, monkeypatch, work):
    import gui.main_window as mw
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    vf = music_video(work)
    vf.path.write_bytes(b"x")
    w.video_files = [vf]
    w._refresh_table_rows()
    w.table.selectRow(0)
    yield w
    for f in w.video_files:
        f.dirty = False
    w.close()


def drive_dialog(monkeypatch, window, action, **handlers):
    """Runs window.<action>() with SubtitleTranslationDialog.exec replaced by `handlers['use'](dialog)`."""
    import gui.subtitle_translation_dialog as dlgmod
    opened = {}

    def fake_exec(self):
        opened["dialog"] = self
        handlers["use"](self)
        return 0

    monkeypatch.setattr(dlgmod.SubtitleTranslationDialog, "exec", fake_exec)
    getattr(window, action)()
    return opened["dialog"]


def cell(dialog, row, col):
    return dialog.table.item(row, col).text()


def test_translate_dialog_side_by_side_edit_and_write(window, work, fake_engine, monkeypatch):
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    seen = {}

    def use(d):
        seen["title"] = d.windowTitle()
        seen["headers"] = [d.table.horizontalHeaderItem(i).text() for i in range(3)]
        seen["sources"] = [d.source_combo.itemText(i) for i in range(d.source_combo.count())]
        seen["lang"] = d.source_language()
        assert not d.write_button.isEnabled()
        assert [cell(d, r, 1) for r in range(3)] == ["Когда переехал — не помню", "Наверное, был я бухой", "Мой адрес — ни дом, и ни улица"]
        assert [cell(d, r, 0) for r in range(3)] == ["0:01", "0:04", "0:08"]
        assert all(cell(d, r, 2) == "" for r in range(3))
        d.run_translate()
        seen["english"] = [cell(d, r, 2) for r in range(3)]
        seen["status"] = d.status_label.text()
        assert d.write_button.isEnabled()
        # the user fixes a machine mistake
        d.table.item(1, 2).setText("He must have been drunk")
        seen["editable"] = bool(d.table.item(1, 2).flags() & Qt.ItemFlag.ItemIsEditable)
        seen["orig_editable"] = bool(d.table.item(1, 1).flags() & Qt.ItemFlag.ItemIsEditable)
        seen["path"] = d.write_english()

    d = drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert seen["title"] == "Subtitle translation"
    assert seen["headers"] == ["Time", "Original", "English (editable)"]
    assert seen["sources"] == ["Leningrad - WWW.ru.srt (file)"] and seen["lang"] == "ru"
    assert seen["english"] == ["I do not remember when I moved", "I must have been drunk", "My address is neither house nor street"]
    assert "Machine translation (engine FakeEngine)" in seen["status"] and "check before relying on it" in seen["status"]
    assert seen["editable"] and not seen["orig_editable"]
    out = work / "Leningrad - WWW.en.srt"
    assert seen["path"] == str(out)
    cues = parse_srt(out.read_text(encoding="utf-8"))
    assert [c.text for c in cues] == ["I do not remember when I moved", "He must have been drunk", "My address is neither house nor street"]
    assert [(c.start, c.end) for c in cues] == [(1.0, 3.0), (4.0, 6.5), (8.0, 10.0)]  # original timing
    assert (work / "Leningrad - WWW.ru.srt").read_text(encoding="utf-8") == RU  # the original is untouched
    assert fake_engine.calls[0][:2] == ("ru", "en") and "Когда переехал" in fake_engine.calls[0][2]
    assert d.written_path == out
    assert mt_registry.engine_for(out.read_text(encoding="utf-8")) == "FakeEngine"


def test_write_never_overwrites_offers_another_name_or_cancel(window, work, fake_engine, monkeypatch):
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    existing = write_srt(work, "Leningrad - WWW.en.srt", "1\n00:00:01,000 --> 00:00:02,000\nHuman made\n")
    before = existing.read_text(encoding="utf-8")
    asked = []
    answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[2]) or next(answers)))
    seen = {}

    def use(d):
        # "Translate..." offers only non-English subtitles: the human English one is not in the list
        seen["sources"] = [d.source_combo.itemText(i) for i in range(d.source_combo.count())]
        d.run_translate()
        seen["declined"] = d.write_english()
        seen["accepted"] = d.write_english()

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert seen["sources"] == ["Leningrad - WWW.ru.srt (file)"]
    assert seen["declined"] is None and existing.read_text(encoding="utf-8") == before
    assert seen["accepted"] == str(work / "Leningrad - WWW.eng.srt")
    assert "Leningrad - WWW.eng.srt" in asked[0] and "never replaced" in asked[0]
    assert (work / "Leningrad - WWW.eng.srt").exists()
    # every English name taken: refused, nothing written
    write_srt(work, "Leningrad - WWW.en-US.srt", "x")
    write_srt(work, "Leningrad - WWW.en-GB.srt", "x")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a[2])))

    def use2(d):
        d.run_translate()
        seen["third"] = d.write_english()

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use2)
    assert seen["third"] is None and warned and "nothing was written" in warned[0]


def test_no_translatable_subtitle_says_so(window, work, monkeypatch):
    write_srt(work, "Leningrad - WWW.en.srt", EN)
    seen = {}

    def use(d):
        seen["count"] = d.source_combo.count()
        seen["status"] = d.status_label.text()
        seen["translate"] = d.translate_button.isEnabled()

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert seen["count"] == 0 and "already has English subtitles" in seen["status"] and not seen["translate"]


def test_language_unknown_is_guessed_and_flagged(window, work, fake_engine, monkeypatch):
    write_srt(work, "Leningrad - WWW.srt", RU)
    seen = {}

    def use(d):
        seen["lang"] = d.source_language()
        seen["note"] = d.language_note.text()

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert seen["lang"] == "ru" and "guessed" in seen["note"] and "confirm" in seen["note"]


def test_engine_down_reports_without_raising(window, work, monkeypatch):
    write_srt(work, "Leningrad - WWW.ru.srt", RU)

    class Down(Translator):
        name = "Down"

        def translate_text(self, text, source, target):
            raise TranslateUnavailable("Could not reach the translation server at http://localhost:5000")

    ts.set_translator_factory(lambda: Down())
    seen = {}

    def use(d):
        d.run_translate()
        seen["status"] = d.status_label.text()
        seen["write"] = d.write_button.isEnabled()

    try:
        drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    finally:
        ts.set_translator_factory(None)
    assert "Could not reach the translation server" in seen["status"] and not seen["write"]


def test_compare_aligns_by_time_with_unequal_cue_counts(window, work, monkeypatch):
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    write_srt(work, "Leningrad - WWW.en.srt", EN)
    seen = {}

    def use(d):
        seen["title"] = d.windowTitle()
        seen["labels"] = [d.source_combo.itemText(i) for i in range(d.source_combo.count())]
        d.source_combo.setCurrentIndex(d.source_combo.findText("Leningrad - WWW.ru.srt (file)"))
        d.second_combo.setCurrentIndex(d.second_combo.findText("Leningrad - WWW.en.srt (file)"))
        seen["rows"] = [(cell(d, r, 0), cell(d, r, 1), cell(d, r, 2)) for r in range(d.table.rowCount())]
        seen["headers"] = [d.table.horizontalHeaderItem(i).text() for i in range(3)]
        seen["status"] = d.status_label.text()
        seen["readonly"] = all(
            not (d.table.item(r, c).flags() & Qt.ItemFlag.ItemIsEditable)
            for r in range(d.table.rowCount()) for c in range(3)
        )
        seen["buttons"] = (d.translate_button.isVisibleTo(d), d.write_button.isVisibleTo(d))

    drive_dialog(monkeypatch, window, "_on_compare_subtitles", use=use)
    assert seen["title"] == "Compare subtitles"
    assert seen["headers"][1:] == ["Leningrad - WWW.ru.srt (file)", "Leningrad - WWW.en.srt (file)"]
    # RU cues 1+2 against EN cue 1 (one long line), RU cue 3 against EN cue 2, the English-only cue alone
    assert seen["rows"] == [
        ("0:01", "Когда переехал — не помню\nНаверное, был я бухой", "I do not remember when I moved. I must have been drunk."),
        ("0:08", "Мой адрес — ни дом, и ни улица", "My address is neither house nor street."),
        ("0:20", "", "An English-only cue."),
    ]
    assert "3 cues on the left, 3 on the right, 3 rows" in seen["status"] and seen["readonly"]
    assert seen["buttons"] == (False, False)


def test_compare_with_fewer_than_two_tracks(window, work, monkeypatch):
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    seen = {}
    drive_dialog(monkeypatch, window, "_on_compare_subtitles", use=lambda d: seen.update(
        rows=d.table.rowCount(), status=d.status_label.text()))
    assert seen["rows"] >= 0 and ("fewer than two" in seen["status"] or "rows aligned" in seen["status"])


def test_needs_exactly_one_selected_video(window, monkeypatch):
    window.table.clearSelection()
    window._on_translate_subtitles()
    assert "exactly one file" in window.status_bar.currentMessage()


def test_columns_are_draggable(window, work, monkeypatch):
    from PyQt6.QtWidgets import QHeaderView
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    seen = {}

    def use(d):
        h = d.table.horizontalHeader()
        seen["modes"] = [h.sectionResizeMode(i) for i in range(h.count() - 1)]

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert all(m == QHeaderView.ResizeMode.Interactive for m in seen["modes"])


# -- Redact ---------------------------------------------------------------------------------------------

def translate_entry(vf, env, **kw):
    recipe, catalogue = only("translate_subtitles", **kw)
    return run_one(vf, env, recipe, catalogue, finalize=False)


def test_redact_step_is_off_by_default():
    assert rs.SubtitleTranslateStep().default_enabled is False
    assert rs.SubtitleTranslateStep.label == "Translate non-English subtitles of music videos to English"


def test_redact_translation_is_only_a_suggestion_and_writes_nothing(work, bin_, tmp_path, scratch, fake_engine):
    vf = music_video(work)
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    entry = translate_entry(vf, make_env(bin_, tmp_path))
    assert entry.status is FileStatus.NEEDS_REVIEW
    assert "machine translation" in entry.review[0].reason
    assert sorted(os.listdir(work)) == ["Leningrad - WWW.mp4", "Leningrad - WWW.ru.srt"]
    # approving the review item writes it (the pipeline calls apply_suggestion with the step result)
    choice = entry.review[0].value
    assert isinstance(choice, rs.TranslationChoice) and choice.source_lang == "ru" and choice.engine == "FakeEngine"
    step = rs.SubtitleTranslateStep()
    text = step.apply_suggestion(type("C", (), {"video": vf})(), type("R", (), {"value": choice})())
    assert text == "saved Leningrad - WWW.en.srt (machine translation, FakeEngine)"
    assert [c.text for c in parse_srt((work / "Leningrad - WWW.en.srt").read_text(encoding="utf-8"))][1] == "I must have been drunk"
    # a second approval never replaces it
    again = step.apply_suggestion(type("C", (), {"video": vf})(), type("R", (), {"value": choice})())
    assert again.startswith("kept the existing")


def test_redact_translation_skipped_when_english_exists_or_not_music_video(work, bin_, tmp_path, scratch, fake_engine):
    vf = music_video(work)
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    write_srt(work, "Leningrad - WWW.en.srt", EN)
    assert translate_entry(vf, make_env(bin_, tmp_path)).status is FileStatus.UNCHANGED
    assert fake_engine.calls == []
    film = stub(work, "Film.mp4", content_type=ContentType.MOVIE)
    write_srt(work, "Film.ru.srt", RU)
    assert translate_entry(film, make_env(bin_, tmp_path)).status is FileStatus.UNCHANGED
    assert fake_engine.calls == []


def test_redact_engine_failure_is_a_note(work, bin_, tmp_path, scratch):
    class Down(Translator):
        name = "Down"

        def translate_text(self, text, source, target):
            raise TranslateUnavailable("server down")

    ts.set_translator_factory(lambda: Down())
    try:
        vf = music_video(work)
        write_srt(work, "Leningrad - WWW.ru.srt", RU)
        entry = translate_entry(vf, make_env(bin_, tmp_path))
    finally:
        ts.set_translator_factory(None)
    assert entry.status is FileStatus.UNCHANGED and entry.failures == []
    assert any("server down" in n for n in entry.notes)


def test_translate_dialog_sorted_table_still_maps_edits_to_their_cues(window, work, fake_engine, monkeypatch):
    """Header clicks sort the table (2026-10-17); the English cell of the FIRST VISUAL row belongs to its own cue."""
    write_srt(work, "Leningrad - WWW.ru.srt", RU)
    seen = {}

    def use(d):
        d.run_translate()
        assert d.table.isSortingEnabled() and d.table.horizontalHeader().isSortIndicatorShown()
        d.table.sortByColumn(0, Qt.SortOrder.DescendingOrder)  # Time: the last cue first
        assert [cell(d, r, 0) for r in range(3)] == ["0:08", "0:04", "0:01"]
        d.table.item(0, 2).setText("EDITED LAST CUE")
        seen["cues"] = [(c.start, c.text) for c in d.english_cues()]
        d.table.sortByColumn(1, Qt.SortOrder.AscendingOrder)  # another column: still the same mapping
        seen["cues2"] = [(c.start, c.text) for c in d.english_cues()]
        seen["texts"] = {cell(d, r, 0): cell(d, r, 2) for r in range(3)}

    drive_dialog(monkeypatch, window, "_on_translate_subtitles", use=use)
    assert [t for _s, t in seen["cues"]] == ["I do not remember when I moved", "I must have been drunk", "EDITED LAST CUE"]
    assert seen["cues"][2][0] == parse_srt(RU)[2].start  # the edit sits on the third cue, in cue order
    assert seen["cues2"] == seen["cues"]
    assert seen["texts"]["0:08"] == "EDITED LAST CUE" and seen["texts"]["0:01"] == "I do not remember when I moved"
