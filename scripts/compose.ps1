param([ValidateSet('up','down','logs')][string]$Action='up')
$ErrorActionPreference='Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.local/settings.json')) { py -3.13 scripts/configure.py }
$taskConfig=Get-Content '.local/settings.json' -Raw | ConvertFrom-Json
$env:PGPASSWORD=$taskConfig.PGPASSWORD
$env:SECRET_KEY=$taskConfig.SECRET_KEY
try {
    if ($Action -eq 'up') { docker compose up --build -d }
    elseif ($Action -eq 'down') { docker compose down }
    else { docker compose logs --tail=100 }
    if ($LASTEXITCODE -ne 0) { throw 'Docker Compose 执行失败' }
} finally { Remove-Item Env:PGPASSWORD,Env:SECRET_KEY -ErrorAction SilentlyContinue }
