"""Real small files for the Set Track Language tests (ffmpeg testsrc2 + sine), the atom walking the tests use to prove
that nothing but the language bytes changed, and a hand-made version-1 mdhd."""

import json
import struct
import subprocess

TRACK_SECONDS = 2


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True, capture_output=True)


def make_mp4(path, audio_tracks=2, languages=None):
    """An MP4 with testsrc2 video and `audio_tracks` sine audio tracks (440 Hz, 880 Hz, ...); no language set."""
    args = ["-f", "lavfi", "-i", f"testsrc2=size=160x120:rate=10:duration={TRACK_SECONDS}"]
    for n in range(audio_tracks):
        args += ["-f", "lavfi", "-i", f"sine=frequency={440 * (n + 1)}:duration={TRACK_SECONDS}"]
    args += ["-map", "0:v"] + [x for n in range(audio_tracks) for x in ("-map", f"{n + 1}:a")]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", "-shortest"]
    for n, language in enumerate(languages or []):
        args += [f"-metadata:s:a:{n}", f"language={language}"]
    ffmpeg(*args, str(path))
    return path


def make_mkv(path, tmp_path, audio_tracks=2, subtitle=True):
    """An MKV with video, `audio_tracks` audio tracks and (optionally) one SRT subtitle track."""
    args = ["-f", "lavfi", "-i", f"testsrc2=size=160x120:rate=10:duration={TRACK_SECONDS}"]
    for n in range(audio_tracks):
        args += ["-f", "lavfi", "-i", f"sine=frequency={440 * (n + 1)}:duration={TRACK_SECONDS}"]
    maps = ["-map", "0:v"] + [x for n in range(audio_tracks) for x in ("-map", f"{n + 1}:a")]
    if subtitle:
        srt = tmp_path / "sub.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
        args += ["-i", str(srt)]
        maps += ["-map", f"{audio_tracks + 1}:s"]
    ffmpeg(*args, *maps, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac",
           *(["-c:s", "srt"] if subtitle else []), "-shortest", str(path))
    return path


def probe_languages(path):
    """{kind: [language, ...]} straight from ffprobe (not through the app's reader)."""
    done = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True)
    out = {}
    for stream in json.loads(done.stdout)["streams"]:
        out.setdefault(stream["codec_type"], []).append((stream.get("tags") or {}).get("language", "und"))
    return out


def top_level_boxes(data):
    pos, boxes = 0, []
    while pos + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, pos)
        if size == 1:
            size = struct.unpack_from(">Q", data, pos + 8)[0]
        elif size == 0:
            size = len(data) - pos
        boxes.append((kind.decode("latin-1"), pos, pos + size))
        pos += size
    return boxes


def without_moov(data):
    """The file's bytes with the moov box cut out: everything the language patch must leave alone."""
    moov = next(b for b in top_level_boxes(data) if b[0] == "moov")
    return data[:moov[1]] + data[moov[2]:], moov


def differing_offsets(a: bytes, b: bytes):
    assert len(a) == len(b)
    return [i for i in range(len(a)) if a[i] != b[i]]


def make_mdhd_version1(path, track_number):
    """Rewrites track `track_number` (0-based) of an MP4 whose moov is the LAST box so that its mdhd is version 1
    (64-bit times: 12 bytes longer; the sizes of mdhd, mdia, trak and moov grow by 12). The media data does not move
    because moov comes after it."""
    data = bytearray(open(path, "rb").read())
    boxes = top_level_boxes(bytes(data))
    assert boxes[-1][0] == "moov", "the test file must have moov at the end"
    moov_at = boxes[-1][1]
    pos, traks = moov_at + 8, []
    while pos < boxes[-1][2]:
        size, kind = struct.unpack_from(">I4s", data, pos)
        if kind == b"trak":
            traks.append(pos)
        pos += size
    trak = traks[track_number]
    mdia = _find(data, trak + 8, "mdia")
    mdhd = _find(data, mdia + 8, "mdhd")
    assert data[mdhd + 8] == 0
    creation, modification, timescale, duration = struct.unpack_from(">IIII", data, mdhd + 12)
    language_and_quality = bytes(data[mdhd + 28:mdhd + 32])
    new_mdhd = (struct.pack(">I4sB3x", 44, b"mdhd", 1) + struct.pack(">QQIQ", creation, modification, timescale, duration)
                + language_and_quality)
    assert len(new_mdhd) == 44
    data[mdhd:mdhd + 32] = new_mdhd
    for box in (mdia, trak, moov_at):
        size = struct.unpack_from(">I", data, box)[0]
        struct.pack_into(">I", data, box, size + 12)
    with open(path, "wb") as handle:
        handle.write(data)


def _find(data, start, kind):
    pos = start
    while True:
        size, name = struct.unpack_from(">I4s", data, pos)
        if name.decode("latin-1") == kind:
            return pos
        pos += size
