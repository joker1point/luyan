"""
Location Aware — Director 世界上下文注入（ADR-009 / §3.5）

职责：
  - 把 WorldEngine.get_context_for_character() 的结果塞进 current_state._world
  - 零模板侵入：与 jiwen 注入 _jiwen 同策略
  - 失败静默：世界引擎未初始化时不影响主流程

参照：
  - backend/jiwen/jiwen_manager.py: get_prompt_context
  - backend/modules/interaction.py: InteractionPipeline.run() 第 1104-1129 行（jiwen 注入）
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def build_world_subfield(
    character_id: int,
    engine=None,
) -> Optional[Dict[str, Any]]:
    """
    构造 current_state._world 子字段。

    失败时返回 None（调用方塞 None 或跳过即可）。

    Args:
        character_id: 角色 ID
        engine: 可选 WorldEngine 实例（默认懒加载单例）

    Returns:
        Dict 形如：
        {
            "summary": "默认世界 / spring / day 32; 当前位置: 酒馆; 天气: rainy; 最近事件: 立春",
            "world": {"id": 1, "name": "默认世界", "season": "spring", "day": 32, "year": 1},
            "location": {...} | None,
            "weather": "rainy" | None,
            "recent_events": [...],
        }
    """
    try:
        if engine is None:
            from backend.world.world_engine import get_world_engine
            engine = get_world_engine()
        ctx = engine.get_context_for_character(character_id)
        if not ctx:
            return None

        # 拼 summary（自然语言片段，可注入 Director prompt）
        parts = []
        if ctx.get("world"):
            w = ctx["world"]
            parts.append(f"{w['name']} / {w['season']} / day {w['day']}")
        if ctx.get("location"):
            loc = ctx["location"]
            parts.append(f"当前位置: {loc['path']}")
        if ctx.get("weather"):
            parts.append(f"天气: {ctx['weather']}")
        if ctx.get("recent_events"):
            ev_titles = "、".join(e["title"] for e in ctx["recent_events"][:3])
            parts.append(f"最近事件: {ev_titles}")

        return {
            "summary": "; ".join(parts) if parts else "",
            **ctx,
            # [Phase 4] 关系网注入：与 _world 同样的零模板侵入策略
            "relationships": _safe_relationship_subfield(character_id, engine),
        }
    except Exception as e:
        logger.debug("build_world_subfield 失败: %s", e)
        return None


def _safe_relationship_subfield(character_id: int, engine=None) -> Optional[Dict[str, Any]]:
    """调用 build_relationship_subfield 失败时返回 None（不破坏主流程）"""
    try:
        from backend.world.relationship_network import build_relationship_subfield
        return build_relationship_subfield(character_id, engine=engine)
    except Exception as e:
        logger.debug("build_relationship_subfield 失败: %s", e)
        return None


def get_style_guidance(character_id: int, engine=None) -> str:
    """
    附加到 Actor.style 的世界感知短句（参考 jiwen.get_style_guidance）。

    例：'（外面正下着小雨，你的语气可能更内敛）'
        '（春暖花开的日子里，你看起来心情不错）'
    """
    try:
        if engine is None:
            from backend.world.world_engine import get_world_engine
            engine = get_world_engine()
        ctx = engine.get_context_for_character(character_id)
        if not ctx:
            return ""

        # 天气 → 短句
        weather_guidance = {
            "rainy":  "（外面正下着雨，你的声音可能更低沉）",
            "stormy": "（外面雷雨交加，你显得有些不安）",
            "snowy":  "（外面飘着雪，你的动作变得更慢）",
            "sunny":  "（阳光明媚，你的心情似乎很好）",
            "cloudy": "（天色阴沉，你略微有些沉闷）",
            "windy":  "（外面风很大，你裹了裹衣服）",
            "foggy":  "（外面雾蒙蒙的，你看不太清远处）",
            "clear":  "（天空澄澈，你深吸了一口新鲜空气）",
        }
        guidance = ""
        if ctx.get("weather"):
            guidance += weather_guidance.get(ctx["weather"], "")

        # 季节 → 短句
        season_guidance = {
            "spring": "（春暖花开，空气中弥漫着花香）",
            "summer": "（夏日炎炎，你找了个阴凉处）",
            "fall":   "（秋意渐浓，落叶纷飞）",
            "winter": "（冬日寒冷，你搓了搓手）",
        }
        if ctx.get("world"):
            guidance += season_guidance.get(ctx["world"]["season"], "")

        return guidance
    except Exception as e:
        logger.debug("get_style_guidance 失败: %s", e)
        return ""
