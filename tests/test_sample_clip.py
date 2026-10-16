"""The tests that need a real clip must not depend on one file on one machine: with the maintainer's sample absent a
clip is generated; they skip only when ffmpeg itself is missing."""

import json
import os
import shutil
import subprocess

import pytest

from tests import sample_clip

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg/ffprobe not installed")


def test_a_missing_maintainer_sample_is_replaced_by_a_generated_real_mp4(tmp_path):
    path, reason = sample_clip._resolve(str(tmp_path / "nowhere" / "Show.S01E01.mp4"))
    assert reason == "" and os.path.isfile(path) and not path.startswith(str(tmp_path))
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height", "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    streams = {s["codec_type"]: s for s in json.loads(out)["streams"]}
    assert streams["video"]["codec_name"] == "h264" and streams["video"]["width"] == 320 and "audio" in streams


def test_without_ffmpeg_the_reason_says_so(monkeypatch):
    monkeypatch.setattr(sample_clip.shutil, "which", lambda name: None)
    path, reason = sample_clip._resolve("x.mp4")
    assert "ffmpeg" in reason
