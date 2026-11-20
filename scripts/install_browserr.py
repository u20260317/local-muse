"""下载 Browserr 官方 release 到项目内 browserr_bin/（替代原 Playwright Chromium）。

- 只从 github.com / objects.githubusercontent.com 下载官方 release 资产
- 必须校验 GitHub API 给出的 sha256 digest，缺失或不符直接拒绝安装
  （与 Browserr 官方 bootstrap 相同的安全策略）
- 平台资产：browserr-<ver>-<linux|macos|windows>-<x64|arm64>.tar.gz/.zip
- 版本可用环境变量 BROWSERR_VERSION 覆盖（不带 v 前缀），默认 0.9.2
- 纯标准库，Windows / Linux / macOS 通用；解压后 browserr_bin/ 内应有
  engine(.exe) 与 driver.py
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "browserr_bin"
REPO = "Omodaka9375/browserr"
DEFAULT_VERSION = "0.9.2"
ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com"}
MAX_BYTES = 500 * 1024 * 1024


def asset_name(version: str) -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "windows":
        os_name, ext = "windows", "zip"
        arch = "x64" if machine in ("amd64", "x86_64") else None
    elif system == "linux":
        os_name, ext = "linux", "tar.gz"
        arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine)
    elif system == "darwin":
        os_name, ext = "macos", "tar.gz"
        arch = "arm64" if machine == "arm64" else "x64"
    else:
        arch, os_name, ext = None, system, ""
    if not arch:
        raise SystemExit(f"不支持的平台：{system}/{machine}")
    return f"browserr-{version}-{os_name}-{arch}.{ext}"


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "local-muse-install-browserr",
        "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    version = os.environ.get("BROWSERR_VERSION", DEFAULT_VERSION).lstrip("v")
    name = asset_name(version)
    release = get_json(f"https://api.github.com/repos/{REPO}/releases/tags/v{version}")
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if not asset:
        raise SystemExit(f"release v{version} 没有本平台资产 {name}")
    url = asset.get("browser_download_url", "")
    host = urllib.request.urlparse(url).hostname or ""
    if urllib.request.urlparse(url).scheme != "https" or host not in ALLOWED_HOSTS:
        raise SystemExit(f"拒绝非 GitHub 下载地址：{url}")
    if int(asset.get("size", 0)) > MAX_BYTES:
        raise SystemExit("资产体积异常，拒绝下载")
    expected = (asset.get("digest") or "").removeprefix("sha256:").lower()
    if not expected:
        raise SystemExit("资产没有 sha256 digest，拒绝安装未校验的二进制")

    DEST.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="browserr-install-"))
    try:
        archive = tmp / name
        print(f"下载 {name}（{int(asset.get('size', 0)) // (1024 * 1024)} MB）...")
        req = urllib.request.Request(url, headers={"User-Agent": "local-muse-install-browserr"})
        with urllib.request.urlopen(req, timeout=600) as resp, open(archive, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != expected:
            raise SystemExit(f"sha256 校验失败：期望 {expected}，实际 {digest}")
        print("sha256 校验通过，正在解压到 browserr_bin/ ...")
        stage = tmp / "unpacked"
        stage.mkdir()
        if name.endswith(".zip"):
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(stage)
        else:
            with tarfile.open(archive) as tf:
                tf.extractall(stage)
        engine_name = "engine.exe" if platform.system().lower() == "windows" else "engine"
        if not (stage / engine_name).exists() or not (stage / "driver.py").exists():
            raise SystemExit("资产内容异常：缺少 engine 或 driver.py，未安装")
        for entry in stage.iterdir():
            target = DEST / entry.name
            if target.is_dir():
                shutil.rmtree(target)
            shutil.move(str(entry), str(target))
        engine = DEST / engine_name
        engine.chmod(engine.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
        print(f"[完成] Browserr v{version} 就绪：browserr_bin/{engine_name} + driver.py")
        print("       Linux 无头运行还需系统包：sudo apt install libegl1 libgl1 libgbm1 libgl1-mesa-dri")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
