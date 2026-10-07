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
    echo Running packaged capability audit…
    set "CAPABILITY_OUT=%TEMP%\alpha_fixer_capability_audit.txt"
    set "ALPHA_FIXER_RUNTIME_CAPABILITY_DUMP=1"
    "!LAUNCH_TARGET!" > "!CAPABILITY_OUT!" 2>&1
    if errorlevel 1 (
        type "!CAPABILITY_OUT!"
        echo ERROR: Packaged capability audit failed.
        del /q "!CAPABILITY_OUT!" >nul 2>nul
        exit /b 1
    )
    type "!CAPABILITY_OUT!"
    findstr /b /c:"ALPHA_FIXER_RUNTIME_CAPABILITIES=" "!CAPABILITY_OUT!" >nul
    if errorlevel 1 (
        echo ERROR: Packaged capability audit did not emit ALPHA_FIXER_RUNTIME_CAPABILITIES output.
        del /q "!CAPABILITY_OUT!" >nul 2>nul
        exit /b 1
    )
    python -c "import json, pathlib, sys; p=pathlib.Path(sys.argv[1]); prefix='ALPHA_FIXER_RUNTIME_CAPABILITIES='; lines=p.read_text(encoding='utf-8', errors='replace').splitlines(); matches=[raw[len(prefix):] for raw in lines if raw.startswith(prefix)]; assert matches, 'Packaged capability audit did not emit ALPHA_FIXER_RUNTIME_CAPABILITIES output.'; payload=json.loads(matches[-1]); assert payload.get('video_runtime_ready'), 'Packaged runtime audit failed: video_runtime_ready=false'; assert not (payload.get('missing_linux_runtime_libs') or []), 'Packaged runtime audit failed: missing_linux_runtime_libs=' + ','.join(str(name) for name in (payload.get('missing_linux_runtime_libs') or [])); print('WARNING: ffprobe unavailable, odd-container probing stays limited.' if not payload.get('odd_container_probe_ready') else ''); print('WARNING: DDS compressed variants remain unavailable without bundled ImageMagick/wand.' if not payload.get('dds_compression_available') else ''); print('Packaged runtime capability audit verified.')" "!CAPABILITY_OUT!"
    if errorlevel 1 (
        del /q "!CAPABILITY_OUT!" >nul 2>nul
        exit /b 1
    )
    del /q "!CAPABILITY_OUT!" >nul 2>nul
)

echo.
echo Build complete!  Output: dist\AlphaFixerConverter
