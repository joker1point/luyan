"""
ChatSession CRUD 单元测试

测试目标：
  1. 创建/获取/重命名/删除会话
  2. get_or_create_session 的"存在复用/不存在创建/角色不匹配降级"三种路径
  3. list_sessions 搜索功能（标题模糊匹配）
  4. list_sessions_with_message_count N+1 优化查询
  5. touch_session 刷新 updated_at
  6. derive_title_from_message 标题推导
  7. ensure_default_session 幂等性

预期运行方式：python -m pytest tests/test_chat_session_crud.py -v
使用内存 SQLite 数据库，不依赖真实数据库文件。
"""
import pytest
from datetime import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import Character, ChatSession, Conversation
from backend.services import chat_session_crud
from backend.crud import character as character_crud
from backend.crud import conversation as conversation_crud


# ==============================================================================
# Fixture：内存 SQLite 引擎 + 空表
# ==============================================================================

@pytest.fixture(scope="module")
def engine():
    """创建内存 SQLite 引擎（模块级，所有测试用例共享）"""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def db_session(engine):
    """每个测试用例独立的事务隔离"""
    conn = engine.connect()
    trans = conn.begin()
    Session = sessionmaker(bind=conn)
    session = Session()

    yield session

    session.close()
    trans.rollback()
    conn.close()


@pytest.fixture
def character(db_session):
    """创建一个测试用角色"""
    char = character_crud.create_character(
        db=db_session,
        name="测试角色",
        description="一个用于测试的角色",
        personality={},
        current_state={},
    )
    # 刷新以便能取到 id
    db_session.flush()
    db_session.refresh(char)
    return char


# ==============================================================================
# Test Suite 1：derive_title_from_message
# ==============================================================================

class TestDeriveTitle:
    """测试首条消息自动生成标题"""

    def test_normal_message(self):
        title = chat_session_crud.derive_title_from_message("你好，请自我介绍")
        assert title == "你好，请自我介绍"

    def test_long_message_truncated(self):
        long_msg = "这是一段非常非常长的消息超过了三十个字符限制应该被截断吧"
        title = chat_session_crud.derive_title_from_message(long_msg)
        assert len(title) <= 30
        assert title.endswith("…")

    def test_empty_message(self):
        title = chat_session_crud.derive_title_from_message("")
        assert title == "新对话"

    def test_whitespace_only(self):
        title = chat_session_crud.derive_title_from_message("   ")
        assert title == "新对话"

    def test_none_message(self):
        title = chat_session_crud.derive_title_from_message(None)
        assert title == "新对话"


# ==============================================================================
# Test Suite 2：基本 CRUD
# ==============================================================================

class TestBasicCRUD:
    """测试会话的创建/读取/重命名/删除"""

    def test_create_session(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        assert sess.id is not None
        assert sess.title == "新对话"
        assert sess.character_id == character.id

    def test_create_session_custom_title(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id, title="自定义标题")
        assert sess.title == "自定义标题"

    def test_get_session(self, db_session, character):
        created = chat_session_crud.create_session(db_session, character.id)
        fetched = chat_session_crud.get_session(db_session, created.id)
        assert fetched.id == created.id
        assert fetched.title == created.title

    def test_get_session_nonexistent(self, db_session):
        assert chat_session_crud.get_session(db_session, 99999) is None

    def test_rename_session(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        updated = chat_session_crud.rename_session(db_session, sess.id, "新名称")
        assert updated.title == "新名称"

    def test_rename_nonexistent(self, db_session):
        assert chat_session_crud.rename_session(db_session, 99999, "啥") is None

    def test_rename_empty_title_falls_back(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        updated = chat_session_crud.rename_session(db_session, sess.id, "  ")
        assert updated.title == "新对话"

    def test_rename_very_long_title_truncated(self, db_session, character):
        """超过 200 字符的标题应被截断"""
        sess = chat_session_crud.create_session(db_session, character.id)
        long_title = "x" * 300
        updated = chat_session_crud.rename_session(db_session, sess.id, long_title)
        assert len(updated.title) <= 200

    def test_delete_session(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        assert chat_session_crud.delete_session(db_session, sess.id) is True
        assert chat_session_crud.get_session(db_session, sess.id) is None

    def test_delete_nonexistent(self, db_session):
        assert chat_session_crud.delete_session(db_session, 99999) is False


# ==============================================================================
# Test Suite 3：get_or_create_session
# ==============================================================================

class TestGetOrCreate:
    """测试 get_or_create_session 的三种分支"""

    def test_existing_session_returns(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id, title="已有会话")
        result = chat_session_crud.get_or_create_session(db_session, sess.id, character.id)
        assert result.id == sess.id
        assert result.title == "已有会话"

    def test_nonexistent_session_id_creates_new(self, db_session, character):
        """传入不存在的 session_id → 降级创建新 session"""
        result = chat_session_crud.get_or_create_session(
            db_session, 99999, character.id, first_user_message="测试消息",
        )
        assert result.title == "测试消息"
        assert result.character_id == character.id

    def test_wrong_character_fallback(self, db_session, character):
        """session_id 对应的 character_id 与传入的不匹配 → 创建新 session"""
        # 创建另一个角色
        other = character_crud.create_character(
            db=db_session, name="其他角色", description="其他",
            personality={}, current_state={},
        )
        other_sess = chat_session_crud.create_session(db_session, other.id)

        # 用 character.id 去获取 other_sess → 应创建新会话
        result = chat_session_crud.get_or_create_session(
            db_session, other_sess.id, character.id,
            first_user_message="新对话消息",
        )
        assert result.id != other_sess.id
        assert result.character_id == character.id

    def test_no_session_id_creates_new(self, db_session, character):
        result = chat_session_crud.get_or_create_session(
            db_session, None, character.id, first_user_message="第一条消息",
        )
        assert result.title == "第一条消息"
        assert result.character_id == character.id

    def test_no_session_id_no_message(self, db_session, character):
        """session_id=None 且无消息 → 标题为默认"""
        result = chat_session_crud.get_or_create_session(
            db_session, None, character.id,
        )
        assert result.title == "新对话"


# ======================================================================
# Test Suite 4：会话列表（含搜索）
# ======================================================================

class TestListSessions:
    """测试列出会话及其搜索功能"""

    def test_empty_list(self, db_session, character):
        sessions = chat_session_crud.list_sessions(db_session, character.id)
        assert sessions == []

    def test_list_multiple(self, db_session, character):
        s1 = chat_session_crud.create_session(db_session, character.id, title="A")
        s2 = chat_session_crud.create_session(db_session, character.id, title="B")
        sessions = chat_session_crud.list_sessions(db_session, character.id)
        # 默认按 updated_at 倒序，所以 B 应在 A 之前
        assert len(sessions) == 2
        assert sessions[0].id == s2.id

    def test_list_search(self, db_session, character):
        chat_session_crud.create_session(db_session, character.id, title="关于天气")
        chat_session_crud.create_session(db_session, character.id, title="讨论剧情")
        results = chat_session_crud.list_sessions(db_session, character.id, search="天气")
        assert len(results) == 1
        assert results[0].title == "关于天气"

    def test_list_search_no_match(self, db_session, character):
        chat_session_crud.create_session(db_session, character.id, title="AAA")
        results = chat_session_crud.list_sessions(db_session, character.id, search="不存在")
        assert results == []

    def test_list_other_character_not_leaked(self, db_session, character):
        """不同角色的会话不应相互泄漏"""
        other = character_crud.create_character(
            db=db_session, name="其他", description="", personality={}, current_state={},
        )
        chat_session_crud.create_session(db_session, character.id, title="属于角色A")
        chat_session_crud.create_session(db_session, other.id, title="属于角色B")

        results = chat_session_crud.list_sessions(db_session, character.id)
        assert len(results) == 1
        assert results[0].title == "属于角色A"


# ==============================================================================
# Test Suite 5：list_sessions_with_message_count（N+1 优化）
# ==============================================================================

class TestListWithMessageCount:
    """测试带消息数的会话列表查询"""

    def test_message_count_zero(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        rows = chat_session_crud.list_sessions_with_message_count(db_session, character.id)
        assert len(rows) == 1
        assert rows[0]["message_count"] == 0

    def test_message_count_correct(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        # 添加两条对话
        conversation_crud.create_conversation(db_session, character.id, "hi", "hello", session_id=sess.id)
        conversation_crud.create_conversation(db_session, character.id, "how", "fine", session_id=sess.id)

        rows = chat_session_crud.list_sessions_with_message_count(db_session, character.id)
        assert rows[0]["message_count"] == 2


# ==============================================================================
# Test Suite 6：touch_session（更新 updated_at）
# ==============================================================================

class TestTouchSession:
    """测试触摸会话更新 updated_at"""

    def test_touch_session(self, db_session, character):
        sess = chat_session_crud.create_session(db_session, character.id)
        old_updated = sess.updated_at

        import time
        time.sleep(0.01)  # 确保时间推进
        chat_session_crud.touch_session(db_session, sess.id)

        db_session.refresh(sess)
        assert sess.updated_at >= old_updated

    def test_touch_nonexistent_does_nothing(self, db_session):
        """touch 不存在的 session 不应抛异常"""
        chat_session_crud.touch_session(db_session, 99999)


# ==============================================================================
# Test Suite 7：ensure_default_session
# ==============================================================================

class TestEnsureDefault:
    """测试 ensure_default_session 的幂等性"""

    def test_create_default(self, db_session, character):
        sess = chat_session_crud.ensure_default_session(db_session, character.id)
        assert sess.title == "默认会话"
        assert sess.character_id == character.id

    def test_ensure_twice_idempotent(self, db_session, character):
        s1 = chat_session_crud.ensure_default_session(db_session, character.id)
        s2 = chat_session_crud.ensure_default_session(db_session, character.id)
        # 幂等：第二次应返回相同的 session
        assert s1.id == s2.id
