"""路径常量与 Windows / Linux 平台兼容工具。

所有运行期数据目录都固定在本项目内，绝不指向系统浏览器的用户目录。
"""

# ============================================================
# 【核心模块】基础层 · 路径与 Browserr 环境
# 作用：全部运行期目录固定在项目内；setup_browserr_env/browserr_ready 定位 browserr_bin 引擎与 SDK
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import os
import subprocess
from pathlib import Path

IS_WINDOWS = os.name == "nt"

# 项目根目录（core/ 的上一级）
ROOT = Path(__file__).resolve().parent.parent

DIRS: dict[str, Path] = {
    "browserr_bin": ROOT / "browserr_bin",         # Browserr 引擎 + driver.py（仅本项目，不入库）
    "browser_bin": ROOT / "browserr_bin",          # 旧键名别名，指向同一目录，避免外部引用失效
    "browser_profile": ROOT / "browser_profile",   # 唯一 Browserr 用户数据根（Servo profile）
    "vault": ROOT / "vault",                       # 加密凭证保险库（受限权限）
    "output": ROOT / "output",                     # 产物根目录
    "logs": ROOT / "logs",                         # 运行日志
    "db": ROOT / "db",                             # SQLite 目录
    "docs": ROOT / "docs",
    "skills": ROOT / "docs" / "skills",
    "scripts": ROOT / "scripts",
    "tmp": ROOT / "output" / ".tmp",               # 下载临时文件与子进程回传
}

DB_PATH = ROOT / "db" / "task.db"
CONFIG_PATH = ROOT / "config.json"
LOG_PATH = ROOT / "logs" / "local_muse.log"
PID_PATH = ROOT / "logs" / "local_muse.pid"


def ensure_dirs() -> None:
    """创建全部运行期目录；重复调用安全。"""
    for directory in DIRS.values():
        directory.mkdir(parents=True, exist_ok=True)


def setup_browserr_env() -> Path:
    """把 Browserr 引擎目录固定到项目内 browserr_bin/。

    Browserr 的 Python SDK（driver.py）通过 BROWSERR_ENGINE / BROWSERR_HOME
    定位引擎二进制；本函数把这两个变量指向项目内目录，绝不使用系统浏览器，
    也不依赖用户全局的 ~/.browserr 缓存。路径由 __file__ 动态解析，
    Windows / Linux 通用，不含任何硬编码绝对路径。
    """
    target = DIRS["browserr_bin"]
    target.mkdir(parents=True, exist_ok=True)
    os.environ["BROWSERR_HOME"] = str(target)
    engine = browserr_engine_path()
    if engine.exists():
        os.environ["BROWSERR_ENGINE"] = str(engine)
    return target


def setup_playwright_env() -> Path:
    """旧名兼容别名：Chromium/Playwright 已移除，统一转发到 Browserr。"""
    return setup_browserr_env()


def browserr_engine_path() -> Path:
    """Browserr 引擎二进制路径（Windows 为 engine.exe，其余为 engine）。"""
    name = "engine.exe" if IS_WINDOWS else "engine"
    return DIRS["browserr_bin"] / name


def browserr_driver_path() -> Path:
    """Browserr Python SDK（driver.py，随官方 release 一同分发）路径。"""
    return DIRS["browserr_bin"] / "driver.py"


def browserr_ready() -> tuple[bool, str]:
    """检查 Browserr 引擎与 SDK 是否已下载到项目内 browserr_bin/。"""
    engine, driver = browserr_engine_path(), browserr_driver_path()
    if engine.exists() and driver.exists():
        return True, rel(engine)
    missing = [rel(p) for p in (engine, driver) if not p.exists()]
    return False, "未下载：" + "、".join(missing) + "，请运行 scripts/install_browserr.*"


def rel(path: Path) -> str:
    """把项目内路径显示为相对项目根的形式（统一用 / 分隔，跨平台可读）。"""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return Path(path).name


def safe_id(task_id: str) -> str:
    """把 task_id 收敛为可安全用于文件名的字符，防路径穿越。"""
    return "".join(c for c in str(task_id) if c.isalnum() or c in "-_")[:80] or "unknown"


def session_dir(task_id: str) -> Path:
    """每个任务独立 Browserr 用户数据目录（Cookie / 缓存 / 本地存储天然隔离）。"""
    path = DIRS["browser_profile"] / f"session_{safe_id(task_id)}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def output_dir(task_id: str) -> Path:
    """每个任务独立产物目录。"""
    path = DIRS["output"] / safe_id(task_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def list_sessions() -> list[dict[str, str | float]]:
    """列出全部独立浏览器会话目录及其占用空间，供面板展示。"""
    root = DIRS["browser_profile"]
    if not root.exists():
        return []
    result: list[dict[str, str | float]] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name.startswith("session_"):
            size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            result.append({
                "name": child.name,
                "task_id": child.name.removeprefix("session_"),
                "path": rel(child),
                "size_mb": round(size / 1024 / 1024, 2),
            })
    return result


def reset_browser_env() -> int:
    """清空并重建 browser_profile/（仅删除本项目内的会话目录）。"""
    import shutil
    root = DIRS["browser_profile"]
    count = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith("session_"):
                shutil.rmtree(child, ignore_errors=True)
                count += 1
    root.mkdir(parents=True, exist_ok=True)
    return count


def subprocess_options() -> dict[str, int | bool]:
    """子进程创建参数：独立进程组 / 会话，便于超时强杀整棵进程树。

    Windows 没有 RLIMIT_AS，内存靠「全局并发上限 + 硬件阈值降级」控制；
    Linux 的内存上限由 worker 启动后自行调用 apply_worker_memory_limit() 设置。
    """
    if IS_WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def apply_worker_memory_limit(memory_limit_mb: int) -> None:
    """在子进程内部设置地址空间上限（仅 POSIX 有效）。"""
    if IS_WINDOWS or memory_limit_mb <= 0:
        return
    try:
        import resource  # noqa: PLC0415 - 仅 Linux 可用
        limit = int(memory_limit_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        # 某些环境不允许设置，静默降级为「依赖全局并发与硬件阈值」
        pass


def kill_process_tree(pid: int) -> bool:
    """强杀指定进程及其子进程树；只允许用于本程序自己创建的子进程。"""
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            proc = subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
            )
            return proc.returncode == 0
        except Exception:
            return False
    try:
        os.killpg(os.getpgid(pid), 9)
        return True
    except Exception:
        try:
            os.kill(pid, 9)
            return True
        except Exception:
            return False
