"""
Event CRUD 模块（Day4 新增）

职责：管理事件表（events）的所有数据库操作，为事件推进轴提供数据层支持。

核心查询模式：
  1. get_next_pending(character_id) → 取 order_index 最小的 pending 事件
     ——"推进事件"按钮调用的核心查询
  2. get_events_by_day(character_id, day_number) → 某天的所有事件
     ——Growth 迭代时读取 "观察材料"
  3. complete_event(event_id, result_json) → 标记完成并写入回执
     ——"推进事件"写入执行结果
  4. package_session_dialogue(session_id, day_number) → 将对话打包为事件
     ——advance_event 前的必要步骤，确保对话被纳入事件观测
"""
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy.orm import Session
from sqlalchemy import and_, func
from backend.models import Event

logger = logging.getLogger(__name__)


def create_event(
    db: Session,
    character_id: int,
    day_number: int,
    order_index: int,
    event_type: str,
    content: str,
    metadata_json: Optional[str] = None,
    status: str = "pending",
    session_id: Optional[int] = None,
    time_period: Optional[str] = None,
) -> Event:
    """
    创建单条事件记录。

    设计考量：
      - content / metadata_json / result_json 在入库前已经是字符串，
        调用方（growth module / advance endpoint）负责序列化。
      - status 默认 pending，由 advance 端点统一推进为 completed。
      - session_id 仅对话事件需要携带，schedule_action 事件为 None。

    为什么不在 CRUD 层处理 JSON 序列化：
      CRUD 层职责是"数据持久化"，JSON 与 Python dict 的转换在模块层完成。
      这保持了 CRUD 层的通用性——如果将来改用 ORM 映射 JSON 列，不需要改 CRUD。
    """
    db_event = Event(
        character_id=character_id,
        day_number=day_number,
        order_index=order_index,
        event_type=event_type,
        content=content,
        metadata_json=metadata_json,
        status=status,
        session_id=session_id,
        time_period=time_period,
    )
    db.add(db_event)
    db.commit()
    db.refresh(db_event)
    logger.debug("创建事件: id=%d, type=%s, day=%d, order=%d",
                 db_event.id, event_type, day_number, order_index)
    return db_event


def create_events_bulk(
    db: Session,
    events_data: List[Dict[str, Any]],
) -> List[Event]:
    """
    批量创建事件（Growth 迭代后写入次日事件列表）。

    使用 bulk insert 而非逐条 create_event：
      - 减少数据库往返次数（一次 commit 替代 N 次）
      - 避免重复的审计日志（批量操作统一打一条 logger）
      - 注意：bulk_insert_savepoints 不会触发 ORM 的 after_insert 事件，
        但 Event 表目前无此类钩子，安全。

    设计考量：
      接收 dict 列表而非模型对象，降低调用方耦合。
      调用方（main.py 的 iterate 端点）只需构造裸 dict 即可。
    """
    if not events_data:
        return []

    db.execute(Event.__table__.insert(), events_data)
    db.commit()
    logger.debug("批量创建 %d 条事件", len(events_data))
    return events_data


def get_next_pending_event(
    db: Session,
    character_id: int,
    day_number: int,
) -> Optional[Event]:
    """
    取指定天中第一条 status=pending 且 order_index 最小的 Event。

    这是"推进事件"的核心查询——找到用户当前最应该推进的事件。

    设计考量：
      - 按 order_index ASC 排序，取第一条（limit 1），是高效的索引查询
      - 只查当天的事件，不与跨天事件混淆
      - 即使同一天有多个 pending 事件，order_index 最小的也是最"紧迫"的
      - 返回 None 表示当天事件已全部完成

    索引利用：ix_events_char_day_status (character_id, day_number, status)
    """
    event = (
        db.query(Event)
        .filter(
            Event.character_id == character_id,
            Event.day_number == day_number,
            Event.status == "pending",
        )
        .order_by(Event.order_index.asc())
        .first()
    )
    return event


def get_events_by_day(
    db: Session,
    character_id: int,
    day_number: int,
    status_filter: Optional[str] = None,
) -> List[Event]:
    """
    获取某天的所有事件，按 order_index 升序排列。

    这是 Growth 迭代时的核心查询——收集当天的"观察材料"。

    Args:
        character_id: 角色 ID
        day_number: 目标天数
        status_filter: 可选过滤（"pending" / "completed"），None 返回全部

    设计考量：
      允许 status_filter 参数让调用方灵活控制：
        - Growth 迭代时取 status=completed（已推进的才算"经历"）
        - 前端展示时取全部（pending 展示待办，completed 展示历史）
    """
    query = (
        db.query(Event)
        .filter(
            Event.character_id == character_id,
            Event.day_number == day_number,
        )
    )
    if status_filter:
        query = query.filter(Event.status == status_filter)
    query = query.order_by(Event.order_index.asc())
    return query.all()


def complete_event(
    db: Session,
    event_id: int,
    result_json: str,
    director_raw: Optional[str] = None,       # v1.6 B7 新增
    actor_raw: Optional[str] = None,          # v1.6 B7 新增
    capabilities_applied: Optional[str] = None,  # v1.6 B7 新增
    emotion: Optional[str] = None,            # v1.6 B7 新增
    expression: Optional[str] = None,         # v1.6 B7 新增
) -> Optional[Event]:
    """
    将事件标记为 completed 并写入 result_json 及叙事元数据。

    这是"推进事件"的写操作——用户完成一个事件后，记录执行回执。

    Args:
        event_id: 待完成的事件 ID
        result_json: 执行回执（JSON 字符串）
           - 对话事件：LLM 生成的对话摘要
           - 日程事件：Actor 生成的叙事文本
           - 主动事件：玩家反应描述
        director_raw: v1.6 B7 新增，Director LLM 原始响应 JSON
        actor_raw: v1.6 B7 新增，Actor LLM 原始响应 JSON
        capabilities_applied: v1.6 B7 新增，角色选择的能力（JSON 数组）
        emotion: v1.6 B7 新增，角色情绪
        expression: v1.6 B7 新增，角色表情

    Returns:
        更新后的 Event 对象，事件不存在时返回 None

    设计考量：
      使用 UPDATE 而非 DELETE + INSERT 模式：
        - 保留事件完整生命周期（pending → completed）
        - result_json 字段不可变（一次写入不再修改），保证数据一致性
        - 不提供"回退"接口（completed → pending），鼓励原子推进
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        logger.warning("complete_event: 事件不存在 id=%d", event_id)
        return None
    event.status = "completed"
    event.result_json = result_json
    # v1.6 B7 新增：叙事元数据字段写入
    if director_raw is not None:
        event.director_raw = director_raw
    if actor_raw is not None:
        event.actor_raw = actor_raw
    if capabilities_applied is not None:
        event.capabilities_applied = capabilities_applied
    if emotion is not None:
        event.emotion = emotion
    if expression is not None:
        event.expression = expression
    db.commit()
    db.refresh(event)
    logger.debug("事件完成: id=%d, day=%d, type=%s", event_id, event.day_number, event.event_type)
    return event


def has_pending_events(
    db: Session,
    character_id: int,
    day_number: int,
) -> bool:
    """
    检查某天是否还有待处理事件。

    用于 advance_event 确定是否还有更多工作要做；
    用于 iterate 确定是否可以安全地进入下一天。

    设计考量：使用 EXISTS 查询而非 COUNT，
      数据库查到第一条就返回，无需扫描全表。
    """
    exists = (
        db.query(Event.id)
        .filter(
            Event.character_id == character_id,
            Event.day_number == day_number,
            Event.status == "pending",
        )
        .first()
    )
    return exists is not None


def get_day_number(
    db: Session,
    character_id: int,
) -> int:
    """
    获取角色当前天数（从 Event 表推断）。

    实现逻辑：取该角色最大的 day_number，不存在则返回 1。

    设计考量：
      - Event 表与 Character.day_number 可能不同步（migration 过渡期）
      - 以 Event 表已有数据为准，优于读 Character.day_number
      - 完全无事件时回退到 1（首次迭代）
    """
    max_day = (
        db.query(func.max(Event.day_number))
        .filter(Event.character_id == character_id)
        .scalar()
    )
    return max_day if max_day else 1


def count_events_by_day(
    db: Session,
    character_id: int,
    day_number: int,
) -> Dict[str, int]:
    """
    统计某天各状态事件数量。

    返回示例：{"pending": 3, "completed": 2, "total": 5}

    用于前端展示事件进度（"3/5 已完成"）。
    """
    rows = (
        db.query(Event.status, func.count(Event.id))
        .filter(
            Event.character_id == character_id,
            Event.day_number == day_number,
        )
        .group_by(Event.status)
        .all()
    )
    result = {"pending": 0, "completed": 0, "total": 0}
    for status, count in rows:
        result[status] = count
        result["total"] += count
    return result


def update_event_content(
    db: Session,
    event_id: int,
    new_content: str,
    new_event_type: Optional[str] = None,
    new_time_period: Optional[str] = None,
) -> Optional[Event]:
    """
    v1.6 B7 新增：修改事件的内容/类型/时间段。

    用于 Director 的 modify_plan 能力 — 角色可以修改当天的未完成事件。
    只允许修改 status=pending 的事件，已完成的不可变。

    设计考量：
      - 不可修改已完成的 event（保证历史一致性）
      - 返回 None 而非抛异常，让调用方可以静默跳过非法修改
      - 字段级更新：只修改传入的参数，未传的保持不变

    Args:
        event_id: 目标事件 ID
        new_content: 新的事件内容描述
        new_event_type: 可选新事件类型
        new_time_period: 可选新时间段

    Returns:
        更新后的 Event 对象；不存在/已完成/失败时返回 None
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        logger.warning("update_event_content: 事件不存在 id=%d", event_id)
        return None
    if event.status != "pending":
        logger.debug("update_event_content: 事件已完成，拒绝修改 id=%d", event_id)
        return None

    if new_content and isinstance(new_content, str):
        event.content = new_content.strip()

    valid_types = {"schedule_action", "player_dialogue", "scene_event", "character_initiative"}
    if new_event_type and new_event_type in valid_types:
        event.event_type = new_event_type

    valid_periods = {"morning", "afternoon", "evening", "night"}
    if new_time_period and new_time_period in valid_periods:
        event.time_period = new_time_period

    db.commit()
    db.refresh(event)
    logger.debug("事件内容更新: id=%d, content=%s", event_id, event.content[:60])
    return event


def reorder_event(
    db: Session,
    event_id: int,
    new_order_index: int,
) -> Optional[Event]:
    """
    v1.6 B7 新增：调整事件在日程中的顺序。

    用于 Director 的 modify_plan 能力 — 角色可以重新安排日程顺序。
    只允许调整 status=pending 的事件。

    设计考量：
      - 不执行"重新编号"操作，调用方负责保证 order_index 不冲突
      - 简单的单字段更新，调用方可多次调用实现复杂重排

    Args:
        event_id: 目标事件 ID
        new_order_index: 新的排序序号

    Returns:
        更新后的 Event 对象
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        logger.warning("reorder_event: 事件不存在 id=%d", event_id)
        return None
    if event.status != "pending":
        logger.debug("reorder_event: 事件已完成，拒绝重排 id=%d", event_id)
        return None

    event.order_index = max(1, int(new_order_index))
    db.commit()
    db.refresh(event)
    logger.debug("事件顺序更新: id=%d, new_order=%d", event_id, event.order_index)
    return event


def list_events(
    db: Session,
    character_id: int,
    day_number: Optional[int] = None,
    status: Optional[str] = None,
) -> List[Event]:
    """
    列出某角色的全部事件（按 day_number, order_index 升序）。

    支持按 day_number 和 status 可选过滤。
    与 get_events_by_day 的区别：day_number 为 None 时返回所有天的事件。
    """
    query = db.query(Event).filter(Event.character_id == character_id)
    if day_number is not None:
        query = query.filter(Event.day_number == day_number)
    if status:
        query = query.filter(Event.status == status)
    return query.order_by(Event.day_number.asc(), Event.order_index.asc()).all()


def count_pending_events(
    db: Session,
    character_id: int,
    day_number: Optional[int] = None,
) -> int:
    """
    统计某角色的 pending 事件数量。

    day_number 为 None 时统计所有天，否则只统计指定天。
    """
    query = db.query(func.count(Event.id)).filter(
        Event.character_id == character_id,
        Event.status == "pending",
    )
    if day_number is not None:
        query = query.filter(Event.day_number == day_number)
    return query.scalar() or 0


def update_event_result(
    db: Session,
    event_id: int,
    result_json: str,
    status: str = "completed",
) -> Optional[Event]:
    """
    更新事件结果（complete_event 的别名，保持向后兼容）。

    用于 event_router 和 event.py 的 advance_one 流程。
    """
    return complete_event(db, event_id=event_id, result_json=result_json)


def delete_events_by_character(
    db: Session,
    character_id: int,
) -> int:
    """
    删除某角色的全部事件（级联删除时使用）。

    返回删除的记录数，0 表示角色无事件。
    """
    count = (
        db.query(Event)
        .filter(Event.character_id == character_id)
        .delete()
    )
    db.commit()
    return count
