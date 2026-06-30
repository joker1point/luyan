#!/usr/bin/env python3
"""
AI 开发 Harness — 操作前冲突预检器
=====================================
AI 在修改代码 *之前* 必须调用本模块，通过四道闸门：

  闸门 1: read_only 检查 — 是否触碰只读文件
  闸门 2: core_lock 检查 — 是否触碰核心锁定模块（需走 approval.py 审批）
  闸门 3: feature_registry 检查 — 是否影响已完成 feature（需回归测试）
  闸门 4: pitfall 检查 — 是否命中已知踩坑点（需引用 pitfall ID）
  闸门 5: danger_patterns 检查 — 是否引入禁止代码模式（critical 即阻断）
  闸门 6: conflict_rules 检查 — 是否触发跨模块冲突规则（需人工确认）

返回 PreCheckResult，包含 passed / blocked / warnings / required_approvals。

用法：
  python pre_check.py --purpose "修复 Jiwen 阈值" --files backend/jiwen/jiwen_core.py
  python pre_check.py --purpose "..." --files a.py b.py --check-content path/to/file
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# 复用 change_logger 的路径配置
sys.path.insert(0, str(Path(__file__).resolve().parent))
from change_logger import HARNESS_DIR, PROJECT_ROOT  # noqa: E402


@dataclass
class GateResult:
    gate: str
    passed: bool
    severity: str = "info"  # info / warning / high / critical
    hits: list[dict] = field(default_factory=list)
    message: str = ""


@dataclass
class PreCheckResult:
    passed: bool  # 全部闸门通过
    blocked: bool  # 有 critical 闸门失败
    purpose: str
    files: list[str]
    gates: list[GateResult]
    required_approvals: list[str]  # 需要的审批 ID（来自 approval.py）
    required_tests: list[str]  # 必须运行的测试文件
    acknowledged_pitfalls: list[str]  # 必须在 purpose 中引用的 pitfall ID

    def to_dict(self) -> dict:
        d = asdict(self)
        d["gates"] = [asdict(g) if isinstance(g, GateResult) else g for g in self.gates]
        return d


def _load_json(name: str) -> dict:
    path = HARNESS_DIR / name
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def _matches_file_or_dir(target: str, files: list[str]) -> bool:
    """检查 files 是否命中 target（文件或目录前缀）。"""
    target_n = _norm(target).rstrip("/")
    for f in files:
        f_n = _norm(f)
        if f_n == target_n or f_n.startswith(target_n + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# 闸门 1: read_only
# ---------------------------------------------------------------------------
def gate_read_only(files: list[str], rules: dict) -> GateResult:
    ro = rules.get("access_levels", {}).get("read_only", {})
    ro_files = ro.get("files", [])
    ro_dirs = ro.get("directories", [])
    hits = []
    for f in files:
        f_n = _norm(f)
        for rof in ro_files:
            if f_n == _norm(rof):
                hits.append({"file": f, "reason": f"匹配只读文件 {rof}"})
        for rod in ro_dirs:
            if _matches_file_or_dir(rod, [f]):
                hits.append({"file": f, "reason": f"匹配只读目录 {rod}"})
    passed = not hits
    return GateResult(
        gate="read_only",
        passed=passed,
        severity="critical" if hits else "info",
        hits=hits,
        message="禁止修改只读文件，必须走 approval.py 审批" if hits else "通过",
    )


# ---------------------------------------------------------------------------
# 闸门 2: core_lock
# ---------------------------------------------------------------------------
def gate_core_lock(files: list[str], core_lock: dict) -> tuple[GateResult, list[str]]:
    """
    返回 (GateResult, required_approval_lock_ids)。
    若已通过 approval.py 获得有效审批，则视为通过。
    """
    try:
        from approval import check_valid
    except ImportError:
        check_valid = None

    hits = []
    unapproved = []
    for lock in core_lock.get("locked_modules", []):
        matched = False
        for lf in lock.get("files", []):
            if _matches_file_or_dir(lf, files):
                hits.append({
                    "lock_id": lock.get("id"),
                    "name": lock.get("name"),
                    "file": lf,
                    "rationale": lock.get("rationale", ""),
                })
                matched = True
                break
        if matched:
            # 检查是否有有效审批
            approved = None
            if check_valid:
                approved = check_valid(lock.get("id"), files)
            if not approved:
                unapproved.append(lock.get("id"))
            else:
                # 在 hit 上标注审批状态
                hits[-1]["approved_by"] = approved.get("reviewer")
                hits[-1]["approval_id"] = approved.get("id")
                hits[-1]["approval_status"] = approved.get("status")

    passed = not unapproved
    return GateResult(
        gate="core_lock",
        passed=passed,
        severity="critical" if unapproved else ("info" if not hits else "info"),
        hits=hits,
        message=f"核心逻辑锁定，需 approval.py 审批: {unapproved}" if unapproved else (
            "通过（已获审批）" if hits else "通过"
        ),
    ), unapproved


# ---------------------------------------------------------------------------
# 闸门 3: feature_registry
# ---------------------------------------------------------------------------
def gate_feature_registry(files: list[str], registry: dict) -> GateResult:
    hits = []
    required_tests = []
    for feat in registry.get("features", []):
        for ff in feat.get("files", []):
            if _matches_file_or_dir(ff, files):
                hits.append({
                    "feature_id": feat.get("id"),
                    "name": feat.get("name"),
                    "matched_file": ff,
                })
                required_tests.extend(feat.get("tests", []))
                break
    passed = True  # feature 命中不阻断，但要求跑测试
    return GateResult(
        gate="feature_registry",
        passed=passed,
        severity="high" if hits else "info",
        hits=hits,
        message=f"命中 {len(hits)} 个已完成功能，必须运行 {len(required_tests)} 个关联测试" if hits else "通过",
    )


# ---------------------------------------------------------------------------
# 闸门 4: pitfall
# ---------------------------------------------------------------------------
def gate_pitfall(files: list[str], purpose: str, registry: dict) -> tuple[GateResult, list[str]]:
    hits = []
    acknowledged = []
    for pit in registry.get("pitfalls", []):
        pit_files = pit.get("files", [])
        if not pit_files:
            continue
        for pf in pit_files:
            if _matches_file_or_dir(pf, files):
                # 检查 purpose 是否引用了 pitfall ID
                pid = pit.get("id", "")
                acked = pid and pid in purpose
                hits.append({
                    "pitfall_id": pid,
                    "title": pit.get("title"),
                    "category": pit.get("category"),
                    "difficulty": pit.get("difficulty"),
                    "acknowledged": acked,
                })
                if not acked:
                    acknowledged.append(pid)
                break
    passed = not acknowledged  # 未在 purpose 中引用 pitfall ID 的，阻断
    return GateResult(
        gate="pitfall",
        passed=passed,
        severity="high" if acknowledged else ("warning" if hits else "info"),
        hits=hits,
        message=f"命中 {len(hits)} 个已知踩坑点；purpose 中需引用未确认的 pitfall ID: {acknowledged}" if acknowledged else (
            f"命中 {len(hits)} 个已知踩坑点，全部已在 purpose 中确认" if hits else "通过"
        ),
    ), acknowledged


# ---------------------------------------------------------------------------
# 闸门 5: danger_patterns
# ---------------------------------------------------------------------------
def gate_danger_patterns(files: list[str], content_snippets: dict[str, str], rules: dict) -> GateResult:
    """content_snippets: {file_path: file_content} 用于扫描危险模式。"""
    patterns = rules.get("danger_patterns", {}).get("patterns", [])
    hits = []
    for f, content in content_snippets.items():
        for p in patterns:
            try:
                if re.search(p["pattern"], content):
                    hits.append({
                        "file": f,
                        "pattern_id": p.get("id"),
                        "severity": p.get("severity"),
                        "message": p.get("message"),
                    })
            except re.error:
                continue
    # critical 级别直接阻断；high 级别 warning
    critical_hits = [h for h in hits if h.get("severity") == "critical"]
    passed = not critical_hits
    return GateResult(
        gate="danger_patterns",
        passed=passed,
        severity="critical" if critical_hits else ("warning" if hits else "info"),
        hits=hits,
        message=f"检测到 {len(critical_hits)} 个 critical 危险模式" if critical_hits else (
            f"检测到 {len(hits)} 个 warning 级危险模式" if hits else "通过"
        ),
    )


# ---------------------------------------------------------------------------
# 闸门 6: conflict_rules
# ---------------------------------------------------------------------------
def gate_conflict_rules(files: list[str], rules: dict) -> GateResult:
    """简单的 detector 字符串规则匹配。"""
    rule_defs = rules.get("conflict_rules", {}).get("rules", [])
    hits = []
    files_n = [_norm(f) for f in files]
    for rule in rule_defs:
        detector = rule.get("detector", "")
        triggered = False
        if "files_include_any:" in detector:
            # 提取 [file1, file2]
            m = re.search(r"files_include_any:\[([^\]]+)\]", detector)
            if m:
                listed = [s.strip() for s in m.group(1).split(",")]
                if any(_norm(x) in files_n for x in listed):
                    triggered = True
        if "files_match_pattern:" in detector:
            m = re.search(r"files_match_pattern:([^\s]+)", detector)
            if m:
                pat = m.group(1)
                if any(re.search(pat, f) for f in files_n):
                    triggered = True
        if "distinct_top_dirs_in:" in detector:
            # distinct_top_dirs_in:backend/* > 3
            m = re.search(r"distinct_top_dirs_in:(\S+)\s*>\s*(\d+)", detector)
            if m:
                prefix = m.group(1).rstrip("/*")
                threshold = int(m.group(2))
                top_dirs = set()
                for f in files_n:
                    if f.startswith(prefix + "/"):
                        parts = f.split("/")
                        if len(parts) >= 2:
                            top_dirs.add(parts[1])
                if len(top_dirs) > threshold:
                    triggered = True
        if triggered:
            hits.append({
                "rule_id": rule.get("id"),
                "description": rule.get("description"),
                "severity": rule.get("severity"),
            })
    critical_hits = [h for h in hits if h.get("severity") == "critical"]
    passed = not critical_hits
    return GateResult(
        gate="conflict_rules",
        passed=passed,
        severity="critical" if critical_hits else ("warning" if hits else "info"),
        hits=hits,
        message=f"触发 {len(critical_hits)} 个 critical 冲突规则" if critical_hits else (
            f"触发 {len(hits)} 个 warning 冲突规则" if hits else "通过"
        ),
    )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def pre_check(purpose: str, files: list[str], content_snippets: dict[str, str] | None = None) -> PreCheckResult:
    """
    执行操作前预检。返回 PreCheckResult。

    Args:
        purpose: 变更目的（用于检查是否引用了 pitfall ID）
        files: 计划修改的文件列表（相对路径）
        content_snippets: 计划写入的新内容 {file: content}，用于 danger_patterns 扫描
    """
    rules = _load_json("protection_rules.json")
    registry = _load_json("feature_registry.json")
    core_lock = _load_json("core_lock.json")
    pitfall_reg = _load_json("pitfall_registry.json")

    content_snippets = content_snippets or {}

    g1 = gate_read_only(files, rules)
    g2, unapproved_locks = gate_core_lock(files, core_lock)
    g3 = gate_feature_registry(files, registry)
    g4, unacked_pitfalls = gate_pitfall(files, purpose, pitfall_reg)
    g5 = gate_danger_patterns(files, content_snippets, rules)
    g6 = gate_conflict_rules(files, rules)

    gates = [g1, g2, g3, g4, g5, g6]

    # global_constraints
    gc = rules.get("global_constraints", {})
    max_files = gc.get("max_files_per_change", 10)
    if len(files) > max_files:
        gates.append(GateResult(
            gate="max_files",
            passed=False,
            severity="warning",
            hits=[{"file_count": len(files), "limit": max_files}],
            message=f"变更文件数 {len(files)} 超过上限 {max_files}",
        ))

    # 汇总
    blocked = any(g.severity == "critical" and not g.passed for g in gates)
    passed = not blocked and all(g.passed for g in gates)

    # 收集需要的审批（核心锁定 + 只读文件 → 必须人工审批）
    required_approvals = []
    if not g1.passed:
        required_approvals.append("read_only")
    required_approvals.extend(unapproved_locks)

    # 收集必须运行的测试
    required_tests = list({t for h in g3.hits for t in (registry.get("features", []) and [])})
    # 重新从 registry 提取（避免上面的简化）
    required_tests = []
    for feat in registry.get("features", []):
        for ff in feat.get("files", []):
            if _matches_file_or_dir(ff, files):
                required_tests.extend(feat.get("tests", []))
                break
    # 加上 pitfall 关联的 guard_tests
    for pit in pitfall_reg.get("pitfalls", []):
        pit_files = pit.get("files", [])
        if not pit_files:
            continue
        for pf in pit_files:
            if _matches_file_or_dir(pf, files):
                required_tests.extend(pit.get("guard_tests", []))
                break
    required_tests = sorted(set(t for t in required_tests if t))

    return PreCheckResult(
        passed=passed,
        blocked=blocked,
        purpose=purpose,
        files=files,
        gates=gates,
        required_approvals=required_approvals,
        required_tests=required_tests,
        acknowledged_pitfalls=unacked_pitfalls,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AI 开发 Harness — 操作前预检")
    parser.add_argument("--purpose", required=True, help="变更目的（如修改踩坑点，请引用 PIT-XXX ID）")
    parser.add_argument("--files", nargs="+", required=True, help="计划修改的文件列表（相对路径）")
    parser.add_argument("--check-content", action="append", default=[],
                        help="待写入内容的文件路径，用于扫描危险模式（可多次指定）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    content_snippets = {}
    for f in args.check_content:
        p = Path(f)
        if p.exists():
            content_snippets[f] = p.read_text(encoding="utf-8")

    result = pre_check(args.purpose, args.files, content_snippets)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Pre-Check Result: {'PASSED' if result.passed else 'BLOCKED' if result.blocked else 'WARNINGS'}")
        print(f"Purpose: {result.purpose}")
        print(f"Files: {', '.join(result.files)}")
        print(f"{'='*60}")
        for g in result.gates:
            status = "PASS" if g.passed else f"FAIL[{g.severity}]"
            print(f"  [{status}] {g.gate}: {g.message}")
            for h in g.hits:
                print(f"         - {h}")
        if result.required_approvals:
            print(f"\nRequired approvals: {result.required_approvals}")
        if result.required_tests:
            print(f"Required tests: {result.required_tests}")
        if result.acknowledged_pitfalls:
            print(f"Unacknowledged pitfalls (引用到 purpose 中即可解除): {result.acknowledged_pitfalls}")
        print()

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
