@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo ============================================
echo  Local-Muse first-time setup (Windows)
echo  No Docker. No system browser is touched.
echo ============================================

set "PY="
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"
if not defined PY (
  where python >nul 2>nul
  if %errorlevel%==0 set "PY=python"
)
if not defined PY (
  echo [ERROR] Python 3 not found. Install Python 3.10+ first.
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto :fail
)

set "VPY=.venv\Scripts\python.exe"
echo [2/4] Installing Python dependencies ...
"%VPY%" -m pip install --upgrade pip
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [3/4] Downloading Browserr (Servo) engine into project subfolder browserr_bin ...
"%VPY%" scripts\install_browserr.py
if errorlevel 1 goto :fail

echo [4/4] Checking environment ...
"%VPY%" main.py --setup-check
if errorlevel 1 goto :fail

echo.
echo [OK] Setup finished. Next: scripts\start.bat
echo      Local API only: start zen-gate first, then set ZEN_GATE_API_KEY or fill zen_gate_local.api_key in config.json.
if "%~1"=="" pause
exit /b 0

:fail
echo [ERROR] Setup failed. See messages above. Check network/proxy and retry.
if "%~1"=="" pause
exit /b 1
