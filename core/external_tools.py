"""
External tool detection: ffmpeg and MKVToolNix (mkvpropedit/mkvmerge)
are shelled-out-to binaries, not pip packages -- same "real CLI tool"
philosophy as the epub tool's Calibre integration. This module checks
whether they're actually resolvable so the GUI can prompt cleanly
rather than let a missing tool surface as a raw subprocess error,
matching the epub tool's v35 "Download Calibre/Sigil" pattern.

Resolution order, via redactor_common.core.tool_locator.find_tool():
  1. A user-configured override path per executable (settings.ini
     under [tools]) -- e.g. a portable/no-admin install where the user
     can't add to PATH at all.
  2. A copy bundled in tools/ next to a frozen build -- this tier is
     new; this project previously only supported override-or-PATH,
     unlike the mp3 project's equivalent (core/tool_locator.py there),
     which meant there was no way to offer a fully portable,
     no-install-needed distribution the way mp3 could. Promoted here
     from mp3's original pattern.
  3. Whatever's on PATH, for a normal system-wide ffmpeg/MKVToolNix
     install.

Callers (ffmpeg_backend.py, mkv_backend.py) route their subprocess
calls through get_executable_path() so a configured override or a
bundled copy actually gets used, not just detected.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import sys

from core.config import get_setting, set_setting
from redactor_common.core.tool_locator import find_tool

TOOLS_SECTION = "tools"


def _bundled_tools_dir() -> Path:
    """Directory a bundled tools/ folder would live in next to a frozen
    build -- same frozen-vs-dev-mode resolution core/config.py's
    _app_dir() already uses, so a bundled ffmpeg.exe/mkvpropedit.exe
    etc. would sit at that directory's tools/ subfolder, matching the
    mp3 project's equivalent layout.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "tools"
    return Path(__file__).resolve().parent.parent / "tools"


# Every individual executable this app ever shells out to, across both
# ToolInfo groups below -- used to validate a key before writing an
# override for it (see set_tool_override), so a typo'd exe name can't
# silently create a dead settings.ini entry nothing ever reads.
KNOWN_EXECUTABLES = frozenset({"ffmpeg", "ffprobe", "mkvpropedit", "mkvmerge", "mkvextract"})


@dataclass
class ToolInfo:
    name: str
    executables: list[str]   # all binaries this tool provides that we use
    download_url: str
    used_for: str             # short human-readable description for the prompt


FFMPEG = ToolInfo(
    name="ffmpeg",
    executables=["ffmpeg", "ffprobe"],
    download_url="https://ffmpeg.org/download.html",
    used_for="thumbnail previews and MP4 remuxing",
)

MKVTOOLNIX = ToolInfo(
    name="MKVToolNix",
    executables=["mkvpropedit", "mkvmerge", "mkvextract"],
    download_url="https://mkvtoolnix.download/downloads.html",
    used_for="reading and writing MKV metadata",
)

ALL_TOOLS = [FFMPEG, MKVTOOLNIX]


def get_tool_override(exe_name: str) -> str:
    """Return the user-configured override path for `exe_name`, or ''
    if none is set. Exposed separately from get_executable_path() so
    the Settings dialog can show "what's actually configured right
    now" (including a blank field, meaning auto-detect) rather than
    the resolved bare-command-name fallback.
    """
    return get_setting(TOOLS_SECTION, exe_name, "")


def set_tool_override(exe_name: str, path: str) -> None:
    """Set (or, with an empty path, clear) the override for `exe_name`.
    Clearing is just setting an empty string -- get_executable_path()'s
    `if override:` check already treats that as "no override," so there's
    no separate delete path needed; setting empty is unsetting.
    """
    if exe_name not in KNOWN_EXECUTABLES:
        raise ValueError(f"Unknown executable name: {exe_name!r}")
    set_setting(TOOLS_SECTION, exe_name, path)


def get_executable_path(exe_name: str) -> str:
    """Resolve what to actually put in a subprocess arg list for
    `exe_name`. A configured override is always returned as-is, with
    NO existence check at this layer -- that's is_executable_available()'s
    separate job; this layer trusts an explicit override and lets a
    stale one fail loudly via the subprocess call itself rather than
    silently substituting something the user didn't ask for. With no
    override, falls back to a bundled tools/ copy, then to the bare
    exe_name unchanged (letting subprocess resolve it via PATH itself)
    -- never returns an empty string or None, since this always gets
    passed straight into a subprocess args list.
    """
    override = get_tool_override(exe_name)
    if override:
        return override
    found = find_tool(exe_name, tools_dir=_bundled_tools_dir())
    return str(found) if found else exe_name


def is_executable_available(exe_name: str) -> bool:
    """True if this exe is actually resolvable right now -- via a
    configured override that points at a real, existing file (a stale
    override pointing at a since-moved/deleted exe must NOT report as
    available just because a setting exists), a bundled tools/ copy,
    or PATH.
    """
    override = get_tool_override(exe_name)
    return find_tool(exe_name, tools_dir=_bundled_tools_dir(), override=override or None) is not None


def is_tool_available(tool: ToolInfo) -> bool:
    """True only if EVERY executable the tool provides is resolvable --
    a partial install (e.g. mkvmerge present but not mkvpropedit,
    which can happen with a stale/incomplete MKVToolNix install) should
    still be flagged as missing rather than silently treated as OK.
    """
    return all(is_executable_available(exe) for exe in tool.executables)


def missing_tools() -> list[ToolInfo]:
    """Tools that are NOT fully available. Checked at startup so the
    GUI can prompt once up front rather than let a missing tool surface
    later as a confusing mid-operation subprocess failure.
    """
    return [tool for tool in ALL_TOOLS if not is_tool_available(tool)]
