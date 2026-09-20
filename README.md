# phone-snapshot

Capture a restorable snapshot of a **stock, non-rooted Android phone** before a service
centre reflashes it, then rebuild the phone from that snapshot afterwards.

Single Python file, standard library only. Needs `adb`.

---

## Read this first: what is actually possible

There is a hard line in Android that no tool crosses without root.

| | Capturable over ADB | How |
|---|---|---|
| Your APKs, exact versions, split APKs | **Yes** | pulled to `02_apps/apk/` |
| Full app inventory + install source + dates | **Yes** | `02_apps/apps_user.json` |
| Every runtime permission grant | **Yes** | re-granted on restore |
| App-ops, battery exemptions, default apps, IMEs | **Yes** | re-applied on restore |
| Every system / secure / global setting | **Yes** | `01_device_config/settings.json` |
| Saved Wi-Fi **SSIDs** and security type | **Yes** | passwords are root-only |
| Accounts on the device (name + type) | **Yes** | credentials are not |
| All your files on internal storage | **Yes** | `05_media/` |
| SMS, call log, calendar | **Usually** | via content providers |
| Home-screen layout | **As screenshots** | `07_screens/` |
| **Private app data** (`/data/data`) — logins, chats, app databases | **No** | see below |
| **Wi-Fi passwords** | **No** | root only |

`adb backup` used to cover private app data. Google gutted it in Android 12 and removed it
in practice — on a modern Pixel-class device it produces an empty archive. The only
supported route left is Google's own cloud backup, so this tool can trigger one for you
(`--force-cloud-backup`) and tells you which apps it will not cover.

That last part is the bit that actually saves people. `09_reports/PRE-WIPE-CHECKLIST.md`
is generated from *your* installed apps and lists exactly which ones you must export by
hand — authenticators first, then Signal/WhatsApp, then banking and UPI apps whose device
binding resets on reinstall.

---

## The desktop app

`snapy_gui.py` is a Tkinter window over everything below. Keep it next to the CLI
(`phone_snapshot.py` or `snapy.py` — it finds either) and run:

```powershell
python snapy_gui.py
```

Six tabs:

- **Device** — live status cards (model, Android, patch level, free space, app count),
  adb path picker, and the full capability probe
- **Capture** — every option as a labelled control, with the trade-off spelled out under
  each one. Defaults to `sideloaded` APKs and cloud backup on
- **Snapshots** — every snapshot in your output folder with date, device, app count and
  size, read from each manifest. Verify and Archive from here
- **Compare** — runs `diff` and renders the result as an expandable tree, green for
  clean and red for gaps, with the actual missing items listed underneath
- **Restore** — checkboxes per subsystem, safe/all settings toggle, dry-run on by default,
  and a reminder of the order that matters
- **Pre-wipe checklist** — the generated checklist as real tick-boxes, with a progress
  bar. Your ticks are saved into the snapshot folder, so closing the app doesn't lose them

Long operations run as subprocesses, so output streams live into the console pane at the
bottom with the same colour coding as the CLI, progress counters update in place, and
Stop actually stops. Nothing the GUI does is unavailable from the command line — it builds
the same commands and shows you each one before it runs.

## Setup (Windows)

1. **Install platform-tools** if you don't have it:
   download <https://developer.android.com/tools/releases/platform-tools>, unzip to
   e.g. `C:\platform-tools`, and add that folder to PATH.
   The tool also auto-detects `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`.

2. **On the phone**: Settings → About phone → tap *Build number* 7 times →
   back → System → Developer options → enable **USB debugging**.

3. Plug in over USB, set USB mode to **File transfer**, and accept the
   *Allow USB debugging?* prompt (tick "Always allow").

4. Check everything is wired up:

```powershell
python phone_snapshot.py doctor
```

`doctor` probes each capability separately and tells you what this particular build
allows — some OEMs block the SMS provider, some block `/sdcard/Android/data`.

---

## Capture

```powershell
python phone_snapshot.py capture --out D:\phone-backup --force-cloud-backup
```

Keep the screen unlocked while it runs. Expect a few minutes plus however long your
photos take to copy.

Useful flags:

| Flag | Effect |
|---|---|
| `--force-cloud-backup` | runs `bmgr backupnow --all` — **do this**, it's the only path to private app data |
| `--include-android-data` | also try `/sdcard/Android/data` + `obb` (slow, often refused on Android 11+) |
| `--skip-media` | inventory + config only, no photos/files |
| `--apks-mode sideloaded` | store APKs **only** for apps not from Play Store. Usually cuts the snapshot by 80–95% — Play reinstalls the rest for you, and the excluded list is written to `02_apps/play_reinstall_list.txt` |
| `--skip-apks` | don't pull APKs at all (same as `--apks-mode none`) |
| `--home-screens N` | screenshot N launcher pages (default 6, `0` to disable) |
| `--workers N` | parallel adb workers (default 4) |
| `--no-hash` | skip SHA-256 in the manifest |

Then verify and archive:

```powershell
python phone_snapshot.py verify  --snapshot D:\phone-backup\snapshot_Pixel-7_20260919-2140
python phone_snapshot.py archive --snapshot D:\phone-backup\snapshot_... --encrypt
```

On a large snapshot (APKs, JPEGs, video — all already compressed) gzip costs hours and
saves nothing. Use `--compresslevel 0` for a store-only `.tar`; the tool warns you about
this above 5 GB.

`--encrypt` uses whichever of gpg / 7-Zip / OpenSSL is installed and prompts for a
passphrase. **Encrypt before it leaves your machine** — the snapshot contains your SMS,
call log, account emails and every file on the phone.

Optional off-site copy:

```powershell
python phone_snapshot.py upload --archive D:\phone-backup\snapshot_....tar.gz.gpg ^
    --dest scp://ramiz@your-vps:/srv/backups/ --identity C:\Users\you\.ssh\id_ed25519
```

`--dest` also accepts `rsync://user@host:/path/` and `rclone:remote:path`.

---

## Restore, after the phone comes back

**Order matters.**

1. At the setup wizard, choose **Restore from cloud backup** and pick this device.
   Do this first — it brings back private app data that nothing else can.
2. Finish setup, connect Wi-Fi, let Play Store finish.
3. Re-enable USB debugging, plug into the PC.
4. Preview what the tool will do, then run it:

```powershell
python phone_snapshot.py restore --snapshot D:\phone-backup\snapshot_... --dry-run
python phone_snapshot.py restore --snapshot D:\phone-backup\snapshot_... --apps --settings --perms --battery --defaults
```

5. Push your files back:

```powershell
python phone_snapshot.py restore --snapshot D:\phone-backup\snapshot_... --media
```

   `--media` pushes everything back and then triggers a MediaStore rescan, because
   pushed files stay invisible to Gallery and Files until the index catches up. If the
   rescan command isn't available on your build, rebooting the phone does the same job.

6. Rebuild the home screen from `07_screens/`, re-pair Bluetooth, re-register UPI apps.

### About `--settings-mode`

Default is `safe`: only a curated allowlist of ~70 keys is written back (brightness,
timeouts, animation scales, gesture navigation, night light, font scale, ringtone,
device name, and so on). Everything is still *captured* — but blanket-writing every
`secure`/`global` key onto a fresh install is how people end up with a broken Settings
app, because many of those keys encode provisioning state and device identity.
`--settings-mode all` exists; think twice before using it.

Permission grants that fail during restore are normal — a handful of permissions are not
grantable over adb. Failures are logged to `09_reports/restore_failures.txt`.

---

## Checking the phone against the backup

`verify` checks the **backup**. `diff` checks the **phone** — it walks the connected
device and reports everything present in the snapshot but missing from the phone.

```powershell
python snapy.py diff --snapshot "J:\Evil Brain\snapy\snapshot_A059_..."
```

It compares:

- **apps** — missing, and whether each one can be reinstalled from this snapshot's APKs
  or needs Play Store; also flags apps at a different version, and apps on the phone that
  aren't in the snapshot (reported, never touched)
- **runtime permissions** — granted in the snapshot, not granted now
- **settings** — the safe-restorable keys whose live value differs
- **battery exemptions, default-app roles, enabled keyboards**
- **media** — every file under `05_media\`, by path and by size, so a push that was cut
  short shows up as a size mismatch rather than passing silently
- **SMS / call log / contacts row counts** with `--deep`

You get a coloured gap summary, a `DIFF_<timestamp>.md` you can read, and a matching
JSON. Exit code is `0` when the phone matches, `1` when it doesn't — so it scripts.

To close the gaps:

```powershell
python snapy.py diff --snapshot "<dir>" --fix --dry-run    # preview every command
python snapy.py diff --snapshot "<dir>" --fix              # do it
```

`--fix` installs only the missing apps, grants only the missing permissions, writes only
the settings that differ, and pushes only the missing files — then re-indexes MediaStore.
Add `--repush-mismatched` to also re-send files whose size doesn't match. Nothing is ever
deleted or downgraded.

Useful flags: `--skip-media` (much faster when you only care about apps and config),
`--deep` (row counts), `-y` (no prompts).

This is the command to run after a restore, and again a day later once Play Store has
finished its own background reinstalls.

## When apps refuse to install

Restore now diagnoses each install failure instead of dumping raw adb output, and writes
`09_reports/install_failures.md` with the cause and the fix per app.

Two categories you can ignore entirely:

- **`org.chromium.webapk.*`** — minted on-device by Play Services when you add a website
  to your home screen, signed with a key unique to that install. They cannot be sideloaded
  and hold no data. Re-add the site from Chrome and a new one appears. These are now
  excluded from both capture and restore.
- **Permission grants for apps that aren't installed** — pure cascade from a failed
  install. Restore now checks the installed list first and skips them.

Real failures, and what they mean:

| Code | Cause | Fix |
|---|---|---|
| `INSTALL_FAILED_TEST_ONLY` | APK built by Android Studio's Run button | retried automatically with `-t`; otherwise build a release APK |
| `INSTALL_FAILED_DEPRECATED_SDK_VERSION` | `targetSdk` too old for this Android | retried with `--bypass-low-target-sdk-block`; real fix is to bump targetSdk |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | native libs don't match this CPU | reinstall from Play Store, or build a universal APK |
| `INSTALL_FAILED_MISSING_SPLIT` / `INVALID_APK` | split-APK app, not all splits present | reinstall from Play Store |
| `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | pulled APK has no valid signature block | reinstall from Play Store or rebuild |
| `INSTALL_FAILED_USER_RESTRICTED` | phone refuses installs over USB | Developer options → enable **Install via USB** |
| `INSTALL_FAILED_VERIFICATION_FAILURE` | Play Protect blocked it | temporarily disable Play Protect scanning |

To see the untruncated reason for one app yourself:

```powershell
adb install -r -d -t "<snapshot>\02_apps\apk\<package>\base.apk"
```

## Snapshot layout

```
snapshot_<model>_<timestamp>/
  00_meta/            device.json, manifest.json (SHA-256 per file), cloud backup status
  01_device_config/   settings.json, settings_restorable.json, properties.json,
                      accounts.json, wifi_saved_networks.txt
  02_apps/            apps_user.json, apps_system.json, play_store_links.txt,
                      apk/<package>/base.apk + split_config.*.apk
  03_app_state/       permissions.json, appops.json, battery_whitelist.json,
                      default_apps.json, input_methods.json, accessibility_and_listeners.json
  04_personal_data/   sms.txt, call_log.txt, calendar_*.txt
  05_media/           DCIM, Download, WhatsApp, Pictures, ... as they were on the phone
  06_android_data/    /sdcard/Android/data + obb, if the build allowed it
  07_screens/         home_01.png ... app_drawer.png
  08_raw_dumps/       unparsed getprop + dumpsys output, for anything the parser missed
  09_reports/         PRE-WIPE-CHECKLIST.md  <- read this one
                      RESTORE.md, SNAPSHOT_REPORT.md
```

`08_raw_dumps/` is deliberately kept: if six months from now you need something the
parser didn't model, the original output is still there.

---

## Notes

- Nothing is written to the phone during `capture` except `input swipe`/`keyevent` for the
  home-screen screenshots and, if you ask for it, the `bmgr` backup trigger.
- The snapshot is plaintext personal data. Treat the folder like a password vault.
- Works on Android 8 through 15. Older builds expose more (`adb backup`), newer ones less.
- Multiple devices connected: pass `--serial` (see `adb devices`).
