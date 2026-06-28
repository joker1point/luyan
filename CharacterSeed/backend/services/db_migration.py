"""
数据库迁移工具

用途：处理 schema 演进。当前内置两个迁移：
  - MIGRATION_V001_SESSIONS: 引入 ChatSession，给 Conversation 加 session_id
    并把存量"孤儿"对话回填到每个角色的"默认会话"。

设计原则：
  - 幂等：可重复执行，不会重复添加列/重复回填
  - 不依赖 alembic 等第三方库，纯 SQL 兼容性最大
  - 失败抛异常让启动失败，便于及早发现
"""
import logging
import sqlite3
from typing import List

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _sqlite_columns(engine: Engine, table: str) -> List[str]:
    """读取 SQLite 表的列名列表（其它数据库 PRAGMA 行为可能不同）"""
    if not engine.url.get_backend_name().startswith("sqlite"):
        # 其它数据库暂时不处理，启动后端时跳过迁移
        return []
    with engine.connect() as conn:
        rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return [r[1] for r in rows]


def _sqlite_table_exists(engine: Engine, table: str) -> bool:
    if not engine.url.get_backend_name().startswith("sqlite"):
        return False
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": table},
        ).fetchone()
    return row is not None


def migrate_v001_sessions(engine: Engine) -> dict:
    """
    迁移 v001：引入 ChatSession + Conversation.session_id + 回填

    步骤：
      1) 确保 chat_sessions 表存在（Base.metadata.create_all 已创建）
      2) 给 conversations 加 session_id 列（若已存在则跳过）
      3) 给每条 session_id IS NULL 的对话，分配到一个名为"默认会话"的 session
         （按 character_id 分组，每个角色一个默认 session）

    Returns:
        {"added_column": bool, "backfilled": int, "default_sessions_created": int}
    """
    result = {"added_column": False, "backfilled": 0, "default_sessions_created": 0}

    if not _sqlite_table_exists(engine, "conversations"):
        return result  # 全新库，不需要迁移

    # 1) 加列
    cols = _sqlite_columns(engine, "conversations")
    if "session_id" not in cols:
        logger.info("迁移 v001: 添加 conversations.session_id 列")
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE conversations ADD COLUMN session_id INTEGER "
                "REFERENCES chat_sessions(id) ON DELETE CASCADE"
            ))
        result["added_column"] = True
    else:
        logger.debug("迁移 v001: conversations.session_id 已存在，跳过")

    # 2) 回填
    with engine.connect() as conn:
        # 找所有存在孤儿对话的 character_id
        rows = conn.execute(text(
            "SELECT DISTINCT character_id FROM conversations WHERE session_id IS NULL"
        )).fetchall()
    char_ids = [r[0] for r in rows]
    if not char_ids:
        logger.debug("迁移 v001: 无孤儿对话，无需回填")
        return result

    logger.info("迁移 v001: 为 %d 个角色回填默认会话", len(char_ids))
    with engine.begin() as conn:
        for cid in char_ids:
            # 用最早一条对话的时间作为 created_at（让默认会话在列表里更靠下）
            earliest = conn.execute(text(
                "SELECT MIN(timestamp) FROM conversations "
                "WHERE character_id = :cid AND session_id IS NULL"
            ), {"cid": cid}).scalar()

            # 创建默认 session
            res = conn.execute(text(
                "INSERT INTO chat_sessions (character_id, title, created_at, updated_at) "
                "VALUES (:cid, :title, :ts, :ts)"
            ), {"cid": cid, "title": "默认会话", "ts": earliest})
            new_sid = res.lastrowid
            result["default_sessions_created"] += 1

            # 把该角色的所有孤儿对话指给新 session
            upd = conn.execute(text(
                "UPDATE conversations SET session_id = :sid "
                "WHERE character_id = :cid AND session_id IS NULL"
            ), {"sid": new_sid, "cid": cid})
            result["backfilled"] += upd.rowcount or 0

    logger.info(
        "迁移 v001 完成: 加列=%s, 回填=%d 条, 创建默认会话=%d",
        result["added_column"], result["backfilled"], result["default_sessions_created"],
    )
    return result


def migrate_v002_event_and_character_fields(engine: Engine) -> dict:
    """
    迁移 v002：引入 events 表 + 给 characters 加 5 个字段

    步骤：
      1) Base.metadata.create_all 已自动建 events 表（若不存在）
      2) 给 characters 加列（已存在则跳过）：speaking_style / values /
         habits / long_term_goal / day_number
      3) 把已存在角色的 day_number 兜底置为 1

    Returns:
        {"added_columns": int, "backfilled_day_number": int}
    """
    result = {"added_columns": 0, "backfilled_day_number": 0}

    if not _sqlite_table_exists(engine, "characters"):
        return result  # 全新库，走 Base.metadata.create_all 即可

    cols = _sqlite_columns(engine, "characters")
    # 注意：SQLite 关键字 "values" 需要双引号转义
    new_cols = [
        ("speaking_style", "TEXT"),
        ("values", "TEXT"),  # SQL 里有 "values"，运行时按 f-string 拼双引号
        ("habits", "TEXT"),
        ("long_term_goal", "TEXT"),
        ("day_number", "INTEGER NOT NULL DEFAULT 1"),
    ]
    with engine.begin() as conn:
        for col_name, col_type in new_cols:
            if col_name in cols:
                continue
            quoted = f'"{col_name}"'  # 兜底转义（关键字也兼容）
            logger.info("迁移 v002: 添加 characters.%s 列", col_name)
            conn.execute(text(
                f"ALTER TABLE characters ADD COLUMN {quoted} {col_type}"
            ))
            result["added_columns"] += 1

        # 兜底回填 day_number（防御极老数据）
        upd = conn.execute(text(
            "UPDATE characters SET day_number = 1 WHERE day_number IS NULL"
        ))
        result["backfilled_day_number"] = upd.rowcount or 0

    logger.info(
        "迁移 v002 完成: 新增列=%d, 回填 day_number=%d",
        result["added_columns"], result["backfilled_day_number"],
    )
    return result


def migrate_v003_world_pillar(engine: Engine) -> dict:
    """
    迁移 v003：世界四要素（ADR-009）

    步骤：
      1) Base.metadata.create_all 已自动建 5 张表（World/Location/Item/Relationship/WorldEvent）
      2) 给 characters 加 world_id / current_location_id 列（外键）
      3) 种子：插入 world id=1 "默认世界"（若不存在）
      4) 兜底：老 characters 的 world_id 设为 1（如果为 NULL）

    Returns:
        {"added_columns": int, "default_world_seeded": bool, "backfilled_world_id": int}
    """
    result = {"added_columns": 0, "default_world_seeded": False, "backfilled_world_id": 0}

    if not _sqlite_table_exists(engine, "characters"):
        return result  # 全新库

    cols = _sqlite_columns(engine, "characters")
    new_cols = [
        ("world_id", "INTEGER REFERENCES worlds(id) ON DELETE SET NULL"),
        ("current_location_id", "INTEGER REFERENCES locations(id) ON DELETE SET NULL"),
    ]
    with engine.begin() as conn:
        for col_name, col_type in new_cols:
            if col_name in cols:
                continue
            logger.info("迁移 v003: 添加 characters.%s 列", col_name)
            conn.execute(text(
                f"ALTER TABLE characters ADD COLUMN {col_name} {col_type}"
            ))
            result["added_columns"] += 1

        # 3) 种子默认世界（id=1）
        existing_world = conn.execute(text(
            "SELECT 1 FROM worlds WHERE id = 1"
        )).fetchone()
        if not existing_world:
            logger.info("迁移 v003: 种子默认世界 (id=1, name='默认世界')")
            conn.execute(text(
                "INSERT INTO worlds (id, name, description, season, day_of_year, year, season_offset) "
                "VALUES (1, '默认世界', '系统启动时自动创建，默认世界', 'winter', 1, 1, 0)"
            ))
            result["default_world_seeded"] = True
        else:
            logger.debug("迁移 v003: 默认世界 (id=1) 已存在，跳过种子")

        # 4) 兜底：老 characters 的 world_id → 1
        upd = conn.execute(text(
            "UPDATE characters SET world_id = 1 WHERE world_id IS NULL"
        ))
        result["backfilled_world_id"] = upd.rowcount or 0

    logger.info(
        "迁移 v003 完成: 新增列=%d, 种子默认世界=%s, 回填 world_id=%d",
        result["added_columns"],
        result["default_world_seeded"],
        result["backfilled_world_id"],
    )
    return result


def migrate_v006_soul_md(engine: Engine) -> dict:
    """
    迁移 v006：给 characters 表新增 soul_md 列（灵魂设定，Markdown 格式）

    Returns:
        {"added_column": bool}
    """
    result = {"added_column": False}

    if not _sqlite_table_exists(engine, "characters"):
        return result

    cols = _sqlite_columns(engine, "characters")
    if "soul_md" in cols:
        logger.debug("迁移 v006: characters.soul_md 已存在，跳过")
        return result

    logger.info("迁移 v006: 添加 characters.soul_md 列")
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE characters ADD COLUMN soul_md TEXT"
        ))
    result["added_column"] = True
    logger.info("迁移 v006 完成: 新增列=%s", result["added_column"])
    return result


def run_all_migrations(engine: Engine) -> List[dict]:
    """
    按版本顺序执行所有迁移。在应用启动时调用一次。
    新增迁移时在此函数中追加。
    """
    history = []
    history.append({
        "version": "v001_sessions",
        **migrate_v001_sessions(engine),
    })
    history.append({
        "version": "v002_event_and_character_fields",
        **migrate_v002_event_and_character_fields(engine),
    })
    history.append({
        "version": "v003_world_pillar",
        **migrate_v003_world_pillar(engine),
    })
    history.append({
        "version": "v004_location_dual_write",
        **migrate_v004_location_dual_write(engine),
    })
    history.append({
        "version": "v005_memory_enhance",
        **migrate_v005_memory_enhance(engine),
    })
    history.append({
        "version": "v006_soul_md",
        **migrate_v006_soul_md(engine),
    })
    return history


def migrate_v005_memory_enhance(engine: Engine) -> dict:
    """
    迁移 v005：Memory 增强字段（jiwen + 遗忘系统）

    步骤：
      1) 给 memories 表加列（幂等：已存在则跳过）：
         theme / strength / recall_count / last_recalled_at /
         forgotten / decay_rate / source_msg_id
      2) 加索引：ix_memories_char_active_strength、ix_memories_char_theme

    Returns:
        {"added_columns": int, "added_indexes": int}
    """
    result = {"added_columns": 0, "added_indexes": 0}

    if not _sqlite_table_exists(engine, "memories"):
        return result  # 全新库，Base.metadata.create_all 已建好

    cols = _sqlite_columns(engine, "memories")
    new_cols = [
        ("theme",            "VARCHAR(20)"),                      # identity/music/taste/moment/todo
        ("strength",         "INTEGER DEFAULT 5"),                # 0-10 当前强度
        ("recall_count",     "INTEGER DEFAULT 0"),                # 被检索次数
        ("last_recalled_at", "DATETIME"),                          # 上次被检索时间
        ("forgotten",        "INTEGER DEFAULT 0"),                # 0/1 bool
        ("decay_rate",       "INTEGER DEFAULT 10"),               # 0-100 衰减率
        ("source_msg_id",    "INTEGER"),                          # 来源 conversation.id
        ("updated_at",       "DATETIME"),                          # onupdate 时间戳
    ]
    with engine.begin() as conn:
        for col_name, col_type in new_cols:
            if col_name in cols:
                continue
            logger.info("迁移 v005: 添加 memories.%s 列", col_name)
            conn.execute(text(
                f"ALTER TABLE memories ADD COLUMN {col_name} {col_type}"
            ))
            result["added_columns"] += 1

        # 索引（幂等：用 IF NOT EXISTS）
        index_ddl = [
            "CREATE INDEX IF NOT EXISTS ix_memories_char_active_strength ON memories (character_id, forgotten, strength)",
            "CREATE INDEX IF NOT EXISTS ix_memories_char_theme ON memories (character_id, theme)",
        ]
        for ddl in index_ddl:
            try:
                conn.execute(text(ddl))
                result["added_indexes"] += 1
            except Exception as e:
                logger.debug("索引可能已存在: %s", e)

    logger.info(
        "迁移 v005 完成: 新增列=%d, 新增索引=%d",
        result["added_columns"], result["added_indexes"],
    )
    return result


def migrate_v004_location_dual_write(engine: Engine) -> dict:
    """
    迁移 v004：location 字符串 → Location 外键（ADR-009 / Phase 3）

    步骤：
      1) Base.metadata.create_all 已建好 locations / characters 表
      2) 调用 backfill_location_strings_sqlite 把 current_state["location"] 字符串
         转为 Location 行 + current_location_id 外键
      3) 保留 current_state["location"] 字符串（双写期兼容）
         → Phase 5 之后才清空

    幂等性：已迁移的角色（current_location_id 非空）会被 SQL 过滤掉，自然跳过。
    """
    result = {"scanned": 0, "migrated": 0, "skipped": 0, "errors": 0}

    if not _sqlite_table_exists(engine, "locations"):
        # locations 表还不存在（v003 之前）→ 跳过
        logger.debug("迁移 v004: locations 表不存在，跳过（需先跑 v003）")
        return result

    from backend.world.location_dual_write import backfill_location_strings_sqlite
    return backfill_location_strings_sqlite(engine)
