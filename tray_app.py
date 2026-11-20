"""系统托盘宿主：服务跑在后台线程，主线程托管托盘图标（仿 zen-gate 的托盘形态）。

Web 面板保持不变，托盘只做宿主层的事：打开控制台、显示状态、复制面板地址、
打开输出目录、重启、退出。托盘不可用（无桌面会话 / 缺 pystray / 缺桌面组件）
时抛 TrayUnavailable，由 main 回退普通服务模式——绝不因托盘让服务起不来。
"""

# ============================================================
# 【核心模块】系统托盘 · 宿主与菜单
# 作用：pystray 图标 + 右键菜单（打开控制台/状态/复制地址/输出目录/重启/退出）；状态项每 5 秒从 /api/status 刷新
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser

from core import paths
from core.logger import get_logger

LOG = get_logger("tray")


class TrayUnavailable(RuntimeError):
    """当前环境无法提供系统托盘（无显示会话或缺依赖/桌面组件）。"""


def panel_url(ctx: dict) -> str:
    return f"http://{ctx['host']}:{ctx['port']}"


def describe_menu() -> list[str]:
    """菜单项文案（顺序即显示顺序），供测试与文档引用。"""
    return ["打开控制台", "状态：…", "复制面板地址", "打开输出目录", "重启服务", "退出"]


def make_icon_image():
    """用 Pillow 现画托盘图标（深蓝底 + 青紫网络节点，与安装包图标同款 motif）。

    不依赖任何二进制资源文件；Pillow 缺失时抛异常，由调用方降级。
    """
    from PIL import Image, ImageDraw

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([4, 4, size - 4, size - 4], radius=52,
                           fill=(15, 23, 52, 255))
    nodes = [(70, 90), (128, 56), (186, 96), (92, 168), (164, 164), (128, 120)]
    for a, b in [(0, 5), (1, 5), (2, 5), (3, 5), (4, 5), (0, 1), (2, 4), (3, 4)]:
        draw.line([nodes[a], nodes[b]], fill=(56, 189, 248, 255), width=5)
    for i, (x, y) in enumerate(nodes):
        color = (34, 211, 238, 255) if i % 2 == 0 else (167, 139, 250, 255)
        r = 15 if i == 5 else 11
        draw.ellipse([x - r, y - r, x + r, y + r], fill=color)
    return img


def _copy_text(text: str) -> bool:
    """尽力把文本放进系统剪贴板（各平台命令行工具），失败返回 False 不抛异常。"""
    try:
        if sys.platform == "win32":
            subprocess.run("clip", input=text, text=True, shell=True, timeout=5)
            return True
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text, text=True, timeout=5)
            return True
        for cmd in (["wl-copy"], ["xclip", "-selection", "clipboard"],
                    ["xsel", "--clipboard", "--input"]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, input=text, text=True, timeout=5)
                return True
    except Exception:
        pass
    return False


def _open_folder(path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606 - 打开本机输出目录
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], timeout=5)
    else:
        subprocess.run(["xdg-open", str(path)], timeout=5)


def open_panel(ctx: dict) -> None:
    webbrowser.open(panel_url(ctx))


def copy_address(ctx: dict, icon=None) -> None:
    url = panel_url(ctx)
    if _copy_text(url):
        _notify(icon, f"面板地址已复制：{url}")
    else:
        _notify(icon, f"面板地址：{url}（自动复制失败，请手动复制）")


def open_output_dir(ctx: dict) -> None:
    _open_folder(paths.ROOT / "output")


def _notify(icon, message: str) -> None:
    if icon is None:
        return
    try:
        icon.notify(message, "Local-Muse")
    except Exception:
        pass


def _status_text(ctx: dict) -> str:
    """从本机 /api/status 拼一行状态；拿不到就报「运行中」，不让托盘线程崩。"""
    try:
        import requests

        data = requests.get(panel_url(ctx) + "/api/status", timeout=2).json()
        active = int(data.get("active_count", 0))
        queued = int(data.get("queue_size", 0))
        if data.get("degraded"):
            return f"状态：已降级（硬件保护）· 活跃 {active} · 排队 {queued}"
        return f"状态：运行中 · 活跃 {active} · 排队 {queued}"
    except Exception:
        return "状态：运行中"


def build_icon(ctx: dict, pystray, on_quit, on_restart):
    """组装 pystray Icon 与菜单（回调由 run_tray 注入，便于单测替换）。"""
    holder = {"text": "状态：启动中…"}

    def _status_label(icon, item):
        return holder["text"]

    menu = pystray.Menu(
        pystray.MenuItem("打开控制台", lambda icon, item: open_panel(ctx), default=True),
        pystray.MenuItem(_status_label, None, enabled=False),
        pystray.MenuItem("复制面板地址", lambda icon, item: copy_address(ctx, icon)),
        pystray.MenuItem("打开输出目录", lambda icon, item: open_output_dir(ctx)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("重启服务", lambda icon, item: on_restart()),
        pystray.MenuItem("退出", lambda icon, item: on_quit(icon)),
    )
    icon = pystray.Icon("local-muse", make_icon_image(),
                        "Local-Muse · 本地自治 Agent", menu)
    icon._lm_status_holder = holder  # 状态刷新线程写这里，菜单绘制时读
    return icon


def run_tray(ctx: dict, serve_fn) -> int:
    """托盘模式入口：serve_fn 在后台线程跑，托盘图标占主线程。

    无桌面会话 / 缺依赖 / 后端初始化失败 → TrayUnavailable（main 回退普通模式）。
    图标运行后若服务自行结束，图标随之退出；点「退出」走 ctx['shutdown'] 优雅停机。
    """
    if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        raise TrayUnavailable("无 DISPLAY/WAYLAND_DISPLAY（无桌面会话）")
    try:
        import pystray
    except Exception as exc:
        raise TrayUnavailable(f"pystray 不可用：{exc}") from exc

    def _shutdown_service() -> None:
        shutdown = ctx.get("shutdown")
        if callable(shutdown):
            try:
                shutdown()
            except Exception as exc:
                LOG.warning("托盘触发停机时出错：%s", exc)

    def on_quit(icon) -> None:
        def _do() -> None:
            _shutdown_service()
            try:
                icon.stop()
            except Exception:
                pass

        threading.Thread(target=_do, name="lm-tray-quit", daemon=True).start()

    def on_restart() -> None:
        def _do() -> None:
            _shutdown_service()
            time.sleep(1.0)
            # 同参数原地重启：套接字为不可继承 FD，exec 后端口自动释放重绑
            os.execv(sys.executable, [sys.executable] + sys.argv)

        threading.Thread(target=_do, name="lm-tray-restart", daemon=True).start()

    try:
        icon = build_icon(ctx, pystray, on_quit, on_restart)
    except Exception as exc:
        raise TrayUnavailable(f"托盘后端不可用：{exc}") from exc

    result: dict = {}

    def _serve() -> None:
        try:
            result["code"] = serve_fn(ctx)
        except Exception:
            LOG.exception("托盘模式下服务线程异常")
        finally:
            try:
                icon.stop()
            except Exception:
                pass

    worker = threading.Thread(target=_serve, name="lm-serve", daemon=True)
    worker.start()

    stop_flag = threading.Event()
    holder = getattr(icon, "_lm_status_holder", {})

    def _poll() -> None:
        while not stop_flag.wait(5):
            holder["text"] = _status_text(ctx)
            try:
                icon.update_menu()
            except Exception:
                pass

    threading.Thread(target=_poll, name="lm-tray-status", daemon=True).start()

    def _hello() -> None:
        time.sleep(1.5)
        _notify(icon, f"Local-Muse 已启动，面板 {panel_url(ctx)}")

    threading.Thread(target=_hello, name="lm-tray-hello", daemon=True).start()

    LOG.info("托盘模式已启动：面板 %s", panel_url(ctx))
    try:
        icon.run()
    except KeyboardInterrupt:
        on_quit(icon)
    finally:
        stop_flag.set()
    worker.join(timeout=12)
    return int(result.get("code") or 0)
