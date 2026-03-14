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
        "sound_enabled": False,
        "click_sound_path": "",
        "use_theme_sound": False,
        # Cursor & trail
        "cursor": "Default",
        "use_theme_cursor": False,
        "trail_enabled": False,
        "trail_color": "#e94560",
        "trail_style": "dots",
        "use_theme_trail": False,
        "trail_length": 50,       # number of trail points kept (deque maxlen)
        "trail_fade_speed": 5,    # 1=slowest fade … 10=fastest fade
        "trail_intensity": 100,   # 10–100 % max trail opacity
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
        "tooltip_mode_changed_once": False,
        "alpha_fix_done_once": False,
        "conversion_done_once": False,
        # Click effects
        "click_effects_enabled": False,
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
        # Tooltip visual style (separate from tooltip text mode)
        "tooltip_style": "Auto (follow theme)",
        # Animated banner SVGs / spinning emojis (off by default for performance)
        "animated_banner_enabled": False,
        # Banner animation style when animated_banner_enabled is True.
        # Valid values: "spin", "bounce", "shake", "pendulum", "flock".
        # "flock" spawns themed emoji flying across the top of the window.
        "banner_anim_style": "spin",
        # When True the banner animation mode comes from the active theme's
        # _banner_anim key rather than the manual banner_anim_style setting.
        "banner_use_theme_anim": True,
        # Splash screen on startup (off by default)
        "show_splash_screen": False,
        # New hidden theme unlock flags
        "unlock_crystal_cave": False,
        "unlock_glitch": False,
        "unlock_wild_west": False,
        "unlock_pirate": False,
        "unlock_deep_space": False,
        "unlock_witchs_brew": False,
        "unlock_lava_lamp": False,
        "unlock_coral_reef": False,
        "unlock_storm_cloud": False,
        "unlock_golden_hour": False,
        # ------------------------------------------------------------------
        # Button press animation settings
        # ------------------------------------------------------------------
        # When True button presses are animated (off by default).
        "button_anim_enabled": False,
        # Animation style: "none", "press", "fall", "shake", "shatter", "bounce"
        "button_anim_style": "press",
        # When True the animation mode comes from the active theme's _button_anim key.
        "use_theme_button_anim": True,
        # ------------------------------------------------------------------
        # Selective Alpha Tool settings
        # ------------------------------------------------------------------
        # Zone alpha values (7 zones, defaults to 128 each – 50% transparent)
        "sa_zone_alphas": "[128,128,128,128,128,128,128]",
        # Brush and eraser sizes in pixels
        "sa_brush_size": 10,
        "sa_eraser_size": 10,
        # Auto-correct (snap to edges) enabled
        "sa_autocorrect": False,
        # Last-used drawing tool key
        "sa_last_tool": "freehand",
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
        # Intentionally do not call _qs.sync() here: syncing after every
        # individual write causes disk I/O on every click and every settings
        # widget interaction, producing severe lag and crash-like
        # unresponsiveness.  QSettings syncs automatically when the
        # application exits and can also be called explicitly on quit.

    def sync(self) -> None:
        """Flush all pending settings writes to disk.

        Design note on sync strategy:
        - ``set()`` deliberately does **not** call ``_qs.sync()`` because
          ``set()`` is invoked on every click and every widget interaction;
          syncing there would cause per-click disk I/O and noticeable lag.
        - Important mutation methods (``save_named_theme``,
          ``save_custom_presets``, ``add_converter_history``, etc.) call
          ``_qs.sync()`` individually to protect user data from crash-induced
          data loss.  Those are infrequent, deliberate user actions, so the
          disk-I/O cost is acceptable.
        - This method handles the final close-time flush to persist any
          remaining in-flight changes (e.g., UI preferences updated through
          ``set()``) before the application exits.
        """
        self._qs.sync()

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------

    def get_theme(self) -> dict:
        raw = self.get("theme_data", json.dumps(self._DEFAULT_THEME))
        try:
            data = json.loads(raw)
            # Guard against non-dict values: json.loads("null") → None,
            # json.loads("42") → int, json.loads("[]") → list, etc.
            if not isinstance(data, dict):
                return dict(self._DEFAULT_THEME)
            # Merge with defaults so all required keys are always present,
            # even if the stored theme was saved by an older app version.
            return {**self._DEFAULT_THEME, **data}
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
            data = json.loads(raw)
            # Protect callers that iterate over the result with .items()
            return data if isinstance(data, dict) else {}
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
            data = json.loads(raw)
            # Protect callers that call .insert() / .append() on the result
            return data if isinstance(data, list) else []
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
            data = json.loads(raw)
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def add_converter_history(self, entry: dict, max_entries: int = 50):
        history = self.get_converter_history()
        history.insert(0, entry)
        history = history[:max_entries]
        self._qs.setValue("converter_history", json.dumps(history))
        self._qs.sync()

    # ------------------------------------------------------------------
    # Alpha & RGBA Adjuster history
    # ------------------------------------------------------------------

    def get_alpha_history(self) -> list:
        raw = self._qs.value("alpha_history", "[]")
        try:
            data = json.loads(raw)
            return data if isinstance(data, list) else []
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
    # Selective Alpha Tool settings
    # ------------------------------------------------------------------

    def get_sa_zone_alphas(self) -> list[int]:
        """Return the 7 zone alpha values as a list of ints (0-255)."""
        raw = self._qs.value(
            "sa_zone_alphas",
            self._DEFAULTS["sa_zone_alphas"],
        )
        try:
            data = json.loads(raw)
            if isinstance(data, list) and len(data) == 7:
                return [max(0, min(255, int(v))) for v in data]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
        return [128] * 7

    def set_sa_zone_alphas(self, alphas: list[int]) -> None:
        """Persist the 7 zone alpha values."""
        self._qs.setValue("sa_zone_alphas", json.dumps(
            [max(0, min(255, int(v))) for v in alphas]
        ))

    def reset_all(self) -> None:
        """Erase every setting and reset to factory defaults.

        Useful for testing/debugging: removes all unlock flags, click counts,
        history, and UI preferences so easter eggs can be re-triggered.
        Equivalent to deleting the .ini file next to the application.
        """
        self._qs.clear()
        self._qs.sync()

    def reset_unlocks_only(self) -> None:
        """Reset only unlock flags, click counter, and first-use flags.

        Preserves all other preferences (theme, sound, trail, cursor, etc.)
        so the user can test easter-egg triggers without losing their setup.
        """
        _unlock_keys = [k for k in self._DEFAULTS if k.startswith("unlock_")]
        _progress_keys = [k for k in self._DEFAULTS if k in (
            "total_clicks", "alpha_fix_done_once", "conversion_done_once",
        )]
        for key in _unlock_keys + _progress_keys:
            self._qs.setValue(key, self._DEFAULTS[key])
        self._qs.sync()

    # ------------------------------------------------------------------
    # Export / import all settings to a JSON file
    # ------------------------------------------------------------------

    EXPORT_KEYS = [
        "theme", "theme_data", "saved_themes",
        "sound_enabled", "click_sound_path", "use_theme_sound",
        "cursor", "use_theme_cursor", "trail_enabled", "trail_color", "trail_style", "use_theme_trail",
        "trail_length", "trail_fade_speed", "trail_intensity",
        "font_size",
        "click_effects_enabled", "use_theme_effect", "tooltip_mode", "tooltip_style",
        "animated_banner_enabled", "show_splash_screen",
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
        Raises OSError on file-read failure, json.JSONDecodeError if the
        file contains invalid JSON syntax, or ValueError if the JSON root
        is not an object (dict).
        """
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"Settings file must contain a JSON object, "
                f"got {type(data).__name__!r} instead."
            )
        imported = []
        for key in self.EXPORT_KEYS:
            if key in data:
                self._qs.setValue(key, data[key])
                imported.append(key)
        self._qs.sync()
        return imported
