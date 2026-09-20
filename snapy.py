#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
phone-snapshot 1.0
==================

Capture a restorable snapshot of a STOCK, NON-ROOTED Android phone over ADB,
then rebuild the phone from that snapshot after the service centre reflashes it.

What this honestly can and cannot do
------------------------------------
CAN (no root required):
  * Every APK you installed, including split/config APKs  -> reinstallable offline
  * Full app inventory: version, installer, install dates, enabled/disabled state
  * Every runtime permission grant, per app                -> re-granted automatically
  * App-ops (per-app special toggles)                      -> re-applied on request
  * Battery-optimisation exemptions                        -> re-applied
  * Default apps / role holders (browser, dialer, SMS, launcher, assistant)
  * Every system / secure / global setting the OS exposes  -> the "phone config"
  * Enabled IMEs, accessibility services, notification listeners
  * Saved Wi-Fi SSIDs + security type (NOT passwords - those need root)
  * Accounts registered on the device (type + name, no credentials)
  * All user files on internal storage (DCIM, Download, WhatsApp, ...)
  * Best-effort /sdcard/Android/data and /sdcard/Android/obb
  * SMS, call log, calendar via content providers (when the shell user is allowed)
  * Home-screen screenshots, page by page, so the launcher can be rebuilt by eye
  * Trigger a fresh Google cloud backup (bmgr) - the only supported route to
    private app data on a stock device

CANNOT (hard OS limits, not a tool limitation):
  * Read /data/data/<pkg> private app data. That is the app's login sessions,
    databases and preferences. Non-root Android does not expose it to anybody,
    and `adb backup` was neutered in Android 12+.
  * Read saved Wi-Fi passwords.
  * Read anything while the phone is locked.

So the strategy is: capture everything capturable, force a cloud backup for the
rest, and print a pre-wipe checklist of the handful of apps whose data only they
themselves can export (authenticators, WhatsApp, Signal, banking/UPI).

Requirements: Python 3.8+, stdlib only, and `adb` on PATH (or auto-detected).

Usage
-----
    python phone_snapshot.py doctor
    python phone_snapshot.py capture --out D:\\phone-backup
    python phone_snapshot.py verify   --snapshot D:\\phone-backup\\snapshot_<...>
    python phone_snapshot.py archive  --snapshot <dir> --encrypt
    python phone_snapshot.py upload   --archive <file> --dest scp://user@host:/backups/
    python phone_snapshot.py restore  --snapshot <dir> --apps --settings --perms
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as _dt
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "1.0"
SCHEMA_VERSION = 1

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- #
#  Console
# --------------------------------------------------------------------------- #

class C:
    if IS_WINDOWS and not os.environ.get("WT_SESSION") and not os.environ.get("ANSICON"):
        # Enable VT processing on modern Windows 10+; harmless if it fails.
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            pass
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[36m"


def _emit(prefix: str, msg: str) -> None:
    sys.stdout.write(f"{prefix} {msg}\n")
    sys.stdout.flush()


def info(msg: str) -> None:
    _emit(f"{C.BLUE}[*]{C.RESET}", msg)


def ok(msg: str) -> None:
    _emit(f"{C.GREEN}[+]{C.RESET}", msg)


def warn(msg: str) -> None:
    _emit(f"{C.YELLOW}[!]{C.RESET}", msg)


def err(msg: str) -> None:
    _emit(f"{C.RED}[x]{C.RESET}", msg)


def step(msg: str) -> None:
    sys.stdout.write(f"\n{C.BOLD}== {msg} =={C.RESET}\n")
    sys.stdout.flush()


def human(nbytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024.0:
            return f"{nbytes:.1f} {unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f} PB"


# --------------------------------------------------------------------------- #
#  Configuration tables
# --------------------------------------------------------------------------- #

# Settings that are safe to write back onto a freshly flashed phone. Blanket
# restoring every key is a genuinely bad idea: many secure/global keys encode
# provisioning state, device identifiers or one-shot flags, and writing them
# back can leave Settings in a broken state. Everything is still *captured*;
# only this allowlist is *restored* unless you pass --settings-mode all.
SAFE_SETTINGS: Dict[str, List[str]] = {
    "system": [
        "screen_brightness", "screen_brightness_mode", "screen_off_timeout",
        "font_scale", "accelerometer_rotation", "user_rotation",
        "haptic_feedback_enabled", "sound_effects_enabled", "dtmf_tone",
        "vibrate_when_ringing", "vibrate_on", "time_12_24", "date_format",
        "ringtone", "notification_sound", "alarm_alert",
        "peak_refresh_rate", "min_refresh_rate", "apply_ramping_ringer",
        "master_mono", "show_touches", "pointer_speed", "screen_auto_brightness_adj",
    ],
    "secure": [
        "navigation_mode", "adaptive_sleep", "screensaver_enabled",
        "screensaver_components", "screensaver_activate_on_dock",
        "screensaver_activate_on_sleep", "doze_enabled", "doze_always_on",
        "doze_pulse_on_pick_up", "doze_tap_gesture", "wake_gesture_enabled",
        "night_display_activated", "night_display_auto_mode",
        "night_display_color_temperature", "night_display_custom_start_time",
        "night_display_custom_end_time", "display_white_balance_enabled",
        "long_press_timeout", "tap_duration_threshold",
        "high_text_contrast_enabled", "accessibility_display_magnification_scale",
        "accessibility_captioning_enabled", "accessibility_captioning_font_scale",
        "accessibility_display_daltonizer", "accessibility_display_daltonizer_enabled",
        "camera_double_tap_power_gesture_disabled", "camera_gesture_disabled",
        "system_navigation_keys_enabled", "incall_power_button_behavior",
        "charging_sounds_enabled", "show_ime_with_hard_keyboard",
        "lock_screen_show_notifications", "lock_screen_allow_private_notifications",
    ],
    "global": [
        "window_animation_scale", "transition_animation_scale",
        "animator_duration_scale", "auto_time", "auto_time_zone",
        "private_dns_mode", "private_dns_specifier", "device_name",
        "heads_up_notifications_enabled", "notification_bubbles",
        "wifi_scan_always_enabled", "mobile_data_always_active",
        "stay_on_while_plugged_in", "development_settings_enabled",
        "charging_sounds_enabled", "wifi_wakeup_enabled", "bluetooth_on",
    ],
}

ROLES = [
    "android.app.role.ASSISTANT",
    "android.app.role.BROWSER",
    "android.app.role.DIALER",
    "android.app.role.SMS",
    "android.app.role.HOME",
    "android.app.role.CALL_REDIRECTION",
    "android.app.role.CALL_SCREENING",
    "android.app.role.EMERGENCY",
    "android.app.role.SYSTEM_GALLERY",
    "android.app.role.NOTES",
    "android.app.role.WALLET",
]

MEDIA_DIRS = [
    "DCIM", "Pictures", "Movies", "Music", "Download", "Documents",
    "Podcasts", "Ringtones", "Alarms", "Notifications", "Audiobooks",
    "Recordings", "Screenshots", "bluetooth", "Bluetooth",
    "WhatsApp", "Telegram", "Signal", "Snapseed", "MIUI", "Android/media",
]

DUMPSYS_SECTIONS = [
    "battery", "batterystats --charged", "power", "display", "wifi",
    "bluetooth_manager", "connectivity", "notification --noredact",
    "deviceidle", "alarm", "usagestats", "shortcut", "location",
    "user", "account", "media.audio_policy", "input_method", "print",
]

CONTENT_PROVIDERS = {
    "sms": "content://sms",
    "mms": "content://mms",
    "call_log": "content://call_log/calls",
    "contacts_phones": "content://com.android.contacts/data/phones",
    "calendar_events": "content://com.android.calendar/events",
    "calendar_calendars": "content://com.android.calendar/calendars",
}

# Apps whose data only the user can export, with the exact action to take
# BEFORE handing the phone over. This is the part that actually saves people.
MANUAL_EXPORT_RULES: List[Tuple[str, str, str]] = [
    ("com.google.android.apps.authenticator2", "CRITICAL",
     "Google Authenticator: open it -> menu -> Transfer accounts -> Export accounts. "
     "Screenshot/photograph the QR codes with ANOTHER device. Losing this locks you "
     "out of every account it guards."),
    ("com.authy.authy", "HIGH",
     "Authy: confirm multi-device is ON and you know your backup password before the wipe."),
    ("com.azure.authenticator", "CRITICAL",
     "Microsoft Authenticator: Settings -> Backup -> turn on cloud backup, then verify it completed."),
    ("com.duosecurity.duomobile", "CRITICAL",
     "Duo Mobile: use Duo Restore (Settings -> Duo Restore) or you must re-enrol with your IT admin."),
    ("org.thoughtcrime.securesms", "CRITICAL",
     "Signal: Settings -> Chats -> Chat backups -> create a backup, SAVE THE 30-DIGIT PASSPHRASE, "
     "and copy the backup file off the phone. Signal has no cloud backup on Android."),
    ("com.whatsapp", "HIGH",
     "WhatsApp: Settings -> Chats -> Chat backup -> Back up now (Google Drive), AND copy "
     "/sdcard/WhatsApp (or Android/media/com.whatsapp) off the phone. Note your 64-digit "
     "encryption key if end-to-end encrypted backup is on."),
    ("com.whatsapp.w4b", "HIGH", "WhatsApp Business: same as WhatsApp - back up and copy the folder off."),
    ("org.telegram.messenger", "LOW", "Telegram: chats are server-side; you only need your login + 2FA password."),
    ("com.google.android.apps.nbu.paisa.user", "HIGH",
     "Google Pay / UPI: device binding is tied to this install. Note your UPI PIN and the SIM slot; "
     "you will re-register after the wipe."),
    ("net.one97.paytm", "HIGH", "Paytm: UPI device binding resets; you will re-verify by SMS."),
    ("com.phonepe.app", "HIGH", "PhonePe: UPI device binding resets; you will re-verify by SMS."),
    ("in.org.npci.upiapp", "HIGH", "BHIM: UPI device binding resets; re-register after the wipe."),
    ("com.google.android.keep", "LOW", "Keep: cloud-synced, nothing to do."),
    ("com.samsung.android.app.notes", "HIGH", "Samsung Notes: export notes to PDF/Samsung Cloud first."),
    ("com.nothing.smartcenter", "LOW", "Nothing device app: nothing to export."),
    ("com.valvesoftware.android.steam.community", "MEDIUM",
     "Steam Guard: move the authenticator to another device or you will need a recovery code."),
]

MANUAL_EXPORT_KEYWORDS: List[Tuple[str, str, str]] = [
    ("bank", "HIGH", "Banking app - device binding and mPIN reset on reinstall. Have your card/net-banking credentials ready."),
    ("upi", "HIGH", "UPI app - device binding resets; the SIM must be in the phone to re-register."),
    ("authenticator", "CRITICAL", "Authenticator app - export or transfer your seeds BEFORE the wipe."),
    ("otp", "HIGH", "OTP/2FA app - check whether it can export its seeds."),
    ("wallet", "MEDIUM", "Wallet app - check for an export/backup option."),
    ("password", "CRITICAL", "Password manager - make sure your vault is synced and you know the master password."),
    ("vpn", "LOW", "VPN app - note the server/profile config; certificates may not survive."),
]

# Packages that exist only as a side effect of something else and can never be
# sideloaded back. Capturing or reinstalling them is pure noise.
SYNTHETIC_PACKAGE_PATTERNS = [
    (r"^org\.chromium\.webapk\.",
     "Chrome WebAPK - minted on-device by Play Services when you add a website to the "
     "home screen, and signed with a key unique to that install. It cannot be sideloaded "
     "and does not need to be: re-add the site from Chrome and a fresh one is generated. "
     "No data lives in it."),
    (r"^com\.google\.android\.trichromelibrary",
     "Chrome's shared library package - installed automatically alongside Chrome."),
    (r"\.overlay$",
     "Runtime resource overlay - part of the ROM, reinstalled with it."),
]

# adb install failure codes, in plain English, with what to actually do.
INSTALL_FAILURE_HELP: Dict[str, Tuple[str, str]] = {
    "INSTALL_FAILED_TEST_ONLY": (
        "APK was built by Android Studio's Run button, which stamps testOnly=true.",
        "Retried automatically with 'adb install -t'. If it still fails, rebuild a release "
        "APK from source - you own this one."),
    "INSTALL_FAILED_DEPRECATED_SDK_VERSION": (
        "App targets an Android version too old for this OS to accept (targetSdk < 23 on "
        "Android 14+).",
        "Retried automatically with --bypass-low-target-sdk-block. The real fix is to bump "
        "targetSdkVersion and rebuild."),
    "INSTALL_FAILED_NO_MATCHING_ABIS": (
        "The APK's native libraries do not match this phone's CPU.",
        "The snapshot only has the split for the old device's ABI. Reinstall from Play "
        "Store, or rebuild a universal APK."),
    "INSTALL_PARSE_FAILED_NO_CERTIFICATES": (
        "The pulled APK has no valid signature block.",
        "Usually means the APK was stored compressed or was only partially readable. "
        "Reinstall from Play Store or rebuild from source."),
    "INSTALL_FAILED_INVALID_APK": (
        "The APK is structurally incomplete.",
        "Most often a split-APK app where not every split was pulled. Reinstall from Play Store."),
    "INSTALL_FAILED_MISSING_SPLIT": (
        "The app needs split APKs that are not in the snapshot.",
        "Reinstall from Play Store - it will fetch the right splits for this device."),
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE": (
        "A package with this name is already installed, signed with a different key.",
        "Uninstall the existing copy first (this deletes its data), then reinstall."),
    "INSTALL_FAILED_VERSION_DOWNGRADE": (
        "A newer version is already installed.",
        "Already installed and newer - nothing to do."),
    "INSTALL_FAILED_INSUFFICIENT_STORAGE": (
        "Not enough free space on the phone.",
        "Free up space and rerun restore; apps already installed are skipped."),
    "INSTALL_FAILED_VERIFICATION_FAILURE": (
        "Play Protect blocked the install.",
        "Settings -> Google -> Play Protect -> turn off scanning temporarily, or install "
        "this app from Play Store."),
    "INSTALL_FAILED_USER_RESTRICTED": (
        "The phone refused the install over USB.",
        "On the phone, enable Developer options -> 'Install via USB' (Xiaomi/Oppo/Vivo/Nothing "
        "builds require this)."),
    "INSTALL_FAILED_ALREADY_EXISTS": (
        "Already installed.", "Nothing to do."),
}


def synthetic_reason(pkg: str) -> Optional[str]:
    """If this package is auto-generated and not worth handling, say why."""
    for pattern, why in SYNTHETIC_PACKAGE_PATTERNS:
        if re.search(pattern, pkg):
            return why
    return None


def install_error_code(text: str) -> Optional[str]:
    """Pull the INSTALL_* / Failure code out of adb's noisy output."""
    m = re.search(r"\[?(INSTALL_[A-Z_]+|INSTALL_PARSE_[A-Z_]+)", text)
    if m:
        return m.group(1)
    m = re.search(r"Failure\s*\[([^\]]+)\]", text)
    return m.group(1).split()[0] if m else None


# --------------------------------------------------------------------------- #
#  ADB
# --------------------------------------------------------------------------- #

class AdbError(RuntimeError):
    pass


class Adb:
    """Thin, forgiving wrapper around the adb binary."""

    def __init__(self, binary: Optional[str] = None, serial: Optional[str] = None):
        self.binary = binary or self._discover()
        if not self.binary:
            raise AdbError(
                "adb not found. Install Android platform-tools and put it on PATH, "
                "or pass --adb <path-to-adb.exe>.\n"
                "  Download: https://developer.android.com/tools/releases/platform-tools"
            )
        self.serial = serial

    # -- discovery ---------------------------------------------------------- #
    @staticmethod
    def _discover() -> Optional[str]:
        found = shutil.which("adb")
        if found:
            return found
        home = Path.home()
        candidates = [
            home / "AppData/Local/Android/Sdk/platform-tools/adb.exe",
            home / "Android/Sdk/platform-tools/adb",
            home / "Library/Android/sdk/platform-tools/adb",
            Path("C:/platform-tools/adb.exe"),
            Path("C:/Android/platform-tools/adb.exe"),
            Path("/usr/lib/android-sdk/platform-tools/adb"),
            Path("/opt/homebrew/bin/adb"),
        ]
        for c in candidates:
            if c.is_file():
                return str(c)
        return None

    # -- low level ---------------------------------------------------------- #
    def _argv(self, args: Sequence[str]) -> List[str]:
        argv = [self.binary]
        if self.serial:
            argv += ["-s", self.serial]
        argv += list(args)
        return argv

    # The adb server dies now and then under a long run of back-to-back commands.
    # When it does, every later command fails for a reason that has nothing to do
    # with the command, so restart the daemon and try again rather than logging
    # hundreds of bogus failures.
    _DAEMON_DEAD = (
        "cannot connect to daemon",
        "daemon still not running",
        "daemon not running",
        "protocol fault",
        "device offline",
        "closed",
    )

    def raw(self, args: Sequence[str], *, timeout: int = 180,
            binary: bool = False, _attempt: int = 0) -> Tuple[int, Any, str]:
        """Run adb. Returns (returncode, stdout, stderr). Never raises on non-zero."""
        try:
            p = subprocess.run(
                self._argv(args),
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return 124, (b"" if binary else ""), f"timed out after {timeout}s"
        except FileNotFoundError as e:
            raise AdbError(str(e))
        out = p.stdout if binary else p.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        errtxt = p.stderr.decode("utf-8", "replace").replace("\r\n", "\n")

        if p.returncode != 0 and _attempt < 2:
            low = errtxt.lower()
            if any(s in low for s in self._DAEMON_DEAD):
                warn(f"adb daemon dropped; restarting and retrying "
                     f"(attempt {_attempt + 2}/3)")
                subprocess.run([self.binary, "kill-server"], capture_output=True, timeout=30)
                time.sleep(1.5)
                subprocess.run([self.binary, "start-server"], capture_output=True, timeout=60)
                time.sleep(1.5)
                return self.raw(args, timeout=timeout, binary=binary, _attempt=_attempt + 1)
        return p.returncode, out, errtxt

    def shell(self, cmd: str, *, timeout: int = 180) -> Tuple[int, str, str]:
        return self.raw(["shell", cmd], timeout=timeout)

    def shell_ok(self, cmd: str, *, timeout: int = 180) -> str:
        """Run a shell command, return stdout, '' on any failure."""
        rc, out, _ = self.shell(cmd, timeout=timeout)
        return out if rc == 0 else ""

    def exec_out(self, cmd: str, *, timeout: int = 180) -> Tuple[int, bytes]:
        rc, out, _ = self.raw(["exec-out", cmd], timeout=timeout, binary=True)
        return rc, out

    # -- device state ------------------------------------------------------- #
    def devices(self) -> List[Dict[str, str]]:
        rc, out, _ = self.raw(["devices", "-l"], timeout=30)
        if rc != 0:
            return []
        devs = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            d = {"serial": parts[0], "state": parts[1] if len(parts) > 1 else "?"}
            for extra in parts[2:]:
                if ":" in extra:
                    k, v = extra.split(":", 1)
                    d[k] = v
            devs.append(d)
        return devs

    def require_device(self) -> str:
        devs = self.devices()
        usable = [d for d in devs if d["state"] == "device"]
        if not devs:
            raise AdbError(
                "No device detected.\n"
                "  1. Enable Developer options (tap Build number 7x in Settings > About phone)\n"
                "  2. Enable USB debugging\n"
                "  3. Plug in via USB and set the USB mode to 'File transfer'\n"
                "  4. Accept the 'Allow USB debugging?' prompt on the phone"
            )
        if not usable:
            states = ", ".join(f"{d['serial']}={d['state']}" for d in devs)
            raise AdbError(
                f"Device present but not usable ({states}).\n"
                "  'unauthorized' -> accept the USB debugging prompt on the phone screen.\n"
                "  'offline'      -> unplug, replug, and run: adb kill-server && adb start-server"
            )
        if len(usable) > 1 and not self.serial:
            lst = "\n".join(f"    {d['serial']}  {d.get('model', '?')}" for d in usable)
            raise AdbError(f"Multiple devices connected. Pick one with --serial:\n{lst}")
        self.serial = self.serial or usable[0]["serial"]
        return self.serial

    def screen_unlocked(self) -> Optional[bool]:
        out = self.shell_ok("dumpsys window 2>/dev/null | grep -E 'mDreamingLockscreen|mShowingLockscreen'")
        if not out:
            return None
        m = re.search(r"m(?:Dreaming|Showing)Lockscreen=(true|false)", out)
        return (m.group(1) == "false") if m else None


# --------------------------------------------------------------------------- #
#  Parsing helpers
# --------------------------------------------------------------------------- #

def parse_getprop(text: str) -> Dict[str, str]:
    props: Dict[str, str] = {}
    for m in re.finditer(r"^\[([^\]]+)\]:\s*\[([^\]]*)\]$", text, re.M):
        props[m.group(1)] = m.group(2)
    return props


def parse_settings(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if k:
                out[k] = v
    return out


def parse_package_dump(blob: str) -> Dict[str, Dict[str, Any]]:
    """Parse the concatenated `dumpsys package <pkg>` output produced by the
    device-side loop in capture_apps()."""
    packages: Dict[str, Dict[str, Any]] = {}
    chunks = blob.split("===PKG===")
    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        if not lines:
            continue
        pkg = lines[0].strip()
        if not pkg:
            continue
        body = "\n".join(lines[1:])
        rec: Dict[str, Any] = {
            "package": pkg,
            "versionName": _first(body, r"versionName=(\S+)"),
            "versionCode": _first(body, r"versionCode=(\d+)"),
            "minSdk": _first(body, r"minSdk=(\d+)"),
            "targetSdk": _first(body, r"targetSdk=(\d+)"),
            "installer": _first(body, r"installerPackageName=(\S+)"),
            "firstInstallTime": _first(body, r"firstInstallTime=(.+)"),
            "lastUpdateTime": _first(body, r"lastUpdateTime=(.+)"),
            "codePath": _first(body, r"codePath=(\S+)"),
            "dataDir": _first(body, r"dataDir=(\S+)"),
            "primaryCpuAbi": _first(body, r"primaryCpuAbi=(\S+)"),
            "uid": _first(body, r"userId=(\d+)"),
        }

        # enabled state from the "User 0:" line
        m = re.search(r"User 0:.*?enabled=(\d+)", body, re.S)
        rec["enabledState"] = int(m.group(1)) if m else 0
        m = re.search(r"User 0:.*?installed=(true|false)", body, re.S)
        rec["installed"] = (m.group(1) == "true") if m else True
        m = re.search(r"User 0:.*?stopped=(true|false)", body, re.S)
        rec["stopped"] = (m.group(1) == "true") if m else False

        rec["requestedPermissions"] = _perm_block(body, "requested permissions:")
        rec["installPermissions"] = _perm_grants(body, "install permissions:")
        rec["runtimePermissions"] = _perm_grants(body, "runtime permissions:")
        packages[pkg] = rec
    return packages


def _first(text: str, pattern: str) -> Optional[str]:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else None


def _section_lines(body: str, header: str) -> List[str]:
    """Return the indented lines following `header` until indentation drops."""
    idx = body.find(header)
    if idx == -1:
        return []
    lines = body[idx:].splitlines()[1:]
    if not lines:
        return []
    base_indent = len(lines[0]) - len(lines[0].lstrip())
    collected = []
    for ln in lines:
        if not ln.strip():
            continue
        indent = len(ln) - len(ln.lstrip())
        if indent < base_indent:
            break
        collected.append(ln.strip())
    return collected


def _perm_block(body: str, header: str) -> List[str]:
    out = []
    for ln in _section_lines(body, header):
        name = ln.split(":")[0].strip()
        if name.startswith("android.permission") or "." in name:
            out.append(name)
    return sorted(set(out))


def _perm_grants(body: str, header: str) -> Dict[str, bool]:
    out: Dict[str, bool] = {}
    for ln in _section_lines(body, header):
        m = re.match(r"([\w.]+):\s*granted=(true|false)", ln)
        if m:
            out[m.group(1)] = m.group(2) == "true"
    return out


def parse_pm_paths(blob: str) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    current = None
    for line in blob.splitlines():
        line = line.strip()
        if line.startswith("===PATH==="):
            current = line[len("===PATH==="):].strip()
            result.setdefault(current, [])
        elif line.startswith("package:") and current:
            result[current].append(line[len("package:"):].strip())
    return {k: v for k, v in result.items() if v}


def parse_appops(blob: str) -> Dict[str, Dict[str, str]]:
    """Parse `dumpsys appops` into {package: {OP: mode}} for explicit modes."""
    result: Dict[str, Dict[str, str]] = {}
    current_pkg: Optional[str] = None
    current_op: Optional[str] = None
    for raw in blob.splitlines():
        line = raw.strip()
        m = re.match(r"^Package\s+([\w.]+):$", line)
        if m:
            current_pkg = m.group(1)
            current_op = None
            result.setdefault(current_pkg, {})
            continue
        if current_pkg is None:
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]+)\s*\(([a-z]+)", line)
        if m:
            result[current_pkg][m.group(1)] = m.group(2)
            current_op = m.group(1)
            continue
        m = re.match(r"^([A-Z][A-Z0-9_]+):\s*mode=([a-z]+)", line)
        if m:
            result[current_pkg][m.group(1)] = m.group(2)
            current_op = m.group(1)
            continue
        if current_op:
            m = re.match(r"^mode=([a-z]+)$", line)
            if m:
                result[current_pkg][current_op] = m.group(1)
    return {p: ops for p, ops in result.items() if ops}


def parse_deviceidle_whitelist(blob: str) -> List[str]:
    pkgs = []
    for line in blob.splitlines():
        line = line.strip()
        m = re.match(r"^(?:system|user)[,-]\s*([\w.]+)[,-]", line)
        if m:
            pkgs.append(m.group(1))
        elif re.match(r"^[\w.]+$", line) and "." in line:
            pkgs.append(line)
    return sorted(set(pkgs))


# --------------------------------------------------------------------------- #
#  Snapshot container
# --------------------------------------------------------------------------- #

class Snapshot:
    def __init__(self, root: Path):
        self.root = root
        self.meta = root / "00_meta"
        self.config = root / "01_device_config"
        self.apps = root / "02_apps"
        self.apk = root / "02_apps" / "apk"
        self.state = root / "03_app_state"
        self.data = root / "04_personal_data"
        self.media = root / "05_media"
        self.appdata = root / "06_android_data"
        self.screens = root / "07_screens"
        self.raw = root / "08_raw_dumps"
        self.reports = root / "09_reports"
        for d in (self.meta, self.config, self.apps, self.apk, self.state,
                  self.data, self.media, self.appdata, self.screens,
                  self.raw, self.reports):
            d.mkdir(parents=True, exist_ok=True)
        self.errors: List[Dict[str, str]] = []
        self.notes: List[str] = []

    def write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", errors="replace")

    def write_json(self, path: Path, obj: Any) -> None:
        self.write_text(path, json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=True))

    def fail(self, what: str, why: str) -> None:
        self.errors.append({"step": what, "reason": why.strip()[:500]})
        warn(f"{what}: {why.strip()[:200]}")


# --------------------------------------------------------------------------- #
#  Capture steps
# --------------------------------------------------------------------------- #

def capture_identity(adb: Adb, snap: Snapshot) -> Dict[str, Any]:
    step("Device identity and hardware")
    props_raw = adb.shell_ok("getprop")
    snap.write_text(snap.raw / "getprop.txt", props_raw)
    props = parse_getprop(props_raw)
    snap.write_json(snap.config / "properties.json", props)

    size = adb.shell_ok("wm size").strip()
    density = adb.shell_ok("wm density").strip()
    df = adb.shell_ok("df -h /data /sdcard 2>/dev/null")
    snap.write_text(snap.raw / "storage_df.txt", df)

    ident = {
        "captured_at": _dt.datetime.now().astimezone().isoformat(),
        "tool_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "serial": adb.serial,
        "manufacturer": props.get("ro.product.manufacturer"),
        "brand": props.get("ro.product.brand"),
        "model": props.get("ro.product.model"),
        "device": props.get("ro.product.device"),
        "name": props.get("ro.product.name"),
        "android_release": props.get("ro.build.version.release"),
        "sdk": props.get("ro.build.version.sdk"),
        "security_patch": props.get("ro.build.version.security_patch"),
        "build_fingerprint": props.get("ro.build.fingerprint"),
        "build_id": props.get("ro.build.id"),
        "bootloader": props.get("ro.bootloader"),
        "baseband": props.get("gsm.version.baseband"),
        "locale": props.get("persist.sys.locale") or props.get("ro.product.locale"),
        "timezone": props.get("persist.sys.timezone"),
        "device_name": props.get("net.hostname"),
        "abilist": props.get("ro.product.cpu.abilist"),
        "screen": {"size": size, "density": density},
    }
    snap.write_json(snap.meta / "device.json", ident)
    ok(f"{ident.get('manufacturer') or '?'} {ident.get('model') or '?'} "
       f"- Android {ident.get('android_release')} (SDK {ident.get('sdk')}), "
       f"patch {ident.get('security_patch')}")
    return ident


def capture_settings(adb: Adb, snap: Snapshot) -> Dict[str, Dict[str, str]]:
    step("System configuration (settings providers)")
    all_settings: Dict[str, Dict[str, str]] = {}
    for ns in ("system", "secure", "global"):
        raw = adb.shell_ok(f"settings list {ns}")
        if not raw:
            snap.fail(f"settings:{ns}", "settings list returned nothing")
            all_settings[ns] = {}
            continue
        snap.write_text(snap.raw / f"settings_{ns}.txt", raw)
        parsed = parse_settings(raw)
        all_settings[ns] = parsed
        ok(f"{ns}: {len(parsed)} keys")
    snap.write_json(snap.config / "settings.json", all_settings)

    restorable = {
        ns: {k: v for k, v in all_settings.get(ns, {}).items() if k in SAFE_SETTINGS[ns]}
        for ns in SAFE_SETTINGS
    }
    snap.write_json(snap.config / "settings_restorable.json", restorable)
    ok(f"{sum(len(v) for v in restorable.values())} keys marked safe to restore automatically")
    return all_settings


def capture_apps(adb: Adb, snap: Snapshot, pull_apks: bool = True,
                 workers: int = 4, apks_mode: str = "all") -> Dict[str, Any]:
    step("Application inventory")

    user_pkgs = _pkg_list(adb, "-3")
    sys_pkgs = _pkg_list(adb, "-s")
    disabled = set(_pkg_list(adb, "-d"))
    ok(f"{len(user_pkgs)} user-installed apps, {len(sys_pkgs)} system apps, "
       f"{len(disabled)} disabled")

    # One round trip for every package's full dump.
    info("Dumping package metadata and permissions (one pass)...")
    loop = ("for p in $(pm list packages -3 | cut -d: -f2); do "
            "echo \"===PKG===$p\"; dumpsys package \"$p\"; done")
    rc, blob, e = adb.shell(loop, timeout=600)
    if rc != 0 or "===PKG===" not in blob:
        snap.fail("package-dump-bulk", e or "bulk dump failed; falling back to per-package")
        blob = _per_package_dump(adb, user_pkgs, workers)
    snap.write_text(snap.raw / "dumpsys_package_user_apps.txt", blob)
    meta = parse_package_dump(blob)
    ok(f"parsed metadata for {len(meta)} apps")

    # APK paths (base + splits)
    info("Resolving APK paths (including split APKs)...")
    ploop = ("for p in $(pm list packages -3 | cut -d: -f2); do "
             "echo \"===PATH===$p\"; pm path \"$p\"; done")
    rc, pblob, e = adb.shell(ploop, timeout=300)
    paths = parse_pm_paths(pblob) if rc == 0 else {}
    if not paths:
        snap.fail("pm-path-bulk", e or "could not resolve APK paths in bulk")
        for p in user_pkgs:
            out = adb.shell_ok(f"pm path {p}", timeout=30)
            got = [l[len("package:"):].strip() for l in out.splitlines()
                   if l.startswith("package:")]
            if got:
                paths[p] = got

    apps: List[Dict[str, Any]] = []
    for pkg in sorted(set(user_pkgs) | set(meta)):
        rec = dict(meta.get(pkg, {"package": pkg}))
        rec["type"] = "user"
        rec["disabled"] = pkg in disabled
        rec["apkPaths"] = paths.get(pkg, [])
        rec["splitCount"] = max(0, len(rec["apkPaths"]) - 1)
        rec["playUrl"] = f"https://play.google.com/store/apps/details?id={pkg}"
        apps.append(rec)

    system_records = [{"package": p, "type": "system", "disabled": p in disabled}
                      for p in sorted(sys_pkgs)]

    snap.write_json(snap.apps / "apps_user.json", apps)
    snap.write_json(snap.apps / "apps_system.json", system_records)
    snap.write_text(
        snap.apps / "play_store_links.txt",
        "\n".join(a["playUrl"] for a in apps) + "\n",
    )

    pulled = {"count": 0, "bytes": 0, "failed": []}
    if pull_apks and apks_mode != "none":
        # Auto-generated packages (Chrome WebAPKs and friends) can never be
        # sideloaded back, so pulling them wastes time and pollutes the restore.
        real = [a for a in apps if not synthetic_reason(a["package"])]
        skipped = len(apps) - len(real)
        if skipped:
            info(f"{skipped} auto-generated packages excluded from the APK pull "
                 f"(they regenerate by themselves)")
        if apks_mode == "sideloaded":
            play = [a for a in real if a.get("installer") == "com.android.vending"]
            real = [a for a in real if a.get("installer") != "com.android.vending"]
            info(f"{len(play)} Play Store apps excluded from the APK pull "
                 f"(--apks-mode sideloaded); they reinstall themselves from Play. "
                 f"Keeping {len(real)} sideloaded / self-built apps.")
            snap.write_text(
                snap.apps / "play_reinstall_list.txt",
                "# These apps were NOT stored as APKs. Reinstall from Play Store.\n"
                + "\n".join(f"{a['package']}  {a.get('versionName') or ''}" for a in play) + "\n")
        pulled = _pull_apks(adb, snap, real, workers)
        pulled["mode"] = apks_mode

    return {"user": apps, "system": system_records, "apk_pull": pulled,
            "disabled": sorted(disabled)}


def _pkg_list(adb: Adb, flag: str) -> List[str]:
    out = adb.shell_ok(f"pm list packages {flag}", timeout=90)
    return sorted({l.split(":", 1)[1].strip() for l in out.splitlines()
                   if l.startswith("package:") and ":" in l})


def _per_package_dump(adb: Adb, pkgs: List[str], workers: int) -> str:
    parts: List[str] = []

    def one(p: str) -> str:
        return f"===PKG==={p}\n" + adb.shell_ok(f"dumpsys package {p}", timeout=60)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(one, pkgs):
            parts.append(res)
    return "\n".join(parts)


def _pull_apks(adb: Adb, snap: Snapshot, apps: List[Dict[str, Any]],
               workers: int) -> Dict[str, Any]:
    step("Pulling APKs")
    total = sum(len(a.get("apkPaths") or []) for a in apps)
    if not total:
        warn("no APK paths resolved; skipping")
        return {"count": 0, "bytes": 0, "failed": []}
    info(f"{total} APK files across {len(apps)} apps")

    done = {"n": 0}
    failed: List[Dict[str, str]] = []

    def pull_one(app: Dict[str, Any]) -> Tuple[str, bool, str]:
        pkg = app["package"]
        dest = snap.apk / pkg
        dest.mkdir(parents=True, exist_ok=True)
        for remote in app.get("apkPaths") or []:
            name = remote.rsplit("/", 1)[-1] or "base.apk"
            rc, out, e = adb.raw(["pull", "-a", remote, str(dest / name)], timeout=300)
            if rc != 0:
                return pkg, False, (e or out or "pull failed").strip()
        return pkg, True, ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for pkg, success, reason in ex.map(pull_one, apps):
            done["n"] += 1
            if not success:
                failed.append({"package": pkg, "reason": reason[:200]})
            if done["n"] % 10 == 0 or done["n"] == len(apps):
                sys.stdout.write(f"\r    {done['n']}/{len(apps)} apps")
                sys.stdout.flush()
    sys.stdout.write("\n")

    nbytes = sum(f.stat().st_size for f in snap.apk.rglob("*.apk"))
    count = len(list(snap.apk.rglob("*.apk")))
    ok(f"pulled {count} APK files ({human(nbytes)})")
    if failed:
        warn(f"{len(failed)} apps could not be pulled (usually protected or system-linked)")
        snap.write_json(snap.apk / "_failed.json", failed)
    return {"count": count, "bytes": nbytes, "failed": failed}


def capture_app_state(adb: Adb, snap: Snapshot, apps: List[Dict[str, Any]]) -> Dict[str, Any]:
    step("Per-app state: permissions, app-ops, battery, defaults")

    perms = {
        a["package"]: {
            "runtime": a.get("runtimePermissions") or {},
            "install": a.get("installPermissions") or {},
        }
        for a in apps
    }
    granted = sum(1 for a in perms.values() for v in a["runtime"].values() if v)
    snap.write_json(snap.state / "permissions.json", perms)
    ok(f"{granted} runtime permission grants captured")

    appops_raw = adb.shell_ok("dumpsys appops", timeout=180)
    snap.write_text(snap.raw / "dumpsys_appops.txt", appops_raw)
    appops = parse_appops(appops_raw)
    snap.write_json(snap.state / "appops.json", appops)
    ok(f"app-ops recorded for {len(appops)} packages")

    idle_raw = adb.shell_ok("dumpsys deviceidle whitelist", timeout=60)
    snap.write_text(snap.raw / "deviceidle_whitelist.txt", idle_raw)
    whitelist = parse_deviceidle_whitelist(idle_raw)
    snap.write_json(snap.state / "battery_whitelist.json", whitelist)
    ok(f"{len(whitelist)} apps exempt from battery optimisation")

    roles: Dict[str, List[str]] = {}
    for role in ROLES:
        out = adb.shell_ok(f"cmd role get-role-holders --user 0 {role}", timeout=30).strip()
        if not out:
            out = adb.shell_ok(f"cmd role get-role-holders {role}", timeout=30).strip()
        holders = [x for x in re.split(r"[\s,]+", out) if x and "." in x]
        if holders:
            roles[role] = holders
    home = adb.shell_ok("cmd package get-home-activities", timeout=30)
    snap.write_text(snap.raw / "home_activities.txt", home)
    snap.write_json(snap.state / "default_apps.json",
                    {"roles": roles, "home_activities_raw": home})
    ok(f"{len(roles)} default-app roles captured")

    imes = {
        "all": [l.strip() for l in adb.shell_ok("ime list -s -a").splitlines() if l.strip()],
        "enabled": [l.strip() for l in adb.shell_ok("ime list -s").splitlines() if l.strip()],
    }
    snap.write_json(snap.state / "input_methods.json", imes)

    accessibility = {
        "enabled_services": adb.shell_ok("settings get secure enabled_accessibility_services").strip(),
        "accessibility_enabled": adb.shell_ok("settings get secure accessibility_enabled").strip(),
        "notification_listeners": adb.shell_ok("settings get secure enabled_notification_listeners").strip(),
        "notification_policy_access": adb.shell_ok("settings get secure enabled_notification_policy_access_packages").strip(),
    }
    snap.write_json(snap.state / "accessibility_and_listeners.json", accessibility)

    notif_raw = adb.shell_ok("dumpsys notification --noredact", timeout=180)
    snap.write_text(snap.raw / "dumpsys_notification.txt", notif_raw)

    return {"permissions": perms, "appops": appops, "battery_whitelist": whitelist,
            "roles": roles, "imes": imes, "accessibility": accessibility}


def capture_network_and_accounts(adb: Adb, snap: Snapshot) -> Dict[str, Any]:
    step("Network, accounts, Bluetooth")

    wifi_list = adb.shell_ok("cmd wifi list-networks", timeout=60)
    snap.write_text(snap.config / "wifi_saved_networks.txt", wifi_list or "(unavailable)")
    ssids = [l for l in wifi_list.splitlines()[1:] if l.strip()] if wifi_list else []
    if ssids:
        ok(f"{len(ssids)} saved Wi-Fi networks (SSID + security only; passwords need root)")
    else:
        warn("could not list saved Wi-Fi networks on this build")

    acc_raw = adb.shell_ok("dumpsys account", timeout=90)
    snap.write_text(snap.raw / "dumpsys_account.txt", acc_raw)
    accounts = sorted(set(re.findall(r"Account\s*\{name=([^,]+),\s*type=([^}]+)\}", acc_raw)))
    snap.write_json(snap.config / "accounts.json",
                    [{"name": n.strip(), "type": t.strip()} for n, t in accounts])
    ok(f"{len(accounts)} accounts registered on the device")

    bt = adb.shell_ok("dumpsys bluetooth_manager", timeout=90)
    snap.write_text(snap.raw / "dumpsys_bluetooth.txt", bt)

    return {"wifi_count": len(ssids), "accounts": len(accounts)}


def capture_dumpsys(adb: Adb, snap: Snapshot) -> None:
    step("Raw system service dumps")
    got = 0
    for section in DUMPSYS_SECTIONS:
        safe = re.sub(r"[^\w]+", "_", section).strip("_")
        out = adb.shell_ok(f"dumpsys {section}", timeout=180)
        if out.strip():
            snap.write_text(snap.raw / f"dumpsys_{safe}.txt", out)
            got += 1
    ok(f"{got}/{len(DUMPSYS_SECTIONS)} service dumps saved")


def capture_providers(adb: Adb, snap: Snapshot) -> Dict[str, Any]:
    step("SMS, call log, calendar (content providers)")
    result: Dict[str, Any] = {}
    for name, uri in CONTENT_PROVIDERS.items():
        rc, out, e = adb.shell(f"content query --uri {uri}", timeout=300)
        rows = [l for l in out.splitlines() if l.startswith("Row:")]
        if rc == 0 and rows:
            snap.write_text(snap.data / f"{name}.txt", out)
            result[name] = len(rows)
            ok(f"{name}: {len(rows)} rows")
        else:
            reason = (e or out or "no rows / permission denied").strip().splitlines()
            result[name] = 0
            warn(f"{name}: not available ({reason[0][:90] if reason else 'empty'})")
    snap.write_json(snap.data / "_provider_counts.json", result)
    if not result.get("sms"):
        snap.notes.append(
            "SMS could not be read over ADB on this build. Install 'SMS Backup & Restore' "
            "on the phone and export to an XML file before the wipe."
        )
    return result


def capture_media(adb: Adb, snap: Snapshot, dirs: Iterable[str],
                  skip: bool = False) -> Dict[str, Any]:
    step("User files on internal storage")
    if skip:
        warn("skipped (--skip-media)")
        return {"skipped": True}

    # Trailing `; true` matters: adb propagates the exit code of the LAST command,
    # and a missing directory would otherwise make the whole probe look like a failure.
    probe = "; ".join(
        f'[ -d "/sdcard/{d}" ] && du -sk "/sdcard/{d}" 2>/dev/null' for d in dirs
    ) + "; true"
    sizes_raw = adb.shell_ok(probe, timeout=300)
    sizes: Dict[str, int] = {}
    for line in sizes_raw.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            sizes[parts[1].strip()] = int(parts[0]) * 1024
    # -L follows the symlink: /sdcard points at /storage/emulated/0, and without
    # -L du measures the link itself and cheerfully reports 4 KB.
    total_raw = adb.shell_ok("du -skL /sdcard 2>/dev/null | tail -1", timeout=900)
    total = 0
    if total_raw.split():
        try:
            total = int(total_raw.split()[0]) * 1024
        except ValueError:
            total = 0
    if total:
        info(f"internal storage in use: ~{human(total)}")
    for path, sz in sorted(sizes.items(), key=lambda kv: -kv[1]):
        info(f"  {path}: {human(sz)}")

    pulled: Dict[str, Any] = {"dirs": {}, "bytes": 0, "failed": []}
    for d in dirs:
        remote = f"/sdcard/{d}"
        if remote not in sizes:
            continue
        local = snap.media / d.replace("/", os.sep)
        local.parent.mkdir(parents=True, exist_ok=True)
        info(f"pulling {remote} ({human(sizes[remote])})...")
        rc, out, e = adb.raw(["pull", "-a", remote, str(local)], timeout=7200)
        if rc != 0:
            pulled["failed"].append({"path": remote, "reason": (e or out).strip()[:200]})
            warn(f"  failed: {(e or out).strip()[:120]}")
        else:
            got = sum(f.stat().st_size for f in local.rglob("*") if f.is_file())
            pulled["dirs"][d] = got
            pulled["bytes"] += got
            ok(f"  {human(got)}")
    snap.write_json(snap.media / "_pull_report.json", pulled)
    ok(f"user files pulled: {human(pulled['bytes'])}")
    return pulled


def capture_android_data(adb: Adb, snap: Snapshot, enabled: bool) -> Dict[str, Any]:
    step("App external data (/sdcard/Android/data and obb)")
    if not enabled:
        warn("skipped (enable with --include-android-data)")
        return {"skipped": True}
    result = {"pulled": [], "failed": []}
    for sub in ("data", "obb"):
        remote = f"/sdcard/Android/{sub}"
        listing = adb.shell_ok(f"ls {remote} 2>/dev/null", timeout=120)
        pkgs = [p.strip() for p in listing.split() if p.strip()]
        if not pkgs:
            warn(f"{remote}: not readable over ADB on this build (scoped storage)")
            continue
        info(f"{remote}: {len(pkgs)} packages")
        for i, pkg in enumerate(pkgs, 1):
            local = snap.appdata / sub / pkg
            local.parent.mkdir(parents=True, exist_ok=True)
            rc, out, e = adb.raw(["pull", "-a", f"{remote}/{pkg}", str(local)], timeout=1200)
            if rc == 0:
                result["pulled"].append(f"{sub}/{pkg}")
            else:
                result["failed"].append({"path": f"{sub}/{pkg}",
                                         "reason": (e or out).strip()[:150]})
            if i % 10 == 0:
                sys.stdout.write(f"\r    {i}/{len(pkgs)}")
                sys.stdout.flush()
        sys.stdout.write("\n")
    snap.write_json(snap.appdata / "_report.json", result)
    ok(f"{len(result['pulled'])} package data folders pulled, {len(result['failed'])} refused")
    return result


def capture_home_screens(adb: Adb, snap: Snapshot, pages: int) -> int:
    step("Home-screen layout (screenshots)")
    if pages <= 0:
        warn("skipped (--home-screens 0)")
        return 0
    adb.shell_ok("input keyevent KEYCODE_HOME")
    time.sleep(1.2)
    # Swipe fully left first so we start on page 1.
    for _ in range(6):
        adb.shell_ok("input swipe 300 1000 900 1000 120")
        time.sleep(0.45)
    saved = 0
    for i in range(pages):
        rc, png = adb.exec_out("screencap -p", timeout=60)
        if rc == 0 and png[:4] == b"\x89PNG":
            (snap.screens / f"home_{i + 1:02d}.png").write_bytes(png)
            saved += 1
        adb.shell_ok("input swipe 900 1000 300 1000 120")
        time.sleep(0.7)
    # App drawer
    adb.shell_ok("input keyevent KEYCODE_HOME")
    time.sleep(0.8)
    adb.shell_ok("input swipe 540 1800 540 600 200")
    time.sleep(1.2)
    rc, png = adb.exec_out("screencap -p", timeout=60)
    if rc == 0 and png[:4] == b"\x89PNG":
        (snap.screens / "app_drawer.png").write_bytes(png)
        saved += 1
    adb.shell_ok("input keyevent KEYCODE_HOME")
    ok(f"{saved} screenshots saved (visual reference for rebuilding the launcher)")
    return saved


def trigger_cloud_backup(adb: Adb, snap: Snapshot, enabled: bool) -> Dict[str, Any]:
    step("Google cloud backup (the only route to private app data)")
    status = {
        "bmgr_enabled": adb.shell_ok("bmgr enabled").strip(),
        "transports": adb.shell_ok("bmgr list transports").strip(),
        "backup_enabled_setting": adb.shell_ok("settings get secure backup_enabled").strip(),
    }
    snap.write_json(snap.meta / "cloud_backup_status.json", status)
    info(f"backup manager: {status['bmgr_enabled'] or 'unknown'}")
    if not enabled:
        warn("not triggered. Re-run with --force-cloud-backup, or do it on the phone: "
             "Settings > Google > Backup > Back up now")
        return status
    if "enabled" not in status["bmgr_enabled"].lower():
        warn("backup manager reports disabled; enable it in Settings > Google > Backup first")
        return status
    info("running 'bmgr backupnow --all' - this can take several minutes...")
    rc, out, e = adb.shell("bmgr backupnow --all", timeout=3600)
    snap.write_text(snap.raw / "bmgr_backupnow.txt", out + "\n" + e)
    status["backupnow_rc"] = rc
    status["backupnow_tail"] = out.strip().splitlines()[-15:] if out else []
    if rc == 0:
        ok("cloud backup run completed - app data with allowBackup will restore on first boot")
    else:
        snap.fail("bmgr backupnow", e or out)
    snap.write_json(snap.meta / "cloud_backup_status.json", status)
    return status


# --------------------------------------------------------------------------- #
#  Reports
# --------------------------------------------------------------------------- #

def classify_manual_exports(apps: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()
    by_pkg = {a["package"]: a for a in apps}
    for pkg, severity, advice in MANUAL_EXPORT_RULES:
        if pkg in by_pkg and pkg not in seen:
            seen.add(pkg)
            found.append({"package": pkg, "severity": severity, "advice": advice})
    for pkg in by_pkg:
        if pkg in seen:
            continue
        low = pkg.lower()
        for kw, severity, advice in MANUAL_EXPORT_KEYWORDS:
            if kw in low:
                seen.add(pkg)
                found.append({"package": pkg, "severity": severity, "advice": advice})
                break
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    found.sort(key=lambda r: (order.get(r["severity"], 9), r["package"]))
    return found


def write_prewipe_checklist(snap: Snapshot, ident: Dict[str, Any],
                            apps: List[Dict[str, Any]], providers: Dict[str, Any]) -> None:
    flagged = classify_manual_exports(apps)
    snap.write_json(snap.reports / "manual_export_required.json", flagged)

    lines = [
        "# Pre-wipe checklist",
        "",
        f"Device: **{ident.get('manufacturer')} {ident.get('model')}** "
        f"(Android {ident.get('android_release')})",
        f"Generated: {_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')}",
        "",
        "Do everything on this page **before** you hand the phone to the service centre.",
        "The snapshot already captured your apps, settings and files. What is listed here",
        "is the part no tool can take for you, because Android does not expose it.",
        "",
        "---",
        "",
        "## 1. Non-negotiable",
        "",
        "- [ ] **Google account**: confirm you know the password and that 2FA will still work "
        "*after* this phone is wiped. If this phone is your only 2FA factor, print backup codes now: "
        "https://myaccount.google.com/security -> 2-Step Verification -> Backup codes.",
        "- [ ] **Force a Google backup**: Settings -> Google -> Backup -> *Back up now*. Wait for "
        "\"Backup complete\". This is the only supported way private app data comes back.",
        "- [ ] **Turn off Factory Reset Protection lockouts**: stay signed in to Google until the "
        "handover, and tell the technician the account so they do not get FRP-locked. Alternatively "
        "remove the account yourself right before handover.",
        "- [ ] **Take out the SIM and the SD card** before handing the phone over.",
        "- [ ] **Write down the IMEI** (dial `*#06#`) and the serial number, and photograph "
        "the box/bill. You want these if the device comes back with different hardware.",
        f"- [ ] Record the exact build you are on now, so you can tell whether they reflashed "
        f"or upgraded you: `{ident.get('build_fingerprint') or 'see 00_meta/device.json'}`",
        "",
        "## 2. Apps on this phone that need manual export",
        "",
    ]

    if flagged:
        lines += ["| Severity | App | What to do |", "|---|---|---|"]
        for f in flagged:
            lines.append(f"| **{f['severity']}** | `{f['package']}` | {f['advice']} |")
    else:
        lines.append("_No apps matched the known manual-export list. Still check any "
                     "authenticator, banking or messaging app you rely on._")

    lines += [
        "",
        "## 3. Data ADB could not take",
        "",
        "- [ ] **Wi-Fi passwords** - not readable without root. Open Settings -> Network & internet "
        "-> Internet -> Saved networks, and for each network use the QR/Share button and screenshot it "
        "with another device. Your saved SSID list is in `01_device_config/wifi_saved_networks.txt`.",
        "- [ ] **Private app data** (`/data/data`) - covered only by the Google backup in step 1.",
        "- [ ] **Alarms, Do-Not-Disturb schedules, widget layouts** - screenshot them.",
    ]
    if not providers.get("sms"):
        lines.append("- [ ] **SMS and call log** - could not be read over ADB on this build. "
                     "Install *SMS Backup & Restore* from Play Store, export to XML, and copy the "
                     "file to your PC.")
    else:
        lines.append(f"- [x] SMS ({providers.get('sms')} rows) and call log "
                     f"({providers.get('call_log')} rows) were exported to `04_personal_data/`.")
    lines += [
        "- [ ] **eSIM profiles** - if you use an eSIM, a reflash deletes it. Get a replacement "
        "QR/activation code from your carrier first.",
        "",
        "## 4. Right before handover",
        "",
        "- [ ] Re-run `python phone_snapshot.py capture` one final time so the snapshot is fresh.",
        "- [ ] Run `python phone_snapshot.py verify --snapshot <dir>` and confirm every file hashes clean.",
        "- [ ] Copy the snapshot folder to a second location (external drive or your VPS).",
        "- [ ] Sign out of any account you do not want a technician to have access to.",
        "",
    ]
    snap.write_text(snap.reports / "PRE-WIPE-CHECKLIST.md", "\n".join(lines))
    ok("PRE-WIPE-CHECKLIST.md written")
    if flagged:
        crit = [f for f in flagged if f["severity"] == "CRITICAL"]
        if crit:
            warn(f"{len(crit)} app(s) need a MANUAL export before the wipe - "
                 f"see 09_reports/PRE-WIPE-CHECKLIST.md")


def write_restore_guide(snap: Snapshot, ident: Dict[str, Any],
                        apps: List[Dict[str, Any]]) -> None:
    lines = [
        "# Restore guide",
        "",
        f"Snapshot of **{ident.get('manufacturer')} {ident.get('model')}**, "
        f"Android {ident.get('android_release')} (build `{ident.get('build_id')}`).",
        f"Captured {ident.get('captured_at')}.",
        "",
        "## Order of operations after the phone comes back",
        "",
        "1. **First boot**: when the setup wizard asks, choose *Restore from cloud backup* and pick "
        "this device's backup. That alone brings back many apps plus their private data, wallpaper, "
        "and most settings. Do this before anything below.",
        "2. Finish setup, connect to Wi-Fi, let Play Store finish restoring apps.",
        "3. Re-enable Developer options and USB debugging, plug into this PC.",
        "4. Run the automated restore:",
        "",
        "```",
        "python phone_snapshot.py restore --snapshot <this folder> --dry-run",
        "python phone_snapshot.py restore --snapshot <this folder> --apps --settings --perms",
        "```",
        "",
        "   `--dry-run` prints every command without executing it. Read it once before committing.",
        "",
        "5. Push your files back:",
        "",
        "```",
        "python phone_snapshot.py restore --snapshot <this folder> --media",
        "```",
        "",
        "6. Re-pair Bluetooth devices, re-add Wi-Fi networks, re-register UPI/banking apps, "
        "restore your authenticator from the export you made.",
        "7. Rebuild the home screen using the screenshots in `07_screens/`.",
        "",
        "## What restores automatically",
        "",
        "| Item | Mechanism | Fidelity |",
        "|---|---|---|",
        "| Apps | `adb install-multiple` from `02_apps/apk/` | exact version you had |",
        "| Runtime permissions | `pm grant` per app | exact |",
        "| App-ops | `appops set` (opt-in, `--appops`) | exact |",
        "| Battery exemptions | `dumpsys deviceidle whitelist +pkg` | exact |",
        "| Display/sound/gesture settings | `settings put` from the safe allowlist | exact |",
        "| Default apps | `cmd role add-role-holder` where permitted | best effort |",
        "| Files and media | `adb push` from `05_media/` | exact |",
        "| Private app data | Google cloud backup at first boot | partial, per app |",
        "",
        "## What does not restore, ever",
        "",
        "- Wi-Fi passwords, VPN certificates, saved passwords in apps",
        "- Login sessions - expect to sign in to everything again",
        "- Authenticator seeds (unless you exported them)",
        "- UPI / banking device binding - re-register with the SIM in the phone",
        "- Home-screen and widget layout (use the screenshots)",
        "",
        f"## App inventory ({len(apps)} user-installed)",
        "",
        "| App | Version | Installer |",
        "|---|---|---|",
    ]
    for a in sorted(apps, key=lambda x: x["package"]):
        lines.append(f"| `{a['package']}` | {a.get('versionName') or '?'} | "
                     f"{(a.get('installer') or 'sideloaded').replace('com.android.vending', 'Play Store')} |")
    lines.append("")
    snap.write_text(snap.reports / "RESTORE.md", "\n".join(lines))
    ok("RESTORE.md written")


def write_summary(snap: Snapshot, ident: Dict[str, Any], stats: Dict[str, Any]) -> None:
    lines = [
        "# Snapshot report",
        "",
        f"- Device: {ident.get('manufacturer')} {ident.get('model')} ({ident.get('device')})",
        f"- Android: {ident.get('android_release')} / SDK {ident.get('sdk')} / patch {ident.get('security_patch')}",
        f"- Build: `{ident.get('build_fingerprint')}`",
        f"- Captured: {ident.get('captured_at')}",
        f"- Tool: phone-snapshot {VERSION}",
        "",
        "## Contents",
        "",
    ]
    for k, v in stats.items():
        lines.append(f"- **{k}**: {v}")
    if snap.notes:
        lines += ["", "## Notes", ""] + [f"- {n}" for n in snap.notes]
    if snap.errors:
        lines += ["", "## Steps that failed or degraded", "",
                  "| Step | Reason |", "|---|---|"]
        for e in snap.errors:
            lines.append(f"| {e['step']} | {e['reason'][:160]} |")
    lines += [
        "",
        "## Folder map",
        "",
        "```",
        "00_meta/            device identity, cloud backup status, manifest",
        "01_device_config/   every settings key, properties, accounts, Wi-Fi SSIDs",
        "02_apps/            app inventory JSON, Play Store links, apk/<pkg>/*.apk",
        "03_app_state/       permissions, app-ops, battery whitelist, default apps, IMEs",
        "04_personal_data/   SMS, call log, calendar exports",
        "05_media/           your files from internal storage",
        "06_android_data/    /sdcard/Android/data and obb (best effort)",
        "07_screens/         home-screen screenshots",
        "08_raw_dumps/       unparsed dumpsys / getprop output, for forensics",
        "09_reports/         PRE-WIPE-CHECKLIST.md, RESTORE.md, this file",
        "```",
        "",
    ]
    snap.write_text(snap.reports / "SNAPSHOT_REPORT.md", "\n".join(lines))


# --------------------------------------------------------------------------- #
#  Manifest / verification
# --------------------------------------------------------------------------- #

def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            b = fh.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def build_manifest(snap: Snapshot, ident: Dict[str, Any], stats: Dict[str, Any],
                   hash_all: bool = True) -> Dict[str, Any]:
    step("Building manifest and checksums")
    files: List[Dict[str, Any]] = []
    total = 0
    manifest_path = snap.meta / "manifest.json"
    all_files = [p for p in snap.root.rglob("*") if p.is_file() and p != manifest_path]
    for i, p in enumerate(all_files, 1):
        size = p.stat().st_size
        total += size
        rec: Dict[str, Any] = {
            "path": p.relative_to(snap.root).as_posix(),
            "bytes": size,
            "mtime": int(p.stat().st_mtime),
        }
        if hash_all:
            rec["sha256"] = sha256_file(p)
        files.append(rec)
        if i % 250 == 0:
            sys.stdout.write(f"\r    hashed {i}/{len(all_files)} files")
            sys.stdout.flush()
    if len(all_files) >= 250:
        sys.stdout.write("\n")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "tool_version": VERSION,
        "device": ident,
        "stats": stats,
        "errors": snap.errors,
        "notes": snap.notes,
        "file_count": len(files),
        "total_bytes": total,
        "files": files,
    }
    snap.write_json(manifest_path, manifest)
    ok(f"{len(files)} files, {human(total)} total")
    return manifest


def cmd_verify(args: argparse.Namespace) -> int:
    root = Path(args.snapshot).expanduser().resolve()
    mpath = root / "00_meta" / "manifest.json"
    if not mpath.is_file():
        err(f"no manifest at {mpath}")
        return 2
    manifest = json.loads(mpath.read_text(encoding="utf-8"))
    step(f"Verifying {manifest['file_count']} files")
    missing, corrupt, okc = [], [], 0
    for i, rec in enumerate(manifest["files"], 1):
        p = root / rec["path"]
        if not p.is_file():
            missing.append(rec["path"])
            continue
        if "sha256" in rec:
            if sha256_file(p) != rec["sha256"]:
                corrupt.append(rec["path"])
                continue
        elif p.stat().st_size != rec["bytes"]:
            corrupt.append(rec["path"])
            continue
        okc += 1
        if i % 250 == 0:
            sys.stdout.write(f"\r    {i}/{manifest['file_count']}")
            sys.stdout.flush()
    sys.stdout.write("\n")
    ok(f"{okc} files verified clean")
    if missing:
        err(f"{len(missing)} missing: " + ", ".join(missing[:5]) + (" ..." if len(missing) > 5 else ""))
    if corrupt:
        err(f"{len(corrupt)} corrupt: " + ", ".join(corrupt[:5]) + (" ..." if len(corrupt) > 5 else ""))
    return 0 if not (missing or corrupt) else 1


# --------------------------------------------------------------------------- #
#  Diff: compare the live phone against a snapshot, and optionally fill the gaps
# --------------------------------------------------------------------------- #

def _current_package_state(adb: Adb) -> Dict[str, Dict[str, Any]]:
    """One bulk pass for current versions + runtime permission grants."""
    loop = ("for p in $(pm list packages -3 | cut -d: -f2); do "
            "echo \"===PKG===$p\"; dumpsys package \"$p\"; done")
    rc, blob, _ = adb.shell(loop, timeout=600)
    if rc != 0 or "===PKG===" not in blob:
        return {}
    return parse_package_dump(blob)


def _device_file_index(adb: Adb, top_dirs: Iterable[str],
                       timeout: int = 900) -> Dict[str, Optional[int]]:
    """Map every file under the given /sdcard subdirectories to its size."""
    index: Dict[str, Optional[int]] = {}
    for d in top_dirs:
        remote = f"/sdcard/{d}"
        out = adb.shell_ok(
            f'find "{remote}" -type f -exec stat -c "%s|%n" {{}} + 2>/dev/null',
            timeout=timeout)
        if out.strip() and "|" in out:
            for line in out.splitlines():
                if "|" not in line:
                    continue
                sz, _, path = line.partition("|")
                path = path.strip()
                if path:
                    try:
                        index[path] = int(sz.strip())
                    except ValueError:
                        index[path] = None
            continue
        # stat -c unsupported on this toybox; fall back to names only.
        out = adb.shell_ok(f'find "{remote}" -type f 2>/dev/null', timeout=timeout)
        for line in out.splitlines():
            path = line.strip()
            if path:
                index[path] = None
    return index


def cmd_diff(args: argparse.Namespace) -> int:
    root = Path(args.snapshot).expanduser().resolve()
    if not (root / "00_meta" / "device.json").is_file():
        err(f"{root} does not look like a snapshot (no 00_meta/device.json)")
        return 2

    adb = Adb(args.adb, args.serial)
    adb.require_device()
    src = json.loads((root / "00_meta" / "device.json").read_text(encoding="utf-8"))
    cur_model = adb.shell_ok("getprop ro.product.model").strip()
    info(f"snapshot: {src.get('manufacturer')} {src.get('model')} "
         f"(Android {src.get('android_release')}, captured {str(src.get('captured_at'))[:16]})")
    info(f"phone now: {cur_model or '?'} "
         f"(Android {adb.shell_ok('getprop ro.build.version.release').strip()})")

    def load(rel: str, default: Any) -> Any:
        p = root / rel
        if not p.is_file():
            return default
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return default

    d: Dict[str, Any] = {}

    # ---- apps ------------------------------------------------------------ #
    step("Apps")
    snap_apps = load("02_apps/apps_user.json", [])
    snap_by_pkg = {a["package"]: a for a in snap_apps
                   if not synthetic_reason(a["package"])}
    current = _current_package_state(adb)
    if not current:
        warn("could not read the phone's package list; skipping app comparison")
        installed_now = set()
    else:
        installed_now = set(current)

    apk_root = root / "02_apps" / "apk"
    missing_apps = []
    for pkg, a in sorted(snap_by_pkg.items()):
        if pkg in installed_now:
            continue
        have_apk = (apk_root / pkg).is_dir() and any((apk_root / pkg).glob("*.apk"))
        missing_apps.append({
            "package": pkg,
            "versionName": a.get("versionName"),
            "restorable": have_apk,
            "playUrl": f"https://play.google.com/store/apps/details?id={pkg}",
        })
    downgraded = []
    for pkg, a in sorted(snap_by_pkg.items()):
        if pkg not in current:
            continue
        was, now = a.get("versionName"), current[pkg].get("versionName")
        if was and now and was != now:
            downgraded.append({"package": pkg, "snapshot": was, "phone": now})
    extra_apps = sorted(p for p in installed_now
                        if p not in snap_by_pkg and not synthetic_reason(p))

    d["apps_missing"] = missing_apps
    d["apps_version_differs"] = downgraded
    d["apps_new_since_snapshot"] = extra_apps
    restorable = sum(1 for m in missing_apps if m["restorable"])
    (err if missing_apps else ok)(
        f"{len(missing_apps)} missing ({restorable} reinstallable from this snapshot, "
        f"{len(missing_apps) - restorable} need Play Store)")
    if downgraded:
        info(f"{len(downgraded)} apps at a different version than the snapshot")
    if extra_apps:
        info(f"{len(extra_apps)} apps on the phone that predate/postdate the snapshot "
             f"(left alone)")

    # ---- permissions ----------------------------------------------------- #
    step("Runtime permissions")
    snap_perms = load("03_app_state/permissions.json", {})
    missing_perms: List[Dict[str, str]] = []
    for pkg, blocks in sorted(snap_perms.items()):
        if pkg not in current:
            continue
        now = current[pkg].get("runtimePermissions") or {}
        for perm, was_granted in (blocks.get("runtime") or {}).items():
            if was_granted and not now.get(perm, False):
                missing_perms.append({"package": pkg, "permission": perm})
    d["permissions_missing"] = missing_perms
    (warn if missing_perms else ok)(
        f"{len(missing_perms)} permission grants present in the snapshot but not on the phone")

    # ---- settings -------------------------------------------------------- #
    step("Settings")
    want = load("01_device_config/settings_restorable.json", {})
    settings_diff: List[Dict[str, str]] = []
    for ns in ("system", "secure", "global"):
        if not want.get(ns):
            continue
        live = parse_settings(adb.shell_ok(f"settings list {ns}"))
        for k, v in sorted(want[ns].items()):
            if v in (None, "null"):
                continue
            if live.get(k) != str(v):
                settings_diff.append({"namespace": ns, "key": k,
                                      "snapshot": str(v), "phone": live.get(k, "(unset)")})
    d["settings_differ"] = settings_diff
    (warn if settings_diff else ok)(f"{len(settings_diff)} settings differ from the snapshot")

    # ---- battery exemptions, roles, IMEs --------------------------------- #
    step("Battery exemptions, default apps, keyboards")
    want_wl = set(load("03_app_state/battery_whitelist.json", []))
    live_wl = set(parse_deviceidle_whitelist(
        adb.shell_ok("dumpsys deviceidle whitelist", timeout=60)))
    missing_wl = sorted(w for w in want_wl - live_wl if w in installed_now)
    d["battery_whitelist_missing"] = missing_wl

    want_roles = (load("03_app_state/default_apps.json", {}) or {}).get("roles") or {}
    role_diff = []
    for role, holders in sorted(want_roles.items()):
        live = adb.shell_ok(f"cmd role get-role-holders --user 0 {role}", timeout=30).strip()
        live_holders = [x for x in re.split(r"[\s,]+", live) if x and "." in x]
        if sorted(live_holders) != sorted(holders):
            role_diff.append({"role": role, "snapshot": holders, "phone": live_holders})
    d["roles_differ"] = role_diff

    want_imes = set((load("03_app_state/input_methods.json", {}) or {}).get("enabled") or [])
    live_imes = {l.strip() for l in adb.shell_ok("ime list -s").splitlines() if l.strip()}
    missing_imes = sorted(want_imes - live_imes)
    d["input_methods_missing"] = missing_imes

    (warn if missing_wl else ok)(f"{len(missing_wl)} battery exemptions missing")
    (warn if role_diff else ok)(f"{len(role_diff)} default-app roles differ")
    (warn if missing_imes else ok)(f"{len(missing_imes)} keyboards not enabled")

    # ---- media ----------------------------------------------------------- #
    media_missing: List[Dict[str, Any]] = []
    media_size_mismatch: List[Dict[str, Any]] = []
    media_root = root / "05_media"
    if args.skip_media or not media_root.is_dir():
        step("Media")
        warn("skipped" if args.skip_media else "no media in this snapshot")
    else:
        step("Media")
        tops = [c.name for c in media_root.iterdir()
                if c.is_dir() and not c.name.startswith("_")]
        info(f"indexing {len(tops)} folders on the phone (this takes a moment)...")
        device_index = _device_file_index(adb, tops)
        ok(f"{len(device_index)} files on the phone under those folders")
        local_files = [p for p in media_root.rglob("*")
                       if p.is_file() and not p.name.startswith("_")]
        for p in local_files:
            rel = p.relative_to(media_root).as_posix()
            remote = f"/sdcard/{rel}"
            if remote not in device_index:
                media_missing.append({"device_path": remote, "local": str(p),
                                      "bytes": p.stat().st_size})
            else:
                dsz = device_index[remote]
                lsz = p.stat().st_size
                if dsz is not None and dsz != lsz:
                    media_size_mismatch.append({"device_path": remote, "local": str(p),
                                                "phone_bytes": dsz, "snapshot_bytes": lsz})
        gone = sum(m["bytes"] for m in media_missing)
        (err if media_missing else ok)(
            f"{len(media_missing)} of {len(local_files)} files missing from the phone "
            f"({human(gone)})")
        if media_size_mismatch:
            warn(f"{len(media_size_mismatch)} files differ in size (possible truncated push)")
    d["media_missing"] = media_missing
    d["media_size_mismatch"] = media_size_mismatch

    # ---- personal data (counts only) ------------------------------------- #
    if args.deep:
        step("SMS / call log / contacts (row counts)")
        counts = load("04_personal_data/_provider_counts.json", {})
        live_counts = {}
        for name, uri in CONTENT_PROVIDERS.items():
            if not counts.get(name):
                continue
            rc, out, _ = adb.shell(
                f"content query --uri {uri} --projection _id", timeout=900)
            n = sum(1 for l in out.splitlines() if l.startswith("Row:")) if rc == 0 else 0
            live_counts[name] = n
            status = ok if n >= counts[name] else warn
            status(f"{name}: snapshot {counts[name]} -> phone {n}")
        d["provider_counts"] = {"snapshot": counts, "phone": live_counts}
        warn("SMS and call history cannot be written back over ADB. If the counts are "
             "short, restore them with the app you exported them with "
             "(e.g. SMS Backup & Restore).")

    # ---- report ---------------------------------------------------------- #
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    reports = root / "09_reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"diff_{stamp}.json").write_text(
        json.dumps(d, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_diff_markdown(reports / f"DIFF_{stamp}.md", src, cur_model, d)

    step("Gap summary")
    rows = [
        ("apps missing", len(d["apps_missing"])),
        ("permission grants missing", len(d["permissions_missing"])),
        ("settings differing", len(d["settings_differ"])),
        ("battery exemptions missing", len(d["battery_whitelist_missing"])),
        ("default-app roles differing", len(d["roles_differ"])),
        ("keyboards not enabled", len(d["input_methods_missing"])),
        ("media files missing", len(d["media_missing"])),
    ]
    width = max(len(r[0]) for r in rows)
    total_gaps = 0
    for label, n in rows:
        total_gaps += n
        line = f"  {label:<{width}}  {n}"
        print((C.GREEN if n == 0 else C.YELLOW) + line + C.RESET)
    print()
    ok(f"report: {reports / f'DIFF_{stamp}.md'}")

    if total_gaps == 0:
        ok("the phone matches the snapshot")
        return 0
    if not args.fix:
        info("re-run with --fix to fill these gaps, or --fix --dry-run to preview")
        return 1

    _apply_diff(adb, root, d, args)
    return 0


def _write_diff_markdown(path: Path, src: Dict[str, Any], cur_model: str,
                         d: Dict[str, Any]) -> None:
    L = [
        "# Phone vs snapshot",
        "",
        f"- Snapshot: {src.get('manufacturer')} {src.get('model')}, captured {src.get('captured_at')}",
        f"- Phone now: {cur_model}",
        f"- Compared: {_dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %z')}",
        "",
    ]
    if d["apps_missing"]:
        L += ["## Apps missing", "", "| App | Version in snapshot | Reinstallable offline |",
              "|---|---|---|"]
        for a in d["apps_missing"]:
            L.append(f"| `{a['package']}` | {a['versionName'] or '?'} | "
                     f"{'yes' if a['restorable'] else 'no - use Play Store'} |")
        L.append("")
    if d["apps_version_differs"]:
        L += ["## Apps at a different version", "", "| App | Snapshot | Phone |", "|---|---|---|"]
        for a in d["apps_version_differs"]:
            L.append(f"| `{a['package']}` | {a['snapshot']} | {a['phone']} |")
        L.append("")
    if d["permissions_missing"]:
        L += [f"## Permission grants missing ({len(d['permissions_missing'])})", ""]
        by_pkg: Dict[str, List[str]] = {}
        for p in d["permissions_missing"]:
            by_pkg.setdefault(p["package"], []).append(p["permission"].rsplit(".", 1)[-1])
        for pkg, perms in sorted(by_pkg.items()):
            L.append(f"- `{pkg}`: {', '.join(sorted(perms))}")
        L.append("")
    if d["settings_differ"]:
        L += ["## Settings that differ", "", "| Namespace | Key | Snapshot | Phone |",
              "|---|---|---|---|"]
        for s in d["settings_differ"]:
            L.append(f"| {s['namespace']} | `{s['key']}` | `{s['snapshot']}` | `{s['phone']}` |")
        L.append("")
    if d["media_missing"]:
        total = sum(m["bytes"] for m in d["media_missing"])
        L += [f"## Media files missing ({len(d['media_missing'])}, {human(total)})", ""]
        for m in d["media_missing"][:200]:
            L.append(f"- `{m['device_path']}` ({human(m['bytes'])})")
        if len(d["media_missing"]) > 200:
            L.append(f"- ... and {len(d['media_missing']) - 200} more (see the JSON)")
        L.append("")
    if d["media_size_mismatch"]:
        L += [f"## Files whose size differs ({len(d['media_size_mismatch'])})", "",
              "These exist on the phone but do not match the snapshot - usually a push "
              "that was cut short.", ""]
        for m in d["media_size_mismatch"][:100]:
            L.append(f"- `{m['device_path']}`: phone {human(m['phone_bytes'])} vs "
                     f"snapshot {human(m['snapshot_bytes'])}")
        L.append("")
    if d.get("provider_counts"):
        L += ["## Personal data row counts", "", "| Source | Snapshot | Phone |", "|---|---|---|"]
        snapc = d["provider_counts"]["snapshot"]
        livec = d["provider_counts"]["phone"]
        for k in sorted(livec):
            L.append(f"| {k} | {snapc.get(k, 0)} | {livec[k]} |")
        L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def _apply_diff(adb: Adb, root: Path, d: Dict[str, Any],
                args: argparse.Namespace) -> None:
    r = Runner(adb, args.dry_run)
    if args.dry_run:
        warn("DRY RUN - printing commands only, nothing will be executed")

    apk_root = root / "02_apps" / "apk"
    todo = [m for m in d["apps_missing"] if m["restorable"]]
    if todo:
        step(f"Installing {len(todo)} missing apps")
        for i, m in enumerate(todo, 1):
            files = sorted((apk_root / m["package"]).glob("*.apk"))
            verb = "install-multiple" if len(files) > 1 else "install"
            if not r.dry:
                sys.stdout.write(f"\r    [{i}/{len(todo)}] {m['package'][:50]:<50}")
                sys.stdout.flush()
            good, blob = r.adb_cmd(
                [verb, "-r", "-d", "-t", "--user", "0"] + [str(f) for f in files],
                timeout=600)
            if not good and not r.dry:
                code = install_error_code(blob)
                if code == "INSTALL_FAILED_DEPRECATED_SDK_VERSION":
                    good, blob = r.adb_cmd(
                        [verb, "-r", "-d", "-t", "--bypass-low-target-sdk-block",
                         "--user", "0"] + [str(f) for f in files], timeout=600)
                if not good:
                    r.install_failures.append({
                        "package": m["package"], "code": code or "UNKNOWN",
                        "detail": blob.replace("\n", " ")[-300:]})
        if not r.dry:
            sys.stdout.write("\n")
        ok("install pass done")
    unrestorable = [m for m in d["apps_missing"] if not m["restorable"]]
    if unrestorable:
        warn(f"{len(unrestorable)} missing apps have no APK in this snapshot - "
             f"install from Play Store:")
        for m in unrestorable[:20]:
            print(f"    {m['playUrl']}")

    if d["permissions_missing"]:
        step(f"Re-granting {len(d['permissions_missing'])} permissions")
        for p in d["permissions_missing"]:
            r.sh(f"pm grant {p['package']} {p['permission']}", timeout=30)
        ok("permission pass done")

    if d["settings_differ"]:
        step(f"Writing {len(d['settings_differ'])} settings")
        for s in d["settings_differ"]:
            r.sh(f"settings put {s['namespace']} {s['key']} {_shq(s['snapshot'])}", timeout=30)
        ok("settings pass done")

    if d["battery_whitelist_missing"]:
        step(f"Restoring {len(d['battery_whitelist_missing'])} battery exemptions")
        for pkg in d["battery_whitelist_missing"]:
            r.sh(f"dumpsys deviceidle whitelist +{pkg}", timeout=30)

    if d["roles_differ"]:
        step(f"Restoring {len(d['roles_differ'])} default-app roles")
        for rd in d["roles_differ"]:
            for h in rd["snapshot"]:
                r.sh(f"cmd role add-role-holder --user 0 {rd['role']} {h}", timeout=30)

    if d["input_methods_missing"]:
        step(f"Enabling {len(d['input_methods_missing'])} keyboards")
        for i in d["input_methods_missing"]:
            r.sh(f"ime enable {i}", timeout=30)

    push_list = list(d["media_missing"])
    if args.repush_mismatched:
        push_list += d["media_size_mismatch"]
    if push_list:
        total = sum(m.get("bytes") or m.get("snapshot_bytes") or 0 for m in push_list)
        step(f"Pushing {len(push_list)} media files ({human(total)})")
        if len(push_list) > 3000 and not args.yes:
            warn(f"{len(push_list)} individual pushes will be slow. A full "
                 f"'restore --media' is faster when this much is missing.")
            if input("    continue file-by-file? [y/N] ").strip().lower() != "y":
                push_list = []
        done = 0
        for m in push_list:
            r.adb_cmd(["push", m["local"], m["device_path"]], timeout=1800)
            done += 1
            if not r.dry and (done % 25 == 0 or done == len(push_list)):
                sys.stdout.write(f"\r    {done}/{len(push_list)}")
                sys.stdout.flush()
        if not r.dry and push_list:
            sys.stdout.write("\n")
        if push_list:
            step("Re-indexing MediaStore")
            good = r.sh("content call --uri content://media --method scan_volume "
                        "--arg external_primary", timeout=900)
            if not good:
                warn("media scan unavailable; reboot the phone to make the files appear")

    step("Fix summary")
    ok(f"{r.ran} commands executed")
    if r.install_failures:
        warn(f"{len(r.install_failures)} apps still would not install:")
        for f in r.install_failures:
            cause, fixtip = INSTALL_FAILURE_HELP.get(
                f["code"], ("Unrecognised install failure.", "Install from Play Store."))
            print(f"    {C.BOLD}{f['package']}{C.RESET}  {f['code']}")
            print(f"      {cause}")
            print(f"      {fixtip}")
    if r.failed:
        log = root / "09_reports" / "diff_fix_failures.txt"
        log.write_text("\n".join(f"{c}\n  {w}" for c, w in r.failed), encoding="utf-8")
        warn(f"{len(r.failed)} commands failed; log: {log}")
    info("re-run 'diff' to confirm the gaps are closed")


# --------------------------------------------------------------------------- #
#  Archive + upload
# --------------------------------------------------------------------------- #

def cmd_archive(args: argparse.Namespace) -> int:
    root = Path(args.snapshot).expanduser().resolve()
    if not root.is_dir():
        err(f"not a directory: {root}")
        return 2
    raw_size = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    store_only = args.compresslevel == 0
    if not store_only and raw_size > 5 * 1024 ** 3:
        warn(f"This snapshot is {human(raw_size)} and is mostly APKs, JPEGs and video - "
             f"all already compressed. gzip will take a long time and save almost nothing. "
             f"Consider --compresslevel 0 (store only, much faster).")
    suffix = ".tar" if store_only else ".tar.gz"
    out = Path(args.output).expanduser().resolve() if args.output else \
        root.parent / f"{root.name}{suffix}"
    step(f"Archiving -> {out}")
    t0 = time.time()
    if store_only:
        with tarfile.open(out, "w") as tf:
            tf.add(root, arcname=root.name)
    else:
        with tarfile.open(out, "w:gz", compresslevel=args.compresslevel) as tf:
            tf.add(root, arcname=root.name)
    ok(f"{human(out.stat().st_size)} in {time.time() - t0:.0f}s")

    sha = sha256_file(out)
    (out.parent / (out.name + ".sha256")).write_text(f"{sha}  {out.name}\n", encoding="utf-8")
    ok(f"sha256 {sha}")

    if args.encrypt:
        enc = _encrypt(out)
        if enc:
            ok(f"encrypted -> {enc}")
            if args.remove_plain:
                out.unlink()
                warn("plaintext archive removed")
        else:
            err("encryption unavailable: install gpg, 7-Zip or OpenSSL, or drop --encrypt")
            return 3
    return 0


def _encrypt(path: Path) -> Optional[Path]:
    """Symmetric-encrypt with whatever is installed. Prompts for a passphrase."""
    gpg = shutil.which("gpg")
    if gpg:
        dest = path.with_suffix(path.suffix + ".gpg")
        info("using gpg (AES-256, you will be prompted for a passphrase)")
        rc = subprocess.call([gpg, "--symmetric", "--cipher-algo", "AES256",
                              "--output", str(dest), str(path)])
        return dest if rc == 0 and dest.exists() else None
    sz = shutil.which("7z") or shutil.which("7za") or (
        r"C:\Program Files\7-Zip\7z.exe" if IS_WINDOWS and
        Path(r"C:\Program Files\7-Zip\7z.exe").is_file() else None)
    if sz:
        dest = path.with_suffix(path.suffix + ".7z")
        info("using 7-Zip (AES-256, headers encrypted)")
        rc = subprocess.call([sz, "a", "-t7z", "-mhe=on", "-p", str(dest), str(path)])
        return dest if rc == 0 and dest.exists() else None
    ossl = shutil.which("openssl")
    if ossl:
        dest = path.with_suffix(path.suffix + ".enc")
        info("using openssl aes-256-cbc with pbkdf2")
        rc = subprocess.call([ossl, "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "600000",
                              "-salt", "-in", str(path), "-out", str(dest)])
        return dest if rc == 0 and dest.exists() else None
    return None


def cmd_upload(args: argparse.Namespace) -> int:
    src = Path(args.archive).expanduser().resolve()
    if not src.is_file():
        err(f"not a file: {src}")
        return 2
    dest = args.dest
    step(f"Uploading {src.name} ({human(src.stat().st_size)}) -> {dest}")

    if dest.startswith("scp://"):
        target = dest[len("scp://"):]
        exe = shutil.which("scp")
        if not exe:
            err("scp not found (install OpenSSH client)")
            return 3
        argv = [exe]
        if args.identity:
            argv += ["-i", args.identity]
        if args.port:
            argv += ["-P", str(args.port)]
        argv += [str(src), target]
    elif dest.startswith("rsync://"):
        target = dest[len("rsync://"):]
        exe = shutil.which("rsync")
        if not exe:
            err("rsync not found")
            return 3
        argv = [exe, "-avP", "--partial"]
        if args.identity:
            argv += ["-e", f"ssh -i {args.identity}"]
        argv += [str(src), target]
    elif dest.startswith("rclone:"):
        target = dest[len("rclone:"):]
        exe = shutil.which("rclone")
        if not exe:
            err("rclone not found")
            return 3
        argv = [exe, "copy", "-P", str(src), target]
    else:
        err("--dest must start with scp://, rsync:// or rclone:")
        return 2

    info(" ".join(argv))
    rc = subprocess.call(argv)
    if rc == 0:
        ok("upload complete")
        sidecar = src.parent / (src.name + ".sha256")
        if sidecar.is_file():
            subprocess.call([a if a != str(src) else str(sidecar) for a in argv])
    else:
        err(f"upload failed (exit {rc})")
    return rc


# --------------------------------------------------------------------------- #
#  Restore
# --------------------------------------------------------------------------- #

class Runner:
    def __init__(self, adb: Adb, dry_run: bool):
        self.adb = adb
        self.dry = dry_run
        self.ran = 0
        self.failed: List[Tuple[str, str]] = []
        self.skipped: List[Tuple[str, str]] = []
        self.install_failures: List[Dict[str, str]] = []

    def sh(self, cmd: str, *, timeout: int = 120) -> bool:
        if self.dry:
            print(f"    adb shell {cmd}")
            return True
        rc, out, e = self.adb.shell(cmd, timeout=timeout)
        self.ran += 1
        bad = rc != 0 or "Exception" in out or "Error" in out or "Failure" in out
        if bad:
            self.failed.append((cmd, (e or out).strip()[:200]))
        return not bad

    def adb_cmd(self, args: Sequence[str], *, timeout: int = 600) -> Tuple[bool, str]:
        """Run adb. Returns (succeeded, combined output) so callers can react to
        the specific failure rather than just 'it failed'."""
        if self.dry:
            print("    adb " + " ".join(args))
            return True, ""
        rc, out, e = self.adb.raw(args, timeout=timeout)
        self.ran += 1
        blob = (str(out) + "\n" + e).strip()
        bad = rc != 0 or "Failure" in blob
        if bad:
            # Keep the *reason*, not 160 characters of Windows path.
            code = install_error_code(blob)
            short = code or blob.replace("\n", " ")[-160:]
            self.failed.append((" ".join(args[:2]) + " ...", short))
        return (not bad), blob


def _shq(value: str) -> str:
    """Quote a value for the Android shell."""
    if value is None:
        return "''"
    if re.fullmatch(r"[\w.,:/@+=-]*", value):
        return value or "''"
    return "'" + value.replace("'", "'\\''") + "'"


def cmd_restore(args: argparse.Namespace) -> int:
    root = Path(args.snapshot).expanduser().resolve()
    if not (root / "00_meta" / "device.json").is_file():
        err(f"{root} does not look like a snapshot (no 00_meta/device.json)")
        return 2

    adb = Adb(args.adb, args.serial)
    adb.require_device()
    src = json.loads((root / "00_meta" / "device.json").read_text(encoding="utf-8"))
    cur_model = adb.shell_ok("getprop ro.product.model").strip()
    info(f"snapshot device: {src.get('model')} / connected device: {cur_model}")
    if cur_model and src.get("model") and cur_model != src.get("model"):
        warn("model mismatch - restoring across different models can behave oddly")
        if not args.yes and input("    continue? [y/N] ").strip().lower() != "y":
            return 1

    r = Runner(adb, args.dry_run)
    selected = any([args.apps, args.settings, args.perms, args.appops,
                    args.battery, args.defaults, args.media])
    if not selected:
        args.apps = args.settings = args.perms = args.battery = args.defaults = True
        info("no subsystem flags given; restoring apps, settings, permissions, "
             "battery exemptions and defaults (media excluded - pass --media)")
    if args.dry_run:
        warn("DRY RUN - printing commands only, nothing will be executed")

    apps: List[Dict[str, Any]] = []
    apps_file = root / "02_apps" / "apps_user.json"
    if apps_file.is_file():
        apps = json.loads(apps_file.read_text(encoding="utf-8"))

    if args.apps:
        _restore_apps(r, root, apps, args)
    if args.settings:
        _restore_settings(r, root, args.settings_mode)
    if args.perms:
        _restore_permissions(r, root, apps)
    if args.appops:
        _restore_appops(r, root)
    if args.battery:
        _restore_battery(r, root)
    if args.defaults:
        _restore_defaults(r, root)
    if args.media:
        _restore_media(r, root)

    step("Restore summary")
    ok(f"{r.ran} commands executed")
    if r.skipped:
        info(f"{len(r.skipped)} package(s) deliberately skipped (nothing to do)")

    if r.install_failures:
        warn(f"{len(r.install_failures)} app(s) could not be installed:")
        lines = ["# Apps that would not install", ""]
        for f in r.install_failures:
            cause, fix = INSTALL_FAILURE_HELP.get(
                f["code"], ("Unrecognised install failure.",
                            "Reinstall from Play Store, or rebuild from source if it is your own app."))
            print(f"    {C.BOLD}{f['package']}{C.RESET}")
            print(f"      {f['code']}")
            print(f"      why: {cause}")
            print(f"      fix: {fix}")
            lines += [
                f"## `{f['package']}`", "",
                f"- **Code:** `{f['code']}`",
                f"- **Why:** {cause}",
                f"- **Fix:** {fix}",
                f"- **Play Store:** https://play.google.com/store/apps/details?id={f['package']}",
                f"- Raw: `{f['detail']}`", "",
            ]
        rep = root / "09_reports" / "install_failures.md"
        rep.parent.mkdir(parents=True, exist_ok=True)
        rep.write_text("\n".join(lines), encoding="utf-8")
        warn(f"details: {rep}")

    other = [f for f in r.failed if not f[0].startswith(("install", "install-multiple"))]
    if other:
        warn(f"{len(other)} other commands failed (mostly non-grantable permissions):")
        for cmd, why in other[:15]:
            print(f"    {C.DIM}{cmd[:90]}{C.RESET} -> {why}")
        if len(other) > 15:
            print(f"    ... and {len(other) - 15} more")
    if r.failed:
        log = root / "09_reports" / "restore_failures.txt"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(f"{c}\n  {w}" for c, w in r.failed), encoding="utf-8")
        warn(f"full log: {log}")
    return 0


def _restore_apps(r: Runner, root: Path, apps: List[Dict[str, Any]],
                  args: argparse.Namespace) -> None:
    step("Reinstalling apps")
    apk_root = root / "02_apps" / "apk"
    if not apk_root.is_dir():
        warn("no APKs in this snapshot")
        return
    installed = set()
    if not args.dry_run:
        installed = set(_pkg_list(r.adb, "-3"))
        if not installed:
            warn("the phone reported ZERO user apps installed. On a freshly flashed "
                 "device that is correct. Otherwise adb hiccuped, and everything is "
                 "about to be reinstalled over the top - Ctrl-C now if that is wrong.")
            time.sleep(4)
    pkgs = sorted(p.name for p in apk_root.iterdir() if p.is_dir())

    synthetic = [p for p in pkgs if synthetic_reason(p)]
    for p in synthetic:
        r.skipped.append((p, synthetic_reason(p) or ""))
    pkgs = [p for p in pkgs if p not in set(synthetic)]
    if synthetic:
        info(f"{len(synthetic)} auto-generated packages skipped "
             f"(Chrome WebAPKs and friends - they regenerate on their own)")

    todo = [p for p in pkgs if args.reinstall_existing or p not in installed]
    info(f"{len(pkgs)} real apps in snapshot, {len(todo)} to install "
         f"({len(pkgs) - len(todo)} already present)")

    # -t allows Android Studio debug builds (testOnly=true), which is the usual
    # reason a developer's own apps refuse to install from a pulled APK.
    BASE_FLAGS = ["-r", "-d", "-t", "--user", "0"]
    RETRY_FLAGS = {
        "INSTALL_FAILED_DEPRECATED_SDK_VERSION": ["--bypass-low-target-sdk-block"],
        "INSTALL_FAILED_TEST_ONLY": ["-t"],
    }

    for i, pkg in enumerate(todo, 1):
        files = sorted((apk_root / pkg).glob("*.apk"))
        if not files:
            continue
        verb = "install-multiple" if len(files) > 1 else "install"
        paths = [str(f) for f in files]
        if r.dry:
            print(f"  [{i}/{len(todo)}] {pkg}")
        else:
            sys.stdout.write(f"\r    [{i}/{len(todo)}] {pkg[:50]:<50}")
            sys.stdout.flush()

        good, blob = r.adb_cmd([verb] + BASE_FLAGS + paths, timeout=600)
        if not good and not r.dry:
            code = install_error_code(blob)
            extra = RETRY_FLAGS.get(code or "")
            if extra:
                good, blob = r.adb_cmd([verb] + BASE_FLAGS + extra + paths, timeout=600)
                code = install_error_code(blob) if not good else None
            if not good:
                r.install_failures.append({
                    "package": pkg,
                    "code": code or "UNKNOWN",
                    "detail": blob.replace("\n", " ")[-300:],
                })
    if not r.dry:
        sys.stdout.write("\n")
    ok(f"app installation pass finished"
       + (f" - {len(r.install_failures)} app(s) refused" if r.install_failures else ""))

    disabled_file = root / "02_apps" / "apps_user.json"
    if disabled_file.is_file():
        for a in apps:
            if a.get("disabled"):
                r.sh(f"pm disable-user --user 0 {a['package']}")


def _restore_settings(r: Runner, root: Path, mode: str) -> None:
    step(f"Restoring settings ({mode} mode)")
    if mode == "all":
        f = root / "01_device_config" / "settings.json"
        warn("ALL mode writes every captured key back. This can destabilise Settings. "
             "Safe mode is strongly recommended.")
    else:
        f = root / "01_device_config" / "settings_restorable.json"
    if not f.is_file():
        warn("no settings file in snapshot")
        return
    data = json.loads(f.read_text(encoding="utf-8"))
    n = 0
    for ns in ("system", "secure", "global"):
        for k, v in sorted(data.get(ns, {}).items()):
            if v is None or v == "null":
                continue
            r.sh(f"settings put {ns} {k} {_shq(str(v))}", timeout=30)
            n += 1
    ok(f"{n} settings written")


def _restore_permissions(r: Runner, root: Path, apps: List[Dict[str, Any]]) -> None:
    step("Re-granting runtime permissions")
    f = root / "03_app_state" / "permissions.json"
    if not f.is_file():
        warn("no permissions file in snapshot")
        return
    perms = json.loads(f.read_text(encoding="utf-8"))

    # Granting a permission to an app that is not installed is a guaranteed
    # failure and just pads the error log. Check once, up front.
    present = set(_pkg_list(r.adb, "-3")) | set(_pkg_list(r.adb, "-s")) if not r.dry else set(perms)
    absent = [p for p in perms if p not in present and not synthetic_reason(p)]
    if absent:
        warn(f"{len(absent)} app(s) from the snapshot are not installed; their permissions "
             f"are skipped: {', '.join(absent[:4])}{' ...' if len(absent) > 4 else ''}")
        for p in absent:
            r.skipped.append((p, "not installed - permissions skipped"))

    grants = 0
    for pkg, blocks in sorted(perms.items()):
        if pkg not in present:
            continue
        for perm, granted in (blocks.get("runtime") or {}).items():
            if granted:
                r.sh(f"pm grant {pkg} {perm}", timeout=30)
                grants += 1
    ok(f"{grants} permission grants attempted "
       f"(failures are normal: some permissions are not grantable via adb)")


def _restore_appops(r: Runner, root: Path) -> None:
    step("Re-applying app-ops")
    f = root / "03_app_state" / "appops.json"
    if not f.is_file():
        warn("no appops file in snapshot")
        return
    data = json.loads(f.read_text(encoding="utf-8"))
    n = 0
    for pkg, ops in sorted(data.items()):
        for op, mode in ops.items():
            if mode in ("default",):
                continue
            r.sh(f"appops set {pkg} {op} {mode}", timeout=30)
            n += 1
    ok(f"{n} app-op assignments attempted")


def _restore_battery(r: Runner, root: Path) -> None:
    step("Restoring battery-optimisation exemptions")
    f = root / "03_app_state" / "battery_whitelist.json"
    if not f.is_file():
        warn("no battery whitelist in snapshot")
        return
    pkgs = json.loads(f.read_text(encoding="utf-8"))
    for pkg in pkgs:
        r.sh(f"dumpsys deviceidle whitelist +{pkg}", timeout=30)
    ok(f"{len(pkgs)} exemptions attempted")


def _restore_defaults(r: Runner, root: Path) -> None:
    step("Restoring default apps")
    f = root / "03_app_state" / "default_apps.json"
    if not f.is_file():
        warn("no default_apps.json in snapshot")
        return
    data = json.loads(f.read_text(encoding="utf-8"))
    roles = data.get("roles") or {}
    for role, holders in sorted(roles.items()):
        for h in holders:
            r.sh(f"cmd role add-role-holder --user 0 {role} {h}", timeout=30)
    ok(f"{sum(len(v) for v in roles.values())} role assignments attempted "
       f"(some roles can only be set from Settings)")
    ime = root / "03_app_state" / "input_methods.json"
    if ime.is_file():
        imes = json.loads(ime.read_text(encoding="utf-8"))
        for i in imes.get("enabled", []):
            r.sh(f"ime enable {i}", timeout=30)


def _restore_media(r: Runner, root: Path) -> None:
    step("Pushing user files back to /sdcard")
    media = root / "05_media"
    if not media.is_dir():
        warn("no media in snapshot")
        return
    children = [c for c in sorted(media.iterdir()) if not c.name.startswith("_")]
    total = sum(f.stat().st_size for c in children for f in c.rglob("*") if f.is_file())
    info(f"{human(total)} to push. This is the slow part - USB 2.0 runs at roughly "
         f"25 MB/s, so budget accordingly.")
    for child in children:
        size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
        info(f"pushing {child.name} ({human(size)})...")
        r.adb_cmd(["push", str(child), "/sdcard/"], timeout=21600)
    ok("media push finished")

    # Pushed files are invisible to Gallery/Files until MediaStore indexes them.
    step("Re-indexing MediaStore")
    good = r.sh("content call --uri content://media --method scan_volume "
                "--arg external_primary", timeout=900)
    if not good:
        good = r.sh("content call --uri content://media/external --method scan_volume "
                    "--arg external_primary", timeout=900)
    if good:
        ok("media scan triggered - photos should appear in Gallery shortly")
    else:
        warn("could not trigger a media scan on this build. Just reboot the phone; "
             "it rescans /sdcard on boot.")


# --------------------------------------------------------------------------- #
#  doctor / capture entry points
# --------------------------------------------------------------------------- #

def cmd_doctor(args: argparse.Namespace) -> int:
    step("Environment check")
    try:
        adb = Adb(args.adb, args.serial)
    except AdbError as e:
        err(str(e))
        return 2
    ok(f"adb: {adb.binary}")
    rc, out, _ = adb.raw(["version"], timeout=30)
    if rc == 0:
        info(out.strip().splitlines()[0])

    devs = adb.devices()
    if not devs:
        err("no device detected - see the hints below")
        warn("Enable Developer options -> USB debugging, plug in, accept the prompt.")
        return 1
    for d in devs:
        line = f"{d['serial']}  state={d['state']}"
        if "model" in d:
            line += f"  model={d['model']}"
        (ok if d["state"] == "device" else warn)(line)
    try:
        adb.require_device()
    except AdbError as e:
        err(str(e))
        return 1

    step("Capability probe")
    probes = [
        ("device properties", "getprop ro.build.version.release"),
        ("settings provider", "settings list global"),
        ("package manager", "pm list packages -3"),
        ("app-ops", "dumpsys appops"),
        ("battery whitelist", "dumpsys deviceidle whitelist"),
        ("role manager", "cmd role get-role-holders --user 0 android.app.role.BROWSER"),
        ("wifi networks", "cmd wifi list-networks"),
        ("SMS provider", "content query --uri content://sms --projection _id"),
        ("call log provider", "content query --uri content://call_log/calls --projection _id"),
        ("internal storage", "ls /sdcard"),
        ("Android/data", "ls /sdcard/Android/data"),
        ("backup manager", "bmgr enabled"),
        ("screencap", "screencap -p /dev/null"),
    ]
    for label, cmd in probes:
        rc, out, e = adb.shell(cmd, timeout=60)
        good = rc == 0 and out.strip() and "Permission Denial" not in out and "denied" not in out.lower()
        (ok if good else warn)(f"{label:<22} {'available' if good else 'unavailable'}")

    unlocked = adb.screen_unlocked()
    if unlocked is False:
        warn("screen appears LOCKED - unlock the phone before capturing")
    elif unlocked:
        ok("screen unlocked")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    t0 = time.time()
    adb = Adb(args.adb, args.serial)
    adb.require_device()

    unlocked = adb.screen_unlocked()
    if unlocked is False and not args.yes:
        warn("The phone screen appears to be locked. Many dumps return nothing while locked.")
        if input("    unlock it now, then press Enter (or type 'skip'): ").strip().lower() == "skip":
            pass

    model = adb.shell_ok("getprop ro.product.model").strip().replace(" ", "-") or "android"
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_root = Path(args.out).expanduser().resolve()
    root = out_root / f"snapshot_{model}_{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    snap = Snapshot(root)
    info(f"snapshot directory: {root}")

    ident = capture_identity(adb, snap)
    settings = capture_settings(adb, snap)
    appinfo = capture_apps(adb, snap, pull_apks=not args.skip_apks,
                           workers=args.workers, apks_mode=args.apks_mode)
    state = capture_app_state(adb, snap, appinfo["user"])
    net = capture_network_and_accounts(adb, snap)
    capture_dumpsys(adb, snap)
    providers = capture_providers(adb, snap)
    media = capture_media(adb, snap, args.media_dirs or MEDIA_DIRS, skip=args.skip_media)
    extdata = capture_android_data(adb, snap, args.include_android_data)
    shots = capture_home_screens(adb, snap, args.home_screens)
    cloud = trigger_cloud_backup(adb, snap, args.force_cloud_backup)

    stats = {
        "user apps": len(appinfo["user"]),
        "system apps": len(appinfo["system"]),
        "disabled apps": len(appinfo["disabled"]),
        "APK files pulled": appinfo["apk_pull"].get("count", 0),
        "APK bytes": human(appinfo["apk_pull"].get("bytes", 0)),
        "settings keys": sum(len(v) for v in settings.values()),
        "runtime permission grants": sum(
            1 for v in state["permissions"].values() for g in v["runtime"].values() if g),
        "battery exemptions": len(state["battery_whitelist"]),
        "default app roles": len(state["roles"]),
        "saved wifi networks": net["wifi_count"],
        "accounts": net["accounts"],
        "sms rows": providers.get("sms", 0),
        "call log rows": providers.get("call_log", 0),
        "media bytes": human(media.get("bytes", 0)) if not media.get("skipped") else "skipped",
        "android/data folders": len(extdata.get("pulled", [])) if not extdata.get("skipped") else "skipped",
        "home screenshots": shots,
        "cloud backup triggered": bool(cloud.get("backupnow_rc") == 0),
    }

    step("Writing reports")
    write_prewipe_checklist(snap, ident, appinfo["user"], providers)
    write_restore_guide(snap, ident, appinfo["user"])
    write_summary(snap, ident, stats)
    build_manifest(snap, ident, stats, hash_all=not args.no_hash)

    step("Done")
    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    ok(f"snapshot complete in {time.time() - t0:.0f}s, {human(total)} at:")
    print(f"    {C.BOLD}{root}{C.RESET}")
    print()
    print(f"  Read this next: {root / '09_reports' / 'PRE-WIPE-CHECKLIST.md'}")
    print(f"  Verify:         python {Path(__file__).name} verify --snapshot \"{root}\"")
    print(f"  Archive:        python {Path(__file__).name} archive --snapshot \"{root}\" --encrypt")
    if snap.errors:
        warn(f"{len(snap.errors)} steps degraded - see 09_reports/SNAPSHOT_REPORT.md")
    return 0


# --------------------------------------------------------------------------- #
#  CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="phone_snapshot",
        description="Snapshot and restore a stock, non-rooted Android phone over ADB.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run 'doctor' first to check your setup.",
    )
    p.add_argument("--adb", help="path to the adb binary (auto-detected by default)")
    p.add_argument("--serial", help="target device serial (adb devices)")
    p.add_argument("--version", action="version", version=f"phone-snapshot {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="check adb, device and what this build allows")
    d.set_defaults(func=cmd_doctor)

    c = sub.add_parser("capture", help="capture a full snapshot")
    c.add_argument("--out", default=".", help="parent directory for the snapshot folder")
    c.add_argument("--skip-media", action="store_true", help="do not pull user files")
    c.add_argument("--skip-apks", action="store_true", help="inventory apps but do not pull APKs")
    c.add_argument("--apks-mode", choices=["all", "sideloaded", "none"], default="all",
                   help="'all' stores every APK (biggest, fully offline); "
                        "'sideloaded' stores only apps NOT from Play Store, which is usually "
                        "a fraction of the size since Play reinstalls the rest for you; "
                        "'none' inventories only")
    c.add_argument("--include-android-data", action="store_true",
                   help="also try /sdcard/Android/data and obb (slow, often refused)")
    c.add_argument("--force-cloud-backup", action="store_true",
                   help="run 'bmgr backupnow --all' - the only route to private app data")
    c.add_argument("--home-screens", type=int, default=6, metavar="N",
                   help="screenshot N home-screen pages (0 to disable, default 6)")
    c.add_argument("--media-dirs", nargs="*", metavar="DIR",
                   help="override the /sdcard folders to pull")
    c.add_argument("--workers", type=int, default=4, help="parallel adb workers (default 4)")
    c.add_argument("--no-hash", action="store_true",
                   help="skip SHA-256 in the manifest (faster, weaker verification)")
    c.add_argument("--yes", "-y", action="store_true", help="do not prompt")
    c.set_defaults(func=cmd_capture)

    v = sub.add_parser("verify", help="re-hash a snapshot against its manifest "
                                      "(checks the BACKUP, not the phone)")
    v.add_argument("--snapshot", required=True)
    v.set_defaults(func=cmd_verify)

    g = sub.add_parser("diff", help="compare the connected PHONE against a snapshot "
                                    "and report what is missing (--fix restores it)")
    g.add_argument("--snapshot", required=True)
    g.add_argument("--fix", action="store_true",
                   help="restore everything the phone is missing")
    g.add_argument("--dry-run", action="store_true",
                   help="with --fix, print commands without running them")
    g.add_argument("--skip-media", action="store_true",
                   help="do not index media files (much faster)")
    g.add_argument("--deep", action="store_true",
                   help="also count SMS / call log / contacts rows on the phone")
    g.add_argument("--repush-mismatched", action="store_true",
                   help="with --fix, also re-push files whose size differs")
    g.add_argument("--yes", "-y", action="store_true", help="do not prompt")
    g.set_defaults(func=cmd_diff)

    a = sub.add_parser("archive", help="tar.gz a snapshot, optionally encrypted")
    a.add_argument("--snapshot", required=True)
    a.add_argument("--output", help="output archive path")
    a.add_argument("--compresslevel", type=int, default=6)
    a.add_argument("--encrypt", action="store_true",
                   help="symmetric-encrypt with gpg / 7-Zip / openssl, whichever is installed")
    a.add_argument("--remove-plain", action="store_true",
                   help="delete the unencrypted archive after encrypting")
    a.set_defaults(func=cmd_archive)

    u = sub.add_parser("upload", help="send an archive to a server")
    u.add_argument("--archive", required=True)
    u.add_argument("--dest", required=True,
                   help="scp://user@host:/path/  |  rsync://user@host:/path/  |  rclone:remote:path")
    u.add_argument("--identity", help="ssh private key")
    u.add_argument("--port", type=int, help="ssh port")
    u.set_defaults(func=cmd_upload)

    r = sub.add_parser("restore", help="rebuild a phone from a snapshot")
    r.add_argument("--snapshot", required=True)
    r.add_argument("--dry-run", action="store_true", help="print commands, execute nothing")
    r.add_argument("--apps", action="store_true", help="reinstall APKs")
    r.add_argument("--reinstall-existing", action="store_true",
                   help="reinstall even apps already present")
    r.add_argument("--settings", action="store_true", help="write settings back")
    r.add_argument("--settings-mode", choices=["safe", "all"], default="safe",
                   help="'safe' restores a curated allowlist (default); 'all' is risky")
    r.add_argument("--perms", action="store_true", help="re-grant runtime permissions")
    r.add_argument("--appops", action="store_true", help="re-apply app-ops")
    r.add_argument("--battery", action="store_true", help="restore battery exemptions")
    r.add_argument("--defaults", action="store_true", help="restore default apps and IMEs")
    r.add_argument("--media", action="store_true", help="push user files back to /sdcard")
    r.add_argument("--yes", "-y", action="store_true", help="do not prompt")
    r.set_defaults(func=cmd_restore)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AdbError as e:
        err(str(e))
        return 2
    except KeyboardInterrupt:
        sys.stdout.write("\n")
        warn("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())