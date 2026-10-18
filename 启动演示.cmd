@echo off
cd /d "%~dp0"
python tools\start_local.py --setup
if errorlevel 1 pause
