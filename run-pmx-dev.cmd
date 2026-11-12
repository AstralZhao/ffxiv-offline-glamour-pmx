@echo off
setlocal
cd /d "%~dp0"
python -X utf8 scripts\run_dev.py %*
if errorlevel 1 pause
