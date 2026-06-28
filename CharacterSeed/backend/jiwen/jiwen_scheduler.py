"""
jiwen 后台 tick 调度器

职责：
  - 周期性推进所有角色的 jiwen 状态
  - 把 contact 触发器入 proactive_message 队列
  - 把 observation/find_activity 触发器落库（jiwen_triggers 表）

设计：
  - 默认 5 分钟一次
  - FastAPI lifespan 启动/停止
  - 单独的 daemon 线程（避免阻塞主服务）
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.jiwen import get_jiwen_manager

logger = logging.getLogger(__name__)


class JiwenBackgroundScheduler:
    """
    jiwen 后台 tick 调度器（单例）
    """

    _instance: Optional["JiwenBackgroundScheduler"] = None
    _lock = threading.Lock()

    def __init__(self, interval_seconds: int = 300):
        """
        Args:
            interval_seconds: tick 间隔（秒），默认 5 分钟
        """
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._last_run_at: Optional[datetime] = None
        self._last_result: Dict[int, List[Dict[str, Any]]] = {}

    @classmethod
    def instance(cls) -> "JiwenBackgroundScheduler":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def start(self) -> None:
        """启动后台线程（幂等）"""
        if self._is_running:
            logger.info("JiwenBackgroundScheduler 已在运行")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="jiwen-tick", daemon=True,
        )
        self._thread.start()
        self._is_running = True
        logger.info(
            "JiwenBackgroundScheduler 已启动 (interval=%ds)",
            self.interval_seconds,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """停止后台线程"""
        if not self._is_running:
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._is_running = False
        logger.info("JiwenBackgroundScheduler 已停止")

    def tick_now(self) -> Dict[int, List[Dict[str, Any]]]:
        """
        立即跑一次（供测试 / 手动触发）。
        Returns:
            {character_id: [triggers]}
        """
        try:
            mgr = get_jiwen_manager()
            result = mgr.tick_all_active()
            self._last_run_at = datetime.now(timezone.utc)
            self._last_result = result
            return result
        except Exception as e:
            logger.warning("JiwenBackgroundScheduler.tick_now 失败: %s", e)
            return {}

    def _loop(self) -> None:
        """主循环"""
        # 启动后等 10s 再开始第一次（让服务先稳定）
        if self._stop_event.wait(timeout=10.0):
            return
        while not self._stop_event.is_set():
            try:
                self.tick_now()
            except Exception as e:
                logger.exception("jiwen tick 循环异常: %s", e)
            # 等待下一次
            if self._stop_event.wait(timeout=self.interval_seconds):
                break

    def status(self) -> Dict[str, Any]:
        return {
            "is_running": self._is_running,
            "interval_seconds": self.interval_seconds,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "characters_with_triggers": len(self._last_result),
            "total_triggers": sum(len(t) for t in self._last_result.values()),
        }


# 便捷函数
def start_scheduler(interval_seconds: int = 300) -> None:
    JiwenBackgroundScheduler.instance(interval_seconds).start() if False else None  # type: ignore
    # 简化：直接调
    sched = JiwenBackgroundScheduler(interval_seconds=interval_seconds)
    JiwenBackgroundScheduler._instance = sched  # noqa
    sched.start()


def stop_scheduler() -> None:
    JiwenBackgroundScheduler.instance().stop()


def get_scheduler() -> JiwenBackgroundScheduler:
    return JiwenBackgroundScheduler.instance()


__all__ = [
    "JiwenBackgroundScheduler",
    "start_scheduler",
    "stop_scheduler",
    "get_scheduler",
]
