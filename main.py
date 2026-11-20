"""Local-Muse 程序入口。

架构：单进程 + 后台 asyncio 调度 loop 线程 + 主线程 Flask + 每任务独立 subprocess。
面板只监听 127.0.0.1；任何非回环地址都会被强制改写，杜绝公网暴露。

CLI（供 scripts/ 一键脚本调用）：
    python main.py                       启动调度器 + Web 面板
    python main.py --setup-check        仅校验环境，返回退出码
    python main.py --dry-run-test       提交多条并行自检任务并等待结果（不启动面板）
    python main.py --host 127.0.0.1 --port 8765
"""

# ============================================================
# 【核心模块】入口 · 模式分发与自检
# 作用：--setup-check 环境检查 / --dry-run-test 并行自检 / 默认 serve（调度器+面板）；build_context 组装全部 services
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import argparse
import asyncio
import atexit
import importlib.util
import os
import signal
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import paths                      # noqa: E402 - 先设置 sys.path
# Browserr SDK 通过 BROWSERR_HOME/BROWSERR_ENGINE 定位项目内引擎，先锁定目录
_ = paths.setup_browserr_env()
from core.config import ConfigManager       # noqa: E402
from core.db import TaskDB                  # noqa: E402
from core.logger import setup_logging, get_logger, bind  # noqa: E402
from core.hardware import HardwareMonitor   # noqa: E402
from core.models import TaskSpec, TaskPriority  # noqa: E402

VERSION = "1.0.0"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
LOG = get_logger("main")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local-Muse 本地自治 Agent 系统")
    parser.add_argument("--setup-check", action="store_true", help="仅校验环境与依赖")
    parser.add_argument("--dry-run-test", action="store_true", help="并行 dry_run 自检")
    parser.add_argument("--dry-run-count", type=int, default=3, help="自检任务条数")
    parser.add_argument("--host", default=None, help="仅允许回环地址")
    parser.add_argument("--port", type=int, default=None, help="本机监听端口")
    parser.add_argument("--tray", action="store_true",
                        help="系统托盘模式（服务后台运行，托盘不可用时自动回退普通模式）")
    return parser.parse_args()


def setup_check() -> int:
    """只读环境检查：依赖、目录、配置、DB、Browserr 引擎。"""
    paths.ensure_dirs()
    results: list[tuple[str, bool, str, bool]] = []

    def check(label: str, ok: bool, detail: str = "", required: bool = True) -> None:
        """required=False 的项目缺失只算警告，不计入退出码。"""
        results.append((label, ok, detail, required))

    for module in ("flask", "requests", "schedule", "cryptography"):
        found = importlib.util.find_spec(module) is not None
        check(f"依赖 {module}", found,
              "已安装" if found else "缺失，请先运行 scripts/setup.* 安装依赖")

    check("运行目录", all(p.exists() for p in paths.DIRS.values()),
          "全部项目内运行期目录就绪（含 browserr_bin/）")
    cfg = ConfigManager()
    check("配置文件 config.json", paths.CONFIG_PATH.exists(),
          f"已配置 {len(cfg.providers())} 个 API 源")
    try:
        db = TaskDB(paths.DB_PATH)
        db.stats()
        check("SQLite 任务库", True, paths.rel(paths.DB_PATH))
    except Exception as exc:  # pragma: no cover - 环境异常
        check("SQLite 任务库", False, str(exc))

    # Browserr 二进制可稍后用 install_browserr.* 单独下载（dry_run 不依赖它），故只作警告
    browserr_ok, browserr_detail = paths.browserr_ready()
    check("Browserr 引擎（browserr_bin/）", browserr_ok, browserr_detail, required=False)
    # 本地 API：只应启用回环地址的源（zen-gate）；远程源应全部关闭
    remote_on = [pr["name"] for pr in cfg.providers()
                 if pr.get("enabled") and not str(pr.get("base_url", "")).startswith(
                     ("http://127.0.0.1", "http://localhost", "https://127.0.0.1"))]
    check("API 源仅本地", not remote_on,
          "全部启用源均为本机回环地址" if not remote_on else f"仍有远程源启用：{', '.join(remote_on)}",
          required=False)

    for label, ok, detail, required in results:
        mark = "[OK]  " if ok else ("[WARN]" if not required else "[FAIL]")
        print(f"{mark} {label} - {detail}")
    failed = [r for r in results if not r[1] and r[3]]
    warned = [r for r in results if not r[1] and not r[3]]
    print(f"\n环境检查完成：{len(results) - len(failed) - len(warned)} 项通过，"
          f"{len(warned)} 项警告，{len(failed)} 项失败。")
    print("提示：API 只使用本机 zen-gate（config.json 的 zen_gate_local）；浏览器只使用项目内 Browserr。")
    return 1 if failed else 0


def build_context(args: argparse.Namespace) -> dict:
    """组装全部依赖（业务模块延迟 import，缺依赖时 setup-check 仍能给出友好提示）。"""
    paths.ensure_dirs()
    _ = paths.setup_browserr_env()   # 幂等：确保本进程使用项目内 browserr_bin/
    cfg = ConfigManager()
    cfg.bootstrap()
    ring = setup_logging(paths.LOG_PATH)
    db = TaskDB(paths.DB_PATH)
    ring.attach_db(db)
    bind("-", "startup")

    from agent_engine.task_queue import Scheduler
    from media_generator.api_pool import ApiPool
    from browser_agent.vault import Vault
    from web_panel.bridge import SchedulerBridge
    from web_panel.app import create_app

    hw = HardwareMonitor(cfg)
    pool = ApiPool(cfg, db)
    vault = Vault(paths.DIRS["vault"])
    from core.memory import MemoryStore
    memory = MemoryStore(paths.DB_PATH) if (cfg.section("memory") or {}).get("enabled", True) else None
    scheduler = Scheduler(cfg, db, hw, pool, vault, memory=memory)
    bridge = SchedulerBridge(scheduler)
    ctx = {
        "config": cfg, "db": db, "ring": ring, "hardware": hw, "pool": pool,
        "memory": memory,
        # 面板只拿到受限视图，无法取得 engine_token 或签发临时凭证
        "vault": vault.panel_view(), "scheduler": scheduler, "bridge": bridge,
        "version": VERSION,
    }
    server_cfg = cfg.section("server")
    host = args.host or server_cfg.get("host", "127.0.0.1")
    if host not in LOOPBACK:
        LOG.warning("检测到非回环地址 %s，已强制改为 127.0.0.1（禁止公网暴露）", host)
        host = "127.0.0.1"
    ctx["host"] = host
    ctx["port"] = int(args.port or server_cfg.get("port", 8765))
    ctx["app"] = create_app(ctx)
    return ctx


async def run_dry_run_test(ctx: dict, count: int) -> int:
    """提交多条不同优先级的并行自检任务，验证完整链路（不启动 Flask）。"""
    scheduler = ctx["scheduler"]
    # 自检不启动真实 Browserr、不联网，临时关闭硬件降级门禁，
    # 避免本机内存偏高时排队任务一直等不到派发（正常运行始终开启该门禁）
    scheduler.set_hardware_gate(False)
    await scheduler.start(enable_schedule=False)
    skills = ["blessing_video_hit_v2", "web_research", "auto_optimize_prompt"]
    ids: list[str] = []
    for index in range(max(1, count)):
        priority = [TaskPriority.HIGH_TEST, TaskPriority.MANUAL, TaskPriority.SCHEDULED][index % 3]
        spec = TaskSpec.create(
            priority=priority, source="test",
            enqueue_mode="insert" if index % 2 == 0 else "queue",
            skill=skills[index % len(skills)],
            instruction=f"并行链路自检任务 #{index + 1}",
            dry_run=True,
        )
        ids.append(await scheduler.submit(spec))

    print(f"已提交 {len(ids)} 条并行自检任务：{', '.join(ids)}")
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if not scheduler.active_snapshot() and scheduler.queue_size() == 0:
            break
        await asyncio.sleep(0.4)

    ok_count = 0
    for task_id in ids:
        row = ctx["db"].get_task(task_id) or {}
        status = row.get("status", "unknown")
        ok_count += int(status == "succeeded")
        print(f"  task={task_id} status={status} stage={row.get('stage')} "
              f"retry={row.get('retry')} simulated={bool(row.get('simulated'))} "
              f"error={row.get('error', '')[:80]}")
    await scheduler.stop()
    print(f"\n自检结果：{ok_count}/{len(ids)} 成功（dry_run 产物均为「模拟」，不代表真实生成）。")
    return 0 if ok_count == len(ids) else 1


def _write_pid() -> None:
    try:
        paths.PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass


def _remove_pid() -> None:
    try:
        paths.PID_PATH.unlink(missing_ok=True)
    except OSError:
        pass


def serve(ctx: dict) -> int:
    """后台线程跑 asyncio 调度 loop，主线程跑 Flask（werkzeug 可优雅关闭）。"""
    from werkzeug.serving import make_server

    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def loop_thread() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(ctx["scheduler"].start())
            ready.set()
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()

    worker = threading.Thread(target=loop_thread, name="lm-scheduler", daemon=True)
    worker.start()
    if not ready.wait(15):
        LOG.error("调度器启动超时")
        return 1

    _write_pid()
    atexit.register(_remove_pid)
    host, port = ctx["host"], ctx["port"]
    server = make_server(host, port, ctx["app"], threaded=True)
    ctx["server"] = server
    stopped = threading.Event()

    def shutdown() -> None:
        if stopped.is_set():
            return
        stopped.set()
        try:
            asyncio.run_coroutine_threadsafe(ctx["scheduler"].stop(), loop).result(timeout=10)
        except Exception as exc:
            LOG.warning("调度器停止时出错：%s", exc)
        loop.call_soon_threadsafe(loop.stop)
        server.shutdown()
        _remove_pid()

    ctx["shutdown"] = shutdown  # 供托盘等外部宿主触发优雅停机

    def on_signal(_signum, _frame) -> None:
        threading.Thread(target=shutdown, name="lm-shutdown", daemon=True).start()

    try:
        signal.signal(signal.SIGINT, on_signal)
        signal.signal(signal.SIGTERM, on_signal)
    except (ValueError, OSError):
        pass  # 某些嵌入式环境不允许注册信号

    LOG.info("Local-Muse v%s 已启动：调度器线程 + 面板 http://%s:%s（仅本机可访问）",
             VERSION, host, port)
    print(f"\n  Local-Muse v{VERSION}  |  面板地址：http://{host}:{port}  |  仅本机可访问")
    print("  按 Ctrl+C 停止；停止时会自动取消全部子任务并清理子进程树。\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        shutdown()
    finally:
        stopped.set()
        _remove_pid()
    return 0


def main() -> int:
    args = parse_args()
    if args.setup_check:
        return setup_check()
    ctx = build_context(args)
    if args.dry_run_test:
        return asyncio.run(run_dry_run_test(ctx, args.dry_run_count))
    if args.tray:
        from tray_app import TrayUnavailable, run_tray

        try:
            return run_tray(ctx, serve)
        except TrayUnavailable as exc:
            LOG.warning("托盘不可用（%s），回退普通服务模式", exc)
    return serve(ctx)


if __name__ == "__main__":
    sys.exit(main())
