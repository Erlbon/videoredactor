"""A small real MP4 for the tests that need real ffmpeg/ffprobe/mutagen behaviour.

`SAMPLE` is the maintainer's sample clip when that file exists; on any other machine a 2-second 320x240 H.264 + AAC
clip is generated once (ffmpeg lavfi testsrc + sine) into a temp folder that is removed at exit. `SKIP_REASON` is ""
when a clip is available and ffprobe exists, else the reason to skip: the tests skip ONLY when no ffmpeg is installed,
never merely because a file on one particular machine is missing."""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile

MAINTAINER_SAMPLE = r"C:\Temp\Claude\exebuild\samples\Show.S01E01.mp4"


def make_sample_mp4(dest: str, seconds: int = 2) -> bool:
    """Generates the tiny sample at `dest` with ffmpeg; True when it was written."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return False
    done = subprocess.run(
        [ffmpeg, "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=24:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", dest],
        capture_output=True, text=True, timeout=120,
    )
    return done.returncode == 0 and os.path.isfile(dest) and os.path.getsize(dest) > 0


def _resolve(maintainer: str = MAINTAINER_SAMPLE) -> tuple[str, str]:
    if shutil.which("ffprobe") is None or shutil.which("ffmpeg") is None:
        return maintainer, "ffmpeg/ffprobe not installed (needed to read or generate the sample clip)"
    if os.path.exists(maintainer):
        return maintainer, ""
    folder = tempfile.mkdtemp(prefix="videoredactor-sample-")
    atexit.register(shutil.rmtree, folder, True)
    path = os.path.join(folder, "Show.S01E01.mp4")
    if make_sample_mp4(path):
        return path, ""
    return maintainer, "ffmpeg could not generate the sample clip"


SAMPLE, SKIP_REASON = _resolve()
