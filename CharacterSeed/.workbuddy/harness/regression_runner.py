#!/usr/bin/env python3
"""
AI 开发 Harness — 回归验证运行器
=====================================
AI 变更后自动运行回归测试，评估质量门禁：

  1. 受影响 feature 关联测试（pre_check 给出）
  2. pitfall 关联 guard_tests
  3. 全量 pytest（可选，用于 final 验证）
  4. py_compile 全部变更文件（lint_clean 门禁）
  5. 前端 vite build（仅当 web/src 被改时）

测试失败时：
  - 记录 incident
  - 若 auto_rollback_on_test_failure=True，自动回滚到 before_hash
  - 返回 RegressionResult.blocked=True

用法：
  python regression_runner.py --files backend/jiwen/jiwen_core.py --required-tests tests/test_jiwen_core.py
  python regression_runner.py --full  # 全量测试
"""

from __future__ import annotations

import argparse
import json
import py_compile
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from change_logger import (  # noqa: E402
    HARNESS_DIR, PROJECT_ROOT, log_incident,
)
from pre_check import _load_json, _norm  # noqa: E402

CHARACTERSEED_DIR = PROJECT_ROOT  # .workbuddy 在 CharacterSeed 下，PROJECT_ROOT 即 CharacterSeed


@dataclass
class TestRun:
    name: str
    passed: bool
    total: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    output_tail: str = ""
    failure_names: list[str] = field(default_factory=list)


@dataclass
class RegressionResult:
    passed: bool
    blocked: bool  # critical 失败 → 阻断合并
    rolled_back: bool = False
    runs: list[TestRun] = field(default_factory=list)
    lint_passed: bool = True
    build_passed: bool | None = None  # None 表示未触发
    summary: str = ""
    pass_rate: float = 1.0
    new_failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _run(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str, str, float]:
    """执行命令，返回 (returncode, stdout, stderr, duration_s)。"""
    import time
    start = time.time()
    try:
        r = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return r.returncode, r.stdout or "", r.stderr or "", time.time() - start
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", e.stderr or "TIMEOUT", time.time() - start


def _parse_pytest_output(stdout: str) -> dict:
    """从 pytest 输出解析总数/失败数等。"""
    # 最后一行通常是：===== 65 passed, 2 failed in 12.34s =====
    m = re.search(r"=?=\s*(\d+)\s+passed(?:[,\s]+(\d+)\s+failed)?(?:[,\s]+(\d+)\s+errors?)?(?:[,\s]+(\d+)\s+skipped)?", stdout)
    if not m:
        return {"passed_count": 0, "failed": 0, "errors": 0, "skipped": 0}
    passed_count = int(m.group(1))
    failed = int(m.group(2) or 0)
    errors = int(m.group(3) or 0)
    skipped = int(m.group(4) or 0)
    return {
        "passed_count": passed_count,
        "failed": failed,
        "errors": errors,
        "skipped": skipped,
        "total": passed_count + failed + errors + skipped,
    }


def _extract_failure_names(stdout: str) -> list[str]:
    """提取 FAILED 用例名。"""
    return re.findall(r"FAILED\s+(\S+)", stdout)


def run_pytest(test_paths: list[str], name: str = "pytest", timeout: int = 600) -> TestRun:
    """运行指定测试文件，返回 TestRun。"""
    if not test_paths:
        return TestRun(name=name, passed=True, total=0)

    cmd = [sys.executable, "-m", "pytest"] + test_paths + ["-v", "--tb=short", "--no-header"]
    rc, out, err, dur = _run(cmd, CHARACTERSEED_DIR, timeout=timeout)

    parsed = _parse_pytest_output(out)
    failures = parsed.get("failed", 0) + parsed.get("errors", 0)
    passed = rc == 0 and failures == 0
    return TestRun(
        name=name,
        passed=passed,
        total=parsed.get("total", 0),
        failures=parsed.get("failed", 0),
        errors=parsed.get("errors", 0),
        skipped=parsed.get("skipped", 0),
        duration_s=round(dur, 2),
        output_tail=(out + err)[-2000:],
        failure_names=_extract_failure_names(out),
    )


def run_lint_check(files: list[str]) -> tuple[bool, dict]:
    """py_compile 全部变更的 .py 文件。"""
    results = {}
    all_passed = True
    for f in files:
        if not f.endswith(".py"):
            continue
        abs_path = CHARACTERSEED_DIR / f
        if not abs_path.exists():
            results[f] = {"passed": False, "error": "文件不存在"}
            all_passed = False
            continue
        try:
            py_compile.compile(str(abs_path), doraise=True)
            results[f] = {"passed": True}
        except py_compile.PyCompileError as e:
            results[f] = {"passed": False, "error": str(e)}
            all_passed = False
    return all_passed, {"files": results}


def run_frontend_build() -> tuple[bool, str]:
    """运行 vite build。返回 (passed, output_tail)。"""
    web_dir = CHARACTERSEED_DIR / "web" / "react-vite"
    if not web_dir.exists():
        return True, "前端目录不存在，跳过"
    cmd = ["npm", "run", "build"]
    if sys.platform == "win32":
        cmd = ["cmd", "/c"] + cmd
    rc, out, err, _ = _run(cmd, web_dir, timeout=300)
    return rc == 0, (out + err)[-2000:]


def detect_new_failures(current_failures: list[str], baseline_failures: list[str]) -> list[str]:
    """对比基线，找出新增失败用例。"""
    baseline_set = set(baseline_failures)
    return [f for f in current_failures if f not in baseline_set]


def run_regression(
    files: list[str],
    required_tests: list[str] | None = None,
    before_hash: str | None = None,
    auto_rollback: bool = True,
    full: bool = False,
    baseline_failures: list[str] | None = None,
) -> RegressionResult:
    """
    运行回归验证。

    Args:
        files: 变更文件列表（用于 lint + 决定是否跑前端 build）
        required_tests: pre_check 要求必跑的测试文件
        before_hash: 变更前 commit hash（用于自动回滚）
        auto_rollback: 测试失败时是否自动回滚
        full: 是否跑全量测试
        baseline_failures: 已知的基线失败用例（用于检测新增失败）
    """
    rules = _load_json("protection_rules.json")
    gates = rules.get("quality_gates", {})
    gc = rules.get("global_constraints", {})
    auto_rollback = auto_rollback and gc.get("auto_rollback_on_test_failure", True)
    baseline_failures = baseline_failures or []

    result = RegressionResult(passed=True, blocked=False)

    # 1. lint_clean
    if gates.get("lint_clean", {}).get("required", True):
        lint_ok, lint_detail = run_lint_check(files)
        result.lint_passed = lint_ok
        if not lint_ok and gates["lint_clean"].get("block_on_fail", True):
            result.blocked = True
            result.passed = False
            result.summary = f"lint_check 失败: {lint_detail}"

    # 2. required tests
    if required_tests and not result.blocked:
        run = run_pytest(required_tests, name="required_tests")
        result.runs.append(run)
        if not run.passed:
            result.passed = False
            if gates.get("functional", {}).get("block_on_fail", True):
                result.blocked = True
                result.summary = f"必跑测试失败: {run.failure_names}"

    # 3. full pytest
    if full and not result.blocked:
        run = run_pytest([], name="full_pytest")  # 空列表会跳过，需要传 tests/ 或 .
        # 修正：传 tests 目录
        run = run_pytest(["tests/"], name="full_pytest", timeout=1200)
        result.runs.append(run)
        if run.total > 0:
            result.pass_rate = (run.total - run.failures - run.errors) / run.total
        result.new_failures = detect_new_failures(run.failure_names, baseline_failures)
        threshold = gates.get("regression", {}).get("threshold_pass_rate", 0.95)
        if result.pass_rate < threshold or result.new_failures:
            result.passed = False
            if gates.get("regression", {}).get("block_on_fail", True):
                result.blocked = True
                result.summary = f"回归通过率 {result.pass_rate:.1%} < {threshold:.0%} 或新增失败 {len(result.new_failures)} 个"

    # 4. frontend build（仅当 web/src 被改）
    web_touched = any(_norm(f).startswith("web/src/") or _norm(f).startswith("web/react-vite/src/") for f in files)
    if web_touched and not result.blocked:
        build_ok, build_out = run_frontend_build()
        result.build_passed = build_ok
        if not build_ok and gates.get("build_ok", {}).get("block_on_fail", True):
            result.blocked = True
            result.passed = False
            result.summary = f"前端 build 失败: {build_out[-500:]}"
    else:
        result.build_passed = None

    # 5. 自动回滚
    if result.blocked and auto_rollback and before_hash:
        try:
            from change_logger import rollback
            rb = rollback(before_hash, dry_run=False, reason=f"回归测试失败自动回滚: {result.summary}")
            result.rolled_back = rb.get("success", False)
        except Exception as e:
            log_incident("auto_rollback_failed", {"before_hash": before_hash, "error": str(e)})

    # 6. incident 记录
    if result.blocked:
        log_incident("regression_blocked", {
            "files": files,
            "before_hash": before_hash,
            "summary": result.summary,
            "rolled_back": result.rolled_back,
            "runs": [{"name": r.name, "passed": r.passed, "failures": r.failures} for r in result.runs],
        })

    if not result.summary:
        result.summary = "全部测试通过" if result.passed else "存在失败但未阻断"

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="AI 开发 Harness — 回归验证")
    parser.add_argument("--files", nargs="+", required=True, help="变更文件列表")
    parser.add_argument("--required-tests", nargs="*", default=[], help="必跑测试文件")
    parser.add_argument("--before-hash", default=None, help="变更前 commit hash（用于自动回滚）")
    parser.add_argument("--full", action="store_true", help="跑全量测试")
    parser.add_argument("--no-auto-rollback", action="store_true", help="禁用失败自动回滚")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = run_regression(
        files=args.files,
        required_tests=args.required_tests,
        before_hash=args.before_hash,
        auto_rollback=not args.no_auto_rollback,
        full=args.full,
    )

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"Regression Result: {'PASSED' if result.passed else 'BLOCKED'}")
        print(f"Summary: {result.summary}")
        print(f"Pass rate: {result.pass_rate:.1%}")
        print(f"Lint passed: {result.lint_passed}")
        print(f"Build passed: {result.build_passed}")
        print(f"Rolled back: {result.rolled_back}")
        print(f"{'='*60}")
        for r in result.runs:
            status = "PASS" if r.passed else "FAIL"
            print(f"  [{status}] {r.name}: total={r.total} failures={r.failures} errors={r.errors} duration={r.duration_s}s")
            if r.failure_names:
                for fn in r.failure_names[:5]:
                    print(f"         FAILED: {fn}")
        if result.new_failures:
            print(f"\nNew failures (not in baseline): {len(result.new_failures)}")
            for nf in result.new_failures[:5]:
                print(f"  - {nf}")
        print()

    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
