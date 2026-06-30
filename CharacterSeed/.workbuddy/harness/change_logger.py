#!/usr/bin/env python3
"""
AI 开发 Harness — 变更日志系统
=================================
功能：
  1. 记录每次 AI 操作的元数据（时间、目的、影响文件）
  2. 使用 git diff 捕获每次变更的完整代码差异
  3. 支持变更追溯与回滚（通过 git commit hash）
  4. 维护操作审计链（不可篡改 — 每条记录包含 prev_hash 形成哈希链）
  5. 操作前后双快照（before/after commit hash + before/after diff）
  6. 影响范围评估（关联受保护 feature / core_lock / pitfall）

用法：
  # 受控模式（推荐）：begin → AI 修改 → end
  from change_logger import operation
  with operation(purpose="修复 Jiwen 阈值计算错误", files=["backend/jiwen/jiwen_core.py"]):
      ... # AI 修改代码

  # CLI 模式
  python change_logger.py record --purpose "..." --files a.py b.py
  python change_logger.py rollback --to HASH
  python change_logger.py history --limit 20
  python change_logger.py verify-chain     # 校验哈希链完整性

日志存储：
  .workbuddy/harness/logs/change_log.jsonl     — 结构化操作日志（含哈希链）
  .workbuddy/harness/logs/diffs/                — 每次变更的 before/after diff 快照
  .workbuddy/harness/logs/incidents.jsonl       — harness 自身事件（如回滚、阻断）
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
HARNESS_DIR = PROJECT_ROOT / ".workbuddy" / "harness"
LOG_DIR = HARNESS_DIR / "logs"
DIFF_DIR = LOG_DIR / "diffs"
CHANGE_LOG_FILE = LOG_DIR / "change_log.jsonl"
INCIDENT_LOG_FILE = LOG_DIR / "incidents.jsonl"


def ensure_dirs():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DIFF_DIR.mkdir(parents=True, exist_ok=True)


def _git(cmd: list[str]) -> subprocess.CompletedProcess:
    """在项目根目录执行 git 命令。"""
    return subprocess.run(
        ["git"] + cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
    )


def _get_current_hash() -> str:
    """获取当前 HEAD 的 commit hash（短格式）。"""
    r = _git(["rev-parse", "--short", "HEAD"])
    return r.stdout.strip() if r.returncode == 0 else "NO_GIT"


def _get_changed_files() -> list[str]:
    """获取当前工作区中已暂存 + 未暂存的变更文件列表。"""
    r = _git(["diff", "--name-only", "HEAD"])
    if r.returncode != 0:
        return []
    files = r.stdout.strip().split("\n") if r.stdout.strip() else []
    # 也包括 untracked files
    r2 = _git(["ls-files", "--others", "--exclude-standard"])
    if r2.returncode == 0 and r2.stdout.strip():
        files.extend(r2.stdout.strip().split("\n"))
    return sorted(set(f for f in files if f))


def _save_diff(timestamp_str: str) -> Optional[Path]:
    """保存当前工作区的 git diff 到文件。"""
    files = _get_changed_files()
    if not files:
        return None
    r = _git(["diff", "HEAD"])
    if r.returncode != 0:
        return None
    diff_path = DIFF_DIR / f"{timestamp_str}.diff"
    diff_path.write_text(r.stdout, encoding="utf-8")
    return diff_path


def _load_feature_registry() -> dict:
    """加载受保护功能注册表。"""
    path = HARNESS_DIR / "feature_registry.json"
    if not path.exists():
        return {"features": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_core_lock() -> dict:
    """加载核心逻辑锁定清单。"""
    path = HARNESS_DIR / "core_lock.json"
    if not path.exists():
        return {"locked_files": [], "locked_modules": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_pitfall_registry() -> dict:
    """加载重复踩坑注册表。"""
    path = HARNESS_DIR / "pitfall_registry.json"
    if not path.exists():
        return {"pitfalls": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _assess_impact(files: list[str]) -> dict:
    """
    评估变更影响范围：
      - 命中的受保护 feature ID 列表
      - 命中的核心锁定模块 ID 列表
      - 命中的 pitfall ID 列表
    """
    files_set = {f.replace("\\", "/") for f in files}

    # feature 命中
    fr = _load_feature_registry()
    hit_features = []
    for feat in fr.get("features", []):
        feat_files = {f.replace("\\", "/") for f in feat.get("files", [])}
        # 同时支持目录前缀匹配（如 backend/jiwen/ 命中 backend/jiwen/jiwen_core.py）
        for ff in feat_files:
            if ff in files_set or any(fs.startswith(ff.rstrip("/") + "/") for fs in files_set):
                hit_features.append({"id": feat["id"], "name": feat.get("name", "")})
                break

    # core_lock 命中
    cl = _load_core_lock()
    hit_locks = []
    for lock in cl.get("locked_modules", []):
        lock_files = {f.replace("\\", "/") for f in lock.get("files", [])}
        for lf in lock_files:
            if lf in files_set or any(fs.startswith(lf.rstrip("/") + "/") for fs in files_set):
                hit_locks.append({"id": lock["id"], "name": lock.get("name", "")})
                break

    # pitfall 命中
    pr = _load_pitfall_registry()
    hit_pitfalls = []
    for pit in pr.get("pitfalls", []):
        pit_files = {f.replace("\\", "/") for f in pit.get("files", [])}
        # 过滤空文件列表（全局规则）
        if not pit_files:
            continue
        for pf in pit_files:
            if pf in files_set or any(fs.startswith(pf.rstrip("/") + "/") for fs in files_set):
                hit_pitfalls.append({
                    "id": pit["id"],
                    "title": pit.get("title", ""),
                    "category": pit.get("category", ""),
                    "difficulty": pit.get("difficulty", "")
                })
                break

    return {
        "hit_features": hit_features,
        "hit_core_locks": hit_locks,
        "hit_pitfalls": hit_pitfalls,
        "total_risks": len(hit_features) + len(hit_locks) + len(hit_pitfalls),
    }


def _last_chain_hash() -> str:
    """读取日志最后一条的 chain_hash，作为新条目的 prev_hash。"""
    if not CHANGE_LOG_FILE.exists():
        return "GENESIS"
    last_line = None
    with open(CHANGE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line
    if not last_line:
        return "GENESIS"
    try:
        return json.loads(last_line).get("chain_hash", "GENESIS")
    except json.JSONDecodeError:
        return "GENESIS"


def _compute_chain_hash(entry: dict, prev_hash: str) -> str:
    """
    计算当前条目的哈希链值：SHA256(prev_hash + entry_payload)。
    排除 prev_hash 和 chain_hash 字段本身，保证写入和验证用相同的字段集。
    """
    payload_entry = {k: v for k, v in entry.items() if k not in ("prev_hash", "chain_hash")}
    payload = json.dumps(payload_entry, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{prev_hash}|{payload}".encode("utf-8")).hexdigest()


def _write_log(entry: dict) -> None:
    """追加一条日志，自动维护哈希链。"""
    ensure_dirs()
    prev_hash = _last_chain_hash()
    entry["prev_hash"] = prev_hash
    entry["chain_hash"] = _compute_chain_hash(entry, prev_hash)
    with open(CHANGE_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def verify_chain() -> dict:
    """校验整个日志链的完整性。任何篡改都会导致 chain_hash 不一致。"""
    if not CHANGE_LOG_FILE.exists():
        return {"valid": True, "entries": 0, "message": "无日志文件"}
    entries = []
    with open(CHANGE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    if not entries:
        return {"valid": True, "entries": 0, "message": "空日志"}

    prev = "GENESIS"
    broken_at = None
    for i, e in enumerate(entries):
        expected_prev = prev
        if e.get("prev_hash") != expected_prev:
            broken_at = i
            break
        recomputed = _compute_chain_hash({k: v for k, v in e.items() if k != "chain_hash" and k != "prev_hash"}, expected_prev)
        if recomputed != e.get("chain_hash"):
            broken_at = i
            break
        prev = e["chain_hash"]

    return {
        "valid": broken_at is None,
        "entries": len(entries),
        "broken_at_index": broken_at,
        "message": "链完整" if broken_at is None else f"第 {broken_at} 条记录被篡改或顺序错乱",
    }


def log_incident(incident_type: str, details: dict) -> None:
    """记录 harness 自身事件（如回滚、阻断、审批）。"""
    ensure_dirs()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": incident_type,
        "details": details,
    }
    with open(INCIDENT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def record(purpose: str, files: Optional[list[str]] = None, author: str = "AI",
           before_hash: Optional[str] = None, after_hash: Optional[str] = None,
           status: str = "completed", error: Optional[str] = None) -> dict:
    """
    记录一次变更操作（含影响范围评估 + 哈希链）。

    Args:
        purpose: 变更目的描述
        files: 手动指定的变更文件列表（为 None 时自动检测）
        author: 操作者标识
        before_hash: 操作前的 commit hash（来自 begin_operation）
        after_hash: 操作后的 commit hash（来自 end_operation）
        status: completed / failed / rolled_back
        error: 失败时的错误信息
    """
    ensure_dirs()
    timestamp = datetime.now(timezone.utc)
    timestamp_str = timestamp.strftime("%Y%m%d_%H%M%S_%f")[:-3]
    current_hash = after_hash or _get_current_hash()

    if files is None:
        files = _get_changed_files()

    diff_path = _save_diff(timestamp_str)
    impact = _assess_impact(files)

    entry = {
        "id": timestamp_str,
        "timestamp": timestamp.isoformat(),
        "author": author,
        "purpose": purpose,
        "files_changed": files,
        "file_count": len(files),
        "before_hash": before_hash,
        "after_hash": current_hash,
        "commit_hash": current_hash,  # 兼容旧字段
        "diff_snapshot": str(diff_path.relative_to(PROJECT_ROOT)) if diff_path else None,
        "status": status,
        "error": error,
        "impact": impact,
    }

    _write_log(entry)
    return entry


@contextmanager
def operation(purpose: str, files: Optional[list[str]] = None, author: str = "AI"):
    """
    受控操作上下文管理器。推荐用法：

        with operation(purpose="修复 Jiwen 阈值", files=["backend/jiwen/jiwen_core.py"]):
            # AI 修改代码
            ...

    自动捕获 before/after hash、记录变更日志、异常时记 status=failed。
    """
    ensure_dirs()
    before_hash = _get_current_hash()
    entry_id = None
    error = None
    try:
        yield {"before_hash": before_hash}
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
        log_incident("operation_failed", {"purpose": purpose, "before_hash": before_hash, "error": error})
        raise
    finally:
        # 无论成功失败都记录日志（失败时记录现场，便于回滚）
        try:
            record(
                purpose=purpose,
                files=files,
                author=author,
                before_hash=before_hash,
                after_hash=_get_current_hash(),
                status="failed" if error else "completed",
                error=error,
            )
        except Exception as log_err:
            log_incident("log_write_failed", {"purpose": purpose, "error": str(log_err)})


def rollback(target_hash: str, dry_run: bool = False, reason: str = "") -> dict:
    """
    受控回滚到指定的 commit hash。

    ⚠️ 此操作需人工确认！仅当 dry_run=True 时只显示将回滚的内容。
    实际回滚使用 git reset --hard，但会：
      1. 先记录 incident（reason 必填）
      2. 保存当前 HEAD 的 diff 快照（防止丢代码）
      3. 通过 protection_rules 的 HARD_RESET danger_pattern 豁免（本函数是受控通道）
    """
    if dry_run:
        r = _git(["log", "--oneline", f"{target_hash}..HEAD"])
        return {
            "action": "dry_run",
            "target": target_hash,
            "commits_to_revert": r.stdout.strip().split("\n") if r.stdout.strip() else [],
        }

    if not reason:
        return {"action": "rollback", "success": False, "message": "必须提供 reason 才能执行回滚"}

    # 保存回滚前的现场快照
    ensure_dirs()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    pre_rollback_diff = _save_diff(f"pre_rollback_{ts}")
    before_hash = _get_current_hash()

    r = _git(["reset", "--hard", target_hash])
    success = r.returncode == 0
    after_hash = _get_current_hash() if success else before_hash

    log_incident("rollback", {
        "reason": reason,
        "target_hash": target_hash,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "success": success,
        "pre_rollback_diff": str(pre_rollback_diff.relative_to(PROJECT_ROOT)) if pre_rollback_diff else None,
        "stderr": r.stderr.strip() if not success else None,
    })

    # 同步记录到 change_log
    record(
        purpose=f"ROLLBACK: {reason}",
        files=[],
        author="human",
        before_hash=before_hash,
        after_hash=after_hash,
        status="rolled_back" if success else "failed",
        error=r.stderr.strip() if not success else None,
    )

    return {
        "action": "rollback",
        "target": target_hash,
        "success": success,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "message": "回滚成功" if success else r.stderr.strip(),
    }


def history(limit: int = 20) -> list[dict]:
    """读取最近的变更日志。"""
    if not CHANGE_LOG_FILE.exists():
        return []
    entries = []
    with open(CHANGE_LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries[-limit:]


def stats() -> dict:
    """生成变更统计。"""
    entries = history(limit=9999)
    if not entries:
        return {"total_changes": 0}

    total_files = set()
    by_date = {}
    for e in entries:
        date_key = e["timestamp"][:10]
        by_date[date_key] = by_date.get(date_key, 0) + 1
        total_files.update(e.get("files_changed", []))

    return {
        "total_changes": len(entries),
        "total_unique_files": len(total_files),
        "changes_by_date": dict(sorted(by_date.items())),
        "first_change": entries[0]["timestamp"] if entries else None,
        "last_change": entries[-1]["timestamp"] if entries else None,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AI 开发 Harness — 变更日志系统"
    )
    sub = parser.add_subparsers(dest="command")

    # record
    p_record = sub.add_parser("record", help="记录一次变更")
    p_record.add_argument("--purpose", required=True, help="变更目的")
    p_record.add_argument("--files", nargs="*", help="变更文件（不指定则自动检测）")
    p_record.add_argument("--author", default="AI", help="操作者")

    # rollback
    p_rollback = sub.add_parser("rollback", help="回滚到指定 commit")
    p_rollback.add_argument("--to", required=True, dest="target", help="目标 commit hash")
    p_rollback.add_argument("--dry-run", action="store_true", help="仅预览，不执行")
    p_rollback.add_argument("--reason", default="", help="回滚原因（实际执行时必填）")

    # history
    p_hist = sub.add_parser("history", help="查看变更历史")
    p_hist.add_argument("--limit", type=int, default=20, help="返回条数")

    # stats
    sub.add_parser("stats", help="变更统计")

    # verify-chain
    sub.add_parser("verify-chain", help="校验日志哈希链完整性")

    args = parser.parse_args()

    if args.command == "record":
        entry = record(args.purpose, args.files, args.author)
        print(json.dumps(entry, ensure_ascii=False, indent=2))

    elif args.command == "rollback":
        result = rollback(args.target, args.dry_run, args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "history":
        entries = history(args.limit)
        print(json.dumps(entries, ensure_ascii=False, indent=2))

    elif args.command == "stats":
        s = stats()
        print(json.dumps(s, ensure_ascii=False, indent=2))

    elif args.command == "verify-chain":
        result = verify_chain()
        print(json.dumps(result, ensure_ascii=False, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
