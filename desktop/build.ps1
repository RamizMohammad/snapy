<#
    build.ps1 - one command from a clean machine to a Snapy installer.

        powershell -ExecutionPolicy Bypass -File build.ps1

    Works in Windows PowerShell 5.1 and PowerShell 7. Installs every dependency
    it needs, fetches Android platform-tools so the installed app ships its own
    adb, freezes the app with PyInstaller, then compiles the Inno Setup
    installer.

    Flags:
        -SkipInstaller   build Snapy.exe but do not run Inno Setup
        -SkipAdb         do not bundle platform-tools (app falls back to PATH)
        -Clean           delete build/ and dist/ first

    Note for editors: keep this file CRLF and avoid here-strings. Windows
    PowerShell 5.1 cannot terminate a here-string in an LF-only file.
#>

[CmdletBinding()]
param(
    [switch]$SkipInstaller,
    [switch]$SkipAdb,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $Root

function Say($msg)  { Write-Host "==> $msg" -ForegroundColor Cyan }
function Good($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn2($msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "!!! $msg" -ForegroundColor Red; exit 1 }

# --------------------------------------------------------------------------- #
# 1. Python
# --------------------------------------------------------------------------- #
Say 'Checking Python'
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pyCmd) { $pyCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pyCmd) { $pyCmd = Get-Command py -ErrorAction SilentlyContinue }
if (-not $pyCmd) {
    Die 'Python not found. Install Python 3.9+ from https://python.org and tick "Add python.exe to PATH".'
}
$Py = $pyCmd.Source
# No double quotes inside these arguments. Windows PowerShell 5.1 strips
# embedded quotes when passing arguments to a native executable, so
# 'print("%d.%d" % ...)' reaches Python as 'print(%d.%d % ...)' and fails.
$pyMajor = & $Py -c 'import sys;print(sys.version_info[0])'
if ($LASTEXITCODE -ne 0) { Die 'Could not run Python.' }
$pyMinor = & $Py -c 'import sys;print(sys.version_info[1])'
if ($LASTEXITCODE -ne 0) { Die 'Could not run Python.' }
$pyMajor = [int]($pyMajor.Trim())
$pyMinor = [int]($pyMinor.Trim())
$ver = "$pyMajor.$pyMinor"
if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 9)) {
    Die "Python 3.9 or newer required, found $ver."
}
Good "Python $ver at $Py"

# --------------------------------------------------------------------------- #
# 2. Dependencies
# --------------------------------------------------------------------------- #
Say 'Installing build dependencies'
# Best-effort only. System and conda Pythons often cannot replace their own
# pip/setuptools/wheel, and that has nothing to do with whether we can build.
& $Py -m pip install --upgrade --quiet pip setuptools wheel 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Warn2 'Could not upgrade pip itself - continuing.' }

& $Py -m pip install --upgrade --quiet PySide6 pyinstaller pillow
if ($LASTEXITCODE -ne 0) {
    Warn2 'Upgrade install failed; retrying without --upgrade.'
    & $Py -m pip install --quiet PySide6 pyinstaller pillow
}
# Trust the imports, not pip's exit code.
& $Py -c 'import PySide6, PyInstaller, PIL' 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Die 'PySide6, PyInstaller or Pillow is still missing. Run:  python -m pip install PySide6 pyinstaller pillow'
}
Good 'PySide6, PyInstaller and Pillow ready'

# --------------------------------------------------------------------------- #
# 3. The CLI this GUI drives
# --------------------------------------------------------------------------- #
Say 'Locating the Snapy CLI'
$cli = $null
$candidates = @('phone_snapshot.py', 'snapy.py', '..\snapy.py', '..\phone_snapshot.py')
foreach ($cand in $candidates) {
    $p = Join-Path $Root $cand
    if (Test-Path $p) { $cli = (Resolve-Path $p).Path; break }
}
if (-not $cli) {
    Die 'Could not find snapy.py or phone_snapshot.py in this folder or one level up. Snapy needs it to run.'
}
Good "Using $cli"

# PyInstaller bundles it at the root under a name find_cli() recognises.
$staged = Join-Path $Root 'phone_snapshot.py'
if ($cli -ne $staged) {
    Copy-Item -LiteralPath $cli -Destination $staged -Force
    Good 'Staged as phone_snapshot.py for bundling'
}

# --------------------------------------------------------------------------- #
# 4. Android platform-tools (adb)
# --------------------------------------------------------------------------- #
$vendor  = Join-Path $Root 'vendor'
$adbExe  = Join-Path $vendor 'platform-tools\adb.exe'
if ($SkipAdb) {
    Warn2 'Skipping adb bundle (-SkipAdb). The app will look for adb on PATH.'
} elseif (Test-Path $adbExe) {
    Good 'platform-tools already vendored'
} else {
    Say 'Downloading Android platform-tools'
    $zip = Join-Path $env:TEMP 'platform-tools-latest-windows.zip'
    $url = 'https://dl.google.com/android/repository/platform-tools-latest-windows.zip'
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
        New-Item -ItemType Directory -Force -Path $vendor | Out-Null
        Expand-Archive -LiteralPath $zip -DestinationPath $vendor -Force
        Remove-Item -LiteralPath $zip -Force
        if (Test-Path $adbExe) { Good 'adb bundled from Google' }
        else { Warn2 'Archive extracted but adb.exe not found; falling back to PATH.'; $SkipAdb = $true }
    } catch {
        Warn2 ('Download failed: ' + $_.Exception.Message)
        Warn2 'Continuing without a bundled adb - the app will use adb from PATH.'
        $SkipAdb = $true
    }
}

# --------------------------------------------------------------------------- #
# 5. Icon
# --------------------------------------------------------------------------- #
$icon = Join-Path $Root 'assets\snapy.ico'
if (-not (Test-Path $icon)) {
    Say 'Generating application icon'
    & $Py (Join-Path $Root 'tools\make_icon.py') (Join-Path $Root 'assets')
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $icon)) { Die 'Icon generation failed.' }
    Good 'assets\snapy.ico'
}

# Wizard banner and header mark for Inno Setup. These live under installer\,
# not assets\, so PyInstaller does not ship the installer's artwork inside the
# application.
$artDir = Join-Path $Root 'installer\art'
$banner = Join-Path $artDir 'wizard-164x314.bmp'
if (-not $SkipInstaller -and -not (Test-Path $banner)) {
    Say 'Generating installer artwork'
    & $Py (Join-Path $Root 'tools\make_installer_art.py') $artDir
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $banner)) {
        Die 'Installer artwork generation failed.'
    }
    Good 'installer\art\*.bmp'
}

# --------------------------------------------------------------------------- #
# 6. Freeze
# --------------------------------------------------------------------------- #
if ($Clean) {
    Say 'Cleaning previous build'
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root 'build')
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $Root 'dist')
}

Say 'Building Snapy.exe with PyInstaller'
# --add-data uses ';' on Windows and ':' elsewhere. $IsWindows does not exist in
# Windows PowerShell 5.1, where the answer is always Windows.
$sep = ';'
$isWinVar = Get-Variable -Name IsWindows -ErrorAction SilentlyContinue
if ($isWinVar -and -not $isWinVar.Value) { $sep = ':' }
# Not named $args: that is an automatic variable in PowerShell.
$piArgs = @(
    '--noconfirm', '--clean', '--windowed',
    '--name', 'Snapy',
    '--icon', $icon,
    # The CLI must be analysed as code, not shipped as data: its stdlib imports
    # (tarfile, argparse, hashlib...) are only bundled if PyInstaller sees them.
    '--hidden-import', 'phone_snapshot',
    '--add-data', "phone_snapshot.py$sep.",
    '--add-data', "assets${sep}assets"
)
if (-not $SkipAdb) { $piArgs += @('--add-data', "vendor\platform-tools${sep}platform-tools") }
$piArgs += 'main.py'

& $Py -m PyInstaller @piArgs
if ($LASTEXITCODE -ne 0) { Die 'PyInstaller failed.' }

$exeName = 'Snapy.exe'
if ($sep -eq ':') { $exeName = 'Snapy' }   # non-Windows build, for testing
$exe = Join-Path $Root ('dist\Snapy\' + $exeName)
if (-not (Test-Path $exe)) { Die ('dist\Snapy\' + $exeName + ' was not produced.') }
$bytes = (Get-ChildItem -Recurse (Join-Path $Root 'dist\Snapy') | Measure-Object -Property Length -Sum).Sum
Good ('dist\Snapy\Snapy.exe  ({0:N1} MB total)' -f ($bytes / 1MB))

# --------------------------------------------------------------------------- #
# 7. Installer
# --------------------------------------------------------------------------- #
if ($SkipInstaller) { Say 'Done (installer skipped)'; exit 0 }

Say 'Compiling the installer with Inno Setup'
$iscc = $null
$isccPaths = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
    (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 5\ISCC.exe')
)
foreach ($c in $isccPaths) {
    if ($c -and (Test-Path $c)) { $iscc = $c; break }
}
if (-not $iscc) {
    $found = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($found) { $iscc = $found.Source }
}

if (-not $iscc) {
    Warn2 'Inno Setup not found.'
    Warn2 'Install it with:  winget install -e --id JRSoftware.InnoSetup'
    Warn2 'then re-run this script. dist\Snapy\ is already built and runnable.'
    exit 0
}

& $iscc (Join-Path $Root 'installer\snapy.iss')
if ($LASTEXITCODE -ne 0) { Die 'Inno Setup failed.' }

$out = Get-ChildItem (Join-Path $Root 'installer\Output\*.exe') -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($out) {
    Good ('Installer: ' + $out.FullName)
    Good ('{0:N1} MB' -f ($out.Length / 1MB))
} else {
    Warn2 'Inno Setup reported success but no .exe was found in installer\Output.'
}
Say 'Done'
