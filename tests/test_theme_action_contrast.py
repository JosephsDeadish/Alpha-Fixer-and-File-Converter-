"""Palette contrast on the actual tab/action fills, including Qt state styling."""
import re

import pytest
from PyQt6 import sip
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.ui.theme_engine import (
    HIDDEN_THEMES, PRESET_THEMES, _readable_color, build_stylesheet,
)


def contrast(foreground, background):
    def luminance(value):
        color = QColor(value)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
                  for c in channels]
        return sum(c * weight for c, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    first, second = luminance(foreground), luminance(background)
    return (max(first, second) + 0.05) / (min(first, second) + 0.05)


def composite(foreground, background):
    foreground, background = QColor(foreground), QColor(background)
    alpha = foreground.alphaF()
    return QColor.fromRgbF(*[
        front * alpha + back * (1 - alpha)
        for front, back in zip(
            (foreground.redF(), foreground.greenF(), foreground.blueF()),
            (background.redF(), background.greenF(), background.blueF()))
    ])


@pytest.mark.parametrize("fill", ["#00ffffff", "#40ffffff", "#80000000"])
@pytest.mark.parametrize("parent", ["surface", "background"])
def test_custom_alpha_fills_choose_text_for_composited_background(fill, parent):
    theme = {**PRESET_THEMES["Panda Dark"],
             **{key: fill for key in ("button_bg", "button_hover", "accent",
                                     "primary", "tab_selected")}}
    stylesheet = build_stylesheet(theme)
    for selector in ("QPushButton", "QPushButton:hover", "QPushButton:pressed",
                     "QPushButton:checked", "QPushButton#accent",
                     "QTabBar::tab", "QTabBar::tab:selected"):
        block = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]+)\}", stylesheet)[1]
        color = re.search(r"(?m)^\s*color:\s*([^;]+);", block)[1]
        actual_parent = "background" if selector.startswith("QTabBar") else parent
        background = composite(fill, theme[actual_parent])
        assert contrast(composite(color, background), background) >= 4.5


@pytest.mark.parametrize("candidate", ["#00ffffff", "#40ffffff", "#40000000"])
def test_translucent_foreground_candidates_are_not_treated_as_opaque(candidate):
    background = "#222222" if QColor(candidate).red() else "#eeeeee"
    color = _readable_color((background,), candidate)
    assert color != candidate
    assert contrast(composite(color, background), background) >= 4.5


@pytest.mark.parametrize("fill", ["#00ffffff", "#40ffffff"])
@pytest.mark.parametrize("parent", ["surface", "background"])
def test_qt_custom_alpha_button_fill_and_text(fill, parent):
    app = QApplication.instance() or QApplication([])
    theme = {**PRESET_THEMES["Panda Dark"], "button_bg": fill,
             "text": "#00ffffff", "panda_white": "#40ffffff"}
    host = QWidget()
    host.setObjectName("alphaTestHost")
    host.setStyleSheet(build_stylesheet(theme) +
                      f"\nQWidget#alphaTestHost {{ background: {theme[parent]}; }}")
    layout = QVBoxLayout(host)
    button = QPushButton("Action")
    layout.addWidget(button)
    try:
        host.show()
        app.processEvents()
        QTest.mouseMove(host, host.rect().bottomRight())
        app.sendEvent(button, QEvent(QEvent.Type.Leave))
        app.processEvents()
        image = host.grab().toImage().copy(button.geometry())
        expected = composite(fill, theme[parent])
        painted = image.pixelColor(4, button.height() // 2)
        assert all(abs(a - b) <= 1 for a, b in zip(
            painted.getRgb()[:3], expected.getRgb()[:3])), (painted.name(), expected.name())
        text_pixels = [image.pixelColor(x, y)
                       for y in range(button.height() // 2 - 4, button.height() // 2 + 5)
                       for x in range(button.width() // 2 - 18, button.width() // 2 + 19)]
        assert any(contrast(pixel, painted) >= 4.5 for pixel in text_pixels)
    finally:
        host.close()
        sip.delete(host)


@pytest.mark.parametrize("theme", [*PRESET_THEMES.values(), *HIDDEN_THEMES.values()],
                         ids=lambda theme: theme["name"])
def test_action_and_tab_text_contrast_on_each_builtin_fill(theme):
    stylesheet = build_stylesheet(theme)
    for selector, fill in (
        ("QTabBar::tab", "primary"),
        ("QTabBar::tab:selected", "tab_selected"),
        ("QTabBar::tab:hover:!selected", "button_hover"),
        ("QPushButton", "button_bg"),
        ("QPushButton:hover", "button_hover"),
        ("QPushButton:pressed", "accent"),
        ("QPushButton:checked", "accent"),
        ("QPushButton:checked:hover", "button_hover"),
        ("QPushButton#accent", "accent"),
        ("QPushButton#accent:hover", "button_hover"),
        ('QPushButton[previewOverlay="true"]:hover', "button_hover"),
    ):
        block = re.search(r"(?m)^" + re.escape(selector) + r" \{([^}]+)\}", stylesheet)
        color = re.search(r"(?m)^\s*color:\s*([^;]+);", block[1])[1]
        assert contrast(color, theme[fill]) >= 4.5, (theme["name"], selector, color)


@pytest.mark.parametrize("name", ["Panda Dark", "Panda Light"])
def test_qt_button_states_apply_readable_text_without_geometry_changes(name):
    app = QApplication.instance() or QApplication([])
    theme = PRESET_THEMES[name]
    host = QWidget()
    host.setStyleSheet(build_stylesheet(theme))
    layout = QVBoxLayout(host)
    ordinary, accent, toggle, overlay = [QPushButton("Action") for _ in range(4)]
    accent.setObjectName("accent")
    toggle.setCheckable(True)
    overlay.setProperty("previewOverlay", True)
    for button in (ordinary, accent, toggle, overlay):
        layout.addWidget(button)
    try:
        host.show()
        app.processEvents()
        for button, fill in ((ordinary, "button_bg"), (accent, "accent"),
                             (toggle, "accent"), (overlay, "surface")):
            QTest.mouseMove(host, host.rect().bottomRight())
            app.sendEvent(button, QEvent(QEvent.Type.Leave))
            if button is toggle:
                button.setChecked(True)
            app.processEvents()
            initial = button.geometry()
            image = button.grab().toImage()
            background = QColor(theme[fill])
            assert image.pixelColor(4, button.height() // 2) == background
            text_pixels = [image.pixelColor(x, y)
                           for y in range(button.height() // 2 - 4, button.height() // 2 + 5)
                           for x in range(button.width() // 2 - 18, button.width() // 2 + 19)]
            assert any(contrast(pixel, background) >= 4.5 for pixel in text_pixels)
            button.setFocus()
            QTest.mouseMove(button, button.rect().center())
            app.sendEvent(button, QEvent(QEvent.Type.Enter))
            app.processEvents()
            # Hover is painted by Qt's stylesheet style, not a palette update.
            image = button.grab().toImage()
            background = QColor(theme["button_hover"])
            assert image.pixelColor(4, button.height() // 2) == background
            text_pixels = [image.pixelColor(x, y)
                           for y in range(button.height() // 2 - 4, button.height() // 2 + 5)
                           for x in range(button.width() // 2 - 18, button.width() // 2 + 19)]
            assert any(contrast(pixel, background) >= 4.5 for pixel in text_pixels)
            assert button.geometry() == initial
    finally:
        host.close()
        sip.delete(host)
