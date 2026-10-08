@echo off
REM build_exe.bat – Build a standalone executable for Windows
REM
REM Usage:
REM   scripts\build_exe.bat            (one-folder build, default)
REM   scripts\build_exe.bat --onefile  (single-file .exe)
REM
REM The finished app lands in  dist\AlphaFixerConverter\

setlocal enabledelayedexpansion

cd /d "%~dp0\.."

REM ── 1. Check / install PyInstaller ──────────────────────────────────────────
python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo PyInstaller not found – installing…
    python -m pip install pyinstaller
)

REM ── 2. Sync runtime dependencies ────────────────────────────────────────────
echo Installing runtime dependencies from requirements.txt…
python -m pip install -r requirements.txt
python -c "import importlib.util, os; has_wand = importlib.util.find_spec('wand') is not None; magick_home = os.environ.get('MAGICK_HOME') or os.environ.get('IMAGEMAGICK_HOME'); print('Build capability audit:'); print('  - imageio/imageio-ffmpeg runtime support will be bundled by the PyInstaller spec.'); print(f'  - DDS compressed variants can be bundled for out-of-box builds (wand + MAGICK_HOME={magick_home}).' if has_wand and magick_home else '  - NOTE: Full bundled DDS compression support needs wand plus MAGICK_HOME/IMAGEMAGICK_HOME set at build time.')"

REM ── 3. Clean previous build artefacts ───────────────────────────────────────
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

REM ── 4. Run PyInstaller ──────────────────────────────────────────────────────
if "%1"=="--onefile" (
    echo Building single-file executable…
    pyinstaller alpha_fixer_onefile.spec
    set "ARTIFACT=dist\AlphaFixerConverter.exe"
    set "LAUNCH_TARGET=!ARTIFACT!"
    if not exist "!ARTIFACT!" set "ARTIFACT=dist\AlphaFixerConverter"
) else (
    echo Building one-folder application…
    pyinstaller alpha_fixer.spec
    set "ARTIFACT=dist\AlphaFixerConverter"
    set "LAUNCH_TARGET=dist\AlphaFixerConverter\AlphaFixerConverter.exe"
)

echo Verifying build artifacts…
if "%1"=="--onefile" (
    if not exist "!ARTIFACT!" (
        echo ERROR: Expected executable not found at !ARTIFACT!
        exit /b 1
    )
    for %%I in ("!ARTIFACT!") do echo Executable verified: %%~zI bytes
) else (
    if not exist "!ARTIFACT!" (
        echo ERROR: Expected app folder not found at !ARTIFACT!
        exit /b 1
    )
    dir /-c "!ARTIFACT!"
)

if exist "!LAUNCH_TARGET!" (
    echo Running packaged validation...
    python scripts\verify_packaged_app.py "!LAUNCH_TARGET!" --smoke-seconds 1.5 --timeout 25 --require-video-runtime --require-no-missing-libs
    if errorlevel 1 (
        exit /b 1
    )
)

echo.
echo Build complete!  Output: dist\AlphaFixerConverter
