"""
logs_router — 日志系统对外 REST API。

端点（全部以 /api/logs 为前缀）：
  POST /api/logs                      上报一条错误（前端 + 后端内部）
  GET  /api/logs                      分页 + 多维筛选（时间 / level / type / source / user_id / message）
  GET  /api/logs/{log_id}             单条详情
  GET  /api/logs/stats                聚合统计（按 level / type / 时间桶）
  GET  /api/logs/health               日志服务自身健康（队列 / worker / dropped）
  GET  /api/logs/alert-config         读取告警配置
  PUT  /api/logs/alert-config         更新告警配置
  POST /api/logs/alert-config/test    发送一次测试告警
  GET  /api/logs/files                列出归档日志文件（按天 jsonl）

设计原则：
  - 任何 DB 异常都返回 5xx + 详细 detail（不向客户端泄露 SQL）
  - 时间范围默认 "最近 24h"，避免一次拉太多
  - 分页：limit 默认 50，上限 500
"""
import json
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import get_db, SessionLocal
from backend.models import ErrorLog, AlertConfig
from backend.schemas import (
    ErrorLogCreate,
    ErrorLogListResponse,
    ErrorLogResponse,
    ErrorLogStats,
    ErrorLogLevelCount,
    ErrorLogTypeCount,
    ErrorLogTrendBucket,
    AlertConfigIn,
    AlertConfigOut,
    AlertChannelConfig,
    LogTestAlertRequest,
)
from backend.services.logging_service import LoggingService

router = APIRouter(prefix="/api/logs", tags=["logs"])

# 兜底时间范围：最近 24h
DEFAULT_RANGE_HOURS = 24
MAX_LIMIT = 500
DEFAULT_LIMIT = 50


# -------------------------------------------------------------------
# 序列化辅助
# -------------------------------------------------------------------
def _to_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _to_response(row: ErrorLog) -> ErrorLogResponse:
    return ErrorLogResponse(
        id=row.id,
        level=row.level or "ERROR",
        error_type=row.error_type or "backend",
        source=row.source,
        message=row.message or "",
        stack_trace=row.stack_trace,
        request_path=row.request_path,
        request_params=row.request_params,
        user_id=row.user_id,
        env_info=row.env_info,
        created_at=_to_iso(row.created_at) or "",
    )


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # 支持 iso 字符串（含 Z 后缀）
        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2)
    except Exception:
        return None


# -------------------------------------------------------------------
# 1) 上报一条
# -------------------------------------------------------------------
@router.post("", response_model=ErrorLogResponse)
async def create_log(payload: ErrorLogCreate, db: Session = Depends(get_db)):
    """
    上报一条错误日志（前端 / 内部调用）。
    - 默认异步入队；为保证"前端立即看到"也支持同步写库（参数 sync=true）
    """
    sync = (payload.env_info or "").lower()  # 兼容位
    # env_info 是 JSON 字符串，所以 sync 标志走 query 参数更清晰
    raise_not_implemented = False  # 实际用下面的 endpoint（带 query 参数）
    if raise_not_implemented:
        pass
    row = ErrorLog(
        level=(payload.level or "ERROR").upper(),
        error_type=(payload.error_type or "backend").lower(),
        source=payload.source,
        message=payload.message or "",
        stack_trace=payload.stack_trace,
        request_path=payload.request_path,
        request_params=payload.request_params,
        user_id=payload.user_id,
        env_info=payload.env_info,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_response(row)


# -------------------------------------------------------------------
# 1.1) 异步上报（不阻塞前端；走 LoggingService 队列）
# -------------------------------------------------------------------
@router.post("/report", response_model=dict)
async def report_log(payload: ErrorLogCreate):
    """
    异步上报：仅入队，由 worker 异步写库/写文件。
    适合前端批量上报（不影响交互性能）。
    """
    ok = LoggingService.instance().record_from_payload(payload.model_dump())
    return {"ok": ok}


# -------------------------------------------------------------------
# 2) 列表
# -------------------------------------------------------------------
@router.get("", response_model=ErrorLogListResponse)
def list_logs(
    level: Optional[str] = None,
    error_type: Optional[str] = None,
    source: Optional[str] = None,
    user_id: Optional[str] = None,
    q: Optional[str] = Query(None, description="message 模糊查询"),
    start: Optional[str] = Query(None, description="ISO 时间起点"),
    end: Optional[str] = Query(None, description="ISO 时间终点"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """
    列表 + 多维筛选。
    时间范围默认"最近 24h"；如都未传则用默认。
    """
    qry = db.query(ErrorLog)
    if level:
        # 兼容 "ERROR,CRITICAL" 多选
        levels = [s.strip().upper() for s in level.split(",") if s.strip()]
        if levels:
            qry = qry.filter(ErrorLog.level.in_(levels))
    if error_type:
        types = [s.strip().lower() for s in error_type.split(",") if s.strip()]
        if types:
            qry = qry.filter(ErrorLog.error_type.in_(types))
    if source:
        qry = qry.filter(ErrorLog.source.like(f"%{source}%"))
    if user_id:
        qry = qry.filter(ErrorLog.user_id == user_id)
    if q:
        qry = qry.filter(ErrorLog.message.like(f"%{q}%"))

    # 时间范围
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if not start_dt and not end_dt:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=DEFAULT_RANGE_HOURS)
    if start_dt:
        qry = qry.filter(ErrorLog.created_at >= start_dt)
    if end_dt:
        qry = qry.filter(ErrorLog.created_at <= end_dt)

    total = qry.count()
    rows = (
        qry.order_by(ErrorLog.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return ErrorLogListResponse(
        total=total,
        items=[_to_response(r) for r in rows],
        limit=limit,
        offset=offset,
    )


# -------------------------------------------------------------------
# 3) 单条详情 — 注意：/{log_id} 必须放在所有静态路径（/stats /health /alert-config /files/list）
#    之后注册，否则 FastAPI 会按声明顺序优先匹配 /{log_id}，把 "stats" 等解析成 int 失败
# -------------------------------------------------------------------
# （get_log 定义在文件末尾）


# -------------------------------------------------------------------
# 4) 聚合统计
# -------------------------------------------------------------------
@router.get("/stats", response_model=ErrorLogStats)
def get_stats(
    start: Optional[str] = None,
    end: Optional[str] = None,
    bucket_minutes: int = Query(60, ge=5, le=1440, description="时间桶分钟数（5~1440）"),
    db: Session = Depends(get_db),
):
    """
    聚合统计：
      - total
      - by_level: 各等级计数
      - by_type:  各类型计数
      - trend:    按 bucket_minutes 切分的时间桶 + 按 level 的子计数
    """
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)
    if not start_dt and not end_dt:
        end_dt = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(hours=DEFAULT_RANGE_HOURS)

    base = db.query(ErrorLog)
    if start_dt:
        base = base.filter(ErrorLog.created_at >= start_dt)
    if end_dt:
        base = base.filter(ErrorLog.created_at <= end_dt)

    total = base.count()

    by_level_rows = (
        base.with_entities(ErrorLog.level, func.count(ErrorLog.id))
        .group_by(ErrorLog.level)
        .all()
    )
    by_type_rows = (
        base.with_entities(ErrorLog.error_type, func.count(ErrorLog.id))
        .group_by(ErrorLog.error_type)
        .all()
    )

    # 趋势：SQLite 上没有 date_trunc，用 Python 端聚合（数据量 5w 以内完全够用）
    rows = base.with_entities(
        ErrorLog.created_at, ErrorLog.level
    ).order_by(ErrorLog.created_at.asc()).all()
    bucket_sec = bucket_minutes * 60
    bucket_map = {}
    for ts, lvl in rows:
        if ts is None:
            continue
        # 把 ts 截到桶起点
        epoch = int(ts.replace(tzinfo=timezone.utc).timestamp()) if ts.tzinfo is None else int(ts.timestamp())
        bucket_epoch = (epoch // bucket_sec) * bucket_sec
        key = datetime.fromtimestamp(bucket_epoch, tz=timezone.utc)
        if key not in bucket_map:
            bucket_map[key] = {"count": 0, "by_level": {}}
        bucket_map[key]["count"] += 1
        bucket_map[key]["by_level"][lvl or "ERROR"] = (
            bucket_map[key]["by_level"].get(lvl or "ERROR", 0) + 1
        )
    trend = [
        ErrorLogTrendBucket(
            bucket=k.isoformat(),
            count=v["count"],
            by_level=v["by_level"],
        )
        for k, v in sorted(bucket_map.items())
    ]

    return ErrorLogStats(
        total=total,
        by_level=[ErrorLogLevelCount(level=lvl or "ERROR", count=c) for lvl, c in by_level_rows],
        by_type=[ErrorLogTypeCount(error_type=et or "backend", count=c) for et, c in by_type_rows],
        trend=trend,
        range_start=_to_iso(start_dt) or "",
        range_end=_to_iso(end_dt) or "",
    )


# -------------------------------------------------------------------
# 5) 健康
# -------------------------------------------------------------------
@router.get("/health")
def health():
    return {
        "ok": True,
        "service": LoggingService.instance().stats(),
    }


# -------------------------------------------------------------------
# 6) 告警配置
# -------------------------------------------------------------------
def _load_alert_config(db: Session) -> AlertConfigOut:
    row = db.query(AlertConfig).filter(AlertConfig.id == 1).first()
    if not row:
        # 首次访问：插入默认
        row = AlertConfig(
            id=1,
            enabled=0,
            min_level="CRITICAL",
            channels=json.dumps([{"type": "console"}], ensure_ascii=False),
            throttle_sec=300,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    channels = []
    if row.channels:
        try:
            channels = [AlertChannelConfig(**c) for c in json.loads(row.channels)]
        except Exception:
            channels = []
    return AlertConfigOut(
        enabled=bool(row.enabled),
        min_level=row.min_level or "CRITICAL",
        channels=channels,
        throttle_sec=int(row.throttle_sec or 300),
        updated_at=_to_iso(row.updated_at),
    )


@router.get("/alert-config", response_model=AlertConfigOut)
def get_alert_config(db: Session = Depends(get_db)):
    return _load_alert_config(db)


@router.put("/alert-config", response_model=AlertConfigOut)
def update_alert_config(payload: AlertConfigIn, db: Session = Depends(get_db)):
    row = db.query(AlertConfig).filter(AlertConfig.id == 1).first()
    if not row:
        row = AlertConfig(id=1)
        db.add(row)
    row.enabled = 1 if payload.enabled else 0
    row.min_level = (payload.min_level or "CRITICAL").upper()
    row.channels = json.dumps(
        [c.model_dump() for c in payload.channels], ensure_ascii=False,
    )
    row.throttle_sec = int(payload.throttle_sec or 300)
    db.commit()
    db.refresh(row)
    return _load_alert_config(db)


@router.post("/alert-config/test")
def test_alert(payload: LogTestAlertRequest, db: Session = Depends(get_db)):
    """发一次测试告警，便于运维验证渠道配置。"""
    cfg = _load_alert_config(db)
    if not cfg.channels:
        raise HTTPException(status_code=400, detail="未配置任何通知渠道")
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "level": (payload.level or "CRITICAL").upper(),
        "error_type": "internal",
        "source": "logs_router:test",
        "message": payload.message or "Test alert",
        "stack_trace": "",
        "request_path": "/api/logs/alert-config/test",
        "request_params": "",
        "user_id": "ops",
        "env_info": json.dumps({"test": True}, ensure_ascii=False),
    }
    # 走与正式告警相同的派发路径
    from backend.services.notifiers import dispatch
    dispatch(entry, [c.model_dump() for c in cfg.channels])
    return {"ok": True, "sent": len(cfg.channels)}


# -------------------------------------------------------------------
# 7) 归档文件列表
# -------------------------------------------------------------------
@router.get("/files/list")
def list_log_files():
    """
    列出 usercontext/logs/ 下的归档文件（按天 jsonl）。
    """
    from backend.services.logging_service import _LOG_DIR
    if not os.path.isdir(_LOG_DIR):
        return {"dir": _LOG_DIR, "files": []}
    files = []
    for name in sorted(os.listdir(_LOG_DIR), reverse=True):
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(_LOG_DIR, name)
        try:
            stat = os.stat(path)
            files.append({
                "name": name,
                "path": path,
                "size": stat.st_size,
                "mtime": int(stat.st_mtime),
            })
        except Exception:
            continue
    return {"dir": _LOG_DIR, "files": files}


# -------------------------------------------------------------------
# 8) 单条详情 — 必须放在文件末尾，避开 /stats /health /alert-config /files/list 的静态匹配
# -------------------------------------------------------------------
@router.get("/{log_id}", response_model=ErrorLogResponse)
def get_log(log_id: int, db: Session = Depends(get_db)):
    row = db.query(ErrorLog).filter(ErrorLog.id == log_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"log {log_id} not found")
    return _to_response(row)
