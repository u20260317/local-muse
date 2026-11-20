@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo Local-Muse starting ... panel: http://127.0.0.1:8765 (default, see config.json)

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)
set "VPY=.venv\Scripts\python.exe"
"%VPY%" -c "import flask, requests, schedule" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Dependencies missing. Run scripts\setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)

"%VPY%" main.py %*
if "%~1"=="" pause
exit /b %errorlevel%
