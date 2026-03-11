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
    alpha_value: Optional[int]  # 0-255; None means "do not change alpha values, only clamp"
    threshold: int              # 0-255; pixels with alpha < threshold are affected (0 = all)
    invert: bool
    description: str
    builtin: bool = True
    # Clamp range applied after all other operations
    clamp_min: int = 0
    clamp_max: int = 255
    # When True: pixels >= threshold become 255, pixels < threshold become 0 (hard binary cut)
    binary_cut: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlphaPreset":
        # Drop legacy fields that no longer exist (fill_mode, fill_value)
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: list[AlphaPreset] = [
    # -----------------------------------------------------------------------
    # Full opacity (alpha = 255) — used by multiple platforms
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Full Opacity  (N64 · DS · Wii · Xbox 360 · PS2 BG)",
        alpha_value=255,
        threshold=0,
        invert=False,
        description=(
            "Forces all pixels to full opacity (255/255). "
            "Used by N64, Nintendo DS, Wii/GameCube, Xbox 360, and PS2 background/environment textures — "
            "all expect opaque textures with no transparency channel."
        ),
    ),
    # -----------------------------------------------------------------------
    # PS2 — native full-opaque value is 128 on the 0–128 GS scale.
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PS2 Set Full Opaque  (fill α=128)",
        alpha_value=128,
        threshold=0,
        invert=False,
        description=(
            "Fills every pixel with alpha=128 — PS2's definition of fully opaque. "
            "Use this for PS2 sprite/foreground textures that should be opaque but need the "
            "raw PS2 value preserved (e.g. when targeting a PS2-accurate renderer)."
        ),
    ),
    AlphaPreset(
        name="PS2 Clamp Max 128  (cap α at 128)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps the alpha channel so no pixel exceeds 128. "
            "Useful for PS2 textures that should stay within the 0–128 range."
        ),
        clamp_min=0,
        clamp_max=128,
    ),
    AlphaPreset(
        name="PS2 Clamp Max 150  (cap α at 150)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps the alpha channel so no pixel exceeds 150. "
            "Covers PS2 game textures where the maximum exported alpha is 150 — "
            "seen in some environment and effects layers."
        ),
        clamp_min=0,
        clamp_max=150,
    ),
    AlphaPreset(
        name="PS2 Clamp Max 145  (cap α at 145)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps the alpha channel so no pixel exceeds 145. "
            "Covers PS2 game textures where the maximum exported alpha is 145 — "
            "seen in certain PS2 UI and prop textures."
        ),
        clamp_min=0,
        clamp_max=145,
    ),
    # -----------------------------------------------------------------------
    # PS2 UI / HUD — 75% opacity
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Fade 75%  (PS2 UI / HUD · α=192)",
        alpha_value=192,
        threshold=0,
        invert=False,
        description=(
            "Sets all alpha to 75% opacity (192/255). "
            "Used for PS2 UI/HUD overlays and general fade effects."
        ),
    ),
    # -----------------------------------------------------------------------
    # GBA / PSP — 50% opacity (alpha = 128)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Half Opacity 50%  (GBA · PSP · α=128)",
        alpha_value=128,
        threshold=0,
        invert=False,
        description=(
            "Sets all pixels to 50% opacity (128/255). "
            "Used by Game Boy Advance and PSP blending modes that target 50% alpha. "
            "Note: for PS2 textures use 'PS2 Set Full Opaque' or 'PS2 Clamp Max 128' instead."
        ),
    ),
    # -----------------------------------------------------------------------
    # Fully transparent
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Transparent  (α=0)",
        alpha_value=0,
        threshold=0,
        invert=False,
        description=(
            "Makes the entire image fully transparent (alpha = 0)."
        ),
    ),
    # -----------------------------------------------------------------------
    # iOS / macOS
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="iOS / macOS  (threshold=128 → opaque)",
        alpha_value=255,
        threshold=128,
        invert=False,
        description=(
            "iOS/macOS assets: any pixel whose alpha is below 128 is raised to full opacity (255). "
            "Pixels already at 128 or above are left unchanged."
        ),
    ),
    # -----------------------------------------------------------------------
    # Invert / threshold
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Invert Alpha",
        alpha_value=None,
        threshold=0,
        invert=True,
        description=(
            "Inverts the alpha channel (transparent↔opaque)."
        ),
    ),
    AlphaPreset(
        name="Threshold Cut  (< 128 → 0, ≥ 128 → 255)",
        alpha_value=None,
        threshold=128,
        invert=False,
        description=(
            "Hard cut at 50%: pixels ≥ 128 alpha become fully opaque (255), "
            "pixels < 128 become fully transparent (0)."
        ),
        clamp_min=0,
        clamp_max=255,
        binary_cut=True,
    ),
    # -----------------------------------------------------------------------
    # Fade levels
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Fade 25%  (α=64)",
        alpha_value=64,
        threshold=0,
        invert=False,
        description=(
            "Sets all alpha to 25% opacity (64/255) — very faint ghost effect."
        ),
    ),
    # -----------------------------------------------------------------------
    # Clamp ranges
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Clamp 128–255  (raise floor to 128)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps alpha: pixels below 128 are raised to 128; values above 128 remain unchanged."
        ),
        clamp_min=128,
        clamp_max=255,
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
