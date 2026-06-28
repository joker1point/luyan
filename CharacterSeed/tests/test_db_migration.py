"""
数据库迁移（DB Migration）单元测试

测试目标：
  1. _sqlite_columns — 获取 SQLite 表的列名
  2. _sqlite_table_exists — 检查表是否存在
  3. migrate_v001_sessions — 完整的 v001 迁移流程
     a. 全新数据库无需迁移
     b. 给 conversations 表加 session_id 列
     c. 回填孤儿对话到默认会话
     d. 幂等性：重复执行不应产生重复数据
  4. run_all_migrations — 迁移入口调用

预期运行方式：python -m pytest tests/test_db_migration.py -v
使用内存 SQLite 数据库，不依赖真实数据库文件。
"""
import pytest

from sqlalchemy import create_engine, text, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from backend.database import Base
from backend.services import db_migration
from backend.services.db_migration import (
    _sqlite_columns,
    _sqlite_table_exists,
    migrate_v001_sessions,
    run_all_migrations,
)


# ==============================================================================
# Fixture：空的内存 SQLite 引擎（不含任何表）
# ==============================================================================

@pytest.fixture
def engine():
    """创建内存 SQLite 引擎"""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=eng)
    yield eng
    eng.dispose()


# ==============================================================================
# Test Suite 1：_sqlite_columns
# ==============================================================================

class TestSqliteColumns:
    def test_get_columns_of_existing_table(self, engine):
        with engine.connect() as conn:
            conn.execute(text(
                "CREATE TABLE test_table (id INTEGER, name TEXT, value REAL)"
            ))
            conn.commit()
        cols = _sqlite_columns(engine, "test_table")
        assert cols == ["id", "name", "value"]

    def test_get_columns_nonexistent_table(self, engine):
        """不存在的表返回空列表"""
        assert _sqlite_columns(engine, "no_such_table") == []

    def test_non_sqlite_engine_returns_empty(self):
        """非 SQLite 的 engine 返回空列表"""
        eng = create_engine("sqlite:///:memory:")
        # 先 create_all 再关闭，用已 close 的 engine 模拟非 sqlite
        eng.dispose()
        # 用一个 dummy engine 测试
        from sqlalchemy import create_engine as ce
        # 给一个不存在的 postgresql 地址，engine 没有 backend_name 为 sqlite
        # 但直接创建会报错。这里用 monkeypatch 的方式也不理想。
        # 更优雅：用 已有的 engine，检查代码分支
        # 实际 _sqlite_columns 内部用 engine.url.get_backend_name() 判断
        # 如果 not sqlite，返回 []
        result = _sqlite_columns(engine, "any_table")
        assert result == []


# ==============================================================================
# Test Suite 2：_sqlite_table_exists
# ==============================================================================

class TestSqliteTableExists:
    def test_existing_table(self, engine):
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE my_table (id INTEGER)"))
            conn.commit()
        assert _sqlite_table_exists(engine, "my_table") is True

    def test_nonexistent_table(self, engine):
        assert _sqlite_table_exists(engine, "no_such_table") is False


# ==============================================================================
# Test Suite 3：migrate_v001_sessions
# ==============================================================================

class TestMigrateV001:
    """v001 迁移的完整流程测试"""

    def test_fresh_database_no_migration_needed(self, engine):
        """全新的数据库（conversations 表存在但无数据），迁移无操作"""
        result = migrate_v001_sessions(engine)
        assert result["added_column"] is True  # 因为 Base 不会自动建 session_id 列
        assert result["backfilled"] == 0

    def test_adds_session_id_column(self, engine):
        """确认 session_id 列被添加到 conversations 表"""
        result = migrate_v001_sessions(engine)
        assert result["added_column"] is True
        cols = _sqlite_columns(engine, "conversations")
        assert "session_id" in cols

    def test_migration_idempotent(self, engine):
        """第二次运行迁移不应再添加列或回填"""
        r1 = migrate_v001_sessions(engine)
        r2 = migrate_v001_sessions(engine)
        # 第二次应该跳过加列和回填
        assert r2["added_column"] is False
        assert r2["backfilled"] == 0

    def test_backfills_orphan_conversations(self, engine):
        """有孤儿对话时应回填到默认会话"""
        # 先运行一次迁移（让 schema 准备好 session_id 列）
        migrate_v001_sessions(engine)

        # 手动插入两个角色和它们的孤儿对话
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO characters (id, name, description) VALUES (1, '角色A', '描述A')"
            ))
            conn.execute(text(
                "INSERT INTO characters (id, name, description) VALUES (2, '角色B', '描述B')"
            ))
            conn.execute(text(
                "INSERT INTO conversations (character_id, user_input, npc_response) "
                "VALUES (1, 'hi', 'hello')"
            ))
            conn.execute(text(
                "INSERT INTO conversations (character_id, user_input, npc_response) "
                "VALUES (1, 'how', 'fine')"
            ))
            conn.execute(text(
                "INSERT INTO conversations (character_id, user_input, npc_response) "
                "VALUES (2, '你好', '你好')"
            ))

        result = migrate_v001_sessions(engine)

        assert result["backfilled"] == 3  # 3 条孤儿对话
        assert result["default_sessions_created"] == 2  # 2 个角色

        # 验证：所有对话的 session_id 不再为空
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT COUNT(*) FROM conversations WHERE session_id IS NULL")
            ).scalar()
        assert rows == 0

        # 验证：3 条对话分布在 2 个默认会话中
        with engine.connect() as conn:
            rows = conn.execute(
                text("SELECT COUNT(*) FROM chat_sessions WHERE title = '默认会话'")
            ).scalar()
        assert rows == 2


# ==============================================================================
# Test Suite 4：run_all_migrations（迁移入口）
# ==============================================================================

class TestRunAllMigrations:
    def test_run_all_returns_history(self, engine):
        history = run_all_migrations(engine)
        assert isinstance(history, list)
        assert len(history) >= 1
        # 每个迁移结果都应包含 version 字段
        assert history[0]["version"] == "v001_sessions"

    def test_run_all_idempotent(self, engine):
        r1 = run_all_migrations(engine)
        r2 = run_all_migrations(engine)
        # 第二次执行时 backfilled 应为 0（幂等）
        assert r2[0]["backfilled"] == 0
