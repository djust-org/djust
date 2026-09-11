"""Exercise shared-sweep scheduling through real pytest and pytest-split."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from tests.corpus_shards import split_groups
from tests.test_ci_python_test_shards import _validate_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _run(root: Path, *args: str, shared: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONPATH": str(ROOT)}
    env.pop("PYTEST_ADDOPTS", None)
    env["PYTEST_PLUGINS"] = "pytest_split.plugin,xdist.plugin,tests.lost_items_guard"
    if shared:
        env["PYTEST_PLUGINS"] += ",tests.corpus_shards"
    return subprocess.run(
        [sys.executable, *args], cwd=root, env=env, capture_output=True, text=True, timeout=30
    )


@pytest.fixture
def small_corpus(tmp_path: Path) -> Path:
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.fixture(scope='session')\n"
        "def corpus_payload():\n"
        "    with Path('sweeps.txt').open('a') as f: f.write('sweep\\n')\n"
        "    return {'value': 42}\n"
    )
    # Readers are deliberately separated in collection order. A fixture that
    # depends on the shared payload tests recognition through fixture closure.
    lines = [
        "import pytest",
        "@pytest.fixture",
        "def indirect(corpus_payload): return corpus_payload",
    ]
    durations = {}
    for i in range(20):
        if i % 4 == 0:
            lines.append(f"def test_{i}(indirect): assert indirect['value'] == 42")
            durations[f"test_sample.py::test_{i}"] = 60
        else:
            lines.append(f"def test_{i}(): pass")
            durations[f"test_sample.py::test_{i}"] = 10
    (tmp_path / "test_sample.py").write_text("\n".join(lines))
    (tmp_path / "durations.json").write_text(json.dumps(durations))
    return tmp_path


@pytest.mark.parametrize("algorithm", ["least_duration", "duration_based_chunks"])
def test_real_shards_share_one_sweep_and_match_probe(small_corpus: Path, algorithm: str) -> None:
    common = (
        "--splits",
        "4",
        "--splitting-algorithm",
        algorithm,
        "--durations-path",
        str(small_corpus / "durations.json"),
    )
    snapshot_path = small_corpus / "snapshot.json"
    probe = _run(
        small_corpus,
        str(ROOT / "scripts/collect-test-shards.py"),
        "--output",
        str(snapshot_path),
        *common,
        "test_sample.py",
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    assert not (small_corpus / "sweeps.txt").exists(), "collection executed the expensive fixture"
    snapshot = json.loads(snapshot_path.read_text())
    _validate_snapshot(snapshot, json.loads((small_corpus / "durations.json").read_text()), 4)
    assert len(snapshot["shared_corpus"]) == 5
    all_ran = []
    for group in range(1, 5):
        result = _run(
            small_corpus, "-m", "pytest", "test_sample.py", "-v", *common, "--group", str(group)
        )
        assert result.returncode == 0, result.stdout + result.stderr
        ran = [line.split()[0] for line in result.stdout.splitlines() if " PASSED" in line]
        assert ran == snapshot["groups"][group - 1]
        all_ran.extend(ran)
    assert sorted(all_ran) == sorted(snapshot["collected"])
    assert (small_corpus / "sweeps.txt").read_text().splitlines() == ["sweep"]


def test_without_grouping_the_same_readers_repeat_the_sweep(small_corpus: Path) -> None:
    for group in range(1, 5):
        result = _run(
            small_corpus,
            "-m",
            "pytest",
            "test_sample.py",
            "-q",
            "--splits",
            "4",
            "--group",
            str(group),
            "--splitting-algorithm",
            "least_duration",
            "--durations-path",
            str(small_corpus / "durations.json"),
            shared=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    assert len((small_corpus / "sweeps.txt").read_text().splitlines()) == 4


def test_group_weight_counts_shared_wait_once_and_preserves_order() -> None:
    readers = [
        SimpleNamespace(nodeid=f"reader{i}", fixturenames=["corpus_payload"]) for i in range(4)
    ]
    ordinary = [SimpleNamespace(nodeid=f"plain{i}", fixturenames=[]) for i in range(12)]
    items = readers + ordinary
    durations = {item.nodeid: 100.0 for item in readers} | {item.nodeid: 10.0 for item in ordinary}
    groups = split_groups(items, durations, 4, "least_duration")
    assert sum(group.duration for group in groups) == 220  # 100 shared, not 400; 120 ordinary
    owners = [g for g in groups if any(item in readers for item in g.selected)]
    assert len(owners) == 1
    for g in groups:
        assert g.selected == [item for item in items if item in g.selected]
        assert len(g.selected) + len(g.deselected) == len(items)


def test_split_readers_are_rejected_even_when_coverage_is_complete() -> None:
    snapshot = {
        "collected": ["reader1", "reader2", "plain"],
        "groups": [["reader1"], ["reader2", "plain"]],
        "shared_corpus": ["reader1", "reader2"],
    }
    with pytest.raises(AssertionError, match="repeat the sweep across shards"):
        _validate_snapshot(snapshot, {name: 1.0 for name in snapshot["collected"]}, 2)


def test_missing_durations_still_assign_every_item_once(small_corpus: Path) -> None:
    output = small_corpus / "without-durations.json"
    result = _run(
        small_corpus,
        str(ROOT / "scripts/collect-test-shards.py"),
        "--output",
        str(output),
        "--splits",
        "4",
        "--splitting-algorithm",
        "least_duration",
        "--durations-path",
        str(small_corpus / "absent.json"),
        "test_sample.py",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    snapshot = json.loads(output.read_text())
    # Equal synthetic costs verify the fallback partition independently of
    # the intentionally absent timing file (which fails the real staleness gate).
    _validate_snapshot(snapshot, {nodeid: 1.0 for nodeid in snapshot["collected"]}, 4)


def test_unsplit_runs_do_not_require_pytest_split(small_corpus: Path) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT),
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_PLUGINS": "tests.corpus_shards",
    }
    env.pop("PYTEST_ADDOPTS", None)
    # Block imports, not only plugin registration, to simulate a minimal
    # environment where pytest-split is not installed at all.
    code = """
import sys
class NoSplit:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'pytest_split' or fullname.startswith('pytest_split.'):
            raise ImportError('pytest-split is intentionally unavailable')
sys.meta_path.insert(0, NoSplit())
import pytest
raise SystemExit(pytest.main(['test_sample.py', '-q']))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=small_corpus,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "20 passed" in result.stdout


def test_repository_sweep_readers_cannot_bypass_the_shared_fixture() -> None:
    # A future reader that calls corpus.sweep directly would be invisible to
    # fixture-based scheduling and bring back an extra full run on its shard.
    for path in (ROOT / "python/tests").glob("test_*.py"):
        source = path.read_text()
        if "corpus" not in source or "sweep" not in source:
            continue
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            if not any(arg.arg == "corpus" for arg in node.args.args):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "corpus"
                    and call.func.attr == "sweep"
                    and not call.args
                    and not call.keywords
                ):
                    pytest.fail(
                        f"{path.name}::{node.name} must request corpus_payload for the full sweep"
                    )
