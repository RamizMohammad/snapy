# -*- coding: utf-8 -*-
"""Snapy — Android snapshot and restore, by Loopax Technologies.

Run:  python main.py

The window is a front end for the phone_snapshot CLI, which it finds beside
this file or one folder up. Every action builds a real command and streams its
output; nothing is simulated.
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout,
                               QWidget)

from backend import Backend, Settings
from pages import (CapturePage, ChecklistPage, ComparePage, DevicePage,
                   RestorePage, Session, SettingsPage, SnapshotsPage)
from theme import T, stylesheet
from widgets import Console, Sidebar, StatusBar, TitleBar

# Frameless matches the design. True gives the system title bar back, which
# keeps the Windows 11 snap-layout flyout on the maximise button.
NATIVE_TITLEBAR = False


RESIZE_MARGIN = 7  # px of grab area along each window edge


class MainWindow(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self.restart_requested = False
        self._shadow_done = False
        self.setObjectName("Root")
        # A plain QWidget subclass ignores QSS background/border unless this is
        # set — which is why the frame border was never drawn at all.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setWindowTitle("Snapy — Loopax Technologies")
        self.resize(1440, 900)
        # Floor: below this the layout squashes. Above it, unbounded —
        # the sidebar collapses to an icon rail so narrow stays usable.
        self.setMinimumSize(880, 600)
        if not NATIVE_TITLEBAR:
            # A frameless window loses the OS resize borders. They are put back
            # by hand below, delegating the actual drag to the window manager.
            self.setWindowFlags(Qt.FramelessWindowHint)
            self.setMouseTracking(True)

        self.settings = settings
        self.backend = Backend(self.settings, self)
        self.session = Session()

        root = QVBoxLayout(self)
        # 1px inset so the frame border painted by QWidget#Root is actually
        # visible — with zero margins the title bar paints straight over it.
        root.setContentsMargins(1, 1, 1, 1) if not NATIVE_TITLEBAR else \
            root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.titlebar = None
        if not NATIVE_TITLEBAR:
            self.titlebar = TitleBar(self)
            root.addWidget(self.titlebar)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.sidebar = Sidebar()
        self.sidebar.navigate.connect(self._go)
        body.addWidget(self.sidebar)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)

        self.stack = QStackedWidget()
        for page in (
            DevicePage(self.backend, self.session),
            CapturePage(self.backend, self.session),
            SnapshotsPage(self.backend, self.session),
            ComparePage(self.backend, self.session),
            RestorePage(self.backend, self.session),
            ChecklistPage(self.backend, self.session),
            SettingsPage(self.backend, self.session),
        ):
            self.stack.addWidget(page)
        right.addWidget(self.stack, 1)

        self.drawer = self._console_drawer()
        self.drawer.hide()
        right.addWidget(self.drawer)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        self.status = StatusBar()
        root.addWidget(self.status)

        # -- wiring ------------------------------------------------------ #
        self.session.navigate.connect(self._go_and_select)
        self.backend.log_line.connect(self._on_log)
        self.backend.status.connect(self._on_status)
        self.backend.job_started.connect(self._on_job_started)
        self.backend.job_finished.connect(self._on_job_finished)
        self.backend.device_ready.connect(self._on_device)
        self.backend.snapshots_ready.connect(lambda _r: None)

        if not self.backend.ready:
            QTimer.singleShot(400, self._no_cli)
        else:
            QTimer.singleShot(200, self.backend.probe_device)
            QTimer.singleShot(300, self.backend.load_snapshots)

    # ------------------------------------------------------------------ #
    def _console_drawer(self) -> QWidget:
        w = QFrame()
        w.setStyleSheet(f"background:{T.LOWEST}; border-top:1px solid {T.OUTLINE_DIM};")
        w.setFixedHeight(190)
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 10, 16, 12)
        v.setSpacing(8)
        self.console = Console()
        v.addWidget(self.console)
        return w

    def _go(self, index: int):
        self.stack.setCurrentIndex(index)
        self.sidebar.set_active(index)
        page = self.stack.widget(index)
        if isinstance(page, ChecklistPage):
            page.load()

    def _go_and_select(self, index: int):
        if index in (3, 4, 5) and not self.session.snapshot:
            QMessageBox.information(
                self, "Snapy", "Pick a snapshot on the Snapshots tab first.")
            self._go(2)
            return
        self._go(index)

    # -- backend signals -------------------------------------------------- #
    def _on_log(self, text: str, tag: str):
        if text.strip():
            self.console.append_line(text, tag)

    def _on_status(self, text: str, tone: str):
        self.status.set_state(text, self.status._detail.text(), tone)

    def _on_job_started(self, label: str):
        # Capture has its own live stream on the page; a second copy in the
        # drawer is just noise.
        if label != "Capture":
            self.drawer.show()
        self.status.set_state(label, "running", "accent")
        if label == "Capture":
            self.sidebar.items[1].set_badge("RUN")

    def _on_job_finished(self, code: int, label: str):
        self.sidebar.items[1].set_badge(None)
        if label == "Capture" and code == 0:
            self.backend.load_snapshots()
        self.backend.probe_device()

    def _on_device(self, d: dict):
        if "error" in d:
            self.status.set_state("No device", d["error"], "bad")
            self.sidebar.set_device("No device", d["error"][:26], False)
        else:
            name = f"{d['manufacturer']} {d['model']}".strip()
            self.status.set_state(
                "Ready", f"{name} · {d['serial']}", "ok")
            self.sidebar.set_device(name, f"Android {d['android']} · USB", True)

    # -- responsive shell -------------------------------------------------- #
    COMPACT_AT = 1120  # px of window width below which the sidebar collapses

    def resizeEvent(self, e):
        super().resizeEvent(e)
        # Qt can deliver a resize during __init__, before the sidebar exists.
        if hasattr(self, "sidebar"):
            self.sidebar.set_compact(self.width() < self.COMPACT_AT)

    def changeEvent(self, e):
        # Keep the maximise glyph correct when the window state is changed by
        # the OS (Win+Up, snap, double-click) rather than by our button.
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange and hasattr(self, "titlebar") \
                and self.titlebar is not None:
            self.titlebar.sync_max_state()

    def showEvent(self, e):
        super().showEvent(e)
        self._enable_native_frame()

    def _enable_native_frame(self):
        """Ask Windows for the real drop shadow on a frameless window.

        Without it the app has no edge at all against a dark desktop. Purely
        cosmetic and best-effort — any failure just leaves the painted border.
        """
        if NATIVE_TITLEBAR or sys.platform != "win32" or self._shadow_done:
            return
        self._shadow_done = True
        try:
            import ctypes

            class MARGINS(ctypes.Structure):
                _fields_ = [("cxLeftWidth", ctypes.c_int),
                            ("cxRightWidth", ctypes.c_int),
                            ("cyTopHeight", ctypes.c_int),
                            ("cyBottomHeight", ctypes.c_int)]

            hwnd = int(self.winId())
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(
                hwnd, ctypes.byref(MARGINS(1, 1, 1, 1)))
        except Exception:
            pass

    # -- frameless window resizing ---------------------------------------- #
    def _edges_at(self, pos) -> Qt.Edges:
        """Which window edges the cursor is over, if any."""
        if NATIVE_TITLEBAR or self.isMaximized():
            return Qt.Edges()
        m, w, h = RESIZE_MARGIN, self.width(), self.height()
        edges = Qt.Edges()
        if pos.x() <= m:
            edges |= Qt.LeftEdge
        elif pos.x() >= w - m:
            edges |= Qt.RightEdge
        if pos.y() <= m:
            edges |= Qt.TopEdge
        elif pos.y() >= h - m:
            edges |= Qt.BottomEdge
        return edges

    @staticmethod
    def _cursor_for(edges: Qt.Edges):
        if edges in (Qt.LeftEdge | Qt.TopEdge, Qt.RightEdge | Qt.BottomEdge):
            return Qt.SizeFDiagCursor
        if edges in (Qt.RightEdge | Qt.TopEdge, Qt.LeftEdge | Qt.BottomEdge):
            return Qt.SizeBDiagCursor
        if edges in (Qt.LeftEdge, Qt.RightEdge):
            return Qt.SizeHorCursor
        if edges in (Qt.TopEdge, Qt.BottomEdge):
            return Qt.SizeVerCursor
        return Qt.ArrowCursor

    def mouseMoveEvent(self, e):
        self.setCursor(self._cursor_for(self._edges_at(e.position().toPoint())))
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            edges = self._edges_at(e.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                # The window manager owns the drag: correct snapping, aspect
                # behaviour and DPI handling for free.
                handle.startSystemResize(edges)
                return
        super().mousePressEvent(e)

    def leaveEvent(self, e):
        self.unsetCursor()
        super().leaveEvent(e)

    def request_restart(self):
        """Theme changed: rebuild the window so every widget re-reads the
        palette. main() handles the actual re-creation."""
        if self.backend.busy:
            QMessageBox.information(
                self, "Snapy",
                "The new theme will appear once the running job finishes.")
            return
        self.restart_requested = True
        self.close()

    def _no_cli(self):
        QMessageBox.critical(
            self, "Snapy",
            "phone_snapshot.py was not found.\n\n"
            "Put this folder next to the CLI, or one level inside the folder "
            "that contains it, then reopen Snapy.")

    def closeEvent(self, e):
        if self.backend.busy:
            ok = QMessageBox.question(
                self, "Snapy", "A job is still running. Stop it and quit?")
            if ok != QMessageBox.Yes:
                e.ignore()
                return
            self.backend.stop()
        self.settings.save()
        e.accept()


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Snapy")
    app.setOrganizationName("Loopax Technologies")

    settings = Settings()
    while True:
        # Palette first: widgets read colours when they are constructed.
        T.apply(settings["theme"])
        app.setStyleSheet(stylesheet())
        window = MainWindow(settings)
        window.show()
        code = app.exec()
        if not window.restart_requested:
            return code
        window.deleteLater()


if __name__ == "__main__":
    sys.exit(main())
