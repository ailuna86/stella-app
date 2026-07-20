# run-dev.ps1
# Clean reinstall + start the ST.ELLA frontend dev server.
# Run this from PowerShell: right-click -> "Run with PowerShell",
# or open a terminal in this folder and type: .\run-dev.ps1
#
# If PowerShell blocks the script with an "execution policy" error, run this
# once first (in an admin PowerShell), then try again:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"

# Always run from the folder this script lives in, regardless of where it's launched from.
Set-Location -Path $PSScriptRoot

Write-Host ""
Write-Host "=== ST.ELLA frontend: clean install + dev server ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "This folder lives inside OneDrive. Before continuing, pause OneDrive" -ForegroundColor Yellow
Write-Host "syncing (tray icon, then Pause syncing, then 2 hours) so it doesn't lock" -ForegroundColor Yellow
Write-Host "files mid-install. Press Enter once you have done that (or to skip)." -ForegroundColor Yellow
Read-Host

# 1. Remove any broken previous install.
if (Test-Path "node_modules") {
    Write-Host "Removing old node_modules..." -ForegroundColor Cyan
    Remove-Item -Recurse -Force "node_modules"
}
if (Test-Path "package-lock.json") {
    Write-Host "Removing old package-lock.json..." -ForegroundColor Cyan
    Remove-Item -Force "package-lock.json"
}

# 2. Make sure .env.local exists.
if (-not (Test-Path ".env.local")) {
    if (Test-Path ".env.local.example") {
        Write-Host "Creating .env.local from .env.local.example..." -ForegroundColor Cyan
        Copy-Item ".env.local.example" ".env.local"
    } else {
        Write-Host "WARNING: no .env.local or .env.local.example found. The app may fail to start." -ForegroundColor Red
    }
}

# 3. Confirm Node version (informational only).
Write-Host ""
Write-Host "Node version in use:" -ForegroundColor Cyan
node -v

# 4. Install dependencies.
Write-Host ""
Write-Host "Running npm install..." -ForegroundColor Cyan
npm install
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "npm install failed. See the error above. Paste it back to Claude for help." -ForegroundColor Red
    exit 1
}

# 5. Start the dev server.
Write-Host ""
Write-Host "Starting dev server. Open http://localhost:3000 once it is ready." -ForegroundColor Green
Write-Host "Press Ctrl+C to stop it." -ForegroundColor Green
Write-Host ""
npm run dev
