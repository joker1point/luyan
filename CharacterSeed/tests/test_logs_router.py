"""
test_logs_router — 日志系统对外 REST API 契约测试。

覆盖：
  POST /api/logs                   同步上报（直接写库）
  POST /api/logs/report            异步上报（入队）
  GET  /api/logs                   列表 + 多维筛选 + 时间范围 + 分页
  GET  /api/logs/{id}              单条详情 + 404
  GET  /api/logs/stats             聚合统计（by_level / by_type / trend）
  GET  /api/logs/health            健康检查
  GET  /api/logs/alert-config      读取（首次自动建默认）
  PUT  /api/logs/alert-config      更新
  POST /api/logs/alert-config/test 测试告警（缺渠道 → 400）

设计：
  - 直接操作 ErrorLog / AlertConfig 表预置数据，验证路由 + 序列化 + 过滤
  - 异步 report 仅验证 ok=True（不等待 worker 落库，避免线程生命周期问题）
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone, timedelta

import pytest

from backend.models import ErrorLog, AlertConfig


# ============================================================
# 同步上报
# ============================================================
def test_create_log_sync(client, db):
    r = client.post(
        "/api/logs",
        json={
            "level": "ERROR",
            "error_type": "frontend",
            "source": "ChatPage:onSend",
            "message": "boom",
            "stack_trace": "Traceback (most recent call last): ...",
            "request_path": "/api/chat",
            "user_id": "user-1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == "ERROR"
    assert body["error_type"] == "frontend"
    assert body["source"] == "ChatPage:onSend"
    assert body["message"] == "boom"
    assert body["user_id"] == "user-1"
    assert "created_at" in body
    assert body["id"] > 0

    # 真的落库了
    row = db.query(ErrorLog).filter(ErrorLog.id == body["id"]).first()
    assert row is not None
    assert row.message == "boom"


def test_create_log_default_level_and_type(client):
    r = client.post(
        "/api/logs",
        json={"message": "missing level/type"},
    )
    assert r.status_code == 200
    body = r.json()
    # level 强制大写、error_type 强制小写
    assert body["level"] == "ERROR"
    assert body["error_type"] == "backend"


def test_create_log_missing_message(client):
    """message 是必填字段 → 422。"""
    r = client.post("/api/logs", json={"source": "x"})
    assert r.status_code == 422


# ============================================================
# 异步上报（仅验证入队，不等 worker 落库）
# ============================================================
def test_report_log_async_enqueue(client):
    r = client.post(
        "/api/logs/report",
        json={
            "level": "WARNING",
            "error_type": "backend",
            "source": "test",
            "message": "async log",
        },
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ============================================================
# 列表
# ============================================================
def _seed_logs(db, n=3, level="ERROR", error_type="backend", source=None, message_prefix="log"):
    """往 ErrorLog 灌 n 条同 level/type 的记录。"""
    rows = []
    for i in range(n):
        row = ErrorLog(
            level=level, error_type=error_type,
            source=source or f"src-{i}",
            message=f"{message_prefix}-{i}",
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def test_list_logs_default_time_range(client, db):
    _seed_logs(db, n=2, message_prefix="recent")
    # 1 条 7 天前的（应该被默认 24h 范围过滤掉）
    old = ErrorLog(
        level="ERROR", error_type="backend",
        source="old", message="very old",
        created_at=datetime.now(timezone.utc) - timedelta(days=7),
    )
    db.add(old)
    db.commit()

    r = client.get("/api/logs")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    # 默认 limit=50, offset=0
    assert body["limit"] == 50
    assert body["offset"] == 0


def test_list_logs_filter_by_level(client, db):
    _seed_logs(db, n=2, level="ERROR", message_prefix="err")
    _seed_logs(db, n=1, level="CRITICAL", message_prefix="crit")
    db.commit()

    r = client.get("/api/logs?level=CRITICAL")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["level"] == "CRITICAL"


def test_list_logs_filter_by_multiple_levels(client, db):
    _seed_logs(db, n=1, level="ERROR", message_prefix="e")
    _seed_logs(db, n=1, level="CRITICAL", message_prefix="c")
    _seed_logs(db, n=1, level="WARNING", message_prefix="w")
    db.commit()

    r = client.get("/api/logs?level=ERROR,CRITICAL")
    body = r.json()
    assert body["total"] == 2
    levels = {item["level"] for item in body["items"]}
    assert levels == {"ERROR", "CRITICAL"}


def test_list_logs_filter_by_type(client, db):
    _seed_logs(db, n=1, error_type="frontend", message_prefix="f")
    _seed_logs(db, n=1, error_type="backend", message_prefix="b")
    db.commit()

    r = client.get("/api/logs?error_type=frontend")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["error_type"] == "frontend"


def test_list_logs_filter_by_source_like(client, db):
    _seed_logs(db, n=1, source="ChatPage:onSend", message_prefix="cp")
    _seed_logs(db, n=1, source="SettingsPage:save", message_prefix="sp")
    db.commit()

    r = client.get("/api/logs?source=ChatPage")
    body = r.json()
    assert body["total"] == 1


def test_list_logs_filter_by_user_id(client, db):
    _seed_logs(db, n=1)
    row = ErrorLog(
        level="ERROR", error_type="backend", source="x",
        message="user-specific", user_id="alice",
    )
    db.add(row)
    db.commit()

    r = client.get("/api/logs?user_id=alice")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["user_id"] == "alice"


def test_list_logs_search_message(client, db):
    _seed_logs(db, n=1, message_prefix="connection refused")
    _seed_logs(db, n=1, message_prefix="timeout")
    db.commit()

    r = client.get("/api/logs?q=connection")
    body = r.json()
    assert body["total"] == 1
    assert "connection" in body["items"][0]["message"]


def test_list_logs_custom_time_range(client, db):
    # 30 天前的一条
    old = ErrorLog(
        level="ERROR", error_type="backend", source="old", message="old",
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    db.add(old)
    db.commit()
    # 1 小时前的一条
    recent = ErrorLog(
        level="ERROR", error_type="backend", source="recent", message="recent",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    db.add(recent)
    db.commit()

    start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    r = client.get(f"/api/logs?start={start}")
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "recent"


def test_list_logs_pagination(client, db):
    _seed_logs(db, n=10)
    r = client.get("/api/logs?limit=3&offset=2")
    body = r.json()
    assert body["total"] == 10
    assert len(body["items"]) == 3
    assert body["limit"] == 3
    assert body["offset"] == 2


def test_list_logs_limit_too_large(client):
    r = client.get("/api/logs?limit=999")
    # Pydantic Query 校验：le=500
    assert r.status_code == 422


# ============================================================
# 单条详情
# ============================================================
def test_get_log_by_id(client, db):
    row = ErrorLog(
        level="CRITICAL", error_type="database", source="x", message="db boom",
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    r = client.get(f"/api/logs/{row.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == row.id
    assert body["level"] == "CRITICAL"
    assert body["error_type"] == "database"
    assert body["message"] == "db boom"


def test_get_log_not_found(client):
    r = client.get("/api/logs/9999")
    assert r.status_code == 404
    assert "9999" in r.json()["detail"]


# ============================================================
# 聚合统计
# ============================================================
def test_stats_empty(client):
    r = client.get("/api/logs/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["by_level"] == []
    assert body["by_type"] == []
    assert body["trend"] == []
    assert body["range_start"] != ""
    assert body["range_end"] != ""


def test_stats_with_logs(client, db):
    _seed_logs(db, n=2, level="ERROR", error_type="backend", message_prefix="e")
    _seed_logs(db, n=1, level="CRITICAL", error_type="frontend", message_prefix="c")
    db.commit()

    r = client.get("/api/logs/stats")
    body = r.json()
    assert body["total"] == 3
    # by_level 至少 2 项
    by_level = {x["level"]: x["count"] for x in body["by_level"]}
    assert by_level.get("ERROR") == 2
    assert by_level.get("CRITICAL") == 1
    # by_type 至少 2 项
    by_type = {x["error_type"]: x["count"] for x in body["by_type"]}
    assert by_type.get("backend") == 2
    assert by_type.get("frontend") == 1
    # trend 至少 1 个 bucket
    assert len(body["trend"]) >= 1
    bucket = body["trend"][0]
    assert "bucket" in bucket
    assert "count" in bucket
    assert "by_level" in bucket


def test_stats_custom_bucket(client, db):
    _seed_logs(db, n=3)
    r = client.get("/api/logs/stats?bucket_minutes=5")
    assert r.status_code == 200
    # 5min 桶可能 1 个或多个，全在范围内就行
    for b in r.json()["trend"]:
        assert b["count"] > 0


def test_stats_custom_time_range(client, db):
    _seed_logs(db, n=2, message_prefix="recent")
    old = ErrorLog(
        level="ERROR", error_type="backend", source="old", message="old",
        created_at=datetime.now(timezone.utc) - timedelta(days=10),
    )
    db.add(old)
    db.commit()

    start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    r = client.get(f"/api/logs/stats?start={start}")
    assert r.json()["total"] == 2


# ============================================================
# 健康
# ============================================================
def test_health(client):
    r = client.get("/api/logs/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "service" in body
    assert "queue_size" in body["service"]


# ============================================================
# 告警配置
# ============================================================
def test_alert_config_default_on_first_read(client, db):
    """首次访问自动建默认行（id=1）。"""
    assert db.query(AlertConfig).count() == 0
    r = client.get("/api/logs/alert-config")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False
    assert body["min_level"] == "CRITICAL"
    assert body["throttle_sec"] == 300
    # default channels: [{"type": "console"}]
    assert any(c.get("type") == "console" for c in body["channels"])
    # 真的建出来了
    assert db.query(AlertConfig).count() == 1


def test_alert_config_update_creates_when_missing(client, db):
    """PUT 在没有行时也能新建。"""
    r = client.put(
        "/api/logs/alert-config",
        json={
            "enabled": True,
            "min_level": "ERROR",
            "channels": [{"type": "webhook", "url": "https://example.com/hook"}],
            "throttle_sec": 60,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body["min_level"] == "ERROR"
    assert body["throttle_sec"] == 60
    assert body["channels"][0]["type"] == "webhook"


def test_alert_config_update_modifies_existing(client, db):
    # 先 PUT 建一行
    client.put(
        "/api/logs/alert-config",
        json={"enabled": True, "min_level": "ERROR", "channels": [], "throttle_sec": 60},
    )
    # 再 PUT 改
    r = client.put(
        "/api/logs/alert-config",
        json={
            "enabled": False, "min_level": "CRITICAL",
            "channels": [{"type": "email", "to": "a@x.com"}],
            "throttle_sec": 600,
        },
    )
    body = r.json()
    assert body["enabled"] is False
    assert body["throttle_sec"] == 600
    assert body["channels"][0]["type"] == "email"
    assert body["channels"][0]["to"] == "a@x.com"


# ============================================================
# 测试告警
# ============================================================
def test_alert_test_no_channels_400(client, db):
    """channels 为空数组时 → 400（路由内显式拦截）。"""
    # 显式建一行 channels=[] 的配置（默认 GET 会注入 console 渠道，要绕开）
    db.add(AlertConfig(
        id=1, enabled=1, min_level="CRITICAL",
        channels=json.dumps([], ensure_ascii=False),
        throttle_sec=300,
    ))
    db.commit()

    r = client.post(
        "/api/logs/alert-config/test",
        json={"level": "CRITICAL", "message": "test"},
    )
    assert r.status_code == 400
    assert "渠道" in r.json()["detail"]


def test_alert_test_console_channel_ok(client, db):
    """default 渠道含 console，测试告警应 ok。"""
    # 触发一次 GET 初始化默认配置
    client.get("/api/logs/alert-config")
    r = client.post(
        "/api/logs/alert-config/test",
        json={"level": "CRITICAL", "message": "smoke test"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["sent"] >= 1


# ============================================================
# 归档文件列表
# ============================================================
def test_files_list_empty_dir(client, tmp_path, monkeypatch):
    """归档目录不存在或为空 → 正常返回空列表（不报错）。"""
    from backend.services import logging_service
    monkeypatch.setattr(logging_service, "_LOG_DIR", str(tmp_path / "logs"))

    r = client.get("/api/logs/files/list")
    assert r.status_code == 200
    body = r.json()
    assert "dir" in body
    assert body["files"] == []


def test_files_list_with_jsonl(client, tmp_path, monkeypatch):
    from backend.services import logging_service
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "2026-06-26.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (log_dir / "2026-06-25.jsonl").write_text('{"b": 2}\n{"c": 3}\n', encoding="utf-8")
    # 非 jsonl 文件应被忽略
    (log_dir / "README.md").write_text("hi", encoding="utf-8")
    monkeypatch.setattr(logging_service, "_LOG_DIR", str(log_dir))

    r = client.get("/api/logs/files/list")
    body = r.json()
    names = [f["name"] for f in body["files"]]
    assert "2026-06-26.jsonl" in names
    assert "2026-06-25.jsonl" in names
    assert "README.md" not in names
    # 每条都有 size/mtime
    for f in body["files"]:
        assert f["size"] > 0
        assert f["mtime"] > 0
