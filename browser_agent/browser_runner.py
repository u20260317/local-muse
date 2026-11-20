"""浏览器任务执行体：只启动 Playwright 自带独立 Chromium。

隔离铁则（本文件是全项目唯一启动浏览器的位置）：
- 只调用安装到项目内 browser_bin/ 的 Playwright 独立 Chromium 二进制
- 用户数据目录固定为项目内 browser_profile/session_<task_id>/
- 不指定任何系统浏览器通道、不指定外部可执行路径、不连接已有浏览器进程
- 凭证只注入这个隔离上下文，绝不写入本机日常浏览器
"""
from __future__ import annotations

import json
from pathlib import Path

from core import paths


def _inject_credentials(context, credential: dict) -> None:
    """把会话级临时凭证注入隔离 Chromium 上下文（不落盘、不记日志）。"""
    cookies = []
    for item in credential.get("cookies", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        entry = {"name": str(item["name"]), "value": str(item.get("value", ""))}
        if item.get("url"):
            entry["url"] = str(item["url"])
        else:
            entry["domain"] = str(item.get("domain", "example.com"))
            entry["path"] = str(item.get("path", "/"))
        cookies.append(entry)
    if cookies:
        context.add_cookies(cookies)
    headers = credential.get("headers") or {}
    if isinstance(headers, dict) and headers:
        context.set_extra_http_headers({str(k): str(v) for k, v in headers.items()})
    storage = credential.get("storage") or {}
    if isinstance(storage, dict) and storage:
        script = "try{" + "".join(
            f"localStorage.setItem({json.dumps(str(k))},{json.dumps(str(v))});"
            for k, v in storage.items()
        ) + "}catch(e){}"
        context.add_init_script(script=script)


def _simulated_result(job: dict) -> dict:
    """dry_run 自检：不联网、不启动 Chromium，但完整回传链路结构。"""
    return {
        "ok": True,
        "simulated": True,
        "session": Path(job.get("user_data_dir", "")).name,
        "materials": [{"type": "text", "source": "dry_run",
                       "value": "自检素材：未访问任何网站，未读取本机浏览器数据"}],
        "screenshots": [],
        "note": "模拟结果，不代表真实网页采集",
    }


def run_browser_job(job: dict) -> dict:
    """在子进程内同步执行一条浏览器任务；失败返回结构化错误，不向外抛异常。"""
    if job.get("dry_run"):
        return _simulated_result(job)

    try:
        paths.setup_playwright_env()  # 必须在 import playwright 之前，锁定项目内独立二进制
        from playwright.sync_api import sync_playwright  # 延迟导入，主进程不依赖浏览器
    except ImportError:
        return {"ok": False, "error": "未安装 playwright，请运行 playwright install chromium"}

    task_id = paths.safe_id(str(job.get("task_id", "")))
    user_data_dir = Path(job.get("user_data_dir") or paths.session_dir(task_id))
    user_data_dir.mkdir(parents=True, exist_ok=True)
    out_dir = paths.output_dir(task_id)
    nav_timeout = int(job.get("navigation_timeout_ms", 30000))
    materials: list[dict] = []
    screenshots: list[str] = []

    try:
        with sync_playwright() as playwright:
            # 独立 Chromium：自带二进制 + 项目内独立用户数据目录
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(user_data_dir),
                headless=bool(job.get("headless", True)),
                args=list(job.get("extra_args", [])),
            )
            try:
                context.set_default_timeout(nav_timeout)
                _inject_credentials(context, job.get("credentials") or {})
                page = context.pages[0] if context.pages else context.new_page()
                # Vault「用户名 / 密码」连接器：凭证经 stdin 进入子进程，只在隔离页面内填写
                for login in (job.get("credentials") or {}).get("form_login", []) or []:
                    try:
                        if login.get("url"):
                            page.goto(str(login["url"]), wait_until="domcontentloaded",
                                      timeout=nav_timeout)
                        page.fill(str(login["username_selector"]), str(login.get("username", "")))
                        page.fill(str(login["password_selector"]), str(login.get("password", "")))
                        if login.get("submit_selector"):
                            page.click(str(login["submit_selector"]))
                        materials.append({"type": "text", "source": "form_login",
                                          "value": "已用 Vault 连接器完成表单登录（凭证不回显）"})
                    except Exception as exc:
                        materials.append({"type": "text", "source": "form_login",
                                          "value": f"表单登录失败：{str(exc)[:120]}"})
                for action in job.get("actions", []) or []:
                    kind = action.get("type")
                    if kind == "goto":
                        page.goto(str(action.get("url", "")), wait_until="domcontentloaded",
                                  timeout=nav_timeout)
                        materials.append({"type": "url", "source": "navigation",
                                          "value": str(action.get("url", ""))[:200]})
                    elif kind == "fill":
                        page.fill(str(action.get("selector", "")), str(action.get("value", "")))
                    elif kind == "click":
                        page.click(str(action.get("selector", "")))
                    elif kind == "extract":
                        text = page.locator(str(action.get("selector", "body"))).first.inner_text(
                            timeout=nav_timeout)
                        materials.append({"type": "text", "source": "page",
                                          "value": (text or "")[:1500]})
                    elif kind == "screenshot":
                        target = out_dir / paths.safe_id(str(action.get("name", "shot.png")))
                        page.screenshot(path=str(target), full_page=False)
                        screenshots.append(str(target))
                    elif kind == "wait":
                        page.wait_for_timeout(int(action.get("ms", 500)))
                return {
                    "ok": True, "simulated": False,
                    "session": user_data_dir.name,
                    "materials": materials, "screenshots": screenshots,
                }
            finally:
                context.close()
    except Exception as exc:
        # 错误信息可能含 URL 查询串，做脱敏后再回传
        from core.logger import redact
        return {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:300],
                "materials": materials, "screenshots": screenshots}
