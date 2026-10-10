$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
Set-Location (Split-Path $PSScriptRoot -Parent)
& .venv/Scripts/python.exe scripts/local_db.py init
if ($LASTEXITCODE -ne 0) { throw '数据库启动失败' }
$taskPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
$taskProcesses = @()
foreach ($spec in @(@{Name='server'; Args=@('manage.py','runserver','127.0.0.1:8000','--noreload')}, @{Name='worker'; Args=@('manage.py','worker')})) {
    $pidFile = Join-Path '.local' ($spec.Name + '.pid')
    if (Test-Path $pidFile) {
        $existing = Get-Process -Id (Get-Content $pidFile) -ErrorAction SilentlyContinue
        if ($existing -and $existing.Path -eq $taskPython) { Write-Host "$($spec.Name) 已运行"; continue }
    }
    if ($spec.Name -eq 'server' -and (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) { throw '8000 端口已被占用，请先确认现有服务' }
    $taskProc = Start-Process -FilePath $taskPython -ArgumentList $spec.Args -WorkingDirectory (Get-Location) -WindowStyle Hidden -PassThru -RedirectStandardOutput ".local/$($spec.Name).log" -RedirectStandardError ".local/$($spec.Name)-error.log"
    $taskProc.Id | Set-Content $pidFile
    $taskProcesses += $taskProc
}
Write-Host '本地服务：http://127.0.0.1:8000。日志位于 .local/；停止使用 scripts/stop.ps1。'
