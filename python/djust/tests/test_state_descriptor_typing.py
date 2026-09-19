"""Consumer-facing typing: fail if state() falls back to Any again."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


def test_mypy_infers_reads_and_rejects_wrong_assignments(tmp_path):
    pytest.importorskip("mypy")
    config = tmp_path / "mypy.ini"
    config.write_text("[mypy]\nfollow_imports = silent\nignore_missing_imports = True\n")
    source = tmp_path / "consumer.py"
    source.write_text(
        "from djust.decorators import state\n"
        "class View:\n"
        "    count = state(0)\n"
        "    items = state(default_factory=lambda: list[int]())\n"
        "view = View()\n"
        "count: int = view.count\n"
        "items: list[int] = view.items\n"
        "view.count = 1\n"
        "view.items.append(2)\n"
        "view.count = 'wrong'\n"
        "view.items.append('wrong')\n"
    )
    root = Path(__file__).resolve().parents[3]
    env = {**os.environ, "MYPYPATH": str(root / "python")}
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(config), str(source)],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    errors = [line for line in result.stdout.splitlines() if ": error:" in line]
    assert len(errors) == 2, result.stdout
    assert "consumer.py:10:" in errors[0], result.stdout
    assert "consumer.py:11:" in errors[1], result.stdout
