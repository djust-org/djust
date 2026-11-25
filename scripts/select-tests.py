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

``--ci``: pull-request CI's selection. Targeted, plus the rules at
``CI_CONFIG_PREFIXES`` (CI configuration, docs and test support select their
consumers; every selection adds the Makefile's ``MERGE_GATES`` and every test
file with a strict xfail, #4259), and FULL when the selection is too big for
the ONE shard CI runs it as (``CI_SHARD_BUDGET_SECONDS``). The merge
queue still runs everything (docs/testing-strategy.md, "Pull-request CI runs
the selection").

Rust: ``--cargo`` prints the ``cargo test`` package arguments — ``-p`` for each
crate whose files changed plus every workspace crate that depends on it, or
``--workspace`` when ``Cargo.toml`` / ``Cargo.lock`` / ``djust_core`` changed.

The selection function is pure (paths in, paths out; the file reader is a
parameter) so tests/unit/test_select_tests.py can exercise it without a git
fixture. Only the CLI touches git.

Usage::

    scripts/select-tests.py [--ci] [--from REF] [--to REF] [--branch NAME]
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
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping
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
        # Not ``.test_durations`` (#4253): it only re-tiers tests and rebalances shards, and the
        # tests that read it name it, so rule (c) selects them.
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
    "crates/djust_base/src/",
    "crates/djust_core/src/",
    "crates/djust_templates/src/",
    "crates/djust_vdom/src/",
    "python/djust/state_backends/",
)
# Harness: always FULL.
FULL_SUITE_PREFIXES = (".github/workflows/",)

#: The environment switch for the targeted pre-push run.
TARGETED_ENV = "DJUST_PREPUSH_TARGETED"

# CI mode (``--ci``): pull-request CI runs the selection instead of the whole
# fast tier, and the merge queue runs the whole fast tier before anything lands,
# so a selection that misses a consumer is caught before merge, one run later.
# CI mode is targeted mode plus three narrowings of what forces FULL:
#
# * CI configuration (``CI_CONFIG_PREFIXES`` / ``CI_CONFIG_FILES``): its pytest
#   consumers are the pin tests that read it, and rule (c) finds them by name.
# * Prose (``DOC_SUFFIXES``): a doc no test names is classified, not unknown.
#   The tests that scan every doc (their text globs ``*.md``) run whenever a
#   doc changed.
# * Test support (a non-test module under a test root, not a ``conftest.py`` or an
#   ``__init__.py``): the tests that name it, directly or through other support
#   modules that do. FULL still, when a ``conftest.py`` or ``pyproject.toml`` names it
#   (a plugin, fixture or settings module every test goes through).
# * Every selection also runs the whole-tree merge gates (``MERGE_GATES`` in the
#   Makefile, #3757), the tests that pass alone and fail together.
# * Every selection also runs every test file with a strict xfail
#   (``STRICT_XFAIL_RE``, #4259): a fix that makes one pass turns it red, often
#   through code the file never imports or names (#4257 rendered through it).
CI_CONFIG_PREFIXES = (".github/workflows/",)
CI_CONFIG_FILES = frozenset({".pre-commit-config.yaml", "Makefile"})
DOC_SUFFIXES = (".md", ".rst")
DOC_SCANNER_RE = re.compile(r"\*\.md\b")
# An ``xfail(`` call whose arguments (one level of nested parens, any number of
# lines) pass ``strict=True``. A strict xfail spelled otherwise (a global
# ``xfail_strict``, ``strict=SOME_CONST``) is not seen; ``pyproject.toml`` changes
# are FULL anyway, and a global ``xfail_strict`` should widen this to any xfail.
STRICT_XFAIL_RE = re.compile(r"xfail\((?:[^()]|\([^()]*\))*?\bstrict\s*=\s*True\b", re.S)
FULL_SUITE_BRANCH_RE = re.compile(r"flip|routing|convergence", re.IGNORECASE)

# CI mode runs a selection as ONE shard; FULL fans out to FULL_SHARDS (8) shards
# in .github/workflows/test.yml. A big selection on one shard is slower than FULL
# and can outlive the job's 45-minute timeout: PR #4284 selected 744 files (~29 min
# of recorded fast-tier time) and was cancelled at 45 min with no failure, while a
# FULL run's shards each finish in 6-13 min. So a selection whose recorded
# fast-tier time exceeds the budget answers FULL. Calibration, from that run: 29
# recorded minutes took ~35 min of wall clock on the runner's 4 xdist workers
# (~1.2 wall min per recorded min), so 600 s is ~12 min of tests, about one FULL
# shard, and far under the 45-minute timeout. The estimate counts only tests at or
# under ``SLOW_SECONDS`` (PR CI runs ``-m "not slow"``, tests/slow_tier.py) and
# charges a file with no recorded test the mean recorded file. Without a durations
# file, more than ``CI_SHARD_MAX_FILES`` files (600 s at the mean ~1.6 s per file,
# rounded down) answers FULL instead.
DURATIONS_FILE = ".test_durations"
SLOW_SECONDS = 1.0  # tests/slow_tier.py SLOW_SECONDS
CI_SHARD_BUDGET_SECONDS = 600.0
CI_SHARD_MAX_FILES = 300

# Workspace-wide cargo run when these change.
CARGO_WORKSPACE_FILES = frozenset({"Cargo.toml", "Cargo.lock"})
CARGO_WORKSPACE_CRATES = frozenset({"djust_base", "djust_core"})


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


def is_ci_config(path: str) -> bool:
    return path in CI_CONFIG_FILES or path.startswith(CI_CONFIG_PREFIXES)


def is_doc(path: str) -> bool:
    return path.endswith(DOC_SUFFIXES)


def is_test_support(path: str) -> bool:
    """A helper module under a test root: not a test, a conftest or a package init."""
    name = PurePosixPath(path).name
    return (
        path.startswith(TEST_ROOTS)
        and path.endswith(".py")
        and not is_test_file(path)
        and name not in ("conftest.py", "__init__.py")
    )


def support_import_re(path: str) -> re.Pattern[str]:
    """How a file refers to the test-support module at ``path``.

    Its dotted name (``tests.git_env``, ``djust.tests._live_session``, also as a
    string such as a ``pytest_plugins`` entry or a settings path), or an import of
    its stem from its own package or a relative one (``from ._live_session import``,
    ``from . import _live_session``). Not the bare stem: ``views`` or ``settings``
    would name half the suite.
    """
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[0] == "python":
        parts = parts[1:]
    dotted = ".".join(parts)
    package = ".".join(parts[:-1])
    stem = re.escape(parts[-1])
    return re.compile(
        r"(?<![\w.])%s(?![\w])"
        r"|from\s+\.+%s(?![\w])"
        r"|from\s+(?:\.+|%s)\s+import\s+[^\n]*(?<![\w.])%s(?![\w])"
        % (re.escape(dotted), stem, re.escape(package), stem)
    )


def support_consumers(
    changed: Iterable[str],
    support: Iterable[str],
    global_files: Iterable[str],
    read_text: Callable[[str], str],
) -> tuple[list[re.Pattern[str]], str | None]:
    """How a file refers to a changed test-support module, or a FULL reason.

    Starts from the changed modules and adds every support module that imports one
    (a helper importing a helper), until nothing new is reached. A ``conftest.py``
    or ``pyproject.toml`` that refers to any of them reaches every test.
    """
    reached = set(changed)
    support = sorted(set(support))
    while True:
        patterns = [support_import_re(p) for p in sorted(reached)]
        for path in global_files:
            text = read_text(path)
            if any(r.search(text) for r in patterns):
                return patterns, "%s uses test support %s" % (path, ", ".join(sorted(reached)))
        grown = {
            path
            for path in support
            if path not in reached and any(r.search(read_text(path)) for r in patterns)
        }
        if not grown:
            return patterns, None
        reached |= grown


def full_suite_trigger(
    changed: Iterable[str], branch: str | None, *, targeted: bool = False, ci: bool = False
) -> str | None:
    """The reason the whole suite must run, or None.

    With ``targeted``, a blast-radius path (``blast_radius_reason``) is not a
    reason: the caller selects its direct consumers instead. Harness changes,
    the branch-name rule and shared test support always are, except that ``ci``
    also selects the consumers of CI configuration (see ``CI_CONFIG_PREFIXES``).
    """
    if branch and FULL_SUITE_BRANCH_RE.search(branch):
        return "branch name %r matches /flip|routing|convergence/" % branch
    for path in changed:
        if path.startswith(NO_PYTEST_CONSUMER_PREFIXES):
            continue
        if ci and (is_ci_config(path) or is_test_support(path)):
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
    ci: bool = False,
    gates: Iterable[str] = (),
    support: Iterable[str] = (),
    global_files: Iterable[str] = (),
    durations: Mapping[str, float] | None = None,
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

    ``ci`` (``--ci``) is targeted mode with the CI-mode narrowings described at
    ``CI_CONFIG_PREFIXES``; ``gates`` are the test files every CI selection runs,
    ``support`` every test-support module and ``global_files`` the files that make
    a support module reach every test (the ``conftest.py`` files, ``pyproject.toml``).
    ``durations`` (``.test_durations``, node id to seconds) sizes a CI selection
    against the one shard it would run on (``ci_shard_overflow``).
    """
    targeted = targeted or ci
    changed = sorted(set(changed))
    all_tests = sorted(set(all_tests))
    trigger = full_suite_trigger(changed, branch, targeted=targeted, ci=ci)
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
        last = dotted.rpartition(".")[2] if dotted else ""
        for t in all_tests:
            tname = PurePosixPath(t).name
            if stem and len(stem) >= 3 and stem in tname:
                chosen[t] = "name contains %r (%s)" % (stem, path)
                classified.add(path)
                continue
            text = text_of(t)
            # Substring checks first: both patterns need the name, and a regex search
            # over every test for every changed file is most of a large range's runtime.
            if base in text and base_re.search(text):
                chosen[t] = "mentions %s" % base
                classified.add(path)
                continue
            if import_res and last in text and any(r.search(text) for r in import_res):
                chosen[t] = "imports %s" % dotted
                classified.add(path)

    no_consumer = {p for p in changed if p.startswith(NO_PYTEST_CONSUMER_PREFIXES)}
    classified |= no_consumer
    helpers = [p for p in changed if is_test_support(p)] if ci else []
    if helpers:
        try:
            patterns, everything = support_consumers(helpers, support, global_files, read_text)
        except (OSError, UnicodeError) as exc:
            return Selection(True, "test support graph unavailable (%s)" % exc)
        if everything:
            return Selection(True, everything)
        for t in all_tests:
            if any(r.search(text_of(t)) for r in patterns):
                chosen.setdefault(t, "uses changed test support")
        classified |= set(helpers)
    if ci:
        docs = {p for p in changed if is_doc(p)}
        classified |= docs
        if docs:
            for t in all_tests:
                if DOC_SCANNER_RE.search(text_of(t)):
                    chosen.setdefault(t, "scans every doc")
        if chosen:
            for t in gates:
                if t in existing:
                    chosen.setdefault(t, "merge gate")
            for t in all_tests:
                if STRICT_XFAIL_RE.search(text_of(t)):
                    chosen.setdefault(t, "strict xfail")
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
    if ci:
        overflow = ci_shard_overflow(tests, durations)
        if overflow:
            return Selection(True, overflow)
    reason = "%d test file(s) selected for %d changed file(s)" % (len(tests), len(changed))
    if waived:
        reason += "; %s=1: direct selection instead of FULL for: %s" % (
            TARGETED_ENV,
            "; ".join(waived),
        )
    return Selection(False, reason, tests)


def ci_shard_overflow(tests: list[str], durations: Mapping[str, float] | None) -> str | None:
    """Why ``tests`` are too many for one CI shard, or None (``CI_SHARD_BUDGET_SECONDS``)."""
    per_file: dict[str, float] = {}
    for nodeid, seconds in (durations or {}).items():
        if isinstance(seconds, (int, float)) and seconds <= SLOW_SECONDS:
            f = nodeid.split("::", 1)[0]
            per_file[f] = per_file.get(f, 0.0) + seconds
    if not per_file:
        if len(tests) > CI_SHARD_MAX_FILES:
            return "%d test files selected, over the %d one CI shard can run (no %s)" % (
                len(tests),
                CI_SHARD_MAX_FILES,
                DURATIONS_FILE,
            )
        return None
    mean = sum(per_file.values()) / len(per_file)
    estimate = sum(per_file.get(t, mean) for t in tests)
    if estimate > CI_SHARD_BUDGET_SECONDS:
        return (
            "%d test files selected, ~%.0f s of recorded fast-tier time in %s, over the "
            "%.0f s one CI shard can run: running every root as FULL shards"
            % (len(tests), estimate, DURATIONS_FILE, CI_SHARD_BUDGET_SECONDS)
        )
    return None


def load_durations(root: Path) -> dict[str, float]:
    """``.test_durations`` (pytest-split's node id to seconds), or {} when unreadable."""
    try:
        data = json.loads((root / DURATIONS_FILE).read_text())
    except (OSError, ValueError) as exc:
        print("select-tests: %s unreadable (%s)" % (DURATIONS_FILE, exc), file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


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


def _test_support_files(root: Path) -> list[str]:
    found: list[str] = []
    for tr in TEST_ROOTS:
        for path in (root / tr).rglob("*.py"):
            rel = path.relative_to(root).as_posix()
            if "__pycache__" not in rel and is_test_support(rel):
                found.append(rel)
    return sorted(found)


def _global_files(root: Path) -> list[str]:
    conftests = [
        p.relative_to(root).as_posix()
        for tr in ("", *TEST_ROOTS)
        for p in (root / tr).glob("conftest.py" if not tr else "**/conftest.py")
    ]
    return sorted(set(conftests)) + ["pyproject.toml"]


def merge_gates(root: Path) -> list[str]:
    """The ``MERGE_GATES`` test files the Makefile lists (#3757), in order."""
    text = (root / "Makefile").read_text()
    m = re.search(r"^MERGE_GATES\s*:=\s*\\\n((?:\t.*\n)+)", text, re.M)
    if not m:
        return []
    return [w for w in m.group(1).split() if w != "\\"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--from", dest="from_ref", default=os.environ.get("PRE_COMMIT_FROM_REF"))
    ap.add_argument("--to", dest="to_ref", default=os.environ.get("PRE_COMMIT_TO_REF"))
    ap.add_argument("--branch", default=None, help="branch name (default: current)")
    ap.add_argument("--cargo", action="store_true", help="print cargo test package args instead")
    ap.add_argument(
        "--ci",
        action="store_true",
        help="pull-request CI selection (targeted, plus the CI-mode rules at CI_CONFIG_PREFIXES)",
    )
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
        ci=args.ci,
        gates=merge_gates(root) if args.ci else (),
        support=_test_support_files(root) if args.ci else (),
        global_files=_global_files(root) if args.ci else (),
        durations=load_durations(root) if args.ci else None,
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
