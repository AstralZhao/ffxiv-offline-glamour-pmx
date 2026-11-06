@echo off
setlocal
cd /d "%~dp0.."
set "FF14_DEMO_PYTHON=%~dp0..\artifacts\runtime\python\python.exe"
if not exist "%FF14_DEMO_PYTHON%" (
  echo The bundled runtime is missing. Please extract the complete application package.
  pause
  exit /b 1
)
"%FF14_DEMO_PYTHON%" -X utf8 "%~dp0launch.py" %*
set "FF14_DEMO_EXIT=%errorlevel%"
if not "%FF14_DEMO_EXIT%"=="0" (
  echo.
  echo The client could not start. See 缓存\logs\client-startup.log
  echo Please keep this window open if you need help.
  pause
)
exit /b %FF14_DEMO_EXIT%
