"""
Settings manager – persists all application settings using QSettings.
"""
import json
import os
from PyQt6.QtCore import QSettings, QStandardPaths


APP_NAME = "AlphaFixerConverter"
ORG_NAME = "PandaTools"


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
        "panda_white": "#f0f0f0",
        "panda_black": "#1a1a1a",
    }

    _DEFAULTS = {
        "theme": "Panda Dark",
        "theme_data": json.dumps(_DEFAULT_THEME),
        "sound_enabled": True,
        "click_sound": "",
        "cursor": "default",
        "trail_enabled": False,
        "trail_color": "#e94560",
        "last_input_dir": "",
        "last_output_dir": "",
        "batch_recursive": True,
        "output_suffix": "",
        "overwrite_originals": False,
        "converter_output_dir": "",
        "converter_recursive": True,
        "window_x": 100,
        "window_y": 100,
        "window_w": 1100,
        "window_h": 750,
        "window_maximized": False,
    }

    def __init__(self):
        self._qs = QSettings(ORG_NAME, APP_NAME)

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

    def get_theme(self) -> dict:
        raw = self.get("theme_data", json.dumps(self._DEFAULT_THEME))
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return dict(self._DEFAULT_THEME)

    def set_theme(self, theme_dict: dict):
        self.set("theme_data", json.dumps(theme_dict))

    # ------------------------------------------------------------------
    # Preset persistence (stored alongside settings)
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
