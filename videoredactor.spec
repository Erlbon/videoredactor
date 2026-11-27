# PyInstaller spec file for The Ʌideo Redactor.
#
# Build on Windows with: build_exe.bat (both programs and the installer), or
#   .venv/Scripts/python -m PyInstaller videoredactor.spec --noconfirm
# (PyInstaller builds for whatever platform it runs ON -- this cannot be
# cross-compiled from Linux/macOS to produce a Windows .exe. The Linux
# download is built from this same spec by redactor-build-tools.)
#
# IMPORTANT: ffmpeg and MKVToolNix (mkvpropedit/mkvmerge) are NOT bundled
# by this spec -- they're external binaries the app shells out to, same
# "shell out to the real CLI tool" philosophy as the epub tool's Calibre
# integration. The app should detect their absence at runtime and prompt
# the user to install them (matching the epub tool's v35 "Download
# Calibre/Sigil" pattern) rather than silently failing -- that runtime
# check is a follow-up item, not yet built as of this spec file.

import os
import sys

block_cipher = None

# VIDEOREDACTOR_BUILD=folder: the one-folder build the installer ships (dist/videoredactor/videoredactor.exe and its
# _internal folder), which starts faster than the one-file exe (that one unpacks itself at every start). The default
# stays the one-file dist/videoredactor.exe: the portable download, and the file a bare copy's Check for Updates swaps.
FOLDER = os.environ.get("VIDEOREDACTOR_BUILD", "") == "folder"

# The update framework's channel (redactor_common docs/update-framework.md): how this copy updates itself. The one-file
# exe carries "windows-exe" inside (it swaps itself), the Linux one-file build "linux-tarball" (it cannot replace itself
# and says so); the folder build gets "windows-zip" beside its exe below, which the installer's contract replaces with
# "windows-installer".
CHANNEL = os.path.join(workpath, "update-channel")
if not FOLDER:
    os.makedirs(workpath, exist_ok=True)
    with open(CHANNEL, "w", encoding="utf-8") as channel:
        channel.write("windows-exe\n" if sys.platform == "win32" else "linux-tarball\n")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ABOUT.md", "."),
        ("CHANGELOG.md", "."),
        ("CREDITS.md", "."),
        ("assets/icon.ico", "assets"),
        ("assets/tmdb-logo.svg", "assets"),
    ] + ([] if FOLDER else [(CHANNEL, ".")]),
    # On Windows send2trash needs its pywin32 back-end (redactor_common card 64).
    hiddenimports=(["send2trash.win.modern", "win32com.shell.shell", "win32com.shell.shellcon", "pythoncom", "pywintypes"] if sys.platform == "win32" else []),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    *([] if FOLDER else [a.binaries, a.zipfiles, a.datas]),
    [],
    exclude_binaries=FOLDER,
    name="videoredactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX off (2026-10-30): Defender flagged the UPX-packed, unsigned release
    # exe as Trojan:Win32/Yomal!rfn. A larger exe beats one that is quarantined.
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # windowed app -- no console popup, same as the epub tool's exe
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
)

if FOLDER:
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, upx_exclude=[], name="videoredactor")
    with open(os.path.join(DISTPATH, "videoredactor", "update-channel"), "w", encoding="utf-8") as channel:
        channel.write("windows-zip\n")
