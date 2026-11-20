# Local-Muse 一键启动（PowerShell 版，适配 Windows / PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$venvPy = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    Write-Host '[错误] 未找到 .venv，请先运行 scripts\setup.bat' -ForegroundColor Red
    exit 1
}

& $venvPy -c 'import flask, requests, schedule' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '[错误] 依赖缺失，请先运行 scripts\setup.bat' -ForegroundColor Red
    exit 1
}

Write-Host 'Local-Muse 启动中 ... 面板：http://127.0.0.1:8765（默认，端口见 config.json）' -ForegroundColor Cyan
Write-Host '仅本机可访问；按 Ctrl+C 停止。' -ForegroundColor Gray
& $venvPy main.py @args
exit $LASTEXITCODE
