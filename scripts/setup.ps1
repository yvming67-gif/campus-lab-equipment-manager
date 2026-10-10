$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Confirm-Exit { if ($LASTEXITCODE -ne 0) { throw "命令执行失败，退出码：$LASTEXITCODE" } }
if (-not (Test-Path '.venv/Scripts/python.exe')) { py -3.13 -m venv .venv; Confirm-Exit }
& .venv/Scripts/python.exe -m pip install -r requirements.txt
Confirm-Exit
& .venv/Scripts/python.exe scripts/configure.py
Confirm-Exit
if (-not (Test-Path '.local/postgresql/pgsql/bin/initdb.exe')) {
    $archive = Join-Path (Get-Location) '.local/postgresql.zip'
    if (-not (Test-Path $archive)) { Invoke-WebRequest 'https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip' -OutFile $archive }
    Expand-Archive -LiteralPath $archive -DestinationPath '.local/postgresql' -Force
}
& .venv/Scripts/python.exe scripts/local_db.py init
Confirm-Exit
& .venv/Scripts/python.exe manage.py migrate
Confirm-Exit
& .venv/Scripts/python.exe manage.py seed_demo
Confirm-Exit
Write-Host '初始化完成。运行 scripts/start.ps1，账号位于 .local/demo-accounts.json。'
