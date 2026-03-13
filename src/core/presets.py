"""
Preset definitions and manager for the Alpha Fixer tool.

Built-in presets cover common game-console alpha conventions.
Users can create, save, and delete their own presets.
"""
import json
from dataclasses import dataclass, asdict, field
from typing import Optional


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AlphaPreset:
    name: str
    description: str
    builtin: bool = True
    # Output alpha range — always applied as normalize: remap image's actual
    # [img_min, img_max] to [clamp_min, clamp_max].  When clamp_min == clamp_max,
    # every pixel gets that exact value (equivalent to old "set" mode).
    clamp_min: int = 0
    clamp_max: int = 255
    # Advanced operations applied independently of the range remap.
    threshold: int = 0        # only process pixels with alpha < this (0 = all)
    invert: bool = False      # invert alpha before range remap
    binary_cut: bool = False  # hard 0/255 split at threshold after remap

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlphaPreset":
        # Backward compat: old presets stored alpha_value + mode fields.
        # Convert: if a fixed alpha_value was set with a non-normalize mode,
        # translate to clamp_min=clamp_max=alpha_value so behavior is preserved.
        alpha_value = d.get("alpha_value")
        mode = d.get("mode", "set")
        clamp_min = int(d.get("clamp_min", 0))
        clamp_max = int(d.get("clamp_max", 255))
        if alpha_value is not None and mode != "normalize":
            clamp_min = int(alpha_value)
            clamp_max = int(alpha_value)
        # Build with only known fields (drops alpha_value, mode, and any other legacy keys)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["clamp_min"] = clamp_min
        known["clamp_max"] = clamp_max
        return cls(**known)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: list[AlphaPreset] = [
    # -----------------------------------------------------------------------
    # Full opacity — used by multiple platforms
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Full Opacity  (N64 · DS · Wii · GameCube · Xbox 360 · PSP · PS2 BG)",
        clamp_min=255,
        clamp_max=255,
        description=(
            "Sets all pixels to full opacity (alpha = 255). "
            "Used by N64, Nintendo DS, Wii, GameCube, Xbox 360, PSP, and PS2 background/environment "
            "textures — all expect opaque textures with no transparency channel. "
            "Also correct for PCSX2 emulator texture replacements (PC alpha range 0–255)."
        ),
    ),
    # -----------------------------------------------------------------------
    # PS2 — native 0–128 GS scale
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PS2 Full Opaque  (native GS α=128)",
        clamp_min=128,
        clamp_max=128,
        description=(
            "Sets all pixels to PS2 full opacity (alpha = 128). "
            "PS2 Graphics Synthesizer uses a 0–128 alpha scale where 128 = fully opaque. "
            "NOTE: PCSX2 emulator texture replacements typically use standard 0–255 alpha (use 'Full Opacity' above)."
        ),
    ),
    AlphaPreset(
        name="PS2 Half Opacity  (native GS α=64)",
        clamp_min=64,
        clamp_max=64,
        description=(
            "Sets all pixels to PS2 50% opacity (alpha = 64). "
            "PS2 Graphics Synthesizer: 64 is exactly half of the native maximum (128). "
            "Used for semi-transparent PS2 overlays, effects, and UI elements."
        ),
    ),
    AlphaPreset(
        name="PS2 Quarter Opacity  (native GS α=32)",
        clamp_min=32,
        clamp_max=32,
        description=(
            "Sets all pixels to PS2 25% opacity (alpha = 32). "
            "PS2 Graphics Synthesizer: 32 is one quarter of the native maximum (128). "
            "Used for very faint PS2 effects, halos, and particle textures."
        ),
    ),
    AlphaPreset(
        name="PS2 Rescale → 0–128  (remap alpha range to PS2 GS scale)",
        clamp_min=0,
        clamp_max=128,
        description=(
            "Remaps the texture's alpha range to PS2's native 0–128 GS scale. "
            "Example: a texture with alpha 0–255 remaps so 0→0 and 255→128. "
            "Use this to convert standard PC alpha to PS2 hardware format."
        ),
    ),
    AlphaPreset(
        name="PS2 Rescale → 0–255  (remap PS2 alpha to standard PC range)",
        clamp_min=0,
        clamp_max=255,
        description=(
            "Remaps a PS2 texture's alpha (0–128 GS scale) back to the standard 0–255 PC range. "
            "Example: alpha=128 (PS2 fully opaque) → 255; alpha=64 → 128. "
            "Use when importing PS2 textures into a PC pipeline that expects 0–255 alpha."
        ),
    ),
    # -----------------------------------------------------------------------
    # Fade levels (standard 0–255 range)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Fade 75%  (α=192)",
        clamp_min=192,
        clamp_max=192,
        description=(
            "Sets all pixels to 75% opacity (alpha = 192). "
            "Used for PS2 UI/HUD overlays and general fade effects. "
            "PS2 native GS equivalent: alpha=96 (75% of PS2's 0–128 scale)."
        ),
    ),
    AlphaPreset(
        name="Fade 50%  (α=128 · GBA · PSP)",
        clamp_min=128,
        clamp_max=128,
        description=(
            "Sets all pixels to 50% opacity (alpha = 128) in standard 0–255 range. "
            "Used by Game Boy Advance and PSP blending modes that target 50% alpha. "
            "NOTE: for PS2 native hardware use 'PS2 Half Opacity (α=64)' instead."
        ),
    ),
    AlphaPreset(
        name="Fade 25%  (α=64)",
        clamp_min=64,
        clamp_max=64,
        description=(
            "Sets all pixels to 25% opacity (alpha = 64). "
            "Very faint ghost, watermark, or particle effect."
        ),
    ),
    # -----------------------------------------------------------------------
    # Transparent
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Transparent  (α=0)",
        clamp_min=0,
        clamp_max=0,
        description=(
            "Sets the entire image to fully transparent (alpha = 0)."
        ),
    ),
    # -----------------------------------------------------------------------
    # N64 — 1-bit binary alpha
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="N64 Binary Alpha  (1-bit cut: 0 or 255)",
        clamp_min=0,
        clamp_max=255,
        threshold=128,
        binary_cut=True,
        description=(
            "Hard cut at 50%: pixels ≥ 128 alpha → fully opaque (255), pixels < 128 → fully transparent (0). "
            "Matches the N64 1-bit alpha texture format (IA4/IA8/RGBA16) used by many "
            "N64 cartridge textures for characters, foliage, and decals."
        ),
    ),
    # -----------------------------------------------------------------------
    # Invert / threshold
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Invert Alpha",
        clamp_min=0,
        clamp_max=255,
        invert=True,
        description=(
            "Inverts the alpha channel (transparent ↔ opaque). "
            "Useful for textures where the alpha mask polarity is reversed."
        ),
    ),
    AlphaPreset(
        name="Threshold Cut  (< 128 → 0, ≥ 128 → 255)",
        clamp_min=0,
        clamp_max=255,
        threshold=128,
        binary_cut=True,
        description=(
            "Hard cut at 50%: pixels ≥ 128 alpha become fully opaque (255), "
            "pixels < 128 become fully transparent (0). "
            "Useful for creating clean cutout transparency from anti-aliased or blended masks."
        ),
    ),
    # -----------------------------------------------------------------------
    # Clamp ranges
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Clamp 0–128  (cap alpha at 128 · PS2 / PSP)",
        clamp_min=0,
        clamp_max=128,
        description=(
            "Remaps the alpha range to 0–128. "
            "PS2/PSP: values above 128 activate double-bright additive blending. "
            "Use this to keep alpha within the safe 0–128 range."
        ),
    ),
    AlphaPreset(
        name="Clamp 128–255  (raise floor to 128)",
        clamp_min=128,
        clamp_max=255,
        description=(
            "Remaps the alpha range so the minimum is 128. "
            "Useful to eliminate near-transparent pixels that cause edge fringing."
        ),
    ),
]

_BUILTIN_NAMES = {p.name for p in BUILTIN_PRESETS}


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PresetManager:
    """Manages both built-in and user-defined presets."""

    def __init__(self, settings_manager):
        self._settings = settings_manager
        self._custom: list[AlphaPreset] = []
        self._load_custom()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def all_presets(self) -> list[AlphaPreset]:
        return list(BUILTIN_PRESETS) + self._custom

    def get_preset(self, name: str) -> Optional[AlphaPreset]:
        for p in self.all_presets():
            if p.name == name:
                return p
        return None

    def save_custom_preset(self, preset: AlphaPreset) -> bool:
        """Add or update a custom preset.  Returns False if name clashes with a built-in."""
        if preset.name in _BUILTIN_NAMES:
            return False
        preset.builtin = False
        # Replace if exists
        for i, p in enumerate(self._custom):
            if p.name == preset.name:
                self._custom[i] = preset
                self._persist()
                return True
        self._custom.append(preset)
        self._persist()
        return True

    def delete_custom_preset(self, name: str) -> bool:
        before = len(self._custom)
        self._custom = [p for p in self._custom if p.name != name]
        if len(self._custom) < before:
            self._persist()
            return True
        return False

    def custom_presets(self) -> list[AlphaPreset]:
        return list(self._custom)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _persist(self):
        data = [p.to_dict() for p in self._custom]
        self._settings.save_custom_presets(data)

    def _load_custom(self):
        raw = self._settings.get_custom_presets()
        loaded = []
        for d in raw:
            try:
                p = AlphaPreset.from_dict(d)
                p.builtin = False
                loaded.append(p)
            except Exception:
                pass
        self._custom = loaded
