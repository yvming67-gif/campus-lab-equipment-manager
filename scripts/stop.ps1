$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskPython = Join-Path (Get-Location) '.venv/Scripts/python.exe'
foreach ($name in @('server','worker')) {
    $pidFile = Join-Path '.local' ($name + '.pid')
    if (Test-Path $pidFile) {
        $taskProc = Get-CimInstance Win32_Process -Filter "ProcessId = $(Get-Content $pidFile)" -ErrorAction SilentlyContinue
        if ($taskProc -and $taskProc.ExecutablePath -eq $taskPython -and $taskProc.CommandLine -like '*manage.py*') { taskkill /PID $taskProc.ProcessId /T /F | Out-Null }
        Remove-Item -LiteralPath $pidFile
    }
}
Write-Host '已停止由 start.ps1 记录的应用进程。数据库可另用 python scripts/local_db.py stop 停止。'
