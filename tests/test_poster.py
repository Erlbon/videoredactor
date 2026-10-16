"""The true-poster rules and the preparation of the embedded picture (core/poster.py). All pictures are generated here
(solid colours, noise): no real artwork."""

import os
import random

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QBuffer, QIODevice  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from core import poster  # noqa: E402
from core.poster import PosterRejected, describe_cover, image_size, judge_poster, prepare_poster  # noqa: E402

_app = QApplication.instance() or QApplication([])


def encode(image: QImage, fmt: str, quality: int = -1) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, fmt, quality)
    return bytes(buffer.data())


def solid(width, height, fmt="PNG", color=0x336699) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(color)
    return encode(image, fmt)


def noise(width, height, fmt="PNG") -> bytes:
    rng = random.Random(7)
    image = QImage(width, height, QImage.Format.Format_RGB32)
    for y in range(height):
        for x in range(0, width, 1):
            image.setPixel(x, y, rng.getrandbits(24))
    return encode(image, fmt)


def test_the_rule_numbers():
    assert (poster.MIN_WIDTH, poster.MIN_HEIGHT) == (400, 600)
    assert (poster.MIN_RATIO, poster.MAX_RATIO) == (1.3, 2.0)
    assert (poster.MAX_WIDTH, poster.JPEG_QUALITY, poster.MAX_BYTES) == (1000, 85, 400 * 1024)


@pytest.mark.parametrize("w,h,ok", [
    (600, 900, True), (400, 600, True), (2000, 3000, True), (500, 650, True), (500, 1000, True),
    (399, 600, False), (400, 599, False), (300, 450, False),            # too small
    (900, 600, False), (1000, 1000, False), (600, 700, False),           # not portrait (< 1.3)
    (500, 1001, False), (400, 1200, False),                             # a tall banner (> 2.0)
    (0, 900, False), (600, 0, False), (-1, 5, False),
])
def test_judge_poster_numbers(w, h, ok):
    assert judge_poster(w, h).ok is ok


def test_ratio_edges():
    assert judge_poster(1000, 1300).ok                # exactly 1.3
    assert not judge_poster(1000, 1299).ok
    assert judge_poster(500, 1000).ok                 # exactly 2.0
    assert not judge_poster(500, 1001).ok


def test_only_a_poster_type_picture_qualifies():
    assert not judge_poster(600, 900, kind="background").ok
    assert not judge_poster(600, 900, kind="").ok
    assert "does not label it a poster" in judge_poster(600, 900, kind="still").reason


def test_landscape_acceptance_is_explicit_and_keeps_a_minimum():
    assert not judge_poster(900, 600).ok
    assert judge_poster(900, 600, accept_landscape=True).ok
    assert not judge_poster(299, 200, accept_landscape=True).ok
    assert not judge_poster(900, 600, kind="background", accept_landscape=True).ok  # the kind rule still applies
    assert judge_poster(600, 900, accept_landscape=True).ok


def test_non_integers_are_refused():
    assert not judge_poster(600.0, 900.0).ok       # type: ignore[arg-type]
    assert not judge_poster(None, 900).ok          # type: ignore[arg-type]


def test_reasons_say_why():
    assert "not portrait" in judge_poster(900, 600).reason
    assert "too small" in judge_poster(300, 450).reason
    assert "too tall" in judge_poster(400, 1000).reason


@pytest.mark.parametrize("fmt", ["PNG", "JPG"])
def test_image_size_reads_headers(fmt):
    assert image_size(solid(123, 77, fmt)) == (123, 77)


def test_image_size_of_webp_and_junk():
    webp = solid(64, 96, "WEBP")
    if webp[:4] == b"RIFF":  # the WebP plugin is part of the Qt build
        assert image_size(webp) == (64, 96)
    for junk in (b"", b"abc", b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", os.urandom(100), None, "text"):
        assert image_size(junk) is None  # type: ignore[arg-type]


def test_describe_cover():
    assert describe_cover(solid(600, 900, "JPG")).startswith("JPEG 600x900, ")
    assert describe_cover(b"junk-bytes-here").startswith("image, ")


def test_a_poster_is_prepared_as_a_jpeg():
    prepared = prepare_poster(solid(600, 900, "PNG"))
    assert prepared.jpeg[:3] == b"\xff\xd8\xff"
    assert (prepared.width, prepared.height) == (600, 900) and prepared.verdict.ok
    assert prepared.description().startswith("JPEG 600x900, ")


def test_a_big_poster_is_scaled_down_never_up():
    prepared = prepare_poster(solid(2000, 3000, "PNG"))
    assert prepared.width == 1000 and prepared.height == 1500
    small = prepare_poster(solid(450, 700, "PNG"))
    assert (small.width, small.height) == (450, 700)  # not enlarged


def test_webp_and_jpeg_inputs_are_accepted():
    assert prepare_poster(solid(600, 900, "JPG")).jpeg[:3] == b"\xff\xd8\xff"
    webp = solid(600, 900, "WEBP")
    if webp[:4] == b"RIFF":
        assert prepare_poster(webp).jpeg[:3] == b"\xff\xd8\xff"


def test_a_transparent_png_is_flattened_on_white():
    image = QImage(600, 900, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    prepared = prepare_poster(encode(image, "PNG"))
    decoded = QImage()
    assert decoded.loadFromData(prepared.jpeg)
    assert decoded.pixelColor(300, 450).red() > 240  # white, not black


def test_a_landscape_picture_is_rejected_unless_accepted():
    wide = solid(900, 600)
    with pytest.raises(PosterRejected, match="not portrait"):
        prepare_poster(wide)
    assert prepare_poster(wide, accept_landscape=True).verdict.ok
    with pytest.raises(PosterRejected, match="too small"):
        prepare_poster(solid(300, 450))
    with pytest.raises(PosterRejected, match="does not label it a poster"):
        prepare_poster(solid(600, 900), kind="background")


def test_bad_bytes_are_rejected():
    for junk in (b"", b"not a picture at all", os.urandom(2000), None):
        with pytest.raises(PosterRejected):
            prepare_poster(junk)  # type: ignore[arg-type]


def test_the_size_cap_is_kept_for_a_noisy_picture():
    prepared = prepare_poster(noise(1000, 1500))
    assert len(prepared.jpeg) <= poster.MAX_BYTES
    assert prepared.width <= 1000 and prepared.jpeg[:3] == b"\xff\xd8\xff"


def test_no_poster_message():
    assert poster.no_poster_message("ThePornDB") == "No poster available from ThePornDB"
