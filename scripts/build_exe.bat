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
    if errorlevel 1 exit /b 1
)

REM ── 2. Sync runtime dependencies ────────────────────────────────────────────
echo Installing runtime dependencies from requirements.txt…
python -m pip install -r requirements.txt
if errorlevel 1 exit /b 1
python scripts\bundle_dependencies.py
if errorlevel 1 exit /b 1

REM ── 3. Clean previous build artefacts ───────────────────────────────────────
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

REM ── 4. Run PyInstaller ──────────────────────────────────────────────────────
if "%1"=="--onefile" (
    echo Building single-file executable…
    pyinstaller alpha_fixer_onefile.spec
    if errorlevel 1 exit /b 1
    set "ARTIFACT=dist\AlphaFixerConverter.exe"
    set "LAUNCH_TARGET=!ARTIFACT!"
    set "BUNDLE_KIND=onefile"
    if not exist "!ARTIFACT!" set "ARTIFACT=dist\AlphaFixerConverter"
) else (
    echo Building one-folder application…
    pyinstaller alpha_fixer.spec
    if errorlevel 1 exit /b 1
    set "ARTIFACT=dist\AlphaFixerConverter"
    set "LAUNCH_TARGET=dist\AlphaFixerConverter\AlphaFixerConverter.exe"
    set "BUNDLE_KIND=folder"
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
    if not exist dist\validation-reports mkdir dist\validation-reports
    python scripts\verify_packaged_app.py "!LAUNCH_TARGET!" --offline --smoke-seconds 1.5 --timeout 25 --run-selftest --selftest-iterations 2 --require-selftest-pass --require-core-selftest-checks --require-output-codec-checks --require-video-selftest-checks --require-dds-selftest-checks --require-video-runtime --require-ffmpeg-selfcheck --require-ffprobe-selfcheck --require-bundled-ffmpeg --require-bundled-ffprobe --require-wand-runtime --require-bundled-imagemagick --require-bundled-default-theme-svg --require-packaged-bundle-ready --require-no-missing-libs --bundle-kind !BUNDLE_KIND! --json-out dist\validation-reports\packaged-runtime-audit-windows-!BUNDLE_KIND!.json
    if errorlevel 1 exit /b 1
    set "VERIFY_PRIVATE_ARGS=--require-wand-runtime --require-bundled-imagemagick"
    if "%ALPHA_FIXER_VERIFY_PRIVATE_SAMPLE_MANIFESTS%"=="1" (
        if not defined ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT (
            if defined ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT (
                set "ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT=!ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT!"
            ) else (
                set "ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT=12"
            )
        )
        if not defined ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR set "ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR=%CD%\.sample-cache-private"
        if not defined ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS set "ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS=4"
        if not defined ALPHA_FIXER_PRIVATE_SELFTEST_REPEAT_RUNS set "ALPHA_FIXER_PRIVATE_SELFTEST_REPEAT_RUNS=1"
        if not exist "!ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR!" mkdir "!ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR!"
        set "VERIFY_PRIVATE_ARGS=--require-wand-runtime --require-bundled-imagemagick --run-selftest --selftest-iterations !ALPHA_FIXER_PRIVATE_SELFTEST_ITERATIONS! --selftest-sample-limit !ALPHA_FIXER_PRIVATE_SAMPLE_LIMIT! --require-selftest-pass --require-core-selftest-checks --require-video-selftest-checks --use-private-local-manifests --require-disc-manifest-group-checks --require-dds-manifest-group-checks --allow-sample-downloads --sample-cache-dir ""!ALPHA_FIXER_PRIVATE_SAMPLE_CACHE_DIR!"""
        if not "!ALPHA_FIXER_PRIVATE_SELFTEST_REPEAT_RUNS!"=="1" set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --repeat-selftest-runs !ALPHA_FIXER_PRIVATE_SELFTEST_REPEAT_RUNS!"
        if defined ALPHA_FIXER_PRIVATE_STRESS_LOOPS if not "!ALPHA_FIXER_PRIVATE_STRESS_LOOPS!"=="0" set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --selftest-stress-loops !ALPHA_FIXER_PRIVATE_STRESS_LOOPS! --require-stress-selftest-checks"
        if defined ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_GROWTH_MB set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --max-selftest-rss-growth-mb !ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_GROWTH_MB!"
        if defined ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_SPREAD_MB set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --max-selftest-rss-spread-mb !ALPHA_FIXER_PRIVATE_MAX_SELFTEST_RSS_SPREAD_MB!"
        if defined ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_GROWTH_SECONDS set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --max-smoke-elapsed-growth-seconds !ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_GROWTH_SECONDS!"
        if defined ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_SPREAD_SECONDS set "VERIFY_PRIVATE_ARGS=!VERIFY_PRIVATE_ARGS! --max-smoke-elapsed-spread-seconds !ALPHA_FIXER_PRIVATE_MAX_SMOKE_ELAPSED_SPREAD_SECONDS!"
    )
    if "%ALPHA_FIXER_VERIFY_PUBLIC_SAMPLE_MANIFESTS%"=="1" (
        if not defined ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT set "ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT=4"
        if not defined ALPHA_FIXER_PUBLIC_SELFTEST_REPEAT_RUNS set "ALPHA_FIXER_PUBLIC_SELFTEST_REPEAT_RUNS=1"
        if not defined ALPHA_FIXER_SAMPLE_CACHE_DIR set "ALPHA_FIXER_SAMPLE_CACHE_DIR=%CD%\.sample-cache"
        if not exist "!ALPHA_FIXER_SAMPLE_CACHE_DIR!" mkdir "!ALPHA_FIXER_SAMPLE_CACHE_DIR!"
        set "VERIFY_DDS_ARGS="
        set "VERIFY_PUBLIC_EXTRA_ARGS="
        if not "!ALPHA_FIXER_PUBLIC_SELFTEST_REPEAT_RUNS!"=="1" set "VERIFY_PUBLIC_EXTRA_ARGS=!VERIFY_PUBLIC_EXTRA_ARGS! --repeat-selftest-runs !ALPHA_FIXER_PUBLIC_SELFTEST_REPEAT_RUNS!"
        if defined ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_GROWTH_MB set "VERIFY_PUBLIC_EXTRA_ARGS=!VERIFY_PUBLIC_EXTRA_ARGS! --max-selftest-rss-growth-mb !ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_GROWTH_MB!"
        if defined ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_SPREAD_MB set "VERIFY_PUBLIC_EXTRA_ARGS=!VERIFY_PUBLIC_EXTRA_ARGS! --max-selftest-rss-spread-mb !ALPHA_FIXER_PUBLIC_MAX_SELFTEST_RSS_SPREAD_MB!"
        if defined ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_GROWTH_SECONDS set "VERIFY_PUBLIC_EXTRA_ARGS=!VERIFY_PUBLIC_EXTRA_ARGS! --max-smoke-elapsed-growth-seconds !ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_GROWTH_SECONDS!"
        if defined ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_SPREAD_SECONDS set "VERIFY_PUBLIC_EXTRA_ARGS=!VERIFY_PUBLIC_EXTRA_ARGS! --max-smoke-elapsed-spread-seconds !ALPHA_FIXER_PUBLIC_MAX_SMOKE_ELAPSED_SPREAD_SECONDS!"
        if "%ALPHA_FIXER_REQUIRE_DDS_SELFTEST%"=="1" set "VERIFY_DDS_ARGS=--require-dds-selftest-checks --require-wand-runtime --require-bundled-imagemagick --require-no-packaged-asset-gaps"
        python scripts\verify_packaged_app.py "!LAUNCH_TARGET!" --smoke-seconds 1.5 --timeout 25 --max-smoke-elapsed-seconds 20 --max-smoke-elapsed-growth-seconds 8 --max-smoke-elapsed-spread-seconds 8 --require-video-runtime --require-ffmpeg-selfcheck --require-ffprobe-selfcheck --require-bundled-ffmpeg --require-bundled-ffprobe --require-bundled-default-theme-svg --require-packaged-bundle-ready --require-no-missing-libs --bundle-kind !BUNDLE_KIND! --json-out dist\validation-reports\packaged-runtime-audit-windows-!BUNDLE_KIND!.json !VERIFY_PRIVATE_ARGS! --run-selftest --selftest-iterations 2 --selftest-sample-limit !ALPHA_FIXER_PUBLIC_SAMPLE_LIMIT! --require-selftest-pass --require-core-selftest-checks --use-public-sample-manifests --require-video-selftest-checks --require-public-manifest-checks --require-public-manifest-group-checks --allow-sample-downloads --sample-cache-dir "!ALPHA_FIXER_SAMPLE_CACHE_DIR!" !VERIFY_PUBLIC_EXTRA_ARGS! !VERIFY_DDS_ARGS!
    ) else if "%ALPHA_FIXER_VERIFY_PRIVATE_SAMPLE_MANIFESTS%"=="1" (
        python scripts\verify_packaged_app.py "!LAUNCH_TARGET!" --smoke-seconds 1.5 --timeout 25 --max-smoke-elapsed-seconds 20 --max-smoke-elapsed-growth-seconds 8 --max-smoke-elapsed-spread-seconds 8 --require-video-runtime --require-ffmpeg-selfcheck --require-ffprobe-selfcheck --require-bundled-ffmpeg --require-bundled-ffprobe --require-bundled-default-theme-svg --require-packaged-bundle-ready --require-no-missing-libs --bundle-kind !BUNDLE_KIND! --json-out dist\validation-reports\packaged-runtime-audit-windows-!BUNDLE_KIND!.json !VERIFY_PRIVATE_ARGS!
    )
    if errorlevel 1 (
        exit /b 1
    )
) else (
    echo ERROR: Packaged executable is missing: !LAUNCH_TARGET!
    exit /b 1
)

echo.
echo Build complete!  Output: dist\AlphaFixerConverter
