"""
Event Manager — 单个事件的推进逻辑

设计动机：
  - "推进一个事件" 看似简单，实际是一次完整的 LLM 推演：
    取 pending 事件 → 把事件内容 + 角色设定 + 已有记忆喂给 LLM
    → 解析 JSON 输出 → 写入 result_json → 标记 completed。
  - 抽离成模块后，前端 / 测试 / 未来其他模块都可以复用。

设计要点：
  - LLM 失败时**降级**：result_json 写一段降级文案（"事件完成"），
    status 仍标 completed，保证游戏流程不会卡住。
    与 InteractionPipeline 的"无降级"不同——事件是非实时交互，可降级。
  - 单例模式：与 CreationModule / InteractionPipeline / GrowthModule 保持一致。
"""
import json
import logging
from typing import Optional, Dict, Any

from sqlalchemy.orm import Session

from backend.services.llm_service import LLMService
from backend.crud import event as event_crud
from backend.crud import character as character_crud
from backend.crud import memory as memory_crud
from backend.models import Event

logger = logging.getLogger(__name__)

# LLM 失败时的兜底文案（短句，给前端"事件已推进"用）
_FALLBACK_RESULT = "事件已推进（LLM 降级：未生成具体回执）"


class EventManager:
    """
    单个事件推进管理器

    公开方法：
      - advance_one(db, character_id) -> Event | None
        取下一个 pending 事件，调 LLM 生成 result_json，标记 completed。
        若该角色无 pending 事件则返回 None。
    """

    def __init__(self):
        self.llm_service = LLMService()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用）"""
        self.llm_service.reload_config()

    def _build_advance_prompt(
        self,
        character_name: str,
        personality_str: str,
        world_setting: Optional[str],
        current_state_str: Optional[str],
        event_content: str,
        event_type: str,
    ) -> str:
        """
        构造"事件推演" prompt。

        要求 LLM 输出 JSON：{
          "result_text": "...",        # 一段叙事（推进后发生的事）
          "narrative_delta": "..."     # 状态/世界变化（可选，简短）
        }
        """
        return (
            f"你是 {character_name}，请基于你的角色设定推演这个事件。\n\n"
            f"【世界设定】\n{world_setting or '（无）'}\n\n"
            f"【当前状态】\n{current_state_str or '（无）'}\n\n"
            f"【当前人格】\n{personality_str or '（无）'}\n\n"
            f"【待推进事件】\n"
            f"类型: {event_type}\n"
            f"内容: {event_content}\n\n"
            "请输出 JSON：\n"
            "{\n"
            '  "result_text": "<一段 50-200 字的叙事，描述事件推进后发生的事>",\n'
            '  "narrative_delta": "<可选，状态/世界变化，1 句话即可>"\n'
            "}\n"
            "严格要求：\n"
            "1) 只输出 JSON，不要 Markdown 代码块或解释\n"
            "2) result_text 用中文，保持角色口吻\n"
        )

    def _parse_result(self, raw: str) -> Dict[str, str]:
        """解析 LLM 响应，失败时返回降级结果"""
        try:
            data = self.llm_service.parse_json_response(raw)
            result_text = (data.get("result_text") or "").strip()
            if not result_text:
                result_text = _FALLBACK_RESULT
            return {
                "result_text": result_text[:1000],  # 限长，防爆库
                "narrative_delta": (data.get("narrative_delta") or "").strip()[:200],
            }
        except Exception as e:
            logger.warning("事件 LLM 解析失败，使用降级文案: %s", str(e)[:200])
            return {
                "result_text": _FALLBACK_RESULT,
                "narrative_delta": "",
            }

    def advance_one(self, db: Session, character_id: int) -> Optional[Event]:
        """
        推进一个 pending 事件。

        流程：
          1) 取该角色下一个 pending 事件；不存在则返回 None
          2) 读角色基础数据
          3) 调 LLM 生成 result_text
          4) 写 result_json + status=completed
          5) （可选）把 narrative_delta 写入记忆
        """
        character = character_crud.get_character(db, character_id)
        if not character:
            raise ValueError(f"角色不存在: id={character_id}")

        current_day = character.day_number or 1
        ev = event_crud.get_next_pending_event(db, character_id, current_day)
        if not ev:
            logger.info("角色 %d 无 pending 事件（Day %d），跳过 advance", character_id, current_day)
            return None

        prompt = self._build_advance_prompt(
            character_name=character.name,
            personality_str=character.personality or "{}",
            world_setting=character.world_setting,
            current_state_str=character.current_state,
            event_content=ev.content,
            event_type=ev.event_type,
        )
        system_prompt = "你是数字生命推演引擎，只输出 JSON。"

        try:
            raw = self.llm_service.call(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.7,
                max_tokens=500,
                response_format={"type": "json_object"},
                task="event",
            )
            parsed = self._parse_result(raw)
        except Exception as e:
            logger.warning("事件 LLM 调用失败，使用降级: %s", str(e)[:200])
            parsed = {
                "result_text": _FALLBACK_RESULT,
                "narrative_delta": "",
            }

        result_json = json.dumps(parsed, ensure_ascii=False)
        updated = event_crud.update_event_result(
            db=db,
            event_id=ev.id,
            result_json=result_json,
            status="completed",
        )

        # 把 narrative_delta 写入记忆（短期上下文，方便后续 Growth 看到）
        if parsed.get("narrative_delta"):
            try:
                memory_crud.create_memory(
                    db=db,
                    character_id=character_id,
                    content=f"[Day {ev.day_number} #{ev.order_index}] {parsed['narrative_delta']}",
                    importance=4,
                    memory_type="event",
                )
            except Exception as e:
                logger.warning("事件结果写入记忆失败: %s", str(e)[:200])

        logger.info(
            "事件 %d (Day %d #%d) 已推进", updated.id, ev.day_number, ev.order_index,
        )
        return updated
