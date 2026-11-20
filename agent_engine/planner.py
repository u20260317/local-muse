"""Agent 思考链与自主反思。

- 计划：加载 Skill → 读取同技能历史 → 优先调用免费文本模型 → 失败降级内置模板
- 反思：按 task_id 独立复盘，写 prompt_history，供下一轮迭代 prompt / 工作流
- 本模块属于「决策引擎」，是唯一可以申请 vault 临时凭证的板块（实际签发在 task_loop）
"""

# ============================================================
# 【核心模块】决策引擎 · 计划生成与反思
# 作用：加载 Skill 与历史复盘，优先用本地文本模型出计划，失败自动降级模板；任务后按 task_id 独立复盘并写回 prompt_history
# 定位与接口：见 docs/MODULES.md；设计约束：见 docs/DESIGN.md
# ============================================================
from __future__ import annotations

import json
import re

from core import token_meter
from core.logger import get_logger

LOG = get_logger("planner")


def _template_plan(spec, skill: dict, history: list[dict],
                   memory_lines: list[str] | None = None) -> dict:
    """无可用文本 API 时的确定性计划模板（不联网也能跑通全链路）。"""
    body = (skill.get("body") or "")[:1200]
    lessons = "；".join(
        list(memory_lines or []) +
        [(h.get("reflection") or "")[:60] for h in history[:3] if h.get("reflection")]
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
    # 模板分镜：景别交替（远景→中景→特写→收束），首镜即封面镜（主体突出）
    shots = ["远景全貌，交代环境与氛围，主体突出、上方留标题区",
             "中景主体，构图居中留白",
             "特写细节，质感与光影",
             "氛围收束，余韵悠长"]
    lines = [topic[:28], "换个角度，看看这一幕的细节",
             "光影之间，自有答案", "把这一幕，留给今天的你"]
    scenes = [{"image_prompt": f"{style}；{shot}；主题：{topic}",
               "narration": line} for shot, line in zip(shots, lines)]
    return {
        "prompt": prompt,
        "style": style,
        "topic": topic,
        "scenes": scenes,
        "steps": ["browser", "image", "video", "compose", "reflect"],
        "needs_browser": "research" in skill.get("name", ""),
        "reference_image": None,
        "source": "template",
        "provider": "",
    }


def _sanitize_scenes(plan: dict) -> list[dict]:
    """校验模型给出的分镜：最多 6 镜，字段强制成字符串并截断，缺画面词回退总提示词。"""
    raw = plan.get("scenes")
    scenes: list[dict] = []
    if isinstance(raw, list):
        for item in raw[:6]:
            if not isinstance(item, dict):
                continue
            scenes.append({
                "image_prompt": str(item.get("image_prompt")
                                    or plan.get("prompt", ""))[:600],
                "narration": str(item.get("narration") or "")[:80],
            })
    return scenes


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

    # Token 预算压缩：历史复盘与会话上下文先计量、超预算按「保新弃旧」规则压缩，
    # 避免本地小模型上下文被长历史撑爆（借鉴 DSH 的 token-meter/上下文压缩插件）
    planner_cfg = {}
    try:
        planner_cfg = services["config"].section("planner") or {}
    except Exception:
        planner_cfg = {}
    hist_budget = int(planner_cfg.get("history_token_budget", 600))
    conv_budget = int(planner_cfg.get("conversation_token_budget", 400))
    _refl = token_meter.compress_texts([h.get("reflection", "") for h in history], hist_budget)
    history = [dict(h, reflection=r) for h, r in zip(history, _refl)]
    _conv = token_meter.compress_texts([str(m.get("content", "")) for m in conversation], conv_budget)
    conversation = [dict(m, content=c) for m, c in zip(conversation, _conv)]

    # 长期记忆召回（core/memory.py）：同技能的历史经验 + 失败教训，跨任务跨重启可用
    memory_lines: list[str] = []
    memory = services.get("memory")
    if memory is not None:
        try:
            _lessons = memory.recall(query=f"{spec.instruction} {skill.get('title', '')}",
                                     kind="lesson", scope=skill.get("name"), limit=3)
            _fails = memory.recall(query=spec.instruction, kind="failure",
                                   scope=skill.get("name"), limit=2)
            memory_lines = ([f"经验：{m['content'][:120]}" for m in _lessons] +
                            [f"避坑（累计失败{m['hits']}次）：{m['content'][:120]}" for m in _fails])
            if memory_lines:
                LOG.info("task=%s 召回长期记忆 %s 条", spec.task_id, len(memory_lines))
        except Exception as exc:
            LOG.warning("task=%s 长期记忆召回失败（忽略）：%s", spec.task_id, str(exc)[:80])

    if not spec.dry_run:
        provider = await pool.pick("text", exclude=set())
        if provider:
            messages = [
                {"role": "system", "content": "你是短视频策划 Agent。只输出 JSON："
                 "{\"prompt\":...,\"style\":...,\"topic\":...,\"steps\":[...],"
                 "\"needs_browser\":false,\"reference_image\":null,"
                 "\"scenes\":[{\"image_prompt\":...,\"narration\":...}]}。"
                 "scenes 为 3-6 个分镜：首镜是封面镜（主体突出、上方留标题区），"
                 "景别要有交替（远景/中景/特写），每镜 narration 一句口播旁白（不超过 28 字）。"
                 "禁止输出任何凭证、Cookie 或密钥。"},
                {"role": "user", "content":
                    f"选题：{spec.instruction}\n技能：{skill.get('title')}\n"
                    f"技能正文：{(skill.get('body') or '')[:800]}\n"
                    f"历史复盘：{[h.get('reflection', '')[:80] for h in history[:3]]}\n"
                    f"长期记忆：{memory_lines}\n"
                    f"会话上下文：{[m['content'][:120] for m in conversation]}"},
            ]
            try:
                text = await pool.call_text(provider, messages, spec.task_id)
                parsed = _extract_json(text)
                if parsed and parsed.get("prompt"):
                    plan = _template_plan(spec, skill, history, memory_lines)
                    plan.update({k: v for k, v in parsed.items() if v is not None})
                    cleaned = _sanitize_scenes(plan)
                    if cleaned:
                        plan["scenes"] = cleaned
                    plan["source"] = "llm"
                    plan["provider"] = provider
                    LOG.info("task=%s 使用文本模型 %s 生成计划", spec.task_id, provider)
                    return plan
            except Exception as exc:
                LOG.warning("task=%s 文本模型失败，降级模板：%s", spec.task_id, str(exc)[:120])

    plan = _template_plan(spec, skill, history, memory_lines)
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
    # 同步沉淀到长期记忆：下次同技能任务计划前会被 recall 召回，不再只靠最近几条历史
    memory = services.get("memory")
    if memory is not None:
        try:
            memory.remember(kind="lesson", scope=spec.skill,
                            key=plan.get("topic", "") or spec.instruction[:40],
                            content=f"score={score}；{reflection}",
                            weight=1.0 if succeeded else 0.5)
        except Exception:
            pass
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
