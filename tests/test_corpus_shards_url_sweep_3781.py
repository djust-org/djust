"""The URL-sweep fixture is shared inside one CI shard, like the corpus sweep (#3781).

``python/tests/test_url_scheme_sweep_3665.py`` has a module-scoped ``runs`` fixture that starts
four subprocess sweeps. pytest-split dealt its readers over all four shards, so every shard that
held one paid for the whole sweep: four sweeps per CI run. ``tests/corpus_shards.py`` already kept
the readers of ``corpus_payload`` together; ``SHARED_FIXTURES`` now names this fixture too, scoped
to its own module because ``runs`` is a generic name.

These tests run real pytest with the real plugin over a small tree that mirrors that layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.corpus_shards import SHARED_FIXTURES, split_groups
from tests.test_ci_python_test_shards import _validate_snapshot
from tests.test_corpus_shards import ROOT, _run

URL_SWEEP_MODULE = "python/tests/test_url_scheme_sweep_3665.py"
URL_UNIT = "@@djust-shared-url-sweep"
CORPUS_UNIT = "@@djust-shared-corpus-sweep"


@pytest.fixture
def two_sweeps(tmp_path: Path) -> Path:
    """Both shared fixtures, plus a decoy: a module fixture also called `runs` elsewhere."""
    (tmp_path / "conftest.py").write_text(
        "import pytest\nfrom pathlib import Path\n"
        "@pytest.fixture(scope='session')\n"
        "def corpus_payload():\n"
        "    with Path('corpus_sweeps.txt').open('a') as f: f.write('sweep\\n')\n"
        "    return {'value': 42}\n"
    )
    durations: dict[str, float] = {}

    sweep = tmp_path / URL_SWEEP_MODULE
    sweep.parent.mkdir(parents=True)
    lines = [
        "import pytest",
        "from pathlib import Path",
        "@pytest.fixture(scope='module')",
        "def runs():",
        "    with Path('url_sweeps.txt').open('a') as f: f.write('sweep\\n')",
        "    return {'value': 7}",
    ]
    for i in range(8):
        lines.append(f"def test_reader_{i}(runs): assert runs['value'] == 7")
        durations[f"{URL_SWEEP_MODULE}::test_reader_{i}"] = 90
    lines.append("def test_not_a_reader(): pass")
    durations[f"{URL_SWEEP_MODULE}::test_not_a_reader"] = 1
    sweep.write_text("\n".join(lines))

    lines = ["import pytest", "@pytest.fixture(scope='module')", "def runs(): return 1"]
    for i in range(4):
        lines.append(f"def test_decoy_{i}(runs): assert runs == 1")
        durations[f"test_decoy_runs.py::test_decoy_{i}"] = 5
    (tmp_path / "test_decoy_runs.py").write_text("\n".join(lines))

    lines = [
        f"def test_corpus_{i}(corpus_payload): assert corpus_payload['value'] == 42"
        for i in range(4)
    ]
    for i in range(4):
        durations[f"test_corpus_readers.py::test_corpus_{i}"] = 40
    (tmp_path / "test_corpus_readers.py").write_text("\n".join(lines))

    (tmp_path / "test_filler.py").write_text(
        "\n".join(f"def test_f{i}(): pass" for i in range(120))
    )
    for i in range(120):
        durations[f"test_filler.py::test_f{i}"] = 10

    (tmp_path / "durations.json").write_text(json.dumps(durations))
    return tmp_path


def _split_args(root: Path) -> tuple[str, ...]:
    return (
        "--splits",
        "4",
        "--splitting-algorithm",
        "least_duration",
        "--durations-path",
        str(root / "durations.json"),
    )


def _run_group(root: Path, group: int, *, shared: bool = True):
    return _run(
        root, "-m", "pytest", ".", "-q", *_split_args(root), "--group", str(group), shared=shared
    )


def test_the_registry_names_the_url_sweep_module_and_its_fixture() -> None:
    by_unit = {f.unit: f for f in SHARED_FIXTURES}
    assert by_unit[URL_UNIT].fixture == "runs"
    assert by_unit[URL_UNIT].module == URL_SWEEP_MODULE
    assert (ROOT / URL_SWEEP_MODULE).is_file(), "the registered module was renamed or moved"
    assert "def runs(" in (ROOT / URL_SWEEP_MODULE).read_text(), "the registered fixture is gone"


def test_url_sweep_readers_land_in_one_shard_and_the_decoy_is_left_alone(two_sweeps: Path) -> None:
    snapshot_path = two_sweeps / "snapshot.json"
    probe = _run(
        two_sweeps,
        str(ROOT / "scripts/collect-test-shards.py"),
        "--output",
        str(snapshot_path),
        *_split_args(two_sweeps),
        ".",
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    snapshot = json.loads(snapshot_path.read_text())
    units = snapshot["shared_units"]
    assert len(units[URL_UNIT]) == 8, units
    assert len(units[CORPUS_UNIT]) == 4, units
    claimed = {n for readers in units.values() for n in readers}
    assert not any("decoy" in n or "not_a_reader" in n for n in claimed), claimed
    # `_validate_snapshot` checks each unit sits whole in one shard and every test once
    _validate_snapshot(snapshot, json.loads((two_sweeps / "durations.json").read_text()), 4)


def test_the_url_sweep_fixture_runs_once_per_run_not_once_per_shard(two_sweeps: Path) -> None:
    for group in range(1, 5):
        result = _run_group(two_sweeps, group)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (two_sweeps / "url_sweeps.txt").read_text().splitlines() == ["sweep"]
    assert (two_sweeps / "corpus_sweeps.txt").read_text().splitlines() == ["sweep"]


def test_without_the_plugin_the_url_sweep_repeats_per_shard(two_sweeps: Path) -> None:
    # The gate-off of the test above: unshared, 8 readers dealt over 4 shards each pay for the
    # module fixture in every shard that holds one. If this stops being true the test above has
    # stopped proving anything.
    for group in range(1, 5):
        result = _run_group(two_sweeps, group, shared=False)
        assert result.returncode == 0, result.stdout + result.stderr
    assert len((two_sweeps / "url_sweeps.txt").read_text().splitlines()) > 1


def test_two_units_each_land_whole_in_one_group_without_losing_a_test() -> None:
    def item(nodeid: str, *fixtures: str):
        return SimpleNamespace(nodeid=nodeid, fixturenames=list(fixtures))

    sweep = [item(f"{URL_SWEEP_MODULE}::t{i}", "runs") for i in range(6)]
    corpus = [item(f"c.py::t{i}", "corpus_payload") for i in range(3)]
    decoy = [item("other.py::t", "runs")]  # same fixture name, another module: not claimed
    plain = [item(f"p.py::t{i}") for i in range(40)]
    items = [*sweep[:3], *plain[:10], *corpus[:1], *decoy, *sweep[3:], *plain[10:], *corpus[1:]]
    durations = {i.nodeid: 100.0 for i in sweep} | {i.nodeid: 50.0 for i in corpus}
    durations |= {i.nodeid: 10.0 for i in plain + decoy}
    for algorithm in ("least_duration", "duration_based_chunks"):
        groups = split_groups(items, durations, 4, algorithm)
        assigned = [i.nodeid for g in groups for i in g.selected]
        assert sorted(assigned) == sorted(i.nodeid for i in items), algorithm
        for readers in (sweep, corpus):
            ids = {r.nodeid for r in readers}
            holders = [g for g in groups if ids & {i.nodeid for i in g.selected}]
            assert len(holders) == 1, (algorithm, len(holders))
            assert ids <= {i.nodeid for i in holders[0].selected}
        for g in groups:  # collection order is preserved inside a group
            assert g.selected == [i for i in items if i in g.selected]
