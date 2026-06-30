"""pytest 全局配置：确保项目根目录在 sys.path 中，并提供共享 fixtures"""
import sys
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient

# 将项目根目录（tests/../）加入模块搜索路径
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 加载 .env 环境变量（LLMService 初始化需要 API Key）
os.chdir(_project_root)
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    pass

from backend.database import Base, get_db
from backend.models import Character


# ============================================================
# 数据库 fixtures
# ============================================================
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

    # 注入到 get_db 依赖
    yield session

    session.close()
    trans.rollback()
    conn.close()


# ============================================================
# 角色 fixture
# ============================================================
@pytest.fixture
def sample_character(db_session):
    """创建示例角色"""
    char = Character(
        name="苏晴",
        description="温柔的高中语文老师",
        world_setting="2026 年春，江城",
        personality='{"empathy": 8, "optimism": 7}',
        current_state='{"mood": "happy"}',
    )
    db_session.add(char)
    db_session.commit()
    db_session.refresh(char)
    return char


@pytest.fixture
def sample_character_2(db_session):
    """创建第二个示例角色（用于列表/关系测试）"""
    char = Character(
        name="李墨",
        description="冷静的刑警队长",
        world_setting="2026 年春，江城",
        personality='{"empathy": 4, "optimism": 5}',
        current_state='{"mood": "calm"}',
    )
    db_session.add(char)
    db_session.commit()
    db_session.refresh(char)
    return char


# ============================================================
# db 别名（部分测试文件使用 db 而非 db_session）
# ============================================================
@pytest.fixture
def db(db_session):
    """db_session 的别名，兼容使用 'db' 参数名的测试文件"""
    return db_session


# ============================================================
# WorldEngine 单例重置（避免跨测试残留 stale session_factory）
# ============================================================
@pytest.fixture(autouse=True)
def _reset_world_engine(db_session):
    """每个测试前重置 WorldEngine 单例并绑定到当前测试 session。"""
    try:
        from backend.world.world_engine import reset_world_engine, get_world_engine
        reset_world_engine()
        get_world_engine(session_factory=lambda: db_session)
        yield
        reset_world_engine()
    except ImportError:
        yield


# ============================================================
# Mock Creation Module
# ============================================================
@pytest.fixture
def mock_creation_module(monkeypatch):
    """Mock backend.state.get_creation_module，避免真实 LLM 调用"""
    from unittest.mock import MagicMock
    from backend import state

    mock_module = MagicMock()
    mock_module.llm_service = MagicMock()

    def fake_run(user_input, input_type):
        parsed_data = {
            "name": "Mocked 角色",
            "description": user_input[:500],
            "personality": {"empathy": 5, "optimism": 5},
            "current_state": {"mood": "neutral"},
            "world_setting": "2026 年春，江城",
            "initial_memories": [
                {"content": "mock 初始记忆", "importance": 5}
            ],
        }
        return parsed_data, '{"raw": "mock"}'

    mock_module.run = fake_run
    mock_module.llm_service.call = MagicMock(return_value="润色后的描述文本")

    monkeypatch.setitem(state._singletons, "creation", mock_module)
    return mock_module


# ============================================================
# Mock Pipeline（对话管线 mock）
# ============================================================
@pytest.fixture
def mock_pipeline(monkeypatch, db_session):
    """Mock backend.state.get_pipeline，避免真实 LLM 调用"""
    from unittest.mock import MagicMock
    from backend import state
    from backend.services import chat_session_crud
    from backend.crud import conversation as conversation_crud
    from datetime import datetime

    class FakePipeline:
        def run(self, character_id, user_message, db, session_id=None):
            from backend.models import Character
            char = db.query(Character).filter(Character.id == character_id).first()
            if char is None:
                raise ValueError(f"角色不存在: {character_id}")

            sess = chat_session_crud.get_or_create_session(
                db, character_id=character_id, session_id=session_id, first_user_message=user_message
            )
            db.commit()

            conv = conversation_crud.create_conversation(
                db, character_id=character_id, session_id=sess.id,
                user_input=user_message, npc_response="mock NPC 回复",
            )
            db.commit()

            return {
                "id": conv.id,
                "character_id": character_id,
                "user_input": user_message,
                "npc_response": "mock NPC 回复",
                "emotion": "happy",
                "action": "smile",
                "expression": "^_^",
                "director_raw": "{}",
                "actor_raw": "{}",
                "timestamp": datetime.now().isoformat(),
                "session_id": sess.id,
                "session_title": sess.title,
                "elapsed_ms": {"director": 10, "actor": 20, "persist": 5, "total": 35},
            }

        def run_stream(self, character_id, user_message, db, session_id=None):
            raise NotImplementedError

        def reload(self):
            pass

    fake = FakePipeline()
    monkeypatch.setitem(state._singletons, "pipeline", fake)
    return fake


# ============================================================
# LLM Settings Store fixture
# ============================================================
@pytest.fixture
def llm_store(tmp_path, monkeypatch):
    """提供干净的 LLMSettingsStore（临时目录，不污染生产配置）"""
    from backend.services import llm_settings_store as store_module

    settings_dir = tmp_path / "usercontext"
    settings_dir.mkdir(exist_ok=True)
    settings_file = settings_dir / "llm_settings.json"

    monkeypatch.setattr(store_module, "_SETTINGS_DIR", str(settings_dir))
    monkeypatch.setattr(store_module, "_SETTINGS_FILE", str(settings_file))
    monkeypatch.setattr(store_module, "_cache", None)

    store = store_module.LLMSettingsStore()
    store._ensure_loaded()
    return store


# ============================================================
# FastAPI TestClient fixture
# ============================================================
@pytest.fixture
def client(db_session):
    """创建 FastAPI TestClient（带数据库覆盖）"""
    from backend.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.clear()
