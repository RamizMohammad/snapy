# -*- coding: utf-8 -*-
"""Reusable pieces of the Snapy shell: title bar, sidebar, cards, badges."""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import (Property, QEasingCurve, QPointF,
                            QPropertyAnimation, QRectF, Qt, Signal)
from PySide6.QtGui import (QColor, QFont, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient, QTextCharFormat,
                           QTextCursor)
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from icons import svg_icon
from theme import T


# --------------------------------------------------------------------------- #
#  Primitives
# --------------------------------------------------------------------------- #

def _rgba(hex_colour: str, alpha: float) -> str:
    """QSS has no reliable 8-digit hex; build an rgba() instead."""
    h = hex_colour.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha:.2f})"


def label(text: str, role: str = "", color: str = "") -> QLabel:
    lb = QLabel(text)
    if role:
        lb.setObjectName(role)
    if color:
        lb.setStyleSheet(f"color: {color};")
    return lb


def divider() -> QFrame:
    d = QFrame()
    d.setObjectName("Divider")
    d.setFixedHeight(1)
    return d


def icon_label(name: str, color: str, size: int = 18) -> QLabel:
    lb = QLabel()
    lb.setPixmap(svg_icon(name, color, size))
    lb.setFixedSize(size, size)
    return lb


class Card(QFrame):
    """Standard bordered surface. `pad` is uniform interior padding."""

    def __init__(self, pad: int = 20, spacing: int = 14, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(pad, pad, pad, pad)
        self.v.setSpacing(spacing)


class Badge(QLabel):
    """Small pill. tone: ok | warn | bad | muted | accent."""

    @staticmethod
    def tones() -> dict:
        """Computed, not a class constant: the palette can change at startup."""
        return {
            "ok": (T.SUCCESS, T.SUCCESS_DIM),
            "warn": (T.WARNING, T.WARNING_DIM),
            "bad": (T.ERROR, T.ERROR_DIM),
            "muted": (T.MUTED, T.SURFACE),
            "accent": (T.PRIMARY, T.SURFACE),
        }

    def __init__(self, text: str, tone: str = "muted", mono: bool = True, parent=None):
        super().__init__(text, parent)
        tones = Badge.tones()
        fg, bg = tones.get(tone, tones["muted"])
        family = T.MONO if mono else T.FONT
        self.setStyleSheet(
            f"color:{fg}; background:{bg}; border:1px solid {_rgba(fg, 0.30)};"
            f"border-radius:6px; padding:3px 9px;"
            f"font-family:{family}; font-size:11px; font-weight:500;")
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class DotBadge(QWidget):
    """Coloured dot followed by text — the ONLINE pill in the header."""

    def __init__(self, text: str, tone: str = "ok", parent=None):
        super().__init__(parent)
        fg = Badge.tones().get(tone, Badge.tones()["muted"])[0]
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 10, 4)
        h.setSpacing(7)
        dot = _Dot(fg, 7)
        h.addWidget(dot)
        lb = QLabel(text)
        lb.setStyleSheet(f"color:{fg}; font-family:{T.MONO}; font-size:11px;"
                         f"font-weight:600; letter-spacing:0.6px;")
        h.addWidget(lb)
        self.setStyleSheet(f"background:{_rgba(fg, 0.12)}; "
                           f"border:1px solid {_rgba(fg, 0.42)}; border-radius:6px;")
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)


class _Dot(QWidget):
    def __init__(self, color: str, size: int = 8, parent=None):
        super().__init__(parent)
        self._c = QColor(color)
        self.setFixedSize(size, size)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(self._c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(self.rect())


# --------------------------------------------------------------------------- #
#  Title bar
# --------------------------------------------------------------------------- #

class TitleBar(QWidget):
    """Frameless-window title bar: brand at left, window controls at right.

    Set NATIVE_TITLEBAR in main.py to fall back to the system bar — frameless
    windows lose the Windows 11 snap-layout flyout on the maximise button
    unless it is reimplemented.
    """

    def __init__(self, window: QWidget, parent=None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(T.TITLEBAR_H)
        self._win = window
        self._drag: Optional[QPointF] = None

        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 0, 0)
        h.setSpacing(10)

        h.addWidget(AppMark(22))
        name = QLabel("Snapy")
        name.setStyleSheet("font-size:14px; font-weight:700;")
        h.addWidget(name)
        sep = QLabel("|")
        sep.setStyleSheet(f"color:{T.OUTLINE};")
        h.addWidget(sep)
        org = QLabel("Loopax Technologies")
        org.setStyleSheet(f"color:{T.MUTED}; font-family:{T.MONO}; font-size:11px;")
        h.addWidget(org)
        h.addStretch(1)

        # Painted, not text. Glyph characters render at different weights and
        # optical sizes per font, and a QPushButton inherits the global padding
        # unless every rule is restated — which is what made these uneven.
        self.btn_min = WindowButton("min")
        self.btn_max = WindowButton("max")
        self.btn_close = WindowButton("close")
        self.btn_min.clicked.connect(self._win.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(self._win.close)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            h.addWidget(b)

    def _toggle_max(self):
        self._win.showNormal() if self._win.isMaximized() else self._win.showMaximized()
        self.sync_max_state()

    def sync_max_state(self):
        self.btn_max.set_kind("restore" if self._win.isMaximized() else "max")

    # Drag to move. startSystemMove() hands the drag to the window manager, so
    # Windows snapping, multi-monitor and DPI changes all behave natively —
    # unlike moving the window by hand on mouse-move.
    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        handle = self._win.windowHandle()
        if handle is not None:
            if self._win.isMaximized():
                self._win.showNormal()
            handle.startSystemMove()
        else:
            self._drag = e.globalPosition() - self._win.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self._win.move((e.globalPosition() - self._drag).toPoint())

    def mouseReleaseEvent(self, _e):
        self._drag = None

    def mouseDoubleClickEvent(self, _e):
        self._toggle_max()


class WindowButton(QWidget):
    """Minimise / maximise / restore / close, drawn rather than typed.

    Fixed 46 x titlebar geometry so all three sit on an even rhythm, and the
    glyph is a 10px box centred in that cell regardless of font.
    """

    clicked = Signal()

    def __init__(self, kind: str = "min", parent=None):
        super().__init__(parent)
        self.kind = kind
        self._hover = False
        self.setFixedSize(46, T.TITLEBAR_H)

    def set_kind(self, kind: str):
        self.kind = kind
        self.update()

    def enterEvent(self, _e):
        self._hover = True
        self.update()

    def leaveEvent(self, _e):
        self._hover = False
        self.update()

    def mousePressEvent(self, e):
        # Must accept the press. Without this it propagates to TitleBar, which
        # starts a system move — the window manager grabs the mouse and the
        # release never arrives, so the button silently never fires.
        if e.button() == Qt.LeftButton:
            e.accept()
        else:
            super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._hover:
            p.fillRect(self.rect(),
                       QColor("#c42b1c") if self.kind == "close" else QColor(T.SURFACE))
        fg = QColor("#ffffff") if (self._hover and self.kind == "close") \
            else QColor(T.TEXT if self._hover else T.MUTED)
        p.setPen(QPen(fg, 1.2, Qt.SolidLine, Qt.SquareCap))

        cx, cy, s = self.width() / 2, self.height() / 2, 5.0
        if self.kind == "min":
            p.drawLine(QPointF(cx - s, cy), QPointF(cx + s, cy))
        elif self.kind == "max":
            p.drawRect(QRectF(cx - s, cy - s, s * 2, s * 2))
        elif self.kind == "restore":
            p.drawRect(QRectF(cx - s, cy - s + 2, s * 2 - 2, s * 2 - 2))
            path = QPainterPath()
            path.moveTo(cx - s + 2, cy - s)
            path.lineTo(cx + s, cy - s)
            path.lineTo(cx + s, cy + s - 2)
            p.drawPath(path)
        else:  # close
            p.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            p.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy + s))


class AppMark(QWidget):
    """The Loopax/Snapy mark: rounded square with an accent glyph."""

    def __init__(self, size: int = 22, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._s = size

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(0, 0, self._s, self._s)
        p.setBrush(QColor(T.PRIMARY_FILL))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(r, self._s * 0.28, self._s * 0.28)
        p.setPen(QPen(QColor("#ffffff"), max(1.4, self._s * 0.085),
                      Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        c = r.center()
        rad = self._s * 0.24
        p.drawEllipse(c, rad, rad)
        p.drawLine(QPointF(c.x(), c.y() - rad * 1.9), QPointF(c.x(), c.y() - rad * 0.9))


# --------------------------------------------------------------------------- #
#  Sidebar
# --------------------------------------------------------------------------- #

class NavItem(QWidget):
    clicked = Signal(int)

    def __init__(self, index: int, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.index = index
        self.icon_name = icon
        self._active = False
        self._hover = False
        self._compact = False
        self.setFixedHeight(44)
        self.setCursor(Qt.PointingHandCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(22, 0, 16, 0)
        h.setSpacing(13)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        h.addWidget(self._icon)
        self._text = QLabel(text)
        h.addWidget(self._text)
        h.addStretch(1)
        self._badge: Optional[QLabel] = None
        self._restyle()

    def set_badge(self, text: Optional[str]):
        if self._badge:
            self._badge.deleteLater()
            self._badge = None
        if text and not self._compact:
            self._badge = Badge(text, "accent")
            self.layout().addWidget(self._badge)

    def set_compact(self, on: bool):
        """Icon-only rail, for narrow windows."""
        self._compact = on
        self._text.setVisible(not on)
        if on and self._badge:
            self._badge.deleteLater()
            self._badge = None
        self.layout().setContentsMargins(*((23, 0, 5, 0) if on else (22, 0, 16, 0)))
        self.setToolTip(self._text.text() if on else "")

    def set_active(self, on: bool):
        self._active = on
        self._restyle()

    def _restyle(self):
        colour = T.PRIMARY if self._active else (T.TEXT if self._hover else T.TEXT_VAR)
        self._icon.setPixmap(svg_icon(self.icon_name, colour, 18))
        weight = 600 if self._active else 400
        self._text.setStyleSheet(f"color:{colour}; font-size:13px; font-weight:{weight};")
        self.update()

    def enterEvent(self, _e):
        self._hover = True
        self._restyle()

    def leaveEvent(self, _e):
        self._hover = False
        self._restyle()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.index)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._active:
            p.fillRect(self.rect(), QColor(T.SURFACE))
            p.fillRect(0, 0, 3, self.height(), QColor(T.PRIMARY))
        elif self._hover:
            p.fillRect(self.rect(), QColor(T.LOW))


class Sidebar(QWidget):
    navigate = Signal(int)

    ITEMS: List[Tuple[str, str]] = [
        ("phone", "Device"),
        ("camera", "Capture"),
        ("history", "Snapshots"),
        ("compare", "Compare"),
        ("restore", "Restore"),
        ("checklist", "Checklist"),
        ("settings", "Settings"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setFixedWidth(T.SIDEBAR_W)

        v = QVBoxLayout(self)
        v.setContentsMargins(0, 18, 0, 14)
        v.setSpacing(0)

        brand = QWidget()
        bl = QVBoxLayout(brand)
        bl.setContentsMargins(22, 0, 16, 18)
        bl.setSpacing(2)
        t = QLabel("Snapy")
        t.setStyleSheet("font-size:15px; font-weight:700;")
        bl.addWidget(t)
        s = QLabel("Loopax Technologies")
        s.setStyleSheet(f"color:{T.MUTED}; font-family:{T.MONO}; font-size:10px;")
        bl.addWidget(s)
        self._brand = brand
        v.addWidget(brand)
        self._compact = False

        self.items: List[NavItem] = []
        for i, (icon, text) in enumerate(self.ITEMS):
            it = NavItem(i, icon, text)
            it.clicked.connect(self._on_click)
            v.addWidget(it)
            self.items.append(it)
        v.addStretch(1)

        self._pill_host = self._device_pill()
        v.addWidget(self._pill_host)
        self.set_active(0)

    def _device_pill(self) -> QWidget:
        w = QFrame()
        w.setObjectName("Inset")
        wrap = QVBoxLayout(w)
        wrap.setContentsMargins(12, 10, 12, 10)
        wrap.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(8)
        self._dev_dot = _Dot(T.MUTED, 7)
        top.addWidget(self._dev_dot)
        self._dev_name = QLabel("No device")
        self._dev_name.setStyleSheet("font-size:12px; font-weight:600;")
        top.addWidget(self._dev_name)
        top.addStretch(1)
        wrap.addLayout(top)

        bottom = QHBoxLayout()
        bottom.setSpacing(7)
        bottom.addWidget(icon_label("usb", T.MUTED, 13))
        self._dev_state = QLabel("not connected")
        self._dev_state.setStyleSheet(
            f"color:{T.MUTED}; font-family:{T.MONO}; font-size:10px;")
        bottom.addWidget(self._dev_state)
        bottom.addStretch(1)
        wrap.addLayout(bottom)

        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.addWidget(w)
        return holder

    def set_compact(self, on: bool):
        """Collapse to an icon rail so a narrow window keeps a usable content
        area instead of just refusing to shrink."""
        if getattr(self, "_compact", False) == on:
            return
        self._compact = on
        self.setFixedWidth(64 if on else T.SIDEBAR_W)
        for it in self.items:
            it.set_compact(on)
        self._brand.setVisible(not on)
        self._pill_host.setVisible(not on)

    def set_device(self, name: str, detail: str, online: bool):
        self._dev_name.setText(name)
        self._dev_state.setText(detail)
        self._dev_dot._c = QColor(T.SUCCESS if online else T.ERROR)
        self._dev_dot.update()

    def _on_click(self, index: int):
        self.set_active(index)
        self.navigate.emit(index)

    def set_active(self, index: int):
        for i, it in enumerate(self.items):
            it.set_active(i == index)


# --------------------------------------------------------------------------- #
#  Status bar
# --------------------------------------------------------------------------- #

class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("StatusBar")
        self.setFixedHeight(T.STATUSBAR_H)
        h = QHBoxLayout(self)
        h.setContentsMargins(14, 0, 8, 0)
        h.setSpacing(10)

        self._dot = _Dot(T.SUCCESS, 7)
        h.addWidget(self._dot)
        self._state = QLabel("Ready")
        self._state.setStyleSheet("font-size:12px;")
        h.addWidget(self._state)
        sep = QLabel("/")
        sep.setStyleSheet(f"color:{T.OUTLINE};")
        h.addWidget(sep)
        self._detail = QLabel("Daemon listening on 127.0.0.1:5037")
        self._detail.setStyleSheet(
            f"color:{T.MUTED}; font-family:{T.MONO}; font-size:11px;")
        h.addWidget(self._detail)
        h.addStretch(1)

        ver = QLabel("ADB 1.0.41")
        ver.setStyleSheet(f"color:{T.FAINT}; font-family:{T.MONO}; font-size:11px;")
        h.addWidget(ver)

        console = QPushButton("  Console")
        console.setObjectName("Ghost")
        console.setIcon(svg_icon("console", T.MUTED, 14))
        h.addWidget(console)

    def set_state(self, text: str, detail: str = "", tone: str = "ok"):
        self._state.setText(text)
        self._detail.setText(detail)
        self._dot._c = QColor(Badge.tones().get(tone, Badge.tones()["muted"])[0])
        self._dot.update()


# --------------------------------------------------------------------------- #
#  Content pieces
# --------------------------------------------------------------------------- #

class StatTile(Card):
    def __init__(self, label_text: str, value: str, foot: str = "",
                 foot_tone: str = "", parent=None):
        super().__init__(pad=18, spacing=6, parent=parent)
        self.v.addWidget(label(label_text.upper(), "FieldLabel"))
        self._val = label(value, "StatValue")
        self.v.addWidget(self._val)
        colour = {"ok": T.SUCCESS, "warn": T.WARNING}.get(foot_tone, T.MUTED)
        self._foot = label(foot, "StatFoot", colour)
        self._foot.setVisible(bool(foot))
        self.v.addWidget(self._foot)
        self.setMinimumHeight(104)

    def set_value(self, value: str, foot: str = "", foot_tone: str = ""):
        self._val.setText(str(value))
        self._foot.setText(foot)
        self._foot.setVisible(bool(foot))
        colour = {"ok": T.SUCCESS, "warn": T.WARNING,
                  "bad": T.ERROR}.get(foot_tone, T.MUTED)
        self._foot.setStyleSheet(f"color:{colour};")

    def set_value_colour(self, colour: str):
        self._val.setStyleSheet(f"color:{colour};")


class Field(QWidget):
    """Uppercase micro-label above a value — the device identity grid."""

    def __init__(self, name: str, value: str, mono: bool = False, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(5)
        v.addWidget(label(name.upper(), "FieldLabel"))
        self._val = QLabel(value)
        family = T.MONO if mono else T.FONT
        self._val.setStyleSheet(
            f"font-family:{family}; font-size:14px; color:{T.TEXT};")
        v.addWidget(self._val)

    def set_value(self, value: str):
        self._val.setText(str(value))


class CapabilityRow(QWidget):
    def __init__(self, name: str, state: str, tone: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 7, 0, 7)
        h.addWidget(label(name, "RowLabel"))
        h.addStretch(1)
        h.addWidget(Badge(state, tone))


class PhoneGlyph(QWidget):
    """Line-art phone with a faint accent glow — the hero illustration."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Not fixed: a fixed 260x330 here set the floor for the whole window.
        self.setMinimumSize(150, 210)
        self.setMaximumSize(260, 330)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self._screen: Optional[QPixmap] = None

    def set_screen(self, png: Optional[bytes]):
        """Show a live screenshot inside the handset, or fall back to the
        placeholder glyph when there is nothing to show."""
        if not png:
            self._screen = None
        else:
            pm = QPixmap()
            self._screen = pm if pm.loadFromData(png, "PNG") else None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        k = min(self.width() / 260.0, self.height() / 330.0)
        p.scale(k, k)
        cx, cy = cx / k, cy / k

        glow = QRadialGradient(QPointF(cx, cy), 150)
        glow.setColorAt(0.0, QColor(98, 139, 255, 46))
        glow.setColorAt(0.55, QColor(98, 139, 255, 12))
        glow.setColorAt(1.0, QColor(98, 139, 255, 0))
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx, cy), 150, 150)

        body = QRectF(cx - 78, cy - 135, 156, 270)
        p.setBrush(QColor(T.LOWEST))
        p.setPen(QPen(QColor(T.OUTLINE), 1.6))
        p.drawRoundedRect(body, 18, 18)

        p.setPen(QPen(QColor(T.OUTLINE), 2.4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 18, cy - 118), QPointF(cx + 10, cy - 118))
        p.setBrush(QColor(T.OUTLINE))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(cx + 20, cy - 118), 3.4, 3.4)

        screen = QRectF(cx - 69, cy - 120, 138, 238)
        if self._screen is not None and not self._screen.isNull():
            clip = QPainterPath()
            clip.addRoundedRect(screen, 9, 9)
            p.save()
            p.setClipPath(clip)
            scaled = self._screen.scaled(
                screen.size().toSize(), Qt.KeepAspectRatioByExpanding,
                Qt.SmoothTransformation)
            p.drawPixmap(
                screen.topLeft() + QPointF(
                    (screen.width() - scaled.width()) / 2,
                    (screen.height() - scaled.height()) / 2),
                scaled)
            p.restore()
            p.setPen(QPen(QColor(T.OUTLINE), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(screen, 9, 9)
            return

        p.setBrush(Qt.NoBrush)
        pen = QPen(QColor(T.OUTLINE_DIM), 1.3, Qt.DashLine)
        pen.setDashPattern([4, 4])
        p.setPen(pen)
        p.drawRoundedRect(QRectF(cx - 58, cy - 96, 116, 172), 8, 8)

        p.setPen(QPen(QColor(T.PRIMARY), 1.9, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        g = QRectF(cx - 15, cy - 26, 30, 44)
        p.drawRoundedRect(g, 5, 5)
        path = QPainterPath()
        path.moveTo(cx - 4, cy - 10)
        path.lineTo(cx - 9, cy - 4)
        path.lineTo(cx - 4, cy + 2)
        p.drawPath(path)
        path2 = QPainterPath()
        path2.moveTo(cx + 4, cy - 10)
        path2.lineTo(cx + 9, cy - 4)
        path2.lineTo(cx + 4, cy + 2)
        p.drawPath(path2)

        p.setPen(QPen(QColor(T.OUTLINE_DIM), 3.4, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx - 30, cy + 54), QPointF(cx + 30, cy + 54))
        p.drawLine(QPointF(cx - 18, cy + 70), QPointF(cx + 18, cy + 70))
        p.drawLine(QPointF(cx - 26, cy + 112), QPointF(cx + 26, cy + 112))


# --------------------------------------------------------------------------- #
#  Progress and state
# --------------------------------------------------------------------------- #

class Ring(QWidget):
    """Circular progress. Indeterminate when total is 0 — sweeps instead."""

    def __init__(self, size: int = 120, thickness: int = 9, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._thick = thickness
        self._pct = 0.0
        self._caption = "TOTAL"
        self._indeterminate = False
        self._sweep = 0.0
        self._anim = QPropertyAnimation(self, b"sweep", self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(360.0)
        self._anim.setDuration(1400)
        self._anim.setLoopCount(-1)

    def set_value(self, pct: Optional[float], caption: str = "TOTAL"):
        self._caption = caption
        if pct is None:
            if not self._indeterminate:
                self._indeterminate = True
                self._anim.start()
        else:
            if self._indeterminate:
                self._indeterminate = False
                self._anim.stop()
            self._pct = max(0.0, min(100.0, pct))
        self.update()

    def get_sweep(self) -> float:
        return self._sweep

    def set_sweep(self, v: float):
        self._sweep = v
        self.update()

    sweep = Property(float, get_sweep, set_sweep)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        m = self._thick / 2 + 1
        box = QRectF(m, m, self._size - 2 * m, self._size - 2 * m)

        p.setPen(QPen(QColor(T.SURFACE_ALT), self._thick, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(box, 0, 360 * 16)

        p.setPen(QPen(QColor(T.PRIMARY_FILL), self._thick, Qt.SolidLine, Qt.RoundCap))
        if self._indeterminate:
            p.drawArc(box, int((90 - self._sweep) * 16), -int(80 * 16))
        else:
            p.drawArc(box, 90 * 16, -int(360 * 16 * self._pct / 100.0))

        if not self._indeterminate:
            p.setPen(QColor(T.TEXT))
            f = QFont()
            f.setFamilies(["JetBrains Mono", "Cascadia Code", "Consolas"])
            f.setPixelSize(int(self._size * 0.21))
            f.setWeight(QFont.Bold)
            p.setFont(f)
            p.drawText(QRectF(0, self._size * 0.30, self._size, self._size * 0.26),
                       Qt.AlignCenter, f"{self._pct:.0f}%")
        p.setPen(QColor(T.MUTED))
        f2 = QFont()
        f2.setFamilies(["JetBrains Mono", "Consolas"])
        f2.setPixelSize(max(9, int(self._size * 0.085)))
        p.setFont(f2)
        p.drawText(QRectF(0, self._size * 0.56, self._size, self._size * 0.16),
                   Qt.AlignCenter, self._caption)


class Toggle(QWidget):
    """Switch. Qt has no native one, and a styled checkbox never reads right."""

    toggled = Signal(bool)

    def __init__(self, on: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(38, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._on = on
        self._pos = 1.0 if on else 0.0
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(130)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def isChecked(self) -> bool:
        return self._on

    def setChecked(self, on: bool):
        if on == self._on:
            return
        self._on = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        self.toggled.emit(on)

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float):
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._on)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        track = QRectF(0, 1, self.width(), self.height() - 2)
        off, on = QColor(T.SURFACE_ALT), QColor(T.PRIMARY_FILL)
        col = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos))
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        r = track.height() / 2 - 3
        x = track.left() + 3 + r + self._pos * (track.width() - 2 * r - 6)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x, track.center().y()), r, r)


class SegmentBar(QWidget):
    """Stacked proportional bar with an optional legend beneath."""

    def __init__(self, segments: Optional[List[Tuple[str, float, str]]] = None,
                 height: int = 8, parent=None):
        super().__init__(parent)
        self._segs = segments or []
        self._h = height
        self.setFixedHeight(height)

    def set_segments(self, segments: List[Tuple[str, float, str]]):
        self._segs = segments
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        total = sum(max(0.0, s[1]) for s in self._segs) or 1.0
        x = 0.0
        w = self.width()
        for _name, value, colour in self._segs:
            seg_w = w * (max(0.0, value) / total)
            if seg_w <= 0:
                continue
            p.setBrush(QColor(colour))
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, 0, max(2.0, seg_w - 2), self._h), 3, 3)
            x += seg_w


def legend_row(segments: List[Tuple[str, float, str]], fmt=None,
               vertical: bool = False) -> QWidget:
    """Legend for a SegmentBar. Vertical keeps values readable in a side panel,
    where a horizontal row clips."""
    w = QWidget()
    g = QVBoxLayout(w) if vertical else QHBoxLayout(w)
    g.setContentsMargins(0, 0, 0, 0)
    g.setSpacing(6 if vertical else 18)
    for name, value, colour in segments:
        item = QWidget()
        h = QHBoxLayout(item)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(7)
        sw = QFrame()
        sw.setFixedSize(9, 9)
        sw.setStyleSheet(f"background:{colour}; border-radius:2px;")
        h.addWidget(sw)
        lb = QLabel(name)
        lb.setStyleSheet(f"color:{T.MUTED}; font-size:11px;")
        h.addWidget(lb)
        h.addStretch(1)
        if fmt:
            val = QLabel(fmt(value))
            val.setStyleSheet(f"color:{T.TEXT_VAR}; font-family:{T.MONO};"
                              f"font-size:11px;")
            h.addWidget(val)
        g.addWidget(item)
    if not vertical:
        g.addStretch(1)
    return w


class StepRow(QWidget):
    """One line of the capture step list: state, title, right-aligned detail."""

    QUEUED, RUNNING, DONE, FAILED = range(4)

    def __init__(self, number: int, title: str, parent=None):
        super().__init__(parent)
        self.state = self.QUEUED
        self._detail = "queued"
        self.setMinimumHeight(46)

        v = QVBoxLayout(self)
        v.setContentsMargins(14, 8, 16, 8)
        v.setSpacing(4)

        top = QHBoxLayout()
        top.setSpacing(12)
        self._icon = QLabel()
        self._icon.setFixedSize(18, 18)
        top.addWidget(self._icon)
        self._title = QLabel(f"{number}. {title}")
        top.addWidget(self._title)
        top.addStretch(1)
        self._det = QLabel(self._detail)
        top.addWidget(self._det)
        v.addLayout(top)

        self._sub = QLabel("")
        self._sub.setStyleSheet(
            f"color:{T.MUTED}; font-family:{T.MONO}; font-size:10px;")
        self._sub.hide()
        v.addWidget(self._sub)
        self._restyle()

    def set_state(self, state: int, detail: str = "", sub: str = ""):
        self.state = state
        if detail:
            self._detail = detail
        self._det.setText(self._detail)
        if sub:
            self._sub.setText(sub)
            self._sub.show()
        elif state != self.RUNNING:
            self._sub.hide()
        self._restyle()

    def _restyle(self):
        if self.state == self.DONE:
            icon, colour, text = "check-circle", T.SUCCESS, T.TEXT
        elif self.state == self.RUNNING:
            icon, colour, text = "spinner", T.PRIMARY, T.TEXT
        elif self.state == self.FAILED:
            icon, colour, text = "x-circle", T.ERROR, T.TEXT
        else:
            icon, colour, text = "circle", T.OUTLINE, T.FAINT
        self._icon.setPixmap(svg_icon(icon, colour, 18))
        weight = 600 if self.state == self.RUNNING else 400
        self._title.setStyleSheet(f"color:{text}; font-size:13px; font-weight:{weight};")
        det_col = T.PRIMARY if self.state == self.RUNNING else (
            T.MUTED if self.state == self.DONE else T.FAINT)
        self._det.setStyleSheet(
            f"color:{det_col}; font-family:{T.MONO}; font-size:11px;")
        self.update()

    def paintEvent(self, _e):
        if self.state == self.RUNNING:
            p = QPainter(self)
            p.fillRect(self.rect(), QColor(T.SURFACE))
            p.fillRect(0, 0, 3, self.height(), QColor(T.PRIMARY))


class Console(QPlainTextEdit):
    """Log pane. Colour comes from the CLI's own [+]/[!]/[x] prefixes."""

    @staticmethod
    def colours() -> dict:
        return {"ok": T.SUCCESS, "warn": T.WARNING, "err": T.ERROR,
                "info": T.PRIMARY, "head": T.CONSOLE_FG, "dim": T.FAINT,
                "": T.CONSOLE_FG}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(4000)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet(
            f"QPlainTextEdit{{background:{T.CONSOLE_BG}; border:1px solid {T.OUTLINE_DIM};"
            f"border-radius:{T.R_CTRL}px; padding:10px;"
            f"font-family:{T.MONO}; font-size:11px; color:{T.CONSOLE_FG};}}")

    def append_line(self, text: str, tag: str = ""):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(Console.colours().get(tag, T.CONSOLE_FG)))
        if tag == "head":
            fmt.setFontWeight(QFont.Bold)
        cur = self.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(text + "\n", fmt)
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


# --------------------------------------------------------------------------- #
#  Form pieces
# --------------------------------------------------------------------------- #

class RadioCard(QWidget):
    """Large selectable option: title, description, right-aligned chip."""

    selected = Signal(str)

    def __init__(self, key: str, title: str, desc: str, chip: str = "", parent=None):
        super().__init__(parent)
        self.key = key
        self._on = False
        self.setCursor(Qt.PointingHandCursor)

        h = QHBoxLayout(self)
        h.setContentsMargins(16, 13, 16, 13)
        h.setSpacing(13)
        self._dot = _RadioDot()
        h.addWidget(self._dot, 0, Qt.AlignTop)

        col = QVBoxLayout()
        col.setSpacing(3)
        self._t = QLabel(title)
        self._t.setStyleSheet("font-size:13px; font-weight:600;")
        col.addWidget(self._t)
        d = QLabel(desc)
        d.setStyleSheet(f"color:{T.MUTED}; font-size:12px;")
        d.setWordWrap(True)
        col.addWidget(d)
        h.addLayout(col, 1)
        if chip:
            h.addWidget(Badge(chip, "muted"), 0, Qt.AlignTop)
        self._restyle()

    def set_selected(self, on: bool):
        self._on = on
        self._dot.set_on(on)
        self._restyle()

    def _restyle(self):
        border = T.PRIMARY_FILL if self._on else T.OUTLINE_DIM
        bg = T.SELECTED if self._on else T.LOW
        self.setStyleSheet(
            f"RadioCard{{background:{bg}; border:1px solid {border};"
            f"border-radius:{T.R_CTRL}px;}}")

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.selected.emit(self.key)

    def paintEvent(self, e):
        from PySide6.QtWidgets import QStyle, QStyleOption
        opt = QStyleOption()
        opt.initFrom(self)
        p = QPainter(self)
        self.style().drawPrimitive(QStyle.PE_Widget, opt, p, self)


class _RadioDot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(17, 17)
        self._on = False

    def set_on(self, on: bool):
        self._on = on
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(1, 1, 15, 15)
        p.setPen(QPen(QColor(T.PRIMARY_FILL if self._on else T.OUTLINE), 1.6))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(r)
        if self._on:
            p.setBrush(QColor(T.PRIMARY_FILL))
            p.setPen(Qt.NoPen)
            p.drawEllipse(r.center(), 4.2, 4.2)


class ToggleRow(QWidget):
    """Label + description on the left, switch on the right."""

    def __init__(self, title: str, desc: str, on: bool = False, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 9, 0, 9)
        h.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:13px;")
        col.addWidget(t)
        d = QLabel(desc)
        d.setStyleSheet(f"color:{T.MUTED}; font-size:11px;")
        d.setWordWrap(True)
        col.addWidget(d)
        h.addLayout(col, 1)
        self.toggle = Toggle(on)
        h.addWidget(self.toggle, 0, Qt.AlignVCenter)

    def isChecked(self) -> bool:
        return self.toggle.isChecked()

    def setChecked(self, on: bool):
        self.toggle.setChecked(on)


def section_label(text: str) -> QWidget:
    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 8, 0, 2)
    h.setSpacing(12)
    lb = QLabel(text.upper())
    lb.setObjectName("SectionLabel")
    h.addWidget(lb)
    line = QFrame()
    line.setObjectName("Divider")
    line.setFixedHeight(1)
    h.addWidget(line, 1)
    return w
