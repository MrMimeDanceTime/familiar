#!/usr/bin/env pwsh
# Builds the frontend (if needed) and starts the backend, which serves
# both the API and the built frontend on a single port.

param(
    [int]$Port = 8420
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$venvPython = Join-Path $root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[setup] Creating backend virtualenv..."
    python -m venv (Join-Path $root "backend\.venv")
    & $venvPython -m pip install -e (Join-Path $root "backend")
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "[setup] Created .env from .env.example - fill in your API key(s) and re-run."
    exit 1
}

if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "[setup] Installing frontend dependencies..."
    npm install --prefix (Join-Path $root "frontend")
}

$distDir = Join-Path $root "frontend\dist"
$srcDir = Join-Path $root "frontend\src"
$needsBuild = $true
if (Test-Path $distDir) {
    $distTime = (Get-ChildItem $distDir -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    $srcTime = (Get-ChildItem $srcDir -Recurse | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
    if ($distTime -gt $srcTime) {
        $needsBuild = $false
    }
}

if ($needsBuild) {
    Write-Host "[build] Building frontend..."
    npm run build --prefix (Join-Path $root "frontend")
} else {
    Write-Host "[build] frontend unchanged, skipping rebuild"
}

$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if ($tailscale) {
    Write-Host "[tailscale] Exposing port $Port on your tailnet..."
    # Expose via plain HTTP to the local uvicorn backend (uvicorn does not
    # speak TLS, so https+insecure would send TLS ClientHello bytes ->
    # "Invalid HTTP request received").
    tailscale serve --bg http://localhost:$Port
}

Write-Host "[start] Familiar running at http://localhost:$Port"
& $venvPython -m uvicorn app.main:app --app-dir (Join-Path $root "backend") --port $Port
