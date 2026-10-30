#!/usr/bin/env python3
"""Pick the pytest files (and cargo packages) a pushed range can affect (#2526).

The pre-push hook used to run the whole Python suite on every push. The
authoritative run is CI; the hook's job is to catch what the PUSHER's change
breaks, before the round-trip. That is a property of the diff, so select
from it.

Selection, from ``git diff --name-only <from>...<to>``:

(a) every changed or added test file;
(b) for each changed non-test Python module, the test files whose NAME
    contains the module stem or whose TEXT imports it (``from djust.x import
    y`` / ``djust.x.y``);
(c) for every changed file, the test files whose text mentions its basename —
    this is what selects the source-pin tests that read ``renderer.rs`` /
    ``context.rs`` and it caught two broken pins on 2026-09-02; keep it;
(d) the FULL suite when the diff touches shared configuration (any
    ``conftest.py``, ``pyproject.toml``, ``pytest.ini``,
    ``.pre-commit-config.yaml``, this script or the hook wrapper), the
    package roots (``python/djust/__init__.py`` / ``apps.py``), anything
    under ``crates/djust_core/src/`` (the Value/Context core, whose blast
    radius is the whole engine), when the selection would be empty, or when
    any changed path cannot be classified, a test source is unreadable, a
    production module imports a changed module (transitive impact), or
    the branch name matches ``flip|routing|convergence`` (the routing-flip
    rule in CLAUDE.md: those PRs must run every test root).

``DJUST_PREPUSH_TARGETED=1`` (the hook passes the environment through): when
the answer would be FULL ONLY because of transitive impact or a blast-radius
path (the package roots, ``live_view.py`` / ``websocket.py``, a package
``__init__.py``, ``state_backends/`` and the engine crates' ``src/``), print the
direct selection (a)-(c) instead; the waived reasons go to stderr. Every other
FULL stands: harness files, the branch-name rule, unreadable or unclassified
paths, and an empty selection (which the hook runs as the full fast tier). CI
remains the authoritative full run.

Rust: ``--cargo`` prints the ``cargo test`` package arguments — ``-p`` for each
crate whose files changed plus every workspace crate that depends on it, or
``--workspace`` when ``Cargo.toml`` / ``Cargo.lock`` / ``djust_core`` changed.

The selection function is pure (paths in, paths out; the file reader is a
parameter) so tests/unit/test_select_tests.py can exercise it without a git
fixture. Only the CLI touches git.

Usage::

    scripts/select-tests.py [--from REF] [--to REF] [--branch NAME]
    scripts/select-tests.py --cargo [...]

stdout is the selection, one path per line, the single word ``FULL``, or the
single word ``NONE`` when every changed path has no pytest consumer by design
(``NO_PYTEST_CONSUMER_PREFIXES``) (``--workspace`` in cargo mode); the reason
goes to stderr. Exit 0 always —
the caller falls back to the full suite when this script cannot run.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

TEST_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")

# Manual-only test dirs pytest itself never collects (pyproject.toml
# ``addopts``: ``--ignore=tests/playwright``, ``--ignore=tests/js``). That
# ignore only takes effect for pytest's own directory-walk collection — it
# does NOT apply to a node id passed explicitly, so a file under one of these
# dirs, if selected here and handed to pytest as an explicit path, DOES get
# collected and run despite the ignore (#2781). Files here are standalone
# scripts (playwright's own async-run pattern; `npm test` for JS) and must
# never be selected as a "changed test file" — they belong to their own CI
# jobs, not the pytest pre-push/CI run.
MANUAL_ONLY_TEST_DIRS = ("tests/playwright/", "tests/js/")

# Paths no pytest test consumes BY DESIGN: the manual-only test dirs above
# (their own CI jobs / the `npm test` hook) and changelog fragments (validated
# by their own pre-commit hooks). A change here is classified without forcing
# FULL — before this, every push that touched a Playwright test ran all ~49k
# pytest tests via the "shared package/test support" rule. Rule (c) still runs
# for them, so a pytest test that names one is still selected. A range made of
# nothing but these selects no pytest tests at all (CLI prints NONE).
NO_PYTEST_CONSUMER_PREFIXES = MANUAL_ONLY_TEST_DIRS + ("changelog.d/",)

# Any change here means the harness itself changed: nothing narrower is safe.
FULL_SUITE_FILES = frozenset(
    {
        "pyproject.toml",
        "pytest.ini",
        ".pre-commit-config.yaml",
        "scripts/pre-push-pytest.sh",
        "scripts/select-tests.py",
        "uv.lock",
        "Cargo.toml",
        "Cargo.lock",
        ".python-version",
        "Makefile",
        "scripts/run-with-venv-python.sh",
        "scripts/pre-push-cargo-test.sh",
        "scripts/embeddable-python.sh",
        ".test_collected_floor",
        ".test_durations",
        "examples/demo_project/demo_project/settings.py",
    }
)
# Whole-suite blast radius: the package roots and the hot shared modules every
# view goes through. Unlike the harness files above, ``DJUST_PREPUSH_TARGETED=1``
# trades these for the direct selection (see ``select_tests``).
BLAST_RADIUS_FILES = frozenset(
    {
        "python/djust/__init__.py",
        "python/djust/apps.py",
        "python/djust/live_view.py",
        "python/djust/websocket.py",
    }
)
# The whole engine: a change under any of these is exercised by every test that
# renders a template, not only by the tests that name the file (the #2575 review
# measured `filters.rs` selecting 11 of 27 floatformat tests by name/import alone).
# Blast radius too: ``DJUST_PREPUSH_TARGETED=1`` trades them for the direct
# selection.
BLAST_RADIUS_PREFIXES = (
    "crates/djust_core/src/",
    "crates/djust_templates/src/",
    "crates/djust_vdom/src/",
    "python/djust/state_backends/",
)
# Harness: always FULL.
FULL_SUITE_PREFIXES = (".github/workflows/",)

#: The environment switch for the targeted pre-push run.
TARGETED_ENV = "DJUST_PREPUSH_TARGETED"
FULL_SUITE_BRANCH_RE = re.compile(r"flip|routing|convergence", re.IGNORECASE)

# Workspace-wide cargo run when these change.
CARGO_WORKSPACE_FILES = frozenset({"Cargo.toml", "Cargo.lock"})
CARGO_WORKSPACE_CRATES = frozenset({"djust_core"})


@dataclass
class Selection:
    full: bool
    reason: str
    tests: list[str] = field(default_factory=list)


def is_test_file(path: str) -> bool:
    p = PurePosixPath(path)
    if p.suffix != ".py" or not path.startswith(TEST_ROOTS):
        return False
    if path.startswith(MANUAL_ONLY_TEST_DIRS):
        return False
    return p.name.startswith("test_") or p.name.endswith("_test.py")


def module_names(path: str) -> tuple[str, str | None]:
    """``(stem, dotted)`` for a Python source path.

    ``python/djust/mixins/jit.py`` -> ``("jit", "djust.mixins.jit")``;
    ``python/djust/mixins/__init__.py`` -> ``("mixins", "djust.mixins")``;
    ``scripts/check-x.py`` -> ``("check-x", None)``.
    """
    p = PurePosixPath(path)
    parts = list(p.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    stem = parts[-1] if parts else p.stem
    dotted = None
    if parts and parts[0] == "python" and len(parts) > 1:
        dotted = ".".join(parts[1:])
    return stem, dotted


def _import_patterns(dotted: str) -> list[re.Pattern[str]]:
    head, _, last = dotted.rpartition(".")
    pats = [re.compile(r"\b%s\b" % re.escape(dotted))]
    if head:
        pats.append(
            re.compile(r"from\s+%s\s+import\b[^\n]*\b%s\b" % (re.escape(head), re.escape(last)))
        )
    return pats


def blast_radius_reason(path: str) -> str | None:
    """Why ``path`` has whole-suite blast radius, or None (see TARGETED_ENV)."""
    if path in BLAST_RADIUS_FILES:
        return "%s changed" % path
    if path.startswith("python/djust/") and path.endswith("/__init__.py"):
        return "%s changed (shared package)" % path
    if path.startswith(BLAST_RADIUS_PREFIXES):
        return "%s changed (shared runtime/engine)" % path
    return None


def full_suite_trigger(
    changed: Iterable[str], branch: str | None, *, targeted: bool = False
) -> str | None:
    """The reason the whole suite must run, or None.

    With ``targeted``, a blast-radius path (``blast_radius_reason``) is not a
    reason: the caller selects its direct consumers instead. Harness changes,
    the branch-name rule and shared test support always are.
    """
    if branch and FULL_SUITE_BRANCH_RE.search(branch):
        return "branch name %r matches /flip|routing|convergence/" % branch
    for path in changed:
        if path.startswith(NO_PYTEST_CONSUMER_PREFIXES):
            continue
        if path in FULL_SUITE_FILES:
            return "%s changed" % path
        blast = blast_radius_reason(path)
        if blast:
            if targeted:
                continue
            return blast
        if PurePosixPath(path).name == "conftest.py":
            return "%s changed" % path
        name = PurePosixPath(path).name
        if name == "Cargo.toml" or (name.startswith("requirements") and name.endswith(".txt")):
            return "%s changed (shared dependencies)" % path
        if path.endswith("/__init__.py") or (
            path.startswith(TEST_ROOTS) and path.endswith(".py") and not is_test_file(path)
        ):
            return "%s changed (shared package/test support)" % path
        if path.startswith(FULL_SUITE_PREFIXES):
            return "%s changed (shared runtime/engine/harness)" % path
    return None


def imported_modules(path: str, text: str) -> set[str]:
    """Static import targets, including relative imports and from-package names.

    Include imports inside functions and TYPE_CHECKING blocks conservatively.
    Dynamic imports cannot establish isolation; the shared-runtime triggers and
    authoritative full CI remain necessary even for a classified leaf change.
    """
    _, dotted = module_names(path)
    package = (dotted or "").split(".")
    if PurePosixPath(path).name != "__init__.py":
        package = package[:-1]
    targets: set[str] = set()
    for node in ast.walk(ast.parse(text, filename=path)):
        if isinstance(node, ast.Import):
            targets.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = node.module.split(".") if node.module else []
            if node.level:
                parts = package[: len(package) - node.level + 1] + parts
            base = ".".join(parts)
            if base:
                targets.add(base)
                targets.update(base + "." + alias.name for alias in node.names)
    return targets


def select_tests(
    changed: Iterable[str],
    all_tests: Iterable[str],
    read_text: Callable[[str], str],
    branch: str | None = None,
    *,
    all_modules: Iterable[str] = (),
    targeted: bool = False,
) -> Selection:
    """Select files only when every change maps to an existing test.

    The CLI supplies all production modules for the transitive-impact guard.
    If another production module imports a changed one, full coverage is safer
    than trying to infer all indirect test consumers from names/source pins.

    ``targeted`` (``DJUST_PREPUSH_TARGETED=1``): when the answer would be FULL
    ONLY because of transitive impact or a blast-radius path, return the direct
    selection (rules a to c) instead, with the waived reasons in ``reason``.
    Every other FULL (harness, branch name, unreadable or unclassified paths,
    an empty selection) stands. CI remains the authoritative full run.
    """
    changed = sorted(set(changed))
    all_tests = sorted(set(all_tests))
    trigger = full_suite_trigger(changed, branch, targeted=targeted)
    if trigger:
        return Selection(True, trigger)
    waived = [r for r in (blast_radius_reason(p) for p in changed) if r] if targeted else []
    if not changed:
        return Selection(True, "no changed files in the range")

    changed_modules = {
        dotted
        for path in changed
        if path.endswith(".py") and not is_test_file(path)
        for _, dotted in [module_names(path)]
        if dotted
    }
    if changed_modules:
        transitive: str | None = None
        for module in sorted(set(all_modules)):
            try:
                imports = imported_modules(module, read_text(module))
            except (OSError, SyntaxError, UnicodeError) as exc:
                return Selection(True, "%s: import graph unavailable (%s)" % (module, exc))
            _, own_name = module_names(module)
            for changed_name in sorted(changed_modules):
                if own_name != changed_name and any(
                    name == changed_name or name.startswith(changed_name + ".") for name in imports
                ):
                    transitive = transitive or "%s imports %s (transitive impact)" % (
                        module,
                        changed_name,
                    )
            if transitive and not targeted:
                return Selection(True, transitive)
        if transitive:
            waived.append(transitive)

    chosen: dict[str, str] = {}
    existing = set(all_tests)
    classified: set[str] = set()

    # (a) changed test files.
    for path in changed:
        if is_test_file(path) and path in existing:
            chosen.setdefault(path, "changed test")
            classified.add(path)

    # (b) modules: name contains the stem, or text imports it.
    # (c) every file: text mentions its basename.
    texts: dict[str, str] = {}

    def text_of(t: str) -> str:
        if t not in texts:
            texts[t] = read_text(t)
        return texts[t]

    # A read failure may hide another consumer, even when a filename matched.
    for test in all_tests:
        try:
            text_of(test)
        except (OSError, UnicodeError) as exc:
            return Selection(True, "%s: unreadable test source (%s)" % (test, exc))

    for path in changed:
        base = PurePosixPath(path).name
        base_re = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(base))
        is_module = path.endswith(".py") and not is_test_file(path)
        stem, dotted = module_names(path) if is_module else (None, None)
        import_res = _import_patterns(dotted) if dotted else []
        for t in all_tests:
            tname = PurePosixPath(t).name
            if stem and len(stem) >= 3 and stem in tname:
                chosen[t] = "name contains %r (%s)" % (stem, path)
                classified.add(path)
                continue
            text = text_of(t)
            if base_re.search(text):
                chosen[t] = "mentions %s" % base
                classified.add(path)
                continue
            if any(r.search(text) for r in import_res):
                chosen[t] = "imports %s" % dotted
                classified.add(path)

    no_consumer = {p for p in changed if p.startswith(NO_PYTEST_CONSUMER_PREFIXES)}
    classified |= no_consumer
    if not chosen:
        if no_consumer == set(changed):
            return Selection(
                False,
                "no pytest consumer for %d changed file(s) "
                "(manual-only tests / changelog fragments)" % len(changed),
            )
        return Selection(True, "selection is empty for %d changed file(s)" % len(changed))
    unclassified = sorted(set(changed) - classified)
    if unclassified:
        return Selection(True, "unclassified changed file(s): " + ", ".join(unclassified))
    tests = sorted(chosen)
    reason = "%d test file(s) selected for %d changed file(s)" % (len(tests), len(changed))
    if waived:
        reason += "; %s=1: direct selection instead of FULL for: %s" % (
            TARGETED_ENV,
            "; ".join(waived),
        )
    return Selection(False, reason, tests)


# --------------------------------------------------------------------------
# cargo
# --------------------------------------------------------------------------


def crate_of(path: str) -> str | None:
    parts = PurePosixPath(path).parts
    if len(parts) >= 2 and parts[0] == "crates":
        return parts[1]
    return None


def select_crates(
    changed: Iterable[str], reverse_deps: dict[str, set[str]]
) -> tuple[bool, str, list[str]]:
    """``(workspace, reason, packages)``; ``reverse_deps[c]`` = crates that depend on c."""
    crates: set[str] = set()
    for path in sorted(set(changed)):
        if path in CARGO_WORKSPACE_FILES:
            return True, "%s changed" % path, []
        c = crate_of(path)
        if c is None:
            continue
        if c in CARGO_WORKSPACE_CRATES:
            return True, "%s changed" % path, []
        crates.add(c)
    if not crates:
        return False, "no crate files changed", []
    closure = set(crates)
    frontier = list(crates)
    while frontier:
        c = frontier.pop()
        for d in reverse_deps.get(c, ()):
            if d not in closure:
                closure.add(d)
                frontier.append(d)
    return False, "crates %s (+ dependents)" % ", ".join(sorted(crates)), sorted(closure)


def read_reverse_deps(root: Path) -> dict[str, set[str]]:
    """Parse ``djust_x = { path = "../djust_x" }`` lines from crates/*/Cargo.toml."""
    rev: dict[str, set[str]] = {}
    for toml in sorted(root.glob("crates/*/Cargo.toml")):
        crate = toml.parent.name
        for m in re.finditer(r"^(djust_\w+)\s*=\s*\{[^}]*path\s*=", toml.read_text(), re.M):
            rev.setdefault(m.group(1), set()).add(crate)
    return rev


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout.strip()


def _range(from_ref: str | None, to_ref: str | None) -> tuple[str, str]:
    """The pushed range: pre-commit's refs when set, else merge-base..HEAD."""
    to = to_ref or "HEAD"
    if from_ref and set(from_ref) != {"0"}:
        try:
            _git("rev-parse", "--verify", "--quiet", from_ref + "^{commit}")
            return from_ref, to
        except subprocess.CalledProcessError:
            pass
    base = (
        _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD").removeprefix("origin/")
        if _has_origin_head()
        else "main"
    )
    return "origin/%s" % base, to


def _has_origin_head() -> bool:
    try:
        _git("symbolic-ref", "--short", "refs/remotes/origin/HEAD")
        return True
    except subprocess.CalledProcessError:
        return False


def _changed_files(from_ref: str, to_ref: str) -> list[str]:
    out = _git("diff", "--name-only", "%s...%s" % (from_ref, to_ref))
    return [line for line in out.splitlines() if line]


def _all_test_files(root: Path) -> list[str]:
    found: list[str] = []
    for tr in TEST_ROOTS:
        for dirpath, dirnames, filenames in os.walk(root / tr):
            dirnames[:] = [d for d in dirnames if d not in {"__pycache__", "node_modules"}]
            for f in filenames:
                rel = str(Path(dirpath, f).relative_to(root).as_posix())
                if is_test_file(rel):
                    found.append(rel)
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="from_ref", default=os.environ.get("PRE_COMMIT_FROM_REF"))
    ap.add_argument("--to", dest="to_ref", default=os.environ.get("PRE_COMMIT_TO_REF"))
    ap.add_argument("--branch", default=None, help="branch name (default: current)")
    ap.add_argument("--cargo", action="store_true", help="print cargo test package args instead")
    args = ap.parse_args(argv)

    root = Path(_git("rev-parse", "--show-toplevel"))
    os.chdir(root)
    from_ref, to_ref = _range(args.from_ref, args.to_ref)
    changed = _changed_files(from_ref, to_ref)
    print(
        "select-tests: range %s...%s, %d changed file(s)" % (from_ref, to_ref, len(changed)),
        file=sys.stderr,
    )

    if args.cargo:
        workspace, reason, packages = select_crates(changed, read_reverse_deps(root))
        print("select-tests: cargo -> %s" % reason, file=sys.stderr)
        if workspace:
            print("--workspace")
        else:
            print(" ".join("-p %s" % p for p in packages))
        return 0

    branch = args.branch
    if branch is None:
        try:
            branch = _git("symbolic-ref", "--short", "HEAD")
        except subprocess.CalledProcessError:
            branch = ""
    sel = select_tests(
        changed,
        _all_test_files(root),
        lambda p: (root / p).read_text(),
        branch,
        all_modules=[
            str(path.relative_to(root).as_posix())
            for path in (root / "python/djust").rglob("*.py")
            if not path.relative_to(root).as_posix().startswith("python/djust/tests/")
        ],
        targeted=os.environ.get(TARGETED_ENV) == "1",
    )
    if sel.full:
        print("select-tests: FULL suite — %s" % sel.reason, file=sys.stderr)
        print("FULL")
    elif not sel.tests:
        print("select-tests: NONE — %s" % sel.reason, file=sys.stderr)
        print("NONE")
    else:
        print("select-tests: %s" % sel.reason, file=sys.stderr)
        for t in sel.tests:
            print("    " + t, file=sys.stderr)
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
