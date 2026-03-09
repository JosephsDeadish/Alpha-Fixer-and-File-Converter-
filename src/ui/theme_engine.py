"""
Theme engine – generates Qt stylesheets from a theme dictionary
and provides helper utilities.
"""
from typing import Optional


# Default panda-themed dark palette
DEFAULT_THEME = {
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
    "progress_bar": "#e94560",
    "input_bg": "#0d1b3e",
    "scrollbar": "#2a2a4a",
    "scrollbar_handle": "#e94560",
}

LIGHT_THEME = {
    "name": "Panda Light",
    "background": "#f5f5f5",
    "surface": "#ffffff",
    "primary": "#3d5a80",
    "accent": "#e94560",
    "text": "#1a1a2e",
    "text_secondary": "#555577",
    "border": "#c8c8d8",
    "success": "#2e7d32",
    "warning": "#e65100",
    "error": "#c62828",
    "tab_selected": "#e94560",
    "button_bg": "#3d5a80",
    "button_hover": "#e94560",
    "panda_white": "#ffffff",
    "panda_black": "#1a1a1a",
    "progress_bar": "#e94560",
    "input_bg": "#e8eaf6",
    "scrollbar": "#c8c8d8",
    "scrollbar_handle": "#e94560",
}

NEON_THEME = {
    "name": "Neon Panda",
    "background": "#0d0d0d",
    "surface": "#111111",
    "primary": "#1a003a",
    "accent": "#00ff88",
    "text": "#e0ffe0",
    "text_secondary": "#80c080",
    "border": "#003322",
    "success": "#00ff88",
    "warning": "#ffdd00",
    "error": "#ff3355",
    "tab_selected": "#00ff88",
    "button_bg": "#1a003a",
    "button_hover": "#00ff88",
    "panda_white": "#e0ffe0",
    "panda_black": "#0d0d0d",
    "progress_bar": "#00ff88",
    "input_bg": "#050505",
    "scrollbar": "#111111",
    "scrollbar_handle": "#00ff88",
    "_effect": "neon",
}

GORE_THEME = {
    "name": "Gore",
    "background": "#1a0000",
    "surface": "#2b0000",
    "primary": "#4a0000",
    "accent": "#cc0000",
    "text": "#ffcccc",
    "text_secondary": "#aa6666",
    "border": "#660000",
    "success": "#228822",
    "warning": "#cc5500",
    "error": "#ff0000",
    "tab_selected": "#cc0000",
    "button_bg": "#4a0000",
    "button_hover": "#cc0000",
    "panda_white": "#ffcccc",
    "panda_black": "#1a0000",
    "progress_bar": "#cc0000",
    "input_bg": "#220000",
    "scrollbar": "#330000",
    "scrollbar_handle": "#cc0000",
    "_effect": "gore",
}

BAT_THEME = {
    "name": "Bat Cave",
    "background": "#0a0a1a",
    "surface": "#10102a",
    "primary": "#1e003a",
    "accent": "#7b2dff",
    "text": "#ddddf0",
    "text_secondary": "#8888bb",
    "border": "#2a1a4a",
    "success": "#44aa77",
    "warning": "#cc8800",
    "error": "#ff3366",
    "tab_selected": "#7b2dff",
    "button_bg": "#1e003a",
    "button_hover": "#7b2dff",
    "panda_white": "#ddddf0",
    "panda_black": "#0a0a1a",
    "progress_bar": "#7b2dff",
    "input_bg": "#08081a",
    "scrollbar": "#1a1a2e",
    "scrollbar_handle": "#7b2dff",
    "_effect": "bat",
}

RAINBOW_THEME = {
    "name": "Rainbow Chaos",
    "background": "#ff00ff",
    "surface": "#ff88ff",
    "primary": "#ff44cc",
    "accent": "#ffff00",
    "text": "#1a001a",
    "text_secondary": "#550055",
    "border": "#ff00cc",
    "success": "#00ff88",
    "warning": "#ff8800",
    "error": "#ff0000",
    "tab_selected": "#ffff00",
    "button_bg": "#ff44cc",
    "button_hover": "#ffff00",
    "panda_white": "#ffffff",
    "panda_black": "#1a001a",
    "progress_bar": "#00ffff",
    "input_bg": "#ff66ff",
    "scrollbar": "#ff44cc",
    "scrollbar_handle": "#ffff00",
    "_effect": "rainbow",
}

OTTER_THEME = {
    "name": "Otter Cove",
    "background": "#1a1206",
    "surface": "#2e1f09",
    "primary": "#4a3210",
    "accent": "#e8a040",
    "text": "#f0e8d0",
    "text_secondary": "#c0a880",
    "border": "#6a4820",
    "success": "#4caf50",
    "warning": "#ff9800",
    "error": "#f44336",
    "tab_selected": "#e8a040",
    "button_bg": "#4a3210",
    "button_hover": "#e8a040",
    "panda_white": "#f0e8d0",
    "panda_black": "#1a1206",
    "progress_bar": "#e8a040",
    "input_bg": "#120c04",
    "scrollbar": "#2e1f09",
    "scrollbar_handle": "#e8a040",
    "_effect": "otter",
}

GALAXY_THEME = {
    "name": "Galaxy",
    "background": "#03030f",
    "surface": "#070720",
    "primary": "#0d0d3a",
    "accent": "#4477ff",
    "text": "#e0e8ff",
    "text_secondary": "#8090cc",
    "border": "#1a1a60",
    "success": "#00ddaa",
    "warning": "#ffcc00",
    "error": "#ff4477",
    "tab_selected": "#4477ff",
    "button_bg": "#0d0d3a",
    "button_hover": "#4477ff",
    "panda_white": "#e0e8ff",
    "panda_black": "#03030f",
    "progress_bar": "#4477ff",
    "input_bg": "#020210",
    "scrollbar": "#070720",
    "scrollbar_handle": "#4477ff",
    "_effect": "galaxy",
}

GALAXY_OTTER_THEME = {
    "name": "Galaxy Otter",
    "background": "#04030f",
    "surface": "#0f0820",
    "primary": "#1a1040",
    "accent": "#a06aff",
    "text": "#ece0f8",
    "text_secondary": "#9080b0",
    "border": "#2a1a50",
    "success": "#44ddaa",
    "warning": "#ffaa44",
    "error": "#ff4477",
    "tab_selected": "#a06aff",
    "button_bg": "#1a1040",
    "button_hover": "#a06aff",
    "panda_white": "#ece0f8",
    "panda_black": "#04030f",
    "progress_bar": "#a06aff",
    "input_bg": "#030210",
    "scrollbar": "#0f0820",
    "scrollbar_handle": "#a06aff",
    "_effect": "galaxy_otter",
}

GOTH_THEME = {
    "name": "Goth",
    "background": "#0a0a0a",
    "surface": "#111111",
    "primary": "#1a001a",
    "accent": "#8800aa",
    "text": "#e8d8ee",
    "text_secondary": "#aa88bb",
    "border": "#330033",
    "success": "#336633",
    "warning": "#664400",
    "error": "#cc0044",
    "tab_selected": "#8800aa",
    "button_bg": "#1a001a",
    "button_hover": "#8800aa",
    "panda_white": "#e8d8ee",
    "panda_black": "#0a0a0a",
    "progress_bar": "#8800aa",
    "input_bg": "#080808",
    "scrollbar": "#111111",
    "scrollbar_handle": "#8800aa",
    "_effect": "goth",
}

# Hidden / unlockable themes  (not shown in normal selector until unlocked)
SECRET_SKELETON_THEME = {
    "name": "Secret Skeleton",
    "background": "#ffffff",
    "surface": "#f0f0f0",
    "primary": "#dddddd",
    "accent": "#1a1a1a",
    "text": "#1a1a1a",
    "text_secondary": "#444444",
    "border": "#cccccc",
    "success": "#336633",
    "warning": "#886600",
    "error": "#990000",
    "tab_selected": "#1a1a1a",
    "button_bg": "#dddddd",
    "button_hover": "#1a1a1a",
    "panda_white": "#ffffff",
    "panda_black": "#1a1a1a",
    "progress_bar": "#1a1a1a",
    "input_bg": "#f8f8f8",
    "scrollbar": "#dddddd",
    "scrollbar_handle": "#1a1a1a",
    "_effect": "goth",
    "_unlock": "skeleton",
}

PRESET_THEMES = {
    "Panda Dark": DEFAULT_THEME,
    "Panda Light": LIGHT_THEME,
    "Neon Panda": NEON_THEME,
    "Gore": GORE_THEME,
    "Bat Cave": BAT_THEME,
    "Rainbow Chaos": RAINBOW_THEME,
    "Otter Cove": OTTER_THEME,
    "Galaxy": GALAXY_THEME,
    "Galaxy Otter": GALAXY_OTTER_THEME,
    "Goth": GOTH_THEME,
}

HIDDEN_THEMES = {
    "Secret Skeleton": SECRET_SKELETON_THEME,
}

# Which effects each theme uses (name → effect key)
THEME_EFFECTS = {t["name"]: t.get("_effect", "default") for t in {
    **PRESET_THEMES, **HIDDEN_THEMES,
}.values()}


def build_stylesheet(theme: Optional[dict] = None) -> str:
    """Generate a full Qt stylesheet from the given theme dictionary."""
    t = {**DEFAULT_THEME, **(theme or {})}
    return f"""
/* ===== Global ===== */
QWidget {{
    background-color: {t['background']};
    color: {t['text']};
    font-family: "Segoe UI", "Ubuntu", "Arial", sans-serif;
    font-size: 13px;
}}

QMainWindow, QDialog {{
    background-color: {t['background']};
}}

/* ===== Tabs ===== */
QTabWidget::pane {{
    border: 1px solid {t['border']};
    background-color: {t['surface']};
    border-radius: 4px;
}}
QTabBar::tab {{
    background: {t['primary']};
    color: {t['text_secondary']};
    padding: 10px 22px;
    margin-right: 2px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 13px;
}}
QTabBar::tab:selected {{
    background: {t['tab_selected']};
    color: {t['panda_white']};
}}
QTabBar::tab:hover:!selected {{
    background: {t['button_hover']};
    color: {t['panda_white']};
}}

/* ===== Buttons ===== */
QPushButton {{
    background-color: {t['button_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 6px;
    padding: 7px 16px;
    font-weight: 600;
}}
QPushButton:hover {{
    background-color: {t['button_hover']};
    color: {t['panda_white']};
    border-color: {t['accent']};
}}
QPushButton:pressed {{
    background-color: {t['accent']};
}}
QPushButton:disabled {{
    background-color: {t['border']};
    color: {t['text_secondary']};
}}

/* ===== Accent Buttons ===== */
QPushButton#accent {{
    background-color: {t['accent']};
    color: {t['panda_white']};
    border: none;
    font-size: 14px;
    padding: 9px 20px;
}}
QPushButton#accent:hover {{
    background-color: {t['button_hover']};
}}

/* ===== Line Edits / Inputs ===== */
QLineEdit, QTextEdit, QPlainTextEdit {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
    selection-background-color: {t['accent']};
}}
QLineEdit:focus, QTextEdit:focus {{
    border-color: {t['accent']};
}}

/* ===== Combo Boxes ===== */
QComboBox {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
    min-height: 28px;
}}
QComboBox:hover {{
    border-color: {t['accent']};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
    selection-background-color: {t['accent']};
}}

/* ===== Spin Boxes ===== */
QSpinBox, QDoubleSpinBox {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    padding: 5px 8px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {t['accent']};
}}

/* ===== Sliders ===== */
QSlider::groove:horizontal {{
    height: 6px;
    background: {t['border']};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {t['accent']};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::sub-page:horizontal {{
    background: {t['accent']};
    border-radius: 3px;
}}

/* ===== Progress Bar ===== */
QProgressBar {{
    background-color: {t['border']};
    border-radius: 5px;
    text-align: center;
    color: {t['text']};
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {t['progress_bar']};
    border-radius: 5px;
}}

/* ===== Labels ===== */
QLabel {{
    color: {t['text']};
}}
QLabel#header {{
    font-size: 18px;
    font-weight: 700;
    color: {t['accent']};
}}
QLabel#subheader {{
    font-size: 14px;
    font-weight: 600;
    color: {t['text_secondary']};
}}
QLabel#section {{
    font-size: 13px;
    font-weight: 700;
    color: {t['accent']};
    padding: 4px 0;
}}

/* ===== Group Box ===== */
QGroupBox {{
    border: 1px solid {t['border']};
    border-radius: 6px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    font-weight: 600;
    color: {t['text_secondary']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: {t['accent']};
}}

/* ===== List Widget ===== */
QListWidget {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
}}
QListWidget::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QListWidget::item:hover {{
    background-color: {t['primary']};
}}

/* ===== Tree Widget ===== */
QTreeWidget {{
    background-color: {t['input_bg']};
    color: {t['text']};
    border: 1px solid {t['border']};
    border-radius: 5px;
    alternate-background-color: {t['surface']};
}}
QTreeWidget::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QHeaderView::section {{
    background-color: {t['primary']};
    color: {t['text']};
    padding: 5px;
    border: none;
    font-weight: 600;
}}

/* ===== Scrollbars ===== */
QScrollBar:vertical {{
    background: {t['scrollbar']};
    width: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:vertical {{
    background: {t['scrollbar_handle']};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
QScrollBar:horizontal {{
    background: {t['scrollbar']};
    height: 10px;
    border-radius: 5px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scrollbar_handle']};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

/* ===== Check Box ===== */
QCheckBox {{
    color: {t['text']};
    spacing: 6px;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {t['border']};
    border-radius: 4px;
    background: {t['input_bg']};
}}
QCheckBox::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
}}

/* ===== Radio Button ===== */
QRadioButton {{
    color: {t['text']};
    spacing: 6px;
}}
QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {t['border']};
    border-radius: 8px;
    background: {t['input_bg']};
}}
QRadioButton::indicator:checked {{
    background: {t['accent']};
    border-color: {t['accent']};
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background-color: {t['border']};
}}

/* ===== Status Bar ===== */
QStatusBar {{
    background-color: {t['surface']};
    color: {t['text_secondary']};
    border-top: 1px solid {t['border']};
}}

/* ===== Tool Bar ===== */
QToolBar {{
    background-color: {t['surface']};
    border-bottom: 1px solid {t['border']};
    spacing: 4px;
    padding: 4px;
}}

/* ===== Menu ===== */
QMenuBar {{
    background-color: {t['surface']};
    color: {t['text']};
    border-bottom: 1px solid {t['border']};
}}
QMenuBar::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}
QMenu {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['border']};
}}
QMenu::item:selected {{
    background-color: {t['accent']};
    color: {t['panda_white']};
}}

/* ===== Frame ===== */
QFrame#card {{
    background-color: {t['surface']};
    border: 1px solid {t['border']};
    border-radius: 8px;
}}

/* ===== Tooltip ===== */
QToolTip {{
    background-color: {t['surface']};
    color: {t['text']};
    border: 1px solid {t['accent']};
    padding: 4px;
    border-radius: 4px;
}}
"""
