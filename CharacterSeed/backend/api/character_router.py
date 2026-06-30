"""
character_router — 角色 CRUD + 描述润色 + soul.md 管理。

端点：
  POST   /api/characters/create                角色创建（text / txt file 二选一）
  GET    /api/characters                       角色列表（分页）
  GET    /api/characters/{character_id}        单角色详情
  PATCH  /api/characters/{character_id}        通用更新（白名单字段，CRUD 校验）
  DELETE /api/characters/{character_id}        级联删除（memories / conversations / growth_logs / events）
  POST   /api/characters/polish-description    一句话润色（LLM）
  PUT    /api/characters/{character_id}/soul   更新灵魂设定（soul_md，向后兼容）

设计要点：
  - 描述润色复用 _creation_module 已加载的 LLMService，避免重复实例化。
  - 删除走 character_crud.cascade_delete_character()，确保无孤儿记录。
  - 删除后清空响应缓存 + 角色数据缓存（避免旧 LLM response 被新角色数据污染）。
  - PATCH 端点统一字段白名单校验，CRUD 层负责 dict/list → JSON 自动序列化。
"""
from __future__ import annotations
import json
import logging
import time
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.schemas import (
    CharacterResponse,
    CharacterUpdateRequest,
    PolishDescriptionRequest,
    PolishDescriptionResponse,
    SoulUpdateRequest,
)
from backend.crud import character as character_crud
from backend.crud import memory as memory_crud
from backend.state import get_creation_module
from backend.models import Character
from backend.modules.interaction import (
    cache_invalidate as invalidate_response_cache,
    char_data_cache_invalidate,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["character"])


@router.post("/api/characters/create", response_model=CharacterResponse)
async def create_character(
    description: Optional[str] = Form(None),
    story_file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """创建角色（支持一句话描述或TXT文件上传）。"""
    if story_file:
        content = await story_file.read()
        user_input = content.decode("utf-8")
        if description:
            user_input = user_input + "\n\n[额外的角色期望]\n" + description
        input_type = "file"
    elif description:
        user_input = description
        input_type = "text"
    else:
        raise HTTPException(status_code=400, detail="必须提供description或story_file")

    try:
        parsed_data, raw_response = get_creation_module().run(user_input, input_type)

        name = parsed_data.get("name", "未命名角色")
        world_setting = parsed_data.get("world_setting")
        personality = parsed_data.get("personality", {})
        current_state = parsed_data.get("current_state", {})
        initial_memories = parsed_data.get("initial_memories", [])
        # v009A: 外貌描述（LLM 在创建时已生成），dict 自动序列化为 JSON 字符串
        appearance = parsed_data.get("appearance")

        db_character = character_crud.create_character(
            db=db,
            name=name,
            description=user_input[:500],
            world_setting=world_setting,
            personality=personality,
            current_state=current_state,
            creation_raw=raw_response,
            appearance=appearance,  # v009A: dict 或 str，crud 会自动序列化
        )

        # Day 3：初始记忆写入 memories 表（type=event）
        for mem in initial_memories:
            if isinstance(mem, dict):
                memory_crud.create_memory(
                    db=db,
                    character_id=db_character.id,
                    content=mem.get("content", ""),
                    importance=mem.get("importance", 5),
                    memory_type="event",
                )

        return db_character
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"角色创建失败: {str(e)}")


@router.get("/api/characters", response_model=List[CharacterResponse])
def list_characters(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """获取角色列表。"""
    return character_crud.get_characters(db, skip=skip, limit=limit)


@router.get("/api/characters/{character_id}", response_model=CharacterResponse)
def get_character(character_id: int, db: Session = Depends(get_db)):
    """获取单个角色详情。"""
    character = character_crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="角色不存在")
    return character


# [P0#1 一致性修复] 新增通用 PATCH 端点，统一走白名单校验
# 之前没有任何端点可以让前端更新 long_term_goal / habits / values / speaking_style /
# world_id / current_location_id / day_number / name；只能通过 PUT /soul 更新 soul_md。
# 现在通过 PATCH /api/characters/{id} 一次性暴露全部可写字段。
@router.patch("/api/characters/{character_id}", response_model=CharacterResponse)
def patch_character(
    character_id: int,
    request: CharacterUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    通用更新角色字段（PATCH 语义：body 中只放需要改的字段）。

    字段白名单：见 backend.schemas.CHARACTER_WRITABLE_FIELDS。
    JSON 字段（personality / current_state / speaking_style / values / habits）
    支持传入 dict/list（自动序列化为 JSON 字符串）或已序列化的 str。

    副作用：清除该角色的数据缓存 + 响应缓存，确保后续请求拿最新数据。
    """
    # 先检查存在性（crud.update_character 在不存在时返回 None 而非 raise，
    # 这里需要返回 404 而不是返回 None，避免与"空更新"语义混淆）
    existing = character_crud.get_character(db, character_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="角色不存在")

    # Pydantic 已做类型校验 + extra=ignore；这里只取非 None 字段
    # （None 表示"未提供"；注意：None 显式提供 = "清空字段"，会被保留）
    payload = {k: v for k, v in request.model_dump(exclude_unset=False).items() if v is not None or k in request.model_fields_set}

    if not payload:
        # 空 body：直接返回当前对象（幂等操作，不报错）
        return existing

    updated = character_crud.update_character(db=db, character_id=character_id, **payload)
    if updated is None:
        # 极端情况：PATCH 过程中被并发删除
        raise HTTPException(status_code=404, detail="角色不存在")

    # 失效该角色的所有缓存
    char_data_cache_invalidate(character_id)
    invalidate_response_cache(character_id)

    return updated


@router.delete("/api/characters/{character_id}")
def delete_character(character_id: int, db: Session = Depends(get_db)):
    """
    级联删除角色及其所有关联数据。
    清理顺序：memories → conversations → growth_logs → characters，
    确保数据库无孤儿记录残留。
    """
    result = character_crud.cascade_delete_character(db, character_id)
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 角色被删除 → 清理该角色相关的所有缓存
    char_data_cache_invalidate(character_id)
    invalidate_response_cache(character_id)

    return {
        "detail": (
            f"角色「{result['name']}」及 {result['events_deleted']} 个事件、"
            f"{result['memories_deleted']} 条记忆、"
            f"{result['conversations_deleted']} 条对话、"
            f"{result['growth_logs_deleted']} 条成长记录已永久删除"
        )
    }


@router.post(
    "/api/characters/polish-description",
    response_model=PolishDescriptionResponse,
)
def polish_description(request: PolishDescriptionRequest):
    """
    润色角色描述（一步到位调用 LLM）。

    复用 CreationModule 已加载的 LLMService。
    提示词约束：保留原意 + 改善文学性 + 长度不超过原文 1.6 倍。
    """
    original = (request.description or "").strip()
    if not original:
        raise HTTPException(status_code=400, detail="描述不能为空")

    system_prompt = (
        "你是一位擅长角色文案的中文写作助手。"
        "你的任务是把用户给出的角色描述润色得更生动、有画面感，"
        "同时严格保留原意，不要凭空添加用户没说过的设定、姓名、时代背景或故事情节。"
        "输出仅返回润色后的中文文本，不要加引号、不要加解释、不要 Markdown 标记。"
    )
    user_prompt = (
        "请将以下角色描述润色得更具文学性与画面感，"
        "保持原意不变，长度不要超过原文的 1.6 倍。\n\n"
        f"原文：\n{original}\n\n"
        "润色后："
    )

    try:
        polished = get_creation_module().llm_service.call(
            prompt=user_prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=600,
            task="creation_polish",
        )
    except Exception as e:
        logger.exception("描述润色失败: %s", e)
        raise HTTPException(status_code=500, detail=f"润色失败: {str(e)[:200]}")

    polished = (polished or "").strip()
    if not polished:
        polished = original
    if (polished.startswith('"') and polished.endswith('"')) or (
        polished.startswith("'") and polished.endswith("'")
    ):
        polished = polished[1:-1].strip()

    return PolishDescriptionResponse(polished=polished, original=original)


@router.put("/api/characters/{character_id}/soul", response_model=CharacterResponse)
def update_character_soul(
    character_id: int,
    request: SoulUpdateRequest,
    db: Session = Depends(get_db),
):
    """
    更新角色灵魂设定（soul.md）。

    用户可通过此端点编辑角色的核心设定，内容以 Markdown 格式存储。
    更新后会清除该角色的数据缓存，确保后续请求获取最新数据。
    """
    character = character_crud.get_character(db, character_id)
    if character is None:
        raise HTTPException(status_code=404, detail="角色不存在")

    # 更新 soul_md 字段
    updated = character_crud.update_character(
        db=db,
        character_id=character_id,
        soul_md=request.soul_md,
    )

    # 清除该角色的数据缓存（避免旧数据污染）
    char_data_cache_invalidate(character_id)

    return updated


# ============================================================
# v009 角色头像自动生成（Agnes Image/Video API）
# ============================================================
# 端点：
#   POST /api/characters/{id}/avatar/generate   异步生成 4 张候选图
#   GET  /api/characters/{id}/avatar/status     查询当前状态 + 候选图
#   POST /api/characters/{id}/avatar/select     选定某张候选图为正式头像
#   POST /api/characters/{id}/avatar/video      异步生成视频头像
# 设计：
#   - 实际生成任务放到 FastAPI BackgroundTasks 异步跑，HTTP 立即返回 202
#   - 任务运行中把 status 写回 DB（前端轮询 status 即可）
#   - 失败时回滚 avatar_video_status='failed'，不影响角色其他功能
# ============================================================


class AvatarGenerateRequest(BaseModel):
    style: str = Field("anime", description="anime/realistic/watercolor/pixel/ink/comic")
    expression: str = Field("neutral", description="neutral/smile/serious/shy/angry")
    background: str = Field("simple", description="simple/scene/transparent")
    regenerate: bool = Field(False, description="True=忽略已有 candidates 重新生成")


class AvatarSelectRequest(BaseModel):
    index: int = Field(0, ge=0, description="候选图下标 0..N-1")


def _run_avatar_generation(
    character_id: int,
    style: str,
    expression: str,
    background: str,
    regenerate: bool,
) -> None:
    """
    后台任务：调 AvatarGenerationService 生成头像并写回 DB。
    使用独立 session（BackgroundTasks 不共享请求的 session）。
    """
    from backend.services.avatar_generation_service import AvatarGenerationService
    from backend.database import SessionLocal

    service = AvatarGenerationService.instance()
    db = SessionLocal()
    try:
        # [v9-fix] 用 asyncio.run 让异步生成在后台线程中跑通
        # FastAPI BackgroundTasks 在 threadpool 中跑同步函数，
        # 所以这里用 asyncio.run 起一个事件循环即可
        import asyncio
        result = asyncio.run(
            service.generate_avatars(
                character_id=character_id,
                db=db,
                style=style,
                expression=expression,
                background=background,
                regenerate=regenerate,
            )
        )
        logger.info(
            "角色 %d 头像生成完成：%d 张候选图 (status=%s)",
            character_id, len(result.get("candidates", [])), result.get("status"),
        )
    except Exception as e:
        logger.exception("角色 %d 头像生成失败: %s", character_id, e)
    finally:
        db.close()


def _run_video_generation(character_id: int, motion: str, duration: int) -> None:
    """后台任务：调 AvatarGenerationService 生成视频头像"""
    from backend.services.avatar_generation_service import AvatarGenerationService
    from backend.database import SessionLocal
    import asyncio

    service = AvatarGenerationService.instance()
    db = SessionLocal()
    try:
        result = asyncio.run(
            service.generate_avatar_video(
                character_id=character_id,
                db=db,
                motion=motion,
                duration=duration,
            )
        )
        logger.info("角色 %d 视频头像生成完成：%s", character_id, result)
    except Exception as e:
        logger.exception("角色 %d 视频头像生成失败: %s", character_id, e)
    finally:
        db.close()


@router.post("/api/characters/{character_id}/avatar/generate")
async def generate_avatar(
    character_id: int,
    request: AvatarGenerateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    异步生成角色头像（4 张候选图）。
    HTTP 立即返回 202 + task_id；前端轮询 GET /avatar/status 获取结果。
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    task_id = f"avatar-{character_id}-{int(time.time())}"

    # 提交后台任务（用 BackgroundTasks，简单且够用）
    background_tasks.add_task(
        _run_avatar_generation,
        character_id=character_id,
        style=request.style,
        expression=request.expression,
        background=request.background,
        regenerate=request.regenerate,
    )

    return {
        "status": "pending",
        "task_id": task_id,
        "estimated_seconds": 30,
        "character_id": character_id,
    }


@router.get("/api/characters/{character_id}/avatar/status")
async def get_avatar_status(character_id: int, db: Session = Depends(get_db)):
    """查询头像生成状态 + 候选图列表 + 视频头像状态"""
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    candidates: List[str] = []
    if character.avatar_candidates:
        try:
            candidates = json.loads(character.avatar_candidates)
        except Exception:
            candidates = []

    return {
        "status": "completed" if candidates else "none",
        "candidates": [
            {"url": url, "index": i} for i, url in enumerate(candidates)
        ],
        "selected_index": character.avatar_selected_index or 0,
        "current_avatar": character.avatar_url,
        "video_url": character.avatar_video_url,
        "video_status": character.avatar_video_status or "none",
        "generation_prompt": character.avatar_generation_prompt,
        "generated_at": (
            character.avatar_generated_at.isoformat()
            if character.avatar_generated_at else None
        ),
    }


@router.post("/api/characters/{character_id}/avatar/select")
async def select_avatar(
    character_id: int,
    request: AvatarSelectRequest,
    db: Session = Depends(get_db),
):
    """把第 N 张候选图"提升"为正式头像（同步）"""
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")

    from backend.services.avatar_generation_service import AvatarGenerationService
    import asyncio
    service = AvatarGenerationService.instance()
    try:
        result = await service.select_avatar(
            character_id=character_id, db=db, index=request.index,
        )
        # 清掉响应缓存，避免老头像被读出
        char_data_cache_invalidate(character_id)
        invalidate_response_cache(character_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("select_avatar 失败: %s", e)
        raise HTTPException(status_code=500, detail=f"选择头像失败: {e}")


@router.post("/api/characters/{character_id}/avatar/video")
async def generate_video_avatar(
    character_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    异步生成视频头像（基于当前 avatar_url）。
    立即返回 202；前端轮询 status 拿 video_url。
    """
    character = db.query(Character).filter(Character.id == character_id).first()
    if not character:
        raise HTTPException(status_code=404, detail="角色不存在")
    if not character.avatar_url:
        raise HTTPException(status_code=400, detail="请先生成静态头像")

    # 默认 motion + duration
    background_tasks.add_task(
        _run_video_generation,
        character_id=character_id,
        motion="breathing",
        duration=3,
    )
    return {
        "status": "pending",
        "estimated_seconds": 90,
        "character_id": character_id,
    }
