"""The test files that state ``exposure_policy = "legacy"`` match the ledger.

``tests/legacy_exposure_ledger.py`` records every test file that still states
the legacy exposure policy, and why (#4082). Every count here is derived from
the tree at run time; the ledger holds names, never totals.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from tests.legacy_exposure_ledger import (
    MIGRATED_4082,
    MIGRATED_4185,
    MIGRATED_STEP3,
    OTHER_LEGACY_FILES,
    REASONED_INLINE,
    REMAINING_4082,
)

ROOT = Path(__file__).resolve().parents[2]
TEST_ROOTS = ("python/djust/tests", "python/tests", "tests")
#: A class body (``exposure_policy = "legacy"``) or a ``type(...)`` dict
#: (``"exposure_policy": "legacy"``) statement.
LEGACY = re.compile(
    r"""exposure_policy\s*=\s*["']legacy["']|["']exposure_policy["']\s*:\s*["']legacy["']"""
)
REASON = re.compile(r"#\s*(legacy subject:|gap #\d+:)")
#: The ledger and this test quote the statement; neither defines a view.
SELF = {"tests/legacy_exposure_ledger.py", "tests/unit/test_legacy_exposure_ledger.py"}


def _legacy_lines(path: str) -> list[str]:
    text = (ROOT / path).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if LEGACY.search(line)]


def _tree_files_stating_legacy() -> set[str]:
    found = set()
    for top in TEST_ROOTS:
        for path in (ROOT / top).rglob("*.py"):
            rel = path.relative_to(ROOT).as_posix()
            if rel not in SELF and _legacy_lines(rel):
                found.add(rel)
    return found


def test_the_scan_finds_what_it_claims_to_find():
    """The pattern matches both statement forms and nothing else."""
    assert LEGACY.search('    exposure_policy = "legacy"')
    assert LEGACY.search("    exposure_policy='legacy'  # legacy subject: x")
    assert LEGACY.search('{"exposure_policy": "legacy", "template": "x"}')
    assert not LEGACY.search('    exposure_policy = "explicit"')
    assert REASON.search('exposure_policy = "legacy"  # legacy subject: why')
    assert REASON.search('exposure_policy = "legacy"  # gap #4149: why')
    assert not REASON.search('exposure_policy = "legacy"  # why')
    # The scan reaches every test root (a root it missed would read as clean).
    found = _tree_files_stating_legacy()
    for top in TEST_ROOTS:
        assert any(path.startswith(top + "/") for path in found), top


def test_every_file_stating_legacy_is_in_the_ledger_and_no_more():
    tree = _tree_files_stating_legacy()
    ledger = set(REMAINING_4082) | set(OTHER_LEGACY_FILES)
    started = sorted(tree - ledger)
    stopped = sorted(ledger - tree)
    assert not started, (
        "These test files state exposure_policy = 'legacy' but are not in "
        f"tests/legacy_exposure_ledger.py: {started}. A new test view states "
        "'explicit'; a test OF the legacy policy is added there with its reason."
    )
    assert not stopped, (
        "These ledger entries no longer state the legacy policy: "
        f"{stopped}. Remove them from tests/legacy_exposure_ledger.py "
        "(a #4082 file moves to MIGRATED_4082, a step-3 file to MIGRATED_STEP3)."
    )


def test_migrated_4082_files_state_no_legacy_policy():
    for path in MIGRATED_4082:
        assert (ROOT / path).is_file(), path
        assert not _legacy_lines(path), f"{path} was migrated to explicit (#4082)"
    assert not set(MIGRATED_4082) & set(REMAINING_4082)
    assert not set(MIGRATED_4082) & set(OTHER_LEGACY_FILES)
    assert not set(REMAINING_4082) & set(OTHER_LEGACY_FILES)


def test_migrated_step3_files_state_no_legacy_policy():
    """A file the step-3 migration (#4171) ran as explicit stays explicit, and is
    recorded once."""
    for path in MIGRATED_STEP3:
        assert (ROOT / path).is_file(), path
        assert not _legacy_lines(path), f"{path} was migrated to explicit (#4171 step 3)"
    assert len(set(MIGRATED_STEP3)) == len(MIGRATED_STEP3)
    others = set(MIGRATED_4082) | set(REMAINING_4082) | set(OTHER_LEGACY_FILES)
    assert not set(MIGRATED_STEP3) & others


def test_migrated_4185_files_state_no_legacy_policy():
    for path in MIGRATED_4185:
        assert (ROOT / path).is_file(), path
        assert not _legacy_lines(path), f"{path} was migrated to explicit (#4185)"
    ledger = set(MIGRATED_4082) | set(REMAINING_4082) | set(OTHER_LEGACY_FILES)
    assert not set(MIGRATED_4185) & ledger


def test_every_remaining_4082_file_has_a_reason():
    for path, reason in REMAINING_4082.items():
        assert reason.strip(), path


def test_kept_statements_carry_their_reason_inline():
    """In a file kept (wholly or partly) on purpose, each legacy statement says why."""
    for path in REASONED_INLINE:
        assert path in REMAINING_4082, path
        lines = _legacy_lines(path)
        assert lines, path
        bare = [line.strip() for line in lines if not REASON.search(line)]
        assert not bare, f"{path}: legacy statements without a reason: {bare}"


# --------------------------------------------------------------------------- #
# Inherited policy: a view subclassed from another test module
# --------------------------------------------------------------------------- #


def _assigns_policy(cls: ast.ClassDef) -> bool:
    for statement in cls.body:
        targets = statement.targets if isinstance(statement, ast.Assign) else []
        if isinstance(statement, ast.AnnAssign):
            targets = [statement.target]
        if any(isinstance(t, ast.Name) and t.id == "exposure_policy" for t in targets):
            return True
    return False


def _policy_stating_classes(path: Path) -> set[str]:
    """Top-level classes of ``path`` that state a policy, or inherit one from a
    class of the same module that does."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    stating = {name for name, cls in classes.items() if _assigns_policy(cls)}
    grew = True
    while grew:
        grew = False
        for name, cls in classes.items():
            if name not in stating and any(
                isinstance(base, ast.Name) and base.id in stating for base in cls.bases
            ):
                stating.add(name)
                grew = True
    return stating


def _imported_test_module(importer: Path, node: ast.ImportFrom) -> Path | None:
    """The file of a ``test_*`` module an ``import from`` names, if it is one."""
    module = node.module or ""
    if not module.split(".")[-1].startswith("test_"):
        return None
    parts = module.split(".")
    if node.level:
        base = importer.parent
        for _ in range(node.level - 1):
            base = base.parent
        candidate = base.joinpath(*parts)
    elif parts[0] == "djust":
        candidate = ROOT / "python" / Path(*parts)
    elif parts[0] == "tests":
        candidate = ROOT / Path(*parts)
    else:
        candidate = importer.parent / Path(*parts)
    candidate = candidate.with_suffix(".py")
    return candidate if candidate.is_file() else None


def _inherited_policy_offenders() -> list[str]:
    offenders = []
    cache: dict[Path, set[str]] = {}
    for top in TEST_ROOTS:
        for path in sorted((ROOT / top).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported: dict[str, tuple[Path, str]] = {}
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                source = _imported_test_module(path, node)
                if source is None:
                    continue
                if source not in cache:
                    cache[source] = _policy_stating_classes(source)
                for alias in node.names:
                    if alias.name in cache[source]:
                        imported[alias.asname or alias.name] = (source, alias.name)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef) or _assigns_policy(node):
                    continue
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id in imported:
                        source, name = imported[base.id]
                        offenders.append(
                            f"{path.relative_to(ROOT)}: {node.name} inherits the policy of "
                            f"{source.relative_to(ROOT)}: {name}"
                        )
    return offenders


def test_the_inherited_policy_scan_finds_what_it_claims_to_find(tmp_path):
    """The scan reports a subclass of an imported, policy-stating test view, and
    not one that states its own policy or subclasses a class that states none."""
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "test_source.py").write_text(
        "class View:\n    exposure_policy = 'explicit'\n\n"
        "class Sub(View):\n    pass\n\n"
        "class Helper:\n    pass\n"
    )
    importer = package / "test_importer.py"
    importer.write_text(
        "from .test_source import Helper, Sub, View\n\n"
        "class Silent(View):\n    pass\n\n"
        "class SilentSub(Sub):\n    pass\n\n"
        "class Stated(View):\n    exposure_policy = 'legacy'\n\n"
        "class Plain(Helper):\n    pass\n"
    )
    tree = ast.parse(importer.read_text())
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    source = _imported_test_module(importer, node)
    assert source == package / "test_source.py"
    assert _policy_stating_classes(source) == {"View", "Sub"}
    silent = [
        n.name
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef)
        and not _assigns_policy(n)
        and any(isinstance(b, ast.Name) and b.id in {"View", "Sub"} for b in n.bases)
    ]
    assert silent == ["Silent", "SilentSub"]


def test_no_test_view_inherits_its_policy_from_another_test_module():
    """A view subclassed from another test module's view states its own policy.

    Migrating a module flips the policy its views state; a subclass in another
    file that states none silently flips with it (#4082: the importer of
    ``test_background_html_fallback.BackgroundView`` broke in CI that way), and
    the ledger cannot see it, since that file states nothing.
    """
    offenders = _inherited_policy_offenders()
    assert not offenders, (
        "These test views inherit exposure_policy from a view in another test module; "
        "state it on the subclass so migrating the source cannot flip it unseen: "
        + "; ".join(offenders)
    )
