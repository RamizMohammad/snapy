<#
    build.ps1 — one command from a clean machine to a Snapy installer.

        powershell -ExecutionPolicy Bypass -File build.ps1

    It installs every dependency it needs, fetches Android platform-tools so the
    installed app ships its own adb, freezes the app with PyInstaller, then
    compiles the Inno Setup installer.

    Flags:
        -SkipInstaller   build Snapy.exe but do not run Inno Setup
        -SkipAdb         do not bundle platform-tools (app falls back to PATH)
        -Clean           delete build/ dist/ first
#>

[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipAdb,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Good($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "!!! $msg" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------------------- #
# 1. Python
# --------------------------------------------------------------------------- #
Say "Checking Python"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if (-not $py) {
    Die "Python not found. Install Python 3.9 or newer from https://python.org and tick 'Add to PATH'."
}
$ver = & $py.Source -c "import sys;print('%d.%d'%sys.version_info[:2])"
Good "Python $ver at $($py.Source)"
$major, $minor = $ver.Split(".")
if ([int]$major -lt 3 -or ([int]$major -eq 3 -and [int]$minor -lt 9)) {
    Die "Python 3.9+ required, found $ver."
}

# --------------------------------------------------------------------------- #
# 2. Dependencies
# --------------------------------------------------------------------------- #
Say "Installing build dependencies"
& $py.Source -m pip install --upgrade --quiet pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Die "pip self-upgrade failed." }
& $py.Source -m pip install --upgrade --quiet PySide6 pyinstaller pillow
if ($LASTEXITCODE -ne 0) { Die "Could not install PySide6 / PyInstaller / Pillow." }
Good "PySide6, PyInstaller and Pillow ready"

# --------------------------------------------------------------------------- #
# 3. The CLI this GUI drives
# --------------------------------------------------------------------------- #
Say "Locating the Snapy CLI"
$cli = $null
foreach ($cand in @("phone_snapshot.py", "snapy.py",
                    "..\snapy.py", "..\phone_snapshot.py")) {
    $p = Join-Path $Root $cand
    if (Test-Path $p) { $cli = (Resolve-Path $p).Path; break }
}
if (-not $cli) {
    Die "Could not find snapy.py or phone_snapshot.py beside this folder or one level up. Snapy needs it to run."
}
Good "Using $cli"
# PyInstaller needs it at the bundle root under a name find_cli() recognises.
Copy-Item $cli (Join-Path $Root "snapy_cli_bundled.py") -Force
$bundledCli = Join-Path $Root "snapy_cli_bundled.py"
Move-Item $bundledCli (Join-Path $Root "phone_snapshot.py") -Force -ErrorAction SilentlyContinue

# --------------------------------------------------------------------------- #
# 4. Android platform-tools (adb)
# --------------------------------------------------------------------------- #
$adbDir = Join-Path $Root "vendor\platform-tools"
if ($SkipAdb) {
    Warn "Skipping adb bundle (-SkipAdb). The app will look for adb on PATH."
} elseif (Test-Path (Join-Path $adbDir "adb.exe")) {
    Good "platform-tools already vendored"
} else {
    Say "Downloading Android platform-tools"
    $zip = Join-Path $env:TEMP "platform-tools-latest-windows.zip"
    $url = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        $vendor = Join-Path $Root "vendor"
        New-Item -ItemType Directory -Force -Path $vendor | Out-Null
        Expand-Archive -Path $zip -DestinationPath $vendor -Force
        Remove-Item $zip -Force
        Good "adb bundled from Google"
    } catch {
        Warn "Download failed: $($_.Exception.Message)"
        Warn "Continuing without a bundled adb — the app will use adb from PATH."
        $SkipAdb = $true
    }
}

# --------------------------------------------------------------------------- #
# 5. Icon
# --------------------------------------------------------------------------- #
$icon = Join-Path $Root "assets\snapy.ico"
if (-not (Test-Path $icon)) {
    Say "Generating application icon"
    & $py.Source -c @"
from PIL import Image, ImageDraw
def mark(size):
    s = size*4
    im = Image.new('RGBA',(s,s),(0,0,0,0)); d = ImageDraw.Draw(im)
    d.rounded_rectangle([0,0,s-1,s-1], radius=int(s*0.235), fill=(98,139,255,255))
    w = max(2,int(s*0.075)); c = s/2; r = s*0.235
    d.ellipse([c-r,c-r,c+r,c+r], outline=(255,255,255,255), width=w)
    d.line([(c,c-r*1.95),(c,c-r*0.78)], fill=(255,255,255,255), width=w)
    d.rectangle([c-w,c-r*0.55,c+w,c+r*0.2], fill=(98,139,255,255))
    return im.resize((size,size), Image.LANCZOS)
ns=[16,20,24,32,40,48,64,96,128,256]
import os; os.makedirs('assets', exist_ok=True)
mark(256).save('assets/snapy.ico', format='ICO', sizes=[(n,n) for n in ns])
"@
    Good "assets\snapy.ico"
}

# --------------------------------------------------------------------------- #
# 6. Freeze
# --------------------------------------------------------------------------- #
if ($Clean) {
    Say "Cleaning"
    Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
}

Say "Building Snapy.exe with PyInstaller"
$args = @(
    "--noconfirm", "--clean", "--windowed",
    "--name", "Snapy",
    "--icon", $icon,
    "--add-data", "phone_snapshot.py;.",
    "--add-data", "assets;assets"
)
if (-not $SkipAdb) { $args += @("--add-data", "vendor\platform-tools;platform-tools") }
$args += "main.py"

& $py.Source -m PyInstaller @args
if ($LASTEXITCODE -ne 0) { Die "PyInstaller failed." }
if (-not (Test-Path "dist\Snapy\Snapy.exe")) { Die "dist\Snapy\Snapy.exe was not produced." }
$size = "{0:N1} MB" -f ((Get-ChildItem -Recurse dist\Snapy | Measure-Object Length -Sum).Sum / 1MB)
Good "dist\Snapy\Snapy.exe  ($size total)"

# --------------------------------------------------------------------------- #
# 7. Installer
# --------------------------------------------------------------------------- #
if ($SkipInstaller) { Say "Done (installer skipped)"; exit 0 }

Say "Compiling the installer with Inno Setup"
$iscc = $null
foreach ($c in @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 5\ISCC.exe")) {
    if (Test-Path $c) { $iscc = $c; break }
}
if (-not $iscc) { $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source }

if (-not $iscc) {
    Warn "Inno Setup not found."
    Warn "Install it with:  winget install -e --id JRSoftware.InnoSetup"
    Warn "then re-run this script. dist\Snapy\ is already built and runnable."
    exit 0
}

& $iscc "installer\snapy.iss"
if ($LASTEXITCODE -ne 0) { Die "Inno Setup failed." }

$out = Get-ChildItem "installer\Output\*.exe" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    Good "Installer: $($out.FullName)"
    Good ("{0:N1} MB" -f ($out.Length / 1MB))
} else {
    Warn "Inno Setup reported success but no .exe was found in installer\Output."
}
Say "Done"
