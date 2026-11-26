@echo off
REM Builds everything a release publishes, in this repo's clean build venv (.venv-build: no shared packages):
REM   dist\videoredactor.exe        the one-file program (the portable download; update channel windows-exe)
REM   dist\videoredactor-setup.exe  the per-user installer around the one-folder build (channel windows-installer)
REM Run this from a Windows machine with Python 3.11+ and Inno Setup 6 (https://jrsoftware.org/isdl.php; a per-user
REM install is enough).
REM
REM Checks the result of every step and stops with a clear message on
REM failure, instead of continuing to a false "Done" message.
REM
REM Does NOT bump the version number -- run bump_version.py yourself
REM first if this build should carry a new version. Does not publish anything either: a release is made with
REM .venv\Scripts\python -m redactor_common.release publish (pyproject.toml), which runs this with --no-pause.

cd /d "%~dp0"
set "PAUSE=pause"
if "%~1"=="--no-pause" set "PAUSE="
echo Working directory: %cd%
echo.

echo Checking for Python...
python --version
if errorlevel 1 (
    echo.
    echo ERROR: "python" was not found on your PATH.
    echo Install Python 3.11+ from python.org and make sure to check
    echo "Add python.exe to PATH" during installation, then try again.
    %PAUSE%
    exit /b 1
)
echo.

set "ISCC="
for %%P in ("%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe") do (
    if exist %%P set "ISCC=%%~P"
)
if not defined ISCC (
    where iscc >nul 2>nul && set "ISCC=iscc"
)
if not defined ISCC (
    echo ERROR: Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php
    %PAUSE%
    exit /b 1
)

echo Making this repo's clean build venv .venv-build at the redactor_common pin ^(it downloads everything the first time^)...
REM Never "pip install -r requirements.txt" into the shared Python: one repo's pin then breaks the others (2026-11-04).
python ..\redactor-build-tools\devenv\venv_sync.py --build .
if errorlevel 1 (
    echo.
    echo ERROR: venv_sync failed. It lives in ..\redactor-build-tools\devenv\ next to this repo;
    echo common causes: that folder is missing, no internet connection, or a proxy/firewall blocking pip.
    %PAUSE%
    exit /b 1
)
echo.

REM "python -m PyInstaller" with .venv-build's python, which sees only the requirements: with the test .venv
REM (--system-site-packages) PyInstaller also bundled optional packages installed on the PC (+20 MB, 2026-11-26).
REM No UPX: the spec has upx=False (Defender flagged a UPX-packed sibling exe, a false positive).
echo Building dist\videoredactor.exe ...
set "VIDEOREDACTOR_BUILD="
.venv-build\Scripts\python -m PyInstaller videoredactor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed. See the error output above for details.
    %PAUSE%
    exit /b 1
)
if not exist "dist\videoredactor.exe" (
    echo.
    echo ERROR: PyInstaller reported success but dist\videoredactor.exe was not found.
    %PAUSE%
    exit /b 1
)
echo.

echo Building the one-folder program dist\videoredactor\ for the installer ...
set "VIDEOREDACTOR_BUILD=folder"
.venv-build\Scripts\python -m PyInstaller videoredactor.spec --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller failed on the one-folder build. See above.
    %PAUSE%
    exit /b 1
)
set "VIDEOREDACTOR_BUILD="
if not exist "dist\videoredactor\videoredactor.exe" (
    echo.
    echo ERROR: PyInstaller reported success but dist\videoredactor\videoredactor.exe was not found.
    %PAUSE%
    exit /b 1
)
echo.

REM The family's installer contract (relaunch, update-channel, waiting for the program to stop) ships with redactor_common.
set "CONTRACT="
for /f "delims=" %%p in ('.venv-build\Scripts\python -m redactor_common.release contract-path') do set "CONTRACT=%%p"
if not defined CONTRACT (
    echo ERROR: redactor_common's installer contract was not found ^(python -m redactor_common.release contract-path^).
    %PAUSE%
    exit /b 1
)
set "APPBUILD="
for /f "usebackq delims=" %%L in (`.venv-build\Scripts\python -c "from core.version import APP_VERSION; print(APP_VERSION)"`) do set "APPBUILD=%%L"
if not defined APPBUILD (
    echo ERROR: could not read APP_VERSION from core\version.py.
    %PAUSE%
    exit /b 1
)

echo Building dist\videoredactor-setup.exe ...
"%ISCC%" /Q "/DAppBuild=%APPBUILD%" "/DUpdateContract=%CONTRACT%" installer\videoredactor.iss
if errorlevel 1 (
    echo.
    echo ERROR: the installer build failed. See above.
    %PAUSE%
    exit /b 1
)
if not exist "dist\videoredactor-setup.exe" (
    echo.
    echo ERROR: Inno Setup reported success but dist\videoredactor-setup.exe was not found.
    %PAUSE%
    exit /b 1
)

REM ffmpeg and MKVToolNix are external programs this app runs, not data: core\external_tools.py looks for them in
REM tools\ next to the exe. A local build copies a local tools\ folder to dist\tools for this PC; releases do not bundle them.
if exist "tools\" (
    xcopy /E /I /Y tools dist\tools >nul
)

echo.
echo ================================================================
echo  SUCCESS. Built in this repo's clean .venv-build:
echo    %cd%\dist\videoredactor.exe        ^(one file, can be copied anywhere and run^)
echo    %cd%\dist\videoredactor-setup.exe  ^(per-user installer^)
echo ================================================================
%PAUSE%
