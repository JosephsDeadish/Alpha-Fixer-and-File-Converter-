"""
Settings / Customization dialog.
"""
import json

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QTabWidget, QWidget, QGridLayout, QCheckBox,
    QLineEdit, QColorDialog, QGroupBox, QScrollArea, QFrame,
    QMessageBox, QInputDialog,
)

from .theme_engine import PRESET_THEMES, DEFAULT_THEME, build_stylesheet


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
        self.setMinimumSize(600, 520)
        self._setup_ui()
        self._load_values()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()

        # ---- Theme tab ----
        theme_tab = QWidget()
        tv = QVBoxLayout(theme_tab)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Preset Theme:"))
        self._theme_preset_combo = QComboBox()
        for name in PRESET_THEMES:
            self._theme_preset_combo.addItem(name)
        self._theme_preset_combo.addItem("Custom")
        preset_row.addWidget(self._theme_preset_combo, 1)
        self._btn_apply_preset = QPushButton("Apply Preset")
        preset_row.addWidget(self._btn_apply_preset)
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
        scroll.setMaximumHeight(280)
        tv.addWidget(scroll)

        tv.addStretch(1)
        tabs.addTab(theme_tab, "🎨 Theme")

        # ---- General tab ----
        gen_tab = QWidget()
        gv = QGridLayout(gen_tab)

        gv.addWidget(QLabel("Sound Effects:"), 0, 0)
        self._sound_check = QCheckBox("Enable")
        gv.addWidget(self._sound_check, 0, 1)

        gv.addWidget(QLabel("Mouse Trail:"), 1, 0)
        self._trail_check = QCheckBox("Enable")
        gv.addWidget(self._trail_check, 1, 1)

        gv.addWidget(QLabel("Trail Color:"), 2, 0)
        self._trail_color_btn = ColorButton("#e94560")
        gv.addWidget(self._trail_color_btn, 2, 1)

        gv.addWidget(QLabel("Cursor:"), 3, 0)
        self._cursor_combo = QComboBox()
        self._cursor_combo.addItems(["Default", "Cross", "Pointing Hand", "Open Hand"])
        gv.addWidget(self._cursor_combo, 3, 1)

        gv.setRowStretch(10, 1)
        tabs.addTab(gen_tab, "⚙ General")

        layout.addWidget(tabs)

        # Buttons
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
        self._btn_ok.clicked.connect(self._apply_and_close)
        self._btn_cancel.clicked.connect(self.reject)
        self._theme_preset_combo.currentTextChanged.connect(self._on_preset_selected)

    def _load_values(self):
        t = self._theme
        for key, btn in self._color_buttons.items():
            btn.set_color(t.get(key, "#888888"))

        name = t.get("name", "")
        idx = self._theme_preset_combo.findText(name)
        self._theme_preset_combo.setCurrentIndex(idx if idx >= 0 else self._theme_preset_combo.count() - 1)

        self._sound_check.setChecked(self._settings.get("sound_enabled", True))
        self._trail_check.setChecked(self._settings.get("trail_enabled", False))
        self._trail_color_btn.set_color(self._settings.get("trail_color", "#e94560"))

    def _on_color_changed(self, key: str, color: str):
        self._theme[key] = color

    def _on_preset_selected(self, name: str):
        pass  # Only apply on button click

    def _apply_preset(self):
        name = self._theme_preset_combo.currentText()
        if name in PRESET_THEMES:
            self._theme = dict(PRESET_THEMES[name])
            for key, btn in self._color_buttons.items():
                btn.set_color(self._theme.get(key, "#888888"))

    def _apply_and_close(self):
        self._settings.set_theme(self._theme)
        self._settings.set("sound_enabled", self._sound_check.isChecked())
        self._settings.set("trail_enabled", self._trail_check.isChecked())
        self._settings.set("trail_color", self._trail_color_btn.color())
        self.theme_changed.emit(self._theme)
        self.settings_changed.emit()
        self.accept()
