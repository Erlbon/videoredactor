@echo off
REM Builds a standalone Windows .exe using PyInstaller.
REM Run this from a Windows machine with Python 3.11+ installed.
REM
REM Checks the result of every step and stops with a clear message on
REM failure, instead of continuing to a false "Done" message.
REM
REM Does NOT bump the version number -- run bump_version.py yourself
REM first if this build should carry a new version. Keeping that a
REM separate, deliberate step means a test/debug build doesn't inflate
REM the official version history every time you run this script.

cd /d "%~dp0"
echo Working directory: %cd%
echo.

echo Checking for Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH.
    echo Install Python 3.11+ from python.org and make sure to check
    echo "Add python.exe to PATH" during installation, then try again.
    pause
    exit /b 1
)
echo.

echo Making this repo's own .venv at the redactor_common pin ^(a minute the first time^)...
REM Never "pip install -r requirements.txt" into the shared Python: one repo's pin then breaks the others (2026-11-04).
python ..\redactor-build-tools\devenv\venv_sync.py .
if errorlevel 1 (
    echo.
    echo ERROR: venv_sync failed. It lives in ..\redactor-build-tools\devenv\ next to this repo;
    echo common causes: that folder is missing, no internet connection, or a proxy/firewall blocking pip.
    pause
    exit /b 1
)
echo.

echo Building videoredactor.exe ...
REM "python -m PyInstaller" with the .venv's python: the pyinstaller console script is often not on PATH.
REM
REM No UPX (2026-10-30): the spec has upx=False, because Defender
REM flagged the UPX-packed release exe as a trojan (false positive).
.venv\Scripts\python -m PyInstaller videoredactor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See the error output above for details.
    echo Common causes: missing Python dependencies or a PyQt6 install
    echo problem -- ffmpeg/MKVToolNix not being on PATH is NOT the issue
    echo here, since those only affect the app at runtime, not the build.
    pause
    exit /b 1
)
echo.

if not exist "dist\videoredactor.exe" (
    echo.
    echo ERROR: PyInstaller reported success but dist\videoredactor.exe
    echo was not found. Please copy the full output above and report it.
    pause
    exit /b 1
)

echo Copying bundled tool binaries ^(ffmpeg.exe, mkvpropedit.exe etc, if present^) alongside the exe...
REM Optional, portable-distribution tier -- promoted from the mp3
REM project's tools\ pattern. Not required: without this, the app
REM still works exactly as before, relying on ffmpeg/MKVToolNix being
REM on PATH. If a local tools\ folder exists (e.g. you've placed
REM portable ffmpeg.exe/ffprobe.exe/mkvpropedit.exe/mkvmerge.exe/
REM mkvextract.exe there yourself), copying it into dist\tools lets
REM core\external_tools.py find them with zero install/PATH setup
REM required on the machine running the built exe.
if exist "tools\" (
    xcopy /E /I /Y tools dist\tools >nul
) else (
    echo   NOTE: no tools\ folder found -- skipping. This is fine; the
    echo   app will look for ffmpeg/MKVToolNix on PATH instead, and warn
    echo   on first launch if either is missing.
)
echo.

echo.
echo ================================================================
echo  SUCCESS. Your app is at: %cd%\dist\videoredactor.exe
echo  That one file ^(plus the dist\tools folder alongside it, if
echo  present^) can be copied anywhere and run.
echo.
echo  Reminder: without a bundled tools\ folder, ffmpeg and MKVToolNix
echo  must be installed separately and on PATH for the app to actually
echo  work -- the app will warn you on first launch if either is
echo  missing. See BUILD.md for details.
echo ================================================================
