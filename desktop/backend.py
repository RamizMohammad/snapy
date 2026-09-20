# -*- coding: utf-8 -*-
"""Bridge between the Qt UI and the phone_snapshot CLI.

Two paths, deliberately:

* Quick, structured reads (device identity, snapshot listings) import the CLI
  module and call into it on a worker thread. No subprocess, no parsing.
* Long operations run as a QProcess so they stream, stay cancellable, and keep
  the UI responsive. Their stdout is parsed into signals the pages bind to.

The CLI stays the single source of truth. The UI never talks to adb directly
for anything it could get from the CLI instead.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (QObject, QProcess, QRunnable, Qt, QThreadPool,
                            Signal, Slot)

HERE = Path(__file__).resolve().parent
CLI_NAMES = ["phone_snapshot.py", "snapy.py", "phone-snapshot.py"]

FROZEN = getattr(sys, "frozen", False)
# Where the app was installed (next to the .exe when frozen).
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else HERE
# Where PyInstaller unpacked bundled data.
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))


def _config_path() -> Path:
    """Settings must be writable. Program Files is not, so an installed build
    keeps them in the user's profile instead."""
    if not FROZEN:
        return HERE / "snapy_desktop.json"
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".config")
    folder = base / "Snapy"
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError:
        return APP_DIR / "snapy_desktop.json"
    return folder / "snapy_desktop.json"


CONFIG_PATH = _config_path()


def bundled_adb() -> str:
    """adb shipped alongside the app, if the installer included it."""
    for base in (APP_DIR, BUNDLE_DIR):
        for rel in ("platform-tools/adb.exe", "platform-tools/adb",
                    "vendor/platform-tools/adb.exe"):
            p = base / rel
            if p.is_file():
                return str(p)
    return ""

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PROGRESS_RE = re.compile(r"(\d[\d,]*)\s*/\s*(\d[\d,]*)")

# CLI section headings, in the order capture emits them, collapsed into the ten
# steps the UI shows. First match wins.
CAPTURE_STEPS: List[tuple] = [
    ("Device identity", ("Device identity",)),
    ("System configuration", ("System configuration",)),
    ("App inventory", ("Application inventory", "Pulling APKs")),
    ("App permissions", ("Per-app state",)),
    ("Personal data", ("Network, accounts", "Raw system service", "SMS, call log")),
    ("Pulling media", ("User files on internal storage",)),
    ("App external data", ("App external data",)),
    ("Home screen layout", ("Home-screen layout",)),
    ("Cloud backup", ("Google cloud backup",)),
    ("Verify and report", ("Writing reports", "Building manifest", "Done")),
]


# --------------------------------------------------------------------------- #
#  CLI discovery / import
# --------------------------------------------------------------------------- #

def find_cli(explicit: Optional[str] = None) -> Optional[Path]:
    """Locate the CLI beside this file, one level up, or at an explicit path."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p.resolve()
    for folder in (APP_DIR, BUNDLE_DIR, HERE, HERE.parent):
        for name in CLI_NAMES:
            p = folder / name
            if p.is_file():
                return p.resolve()
    for folder in (APP_DIR, BUNDLE_DIR, HERE, HERE.parent):
        for p in sorted(folder.glob("*.py")):
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:3000]
            except OSError:
                continue
            if "phone-snapshot" in head and "def cmd_capture" in head:
                return p.resolve()
    return None


def import_cli(path: Path):
    try:
        spec = importlib.util.spec_from_file_location("snapy_cli", path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Settings
# --------------------------------------------------------------------------- #

class Settings:
    DEFAULTS = {
        "theme": "dark",
        "out_dir": str(Path.home() / "Snapy"),
        "adb_path": "",
        "apks_mode": "sideloaded",
        "cloud_backup": True,
        "include_media": True,
        "android_data": False,
        "hash": True,
        "home_screens": 6,
        "workers": 4,
    }

    def __init__(self):
        self.data = dict(self.DEFAULTS)
        try:
            self.data.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def __getitem__(self, k):
        value = self.data.get(k, self.DEFAULTS.get(k))
        if k == "adb_path" and not value:
            return bundled_adb()
        return value

    def __setitem__(self, k, v):
        self.data[k] = v

    def save(self):
        try:
            CONFIG_PATH.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        except OSError:
            pass


# --------------------------------------------------------------------------- #
#  Worker for quick structured reads
# --------------------------------------------------------------------------- #

class _Task(QRunnable):
    def __init__(self, fn, done_signal):
        super().__init__()
        self.fn = fn
        self.done = done_signal

    @Slot()
    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as e:  # never let a probe kill the app
            self.done.emit({"error": str(e)})


# --------------------------------------------------------------------------- #
#  Backend
# --------------------------------------------------------------------------- #

class Backend(QObject):
    # device / data
    device_ready = Signal(dict)
    snapshots_ready = Signal(list)
    diff_ready = Signal(dict)
    screen_ready = Signal(object)   # PNG bytes, or None

    # job lifecycle
    job_started = Signal(str)          # label
    job_finished = Signal(int, str)    # exit code, label
    log_line = Signal(str, str)        # text, tag (ok|warn|err|info|head|dim)
    phase_changed = Signal(str)        # CLI section heading
    progress = Signal(int, int, str)   # current, total, detail ("" = indeterminate)
    status = Signal(str, str)          # text, tone

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.cli_path = find_cli()
        self.cli = import_cli(self.cli_path) if self.cli_path else None
        self.proc: Optional[QProcess] = None
        self.current_label = ""
        self._buf = ""
        self._pool = QThreadPool.globalInstance()

    # -- availability ---------------------------------------------------- #
    @property
    def ready(self) -> bool:
        return self.cli_path is not None

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    # ------------------------------------------------------------------ #
    #  Quick reads
    # ------------------------------------------------------------------ #
    def probe_device(self) -> None:
        self._pool.start(_Task(self._probe_device_sync, self.device_ready))

    def _probe_device_sync(self) -> Dict[str, Any]:
        if not self.cli:
            return {"error": "CLI not found"}
        adb = self.cli.Adb(self.settings["adb_path"] or None, None)
        usable = [d for d in adb.devices() if d.get("state") == "device"]
        if not usable:
            devs = adb.devices()
            if devs:
                return {"error": f"device {devs[0].get('state', 'not ready')}"}
            return {"error": "no device connected"}
        adb.serial = usable[0]["serial"]

        props = self.cli.parse_getprop(adb.shell_ok("getprop"))
        apps = [l for l in adb.shell_ok("pm list packages -3").splitlines()
                if l.startswith("package:")]

        free = used = pct = "-"
        for line in adb.shell_ok("df -h /sdcard 2>/dev/null").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                used, free, pct = parts[2], parts[3], parts[4]
                break

        caps = self._probe_capabilities(adb)
        return {
            "serial": adb.serial,
            "manufacturer": props.get("ro.product.manufacturer", ""),
            "model": props.get("ro.product.model", "?"),
            "android": props.get("ro.build.version.release", "?"),
            "sdk": props.get("ro.build.version.sdk", "?"),
            "patch": props.get("ro.build.version.security_patch", "-"),
            "fingerprint": props.get("ro.build.fingerprint", ""),
            "apps": len(apps),
            "storage_used": used,
            "storage_free": free,
            "storage_pct": pct,
            "capabilities": caps,
        }

    @staticmethod
    def _probe_capabilities(adb) -> List[tuple]:
        """Return [(label, state, tone)] for the capability matrix."""
        checks = [
            ("SMS provider", "content query --uri content://sms --projection _id"),
            ("Call log", "content query --uri content://call_log/calls --projection _id"),
            ("Contacts", "content query --uri content://com.android.contacts/data/phones --projection _id"),
            ("Saved Wi-Fi list", "cmd wifi list-networks"),
            ("App data (/sdcard/Android/data)", "ls /sdcard/Android/data"),
            ("Cloud backup", "bmgr enabled"),
            ("Screen capture", "screencap -p /dev/null"),
        ]
        out = []
        for label, cmd in checks:
            rc, text, _ = adb.shell(cmd, timeout=45)
            good = (rc == 0 and text.strip()
                    and "Permission Denial" not in text
                    and "denied" not in text.lower())
            if label == "Cloud backup":
                out.append((label, "available" if "enabled" in text.lower()
                            else "disabled", "ok" if "enabled" in text.lower() else "warn"))
            elif label == "App data (/sdcard/Android/data)":
                out.append((label, "allowed" if good else "blocked",
                            "ok" if good else "warn"))
            else:
                out.append((label, "allowed" if good else "blocked",
                            "ok" if good else "warn"))
        # Not probeable — a hard OS limit, stated rather than tested.
        out.insert(5, ("Wi-Fi passwords", "needs root", "muted"))
        return out

    # ------------------------------------------------------------------ #
    #  Snapshot library
    # ------------------------------------------------------------------ #
    def grab_screen(self) -> None:
        """Pull a live screenshot off the phone for the device illustration."""
        self._pool.start(_Task(self._grab_screen_sync, self.screen_ready))

    def _grab_screen_sync(self):
        if not self.cli:
            return None
        try:
            adb = self.cli.Adb(self.settings["adb_path"] or None, None)
            usable = [d for d in adb.devices() if d.get("state") == "device"]
            if not usable:
                return None
            adb.serial = usable[0]["serial"]
            rc, png = adb.exec_out("screencap -p", timeout=30)
            # Guard the magic bytes: a locked or refusing device returns an
            # error string on stdout, which would otherwise be drawn as a frame.
            if rc == 0 and png[:4] == b"\x89PNG":
                return bytes(png)
        except Exception:
            pass
        return None

    def load_snapshots(self) -> None:
        self._pool.start(_Task(self._load_snapshots_sync, self.snapshots_ready))

    def _load_snapshots_sync(self) -> List[Dict[str, Any]]:
        root = Path(self.settings["out_dir"])
        if not root.is_dir():
            return []
        rows = []
        for p in sorted(root.glob("snapshot_*"), reverse=True):
            if not p.is_dir():
                continue
            rec: Dict[str, Any] = {"path": p, "name": p.name, "integrity": "unknown"}
            man = p / "00_meta" / "manifest.json"
            try:
                m = json.loads(man.read_text(encoding="utf-8"))
                dev = m.get("device", {})
                rec.update({
                    "captured": str(dev.get("captured_at", ""))[:16].replace("T", " "),
                    "device": f"{dev.get('manufacturer', '')} "
                              f"{dev.get('model', '')}".strip(),
                    "android": str(dev.get("android_release", "-")),
                    "apps": m.get("stats", {}).get("user apps", "-"),
                    "bytes": m.get("total_bytes", 0),
                    "files": m.get("file_count", 0),
                    "build": dev.get("build_id", "-"),
                    "stats": m.get("stats", {}),
                    "errors": len(m.get("errors", [])),
                })
                rec["integrity"] = "verified" if not m.get("errors") else "degraded"
            except (OSError, ValueError):
                dj = p / "00_meta" / "device.json"
                rec["integrity"] = "incomplete"
                rec.setdefault("bytes", 0)
                try:
                    d = json.loads(dj.read_text(encoding="utf-8"))
                    rec["device"] = f"{d.get('manufacturer', '')} {d.get('model', '')}".strip()
                    rec["captured"] = str(d.get("captured_at", ""))[:16].replace("T", " ")
                except (OSError, ValueError):
                    pass
            rows.append(rec)
        return rows

    def load_latest_diff(self, snapshot: Path) -> None:
        def work():
            reports = sorted((snapshot / "09_reports").glob("diff_*.json"), reverse=True)
            if not reports:
                return {}
            try:
                d = json.loads(reports[0].read_text(encoding="utf-8"))
                d["_file"] = reports[0].name
                return d
            except (OSError, ValueError):
                return {}
        self._pool.start(_Task(work, self.diff_ready))

    # ------------------------------------------------------------------ #
    #  Long jobs
    # ------------------------------------------------------------------ #
    def run(self, args: List[str], label: str) -> bool:
        if self.busy:
            return False
        if not self.cli_path:
            self.log_line.emit("[x] phone_snapshot.py not found", "err")
            return False

        argv = ["-u", str(self.cli_path)]
        if self.settings["adb_path"]:
            argv += ["--adb", self.settings["adb_path"]]
        argv += args

        self.current_label = label
        self._buf = ""
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)

        self.job_started.emit(label)
        self.log_line.emit("$ " + " ".join(args), "dim")
        self.proc.start(sys.executable, argv)
        return True

    def stop(self) -> None:
        if self.busy and self.proc:
            self.log_line.emit("[!] stopping...", "warn")
            self.proc.terminate()
            if not self.proc.waitForFinished(4000):
                self.proc.kill()

    # -- stream parsing --------------------------------------------------- #
    def _on_output(self) -> None:
        if not self.proc:
            return
        chunk = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buf += chunk
        # Split on both, so \r progress counters surface immediately.
        while True:
            idx = min([i for i in (self._buf.find("\n"), self._buf.find("\r")) if i >= 0],
                      default=-1)
            if idx < 0:
                break
            piece, sep, self._buf = (self._buf[:idx], self._buf[idx],
                                     self._buf[idx + 1:])
            self._emit_line(piece, sep == "\n")

    def _emit_line(self, raw: str, newline: bool) -> None:
        text = ANSI_RE.sub("", raw).rstrip()
        stripped = text.strip()
        if not stripped:
            if newline:
                self.log_line.emit("", "")
            return

        tag = ""
        if stripped.startswith("[+]"):
            tag = "ok"
        elif stripped.startswith("[!]"):
            tag = "warn"
        elif stripped.startswith("[x]"):
            tag = "err"
        elif stripped.startswith("[*]"):
            tag = "info"
        elif stripped.startswith("==") and stripped.endswith("=="):
            tag = "head"
            heading = stripped.strip("= ").strip()
            self.phase_changed.emit(heading)
            self.status.emit(heading, "info")
        elif stripped.startswith("$"):
            tag = "dim"

        if not newline:
            m = PROGRESS_RE.search(stripped)
            if m:
                cur = int(m.group(1).replace(",", ""))
                total = int(m.group(2).replace(",", ""))
                self.progress.emit(cur, total, stripped)
            return  # progress counters do not belong in the log

        self.log_line.emit(text, tag)

    def _on_error(self, err) -> None:
        self.log_line.emit(f"[x] process error: {err}", "err")

    def _on_finished(self, code: int, _status) -> None:
        if self._buf.strip():
            self._emit_line(self._buf, True)
            self._buf = ""
        label, self.current_label = self.current_label, ""
        self.proc = None
        if code == 0:
            self.status.emit("Done", "ok")
        elif code == 1:
            self.status.emit("Finished with differences", "warn")
        else:
            self.status.emit(f"Failed (exit {code})", "bad")
        self.job_finished.emit(code, label)

    # ------------------------------------------------------------------ #
    #  Command builders — one place that knows the CLI's flags
    # ------------------------------------------------------------------ #
    def capture_args(self) -> List[str]:
        s = self.settings
        args = ["capture", "--out", s["out_dir"], "--yes",
                "--apks-mode", s["apks_mode"],
                "--home-screens", str(s["home_screens"]),
                "--workers", str(s["workers"])]
        if not s["include_media"]:
            args.append("--skip-media")
        if s["android_data"]:
            args.append("--include-android-data")
        if s["cloud_backup"]:
            args.append("--force-cloud-backup")
        if not s["hash"]:
            args.append("--no-hash")
        return args

    @staticmethod
    def diff_args(snapshot: Path, skip_media: bool, deep: bool,
                  fix: bool = False, dry: bool = True,
                  repush: bool = False) -> List[str]:
        args = ["diff", "--snapshot", str(snapshot)]
        if skip_media:
            args.append("--skip-media")
        if deep:
            args.append("--deep")
        if fix:
            args += ["--fix", "--yes"]
            if repush:
                args.append("--repush-mismatched")
            if dry:
                args.append("--dry-run")
        return args

    @staticmethod
    def restore_args(snapshot: Path, parts: Dict[str, bool], mode: str,
                     dry: bool, reinstall: bool) -> List[str]:
        args = ["restore", "--snapshot", str(snapshot),
                "--settings-mode", mode, "--yes"]
        for key, flag in [("apps", "--apps"), ("settings", "--settings"),
                          ("perms", "--perms"), ("appops", "--appops"),
                          ("battery", "--battery"), ("defaults", "--defaults"),
                          ("media", "--media")]:
            if parts.get(key):
                args.append(flag)
        if reinstall:
            args.append("--reinstall-existing")
        if dry:
            args.append("--dry-run")
        return args


def step_index_for(heading: str) -> int:
    """Map a CLI section heading onto a capture step index, or -1."""
    for i, (_title, needles) in enumerate(CAPTURE_STEPS):
        for n in needles:
            if n.lower() in heading.lower():
                return i
    return -1
