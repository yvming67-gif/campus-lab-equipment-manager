@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
py -3.13 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>nul
if not errorlevel 1 (
    py -3.13 tools\start_local.py --setup %*
    goto finished
)
python -c "import sys,struct; assert sys.version_info[:2] == (3,13) and struct.calcsize('P') == 8" >nul 2>nul
if errorlevel 1 (
    echo 请先安装Python 3.13的Windows 64位版本，安装时勾选Add python.exe to PATH。
    echo 官方下载：https://www.python.org/downloads/windows/
    echo 安装完成后重新双击本文件。详细步骤见docs中的“老师运行指南”。
    pause
    exit /b 1
)
python tools\start_local.py --setup %*
:finished
set "CAMPUS_EXIT=%ERRORLEVEL%"
if not "%CAMPUS_EXIT%"=="0" pause
exit /b %CAMPUS_EXIT%
