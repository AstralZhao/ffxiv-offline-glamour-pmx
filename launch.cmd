@echo off
setlocal
cd /d "%~dp0.."
set "FF14_DEMO_PYTHON=python"
if exist "G:\Tools\Anaconda3\python.exe" set "FF14_DEMO_PYTHON=G:\Tools\Anaconda3\python.exe"
"%FF14_DEMO_PYTHON%" -X utf8 "%~dp0launch.py" %*
set "FF14_DEMO_EXIT=%errorlevel%"
if not "%FF14_DEMO_EXIT%"=="0" (
  echo.
  echo The client could not start. See artifacts\demo\client-startup.log
  echo Please keep this window open if you need help.
  pause
)
exit /b %FF14_DEMO_EXIT%
