"""core/subtitle_ingest: finding, decoding, tagging and ordering external .srt files."""

import os

from core import subtitle_ingest as si

V = "F:/Video/Show/Show.S01E01.mkv"


def sub(lang, text="", hint=False, name=None):
    return si.ExternalSub(name or f"x.{lang}.srt", lang, hearing_impaired_hint=hint, text=text)


def test_language_codes():
    assert si.language_code("en") == "eng" and si.language_code("English") == "eng"
    assert si.language_code("nl") == "dut" and si.language_code("pt-BR") == "por"
    assert si.language_code("ger") == "ger" and si.language_code("xx") == "" and si.language_code("") == ""


def test_parse_subtitle_names():
    p = si.parse_subtitle_name
    assert p(V, "F:/Video/Show/Show.S01E01.srt").language == "und"
    assert p(V, "F:/Video/Show/Show.S01E01.en.srt").language == "eng"
    assert p(V, "F:/Video/Show/Show.S01E01.nl.sdh.srt").hearing_impaired_hint
    assert p(V, "F:/Video/Show/Show.S01E01 - German.srt").language == "ger"
    assert p(V, "F:/Video/Show/Show.S01E01.forced.en.srt").language == "eng"
    assert p(V, "F:/Video/Show/Show.S01E02.en.srt") is None  # another episode
    assert p(V, "F:/Video/Show/Show.S01E01.en.sub") is None  # not SubRip
    assert p(V, "F:/Video/Show/Show.S01E01.extras.srt") is None  # something else after the stem


def test_find_external_subs_beside_the_video(tmp_path):
    for n in ("Show.S01E01.mkv", "Show.S01E01.en.srt", "Show.S01E01.fr.srt", "Show.S01E02.en.srt", "Show.S01E01.idx"):
        (tmp_path / n).write_text("x")
    found = si.find_external_subs(str(tmp_path / "Show.S01E01.mkv"))
    assert [os.path.basename(s.path) for s in found] == ["Show.S01E01.en.srt", "Show.S01E01.fr.srt"]
    assert si.find_external_subs(str(tmp_path / "missing" / "a.mkv")) == []


def test_decode_utf8_bom_then_legacy_codepage():
    assert si.decode_srt("Caf\u00e9".encode("utf-8-sig")) == "Caf\u00e9"
    assert si.decode_srt("Za\u017c\u00f3\u0142\u0107".encode("cp1250"), "pol") == "Za\u017c\u00f3\u0142\u0107"  # Polish codepage
    assert si.decode_srt("\u041f\u0440\u0438\u0432\u0435\u0442".encode("cp1251"), "rus") == "\u041f\u0440\u0438\u0432\u0435\u0442"
    assert si.decode_srt(b"caf\xe9", "fre") == "caf\u00e9"  # cp1252 default
    assert si.decode_srt(b"abc\x81") != ""  # never raises


def test_sound_tags_and_similarity():
    plain = "1\n00:00:01,000 --> 00:00:02,000\nHello there\n"
    sdh = "1\n00:00:01,000 --> 00:00:02,000\n[door creaks]\nHello there\n\n2\n00:00:03,000 --> 00:00:04,000\n\u266a music \u266a\n"
    assert si.sound_tag_count(plain) == 0 and si.sound_tag_count(sdh) == 3
    assert si.similarity(plain, plain) == 1.0
    assert si.similarity(plain, "1\n00:00:01,000 --> 00:00:02,000\nCompletely different words\n") < 0.5
    assert si.duplicate_of_embedded(plain, [plain]) and not si.duplicate_of_embedded(plain, ["unrelated text here"])
    assert si.similarity("", plain) == 0.0


def test_whitelist():
    assert si.language_whitelist("all") is None and si.language_whitelist("") is None
    assert si.language_whitelist("en, nl ger") == {"eng", "dut", "ger"}


def test_order_english_first_then_alphabetical():
    subs = [sub("ger"), sub("fre"), sub("eng"), sub("dut")]
    assert [s.language for s in si.prepare(subs, video_had_subtitles=True)] == ["eng", "dut", "fre", "ger"]


def test_default_flag_only_when_video_had_no_subtitles_and_plain_english():
    out = si.prepare([sub("dut"), sub("eng")], video_had_subtitles=False)
    assert [s.default for s in out] == [True, False]
    assert not any(s.default for s in si.prepare([sub("eng")], video_had_subtitles=True))
    assert not any(s.default for s in si.prepare([sub("dut")], video_had_subtitles=False))  # no English


def test_two_tracks_of_one_language_sdh_and_default_goes_to_the_plain_one():
    plain = sub("eng", "1\n00:00:01,000 --> 00:00:02,000\nHello\n", name="a.srt")
    rich = sub("eng", "1\n00:00:01,000 --> 00:00:02,000\n[music]\n\u266a Hello \u266a\n", name="b.srt")
    out = si.prepare([rich, plain], video_had_subtitles=False)
    by = {s.path: s for s in out}
    assert by["b.srt"].title == "(SDH)" and by["b.srt"].hearing_impaired
    assert by["a.srt"].default and not by["b.srt"].default and by["a.srt"].title == ""


def test_three_tracks_are_numbered_and_hint_marks_sdh():
    out = si.prepare([sub("fre", name="1.srt"), sub("fre", name="2.srt"), sub("fre", name="3.srt")], video_had_subtitles=True)
    assert [s.title for s in out] == ["(1)", "(2)", "(3)"]
    single = si.prepare([sub("dut", hint=True)], video_had_subtitles=True)
    assert single[0].hearing_impaired and single[0].title == "(SDH)"
    tie = si.prepare([sub("dut", name="a.srt"), sub("dut", name="b.srt")], video_had_subtitles=True)
    assert [s.title for s in tie] == ["(1)", "(2)"] and not any(s.hearing_impaired for s in tie)


def test_whitelist_filters_in_prepare():
    out = si.prepare([sub("eng"), sub("dut"), sub("ger")], video_had_subtitles=True, whitelist={"eng", "ger"})
    assert [s.language for s in out] == ["eng", "ger"]


def test_load_decodes_with_injected_reader():
    s = si.load(sub("fre"), reader=lambda p: b"caf\xe9")
    assert s.text == "caf\u00e9"
