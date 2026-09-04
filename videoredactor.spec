# PyInstaller spec file for The Ʌideo Redactor.
#
# Build on Windows with: pyinstaller videoredactor.spec
# (PyInstaller builds for whatever platform it runs ON -- this cannot be
# cross-compiled from Linux/macOS to produce a Windows .exe, so this file
# is written and reviewed here but must actually be run on Windows.)
#
# IMPORTANT: ffmpeg and MKVToolNix (mkvpropedit/mkvmerge) are NOT bundled
# by this spec -- they're external binaries the app shells out to, same
# "shell out to the real CLI tool" philosophy as the epub tool's Calibre
# integration. The app should detect their absence at runtime and prompt
# the user to install them (matching the epub tool's v35 "Download
# Calibre/Sigil" pattern) rather than silently failing -- that runtime
# check is a follow-up item, not yet built as of this spec file.

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("ABOUT.md", "."),
        ("CHANGELOG.md", "."),
        ("assets/icon.ico", "assets"),
    ],
    hiddenimports=[],
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="TheVideoRedactor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
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
