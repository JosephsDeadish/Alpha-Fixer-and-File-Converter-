"""
Settings manager – persists all application settings using QSettings.

Settings are stored in an INI file next to the executable so they are easy
to find, back-up, and delete when testing.  When running from source the INI
file lands next to main.py; when frozen by PyInstaller it lands next to the
.exe.
"""
import json
import os
import sys
from PyQt6.QtCore import QSettings


APP_NAME = "AlphaFixerConverter"
ORG_NAME = "PandaTools"

# Default custom emoji used when none have been configured yet
DEFAULT_CUSTOM_EMOJI = "✨ ⭐ 💫"


def _settings_ini_path() -> str:
    """Return the absolute path to the INI file that stores all settings.

    Priority:
    1. Next to the frozen executable (sys.frozen / PyInstaller).
    2. Next to main.py when running from source (three levels up from this
       file: src/core/settings_manager.py → src/core → src → project root).
    """
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller bundle – place the INI next to the .exe
        exe_dir = os.path.dirname(sys.executable)
    else:
        # Running from source – project root is three directories above this file
        exe_dir = os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
    return os.path.join(exe_dir, f"{APP_NAME}.ini")


class SettingsManager:
    """Central settings manager backed by QSettings."""

    _DEFAULT_THEME = {
        "name": "Panda Dark",
        "background": "#1a1a2e",
        "surface": "#16213e",
        "primary": "#0f3460",
        "accent": "#e94560",
        "text": "#eaeaea",
        "text_secondary": "#a0a0b0",
        "border": "#2a2a4a",
        "success": "#4caf50",
        "warning": "#ff9800",
        "error": "#f44336",
        "tab_selected": "#e94560",
        "button_bg": "#0f3460",
        "button_hover": "#e94560",
        "progress_bar": "#e94560",
        "input_bg": "#0d1b3e",
        "scrollbar": "#2a2a4a",
        "scrollbar_handle": "#e94560",
        "panda_white": "#f0f0f0",
        "panda_black": "#1a1a1a",
        "_effect": "panda",
    }

    _DEFAULTS = {
        "theme": "Panda Dark",
        "theme_data": json.dumps(_DEFAULT_THEME),
        # Sound
        "sound_enabled": True,
        "click_sound_path": "",
        # Cursor & trail
        "cursor": "Default",
        "use_theme_cursor": False,
        "trail_enabled": False,
        "trail_color": "#e94560",
        "trail_style": "dots",
        "use_theme_trail": False,
        # Appearance
        "font_size": 10,
        # Last-used state
        "last_input_dir": "",
        "last_output_dir": "",
        "last_alpha_preset": "",
        "last_converter_format": "PNG",
        "last_converter_quality": 90,
        # Batch options
        "batch_recursive": True,
        "output_suffix": "",
        "overwrite_originals": False,
        "converter_output_dir": "",
        "converter_recursive": True,
        "converter_keep_metadata": False,
        # Window geometry
        "window_x": 100,
        "window_y": 100,
        "window_w": 1024,
        "window_h": 720,
        "window_maximized": False,
        # Tooltip
        "tooltip_mode": "No Filter 🤬",
        # Click effects
        "click_effects_enabled": True,
        "use_theme_effect": False,
        # Custom emoji particles
        "custom_emoji": DEFAULT_CUSTOM_EMOJI,
        # Unlock flags
        "unlock_skeleton": False,
        "unlock_sakura": False,
        "unlock_ocean": False,
        "unlock_blood_moon": False,
        "unlock_ice_cave": False,
        "unlock_cyber_otter": False,
        "unlock_toxic_neon": False,
        "unlock_lava_cave": False,
        "unlock_sunset_beach": False,
        "unlock_midnight_forest": False,
        "unlock_candy_land": False,
        "unlock_zombie": False,
        "unlock_dragon_fire": False,
        "unlock_bubblegum": False,
        "unlock_thunder_storm": False,
        "unlock_rose_gold": False,
        "unlock_space_cat": False,
        "unlock_magic_mushroom": False,
        "unlock_abyssal_void": False,
        "unlock_spring_bloom": False,
        "unlock_gold_rush": False,
        "unlock_nebula": False,
        "total_clicks": 0,
    }

    def __init__(self):
        self._qs = QSettings(_settings_ini_path(), QSettings.Format.IniFormat)

    # ------------------------------------------------------------------
    # Generic helpers
    # ------------------------------------------------------------------

    def get(self, key, fallback=None):
        default = fallback if fallback is not None else self._DEFAULTS.get(key)
        val = self._qs.value(key, default)
        # QSettings may return strings for bool/int; cast accordingly
        if isinstance(default, bool):
            if isinstance(val, str):
                val = val.lower() in ("true", "1", "yes")
            else:
                val = bool(val)
        elif isinstance(default, int):
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = default
        return val

    def set(self, key, value):
        self._qs.setValue(key, value)
        self._qs.sync()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def get_theme(self) -> dict:
        raw = self.get("theme_data", json.dumps(self._DEFAULT_THEME))
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return dict(self._DEFAULT_THEME)

    def set_theme(self, theme_dict: dict):
        self.set("theme_data", json.dumps(theme_dict))

    # ------------------------------------------------------------------
    # Named custom themes
    # ------------------------------------------------------------------

    def get_saved_themes(self) -> dict:
        """Return {name: theme_dict} for all user-saved named themes."""
        raw = self._qs.value("saved_themes", "{}")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def save_named_theme(self, name: str, theme: dict) -> None:
        saved = self.get_saved_themes()
        saved[name] = theme
        self._qs.setValue("saved_themes", json.dumps(saved))
        self._qs.sync()

    def delete_named_theme(self, name: str) -> bool:
        saved = self.get_saved_themes()
        if name in saved:
            del saved[name]
            self._qs.setValue("saved_themes", json.dumps(saved))
            self._qs.sync()
            return True
        return False

    # ------------------------------------------------------------------
    # Alpha presets
    # ------------------------------------------------------------------

    def get_custom_presets(self) -> list:
        raw = self._qs.value("custom_presets", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def save_custom_presets(self, presets: list):
        self._qs.setValue("custom_presets", json.dumps(presets))
        self._qs.sync()

    # ------------------------------------------------------------------
    # Converter history
    # ------------------------------------------------------------------

    def get_converter_history(self) -> list:
        raw = self._qs.value("converter_history", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def add_converter_history(self, entry: dict, max_entries: int = 50):
        history = self.get_converter_history()
        history.insert(0, entry)
        history = history[:max_entries]
        self._qs.setValue("converter_history", json.dumps(history))
        self._qs.sync()

    # ------------------------------------------------------------------
    # Alpha Fixer history
    # ------------------------------------------------------------------

    def get_alpha_history(self) -> list:
        raw = self._qs.value("alpha_history", "[]")
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []

    def add_alpha_history(self, entry: dict, max_entries: int = 50):
        history = self.get_alpha_history()
        history.insert(0, entry)
        history = history[:max_entries]
        self._qs.setValue("alpha_history", json.dumps(history))
        self._qs.sync()

    def clear_converter_history(self) -> None:
        """Erase all converter history entries."""
        self._qs.setValue("converter_history", "[]")
        self._qs.sync()

    def clear_alpha_history(self) -> None:
        """Erase all alpha-fixer history entries."""
        self._qs.setValue("alpha_history", "[]")
        self._qs.sync()

    # ------------------------------------------------------------------
    # Export / import all settings to a JSON file
    # ------------------------------------------------------------------

    EXPORT_KEYS = [
        "theme", "theme_data", "saved_themes",
        "sound_enabled", "click_sound_path",
        "cursor", "use_theme_cursor", "trail_enabled", "trail_color", "trail_style", "use_theme_trail",
        "font_size",
        "click_effects_enabled", "use_theme_effect", "tooltip_mode",
        "custom_emoji",
        "batch_recursive", "output_suffix", "overwrite_originals",
        "converter_output_dir", "converter_recursive", "converter_keep_metadata",
        "last_alpha_preset", "last_converter_format", "last_converter_quality",
        "custom_presets",
    ]

    def export_settings(self, path: str) -> None:
        """Write a subset of settings to *path* as JSON."""
        data = {}
        for key in self.EXPORT_KEYS:
            val = self._qs.value(key, None)
            if val is not None:
                data[key] = val
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def import_settings(self, path: str) -> list[str]:
        """
        Load settings from a JSON file exported by export_settings().
        Returns a list of keys that were imported.
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        imported = []
        for key in self.EXPORT_KEYS:
            if key in data:
                self._qs.setValue(key, data[key])
                imported.append(key)
        self._qs.sync()
        return imported
