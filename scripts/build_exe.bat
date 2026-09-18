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

REM ── 3. Clean previous build artefacts ───────────────────────────────────────
if exist build   rmdir /s /q build
if exist dist    rmdir /s /q dist

REM ── 4. Run PyInstaller ──────────────────────────────────────────────────────
if "%1"=="--onefile" (
    echo Building single-file executable…
    pyinstaller alpha_fixer_onefile.spec
) else (
    echo Building one-folder application…
    pyinstaller alpha_fixer.spec
)

echo.
echo Build complete!  Output: dist\AlphaFixerConverter
