# -*- coding: utf-8 -*-
"""Design tokens and global stylesheet for Snapy.

Values are lifted from the Stitch export (precision_utility_dark/DESIGN.md) so the
desktop build and the reference mockups stay in sync. Change a colour here, not in
a widget.
"""

from __future__ import annotations


class _Palettes:
    """Named palettes. T.apply() copies one onto T before any widget is built."""

    dark = {
        "BG": "#0f1219", "LOWEST": "#090c12", "LOW": "#1a1f2a",
        "SURFACE": "#232937", "HIGH": "#2e3443", "SURFACE_ALT": "#394151",
        "TEXT": "#eef1f7", "TEXT_VAR": "#c8cfdd", "MUTED": "#9aa5ba",
        "FAINT": "#717d92",
        "OUTLINE": "#4e596c", "OUTLINE_DIM": "#2d3546",
        "PRIMARY": "#b4c5ff", "PRIMARY_FILL": "#628bff",
        "PRIMARY_FILL_HOVER": "#7ba0ff", "ON_PRIMARY": "#ffffff",
        "SUCCESS": "#57df8d", "SUCCESS_DIM": "#1c4030",
        "WARNING": "#e8a33d", "WARNING_DIM": "#3d2f18",
        "ERROR": "#ff8a80", "ERROR_DIM": "#4a1f1c",
        "LABEL": "#aab4c8", "SECTION": "#bcc6d8", "SELECTED": "#1b2333",
        "FRAME": "#525f78",
        "CONSOLE_BG": "#090c12", "CONSOLE_FG": "#c8cfdd",
    }

    # Not an inversion — a light utility palette with its own contrast budget.
    light = {
        "BG": "#f2f5f9", "LOWEST": "#ffffff", "LOW": "#ffffff",
        "SURFACE": "#eef1f6", "HIGH": "#e2e7f0", "SURFACE_ALT": "#cfd6e2",
        "TEXT": "#121721", "TEXT_VAR": "#39424f", "MUTED": "#5b6676",
        "FAINT": "#8a94a3",
        "OUTLINE": "#b9c2d0", "OUTLINE_DIM": "#dce1e9",
        "PRIMARY": "#2b56c9", "PRIMARY_FILL": "#2f5fd9",
        "PRIMARY_FILL_HOVER": "#4472e8", "ON_PRIMARY": "#ffffff",
        "SUCCESS": "#12724a", "SUCCESS_DIM": "#d8f0e2",
        "WARNING": "#8a5800", "WARNING_DIM": "#fbeed2",
        "ERROR": "#b3261e", "ERROR_DIM": "#f9dcd9",
        "LABEL": "#4d5868", "SECTION": "#39424f", "SELECTED": "#e8effd",
        "FRAME": "#aab4c4",
        "CONSOLE_BG": "#111722", "CONSOLE_FG": "#c8cfdd",
    }


class T:
    # -- surfaces (darkest to lightest) --------------------------------- #
    # Each step is a visible jump. The first pass had ~9 points of luminance
    # between the window and a card, which reads as one flat black sheet.
    BG = "#0f1219"          # window background
    LOWEST = "#090c12"      # title bar, console, inset panels
    LOW = "#1a1f2a"         # cards
    SURFACE = "#232937"     # raised rows
    HIGH = "#2e3443"        # hover / selected rows
    SURFACE_ALT = "#394151"  # progress tracks, switch backs

    # -- text ------------------------------------------------------------ #
    TEXT = "#eef1f7"
    TEXT_VAR = "#c8cfdd"
    MUTED = "#9aa5ba"       # readable on LOW, not decorative
    FAINT = "#717d92"

    # -- lines ----------------------------------------------------------- #
    OUTLINE = "#4e596c"
    OUTLINE_DIM = "#2d3546"  # card borders you can actually see

    # -- accent and semantics -------------------------------------------- #
    PRIMARY = "#b4c5ff"           # accent text / icons
    PRIMARY_FILL = "#628bff"      # filled buttons
    PRIMARY_FILL_HOVER = "#7ba0ff"
    ON_PRIMARY = "#ffffff"

    SUCCESS = "#57df8d"
    SUCCESS_DIM = "#1c4030"
    WARNING = "#e8a33d"
    WARNING_DIM = "#3d2f18"
    ERROR = "#ffb4ab"
    ERROR_DIM = "#4a1f1c"

    # -- type ------------------------------------------------------------ #
    # Inter and JetBrains Mono if installed; otherwise Windows ships good
    # fallbacks that keep the metrics close.
    FONT = '"Inter", "Segoe UI Variable Display", "Segoe UI", sans-serif'
    MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'

    # -- geometry --------------------------------------------------------- #
    R_CARD = 12
    R_CTRL = 8
    R_PILL = 999
    SIDEBAR_W = 240
    TITLEBAR_H = 48
    STATUSBAR_H = 34

    LABEL = "#aab4c8"
    SECTION = "#bcc6d8"
    SELECTED = "#1b2333"
    FRAME = "#525f78"
    CONSOLE_BG = "#090c12"
    CONSOLE_FG = "#c8cfdd"
    NAME = "dark"

    @classmethod
    def apply(cls, name: str) -> None:
        """Swap the active palette. Call before building any widget — colours
        are read at construction time, not on every repaint."""
        palette = getattr(_Palettes, name, None) or _Palettes.dark
        for key, value in palette.items():
            setattr(cls, key, value)
        cls.NAME = name if hasattr(_Palettes, name) else "dark"


def stylesheet() -> str:
    """Global QSS. Widget-specific painting lives in widgets.py."""
    return f"""
    QWidget {{
        background: transparent;
        color: {T.TEXT};
        font-family: {T.FONT};
        font-size: 13px;
    }}
    /* Frameless windows have no OS border. Against a dark desktop the app edge
       vanished entirely, so the frame gets its own brighter token — not the
       card-border grey. */
    QWidget#Root {{ background: {T.BG}; border: 1px solid {T.FRAME}; }}
    QWidget#TitleBar {{
        background: {T.LOWEST};
        border-bottom: 1px solid {T.OUTLINE_DIM};
    }}
    QWidget#Sidebar {{ background: {T.LOWEST}; border-right: 1px solid {T.OUTLINE_DIM}; }}
    QWidget#StatusBar {{ background: {T.LOWEST}; border-top: 1px solid {T.OUTLINE_DIM}; }}
    QWidget#Content {{ background: {T.BG}; }}

    /* ---- cards ---- */
    QFrame#Card {{
        background: {T.LOW};
        border: 1px solid {T.OUTLINE_DIM};
        border-radius: {T.R_CARD}px;
    }}
    QFrame#Inset {{
        background: {T.LOWEST};
        border: 1px solid {T.OUTLINE_DIM};
        border-radius: {T.R_CTRL}px;
    }}
    QFrame#Divider {{ background: {T.OUTLINE_DIM}; max-height: 1px; border: none; }}

    /* ---- type roles ---- */
    QLabel#H1 {{ font-size: 24px; font-weight: 600; letter-spacing: -0.4px; }}
    QLabel#H2 {{ font-size: 15px; font-weight: 600; }}
    QLabel#Sub {{ color: {T.MUTED}; font-size: 13px; }}
    /* Micro-labels carry the structure of every screen. At 10px in a dim grey
       they disappear, which is what made sections and keys hard to find. */
    QLabel#FieldLabel {{
        color: {T.LABEL}; font-size: 11px; font-weight: 700;
        letter-spacing: 1.0px;
    }}
    QLabel#SectionLabel {{
        color: {T.SECTION}; font-size: 11px; font-weight: 700;
        letter-spacing: 1.2px;
    }}
    QLabel#FieldValue {{ font-size: 14px; color: {T.TEXT}; }}
    QLabel#Mono {{ font-family: {T.MONO}; font-size: 13px; color: {T.TEXT}; }}
    QLabel#StatValue {{ font-family: {T.MONO}; font-size: 26px; font-weight: 600; }}
    QLabel#StatFoot {{ color: {T.MUTED}; font-size: 11px; font-family: {T.MONO}; }}
    QLabel#CardTitle {{ font-size: 14px; font-weight: 600; }}
    QLabel#CardNote {{ color: {T.MUTED}; font-size: 11px; font-family: {T.MONO}; }}
    QLabel#RowLabel {{ font-size: 13px; color: {T.TEXT_VAR}; }}

    /* ---- buttons ---- */
    QPushButton {{
        background: transparent;
        border: 1px solid {T.OUTLINE};
        border-radius: {T.R_CTRL}px;
        color: {T.TEXT};
        padding: 9px 16px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton:hover {{ background: {T.HIGH}; }}
    QPushButton:pressed {{ background: {T.SURFACE}; }}
    QPushButton#Primary {{
        background: {T.PRIMARY_FILL};
        border: 1px solid {T.PRIMARY_FILL};
        color: {T.ON_PRIMARY};
        font-weight: 600;
    }}
    QPushButton#Primary:hover {{
        background: {T.PRIMARY_FILL_HOVER};
        border-color: {T.PRIMARY_FILL_HOVER};
    }}
    QPushButton#Danger {{ border-color: {T.ERROR_DIM}; color: {T.ERROR}; }}
    QPushButton#Danger:hover {{ background: {T.ERROR_DIM}; }}
    QPushButton#Ghost {{ border: none; color: {T.MUTED}; padding: 6px 10px; }}
    QPushButton#Ghost:hover {{ color: {T.TEXT}; background: {T.SURFACE}; }}

    /* ---- window controls ---- */
    QPushButton#WinBtn {{
        border: none; border-radius: 0px; padding: 0px;
        color: {T.MUTED}; font-size: 14px; min-width: 46px; min-height: {T.TITLEBAR_H}px;
    }}
    QPushButton#WinBtn:hover {{ background: {T.SURFACE}; color: {T.TEXT}; }}
    QPushButton#WinClose:hover {{ background: #c42b1c; color: #ffffff; }}

    /* ---- scrollbars ---- */
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0px;
    }}
    QScrollBar::handle:vertical {{
        background: {T.OUTLINE}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {T.MUTED}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0px; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QScrollArea {{ border: none; background: transparent; }}
    """
