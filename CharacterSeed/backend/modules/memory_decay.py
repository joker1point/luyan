"""
记忆衰减引擎（Forgetting Engine）

职责：
  1) 按 Ebbinghaus 曲线 + importance 修正，计算每条 memory 的当前 strength
  2) 把 strength < threshold 的 memory 标为 forgotten
  3) 给检索排序提供 boost 因子

设计：
  - decay_rate 按 theme 差异化：identity 慢，moment 快
  - importance 越高，半衰期越长（最长 90 天）
  - recall_count 提升强度（"被想起 = 抗遗忘"）
  - 永不物理删除（forgotten=true 是 soft-delete）
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models import Memory

logger = logging.getLogger(__name__)


# ======================================================================
# 主题差异化参数
# ======================================================================
THEME_DECAY_CONFIG = {
    # theme     : (base_decay_rate, min_half_life_days, max_half_life_days)
    "identity":  (0.005, 30, 365),   # 身份/性格 → 极慢衰减
    "music":     (0.010, 14, 180),   # 音乐品味 → 慢衰减
    "taste":     (0.015, 7, 90),     # 喜好/偏好 → 中速衰减
    "moment":    (0.050, 1, 14),     # 瞬间/事件 → 快衰减
    "todo":      (0.100, 0.5, 3),    # 时效待办 → 极快衰减
    "default":   (0.020, 7, 60),     # 未分类默认
}


# 默认遗忘阈值（strength 低于此值 → forgotten=1）
DEFAULT_SHOULD_FORGET_THRESHOLD: float = 0.5


def get_theme_decay_config(
    db: "Session" = None,
    character_id: Optional[int] = None,
) -> Dict[str, tuple]:
    """
    获取主题衰减配置（默认值 + 角色级覆盖）

    Args:
        db: SQLAlchemy session（可选，传入时尝试从 character.config 读取覆盖）
        character_id: 角色 ID（可选）

    Returns:
        {theme: (base_decay_rate, min_half_life_days, max_half_life_days)}
    """
    config: Dict[str, tuple] = dict(THEME_DECAY_CONFIG)
    if db is not None and character_id is not None:
        try:
            from backend.models import Character
            import json as _json
            char = db.query(Character).filter(Character.id == character_id).first()
            if char and char.config:
                cfg = _json.loads(char.config)
                decay_cfg = cfg.get("decay", {}) or {}
                themes_override = decay_cfg.get("themes", {}) or {}
                for theme, params in themes_override.items():
                    if theme in config and isinstance(params, dict):
                        config[theme] = (
                            params.get("base_decay_rate", config[theme][0]),
                            params.get("min_half_life_days", config[theme][1]),
                            params.get("max_half_life_days", config[theme][2]),
                        )
        except Exception as e:
            logger.debug("get_theme_decay_config 读取角色配置失败: %s", e)
    return config


def get_should_forget_threshold(
    db: "Session" = None,
    character_id: Optional[int] = None,
    default: Optional[float] = None,
) -> float:
    """获取遗忘阈值（默认值 + 角色级覆盖）"""
    threshold = default if default is not None else DEFAULT_SHOULD_FORGET_THRESHOLD
    if db is not None and character_id is not None:
        try:
            from backend.models import Character
            import json as _json
            char = db.query(Character).filter(Character.id == character_id).first()
            if char and char.config:
                cfg = _json.loads(char.config)
                decay_cfg = cfg.get("decay", {}) or {}
                if "should_forget_threshold" in decay_cfg:
                    threshold = decay_cfg["should_forget_threshold"]
        except Exception as e:
            logger.debug("get_should_forget_threshold 读取角色配置失败: %s", e)
    return threshold


# ======================================================================
# 核心函数
# ======================================================================
def compute_half_life_days(
    importance: int,
    theme: Optional[str] = None,
    db: "Session" = None,
    character_id: Optional[int] = None,
) -> float:
    """
    根据 importance 和 theme 计算半衰期（天）。

    Args:
        importance: 1-10 重要性评分
        theme: 主题（identity/music/taste/moment/todo）
        db: SQLAlchemy session（可选，传入时尝试从 character.config 读取覆盖）
        character_id: 角色 ID（可选）

    Returns:
        半衰期天数（float）
    """
    config = get_theme_decay_config(db, character_id)
    _, min_hl, max_hl = config.get(theme or "default", config["default"])
    # importance 0-10 → multiplier 0.5 ~ 1.5
    importance = max(1, min(10, importance))
    multiplier = 0.5 + (importance - 1) * (1.0 / 9.0)  # 0.5 - 1.5
    half_life = min_hl + (max_hl - min_hl) * ((importance - 1) / 9.0)
    return half_life * multiplier


def compute_current_strength(
    initial_strength: int,
    importance: int,
    age_days: float,
    recall_count: int = 0,
    theme: Optional[str] = None,
    now: Optional[datetime] = None,
    db: "Session" = None,
    character_id: Optional[int] = None,
) -> float:
    """
    计算当前 strength（衰减后）。

    公式：strength(t) = initial * 0.5^(t / half_life) * (1 + 0.1 * log(1 + recall_count))

    Args:
        initial_strength: 初始强度 0-10
        importance: 重要性 1-10
        age_days: 距 created_at 的天数
        recall_count: 被检索次数
        theme: 主题
        now: 当前时间（用于测试，默认 utcnow）
    """
    half_life = compute_half_life_days(
        importance, theme,
        db=db, character_id=character_id,
    )
    if half_life <= 0:
        return 0.0
    decay_factor = 0.5 ** (age_days / half_life)
    recall_boost = 1.0 + 0.1 * math.log1p(recall_count)
    return initial_strength * decay_factor * recall_boost


def should_forget(current_strength: float, threshold: float = 0.5) -> bool:
    """
    是否应被遗忘。

    Args:
        current_strength: 当前强度（0-10）
        threshold: 阈值（默认 0.5，即 strength < 0.5 视为遗忘）
    """
    return current_strength < threshold


def boost_factor(
    recall_count: int,
    age_days: float,
    importance: int,
    half_life: Optional[float] = None,
    lambda_decay: float = 0.01,
) -> float:
    """
    RRF 后置 boost 因子。

    boost = log(1 + recall_count) * exp(-lambda * age_days) * importance_normalized

    Args:
        recall_count: 被检索次数
        age_days: 距今天数
        importance: 重要性 1-10
        half_life: 半衰期（可选，备用）
        lambda_decay: 衰减常数（默认 0.01，0.5 年 ≈ 1.8）
    """
    rec = math.log1p(recall_count)
    age = math.exp(-lambda_decay * age_days)
    imp = max(1, min(10, importance)) / 10.0
    return rec * age * imp


# ======================================================================
# DB 操作
# ======================================================================
def run_decay_pass(
    db: Session,
    character_id: Optional[int] = None,
    forgotten_threshold: float = 0.5,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """
    跑一次衰减巡检：更新所有 memory 的 current strength，把低于阈值的标 forgotten。

    Args:
        db: SQLAlchemy session
        character_id: 限定角色（None = 全部）
        forgotten_threshold: 遗忘阈值（strength < 此值 → forgotten=1）
        now: 当前时间（默认 utcnow）

    Returns:
        {"scanned": N, "decayed": M, "forgotten": K}
    """
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)  # SQLAlchemy 存的是 naive UTC

    # 角色级 config 覆盖：threshold / theme decay rates
    if character_id is not None and forgotten_threshold == 0.5:
        # 默认 0.5 时才查角色 config；显式传入时优先用调用方的值
        forgotten_threshold = get_should_forget_threshold(db, character_id)

    q = db.query(Memory)
    if character_id is not None:
        q = q.filter(Memory.character_id == character_id)
    rows = q.all()

    scanned = 0
    decayed = 0
    forgotten = 0
    for row in rows:
        scanned += 1
        if row.created_at is None:
            continue
        # 计算 age_days（注意 DB 存的是 naive UTC）
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now.replace(tzinfo=timezone.utc) - created).total_seconds() / 86400.0)

        importance = row.importance or 5
        initial = row.strength or importance  # strength 默认等于 importance
        recall_count = row.recall_count or 0
        theme = row.theme or "default"

        new_strength = compute_current_strength(
            initial_strength=initial,
            importance=importance,
            age_days=age_days,
            recall_count=recall_count,
            theme=theme,
            db=db,
            character_id=character_id,
        )
        # strength 字段以 0-10 整数存储，向上/下取整
        new_int = int(round(new_strength))
        if new_int != (row.strength or 0):
            row.strength = max(0, min(10, new_int))
            decayed += 1

        # 遗忘判定
        if should_forget(new_strength, threshold=forgotten_threshold):
            if not row.forgotten:
                row.forgotten = 1
                forgotten += 1
        else:
            if row.forgotten:
                # 复活（重要性提升 / 频繁召回）
                row.forgotten = 0
    db.commit()
    return {"scanned": scanned, "decayed": decayed, "forgotten": forgotten}


def recall_memory(db: Session, memory_id: int) -> bool:
    """
    标记一条 memory 被"想起"（更新 last_recalled_at + recall_count += 1）。

    作用：recall_count 提升强度（"被想起 = 抗遗忘"）
    """
    try:
        row = db.query(Memory).filter(Memory.id == memory_id).first()
        if row is None:
            return False
        row.recall_count = (row.recall_count or 0) + 1
        row.last_recalled_at = datetime.now(timezone.utc).replace(tzinfo=None)
        # 每次 recall 也补一点 strength（防过快衰减）
        row.strength = min(10, (row.strength or 0) + 1)
        db.commit()
        return True
    except Exception as e:
        logger.warning("recall_memory 失败: %s", e)
        db.rollback()
        return False


def get_active_memories(
    db: Session,
    character_id: int,
    limit: int = 5,
    theme: Optional[str] = None,
    include_decay_boost: bool = True,
) -> List[Dict[str, Any]]:
    """
    获取角色的"活跃"记忆（forgotten=0），按 strength DESC 排序。

    Args:
        db: SQLAlchemy session
        character_id: 角色 ID
        limit: 返回数量
        theme: 限定主题（可选）
        include_decay_boost: 是否计算 boost 因子

    Returns:
        [{id, content, importance, theme, strength, recall_count, boost?}, ...]
    """
    q = db.query(Memory).filter(
        Memory.character_id == character_id,
        Memory.forgotten == 0,
    )
    if theme:
        q = q.filter(Memory.theme == theme)
    rows = q.order_by(Memory.strength.desc()).limit(limit).all()

    now = datetime.now(timezone.utc)
    results = []
    for row in rows:
        item = {
            "id": row.id,
            "content": row.content,
            "importance": row.importance,
            "theme": row.theme,
            "strength": row.strength,
            "recall_count": row.recall_count or 0,
            "memory_type": row.memory_type,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        if include_decay_boost and row.created_at:
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created).total_seconds() / 86400.0)
            item["boost"] = round(boost_factor(
                recall_count=row.recall_count or 0,
                age_days=age_days,
                importance=row.importance or 5,
            ), 4)
        results.append(item)
    return results


def get_forgotten_ratio(db: Session, character_id: int) -> float:
    """
    计算角色"遗忘比例"（forgotten / total）。触发摘要的信号之一。
    """
    total = db.query(Memory).filter(Memory.character_id == character_id).count()
    if total == 0:
        return 0.0
    forgotten = db.query(Memory).filter(
        Memory.character_id == character_id,
        Memory.forgotten == 1,
    ).count()
    return forgotten / total


# ======================================================================
# 模块导出
# ======================================================================
__all__ = [
    "compute_half_life_days",
    "compute_current_strength",
    "should_forget",
    "boost_factor",
    "run_decay_pass",
    "recall_memory",
    "get_active_memories",
    "get_forgotten_ratio",
    "THEME_DECAY_CONFIG",
    "DEFAULT_SHOULD_FORGET_THRESHOLD",
    "get_theme_decay_config",
    "get_should_forget_threshold",
]
