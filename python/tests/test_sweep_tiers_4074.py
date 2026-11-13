"""The exhaustive sweeps lose no coverage by being sampled before merge (#4074).

``tests/sweep_sample.py`` runs the security sweeps in full nightly and on a seeded sample before
merge. Sampling is only safe while:

* a sampled selection is never empty, and over successive seeds it reaches every unit;
* the full populations do not shrink (a refactor that drops classes or mutants from a sweep must
  lower a number here, in review, rather than quietly sweep less);
* every slow test in a sweep file is in exactly one tier: sampled (``sweep``), full nightly only
  (``nightly``), or a structural pin that runs in full on every pull request (``pr_tier``);
* the workflows still run the full sweep somewhere: the nightly ``main-health`` suite passes no
  ``-m`` and no ``DJUST_SWEEP_SAMPLE``, so it is full.
"""

from __future__ import annotations

import ast
import json
import re
from functools import lru_cache
from pathlib import Path

import pytest

from tests import sweep_sample

ROOT = Path(__file__).resolve().parents[2]

#: sweep file -> its importable module name
SWEEP_FILES = {
    "python/djust/tests/test_marked_safe_attribute_sweep_3898.py": (
        "djust.tests.test_marked_safe_attribute_sweep_3898"
    ),
    "python/tests/test_safe_markup_audit_3774.py": "test_safe_markup_audit_3774",
    "python/tests/test_safe_markup_guard_3820.py": "test_safe_markup_guard_3820",
}

#: The ratchet: each full population is at least this big. Raise a number when a sweep grows;
#: lowering one is a decision to sweep less and belongs in the PR that does it.
POPULATION_FLOORS = {
    "marked-safe/classes": 212,
    "marked-safe/tags": 18,
    "marked-safe/item-parameters": 43,
    # 434 before 2.0 removed the Rust-native handlers that had a Python twin (train/2.0)
    "payload-sweep/units": 275,
    "guard-fuzz/seeds": 3,
    "guard-fuzz/mutants": 26,
}

TIERS = ("sweep", "nightly", "pr_tier")


@pytest.fixture
def restore_sampler():
    saved = dict(sweep_sample._state)
    yield
    sweep_sample._state.clear()
    sweep_sample._state.update(saved)


@lru_cache(maxsize=None)
def _populations() -> dict[str, list[str]]:
    import importlib

    out: dict[str, list[str]] = {}
    for module_name in SWEEP_FILES.values():
        module = importlib.import_module(module_name)
        for name, units in module.SWEEP_POPULATIONS.items():
            out[name] = list(units())
    return out


# --- the sampler -------------------------------------------------------------------------------


class TestTheSampler:
    UNITS = [f"u{i}" for i in range(40)]

    def test_full_mode_selects_everything(self, restore_sampler):
        sweep_sample.configure(sampled=False)
        assert sweep_sample.select("p", self.UNITS, budget=2) == self.UNITS

    def test_a_sample_is_the_budget_plus_the_pinned_in_order(self, restore_sampler):
        sweep_sample.configure(sampled=True, seed="s")
        chosen = sweep_sample.select("p", self.UNITS, budget=3, always=["u39"])
        assert len(chosen) == 4 and "u39" in chosen
        assert chosen == [u for u in self.UNITS if u in chosen]

    def test_the_same_seed_draws_the_same_sample(self, restore_sampler):
        sweep_sample.configure(sampled=True, seed="s")
        first = sweep_sample.select("p", self.UNITS, budget=5)
        sweep_sample.configure(sampled=True, seed="s")
        assert sweep_sample.select("p", self.UNITS, budget=5) == first

    def test_successive_seeds_reach_every_unit(self, restore_sampler):
        seen: set[str] = set()
        for n in range(200):
            sweep_sample.configure(sampled=True, seed=f"sha{n}")
            seen.update(sweep_sample.select("p", self.UNITS, budget=2))
        assert seen == set(self.UNITS)

    def test_a_changed_path_forces_its_units(self, restore_sampler):
        sweep_sample.configure(sampled=True, seed="s", changed=["a/u7.py"])
        chosen = sweep_sample.select("p", self.UNITS, budget=0, paths=lambda u: [f"a/{u}.py"])
        assert chosen == ["u7"]

    def test_a_changed_shared_path_drives_every_unit(self, restore_sampler):
        sweep_sample.configure(sampled=True, seed="s", changed=["lib/core.py"])
        assert sweep_sample.select("p", self.UNITS, budget=0, shared=["lib/core.py"]) == self.UNITS

    def test_the_floor_scales_with_the_sample_and_is_whole_when_full(self, restore_sampler):
        sweep_sample.configure(sampled=False)
        assert sweep_sample.floor(1000, 5, 100) == 1000
        sweep_sample.configure(sampled=True, seed="s")
        assert sweep_sample.floor(1000, 10, 100) == 50
        assert sweep_sample.floor(1000, 100, 100) == 1000

    def test_changed_paths_come_from_a_list_or_a_file(self, tmp_path):
        listing = tmp_path / "changed.txt"
        listing.write_text("a/x.py\n./b/y.py\n\n")
        assert sweep_sample._read_changed(None, str(listing)) == {"a/x.py", "b/y.py"}
        # a listed path that happens to exist is a path, not a listing (the first version read it)
        assert sweep_sample._read_changed(str(listing), None) == {str(listing)}
        assert sweep_sample._read_changed("a/x.py,b/y.py", None) == {"a/x.py", "b/y.py"}

    def test_the_tier_turns_sampling_on(self):
        assert sweep_sample._excludes_slow("not nightly and not slow")
        assert not sweep_sample._excludes_slow("not nightly")
        assert not sweep_sample._excludes_slow("")

    def test_the_repro_command_names_the_seed_and_the_test(self, restore_sampler):
        sweep_sample.configure(sampled=True, seed="abc123")
        cmd = sweep_sample.repro_command("python/tests/x.py::test_y[1]")
        assert "DJUST_SWEEP_SAMPLE=1" in cmd and "DJUST_SWEEP_SEED=abc123" in cmd
        assert "'python/tests/x.py::test_y[1]'" in cmd


# --- the real sweeps ---------------------------------------------------------------------------


class TestThePopulations:
    def test_every_population_is_ratcheted(self):
        assert set(_populations()) == set(POPULATION_FLOORS)

    @pytest.mark.parametrize("name", sorted(POPULATION_FLOORS))
    def test_the_full_population_did_not_shrink(self, name):
        units = _populations()[name]
        assert len(units) >= POPULATION_FLOORS[name], (
            f"{name} sweeps {len(units)} units, fewer than the {POPULATION_FLOORS[name]} pinned in "
            "POPULATION_FLOORS: the full sweep shrank. If that is intended, lower the number here."
        )
        assert len(set(units)) == len(units), f"{name}: duplicate unit names"

    @pytest.mark.parametrize("name", sorted(POPULATION_FLOORS))
    def test_a_sampled_selection_is_never_empty(self, name, restore_sampler):
        units = _populations()[name]
        for n in range(5):
            sweep_sample.configure(sampled=True, seed=f"seed{n}")
            assert sweep_sample.select(name, units, budget=1), name


# --- the tiers ---------------------------------------------------------------------------------


def _mark_names(decorators: list[ast.expr]) -> dict[str, ast.expr]:
    out = {}
    for d in decorators:
        target = d.func if isinstance(d, ast.Call) else d
        text = ast.unparse(target)
        m = re.fullmatch(r"pytest\.mark\.(\w+)", text)
        if m:
            out[m.group(1)] = d
    return out


def _tests(path: str, root: Path = ROOT):
    """``(test id without params, {tier: decorator})`` with marks inherited from class and module."""
    tree = ast.parse((root / path).read_text())
    module_marks: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "pytestmark" for t in node.targets
        ):
            values = node.value.elts if isinstance(node.value, ast.List) else [node.value]
            module_marks.update(_mark_names(values))

    def walk(body, prefix, inherited):
        for node in body:
            if isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
                yield from walk(
                    node.body,
                    prefix + node.name + "::",
                    {**inherited, **_mark_names(node.decorator_list)},
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test_"
            ):
                marks = {**inherited, **_mark_names(node.decorator_list)}
                yield prefix + node.name, {k: v for k, v in marks.items() if k in TIERS}

    yield from walk(tree.body, f"{path}::", module_marks)


#: The committed durations, read once at import: each CI xdist worker rewrites the live file with
#: only its own tests (tests/test_ci_python_test_shards.py), so a read inside a test can see a subset.
_DURATIONS_TEXT = (ROOT / ".test_durations").read_text()


def _recorded() -> dict[str, float]:
    """Recorded seconds of the slowest case of each test function (what ``slow_tier`` judges)."""
    out: dict[str, float] = {}
    for nodeid, seconds in json.loads(_DURATIONS_TEXT).items():
        base = nodeid.split("[", 1)[0]
        out[base] = max(out.get(base, 0.0), seconds)
    return out


@pytest.mark.parametrize("path", sorted(SWEEP_FILES))
def test_every_slow_test_in_a_sweep_file_has_exactly_one_tier(path):
    recorded = _recorded()
    wrong = []
    for test_id, tiers in _tests(path):
        if recorded.get(test_id, 0.0) > 1.0 and len(tiers) != 1:
            wrong.append(f"{test_id}: {sorted(tiers) or 'no tier'}")
    assert not wrong, (
        "a slow test in a sweep file must be @pytest.mark.sweep(<population>) (sampled before merge,"
        " full nightly), @pytest.mark.nightly (full only) or @pytest.mark.pr_tier (a structural pin,"
        " full on every PR) -- otherwise the PR tier never runs it at all (#4074):\n  "
        + "\n  ".join(wrong)
    )


@pytest.mark.parametrize("path", sorted(SWEEP_FILES))
def test_every_sweep_mark_names_a_ratcheted_population(path):
    named = set()
    for test_id, tiers in _tests(path):
        if "sweep" in tiers:
            mark = tiers["sweep"]
            assert isinstance(mark, ast.Call) and mark.args, (
                f"{test_id}: sweep() needs a population"
            )
            named.add(ast.literal_eval(mark.args[0]))
    assert named, f"{path} has no sweep test"
    assert named <= set(POPULATION_FLOORS), sorted(named - set(POPULATION_FLOORS))


def test_every_population_is_swept_by_some_test():
    named = {
        ast.literal_eval(tiers["sweep"].args[0])
        for path in SWEEP_FILES
        for _test_id, tiers in _tests(path)
        if "sweep" in tiers
    }
    assert named == set(POPULATION_FLOORS)


def test_the_tier_check_can_fail(tmp_path):
    """Non-vacuous: a test with no tier, and one with two, are both seen as such."""
    rel = "python/tests/_tier_canary.py"
    (tmp_path / "python/tests").mkdir(parents=True)
    (tmp_path / rel).write_text(
        "import pytest\n"
        "def test_none():\n    pass\n"
        "@pytest.mark.pr_tier\n@pytest.mark.sweep('x')\ndef test_two():\n    pass\n"
        "@pytest.mark.nightly\ndef test_one():\n    pass\n"
    )
    found = dict(_tests(rel, tmp_path))
    assert found[f"{rel}::test_none"] == {}
    assert sorted(found[f"{rel}::test_two"]) == ["pr_tier", "sweep"]
    assert sorted(found[f"{rel}::test_one"]) == ["nightly"]


# --- the workflows still run the full sweep, and sample before merge --------------------------


def test_the_nightly_suite_runs_every_unit():
    text = (ROOT / ".github/workflows/main-health.yml").read_text()
    run = text.split("name: Run the suite", 1)[1].split("\n  report:", 1)[0]
    assert "python -m pytest tests/ python/tests/ python/djust/tests/" in run
    assert not re.search(r"pytest\b[^\n]*\s-m\s", run), "the nightly suite must not deselect a tier"
    suite = text.split("\n  suite:", 1)[1].split("\n  matrix:", 1)[0]
    assert "DJUST_SWEEP_SAMPLE" not in suite, "the nightly serial suite must sweep in full"


def test_ci_samples_on_the_push_to_main_and_says_which_seed():
    text = (ROOT / ".github/workflows/test.yml").read_text()
    assert "DJUST_SWEEP_SAMPLE:" in text and "DJUST_SWEEP_SEED:" in text
    assert "DJUST_SWEEP_CHANGED_FILE:" in text
