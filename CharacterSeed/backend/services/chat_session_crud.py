"""
ChatSession CRUD（参考 NextChat 的会话管理能力）

提供：
  - create_session：创建新会话
  - get_session：按 ID 获取
  - list_sessions：按 character_id 列出，支持 search（标题模糊匹配）
  - rename_session：重命名（更新 title）
  - delete_session：删除会话 + 级联删除其下 conversations
  - touch_session：更新 updated_at（chat 完成后调用）
  - ensure_default_session：确保角色存在"默认会话"（migrate 或新建时使用）

设计：
  - 全部使用 ORM，不写裸 SQL
  - 列表查询按 updated_at 倒序
  - 搜索使用 LIKE %keyword%（SQLite/PG 都支持）
"""
import logging
from typing import List, Optional

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.orm import Session

from backend.models import ChatSession, Conversation

logger = logging.getLogger(__name__)


# ============================================================
# 业务工具函数
# ============================================================

def _sanitize_title(s: str) -> str:
    """
    清理标题中的损坏字符。

    处理：
      - 去除 U+FFFD 替换字符（encoding 丢失时 Python 插入的 ）
      - 去除 U+25A1 白色方块（□，某些损坏场景出现）
      - 连续 2+ 个 '?' 压缩为 1 个（避免 "??" 类噪声）
      - 如果清理后只剩标点或为空 → 返回 "新对话"
      - 去除首尾空白/标点
    """
    if not s:
        return s
    # 去除替换字符和白色方块
    s = s.replace('\ufffd', '').replace('\u25a1', '')
    # 压缩连续问号（2+ → 1）
    import re
    s = re.sub(r'\?{2,}', '?', s)
    # 如果清理后只剩问号或空白，视为损坏
    s = s.strip()
    if not s or s == '?' or all(c in '?.' for c in s):
        return ''
    return s


def derive_title_from_message(message: str, max_len: int = 30) -> str:
    """
    从首条用户消息自动生成会话标题。
    截断 + 去除空白 + 末尾省略号。

    Examples:
        "你好，请简单自我介绍" -> "你好，请简单自我介绍"
        ("这是一段非常非常长" * 10) -> "这是一段非常非常长这是一段非常..."
    """
    if not message:
        return "新对话"
    s = " ".join(str(message).split())  # 合并空白
    s = _sanitize_title(s)
    if not s:
        return "新对话"
    if len(s) <= max_len:
        return s or "新对话"
    return s[: max_len - 1] + "…"


# ============================================================
# CRUD
# ============================================================

def create_session(
    db: Session,
    character_id: int,
    title: Optional[str] = None,
) -> ChatSession:
    """创建新会话。title 为空时使用占位"新对话"，首条消息到达后再改。"""
    sess = ChatSession(
        character_id=character_id,
        title=title or "新对话",
    )
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return sess


def get_session(db: Session, session_id: int) -> Optional[ChatSession]:
    return db.get(ChatSession, session_id)


def get_or_create_session(
    db: Session,
    session_id: Optional[int],
    character_id: int,
    first_user_message: Optional[str] = None,
) -> ChatSession:
    """
    如果传了 session_id 且存在 → 返回该 session
    否则 → 为角色创建一个新 session，标题从 first_user_message 推导
    """
    if session_id is not None:
        sess = get_session(db, session_id)
        if sess and sess.character_id == character_id:
            return sess
        # session_id 不存在或角色不匹配 → 降级为创建新 session
        logger.warning(
            "session_id=%s 不存在或角色不匹配（期望 character_id=%s），创建新 session",
            session_id, character_id,
        )
    title = derive_title_from_message(first_user_message) if first_user_message else "新对话"
    return create_session(db, character_id, title=title)


def list_sessions(
    db: Session,
    character_id: int,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[ChatSession]:
    """
    列出某角色的会话，按 updated_at 倒序。
    search 关键字同时匹配 title（大小写不敏感）。
    """
    stmt = select(ChatSession).where(ChatSession.character_id == character_id)
    if search:
        kw = f"%{search.strip()}%"
        stmt = stmt.where(ChatSession.title.ilike(kw))
    stmt = stmt.order_by(desc(ChatSession.updated_at)).limit(limit).offset(offset)
    return list(db.execute(stmt).scalars().all())


def list_sessions_with_message_count(
    db: Session,
    character_id: int,
    search: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[dict]:
    """
    list_sessions 的增强版，附带每个 session 的消息条数。
    用单次子查询避免 N+1。

    Returns:
        [{id, character_id, title, created_at, updated_at, message_count}, ...]
    """
    from sqlalchemy import func

    # 消息数子查询
    msg_count_subq = (
        select(
            Conversation.session_id.label("sid"),
            func.count(Conversation.id).label("msg_count"),
        )
        .group_by(Conversation.session_id)
        .subquery()
    )

    stmt = (
        select(
            ChatSession,
            func.coalesce(msg_count_subq.c.msg_count, 0).label("message_count"),
        )
        .outerjoin(msg_count_subq, ChatSession.id == msg_count_subq.c.sid)
        .where(ChatSession.character_id == character_id)
    )
    if search:
        kw = f"%{search.strip()}%"
        stmt = stmt.where(ChatSession.title.ilike(kw))
    stmt = (
        stmt.order_by(desc(ChatSession.updated_at))
        .limit(limit)
        .offset(offset)
    )

    rows = db.execute(stmt).all()
    out = []
    for sess, msg_count in rows:
        out.append({
            "id": sess.id,
            "character_id": sess.character_id,
            "title": sess.title,
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
            "message_count": int(msg_count or 0),
        })
    return out


def rename_session(
    db: Session,
    session_id: int,
    new_title: str,
) -> Optional[ChatSession]:
    """重命名会话。返回更新后的对象，session 不存在返回 None。"""
    sess = get_session(db, session_id)
    if not sess:
        return None
    title = _sanitize_title((new_title or "").strip()) or "新对话"
    if len(title) > 200:
        title = title[:200]
    sess.title = title
    db.commit()
    db.refresh(sess)
    return sess


def delete_session(db: Session, session_id: int) -> bool:
    """
    删除会话（级联删除其下 conversation，依赖外键 ON DELETE CASCADE）。
    返回是否真的删了东西。
    """
    sess = get_session(db, session_id)
    if not sess:
        return False
    db.delete(sess)
    db.commit()
    return True


def touch_session(db: Session, session_id: int) -> None:
    """
    刷新 updated_at（每次 chat 完成后调用，让活跃会话在列表里排前面）。
    注意 onupdate=func.now() 会在 commit 时自动触发，
    这里显式更新一次确保 list 立即看到变化。
    """
    from sqlalchemy.sql import func
    sess = get_session(db, session_id)
    if sess:
        sess.updated_at = func.now()
        db.commit()


def ensure_default_session(db: Session, character_id: int) -> ChatSession:
    """
    确保角色有"默认会话"（migrate 已保证；后续如果误删可用此恢复）。
    若已存在同名 session 则复用。
    """
    stmt = select(ChatSession).where(
        and_(
            ChatSession.character_id == character_id,
            ChatSession.title == "默认会话",
        )
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing:
        return existing
    return create_session(db, character_id, title="默认会话")
