"""浏览器任务执行体：只启动项目内 Browserr（Servo）引擎，不再使用 Chromium。

隔离铁则（本文件是全项目唯一启动浏览器的位置）：
- 只调用由 scripts/install_browserr.* 下载到项目内 browserr_bin/ 的
  Browserr 官方 release：engine 二进制 + driver.py（Python SDK，纯标准库）
- 用户数据目录固定为项目内 browser_profile/session_<task_id>/
  （作为 Browserr 的 --profile-dir 传入，Servo profile 与 Chromium 无关）
- 不指定任何系统浏览器通道、不连接本机 Chrome / Edge / Firefox
- 凭证只注入这个隔离会话，绝不写入本机日常浏览器

与 Chromium/Playwright 版的接口契约保持不变（job dict -> result dict），
上层 task_loop / sandbox_wrapper 无需改动：
  actions: goto / fill / click / extract / screenshot / wait
  credentials: cookies / headers / storage / form_login

已知差异（Browserr/Servo 的能力边界，如实标注，不假装等价）：
- 自定义请求头（headers）：Browserr SDK 没有等价的 context 级注入口，
  本实现会记录一条 material 说明并跳过，不会静默冒充已注入。
- Cookie：没有预导航注入 API，改为在每次 goto 落地后用 document.cookie
  写入当前域可写的 Cookie（HttpOnly Cookie 无法用此方式写入）。
- localStorage：同样在 goto 落地后用 eval_js 写入。
- HTTP Auth 类凭证：通过 Driver 的 auth challenge 机制处理
  （vault 的 form_login 仍走表单填写）。
"""

# ============================================================
# 【核心模块】浏览器执行体 · Browserr
# 作用：全项目唯一启动浏览器的位置：只用项目内 browserr_bin 的 Servo 引擎，job dict → result dict 契约不变
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

from core import paths


def _load_driver_module():
    """从项目内 browserr_bin/driver.py 加载 Browserr SDK（不依赖 pip 包）。"""
    driver_path = paths.browserr_driver_path()
    if not driver_path.exists():
        raise ImportError(
            "未找到 Browserr SDK（browserr_bin/driver.py），"
            "请运行 scripts/install_browserr.* 下载官方 release")
    spec = importlib.util.spec_from_file_location("browserr_driver", driver_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("browserr_driver", module)
    spec.loader.exec_module(module)
    return module


def _apply_web_storage(driver, credential: dict) -> None:
    """goto 落地后注入 Cookie / localStorage（Browserr 无预导航注入口）。"""
    storage = credential.get("storage") or {}
    if isinstance(storage, dict) and storage:
        script = "try{" + "".join(
            f"localStorage.setItem({json.dumps(str(k))},{json.dumps(str(v))});"
            for k, v in storage.items()
        ) + "}catch(e){}"
        try:
            driver.eval_js(script)
        except Exception:
            pass
    for item in credential.get("cookies", []) or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        pair = f"{item['name']}={item.get('value', '')}"
        path = str(item.get("path", "/"))
        try:
            driver.eval_js(
                f"try{{document.cookie={json.dumps(pair + '; path=' + path)};}}catch(e){{}}")
        except Exception:
            pass


def _simulated_result(job: dict) -> dict:
    """dry_run 自检：不联网、不启动 Browserr，但完整回传链路结构。"""
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

    paths.setup_browserr_env()
    try:
        sdk = _load_driver_module()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}

    engine = paths.browserr_engine_path()
    if not engine.exists():
        return {"ok": False,
                "error": "Browserr 引擎未下载到项目内 browserr_bin/，请运行 scripts/install_browserr.*"}

    task_id = paths.safe_id(str(job.get("task_id", "")))
    user_data_dir = Path(job.get("user_data_dir") or paths.session_dir(task_id))
    user_data_dir.mkdir(parents=True, exist_ok=True)
    out_dir = paths.output_dir(task_id)
    nav_timeout_s = max(5.0, int(job.get("navigation_timeout_ms", 30000)) / 1000)
    materials: list[dict] = []
    screenshots: list[str] = []
    credential = job.get("credentials") or {}

    if credential.get("headers"):
        materials.append({"type": "text", "source": "credentials",
                          "value": "提示：Browserr SDK 无 context 级自定义请求头注入口，"
                                   "headers 凭证本次未注入（其余凭证照常）"})

    driver = None
    try:
        import os
        driver = sdk.Driver(
            engine_path=str(engine),
            profile_dir=str(user_data_dir),
            # 并发任务各自是独立子进程，端口用 PID 派生，避免撞默认 7411
            port=7411 + (os.getpid() % 200),
        )
        try:
            driver.start_auto_auth()
        except Exception:
            pass

        def goto(url: str) -> None:
            driver.navigate(str(url))
            try:
                driver.wait_ready(timeout=nav_timeout_s)
            except Exception:
                pass  # 未在超时内 ready 不算致命，后续动作会自行报错
            _apply_web_storage(driver, credential)

        # Vault「用户名 / 密码」连接器：凭证经 stdin 进入子进程，只在隔离页面内填写
        for login in credential.get("form_login", []) or []:
            try:
                if login.get("url"):
                    goto(str(login["url"]))
                driver.set_value(str(login["username_selector"]), str(login.get("username", "")))
                driver.set_value(str(login["password_selector"]), str(login.get("password", "")))
                if login.get("submit_selector"):
                    driver.click_element(str(login["submit_selector"]))
                materials.append({"type": "text", "source": "form_login",
                                  "value": "已用 Vault 连接器完成表单登录（凭证不回显）"})
            except Exception as exc:
                materials.append({"type": "text", "source": "form_login",
                                  "value": f"表单登录失败：{str(exc)[:120]}"})

        for action in job.get("actions", []) or []:
            kind = action.get("type")
            if kind == "goto":
                goto(str(action.get("url", "")))
                materials.append({"type": "url", "source": "navigation",
                                  "value": str(action.get("url", ""))[:200]})
            elif kind == "fill":
                driver.set_value(str(action.get("selector", "")), str(action.get("value", "")),
                                 timeout=nav_timeout_s)
            elif kind == "click":
                driver.click_element(str(action.get("selector", "")), timeout=nav_timeout_s)
            elif kind == "extract":
                selector = str(action.get("selector", "body"))
                text = driver.eval_js(
                    "(function(){var e=document.querySelector("
                    + json.dumps(selector) + ");return e?e.innerText:'';})()",
                    timeout=nav_timeout_s)
                materials.append({"type": "text", "source": "page",
                                  "value": (str(text or ""))[:1500]})
            elif kind == "screenshot":
                _raw = str(action.get("name", "shot.png"))
                _stem, _dot, _ext = _raw.rpartition(".")
                _name = (paths.safe_id(_stem) + "." + "".join(
                    c for c in _ext if c.isalnum())[:8]) if _dot else paths.safe_id(_raw)
                target = out_dir / _name
                w, h, bgra = driver.screenshot(timeout=nav_timeout_s)
                sdk.save_png(str(target), w, h, bgra)
                screenshots.append(str(target))
            elif kind == "wait":
                time.sleep(int(action.get("ms", 500)) / 1000)
        return {
            "ok": True, "simulated": False, "engine": "browserr",
            "session": user_data_dir.name,
            "materials": materials, "screenshots": screenshots,
        }
    except Exception as exc:
        from core.logger import redact
        return {"ok": False, "error": redact(f"{type(exc).__name__}: {exc}")[:300],
                "materials": materials, "screenshots": screenshots}
    finally:
        if driver is not None:
            try:
                driver.close()
            except Exception:
                pass
