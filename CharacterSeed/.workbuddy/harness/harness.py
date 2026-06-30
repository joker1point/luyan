#!/usr/bin/env python3
"""
AI 开发 Harness — 统一编排入口
=====================================
单文件 CLI，串联 pre_check → change_logger → regression_runner → approval，
形成完整闭环。AI 在开发过程中只需调用本文件。

工作流：
  1. harness.py pre-check --purpose "..." --files a.py b.py
     → AI 修改代码前预检四道闸门
  2. harness.py run --purpose "..." --files a.py b.py -- <command>
     → 受控执行命令（包 begin/end + 自动记录 + 自动回归）
  3. harness.py post-verify --files a.py b.py --before-hash HASH [--full]
     → 变更后回归验证
  4. harness.py rollback --to HASH --reason "..."
     → 受控回滚
  5. harness.py status
     → 总览（feature/core_lock/pitfall/最近变更/待审批）
  6. harness.py report
     → 生成 Markdown 报告
  7. harness.py review
     → 反馈循环：扫描最近 incidents，提出规则更新建议

完整示例：
  # AI 想修改 jiwen_core.py 修复 bug
  python harness.py pre-check --purpose "修复 tick 死循环 (PIT-007)" --files backend/jiwen/jiwen_core.py

  # 若 pre-check 返回 BLOCKED（需审批）
  python approval.py request --lock-id LOCK-003 --purpose "..." --files backend/jiwen/jiwen_core.py --changeset "..."
  # 人工批准
  python approval.py approve --id APP-... --reviewer 张三 --note "已确认"

  # 执行修改 + 自动回归
  python harness.py run --purpose "修复 tick 死循环 (PIT-007)" --files backend/jiwen/jiwen_core.py -- python -c "..."
  # 或仅触发后置验证
  python harness.py post-verify --files backend/jiwen/jiwen_core.py --before-hash abc1234
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import change_logger  # noqa: E402
from change_logger import (  # noqa: E402
    HARNESS_DIR, PROJECT_ROOT, log_incident,
    record as log_record, history as log_history, stats as log_stats,
    verify_chain as log_verify_chain, rollback as log_rollback,
    operation as log_operation,
)
from pre_check import pre_check as run_pre_check, _load_json, _norm  # noqa: E402
from regression_runner import run_regression  # noqa: E402
import approval  # noqa: E402


# ---------------------------------------------------------------------------
# status: 总览
# ---------------------------------------------------------------------------
def cmd_status() -> int:
    rules = _load_json("protection_rules.json")
    registry = _load_json("feature_registry.json")
    core_lock = _load_json("core_lock.json")
    pitfall_reg = _load_json("pitfall_registry.json")

    print(f"\n{'='*60}")
    print(f"AI 开发 Harness — 系统状态")
    print(f"{'='*60}")

    print(f"\n[受保护功能] {registry.get('_total', 0)} 个，全部已验证")
    for f in registry.get("features", []):
        print(f"  - {f['id']}  {f.get('name', '')}  [{f.get('status', '')}]")

    print(f"\n[核心锁定] {core_lock.get('_total_locks', 0)} 个模块 / {core_lock.get('_total_locked_files', 0)} 个文件")
    for l in core_lock.get("locked_modules", []):
        print(f"  - {l['id']}  {l.get('name', '')}")

    print(f"\n[已知踩坑] {pitfall_reg.get('_total', 0)} 个")
    by_cat = pitfall_reg.get("_by_category", {})
    for cat, cnt in by_cat.items():
        print(f"  - {cat}: {cnt} 个")

    pending = approval.list_pending()
    print(f"\n[待审批] {len(pending)} 个")
    for p in pending:
        print(f"  - {p['id']}  lock={p['lock_id']}  purpose={p['purpose'][:50]}")

    stats = log_stats()
    print(f"\n[变更统计] 总变更 {stats.get('total_changes', 0)} 次")
    print(f"  首次: {stats.get('first_change', '-')}")
    print(f"  最近: {stats.get('last_change', '-')}")
    by_date = stats.get("changes_by_date", {})
    if by_date:
        print(f"  按日分布:")
        for d, c in list(by_date.items())[-7:]:
            print(f"    {d}: {c}")

    chain = log_verify_chain()
    print(f"\n[日志链] {'完整' if chain['valid'] else '损坏'} ({chain['entries']} 条)")
    if not chain["valid"]:
        print(f"  ⚠ {chain['message']}")

    print(f"\n[质量门禁]")
    gates = rules.get("quality_gates", {})
    for name, g in gates.items():
        if name.startswith("_"):
            continue
        req = "必跑" if g.get("required") else "可选"
        block = "阻断" if g.get("block_on_fail") else "不阻断"
        print(f"  - {name}: {req} / {block}  — {g.get('description', '')[:60]}")

    print(f"\n{'='*60}\n")
    return 0


# ---------------------------------------------------------------------------
# report: Markdown 报告
# ---------------------------------------------------------------------------
def cmd_report() -> int:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    rules = _load_json("protection_rules.json")
    registry = _load_json("feature_registry.json")
    core_lock = _load_json("core_lock.json")
    pitfall_reg = _load_json("pitfall_registry.json")
    stats = log_stats()
    chain = log_verify_chain()
    pending = approval.list_pending()
    recent = log_history(limit=10)

    lines = [
        f"# AI 开发 Harness 报告",
        f"",
        f"生成时间: {ts}",
        f"",
        f"## 1. 总览",
        f"",
        f"- 受保护功能: {registry.get('_total', 0)} 个",
        f"- 核心锁定模块: {core_lock.get('_total_locks', 0)} 个",
        f"- 已知踩坑: {pitfall_reg.get('_total', 0)} 个",
        f"- 累计变更: {stats.get('total_changes', 0)} 次",
        f"- 待审批申请: {len(pending)} 个",
        f"- 日志链完整性: {'✓ 完整' if chain['valid'] else '✗ 损坏'} ({chain['entries']} 条)",
        f"",
        f"## 2. 受保护功能清单",
        f"",
        f"| ID | 名称 | 状态 | 验证日期 | 文件数 | 测试数 |",
        f"|----|------|------|----------|--------|--------|",
    ]
    for f in registry.get("features", []):
        lines.append(f"| {f['id']} | {f.get('name', '')} | {f.get('status', '')} | {f.get('verified_at', '')} | {len(f.get('files', []))} | {len(f.get('tests', []))} |")

    lines += [
        f"",
        f"## 3. 核心锁定模块",
        f"",
    ]
    for l in core_lock.get("locked_modules", []):
        lines.append(f"- **{l['id']} {l.get('name', '')}** — {l.get('rationale', '')[:80]}")
        lines.append(f"  - 文件: {', '.join(l.get('files', []))}")

    lines += [
        f"",
        f"## 4. 已知踩坑点",
        f"",
    ]
    for p in pitfall_reg.get("pitfalls", []):
        lines.append(f"- **{p['id']}** [{p.get('category', '')}/{p.get('difficulty', '')}] 频次={p.get('frequency', 0)} — {p.get('title', '')}")
        lines.append(f"  - 文件: {', '.join(p.get('files', [])) or '全局'}")
        lines.append(f"  - 修复策略: {p.get('fix_strategy', '')}")

    lines += [
        f"",
        f"## 5. 最近 10 次变更",
        f"",
    ]
    if not recent:
        lines.append("（无）")
    else:
        lines.append("| 时间 | 目的 | 文件数 | 状态 | 影响风险 |")
        lines.append("|------|------|--------|------|----------|")
        for e in recent:
            ts_short = e.get("timestamp", "")[:19]
            purpose = e.get("purpose", "")[:40]
            impact_count = e.get("impact", {}).get("total_risks", 0) if isinstance(e.get("impact"), dict) else 0
            lines.append(f"| {ts_short} | {purpose} | {e.get('file_count', 0)} | {e.get('status', '')} | {impact_count} |")

    lines += [
        f"",
        f"## 6. 质量门禁状态",
        f"",
    ]
    gates = rules.get("quality_gates", {})
    for name, g in gates.items():
        if name.startswith("_"):
            continue
        req = "必跑" if g.get("required") else "可选"
        block = "阻断" if g.get("block_on_fail") else "不阻断"
        lines.append(f"- **{name}** ({req}/{block}): {g.get('description', '')}")

    # 输出到 stdout 和文件
    report = "\n".join(lines)
    print(report)
    report_path = HARNESS_DIR / "logs" / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    print(f"\n[已保存到 {report_path.relative_to(PROJECT_ROOT)}]")
    return 0


# ---------------------------------------------------------------------------
# review: 反馈循环
# ---------------------------------------------------------------------------
def cmd_review() -> int:
    """扫描最近 incidents，提出规则更新建议。"""
    incidents_path = HARNESS_DIR / "logs" / "incidents.jsonl"
    if not incidents_path.exists():
        print("无 incidents 记录，无需审查。")
        return 0

    incidents = []
    with open(incidents_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                incidents.append(json.loads(line))

    # 按类型聚合
    by_type: dict[str, int] = {}
    for inc in incidents:
        t = inc.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    print(f"\n{'='*60}")
    print(f"Harness 反馈循环 — 最近 incidents 分析")
    print(f"{'='*60}")
    print(f"\n总事件数: {len(incidents)}")
    print(f"\n按类型分布:")
    for t, c in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"  - {t}: {c} 次")

    # 触发规则更新建议
    suggestions = []
    if by_type.get("regression_blocked", 0) >= 3:
        suggestions.append("回归阻断 ≥3 次，建议加强 pre_check 的 feature 命中检测")
    if by_type.get("approval_emergency", 0) >= 2:
        suggestions.append("紧急通道使用 ≥2 次，建议审查核心锁定范围是否过宽")
    if by_type.get("auto_rollback_failed", 0) >= 1:
        suggestions.append("自动回滚失败，建议检查 git 状态或 before_hash 有效性")
    if by_type.get("operation_failed", 0) >= 5:
        suggestions.append("AI 操作失败 ≥5 次，建议补充对应 pitfall 条目")

    print(f"\n规则更新建议:")
    if not suggestions:
        print("  （暂无）")
    else:
        for i, s in enumerate(suggestions, 1):
            print(f"  {i}. {s}")

    # 写入 review 记录
    review_path = HARNESS_DIR / "logs" / "reviews.jsonl"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "incident_count": len(incidents),
        "by_type": by_type,
        "suggestions": suggestions,
    }
    with open(review_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"\n[已记录到 {review_path.relative_to(PROJECT_ROOT)}]")
    return 0


# ---------------------------------------------------------------------------
# pre-check
# ---------------------------------------------------------------------------
def cmd_pre_check(args) -> int:
    content_snippets = {}
    for f in args.check_content or []:
        p = Path(f)
        if p.exists():
            content_snippets[f] = p.read_text(encoding="utf-8")
    result = run_pre_check(args.purpose, args.files, content_snippets)
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


# ---------------------------------------------------------------------------
# run: 受控执行
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    """受控执行：pre_check → 执行命令 → 回归验证 → 记日志"""
    # 1. 预检
    print("[1/3] 预检...", file=sys.stderr)
    result = run_pre_check(args.purpose, args.files)
    if result.blocked:
        print(f"预检阻断: {result.to_dict()}", file=sys.stderr)
        log_incident("pre_check_blocked", {"purpose": args.purpose, "files": args.files, "result": result.to_dict()})
        return 2
    if not result.passed and args.strict:
        print(f"预检有警告（--strict 模式阻断）: {result.to_dict()}", file=sys.stderr)
        return 2

    before_hash = change_logger._get_current_hash()

    # 2. 执行命令 + 记日志
    print(f"[2/3] 执行命令: {' '.join(args.command)}", file=sys.stderr)
    exit_code = 0
    error_msg = None
    try:
        with log_operation(purpose=args.purpose, files=args.files, author="AI"):
            if args.command:
                proc = subprocess.run(args.command, cwd=str(PROJECT_ROOT))
                exit_code = proc.returncode
                if exit_code != 0:
                    error_msg = f"命令退出码 {exit_code}"
    except Exception as e:
        error_msg = str(e)
        exit_code = 1

    # 3. 回归验证
    if not args.skip_verify and exit_code == 0:
        print("[3/3] 回归验证...", file=sys.stderr)
        reg = run_regression(
            files=args.files,
            required_tests=result.required_tests,
            before_hash=before_hash,
            auto_rollback=not args.no_auto_rollback,
            full=args.full,
        )
        print(json.dumps(reg.to_dict(), ensure_ascii=False, indent=2))
        if reg.blocked:
            return 3
    else:
        print(f"[3/3] 跳过回归（exit_code={exit_code} 或 --skip-verify）", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# post-verify
# ---------------------------------------------------------------------------
def cmd_post_verify(args) -> int:
    reg = run_regression(
        files=args.files,
        required_tests=args.required_tests or [],
        before_hash=args.before_hash,
        auto_rollback=not args.no_auto_rollback,
        full=args.full,
    )
    print(json.dumps(reg.to_dict(), ensure_ascii=False, indent=2))
    return 0 if reg.passed else 1


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------
def cmd_rollback(args) -> int:
    if args.dry_run:
        r = log_rollback(args.target, dry_run=True)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    if not args.reason:
        print("ERROR: 实际回滚必须提供 --reason", file=sys.stderr)
        return 1
    r = log_rollback(args.target, dry_run=False, reason=args.reason)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("success") else 1


# ---------------------------------------------------------------------------
# verify-chain
# ---------------------------------------------------------------------------
def cmd_verify_chain() -> int:
    r = log_verify_chain()
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r["valid"] else 1


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="AI 开发 Harness — 统一编排入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # pre-check
    p_pc = sub.add_parser("pre-check", help="操作前预检")
    p_pc.add_argument("--purpose", required=True)
    p_pc.add_argument("--files", nargs="+", required=True)
    p_pc.add_argument("--check-content", action="append", default=[])

    # run
    p_run = sub.add_parser("run", help="受控执行命令")
    p_run.add_argument("--purpose", required=True)
    p_run.add_argument("--files", nargs="+", required=True)
    p_run.add_argument("--strict", action="store_true", help="有警告也阻断")
    p_run.add_argument("--skip-verify", action="store_true", help="跳过回归验证")
    p_run.add_argument("--full", action="store_true", help="跑全量回归")
    p_run.add_argument("--no-auto-rollback", action="store_true", help="禁用自动回滚")
    p_run.add_argument("command", nargs=argparse.REMAINDER, help="要执行的命令（以 -- 分隔）")

    # post-verify
    p_pv = sub.add_parser("post-verify", help="变更后回归验证")
    p_pv.add_argument("--files", nargs="+", required=True)
    p_pv.add_argument("--required-tests", nargs="*", default=[])
    p_pv.add_argument("--before-hash", default=None)
    p_pv.add_argument("--full", action="store_true")
    p_pv.add_argument("--no-auto-rollback", action="store_true")

    # rollback
    p_rb = sub.add_parser("rollback", help="受控回滚")
    p_rb.add_argument("--to", required=True, dest="target")
    p_rb.add_argument("--reason", default="")
    p_rb.add_argument("--dry-run", action="store_true")

    # status
    sub.add_parser("status", help="系统总览")

    # report
    sub.add_parser("report", help="生成 Markdown 报告")

    # review
    sub.add_parser("review", help="反馈循环 — 扫描 incidents 提建议")

    # verify-chain
    sub.add_parser("verify-chain", help="校验日志哈希链")

    args = parser.parse_args()

    if args.command == "pre-check":
        return cmd_pre_check(args)
    elif args.command == "run":
        return cmd_run(args)
    elif args.command == "post-verify":
        return cmd_post_verify(args)
    elif args.command == "rollback":
        return cmd_rollback(args)
    elif args.command == "status":
        return cmd_status()
    elif args.command == "report":
        return cmd_report()
    elif args.command == "review":
        return cmd_review()
    elif args.command == "verify-chain":
        return cmd_verify_chain()
    return 0


if __name__ == "__main__":
    sys.exit(main())
