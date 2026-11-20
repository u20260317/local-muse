@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0.."
echo Stopping Local-Muse service (this project only) ...

if not exist "logs\local_muse.pid" (
  echo [INFO] No PID file found. Service is probably not running.
  if "%~1"=="" pause
  exit /b 0
)
set "LM_PID="
set /p LM_PID=<"logs\local_muse.pid"
if not defined LM_PID (
  echo [INFO] Empty PID file, nothing to stop.
  del /q "logs\local_muse.pid" >nul 2>nul
  if "%~1"=="" pause
  exit /b 0
)

tasklist /FI "PID eq %LM_PID%" /FO CSV /NH 2>nul | findstr /i "python" >nul
if errorlevel 1 (
  echo [INFO] Process %LM_PID% is not running. Cleaning PID file.
  del /q "logs\local_muse.pid" >nul 2>nul
  if "%~1"=="" pause
  exit /b 0
)

set "CMD_FILE=%TEMP%\lm_verify_%RANDOM%.txt"
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter 'ProcessId=%LM_PID%' -ErrorAction SilentlyContinue; if ($p) { $p.CommandLine }" > "%CMD_FILE%" 2>nul
findstr /i /c:"main.py" "%CMD_FILE%" >nul
if errorlevel 1 (
  echo [ERROR] PID %LM_PID% does not belong to Local-Muse. Refusing to kill.
  del /q "%CMD_FILE%" >nul 2>nul
  if "%~1"=="" pause
  exit /b 1
)
del /q "%CMD_FILE%" >nul 2>nul

taskkill /PID %LM_PID% /T /F >nul 2>nul
del /q "logs\local_muse.pid" >nul 2>nul
echo [OK] Local-Muse stopped. Child processes were cleaned up by the application.
if "%~1"=="" pause
exit /b 0
