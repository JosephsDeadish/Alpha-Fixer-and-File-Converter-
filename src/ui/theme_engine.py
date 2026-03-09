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
}

PRESET_THEMES = {
    "Panda Dark": DEFAULT_THEME,
    "Panda Light": LIGHT_THEME,
    "Neon Panda": NEON_THEME,
}


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
