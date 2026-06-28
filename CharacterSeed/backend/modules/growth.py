"""
Day 3 — 角色成长模块（Growth Module）

设计理念：采用"昨日回顾 → 人格演化"的单 LLM 管路。
    Growth.analyze() → 分析角色昨日经历，推导人格变化并提炼关键记忆

与 Director/Actor 双 LLM 管路的区别：
  - Growth 是异步（手动/定期触发），不是实时交互
  - 因此不加降级策略，失败向上抛异常，由调用方决定是否重试
  - 单 LLM 调用即可完成分析，无需拆分为"感知→表达"两阶段

温度参数选择依据：
  - temperature=0.5：人格分析需要逻辑一致性，偏低减少随机性

管线流程（6 节点）：
  1. 读取角色当前状态（人格字典 + 名称）
  2. 读取昨日对话记录（最近 10 条，10 条在覆盖度和 token 预算之间平衡）
  3. 组装 prompt → 调用 Growth LLM（response_format=json_object）
  4. 解析响应 → validate_growth_schema 校验
  5. 计算新人格 = 旧人格 + delta（代码侧完成，不依赖 LLM 输出绝对值）
  6. 持久化更新（growth_log + memories + character.personality）
"""

import json
import logging
from typing import Dict, Any, List, Tuple, Optional

from sqlalchemy.orm import Session

from backend.services.llm_service import LLMService
from backend.crud import character as character_crud
from backend.crud import conversation as conversation_crud
from backend.crud import memory as memory_crud
from backend.crud import growth as growth_crud
from backend.modules.interaction import (
    char_data_cache_invalidate as _char_data_cache_invalidate,
    cache_invalidate as _invalidate_response_cache,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 人格维度常量：系统中不变的 6 个人格属性
# 设计考量：集中管理人格字段名，避免多处硬编码字符串
# ============================================================================

PERSONALITY_DIMENSIONS = [
    "optimism", "courage", "empathy",
    "loyalty", "intelligence", "sociability"
]


class GrowthModule:
    """
    角色成长模块（Pipeline 模式）

    职责：分析角色昨日经历，推导人格变化并提炼关键记忆。

    输入 → 输出链路：
        character_name + old_personality + yesterday_conversations
            ↓  一次 LLM 调用 (temperature=0.5, response_format=json_object)
        personality_delta + new_memories + event_summary
            ↓  代码侧计算
        新人格 = 旧人格 + delta
            ↓  持久化
        growth_log + memories + 更新 character.personality

    设计考量：
      - 人格计算在代码侧而非 LLM 输出：避免 LLM "发明"不存在的属性名
        或输出偏离原始值的危险
      - 昨日对话取最近 10 条：5 条太少（覆盖度不够），全部太多（token 超标），
        10 条在约 1500 token 和足够的时间覆盖率之间取得平衡
      - 不加降级：Growth 是异步触发，失败可重试，不需要"无论如何都要返回"
    """

    def __init__(self):
        self.llm_service = LLMService()
        self.prompt_template = self._load_prompt_template()

    def _load_prompt_template(self) -> str:
        """加载 Growth prompt 模板文件"""
        with open("backend/prompts/growth.txt", "r", encoding="utf-8") as f:
            return f.read()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用，复用已加载的 prompt 模板）"""
        self.llm_service.reload_config()

    @staticmethod
    def _safe_load_json(raw: Optional[str]) -> dict:
        """安全地将 JSON 字符串转为 dict，失败返回空字典"""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    def _format_yesterday_conversations(
        self, conversations: List[Any]
    ) -> str:
        """
        将对话记录列表格式化为 LLM 可读的文本。

        格式：
          [玩家]: 你好！
          [NPC]: 欢迎来到酒馆！
          [玩家]: 有什么新鲜事吗？
          ...

        设计考量：使用 [玩家]/[NPC] 标签而非角色名，因为
        角色名可能与 NPC 名相同，造成 LLM 混淆。
        按时间升序排列（自然对话流），而非降序。
        """
        if not conversations:
            return "  （昨日暂无对话记录）"

        lines = []
        for conv in conversations:
            player_msg = getattr(conv, "user_input", "")
            npc_msg = getattr(conv, "npc_response", "")
            if player_msg:
                lines.append(f"[玩家]: {player_msg}")
            if npc_msg:
                lines.append(f"[NPC]: {npc_msg}")

        return "\n".join(lines) if lines else "  （昨日暂无对话记录）"

    def _calculate_new_personality(
        self,
        old_personality: Dict[str, int],
        delta: Dict[str, int]
    ) -> Dict[str, int]:
        """
        计算新人格 = 旧人格 + delta。

        边界保护：
          - 每个属性值钳位在 [0, 100] 内
          - 旧人格中不存在的属性跳过（兼容 schema 演进）

        设计考量：钳位而非报错——即使 delta 导致某个属性超过 100，
        也只是停留在 100，不阻断完整流程。人格变化是渐进的，
        某次越界的数据误差会在后续 Growth 中自然修正。
        """
        new_personality = {}
        for dim in PERSONALITY_DIMENSIONS:
            old_val = old_personality.get(dim, 50)  # 默认 50
            delta_val = delta.get(dim, 0)
            new_val = old_val + delta_val
            # 钳位到 [0, 100]
            new_val = max(0, min(100, new_val))
            new_personality[dim] = new_val

        return new_personality

    def run(
        self,
        character_id: int,
        db: Session,
        conversation_limit: int = 10
    ) -> Dict[str, Any]:
        """
        运行完整的成长管线。

        Args:
            character_id:      角色 ID
            db:                SQLAlchemy 数据库会话
            conversation_limit: 读取最近多少条对话（默认 10）

        Returns:
            字典，包含以下字段，可直接用于 GrowthResponse schema：
            {
                "id": int,                # growth_log 记录 ID
                "character_id": int,
                "personality_delta": str,  # JSON 字符串
                "event_summary": str,
                "new_memories": str,       # JSON 数组字符串
                "growth_raw": str,
                "created_at": datetime,
            }

        Raises:
            ValueError: 角色不存在时抛出
        """
        # ---- 节点 1：读取角色当前状态 ----
        # 获取角色的名称和人格数据，personality 是 JSON 字符串需反序列化
        character = character_crud.get_character(db, character_id)
        if not character:
            raise ValueError(f"角色不存在: id={character_id}")

        old_personality = self._safe_load_json(character.personality)
        # 确保所有人格维度都有值（未设置的默认为 50）
        for dim in PERSONALITY_DIMENSIONS:
            if dim not in old_personality:
                old_personality[dim] = 50

        # ---- 节点 2：读取昨日对话记录 ----
        # conversation_limit=10 的设计考量：
        #   5 条太少——一场对话可能有 8-10 轮交替
        #   全部太多——长时间运行后可能达数百条，token 超标
        #   10 条在约 1500 token 和足够覆盖度之间取得平衡
        conversations = conversation_crud.get_character_conversations(
            db, character_id, limit=conversation_limit
        )
        yesterday_text = self._format_yesterday_conversations(conversations)

        # ---- 节点 3：组装 prompt → 调用 Growth LLM ----
        # temperature=0.5 的设计考量：
        #   人格分析是"逻辑推导"任务，需要一致性而非创造性。
        #   过低（0.1）可能导致过于保守（所有 delta=0），
        #   过高（0.8）可能导致随机大幅度变化，违背人格渐进的设定。
        personality_str = json.dumps(old_personality, ensure_ascii=False)

        prompt = self.prompt_template.format(
            character_name=character.name,
            personality=personality_str,
            yesterday_conversations=yesterday_text,
        )

        system_prompt = (
            "你是一个专业的角色成长分析师，"
            "擅长根据角色的对话历史推导其人格变化和关键记忆。"
        )

        raw_response = self.llm_service.call(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.5,
            response_format={"type": "json_object"},
            task="growth",
        )

        # ---- 节点 4：解析并校验 ----
        parsed = self.llm_service.parse_json_response(raw_response)
        parsed = LLMService.validate_growth_schema(parsed)

        personality_delta = parsed["personality_delta"]
        new_memories = parsed["new_memories"]
        event_summary = parsed["event_summary"]

        # ---- 节点 5：计算新人格 ----
        # 关键设计决策：在代码侧计算新人格 = 旧人格 + delta，
        # 而非让 LLM 直接输出新人格值。
        # 原因：LLM 可能"发明"不存在的属性名或输出偏离原始值的数值，
        #       代码侧计算保证人格属性的完整性和数值的渐进性。
        new_personality = self._calculate_new_personality(
            old_personality, personality_delta
        )

        # ---- 节点 6：持久化更新 ----
        # 6a. 创建 growth_log 记录
        #     personality_delta 和 new_memories 存入数据库前序列化为 JSON 字符串
        growth_log = growth_crud.create_growth_log(
            db=db,
            character_id=character_id,
            personality_delta=json.dumps(personality_delta, ensure_ascii=False),
            event_summary=event_summary,
            new_memories=json.dumps(new_memories, ensure_ascii=False),
            growth_raw=raw_response,
        )

        # 6b. 将新记忆写入 memories 表
        #     每条新记忆单独持久化，importance < 5 的也保留（完整数据）
        #     memory_type="growth" 标记来自成长系统
        for mem in new_memories:
            memory_crud.create_memory(
                db=db,
                character_id=character_id,
                content=mem["content"],
                importance=mem["importance"],
                memory_type="growth",
            )

        # 6c. 更新角色的 personality 字段为新人格值
        #     CRUD 层自动将 dict 序列化为 JSON 字符串
        character_crud.update_character(
            db=db,
            character_id=character_id,
            personality=new_personality,
        )

        # ---- 节点 6.5：失效该角色相关的缓存 ----
        # 成长改变了 personality，旧的缓存数据（personality 解析 + LLM 响应）都已陈旧
        _char_data_cache_invalidate(character_id)
        _invalidate_response_cache(character_id)

        # ---- 节点 7：返回结果 ----
        return {
            "id": growth_log.id,
            "character_id": character_id,
            "personality_delta": growth_log.personality_delta,
            "event_summary": growth_log.event_summary,
            "new_memories": growth_log.new_memories,
            "growth_raw": growth_log.growth_raw,
            "created_at": growth_log.created_at,
        }
