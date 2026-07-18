"""
Interactive Tutorial Dialog (item 35).

Opens a step-by-step tutorial that introduces the main tools and features of
the application with fun, friendly descriptions and keyboard-shortcut hints.
"""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QProgressBar, QApplication, QScrollArea, QWidget,
)


_TUTORIAL_STEPS = [
    {
        "icon": "🐼",
        "title": "Welcome to Alpha Fixer & File Converter!",
        "body": (
            "This app lets you <b>convert images between formats</b>, "
            "<b>fix or adjust transparency (alpha channels)</b>, and "
            "<b>paint custom alpha zones</b> on individual images.\n\n"
            "Use the tabs at the top to switch between tools.\n\n"
            "Press <b>Next</b> to begin the tour, or press <b>Esc</b> to close."
        ),
        "tip": "💡  Tip: You can drag & drop files directly onto the file list in any tool.",
        "shortcut": None,
    },
    {
        "icon": "🔄",
        "title": "File Converter",
        "body": (
            "The <b>File Converter</b> tab lets you convert one or many images "
            "to a different format all at once — PNG, JPEG, WebP, TIFF, BMP, TGA, and more.\n\n"
            "1. <b>Add Files</b> — click 'Add Files' or drag images onto the list.\n"
            "2. <b>Choose a Format</b> — pick the output format from the dropdown.\n"
            "3. <b>Run Conversion</b> — click the Convert button and watch the progress.\n\n"
            "After conversion you can choose to delete the originals."
        ),
        "tip": "💡  Tip: Hold Ctrl and click to select multiple files at once.",
        "shortcut": "Ctrl+Enter to start conversion",
    },
    {
        "icon": "🖼",
        "title": "Alpha & RGBA Adjuster",
        "body": (
            "The <b>Alpha & RGBA Adjuster</b> lets you globally change the "
            "transparency of images — increase it, decrease it, or clamp it to "
            "a specific range.\n\n"
            "• Use the <b>Alpha sliders</b> to set min / max / target values.\n"
            "• Click <b>Detect Atlas</b> to find individual sprites in a sprite sheet.\n"
            "• Enable <b>Highlight Alpha Values</b> to see a heat-map of the "
            "transparency across the image.\n\n"
            "The live preview updates as you drag the sliders."
        ),
        "tip": "💡  Tip: Right-click the preview to copy alpha zones to the Selective Alpha tool.",
        "shortcut": "Ctrl+Z to undo; Ctrl+Enter to process",
    },
    {
        "icon": "🎭",
        "title": "Selective Alpha Tool",
        "body": (
            "The <b>Selective Alpha</b> tool lets you <b>paint zones</b> on an "
            "image and assign a different alpha (transparency) value to each zone.\n\n"
            "• Choose a drawing tool: Freehand, Line, Rectangle, Ellipse, or Fill.\n"
            "• Set the zone's alpha (0 = fully transparent, 255 = fully opaque).\n"
            "• Use up to 40 independent colour-coded zones per image.\n"
            "• Press <b>Ctrl+Z</b> to undo strokes, or the floating Undo button.\n\n"
            "When you're happy, click <b>Save Result</b> to write the new PNG."
        ),
        "tip": "💡  Tip: Right-click on the canvas to paste zones from the Alpha Adjuster.",
        "shortcut": "Ctrl+Z undo · Ctrl+Y redo · Ctrl+S save",
    },
    {
        "icon": "🎞",
        "title": "GIF Builder",
        "body": (
            "The <b>GIF Builder</b> lets you assemble animated GIFs from a "
            "collection of images.\n\n"
            "1. Add frames from files or folders.\n"
            "2. Drag them to reorder the sequence.\n"
            "3. Set the frame delay (speed) and loop count.\n"
            "4. Click <b>Build GIF</b> to generate the animated file.\n\n"
            "Your built GIFs show up in the History tab with an animated thumbnail."
        ),
        "tip": "💡  Tip: You can right-click frames to remove or duplicate them.",
        "shortcut": None,
    },
    {
        "icon": "🎬",
        "title": "Video Builder",
        "body": (
            "The <b>Video Builder</b> tab lets you combine video clips, images, "
            "and GIFs into a single video file using FFmpeg.\n\n"
            "• Add source clips and reorder them in the timeline.\n"
            "• Set the output format (MP4, WebM, etc.) and frame rate.\n"
            "• Click <b>Build Video</b> to render the output.\n\n"
            "Note: FFmpeg ships bundled with the app via imageio-ffmpeg — "
            "no separate FFmpeg installation is required."
        ),
        "tip": "💡  Tip: Disc-image formats (.iso, .umd, .bin) are experimental — "
               "they work when the image contains a demuxable video track.",
        "shortcut": None,
    },
    {
        "icon": "⚙",
        "title": "Settings & Themes",
        "body": (
            "Click the <b>⚙ Settings</b> button (top-right) to customise everything:\n\n"
            "• <b>Theme</b> — choose from dozens of preset themes or build your own.\n"
            "• <b>Effects</b> — click animations, mouse trails, background drips, and more.\n"
            "• <b>Sounds</b> — enable sound effects for actions like conversion and errors.\n"
            "• <b>UI Scaling</b> — adjust font size, button height, spacing, and border style.\n"
            "• <b>History</b> — control how many past runs are saved per tool.\n"
            "• <b>Shortcuts</b> — remap keyboard shortcuts to your liking (press F1)."
        ),
        "tip": "💡  Tip: Hover over any control to see a helpful tooltip.",
        "shortcut": "F1 for keyboard shortcuts · ⚙ for settings",
    },
]


class TutorialDialog(QDialog):
    """Step-by-step interactive tutorial dialog (item 35)."""

    def __init__(self, parent=None, theme_name: str = ""):
        super().__init__(parent)
        self._step = 0
        self._total = len(_TUTORIAL_STEPS)
        self._theme_name = theme_name

        self.setWindowTitle("📚  Interactive Tutorial")
        self.setMinimumSize(520, 420)
        self.resize(640, 480)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
        )
        self._build_ui()
        self._show_step(0)

        # Keyboard navigation: Left/Right arrows advance steps.
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=self._next)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=self._prev)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)

        # Progress bar
        self._progress = QProgressBar()
        self._progress.setRange(0, self._total - 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(6)
        root.addWidget(self._progress)

        # Step counter label
        self._counter_lbl = QLabel("Step 1 / " + str(self._total))
        self._counter_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._counter_lbl.setStyleSheet("color: #888; font-size: 10px;")
        root.addWidget(self._counter_lbl)

        # Icon + title row
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self._icon_lbl = QLabel("🐼")
        icon_font = QFont()
        icon_font.setFamilies(["Segoe UI Emoji", "Noto Color Emoji", "Apple Color Emoji", "sans-serif"])
        icon_font.setPointSize(32)
        self._icon_lbl.setFont(icon_font)
        self._icon_lbl.setFixedWidth(56)
        self._icon_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        header_row.addWidget(self._icon_lbl)

        self._title_lbl = QLabel()
        title_font = QFont()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self._title_lbl.setFont(title_font)
        self._title_lbl.setWordWrap(True)
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header_row.addWidget(self._title_lbl, 1)
        root.addLayout(header_row)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        root.addWidget(sep)

        # Body text in a scroll area so long text doesn't overflow
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body_container = QWidget()
        body_vl = QVBoxLayout(body_container)
        body_vl.setContentsMargins(0, 0, 0, 0)
        self._body_lbl = QLabel()
        self._body_lbl.setWordWrap(True)
        self._body_lbl.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._body_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._body_lbl.setOpenExternalLinks(False)
        body_vl.addWidget(self._body_lbl)
        body_vl.addStretch(1)
        scroll.setWidget(body_container)
        root.addWidget(scroll, 1)

        # Tip label
        self._tip_lbl = QLabel()
        self._tip_lbl.setWordWrap(True)
        self._tip_lbl.setStyleSheet(
            "color: #88c0d0; font-size: 10px; padding: 4px 6px;"
            " background: rgba(136,192,208,30);"
            " border-radius: 4px;"
        )
        root.addWidget(self._tip_lbl)

        # Shortcut hint
        self._shortcut_lbl = QLabel()
        self._shortcut_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._shortcut_lbl.setStyleSheet("color: #a3be8c; font-size: 10px; font-family: monospace;")
        root.addWidget(self._shortcut_lbl)

        # Navigation buttons
        nav_row = QHBoxLayout()
        self._btn_prev = QPushButton("◀  Previous")
        self._btn_prev.setMinimumHeight(30)
        self._btn_prev.setToolTip("Go to the previous tutorial step.  (← arrow key)")
        self._btn_prev.clicked.connect(self._prev)

        self._btn_next = QPushButton("Next  ▶")
        self._btn_next.setMinimumHeight(30)
        self._btn_next.setDefault(True)
        self._btn_next.setToolTip("Go to the next tutorial step.  (→ arrow key)")
        self._btn_next.clicked.connect(self._next)

        btn_close = QPushButton("✕  Close")
        btn_close.setMinimumHeight(30)
        btn_close.setToolTip("Close the tutorial.")
        btn_close.clicked.connect(self.close)

        nav_row.addWidget(self._btn_prev)
        nav_row.addStretch(1)
        nav_row.addWidget(btn_close)
        nav_row.addStretch(1)
        nav_row.addWidget(self._btn_next)
        root.addLayout(nav_row)

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_step(self, idx: int) -> None:
        idx = max(0, min(idx, self._total - 1))
        self._step = idx
        step = _TUTORIAL_STEPS[idx]

        self._icon_lbl.setText(step["icon"])
        self._title_lbl.setText(step["title"])

        # Convert newlines in body to HTML line breaks for RichText rendering
        body_html = step["body"].replace("\n", "<br>")
        self._body_lbl.setText(body_html)

        tip = step.get("tip", "")
        self._tip_lbl.setText(tip)
        self._tip_lbl.setVisible(bool(tip))

        sc = step.get("shortcut")
        if sc:
            self._shortcut_lbl.setText(f"⌨  {sc}")
            self._shortcut_lbl.setVisible(True)
        else:
            self._shortcut_lbl.setVisible(False)

        self._progress.setValue(idx)
        self._counter_lbl.setText(f"Step {idx + 1} / {self._total}")
        self._btn_prev.setEnabled(idx > 0)
        self._btn_next.setText(
            "Finish  ✓" if idx == self._total - 1 else "Next  ▶"
        )

    def _next(self) -> None:
        if self._step < self._total - 1:
            self._show_step(self._step + 1)
        else:
            self.close()

    def _prev(self) -> None:
        if self._step > 0:
            self._show_step(self._step - 1)
