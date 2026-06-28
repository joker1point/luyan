"""
Creation 管线 + 入库 测试脚本（单文件）

用法：
    - 终端运行：python backend/test_temp_creationpipeline.py
    - VS Code 调试：选择 "Debug Creation Pipeline Test" 配置，F5 启动

功能：输入文本 → LLM 生成角色 → 持久化到数据库 → 打印角色 ID
"""

import json
import logging
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.database import SessionLocal, Base, engine
from backend.modules.creation import CreationModule
from backend.crud import character as character_crud
from backend.crud import memory as memory_crud
from backend.crud import event as event_crud
from backend.crud import world as world_crud
from backend.crud import scene as scene_crud

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)
Base.metadata.create_all(bind=engine)


def persist_character(db, parsed_data, user_input, raw_response):
    """
    将 parsed_data 持久化：World → Scenes → Character → Memories → Events → Goals
    逻辑与 main.py create_character 端点一致。
    """
    db = SessionLocal()
    try:
        name = parsed_data.get("name", "未命名")
        world_setting = parsed_data.get("world_setting")
        personality = parsed_data.get("personality", {})
        current_state = parsed_data.get("current_state", {})
        initial_memories = parsed_data.get("initial_memories", [])
        speaking_style = parsed_data.get("speaking_style", [])
        values = parsed_data.get("values", [])
        habits = parsed_data.get("habits", [])
        long_term_goal = parsed_data.get("long_term_goal", "")
        world_name = parsed_data.get("world_name", f"{name}的世界")
        core_worldview = parsed_data.get("core_worldview", (world_setting or "")[:100])
        scenes_data = parsed_data.get("scenes", [])
        day1_schedule = parsed_data.get("day1_schedule", [])
        short_term_goals = parsed_data.get("short_term_goals", [])

        # 1. World
        db_world = world_crud.create_world(db, name=world_name, core_worldview=core_worldview)

        # 2. Scenes
        idx_to_id = {}
        first_actual_id = None
        for i, s in enumerate(scenes_data):
            if not isinstance(s, dict):
                continue
            parent = idx_to_id.get(s.get("parent_index", -1))
            try:
                sc = scene_crud.create_scene(
                    db, world_id=db_world.id, name=s["name"],
                    scene_layer=s["scene_layer"], scene_type=s.get("scene_type"),
                    parent_scene_id=parent, description=s.get("description"), created_day=1,
                )
                idx_to_id[i] = sc.id
                if s["scene_layer"] == "actual" and first_actual_id is None:
                    first_actual_id = sc.id
            except Exception as e:
                logger.warning("Scene skip: '%s' → %s", s.get("name", "?"), e)

        if first_actual_id is None:
            loc = current_state.get("location", "初始地点") if isinstance(current_state, dict) else "初始地点"
            fb = scene_crud.create_scene(
                db, world_id=db_world.id, name=loc, scene_layer="actual",
                scene_type="location", created_day=1,
            )
            first_actual_id = fb.id

        # 3. Character
        char = character_crud.create_character(
            db, name=name, description=user_input[:500],
            world_setting=world_setting, personality=personality,
            current_state=current_state, creation_raw=raw_response,
            speaking_style=json.dumps(speaking_style, ensure_ascii=False) if isinstance(speaking_style, list) else speaking_style,
            values=json.dumps(values, ensure_ascii=False) if isinstance(values, list) else values,
            habits=json.dumps(habits, ensure_ascii=False) if isinstance(habits, list) else habits,
            long_term_goal=long_term_goal,
        )

        # 4. 关联 World + Scene
        character_crud.update_character(db, char.id, world_id=db_world.id, current_scene_id=first_actual_id)
        db.refresh(char)

        # 5. Memories
        for mem in initial_memories:
            if isinstance(mem, dict) and mem.get("content", "").strip():
                memory_crud.create_memory(
                    db, character_id=char.id, content=mem["content"].strip(),
                    importance=mem.get("importance", 5), memory_type="event",
                )

        # 6. Day1 Events
        for item in day1_schedule:
            if isinstance(item, dict) and item.get("content", "").strip():
                event_crud.create_event(
                    db, character_id=char.id, day_number=1,
                    order_index=item.get("order_index", 1),
                    event_type=item.get("event_type", "schedule_action"),
                    content=item["content"].strip(), status="pending",
                    time_period=item.get("time_period"),
                )

        # 7. short_term_goals
        if short_term_goals:
            character_crud.update_character(
                db, char.id,
                short_term_goals=json.dumps(short_term_goals, ensure_ascii=False),
            )
            db.refresh(char)

        db.commit()
        return char

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main():
    print("=" * 60)
    print("  CharacterSeed · Creation 管线 + 入库测试")
    print("=" * 60)
    print()
    print("输入方式：一句话 / 多行粘贴(Ctrl+Z结束) / :q 退出")
    print()

    lines = []
    try:
        while True:
            line = input()
            if line.strip() == ":q":
                print("已退出。")
                return
            lines.append(line)
    except EOFError:
        pass

    user_input = "\n".join(lines).strip()
    if not user_input:
        print("[错误] 输入为空。")
        return

    print(f"\n正在调用 Creation 管线 (输入 {len(user_input)} 字符)...")
    print("-" * 60)

    module = CreationModule()
    try:
        parsed_data, raw_response = module.run(user_input)
    except ValueError as e:
        print(f"\n[管线错误] {e}")
        return
    except Exception as e:
        print(f"\n[未知错误] {type(e).__name__}: {e}")
        raise

    print("\n✅ LLM 生成成功！")
    print(json.dumps(parsed_data, ensure_ascii=False, indent=2))
    print("-" * 60)

    # ── 持久化 ──
    print("\n正在持久化到数据库...")
    try:
        char = persist_character(None, parsed_data, user_input, raw_response)
    except Exception as e:
        print(f"\n[入库错误] {type(e).__name__}: {e}")
        raise

    print()
    print("=" * 60)
    print(f"  🎉 角色已入库！")
    print(f"  Character ID: {char.id}")
    print(f"  名称:         {parsed_data.get('name', '?')}")
    print(f"  世界:         {parsed_data.get('world_name', '?')}")
    print(f"  记忆:         {len(parsed_data.get('initial_memories', []))} 条")
    print(f"  日程:         {len(parsed_data.get('day1_schedule', []))} 条")
    print("=" * 60)
    print(f"  API: GET http://localhost:8000/api/characters/{char.id}")
    print(f"\n[调试] LLM 原始响应前 300 字符: {raw_response[:300]}...")


if __name__ == "__main__":
    main()
