# Physiology Analysis Tools - first-time setup for Windows.
#
# Start it by double-clicking install.bat (in this same folder). It checks
# for Python 3.12 and Node.js (and offers to install them), then installs
# everything the app needs into this folder. Safe to run again: it only
# adds what is missing.

$ErrorActionPreference = 'Continue'
# This script lives in the installation folder; the app folder is one up.
$AppRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $AppRoot

$PythonVersion = '3.12'
$MinNode = [version]'22.12.0'
$Steps = 5

function Step($number, $text) {
    Write-Host ''
    Write-Host "[$number/$Steps] $text" -ForegroundColor Cyan
}
function Ok($text) { Write-Host "  OK   $text" -ForegroundColor Green }
function Info($text) { Write-Host "       $text" }
function Warn($text) { Write-Host "  !    $text" -ForegroundColor Yellow }
function Fail($text) {
    Write-Host ''
    Write-Host "  X    $text" -ForegroundColor Red
    Write-Host ''
    Write-Host 'Setup did not finish. Fix the problem above, then double-click installation\install.bat again.' -ForegroundColor Red
    Write-Host 'Everything that already worked is kept, so the next run is quicker.'
    exit 1
}
function Ask($question) {
    $answer = Read-Host "       $question [Y/n]"
    return ($answer -eq '' -or $answer -match '^(y|yes)$')
}

# Programs installed a moment ago are not on this window's PATH yet.
function Update-PathFromSystem {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# The command that runs Python 3.12 (as an array: program, then arguments), or $null.
function Find-Python {
    $candidates = @(
        @('py', '-3.12'),
        @('python3.12'),
        @('python'),
        # Where the python.org installer puts it, in case PATH was not updated.
        @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"),
        @("$env:ProgramFiles\Python312\python.exe")
    )
    foreach ($candidate in $candidates) {
        $program = $candidate[0]
        $extra = @($candidate | Select-Object -Skip 1)
        if (-not (Test-Command $program)) { continue }
        $version = & $program @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -eq 0 -and "$version".Trim() -eq $PythonVersion) { return , $candidate }
    }
    return $null
}

function Get-NodeVersion {
    if (-not (Test-Command 'node')) { return $null }
    $text = & node --version 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $text) { return $null }
    try { return [version]("$text".Trim().TrimStart('v')) } catch { return $null }
}

# Installs a program with winget (built into Windows 10 and 11). Returns $true if it ran.
function Install-WithWinget($id, $name) {
    if (-not (Test-Command 'winget')) {
        Warn 'This computer cannot install it automatically (winget is not available).'
        return $false
    }
    if (-not (Ask "Install $name now? Windows may ask for permission.")) { return $false }
    Info "Installing $name. This can take a few minutes..."
    & winget install --id $id -e --source winget --accept-package-agreements --accept-source-agreements
    Update-PathFromSystem
    return $true
}

Write-Host ''
Write-Host '==============================================' -ForegroundColor Cyan
Write-Host '  Physiology Analysis Tools - first-time setup' -ForegroundColor Cyan
Write-Host '==============================================' -ForegroundColor Cyan
Info "Folder: $AppRoot"

if (-not (Test-Path 'package.json') -or -not (Test-Path 'backend')) {
    Fail 'This is not the Physiology-Analysis-Tools folder. Keep the installation folder inside the app folder and run install.bat from there.'
}

# ---------------------------------------------------------------------------
Step 1 "Checking for Python $PythonVersion"
$python = Find-Python
if (-not $python) {
    Warn "Python $PythonVersion was not found."
    if (Install-WithWinget 'Python.Python.3.12' "Python $PythonVersion") { $python = Find-Python }
}
if (-not $python) {
    Write-Host ''
    Info 'Please install Python 3.12 yourself:'
    Info '  1. Go to https://www.python.org/downloads/windows/'
    Info '  2. Download the latest "Python 3.12" Windows installer (64-bit).'
    Info '  3. Run it and keep "Use admin privileges when installing py.exe" ticked.'
    Info '  4. Close this window and double-click installation\install.bat again.'
    Fail "Python $PythonVersion is needed."
}
Ok "Python $PythonVersion found ($($python -join ' '))"

# ---------------------------------------------------------------------------
Step 2 'Checking for Node.js'
$node = Get-NodeVersion
if (-not $node -or $node -lt $MinNode) {
    if ($node) { Warn "Node.js $node is too old (version $MinNode or newer is needed)." }
    else { Warn 'Node.js was not found.' }
    if (Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js (LTS)') { $node = Get-NodeVersion }
}
if (-not $node -or $node -lt $MinNode -or -not (Test-Command 'npm')) {
    Write-Host ''
    Info 'Please install Node.js yourself:'
    Info '  1. Go to https://nodejs.org/'
    Info '  2. Download the "LTS" version for Windows and run the installer (the default options are fine).'
    Info '  3. Close this window and double-click installation\install.bat again.'
    Fail "Node.js $MinNode or newer is needed."
}
Ok "Node.js $node found"

# ---------------------------------------------------------------------------
Step 3 'Installing the Python packages (analysis engine)'
$venvPython = Join-Path $AppRoot '.venv\Scripts\python.exe'
if (Test-Path $venvPython) {
    $venvVersion = & $venvPython -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ("$venvVersion".Trim() -ne $PythonVersion) {
        Warn "The existing .venv folder uses a different Python version ($venvVersion). Making a new one."
        Remove-Item -Recurse -Force '.venv'
    }
}
if (-not (Test-Path $venvPython)) {
    Info 'Creating the private Python environment (.venv folder)...'
    $program = $python[0]
    $extra = @($python | Select-Object -Skip 1)
    & $program @extra -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPython)) { Fail 'Could not create the Python environment (.venv).' }
}
Ok 'Python environment ready (.venv)'
Info 'Downloading and installing packages. The first time this is several hundred MB'
Info 'and can take 5-15 minutes. Lots of text scrolling past is normal.'
& $venvPython -m pip install --upgrade pip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { Fail 'Could not update pip. Check your internet connection.' }
& $venvPython -m pip install --disable-pip-version-check -e . -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { Fail 'Installing the Python packages failed. Check your internet connection; the error is shown above.' }
Ok 'Python packages installed'

# ---------------------------------------------------------------------------
Step 4 'Installing the app packages (window and screens)'
Info 'Downloading and installing packages, including the app window (about 300 MB).'
Info 'This can take a few minutes.'
& npm install --no-fund --no-audit
if ($LASTEXITCODE -ne 0) { Fail 'Installing the app packages failed. Check your internet connection; the error is shown above.' }
Ok 'App packages installed'

# ---------------------------------------------------------------------------
Step 5 'Done'
Write-Host ''
Write-Host '  Setup is complete.' -ForegroundColor Green
Write-Host ''
Info 'To start the app from now on: double-click start-app.bat in this folder.'
Info 'The very first start can take a minute or two.'
Write-Host ''
if (Ask 'Start the app now?') {
    Info 'Starting... keep this window open while you use the app.'
    & npm run dev
}
