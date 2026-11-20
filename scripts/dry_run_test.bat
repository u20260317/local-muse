@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo Local-Muse parallel dry-run self test (no API key required) ...
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)
".venv\Scripts\python.exe" main.py --dry-run-test --dry-run-count 3
set "RC=%errorlevel%"
echo Exit code: %RC%
if "%~1"=="" pause
exit /b %RC%
