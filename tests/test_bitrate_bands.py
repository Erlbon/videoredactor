"""The Bitrate column's quality colours: the pure video profile (core/bitrate_bands.py: bits per pixel per frame, codec
factors, the conversion limit), its agreement with the conversion plan engine (a property test on generated files), the
real MainWindow offscreen (cell colours, tooltips, no colour for unknown, a failed file's red wins, settings edits,
sorting), the Preferences page and the Export/Import round trip of the two thresholds."""

import itertools
import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402
from redactor_common.core.quality_bands import BAND_COLORS, Band  # noqa: E402
from redactor_common.gui.colors import ERROR_COLOR, HIGHLIGHT_TEXT_COLOR  # noqa: E402

from core import bitrate_bands as bb  # noqa: E402
from core import conversion_plan as cp  # noqa: E402
from core.format_helpers import bitrate_bps  # noqa: E402
from core.video_file import VideoFile  # noqa: E402
from core.video_metadata import ContentType  # noqa: E402
from tests.test_per_file_confirmation import close, make_window  # noqa: E402

_app = QApplication.instance() or QApplication([])

CONV = cp.ConversionSettings()
SMALL = 100 * 1048576  # under the 400 MB the rules ignore: never OVER


def vid(width=1920, height=1080, fps=24.0, codec="h264", bps=None, size=SMALL, duration=3000.0, audio=0.0, ext=".mp4",
        series=True):
    return bb.BitrateInput(width=width, height=height, fps=fps, codec=codec, video_bps=bps, size_bytes=size,
                           duration=duration, audio_bps=audio, ext=ext, is_series=series)


def band(inp, **kw):
    return bb.classify(inp, conv=CONV, **kw).band


# -- the pure profile ---------------------------------------------------------------------------------------------------

def test_defaults_and_the_codec_factor_table():
    assert (bb.POOR_BPP, bb.GOOD_BPP) == (0.05, 0.10) and bb.BandSettings() == bb.BandSettings(0.05, 0.10)
    from redactor_common.core.quality_bands import codec_factor as f
    table = bb.VIDEO_CODEC_FACTORS
    expected = {"hevc": 0.6, "h265": 0.6, "av1": 0.5, "vp9": 0.65, "h264": 1.0, "mpeg4": 1.3, "xvid": 1.3, "divx": 1.3,
                "msmpeg4v3": 1.3, "wmv3": 1.3, "vc1": 1.3, "mpeg2video": 1.6, "mpeg1video": 1.0, "unknown_codec": 1.0}
    assert {c: f(c, table) for c in expected} == expected


def test_the_boundary_of_poor_at_1080p_24fps_h264():
    poor_bps = 0.05 * 1920 * 1080 * 24  # 2,488,320 b/s
    assert band(vid(bps=int(poor_bps) - 2)) is Band.POOR
    assert band(vid(bps=int(poor_bps) + 2)) is Band.OK
    assert band(vid(bps=500_000)) is Band.POOR and band(vid(bps=5_000_000)) is Band.OK


@pytest.mark.parametrize("codec,factor", [("h264", 1.0), ("hevc", 0.6), ("av1", 0.5), ("vp9", 0.65), ("mpeg4", 1.3),
                                          ("mpeg2video", 1.6)])
def test_each_codec_factor_moves_the_poor_boundary(codec, factor):
    poor_bps = 0.05 * factor * 1920 * 1080 * 24
    assert band(vid(codec=codec, bps=int(poor_bps * 0.99))) is Band.POOR
    assert band(vid(codec=codec, bps=int(poor_bps * 1.01) + 1)) is Band.OK


def test_the_tooltip_example_for_1080p_hevc():
    result = bb.classify(vid(codec="hevc", bps=2_100_000), conv=CONV)
    assert result.band is Band.OK
    first, second = result.tooltip.split("\n")[:2]
    assert first == "1080p HEVC, 2.1 Mb/s video (0.04 bpp, HEVC-equivalent 0.07)."
    assert second == "Poor below 1.5 Mb/s for HEVC; good from about 3 Mb/s."
    assert "check it" in result.tooltip and "not 'bad'" in result.tooltip
    poor = bb.classify(vid(codec="hevc", bps=1_000_000), conv=CONV)
    assert poor.band is Band.POOR and "Poor: below 1.5 Mb/s." in poor.tooltip


def test_reference_values_for_h264_at_24_fps():
    reference = {480: (0.5, 1.0), 720: (1.1, 2.2), 1080: (2.5, 5.0), 2160: (10.0, 20.0)}
    for height, (poor_mbps, good_mbps) in reference.items():
        width = {480: 854, 720: 1280, 1080: 1920, 2160: 3840}[height]
        poor_bps = bb.POOR_BPP * width * height * 24
        good_bps = bb.GOOD_BPP * width * height * 24
        assert abs(poor_bps / 1e6 - poor_mbps) < 0.25 * poor_mbps + 0.1, height
        assert abs(good_bps / 1e6 - good_mbps) < 0.25 * good_mbps + 0.1, height


def test_the_video_stream_bitrate_is_used_else_the_total_minus_the_audio():
    own = bb.classify(vid(bps=5_000_000, size=SMALL, duration=3000), conv=CONV)
    assert own.band is Band.OK
    # no stream rate: 100 MB over 3000 s is 0.28 Mb/s total, 0.2 after 128 kb/s of audio: poor
    derived = bb.classify(vid(bps=None, audio=128_000), conv=CONV)
    assert derived.band is Band.POOR and "0.2 Mb/s video" in derived.tooltip


def test_unknown_data_gives_no_colour_and_the_tooltip_says_what_is_missing():
    assert bb.classify(vid(width=0, height=0, bps=5_000_000), conv=CONV).band is Band.UNKNOWN
    assert "the resolution" in bb.classify(vid(width=0, height=0, bps=5_000_000), conv=CONV).tooltip
    assert "the frame rate" in bb.classify(vid(fps=None, bps=5_000_000), conv=CONV).tooltip
    no_rate = bb.classify(vid(bps=None, size=None, duration=None), conv=CONV)
    assert no_rate.band is Band.UNKNOWN and "the video bit rate" in no_rate.tooltip
    both = bb.classify(vid(fps=0, bps=None, size=None), conv=CONV)
    assert "the frame rate and the video bit rate" in both.tooltip


def test_a_movie_is_coloured_like_a_series_with_the_information_only_note():
    movie = bb.classify(vid(series=False, bps=500_000), conv=CONV)
    assert movie.band is Band.POOR and "exempt from the conversion rules unless 'apply to movies' is on" in movie.tooltip
    assert "information only" in movie.tooltip
    on = cp.ConversionSettings(apply_to_movies=True)
    assert "information only" not in bb.classify(vid(series=False, bps=500_000), conv=on).tooltip
    big_movie = vid(series=False, bps=9_000_000, size=900 * 1048576, duration=800.0, audio=0)
    assert bb.classify(big_movie, conv=CONV).band is Band.OVER  # judged as if it were a series
    assert "information only" in bb.classify(big_movie, conv=CONV).tooltip


def test_over_names_the_limit_and_the_rules_reason():
    # 720p-or-less, 2.5 Mb/s over the 2.0 limit below 650p
    result = bb.classify(vid(width=854, height=480, bps=6_000_000, size=900 * 1048576, duration=1200.0), conv=CONV)
    assert result.band is Band.OVER
    assert "Convert to MP4/MKV (Rules) would re-encode it" in result.tooltip and "(limit 2)" in result.tooltip
    tall = bb.classify(vid(bps=9_000_000, size=900 * 1048576, duration=800.0), conv=CONV)
    assert tall.band is Band.OVER and "scale to 720p" in tall.tooltip


def test_a_file_under_the_minimum_size_or_a_codec_the_rules_never_touch_is_not_over():
    small = bb.classify(vid(bps=20_000_000, size=300 * 1048576, duration=100.0), conv=CONV)
    assert small.band is Band.OK and "under 400 MB" in small.tooltip
    for odd in (dict(codec="rv40"), dict(codec="mystery"), dict(ext=".iso"), dict(ext=".xyz")):
        assert band(vid(bps=20_000_000, size=900 * 1048576, duration=800.0, **odd)) is not Band.OVER, odd


def test_thresholds_are_validated_stored_and_read_back():
    store = {}
    put = lambda s, k, v: store.__setitem__((s, k), v)  # noqa: E731
    get = lambda s, k, d="": store.get((s, k), d)  # noqa: E731
    assert bb.get_band_settings(get) == bb.BandSettings()
    bb.set_band_settings(bb.BandSettings(0.04, 0.12), put)
    assert bb.get_band_settings(get) == bb.BandSettings(0.04, 0.12)
    for bad in (bb.BandSettings(0.12, 0.04), bb.BandSettings(0.005, 0.1), bb.BandSettings(0.05, 0.6),
                bb.BandSettings(0.05, 0.05)):
        with pytest.raises(ValueError):
            bb.set_band_settings(bad, put)
    store[("bitrate_colours", "poor_bpp")] = "abc"
    assert bb.get_band_settings(get) == bb.BandSettings()  # damaged: the defaults
    store[("bitrate_colours", "poor_bpp")], store[("bitrate_colours", "good_bpp")] = "0.3", "0.2"
    assert bb.get_band_settings(get) == bb.BandSettings()  # poor above good: the defaults
    assert not bb.valid_thresholds(0.0, 0.1) and bb.valid_thresholds(0.01, 0.5)


# -- the table and the conversion rules can never disagree -----------------------------------------------------------------

def _media(height, width, size, duration, codec, ext, audio_rate, video_rate):
    return cp.MediaInfo(
        path="F:/x/Episode" + ext, size_bytes=size, duration=duration,
        streams=[cp.StreamInfo(0, "video", codec, bit_rate=video_rate, width=width, height=height),
                 cp.StreamInfo(1, "audio", "aac", bit_rate=audio_rate)])


def test_over_is_exactly_what_the_plan_engine_re_encodes_for_a_bitrate_in_a_series():
    rng = random.Random(20261017)
    heights = [240, 480, 576, 640, 649, 650, 651, 720, 721, 1080, 1440, 2160]
    sizes = [50, 399, 400, 401, 700, 1023, 1024, 1025, 1500, 4000, 20000]
    codecs = ["h264", "hevc", "av1", "vp9", "mpeg4", "mpeg2video", "mpeg1video", "vp8", "rv40", "wmv3", "mystery", ""]
    exts = [".mp4", ".mkv", ".avi", ".wmv", ".iso", ".xyz", ".MP4"]
    settings = [cp.ConversionSettings(), cp.ConversionSettings(scale_video_mbps=2.5, reencode_video_mbps=1.5,
                                                               reencode_video_mbps_small=1.0, min_size_mb=0),
                cp.ConversionSettings(scale_size_trigger_gb=0.5, scale_if_wider_than=1000, min_size_mb=800)]
    over = total = 0
    for _ in range(4000):
        height = rng.choice(heights)
        width = rng.choice([int(height * 16 / 9), int(height * 4 / 3), 1301, 1280, 0])
        size = rng.choice(sizes) * 1048576 + rng.choice([0, 1, -1])
        duration = rng.choice([None, 0.0, 300.0, 1500.0, 2700.0, 7200.0])
        info = _media(height, width, size, duration, rng.choice(codecs), rng.choice(exts),
                      rng.choice([None, 64_000, 128_000, 640_000]), rng.choice([None, 800_000, 5_000_000]))
        conv = rng.choice(settings)
        driven = cp.plan_conversion(info, conv, is_series=True).bitrate_driven
        shown = bb.classify(bb.input_from_media_info(info, fps=24.0, is_series=True), conv=conv)
        assert (shown.band is Band.OVER) == driven, (info, conv)
        total += 1
        over += driven
        # a movie is judged as a series: the colour does not depend on is_series
        assert bb.classify(bb.input_from_media_info(info, fps=24.0, is_series=False), conv=conv).band == shown.band
    assert 200 < over < total - 200  # the generated files really cover both outcomes


def test_over_agrees_on_a_grid_around_every_limit():
    for height, width, mbps_x10, size_mb in itertools.product((480, 649, 650, 720, 721, 1080), (854, 1301), range(5, 90, 5),
                                                              (399, 400, 1023, 1025)):
        duration = size_mb * 1048576 * 8 / (mbps_x10 / 10 * 1_000_000)
        info = _media(height, width, size_mb * 1048576, duration, "h264", ".mkv", 0, None)
        info.streams[1].bit_rate = 1  # ~0 audio so the file rate is the video rate
        driven = cp.plan_conversion(info, CONV, is_series=True).bitrate_driven
        shown = bb.classify(bb.input_from_media_info(info, fps=24.0), conv=CONV).band
        assert (shown is Band.OVER) == driven, (height, width, mbps_x10, size_mb)


# -- the real window ------------------------------------------------------------------------------------------------------

def set_video(vf, bps, resolution="1920x1080", codec="h264", fps="24", ctype=ContentType.TV, duration=3000.0):
    md = vf.metadata
    md.resolution, md.video_codec, md.frame_rate, md.duration_seconds = resolution, codec, fps, duration
    md.video_bitrate, md.audio_bitrate_total, md.bitrate = (str(bps) if bps else ""), "128000", str((bps or 0) + 128000)
    md.content_type = ctype


@pytest.fixture
def table(monkeypatch, tmp_path):
    sizes = {}
    monkeypatch.setattr(VideoFile, "size_bytes", property(lambda self: sizes.get(self.path.name, 100 * 1048576)))
    w = make_window(monkeypatch, tmp_path, ["Poor.S01E01.mp4", "Fine.S01E02.mp4", "Over.S01E03.mp4", "Unknown.S01E04.mp4"])
    w.sizes = sizes
    poor, fine, over, unknown = w.video_files
    set_video(poor, 500_000)
    set_video(fine, 5_000_000)
    set_video(over, 9_000_000, duration=800.0)
    sizes[over.path.name] = 900 * 1048576
    set_video(unknown, None, resolution="")
    w._refresh_table_rows()
    yield w
    close(w)


def cell(w, vf):
    col = w._column_order.index("bitrate")
    for row in range(w.table.rowCount()):
        if w._file_for_row(row) is vf:
            return w.table.item(row, col)
    raise AssertionError("row not found")


def test_cells_are_coloured_by_band_with_dark_text_and_a_tooltip(table):
    poor, fine, over, unknown = table.video_files
    for vf, band_ in ((poor, Band.POOR), (fine, Band.OK), (over, Band.OVER)):
        item = cell(table, vf)
        assert item.background().color() == QColor(BAND_COLORS[band_]), vf.path.name
        assert item.foreground().color() == HIGHLIGHT_TEXT_COLOR
        assert "bpp" in item.toolTip() and "Poor below" in item.toolTip()
    assert "Above the conversion limit" in cell(table, over).toolTip()
    blank = cell(table, unknown)
    assert blank.background().style().name == "NoBrush"
    assert "No colour" in blank.toolTip() and "the resolution" in blank.toolTip()


def test_a_music_video_a_clip_and_a_movie_are_coloured_too(table):
    poor = table.video_files[0]
    for ctype in (ContentType.MUSIC_VIDEO, ContentType.CLIP, ContentType.MOVIE, ContentType.UNSET, ContentType.MISC):
        poor.metadata.content_type = ctype
        table._refresh_table_rows()
        assert cell(table, poor).background().color() == QColor(BAND_COLORS[Band.POOR]), ctype


def test_the_dirty_tint_does_not_hide_the_band_colour(table):
    fine = table.video_files[1]
    fine.dirty = True
    table._refresh_table_rows()
    assert cell(table, fine).background().color() == QColor(BAND_COLORS[Band.OK])
    other = table.table.item(0, 0)
    assert other is not None  # the other cells of the dirty row keep the row tint (checked in the existing suite)


def test_a_failed_file_keeps_the_red_tint_and_gets_no_band_colour(table):
    poor, fine, over, _unknown = table.video_files
    poor.load_error = "could not read"
    fine.save_error = "disk full"
    table._refresh_table_rows()
    assert cell(table, poor).background().color() == ERROR_COLOR and "bpp" not in cell(table, poor).toolTip()
    saved = cell(table, fine)
    assert saved.background().color() == ERROR_COLOR  # a failed save: red wins
    assert "disk full" in saved.toolTip() and "bpp" in saved.toolTip()  # the error stays, the band text follows
    assert cell(table, over).background().color() == QColor(BAND_COLORS[Band.OVER])  # the others unchanged


def test_changing_the_thresholds_changes_the_colours_live(table):
    fine = table.video_files[1]
    assert cell(table, fine).background().color() == QColor(BAND_COLORS[Band.OK])
    bb.set_band_settings(bb.BandSettings(0.2, 0.4))  # 5 Mb/s is 0.10 bpp: now poor
    table._refresh_table_rows()
    assert cell(table, fine).background().color() == QColor(BAND_COLORS[Band.POOR])
    assert "Poor below" in cell(table, fine).toolTip()


def test_an_updated_probe_changes_the_colour_after_a_refresh(table):
    poor = table.video_files[0]
    set_video(poor, 6_000_000)  # as if re-probed after a conversion
    table._refresh_table_rows()
    assert cell(table, poor).background().color() == QColor(BAND_COLORS[Band.OK])


def test_the_column_still_sorts_numerically_and_the_header_has_the_legend(table):
    from PyQt6.QtCore import Qt
    col = table._column_order.index("bitrate")
    table.table.sortItems(col, Qt.SortOrder.AscendingOrder)
    order = [bitrate_bps(table._file_for_row(r).metadata.bitrate) or 0 for r in range(table.table.rowCount())]
    assert order == sorted(order)
    assert cell(table, table.video_files[0]).background().color() == QColor(BAND_COLORS[Band.POOR])  # sorted, still painted
    tip = table.table.horizontalHeaderItem(col).toolTip()
    assert "yellow = poor" in tip and "green = fine" in tip and "orange = above the conversion limit" in tip


# -- preferences, export / import ------------------------------------------------------------------------------------------

def test_the_conversion_page_edits_and_validates_the_thresholds(monkeypatch, tmp_path):
    import core.config as config
    from gui.video_preferences import ConversionPage

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    page = ConversionPage()
    assert (page.poor_bpp.value(), page.good_bpp.value()) == (0.05, 0.10)
    assert (page.poor_bpp.minimum(), page.poor_bpp.maximum()) == (0.01, 0.5)
    page.poor_bpp.setValue(0.06)
    page.good_bpp.setValue(0.15)
    page.save()
    assert bb.get_band_settings() == bb.BandSettings(0.06, 0.15)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a, **k: warnings.append(text))
    page.poor_bpp.setValue(0.3)
    page.good_bpp.setValue(0.2)
    page.save()
    assert bb.get_band_settings() == bb.BandSettings(0.06, 0.15) and "must be below" in warnings[0]  # not saved


def test_export_and_import_round_trip_the_two_thresholds(monkeypatch, tmp_path):
    import core.config as config
    from core.settings_adapter import SECTIONS, VideoSettingsAdapter

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "settings.ini")
    assert "bitrate_colours" in SECTIONS and SECTIONS["bitrate_colours"][1] is True  # portable
    bb.set_band_settings(bb.BandSettings(0.07, 0.2))
    adapter = VideoSettingsAdapter()
    exported = adapter.read_section("bitrate_colours")
    assert exported == {"poor_bpp": 0.07, "good_bpp": 0.2}
    bb.set_band_settings(bb.BandSettings(0.05, 0.10))
    adapter.write_section("bitrate_colours", exported)
    assert bb.get_band_settings() == bb.BandSettings(0.07, 0.2)
    with pytest.raises(ValueError, match="ignored invalid value"):
        adapter.write_section("bitrate_colours", {"poor_bpp": 9, "good_bpp": 0.3})
    assert bb.get_band_settings() == bb.BandSettings(0.07, 0.3)  # the valid half was written, the bad one named


# -- the real probe feeds the colours ---------------------------------------------------------------------------------------

def test_a_real_clip_is_probed_for_its_video_and_audio_rates_and_coloured(monkeypatch, tmp_path):
    from tests.sample_clip import SAMPLE, SKIP_REASON
    if SKIP_REASON:
        pytest.skip(SKIP_REASON)
    import shutil
    clip = tmp_path / "Show.S01E01.mp4"
    shutil.copy(SAMPLE, clip)
    vf = VideoFile(path=clip)
    vf.load()
    md = vf.metadata
    assert md.resolution and md.video_codec == "h264" and md.frame_rate
    assert int(md.audio_bitrate_total) > 0  # a real stream rate or the rules' estimate
    inp = bb.input_from_file(vf, is_series=True)
    assert inp.width > 0 and inp.fps and inp.size_bytes
    result = bb.classify(inp, conv=CONV)
    assert result.band in (Band.POOR, Band.OK) and "bpp" in result.tooltip  # a tiny clip is never over the limit
