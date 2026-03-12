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
    # Apply mode: 'set' (default), 'multiply', 'add', 'subtract', or 'normalize'.
    # 'normalize' remaps the image's actual alpha range to [clamp_min, clamp_max].
    mode: str = "set"

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
        name="Full Opacity  (N64 · DS · Wii · GameCube · Xbox 360 · PSP · PS2 BG)",
        alpha_value=255,
        threshold=0,
        invert=False,
        description=(
            "Forces all pixels to full opacity (255/255). "
            "Used by N64, Nintendo DS, Wii, GameCube, Xbox 360, PSP, and PS2 background/environment "
            "textures — all expect opaque textures with no transparency channel. "
            "Also correct for PCSX2 emulator texture replacements (PC alpha range 0–255)."
        ),
    ),
    # -----------------------------------------------------------------------
    # PS2 — native full-opaque value is 128 on the 0–128 GS scale.
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PS2 Full Opaque  (native GS α=128)",
        alpha_value=128,
        threshold=0,
        invert=False,
        description=(
            "PS2 Graphics Synthesizer uses a 0–128 alpha scale where 128 = fully opaque. "
            "Fill every pixel with alpha=128 for fully opaque PS2 textures targeting real PS2 hardware "
            "or a PS2-accurate renderer. "
            "NOTE: PCSX2 emulator texture replacements typically use standard 0–255 alpha (use 'Full Opacity' above)."
        ),
    ),
    AlphaPreset(
        name="PS2 Half Opacity  (native GS α=64)",
        alpha_value=64,
        threshold=0,
        invert=False,
        description=(
            "PS2 Graphics Synthesizer 50% opacity in the native 0–128 scale. "
            "alpha=64 is exactly half of PS2's maximum (128). "
            "Used for semi-transparent PS2 overlays, effects, and UI elements targeting real PS2 hardware."
        ),
    ),
    AlphaPreset(
        name="PS2 Quarter Opacity  (native GS α=32)",
        alpha_value=32,
        threshold=0,
        invert=False,
        description=(
            "PS2 Graphics Synthesizer 25% opacity in the native 0–128 scale. "
            "alpha=32 is one quarter of PS2's maximum (128). "
            "Used for very faint PS2 effects, halos, and particle textures targeting real PS2 hardware."
        ),
    ),
    AlphaPreset(
        name="PS2 Clamp 0–128  (cap α at 128)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps the alpha channel so no pixel exceeds 128 — PS2's native fully-opaque value. "
            "Useful when converting textures for real PS2 hardware so alpha stays within the 0–128 GS range. "
            "Values above 128 on the PS2 GS activate 'double-bright' additive blending."
        ),
        clamp_min=0,
        clamp_max=128,
    ),
    # Normalize: remap any alpha range to the PS2 native 0–128 scale.
    AlphaPreset(
        name="PS2 Normalize → 0–128  (rescale alpha range to PS2 GS scale)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Linearly rescales the texture's existing alpha range to PS2's native 0–128 GS scale. "
            "Example: a texture with alpha 0–255 is remapped so 0→0 and 255→128. "
            "A texture with min=0, max=255, mean=247 becomes min=0, max=128, mean≈62. "
            "Use this instead of 'PS2 Clamp' when you want proportional rescaling rather than hard capping."
        ),
        clamp_min=0,
        clamp_max=128,
        mode="normalize",
    ),
    # Normalize: rescale from PS2 0–128 back to standard 0–255.
    AlphaPreset(
        name="PS2 Normalize → 0–255  (rescale PS2 alpha to standard PC range)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Linearly rescales a PS2 texture's alpha (0–128 GS scale) to the standard 0–255 PC range. "
            "Example: alpha=128 (PS2 fully opaque) → 255; alpha=64 → 128. "
            "Use when importing PS2 textures into a PC pipeline that expects 0–255 alpha."
        ),
        clamp_min=0,
        clamp_max=255,
        mode="normalize",
    ),
    # -----------------------------------------------------------------------
    # PS2 UI / HUD — 75% opacity in standard 0–255 range
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="Fade 75%  (α=192 · PS2 UI / HUD)",
        alpha_value=192,
        threshold=0,
        invert=False,
        description=(
            "Sets all alpha to 75% opacity (192/255 in standard 0–255 range). "
            "Commonly used for PS2 UI/HUD overlays and general fade effects. "
            "PS2 native GS equivalent: alpha=96 (75% of PS2's 0–128 scale)."
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
            "Sets all pixels to 50% opacity (128/255) in standard 0–255 alpha range. "
            "Used by Game Boy Advance and PSP blending modes that target 50% alpha, "
            "and for general half-opacity fade effects. "
            "NOTE: for PS2 native hardware use 'PS2 Half Opacity  (native GS α=64)' instead."
        ),
    ),
    # -----------------------------------------------------------------------
    # N64 — 1-bit binary alpha (common for N64 cartridge textures)
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="N64 Binary Alpha  (1-bit cut: 0 or 255)",
        alpha_value=None,
        threshold=128,
        invert=False,
        description=(
            "Hard cut at 50%: pixels ≥ 128 alpha → fully opaque (255), pixels < 128 → fully transparent (0). "
            "Matches the N64 1-bit alpha texture format (IA4/IA8/RGBA16 with binary transparency) used by many "
            "N64 cartridge textures for characters, foliage, and decals."
        ),
        clamp_min=0,
        clamp_max=255,
        binary_cut=True,
    ),
    # -----------------------------------------------------------------------
    # PSP — specific alpha for PSP texture conventions
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="PSP Full Opacity  (α=255)",
        alpha_value=255,
        threshold=0,
        invert=False,
        description=(
            "Sets all pixels to full opacity (255/255). "
            "PSP uses standard 0–255 alpha. Use this for PSP textures that should be fully opaque "
            "(backgrounds, solid character textures, environmental assets)."
        ),
    ),
    AlphaPreset(
        name="PSP Clamp 0–128  (cap α at 128 for PSP additive effects)",
        alpha_value=None,
        threshold=0,
        invert=False,
        description=(
            "Clamps the alpha channel so no pixel exceeds 128. "
            "Some PSP games use additive blending where alpha values above 128 cause double-bright effects. "
            "Use this to constrain PSP particle/effect textures to the standard 0–128 range."
        ),
        clamp_min=0,
        clamp_max=128,
    ),
    # -----------------------------------------------------------------------
    # GameCube / Wii
    # -----------------------------------------------------------------------
    AlphaPreset(
        name="GameCube / Wii  (TPL α=255 full opaque)",
        alpha_value=255,
        threshold=0,
        invert=False,
        description=(
            "Sets all pixels to full opacity (255/255). "
            "GameCube and Wii textures use standard 0–255 alpha (stored in TPL/BTI format). "
            "Use for solid background, character, and environment textures where transparency is not needed."
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
            "Inverts the alpha channel (transparent↔opaque). "
            "Useful for textures where the alpha mask polarity is reversed from what is expected."
        ),
    ),
    AlphaPreset(
        name="Threshold Cut  (< 128 → 0, ≥ 128 → 255)",
        alpha_value=None,
        threshold=128,
        invert=False,
        description=(
            "Hard cut at 50%: pixels ≥ 128 alpha become fully opaque (255), "
            "pixels < 128 become fully transparent (0). "
            "Useful for creating clean cutout transparency from anti-aliased or blended masks."
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
            "Sets all alpha to 25% opacity (64/255) — very faint ghost or watermark effect."
        ),
    ),
    AlphaPreset(
        name="Fade 50%  (α=128)",
        alpha_value=128,
        threshold=0,
        invert=False,
        description=(
            "Sets all alpha to 50% opacity (128/255) — classic half-transparent overlay effect."
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
            "Clamps alpha: pixels below 128 are raised to 128; values above 128 remain unchanged. "
            "Useful to eliminate near-transparent pixels that cause edge fringing in certain renderers."
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
