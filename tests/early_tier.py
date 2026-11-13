"""The early tier: tests that run before merge even though they are slow (the promotion ledger).

PR and merge-queue CI deselect the ``slow`` tier (``tests/slow_tier.py``: recorded over 1 s in
``.test_durations``). A test that only runs late (the push to ``main``, the nightly
``main-health`` suite) fails late, after the change that broke it has merged. Two mechanisms keep
such a test early, both honoured where ``pr_tier`` is (``tests/slow_tier.py``):

* **The promotion ledger** (``tests/promotion-ledger.json``): a test that failed late is
  promoted, by ``scripts/promotion_ledger.py promote`` from the nightly's junit report. It stays
  early until it has been green for ``DEMOTE_AFTER_GREEN`` consecutive green nightlies
  (``scripts/promotion_ledger.py demote``), unless it is ``pinned``. The early-tier cost of the
  ledger is capped at ``BUDGET_SECONDS`` of recorded duration; over the cap the entries whose last
  failure is oldest leave first, and pinned entries alone over the cap is an error.
* **Path rules** (``PATH_RULES`` below): a change of a given shape always runs a given set of pins,
  whatever their duration. A rule fires on the changed paths of the run (CI lists them from the
  PR's diff; locally it is the diff against ``origin/main`` plus the working tree and untracked
  files), and only in a run that deselects the slow tier or in ``make test-early``.

Ledger granularity is the test FUNCTION (``path::Class::test_name``, parameters stripped): a
parametrized id is derived from parameter values and churns with them, and a whole file is too
coarse for a per-second budget. An entry promotes every parametrization of its function.

``make test-early`` (``DJUST_EARLY_ONLY=1``) runs exactly the early tier: every ledger entry,
every ``pr_tier`` test and the pins the current change's path rules select.

The numbers are starting values, kept here and nowhere else.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.git_env import isolated_git_env

#: An unpinned entry leaves the ledger after this many consecutive green nightlies.
DEMOTE_AFTER_GREEN = 30
#: The ledger's early-tier cost cap, in seconds of recorded duration (``.test_durations``).
BUDGET_SECONDS = 180.0

LEDGER_FILE = "tests/promotion-ledger.json"
DURATIONS_FILE = ".test_durations"
MAKEFILE = "Makefile"

#: ``make test-early``: keep only the early tier.
EARLY_ONLY_ENV = "DJUST_EARLY_ONLY"
#: Changed paths as ``git diff --name-status --no-renames`` lines (``A\tpath``), one per line, in
#: a file (CI writes it from the PR's diff) or inline (tests, reproductions). Neither set, the
#: local default is git (see ``changed_status``).
CHANGED_STATUS_FILE_ENV = "DJUST_CHANGED_STATUS_FILE"
CHANGED_STATUS_ENV = "DJUST_CHANGED_STATUS"

#: ``targets`` entry that expands to the Makefile's ``MERGE_GATES`` list (#3757), the one list
#: of tree-scanning test modules; it is read, not restated.
MERGE_GATES_TARGET = "@MERGE_GATES"

TEST_ROOTS = ("tests", "python/tests", "python/djust/tests")


@dataclass(frozen=True)
class PathRule:
    """A change shape that always runs a set of pins before merge."""

    name: str
    why: str
    #: ``git diff --name-status`` letters that fire the rule (``A`` added, ``D`` deleted,
    #: ``M`` modified; a rename is ``D`` + ``A`` under ``--no-renames``).
    statuses: frozenset[str]
    #: Regular expressions over the repository-relative path.
    patterns: tuple[str, ...]
    #: Node-id prefixes (a file, a class or a test), or ``MERGE_GATES_TARGET``.
    targets: tuple[str, ...]
    #: A path that must fire the rule (``tests/test_promotion_ledger.py`` checks it does).
    example: str


_TEST_MODULE = r"^(tests|python/tests|python/djust/tests)/(.+/)?test_[^/]*\.py$"

#: The rule table. Keep it small: each rule names the incident that justifies it.
PATH_RULES: tuple[PathRule, ...] = (
    PathRule(
        name="test-module-added-or-removed",
        why=(
            "the tree-scanning pins (registries of test modules: the merge-gate list, the git-env "
            "guard, the shard and fixture-hygiene scans) pass on a PR's own tree and fail when a "
            "test module appears or disappears: main went red on a54278ebd (#3757), and on "
            "2026-11-12 the merge-gate and git-env pins failed only in the nightly after a new "
            "test file landed"
        ),
        statuses=frozenset({"A", "D"}),
        patterns=(_TEST_MODULE,),
        targets=(MERGE_GATES_TARGET,),
        example="tests/test_brand_new_4999.py",
    ),
    PathRule(
        name="system-check-changed",
        why=(
            "a check ID was allocated to two checks (djust.S004, #2070), and the C0xx docs "
            "table drifted from the registered checks (#2562): a new or moved check must run "
            "the ID-uniqueness and docs-table pins"
        ),
        statuses=frozenset({"A", "D", "M"}),
        patterns=(r"^python/djust/checks/", r"^docs/system-checks\.md$"),
        targets=(
            "python/djust/tests/test_check_id_uniqueness_2070.py",
            "python/djust/tests/test_c0xx_docs_table_pin_2562.py",
        ),
        example="python/djust/checks/security.py",
    ),
    PathRule(
        name="client-js-source-changed",
        why=(
            "the client size figures quoted in docs are checked against the generated "
            "client-sizes.json (#2138), and several of those tests are recorded over 1 s, so a "
            "client change that moves the size reached them only after merge"
        ),
        statuses=frozenset({"A", "D", "M"}),
        patterns=(r"^python/djust/static/djust/src/",),
        targets=("tests/test_client_size_manifest_2138.py",),
        example="python/djust/static/djust/src/12-vdom-patch.js",
    ),
)


# --------------------------------------------------------------------------- #
# Node ids and the ledger
# --------------------------------------------------------------------------- #


def entry_key(nodeid: str) -> str:
    """The ledger key of a node id: the test function, parameters stripped."""
    cut = nodeid.find("[")
    return nodeid if cut < 0 else nodeid[:cut]


def matches_prefix(nodeid: str, prefix: str) -> bool:
    return nodeid == prefix or nodeid.startswith(prefix + "::")


def load_ledger(path: Path) -> dict:
    """The ledger document; a missing file is an empty ledger."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError(f"{path}: expected an object with an 'entries' list")
    return data


def ledger_keys(rootpath: Path) -> frozenset[str]:
    try:
        data = load_ledger(rootpath / LEDGER_FILE)
    except (OSError, ValueError):
        return frozenset()
    return frozenset(str(e.get("test", "")) for e in data["entries"] if e.get("test"))


def load_durations(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def make_merge_gates(rootpath: Path) -> list[str]:
    """The ``MERGE_GATES := ...`` list in the Makefile (empty when there is none)."""
    try:
        text = (rootpath / MAKEFILE).read_text(encoding="utf-8")
    except OSError:
        return []
    match = re.search(r"^MERGE_GATES\s*:=((?:.*\\\n)*.*)$", text, re.MULTILINE)
    return match.group(1).replace("\\\n", " ").split() if match else []


# --------------------------------------------------------------------------- #
# Path rules
# --------------------------------------------------------------------------- #


def parse_status(lines: Iterable[str]) -> set[tuple[str, str]]:
    """``{(status letter, path)}`` from ``git diff --name-status`` lines.

    A rename or copy line (``R100\\told\\tnew``) counts as the old path deleted and the new one
    added, which is what it is to a registry of modules.
    """
    out: set[tuple[str, str]] = set()
    for line in lines:
        parts = line.rstrip("\n").split("\t")
        if len(parts) < 2 or not parts[0]:
            continue
        letter = parts[0][0].upper()
        if letter in ("R", "C") and len(parts) >= 3:
            if letter == "R":
                out.add(("D", _norm(parts[1])))
            out.add(("A", _norm(parts[2])))
        else:
            out.add((letter, _norm(parts[1])))
    return out


def _norm(path: str) -> str:
    path = path.strip()
    return path[2:] if path.startswith("./") else path


def fired_rules(
    changes: Iterable[tuple[str, str]], rules: Iterable[PathRule] = PATH_RULES
) -> list[PathRule]:
    changes = list(changes)
    fired = []
    for rule in rules:
        compiled = [re.compile(p) for p in rule.patterns]
        if any(
            status in rule.statuses and any(c.search(path) for c in compiled)
            for status, path in changes
        ):
            fired.append(rule)
    return fired


def expand_targets(rule: PathRule, rootpath: Path) -> list[str]:
    out: list[str] = []
    for target in rule.targets:
        out.extend(make_merge_gates(rootpath) if target == MERGE_GATES_TARGET else [target])
    return out


def rule_targets(rootpath: Path, rules: Iterable[PathRule] = PATH_RULES) -> dict[str, str]:
    """Every node-id prefix any rule can select, mapped to the (first) rule naming it."""
    out: dict[str, str] = {}
    for rule in rules:
        for target in expand_targets(rule, rootpath):
            out.setdefault(target, rule.name)
    return out


def changed_status(rootpath: Path, environ=None) -> set[tuple[str, str]]:
    """The changes of this run: the env (CI, tests), else git against ``origin/main``."""
    env = os.environ if environ is None else environ
    listing = env.get(CHANGED_STATUS_FILE_ENV)
    if listing is not None:
        try:
            return parse_status(Path(listing).read_text(encoding="utf-8").splitlines())
        except OSError:
            return set()
    inline = env.get(CHANGED_STATUS_ENV)
    if inline is not None:
        return parse_status(inline.splitlines())
    out: set[tuple[str, str]] = set()
    commands = (
        ["diff", "--name-status", "--no-renames", "origin/main...HEAD"],
        ["diff", "--name-status", "--no-renames", "HEAD"],
    )
    for args in commands:
        res = _git(rootpath, args)
        if res is not None:
            out |= parse_status(res.splitlines())
    untracked = _git(rootpath, ["ls-files", "--others", "--exclude-standard"])
    if untracked is not None:
        out |= {("A", _norm(p)) for p in untracked.splitlines() if p.strip()}
    return out


def _git(rootpath: Path, args: list[str]) -> str | None:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=rootpath,
            env=isolated_git_env(),
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout if res.returncode == 0 else None


def selected_by_rules(
    rootpath: Path, changes: Iterable[tuple[str, str]], rules: Iterable[PathRule] = PATH_RULES
) -> dict[str, str]:
    """Node-id prefixes the fired rules select, mapped to the rule's name."""
    out: dict[str, str] = {}
    for rule in fired_rules(changes, rules):
        for target in expand_targets(rule, rootpath):
            out.setdefault(target, rule.name)
    return out


# --------------------------------------------------------------------------- #
# The pytest plugin
# --------------------------------------------------------------------------- #


@dataclass
class EarlyTier:
    ledger: frozenset[str]
    rule_prefixes: dict[str, str]
    only: bool

    def reason(self, item: pytest.Item) -> str | None:
        """Why this item is early (ledger or a path rule), or None."""
        if entry_key(item.nodeid) in self.ledger:
            return "promotion ledger"
        for prefix, rule in self.rule_prefixes.items():
            if matches_prefix(item.nodeid, prefix):
                return f"path rule {rule}"
        return None


_KEY = pytest.StashKey[EarlyTier]()


def _excludes_slow(markexpr: str) -> bool:
    return "not slow" in " ".join(markexpr.split())


def pytest_configure(config: pytest.Config) -> None:
    # Computed here, before the report header and before any collection hook asks.
    early_tier(config)


def early_tier(config: pytest.Config) -> EarlyTier:
    """This session's early tier (computed once per session, never module-global)."""
    found = config.stash.get(_KEY, None)
    if found is None:
        only = os.environ.get(EARLY_ONLY_ENV) == "1"
        rootpath = Path(config.rootpath)
        # Path rules matter only when the slow tier is deselected (or in `make test-early`):
        # a full run already runs every pin, and need not ask git anything.
        wants_rules = only or _excludes_slow(config.option.markexpr or "")
        prefixes = selected_by_rules(rootpath, changed_status(rootpath)) if wants_rules else {}
        found = EarlyTier(ledger=ledger_keys(rootpath), rule_prefixes=prefixes, only=only)
        config.stash[_KEY] = found
    return found


def tier_of(item: pytest.Item) -> str:
    """``nightly`` / ``early`` / ``late``: where this item runs before merge, AFTER slow marking."""
    if item.get_closest_marker("nightly") is not None:
        return "nightly"
    return "late" if item.get_closest_marker("slow") is not None else "early"


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    tier = early_tier(config)
    # The nightly's junit report carries each test's node id and pre-merge tier, which is what
    # `scripts/promotion_ledger.py promote` reads (junit names are not node ids).
    if getattr(config.option, "xmlpath", None):
        for item in items:
            item.user_properties.append(("djust_nodeid", item.nodeid))
            item.user_properties.append(("djust_tier", tier_of(item)))
    if not tier.only:
        return
    keep, drop = [], []
    for item in items:
        early = item.get_closest_marker("pr_tier") is not None or tier.reason(item) is not None
        (keep if early else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_report_header(config: pytest.Config) -> str | None:
    tier = config.stash.get(_KEY, None)
    if tier is None or not (tier.ledger or tier.rule_prefixes or tier.only):
        return None
    rules = sorted(set(tier.rule_prefixes.values()))
    return "early tier: %d ledger entr%s, path rules fired: %s%s" % (
        len(tier.ledger),
        "y" if len(tier.ledger) == 1 else "ies",
        ", ".join(rules) or "none",
        " (early tier only)" if tier.only else "",
    )
