#!/usr/bin/env python3
"""Run ADR-034's isolated typing proof; missing tools fail rather than skip.

Use the current Python environment's mypy and a separately installed Pyright.
--pyright-command accepts an explicit executable command (no shell expansion).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys


FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "typing_component_bindings"


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, "PYTHONPATH": str(FIXTURES.parents[1] / "python")}
    return subprocess.run(command, cwd=FIXTURES, env=environment, text=True, capture_output=True, timeout=90)


def check_diagnostics(name: str, actual: set[tuple[str, int]], expected: set[tuple[str, int]]) -> None:
    missing, unexpected = expected - actual, actual - expected
    if missing or unexpected:
        raise RuntimeError(f"{name}: missing={sorted(missing)}, unexpected={sorted(unexpected)}")
    print(f"{name}: {len(expected)} negative locations rejected; positive/prototype clean")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyright-command", default="pyright")
    args = parser.parse_args()
    expected = {
        ("negative.py", number)
        for number, line in enumerate((FIXTURES / "negative.py").read_text().splitlines(), 1)
        if "# error:" in line
    }
    if not expected:
        raise RuntimeError("No negative fixture markers found")
    mypy = run([
        sys.executable, "-m", "mypy", "--config-file", "mypy.ini",
        "--no-incremental", "--show-column-numbers", "--no-pretty",
        "prototype.py", "positive.py", "negative.py",
    ])
    if mypy.returncode != 1:
        raise RuntimeError(f"mypy did not report expected errors:\n{mypy.stdout}\n{mypy.stderr}")
    actual = {
        (Path(match[1]).name, int(match[2]))
        for line in mypy.stdout.splitlines()
        if (match := re.match(r"(.+?):(\d+):\d+: error:", line))
    }
    check_diagnostics("mypy", actual, expected)
    pyright = run(shlex.split(args.pyright_command) + ["--project", "pyrightconfig.json", "--outputjson"])
    if pyright.returncode != 1:
        raise RuntimeError(f"Pyright did not report expected errors:\n{pyright.stdout}\n{pyright.stderr}")
    report = json.loads(pyright.stdout)
    actual = {
        (Path(item["file"]).name, item["range"]["start"]["line"] + 1)
        for item in report["generalDiagnostics"]
        if item["severity"] in {"error", "warning"}
    }
    check_diagnostics(f"Pyright {report['version']}", actual, expected)
    runtime = run([sys.executable, "runtime_check.py"])
    if runtime.returncode:
        raise RuntimeError(f"Runtime identity assertions failed:\n{runtime.stdout}\n{runtime.stderr}")
    print("Runtime: bound identity, isolated state/configuration, inheritance and async callbacks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
