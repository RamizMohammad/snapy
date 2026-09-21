# -*- coding: utf-8 -*-
"""The seven Snapy screens, bound to the CLI through Backend."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QScrollArea, QSizePolicy, QSpinBox,
                               QStackedWidget, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from backend import Backend, CAPTURE_STEPS, step_index_for
from icons import svg_icon
from theme import T
from widgets import (Badge, Card, CapabilityRow, Console, DotBadge, Field,
                     PhoneGlyph, RadioCard, Ring, SegmentBar, StepRow, Toggle,
                     ToggleRow, divider, icon_label, label, legend_row,
                     section_label)


def human(n: float) -> str:
    n = float(n or 0)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class Session(QObject):
    """Shared selection state between pages."""
    snapshot_changed = Signal(object)   # Path or None
    navigate = Signal(int)

    def __init__(self):
        super().__init__()
        self.snapshot: Optional[Path] = None
        self.snapshots: List[Dict[str, Any]] = []

    def select(self, path: Optional[Path]):
        self.snapshot = path
        self.snapshot_changed.emit(path)


# --------------------------------------------------------------------------- #
#  Base
# --------------------------------------------------------------------------- #

class Page(QWidget):
    def __init__(self, title: str, subtitle: str = "", scroll: bool = True,
                 parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        if scroll:
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            outer.addWidget(area)
            host = QWidget()
            host.setObjectName("Content")
            area.setWidget(host)
        else:
            host = QWidget()
            host.setObjectName("Content")
            outer.addWidget(host)

        self.v = QVBoxLayout(host)
        self.v.setContentsMargins(32, 26, 32, 28)
        self.v.setSpacing(16)

        head = QHBoxLayout()
        head.setSpacing(12)
        col = QVBoxLayout()
        col.setSpacing(5)
        col.addWidget(label(title, "H1"))
        self.subtitle = label(subtitle, "Sub")
        col.addWidget(self.subtitle)
        head.addLayout(col)
        head.addStretch(1)
        self.head_right = QHBoxLayout()
        self.head_right.setSpacing(8)
        head.addLayout(self.head_right)
        self.v.addLayout(head)


# --------------------------------------------------------------------------- #
#  1. Device
# --------------------------------------------------------------------------- #

class DevicePage(Page):
    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Device",
                         "Connected over ADB. Keep the phone unlocked while "
                         "Snapy is working.", parent=parent)
        self.backend = backend
        self.session = session

        self.pill = DotBadge("CHECKING", "muted")
        self.head_right.addWidget(self.pill)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(backend.probe_device)
        refresh.clicked.connect(backend.grab_screen)
        self.head_right.addWidget(refresh)

        self.fields: Dict[str, Field] = {}
        self.v.addWidget(self._hero())
        self.tiles_row = QHBoxLayout()
        self.tiles_row.setSpacing(16)
        self.v.addLayout(self.tiles_row)
        self.tiles: Dict[str, Card] = {}
        self._build_tiles()
        self.cap_card, self.cap_grid = self._capabilities()
        self.v.addWidget(self.cap_card)
        self.v.addStretch(1)

        backend.device_ready.connect(self._apply)
        backend.screen_ready.connect(self.glyph.set_screen)

    def _hero(self) -> Card:
        card = Card(pad=24, spacing=0)
        row = QHBoxLayout()
        row.setSpacing(28)
        self.glyph = PhoneGlyph()
        row.addWidget(self.glyph, 0, Qt.AlignVCenter)

        right = QVBoxLayout()
        right.setSpacing(0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(56)
        grid.setVerticalSpacing(22)
        for i, (key, name, mono) in enumerate([
            ("model", "Model", False), ("android", "Android", True),
            ("patch", "Security patch", True), ("serial", "Serial", True),
        ]):
            f = Field(name, "-", mono=mono)
            self.fields[key] = f
            grid.addWidget(f, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        right.addLayout(grid)
        right.addStretch(1)
        right.addSpacing(18)
        right.addWidget(divider())
        right.addSpacing(18)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.addStretch(1)
        diag = QPushButton("  Run diagnostics")
        diag.setIcon(svg_icon("sliders", T.TEXT, 16))
        diag.clicked.connect(lambda: self.backend.run(["doctor"], "Diagnostics"))
        actions.addWidget(diag)
        create = QPushButton("  Create snapshot")
        create.setObjectName("Primary")
        create.setIcon(svg_icon("snapshot", T.ON_PRIMARY, 16))
        create.clicked.connect(lambda: self.session.navigate.emit(1))
        actions.addWidget(create)
        right.addLayout(actions)

        row.addLayout(right, 1)
        card.v.addLayout(row)
        return card

    def _build_tiles(self):
        from widgets import StatTile
        for key, name in [("apps", "Apps"), ("used", "Storage used"),
                          ("free", "Free space"), ("last", "Last snapshot")]:
            t = StatTile(name, "-", "")
            self.tiles[key] = t
            self.tiles_row.addWidget(t, 1)

    def _capabilities(self):
        card = Card(pad=22, spacing=0)
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(icon_label("shield", T.PRIMARY, 17))
        head.addWidget(label("What this phone allows", "CardTitle"))
        head.addStretch(1)
        head.addWidget(label("probed live over ADB", "CardNote"))
        card.v.addLayout(head)
        card.v.addSpacing(16)
        card.v.addWidget(divider())
        grid = QGridLayout()
        grid.setHorizontalSpacing(64)
        grid.setVerticalSpacing(0)
        grid.setContentsMargins(0, 6, 0, 0)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        card.v.addLayout(grid)
        return card, grid

    def _apply(self, d: Dict[str, Any]):
        if "error" in d:
            self.pill.setParent(None)
            self.pill = DotBadge("OFFLINE", "bad")
            self.head_right.insertWidget(0, self.pill)
            for f in self.fields.values():
                f.set_value("-")
            self.subtitle.setText(f"No device: {d['error']}")
            self.glyph.set_screen(None)
            return

        self.pill.setParent(None)
        self.pill = DotBadge("ONLINE", "ok")
        self.head_right.insertWidget(0, self.pill)
        self.subtitle.setText("Connected over ADB. Keep the phone unlocked "
                              "while Snapy is working.")
        self.fields["model"].set_value(f"{d['manufacturer']} {d['model']}".strip())
        self.fields["android"].set_value(f"{d['android']} (SDK {d['sdk']})")
        self.fields["patch"].set_value(d["patch"])
        self.fields["serial"].set_value(d["serial"])
        self.backend.grab_screen()

        self.tiles["apps"].set_value(str(d["apps"]), "user-installed", "")
        self.tiles["used"].set_value(d["storage_used"],
                                     f"{d['storage_pct']} of internal storage", "")
        self.tiles["free"].set_value(d["storage_free"], "available on /sdcard", "ok")
        last = self.session.snapshots[0]["captured"] if self.session.snapshots else "never"
        self.tiles["last"].set_value(last.split()[0] if last != "never" else "never",
                                     self.session.snapshots[0]["name"][:28]
                                     if self.session.snapshots else "no snapshots yet", "")

        while self.cap_grid.count():
            item = self.cap_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        caps = d.get("capabilities", [])
        half = (len(caps) + 1) // 2
        for i, (name, state, tone) in enumerate(caps):
            self.cap_grid.addWidget(CapabilityRow(name, state, tone),
                                    i % half, i // half)


# --------------------------------------------------------------------------- #
#  2. Capture  (setup + running in one page)
# --------------------------------------------------------------------------- #

class CapturePage(Page):
    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Create snapshot",
                         "Choose what to include. Larger selections take longer "
                         "and need more disk space.", scroll=False, parent=parent)
        self.backend = backend
        self.session = session
        self.steps: List[StepRow] = []
        self._step = -1

        self.stack = QStackedWidget()
        self.stack.addWidget(self._setup_view())
        self.stack.addWidget(self._running_view())
        self.v.addWidget(self.stack, 1)

        backend.job_started.connect(self._on_start)
        backend.job_finished.connect(self._on_finish)
        backend.phase_changed.connect(self._on_phase)
        backend.progress.connect(self._on_progress)
        backend.log_line.connect(self._on_log)

    # -- setup ----------------------------------------------------------- #
    def _setup_view(self) -> QWidget:
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(16)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("Content")
        area.setWidget(host)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(16)
        outer.addWidget(area, 1)

        # destination
        dest = Card(pad=18, spacing=10)
        dest.v.addWidget(label("SAVE TO", "FieldLabel"))
        row = QHBoxLayout()
        row.setSpacing(10)
        self.out_edit = QLineEdit(self.backend.settings["out_dir"])
        self.out_edit.setStyleSheet(
            f"QLineEdit{{background:{T.LOWEST}; border:1px solid {T.OUTLINE_DIM};"
            f"border-radius:{T.R_CTRL}px; padding:9px 12px;"
            f"font-family:{T.MONO}; font-size:12px;}}")
        row.addWidget(self.out_edit, 1)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._pick_out)
        row.addWidget(browse)
        dest.v.addLayout(row)
        col.addWidget(dest)

        # apk mode
        col.addWidget(section_label("App storage"))
        self.radios: Dict[str, RadioCard] = {}
        for key, title, desc, chip in [
            ("sideloaded", "Sideloaded apps only",
             "Apps not from Play Store. Play reinstalls the rest.", "smallest"),
            ("all", "Every app",
             "Fully offline restore. Much larger.", "largest"),
            ("none", "Inventory only",
             "Record what is installed, store no installers.", "tiny"),
        ]:
            rc = RadioCard(key, title, desc, chip)
            rc.selected.connect(self._pick_mode)
            self.radios[key] = rc
            col.addWidget(rc)
        self._pick_mode(self.backend.settings["apks_mode"])

        # toggles
        col.addWidget(section_label("Also include"))
        opts = Card(pad=18, spacing=0)
        s = self.backend.settings
        self.tog: Dict[str, ToggleRow] = {}
        for key, title, desc in [
            ("cloud_backup", "Trigger a Google cloud backup",
             "The only supported route to private app data. Recommended."),
            ("include_media", "Photos, videos and files",
             "Everything on internal storage."),
            ("android_data", "App external data",
             "/sdcard/Android/data and obb. Often refused on Android 11+."),
            ("hash", "Verify with SHA-256",
             "Slower to finish, stronger verification later."),
        ]:
            tr = ToggleRow(title, desc, bool(s[key]))
            self.tog[key] = tr
            opts.v.addWidget(tr)
        col.addWidget(opts)

        adv = QHBoxLayout()
        adv.setSpacing(24)
        adv.addWidget(label("Home-screen pages", "RowLabel"))
        self.spin_home = self._spin(0, 12, int(s["home_screens"]))
        adv.addWidget(self.spin_home)
        adv.addWidget(label("Parallel workers", "RowLabel"))
        self.spin_workers = self._spin(1, 8, int(s["workers"]))
        adv.addWidget(self.spin_workers)
        adv.addStretch(1)
        col.addLayout(adv)
        col.addStretch(1)

        # action bar
        bar = QHBoxLayout()
        bar.addStretch(1)
        self.hint = label("", "Sub")
        bar.addWidget(self.hint)
        self.btn_start = QPushButton("  Start capture")
        self.btn_start.setObjectName("Primary")
        self.btn_start.setIcon(svg_icon("snapshot", T.ON_PRIMARY, 16))
        self.btn_start.clicked.connect(self._start)
        bar.addWidget(self.btn_start)
        outer.addLayout(bar)
        return w

    @staticmethod
    def _spin(lo: int, hi: int, val: int) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(val)
        sp.setFixedWidth(64)
        sp.setStyleSheet(
            f"QSpinBox{{background:{T.LOWEST}; border:1px solid {T.OUTLINE_DIM};"
            f"border-radius:6px; padding:5px 8px; font-family:{T.MONO};}}")
        return sp

    def _pick_mode(self, key: str):
        for k, rc in self.radios.items():
            rc.set_selected(k == key)
        self.backend.settings["apks_mode"] = key

    def _pick_out(self):
        p = QFileDialog.getExistingDirectory(self, "Where should snapshots be saved?",
                                             self.out_edit.text())
        if p:
            self.out_edit.setText(p)

    def _start(self):
        s = self.backend.settings
        s["out_dir"] = self.out_edit.text().strip()
        for key, tr in self.tog.items():
            s[key] = tr.isChecked()
        s["home_screens"] = self.spin_home.value()
        s["workers"] = self.spin_workers.value()
        s.save()
        Path(s["out_dir"]).mkdir(parents=True, exist_ok=True)
        self.backend.run(self.backend.capture_args(), "Capture")

    # -- running --------------------------------------------------------- #
    def _running_view(self) -> QWidget:
        # Scrolled: ten step rows plus the header and console exceed the
        # viewport on a 900px window, and Qt would otherwise squash the rows.
        w = QScrollArea()
        w.setWidgetResizable(True)
        w.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        host = QWidget()
        host.setObjectName("Content")
        w.setWidget(host)
        col = QVBoxLayout(host)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(16)

        header = Card(pad=22, spacing=0)
        row = QHBoxLayout()
        row.setSpacing(26)
        self.ring = Ring(118, 9)
        row.addWidget(self.ring, 0, Qt.AlignVCenter)

        info = QVBoxLayout()
        info.setSpacing(6)
        self.run_title = label("Starting", "H2")
        info.addWidget(self.run_title)
        self.run_step = label("", "Sub")
        info.addWidget(self.run_step)
        self.run_detail = label("", "Mono", T.MUTED)
        info.addWidget(self.run_detail)
        info.addStretch(1)
        row.addLayout(info, 1)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.addStretch(1)
        self.btn_stop = QPushButton("  Stop capture")
        self.btn_stop.setObjectName("Danger")
        self.btn_stop.clicked.connect(self.backend.stop)
        right.addWidget(self.btn_stop, 0, Qt.AlignRight)
        right.addStretch(1)
        row.addLayout(right)
        header.v.addLayout(row)
        col.addWidget(header)

        steps_card = Card(pad=8, spacing=0)
        for i, (title, _n) in enumerate(CAPTURE_STEPS, 1):
            sr = StepRow(i, title)
            self.steps.append(sr)
            steps_card.v.addWidget(sr)
        col.addWidget(steps_card)

        con = Card(pad=14, spacing=10)
        head = QHBoxLayout()
        head.addWidget(icon_label("console", T.MUTED, 15))
        head.addWidget(label("LIVE ADB STREAM", "FieldLabel"))
        head.addStretch(1)
        con.v.addLayout(head)
        self.console = Console()
        self.console.setFixedHeight(150)
        con.v.addWidget(self.console)
        col.addWidget(con)
        col.addStretch(1)
        return w

    # -- job wiring ------------------------------------------------------- #
    def _on_start(self, label_text: str):
        if label_text != "Capture":
            return
        self._step = -1
        for s in self.steps:
            s.set_state(StepRow.QUEUED, "queued")
        self.console.clear()
        self.ring.set_value(0, "TOTAL")
        self.run_title.setText("Starting")
        self.stack.setCurrentIndex(1)

    def _on_finish(self, code: int, label_text: str):
        if label_text != "Capture":
            return
        for i, s in enumerate(self.steps):
            if s.state == StepRow.RUNNING:
                s.set_state(StepRow.DONE if code == 0 else StepRow.FAILED,
                            "done" if code == 0 else "failed")
        if code == 0:
            self.ring.set_value(100, "COMPLETE")
            self.run_title.setText("Snapshot complete")
        else:
            self.run_title.setText(f"Stopped (exit {code})")
        self.btn_stop.setText("  Back")
        try:
            self.btn_stop.clicked.disconnect()
        except RuntimeError:
            pass
        self.btn_stop.clicked.connect(lambda: self.stack.setCurrentIndex(0))
        self.backend.load_snapshots()

    def _on_phase(self, heading: str):
        if not self.backend.current_label == "Capture":
            return
        idx = step_index_for(heading)
        if idx < 0:
            return
        for i in range(idx):
            if self.steps[i].state != StepRow.DONE:
                self.steps[i].set_state(StepRow.DONE, "done")
        if idx != self._step:
            self._step = idx
            self.steps[idx].set_state(StepRow.RUNNING, "working")
            self.run_title.setText(CAPTURE_STEPS[idx][0])
            self.run_step.setText(f"Step {idx + 1} of {len(CAPTURE_STEPS)}")
            self.ring.set_value(idx / len(CAPTURE_STEPS) * 100)

    def _on_progress(self, cur: int, total: int, text: str):
        if self.backend.current_label != "Capture" or self._step < 0:
            return
        frac = cur / total if total else 0
        base = self._step / len(CAPTURE_STEPS)
        self.ring.set_value((base + frac / len(CAPTURE_STEPS)) * 100)
        self.steps[self._step].set_state(StepRow.RUNNING, f"{cur} / {total}")
        self.run_detail.setText(text.strip())

    def _on_log(self, text: str, tag: str):
        if self.backend.current_label == "Capture" and text.strip():
            self.console.append_line(text, tag)
            if tag == "ok" and self._step >= 0:
                detail = text.strip()[3:].strip()
                if len(detail) > 30:
                    detail = detail[:29] + "…"
                self.steps[self._step].set_state(StepRow.RUNNING, detail)


# --------------------------------------------------------------------------- #
#  3. Snapshots
# --------------------------------------------------------------------------- #

class SnapshotsPage(Page):
    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Snapshots", "", scroll=False, parent=parent)
        self.backend = backend
        self.session = session

        refresh = QPushButton("Refresh")
        refresh.clicked.connect(backend.load_snapshots)
        self.head_right.addWidget(refresh)
        new = QPushButton("  New snapshot")
        new.setObjectName("Primary")
        new.setIcon(svg_icon("snapshot", T.ON_PRIMARY, 15))
        new.clicked.connect(lambda: session.navigate.emit(1))
        self.head_right.addWidget(new)

        body = QHBoxLayout()
        body.setSpacing(16)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(["Snapshot", "Captured", "Device", "Android",
                                   "Apps", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(False)
        self.tree.setStyleSheet(self._tree_qss())
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            self.tree.header().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_select)
        body.addWidget(self.tree, 1)
        body.addWidget(self._detail_panel())
        self.v.addLayout(body, 1)

        backend.snapshots_ready.connect(self._populate)

    @staticmethod
    def _tree_qss() -> str:
        return (
            f"QTreeWidget{{background:{T.LOW}; border:1px solid {T.OUTLINE_DIM};"
            f"border-radius:{T.R_CARD}px; outline:none;"
            f"alternate-background-color:{T.LOW};}}"
            f"QTreeWidget::item{{padding:11px 8px; border-bottom:1px solid {T.OUTLINE_DIM};}}"
            f"QTreeWidget::item:selected{{background:{T.HIGH}; color:{T.TEXT};}}"
            f"QTreeWidget::item:hover{{background:{T.SURFACE};}}"
            f"QHeaderView::section{{background:{T.SURFACE}; color:{T.MUTED};"
            f"padding:9px 8px; border:none; border-bottom:1px solid {T.OUTLINE_DIM};"
            f"font-size:11px; font-weight:600;}}")

    def _detail_panel(self) -> QWidget:
        panel = Card(pad=20, spacing=12)
        panel.setMinimumWidth(292)
        panel.setMaximumWidth(360)
        self.d_name = label("No snapshot selected", "H2")
        self.d_name.setWordWrap(True)
        panel.v.addWidget(self.d_name)
        self.d_badges = QHBoxLayout()
        self.d_badges.setSpacing(8)
        panel.v.addLayout(self.d_badges)
        panel.v.addWidget(divider())

        self.d_grid = QGridLayout()
        self.d_grid.setVerticalSpacing(9)
        self.d_grid.setColumnStretch(1, 1)
        panel.v.addLayout(self.d_grid)

        panel.v.addWidget(divider())
        panel.v.addWidget(label("STORAGE BREAKDOWN", "FieldLabel"))
        self.d_bar = SegmentBar([], 8)
        panel.v.addWidget(self.d_bar)
        self.d_legend_host = QVBoxLayout()
        panel.v.addLayout(self.d_legend_host)
        panel.v.addStretch(1)

        for text, obj, slot in [
            ("Compare with phone", "Primary", lambda: self.session.navigate.emit(3)),
            ("Restore from this", "", lambda: self.session.navigate.emit(4)),
            ("Verify integrity", "Ghost", self._verify),
            ("Create archive", "Ghost", self._archive),
        ]:
            b = QPushButton(text)
            if obj:
                b.setObjectName(obj)
            b.clicked.connect(slot)
            panel.v.addWidget(b)
        return panel

    def _populate(self, rows: List[Dict[str, Any]]):
        self.session.snapshots = rows
        self.tree.clear()
        total = sum(r.get("bytes", 0) for r in rows)
        self.subtitle.setText(f"{len(rows)} snapshots · {human(total)} total"
                              if rows else "No snapshots in this folder yet")
        for r in rows:
            it = QTreeWidgetItem([
                r["name"], r.get("captured", "-"), r.get("device", "-"),
                str(r.get("android", "-")), str(r.get("apps", "-")),
                human(r.get("bytes", 0)),
            ])
            it.setData(0, Qt.UserRole, r)
            for c in (3, 4, 5):
                it.setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
            self.tree.addTopLevelItem(it)
        if rows:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def _on_select(self):
        items = self.tree.selectedItems()
        if not items:
            return
        r = items[0].data(0, Qt.UserRole)
        self.session.select(r["path"])
        self.d_name.setText(r["name"])

        while self.d_badges.count():
            w = self.d_badges.takeAt(0).widget()
            if w:
                w.deleteLater()
        tone = {"verified": "ok", "degraded": "warn"}.get(r["integrity"], "muted")
        self.d_badges.addWidget(Badge(r["integrity"], tone))
        self.d_badges.addStretch(1)

        while self.d_grid.count():
            w = self.d_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        stats = r.get("stats", {})
        rows_ = [
            ("Device", r.get("device", "-")), ("Android", str(r.get("android", "-"))),
            ("Build", r.get("build", "-")), ("Captured", r.get("captured", "-")),
            ("Files", f"{r.get('files', 0):,}"), ("Size", human(r.get("bytes", 0))),
            ("Apps", str(stats.get("user apps", "-"))),
            ("SMS rows", f"{stats.get('sms rows', 0):,}"),
        ]
        for i, (k, val) in enumerate(rows_):
            kl = label(k, "RowLabel", T.MUTED)
            vl = label(str(val), "Mono")
            vl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.d_grid.addWidget(kl, i, 0)
            self.d_grid.addWidget(vl, i, 1)

        apk = self._num(stats.get("APK bytes", "0 B"))
        media = self._num(stats.get("media bytes", "0 B"))
        other = max(0.0, r.get("bytes", 0) - apk - media)
        segs = [("Apps", apk, T.PRIMARY_FILL), ("Media", media, T.SUCCESS),
                ("Other", other, T.OUTLINE)]
        self.d_bar.set_segments(segs)
        while self.d_legend_host.count():
            w = self.d_legend_host.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.d_legend_host.addWidget(legend_row(segs, human, vertical=True))

    @staticmethod
    def _num(text: Any) -> float:
        m = re.match(r"([\d.]+)\s*([KMGT]?B)", str(text))
        if not m:
            return 0.0
        mult = {"B": 1, "KB": 1024, "MB": 1024 ** 2,
                "GB": 1024 ** 3, "TB": 1024 ** 4}[m.group(2)]
        return float(m.group(1)) * mult

    def _verify(self):
        if self.session.snapshot:
            self.backend.run(["verify", "--snapshot", str(self.session.snapshot)],
                             "Verify")

    def _archive(self):
        if not self.session.snapshot:
            return
        self.backend.run(["archive", "--snapshot", str(self.session.snapshot),
                          "--compresslevel", "0"], "Archive")


# --------------------------------------------------------------------------- #
#  4. Compare
# --------------------------------------------------------------------------- #

class ComparePage(Page):
    CATEGORIES = [
        ("Apps missing", "apps_missing", "package"),
        ("Apps at a different version", "apps_version_differs", "package"),
        ("Permission grants missing", "permissions_missing", "permission"),
        ("Settings differing", "settings_differ", "key"),
        ("Battery exemptions missing", "battery_whitelist_missing", None),
        ("Default-app roles differing", "roles_differ", "role"),
        ("Keyboards not enabled", "input_methods_missing", None),
        ("Media files missing", "media_missing", "device_path"),
        ("Files with size mismatch", "media_size_mismatch", "device_path"),
    ]

    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Compare", "No snapshot selected", scroll=False,
                         parent=parent)
        self.backend = backend
        self.session = session

        rerun = QPushButton("  Compare")
        rerun.setObjectName("Primary")
        rerun.setIcon(svg_icon("compare", T.ON_PRIMARY, 15))
        rerun.clicked.connect(self._run)
        self.head_right.addWidget(rerun)

        self.banner = self._banner()
        self.v.addWidget(self.banner)
        self.banner.hide()

        self.tiles = QHBoxLayout()
        self.tiles.setSpacing(16)
        self.v.addLayout(self.tiles)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderLabels(["Category", "Missing / differing"])
        self.tree.setStyleSheet(SnapshotsPage._tree_qss())
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.v.addWidget(self.tree, 1)

        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.t_skip = Toggle(False)
        bar.addWidget(self.t_skip)
        bar.addWidget(label("Skip media index", "RowLabel"))
        self.t_deep = Toggle(False)
        bar.addWidget(self.t_deep)
        bar.addWidget(label("Count SMS / calls", "RowLabel"))
        bar.addSpacing(20)
        self.t_dry = Toggle(True)
        bar.addWidget(self.t_dry)
        bar.addWidget(label("Dry run", "RowLabel"))
        bar.addStretch(1)
        self.btn_fix = QPushButton("  Restore what's missing")
        self.btn_fix.setIcon(svg_icon("restore", T.TEXT, 15))
        self.btn_fix.clicked.connect(self._fix)
        bar.addWidget(self.btn_fix)
        self.v.addLayout(bar)

        session.snapshot_changed.connect(self._on_snapshot)
        backend.diff_ready.connect(self._render)
        backend.job_finished.connect(self._after_job)

    def _banner(self) -> QFrame:
        f = QFrame()
        f.setStyleSheet(
            f"QFrame{{background:#2a2113; border:1px solid {T.WARNING}55;"
            f"border-left:3px solid {T.WARNING}; border-radius:{T.R_CTRL}px;}}")
        h = QHBoxLayout(f)
        h.setContentsMargins(16, 13, 16, 13)
        h.setSpacing(13)
        self.b_title = label("", "H2")
        self.b_sub = label("", "Sub")
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(self.b_title)
        col.addWidget(self.b_sub)
        h.addLayout(col)
        h.addStretch(1)
        return f

    def _on_snapshot(self, path: Optional[Path]):
        self.subtitle.setText(f"Phone checked against {path.name}" if path
                              else "No snapshot selected")
        if path:
            self.backend.load_latest_diff(path)

    def _run(self):
        if not self.session.snapshot:
            QMessageBox.information(self, "Snapy", "Pick a snapshot first.")
            return
        self.backend.run(self.backend.diff_args(
            self.session.snapshot, self.t_skip.isChecked(),
            self.t_deep.isChecked()), "Compare")

    def _fix(self):
        if not self.session.snapshot:
            return
        if not self.t_dry.isChecked():
            ok = QMessageBox.question(
                self, "Snapy",
                "This will install apps, change settings and push files to the "
                "connected phone.\n\nContinue?")
            if ok != QMessageBox.Yes:
                return
        self.backend.run(self.backend.diff_args(
            self.session.snapshot, self.t_skip.isChecked(), False,
            fix=True, dry=self.t_dry.isChecked()), "Compare fix")

    def _after_job(self, _code: int, label_text: str):
        if label_text.startswith("Compare") and self.session.snapshot:
            self.backend.load_latest_diff(self.session.snapshot)

    def _render(self, d: Dict[str, Any]):
        self.tree.clear()
        while self.tiles.count():
            w = self.tiles.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not d:
            self.banner.hide()
            return

        from widgets import StatTile
        apps = len(d.get("apps_missing", []))
        files = len(d.get("media_missing", []))
        settings_n = len(d.get("settings_differ", []))
        for name, n in [("Apps missing", apps), ("Files missing", files),
                        ("Settings differing", settings_n)]:
            t = StatTile(name, str(n), "identical" if n == 0 else "needs attention",
                         "ok" if n == 0 else "warn")
            t.set_value_colour(T.SUCCESS if n == 0 else T.ERROR)
            self.tiles.addWidget(t, 1)
        self.tiles.addStretch(1)

        total = sum(len(d.get(key, [])) for _t, key, _k in self.CATEGORIES)
        if total:
            self.b_title.setText(f"{total} differences found")
            self.b_sub.setText("Your phone is missing items this snapshot contains.")
            self.banner.show()
        else:
            self.banner.hide()

        for title, key, field in self.CATEGORIES:
            items = d.get(key, [])
            n = len(items)
            parent = QTreeWidgetItem([title, str(n)])
            parent.setForeground(1, QBrush(QColor(T.SUCCESS if n == 0 else T.ERROR)))
            parent.setTextAlignment(1, Qt.AlignCenter)
            self.tree.addTopLevelItem(parent)
            for it in items[:200]:
                if isinstance(it, dict):
                    if key == "settings_differ":
                        text = (f"{it.get('namespace')}.{it.get('key')}: "
                                f"{it.get('phone')} → {it.get('snapshot')}")
                    elif key == "apps_missing":
                        text = (f"{it.get('package')}   "
                                + ("offline reinstall" if it.get("restorable")
                                   else "needs Play Store"))
                    elif field:
                        text = str(it.get(field, it))
                    else:
                        text = json.dumps(it)
                else:
                    text = str(it)
                parent.addChild(QTreeWidgetItem(["    " + text, ""]))
            if n > 200:
                parent.addChild(QTreeWidgetItem([f"    ... and {n - 200} more", ""]))


# --------------------------------------------------------------------------- #
#  5. Restore
# --------------------------------------------------------------------------- #

class RestorePage(Page):
    PARTS = [
        ("apps", "Apps", "Reinstall from the APKs in this snapshot", True),
        ("perms", "Runtime permissions", "Re-grant exactly what was granted before", True),
        ("settings", "Settings", "Display, sound, gestures, night light", True),
        ("battery", "Battery exemptions", "Apps allowed to run unrestricted", True),
        ("defaults", "Default apps and keyboards", "Browser, dialer, SMS, launcher", True),
        ("appops", "App-ops", "Per-app special toggles", False),
        ("media", "Photos, videos and files", "Slow over USB — can be many GB", False),
    ]

    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Restore", "No snapshot selected", parent=parent)
        self.backend = backend
        self.session = session

        note = Card(pad=18, spacing=8)
        note.v.addWidget(label("Order matters", "CardTitle"))
        for text in [
            "1.  At the setup wizard, choose 'Restore from cloud backup' first — "
            "that is the only thing that brings private app data back.",
            "2.  Let Play Store finish its own reinstalls.",
            "3.  Then run the restore below.",
            "4.  Push media before opening WhatsApp for the first time, or its "
            "local-backup offer never appears.",
        ]:
            lb = label(text, "Sub")
            lb.setWordWrap(True)
            note.v.addWidget(lb)
        self.v.addWidget(note)

        parts = Card(pad=18, spacing=0)
        parts.v.addWidget(label("WHAT TO RESTORE", "FieldLabel"))
        parts.v.addSpacing(8)
        self.rows: Dict[str, ToggleRow] = {}
        for key, title, desc, default in self.PARTS:
            tr = ToggleRow(title, desc, default)
            self.rows[key] = tr
            parts.v.addWidget(tr)
        self.v.addWidget(parts)

        mode = Card(pad=18, spacing=10)
        mode.v.addWidget(label("SETTINGS MODE", "FieldLabel"))
        self.mode_cards: Dict[str, RadioCard] = {}
        for key, title, desc in [
            ("safe", "Safe", "A curated allowlist of ~70 keys. Recommended."),
            ("all", "Everything captured",
             "Writes all captured keys back. Can destabilise Settings."),
        ]:
            rc = RadioCard(key, title, desc)
            rc.selected.connect(self._pick_mode)
            self.mode_cards[key] = rc
            mode.v.addWidget(rc)
        self._pick_mode("safe")
        self.v.addWidget(mode)

        bar = QHBoxLayout()
        bar.setSpacing(12)
        self.t_dry = Toggle(True)
        bar.addWidget(self.t_dry)
        bar.addWidget(label("Dry run — print commands, change nothing", "RowLabel"))
        self.t_reinstall = Toggle(False)
        bar.addSpacing(18)
        bar.addWidget(self.t_reinstall)
        bar.addWidget(label("Reinstall apps already present", "RowLabel"))
        bar.addStretch(1)
        run = QPushButton("  Run restore")
        run.setObjectName("Primary")
        run.setIcon(svg_icon("restore", T.ON_PRIMARY, 15))
        run.clicked.connect(self._run)
        bar.addWidget(run)
        self.v.addLayout(bar)
        self.v.addStretch(1)

        session.snapshot_changed.connect(
            lambda p: self.subtitle.setText(f"From {p.name}" if p
                                            else "No snapshot selected"))

    def _pick_mode(self, key: str):
        for k, rc in self.mode_cards.items():
            rc.set_selected(k == key)
        self._mode = key
        if key == "all":
            QMessageBox.warning(
                self, "Snapy",
                "'Everything captured' writes every secure and global setting back.\n\n"
                "Many encode provisioning state and device identity, and writing them "
                "onto a fresh install can leave Settings in a broken state.\n\n"
                "Use Safe unless you have a specific reason.")

    def _run(self):
        if not self.session.snapshot:
            QMessageBox.information(self, "Snapy", "Pick a snapshot first.")
            return
        parts = {k: r.isChecked() for k, r in self.rows.items()}
        if not any(parts.values()):
            QMessageBox.information(self, "Snapy", "Nothing selected to restore.")
            return
        if not self.t_dry.isChecked():
            ok = QMessageBox.question(
                self, "Snapy",
                "This will modify the connected phone: installing apps, writing "
                "settings and granting permissions.\n\nContinue?")
            if ok != QMessageBox.Yes:
                return
        self.backend.run(self.backend.restore_args(
            self.session.snapshot, parts, self._mode,
            self.t_dry.isChecked(), self.t_reinstall.isChecked()), "Restore")


# --------------------------------------------------------------------------- #
#  6. Checklist
# --------------------------------------------------------------------------- #

class ChecklistPage(Page):
    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Before the wipe",
                         "Things no tool can do for you, because Android does "
                         "not expose them.", scroll=False, parent=parent)
        self.backend = backend
        self.session = session
        self._vars: List[tuple] = []
        self._state_path: Optional[Path] = None

        self.counter = label("", "H2")
        self.head_right.addWidget(self.counter)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.host = QWidget()
        self.host.setObjectName("Content")
        area.setWidget(self.host)
        self.col = QVBoxLayout(self.host)
        self.col.setContentsMargins(0, 0, 10, 0)
        self.col.setSpacing(8)
        self.v.addWidget(area, 1)

        session.snapshot_changed.connect(lambda _p: self.load())

    def load(self):
        while self.col.count():
            item = self.col.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._vars = []
        p = self.session.snapshot
        if not p:
            self.col.addWidget(label("No snapshot selected", "Sub"))
            self.counter.setText("")
            return
        md = p / "09_reports" / "PRE-WIPE-CHECKLIST.md"
        if not md.is_file():
            self.col.addWidget(label("This snapshot has no checklist", "Sub"))
            self.counter.setText("")
            return
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            return
        self._state_path = p / "09_reports" / "checklist_state.json"
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

        for raw in text.splitlines():
            line = raw.rstrip()
            s = line.strip()
            if not s or s == "---":
                continue
            if s.startswith("## "):
                self.col.addSpacing(10)
                self.col.addWidget(section_label(s[3:]))
            elif s.startswith("# "):
                continue
            elif re.match(r"^- \[[ xX]\] ", s):
                done = s[3].lower() == "x"
                txt = self._clean(re.sub(r"^- \[[ xX]\] ", "", s))
                self._add(txt[:90], txt, bool(state.get(txt[:90], done)))
            elif s.startswith("| **"):
                cells = [c.strip() for c in s.strip("|").split("|")]
                if len(cells) >= 3:
                    sev = cells[0].replace("*", "")
                    pkg = cells[1].replace("`", "")
                    self._add(f"app::{pkg}", self._clean(cells[2]),
                              bool(state.get(f"app::{pkg}", False)),
                              severity=sev, title=pkg)
            elif s.startswith("|") or s.startswith("_"):
                continue
            else:
                lb = label(self._clean(s), "Sub")
                lb.setWordWrap(True)
                self.col.addWidget(lb)
        self.col.addStretch(1)
        self._update_counter()

    @staticmethod
    def _clean(s: str) -> str:
        return re.sub(r"`(.+?)`", r"\1", re.sub(r"\*\*(.+?)\*\*", r"\1", s))

    def _add(self, key: str, text: str, checked: bool,
             severity: str = "", title: str = ""):
        row = QFrame()
        row.setObjectName("Inset")
        h = QHBoxLayout(row)
        h.setContentsMargins(14, 11, 14, 11)
        h.setSpacing(13)

        tog = Toggle(checked)
        tog.setFixedSize(34, 20)
        h.addWidget(tog, 0, Qt.AlignTop)
        if severity:
            tone = {"CRITICAL": "bad", "HIGH": "warn"}.get(severity, "muted")
            h.addWidget(Badge(severity, tone), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        if title:
            t = label(title, "Mono")
            t.setStyleSheet(f"font-family:{T.MONO}; font-size:12px; font-weight:600;")
            col.addWidget(t)
        body = label(text, "Sub")
        body.setWordWrap(True)
        col.addWidget(body)
        h.addLayout(col, 1)
        self.col.addWidget(row)

        tog.toggled.connect(lambda _v: self._update_counter())
        self._vars.append((key, tog))

    def _update_counter(self):
        if not self._vars:
            self.counter.setText("")
            return
        done = sum(1 for _k, t in self._vars if t.isChecked())
        self.counter.setText(f"{done} / {len(self._vars)} done")
        if self._state_path:
            try:
                self._state_path.write_text(
                    json.dumps({k: t.isChecked() for k, t in self._vars}, indent=2),
                    encoding="utf-8")
            except OSError:
                pass


# --------------------------------------------------------------------------- #
#  7. Settings
# --------------------------------------------------------------------------- #

class SettingsPage(Page):
    def __init__(self, backend: Backend, session: Session, parent=None):
        super().__init__("Settings", "Paths and defaults. Saved automatically.",
                         parent=parent)
        self.backend = backend
        s = backend.settings

        look = Card(pad=20, spacing=12)
        look.v.addWidget(label("APPEARANCE", "FieldLabel"))
        self.theme_cards: Dict[str, RadioCard] = {}
        row = QHBoxLayout()
        row.setSpacing(12)
        for key, title, desc in [
            ("dark", "Dark", "Charcoal surfaces, for low-light work."),
            ("light", "Light", "Brighter, higher contrast in a lit room."),
        ]:
            rc = RadioCard(key, title, desc)
            rc.selected.connect(self._pick_theme)
            self.theme_cards[key] = rc
            row.addWidget(rc, 1)
        look.v.addLayout(row)
        self._theme = s["theme"]
        self._pick_theme(self._theme, announce=False)
        self.v.addWidget(look)

        card = Card(pad=20, spacing=16)
        card.v.addWidget(label("SNAPSHOT FOLDER", "FieldLabel"))
        self.out = self._path_row(s["out_dir"], self._pick_dir)
        card.v.addWidget(self.out[0])
        card.v.addWidget(divider())
        card.v.addWidget(label("ADB BINARY", "FieldLabel"))
        self.adb = self._path_row(s["adb_path"] or "", self._pick_adb,
                                  "blank = auto-detect")
        card.v.addWidget(self.adb[0])
        self.v.addWidget(card)

        # Name and version, not a file path: the install location is inside the
        # user's profile and has no business being on screen or in a screenshot.
        info = Card(pad=20, spacing=10)
        info.v.addWidget(label("ENGINE", "FieldLabel"))
        eng = backend.engine
        eng_row = QHBoxLayout()
        eng_row.setSpacing(10)
        eng_row.addWidget(Badge(eng["badge"], eng["tone"]), 0, Qt.AlignVCenter)
        detail = label(eng["detail"], "Mono",
                       T.TEXT if eng["tone"] == "ok" else T.ERROR)
        detail.setWordWrap(True)
        eng_row.addWidget(detail, 1)
        info.v.addLayout(eng_row)
        info.v.addWidget(label(
            "Snapy drives a command-line tool. Every action in the UI runs the "
            "same commands you could type yourself.", "Sub"))
        self.v.addWidget(info)

        save = QPushButton("Save")
        save.setObjectName("Primary")
        save.clicked.connect(self._save)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(save)
        self.v.addLayout(row)
        self.v.addStretch(1)

    def _path_row(self, value: str, slot, hint: str = ""):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        edit = QLineEdit(value)
        edit.setStyleSheet(
            f"QLineEdit{{background:{T.LOWEST}; border:1px solid {T.OUTLINE_DIM};"
            f"border-radius:{T.R_CTRL}px; padding:9px 12px;"
            f"font-family:{T.MONO}; font-size:12px;}}")
        h.addWidget(edit, 1)
        b = QPushButton("Browse")
        b.clicked.connect(lambda: slot(edit))
        h.addWidget(b)
        if hint:
            h.addWidget(label(hint, "Sub"))
        return w, edit

    def _pick_dir(self, edit: QLineEdit):
        p = QFileDialog.getExistingDirectory(self, "Snapshot folder", edit.text())
        if p:
            edit.setText(p)

    def _pick_adb(self, edit: QLineEdit):
        p, _ = QFileDialog.getOpenFileName(self, "Locate adb", edit.text())
        if p:
            edit.setText(p)

    def _pick_theme(self, key: str, announce: bool = True):
        for k, rc in self.theme_cards.items():
            rc.set_selected(k == key)
        self._theme = key
        if announce and key != self.backend.settings["theme"]:
            self.backend.settings["theme"] = key
            self.backend.settings.save()
            # Colours are read when widgets are constructed, so the window is
            # rebuilt rather than restyled in place.
            win = self.window()
            if hasattr(win, "request_restart"):
                win.request_restart()

    def _save(self):
        self.backend.settings["theme"] = self._theme
        self.backend.settings["out_dir"] = self.out[1].text().strip()
        self.backend.settings["adb_path"] = self.adb[1].text().strip()
        self.backend.settings.save()
        self.backend.probe_device()
        self.backend.load_snapshots()
