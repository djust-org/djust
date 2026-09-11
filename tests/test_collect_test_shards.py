"""Check the fast shard probe against real pytest-split CLI invocations."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_ci_python_test_shards import _validate_snapshot

PROBE = Path(__file__).resolve().parents[1] / "scripts/collect-test-shards.py"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # The tiny corpus is independent of project plugins/settings. Explicitly
    # load the installed splitter just as its entry point normally would.
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_PLUGINS"] = "pytest_split.plugin"
    return subprocess.run(
        [sys.executable, *args],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


@pytest.mark.parametrize("algorithm", ["least_duration", "duration_based_chunks"])
def test_snapshot_matches_real_split_cli(tmp_path: Path, algorithm: str) -> None:
    # Collection counter proves the probe imports this module exactly once.
    # Bodies must never run in either collection path.
    (tmp_path / "test_sample.py").write_text(
        "from pathlib import Path\n"
        "with Path('collections.txt').open('a') as f: f.write('collected\\n')\n"
        + "\n".join(f"def test_{i}(): raise AssertionError('executed')" for i in range(12))
    )
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --ignore=ignored\n")
    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored/test_broken.py").write_text("raise RuntimeError('must stay excluded')\n")
    durations = tmp_path / "durations.json"
    # Uneven times, one missing test, and a stale id exercise plugin defaults.
    durations.write_text(
        json.dumps({**{f"test_sample.py::test_{i}": i + 1 for i in range(11)}, "stale": 40})
    )
    output = tmp_path / "snapshot.json"
    common = (
        "--splits",
        "4",
        "--splitting-algorithm",
        algorithm,
        "--durations-path",
        str(durations),
    )
    result = _run(tmp_path, str(PROBE), "--output", str(output), *common, ".")
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = json.loads(output.read_text())
    assert (tmp_path / "collections.txt").read_text() == "collected\n"
    assert len(snapshot["collected"]) == 12
    for group in range(1, 5):
        cli = _run(
            tmp_path,
            "-m",
            "pytest",
            "test_sample.py",
            "--collect-only",
            "-q",
            *common,
            "--group",
            str(group),
        )
        assert cli.returncode == 0, cli.stdout + cli.stderr
        ids = [line for line in cli.stdout.splitlines() if line.startswith("test_sample.py::")]
        assert snapshot["groups"][group - 1] == ids


def test_failed_collection_cannot_publish_partial_or_stale_snapshot(tmp_path: Path) -> None:
    (tmp_path / "test_good.py").write_text("def test_good(): pass\n")
    (tmp_path / "test_broken.py").write_text("raise RuntimeError('broken collection')\n")
    output = tmp_path / "snapshot.json"
    output.write_text('{"collected": ["old"]}')
    result = _run(
        tmp_path,
        str(PROBE),
        "--output",
        str(output),
        "--splits",
        "2",
        "--splitting-algorithm",
        "least_duration",
        "--durations-path",
        str(tmp_path / "absent.json"),
        ".",
    )
    assert result.returncode != 0
    assert "broken collection" in result.stdout
    assert not output.exists()


@pytest.mark.parametrize(
    ("groups", "durations", "message"),
    [
        ([["a"], ["a", "b"]], {"a": 1, "b": 1}, "more than once"),
        ([["a"], []], {"a": 1, "b": 1}, "omit or invent"),
        ([["a"], ["b"]], {"a": 1}, "no recorded duration"),
        ([["a"], ["b"]], {"a": 1, "b": 10}, "balance limit"),
    ],
)
def test_validation_rejects_broken_shards(groups: list, durations: dict, message: str) -> None:
    good = {"collected": ["a", "b"], "groups": [["a"], ["b"]]}
    _validate_snapshot(good, {"a": 1, "b": 1}, 2)
    with pytest.raises(AssertionError, match=message):
        _validate_snapshot({**good, "groups": groups}, durations, 2)
