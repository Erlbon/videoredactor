"""Set Track Language, core: REAL small MP4 files made with ffmpeg, languages read back with ffprobe AND the app's own
reader, nothing but the language bytes changing, version 0 and version 1 mdhd, read-only refusal, the journal and its
restore, the Check Files note going away. The MKV path runs against a model of mkvpropedit (MKVToolNix is not installed
on the machine these were written on): it proves the command lines and the verification logic, NOT the real tool."""

import os
import shutil
import stat
import subprocess

import pytest

from core import history, track_language as tl
from core import track_language_codes as codes
from core.file_check import quick_check
from tests.track_language_helpers import (
    differing_offsets, ffmpeg, make_mdhd_version1, make_mkv, make_mp4, probe_languages, without_moov,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed",
)


@pytest.fixture
def mp4(tmp_path):
    return make_mp4(tmp_path / "two audio.mp4")


def _audio(path):
    return [t for t in tl.list_tracks(path) if t.kind == "audio"]


def test_tracks_are_listed_with_und_codec_and_position(mp4):
    tracks = tl.list_tracks(mp4)
    assert [(t.kind, t.position, t.key) for t in tracks] == [("video", 1, "v1"), ("audio", 1, "a1"), ("audio", 2, "a2")]
    assert [t.language for t in tracks] == ["und", "und", "und"]
    assert tracks[1].codec == "aac"


def test_set_one_track_changes_only_that_track_and_only_two_bytes(mp4):
    before = mp4.read_bytes()
    result = tl.set_track_languages(mp4, {_audio(mp4)[1].index: "eng"})
    after = mp4.read_bytes()
    assert [(c.key, c.old, c.new) for c in result.changes] == [("a2", "und", "eng")]
    assert probe_languages(mp4)["audio"] == ["und", "eng"]            # ffprobe itself
    assert [t.language for t in _audio(mp4)] == ["und", "eng"]         # the app's own reader
    assert len(after) == len(before)
    assert len(differing_offsets(before, after)) <= 2                  # the packed language field, nothing else
    media_before, moov_before = without_moov(before)
    media_after, _ = without_moov(after)
    assert media_before == media_after                                 # mdat and every other box: byte-identical
    assert before[moov_before[1]:moov_before[2]] != after[moov_before[1]:moov_before[2]]


def test_packet_checksums_are_unchanged(mp4):
    def packets():
        return subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-map", "0", "-c", "copy", "-f", "streamhash",
                               "-hash", "md5", "-"], capture_output=True, text=True, check=True).stdout
    before = packets()
    tl.set_track_languages(mp4, {t.index: "nor" for t in _audio(mp4)})
    assert packets() == before and probe_languages(mp4)["audio"] == ["nor", "nor"]


def test_version_1_mdhd_is_handled(tmp_path):
    path = make_mp4(tmp_path / "v1.mp4")
    # moov at the end (no faststart) so growing it does not move the media data
    make_mdhd_version1(str(path), 2)
    assert [t.language for t in _audio(path)] == ["und", "und"]
    media_before, _ = without_moov(path.read_bytes())
    tl.set_track_languages(path, {_audio(path)[1].index: "fre", _audio(path)[0].index: "jpn"})
    assert probe_languages(path)["audio"] == ["jpn", "fre"]
    assert without_moov(path.read_bytes())[0] == media_before


def test_unchanged_languages_do_not_touch_the_file(tmp_path):
    path = make_mp4(tmp_path / "labelled.mp4", languages=["eng", "nor"])
    stamp = path.stat().st_mtime_ns
    result = tl.set_track_languages(path, {t.index: t.language for t in _audio(path)})
    assert result.changes == [] and path.stat().st_mtime_ns == stamp


def test_read_only_file_is_refused_and_unchanged(mp4):
    original = mp4.read_bytes()
    os.chmod(mp4, stat.S_IREAD)
    try:
        with pytest.raises(tl.TrackLanguageError, match="read-only"):
            tl.set_track_languages(mp4, {_audio(mp4)[0].index: "eng"})
        assert mp4.read_bytes() == original
    finally:
        os.chmod(mp4, stat.S_IREAD | stat.S_IWRITE)


def test_unsupported_file_is_refused_with_a_reason(tmp_path):
    avi = tmp_path / "x.avi"
    avi.write_bytes(b"RIFF")
    assert "MP4 and MKV" in tl.unsupported_reason(avi)
    with pytest.raises(tl.TrackLanguageError, match="MP4 and MKV"):
        tl.set_track_languages(avi, {0: "eng"})
    assert tl.unsupported_reason("a.MP4") == "" and tl.unsupported_reason("a.mkv") == ""


def test_bad_inputs_are_refused_before_writing(mp4):
    original = mp4.read_bytes()
    with pytest.raises(tl.TrackLanguageError, match="no track 99"):
        tl.set_track_languages(mp4, {99: "eng"})
    with pytest.raises(tl.TrackLanguageError, match="three-letter"):
        tl.set_track_languages(mp4, {_audio(mp4)[0].index: "e1"})
    assert mp4.read_bytes() == original


def test_damaged_atom_layout_is_refused_and_unchanged(mp4):
    data = bytearray(mp4.read_bytes())
    at = data.rfind(b"mdhd") - 4
    data[at:at + 4] = (10_000_000).to_bytes(4, "big")  # an impossible mdhd size
    mp4.write_bytes(bytes(data))
    broken = mp4.read_bytes()
    with pytest.raises(tl.TrackLanguageError):
        tl.set_track_languages(mp4, {2: "eng"})
    assert mp4.read_bytes() == broken


def test_read_back_mismatch_puts_the_old_bytes_back(mp4, monkeypatch):
    original = mp4.read_bytes()
    monkeypatch.setattr(tl, "_verify", lambda *a, **k: "the read-back does not match: test")
    with pytest.raises(tl.TrackLanguageError, match="written back"):
        tl.set_track_languages(mp4, {_audio(mp4)[0].index: "eng"})
    assert mp4.read_bytes() == original


def test_journal_records_old_and_new_and_restore_puts_the_old_back(mp4, journal):
    history.set_history_enabled(True)
    try:
        tl.set_track_languages(mp4, {_audio(mp4)[1].index: "eng"})
        entry = journal.entries()[0]
        assert entry.kind == "save" and entry.status == "ok"
        assert entry.changes == {"track a2 language": ("und", "eng")}
        values, _ = journal.restore_values(entry)
        assert history.apply_restore(str(mp4), values) == ""
        assert probe_languages(mp4)["audio"] == ["und", "und"]
    finally:
        history.set_history_enabled(False)


def test_no_journal_entry_while_history_is_off(mp4, journal):
    tl.set_track_languages(mp4, {_audio(mp4)[1].index: "eng"})
    assert journal.entries() == []


def test_check_files_note_names_the_fix_and_goes_away(mp4):
    note = next(f for f in quick_check(str(mp4)).findings if f.code == "no_language")
    assert "2 audio track(s) without a language" in note.message
    assert "Metadata > Set Track Language" in note.message
    tl.set_track_languages(mp4, {t.index: "eng" for t in _audio(mp4)})
    assert not [f for f in quick_check(str(mp4)).findings if f.code == "no_language"]


def test_plan_changes_unlabelled_only(tmp_path):
    path = make_mp4(tmp_path / "mixed.mp4", languages=["nor", None])
    tracks = tl.list_tracks(path)
    audio = [t for t in tracks if t.kind == "audio"]
    assert [t.language for t in audio] == ["nor", "und"]
    assert tl.plan_changes(tracks, audio="eng") == {audio[1].index: "eng"}
    assert tl.plan_changes(tracks, audio="eng", only_unlabelled=False) == {audio[0].index: "eng", audio[1].index: "eng"}
    assert tl.plan_changes(tracks, subtitle="nor") == {}


def test_field_names():
    assert tl.field_name("a1") == "track a1 language"
    assert tl.key_of_field("track s2 language") == "s2" and tl.key_of_field("title") == "" and tl.key_of_field("track x1 language") == ""


def test_packed_language_round_trip():
    assert tl.decode_packed(tl.encode_packed("eng")) == "eng"
    assert tl.encode_packed("und") == 0x55C4
    assert tl.decode_packed(0x7FFF) == "und" and tl.decode_packed(0) is None


def test_language_table_order_and_aliases():
    labels = [code for code, _ in codes.choices()]
    assert labels[:12] == ["eng", "nor", "swe", "dan", "ger", "fre", "spa", "ita", "jpn", "kor", "chi", "rus"]
    assert labels[-1] == "und" and len(set(labels)) == len(labels)
    rest = [name for _, _, name in codes.LANGUAGES[12:]]
    assert rest == sorted(rest)
    assert codes.normalize("deu") == "ger" and codes.normalize("") == "und" and codes.normalize("en") == "eng"
    assert codes.ietf_of("nor") == "no"


# --- MKV, against a model of mkvpropedit --------------------------------------------------------------------------

class FakeMkvpropedit:
    """Understands `--edit track:a1 --set language=xxx [--set language-ietf=yy]` and applies it with a real ffmpeg
    stream copy (so the verification reads a really changed file)."""

    def __init__(self, tmp_path, returncode=0, apply=True):
        self.calls, self.tmp, self.returncode, self.apply = [], tmp_path, returncode, apply

    def __call__(self, path, args):
        self.calls.append(list(args))
        if self.returncode or not self.apply:
            return subprocess.CompletedProcess(args, self.returncode, "", "Error: boom" if self.returncode else "")
        meta, selector = [], None
        for i, arg in enumerate(args):
            if arg == "--edit":
                selector = args[i + 1].split(":", 1)[1]
            elif arg == "--set" and args[i + 1].startswith("language="):
                kind = {"a": "a", "s": "s", "v": "v"}[selector[0]]
                meta += [f"-metadata:s:{kind}:{int(selector[1:]) - 1}", args[i + 1]]
        out = str(self.tmp / "fake_out.mkv")
        ffmpeg("-i", path, "-map", "0", "-c", "copy", *meta, out)
        os.replace(out, path)
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def mkv(tmp_path, monkeypatch):
    path = make_mkv(tmp_path / "movie.mkv", tmp_path)
    monkeypatch.setattr(tl, "is_tool_available", lambda tool: True)
    monkeypatch.setattr(tl, "_mkv_ietf_keys", lambda p: {"a1"})
    return path


def test_mkv_tracks_and_command_line(mkv, tmp_path, monkeypatch):
    fake = FakeMkvpropedit(tmp_path)
    monkeypatch.setattr("core.mkv_backend.run_mkvpropedit", fake)
    tracks = tl.list_tracks(mkv)
    assert [t.key for t in tracks] == ["v1", "a1", "a2", "s1"]
    audio1, audio2, sub = tracks[1], tracks[2], tracks[3]
    result = tl.set_track_languages(mkv, {audio1.index: "eng", audio2.index: "nor", sub.index: "swe"})
    assert [c.key for c in result.changes] == ["a1", "a2", "s1"]
    assert fake.calls == [[
        "--edit", "track:a1", "--set", "language=eng", "--set", "language-ietf=en",   # a1 carries a language-ietf
        "--edit", "track:a2", "--set", "language=nor",
        "--edit", "track:s1", "--set", "language=swe",
    ]]
    languages = probe_languages(mkv)
    assert languages["audio"] == ["eng", "nor"] and languages["subtitle"] == ["swe"]


def test_mkv_without_mkvtoolnix_says_so(mkv, monkeypatch):
    monkeypatch.setattr(tl, "is_tool_available", lambda tool: False)
    with pytest.raises(tl.TrackLanguageError, match="MKVToolNix is not installed"):
        tl.set_track_languages(mkv, {1: "eng"})


def test_mkv_tool_failure_is_reported_with_the_tools_words(mkv, tmp_path, monkeypatch):
    monkeypatch.setattr("core.mkv_backend.run_mkvpropedit", FakeMkvpropedit(tmp_path, returncode=2))
    with pytest.raises(tl.TrackLanguageError, match="mkvpropedit failed.*boom"):
        tl.set_track_languages(mkv, {1: "eng"})


def test_mkv_edit_that_does_not_read_back_is_reported(mkv, tmp_path, monkeypatch):
    monkeypatch.setattr("core.mkv_backend.run_mkvpropedit", FakeMkvpropedit(tmp_path, apply=False))
    with pytest.raises(tl.TrackLanguageError, match="read-back does not match"):
        tl.set_track_languages(mkv, {1: "eng"})
