$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host 'Starting model provider API on http://127.0.0.1:8000 ...'
Start-Process powershell -WindowStyle Hidden -WorkingDirectory "$Root\backend" -ArgumentList @(
    '-NoProfile',
    '-Command',
    'python -m uvicorn app.main:app --reload --port 8000'
)

Write-Host 'Starting Web console on http://127.0.0.1:5173 ...'
Set-Location "$Root\frontend"
npm run dev
