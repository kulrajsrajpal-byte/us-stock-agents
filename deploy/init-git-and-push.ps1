# Run AFTER Git is installed and you have created an empty GitHub repo.
# Usage:
#   .\deploy\init-git-and-push.ps1 -RepoUrl "https://github.com/YOUR_USER/us-stock-agents.git"

param(
    [Parameter(Mandatory = $true)]
    [string]$RepoUrl
)

$ErrorActionPreference = "Stop"
$candidates = @(
    "C:\Users\C9D7WP\AppData\Local\Programs\Git\cmd\git.exe",
    "C:\Program Files\Git\bin\git.exe",
    "C:\Program Files\Git\cmd\git.exe"
)
$git = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $git) {
    $gitCmd = Get-Command git -ErrorAction SilentlyContinue
    if (-not $gitCmd) { throw "Git not found. Finish installing Git, then open a NEW terminal." }
    $git = $gitCmd.Source
}

Set-Location $PSScriptRoot\..

if (-not (Test-Path .git)) {
    & $git init -b main
}

& $git add .
$status = & $git status --porcelain
if ($status) {
    & $git commit -m "Initial US stock multi-agent for Railway"
} else {
    Write-Host "Nothing new to commit."
}

$remote = & $git remote
if ($remote -notcontains "origin") {
    & $git remote add origin $RepoUrl
} else {
    & $git remote set-url origin $RepoUrl
}

& $git push -u origin main
Write-Host "Pushed to $RepoUrl"
Write-Host "Next: https://railway.app/new → Deploy from GitHub repo → us-stock-agents"
