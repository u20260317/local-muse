"""Skill / System Prompt 库读写（docs/skills/*.md）。

不引入 yaml 依赖：只解析极简 front-matter（key: value）。
所有文件名都经过白名单与目录穿越校验，面板可安全地查看/新增/编辑/删除。
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from core import paths

NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    """解析 --- 包裹的元数据块，返回 (meta, 正文)。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    meta: dict = {}
    end = 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
        if ":" in lines[index]:
            key, _, value = lines[index].partition(":")
            meta[key.strip()] = value.strip().strip('"').strip("'")
    if not end:
        return {}, text
    return meta, "\n".join(lines[end + 1:]).strip()


class SkillLoader:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else paths.DIRS["skills"]
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        """把技能名映射为 docs/skills 内的 .md 路径，杜绝 ../ 路径穿越。"""
        clean = str(name or "").removesuffix(".md")
        if not NAME_RE.fullmatch(clean):
            raise ValueError("技能名只允许字母、数字、下划线、连字符（1-64 位）")
        candidate = (self.root / f"{clean}.md").resolve()
        if self.root.resolve() not in candidate.parents:
            raise ValueError("非法技能路径")
        return candidate

    def list_skills(self) -> list[dict]:
        items = []
        for path in sorted(self.root.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
                meta, body = _parse_front_matter(text)
            except OSError:
                meta, body, text = {}, "", ""
            items.append({
                "name": path.stem,
                "title": meta.get("title", path.stem),
                "description": meta.get("description", ""),
                "enabled": str(meta.get("enabled", "true")).lower() != "false",
                "updated_at": round(path.stat().st_mtime, 2),
                "chars": len(body),
                "path": str(path),
            })
        return items

    def get(self, name: str) -> dict:
        path = self._path(name)
        if not path.exists():
            raise FileNotFoundError(f"技能不存在：{path.stem}")
        text = path.read_text(encoding="utf-8")
        meta, body = _parse_front_matter(text)
        return {
            "name": path.stem,
            "title": meta.get("title", path.stem),
            "description": meta.get("description", ""),
            "enabled": str(meta.get("enabled", "true")).lower() != "false",
            "meta": meta,
            "body": body,
            "raw": text,
            "updated_at": round(path.stat().st_mtime, 2),
            "path": str(path),
        }

    def save(self, name: str, content: str) -> dict:
        """新增或覆盖技能；内容大小限制 200KB，避免误写大文件。"""
        path = self._path(name)
        payload = str(content or "")
        if len(payload.encode("utf-8")) > 200 * 1024:
            raise ValueError("技能内容超过 200KB")
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)
        return self.get(path.stem)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if path.stem in ("blessing_video_hit_v2", "web_research", "auto_optimize_prompt"):
            raise ValueError("内置技能不可删除，可编辑或禁用")
        if not path.exists():
            return False
        path.unlink()
        return True

    def default_skill(self) -> str:
        names = [s["name"] for s in self.list_skills()]
        return "blessing_video_hit_v2" if "blessing_video_hit_v2" in names else (names[0] if names else "")

    def enabled_names(self) -> list[str]:
        return [s["name"] for s in self.list_skills() if s["enabled"]]
