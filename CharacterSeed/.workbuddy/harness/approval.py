#!/usr/bin/env python3
"""
AI 开发 Harness — 核心逻辑修改审批工作流
=============================================
对 core_lock.json 中标记的锁定模块，任何 AI 修改必须走完整审批流程：

  申请 (request) → 审核 (review) → 批准/拒绝 (approve/reject) → 执行 (execute)

支持紧急通道（emergency）：临时授权 24 小时内有效，事后必须补审批。

审批记录持久化到 .workbuddy/harness/approvals.jsonl，每条记录有唯一 ID。
未获批准的修改会被 pre_check.py 阻断。

用法：
  # AI 发起申请
  python approval.py request --lock-id LOCK-003 --purpose "修复 Jiwen tick 死循环" \\
      --files backend/jiwen/jiwen_core.py --changeset "描述具体修改内容"

  # 人工审核（批准或拒绝）
  python approval.py approve --id APP-20260629_223000_abc123 --reviewer 张三 --note "已确认安全"
  python approval.py reject  --id APP-20260629_223000_abc123 --reviewer 张三 --note "缺少测试覆盖"

  # 紧急通道
  python approval.py emergency --lock-id LOCK-003 --purpose "生产事故修复" \\
      --reviewer 值班工程师 --ttl-hours 24

  # 查询待审申请
  python approval.py pending
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from change_logger import HARNESS_DIR, log_incident  # noqa: E402

APPROVALS_FILE = HARNESS_DIR / "approvals.jsonl"
HARNESS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class Approval:
    id: str
    lock_id: str  # core_lock 中的 LOCK-XXX
    purpose: str
    files: list[str]
    changeset_description: str
    requested_by: str
    requested_at: str
    status: str  # pending / approved / rejected / expired / emergency
    reviewer: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None
    expires_at: str | None = None  # 紧急通道有效期
    executed_at: str | None = None  # 实际执行修改的时间

    def to_dict(self) -> dict:
        return asdict(self)


def _load_core_lock() -> dict:
    path = HARNESS_DIR / "core_lock.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_all() -> list[dict]:
    if not APPROVALS_FILE.exists():
        return []
    out = []
    with open(APPROVALS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _append(record: dict) -> None:
    with open(APPROVALS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _gen_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    rand = secrets.token_hex(4)
    return f"APP-{ts}_{rand}"


def _find_lock(lock_id: str) -> dict | None:
    cl = _load_core_lock()
    for lock in cl.get("locked_modules", []):
        if lock.get("id") == lock_id:
            return lock
    return None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def request(lock_id: str, purpose: str, files: list[str],
            changeset_description: str, requested_by: str = "AI") -> Approval:
    """AI 发起审批申请。"""
    lock = _find_lock(lock_id)
    if not lock:
        raise ValueError(f"未知的 lock_id: {lock_id}（请检查 core_lock.json）")

    approval = Approval(
        id=_gen_id(),
        lock_id=lock_id,
        purpose=purpose,
        files=files,
        changeset_description=changeset_description,
        requested_by=requested_by,
        requested_at=datetime.now(timezone.utc).isoformat(),
        status="pending",
    )
    _append(approval.to_dict())
    log_incident("approval_requested", {"approval_id": approval.id, "lock_id": lock_id, "purpose": purpose})
    return approval


def approve(approval_id: str, reviewer: str, note: str = "") -> Approval:
    """人工批准申请。"""
    records = _load_all()
    target = None
    for r in records:
        if r["id"] == approval_id and r["status"] == "pending":
            target = r
            break
    if not target:
        raise ValueError(f"找不到待批准的申请: {approval_id}")

    target["status"] = "approved"
    target["reviewer"] = reviewer
    target["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    target["review_note"] = note

    # 重写整个文件（审批记录数量不会很多）
    _rewrite(records)
    log_incident("approval_approved", {"approval_id": approval_id, "reviewer": reviewer})
    return Approval(**target)


def reject(approval_id: str, reviewer: str, note: str = "") -> Approval:
    """人工拒绝申请。"""
    records = _load_all()
    target = None
    for r in records:
        if r["id"] == approval_id and r["status"] == "pending":
            target = r
            break
    if not target:
        raise ValueError(f"找不到待拒绝的申请: {approval_id}")

    target["status"] = "rejected"
    target["reviewer"] = reviewer
    target["reviewed_at"] = datetime.now(timezone.utc).isoformat()
    target["review_note"] = note

    _rewrite(records)
    log_incident("approval_rejected", {"approval_id": approval_id, "reviewer": reviewer, "note": note})
    return Approval(**target)


def emergency(lock_id: str, purpose: str, files: list[str], reviewer: str,
              ttl_hours: int = 24, note: str = "") -> Approval:
    """
    紧急通道：值班工程师临时授权，TTL 内有效。事后必须补审批。
    """
    approval = Approval(
        id=_gen_id(),
        lock_id=lock_id,
        purpose=f"[EMERGENCY] {purpose}",
        files=files,
        changeset_description=f"紧急授权，TTL {ttl_hours}h。事后补审批。Note: {note}",
        requested_by=reviewer,  # 紧急通道由人工主动授权
        requested_at=datetime.now(timezone.utc).isoformat(),
        status="emergency",
        reviewer=reviewer,
        reviewed_at=datetime.now(timezone.utc).isoformat(),
        review_note=note,
        expires_at=(datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat(),
    )
    _append(approval.to_dict())
    log_incident("approval_emergency", {
        "approval_id": approval.id,
        "lock_id": lock_id,
        "reviewer": reviewer,
        "ttl_hours": ttl_hours,
        "expires_at": approval.expires_at,
    })
    return approval


def check_valid(lock_id: str, files: list[str]) -> dict | None:
    """
    检查指定 lock_id 和 files 是否有有效审批。
    返回 Approval 字典或 None。

    被 pre_check.py 调用，用于判断核心锁定修改是否获授权。
    """
    records = _load_all()
    now = datetime.now(timezone.utc)
    for r in records:
        if r["lock_id"] != lock_id:
            continue
        if r["status"] not in ("approved", "emergency"):
            continue
        # 检查文件覆盖
        approved_files = set(r.get("files", []))
        requested_files = set(files)
        if not requested_files.issubset(approved_files):
            continue
        # 检查紧急通道是否过期
        if r["status"] == "emergency" and r.get("expires_at"):
            exp = datetime.fromisoformat(r["expires_at"])
            if now > exp:
                continue
        return r
    return None


def list_pending() -> list[dict]:
    """列出所有待审申请。"""
    return [r for r in _load_all() if r["status"] == "pending"]


def list_recent(limit: int = 20) -> list[dict]:
    """列出最近 N 条审批记录。"""
    return _load_all()[-limit:]


def _rewrite(records: list[dict]) -> None:
    """重写整个 approvals.jsonl。"""
    tmp = APPROVALS_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(APPROVALS_FILE)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AI 开发 Harness — 核心逻辑审批")
    sub = parser.add_subparsers(dest="command", required=True)

    # request
    p_req = sub.add_parser("request", help="AI 发起审批申请")
    p_req.add_argument("--lock-id", required=True)
    p_req.add_argument("--purpose", required=True)
    p_req.add_argument("--files", nargs="+", required=True)
    p_req.add_argument("--changeset", required=True, help="具体修改内容描述")
    p_req.add_argument("--requested-by", default="AI")

    # approve
    p_app = sub.add_parser("approve", help="人工批准")
    p_app.add_argument("--id", required=True, dest="approval_id")
    p_app.add_argument("--reviewer", required=True)
    p_app.add_argument("--note", default="")

    # reject
    p_rej = sub.add_parser("reject", help="人工拒绝")
    p_rej.add_argument("--id", required=True, dest="approval_id")
    p_rej.add_argument("--reviewer", required=True)
    p_rej.add_argument("--note", default="")

    # emergency
    p_em = sub.add_parser("emergency", help="紧急通道临时授权")
    p_em.add_argument("--lock-id", required=True)
    p_em.add_argument("--purpose", required=True)
    p_em.add_argument("--files", nargs="+", required=True)
    p_em.add_argument("--reviewer", required=True)
    p_em.add_argument("--ttl-hours", type=int, default=24)
    p_em.add_argument("--note", default="")

    # pending
    sub.add_parser("pending", help="列出待审申请")

    # list
    p_list = sub.add_parser("list", help="列出最近审批记录")
    p_list.add_argument("--limit", type=int, default=20)

    args = parser.parse_args()

    if args.command == "request":
        a = request(args.lock_id, args.purpose, args.files, args.changeset, args.requested_by)
        print(json.dumps(a.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "approve":
        a = approve(args.approval_id, args.reviewer, args.note)
        print(json.dumps(a.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "reject":
        a = reject(args.approval_id, args.reviewer, args.note)
        print(json.dumps(a.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "emergency":
        a = emergency(args.lock_id, args.purpose, args.files, args.reviewer, args.ttl_hours, args.note)
        print(json.dumps(a.to_dict(), ensure_ascii=False, indent=2))
    elif args.command == "pending":
        print(json.dumps(list_pending(), ensure_ascii=False, indent=2))
    elif args.command == "list":
        print(json.dumps(list_recent(args.limit), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
