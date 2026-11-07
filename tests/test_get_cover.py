"""Get Cover (Music Video): the three sources' replies parsed from canned JSON (iTunes, MusicBrainz with its ranking and
its 503 retry, Discogs with and without a token), the square-cover rule, find_covers' notes, the menu visibility on the
real MainWindow, and the dialog + flow staging the chosen cover as the poster (undoable). No network."""

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import music_cover as mc  # noqa: E402
from core.poster import ALBUM_KIND, PosterRejected, judge_poster, prepare_poster  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from tests.test_batch_operations_and_undo import window  # noqa: E402,F401
from tests.test_get_lyrics import _lookup_texts, _select_only, music_video  # noqa: E402

_app = QApplication.instance() or QApplication([])


def jpeg(width, height, colour="teal") -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPG", 85)
    return bytes(buffer.data())


def replies(mapping):
    """A fetch(url) answering from {url fragment: bytes or dict}."""
    seen = []

    def fetch(url):
        url = url if isinstance(url, str) else url.full_url
        seen.append(url)
        for fragment, body in mapping.items():
            if fragment in url:
                return json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        raise AssertionError(f"unexpected request {url}")
    fetch.seen = seen
    return fetch


# -- the square-cover rule -------------------------------------------------------------------------------------------


def test_an_album_cover_is_square_and_big_enough_and_a_poster_still_portrait():
    assert judge_poster(1000, 1000, ALBUM_KIND).ok and judge_poster(500, 492, ALBUM_KIND).ok
    assert "too small" in judge_poster(300, 300, ALBUM_KIND).reason
    assert "not a square" in judge_poster(1000, 600, ALBUM_KIND).reason
    assert not judge_poster(1000, 1000).ok  # the poster rule is unchanged
    prepared = prepare_poster(jpeg(1200, 1200), ALBUM_KIND)
    assert (prepared.width, prepared.height) == (1000, 1000) and len(prepared.jpeg) <= 400 * 1024
    with pytest.raises(PosterRejected):
        prepare_poster(jpeg(200, 200), ALBUM_KIND)


# -- the sources --------------------------------------------------------------------------------------------------------


ITUNES = {"results": [
    {"artistName": "Depeche Mode", "collectionName": "Violator", "releaseDate": "1990-03-19T08:00:00Z",
     "artworkUrl100": "https://is1.mzstatic.com/a/100x100bb.jpg"},
    {"artistName": "Depeche Mode", "collectionName": "Violator", "artworkUrl100": "https://is1.mzstatic.com/b/100x100bb.jpg"},
    {"artistName": "Lacuna Coil", "collectionName": "Karmacode", "artworkUrl100": "https://is1.mzstatic.com/c/100x100bb.jpg"},
    {"artistName": "Depeche Mode", "collectionName": "No art"},
]}


def test_itunes_keeps_one_cover_per_album_of_this_artist_at_1000_pixels():
    fetch = replies({"itunes.apple.com/search": ITUNES})
    found = mc.search_itunes("Depeche Mode", "Enjoy the Silence", fetch)
    assert [(c.source, c.release, c.url) for c in found] == [
        ("iTunes", "Violator (1990)", "https://is1.mzstatic.com/a/1000x1000bb.jpg")]
    assert "term=Depeche%20Mode%20Enjoy%20the%20Silence" in fetch.seen[0] and "entity=song" in fetch.seen[0]


def _rec(*releases):
    return {"recordings": [{"releases": list(releases)}]}


def _rel(group_id, title, primary, secondary=(), status="Official", date=""):
    return {"title": title, "status": status, "date": date,
            "release-group": {"id": group_id, "primary-type": primary, "secondary-types": list(secondary)}}


def test_musicbrainz_ranks_official_singles_and_albums_before_live_and_bootlegs():
    body = _rec(_rel("g1", "Live in Berlin", "Album", ["Live"], date="2014"),
                _rel("g2", "Violator", "Album", date="1990-03-19"),
                _rel("g3", "Enjoy the Silence", "Single", date="1990-02-05"),
                _rel("g2", "Violator (remaster)", "Album", date="2006"),
                _rel("g4", "Some Bootleg", "Album", status="Bootleg"),
                _rel("g5", "Undated", "Album"))
    fetch = replies({"/ws/2/recording": body})
    found = mc.search_musicbrainz("Depeche Mode", "Enjoy the Silence", fetch)
    assert [c.release for c in found] == ["Enjoy the Silence (1990)", "Violator (1990)", "Undated", "Live in Berlin (2014)",
                                          "Some Bootleg"]
    assert found[0].url == "https://coverartarchive.org/release-group/g3/front-500"
    assert "status%3Aofficial" in fetch.seen[0]  # the studio releases are asked for first


def test_musicbrainz_falls_back_to_every_release_and_waits_out_its_rate_limit():
    import urllib.error

    calls, waits = [], []

    def fetch(url):
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 503, "busy", {}, None)
        if "status%3Aofficial" in url:
            return json.dumps({"recordings": []}).encode()
        return json.dumps(_rec(_rel("g9", "Live", "Album", ["Live"]))).encode()

    found = mc.search_musicbrainz("A", "Song", fetch, sleep=waits.append)
    assert [c.release for c in found] == ["Live"] and waits == [2.0] and len(calls) == 3

    def always_busy(url):
        raise urllib.error.HTTPError(url, 503, "busy", {}, None)

    with pytest.raises(mc.CoverSearchError, match="busy"):
        mc.search_musicbrainz("A", "Song", always_busy, sleep=lambda s: None)


class FakeDiscogs:
    def __init__(self, body):
        self.body, self.params = body, None

    def get_json(self, path, **params):
        self.params = (path, params)
        return self.body

    def get_image(self, url):
        return jpeg(600, 600)


def test_discogs_reads_cover_images_once_per_master_and_says_so_without_a_token(monkeypatch):
    client = FakeDiscogs({"results": [
        {"title": "Depeche Mode - Violator", "year": "1990", "master_id": 1, "cover_image": "https://i.discogs.com/a.jpg"},
        {"title": "Depeche Mode - Violator", "year": "1990", "master_id": 1, "cover_image": "https://i.discogs.com/b.jpg"},
        {"title": "Depeche Mode - Single", "cover_image": "https://st.discogs.com/spacer.gif"},
    ]})
    found = mc.search_discogs("Depeche Mode", "Enjoy the Silence", client)
    assert [(c.release, c.url) for c in found] == [("Depeche Mode - Violator (1990)", "https://i.discogs.com/a.jpg")]
    assert client.params == ("database/search", {"type": "release", "per_page": 15, "artist": "Depeche Mode",
                                                 "track": "Enjoy the Silence"})
    monkeypatch.setattr(mc.discogs, "load_token", lambda: "")
    with pytest.raises(mc.CoverSearchError, match="token"):
        mc.search_discogs("A", "B")


def test_find_covers_downloads_judges_and_notes_every_source():
    pictures = {"good": jpeg(1000, 1000), "wide": jpeg(1280, 720), "tiny": jpeg(200, 200)}
    searches = {
        "iTunes": lambda a, t: [mc.CoverCandidate("iTunes", "Violator (1990)", "good"),
                                mc.CoverCandidate("iTunes", "Video still", "wide")],
        "MusicBrainz": lambda a, t: [mc.CoverCandidate("MusicBrainz", "Small", "tiny"),
                                     mc.CoverCandidate("MusicBrainz", "404", "missing")],
        "Discogs": lambda a, t: (_ for _ in ()).throw(mc.CoverSearchError("no Discogs token (Tools > API Keys)")),
    }
    results = mc.find_covers("Depeche Mode", "Enjoy the Silence", searches=searches,
                             download=lambda c: pictures.get(c.url, b""))
    assert [(c.release, c.size) for c in results.candidates] == [("Violator (1990)", (1000, 1000))]
    assert results.notes == ["MusicBrainz: no usable cover (too small (200x200, at least 400x400))",
                             "Discogs: no Discogs token (Tools > API Keys)"]
    assert mc.find_covers("A", " ").notes[0].startswith("no song title")


def test_the_user_agent_has_no_hash_sign():
    assert "#" not in mc.USER_AGENT and mc.musicbrainz.USER_AGENT == mc.USER_AGENT


# -- the window ------------------------------------------------------------------------------------------------------


def test_get_cover_is_visible_only_for_one_music_video(window):
    action = window.actions_by_key["lookup_get_cover"]
    first, second = window.video_files
    first.metadata.content_type = ContentType.MUSIC_VIDEO
    second.metadata.content_type = ContentType.MOVIE
    window._refresh_table_rows()
    _select_only(window, second)
    assert not action.isVisible() and "ctx_get_cover" not in _lookup_texts(window, [second])
    _select_only(window, first)
    assert action.isVisible() and action.isEnabled() and "ctx_get_cover" in _lookup_texts(window, [first])


def test_the_dialog_shows_the_covers_and_the_flow_stages_the_chosen_one_undoably(window, monkeypatch):
    from gui import cover_flow

    vf = window.video_files[0]
    vf.metadata.content_type = ContentType.MUSIC_VIDEO
    vf.metadata.artist, vf.metadata.title = "Depeche Mode", "Enjoy the Silence"
    _select_only(window, vf)
    asked = []

    def find(artist, title, should_cancel=None):
        asked.append((artist, title))
        return mc.CoverResults([mc.CoverCandidate("iTunes", "Violator (1990)", "u1", jpeg(1000, 1000), (1000, 1000)),
                                mc.CoverCandidate("MusicBrainz", "Single (1990)", "u2", jpeg(500, 500, "red"), (500, 500))],
                               ["Discogs: no Discogs token (Tools > API Keys)"])

    dialog = cover_flow.CoverDialog(vf, find=find)
    dialog.start()
    assert asked == [("Depeche Mode", "Enjoy the Silence")]
    assert dialog.results_list.count() == 2 and not dialog.results_list.item(0).icon().isNull()
    assert "Discogs" in dialog.notes_label.text() and dialog.current_label.text() == "None"
    dialog.results_list.setCurrentRow(1)
    assert dialog.use_button.isEnabled()
    dialog._on_accept()
    assert dialog.chosen.source == "MusicBrainz" and (dialog.poster.width, dialog.poster.height) == (500, 500)

    class Accepted:
        def __init__(self, video, parent=None):
            self.chosen, self.poster = dialog.chosen, dialog.poster

        def exec(self):
            return 1

    cover_flow.run_get_cover(window, dialog_class=Accepted)
    assert vf.metadata.cover_pending == dialog.poster.jpeg and vf.dirty
    assert window.status_bar.currentMessage().startswith("Get Cover: cover from MusicBrainz (Single (1990)")
    window.undo_last_action()
    assert vf.metadata.cover_pending is None


def test_the_flow_refuses_anything_but_one_music_video(window):
    from gui import cover_flow

    window.video_files[0].metadata.content_type = ContentType.MOVIE
    _select_only(window, window.video_files[0])
    cover_flow.run_get_cover(window)
    assert window.status_bar.currentMessage() == cover_flow.ONLY_MUSIC_VIDEO
