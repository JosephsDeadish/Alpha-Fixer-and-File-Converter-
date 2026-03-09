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
    alpha_value: Optional[int]  # 0-255; None means "do not change"
    fill_mode: str              # "set", "multiply", "add", "subtract", "clamp_min", "clamp_max"
    fill_value: int             # used by multiply/add/subtract as percentage/amount
    threshold: int              # 0-255; pixels with alpha < threshold are affected (0 = all)
    invert: bool
    description: str
    builtin: bool = True
    # Clamp range (optional, used when fill_mode is "clamp_min" or "clamp_max")
    clamp_min: int = 0
    clamp_max: int = 255

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlphaPreset":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

BUILTIN_PRESETS: list[AlphaPreset] = [
    AlphaPreset(
        name="PS2",
        alpha_value=128,
        fill_mode="set",
        fill_value=128,
        threshold=0,
        invert=False,
        description="PlayStation 2 – sets all pixels to half-opacity (128/255).",
    ),
    AlphaPreset(
        name="N64",
        alpha_value=255,
        fill_mode="set",
        fill_value=255,
        threshold=0,
        invert=False,
        description="Nintendo 64 – forces full opacity (255/255) on all pixels.",
    ),
    AlphaPreset(
        name="No Alpha",
        alpha_value=255,
        fill_mode="set",
        fill_value=255,
        threshold=0,
        invert=False,
        description="Removes transparency – sets alpha to fully opaque (255).",
    ),
    AlphaPreset(
        name="Max Alpha",
        alpha_value=255,
        fill_mode="set",
        fill_value=255,
        threshold=0,
        invert=False,
        description="Sets all alpha values to maximum (255).",
    ),
    AlphaPreset(
        name="Transparent",
        alpha_value=0,
        fill_mode="set",
        fill_value=0,
        threshold=0,
        invert=False,
        description="Makes the entire image fully transparent (alpha = 0).",
    ),
    AlphaPreset(
        name="Half Transparent",
        alpha_value=128,
        fill_mode="set",
        fill_value=128,
        threshold=0,
        invert=False,
        description="Sets all alpha to 50% opacity.",
    ),
    AlphaPreset(
        name="Invert Alpha",
        alpha_value=None,
        fill_mode="set",
        fill_value=0,
        threshold=0,
        invert=True,
        description="Inverts the alpha channel (transparent↔opaque).",
    ),
    AlphaPreset(
        name="Threshold Cut",
        alpha_value=None,
        fill_mode="clamp_min",
        fill_value=128,
        threshold=128,
        invert=False,
        description="Converts semi-transparent pixels to fully opaque or fully transparent.",
        clamp_min=0,
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
