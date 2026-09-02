#!/usr/bin/env python3
"""Pick the pytest files (and cargo packages) a pushed range can affect (#2526).

The pre-push hook used to run the whole Python suite — 20,000 tests, ~8.5
minutes — on every push, and a PR pushes three or four times. The
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
    the branch name matches ``flip|routing|convergence`` (the routing-flip
    rule in CLAUDE.md: those PRs must run every test root).

Rust: ``--cargo`` prints the ``cargo test`` package arguments — ``-p`` for each
crate whose files changed plus every workspace crate that depends on it, or
``--workspace`` when ``Cargo.toml`` / ``Cargo.lock`` / ``djust_core`` changed.

The selection function is pure (paths in, paths out; the file reader is a
parameter) so tests/unit/test_select_tests.py can exercise it without a git
fixture. Only the CLI touches git.

Usage::

    scripts/select-tests.py [--from REF] [--to REF] [--branch NAME]
    scripts/select-tests.py --cargo [...]

stdout is the selection, one path per line, or the single word ``FULL``
(``--workspace`` in cargo mode); the reason goes to stderr. Exit 0 always —
the caller falls back to the full suite when this script cannot run.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

TEST_ROOTS = ("tests/", "python/tests/", "python/djust/tests/")

# Any change here means the harness itself changed: nothing narrower is safe.
FULL_SUITE_FILES = frozenset(
    {
        "pyproject.toml",
        "pytest.ini",
        ".pre-commit-config.yaml",
        "scripts/pre-push-pytest.sh",
        "scripts/select-tests.py",
        "python/djust/__init__.py",
        "python/djust/apps.py",
    }
)
# The whole engine: a change under any of these is exercised by every test that
# renders a template, not only by the tests that name the file (the #2575 review
# measured `filters.rs` selecting 11 of 27 floatformat tests by name/import alone).
FULL_SUITE_PREFIXES = (
    "crates/djust_core/src/",
    "crates/djust_templates/src/",
    "crates/djust_vdom/src/",
)
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


def full_suite_trigger(changed: Iterable[str], branch: str | None) -> str | None:
    """The reason the whole suite must run, or None."""
    if branch and FULL_SUITE_BRANCH_RE.search(branch):
        return "branch name %r matches /flip|routing|convergence/" % branch
    for path in changed:
        if path in FULL_SUITE_FILES:
            return "%s changed" % path
        if PurePosixPath(path).name == "conftest.py":
            return "%s changed" % path
        if path.startswith(FULL_SUITE_PREFIXES):
            return "%s changed (djust_core: whole-engine blast radius)" % path
    return None


def select_tests(
    changed: Iterable[str],
    all_tests: Iterable[str],
    read_text: Callable[[str], str],
    branch: str | None = None,
) -> Selection:
    """The pure selection. ``all_tests`` is every EXISTING test file."""
    changed = sorted(set(changed))
    all_tests = sorted(set(all_tests))
    trigger = full_suite_trigger(changed, branch)
    if trigger:
        return Selection(True, trigger)
    if not changed:
        return Selection(True, "no changed files in the range")

    chosen: dict[str, str] = {}
    existing = set(all_tests)

    # (a) changed test files.
    for path in changed:
        if is_test_file(path) and path in existing:
            chosen.setdefault(path, "changed test")

    # (b) modules: name contains the stem, or text imports it.
    # (c) every file: text mentions its basename.
    texts: dict[str, str] = {}

    def text_of(t: str) -> str:
        if t not in texts:
            try:
                texts[t] = read_text(t)
            except OSError:
                texts[t] = ""
        return texts[t]

    for path in changed:
        base = PurePosixPath(path).name
        base_re = re.compile(r"(?<![\w-])%s(?![\w-])" % re.escape(base))
        is_module = path.endswith(".py") and not is_test_file(path)
        stem, dotted = module_names(path) if is_module else (None, None)
        import_res = _import_patterns(dotted) if dotted else []
        for t in all_tests:
            if t in chosen:
                continue
            tname = PurePosixPath(t).name
            if stem and len(stem) >= 3 and stem in tname:
                chosen[t] = "name contains %r (%s)" % (stem, path)
                continue
            text = text_of(t)
            if base_re.search(text):
                chosen[t] = "mentions %s" % base
                continue
            if any(r.search(text) for r in import_res):
                chosen[t] = "imports %s" % dotted

    if not chosen:
        return Selection(True, "selection is empty for %d changed file(s)" % len(changed))
    tests = sorted(chosen)
    reason = "%d test file(s) selected for %d changed file(s)" % (len(tests), len(changed))
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
        changed, _all_test_files(root), lambda p: (root / p).read_text(errors="replace"), branch
    )
    if sel.full:
        print("select-tests: FULL suite — %s" % sel.reason, file=sys.stderr)
        print("FULL")
    else:
        print("select-tests: %s" % sel.reason, file=sys.stderr)
        for t in sel.tests:
            print("    " + t, file=sys.stderr)
            print(t)
    return 0


if __name__ == "__main__":
    sys.exit(main())
