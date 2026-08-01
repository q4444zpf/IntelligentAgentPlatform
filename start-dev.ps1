$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host 'Starting PostgreSQL ...'
docker compose -f "$Root\compose.yaml" up -d postgres

Write-Host 'Starting platform API on http://127.0.0.1:8000 ...'
Start-Process powershell -WindowStyle Hidden -WorkingDirectory "$Root\backend" -ArgumentList @(
    '-NoProfile',
    '-Command',
    'python -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; python -m app.migrations.sqlite_to_postgres; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; python -m uvicorn app.main:app --reload --port 8000'
)

Write-Host 'Starting Web console on http://127.0.0.1:5173 ...'
Set-Location "$Root\frontend"
npm run dev
