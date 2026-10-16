"""Media > Embed Existing Poster Files...: finds <name>-poster.(jpg|png) beside real MP4 copies, judges them by the true-poster
rules, embeds the ticked ones, and only after the read-back moves the poster file to a FAKE recycle bin (the real Recycle Bin
is never touched here)."""

import os
import shutil

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox  # noqa: E402
from redactor_common.core.trash import TrashError  # noqa: E402

import gui.embed_posters_dialog as epd  # noqa: E402
import gui.main_window as mw  # noqa: E402
from core import embed_posters, sidecars  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import VideoMetadata  # noqa: E402
from tests.test_poster import solid  # noqa: E402

from tests.sample_clip import SAMPLE, SKIP_REASON  # noqa: E402

pytestmark = pytest.mark.skipif(bool(SKIP_REASON), reason=SKIP_REASON or "sample clip")

_app = QApplication.instance() or QApplication([])


class FakeBin:
    def __init__(self, fail_for=()):
        self.binned: list[str] = []
        self.fail_for = fail_for
        self.seen_cover_first: list[bool] = []

    def __call__(self, path):
        if os.path.basename(path) in self.fail_for:
            raise TrashError("the Recycle Bin is not available on this drive")
        os.remove(path)  # a stand-in for 'moved away'; the real bin is never used
        self.binned.append(os.path.basename(path))


@pytest.fixture
def library(tmp_path):
    """Three real clips: one with a portrait JPEG poster, one with a landscape PNG, one with a poster and an old cover."""
    clips = {}
    for name in ("Show.S01E01", "Show.S01E02", "Show.S01E03"):
        path = tmp_path / f"{name}.mp4"
        shutil.copy(SAMPLE, path)
        clips[name] = path
    (tmp_path / "Show.S01E01-poster.jpg").write_bytes(solid(600, 900, "JPG"))
    (tmp_path / "Show.S01E02-poster.png").write_bytes(solid(900, 600, "PNG"))
    (tmp_path / "Show.S01E03-poster.jpg").write_bytes(solid(700, 1000, "JPG", color=0x884422))
    # E03 already carries a cover
    vf = VideoFile(path=clips["Show.S01E03"])
    vf.load()
    from core.mp4_backend import write_mp4_cover

    write_mp4_cover(str(clips["Show.S01E03"]), solid(500, 750, "JPG", color=0x00AA00))
    return tmp_path


def load_all(folder):
    files = []
    for path in sorted(folder.glob("*.mp4")):
        vf = VideoFile(path=path)
        vf.load()
        files.append(vf)
    return files


def test_poster_sidecars_are_found_by_exact_stem(tmp_path):
    video = tmp_path / "Show S01E01.mkv"
    video.write_bytes(b"x")
    for name in ("Show S01E01-poster.jpg", "Show S01E01-poster.PNG", "Show S01E010-poster.jpg", "Show S01E01.en.srt",
                 "Show S01E01-poster.jpg.bak"):
        (tmp_path / name).write_bytes(b"x")
    found = [os.path.basename(p) for p in sidecars.poster_sidecars(str(video))]
    assert found == ["Show S01E01-poster.jpg", "Show S01E01-poster.PNG"]  # jpg first; another episode's file is not claimed


def test_the_offers_carry_the_true_poster_verdict(library):
    offers = {o.video.path.stem: o for o in embed_posters.find_offers(load_all(library))}
    assert set(offers) == {"Show.S01E01", "Show.S01E02", "Show.S01E03"}
    e1, e2, e3 = offers["Show.S01E01"], offers["Show.S01E02"], offers["Show.S01E03"]
    assert e1.verdict.ok and e1.shape_text == "600x900" and e1.default_ticked
    assert not e2.verdict.ok and "not portrait" in e2.verdict.reason and not e2.default_ticked  # landscape: unticked
    assert e3.verdict.ok and e3.existing_cover.startswith("JPEG 500x750") and not e3.default_ticked  # would replace a cover


def test_embedding_reads_back_then_bins_the_poster_file(library):
    bin_ = FakeBin()
    offer = embed_posters.find_offers(load_all(library))[0]
    poster_file = offer.poster_path
    outcome = embed_posters.embed_offer(offer, bin_)
    assert outcome.embedded and outcome.trashed and bin_.binned == ["Show.S01E01-poster.jpg"]
    assert not os.path.exists(poster_file)
    assert offer.video.read_cover()[:3] == b"\xff\xd8\xff" and offer.video.metadata.cover_info.startswith("JPEG 600x900")
    fresh = VideoFile(path=offer.video.path)
    fresh.load()
    assert fresh.metadata.cover_info.startswith("JPEG 600x900")


def test_a_failed_embed_keeps_the_poster_file_and_bins_nothing(library, monkeypatch):
    offer = embed_posters.find_offers(load_all(library))[0]
    monkeypatch.setattr(VideoFile, "embed_cover_now", lambda self, jpeg: "the poster is not in the file after writing it")
    bin_ = FakeBin()
    outcome = embed_posters.embed_offer(offer, bin_)
    assert not outcome.embedded and not outcome.trashed and bin_.binned == []
    assert os.path.exists(offer.poster_path) and "poster file is kept" in outcome.message


def test_an_unavailable_recycle_bin_keeps_the_file_and_says_so(library):
    offer = embed_posters.find_offers(load_all(library))[0]
    outcome = embed_posters.embed_offer(offer, FakeBin(fail_for=("Show.S01E01-poster.jpg",)))
    assert outcome.embedded and not outcome.trashed and os.path.exists(offer.poster_path)
    assert "Recycle Bin" in outcome.message


def test_a_picture_file_that_is_not_a_picture_is_never_embedded_or_binned(tmp_path):
    clip = tmp_path / "Show.S01E01.mp4"
    shutil.copy(SAMPLE, clip)
    (tmp_path / "Show.S01E01-poster.jpg").write_bytes(b"this is not a picture")
    vf = VideoFile(path=clip)
    vf.load()
    (offer,) = embed_posters.find_offers([vf])
    assert not offer.verdict.ok and offer.shape_text == "unreadable"
    bin_ = FakeBin()
    outcome = embed_posters.embed_offer(offer, bin_)
    assert not outcome.embedded and bin_.binned == [] and os.path.exists(offer.poster_path)


@pytest.fixture
def window(monkeypatch, tmp_path):
    import core.config as config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    monkeypatch.setattr(mw.MainWindow, "_restore_last_folder_on_startup", lambda self: None)
    monkeypatch.setattr(mw.MainWindow, "_check_external_tools_on_startup", lambda self: None)
    w = mw.MainWindow()
    yield w
    w.close()


def test_the_dialog_lists_the_verdicts_and_embeds_only_the_ticked(window, library, monkeypatch):
    window.video_files = load_all(library)
    window._refresh_table_rows()
    seen = []

    def fake_exec(self):
        seen.append(self)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(epd.EmbedPostersDialog, "exec", fake_exec)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: seen.append(("warning", a[2])))
    bin_ = FakeBin()
    outcomes = epd.run_embed_posters(window, window.video_files, mw._error_details, trash=bin_)
    dialog = seen[0]
    texts = [[dialog.table.item(r, c).text() for c in range(1, 6)] for r in range(dialog.table.rowCount())]
    assert [t[0] for t in texts] == ["Show.S01E01.mp4", "Show.S01E02.mp4", "Show.S01E03.mp4"]
    assert texts[0][3] == "Yes" and texts[1][3].startswith("No: not portrait") and texts[2][4].startswith("JPEG 500x750")
    ticks = [dialog.table.item(r, 0).checkState() for r in range(3)]
    assert ticks == [Qt.CheckState.Checked, Qt.CheckState.Unchecked, Qt.CheckState.Unchecked]
    assert [o.offer.video.path.stem for o in outcomes] == ["Show.S01E01"] and bin_.binned == ["Show.S01E01-poster.jpg"]
    assert os.path.exists(library / "Show.S01E02-poster.png") and os.path.exists(library / "Show.S01E03-poster.jpg")
    assert "Embedded 1 poster(s); 1 poster file(s) moved to the Recycle Bin" == window.status_bar.currentMessage()


def test_a_ticked_landscape_or_replacement_is_the_users_explicit_choice(window, library, monkeypatch):
    window.video_files = load_all(library)

    def tick_all(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.CheckState.Checked)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(epd.EmbedPostersDialog, "exec", tick_all)
    bin_ = FakeBin()
    outcomes = epd.run_embed_posters(window, window.video_files, mw._error_details, trash=bin_)
    assert sorted(o.offer.video.path.stem for o in outcomes if o.embedded) == ["Show.S01E01", "Show.S01E02", "Show.S01E03"]
    by = {v.path.stem: v for v in window.video_files}
    assert by["Show.S01E03"].metadata.cover_info.startswith("JPEG 700x1000")  # the old cover was replaced by choice
    assert by["Show.S01E02"].metadata.cover_info.startswith("JPEG 900x600")


def test_cancel_changes_nothing(window, library, monkeypatch):
    window.video_files = load_all(library)
    monkeypatch.setattr(epd.EmbedPostersDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    bin_ = FakeBin()
    assert epd.run_embed_posters(window, window.video_files, mw._error_details, trash=bin_) == []
    assert bin_.binned == [] and len(list(library.glob("*-poster.*"))) == 3


def test_nothing_to_embed_and_no_files(window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, text, *a, **k: shown.append(text))
    assert epd.run_embed_posters(window, [], mw._error_details) == []
    assert "Open some video files" in shown[-1]
    clip = tmp_path / "Lonely.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    assert epd.run_embed_posters(window, [vf], mw._error_details) == []
    assert "No <name>-poster.jpg" in shown[-1]


def test_the_menu_item_runs_the_flow_for_the_selection(window, library, monkeypatch):
    window.video_files = load_all(library)
    window._refresh_table_rows()
    window.table.selectRow(0)
    got = []
    monkeypatch.setattr(epd, "run_embed_posters", lambda win, files, details, **k: got.append([f.path.stem for f in files]) or [])
    window._on_embed_posters()
    assert got == [["Show.S01E01"]]
    window.table.clearSelection()
    window._on_embed_posters()
    assert len(got[1]) == 3  # nothing selected: every loaded video
    assert "embed_posters" in window.actions_by_key


def test_renaming_a_video_still_carries_an_existing_poster_file(library):
    pairs = sidecars.sidecar_pairs(str(library / "Show.S01E01.mp4"), str(library / "Renamed.mp4"))
    assert (str(library / "Show.S01E01-poster.jpg"), str(library / "Renamed-poster.jpg")) in pairs
