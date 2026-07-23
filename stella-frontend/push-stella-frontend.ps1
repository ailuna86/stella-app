# push-stella-frontend.ps1
# Run this from inside C:\dev\stella-frontend (or it cd's there itself below).
#
# Context: this folder was just git-initialized and given one commit
# ("Initial commit: ST.ELLA Gold pipeline frontend") covering everything
# built so far, with origin set to https://github.com/ailuna86/stella-app.git.
# Since stella-app is already connected to Render, it likely already has
# commits on GitHub -- this script checks that BEFORE pushing, instead of
# assuming the remote is empty, so it won't silently clobber anything.

$ErrorActionPreference = "Stop"
Set-Location "C:\dev\stella-frontend"

Write-Host "== Remote =="
git remote -v

Write-Host "`n== Fetching from GitHub (you may be prompted to sign in) =="
git fetch origin

$remoteMain = git rev-parse --verify --quiet origin/main
$remoteMaster = git rev-parse --verify --quiet origin/master

if (-not $remoteMain -and -not $remoteMaster) {
    Write-Host "`nRemote has no main/master branch yet -- pushing directly." -ForegroundColor Green
    git push -u origin main
    Write-Host "`nDone. Check https://github.com/ailuna86/stella-app" -ForegroundColor Green
    return
}

$remoteBranch = if ($remoteMain) { "main" } else { "master" }
Write-Host "`nRemote already has commits on '$remoteBranch'." -ForegroundColor Yellow
Write-Host "This local repo was just initialized today, so it has NO shared history"
Write-Host "with whatever is already on GitHub. You have two real options -- pick ONE:"
Write-Host ""
Write-Host "  OPTION A -- replace what's on GitHub with this local snapshot" -ForegroundColor Cyan
Write-Host "  (only do this if the GitHub repo is stale/not the real source of truth):"
Write-Host "    git push origin main:$remoteBranch --force"
Write-Host ""
Write-Host "  OPTION B -- keep GitHub's history, add this as a new commit on top" -ForegroundColor Cyan
Write-Host "  (safer default if you're not sure what's currently on GitHub):"
Write-Host "    git merge origin/$remoteBranch --allow-unrelated-histories"
Write-Host "    # resolve any conflicts if prompted, then:"
Write-Host "    git push -u origin main:$remoteBranch"
Write-Host ""
Write-Host "Nothing has been pushed yet -- this script stops here so you can choose." -ForegroundColor Yellow
