"""
主动消息生成模块（Proactive Message Generator）

职责：
  - 异步调用 LLM 生成角色主动消息内容
  - LLM 失败时 fallback 到硬编码模板
  - 根据 Jiwen 状态（connection/pride）生成不同语气的消息

设计：
  - asyncio.create_task() 异步生成，不阻塞 tick 调度器
  - 超时 10 秒 + fallback 模板
  - 模板覆盖 3 档 connection × 2 档 pride = 6 种场景

测试隔离：
  - generate_proactive_content() 接受 session_factory 参数
  - 测试可注入 TestingSessionLocal
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import threading
from typing import Any, Callable, Dict, Optional

from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import Character
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)


# ======================================================================
# 后台事件循环（供同步上下文调度异步任务）
# ======================================================================
_proactive_loop: Optional[asyncio.AbstractEventLoop] = None
_proactive_thread: Optional[threading.Thread] = None
_proactive_lock = threading.Lock()


def _get_proactive_loop() -> asyncio.AbstractEventLoop:
    """获取/创建主动消息专用后台事件循环（单例）"""
    global _proactive_loop, _proactive_thread
    with _proactive_lock:
        if _proactive_loop is not None and _proactive_loop.is_running():
            return _proactive_loop
        _proactive_loop = asyncio.new_event_loop()
        _proactive_thread = threading.Thread(
            target=_proactive_loop.run_forever,
            name="proactive-loop",
            daemon=True,
        )
        _proactive_thread.start()
        logger.info("主动消息后台事件循环已启动")
        return _proactive_loop


def dispatch_proactive_message(
    character_id: int,
    trigger_state: Dict[str, Any],
    trigger_id: int,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """
    同步入口：将异步生成任务提交到后台事件循环。
    可从任意线程安全调用（jiwen scheduler daemon thread 等）。
    """
    loop = _get_proactive_loop()
    asyncio.run_coroutine_threadsafe(
        generate_and_store_proactive_message(
            character_id=character_id,
            trigger_state=trigger_state,
            trigger_id=trigger_id,
            session_factory=session_factory,
        ),
        loop,
    )


# ======================================================================
# Fallback 模板（默认 + 角色级覆盖）
# ======================================================================
def get_fallback_template(
    trigger_state: Dict[str, Any],
    character_id: Optional[int] = None,
    session_factory: Optional[Callable[[], Session]] = None,
) -> str:
    """
    根据 Jiwen 状态返回 fallback 模板。

    优先顺序：
      1) Character.config.jiwen.fallback_templates（角色自定义列表中随机选一条）
      2) 硬编码默认模板（按 connection × pride 3×2 矩阵共 6 种）

    Args:
        trigger_state: 触发时的状态快照，包含 connection/pride 等
        character_id: 角色 ID（可选；传入时尝试读取角色级 config）
        session_factory: 自定义 session 工厂（可选）

    Returns:
        主动消息内容字符串
    """
    # 1) 角色级覆盖
    if character_id is not None:
        sf = session_factory or SessionLocal
        try:
            with sf() as db:
                char = db.query(Character).filter(Character.id == character_id).first()
                if char and char.config:
                    cfg = json.loads(char.config)
                    templates = (
                        (cfg.get("jiwen", {}) or {}).get("fallback_templates") or []
                    )
                    if templates:
                        return random.choice(templates)
        except Exception as e:
            logger.debug("get_fallback_template 读角色 config 失败: %s", e)

    # 2) 硬编码默认
    connection = trigger_state.get("connection", 0)
    pride = trigger_state.get("pride", 0)

    if connection >= 0.5:
        if pride >= 0.3:
            return "（嘴硬地）人呢？怎么不说话了？"
        else:
            return "在忙吗？想找你聊聊。"
    elif connection >= 0.35:
        if pride >= 0.3:
            return "（犹豫了一下）...在吗？"
        else:
            return "最近怎么样？"
    else:
        return "嘿，有空吗？"


# ======================================================================
# LLM 动态生成
# ======================================================================
async def generate_proactive_content(
    character_id: int,
    trigger_state: Dict[str, Any],
    session_factory: Optional[Callable[[], Session]] = None,
) -> str:
    """
    异步调用 LLM 生成主动消息内容，失败时 fallback 到模板。

    Args:
        character_id: 角色 ID
        trigger_state: 触发时的状态快照（connection/pride/reason 等）
        session_factory: 可选的自定义 session 工厂（测试用）

    Returns:
        生成的主动消息内容（LLM 或 fallback）
    """
    _session_factory = session_factory or SessionLocal

    try:
        # 1. 获取角色设定
        with _session_factory() as db:
            character = db.query(Character).filter(Character.id == character_id).first()
            if not character:
                logger.warning("角色不存在: %d", character_id)
                return get_fallback_template(
                    trigger_state, character_id=character_id,
                    session_factory=_session_factory,
                )

            character_name = character.name
            soul_md = character.soul_md or "无特殊设定"

        # 2. 构建 prompt
        reason = trigger_state.get("reason", "想和你聊聊")
        connection = trigger_state.get("connection", 0)
        pride = trigger_state.get("pride", 0)

        prompt = f"""角色：{character_name}
灵魂设定：{soul_md}
当前情绪状态：
  - connection（连接需求）: {connection:.2f}
  - pride（自尊）: {pride:.2f}
触发原因：{reason}

请生成一句角色会主动说的话（1-2句，符合角色性格和当前情绪，自然口语化）："""

        # 3. 调用 LLM（超时 10 秒）
        llm = LLMService()
        # [切换] 主动消息走 task_routing 配置（默认 'time' 任务，对应 qwen）
        content = await asyncio.wait_for(
            asyncio.to_thread(llm.call, prompt, task="time"),
            timeout=10.0,
        )

        # 4. 清理输出
        content = content.strip()
        if not content:
            raise ValueError("LLM 返回空内容")

        logger.info(
            "LLM 生成主动消息成功: character=%d, content=%s",
            character_id,
            content[:50],
        )
        return content

    except asyncio.TimeoutError:
        logger.warning("LLM 生成主动消息超时: character=%d", character_id)
        return get_fallback_template(
            trigger_state, character_id=character_id,
            session_factory=_session_factory,
        )
    except Exception as e:
        logger.warning("LLM 生成主动消息失败: character=%d, error=%s", character_id, e)
        return get_fallback_template(
            trigger_state, character_id=character_id,
            session_factory=_session_factory,
        )


# ======================================================================
# 异步任务包装（供 jiwen_manager 调用）
# ======================================================================
async def generate_and_store_proactive_message(
    character_id: int,
    trigger_state: Dict[str, Any],
    trigger_id: int,
    session_factory: Optional[Callable[[], Session]] = None,
) -> None:
    """
    异步生成并存储主动消息（供 asyncio.create_task 调用）。

    Args:
        character_id: 角色 ID
        trigger_state: 触发时的状态快照
        trigger_id: 对应的触发器 ID
        session_factory: 可选的自定义 session 工厂
    """
    from backend.models import ProactiveMessage

    _session_factory = session_factory or SessionLocal

    # 1. 生成内容
    content = await generate_proactive_content(
        character_id=character_id,
        trigger_state=trigger_state,
        session_factory=_session_factory,
    )

    # 2. 入库
    try:
        with _session_factory() as db:
            msg = ProactiveMessage(
                character_id=character_id,
                content=content,
                trigger_id=trigger_id,
            )
            db.add(msg)
            db.commit()
            logger.info(
                "主动消息已入库: character=%d, trigger=%d, content=%s",
                character_id,
                trigger_id,
                content[:50],
            )

            # 3. 通过 SSE 推送给所有连接的客户端
            try:
                from backend.api.jiwen_router import push_proactive_message, _sse_clients
                if _sse_clients:
                    await push_proactive_message({
                        "message_id": msg.id,
                        "character_id": character_id,
                        "content": content,
                        "is_proactive": True,
                    })
            except Exception as push_err:
                logger.warning("SSE 推送失败: %s", push_err)
    except Exception as e:
        logger.warning("主动消息入库失败: character=%d, error=%s", character_id, e)
