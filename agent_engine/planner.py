"""Agent 思考链与自主反思。

- 计划：加载 Skill → 读取同技能历史 → 优先调用免费文本模型 → 失败降级内置模板
- 反思：按 task_id 独立复盘，写 prompt_history，供下一轮迭代 prompt / 工作流
- 本模块属于「决策引擎」，是唯一可以申请 vault 临时凭证的板块（实际签发在 task_loop）
"""
from __future__ import annotations

import json
import re

from core.logger import get_logger

LOG = get_logger("planner")


def _template_plan(spec, skill: dict, history: list[dict]) -> dict:
    """无可用文本 API 时的确定性计划模板（不联网也能跑通全链路）。"""
    body = (skill.get("body") or "")[:1200]
    lessons = "；".join(
        (h.get("reflection") or "")[:60] for h in history[:3] if h.get("reflection")
    )
    topic = spec.instruction.strip() or skill.get("title") or "每日祝福短视频"
    style = "温暖治愈、国风轻渐变、竖屏 9:16、柔和光晕、字幕清晰"
    if "research" in skill.get("name", ""):
        style = "纪实感、冷暖对比、信息密度高、竖屏 9:16"
    prompt = (
        f"【技能】{skill.get('title', skill.get('name', '通用'))}\n"
        f"【选题】{topic}\n"
        f"【风格】{style}\n"
        f"【画面要求】主体明确、构图居中留白、色彩统一、避免文字水印、适合短视频首帧\n"
        f"【历史经验】{lessons or '暂无历史复盘，采用默认风格'}\n"
        f"【技能要点】{body[:400]}\n"
        f"【输出】一段可直接用于文生图与文生视频的中文画面描述，不要包含任何账号或凭证信息"
    )
    return {
        "prompt": prompt,
        "style": style,
        "topic": topic,
        "steps": ["browser", "image", "video", "reflect"],
        "needs_browser": "research" in skill.get("name", ""),
        "reference_image": None,
        "source": "template",
        "provider": "",
    }


def _extract_json(text: str) -> dict | None:
    """从模型输出里提取第一个 JSON 对象，容忍 ```json 包裹。"""
    try:
        match = re.search(r"\{[\s\S]*\}", text or "")
        return json.loads(match.group(0)) if match else None
    except Exception:
        return None


async def build_plan(services: dict, spec) -> dict:
    """生成单任务计划；任何外部调用失败都降级为模板，不阻断任务。"""
    loader = services["skill_loader"]
    db = services["db"]
    pool = services["pool"]
    try:
        skill = loader.get(spec.skill)
    except (FileNotFoundError, ValueError):
        skill = {"name": "default", "title": "通用", "body": "", "description": ""}
    history = db.recent_prompts(skill=skill.get("name"), limit=5)
    # SQLite 会话记忆：只取当前会话最近的角色/正文，绝不携带凭证或密钥
    conv_id = getattr(spec, "conversation_id", "") or ""
    conversation = db.conversation_context(conv_id, limit=8) if conv_id else []

    if not spec.dry_run:
        provider = await pool.pick("text", exclude=set())
        if provider:
            messages = [
                {"role": "system", "content": "你是短视频策划 Agent。只输出 JSON："
                 "{\"prompt\":...,\"style\":...,\"topic\":...,\"steps\":[...],"
                 "\"needs_browser\":false,\"reference_image\":null}。"
                 "禁止输出任何凭证、Cookie 或密钥。"},
                {"role": "user", "content":
                    f"选题：{spec.instruction}\n技能：{skill.get('title')}\n"
                    f"技能正文：{(skill.get('body') or '')[:800]}\n"
                    f"历史复盘：{[h.get('reflection', '')[:80] for h in history[:3]]}\n"
                    f"会话上下文：{[m['content'][:120] for m in conversation]}"},
            ]
            try:
                text = await pool.call_text(provider, messages, spec.task_id)
                parsed = _extract_json(text)
                if parsed and parsed.get("prompt"):
                    plan = _template_plan(spec, skill, history)
                    plan.update({k: v for k, v in parsed.items() if v is not None})
                    plan["source"] = "llm"
                    plan["provider"] = provider
                    LOG.info("task=%s 使用文本模型 %s 生成计划", spec.task_id, provider)
                    return plan
            except Exception as exc:
                LOG.warning("task=%s 文本模型失败，降级模板：%s", spec.task_id, str(exc)[:120])

    plan = _template_plan(spec, skill, history)
    if spec.dry_run:
        plan["source"] = "dry_run"
    LOG.info("task=%s 计划来源=%s 选题=%s", spec.task_id, plan["source"], plan["topic"][:40])
    return plan


async def reflect(services: dict, runtime, plan: dict, result: dict,
                  succeeded: bool = True) -> dict:
    """按 task_id 独立复盘：记录 prompt / 风格 / 选题 / 成败，产出下一轮优化建议。

    succeeded 由调用方显式传入：能走到复盘阶段说明流水线各阶段已成功；
    Scheduler 在 execute() 返回之后才把 runtime.status 置为 SUCCEEDED，故不能用该字段判断。
    """
    db = services["db"]
    spec = runtime.spec
    history = db.recent_prompts(skill=spec.skill, limit=10)
    success_count = sum(1 for h in history if float(h.get("score") or 0) >= 0.5)
    ratio = success_count / len(history) if history else 0.0

    suggestions = []
    if not succeeded:
        suggestions.append("上轮未完成，优先缩短素材采集路径并降低并发占用")
    if result.get("simulated"):
        suggestions.append("当前为 dry_run 自检，配置真实 API Key 后需重新评估画面质量")
    if runtime.retry > 0:
        suggestions.append("发生重试，建议延长单任务超时或降低单服务商并发配额")
    if ratio < 0.6 and history:
        suggestions.append("历史成功率偏低，收敛风格描述、强化首帧主体与字幕可读性")
    if not suggestions:
        suggestions.append("链路稳定，下一轮可增加镜头节奏与情绪层次描述")

    reflection = "；".join(suggestions)
    score = 1.0 if succeeded and not runtime.retry else (0.5 if succeeded else 0.0)
    db.add_prompt_history(
        task_id=spec.task_id, skill=spec.skill,
        prompt=plan.get("prompt", "")[:2000],
        reflection=f"状态={runtime.status.value} 重试={runtime.retry} 来源={plan.get('source')}；{reflection}",
        score=score,
    )
    note = {
        "task_id": spec.task_id,
        "skill": spec.skill,
        "topic": plan.get("topic", ""),
        "style": plan.get("style", ""),
        "score": score,
        "reflection": reflection,
        "next_prompt_hint": (plan.get("prompt", "")[:120] + "（强化：" + reflection[:60] + ")"),
    }
    LOG.info("task=%s 复盘完成 score=%.1f 建议=%s", spec.task_id, score, reflection[:80])
    return note
