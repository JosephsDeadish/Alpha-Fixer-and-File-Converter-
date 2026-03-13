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
    # PS2 — native 0–128 GS scale (range presets, min ≠ max)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PS2 Normalize  (0 → 128)",
        clamp_min=0,
        clamp_max=128,
        description=(
            "Remaps the image's full alpha range to PS2's native 0–128 GS scale. "
            "Source minimum → 0, source maximum → 128. "
            "Fully opaque PC textures (alpha=255) are clamped to PS2 opaque (128). "
            "Use for any texture going to real PS2 hardware — guarantees output is always "
            "within the PS2 GS safe range regardless of source alpha."
        ),
    ),
    AlphaPreset(
        name="PS2 Semi-Transparent  (0 → 64)",
        clamp_min=0,
        clamp_max=64,
        description=(
            "Remaps alpha to 0–64 for PS2 semi-transparent effects. "
            "64 is exactly 50% opacity on PS2's native 0–128 GS scale. "
            "Source minimum → 0, source maximum → 64. "
            "Use for glass, water surfaces, UI overlays, and particle effects on PS2."
        ),
    ),
    AlphaPreset(
        name="PS2 Faint Effects  (0 → 32)",
        clamp_min=0,
        clamp_max=32,
        description=(
            "Remaps alpha to 0–32 for very subtle PS2 transparency. "
            "32 is 25% opacity on PS2's native 0–128 GS scale. "
            "Source minimum → 0, source maximum → 32. "
            "Use for halos, subtle glow auras, and particle trails on PS2 hardware."
        ),
    ),
    AlphaPreset(
        name="PS2 Additive Blend  (128 → 255)",
        clamp_min=128,
        clamp_max=255,
        description=(
            "Remaps alpha to the 128–255 range. "
            "On PS2 GS, values above 128 trigger additive/subtractive blending modes "
            "instead of standard alpha blending. "
            "Source minimum → 128, source maximum → 255. "
            "Use for lens flares, explosions, and bright glow effects on PS2."
        ),
    ),
    # -----------------------------------------------------------------------
    # PS2 — fixed-value presets (min == max, force all pixels to one value)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PS2 Force Opaque  (α=128)",
        clamp_min=128,
        clamp_max=128,
        description=(
            "Sets every pixel to PS2 full opacity (alpha = 128). "
            "128 is 'fully opaque' on PS2's GS native 0–128 scale. "
            "Use for environment, character, and prop textures on real PS2 hardware "
            "where every pixel must be fully opaque regardless of source alpha."
        ),
    ),
    # -----------------------------------------------------------------------
    # Standard PC / DirectX / OpenGL (range presets, min ≠ max)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PC Normalize Full Range  (0 → 255)",
        clamp_min=0,
        clamp_max=255,
        description=(
            "Stretches the source alpha range to the full 0–255 PC scale. "
            "Source minimum → 0, source maximum → 255. "
            "Also converts PS2 native alpha (0–128) back to standard PC range: "
            "PS2 128 (fully opaque) → 255, PS2 64 (half) → 128. "
            "Use when importing PS2 textures into a PC pipeline or emulator replacement pack."
        ),
    ),
    AlphaPreset(
        name="PC Raise Floor  (128 → 255)",
        clamp_min=128,
        clamp_max=255,
        description=(
            "Remaps alpha to the upper half (128–255). "
            "Source minimum → 128, source maximum → 255. "
            "All output pixels are at least 50% opaque. "
            "Use to remove near-transparent fringe pixels or guarantee minimum visibility."
        ),
    ),
    # -----------------------------------------------------------------------
    # PC — fixed-value presets
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PC Full Opacity  (α=255)",
        clamp_min=255,
        clamp_max=255,
        description=(
            "Sets every pixel to full opacity (alpha = 255). "
            "Standard PC / DirectX / OpenGL fully opaque. "
            "Use for PCSX2 texture replacements, N64, DS, Wii, GameCube, Xbox 360, PSP, "
            "and any 0–255 alpha pipeline that expects fully opaque textures."
        ),
    ),
    # -----------------------------------------------------------------------
    # N64 — 1-bit binary alpha
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="N64 Binary Alpha  (0 / 255 cut at 128)",
        clamp_min=0,
        clamp_max=255,
        threshold=128,
        binary_cut=True,
        description=(
            "Hard cut: pixels ≥ 128 → fully opaque (255), pixels < 128 → fully transparent (0). "
            "Matches the N64 1-bit alpha texture formats (IA4/IA8/RGBA16). "
            "Used for N64 characters, foliage, and decal textures."
        ),
    ),
    # -----------------------------------------------------------------------
    # Utilities
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
        name="Force Transparent  (α=0)",
        clamp_min=0,
        clamp_max=0,
        description=(
            "Sets the entire image to fully transparent (alpha = 0). "
            "Use to create invisible layers or clear the alpha channel entirely."
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
