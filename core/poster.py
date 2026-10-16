"""
core/poster.py

What may be embedded in a video as its cover, and how it is prepared. The owner's rule: a cover is a TRUE POSTER,
never a frame grab: a picture the source itself labels a poster (ThePornDB's posters, TMDB's poster_path, TheTVDB's
poster artwork, a StashDB image that is portrait), portrait in shape, big enough to look right. Nothing in the app
makes a cover from the video's own frames (the side panel's frame preview is a preview only, and is never written).

The rule numbers (judge_poster, one pure function):

    kind             must be "poster" (the source's poster-type image); anything else is refused
    shape            height / width >= 1.3 (a poster is about 2:3 = 1.5) and <= 2.0 (a tall banner is not a poster)
    size             at least 400 x 600 pixels
    "accept landscape" (off by default, per lookup dialog): the shape and size rules are dropped, except that the
                     picture must still be at least 300 pixels wide

prepare_poster() turns downloaded bytes into what is embedded: decoded with Qt's QImage (no extra dependency; Pillow is
not one of this app's requirements), PNG/WebP converted to JPEG (transparency on white), scaled DOWN to at most 1000
pixels wide (never up), JPEG quality 85, and kept under 400 KB (quality 85, 75, 65, 55, then 10 % smaller steps).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

POSTER_KIND = "poster"
MIN_WIDTH, MIN_HEIGHT = 400, 600
MIN_RATIO, MAX_RATIO = 1.3, 2.0
MIN_WIDTH_LANDSCAPE = 300
MAX_WIDTH = 1000
JPEG_QUALITY = 85
MAX_BYTES = 400 * 1024
_QUALITY_STEPS = (85, 75, 65, 55)
MAX_INPUT_BYTES = 30 * 1024 * 1024


class PosterRejected(ValueError):
    """The picture is not a poster this app embeds; the message says why in plain words."""


@dataclass(frozen=True)
class PosterVerdict:
    ok: bool
    reason: str
    width: int = 0
    height: int = 0

    @property
    def shape(self) -> str:
        return f"{self.width}x{self.height}" if self.width and self.height else "unknown size"


def judge_poster(width: int, height: int, kind: str = POSTER_KIND, accept_landscape: bool = False) -> PosterVerdict:
    """Whether a picture of this size and source type is a true poster (see the module text)."""
    if not (isinstance(width, int) and isinstance(height, int)) or width <= 0 or height <= 0:
        return PosterVerdict(False, "its size is unknown", 0, 0)
    if kind != POSTER_KIND:
        return PosterVerdict(False, "the source does not label it a poster", width, height)
    if accept_landscape:
        if width < MIN_WIDTH_LANDSCAPE:
            return PosterVerdict(False, f"too small ({width}x{height}, at least {MIN_WIDTH_LANDSCAPE} pixels wide)", width, height)
        return PosterVerdict(True, "accepted although it is not a portrait poster" if height / width < MIN_RATIO else "poster", width, height)
    ratio = height / width
    if ratio < MIN_RATIO:
        return PosterVerdict(False, f"not portrait ({width}x{height}: a poster is about 2:3)", width, height)
    if ratio > MAX_RATIO:
        return PosterVerdict(False, f"too tall and narrow for a poster ({width}x{height})", width, height)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        return PosterVerdict(False, f"too small ({width}x{height}, at least {MIN_WIDTH}x{MIN_HEIGHT})", width, height)
    return PosterVerdict(True, "poster", width, height)


# --- sizes without decoding (the headers) -----------------------------------------------------------------------------


def image_size(data: bytes) -> Optional[tuple[int, int]]:
    """(width, height) read from a JPEG, PNG or WebP header; None when it is none of these or cut short."""
    if not isinstance(data, (bytes, bytearray)):
        return None
    data = bytes(data)
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xFF:
                i += 1
                continue
            length = struct.unpack(">H", data[i + 2:i + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                height, width = struct.unpack(">HH", data[i + 5:i + 9])
                return (width, height) if width and height else None
            i += 2 + length
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 30:
        chunk = data[12:16]
        if chunk == b"VP8X":
            return (int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1)
        if chunk == b"VP8 " and data[23:26] == b"\x9d\x01\x2a":
            return (struct.unpack("<H", data[26:28])[0] & 0x3FFF, struct.unpack("<H", data[28:30])[0] & 0x3FFF)
        if chunk == b"VP8L" and data[20] == 0x2F:
            bits = int.from_bytes(data[21:25], "little")
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
    return None


def describe_cover(data: bytes) -> str:
    """'JPEG 600x900, 88 KB' (the Sources/cover line of a file)."""
    kind = "JPEG" if data[:3] == b"\xff\xd8\xff" else "PNG" if data[:4] == b"\x89PNG" else "WEBP" if data[8:12] == b"WEBP" else "image"
    size = image_size(data)
    shape = f" {size[0]}x{size[1]}" if size else ""
    return f"{kind}{shape}, {max(1, round(len(data) / 1024))} KB"


# --- preparing the bytes ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PreparedPoster:
    jpeg: bytes
    width: int
    height: int
    verdict: PosterVerdict

    def description(self) -> str:
        return f"JPEG {self.width}x{self.height}, {max(1, round(len(self.jpeg) / 1024))} KB"


def prepare_poster(data: bytes, kind: str = POSTER_KIND, accept_landscape: bool = False) -> PreparedPoster:
    """Downloaded picture bytes -> the JPEG to embed. PosterRejected (with the reason) when it is not a true poster
    or cannot be read or made small enough. The verdict is taken on the DECODED size, not on what the service said."""
    from PyQt6.QtCore import QBuffer, QIODevice, Qt
    from PyQt6.QtGui import QColor, QImage, QPainter

    if not isinstance(data, (bytes, bytearray)) or not data or len(data) > MAX_INPUT_BYTES:
        raise PosterRejected("the picture is empty or far too large")
    image = QImage()
    if not image.loadFromData(bytes(data)) or image.isNull():
        raise PosterRejected("it is not a picture this app can read")
    verdict = judge_poster(image.width(), image.height(), kind, accept_landscape)
    if not verdict.ok:
        raise PosterRejected(verdict.reason)
    flat = QImage(image.size(), QImage.Format.Format_RGB32)
    flat.fill(QColor("white"))
    painter = QPainter(flat)
    painter.drawImage(0, 0, image)
    painter.end()
    if flat.width() > MAX_WIDTH:
        flat = flat.scaledToWidth(MAX_WIDTH, Qt.TransformationMode.SmoothTransformation)

    def encode(img: QImage, quality: int) -> bytes:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not img.save(buffer, "JPG", quality):
            raise PosterRejected("it could not be converted to a JPEG")
        return bytes(buffer.data())

    current = flat
    for _ in range(12):
        for quality in _QUALITY_STEPS:
            out = encode(current, quality)
            if len(out) <= MAX_BYTES:
                return PreparedPoster(out, current.width(), current.height(), verdict)
        current = current.scaledToWidth(max(100, int(current.width() * 0.9)), Qt.TransformationMode.SmoothTransformation)
    raise PosterRejected("it could not be made small enough (400 KB)")


def no_poster_message(service: str) -> str:
    return f"No poster available from {service}"
