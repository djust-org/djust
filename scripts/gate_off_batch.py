#!/usr/bin/env python3
"""Batch gate-off: many mutations, ONE full test run per batch, attributed by failure text (#3905).

``scripts/gate_off.py`` (#3714) runs the test command once per mutation. That does not scale:
a sweep over ~190 call sites whose full run takes four minutes is twelve hours. Narrowing each
run to "the tests of that component" makes it fast, but then the run is not the CI
configuration, and a narrowed run can hide a gap in what the full run covers.

This tool applies every mutation of a *batch* at once, runs the whole test command once, and
says which mutation each failing test belongs to by the text of its failure. It reuses the
single-mutation harness for everything that decides whether a result can be trusted (the
bounded run, the per-directory lock and journal, the byte-exact restore, the classification of
pytest's summary), so the rules in CLAUDE.md (#2129, #2135, #2230, #2730) are not restated here.

Usage::

    python scripts/gate_off_batch.py mutations.json [--only TEXT] [--survivors-only]
        [--jobs 1|2] [--json REPORT] [--no-control] [--allow-dirty] [--verify-survivors]
        [--max-batch N] [--deadline SECONDS] [--rss-mb MB] [--verbose]

``mutations.json`` (or ``.toml``) is the ``gate_off.py`` spec plus the fields that attribute a
failure to a mutation::

    {
      "root": ".",
      "test_files": ["tests/test_sweep.py"],           # appended to "command"
      "command": ["{python}", "-m", "pytest", "-q"],   # default shown; do not pass -x / --maxfail
      "deadline": 300, "rss_mb": 4096, "max_batch": 64,
      "mutations": [
        {"label": "alpha() returns the wrong value",
         "file": "pkg/calc.py",
         "old": "def alpha():\\n    return 1", "new": "def alpha():\\n    return 99",
         "component": "alpha",              # at most ONE mutation per component in a batch
         "names": ["calc.alpha("],           # substrings; a failure whose text has one is alpha's
         "test_ids": ["test_alpha"]}         # regexes on "classname::name" (optional, same effect)
      ]
    }

How a batch is judged (a result is never guessed):

* A failing test (a ``<failure>`` or an ``<error>`` in the junit report) belongs to the mutation
  whose ``names`` appear in its failure text or whose ``test_ids`` match its id. A mutation with
  at least one failing test that no other mutation claims is ``CAUGHT``, and that test is its
  evidence.
* A test claimed by TWO mutations (a shared helper, a test that touches both) is ambiguous. So
  is a failing test nobody claims. A mutation with no test of its own that is touched by either
  is re-run ALONE (``how: single``), and the single run decides.
* A mutation with no failing test of its own, when every failing test of the batch was claimed
  by some other mutation, is ``SURVIVED``: its name never appeared in a failure. ``--verify-
  survivors`` re-runs those alone too; ``--survivors-only`` re-runs the survivors of an earlier
  report alone.
* A batch that does not collect (a mutation broke an import), hangs, or exceeds the RSS
  ceiling cannot be attributed: every member is re-run alone. The member that caused it is then
  reported ``INVALID`` (collection), or ``CAUGHT`` as ``hung`` / ``runaway-memory`` with the
  measurement (the repository's rule: a runaway under gate-off is the strongest proof the fix
  is load-bearing), exactly as ``gate_off.py`` reports it.
* A mutation with no ``component``, ``names`` and ``test_ids`` cannot be attributed and always
  runs alone. Mutations that differ in ``command``, ``deadline``, ``rss_mb`` or ``smoke``, or
  whose texts overlap in one file, never share a batch.

Limits, stated so a result is read for what it is:

* Attribution is by NAME. The failure text has to name the component; a site inside a
  module-level helper shared by several components is attributed to the module, and a name that
  is a substring of another mutation's name will claim that mutation's failures (the result is
  then an ambiguity, which re-runs, not a wrong answer). Use specific names (``calc.alpha(``).
* A failure that names mutation A but was caused by B alone is attributed to A. Names are the
  user's declaration of "this failure is that site's"; ``--verify-survivors`` and the single
  re-runs bound the damage but cannot remove it.
* Applying several mutations at once can mask an interaction (B's breakage hiding A's). A
  survivor is a candidate to inspect, not a verdict: equivalent mutation, two mechanisms
  shadowing each other, or genuinely missing coverage look identical from here.
* The runner is pytest (the junit report is how failures are read). ``-x`` and ``--maxfail``
  stop at the first failure and are refused. The test ids are junit's ``classname::name``.
* ``--jobs 2`` runs the second half of the batches in a COPY of the tree (the copy excludes
  ``.git``, ``target``, ``node_modules``, ``.venv`` and bytecode; ``copy_ignore`` in the spec
  replaces that list), so the command and the tests must work from a copy. Locking forbids two
  runs on one tree. More than two is refused: the machine is shared.

The tree must be clean in the mutated files (``git status``) when the run starts, and is checked
again after it; ``--allow-dirty`` skips both and relies on the SHA-256 check alone.

Exit status: 0 all CAUGHT; 1 any SURVIVED or INVALID; 2 the baseline was red / bad usage;
3 a file could not be restored (or git says it differs afterwards); 4 a target is locked by
another run or its journal cannot be reconciled; 5 the tree is dirty in a mutated file.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


def _load_gate_off() -> Any:
    """The single-mutation harness, loaded by path so this works however it is invoked."""
    loaded = sys.modules.get("gate_off")
    if loaded is not None and hasattr(loaded, "_run_bounded"):
        return loaded
    path = Path(__file__).resolve().with_name("gate_off.py")
    spec = importlib.util.spec_from_file_location("gate_off", path)
    if spec is None or spec.loader is None:  # pragma: no cover - the file is next to this one
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["gate_off"] = module
    spec.loader.exec_module(module)
    return module


go = _load_gate_off()

CAUGHT = go.CAUGHT
SURVIVED = go.SURVIVED
INVALID = go.INVALID

DEFAULT_COMMAND = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
DEFAULT_MAX_BATCH = 64
MAX_JOBS = 2
DEFAULT_COPY_IGNORE = (
    ".git",
    "target",
    "node_modules",
    ".venv",
    "__pycache__",
    "*.pyc",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
)

EXIT_OK, EXIT_NOT_CAUGHT, EXIT_BAD, EXIT_RESTORE, EXIT_LOCKED, EXIT_DIRTY = 0, 1, 2, 3, 4, 5


class DirtyTree(RuntimeError):
    """A mutated file has uncommitted changes, so a byte-exact restore cannot be told from them."""


# --------------------------------------------------------------------------- data


@dataclasses.dataclass
class BatchMutation(go.Mutation):
    """A ``gate_off.Mutation`` plus what attributes a failing test to it."""

    component: str | None = None
    names: list[str] = dataclasses.field(default_factory=list)
    test_ids: list[str] = dataclasses.field(default_factory=list)

    @property
    def attributable(self) -> bool:
        return bool(self.names or self.test_ids)

    def to_spec(self) -> dict[str, Any]:
        """The spec entry that rebuilds this mutation (used to hand a share to a worker)."""
        entry: dict[str, Any] = {
            "label": self.label,
            "file": self.file,
            "old": self.old,
            "new": self.new,
            "names": list(self.names),
            "test_ids": list(self.test_ids),
        }
        for key in ("component", "command", "deadline", "rss_mb", "smoke"):
            value = getattr(self, key)
            if value is not None:
                entry[key] = value
        return entry


@dataclasses.dataclass
class Failure:
    """One failing or erroring test, as the junit report records it."""

    id: str
    text: str
    kinds: list[str]


@dataclasses.dataclass
class Row:
    """The verdict on one mutation (what the JSON report holds)."""

    label: str
    file: str
    status: str
    kind: str
    detail: str
    how: str = ""  # "batch" or "single"
    batch: int | None = None
    fallback: str = ""  # why this mutation was re-run alone, when it was
    failing_tests: list[str] = dataclasses.field(default_factory=list)
    failed: int = 0
    errors: int = 0
    peak_rss_mb: float | None = None
    output_tail: str = ""


@dataclasses.dataclass
class GroupRun:
    """One application of a group of mutations and the one full run made against it."""

    outcome: Any = None  # gate_off.RunOutcome
    failures: list[Failure] | None = None  # None: no readable junit report
    error: str = ""  # the group could not be applied / smoke-tested
    error_kind: str = ""
    probe: Any = None  # a --collect-only run, made only when a single mutation hit a bound


@dataclasses.dataclass
class Attribution:
    exclusive: dict[int, list[str]]  # mutation index -> test ids only it claims
    contested: dict[int, list[str]]  # mutation index -> ids it shares with another mutation
    unclaimed: list[str]  # failing test ids no mutation claims


# --------------------------------------------------------------------------- junit and attribution


def parse_junit(path: Path) -> list[Failure] | None:
    """Failing and erroring tests from a junit file, or None when there is no readable report."""
    try:
        root = ET.parse(path).getroot()  # our own pytest wrote it, into a private directory
    except (OSError, ET.ParseError):
        # No report at all (the run was killed, or the interpreter died before pytest wrote
        # one): the caller treats None as "cannot attribute" and re-runs, never as "no failures".
        return None
    merged: dict[str, Failure] = {}
    for case in root.iter("testcase"):
        classname, name = case.get("classname") or "", case.get("name") or ""
        test_id = f"{classname}::{name}" if classname else name
        for kind in ("failure", "error"):
            for element in case.findall(kind):
                text = f"{element.get('message') or ''}\n{element.text or ''}"
                seen = merged.setdefault(test_id, Failure(test_id, "", []))
                seen.text += text + "\n"
                seen.kinds.append(kind)
    return list(merged.values())


def claims(mutation: BatchMutation, failure: Failure) -> bool:
    """True when the failure's text names the mutation or its id matches a declared pattern."""
    if any(name and name in failure.text for name in mutation.names):
        return True
    return any(re.search(pattern, failure.id) for pattern in mutation.test_ids)


def attribute(failures: Sequence[Failure], mutations: Sequence[BatchMutation]) -> Attribution:
    """Pure: assign each failing test to the mutation(s) that claim it."""
    exclusive: dict[int, list[str]] = {}
    contested: dict[int, list[str]] = {}
    unclaimed: list[str] = []
    for failure in failures:
        owners = [i for i, m in enumerate(mutations) if claims(m, failure)]
        if not owners:
            unclaimed.append(failure.id)
        elif len(owners) == 1:
            exclusive.setdefault(owners[0], []).append(failure.id)
        else:
            for i in owners:
                contested.setdefault(i, []).append(failure.id)
    return Attribution(exclusive, contested, unclaimed)


def judge(attribution: Attribution, index: int) -> tuple[str, str]:
    """("caught" | "survived" | "single", reason) for the mutation at ``index``."""
    if attribution.exclusive.get(index):
        return "caught", ""
    if attribution.contested.get(index):
        return "single", "its failing tests are also claimed by another mutation"
    if attribution.unclaimed:
        return "single", (
            f"{len(attribution.unclaimed)} failing test(s) name no mutation, so this one "
            "cannot be ruled out"
        )
    return "survived", ""


# --------------------------------------------------------------------------- planning


def _locate(original: bytes, mutation: BatchMutation) -> tuple[int, int] | None:
    old = mutation.old.encode()
    if not old or original.count(old) != 1:
        return None
    start = original.index(old)
    return start, start + len(old)


def _batch_key(mutation: BatchMutation) -> tuple[Any, ...]:
    return (
        tuple(mutation.command or ()),
        mutation.deadline,
        mutation.rss_mb,
        tuple(mutation.smoke or ()),
    )


def plan_batches(
    mutations: Sequence[BatchMutation],
    spans: dict[int, tuple[Path, int, int]],
    max_batch: int = DEFAULT_MAX_BATCH,
) -> list[list[int]]:
    """Group mutation indices into batches. Pure.

    A mutation joins the first batch that has the same run configuration, no mutation of the
    same component, and no text overlapping its own in the same file. An unattributable
    mutation (no names, no test ids) is a batch of one.
    """
    batches: list[list[int]] = []
    for index, mutation in enumerate(mutations):
        placed = False
        if mutation.attributable and max_batch > 1:
            for batch in batches:
                first = mutations[batch[0]]
                if (
                    len(batch) >= max_batch
                    or not first.attributable
                    or _batch_key(first) != _batch_key(mutation)
                ):
                    continue
                if mutation.component and any(
                    mutations[j].component == mutation.component for j in batch
                ):
                    continue
                path, start, end = spans[index]
                if any(
                    spans[j][0] == path and start < spans[j][2] and spans[j][1] < end for j in batch
                ):
                    continue
                batch.append(index)
                placed = True
                break
        if not placed:
            batches.append([index])
    return batches


# --------------------------------------------------------------------------- running a group


@dataclasses.dataclass
class Context:
    root: Path
    command: list[str]
    deadline: float
    rss_mb: float
    scratch: Path
    max_batch: int = DEFAULT_MAX_BATCH
    verify_survivors: bool = False
    suite_runs: int = 0
    batch_counter: int = 0


def _combine(root: Path, group: Sequence[BatchMutation]) -> dict[Path, bytes] | str:
    """The mutated bytes of every file the group touches, or why they cannot be built."""
    by_file: dict[Path, list[BatchMutation]] = {}
    for mutation in group:
        by_file.setdefault((root / mutation.file).resolve(), []).append(mutation)
    combined: dict[Path, bytes] = {}
    for path, members in by_file.items():
        original = path.read_bytes()
        edits = []
        for mutation in members:
            span = _locate(original, mutation)
            if span is None:
                return f"{mutation.label!r}: its text no longer matches exactly once in {path}"
            edits.append((span[0], span[1], mutation.new.encode()))
        edits.sort()
        out, position = b"", 0
        for start, end, new in edits:
            if start < position:
                return f"two mutations overlap in {path}"
            out += original[position:start] + new
            position = end
        out += original[position:]
        if out == original:
            return f"the group leaves {path} unchanged"
        if path.suffix == ".py":
            try:
                compile(out, str(path), "exec")
            except SyntaxError as exc:
                return (
                    f"the combined source of {path} does not compile: {exc.msg} (line {exc.lineno})"
                )
        combined[path] = out
    return combined


#: Runs the test command as its child and kills their whole process group the moment the tool that
#: started it is gone. ``gate_off._run_bounded`` kills the group on a deadline, the RSS ceiling,
#: SIGINT and SIGTERM, but nothing can run in a tool that is SIGKILLed (or whose pytest worker an
#: outer harness SIGKILLs): the command sits in its own session, so it outlives its parent as an
#: orphan, spinning. macOS has no PR_SET_PDEATHSIG, so the child watches its parent instead.
_WATCHDOG = """\
import os, signal, subprocess, sys
parent, command = int(sys.argv[1]), sys.argv[2:]
try:
    proc = subprocess.Popen(command)
except OSError as exc:
    sys.stderr.write("watchdog: cannot run %r: %s\\n" % (command[:1], exc))
    sys.exit(127)
while True:
    try:
        code = proc.wait(timeout=0.2)
        break
    except subprocess.TimeoutExpired:
        if os.getppid() != parent:
            os.killpg(os.getpgrp(), signal.SIGKILL)
sys.exit(code if code >= 0 else 128 - code)
"""


def _guarded(command: Sequence[str]) -> list[str]:
    """``command`` wrapped so it cannot outlive this process (see ``_WATCHDOG``)."""
    if shutil.which(command[0]) is None:
        raise go.CommandNotRunnable(f"cannot run {list(command)[:1]}: not found")
    return [sys.executable, "-c", _WATCHDOG, str(os.getpid()), *command]


def _tail(text: str, lines: int = 6) -> str:
    return "\n".join(text.rstrip().splitlines()[-lines:])


def run_group(ctx: Context, group: Sequence[BatchMutation], *, probe: bool = False) -> GroupRun:
    """Apply every mutation of ``group``, run the command once, restore, return what happened.

    The files are restored from the bytes saved before the first write, in a ``finally``, and
    each restore is verified by SHA-256 (``gate_off._restore``). ``probe`` makes a
    ``--collect-only`` run when the run hit a bound, to tell a hang at import from a hang in a
    test (a single mutation only).
    """
    combined = _combine(ctx.root, group)
    if isinstance(combined, str):
        return GroupRun(error=combined, error_kind="not-applicable")

    deadline = group[0].deadline if group[0].deadline is not None else ctx.deadline
    ceiling = group[0].rss_mb if group[0].rss_mb is not None else ctx.rss_mb
    command = group[0].command or ctx.command
    smoke = group[0].smoke

    saved = {path: go._snapshot(path) for path in combined}
    landed: list[Path] = []
    result = GroupRun()
    for path, mutated in combined.items():
        go._journal_write(path, saved[path], mutated)
        go._ACTIVE[path] = saved[path]
    try:
        for path, mutated in combined.items():
            try:
                go._write_atomic(path, mutated, saved[path].mode)
            except OSError as exc:
                if path.read_bytes() != saved[path].data:
                    raise
                # The write failed before the replace: this file is untouched. The group did
                # not run; the files already written are restored by the ``finally``.
                return GroupRun(error=f"cannot write {path}: {exc}", error_kind="unwritable")
            landed.append(path)
            go._clear_pycache(path)
        if smoke:
            check = go._run_bounded(_guarded(smoke), ctx.root, deadline, ceiling)
            if check.verdict or check.returncode != 0:
                return GroupRun(
                    error="the smoke command failed on the mutated tree: "
                    "the mutation broke the build\n" + _tail(check.output),
                    error_kind="smoke",
                )
        junit = ctx.scratch / f"junit-{ctx.suite_runs}.xml"
        ctx.suite_runs += 1
        result.outcome = go._run_bounded(
            _guarded(list(command) + [f"--junitxml={junit}"]), ctx.root, deadline, ceiling
        )
        if result.outcome.verdict:
            if probe:
                result.probe = go._run_bounded(
                    _guarded(list(command) + ["--collect-only", "-q"]),
                    ctx.root,
                    deadline,
                    ceiling,
                )
        else:
            result.failures = parse_junit(junit)
    except go.CommandNotRunnable as exc:
        return GroupRun(error=str(exc), error_kind="command-not-found")
    finally:
        problems: list[BaseException] = []
        for path in combined:
            # ``landed`` can lag the replace by one signal; the bytes on disk are the truth.
            try:
                if path in landed or path.read_bytes() != saved[path].data:
                    go._restore(path, saved[path])
            except BaseException as exc:  # noqa: BLE001 - keep restoring the others, then raise
                problems.append(exc)
            else:
                go._ACTIVE.pop(path, None)
                go._journal_path(path).unlink(missing_ok=True)
        if problems:
            raise problems[0]
    return result


# --------------------------------------------------------------------------- judging runs


def _single_row(mutation: BatchMutation, run: GroupRun, batch: int | None, fallback: str) -> Row:
    """The verdict on one mutation run alone."""
    base = dict(label=mutation.label, file=mutation.file, how="single", batch=batch)
    base["fallback"] = fallback
    if run.error:
        kind = {"smoke": "smoke", "unwritable": "unwritable"}.get(run.error_kind, run.error_kind)
        return Row(**base, status=INVALID, kind=kind, detail=run.error)  # type: ignore[arg-type]
    outcome = run.outcome
    if outcome.verdict:
        probe = run.probe
        if probe is not None and (probe.verdict or probe.returncode != 0):
            return Row(
                **base,  # type: ignore[arg-type]
                status=INVALID,
                kind="hung-at-import" if probe.verdict else "collection",
                detail="the mutation breaks collection (import time): "
                + (
                    f"it {probe.verdict} before any test ran"
                    if probe.verdict
                    else "it fails to collect"
                ),
                output_tail=_tail(probe.output),
            )
        return Row(
            **base,  # type: ignore[arg-type]
            status=CAUGHT,
            kind=outcome.verdict.lower(),
            detail=(
                f"{outcome.verdict}: killed after {outcome.elapsed:.1f}s; "
                f"peak RSS {outcome.peak_rss_mb:.0f} MB"
            ),
            peak_rss_mb=outcome.peak_rss_mb,
            output_tail=_tail(outcome.output),
        )
    verdict = go.classify(outcome.returncode, outcome.output, "pytest")
    row = Row(
        **base,  # type: ignore[arg-type]
        status=verdict.status,
        kind=verdict.kind,
        detail=verdict.detail,
        failed=verdict.failed,
        errors=verdict.errors,
        output_tail=verdict.output_tail,
    )
    if verdict.status == CAUGHT and run.failures:
        row.failing_tests = [f.id for f in run.failures]
    return row


def _run_single(ctx: Context, mutation: BatchMutation, batch: int | None, fallback: str) -> Row:
    return _single_row(mutation, run_group(ctx, [mutation], probe=True), batch, fallback)


def execute_batch(
    ctx: Context, mutations: Sequence[BatchMutation], batch: Sequence[int]
) -> dict[int, Row]:
    """Run one planned batch; returns a Row for every index in it."""
    ctx.batch_counter += 1
    number = ctx.batch_counter
    members = [mutations[i] for i in batch]
    if len(batch) == 1:
        return {batch[0]: _run_single(ctx, members[0], number, "")}

    def alone(reason: str) -> dict[int, Row]:
        return {i: _run_single(ctx, mutations[i], number, reason) for i in batch}

    run = run_group(ctx, members)
    if run.error:
        return alone(f"the batch could not run ({run.error.splitlines()[0]})")
    outcome = run.outcome
    if outcome.verdict:
        return alone(
            f"the batch hit {outcome.verdict} (peak RSS {outcome.peak_rss_mb:.0f} MB): "
            "it cannot be attributed to a member"
        )
    verdict = go.classify(outcome.returncode, outcome.output, "pytest")
    if verdict.status == INVALID:
        return alone(f"the batch did not run cleanly ({verdict.kind}: {verdict.detail})")
    if verdict.status == SURVIVED:
        rows = {
            i: Row(
                label=mutations[i].label,
                file=mutations[i].file,
                status=SURVIVED,
                kind="survived",
                detail=f"the whole batch stayed green ({verdict.detail})",
                how="batch",
                batch=number,
            )
            for i in batch
        }
        return _verify_survivors(ctx, mutations, rows, number)
    if not run.failures:
        return alone("pytest reported failures but the junit report holds none")

    attribution = attribute(run.failures, members)
    rows: dict[int, Row] = {}
    for position, i in enumerate(batch):
        decision, reason = judge(attribution, position)
        mutation = mutations[i]
        if decision == "caught":
            ids = attribution.exclusive[position]
            rows[i] = Row(
                label=mutation.label,
                file=mutation.file,
                status=CAUGHT,
                kind="failed",
                detail=f"{len(ids)} failing test(s) name it; first: {ids[0]}",
                how="batch",
                batch=number,
                failing_tests=ids,
                failed=sum(1 for f in run.failures if f.id in ids and "failure" in f.kinds),
                errors=sum(1 for f in run.failures if f.id in ids and "error" in f.kinds),
            )
        elif decision == "survived":
            rows[i] = Row(
                label=mutation.label,
                file=mutation.file,
                status=SURVIVED,
                kind="survived",
                detail=(
                    f"no failing test names it; the batch's {len(run.failures)} failure(s) "
                    "all belong to other mutations"
                ),
                how="batch",
                batch=number,
            )
        else:
            rows[i] = _run_single(ctx, mutation, number, reason)
    survivors = {i: r for i, r in rows.items() if r.status == SURVIVED and r.how == "batch"}
    rows.update(_verify_survivors(ctx, mutations, survivors, number))
    return rows


def _verify_survivors(
    ctx: Context, mutations: Sequence[BatchMutation], rows: dict[int, Row], number: int
) -> dict[int, Row]:
    if not ctx.verify_survivors:
        return rows
    return {
        i: _run_single(ctx, mutations[i], number, "--verify-survivors")
        if row.status == SURVIVED
        else row
        for i, row in rows.items()
    }


# --------------------------------------------------------------------------- git guard


#: Variables git exports to hooks that would aim ``git -C <dir>`` at another repository (#3179).
_GIT_EXECUTION_VARS = (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_INDEX_VERSION",
    "GIT_NAMESPACE",
)


def _git_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in _GIT_EXECUTION_VARS}


def git_dirty(files: Sequence[Path]) -> tuple[list[str], bool]:
    """(dirty lines, verified). ``verified`` is False when git could not answer (not a work tree)."""
    dirty: list[str] = []
    verified = True
    by_dir: dict[Path, list[str]] = {}
    for file in files:
        by_dir.setdefault(file.parent, []).append(file.name)
    for directory, names in by_dir.items():
        try:
            proc = subprocess.run(
                [
                    "git",
                    "-C",
                    str(directory),
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                    "--",
                ]
                + names,
                capture_output=True,
                text=True,
                check=False,
                env=_git_env(),
            )
        except OSError:
            # No git binary: nothing can be verified, and the caller says so.
            verified = False
            continue
        if proc.returncode != 0:
            verified = False
            continue
        dirty.extend(line for line in proc.stdout.splitlines() if line.strip())
    return dirty, verified


# --------------------------------------------------------------------------- orchestration


def validate(
    mutations: Sequence[BatchMutation], root: Path
) -> tuple[list[BatchMutation], list[Row], dict[int, tuple[Path, int, int]]]:
    """Split the spec into runnable mutations (with their text spans) and INVALID rows."""
    runnable: list[BatchMutation] = []
    invalid: list[Row] = []
    spans: dict[int, tuple[Path, int, int]] = {}
    for mutation in mutations:
        if mutation.expect != 1:
            problem = Row(
                mutation.label,
                mutation.file,
                INVALID,
                "text-not-found",
                f"batch mode needs the text to match exactly once (expect is {mutation.expect})",
            )
        else:
            result, _ = go._check_applies(mutation, root)
            problem = (
                Row(mutation.label, mutation.file, INVALID, result.kind, result.detail)
                if result
                else None
            )
        if problem:
            invalid.append(problem)
            continue
        path = (root / mutation.file).resolve()
        span = _locate(path.read_bytes(), mutation)
        spans[len(runnable)] = (path, span[0], span[1])  # type: ignore[index]
        runnable.append(mutation)
    return runnable, invalid, spans


def _run_plan(
    ctx: Context,
    mutations: Sequence[BatchMutation],
    plan: Sequence[Sequence[int]],
    on_row: Callable[[Row], None] | None,
) -> dict[int, Row]:
    rows: dict[int, Row] = {}
    for batch in plan:
        done = execute_batch(ctx, mutations, batch)
        rows.update(done)
        if on_row:
            for i in batch:
                on_row(done[i])
    return rows


def _split_plan(plan: Sequence[Sequence[int]], jobs: int) -> list[list[list[int]]]:
    """Deal batches to ``jobs`` workers, largest first, to the least loaded."""
    shares: list[list[list[int]]] = [[] for _ in range(jobs)]
    loads = [0] * jobs
    for batch in sorted(plan, key=len, reverse=True):
        slot = loads.index(min(loads))
        shares[slot].append(list(batch))
        loads[slot] += len(batch)
    return shares


@dataclasses.dataclass
class Report:
    rows: list[Row]
    suite_runs: int
    control_runs: int
    naive_runs: int


def run_batches(
    mutations: Sequence[BatchMutation],
    *,
    root: Path,
    command: Sequence[str],
    deadline: float = go.DEFAULT_DEADLINE,
    rss_mb: float = go.DEFAULT_RSS_MB,
    max_batch: int = DEFAULT_MAX_BATCH,
    control: bool = True,
    verify_survivors: bool = False,
    git_guard: bool = True,
    jobs: int = 1,
    copy_ignore: Sequence[str] = DEFAULT_COPY_IGNORE,
    on_row: Callable[[Row], None] | None = None,
) -> tuple[Report, Row | None]:
    """Run every mutation in as few full runs as attribution allows.

    Returns (report, baseline problem or None). Raises ``go.TargetLocked`` /
    ``go.StaleJournal`` (exit 4), ``go.RestoreError`` (exit 3) and ``DirtyTree`` (exit 5).
    """
    if jobs < 1 or jobs > MAX_JOBS:
        raise ValueError(f"--jobs must be 1 or {MAX_JOBS}: the machine is shared")
    root = Path(root).resolve()
    runnable, invalid_rows, spans = validate(mutations, root)
    if on_row:
        for row in invalid_rows:
            on_row(row)
    targets = sorted({spans[i][0] for i in spans})
    directories = sorted({t.parent for t in targets})
    guards: list[Any] = []
    rows: dict[int, Row] = {}
    scratch = Path(tempfile.mkdtemp(prefix="gate-off-batch-"))
    ctx = Context(root, list(command), deadline, rss_mb, scratch, max_batch, verify_survivors)
    control_runs = 0
    finished = False
    started = False  # True once the tree passed the pre-checks and may be mutated
    worker: _Worker | None = None

    with go._TerminationAsExit():
        try:
            for directory in directories:
                guards.append(go._lock_dir(directory))
            for target in targets:
                go._recover(target)
            if git_guard and targets:
                dirty, verified = git_dirty(targets)
                if dirty:
                    raise DirtyTree(
                        "refusing to start: a mutated file has uncommitted changes, so a "
                        "restore could not be told from them (commit or stash them, or pass "
                        "--allow-dirty to rely on the SHA-256 check alone):\n  "
                        + "\n  ".join(dirty)
                    )
                if not verified:
                    print(
                        "gate-off-batch: not inside a git work tree; the restore is verified "
                        "by SHA-256 only",
                        file=sys.stderr,
                    )
            started = True
            if control and runnable:
                problem = go._baseline(runnable, root, list(command), deadline, rss_mb, "pytest")
                control_runs = 1
                if problem:
                    return Report([], 0, control_runs, 0), Row(
                        "baseline",
                        "",
                        INVALID,
                        "baseline",
                        problem.detail,
                        output_tail=problem.output_tail,
                    )

            plan = plan_batches(runnable, spans, max_batch)
            shares = _split_plan(plan, jobs) if jobs > 1 else [list(plan)]
            if jobs > 1 and len(plan) > 1:
                worker = _Worker.start(runnable, shares[1], root, ctx, copy_ignore)
            else:
                shares = [list(plan)]
            rows = _run_plan(ctx, runnable, shares[0], on_row)
            suite_runs = ctx.suite_runs
            if worker is not None:
                for index, row in worker.finish(ctx.batch_counter).items():
                    rows[index] = row
                    if on_row:
                        on_row(row)
                suite_runs += worker.suite_runs
            finished = True
        finally:
            if worker is not None:
                worker.stop()
            for guard in guards:
                go._release(guard)
            shutil.rmtree(scratch, ignore_errors=True)
            if git_guard and targets and started:
                dirty, _ = git_dirty(targets)
                if dirty:
                    message = "a mutated file differs from git after the run:\n  " + "\n  ".join(
                        dirty
                    )
                    if finished:
                        raise go.RestoreError(message)
                    print(f"gate-off-batch: {message}", file=sys.stderr)

    ordered = invalid_rows + [rows[i] for i in sorted(rows)]
    return Report(ordered, suite_runs, control_runs, len(runnable)), None


class _Worker:
    """The second worker: this script, run in a copy of the tree, on its share of the batches."""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.workdir: Path | None = None
        self.report_path: Path | None = None
        self.sink: Any = None
        self.suite_runs = 0
        self.labels: list[str] = []
        self.indices: list[int] = []

    @classmethod
    def start(
        cls,
        mutations: Sequence[BatchMutation],
        share: Sequence[Sequence[int]],
        root: Path,
        ctx: Context,
        copy_ignore: Sequence[str],
    ) -> _Worker:
        worker = cls()
        worker.indices = [i for batch in share for i in batch]
        worker.labels = [mutations[i].label for i in worker.indices]
        worker.workdir = Path(tempfile.mkdtemp(prefix="gate-off-batch-worker-"))
        tree = worker.workdir / "tree"
        shutil.copytree(root, tree, symlinks=True, ignore=shutil.ignore_patterns(*copy_ignore))
        spec = {
            "root": str(tree),
            "command": ctx.command,
            "deadline": ctx.deadline,
            "rss_mb": ctx.rss_mb,
            "max_batch": ctx.max_batch,
            "mutations": [mutations[i].to_spec() for i in worker.indices],
        }
        spec_path = worker.workdir / "spec.json"
        spec_path.write_text(json.dumps(spec))
        worker.report_path = worker.workdir / "report.json"
        worker.sink = open(worker.workdir / "worker.log", "wb")  # noqa: SIM115 - closed in stop()
        args = [sys.executable, str(Path(__file__).resolve()), str(spec_path)]
        args += ["--no-control", "--allow-dirty", "--json", str(worker.report_path)]
        if ctx.verify_survivors:
            args.append("--verify-survivors")
        worker.proc = subprocess.Popen(
            args, stdout=worker.sink, stderr=subprocess.STDOUT, start_new_session=True
        )
        return worker

    def finish(self, batch_offset: int) -> dict[int, Row]:
        assert self.proc is not None and self.report_path is not None
        self.proc.wait()
        try:
            data = json.loads(self.report_path.read_text())
        except (OSError, ValueError) as exc:
            log = (self.workdir / "worker.log").read_text(errors="replace") if self.workdir else ""
            raise RuntimeError(
                f"the second worker produced no report (exit {self.proc.returncode}): {exc!r}\n"
                + _tail(log, 20)
            ) from exc
        self.suite_runs = int(data.get("suite_runs", 0))
        by_label = {row["label"]: row for row in data["rows"]}
        out: dict[int, Row] = {}
        for index, label in zip(self.indices, self.labels, strict=True):
            row = Row(**by_label[label])
            if row.batch is not None:
                row.batch += batch_offset  # keep the two workers' batch numbers apart
            out[index] = row
        return out

    def stop(self) -> None:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            # A TERM lets the worker's own handlers restore its (copied) files and kill its
            # test group; the KILL is the backstop if it does not exit.
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self.sink is not None:
            self.sink.close()
        if self.workdir is not None:
            shutil.rmtree(self.workdir, ignore_errors=True)


# --------------------------------------------------------------------------- reporting


def format_row(row: Row, *, verbose: bool = False) -> str:
    if row.batch is not None:
        how = f"  [batch {row.batch}, {row.how}]"
    else:
        how = f"  [{row.how}]" if row.how else ""
    lines = [f"{row.status:<9} {row.label}  --  {row.detail}{how}"]
    if row.fallback:
        lines.append(f"            re-run alone: {row.fallback}")
    if row.status == CAUGHT and row.failing_tests:
        shown = ", ".join(row.failing_tests[:3])
        more = len(row.failing_tests) - 3
        lines.append(f"            caught by: {shown}" + (f" (+{more} more)" if more > 0 else ""))
    if verbose and row.status != CAUGHT and row.output_tail:
        lines.extend(f"            | {t}" for t in row.output_tail.splitlines())
    return "\n".join(lines)


def summarize(report: Report) -> dict[str, Any]:
    rows = report.rows
    count = lambda status: sum(1 for r in rows if r.status == status)  # noqa: E731
    return {
        "caught": count(CAUGHT),
        "hung": sum(1 for r in rows if r.status == CAUGHT and r.kind == "hung"),
        "runaway_memory": sum(1 for r in rows if r.status == CAUGHT and r.kind == "runaway-memory"),
        "survived": count(SURVIVED),
        "invalid": count(INVALID),
        "suite_runs": report.suite_runs,
        "control_runs": report.control_runs,
        "naive_runs": report.naive_runs,
    }


def format_summary(report: Report) -> str:
    s = summarize(report)
    bounded = []
    if s["hung"]:
        bounded.append(f"{s['hung']} HUNG")
    if s["runaway_memory"]:
        bounded.append(f"{s['runaway_memory']} RUNAWAY-MEMORY")
    extra = f" ({', '.join(bounded)})" if bounded else ""
    lines = [
        f"summary: {s['caught']} CAUGHT{extra}, {s['survived']} SURVIVED, {s['invalid']} INVALID; "
        f"{s['suite_runs']} full test run(s) for {s['naive_runs']} runnable mutation(s) "
        f"(one run each would be {s['naive_runs']}), plus {s['control_runs']} baseline run(s)"
    ]
    if s["survived"]:
        lines.append(
            "a SURVIVED mutation is a question, not a pass: equivalent mutation, two mechanisms "
            "shadowing each other, or coverage genuinely missing? Attribution is by name; see the "
            "limits in the docstring."
        )
    return "\n".join(lines)


def report_to_json(report: Report, spec_path: Path, root: Path) -> dict[str, Any]:
    return {
        "spec": str(spec_path),
        "root": str(root),
        "summary": summarize(report),
        "suite_runs": report.suite_runs,
        "control_runs": report.control_runs,
        "naive_runs": report.naive_runs,
        "rows": [dataclasses.asdict(r) for r in report.rows],
    }


# --------------------------------------------------------------------------- CLI


def _command_problem(command: Sequence[str]) -> str | None:
    for token in command:
        if token in ("-x", "--exitfirst") or token.startswith("--maxfail"):
            return f"{token!r} stops at the first failure, so the other mutations of a batch could never be attributed"
    return None


def mutations_from_spec(spec: dict[str, Any]) -> list[BatchMutation]:
    out: list[BatchMutation] = []
    for raw in spec.get("mutations", []):
        names = raw.get("names", [])
        names = [names] if isinstance(names, str) else [str(n) for n in names]
        test_ids = raw.get("test_ids", [])
        test_ids = [test_ids] if isinstance(test_ids, str) else [str(t) for t in test_ids]
        component = raw.get("component")
        if component and not names and not test_ids:
            names = [str(component)]
        out.append(
            BatchMutation(
                label=raw["label"],
                file=raw["file"],
                old=raw["old"],
                new=raw["new"],
                command=go._as_command(raw.get("command")),
                deadline=raw.get("deadline"),
                rss_mb=raw.get("rss_mb"),
                expect=int(raw.get("expect", 1)),
                smoke=go._as_command(raw.get("smoke")),
                component=str(component) if component else None,
                names=names,
                test_ids=test_ids,
            )
        )
    return out


def default_report_path(spec: Path) -> Path:
    return spec.with_name(spec.name + ".gateoff-report.json")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="mutation list (.json or .toml)")
    parser.add_argument("--only", help="run only mutations whose label contains this text")
    parser.add_argument(
        "--survivors-only",
        action="store_true",
        help="re-run, one at a time, the mutations the earlier report (--json) lists as SURVIVED",
    )
    parser.add_argument(
        "--verify-survivors",
        action="store_true",
        help="re-run every batch survivor alone before reporting it",
    )
    parser.add_argument("--jobs", type=int, default=1, help=f"1 or {MAX_JOBS} (default 1)")
    parser.add_argument(
        "--json", type=Path, help="report path (default: <spec>.gateoff-report.json)"
    )
    parser.add_argument(
        "--max-batch", type=int, help="most mutations per batch (overrides the spec)"
    )
    parser.add_argument("--no-control", action="store_true", help="skip the unmutated baseline run")
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="do not require the mutated files to be clean in git (SHA-256 check only)",
    )
    parser.add_argument("--deadline", type=float, help="seconds per run (overrides the spec)")
    parser.add_argument(
        "--rss-mb", type=float, help="RSS ceiling per run in MB (overrides the spec)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="show output tails of non-CAUGHT runs"
    )
    args = parser.parse_args(argv)

    if args.jobs < 1 or args.jobs > MAX_JOBS:
        print(f"gate-off-batch: --jobs must be 1 or {MAX_JOBS}", file=sys.stderr)
        return EXIT_BAD
    try:
        spec = go.load_spec(args.spec)
        mutations = mutations_from_spec(spec)
        command = go._as_command(spec.get("command")) or go._as_command(DEFAULT_COMMAND) or []
        command = command + [str(t) for t in spec.get("test_files", [])]
        if not any(token.startswith("--tb") for token in command):
            command.append("--tb=short")
    except (OSError, ValueError, KeyError) as exc:
        print(f"gate-off-batch: bad spec {args.spec}: {exc!r}", file=sys.stderr)
        return EXIT_BAD
    if spec.get("runner", "pytest") != "pytest":
        print("gate-off-batch: only the pytest runner can be attributed", file=sys.stderr)
        return EXIT_BAD
    problem = _command_problem(command)
    for mutation in mutations:
        problem = problem or (_command_problem(mutation.command) if mutation.command else None)
    if problem:
        print(f"gate-off-batch: {problem}", file=sys.stderr)
        return EXIT_BAD
    labels = [m.label for m in mutations]
    if len(set(labels)) != len(labels):
        print("gate-off-batch: mutation labels must be unique", file=sys.stderr)
        return EXIT_BAD

    report_path = args.json or default_report_path(args.spec)
    previous: dict[str, Any] | None = None
    if args.survivors_only:
        try:
            previous = json.loads(report_path.read_text())
        except (OSError, ValueError) as exc:
            print(
                f"gate-off-batch: --survivors-only needs the earlier report {report_path}: {exc!r}",
                file=sys.stderr,
            )
            return EXIT_BAD
        wanted = {r["label"] for r in previous["rows"] if r["status"] == SURVIVED}
        mutations = [m for m in mutations if m.label in wanted]
        if not mutations:
            print("gate-off-batch: the earlier report lists no SURVIVED mutation")
            return EXIT_OK
    if args.only:
        mutations = [m for m in mutations if args.only in m.label]
    if not mutations:
        print("gate-off-batch: no mutations to run", file=sys.stderr)
        return EXIT_BAD

    root = Path(spec.get("root", "."))
    deadline = args.deadline or float(spec.get("deadline", go.DEFAULT_DEADLINE))
    rss_mb = args.rss_mb or float(spec.get("rss_mb", go.DEFAULT_RSS_MB))
    max_batch = (
        1
        if args.survivors_only
        else (args.max_batch or int(spec.get("max_batch", DEFAULT_MAX_BATCH)))
    )

    try:
        report, baseline = run_batches(
            mutations,
            root=root,
            command=command,
            deadline=deadline,
            rss_mb=rss_mb,
            max_batch=max_batch,
            control=not args.no_control,
            verify_survivors=args.verify_survivors,
            git_guard=not args.allow_dirty,
            jobs=args.jobs,
            copy_ignore=tuple(spec.get("copy_ignore", DEFAULT_COPY_IGNORE)),
            on_row=lambda r: print(format_row(r, verbose=args.verbose), flush=True),
        )
    except go.RestoreError as exc:
        print(f"gate-off-batch: RESTORE FAILED: {exc}", file=sys.stderr)
        return EXIT_RESTORE
    except (go.TargetLocked, go.StaleJournal) as exc:
        print(f"gate-off-batch: REFUSED: {exc}", file=sys.stderr)
        return EXIT_LOCKED
    except DirtyTree as exc:
        print(f"gate-off-batch: {exc}", file=sys.stderr)
        return EXIT_DIRTY
    if baseline:
        print(f"gate-off-batch: BASELINE: {baseline.detail}", file=sys.stderr)
        if baseline.output_tail:
            print(baseline.output_tail, file=sys.stderr)
        return EXIT_BAD

    if previous is not None:
        # A confirmation run replaces only the rows it re-ran; everything else stays as it was.
        redone = {r.label: r for r in report.rows}
        kept = [Row(**r) for r in previous["rows"] if r["label"] not in redone]
        report = Report(
            kept + list(redone.values()),
            report.suite_runs + int(previous.get("suite_runs", 0)),
            int(previous.get("control_runs", 0)),
            int(previous.get("naive_runs", 0)),
        )
    report_path.write_text(json.dumps(report_to_json(report, args.spec, root), indent=2) + "\n")
    print(format_summary(report))
    print(f"report: {report_path}")
    return EXIT_OK if all(r.status == CAUGHT for r in report.rows) else EXIT_NOT_CAUGHT


if __name__ == "__main__":
    sys.exit(main())
