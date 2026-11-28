"""The component benchmark keeps running.

``scripts/bench_components.py`` measures each layer (component tags, Python
components, Rust render, VDOM diff, page GET and mount, turns) and records the
results to compare between runs, so it must not rot between runs. This runs it
end to end at a tiny size and checks the shape of what it reports, the
comparison and the profiling output. It asserts nothing about durations (#2156).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
SCRIPT = os.path.join(ROOT, "scripts", "bench_components.py")


def _run(*args: str) -> subprocess.CompletedProcess:
    # The script runs `git describe` for the results' revision: strip git's
    # execution variables so a hook's GIT_DIR is not inherited (#3179).
    from tests.git_env import isolated_git_env

    env = isolated_git_env()
    env.pop("DJANGO_SETTINGS_MODULE", None)
    return subprocess.run(
        [sys.executable, SCRIPT, "--quick", *args],
        capture_output=True,
        text=True,
        timeout=600,
        env=env,
        cwd=ROOT,
    )


@pytest.mark.slow
def test_every_suite_runs_and_compares(tmp_path):
    out = tmp_path / "result.json"
    proc = _run("--json", str(out))
    assert proc.returncode == 0, proc.stdout + proc.stderr

    result = json.loads(out.read_text())
    assert result["errors"] == {}, result["errors"]
    cases = result["cases"]
    suites = {name.split("/", 1)[0] for name in cases}
    assert suites == {"tag", "component", "render", "vdom", "page", "turn"}
    for name in (
        "page/includes/get",
        "page/includes/mount",
        "page/big/get",
        "turn/table/ws",
        "turn/table/http",
        "vdom/table/diff_html",
        "render/includes/rust-cold",
        "tag/badge/render",
    ):
        assert name in cases, name
    for name, case in cases.items():
        assert case["rounds"] == 2, name
        assert case["p50_ms"] > 0 and case["p95_ms"] >= case["p50_ms"], name
        assert case["alloc_peak_kb"] >= 0, name
    # Rust reports its own split of a render_with_diff turn.
    assert "rust_diff_ms" in cases["vdom/table/render_with_diff"]

    proc = _run("--suites", "vdom", "--compare", str(out), "--no-alloc")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "Compared with" in proc.stdout
    assert "vdom/table/diff_html" in proc.stdout
    assert "(not run this time:" in proc.stdout


@pytest.mark.slow
def test_profile_mode_writes_hotspots(tmp_path):
    prof = tmp_path / "prof"
    out = tmp_path / "result.json"
    proc = _run(
        "--cases", "page/includes/get,vdom/table", "--profile", str(prof), "--json", str(out)
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (prof / "page__includes__get.prof").exists()
    text = (prof / "hotspots.txt").read_text()
    assert text.startswith("== across 3 cases")
    assert "== page/includes/get" in text
    result = json.loads(out.read_text())
    assert result["hotspots"] and "function" in result["hotspots"][0]
