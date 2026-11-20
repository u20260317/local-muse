@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo Installing Playwright built-in Chromium into project subfolder browser_bin ...
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found. Run scripts\setup.bat first.
  if "%~1"=="" pause
  exit /b 1
)
REM Relative to project root (resolved at runtime); NO hard-coded absolute path.
set "PLAYWRIGHT_BROWSERS_PATH=browser_bin"
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 (
  echo [ERROR] Chromium download failed. Check network/proxy and retry.
  if "%~1"=="" pause
  exit /b 1
)
echo [OK] Playwright built-in Chromium is ready. System browsers were NOT touched.
if "%~1"=="" pause
exit /b 0
