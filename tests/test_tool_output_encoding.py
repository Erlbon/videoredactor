"""
Regression tests for tool-output decoding and stdin handling (2026-09-23).

mkvmerge -J and ffprobe -of json write UTF-8. The backends used to call
subprocess.run(..., text=True) with no encoding, which decodes with the
Windows locale codepage (cp1252): an MKV titled "Amélie" loaded as
"AmÃ©lie", and a later Save wrote the mojibake back into the file. They
also never passed stdin=DEVNULL, which lets ffmpeg hang waiting on an
inherited stdin. Both now go through redactor_common's run_tool().

These mock subprocess.run to return raw UTF-8 bytes, exactly what a real
tool writes to a pipe when no text decoding is requested.
"""

import json
import subprocess
from unittest import mock

from core import ffmpeg_backend, mkv_backend


def _fake_run_factory(stdout_bytes: bytes, seen_kwargs: list):
    def fake_run(args, **kwargs):
        seen_kwargs.append(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout_bytes, stderr=b"")
    return fake_run


def test_mkvmerge_title_with_non_ascii_is_decoded_as_utf8():
    payload = json.dumps(
        {"container": {"properties": {"title": "Amélie – Le Fabuleux Destin"}}},
        ensure_ascii=False,
    ).encode("utf-8")
    seen: list = []
    with mock.patch("subprocess.run", side_effect=_fake_run_factory(payload, seen)):
        meta = mkv_backend.read_mkv_metadata("movie.mkv")
    assert meta.title == "Amélie – Le Fabuleux Destin"
    assert all(kw.get("stdin") is subprocess.DEVNULL for kw in seen)
    assert all(kw.get("timeout") for kw in seen)


def test_ffprobe_json_decoded_as_utf8_and_stdin_closed():
    payload = json.dumps({"format": {"format_name": "matroska,webm", "duration": "12.5"}}).encode("utf-8")
    seen: list = []
    with mock.patch("subprocess.run", side_effect=_fake_run_factory(payload, seen)):
        info = ffmpeg_backend.probe_technical_info("movie.mkv")
    assert info["container"] == "matroska"
    assert info["duration_seconds"] == 12.5
    assert seen[0].get("stdin") is subprocess.DEVNULL


def test_hung_tool_times_out_to_a_clean_failure():
    def hang(args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout"))

    with mock.patch("subprocess.run", side_effect=hang):
        assert ffmpeg_backend.probe_technical_info("movie.mkv") == {}
        assert ffmpeg_backend.extract_thumbnail("movie.mkv", "out.jpg", timestamp_seconds=1.0) is False
        ok, message = ffmpeg_backend.remux_to_mp4("movie.mkv", "movie.mp4")
    assert ok is False and "ffmpeg" in message
