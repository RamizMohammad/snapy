#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
snapy_gui - desktop front end for phone-snapshot.

A Tkinter window over the phone_snapshot CLI: device status, capture with every
option, a snapshot browser, phone-vs-backup comparison, restore, and the pre-wipe
checklist as a real tick-list that remembers what you ticked.

Stdlib only. Put it next to phone_snapshot.py (or snapy.py) and run:

    python snapy_gui.py

Long operations run as subprocesses so they stream live and can be stopped.
Quick device lookups import the CLI module directly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "Tkinter is not available for this Python.\n"
        "  Windows/macOS: reinstall Python from python.org (Tk is included).\n"
        "  Linux: sudo apt install python3-tk\n")
    sys.exit(1)

APP_NAME = "Snapy"
APP_VERSION = "1.0"
HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "snapy_gui.json"
CLI_CANDIDATES = ["phone_snapshot.py", "snapy.py", "phone-snapshot.py"]

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

# Palette. Native ttk chrome, dark console for the log.
BG_LOG = "#11151c"
FG_LOG = "#c9d3e0"
COL_OK = "#3fb950"
COL_WARN = "#d29922"
COL_ERR = "#f85149"
COL_INFO = "#58a6ff"
COL_HEAD = "#e6edf3"
COL_DIM = "#6e7a8a"


# --------------------------------------------------------------------------- #
#  CLI discovery / import
# --------------------------------------------------------------------------- #

def find_cli(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_file() else None
    for name in CLI_CANDIDATES:
        p = HERE / name
        if p.is_file():
            return p
    for p in sorted(HERE.glob("*.py")):
        if p.name == Path(__file__).name:
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:3000]
        except OSError:
            continue
        if "phone-snapshot" in head and "def cmd_capture" in head:
            return p
    return None


def import_cli(path: Path):
    """Import the CLI module so the GUI can reuse Adb and the parsers."""
    try:
        spec = importlib.util.spec_from_file_location("phone_snapshot_cli", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Background job
# --------------------------------------------------------------------------- #

class Job:
    """One CLI subprocess, streamed line by line into a queue.

    Reads raw bytes rather than lines because the CLI uses \\r for progress
    counters; those must update in place instead of flooding the log.
    """

    def __init__(self, argv: List[str], out_q: "queue.Queue",
                 on_exit: Optional[Callable[[int], None]] = None):
        self.argv = argv
        self.q = out_q
        self.on_exit = on_exit
        self.proc: Optional[subprocess.Popen] = None
        self.thread: Optional[threading.Thread] = None
        self.stopped = False

    def start(self) -> None:
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                self.argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as e:
            self.q.put(("line", f"[x] could not start: {e}", True))
            if self.on_exit:
                self.on_exit(-1)
            return

        buf = b""
        assert self.proc.stdout is not None
        while True:
            chunk = self.proc.stdout.read(1)
            if not chunk:
                break
            if chunk in (b"\n", b"\r"):
                text = buf.decode("utf-8", "replace")
                buf = b""
                self.q.put(("line", text, chunk == b"\n"))
            else:
                buf += chunk
        if buf:
            self.q.put(("line", buf.decode("utf-8", "replace"), True))

        rc = self.proc.wait()
        self.q.put(("exit", rc, True))
        if self.on_exit:
            self.on_exit(rc)

    def stop(self) -> None:
        self.stopped = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass


# --------------------------------------------------------------------------- #
#  Small widgets
# --------------------------------------------------------------------------- #

class StatCard(ttk.Frame):
    """A labelled value with an optional coloured state dot."""

    def __init__(self, master, label: str, value: str = "-", **kw):
        super().__init__(master, padding=(10, 6), **kw)
        self.columnconfigure(0, weight=1)
        ttk.Label(self, text=label.upper(), foreground="#6e7a8a",
                  font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w")
        self.var = tk.StringVar(value=value)
        self.value_label = ttk.Label(self, textvariable=self.var,
                                     font=("Segoe UI", 11))
        self.value_label.grid(row=1, column=0, sticky="w", pady=(1, 0))

    def set(self, value: str, colour: Optional[str] = None) -> None:
        self.var.set(value)
        if colour:
            self.value_label.configure(foreground=colour)


class ScrollFrame(ttk.Frame):
    """A vertically scrollable frame. `body` is where you put widgets."""

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")

    def _on_body(self, _e=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)

    def _on_wheel(self, e):
        try:
            if str(self.canvas.winfo_containing(e.x_root, e.y_root)).startswith(str(self.canvas)):
                self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except (tk.TclError, AttributeError):
            pass

    def clear(self):
        for w in self.body.winfo_children():
            w.destroy()


# --------------------------------------------------------------------------- #
#  Main window
# --------------------------------------------------------------------------- #

class App(tk.Tk):
    def __init__(self, cli: Optional[Path]):
        super().__init__()
        self.title(f"{APP_NAME} - Android snapshot & restore")
        self.geometry("1120x780")
        self.minsize(940, 640)

        self.cfg = self._load_config()
        self.cli_path = cli
        self.cli_mod = import_cli(cli) if cli else None
        self.q: "queue.Queue" = queue.Queue()
        self.job: Optional[Job] = None
        self.selected_snapshot: Optional[Path] = None
        self._last_line_was_cr = False

        style = ttk.Style(self)
        if os.name != "nt" and "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Danger.TButton", foreground="#a4262c")
        style.configure("Go.TButton", font=("Segoe UI", 9, "bold"))

        self._build_header()
        self._build_tabs()
        self._build_log()
        self._build_statusbar()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._drain_queue)
        self.after(400, self.refresh_device)
        self.after(600, self.refresh_snapshots)

        if not self.cli_path:
            self.log_line("[x] phone_snapshot.py not found next to this file.", "err")
            self.log_line("    Put snapy_gui.py in the same folder as the CLI, "
                          "or start it with:  python snapy_gui.py --cli <path>", "dim")

    # -- config --------------------------------------------------------- #
    def _load_config(self) -> Dict[str, Any]:
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_config(self) -> None:
        try:
            self.cfg["out_dir"] = self.out_dir.get()
            self.cfg["adb_path"] = self.adb_path.get()
            self.cfg["geometry"] = self.geometry()
            CONFIG_PATH.write_text(json.dumps(self.cfg, indent=2), encoding="utf-8")
        except (OSError, AttributeError):
            pass

    # -- chrome --------------------------------------------------------- #
    def _build_header(self) -> None:
        bar = ttk.Frame(self, padding=(12, 10, 12, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(side="left")
        ttk.Label(bar, text=f"  v{APP_VERSION}", foreground=COL_DIM).pack(side="left")

        self.dot = tk.Canvas(bar, width=12, height=12, highlightthickness=0)
        self.dot_id = self.dot.create_oval(2, 2, 11, 11, fill="#555", outline="")
        self.dot.pack(side="right", padx=(8, 0))
        self.conn_var = tk.StringVar(value="checking...")
        ttk.Label(bar, textvariable=self.conn_var).pack(side="right")

    def _build_tabs(self) -> None:
        self.nb = ttk.Notebook(self)
        self.nb.pack(fill="both", expand=True, padx=12, pady=(4, 0))
        self.tab_device = ttk.Frame(self.nb, padding=12)
        self.tab_capture = ttk.Frame(self.nb, padding=12)
        self.tab_snaps = ttk.Frame(self.nb, padding=12)
        self.tab_compare = ttk.Frame(self.nb, padding=12)
        self.tab_restore = ttk.Frame(self.nb, padding=12)
        self.tab_check = ttk.Frame(self.nb, padding=12)
        for frame, label in [
            (self.tab_device, "  Device  "),
            (self.tab_capture, "  Capture  "),
            (self.tab_snaps, "  Snapshots  "),
            (self.tab_compare, "  Compare  "),
            (self.tab_restore, "  Restore  "),
            (self.tab_check, "  Pre-wipe checklist  "),
        ]:
            self.nb.add(frame, text=label)
        self._build_device_tab()
        self._build_capture_tab()
        self._build_snapshots_tab()
        self._build_compare_tab()
        self._build_restore_tab()
        self._build_checklist_tab()

    def _build_log(self) -> None:
        wrap = ttk.Frame(self, padding=(12, 8, 12, 0))
        wrap.pack(fill="both", expand=False)
        head = ttk.Frame(wrap)
        head.pack(fill="x")
        ttk.Label(head, text="Activity", font=("Segoe UI", 9, "bold")).pack(side="left")
        ttk.Button(head, text="Clear", width=8,
                   command=lambda: self._log_clear()).pack(side="right")
        ttk.Button(head, text="Save log", width=10,
                   command=self._save_log).pack(side="right", padx=(0, 6))

        mono = "Consolas" if os.name == "nt" else "monospace"
        self.log = tk.Text(wrap, height=13, bg=BG_LOG, fg=FG_LOG, insertbackground=FG_LOG,
                           font=(mono, 9), wrap="none", relief="flat",
                           padx=10, pady=8)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set, state="disabled")
        sb.pack(side="right", fill="y", pady=(6, 0))
        self.log.pack(fill="both", expand=True, pady=(6, 0))
        for tag, col in (("ok", COL_OK), ("warn", COL_WARN), ("err", COL_ERR),
                         ("info", COL_INFO), ("dim", COL_DIM)):
            self.log.tag_configure(tag, foreground=col)
        self.log.tag_configure("head", foreground=COL_HEAD, font=(mono, 9, "bold"))

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(12, 6, 12, 10))
        bar.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status).pack(side="left")
        self.progress = ttk.Progressbar(bar, length=240, mode="determinate")
        self.progress.pack(side="right")
        self.btn_stop = ttk.Button(bar, text="Stop", width=8, state="disabled",
                                   command=self.stop_job)
        self.btn_stop.pack(side="right", padx=(0, 10))

    # ------------------------------------------------------------------ #
    #  Device tab
    # ------------------------------------------------------------------ #
    def _build_device_tab(self) -> None:
        t = self.tab_device
        grid = ttk.Frame(t)
        grid.pack(fill="x")
        for i in range(4):
            grid.columnconfigure(i, weight=1, uniform="cards")
        self.cards: Dict[str, StatCard] = {}
        for i, (key, label) in enumerate([
            ("model", "Device"), ("android", "Android"),
            ("serial", "Serial"), ("patch", "Security patch"),
            ("storage", "Storage free"), ("apps", "User apps"),
            ("screen", "Screen"), ("locale", "Locale / timezone"),
        ]):
            card = StatCard(grid, label)
            card.grid(row=i // 4, column=i % 4, sticky="ew", padx=4, pady=4)
            self.cards[key] = card

        ttk.Separator(t, orient="horizontal").pack(fill="x", pady=12)

        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="adb path").pack(side="left")
        self.adb_path = tk.StringVar(value=self.cfg.get("adb_path", ""))
        ttk.Entry(row, textvariable=self.adb_path, width=52).pack(side="left", padx=8)
        ttk.Button(row, text="Browse", command=self._pick_adb).pack(side="left")
        ttk.Label(row, text="  (blank = auto-detect)",
                  foreground=COL_DIM).pack(side="left")

        btns = ttk.Frame(t)
        btns.pack(fill="x", pady=14)
        ttk.Button(btns, text="Refresh status", command=self.refresh_device).pack(side="left")
        ttk.Button(btns, text="Run full capability probe (doctor)",
                   command=self.run_doctor).pack(side="left", padx=8)

        help_txt = (
            "Before capturing: unlock the phone, plug it in over USB, set the USB mode to "
            "File transfer, and accept the 'Allow USB debugging?' prompt.\n\n"
            "Developer options live behind Settings > About phone > tap Build number seven "
            "times. USB debugging is inside Developer options.\n\n"
            "The capability probe tells you exactly what this phone's build allows - some "
            "manufacturers block the SMS provider or /sdcard/Android/data."
        )
        box = ttk.LabelFrame(t, text="Getting connected", padding=12)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=help_txt, wraplength=980, justify="left",
                  foreground="#4a5464").pack(anchor="w")

    def _pick_adb(self) -> None:
        p = filedialog.askopenfilename(
            title="Locate adb",
            filetypes=[("adb", "adb.exe adb"), ("All files", "*.*")])
        if p:
            self.adb_path.set(p)
            self.refresh_device()

    def refresh_device(self) -> None:
        if self.job:
            return
        threading.Thread(target=self._refresh_device_worker, daemon=True).start()

    def _refresh_device_worker(self) -> None:
        if not self.cli_mod:
            self.q.put(("device", {"error": "CLI module unavailable"}, True))
            return
        try:
            adb = self.cli_mod.Adb(self.adb_path.get().strip() or None, None)
        except Exception as e:
            self.q.put(("device", {"error": str(e)}, True))
            return
        devs = [d for d in adb.devices() if d.get("state") == "device"]
        if not devs:
            self.q.put(("device", {"error": "no device connected"}, True))
            return
        adb.serial = devs[0]["serial"]
        props = self.cli_mod.parse_getprop(adb.shell_ok("getprop"))
        df = adb.shell_ok("df -h /sdcard 2>/dev/null")
        free = "-"
        for line in df.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                free = parts[3]
                break
        napps = len([l for l in adb.shell_ok("pm list packages -3").splitlines()
                     if l.startswith("package:")])
        self.q.put(("device", {
            "model": f"{props.get('ro.product.manufacturer', '')} "
                     f"{props.get('ro.product.model', '?')}".strip(),
            "android": f"{props.get('ro.build.version.release', '?')} "
                       f"(SDK {props.get('ro.build.version.sdk', '?')})",
            "serial": adb.serial,
            "patch": props.get("ro.build.version.security_patch", "-"),
            "storage": free,
            "apps": str(napps),
            "screen": adb.shell_ok("wm size").strip().replace("Physical size: ", ""),
            "locale": f"{props.get('persist.sys.locale', '-')} / "
                      f"{props.get('persist.sys.timezone', '-')}",
        }, True))

    def _apply_device(self, data: Dict[str, Any]) -> None:
        if "error" in data:
            self.dot.itemconfigure(self.dot_id, fill=COL_ERR)
            self.conn_var.set(data["error"])
            for c in self.cards.values():
                c.set("-")
            return
        self.dot.itemconfigure(self.dot_id, fill=COL_OK)
        self.conn_var.set(f"{data['model']} connected")
        for k, c in self.cards.items():
            c.set(str(data.get(k, "-")))

    def run_doctor(self) -> None:
        self.start_job(["doctor"], "Probing device capabilities")

    # ------------------------------------------------------------------ #
    #  Capture tab
    # ------------------------------------------------------------------ #
    def _build_capture_tab(self) -> None:
        t = self.tab_capture
        row = ttk.Frame(t)
        row.pack(fill="x")
        ttk.Label(row, text="Save snapshots to").pack(side="left")
        self.out_dir = tk.StringVar(value=self.cfg.get("out_dir", str(HERE)))
        ttk.Entry(row, textvariable=self.out_dir).pack(side="left", fill="x",
                                                       expand=True, padx=8)
        ttk.Button(row, text="Browse", command=self._pick_out).pack(side="left")

        opts = ttk.LabelFrame(t, text="What to capture", padding=12)
        opts.pack(fill="x", pady=14)
        opts.columnconfigure(1, weight=1)

        ttk.Label(opts, text="APK storage").grid(row=0, column=0, sticky="w", pady=4)
        self.apks_mode = tk.StringVar(value=self.cfg.get("apks_mode", "sideloaded"))
        cb = ttk.Combobox(opts, textvariable=self.apks_mode, state="readonly", width=16,
                          values=["all", "sideloaded", "none"])
        cb.grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(opts, foreground=COL_DIM, wraplength=620, justify="left",
                  text="all = every APK, fully offline restore, very large.   "
                       "sideloaded = only apps not from Play Store (usually a fraction "
                       "of the size).   none = inventory only.").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))

        self.opt_skip_media = tk.BooleanVar(value=False)
        self.opt_android_data = tk.BooleanVar(value=False)
        self.opt_cloud = tk.BooleanVar(value=True)
        self.opt_nohash = tk.BooleanVar(value=False)
        for i, (var, label, hint) in enumerate([
            (self.opt_cloud, "Trigger a Google cloud backup first",
             "The only supported route to private app data. Strongly recommended."),
            (self.opt_skip_media, "Skip photos, videos and files",
             "Much faster, but your media is then not in the snapshot."),
            (self.opt_android_data, "Also try /sdcard/Android/data and obb",
             "Slow, and often refused on Android 11+."),
            (self.opt_nohash, "Skip SHA-256 hashing",
             "Faster to finish, weaker verification later."),
        ]):
            ttk.Checkbutton(opts, text=label, variable=var).grid(
                row=2 + i * 2, column=0, columnspan=3, sticky="w")
            ttk.Label(opts, text="       " + hint, foreground=COL_DIM).grid(
                row=3 + i * 2, column=0, columnspan=3, sticky="w", pady=(0, 4))

        adv = ttk.Frame(opts)
        adv.grid(row=12, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(adv, text="Home-screen pages").pack(side="left")
        self.home_screens = tk.IntVar(value=6)
        ttk.Spinbox(adv, from_=0, to=12, width=4,
                    textvariable=self.home_screens).pack(side="left", padx=(6, 18))
        ttk.Label(adv, text="Parallel workers").pack(side="left")
        self.workers = tk.IntVar(value=4)
        ttk.Spinbox(adv, from_=1, to=8, width=4,
                    textvariable=self.workers).pack(side="left", padx=6)

        go = ttk.Frame(t)
        go.pack(fill="x")
        self.btn_capture = ttk.Button(go, text="Start capture", style="Go.TButton",
                                      command=self.run_capture)
        self.btn_capture.pack(side="left", ipadx=10, ipady=3)
        ttk.Label(go, foreground=COL_DIM,
                  text="   Keep the phone unlocked and plugged in for the whole run."
                  ).pack(side="left")

    def _pick_out(self) -> None:
        p = filedialog.askdirectory(title="Where should snapshots be saved?",
                                    initialdir=self.out_dir.get() or str(HERE))
        if p:
            self.out_dir.set(p)
            self.refresh_snapshots()

    def run_capture(self) -> None:
        out = self.out_dir.get().strip()
        if not out:
            messagebox.showwarning(APP_NAME, "Choose a folder to save snapshots into.")
            return
        args = ["capture", "--out", out, "--yes",
                "--apks-mode", self.apks_mode.get(),
                "--home-screens", str(self.home_screens.get()),
                "--workers", str(self.workers.get())]
        if self.opt_skip_media.get():
            args.append("--skip-media")
        if self.opt_android_data.get():
            args.append("--include-android-data")
        if self.opt_cloud.get():
            args.append("--force-cloud-backup")
        if self.opt_nohash.get():
            args.append("--no-hash")
        self.cfg["apks_mode"] = self.apks_mode.get()
        self.start_job(args, "Capturing snapshot", on_done=lambda rc: self.refresh_snapshots())

    # ------------------------------------------------------------------ #
    #  Snapshots tab
    # ------------------------------------------------------------------ #
    def _build_snapshots_tab(self) -> None:
        t = self.tab_snaps
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        ttk.Button(bar, text="Refresh", command=self.refresh_snapshots).pack(side="left")
        ttk.Button(bar, text="Open folder", command=self._open_snapshot_folder
                   ).pack(side="left", padx=6)
        ttk.Button(bar, text="Verify integrity", command=self.run_verify
                   ).pack(side="left", padx=6)
        ttk.Button(bar, text="Archive...", command=self.run_archive).pack(side="left")

        cols = ("captured", "device", "android", "apps", "size")
        self.tree = ttk.Treeview(t, columns=cols, show="tree headings", height=12)
        self.tree.heading("#0", text="Snapshot")
        self.tree.column("#0", width=290, anchor="w")
        for c, label, w in [("captured", "Captured", 140), ("device", "Device", 150),
                            ("android", "Android", 80), ("apps", "Apps", 60),
                            ("size", "Size", 90)]:
            self.tree.heading(c, text=label)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(t, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=10)
        sb.pack(side="left", fill="y", pady=10)
        self.tree.bind("<<TreeviewSelect>>", self._on_snapshot_select)
        self._snap_paths: Dict[str, Path] = {}

    def refresh_snapshots(self) -> None:
        for i in self.tree.get_children():
            self.tree.delete(i)
        self._snap_paths.clear()
        root = Path(self.out_dir.get().strip() or HERE)
        if not root.is_dir():
            return
        found = sorted([p for p in root.glob("snapshot_*") if p.is_dir()], reverse=True)
        for p in found:
            man = p / "00_meta" / "manifest.json"
            captured = device = android = apps = size = "-"
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
                dev = m.get("device", {})
                captured = str(dev.get("captured_at", ""))[:16].replace("T", "  ")
                device = f"{dev.get('manufacturer', '')} {dev.get('model', '')}".strip()
                android = str(dev.get("android_release", "-"))
                apps = str(m.get("stats", {}).get("user apps", "-"))
                size = self._human(m.get("total_bytes", 0))
            except (OSError, ValueError):
                dev_json = p / "00_meta" / "device.json"
                if dev_json.is_file():
                    try:
                        d = json.loads(dev_json.read_text(encoding="utf-8"))
                        device = f"{d.get('manufacturer', '')} {d.get('model', '')}".strip()
                        captured = str(d.get("captured_at", ""))[:16].replace("T", "  ")
                    except (OSError, ValueError):
                        pass
                size = "(incomplete)"
            iid = self.tree.insert("", "end", text=p.name,
                                   values=(captured, device, android, apps, size))
            self._snap_paths[iid] = p
        if found:
            first = self.tree.get_children()[0]
            self.tree.selection_set(first)
            self.tree.focus(first)

    @staticmethod
    def _human(n: float) -> str:
        for u in ("B", "KB", "MB", "GB", "TB"):
            if abs(n) < 1024:
                return f"{n:.1f} {u}"
            n /= 1024
        return f"{n:.1f} PB"

    def _on_snapshot_select(self, _e=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        self.selected_snapshot = self._snap_paths.get(sel[0])
        if self.selected_snapshot:
            name = self.selected_snapshot.name
            self.lbl_compare_target.configure(text=name)
            self.lbl_restore_target.configure(text=name)
            self.load_checklist()

    def _require_snapshot(self) -> Optional[Path]:
        if not self.selected_snapshot:
            messagebox.showinfo(APP_NAME, "Pick a snapshot on the Snapshots tab first.")
            self.nb.select(self.tab_snaps)
            return None
        return self.selected_snapshot

    def _open_snapshot_folder(self) -> None:
        p = self._require_snapshot()
        if not p:
            return
        try:
            if os.name == "nt":
                os.startfile(str(p))  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(p)])
            else:
                subprocess.Popen(["xdg-open", str(p)])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not open the folder:\n{e}")

    def run_verify(self) -> None:
        p = self._require_snapshot()
        if p:
            self.start_job(["verify", "--snapshot", str(p)],
                           f"Verifying {p.name}")

    def run_archive(self) -> None:
        p = self._require_snapshot()
        if not p:
            return
        win = tk.Toplevel(self)
        win.title("Archive snapshot")
        win.transient(self)
        win.resizable(False, False)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text=p.name, font=("Segoe UI", 10, "bold")).pack(anchor="w")
        store = tk.BooleanVar(value=True)
        enc = tk.BooleanVar(value=True)
        rmplain = tk.BooleanVar(value=False)
        ttk.Checkbutton(frm, variable=store,
                        text="Store only, no compression (recommended - the contents are "
                             "already compressed)").pack(anchor="w", pady=(10, 2))
        ttk.Checkbutton(frm, variable=enc,
                        text="Encrypt (gpg / 7-Zip / OpenSSL - prompts in a console)"
                        ).pack(anchor="w", pady=2)
        ttk.Checkbutton(frm, variable=rmplain,
                        text="Delete the unencrypted archive afterwards"
                        ).pack(anchor="w", pady=2)
        ttk.Label(frm, foreground=COL_WARN, wraplength=460, justify="left",
                  text="Encryption asks for a passphrase in a terminal window, not here. "
                       "If nothing seems to happen, check for a console prompt."
                  ).pack(anchor="w", pady=(8, 0))

        def go():
            args = ["archive", "--snapshot", str(p)]
            if store.get():
                args += ["--compresslevel", "0"]
            if enc.get():
                args.append("--encrypt")
            if rmplain.get():
                args.append("--remove-plain")
            win.destroy()
            self.start_job(args, f"Archiving {p.name}")

        btns = ttk.Frame(frm)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="right")
        ttk.Button(btns, text="Create archive", style="Go.TButton",
                   command=go).pack(side="right", padx=6)

    # ------------------------------------------------------------------ #
    #  Compare tab
    # ------------------------------------------------------------------ #
    def _build_compare_tab(self) -> None:
        t = self.tab_compare
        head = ttk.Frame(t)
        head.pack(fill="x")
        ttk.Label(head, text="Comparing phone against:").pack(side="left")
        self.lbl_compare_target = ttk.Label(head, text="(no snapshot selected)",
                                            font=("Segoe UI", 9, "bold"))
        self.lbl_compare_target.pack(side="left", padx=6)

        opt = ttk.Frame(t)
        opt.pack(fill="x", pady=10)
        self.cmp_skip_media = tk.BooleanVar(value=False)
        self.cmp_deep = tk.BooleanVar(value=False)
        self.cmp_repush = tk.BooleanVar(value=False)
        self.cmp_dry = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="Skip media index (much faster)",
                        variable=self.cmp_skip_media).pack(side="left")
        ttk.Checkbutton(opt, text="Count SMS / calls / contacts",
                        variable=self.cmp_deep).pack(side="left", padx=14)

        btns = ttk.Frame(t)
        btns.pack(fill="x")
        ttk.Button(btns, text="Compare", style="Go.TButton",
                   command=self.run_compare).pack(side="left", ipadx=8)
        ttk.Separator(btns, orient="vertical").pack(side="left", fill="y", padx=14)
        ttk.Checkbutton(btns, text="Dry run", variable=self.cmp_dry).pack(side="left")
        ttk.Checkbutton(btns, text="Also re-push size mismatches",
                        variable=self.cmp_repush).pack(side="left", padx=10)
        ttk.Button(btns, text="Restore what's missing",
                   command=self.run_compare_fix).pack(side="left", padx=6)

        self.cmp_tree = ttk.Treeview(t, columns=("count",), show="tree headings", height=9)
        self.cmp_tree.heading("#0", text="Category")
        self.cmp_tree.column("#0", width=320)
        self.cmp_tree.heading("count", text="Missing / differing")
        self.cmp_tree.column("count", width=150, anchor="center")
        self.cmp_tree.tag_configure("good", foreground="#1a7f37")
        self.cmp_tree.tag_configure("bad", foreground="#a4262c")
        self.cmp_tree.pack(fill="both", expand=True, pady=12)
        ttk.Label(t, foreground=COL_DIM, wraplength=980, justify="left",
                  text="Compare is read-only. 'Restore what's missing' installs only the "
                       "absent apps, grants only the missing permissions, writes only the "
                       "settings that differ and pushes only the files that are gone - "
                       "then re-indexes the gallery. Nothing is deleted or downgraded."
                  ).pack(anchor="w")

    def run_compare(self) -> None:
        p = self._require_snapshot()
        if not p:
            return
        args = ["diff", "--snapshot", str(p)]
        if self.cmp_skip_media.get():
            args.append("--skip-media")
        if self.cmp_deep.get():
            args.append("--deep")
        self.start_job(args, f"Comparing phone against {p.name}",
                       on_done=lambda rc: self.load_diff_result())

    def run_compare_fix(self) -> None:
        p = self._require_snapshot()
        if not p:
            return
        if not self.cmp_dry.get():
            if not messagebox.askyesno(
                    APP_NAME,
                    "This will install apps, change settings and push files to the "
                    "connected phone.\n\nContinue?"):
                return
        args = ["diff", "--snapshot", str(p), "--fix", "--yes"]
        if self.cmp_skip_media.get():
            args.append("--skip-media")
        if self.cmp_repush.get():
            args.append("--repush-mismatched")
        if self.cmp_dry.get():
            args.append("--dry-run")
        self.start_job(args, "Restoring missing items",
                       on_done=lambda rc: self.load_diff_result())

    def load_diff_result(self) -> None:
        for i in self.cmp_tree.get_children():
            self.cmp_tree.delete(i)
        p = self.selected_snapshot
        if not p:
            return
        reports = sorted((p / "09_reports").glob("diff_*.json"), reverse=True)
        if not reports:
            return
        try:
            d = json.loads(reports[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        rows = [
            ("Apps missing", d.get("apps_missing", []), "package"),
            ("Apps at a different version", d.get("apps_version_differs", []), "package"),
            ("Permission grants missing", d.get("permissions_missing", []), "permission"),
            ("Settings differing", d.get("settings_differ", []), "key"),
            ("Battery exemptions missing", d.get("battery_whitelist_missing", []), None),
            ("Default-app roles differing", d.get("roles_differ", []), "role"),
            ("Keyboards not enabled", d.get("input_methods_missing", []), None),
            ("Media files missing", d.get("media_missing", []), "device_path"),
            ("Files with size mismatch", d.get("media_size_mismatch", []), "device_path"),
        ]
        for label, items, key in rows:
            n = len(items)
            parent = self.cmp_tree.insert(
                "", "end", text=label, values=(n,),
                tags=("good" if n == 0 else "bad",),
                open=False)
            for it in items[:150]:
                if key and isinstance(it, dict):
                    text = str(it.get(key, it))
                    if label.startswith("Apps missing"):
                        text += "   (offline reinstall)" if it.get("restorable") \
                            else "   -> needs Play Store"
                    if label.startswith("Settings"):
                        text = f"{it.get('namespace')}.{it.get('key')}: " \
                               f"{it.get('phone')} -> {it.get('snapshot')}"
                elif isinstance(it, dict):
                    text = json.dumps(it)
                else:
                    text = str(it)
                self.cmp_tree.insert(parent, "end", text="    " + text, values=("",))
            if len(items) > 150:
                self.cmp_tree.insert(parent, "end",
                                     text=f"    ... and {len(items) - 150} more",
                                     values=("",))
        self.log_line(f"[*] loaded {reports[0].name}", "info")

    # ------------------------------------------------------------------ #
    #  Restore tab
    # ------------------------------------------------------------------ #
    def _build_restore_tab(self) -> None:
        t = self.tab_restore
        head = ttk.Frame(t)
        head.pack(fill="x")
        ttk.Label(head, text="Restoring from:").pack(side="left")
        self.lbl_restore_target = ttk.Label(head, text="(no snapshot selected)",
                                            font=("Segoe UI", 9, "bold"))
        self.lbl_restore_target.pack(side="left", padx=6)

        warn = ttk.LabelFrame(t, text="Order matters", padding=12)
        warn.pack(fill="x", pady=12)
        ttk.Label(warn, justify="left", wraplength=980, foreground="#4a5464",
                  text="1. At the setup wizard, choose 'Restore from cloud backup' FIRST - "
                       "that is the only thing that brings private app data back.\n"
                       "2. Let Play Store finish its own reinstalls.\n"
                       "3. Then run the restore below.\n"
                       "4. Push media BEFORE opening WhatsApp for the first time, or its "
                       "'restore from local backup' offer never appears."
                  ).pack(anchor="w")

        box = ttk.LabelFrame(t, text="What to restore", padding=12)
        box.pack(fill="x")
        self.r_apps = tk.BooleanVar(value=True)
        self.r_settings = tk.BooleanVar(value=True)
        self.r_perms = tk.BooleanVar(value=True)
        self.r_battery = tk.BooleanVar(value=True)
        self.r_defaults = tk.BooleanVar(value=True)
        self.r_appops = tk.BooleanVar(value=False)
        self.r_media = tk.BooleanVar(value=False)
        self.r_reinstall = tk.BooleanVar(value=False)
        self.r_dry = tk.BooleanVar(value=True)
        items = [
            (self.r_apps, "Apps", "reinstall from the APKs in this snapshot"),
            (self.r_perms, "Runtime permissions", "re-grant what was granted before"),
            (self.r_settings, "Settings", "display, sound, gestures, night light..."),
            (self.r_battery, "Battery exemptions", "apps allowed to run unrestricted"),
            (self.r_defaults, "Default apps & keyboards", "browser, dialer, SMS, launcher"),
            (self.r_appops, "App-ops", "per-app special toggles; off by default"),
            (self.r_media, "Photos, videos and files", "slow - can be many GB"),
        ]
        for i, (var, label, hint) in enumerate(items):
            ttk.Checkbutton(box, text=label, variable=var).grid(
                row=i, column=0, sticky="w", pady=2)
            ttk.Label(box, text=hint, foreground=COL_DIM).grid(
                row=i, column=1, sticky="w", padx=14)

        mode = ttk.Frame(t)
        mode.pack(fill="x", pady=12)
        ttk.Label(mode, text="Settings mode").pack(side="left")
        self.settings_mode = tk.StringVar(value="safe")
        ttk.Radiobutton(mode, text="Safe (curated allowlist)", value="safe",
                        variable=self.settings_mode).pack(side="left", padx=8)
        ttk.Radiobutton(mode, text="All captured keys (risky)", value="all",
                        variable=self.settings_mode,
                        command=self._warn_settings_all).pack(side="left")

        go = ttk.Frame(t)
        go.pack(fill="x", pady=6)
        ttk.Checkbutton(go, text="Dry run - print commands, change nothing",
                        variable=self.r_dry).pack(side="left")
        ttk.Checkbutton(go, text="Reinstall apps already present",
                        variable=self.r_reinstall).pack(side="left", padx=14)
        ttk.Button(go, text="Run restore", style="Go.TButton",
                   command=self.run_restore).pack(side="right", ipadx=10, ipady=2)

    def _warn_settings_all(self) -> None:
        if self.settings_mode.get() == "all":
            messagebox.showwarning(
                APP_NAME,
                "'All captured keys' writes every secure and global setting back.\n\n"
                "Many of those encode provisioning state and device identity, and "
                "writing them onto a fresh install can leave the Settings app in a "
                "broken state.\n\nUse Safe unless you have a specific reason.")

    def run_restore(self) -> None:
        p = self._require_snapshot()
        if not p:
            return
        args = ["restore", "--snapshot", str(p), "--settings-mode",
                self.settings_mode.get(), "--yes"]
        for var, flag in [(self.r_apps, "--apps"), (self.r_settings, "--settings"),
                          (self.r_perms, "--perms"), (self.r_appops, "--appops"),
                          (self.r_battery, "--battery"), (self.r_defaults, "--defaults"),
                          (self.r_media, "--media")]:
            if var.get():
                args.append(flag)
        if self.r_reinstall.get():
            args.append("--reinstall-existing")
        if self.r_dry.get():
            args.append("--dry-run")
        elif not messagebox.askyesno(
                APP_NAME,
                "This will modify the connected phone: installing apps, writing "
                "settings and granting permissions.\n\nContinue?"):
            return
        self.start_job(args, f"Restoring from {p.name}")

    # ------------------------------------------------------------------ #
    #  Checklist tab
    # ------------------------------------------------------------------ #
    def _build_checklist_tab(self) -> None:
        t = self.tab_check
        bar = ttk.Frame(t)
        bar.pack(fill="x")
        self.check_progress = tk.StringVar(value="No snapshot selected")
        ttk.Label(bar, textvariable=self.check_progress,
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(bar, text="Reload", command=self.load_checklist).pack(side="right")
        self.check_bar = ttk.Progressbar(t, mode="determinate")
        self.check_bar.pack(fill="x", pady=(8, 10))
        self.check_scroll = ScrollFrame(t)
        self.check_scroll.pack(fill="both", expand=True)
        self._check_vars: List[Any] = []

    def load_checklist(self) -> None:
        self.check_scroll.clear()
        self._check_vars = []
        p = self.selected_snapshot
        if not p:
            self.check_progress.set("No snapshot selected")
            return
        md = p / "09_reports" / "PRE-WIPE-CHECKLIST.md"
        if not md.is_file():
            self.check_progress.set("This snapshot has no checklist")
            return
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            return
        state_path = p / "09_reports" / "checklist_state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}

        body = self.check_scroll.body
        body.columnconfigure(0, weight=1)
        row = 0

        def add_check(key: str, label: str, initial: bool,
                      severity: str = "", title: str = "") -> None:
            """ttk.Checkbutton cannot wrap text, so pair a bare box with a
            wrapping label and make the label clickable too."""
            nonlocal row
            var = tk.BooleanVar(value=initial)
            frame = ttk.Frame(body)
            frame.grid(row=row, column=0, sticky="ew", padx=8, pady=3)
            frame.columnconfigure(2, weight=1)
            ttk.Checkbutton(frame, variable=var,
                            command=self._checklist_changed).grid(row=0, column=0,
                                                                  sticky="nw")
            if severity:
                colour = {"CRITICAL": COL_ERR, "HIGH": COL_WARN}.get(severity, "#4a5464")
                ttk.Label(frame, text=severity, foreground=colour, width=9,
                          font=("Segoe UI", 8, "bold")).grid(row=0, column=1, sticky="nw")
            inner = ttk.Frame(frame)
            inner.grid(row=0, column=2, sticky="ew")
            if title:
                ttk.Label(inner, text=title,
                          font=("Segoe UI", 9, "bold")).pack(anchor="w")
            text_lbl = ttk.Label(inner, text=label, wraplength=840, justify="left")
            text_lbl.pack(anchor="w")
            text_lbl.bind("<Button-1>",
                          lambda _e, v=var: (v.set(not v.get()), self._checklist_changed()))
            self._check_vars.append((key, var))
            row += 1

        def add_heading(txt: str, size: int = 11) -> None:
            nonlocal row
            ttk.Label(body, text=txt, font=("Segoe UI", size, "bold")).grid(
                row=row, column=0, sticky="w", pady=(14, 4), padx=2)
            row += 1

        def add_note(txt: str, colour: str = "#4a5464") -> None:
            nonlocal row
            ttk.Label(body, text=txt, wraplength=900, justify="left",
                      foreground=colour).grid(row=row, column=0, sticky="w", padx=8)
            row += 1

        for raw in text.splitlines():
            line = raw.rstrip()
            if not line.strip() or line.strip() in ("---",):
                continue
            if line.startswith("# "):
                add_heading(line[2:].strip(), 14)
            elif line.startswith("## "):
                add_heading(line[3:].strip(), 11)
            elif re.match(r"^- \[[ xX]\] ", line):
                done_in_md = line[3].lower() == "x"
                label = re.sub(r"^- \[[ xX]\] ", "", line)
                label = re.sub(r"\*\*(.+?)\*\*", r"\1", label)
                label = re.sub(r"`(.+?)`", r"\1", label)
                key = label[:90]
                add_check(key, label, bool(state.get(key, done_in_md)))
            elif line.startswith("| **") and "|" in line[3:]:
                cells = [c.strip() for c in line.strip("|").split("|")]
                if len(cells) >= 3:
                    sev = cells[0].replace("*", "")
                    pkg = cells[1].replace("`", "")
                    key = f"app::{pkg}"
                    add_check(key, cells[2], bool(state.get(key, False)),
                              severity=sev, title=pkg)
            elif line.startswith("|") or line.startswith("_"):
                continue
            else:
                add_note(line.replace("**", "").replace("`", ""))

        self._state_path = state_path
        self._checklist_changed()

    def _checklist_changed(self) -> None:
        if not self._check_vars:
            self.check_progress.set("Nothing to tick")
            self.check_bar.configure(value=0)
            return
        done = sum(1 for _, v in self._check_vars if v.get())
        total = len(self._check_vars)
        self.check_progress.set(f"{done} of {total} done")
        self.check_bar.configure(maximum=total, value=done)
        try:
            self._state_path.write_text(
                json.dumps({k: v.get() for k, v in self._check_vars}, indent=2),
                encoding="utf-8")
        except (OSError, AttributeError):
            pass

    # ------------------------------------------------------------------ #
    #  Job plumbing
    # ------------------------------------------------------------------ #
    def start_job(self, cli_args: List[str], label: str,
                  on_done: Optional[Callable[[int], None]] = None) -> None:
        if self.job:
            messagebox.showinfo(APP_NAME, "Something is already running. "
                                          "Wait for it, or press Stop.")
            return
        if not self.cli_path:
            messagebox.showerror(APP_NAME, "phone_snapshot.py was not found.")
            return
        argv = [sys.executable, "-u", str(self.cli_path)]
        adb = self.adb_path.get().strip()
        if adb:
            argv += ["--adb", adb]
        argv += cli_args

        self._on_done = on_done
        self.status.set(label + "...")
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(14)
        self.btn_stop.configure(state="normal")
        self._set_actions_enabled(False)
        self.log_line("", None)
        self.log_line(f"$ {' '.join(cli_args)}", "dim")
        self.job = Job(argv, self.q)
        self.job.start()

    def stop_job(self) -> None:
        if self.job:
            self.job.stop()
            self.log_line("[!] stopped by user", "warn")

    def _set_actions_enabled(self, on: bool) -> None:
        state = "normal" if on else "disabled"
        for w in (getattr(self, "btn_capture", None),):
            if w:
                w.configure(state=state)

    def _drain_queue(self) -> None:
        try:
            while True:
                kind, payload, newline = self.q.get_nowait()
                if kind == "line":
                    self._handle_line(payload, newline)
                elif kind == "device":
                    self._apply_device(payload)
                elif kind == "exit":
                    self._job_finished(payload)
        except queue.Empty:
            pass
        self.after(60, self._drain_queue)

    def _handle_line(self, text: str, newline: bool) -> None:
        clean = ANSI_RE.sub("", text)
        if not clean.strip() and not newline:
            return
        tag = None
        s = clean.strip()
        if s.startswith("[+]"):
            tag = "ok"
        elif s.startswith("[!]"):
            tag = "warn"
        elif s.startswith("[x]"):
            tag = "err"
        elif s.startswith("[*]"):
            tag = "info"
        elif s.startswith("==") and s.endswith("=="):
            tag = "head"
        elif s.startswith("$"):
            tag = "dim"

        if not newline:
            # progress counter: replace the previous line instead of appending
            m = PROGRESS_RE.search(clean)
            if m:
                cur, total = int(m.group(1)), int(m.group(2))
                if total:
                    self.progress.stop()
                    self.progress.configure(mode="determinate",
                                            maximum=total, value=cur)
                self.status.set(clean.strip())
            self._replace_last_line(clean, tag)
            self._last_line_was_cr = True
            return

        if self._last_line_was_cr:
            self._replace_last_line(clean, tag)
            self._last_line_was_cr = False
        else:
            self.log_line(clean, tag)
        if s.startswith("=="):
            self.status.set(s.strip("= ").strip())

    def log_line(self, text: str, tag: Optional[str]) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n", (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _replace_last_line(self, text: str, tag: Optional[str]) -> None:
        self.log.configure(state="normal")
        self.log.delete("end-2l linestart", "end-1c")
        self.log.insert("end", "\n" + text, (tag,) if tag else ())
        self.log.see("end")
        self.log.configure(state="disabled")

    def _log_clear(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _save_log(self) -> None:
        p = filedialog.asksaveasfilename(defaultextension=".txt",
                                         initialfile="snapy-log.txt")
        if p:
            try:
                Path(p).write_text(self.log.get("1.0", "end"), encoding="utf-8")
                self.log_line(f"[+] log saved to {p}", "ok")
            except OSError as e:
                messagebox.showerror(APP_NAME, str(e))

    def _job_finished(self, rc: int) -> None:
        self.job = None
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self.btn_stop.configure(state="disabled")
        self._set_actions_enabled(True)
        self._last_line_was_cr = False
        if rc == 0:
            self.status.set("Done")
            self.log_line("[+] finished", "ok")
        elif rc == 1:
            self.status.set("Finished with differences")
            self.log_line("[!] finished with differences (exit 1)", "warn")
        else:
            self.status.set(f"Failed (exit {rc})")
            self.log_line(f"[x] failed with exit code {rc}", "err")
        cb, self._on_done = getattr(self, "_on_done", None), None
        if cb:
            try:
                cb(rc)
            except Exception as e:  # keep the UI alive whatever the callback does
                self.log_line(f"[x] post-step error: {e}", "err")
        self.refresh_device()

    def _on_close(self) -> None:
        if self.job and not messagebox.askyesno(
                APP_NAME, "A job is still running. Stop it and quit?"):
            return
        if self.job:
            self.job.stop()
        self._save_config()
        self.destroy()


# --------------------------------------------------------------------------- #

def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    cli_override = None
    if "--cli" in args:
        i = args.index("--cli")
        if i + 1 < len(args):
            cli_override = args[i + 1]
    cli = find_cli(cli_override)
    app = App(cli)
    geo = app.cfg.get("geometry")
    if geo:
        try:
            app.geometry(geo)
        except tk.TclError:
            pass
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
