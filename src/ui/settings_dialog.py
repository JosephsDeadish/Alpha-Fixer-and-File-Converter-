"""
Settings / Customization dialog.
"""
import json
import os

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QWidget, QGridLayout, QCheckBox,
    QLineEdit, QColorDialog, QGroupBox, QScrollArea,
    QMessageBox, QInputDialog, QSpinBox, QFileDialog,
)

from .theme_engine import PRESET_THEMES, HIDDEN_THEMES, DEFAULT_THEME, build_stylesheet
from .tooltip_manager import TOOLTIP_MODES

# Prefix characters used on theme combo items (user-saved = ★, unlocked hidden = 🔓)
_THEME_PREFIX_CHARS = "★🔓 "


class ColorButton(QPushButton):
    """A button that shows a color swatch and opens a color picker."""
    color_changed = pyqtSignal(str)

    def __init__(self, color: str = "#ffffff", parent=None):
        super().__init__(parent)
        self._color = color
        self._update_style()
        self.clicked.connect(self._pick)
        self.setFixedSize(50, 26)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str):
        self._color = color
        self._update_style()

    def _update_style(self):
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color}; border: 1px solid #888; border-radius: 4px; }}"
        )

    def _pick(self):
        c = QColorDialog.getColor(QColor(self._color), self, "Pick Color")
        if c.isValid():
            self._color = c.name()
            self._update_style()
            self.color_changed.emit(self._color)


class SettingsDialog(QDialog):
    theme_changed = pyqtSignal(dict)
    settings_changed = pyqtSignal()

    def __init__(self, settings_manager, parent=None):
        super().__init__(parent)
        self._settings = settings_manager
        self._theme = settings_manager.get_theme()
        self._color_buttons: dict[str, ColorButton] = {}
        self.setWindowTitle("Settings & Customization 🐼")
        self.setMinimumSize(620, 540)
        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ================================================================
        # ---- Theme tab ----
        # ================================================================
        theme_tab = QWidget()
        tv = QVBoxLayout(theme_tab)

        # Preset / saved-theme row
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Theme:"))
        self._theme_preset_combo = QComboBox()
        self._theme_preset_combo.setMinimumWidth(160)
        self._rebuild_theme_combo()
        preset_row.addWidget(self._theme_preset_combo, 1)
        self._btn_apply_preset = QPushButton("Apply")
        self._btn_save_theme = QPushButton("Save as…")
        self._btn_delete_theme = QPushButton("Delete")
        preset_row.addWidget(self._btn_apply_preset)
        preset_row.addWidget(self._btn_save_theme)
        preset_row.addWidget(self._btn_delete_theme)
        tv.addLayout(preset_row)

        # Color grid
        grp_colors = QGroupBox("Theme Colors")
        color_grid = QGridLayout(grp_colors)
        color_keys = [
            ("background", "Background"),
            ("surface", "Surface"),
            ("primary", "Primary"),
            ("accent", "Accent"),
            ("text", "Text"),
            ("text_secondary", "Text Secondary"),
            ("border", "Border"),
            ("success", "Success"),
            ("warning", "Warning"),
            ("error", "Error"),
            ("button_bg", "Button BG"),
            ("button_hover", "Button Hover"),
            ("progress_bar", "Progress Bar"),
            ("input_bg", "Input BG"),
            ("scrollbar_handle", "Scrollbar Handle"),
        ]
        for i, (key, label) in enumerate(color_keys):
            row, col = divmod(i, 2)
            color_grid.addWidget(QLabel(label + ":"), row, col * 3)
            btn = ColorButton(self._theme.get(key, "#888888"))
            btn.color_changed.connect(lambda c, k=key: self._on_color_changed(k, c))
            self._color_buttons[key] = btn
            color_grid.addWidget(btn, row, col * 3 + 1)
            color_grid.setColumnStretch(col * 3 + 2, 1)

        scroll = QScrollArea()
        scroll.setWidget(grp_colors)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(290)
        tv.addWidget(scroll)
        tv.addStretch(1)
        tabs.addTab(theme_tab, "🎨 Theme")

        # ================================================================
        # ---- General tab ----
        # ================================================================
        gen_tab = QWidget()
        gv = QGridLayout(gen_tab)
        row = 0

        # Sound
        gv.addWidget(QLabel("Sound Effects:"), row, 0)
        self._sound_check = QCheckBox("Enable click sounds")
        gv.addWidget(self._sound_check, row, 1, 1, 2)
        row += 1

        gv.addWidget(QLabel("Custom click sound:"), row, 0)
        sound_row = QHBoxLayout()
        self._click_sound_edit = QLineEdit()
        self._click_sound_edit.setPlaceholderText("Path to .wav file (blank = built-in)")
        self._btn_sound_browse = QPushButton("Browse")
        sound_row.addWidget(self._click_sound_edit, 1)
        sound_row.addWidget(self._btn_sound_browse)
        gv.addLayout(sound_row, row, 1, 1, 2)
        row += 1

        # Mouse trail
        gv.addWidget(QLabel("Mouse Trail:"), row, 0)
        self._trail_check = QCheckBox("Enable")
        gv.addWidget(self._trail_check, row, 1)
        row += 1

        gv.addWidget(QLabel("Trail Color:"), row, 0)
        self._trail_color_btn = ColorButton("#e94560")
        gv.addWidget(self._trail_color_btn, row, 1)
        row += 1

        # Cursor
        gv.addWidget(QLabel("Cursor Style:"), row, 0)
        self._cursor_combo = QComboBox()
        self._cursor_combo.addItems(["Default", "Cross", "Pointing Hand", "Open Hand"])
        gv.addWidget(self._cursor_combo, row, 1)
        row += 1

        # Font size
        gv.addWidget(QLabel("Font Size (pt):"), row, 0)
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(8, 24)
        self._font_size_spin.setValue(10)
        gv.addWidget(self._font_size_spin, row, 1)
        row += 1

        # Click effects
        gv.addWidget(QLabel("Click Effects:"), row, 0)
        self._click_effects_check = QCheckBox("Enable per-theme click particle effects")
        gv.addWidget(self._click_effects_check, row, 1, 1, 2)
        row += 1

        # Tooltip mode
        gv.addWidget(QLabel("Tooltip Mode:"), row, 0)
        self._tooltip_mode_combo = QComboBox()
        self._tooltip_mode_combo.addItems(TOOLTIP_MODES)
        self._tooltip_mode_combo.setToolTip(
            "Controls how tooltips appear throughout the app.\n"
            "Potty Mouth Pro 🤬 is the best mode – trust us."
        )
        gv.addWidget(self._tooltip_mode_combo, row, 1)
        row += 1

        gv.setRowStretch(row, 1)
        tabs.addTab(gen_tab, "⚙ General")

        layout.addWidget(tabs)

        # ---- Dialog buttons ----
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        self._btn_ok = QPushButton("Apply & Close")
        self._btn_ok.setObjectName("accent")
        self._btn_cancel = QPushButton("Cancel")
        btn_row.addWidget(self._btn_cancel)
        btn_row.addWidget(self._btn_ok)
        layout.addLayout(btn_row)

        # Connections
        self._btn_apply_preset.clicked.connect(self._apply_preset)
        self._btn_save_theme.clicked.connect(self._save_custom_theme)
        self._btn_delete_theme.clicked.connect(self._delete_custom_theme)
        self._btn_ok.clicked.connect(self._apply_and_close)
        self._btn_cancel.clicked.connect(self.reject)
        self._btn_sound_browse.clicked.connect(self._browse_sound)
        self._theme_preset_combo.currentTextChanged.connect(self._update_delete_btn)

    # ------------------------------------------------------------------
    # Theme combo helpers
    # ------------------------------------------------------------------

    def _rebuild_theme_combo(self, select: str = ""):
        self._theme_preset_combo.blockSignals(True)
        self._theme_preset_combo.clear()
        for name in PRESET_THEMES:
            self._theme_preset_combo.addItem(name)
        # Show hidden themes that have been unlocked
        for name, t in HIDDEN_THEMES.items():
            unlock_key = f"unlock_{t.get('_unlock', '')}"
            if self._settings.get(unlock_key, False):
                self._theme_preset_combo.addItem(f"🔓 {name}")
        saved = self._settings.get_saved_themes()
        if saved:
            self._theme_preset_combo.insertSeparator(self._theme_preset_combo.count())
            for name in sorted(saved):
                self._theme_preset_combo.addItem(f"★ {name}")
        self._theme_preset_combo.addItem("— Custom —")
        if select:
            idx = self._theme_preset_combo.findText(select)
            if idx >= 0:
                self._theme_preset_combo.setCurrentIndex(idx)
        self._theme_preset_combo.blockSignals(False)
        self._update_delete_btn()

    def _update_delete_btn(self):
        name = self._theme_preset_combo.currentText().lstrip(_THEME_PREFIX_CHARS)
        is_user = name in self._settings.get_saved_themes()
        self._btn_delete_theme.setEnabled(is_user)

    # ------------------------------------------------------------------
    # Load persisted values into controls
    # ------------------------------------------------------------------

    def _load_values(self):
        t = self._theme
        for key, btn in self._color_buttons.items():
            btn.set_color(t.get(key, "#888888"))

        theme_name = t.get("name", "")
        idx = self._theme_preset_combo.findText(theme_name)
        if idx < 0:
            idx = self._theme_preset_combo.findText(f"★ {theme_name}")
        if idx < 0:
            idx = self._theme_preset_combo.findText(f"🔓 {theme_name}")
        self._theme_preset_combo.setCurrentIndex(
            idx if idx >= 0 else self._theme_preset_combo.count() - 1
        )

        self._sound_check.setChecked(self._settings.get("sound_enabled", True))
        self._click_sound_edit.setText(self._settings.get("click_sound_path", ""))
        self._trail_check.setChecked(self._settings.get("trail_enabled", False))
        self._trail_color_btn.set_color(self._settings.get("trail_color", "#e94560"))
        cursor_val = self._settings.get("cursor", "Default")
        idx = self._cursor_combo.findText(cursor_val)
        self._cursor_combo.setCurrentIndex(max(idx, 0))
        self._font_size_spin.setValue(self._settings.get("font_size", 10))
        self._click_effects_check.setChecked(
            self._settings.get("click_effects_enabled", True)
        )
        mode_val = self._settings.get("tooltip_mode", "Normal")
        idx_m = self._tooltip_mode_combo.findText(mode_val)
        self._tooltip_mode_combo.setCurrentIndex(max(idx_m, 0))

    # ------------------------------------------------------------------
    # Color-button callback
    # ------------------------------------------------------------------

    def _on_color_changed(self, key: str, color: str):
        self._theme[key] = color

    # ------------------------------------------------------------------
    # Preset & custom theme management
    # ------------------------------------------------------------------

    def _apply_preset(self):
        raw_name = self._theme_preset_combo.currentText()
        name = raw_name.lstrip(_THEME_PREFIX_CHARS)
        if name in PRESET_THEMES:
            self._theme = dict(PRESET_THEMES[name])
        elif name in HIDDEN_THEMES:
            self._theme = dict(HIDDEN_THEMES[name])
        else:
            saved = self._settings.get_saved_themes()
            if name in saved:
                self._theme = dict(saved[name])
            else:
                return  # "— Custom —" or separator
        for key, btn in self._color_buttons.items():
            btn.set_color(self._theme.get(key, "#888888"))

    def _save_custom_theme(self):
        name, ok = QInputDialog.getText(self, "Save Theme", "Theme name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if name in PRESET_THEMES:
            QMessageBox.warning(self, "Save Theme",
                                f"'{name}' is a built-in theme name. Choose a different name.")
            return
        self._settings.save_named_theme(name, dict(self._theme))
        self._rebuild_theme_combo(select=f"★ {name}")
        QMessageBox.information(self, "Save Theme", f"Theme '{name}' saved.")

    def _delete_custom_theme(self):
        raw_name = self._theme_preset_combo.currentText().lstrip(_THEME_PREFIX_CHARS)
        reply = QMessageBox.question(
            self, "Delete Theme",
            f"Delete saved theme '{raw_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._settings.delete_named_theme(raw_name)
            self._rebuild_theme_combo()

    # ------------------------------------------------------------------
    # Sound file browser
    # ------------------------------------------------------------------

    def _browse_sound(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Click Sound", "",
            "WAV Files (*.wav);;All Files (*)",
        )
        if path:
            self._click_sound_edit.setText(path)

    # ------------------------------------------------------------------
    # Apply & close
    # ------------------------------------------------------------------

    def _apply_and_close(self):
        self._settings.set_theme(self._theme)
        self._settings.set("sound_enabled", self._sound_check.isChecked())
        self._settings.set("click_sound_path", self._click_sound_edit.text().strip())
        self._settings.set("trail_enabled", self._trail_check.isChecked())
        self._settings.set("trail_color", self._trail_color_btn.color())
        self._settings.set("cursor", self._cursor_combo.currentText())
        self._settings.set("font_size", self._font_size_spin.value())
        self._settings.set("click_effects_enabled", self._click_effects_check.isChecked())
        self._settings.set("tooltip_mode", self._tooltip_mode_combo.currentText())
        self.theme_changed.emit(self._theme)
        self.settings_changed.emit()
        self.accept()
