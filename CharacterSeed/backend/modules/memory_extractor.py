"""
记忆提取器（Memory Extractor）

职责：
  - 从对话/事件中提取"事实/偏好/情绪碎片"
  - 写入 Memory 表（带 importance/theme/strength/decay_rate）
  - 复用 Director LLM（避免额外 LLM 接入）

设计：
  - 提示词：backend/prompts/extractor.txt
  - 温度：0.3（稳定提取）
  - 输出：JSON 数组 [{content, importance, theme, type}]
  - 异步执行（不阻塞主流程）
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import Conversation, Memory
from backend.modules.memory_decay import compute_half_life_days
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)

VALID_THEMES = {"identity", "music", "taste", "moment", "todo"}
THEME_TO_DECAY_KEY = {
    "identity": "identity",
    "music":    "music",
    "taste":    "taste",
    "moment":   "moment",
    "todo":     "todo",
    None:       "default",
    "default":  "default",
}


def _load_prompt() -> str:
    """加载 extractor 提示词"""
    candidates = [
        "backend/prompts/extractor.txt",
        os.path.join(os.path.dirname(__file__), "..", "prompts", "extractor.txt"),
    ]
    for p in candidates:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                return f.read()
    return _FALLBACK_PROMPT


_FALLBACK_PROMPT = """你是一个记忆提取器。从以下对话片段中，提取值得"长期记住"的事实/偏好/情绪碎片。

输出严格 JSON 数组（不要任何解释文字，不要 ```json``` 标记）：
[
  {{
    "content": "一句话描述（中文）",
    "importance": 1-10,
    "theme": "identity | music | taste | moment | todo",
    "type": "fact | preference | emotion"
  }}
]

提取规则：
- 重要性 ≥ 6：用户的核心偏好、长期目标、身份相关
- 重要性 4-5：偏好/喜好、阶段性目标
- 重要性 ≤ 3：当下瞬间、临时信息
- 只提取"值得未来回想起"的内容，不提取寒暄和废话
- 0-3 条都可以，没有则返回空数组 []

对话：
{conversation}

仅输出 JSON 数组。"""


def extract_memories_from_conversation(
    user_input: str,
    npc_response: str,
    llm_service: Optional[LLMService] = None,
    max_items: int = 5,
) -> List[Dict[str, Any]]:
    """
    从单条对话提取记忆碎片（不写库）。

    Args:
        user_input: 用户输入
        npc_response: 角色回复
        llm_service: LLM 服务（默认新实例）
        max_items: 最多返回几条

    Returns:
        [{content, importance, theme, type}, ...]
    """
    llm = llm_service or LLMService()
    prompt = _load_prompt()

    # 拼接对话
    conversation = f"[用户]: {user_input}\n[角色]: {npc_response}"

    system_prompt = (
        "你是一个记忆提取器。从对话中提取值得长期记住的信息。"
        "仅输出 JSON 数组，不要任何解释文字。"
    )

    try:
        if "{conversation}" in prompt:
            user_msg = prompt.format(conversation=conversation)
        else:
            user_msg = prompt + "\n\n对话：\n" + conversation
        raw = llm.call(
            prompt=user_msg,
            system_prompt=system_prompt,
            temperature=0.3,
            response_format={"type": "json_object"},
            task="memory_extraction",
        )
    except Exception as e:
        logger.warning("extractor LLM 调用失败: %s", e)
        return []

    parsed = llm.parse_json_response(raw)
    items: List[Dict[str, Any]] = []
    if isinstance(parsed, list):
        items = parsed
    elif isinstance(parsed, dict):
        # 兼容 {"memories": [...]} 包装
        for key in ("memories", "items", "extracted", "data"):
            if key in parsed and isinstance(parsed[key], list):
                items = parsed[key]
                break
        if not items:
            # 兼容"对象就是唯一一条"
            if "content" in parsed:
                items = [parsed]

    # 验证 + 截断
    results: List[Dict[str, Any]] = []
    for item in items[:max_items]:
        if not isinstance(item, dict):
            continue
        content = (item.get("content") or "").strip()
        if not content:
            continue
        try:
            importance = int(item.get("importance", 5))
        except Exception:
            importance = 5
        importance = max(1, min(10, importance))
        theme = item.get("theme")
        if theme not in VALID_THEMES:
            theme = None
        mem_type = (item.get("type") or "fact").strip()
        if mem_type not in {"fact", "preference", "emotion"}:
            mem_type = "fact"
        results.append({
            "content": content[:1000],
            "importance": importance,
            "theme": theme,
            "type": mem_type,
        })
    return results


def _theme_to_decay_rate(theme: Optional[str]) -> int:
    """把 theme 映射到 0-100 的 decay_rate（供 DB 存）"""
    key = THEME_TO_DECAY_KEY.get(theme, "default")
    from backend.modules.memory_decay import THEME_DECAY_CONFIG
    base, _, _ = THEME_DECAY_CONFIG.get(key, THEME_DECAY_CONFIG["default"])
    return int(round(base * 1000))  # 0.005 → 5, 0.05 → 50


def save_memories(
    db: Session,
    character_id: int,
    items: List[Dict[str, Any]],
    source_msg_id: Optional[int] = None,
) -> List[int]:
    """
    把提取的记忆写库。返回写入的 memory ids。
    """
    if not items:
        return []
    ids: List[int] = []
    try:
        for item in items:
            importance = item.get("importance", 5)
            theme = item.get("theme")
            mem_type = item.get("type", "fact")
            row = Memory(
                character_id=character_id,
                content=item["content"],
                importance=importance,
                memory_type=mem_type,
                theme=theme,
                strength=importance,  # 初始 strength = importance
                recall_count=0,
                forgotten=0,
                decay_rate=_theme_to_decay_rate(theme),
                source_msg_id=source_msg_id,
            )
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    except Exception as e:
        logger.error("save_memories 失败: %s", e)
        db.rollback()
        return []
    return ids


def extract_and_save(
    db: Session,
    character_id: int,
    conversation: Conversation,
    llm_service: Optional[LLMService] = None,
) -> List[int]:
    """
    一站式：从 conversation 提取并保存。返回写入的 ids。
    """
    items = extract_memories_from_conversation(
        user_input=conversation.user_input or "",
        npc_response=conversation.npc_response or "",
        llm_service=llm_service,
    )
    return save_memories(
        db=db,
        character_id=character_id,
        items=items,
        source_msg_id=conversation.id,
    )


def extract_from_text(
    user_input: str,
    npc_response: str,
) -> List[Dict[str, Any]]:
    """兼容旧接口的简单包装"""
    return extract_memories_from_conversation(user_input, npc_response)


# 兼容旧 import 路径
extract_from_conversation = extract_memories_from_conversation


__all__ = [
    "extract_memories_from_conversation",
    "extract_and_save",
    "extract_from_text",
    "extract_from_conversation",
    "save_memories",
    "VALID_THEMES",
]
