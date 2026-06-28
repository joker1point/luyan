"""
Time Engine — 时间推进（迭代到下一天 / 一键推演）

职责：
  - iterate: 跑一次"日迭代"
      1) 调用 GrowthModule 推人格变化 + 新记忆（已有）
      2) LLM 生成次日 schedule（3-5 个事件）
      3) 把 schedule 拆成 Event 记录落库（status=pending）
      4) characters.day_number + 1
      5) 返回 IterateResponse（成长结果 + schedule + 新增事件数）

  - auto: 串联"先推完所有 pending → 再 iterate"
      返回 AutoResponse

设计要点：
  - 与 EventManager 共享 LLMService 实例（reload() 一并更新）
  - schedule 生成 LLM 失败时降级：用 growth 的 event_summary + 一段模板
    生成 3 条简单事件，保证用户能看到"今天有事件被安排"
  - 删角色时已有 cascade_delete_character 处理 events 表，无需在此处理
"""
import json
import logging
import random
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session

from backend.services.llm_service import LLMService
from backend.crud import event as event_crud
from backend.crud import character as character_crud
from backend.models import Event
from backend.modules.growth import GrowthModule

logger = logging.getLogger(__name__)

# 时段分布（一天最多 4 个时段，简单均分）
_TIME_PERIODS = ["morning", "afternoon", "evening", "night"]


def _compute_short_delay(attempt: int) -> float:
    """
    [v3.x 新增] 快速路径的重试退避（比 LLMService._compute_retry_delay 短）。
    实际延迟区间：
      attempt=0: [0.5, 1.0)s
    """
    base = 0.5
    return base + random.uniform(0, base)


class TimeEngine:
    """
    时间推进引擎

    公开方法：
      - iterate(db, character_id) -> dict  (匹配 IterateResponse)
      - auto(db, character_id)    -> dict  (匹配 AutoResponse)
    """

    def __init__(self):
        self.llm_service = LLMService()
        self.growth_module = GrowthModule()

    def reload(self) -> None:
        """热更新 LLM 配置（设置页改动后调用）"""
        self.llm_service.reload_config()
        self.growth_module.reload()

    # ------------------------------------------------------------
    # schedule 生成
    # ------------------------------------------------------------
    def _build_schedule_prompt(
        self,
        character_name: str,
        personality_str: str,
        world_setting: Optional[str],
        event_summary: Optional[str],
        new_memories: List[dict],
        next_day: int,
    ) -> str:
        mem_lines = "\n".join(
            f"- {m.get('content', '')}" for m in new_memories[:3]
        ) or "（无）"
        return (
            f"你是 {character_name} 的日程规划师。请基于昨日回顾规划 Day {next_day} 的事件。\n\n"
            f"【世界设定】\n{world_setting or '（无）'}\n\n"
            f"【当前人格】\n{personality_str or '（无）'}\n\n"
            f"【昨日事件摘要】\n{event_summary or '（无）'}\n\n"
            f"【昨日新增记忆】\n{mem_lines}\n\n"
            f"请输出 JSON，描述 Day {next_day} 的日程，包含 3-5 个事件：\n"
            "{\n"
            '  "world_changes": "<一句话描述世界/状态的变化，可选>",\n'
            '  "schedule": [\n'
            '    {"time_period": "morning|afternoon|evening|night", '
            ' "event_type": "schedule_action|scene_event|character_initiative", '
            ' "content": "<50-150 字的事件描述>"},\n'
            "    ...\n"
            "  ]\n"
            "}\n"
            "严格要求：\n"
            "1) 只输出 JSON，不输出 Markdown\n"
            "2) schedule 长度 3-5\n"
            "3) 事件要符合角色人设，互不重复\n"
            "4) time_period 必须从 morning/afternoon/evening/night 中选\n"
        )

    def _parse_schedule(self, raw: str) -> Dict[str, Any]:
        """解析 LLM 输出，失败时返回兜底 schedule"""
        try:
            data = self.llm_service.parse_json_response(raw)
            schedule = data.get("schedule") or []
            if not isinstance(schedule, list) or not schedule:
                raise ValueError("schedule 字段缺失或为空")
            cleaned = []
            for i, item in enumerate(schedule[:5]):
                if not isinstance(item, dict):
                    continue
                tp = item.get("time_period", _TIME_PERIODS[min(i, 3)])
                if tp not in _TIME_PERIODS:
                    tp = _TIME_PERIODS[min(i, 3)]
                et = item.get("event_type", "schedule_action")
                if et not in ("schedule_action", "scene_event", "character_initiative", "player_dialogue"):
                    et = "schedule_action"
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                cleaned.append({
                    "time_period": tp,
                    "event_type": et,
                    "content": content[:500],
                })
            if not cleaned:
                raise ValueError("schedule 全部条目为空")
            return {
                "world_changes": (data.get("world_changes") or "").strip()[:200],
                "schedule": cleaned,
            }
        except Exception as e:
            logger.warning("schedule LLM 解析失败，使用兜底 schedule: %s", str(e)[:200])
            return {
                "world_changes": "",
                "schedule": [
                    {"time_period": "morning", "event_type": "schedule_action",
                     "content": "在熟悉的地点醒来，整理昨日的思绪。"},
                    {"time_period": "afternoon", "event_type": "scene_event",
                     "content": "处理日常事务，尝试新的行动。"},
                    {"time_period": "evening", "event_type": "character_initiative",
                     "content": "回顾今日，与信任的人交流心得。"},
                ],
            }

    def _generate_schedule(
        self,
        db: Session,
        character_id: int,
        next_day: int,
        growth_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        """调 LLM 生成 schedule（可能降级）

        [v3.x 修复] 激进的兜底策略：
          - LLMService 默认 _MAX_RETRIES=3 + _TIMEOUT=25s + 指数退避，
            一次失败调用最坏耗时 = 3 * 25 + (1+2+4) ≈ 82 秒
          - iterate 阶段会调两次 LLM（growth + schedule），总耗时最坏 ~150 秒
          - 前端 iterateTime 8s timeout 严重不够
          - 修复 1：schedule 阶段用更短的 LLM 超时（_fast_llm_call）
            —— 单次 timeout 砍到 10s，最多 1 次重试，失败立即降级
          - 修复 2：即便 schedule 解析失败，兜底 schedule 仍能保证 day_number + 1
        """
        character = character_crud.get_character(db, character_id)
        if not character:
            raise ValueError(f"角色不存在: id={character_id}")

        new_memories = []
        try:
            new_memories = json.loads(growth_result.get("new_memories") or "[]")
        except Exception:
            new_memories = []

        prompt = self._build_schedule_prompt(
            character_name=character.name,
            personality_str=character.personality or "{}",
            world_setting=character.world_setting,
            event_summary=growth_result.get("event_summary"),
            new_memories=new_memories,
            next_day=next_day,
        )
        system_prompt = "你是日程规划引擎，只输出 JSON。"

        # [v3.x 修复] schedule 阶段用"快速失败"路径：
        #   - 内部用 _fast_llm_call 走 1 次重试 + 短 timeout（10s）
        #   - 任何异常都吞掉，立刻降级为兜底 schedule
        #   - 整个 schedule 阶段最坏 ~12s（10s 第一次 + 重试 1 次 + 2s 退避）
        # 这样整个 iterate 接口最坏 ~95s（growth 75s + schedule 12s + 其他 8s），
        # 前端 180s timeout 完全 cover。
        try:
            raw = self._fast_llm_call(
                system_prompt=system_prompt,
                user_prompt=prompt,
                temperature=0.8,
                max_tokens=800,
                response_format={"type": "json_object"},
                per_call_timeout=10.0,
                max_retries=1,
            )
            return self._parse_schedule(raw)
        except Exception as e:
            logger.warning("schedule LLM 快速失败降级为兜底: %s", str(e)[:200])
            return self._fallback_schedule()

    def _fast_llm_call(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        response_format: Optional[dict] = None,
        per_call_timeout: float = 10.0,
        max_retries: int = 1,
    ) -> str:
        """
        [v3.x 新增] 快速 LLM 调用：单次短 timeout + 最多 1 次重试。

        与 LLMService._call_with_retry_for 的区别：
          - 单次 timeout 可配（默认 10s，而不是 25s）
          - 重试次数可配（默认 1 次，而不是 3 次）
          - 用 _resolve_task_provider() 拿到对应 task 的 provider dict
          - 临时构造一个短 timeout 的 httpx.Client，不污染主 provider 缓存

        用于"time 推进"等对延迟敏感、可降级的场景。Growth 模块
        （失败会回 500）仍用默认 _call_with_retry_for。
        """
        from backend.services.llm_service import LLMService  # 避免循环引用

        # 拿到当前主 provider 的缓存 dict
        prov = self.llm_service._PROVIDER_CACHE.get(self.llm_service.provider)
        if not prov:
            # 缓存未初始化（极端情况，如 time 模块先于 llm_service 加载）— 退化
            return self.llm_service.call(
                prompt=user_prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                task="time",
            )

        # 临时构造短 timeout 的 httpx.Client，复制主 provider 的 keepalive 配置
        from backend.services.llm_service import LLMService as _L
        # keepalive limits 复用：agns 关 keepalive，其他保持
        is_agn = prov.get("provider") == "agnes"
        limits = _L._HTTPX_LIMITS_AGN if is_agn else _L._HTTPX_LIMITS
        import httpx
        from openai import OpenAI
        http_client = httpx.Client(
            timeout=httpx.Timeout(per_call_timeout, connect=5, write=5, pool=5),
            limits=limits,
        )
        client = OpenAI(
            api_key=prov["api_key"] if prov["provider"] != "ollama" else "ollama",
            base_url=prov["base_url"],
            http_client=http_client,
        )

        kwargs = {
            "model": prov["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format

        last_exception = None
        try:
            for attempt in range(max_retries + 1):  # 0..max_retries 共 max_retries+1 次
                try:
                    response = client.chat.completions.create(**kwargs)
                    content = self.llm_service._extract_content(response)
                    return content
                except Exception as e:
                    last_exception = e
                    if attempt < max_retries:
                        import time as _t
                        _t.sleep(_compute_short_delay(attempt))
                        continue
                    raise
        finally:
            # 清理临时 client（避免 fd 泄漏）
            try:
                http_client.close()
            except Exception:
                pass

        if last_exception:
            raise last_exception
        raise RuntimeError("_fast_llm_call 异常结束")

    @staticmethod
    def _fallback_schedule() -> Dict[str, Any]:
        """schedule 兜底：3 条简单事件 + 空 world_changes"""
        return {
            "world_changes": "",
            "schedule": [
                {"time_period": "morning", "event_type": "schedule_action",
                 "content": "在熟悉的地点醒来，整理昨日的思绪。"},
                {"time_period": "afternoon", "event_type": "scene_event",
                 "content": "处理日常事务，尝试新的行动。"},
                {"time_period": "evening", "event_type": "character_initiative",
                 "content": "回顾今日，与信任的人交流心得。"},
            ],
        }

    # ------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------
    def iterate(self, db: Session, character_id: int) -> Dict[str, Any]:
        """
        日迭代：
          1) 调 GrowthModule（人格 + 记忆）
          2) LLM 生成新一天 schedule
          3) 落库 Event 记录（pending）
          4) characters.day_number + 1
          5) 返回 IterateResponse dict

        [v3.x 修复] 端到端韧性（iterate 必须能在 LLM 服务降级时仍能推进 day）：
          - 之前：GrowthModule 失败 → ValueError → 500 → 前端事件页报错
          - 之前：schedule LLM 失败 → 兜底 schedule（OK）
          - 现在：Growth 失败也降级 → 人格不变 + 空 memories + 兜底 schedule → day+1
          - 整个 iterate 最坏 ~95s（growth 75s + schedule 12s + 其他 8s），
            前端 180s timeout 完全 cover。
        """
        character = character_crud.get_character(db, character_id)
        if not character:
            raise ValueError(f"角色不存在: id={character_id}")

        # 1) Growth（带降级：失败时用人格=不变 + 空 memories 的兜底 growth_result）
        try:
            growth_result = self.growth_module.run(
                character_id=character_id, db=db,
            )
            growth_degraded = False
        except Exception as e:
            logger.warning(
                "iterate: growth 阶段异常，降级为不动人格: %s",
                str(e)[:200],
            )
            growth_result = {
                "id": None,
                "personality_delta": "{}",
                "event_summary": "（成长分析暂不可用，跳过本轮人格演化）",
                "new_memories": "[]",
                "growth_raw": None,
                "created_at": None,
            }
            growth_degraded = True

        # 2) 推进到下一天，生成 schedule（内部已有兜底）
        next_day = (character.day_number or 1) + 1
        schedule_data = self._generate_schedule(
            db=db,
            character_id=character_id,
            next_day=next_day,
            growth_result=growth_result,
        )

        # 3) 落库 Event
        event_rows = [
            {
                "character_id": character_id,
                "day_number": next_day,
                "order_index": idx,
                "event_type": item["event_type"],
                "content": item["content"],
                "status": "pending",
                "time_period": item["time_period"],
            }
            for idx, item in enumerate(schedule_data["schedule"])
        ]
        if event_rows:
            event_crud.create_events_bulk(db, event_rows)
        events_created = len(event_rows)

        # 4) 更新 day_number
        character_crud.update_character(
            db=db, character_id=character_id, day_number=next_day,
        )

        # 5) 拼装响应
        return {
            "growth_log_id": growth_result.get("id"),
            "character_id": character_id,
            "day_number": next_day,
            "personality_delta": growth_result.get("personality_delta"),
            "event_summary": growth_result.get("event_summary"),
            "new_memories": growth_result.get("new_memories"),
            "world_changes_json": json.dumps(
                {"world_changes": schedule_data.get("world_changes", "")},
                ensure_ascii=False,
            ),
            "schedule_json": json.dumps(
                schedule_data, ensure_ascii=False,
            ),
            "events_created": events_created,
            "growth_raw": growth_result.get("growth_raw"),
            "created_at": growth_result.get("created_at"),
            # [v3.x 新增] 降级标记：前端可在结果里显示"成长分析降级"提示
            "growth_degraded": growth_degraded,
        }

    def auto(self, db: Session, character_id: int) -> Dict[str, Any]:
        """
        一键推演：
          1) 循环调 EventManager.advance_one 直到没有 pending（或达上限）
          2) 再调 iterate 到下一天
          3) 返回 AutoResponse dict（completed_events + iterate_result + error）
        """
        from backend.modules.event import EventManager

        character = character_crud.get_character(db, character_id)
        if not character:
            return {
                "character_id": character_id,
                "completed_events": [],
                "iterate_result": None,
                "error": f"角色不存在: id={character_id}",
            }

        manager = EventManager()
        completed: List[Event] = []
        try:
            # 上限 20 防止死循环（一天 3-5 个事件，正常最多推 5 个就够）
            for _ in range(20):
                ev = manager.advance_one(db, character_id)
                if ev is None:
                    break
                completed.append(ev)
        except Exception as e:
            logger.warning("auto: advance 阶段异常: %s", str(e)[:200])
            return {
                "character_id": character_id,
                "completed_events": [_serialize_event(e) for e in completed],
                "iterate_result": None,
                "error": f"推进阶段异常: {str(e)[:200]}",
            }

        # 2) 迭代到下一天
        try:
            iter_result = self.iterate(db, character_id)
        except Exception as e:
            logger.warning("auto: iterate 阶段异常: %s", str(e)[:200])
            return {
                "character_id": character_id,
                "completed_events": [_serialize_event(e) for e in completed],
                "iterate_result": None,
                "error": f"迭代阶段异常: {str(e)[:200]}",
            }

        return {
            "character_id": character_id,
            "completed_events": [_serialize_event(e) for e in completed],
            "iterate_result": iter_result,
            "error": None,
        }


def _serialize_event(ev: Event) -> dict:
    """把 Event ORM 转成 dict（Pydantic EventResponse 可直接 model_validate）"""
    return {
        "id": ev.id,
        "character_id": ev.character_id,
        "day_number": ev.day_number,
        "order_index": ev.order_index,
        "event_type": ev.event_type,
        "content": ev.content,
        "metadata_json": ev.metadata_json,
        "result_json": ev.result_json,
        "status": ev.status,
        "session_id": ev.session_id,
        "time_period": ev.time_period,
        "created_at": ev.created_at,
    }
