"""
jiwen 后台 tick 调度器

职责：
  - 周期性推进所有角色的 jiwen 状态
  - 把 contact 触发器入 proactive_message 队列
  - 把 observation/find_activity 触发器落库（jiwen_triggers 表）

设计：
  - 默认 5 分钟一次（可配置）
  - 支持「降级间隔」和「恢复间隔」：
    * degraded_interval_seconds: 系统高负载/手动降级时使用（更长间隔）
    * recovery_interval_seconds: 恢复后使用的间隔
    * 默认 normal interval = 300s
  - FastAPI lifespan 启动/停止
  - 单独的 daemon 线程（避免阻塞主服务）
  - jiwen 是 CRITICAL_MODULE，降级只是降低 tick 频率，不能停
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
            interval_seconds: 默认 tick 间隔（秒），默认 5 分钟
        """
        self.interval_seconds = interval_seconds
        # 降级/恢复间隔（用户可配置）
        # degraded: 调高间隔，减少资源占用
        # recovery: 恢复正常频率
        self.degraded_interval_seconds: int = 900  # 15 分钟
        self.recovery_interval_seconds: int = 300   # 5 分钟
        # 当前模式：'normal' | 'degraded' | 'recovery'
        self.mode: str = 'normal'
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._is_running = False
        self._last_run_at: Optional[datetime] = None
        self._last_result: Dict[int, List[Dict[str, Any]]] = {}
        # 标记本调度器为关键模块（不得被停止 / 降级到 0）
        self.is_critical: bool = True

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
            "JiwenBackgroundScheduler 已启动 (interval=%ds, mode=%s, critical=%s)",
            self.interval_seconds, self.mode, self.is_critical,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """停止后台线程（CRITICAL 模块保护：拒绝停止关键模块）"""
        if not self._is_running:
            return
        if self.is_critical:
            logger.warning(
                "JiwenBackgroundScheduler 是关键模块（is_critical=True），"
                "拒绝完全停止。请使用 set_mode('degraded') 降低频率。"
            )
            return
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        self._is_running = False
        logger.info("JiwenBackgroundScheduler 已停止")

    def set_mode(
        self,
        mode: str,
        degraded_interval: Optional[int] = None,
        recovery_interval: Optional[int] = None,
    ) -> None:
        """
        切换运行模式。

        Args:
            mode: 'normal' | 'degraded' | 'recovery'
            degraded_interval: 自定义降级间隔（秒）
            recovery_interval: 自定义恢复间隔（秒）
        """
        if mode not in ('normal', 'degraded', 'recovery'):
            raise ValueError(f"未知模式: {mode}")

        if degraded_interval is not None and degraded_interval >= 30:
            self.degraded_interval_seconds = degraded_interval
        if recovery_interval is not None and recovery_interval >= 30:
            self.recovery_interval_seconds = recovery_interval

        self.mode = mode
        if mode == 'degraded':
            self.interval_seconds = self.degraded_interval_seconds
        else:
            # normal / recovery 都使用 recovery_interval
            self.interval_seconds = self.recovery_interval_seconds

        logger.info(
            "JiwenBackgroundScheduler 模式切换: %s (interval=%ds)",
            mode, self.interval_seconds,
        )

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
            "mode": self.mode,
            "degraded_interval_seconds": self.degraded_interval_seconds,
            "recovery_interval_seconds": self.recovery_interval_seconds,
            "is_critical": self.is_critical,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "characters_with_triggers": len(self._last_result),
            "total_triggers": sum(len(t) for t in self._last_result.values()),
        }


# 便捷函数
def start_scheduler(
    interval_seconds: int = 300,
    degraded_interval: Optional[int] = None,
    recovery_interval: Optional[int] = None,
    mode: str = 'normal',
) -> None:
    """
    启动调度器（可同时配置降级/恢复间隔）。

    Args:
        interval_seconds: normal 模式下的间隔
        degraded_interval: degraded 模式下的间隔（更高频率节省资源）
        recovery_interval: 恢复后的间隔
        mode: 启动模式（normal / degraded / recovery）
    """
    sched = JiwenBackgroundScheduler.instance()
    sched.interval_seconds = interval_seconds
    if degraded_interval is not None:
        sched.degraded_interval_seconds = degraded_interval
    if recovery_interval is not None:
        sched.recovery_interval_seconds = recovery_interval
    sched.mode = mode
    if mode == 'degraded':
        sched.interval_seconds = sched.degraded_interval_seconds
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
