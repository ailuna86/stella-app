# sync-and-push-stella-app.ps1
#
# Corrects an earlier mistake: stella-app is a monorepo with "stella-frontend"
# and "full pipleine" as SIBLING folders at its root (confirmed from the
# GitHub repo page), and already has 24 real commits. An earlier script
# treated C:\dev\stella-frontend itself as the repo root, which would have
# replaced stella-app's entire root -- including the "full pipleine" folder
# -- with just the frontend. This script instead clones the REAL repo and
# copies files into the right subfolders, so it builds on top of the
# existing history instead of fighting it.
#
# Run this from PowerShell. It will:
#   1. Remove the incorrect git repo that was set up directly inside
#      C:\dev\stella-frontend (leftover from before this correction).
#   2. Clone (or update) a full local copy of stella-app into
#      C:\dev\stella-app-repo.
#   3. Copy your current stella-frontend and full-pipeline files into the
#      right subfolders of that clone (source code only -- node_modules,
#      .next, .env.local, the local SQLite db, and build artifacts are
#      excluded, same as a normal .gitignore would exclude them).
#   4. Show you what changed (git status) and PAUSE before committing/
#      pushing, so you can bail out with Ctrl+C if anything looks wrong.

$ErrorActionPreference = "Stop"

$frontendSource = "C:\dev\stella-frontend"
$pipelineSource = "C:\Users\Ailuna Shamurzaeva\OneDrive\Desktop\AGART\VA English, IELTS\gold\full pipleine"
$repoUrl        = "https://github.com/ailuna86/stella-app.git"
$repoRoot       = "C:\dev\stella-app-repo"

# --- Step 1: clean up the incorrect in-place repo ---------------------------
Write-Host "== Step 1: removing the incorrect git repo inside stella-frontend ==" -ForegroundColor Cyan
$badGit = Join-Path $frontendSource ".git"
if (Test-Path $badGit) {
    Remove-Item -Recurse -Force $badGit
    Write-Host "Removed $badGit"
} else {
    Write-Host "Nothing to remove -- already clean."
}
$oldScript = Join-Path $frontendSource "push-stella-frontend.ps1"
if (Test-Path $oldScript) { Remove-Item -Force $oldScript }

# --- Step 2: clone or update the real monorepo ------------------------------
Write-Host "`n== Step 2: cloning/updating stella-app ==" -ForegroundColor Cyan
if (Test-Path $repoRoot) {
    Set-Location $repoRoot
    git checkout main
    git pull origin main
} else {
    git clone $repoUrl $repoRoot
    Set-Location $repoRoot
}

# --- Step 3: sync files into place ------------------------------------------
Write-Host "`n== Step 3: syncing stella-frontend ==" -ForegroundColor Cyan
$frontendDest = Join-Path $repoRoot "stella-frontend"
robocopy $frontendSource $frontendDest /E `
  /XD node_modules .next .git `
  /XF ".env.local" "*.db" "*.db-journal" "*.db-wal" "*.db-shm" "tsconfig.tsbuildinfo" "npm_out.log" "stella-frontend-stitch-design-main.zip" `
  /NFL /NDL /NP
# robocopy's own exit codes 0-7 all mean "success" (8+ means a real error)
if ($LASTEXITCODE -ge 8) { throw "robocopy reported an error syncing stella-frontend (exit code $LASTEXITCODE)" }

Write-Host "`n== Step 3b: syncing full pipleine ==" -ForegroundColor Cyan
$pipelineDest = Join-Path $repoRoot "full pipleine"
robocopy $pipelineSource $pipelineDest /E /NFL /NDL /NP
if ($LASTEXITCODE -ge 8) { throw "robocopy reported an error syncing full pipleine (exit code $LASTEXITCODE)" }

# --- Step 4: review, then commit + push -------------------------------------
Set-Location $repoRoot
Write-Host "`n== Step 4: what changed ==" -ForegroundColor Cyan
git add -A
git status

Write-Host "`nReview the file list above." -ForegroundColor Yellow
$confirm = Read-Host "Type 'yes' to commit and push these changes to GitHub, anything else to stop"
if ($confirm -ne "yes") {
    Write-Host "Stopped -- nothing was committed or pushed. Changes are staged in $repoRoot if you want to inspect them further." -ForegroundColor Yellow
    return
}

git commit -m "Session update: Vocab Coach 3-source family bias (LRET + Evaluator + Practice history) and essay-topic matching, Priority Engine/Practice signal wiring, essay-submission timer, continuous LIE profile refresh"
git push origin main

Write-Host "`nDone. Check https://github.com/ailuna86/stella-app" -ForegroundColor Green
