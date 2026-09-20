# Snapy desktop

PySide6 front end for the `phone_snapshot` CLI. Dark, Loopax-branded, built from
the Stitch reference design.

```powershell
pip install PySide6
cd "J:\Evil Brain\snapy\desktop"
python main.py
```

It finds the CLI automatically — `snapy.py` or `phone_snapshot.py`, beside this
folder or one level up. Nothing else to configure.

## How it fits together

```
main.py       window shell, sidebar, status bar, console drawer, signal wiring
backend.py    the only file that knows the CLI exists
pages.py      the seven screens
widgets.py    shared components (cards, badges, ring, toggles, step rows, console)
icons.py      inline SVG stroke icons, tinted at render time
theme.py      colour, type and geometry tokens + global stylesheet
```

**`backend.py` is the seam.** It does two different things on purpose:

- Structured reads (device identity, capability probe, snapshot listings, diff
  results) import the CLI module and call into it on a worker thread. No
  subprocess, no output parsing, no guessing.
- Long operations run as a `QProcess` so they stream live, stay cancellable and
  never block the UI. Their stdout is parsed into Qt signals — `phase_changed`,
  `progress`, `log_line`, `job_finished` — that the pages bind to.

The CLI stays the single source of truth. Every button builds a real command and
shows it in the console before running it, so anything the UI can do you can also
type yourself. `capture_args()`, `diff_args()` and `restore_args()` are the only
places that know the CLI's flags — change a flag there, once.

## Appearance

Settings → Appearance switches between **Dark** and **Light**. Colours are read
when widgets are constructed, not on every repaint, so changing the theme rebuilds
the window — that happens automatically and takes a blink. If a job is running the
switch waits until it finishes rather than tearing down a live capture.

Both palettes live in `_Palettes` in `theme.py`. Light is not an inversion of dark;
it has its own contrast budget. To add a third, copy a dict, give it a name, and it
appears wherever the theme is read.

## Things worth knowing

**Capture progress** is derived, not reported. The CLI prints section headings;
`CAPTURE_STEPS` in `backend.py` maps those onto the ten steps the UI shows, and
`step_index_for()` resolves a heading to an index. Percentage is step position
plus within-step progress from the CLI's `n/total` counters. If you add or rename
a section in the CLI, update that table.

**Resizing.** Minimum is 880 x 600 — the floor where the layout still holds
together. Above that there is no ceiling. Rather than enforcing a large floor,
the shell adapts: below 1120px wide the sidebar collapses to a 64px icon rail
(`Sidebar.set_compact()`, driven from `MainWindow.resizeEvent`), the phone glyph
scales down, and the snapshot detail panel flexes between 292 and 360px. Change
the threshold with `MainWindow.COMPACT_AT`.

**The window is frameless**, matching the design. That removes the OS title bar
*and* the OS resize borders, so both are reimplemented: `_edges_at()` in `main.py`
detects the 7px grab zone on each edge and corner, and the drag itself is handed to
the window manager via `startSystemResize()` / `startSystemMove()` rather than moved
by hand — which keeps Windows snapping, multi-monitor and DPI changes behaving
natively. Resizing is disabled while maximised.

Two things a frameless window needs that are easy to miss, both handled in
`MainWindow`: `WA_StyledBackground` (without it a plain QWidget subclass ignores
its QSS background *and border*, so the frame is never drawn), and a 1px root
layout inset so child widgets do not paint over that border. On Windows it also
asks DWM for the native drop shadow via `DwmExtendFrameIntoClientArea`, which is
what makes the app read as a window against a dark desktop.

Set **`NATIVE_TITLEBAR = True`** at the top of `main.py` for the system title bar
instead. You lose the branded bar but regain the Windows 11 snap-layout flyout on
the maximise button.

**Dry run defaults to on** for Restore and for Compare's fix action. Turning it
off prompts before anything touches the phone.

**The phone on the Device page is live.** `PhoneGlyph.set_screen()` takes PNG
bytes from `Backend.grab_screen()` (`adb exec-out screencap -p`, on a worker
thread) and draws the real home screen clipped to the handset's rounded screen
rect. It falls back to the placeholder glyph whenever there is no device or the
screen is locked — the magic bytes are checked, because a refusing device
returns an error string on stdout that would otherwise be drawn as a frame. It
refreshes whenever a device is detected and on the Refresh button.

**Custom-painted widgets** (QSS can't do these): `Ring`, `Toggle`, `SegmentBar`,
`PhoneGlyph`, `StepRow`, `_RadioDot`. Each is a short `QPainter` implementation
in `widgets.py` — copy the pattern if you need another.

**Settings** live in `snapy_desktop.json` beside `main.py`, written on close and
on Save.

## Building an installer

One command, from a clean machine:

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

`build.ps1` does everything: checks for Python 3.9+, installs PySide6,
PyInstaller and Pillow, locates the CLI (beside the folder or one level up),
downloads Android platform-tools so the installed app **ships its own adb**,
generates the icon if missing, freezes the app, then compiles
`installer\snapy.iss` with Inno Setup. The result lands in
`installer\Output\Snapy-1.0.0-setup.exe`.

Flags: `-SkipInstaller` (exe only), `-SkipAdb` (use adb from PATH instead of
bundling it), `-Clean` (wipe build/ and dist/ first).

If Inno Setup is missing the script says so and stops cleanly — `dist\Snapy\`
is already built and runnable. Install it with:

```powershell
winget install -e --id JRSoftware.InnoSetup
```

The installer is **per-user by default**, so it raises no UAC prompt; the user
can switch to all-users in the wizard. Uninstalling also removes
`%LOCALAPPDATA%\Snapy`, where an installed build keeps its settings — Program
Files is not writable, so `Settings` relocates automatically when frozen
(`_config_path()` in `backend.py`). The same logic makes `find_cli()` and
`bundled_adb()` look next to the .exe and inside the PyInstaller bundle, so an
installed Snapy needs nothing on PATH.
