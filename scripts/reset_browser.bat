@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0.."
echo This will DELETE browser_profile\session_* (isolated Browserr sessions only).
echo Your daily Chrome / Edge / Firefox data is NEVER touched.
set "ANS="
set /p ANS=Type Y to continue:
if /i not "%ANS%"=="Y" (
  echo Cancelled.
  if "%~1"=="" pause
  exit /b 0
)
if exist "browser_profile" (
  for /d %%D in ("browser_profile\session_*") do rd /s /q "%%D"
) else (
  mkdir "browser_profile"
)
echo [OK] Isolated browser environment has been reset.
if "%~1"=="" pause
exit /b 0
