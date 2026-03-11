"""
rom_detector.py – Detect retro game console folder structures.

When a user opens a folder containing textures extracted from a game disc,
this module recognises the parent-folder naming conventions used by common
emulators and can report:

  • Which console the game is from
  • A best-guess game title (from the disc ID if known, or from the folder name)
  • Where to look for cover art (standard emulator directories)

Supported console heuristics
-----------------------------
  PS2     – SLUS_xxx / SCUS_xxx / SLES_xxx / SLPS_xxx folder or file names,
            or presence of SYSTEM.CNF / SYSTEM.INI
  PSX     – PSX-specific ID patterns (SCUS/SLUS with different structure)
  GameCube– boot.bin / bi2.bin / apploader.img / TOC.bin / sys/ subfolder
  Wii     – Same as GameCube (common.key, ticket.bin patterns)
  N64     – .z64 / .n64 / .v64 ROM file extensions
  GBA     – .gba ROM files
  NDS     – .nds ROM files
  PSP     – EBOOT.PBP / UMD_DATA.BIN / ISO_ROOT patterns
  Dreamcast – .gdi / .cdi / IP.BIN
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class RomDetectionResult:
    """Result of a ROM folder detection pass."""
    console: str = ""          # e.g. "PS2", "GameCube", "N64"
    disc_id: str = ""          # e.g. "SLUS-20626" (empty if not found)
    title_guess: str = ""      # folder name / disc ID prettified
    cover_art_hint: str = ""   # absolute path to cover art file (may be empty)
    confidence: str = ""       # "high" | "medium" | "low"

    @property
    def detected(self) -> bool:
        return bool(self.console)

    def description(self) -> str:
        if not self.detected:
            return ""
        parts = [f"📀 {self.console}"]
        if self.disc_id:
            parts.append(f"ID: {self.disc_id}")
        if self.title_guess:
            parts.append(self.title_guess)
        return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Pattern tables
# ---------------------------------------------------------------------------

# PS2 disc IDs
_PS2_ID_PATTERN = re.compile(
    r"\b(SLUS|SCUS|SLES|SCES|SLPS|SLPM|SLPN|SLAJ|SCES|SCED"
    r"|SLUS|SCAJ|SCED)[_-]?\d{5}\b",
    re.IGNORECASE,
)

# PSX disc IDs (often 3 digits after the suffix)
_PSX_ID_PATTERN = re.compile(
    r"\b(SCUS|SLUS|SLES|SCES|SLPS|SLPM)[_-]?\d{3,5}\b",
    re.IGNORECASE,
)

# File/folder names that indicate each console
_CONSOLE_FILE_SIGNATURES: list[tuple[str, str, str]] = [
    # (console_name, filename_pattern, confidence)
    # PS2
    ("PS2", "SYSTEM.CNF", "high"),
    ("PS2", "SYSTEM.INI", "medium"),
    # GameCube / Wii – only high-confidence disc-image fingerprints (avoid
    # matching common folder names like 'sys' which appear on every Linux box)
    ("GameCube", "boot.bin", "high"),
    ("GameCube", "bi2.bin", "high"),
    ("GameCube", "apploader.img", "high"),
    ("GameCube", "TOC.bin", "high"),
    ("Wii", "common-key.bin", "high"),
    ("Wii", "ticket.bin", "high"),
    # PSP
    ("PSP", "EBOOT.PBP", "high"),
    ("PSP", "UMD_DATA.BIN", "high"),
    # Dreamcast
    ("Dreamcast", "IP.BIN", "high"),
]

# File extensions → console (ROM file detection when files are added directly)
_EXTENSION_MAP: dict[str, str] = {
    ".z64": "N64",
    ".n64": "N64",
    ".v64": "N64",
    ".gba": "GBA",
    ".nds": "Nintendo DS",
    ".gdi": "Dreamcast",
    ".cdi": "Dreamcast",
    ".iso": "",              # ambiguous – need other signals
    ".cso": "PSP",
    ".pbp": "PSP",
}

# Common emulator cover art search roots (cross-platform)
_COVER_ART_ROOTS: list[Path] = [
    # PCSX2 – portable install (next to exe)
    Path.home() / "Documents" / "PCSX2" / "covers",
    # PCSX2 (Linux / Flatpak)
    Path.home() / ".config" / "PCSX2" / "covers",
    # RPCS3 (PS3)
    Path.home() / ".config" / "rpcs3" / "dev_hdd0" / "game",
    # Dolphin (GameCube/Wii) – Linux
    Path.home() / ".local" / "share" / "dolphin-emu" / "Cache" / "GameCovers",
    # Dolphin – macOS
    Path.home() / "Library" / "Application Support" / "Dolphin" / "Cache" / "GameCovers",
    # Dolphin – Windows (portable, via Documents)
    Path.home() / "Documents" / "Dolphin Emulator" / "Cache" / "GameCovers",
    # RetroArch
    Path.home() / ".config" / "retroarch" / "thumbnails",
    # PPSSPP
    Path.home() / ".config" / "ppsspp" / "PSP" / "SYSTEM",
]

_COVER_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_from_paths(paths: list[str]) -> RomDetectionResult:
    """Inspect *paths* and return the best detection result found.

    *paths* may be individual files or folders.  The function scans each
    item and its immediate parent directory for known disc/ROM signatures.
    Returns an empty `RomDetectionResult` (`.detected == False`) if nothing
    is recognised.
    """
    candidates: list[RomDetectionResult] = []

    dirs_checked: set[Path] = set()
    for raw in paths:
        p = Path(raw)
        # Check the item itself (may be a dir) and its parent
        for d in _dirs_to_check(p):
            if d in dirs_checked:
                continue
            dirs_checked.add(d)
            result = _detect_dir(d)
            if result.detected:
                candidates.append(result)
        # Also check extension of file itself
        if p.is_file():
            ext = p.suffix.lower()
            console = _EXTENSION_MAP.get(ext, "")
            if console:
                candidates.append(RomDetectionResult(
                    console=console,
                    title_guess=p.parent.name,
                    confidence="medium",
                ))

    if not candidates:
        return RomDetectionResult()

    # Prefer "high" confidence results, then sort by console specificity
    priority = {"high": 0, "medium": 1, "low": 2}
    candidates.sort(key=lambda r: priority.get(r.confidence, 9))
    best = candidates[0]

    # Attempt to find cover art
    if best.disc_id:
        best.cover_art_hint = _find_cover_art(best.disc_id, best.console)

    return best


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _dirs_to_check(p: Path) -> list[Path]:
    """Return the directories we should inspect for a given path.

    We intentionally stop at the user's home directory to avoid false
    positives from Linux system folders like /sys, /boot, etc.
    """
    dirs = []
    if p.is_dir():
        dirs.append(p)
    # Check the parent directory as well
    parent = p.parent
    if parent and parent != p and parent.exists():
        dirs.append(parent)
    # Check the grandparent only if it's not above home or a system-level root
    gp = parent.parent if parent else None
    if gp and gp != parent and gp.exists() and _is_safe_dir(gp):
        dirs.append(gp)
    return dirs


def _is_safe_dir(d: Path) -> bool:
    """Return True if *d* is a safe directory to scan (not a system root)."""
    home = Path.home()
    # Avoid scanning filesystem roots (/, C:\, D:\, etc.)
    # d.anchor gives the drive root on all platforms ("/" on Unix, "C:\\" on Windows)
    if d == Path(d.anchor):
        return False
    # If the directory is very short (depth ≤ 2 on Linux), it's probably a
    # system-level path — skip it
    parts = d.parts
    if len(parts) <= 2 and d != home:
        return False
    return True


def _detect_dir(d: Path) -> RomDetectionResult:
    """Inspect directory *d* for console fingerprints."""
    if not d.is_dir():
        return RomDetectionResult()

    try:
        names_lower = {n.lower() for n in os.listdir(d)}
    except PermissionError:
        logger.debug("ROM detection: skipping '%s' — permission denied", d)
        return RomDetectionResult()

    # 1) Check file/folder signatures
    for console, sig, confidence in _CONSOLE_FILE_SIGNATURES:
        if sig.lower() in names_lower:
            disc_id, title = _extract_ps2_id(d) if "PS2" in console else ("", "")
            if not disc_id:
                disc_id, title = _extract_id_from_dirname(d.name, console)
            return RomDetectionResult(
                console=console,
                disc_id=disc_id,
                title_guess=title or _prettify(d.name),
                confidence=confidence,
            )

    # 2) Check for PS2 ID patterns in file names or directory name
    disc_id = _scan_for_ps2_id(d, names_lower)
    if disc_id:
        return RomDetectionResult(
            console="PS2",
            disc_id=disc_id,
            title_guess=_prettify(d.name),
            confidence="high",
        )

    # 3) Check directory name itself against PS2 pattern
    m = _PS2_ID_PATTERN.search(d.name)
    if m:
        raw_id = m.group(0).upper().replace("_", "-")
        # Ensure format SLUS-XXXXX
        norm = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", raw_id.replace("_", ""))
        return RomDetectionResult(
            console="PS2",
            disc_id=norm,
            title_guess=_prettify(d.name),
            confidence="medium",
        )

    return RomDetectionResult()


def _extract_ps2_id(d: Path) -> tuple[str, str]:
    """Try to read SYSTEM.CNF inside *d* to get the exact disc ID."""
    cnf = d / "SYSTEM.CNF"
    if not cnf.is_file():
        cnf = d / "system.cnf"
    if not cnf.is_file():
        return "", ""
    try:
        text = cnf.read_text(encoding="ascii", errors="ignore")
        # BOOT2 = cdrom0:\SLUS_206.26;1  (cdrom0: is standard; cdrom: without
        # the trailing 0 appears on some older pressed discs — both are matched)
        m = re.search(r"BOOT2\s*=\s*cdrom0?:\\([^;]+)", text, re.IGNORECASE)
        if m:
            raw = m.group(1).strip().replace("\\", "/").split("/")[-1]
            raw = raw.replace("_", "-").replace(".", "")
            # Insert hyphen: SLUS20626 → SLUS-20626
            norm = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", raw)
            return norm.upper(), ""
    except Exception:
        pass
    return "", ""


def _scan_for_ps2_id(d: Path, names_lower: set[str]) -> str:
    """Check file names inside *d* for PS2 disc IDs (SLUS_xxx etc)."""
    for name in names_lower:
        m = _PS2_ID_PATTERN.match(name.upper())
        if m:
            raw = m.group(0).replace("_", "-")
            norm = re.sub(r"([A-Z]+)(\d+)", r"\1-\2", raw)
            return norm.upper()
    return ""


def _extract_id_from_dirname(name: str, console: str) -> tuple[str, str]:
    """Try to pull a disc ID from a directory name."""
    if console == "PS2":
        m = _PS2_ID_PATTERN.search(name)
        if m:
            raw = m.group(0).replace("_", "-")
            return re.sub(r"([A-Z]+)(\d+)", r"\1-\2", raw.upper()), ""
    return "", ""


def _prettify(name: str) -> str:
    """Turn a raw folder name into something human-readable."""
    # Replace underscores/hyphens with spaces, title-case
    pretty = re.sub(r"[_\-]+", " ", name).strip()
    # Remove leading disc IDs like SLUS-20626
    pretty = re.sub(r"^[A-Z]{2,4}[-_]?\d{3,6}\s*", "", pretty).strip()
    return pretty.title() if pretty else name


def _find_cover_art(disc_id: str, console: str) -> str:
    """Search common emulator cover-art directories for *disc_id*.

    Returns the absolute path to the first matching image, or "" if not found.
    """
    # Normalise disc_id: SLUS-20626 → SLUS_20626, SLUS20626
    variants = {
        disc_id,
        disc_id.replace("-", "_"),
        disc_id.replace("-", ""),
        disc_id.lower(),
        disc_id.lower().replace("-", "_"),
    }

    for root in _COVER_ART_ROOTS:
        if not root.exists():
            continue
        try:
            for entry in root.iterdir():
                stem = entry.stem.upper().replace("_", "-").replace(" ", "-")
                if stem in {v.upper() for v in variants}:
                    for ext in _COVER_EXTENSIONS:
                        candidate = entry.parent / (entry.stem + ext)
                        if candidate.is_file():
                            return str(candidate)
        except (PermissionError, NotADirectoryError):
            continue

    return ""
