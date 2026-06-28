"""
角色 CRUD 封装。

[字段一致性约束]
所有写入操作（create_character / update_character）都通过
backend.schemas.CHARACTER_WRITABLE_FIELDS / CHARACTER_JSON_FIELDS / CHARACTER_IMMUTABLE_FIELDS
做白名单校验。任何绕过（直接 setattr(character, 'field', x)）会破坏 schema 与 DB 的契约。
"""
import json
from sqlalchemy.orm import Session
from backend.models import Character
from backend.schemas import (
    CHARACTER_WRITABLE_FIELDS,
    CHARACTER_JSON_FIELDS,
    CHARACTER_IMMUTABLE_FIELDS,
)
from typing import Optional, Union, Dict, Any


def _coerce_json_value(field: str, value: Any) -> Any:
    """
    如果字段是 JSON 字段（CHARACTER_JSON_FIELDS），把 dict/list 自动序列化为 JSON 字符串。
    否则原值返回。

    行为：
      - dict / list → json.dumps(ensure_ascii=False)
      - str         → 原样返回（假定调用方已序列化；不重复解析）
      - None        → 原样返回（表示"清空该字段"）
    """
    if field not in CHARACTER_JSON_FIELDS:
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def get_character(db: Session, character_id: int):
    """获取单个角色"""
    return db.query(Character).filter(Character.id == character_id).first()


def get_characters(db: Session, skip: int = 0, limit: int = 100):
    """获取角色列表"""
    return db.query(Character).offset(skip).limit(limit).all()


def _filter_writable(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    过滤 payload，只保留字段白名单中的字段。
    对不可写字段（CHARACTER_IMMUTABLE_FIELDS）和未知字段都直接丢弃。
    对 JSON 字段统一做 dict/list → str 序列化。
    """
    if not payload:
        return {}
    out: Dict[str, Any] = {}
    for k, v in payload.items():
        if k in CHARACTER_IMMUTABLE_FIELDS:
            # 静默丢弃（或后续可加 logger.warning）
            continue
        if k not in CHARACTER_WRITABLE_FIELDS:
            # 未知字段：直接丢弃（保护 schema 契约）
            continue
        out[k] = _coerce_json_value(k, v)
    return out


def create_character(
    db: Session,
    name: str,
    description: Optional[str] = None,
    world_setting: Optional[str] = None,
    personality: Optional[Union[str, dict]] = None,
    current_state: Optional[Union[str, dict]] = None,
    creation_raw: Optional[str] = None,
    **extra: Any,
):
    """
    创建角色。

    personality / current_state 支持传入 dict（自动序列化为 JSON 字符串）
    或直接传入 JSON 字符串（向后兼容）。

    其他可写字段可通过 **extra 传入（如 speaking_style / values / habits / long_term_goal /
    soul_md / day_number / world_id / current_location_id），会经过白名单 + JSON 序列化校验。
    不可写字段（id / created_at / updated_at）和未知字段会被自动丢弃。
    """
    payload = {
        "name": name,
        "description": description,
        "world_setting": world_setting,
        "personality": personality,
        "current_state": current_state,
        "creation_raw": creation_raw,
        **extra,
    }
    payload = _filter_writable(payload)
    # name 是必填；如果被白名单过滤掉了（比如传入空字符串），需 raise
    if "name" not in payload or not (payload["name"] or "").strip():
        raise ValueError("name 字段必填且不能为空")

    db_character = Character(**payload)
    db.add(db_character)
    db.commit()
    db.refresh(db_character)
    return db_character


def update_character(db: Session, character_id: int, **kwargs):
    """
    更新角色。

    [P0#1 一致性修复]
      - 之前 **kwargs 接受任意字段（包括 id / created_at），存在数据污染风险。
      - 现在通过 _filter_writable() 做白名单过滤，丢弃：
          * 不可写字段（CHARACTER_IMMUTABLE_FIELDS：id / created_at / updated_at）
          * 未知字段（不在 CHARACTER_WRITABLE_FIELDS 中的字段）
      - 对 JSON 字段（personality / current_state / speaking_style / values / habits）做
        dict/list → str 自动序列化（之前只对 personality / current_state 两个字段做）。

    返回更新后的对象（如果 character_id 不存在则返回 None）。
    """
    db_character = db.query(Character).filter(Character.id == character_id).first()
    if db_character is None:
        return None

    payload = _filter_writable(kwargs)
    for key, value in payload.items():
        setattr(db_character, key, value)
    if payload:
        db.commit()
        db.refresh(db_character)
    return db_character


def delete_character(db: Session, character_id: int):
    """删除角色（仅删除主记录，不含级联）"""
    db_character = db.query(Character).filter(Character.id == character_id).first()
    if db_character:
        db.delete(db_character)
        db.commit()
        return True
    return False


def cascade_delete_character(db: Session, character_id: int) -> dict:
    """
    级联删除角色及其所有关联数据。

    按顺序删除：events → memories → conversations → growth_logs → characters，
    确保无孤儿记录残留。使用 query.delete() 批量删除关联数据，
    避免逐条加载再 delete 的性能开销。
    """
    from backend.models import Memory, Conversation, GrowthLog, Event

    db_character = db.query(Character).filter(Character.id == character_id).first()
    if not db_character:
        return {"deleted": False, "name": None}

    name = db_character.name

    # 按子表→主表顺序批量删除
    events_count = db.query(Event).filter(Event.character_id == character_id).delete()
    mem_count = db.query(Memory).filter(Memory.character_id == character_id).delete()
    conv_count = db.query(Conversation).filter(Conversation.character_id == character_id).delete()
    growth_count = db.query(GrowthLog).filter(GrowthLog.character_id == character_id).delete()
    db.delete(db_character)
    db.commit()

    return {
        "deleted": True,
        "name": name,
        "events_deleted": events_count,
        "memories_deleted": mem_count,
        "conversations_deleted": conv_count,
        "growth_logs_deleted": growth_count,
    }


def get_character_field_set() -> set:
    """
    暴露给上层（router / audit / migration）使用的"当前白名单快照"。
    用于运行时校验"某字段是否可写"。
    """
    return set(CHARACTER_WRITABLE_FIELDS)
