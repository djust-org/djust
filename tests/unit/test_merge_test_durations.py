"""The per-shard durations merge, and what it refuses (#2584).

``scripts/merge-test-durations.py`` unions the four ``.test_durations``
artifacts CI's ``python-tests`` shards upload into the file that decides how
the next run deals those same shards. Every refusal below is a way of writing
a file that LOOKS fresh and silently count-balances part of the suite — the
condition that dealt shard 3 9909 tests against shard 2's 1898 (#2703). A
merge that writes anyway is worse than one that fails, because the failure is
loud on the day and the bad file is quiet for weeks.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "merge-test-durations.py"


def _load():
    spec = importlib.util.spec_from_file_location("merge_test_durations", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["merge_test_durations"] = mod
    spec.loader.exec_module(mod)
    return mod


mtd = _load()


def _write(tmp_path: Path, name: str, data: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _shards(tmp_path: Path) -> list[Path]:
    return [
        _write(tmp_path, "s1.json", {"tests/test_a.py::test_1": 1.0}),
        _write(tmp_path, "s2.json", {"python/tests/test_b.py::test_2": 2.0}),
        _write(tmp_path, "s3.json", {"python/djust/tests/test_c.py::test_3": 3.0}),
        _write(tmp_path, "s4.json", {"tests/test_d.py::test_4": 4.5}),
    ]


class TestUnion:
    def test_four_disjoint_shards_union_into_one_file(self, tmp_path: Path) -> None:
        out = tmp_path / "merged.json"
        rc = mtd.main([*[str(p) for p in _shards(tmp_path)], "-o", str(out)])
        assert rc == 0
        assert json.loads(out.read_text()) == {
            "tests/test_a.py::test_1": 1.0,
            "python/tests/test_b.py::test_2": 2.0,
            "python/djust/tests/test_c.py::test_3": 3.0,
            "tests/test_d.py::test_4": 4.5,
        }

    def test_output_is_pytest_splits_own_on_disk_shape(self, tmp_path: Path) -> None:
        """Sorted keys, indent 4, trailing newline — so a regenerated file
        diffs line-by-line against the committed one instead of as one blob.
        """
        out = tmp_path / "merged.json"
        assert mtd.main([*[str(p) for p in _shards(tmp_path)], "-o", str(out)]) == 0
        text = out.read_text()
        assert text.endswith("}\n")
        keys = [ln.split('"')[1] for ln in text.splitlines() if ln.startswith("    ")]
        assert keys == sorted(keys)


class TestRefusals:
    def test_a_duplicate_id_across_shards_is_refused(self, tmp_path: Path) -> None:
        """Shards are disjoint by construction; an overlap means the deal
        changed mid-run, so neither recorded value describes the run."""
        shards = _shards(tmp_path)
        _write(tmp_path, "s4.json", {"tests/test_a.py::test_1": 9.0})
        out = tmp_path / "merged.json"
        rc = mtd.main([*[str(p) for p in shards], "-o", str(out)])
        assert rc == 1
        assert not out.exists()

    def test_fewer_than_four_inputs_is_refused(self, tmp_path: Path) -> None:
        """A cancelled shard leaves a quarter of the suite unrecorded, and
        pytest-split count-balances exactly those ids."""
        shards = _shards(tmp_path)[:3]
        out = tmp_path / "merged.json"
        assert mtd.main([*[str(p) for p in shards], "-o", str(out)]) == 1
        assert not out.exists()

    def test_expect_override_allows_a_deliberate_partial(self, tmp_path: Path) -> None:
        shards = _shards(tmp_path)[:2]
        out = tmp_path / "merged.json"
        assert mtd.main([*[str(p) for p in shards], "-o", str(out), "--expect", "2"]) == 0
        assert len(json.loads(out.read_text())) == 2

    @pytest.mark.parametrize(
        "bad",
        [
            {"docs/test_x.py::test_1": 1.0},  # outside the three CI roots
            {"tests/test_x.py::test_1": -1.0},  # not a duration
            {"tests/test_x.py::test_1": "1.0"},  # not a number
            {"tests/test_x.py::test_1": True},  # bool is an int in Python
            {},  # empty: a run that recorded nothing
        ],
    )
    def test_a_file_that_is_not_a_durations_file_is_refused(
        self, tmp_path: Path, bad: dict
    ) -> None:
        shards = _shards(tmp_path)
        _write(tmp_path, "s4.json", bad)
        out = tmp_path / "merged.json"
        assert mtd.main([*[str(p) for p in shards], "-o", str(out)]) == 1
        assert not out.exists()

    def test_an_unreadable_input_is_refused_not_skipped(self, tmp_path: Path) -> None:
        shards = _shards(tmp_path)
        (tmp_path / "s4.json").write_text("{not json", encoding="utf-8")
        out = tmp_path / "merged.json"
        assert mtd.main([*[str(p) for p in shards], "-o", str(out)]) == 1
        assert not out.exists()


class TestAgainstTheCommittedFile:
    def test_the_committed_file_passes_the_same_validation(self) -> None:
        """The merger's idea of a durations file and the repo's must agree, or
        `make test-durations-from-ci` writes something the shard pins reject.
        """
        data = mtd.load(ROOT / ".test_durations")
        assert len(data) > 1000, len(data)
