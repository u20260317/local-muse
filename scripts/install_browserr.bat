@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo Installing Browserr (Servo) engine into project subfolder browserr_bin ...
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" scripts\install_browserr.py
if errorlevel 1 (
  echo [ERROR] Browserr download failed. Check network/proxy and retry.
  if "%~1"=="" pause
  exit /b 1
)
echo [OK] Browserr is ready in browserr_bin. System browsers were NOT touched.
if "%~1"=="" pause
exit /b 0
