"""``make merge-gates`` names every whole-tree meta test, and a new one must join it (#3757).

A cross-file gate (a ratchet over every test file, shard coverage, a settings-leak scan, a
git-environment scan) passes on a PR's own tree and can fail on the merge of two PRs, because
each PR changed the tree the other's gate reads. The one-PR-at-a-time rule says to run those gates
on a scratch merge of the PR with main. The list used to be hand-picked each time; main went red
on ``a54278ebd`` because ``tests/test_git_env_guard_3179.py`` was not on it.

The list now lives in one place, ``MERGE_GATES`` in the ``Makefile`` (read here, not restated),
and this module keeps it complete:

* ``scans_the_test_tree`` finds, by AST, every test module that walks the tree
  (``rglob`` / ``glob`` / ``os.walk`` / ``iterdir``) and names the test tree it walks: a test root,
  a ``test_*.py`` glob, or a ``startswith("test_")`` filter.
* every such module is in ``MERGE_GATES`` or in ``EXCLUDED`` below with the reason it is not a
  cross-file gate, so a NEW tree scanner is a visible decision rather than a silent omission;
* ``NOT_DETECTABLE`` lists the gates that are cross-file but reach the tree another way (a
  subprocess over ``scripts/``, a workflow file), so the detector cannot see them; an entry that
  the detector DOES see is stale and fails.

What the detector does not see: a gate that reads the tree without a walker and without naming
a test root. That is why ``NOT_DETECTABLE`` exists and why adding a gate by hand is always allowed.
"""

from __future__ import annotations

import ast
import functools
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ROOTS = ("tests", "python/tests", "python/djust/tests")
SKIP_PARTS = {"node_modules", "static", ".venv", "target", "__pycache__", "worktrees"}

WALKERS = {"rglob", "glob", "walk", "iterdir"}
TEST_TREE_NAMES = {"tests", "python/tests", "python/djust/tests", "test_*.py", "*_test.py"}

#: Cross-file gates the detector cannot see: they reach the tree through a subprocess over
#: ``scripts/`` or a workflow file, not a walker in the module.
NOT_DETECTABLE = {
    "tests/test_ci_python_test_shards.py": (
        "runs scripts/collect-test-shards.py, which walks every test root, against the CI "
        "workflow and .test_durations: a PR that adds a test file moves the shards another "
        "PR's durations were cut for"
    ),
    "python/tests/test_safe_markup_gate_3848.py": (
        "runs the safe-markup guard over python/djust and reconciles it with the committed audit "
        "baseline: a PR that adds an interpolated mark_safe / _safe / safe_html, or a class that "
        "writes its constructor keywords with setattr, changes what another PR's baseline must hold "
        "(#3848)"
    ),
    "python/tests/test_template_library_parity_3663.py": (
        "runs scripts/template-library-parity.py over the whole template corpus against a "
        "committed baseline CSV that every parity PR edits (#3757 names it)"
    ),
}

_SOURCE = (
    "walks djust's own package source, skipping its tests/: it can only collide with a PR that "
    "adds violating SOURCE, not with the new test file nearly every PR adds. A judgement, not a "
    "proof: move it into MERGE_GATES the first time it goes red on main after a merge."
)

#: Modules the detector flags that are NOT cross-file gates, each with its reason. These walk
#: ``djust``'s own source, a fixed fixture directory, or a temporary tree a test built: nothing a
#: different PR adds under the test roots moves what they read.
EXCLUDED: dict[str, str] = {
    "python/djust/tests/test_free_threaded_class_caches_3151.py": _SOURCE,
    "python/djust/tests/test_log_exposure_pin.py": _SOURCE,
    "python/djust/tests/test_module_getattr_hooks.py": _SOURCE,
    "python/djust/tests/test_property_shadow_parity_2614.py": _SOURCE,
    "python/djust/tests/test_template_auto_call_1985.py": _SOURCE,
    "python/tests/test_decimal_converters_2239.py": _SOURCE,
    "python/tests/test_decimal_state_tag_2252.py": _SOURCE,
    "python/tests/test_passthrough_attr_policy_3818.py": _SOURCE,
    "python/tests/test_pwa_tag_template_injection_3824.py": _SOURCE,
    "python/tests/test_query_budget_review_3722.py": _SOURCE,
    "python/tests/test_render_call_guard_3815.py": _SOURCE,
    "tests/test_red_main_attribution_behaviour_2139.py": (
        "builds a throwaway repository and globs the Rust extension inside it"
    ),
    "tests/unit/test_egress_normalizer_rust_parity.py": (
        "reads the fixed tests/js/fixtures/*.json corpus and the templates, not test modules"
    ),
    "tests/unit/test_gate_off_3714.py": ("iterates temporary directories the test itself created"),
    "tests/unit/test_gate_off_batch_3905.py": (
        "iterates the temporary fixture tree the test itself created"
    ),
}


def _call_name(node: ast.Call) -> str:
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def scans_the_test_tree(source: str) -> bool:
    """Does this module walk a directory tree and name the test tree it walks?

    A walker call, plus a test-root string (not in a comparison, not a docstring), a
    ``test_*.py`` glob, or a ``startswith("test_")`` filter.
    """
    tree = ast.parse(source)
    walks = names_tests = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in WALKERS:
                walks = True
            if (
                name == "startswith"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and str(node.args[0].value).startswith("test_")
            ):
                names_tests = True
        if isinstance(node, (ast.Compare, ast.Expr)):
            continue
        for child in ast.iter_child_nodes(node):
            if (
                isinstance(child, ast.Constant)
                and isinstance(child.value, str)
                and child.value in TEST_TREE_NAMES
            ):
                names_tests = True
    return walks and names_tests


def make_merge_gates() -> list[str]:
    """The ``MERGE_GATES := ...`` list in the Makefile, in order."""
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    match = re.search(r"^MERGE_GATES\s*:=((?:.*\\\n)*.*)$", text, re.MULTILINE)
    assert match, "Makefile has no `MERGE_GATES :=` list (#3757)"
    return match.group(1).replace("\\\n", " ").split()


def _test_modules() -> list[Path]:
    found = []
    for root in ROOTS:
        for path in (REPO / root).rglob("test_*.py"):
            if SKIP_PARTS & set(path.relative_to(REPO).parts):
                continue
            found.append(path)
    return sorted(found)


@functools.lru_cache(maxsize=1)
def _detected() -> frozenset[str]:
    out = set()
    for path in _test_modules():
        try:
            if scans_the_test_tree(path.read_text(encoding="utf-8")):
                out.add(path.relative_to(REPO).as_posix())
        except SyntaxError:
            continue
    return frozenset(out)


def detected() -> set[str]:
    return set(_detected())


# --------------------------------------------------------------------------- #
# The set is complete
# --------------------------------------------------------------------------- #


def unaccounted(flagged: set[str], gates: set[str], excluded: set[str]) -> list[str]:
    """Flagged modules that are neither a gate nor explicitly excluded."""
    return sorted(flagged - gates - excluded)


def test_every_tree_scanning_test_module_is_a_merge_gate_or_excluded_with_a_reason():
    missing = unaccounted(detected(), set(make_merge_gates()), set(EXCLUDED))
    assert not missing, (
        "these test modules walk the test tree, so they can pass on a PR's own tree and fail on "
        "the merge of two PRs. Add each to MERGE_GATES in the Makefile, or to EXCLUDED here with "
        f"the reason it is not a cross-file gate (#3757): {missing}"
    )


def test_the_scan_reads_the_modules_it_claims_to():
    assert len(_test_modules()) > 500, "the scan found far fewer test modules than the suite has"
    assert "tests/test_git_env_guard_3179.py" in detected()


def test_every_gate_exists_and_none_is_listed_twice():
    gates = make_merge_gates()
    assert gates, "MERGE_GATES is empty"
    assert len(gates) == len(set(gates)), "a gate is listed twice"
    missing = [g for g in gates if not (REPO / g).is_file()]
    assert not missing, f"MERGE_GATES names files that do not exist: {missing}"


def test_the_gates_are_either_detected_or_declared_not_detectable():
    gates, flagged = set(make_merge_gates()), detected()
    hidden = sorted(gates - flagged - set(NOT_DETECTABLE))
    assert not hidden, (
        "these gates are not found by the detector, so nothing would notice if it stopped "
        f"finding them: declare each in NOT_DETECTABLE with why: {hidden}"
    )


def test_a_not_detectable_entry_the_detector_sees_is_stale_and_every_one_is_a_gate():
    gates, flagged = set(make_merge_gates()), detected()
    assert set(NOT_DETECTABLE) <= gates, "NOT_DETECTABLE names a module that is not a gate"
    assert not (set(NOT_DETECTABLE) & flagged), "the detector now sees these: delete the entries"
    assert all(reason.strip() for reason in NOT_DETECTABLE.values())


def test_every_exclusion_is_a_flagged_module_with_a_reason_and_is_not_also_a_gate():
    gates, flagged = set(make_merge_gates()), detected()
    assert all(reason.strip() for reason in EXCLUDED.values())
    assert not (set(EXCLUDED) & gates), "a module is both a gate and excluded"
    stale = sorted(set(EXCLUDED) - flagged)
    assert not stale, f"these exclusions are no longer flagged by the detector: {stale}"


def test_the_makefile_target_runs_the_list():
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^merge-gates:.*\n\t.*pytest \$\(MERGE_GATES\)", text, re.MULTILINE)


def test_claude_md_names_the_target():
    text = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "make merge-gates" in text


# --------------------------------------------------------------------------- #
# The detector: what it must flag and what it must not
# --------------------------------------------------------------------------- #

FLAGGED = {
    "rglob over the test roots": """
ROOTS = ("tests", "python/tests")
def test_a():
    for root in ROOTS:
        for p in (REPO / root).rglob("*.py"):
            p.read_text()
""",
    "glob of test files": """
def test_a():
    for p in ROOT.glob("test_*.py"):
        pass
""",
    "os.walk with a startswith filter": """
def test_a():
    for _, _, names in os.walk(ROOT):
        for n in names:
            if n.startswith("test_"):
                pass
""",
    "one test root as a path part": """
def test_a():
    for p in (REPO / "python/djust/tests").iterdir():
        pass
""",
}

NOT_FLAGGED = {
    "a walker over source that only skips a tests directory": """
def test_a():
    for p in PKG.rglob("*.py"):
        if "tests" in p.parts:
            continue
""",
    "a test root with no walker": """
ROOTS = ("tests", "python/tests")
def test_a():
    assert ROOTS
""",
    "a walker whose only mention of tests is a docstring": '''
def test_a():
    """Scans tests."""
    for p in PKG.rglob("*.py"):
        pass
''',
    "a walker over a temp dir": """
def test_a(tmp_path):
    assert list(tmp_path.glob("*.json")) == []
""",
}


@pytest.mark.parametrize("source", FLAGGED.values(), ids=FLAGGED.keys())
def test_the_detector_flags(source):
    assert scans_the_test_tree(source)


@pytest.mark.parametrize("source", NOT_FLAGGED.values(), ids=NOT_FLAGGED.keys())
def test_the_detector_leaves_alone(source):
    assert not scans_the_test_tree(source)


def test_a_new_scanner_that_is_in_neither_list_is_reported():
    """The completeness check is not vacuous: a scanner in neither list is what it returns."""
    flagged = detected() | {"tests/test_a_brand_new_scanner.py"}
    assert unaccounted(flagged, set(make_merge_gates()), set(EXCLUDED)) == [
        "tests/test_a_brand_new_scanner.py"
    ]
