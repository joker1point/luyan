"""
测试套件入口
- 控制台运行:  python tests/run_tests.py
- 输出到文件:  python tests/run_tests.py -o
- 指定测试:    python tests/run_tests.py test_short_term.py -o

记录文件统一保存在 tests/ 目录下。
"""

import sys
import os
import subprocess
from datetime import datetime

# 记录文件统一输出到 tests/records/ 目录
_RECORDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "records")


def _resolve_output_path(test_name: str) -> str:
    """
    在 tests/records/ 目录下生成唯一的记录文件路径。
    若文件已存在则自动添加数字后缀，避免覆盖。
    """
    os.makedirs(_RECORDS_DIR, exist_ok=True)
    base_name = f"test_output_{test_name}" if test_name else "test_output_all"
    file_path = os.path.join(_RECORDS_DIR, f"{base_name}.txt")
    if not os.path.exists(file_path):
        return file_path
    for idx in range(1, 100):
        alt_path = os.path.join(_RECORDS_DIR, f"{base_name}_{idx}.txt")
        if not os.path.exists(alt_path):
            return alt_path
    return file_path


def run_tests(test_target: str = "", output_to_file: bool = False) -> int:
    """运行测试，支持指定测试文件和可选的输出记录"""
    # 构建 pytest 参数
    if test_target:
        target = test_target if test_target.startswith("tests/") else f"tests/{test_target}"
    else:
        target = "tests/"

    pytest_args = [sys.executable, "-m", "pytest", target, "-v", "--tb=short"]

    if output_to_file:
        out_path = _resolve_output_path(test_target.replace(".py", "") if test_target else "")
        header = (
            f"CharacterSeed Test Report\n"
            f"Target: {target}\n"
            f"Run at: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"{'=' * 60}\n"
        )
        print(header)
        print(f"记录文件: {out_path}\n")

        with open(out_path, "w", encoding="utf-8") as f:
            _ = f.write(header + "\n")
            result = subprocess.run(pytest_args + ["-s"], capture_output=True, text=True)
            _ = f.write(result.stdout)
            if result.stderr:
                _ = f.write("\n--- stderr ---\n")
                _ = f.write(result.stderr)
            _ = f.write(f"\n{'=' * 60}\n")
            _ = f.write(f"Exit code: {result.returncode}\n")

        # 终端也打印结果
        print(result.stdout)
        if result.stderr:
            print(result.stderr, file=sys.stderr)
        print(f"退出码: {result.returncode}")
        return result.returncode
    else:
        return subprocess.run(pytest_args).returncode


if __name__ == "__main__":
    output_to_file = False
    test_target = ""

    for arg in sys.argv[1:]:
        if arg == "-o":
            output_to_file = True
        elif not arg.startswith("-"):
            test_target = arg

    exit_code = run_tests(test_target, output_to_file)
    sys.exit(exit_code)
