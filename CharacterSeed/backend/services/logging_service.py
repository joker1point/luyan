"""
LoggingService — 完整的系统日志服务。

设计目标（对应任务 1-7）：
  1. 错误捕获覆盖：前端 / 后端 / 数据库 / 第三方 / 内部 → 统一通过 record() 入口
  2. 每条记录含：timestamp / level / type / source / message / stack / request_path /
     request_params / user_id / env_info（详见 models.ErrorLog）
  3. 分级存储：
     - ERROR / CRITICAL → 同步写库（error_logs 表），实时可查
     - WARNING / INFO / DEBUG → 异步写文件（usercontext/logs/YYYY-MM-DD.jsonl）
  4. 日志管理：list / stats / trend API（在 api/logs_router.py）
  5. 告警：CRITICAL/ERROR 触发时异步调用 Notifier（webhook / email / console）
  6. 高可用：
     - 异步队列 + 后台 worker 线程，主线程 record() 不阻塞
     - 队列容量上限（默认 10000），溢出时丢弃 + 计数器自监控
     - worker 崩溃自愈（daemon=True 不会阻塞进程退出）
     - 写库 / 写文件失败时回退到 stderr，永不抛出影响主流程
  7. 统计：内存 ring buffer 维护近 N 条记录，stats/trend 直接走 SQL 聚合

单例模式：与项目其他服务一致（参考 llm_settings_store / llm_service），
          整个进程共享一个 LoggingService 实例。
"""
import json
import logging
import os
import queue
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.models import ErrorLog, AlertConfig
from backend.database import SessionLocal

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# 常量
# -------------------------------------------------------------------
LEVEL_ORDER = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
# 哪些 level 走 DB（实时可查）；其余走文件系统
DB_LEVELS = {"ERROR", "CRITICAL"}
# 哪些 level 触发告警候选（实际是否触发由 AlertConfig.min_level 决定）
ALERT_CANDIDATE_LEVELS = {"ERROR", "CRITICAL"}

DEFAULT_QUEUE_SIZE = 10000
DEFAULT_RING_SIZE = 500  # 内存 ring buffer 保留近 N 条（仅用于快速预览/统计缓存）

# 路径：<project_root>/usercontext/logs/
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_LOG_DIR = os.path.join(_PROJECT_ROOT, "usercontext", "logs")
os.makedirs(_LOG_DIR, exist_ok=True)


# -------------------------------------------------------------------
# LoggingService
# -------------------------------------------------------------------
class LoggingService:
    """
    日志服务单例。

    用法：
        svc = LoggingService.instance()
        svc.record(level="ERROR", error_type="backend", source="interaction:run",
                   message="对话失败", stack_trace=traceback.format_exc(),
                   request_path="/api/chat", request_params='{"id":1}',
                   user_id="anonymous", env_info='{"browser":"chrome"}')
    """

    _instance: Optional["LoggingService"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=DEFAULT_QUEUE_SIZE)
        self._ring = deque(maxlen=DEFAULT_RING_SIZE)  # 最近 N 条
        self._dropped = 0  # 队列溢出计数
        # [P0#1 修复] _stop 必须在启动 worker 前初始化，避免线程先于属性赋值触发 AttributeError
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._run_worker, name="cs-logger", daemon=True)
        self._worker.start()
        logger.info("[logging] LoggingService 已启动，DB_LEVELS=%s, log_dir=%s", DB_LEVELS, _LOG_DIR)

    # -------------------------------------------------------------------
    # 单例
    # -------------------------------------------------------------------
    @classmethod
    def instance(cls) -> "LoggingService":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # -------------------------------------------------------------------
    # 公共入口
    # -------------------------------------------------------------------
    def record(
        self,
        level: str = "ERROR",
        error_type: str = "backend",
        source: Optional[str] = None,
        message: str = "",
        stack_trace: Optional[str] = None,
        request_path: Optional[str] = None,
        request_params: Optional[str] = None,
        user_id: Optional[str] = None,
        env_info: Optional[str] = None,
    ) -> bool:
        """
        记录一条日志（非阻塞；队列满则丢弃并自增 dropped 计数）。

        返回 True 表示成功入队，False 表示被丢弃。
        """
        # 归一化 level
        lvl = (level or "ERROR").upper()
        if lvl not in LEVEL_ORDER:
            lvl = "ERROR"
        # 归一化 error_type
        et = (error_type or "backend").lower()
        if et not in {"frontend", "backend", "database", "third_party", "internal"}:
            et = "internal"

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": lvl,
            "error_type": et,
            "source": source or "",
            "message": message or "",
            "stack_trace": stack_trace or "",
            "request_path": request_path or "",
            "request_params": request_params or "",
            "user_id": user_id or "",
            "env_info": env_info or "",
        }

        # ring buffer 先入（快速预览用）
        try:
            self._ring.append(entry)
        except Exception:
            pass

        # 入队（非阻塞；满了直接丢）
        try:
            self._queue.put_nowait(entry)
            return True
        except queue.Full:
            self._dropped += 1
            return False

    # 便捷封装：从前端 request 体构造记录
    def record_from_payload(self, payload: Dict[str, Any]) -> bool:
        if not isinstance(payload, dict):
            return False
        return self.record(
            level=payload.get("level", "ERROR"),
            error_type=payload.get("error_type", "backend"),
            source=payload.get("source"),
            message=payload.get("message", ""),
            stack_trace=payload.get("stack_trace"),
            request_path=payload.get("request_path"),
            request_params=payload.get("request_params"),
            user_id=payload.get("user_id"),
            env_info=payload.get("env_info"),
        )

    # -------------------------------------------------------------------
    # 内部：worker 线程
    # -------------------------------------------------------------------
    def _run_worker(self) -> None:
        """
        后台消费者：不断从队列取日志，分发到 DB / 文件。
        worker 异常被 try/except 捕获并打 stderr，绝不退出进程。
        """
        while not self._stop.is_set():
            try:
                entry = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._persist(entry)
            except Exception as e:
                # 最后的兜底：写到 stderr
                try:
                    print(f"[logging] worker persist error: {e}", flush=True)
                except Exception:
                    pass
            finally:
                self._queue.task_done()

    def _persist(self, entry: Dict[str, Any]) -> None:
        """分发：ERROR/CRITICAL → DB；其余 → 文件"""
        if entry["level"] in DB_LEVELS:
            self._write_db(entry)
        else:
            self._write_file(entry)
        # 告警候选（ERROR/CRITICAL）
        if entry["level"] in ALERT_CANDIDATE_LEVELS:
            self._maybe_alert(entry)

    # -------------------------------------------------------------------
    # 写 DB
    # -------------------------------------------------------------------
    def _write_db(self, entry: Dict[str, Any]) -> None:
        """写一条 ErrorLog（独立 Session，避免与请求事务冲突）"""
        db = SessionLocal()
        try:
            row = ErrorLog(
                level=entry["level"],
                error_type=entry["error_type"],
                source=entry["source"] or None,
                message=entry["message"],
                stack_trace=entry["stack_trace"] or None,
                request_path=entry["request_path"] or None,
                request_params=entry["request_params"] or None,
                user_id=entry["user_id"] or None,
                env_info=entry["env_info"] or None,
            )
            db.add(row)
            db.commit()
        except Exception as e:
            try:
                db.rollback()
            except Exception:
                pass
            print(f"[logging] DB write failed: {e}; fallback to file", flush=True)
            # DB 失败 → 兜底写文件
            try:
                self._write_file(entry)
            except Exception:
                pass
        finally:
            try:
                db.close()
            except Exception:
                pass

    # -------------------------------------------------------------------
    # 写文件（按天归档 jsonl）
    # -------------------------------------------------------------------
    @staticmethod
    def _today_str() -> str:
        # 文件名按"当地日期"切分（便于运维按天排查）；UTC 时间存
        return datetime.now().strftime("%Y-%m-%d")

    def _write_file(self, entry: Dict[str, Any]) -> None:
        path = os.path.join(_LOG_DIR, f"{self._today_str()}.jsonl")
        line = json.dumps(entry, ensure_ascii=False)
        # append 模式；flush 以保证 tail -f 可立即看到
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            try:
                f.flush()
            except Exception:
                pass

    # -------------------------------------------------------------------
    # 告警（异步触发；不阻塞 worker）
    # -------------------------------------------------------------------
    def _maybe_alert(self, entry: Dict[str, Any]) -> None:
        # 读取告警配置（不传则禁用）
        try:
            cfg = self._load_alert_config()
        except Exception:
            return
        if not cfg or not cfg.get("enabled"):
            return
        min_level = (cfg.get("min_level") or "CRITICAL").upper()
        if LEVEL_ORDER.index(entry["level"]) < LEVEL_ORDER.index(min_level):
            return
        # 节流：同 (message, source) 在 throttle_sec 内不重复
        throttle = int(cfg.get("throttle_sec") or 300)
        sig = f"{entry['message']}|{entry['source']}"
        now = time.time()
        last = self._alert_last_sent.get(sig, 0)
        if now - last < throttle:
            return
        self._alert_last_sent[sig] = now
        # 异步发送（不阻塞 worker）
        channels = cfg.get("channels") or []
        t = threading.Thread(
            target=self._dispatch_alert,
            args=(entry, channels),
            daemon=True,
        )
        t.start()

    # 进程内节流字典（按 message+source 聚合）
    _alert_last_sent: Dict[str, float] = {}

    @staticmethod
    def _load_alert_config() -> Optional[Dict[str, Any]]:
        db = SessionLocal()
        try:
            row = db.query(AlertConfig).filter(AlertConfig.id == 1).first()
            if not row:
                return None
            channels = []
            if row.channels:
                try:
                    channels = json.loads(row.channels)
                except Exception:
                    channels = []
            return {
                "enabled": bool(row.enabled),
                "min_level": row.min_level or "CRITICAL",
                "channels": channels,
                "throttle_sec": int(row.throttle_sec or 300),
            }
        except Exception:
            return None
        finally:
            try:
                db.close()
            except Exception:
                pass

    def _dispatch_alert(self, entry: Dict[str, Any], channels: List[Dict[str, Any]]) -> None:
        """调用 notifiers 实际发送（失败仅 stderr）"""
        try:
            # 延迟导入避免循环依赖
            from backend.services.notifiers import dispatch
            dispatch(entry, channels)
        except Exception as e:
            print(f"[logging] alert dispatch failed: {e}", flush=True)

    # -------------------------------------------------------------------
    # 公共查询方法
    # -------------------------------------------------------------------
    def stats(self) -> Dict[str, int]:
        """快速健康检查：队列长度 / ring 长度 / 丢弃计数 / worker 是否存活"""
        return {
            "queue_size": self._queue.qsize(),
            "ring_size": len(self._ring),
            "dropped": self._dropped,
            "worker_alive": self._worker.is_alive(),
        }

    def shutdown(self, timeout: float = 2.0) -> None:
        """优雅关闭：通知 worker 退出"""
        self._stop.set()
        try:
            self._worker.join(timeout=timeout)
        except Exception:
            pass
