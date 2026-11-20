"""Flask 本机控制面板：对话工作台 / 看板 / 配置 / Skill / API 源 / 日志 + 全部 JSON 接口。

仅由 main.py 在 127.0.0.1 上启动，use_reloader=False，不开放外网。
所有敏感配置只返回脱敏视图；vault 凭证永不出现在任何响应正文。
"""

# ============================================================
# 【核心模块】面板层 · Flask 控制面板
# 作用：仅监听 127.0.0.1；对话工作台/看板/配置/Skill/API 源/日志页面与 JSON 接口；敏感信息只回脱敏视图
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import time
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_from_directory, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.utils import secure_filename

from core import paths
from core.logger import get_logger, redact, snapshot as log_snapshot
from core.models import TaskPriority, TaskSpec
from agent_engine.skill_loader import SkillLoader

LOG = get_logger("panel")

PROVIDER_EDITABLE = {
    "api_key", "enabled", "weight", "priority", "rpm", "max_concurrency", "timeout_s",
    "model", "path", "base_url", "style", "kind", "doc", "quota_note", "risk_note",
}
PRIORITIES = {
    "high_test": TaskPriority.HIGH_TEST, "test": TaskPriority.HIGH_TEST,
    "manual": TaskPriority.MANUAL, "scheduled": TaskPriority.SCHEDULED,
}


def create_app(ctx: dict) -> Flask:
    here = Path(__file__).resolve().parent
    app = Flask(__name__, template_folder=str(here / "templates"),
                static_folder=str(here / "static"))
    app.json.ensure_ascii = False
    # 本机开发态：模板与静态资源改动即时生效，不必每次重启服务
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    config = ctx["config"]
    db = ctx["db"]
    scheduler = ctx["scheduler"]
    bridge = ctx["bridge"]
    pool = ctx["pool"]
    vault = ctx["vault"]
    loader = SkillLoader()

    @app.context_processor
    def inject_globals():
        return {"version": ctx["version"], "host": ctx["host"], "port": ctx["port"]}

    def body() -> dict:
        return request.get_json(silent=True) or {}

    def fail(exc: Exception, code: int = 400):
        return jsonify({"ok": False, "error": redact(str(exc))[:200]}), code

    # ---------------- 页面 ----------------
    @app.get("/")
    def page_chat():
        return render_template("chat.html", page="chat")

    @app.get("/board")
    def page_dashboard():
        return render_template("dashboard.html", page="dashboard")

    @app.get("/config")
    def page_config():
        masked = config.masked()
        key_count = sum(1 for p in masked.get("providers", []) if p.get("has_key"))
        return render_template("config.html", page="config", config=masked,
                               key_count=key_count)

    @app.get("/skills")
    def page_skills():
        skills = loader.list_skills()
        return render_template("skills.html", page="skills", skills=skills,
                               enabled_count=sum(1 for s in skills if s["enabled"]))

    @app.get("/sources")
    def page_sources():
        return render_template("sources.html", page="sources", sources=pool.snapshot())

    @app.get("/logs")
    def page_logs():
        return render_template("logs.html", page="logs")

    @app.get("/memory")
    def page_memory():
        return render_template("memory.html", page="memory")

    # ---------------- 状态 / 任务 ----------------
    @app.get("/api/status")
    def api_status():
        try:
            status = scheduler.snapshot()
        except Exception as exc:
            status = {"active": [], "queued": [], "active_count": 0, "queue_size": 0,
                      "max_concurrency": config.section("scheduler").get("max_concurrency", 2),
                      "degraded": False, "hardware": ctx["hardware"].snapshot(),
                      "stats": {}, "today": 0, "daily_limit": 0,
                      "providers": pool.snapshot(), "error": redact(str(exc))[:120]}
        status.update({
            "ok": True, "version": ctx["version"], "host": ctx["host"],
            "port": ctx["port"], "sessions": len(paths.list_sessions()),
            "vault_sessions": len(vault.sessions_snapshot()),
            "server_time": time.time(),
        })
        return jsonify(status)

    @app.get("/api/pipeline")
    def api_pipeline():
        # 阶段插件注册表：名称/权限/并行组/启停状态，供看板与插件管理展示
        from agent_engine import pipeline as _pipeline
        from agent_engine import task_loop as _task_loop  # noqa: F401  导入即完成内置插件注册
        return jsonify({"ok": True, "plugins": _pipeline.describe_all(config)})

    @app.get("/api/memory")
    def api_memory():
        memory = ctx.get("memory")
        if memory is None:
            return jsonify({"ok": True, "enabled": False, "items": [], "stats": {"total": 0, "by_kind": {}}})
        query = request.args.get("q", "")
        kind = request.args.get("kind") or None
        if query:
            items = memory.recall(query=query, kind=kind, limit=30)
        else:
            items = memory.recent(limit=50, kind=kind)
        return jsonify({"ok": True, "enabled": True, "items": items, "stats": memory.stats()})

    @app.delete("/api/memory/<int:memory_id>")
    def api_memory_delete(memory_id: int):
        memory = ctx.get("memory")
        if memory is None:
            return fail(RuntimeError("记忆模块未启用"))
        return jsonify({"ok": memory.delete(memory_id)})

    @app.get("/api/tasks")
    def api_tasks():
        return jsonify({"ok": True, "active": scheduler.active_snapshot(),
                        "queued": scheduler.queue_snapshot(),
                        "history": db.list_tasks(80)})

    @app.post("/api/tasks/submit")
    def api_submit():
        """面板手动触发：支持一次提交多条并行任务、选择优先级、插队或排队。"""
        data = body()
        try:
            count = min(20, max(1, int(data.get("count", 1))))
            priority = PRIORITIES.get(str(data.get("priority", "manual")), TaskPriority.MANUAL)
            mode = str(data.get("enqueue_mode")
                       or config.section("scheduler").get("manual_enqueue_mode", "queue"))
            mode = mode if mode in ("insert", "queue") else "queue"
            skill = str(data.get("skill") or loader.default_skill())
            instruction = str(data.get("instruction") or "面板手动触发任务")[:300]
            dry_run = bool(data.get("dry_run", True))
            source = "test" if priority == TaskPriority.HIGH_TEST else "manual"
            conv_id = str(data.get("conversation_id") or "")
            if conv_id and not db.get_conversation(conv_id):
                conv_id = ""
            if conv_id:
                db.add_message(conv_id, "user", instruction)
            task_ids = []
            for index in range(count):
                spec = TaskSpec.create(
                    priority=priority, source=source, enqueue_mode=mode,
                    skill=skill, instruction=f"{instruction} #{index + 1}",
                    dry_run=dry_run, conversation_id=conv_id)
                task_id = bridge.submit(spec)
                task_ids.append(task_id)
                if conv_id:
                    db.add_message(conv_id, "assistant",
                                   f"已提交任务 {task_id}，等待调度执行",
                                   task_id=task_id, meta={"queued": True})
            LOG.info("面板提交 %s 条任务 priority=%s mode=%s dry_run=%s",
                     len(task_ids), priority.name, mode, dry_run)
            return jsonify({"ok": True, "task_ids": task_ids, "count": len(task_ids),
                            "conversation_id": conv_id})
        except Exception as exc:
            return fail(exc, 503)

    @app.post("/api/tasks/<task_id>/cancel")
    def api_cancel(task_id: str):
        """单独终止一个 task_id：不杀主程序、不影响其他并行任务。"""
        try:
            cancelled = bridge.cancel(paths.safe_id(task_id))
            return jsonify({"ok": bool(cancelled), "task_id": task_id})
        except Exception as exc:
            return fail(exc, 503)

    # ---------------- 日志 ----------------
    @app.get("/api/logs")
    def api_logs():
        try:
            since = float(request.args.get("since", 0))
        except ValueError:
            since = 0.0
        return jsonify({
            "ok": True, "now": time.time(),
            "logs": log_snapshot(limit=int(request.args.get("limit", 300)),
                                 task_id=request.args.get("task_id") or None,
                                 level=request.args.get("level") or None,
                                 since=since),
        })

    # ---------------- 配置 / 凭证 ----------------
    @app.get("/api/config")
    def api_config_get():
        return jsonify({"ok": True, "config": config.masked()})

    @app.post("/api/config")
    def api_config_save():
        try:
            payload = body()
            incoming = payload.pop("providers", None)
            if incoming is not None:
                providers = config.providers()
                index = {p["name"]: p for p in providers}
                for item in incoming:
                    name = str(item.get("name", ""))
                    if name not in index or not isinstance(item, dict):
                        continue
                    for key, value in item.items():
                        if key not in PROVIDER_EDITABLE:
                            continue
                        if key == "api_key" and (not value or value == "******"):
                            continue  # 脱敏占位表示保持原值
                        index[name][key] = value
                payload["providers"] = list(index.values())
            saved = config.save(payload)
            LOG.info("面板保存配置成功（密钥保持脱敏）")
            return jsonify({"ok": True, "config": config.masked(),
                            "version": saved.get("version", 1)})
        except Exception as exc:
            return fail(exc)

    @app.get("/api/vault/connectors")
    def api_vault_connectors():
        """自定义连接器类型定义（不含任何凭证值），供面板动态渲染字段。"""
        return jsonify({"ok": True, "connectors": vault.connector_schema(),
                        "encrypted": True})

    @app.get("/api/vault")
    def api_vault_list():
        return jsonify({"ok": True, "cards": vault.list_cards(),
                        "sessions": vault.sessions_snapshot(), "encrypted": True})

    @app.post("/api/vault")
    def api_vault_add():
        try:
            data = body()
            fields = data.get("fields")
            card = vault.add_card(
                str(data.get("name", "")), str(data.get("kind", "api_key")),
                fields if isinstance(fields, dict) else None,
                str(data.get("note", "")), str(data.get("secret", "")))
            LOG.info("vault 新增连接器 %s（加密保存）", card["name"])
            return jsonify({"ok": True, "card": card})
        except Exception as exc:
            return fail(exc)

    @app.delete("/api/vault/<name>")
    def api_vault_delete(name: str):
        return jsonify({"ok": vault.delete_card(paths.safe_id(name))})

    @app.post("/api/vault/<name>/toggle")
    def api_vault_toggle(name: str):
        return jsonify({"ok": vault.toggle_card(paths.safe_id(name),
                                                 bool(body().get("enabled", True)))})

    # 兼容尚在浏览器缓存中的旧版前端
    @app.get("/api/wallet")
    def api_vault_legacy_list():
        return api_vault_list()

    @app.post("/api/wallet")
    def api_vault_legacy_add():
        return api_vault_add()

    @app.delete("/api/wallet/<name>")
    def api_vault_legacy_delete(name: str):
        return api_vault_delete(name)

    @app.post("/api/wallet/<name>/toggle")
    def api_vault_legacy_toggle(name: str):
        return api_vault_toggle(name)

    # ---------------- 会话记忆（SQLite 持久化） ----------------
    @app.get("/api/conversations")
    def api_conversations():
        return jsonify({"ok": True, "conversations": db.list_conversations()})

    @app.post("/api/conversations")
    def api_conversation_create():
        title = str(body().get("title") or "新会话")[:80]
        return jsonify({"ok": True, "id": db.create_conversation(title), "title": title})

    @app.post("/api/conversations/<conv_id>")
    def api_conversation_update(conv_id: str):
        data = body()
        action = str(data.get("action") or "rename")
        if action == "delete":
            ok = db.delete_conversation(conv_id)
        elif action == "archive":
            ok = db.archive_conversation(conv_id)
        else:
            ok = db.rename_conversation(conv_id, str(data.get("title") or "新会话"))
        return jsonify({"ok": ok, "id": conv_id})

    @app.get("/api/conversations/<conv_id>")
    def api_conversation_detail(conv_id: str):
        conv = db.get_conversation(conv_id)
        if not conv:
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        tasks = db.list_tasks_by_conversation(conv_id)
        active = {str(t.get("task_id", "")): t for t in scheduler.active_snapshot()}
        for task in tasks:
            if task["task_id"] in active:
                task.update(active[task["task_id"]])
        messages = db.list_messages(conv_id)
        # 收尾消息只补写一次，保证刷新页面后对话记忆完整且可去重
        done = {m.get("task_id") for m in messages
                if m.get("role") == "assistant" and m.get("meta", {}).get("done")}
        for task in tasks:
            tid = task["task_id"]
            status = str(task.get("status", "")).lower()
            if status not in ("succeeded", "success", "failed", "cancelled") or tid in done:
                continue
            label = ("任务已完成" if status in ("succeeded", "success") else
                     f"任务{'失败' if status == 'failed' else '已终止'}："
                     f"{str(task.get('error') or '')[:120]}")
            db.add_message(conv_id, "assistant", f"{label}（{tid}）",
                           task_id=tid, meta={"done": True, "status": status})
            messages = db.list_messages(conv_id)
            done.add(tid)
        return jsonify({"ok": True, "conversation": conv,
                        "messages": messages, "tasks": tasks})

    @app.post("/api/conversations/<conv_id>/messages")
    def api_conversation_message(conv_id: str):
        if not db.get_conversation(conv_id):
            return jsonify({"ok": False, "error": "会话不存在"}), 404
        text = str(body().get("content") or "")[:2000]
        if not text:
            return jsonify({"ok": False, "error": "消息不能为空"}), 400
        return jsonify({"ok": True, "id": db.add_message(conv_id, "user", text)})

    # ---------------- Skill ----------------
    @app.get("/api/skills")
    def api_skills():
        return jsonify({"ok": True, "skills": loader.list_skills()})

    @app.get("/api/skills/<name>")
    def api_skill_get(name: str):
        try:
            return jsonify({"ok": True, "skill": loader.get(name)})
        except Exception as exc:
            return fail(exc, 404)

    @app.post("/api/skills/<name>")
    def api_skill_save(name: str):
        try:
            return jsonify({"ok": True, "skill": loader.save(name, str(body().get("content", "")))})
        except Exception as exc:
            return fail(exc)

    @app.delete("/api/skills/<name>")
    def api_skill_delete(name: str):
        try:
            return jsonify({"ok": loader.delete(name)})
        except Exception as exc:
            return fail(exc)

    # ---------------- API 源 / 浏览器会话 ----------------
    @app.get("/api/sources")
    def api_sources():
        return jsonify({"ok": True, "sources": pool.snapshot(),
                        "usage": pool.usage_snapshot(),
                        "history": db.list_api_usage(60)})

    @app.post("/api/sources/<name>")
    def api_source_update(name: str):
        try:
            providers = config.providers()
            target = next((p for p in providers if p["name"] == name), None)
            if target is None:
                return jsonify({"ok": False, "error": "API 源不存在"}), 404
            for key, value in body().items():
                if key in PROVIDER_EDITABLE and not (key == "api_key" and not value):
                    target[key] = value
            config.save({"providers": providers})
            return jsonify({"ok": True, "sources": pool.snapshot()})
        except Exception as exc:
            return fail(exc)

    @app.get("/api/sessions")
    def api_sessions():
        return jsonify({"ok": True, "chromium": paths.list_sessions(), "browserr": paths.list_sessions(),
                        "vault": vault.sessions_snapshot()})

    @app.post("/api/sessions/reset")
    def api_sessions_reset():
        """清空 browser_profile 会话目录；有活跃任务时拒绝，避免删掉正在使用的配置。"""
        if scheduler.active_snapshot():
            return jsonify({"ok": False, "error": "仍有运行中的任务，请先等待或终止后再重置"}), 409
        count = paths.reset_browser_env()
        LOG.warning("面板重置浏览器环境，清理 %s 个独立会话目录", count)
        return jsonify({"ok": True, "removed": count})

    # ---------------- 产物预览 ----------------
    @app.get("/api/outputs")
    def api_outputs():
        items = []
        root = paths.DIRS["output"]
        if root.exists():
            directories = sorted((p for p in root.iterdir() if p.is_dir()
                                  and not p.name.startswith(".")),
                                 key=lambda p: p.stat().st_mtime, reverse=True)[:30]
            for directory in directories:
                for file in sorted(directory.iterdir(), key=lambda p: p.stat().st_mtime,
                                   reverse=True):
                    if not file.is_file():
                        continue
                    suffix = file.suffix.lower()
                    if suffix not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4",
                                      ".webm", ".json"):
                        continue
                    items.append({
                        "task_id": directory.name, "name": file.name,
                        "url": url_for("serve_output", task_id=directory.name,
                                       filename=file.name),
                        "kind": "image" if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif")
                                else ("video" if suffix in (".mp4", ".webm") else "manifest"),
                        "size": file.stat().st_size,
                        "mtime": file.stat().st_mtime,
                    })
        return jsonify({"ok": True, "items": items[:60]})

    @app.get("/output/<task_id>/<path:filename>")
    def serve_output(task_id: str, filename: str):
        directory = paths.DIRS["output"] / paths.safe_id(task_id)
        filename = secure_filename(Path(filename).name)
        if not directory.is_dir() or not (directory / filename).is_file():
            abort(404)
        return send_from_directory(str(directory), filename)

    @app.errorhandler(Exception)
    def on_error(exc):
        if isinstance(exc, HTTPException):
            return exc
        LOG.error("面板请求异常：%s", redact(str(exc))[:200])
        return jsonify({"ok": False, "error": redact(str(exc))[:200]}), 500

    return app
