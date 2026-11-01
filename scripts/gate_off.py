#!/usr/bin/env python3
"""A shared gate-off harness: state mutations as data, get CAUGHT / SURVIVED / INVALID (#3714).

A gate-off reverts (or breaks) the change under test and re-runs the tests that are
supposed to guard it. Every one of them must go red. CLAUDE.md states the rules a
harness has to follow (#2129, #2135, #2230, #2233, #2730); five agent PRs in
v1.3.0-15 each wrote a throwaway harness and re-learned them. This is the one copy.

Usage::

    python scripts/gate_off.py mutations.json [--only TEXT] [--no-control]
                               [--deadline SECONDS] [--rss-mb MB] [--verbose]

``mutations.json`` (or ``.toml``)::

    {
      "root": ".",                                  # files are relative to this (default: cwd)
      "command": ["{python}", "-m", "pytest", "-q", "tests/test_thing.py"],   # {python} = this interpreter
      "deadline": 300,                              # seconds per run (default 300)
      "rss_mb": 4096,                               # RSS ceiling per run, MB (default 4096)
      "mutations": [
        {"label": "what this breaks",
         "file": "pkg/thing.py",
         "old": "exact text to find",              # must occur exactly ``expect`` times (default 1)
         "new": "its replacement"}                 # optional per-mutation: command, deadline, rss_mb, expect, smoke
      ]
    }

Outcomes, one per mutation:

* ``CAUGHT``   the tests went red (failures and/or errors counted), OR the run hit the
  deadline (``HUNG``) or the RSS ceiling (``RUNAWAY-MEMORY``). The last two carry the
  measurement and count as caught: a runaway under gate-off can only happen when the suite
  reaches the unbounded path, which is the strongest proof the fix is load-bearing.
* ``SURVIVED`` the tests stayed green. This is a question, not a pass: was the mutation
  equivalent, do two mechanisms shadow each other, or is coverage missing? The harness
  cannot tell those apart; you can.
* ``INVALID``  the mutation proved nothing and no number is reported: the text was not
  found (or found the wrong number of times), the replacement changed nothing, a ``.py``
  target no longer compiles, the optional ``smoke`` command failed on the mutated tree, the
  tests failed to *collect* (pytest prints ``1 error``, not ``1 failed``), no test ran, or
  the baseline run was not green, the command could not be started, or the mutation made the
  run hang or blow the RSS ceiling *while collecting* (an import-time hang is the same
  "broke the module" as an import-time raise; a loop that runs inside a test is ``HUNG``).

Spec extras: ``"runner": "pytest"`` (default) reads pytest's summary; ``"runner": "exit"``
reads only the exit status (for ``cargo test`` and the like: nonzero is ``CAUGHT``, 126/127
are ``INVALID``, and a build break is indistinguishable from a failure, so pair it with
``smoke``).

Guarantees (each pinned by ``tests/unit/test_gate_off_3714.py``, and the harness is
gate-off'd with itself: ``scripts/gate-off-selfcheck.json``):

* The mutation is applied to the real file in place and the saved bytes are restored in a
  ``finally`` (also on SIGINT, SIGTERM, SIGHUP and ``atexit``), then verified by SHA-256.
  A restore that does not match raises ``RestoreError`` and exits 3. A symlinked target is
  resolved first: the real file is mutated and the link is left alone.
* Two runs can never mutate the same file at once, and a killed run cannot leave a mutant
  that the next run adopts as the original. Before the first byte is written the harness
  holds an exclusive ``flock`` on a per-target lock and writes a journal (the original bytes
  and the mutant's hash), both in a state directory OUTSIDE the tree (``$GATE_OFF_STATE_DIR``,
  else ``$XDG_CACHE_HOME/gate-off``, else ``~/.cache/gate-off``). A run that finds the lock
  held exits 4 without touching anything. A run that finds a journal but no lock holder (the
  previous run was SIGKILLed) restores the original from the journal BEFORE it snapshots the
  file, or exits 4 if the file matches neither the original nor the mutant (it was edited
  since). The journal is deleted only after a verified restore.
* Writes go to a temp file in the same directory and ``os.replace`` into place: a new
  inode, a fresh mtime (never the original's, which cargo and ``.pyc`` validation would
  treat as already built from the mutant) and mode preserved. Nothing is ``cp``'d over a
  file another process may have mapped.
* ``__pycache__`` entries for each touched file are removed before the run and after the
  restore, and children run with ``PYTHONDONTWRITEBYTECODE=1``.
* Each run is bounded by a deadline and an RSS ceiling (summed over the child's process
  group, sampled with ``ps``); the whole group is SIGKILLed. POSIX only.

Exit status: 0 all CAUGHT; 1 any SURVIVED or INVALID; 2 the baseline was red / bad usage;
3 a file could not be restored; 4 a target is locked by another run, or its journal cannot be
reconciled.
"""

from __future__ import annotations

import argparse
import atexit
import base64
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

CAUGHT = "CAUGHT"
SURVIVED = "SURVIVED"
INVALID = "INVALID"

DEFAULT_DEADLINE = 300.0
DEFAULT_RSS_MB = 4096.0
POLL_SECONDS = 0.1


class RestoreError(RuntimeError):
    """A mutated file could not be put back byte-for-byte."""


class TargetLocked(RuntimeError):
    """Another gate_off run holds this target (it may be mutated right now)."""


class StaleJournal(RuntimeError):
    """A journal from a killed run cannot be reconciled with the file on disk."""


class CommandNotRunnable(RuntimeError):
    """The test command could not be started at all (missing binary, not executable)."""


@dataclasses.dataclass
class Mutation:
    label: str
    file: str
    old: str
    new: str
    command: list[str] | None = None
    deadline: float | None = None
    rss_mb: float | None = None
    expect: int = 1
    smoke: list[str] | None = None


@dataclasses.dataclass
class Result:
    label: str
    status: str
    detail: str
    kind: str = ""  # "failed", "hung", "runaway-memory", "survived", or the INVALID reason class
    failed: int = 0
    errors: int = 0
    peak_rss_mb: float | None = None
    output_tail: str = ""


@dataclasses.dataclass
class RunOutcome:
    returncode: int | None
    output: str
    verdict: str | None  # None, "HUNG" or "RUNAWAY-MEMORY"
    elapsed: float
    peak_rss_mb: float


# --------------------------------------------------------------------------- classification

_COUNT = re.compile(r"(\d+) (failed|passed|errors?|skipped|xfailed|xpassed|deselected|warnings?)")
_COLLECTION = re.compile(
    r"errors? during collection|ERROR collecting|Interrupted:.*collection"
    r"|ImportError while loading conftest"
)


def parse_pytest_summary(output: str) -> dict[str, int] | None:
    """Counts from pytest's final summary line, or None when there is none."""
    for line in reversed(output.splitlines()):
        if re.search(r"\bin [\d.]+s\b", line) or "no tests ran" in line:
            counts: dict[str, int] = {}
            for number, word in _COUNT.findall(line):
                key = "errors" if word.startswith("error") else word.rstrip("s")
                counts[key] = counts.get(key, 0) + int(number)
            if counts or "no tests ran" in line:
                return counts
    return None


def classify(returncode: int | None, output: str, runner: str = "pytest") -> Result:
    """Turn one finished run into a Result. Pure: no I/O."""
    tail = "\n".join(output.rstrip().splitlines()[-12:])
    if runner == "exit":
        if returncode == 0:
            return Result("", SURVIVED, "exit 0", "survived", output_tail=tail)
        if returncode in (126, 127):
            return Result(
                "",
                INVALID,
                f"exit {returncode}: the command was not found or not executable",
                "not-executable",
                output_tail=tail,
            )
        return Result("", CAUGHT, f"exit {returncode}", "failed", output_tail=tail)

    if _COLLECTION.search(output):
        return Result(
            "",
            INVALID,
            "the tests did not collect (import or collection error): the mutation broke the module",
            "collection",
            output_tail=tail,
        )
    counts = parse_pytest_summary(output)
    if counts is None:
        return Result(
            "",
            INVALID,
            f"no pytest summary line in the output (exit {returncode})",
            "no-summary",
            output_tail=tail,
        )
    failed, errors = counts.get("failed", 0), counts.get("errors", 0)
    if returncode == 0:
        if counts.get("passed", 0) == 0:
            return Result(
                "",
                INVALID,
                "exit 0 but no test passed (all skipped/deselected?)",
                "nothing-ran",
                output_tail=tail,
            )
        return Result("", SURVIVED, f"{counts['passed']} passed", "survived", output_tail=tail)
    if returncode == 1 and (failed or errors):
        parts = [f"{failed} failed"] if failed else []
        if errors:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        first = re.search(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)
        detail = ", ".join(parts) + (f"; first: {first.group(1)}" if first else "")
        return Result("", CAUGHT, detail, "failed", failed=failed, errors=errors, output_tail=tail)
    return Result(
        "",
        INVALID,
        f"pytest exited {returncode} without a failure to count (interrupted, usage or internal error)",
        "bad-exit",
        output_tail=tail,
    )


# --------------------------------------------------------------------------- bounded runs


def _child_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _group_rss_mb(pgid: int) -> float:
    """Resident memory (MB) summed over every process in the process group."""
    try:
        out = subprocess.run(
            ["ps", "-A", "-o", "pgid=,rss="], capture_output=True, text=True, check=False
        ).stdout
    except OSError as exc:
        # Returning 0 here would silently turn the RSS ceiling off; refuse to run unbounded.
        raise RuntimeError(f"cannot sample RSS (is `ps` available? POSIX only): {exc}") from exc
    kib = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == str(pgid) and parts[1].isdigit():
            kib += int(parts[1])
    return kib / 1024.0


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        # The group is already gone (the normal case after a clean exit): nothing to kill.
        # PermissionError is NOT swallowed: a group we cannot kill is a real failure.
        return


def _run_bounded(command: Sequence[str], cwd: Path, deadline: float, rss_mb: float) -> RunOutcome:
    """Run ``command``; kill its whole process group at the deadline or the RSS ceiling."""
    with tempfile.TemporaryFile() as sink:
        try:
            proc = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=_child_env(),
                stdin=subprocess.DEVNULL,
                stdout=sink,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            raise CommandNotRunnable(f"cannot run {list(command)[:1]}: {exc}") from exc
        start = time.monotonic()
        peak = 0.0
        verdict: str | None = None
        try:
            while True:
                try:
                    proc.wait(timeout=POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    # Not an error: the poll interval elapsed and the child is still running.
                    peak = max(peak, _group_rss_mb(proc.pid))
                    if peak > rss_mb:
                        verdict = "RUNAWAY-MEMORY"
                        break
                    if time.monotonic() - start > deadline:
                        verdict = "HUNG"
                        break
        finally:
            # Whatever happened (verdict, SIGINT, an exception): nothing of the run survives.
            _kill_group(proc.pid)
            proc.wait()
        elapsed = time.monotonic() - start
        sink.seek(0)
        output = sink.read().decode("utf-8", "replace")
    return RunOutcome(None if verdict else proc.returncode, output, verdict, elapsed, peak)


# --------------------------------------------------------------------------- mutate / restore


@dataclasses.dataclass
class _Saved:
    data: bytes
    sha: str
    mode: int


#: Files currently mutated. ``atexit`` and the signal path restore what is left in it.
_ACTIVE: dict[Path, _Saved] = {}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(path: Path) -> _Saved:
    data = path.read_bytes()
    return _Saved(data, _sha(data), stat.S_IMODE(path.stat().st_mode))


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    """Temp file next to ``path``, then ``os.replace``: a new inode and a fresh mtime."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".gateoff")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        # Best-effort removal of OUR temp file; the original error is what matters and is
        # re-raised. ``missing_ok``: the failure may be the temp file never having existed.
        Path(tmp).unlink(missing_ok=True)
        raise


def _clear_pycache(path: Path) -> None:
    """Delete this file's bytecode so a stale ``.pyc`` cannot certify a mutation that never ran."""
    cache = path.parent / "__pycache__"
    if not cache.is_dir():
        return
    for entry in cache.glob(f"{path.stem}.*.pyc"):
        # ``missing_ok`` is the only tolerance: a stale .pyc we cannot delete (permissions)
        # could certify a mutation that never ran, so any other error propagates.
        entry.unlink(missing_ok=True)


def _restore(path: Path, saved: _Saved) -> None:
    """Put ``saved`` back and prove, by hash, that the file is byte-identical."""
    try:
        _write_atomic(path, saved.data, saved.mode)
        _clear_pycache(path)
        got = _sha(path.read_bytes())
    except OSError as exc:
        # A restore that fails must never be silent, whatever the cause.
        raise RestoreError(f"{path} could not be restored: {exc!r}") from exc
    if got != saved.sha:
        raise RestoreError(f"{path} was restored to sha256 {got}, expected {saved.sha}")


def _state_dir() -> Path:
    """Where locks and journals live: OUTSIDE the tree, so a mutant can never shadow them."""
    override = os.environ.get("GATE_OFF_STATE_DIR")
    base = (
        Path(override)
        if override
        else Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "gate-off"
    )
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    return base


def _key(path: Path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()[:32]


def _journal_path(path: Path) -> Path:
    return _state_dir() / f"{_key(path)}.journal"


@dataclasses.dataclass
class _Guard:
    path: Path
    fd: int


#: Targets this process holds the lock for.
_GUARDS: dict[Path, _Guard] = {}


def _journal_write(path: Path, saved: _Saved, mutated: bytes) -> None:
    record = {
        "path": str(path),
        "original_sha256": saved.sha,
        "mutant_sha256": _sha(mutated),
        "mode": saved.mode,
        "original_b64": base64.b64encode(saved.data).decode(),
    }
    _write_atomic(_journal_path(path), json.dumps(record).encode(), 0o600)


def _recover(path: Path) -> None:
    """Reconcile a journal left by a killed run BEFORE anything snapshots the file."""
    journal = _journal_path(path)
    if not journal.exists():
        return
    try:
        record = json.loads(journal.read_text())
        original = base64.b64decode(record["original_b64"])
        saved = _Saved(original, record["original_sha256"], int(record["mode"]))
        mutant_sha = record["mutant_sha256"]
    except (OSError, ValueError, KeyError) as exc:
        raise StaleJournal(
            f"{journal} exists but cannot be read ({exc!r}); {path} may still be mutated. "
            "Inspect it, restore the file by hand, then delete the journal."
        ) from exc
    current = _sha(path.read_bytes())
    if current == mutant_sha:
        _restore(path, saved)
        print(
            f"gate-off: recovered {path} from a stale journal (an earlier run was killed while "
            "it was mutated)",
            file=sys.stderr,
        )
    elif current != saved.sha:
        raise StaleJournal(
            f"{path} matches neither the original (sha256 {saved.sha}) nor the mutant recorded "
            f"in {journal}: it was edited after a killed run. Inspect it, then delete the journal."
        )
    journal.unlink(missing_ok=True)


def _acquire(path: Path) -> _Guard:
    """Take the per-target lock (refusing if held), then recover a stale journal."""
    lock_path = _state_dir() / f"{_key(path)}.lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise TargetLocked(
            f"{path} is locked by another gate_off run ({lock_path}); it may be mutated right "
            "now, so this run refuses to start. Wait for it, or stop it and re-run."
        ) from exc
    guard = _Guard(path, fd)
    try:
        _recover(path)
    except BaseException:
        os.close(fd)
        raise
    _GUARDS[path] = guard
    return guard


def _release(guard: _Guard) -> None:
    _GUARDS.pop(guard.path, None)
    os.close(guard.fd)  # closing the descriptor drops the flock


def _restore_all_failsafe() -> None:
    """atexit / signal safety net: restore anything still mutated, never raise."""
    for path, saved in list(_ACTIVE.items()):
        try:
            _restore(path, saved)
            del _ACTIVE[path]
            _journal_path(path).unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - last line of defence, report and go on
            print(f"gate-off: COULD NOT RESTORE {path}: {exc}", file=sys.stderr)


atexit.register(_restore_all_failsafe)


class _TerminationAsExit:
    """Turn SIGINT/SIGTERM/SIGHUP into an exception so ``finally`` blocks run, even if SIGINT was ignored."""

    _SIGNALS = tuple(
        getattr(signal, name) for name in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, name)
    )

    def __enter__(self) -> _TerminationAsExit:
        self._old: dict[int, Any] = {}
        try:
            for number in self._SIGNALS:
                self._old[number] = signal.signal(number, self._raise)
        except ValueError:  # not the main thread
            self._old = {}
        return self

    @staticmethod
    def _raise(number: int, _frame: Any) -> None:
        if number == signal.SIGINT:
            raise KeyboardInterrupt
        raise SystemExit(128 + number)

    def __exit__(self, *exc: object) -> None:
        for number, old in self._old.items():
            signal.signal(number, old)


# --------------------------------------------------------------------------- the run


def _check_applies(mutation: Mutation, root: Path) -> tuple[Result | None, bytes]:
    """Validate the mutation against the file on disk. Returns (INVALID result | None, mutated bytes)."""
    path = (root / mutation.file).resolve()

    def invalid(reason: str, kind: str) -> tuple[Result, bytes]:
        return Result(mutation.label, INVALID, reason, kind), b""

    if not path.is_file():
        return invalid(f"{mutation.file} does not exist under {root}", "missing-file")
    original = path.read_bytes()
    old = mutation.old.encode()
    if not old:
        return invalid("the mutation text to find is empty", "text-not-found")
    found = original.count(old)
    if found != mutation.expect:
        hint = ""
        if found == 0 and b"\r\n" in original and b"\n" in old and b"\r\n" not in old:
            hint = " (the file uses CRLF line endings: write \\r\\n in the mutation text)"
        return invalid(
            f"mutation text found {found} time(s) in {mutation.file}, expected {mutation.expect}"
            + hint,
            "text-not-found",
        )
    mutated = original.replace(old, mutation.new.encode())
    if mutated == original:
        return invalid("no-op mutation: the replacement leaves the source unchanged", "no-op")
    if path.suffix == ".py":
        try:
            compile(mutated, str(path), "exec")
        except SyntaxError as exc:
            return invalid(
                f"the mutated source does not compile: {exc.msg} (line {exc.lineno})", "syntax"
            )
    return None, mutated


def run_mutations(
    mutations: Sequence[Mutation],
    *,
    root: Path,
    command: Sequence[str],
    deadline: float = DEFAULT_DEADLINE,
    rss_mb: float = DEFAULT_RSS_MB,
    runner: str = "pytest",
    control: bool = True,
    on_result: Callable[[Result], None] | None = None,
) -> tuple[list[Result], Result | None]:
    """Run every mutation. Returns (results, baseline problem or None).

    Every target is locked (and a stale journal reconciled) before anything runs; a held lock
    raises ``TargetLocked``. When ``control`` is true the unmutated tree then runs first for
    every distinct command; a baseline that is not green means no mutation can be judged and
    none is run.
    """
    root = Path(root).resolve()
    results: list[Result] = []
    targets = sorted({(root / m.file).resolve() for m in mutations if (root / m.file).is_file()})
    guards: list[_Guard] = []

    with _TerminationAsExit():
        try:
            for target in targets:
                guards.append(_acquire(target))

            if control:
                problem = _baseline(mutations, root, command, deadline, rss_mb, runner)
                if problem:
                    return results, problem

            for mutation in mutations:
                result = _run_one(mutation, root, command, deadline, rss_mb, runner)
                results.append(result)
                if on_result:
                    on_result(result)
        finally:
            for guard in guards:
                _release(guard)
    return results, None


def _baseline(
    mutations: Sequence[Mutation],
    root: Path,
    command: Sequence[str],
    deadline: float,
    rss_mb: float,
    runner: str,
) -> Result | None:
    seen: set[tuple[str, ...]] = set()
    for cmd in [list(command)] + [m.command for m in mutations if m.command]:
        key = tuple(cmd)
        if key in seen:
            continue
        seen.add(key)
        try:
            outcome = _run_bounded(cmd, root, deadline, rss_mb)
        except CommandNotRunnable as exc:
            return Result(
                "baseline", INVALID, f"the unmutated run could not start: {exc}", "baseline"
            )
        if outcome.verdict:
            return Result(
                "baseline", INVALID, f"the unmutated run hit {outcome.verdict}", "baseline"
            )
        verdict = classify(outcome.returncode, outcome.output, runner)
        if verdict.status != SURVIVED:
            return Result(
                "baseline",
                INVALID,
                f"the unmutated tree is not green ({verdict.detail}); nothing was mutated",
                "baseline",
                output_tail=verdict.output_tail,
            )
    return None


def _run_one(
    mutation: Mutation,
    root: Path,
    command: Sequence[str],
    deadline: float,
    rss_mb: float,
    runner: str,
) -> Result:
    invalid, mutated = _check_applies(mutation, root)
    if invalid:
        return invalid

    path = (root / mutation.file).resolve()
    cmd = mutation.command or list(command)
    limit = mutation.deadline if mutation.deadline is not None else deadline
    ceiling = mutation.rss_mb if mutation.rss_mb is not None else rss_mb

    saved = _snapshot(path)
    _journal_write(path, saved, mutated)
    _ACTIVE[path] = saved
    landed = False
    try:
        try:
            _write_atomic(path, mutated, saved.mode)
            landed = True
        except OSError as exc:
            if path.read_bytes() != saved.data:
                raise
            # The write failed before the replace: the file is untouched, so there is nothing
            # to restore and nothing to alarm about.
            return Result(
                mutation.label, INVALID, f"cannot write {mutation.file}: {exc}", "unwritable"
            )
        _clear_pycache(path)
        if mutation.smoke:
            smoke = _run_bounded(mutation.smoke, root, limit, ceiling)
            if smoke.verdict or smoke.returncode != 0:
                tail = "\n".join(smoke.output.rstrip().splitlines()[-6:])
                return Result(
                    mutation.label,
                    INVALID,
                    "the smoke command failed on the mutated tree: the mutation broke the build",
                    "smoke",
                    output_tail=tail,
                )
        outcome = _run_bounded(cmd, root, limit, ceiling)
        if outcome.verdict and runner == "pytest":
            # A hang or runaway is CAUGHT only if it happens while tests RUN. If collection
            # itself hangs or fails, the mutation broke the import, exactly like an import-time
            # raise (rule (d)): refuse to call that a catch.
            probe = _run_bounded(cmd + ["--collect-only", "-q"], root, limit, ceiling)
            if probe.verdict or probe.returncode != 0:
                return Result(
                    mutation.label,
                    INVALID,
                    "the mutation breaks collection (import time): "
                    + (
                        f"it {probe.verdict} before any test ran"
                        if probe.verdict
                        else "it fails to collect"
                    ),
                    "hung-at-import" if probe.verdict else "collection",
                    output_tail="\n".join(probe.output.rstrip().splitlines()[-6:]),
                )
    except CommandNotRunnable as exc:
        return Result(mutation.label, INVALID, str(exc), "command-not-found")
    finally:
        # ``landed`` can lag the replace by one signal; the bytes on disk are the truth.
        if landed or path.read_bytes() != saved.data:
            _restore(path, saved)
        _ACTIVE.pop(path, None)
        _journal_path(path).unlink(missing_ok=True)

    if outcome.verdict:
        return Result(
            mutation.label,
            CAUGHT,
            f"{outcome.verdict}: killed after {outcome.elapsed:.1f}s; peak RSS {outcome.peak_rss_mb:.0f} MB",
            outcome.verdict.lower(),
            peak_rss_mb=outcome.peak_rss_mb,
            output_tail="\n".join(outcome.output.rstrip().splitlines()[-6:]),
        )
    result = classify(outcome.returncode, outcome.output, runner)
    result.label = mutation.label
    return result


# --------------------------------------------------------------------------- reporting / CLI


def format_row(r: Result, *, verbose: bool = False) -> str:
    lines = [f"{r.status:<9} {r.label}  --  {r.detail}"]
    if verbose and r.status != CAUGHT and r.output_tail:
        lines.extend(f"            | {t}" for t in r.output_tail.splitlines())
    return "\n".join(lines)


def format_summary(results: Sequence[Result]) -> str:
    counts = {s: sum(1 for r in results if r.status == s) for s in (CAUGHT, SURVIVED, INVALID)}
    lines = [
        f"summary: {counts[CAUGHT]} CAUGHT, {counts[SURVIVED]} SURVIVED, {counts[INVALID]} INVALID"
    ]
    if counts[SURVIVED]:
        lines.append(
            "a SURVIVED mutation is a question, not a pass: equivalent mutation, two mechanisms "
            "shadowing each other, or coverage genuinely missing?"
        )
    return "\n".join(lines)


def format_table(results: Sequence[Result], *, verbose: bool = False) -> str:
    rows = [format_row(r, verbose=verbose) for r in results]
    return "\n".join(rows + [format_summary(results)])


def _as_command(value: Any) -> list[str] | None:
    if value is None:
        return None
    parts = shlex.split(value) if isinstance(value, str) else [str(v) for v in value]
    return [sys.executable if part == "{python}" else part for part in parts]


def load_spec(path: Path) -> dict[str, Any]:
    text = path.read_text()
    if path.suffix == ".toml":
        import tomllib

        return tomllib.loads(text)
    return json.loads(text)


def mutations_from_spec(spec: dict[str, Any]) -> list[Mutation]:
    out = []
    for raw in spec.get("mutations", []):
        out.append(
            Mutation(
                label=raw["label"],
                file=raw["file"],
                old=raw["old"],
                new=raw["new"],
                command=_as_command(raw.get("command")),
                deadline=raw.get("deadline"),
                rss_mb=raw.get("rss_mb"),
                expect=int(raw.get("expect", 1)),
                smoke=_as_command(raw.get("smoke")),
            )
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("spec", type=Path, help="mutation list (.json or .toml)")
    parser.add_argument("--only", help="run only mutations whose label contains this text")
    parser.add_argument("--no-control", action="store_true", help="skip the unmutated baseline run")
    parser.add_argument("--deadline", type=float, help="seconds per run (overrides the spec)")
    parser.add_argument(
        "--rss-mb", type=float, help="RSS ceiling per run in MB (overrides the spec)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="show output tails of non-CAUGHT runs"
    )
    args = parser.parse_args(argv)

    try:
        spec = load_spec(args.spec)
        mutations = mutations_from_spec(spec)
        command = _as_command(spec.get("command"))
    except (OSError, ValueError, KeyError) as exc:
        print(f"gate-off: bad spec {args.spec}: {exc!r}", file=sys.stderr)
        return 2
    if args.only:
        mutations = [m for m in mutations if args.only in m.label]
    if not mutations:
        print("gate-off: no mutations to run", file=sys.stderr)
        return 2
    if not command and not all(m.command for m in mutations):
        print(
            "gate-off: the spec needs a top-level 'command' (or one per mutation)", file=sys.stderr
        )
        return 2

    root = Path(spec.get("root", "."))
    deadline = args.deadline or float(spec.get("deadline", DEFAULT_DEADLINE))
    rss_mb = args.rss_mb or float(spec.get("rss_mb", DEFAULT_RSS_MB))
    runner = spec.get("runner", "pytest")

    try:
        results, baseline = run_mutations(
            mutations,
            root=root,
            command=command or [],
            deadline=deadline,
            rss_mb=rss_mb,
            runner=runner,
            control=not args.no_control,
            on_result=lambda r: print(format_row(r, verbose=args.verbose), flush=True),
        )
    except RestoreError as exc:
        print(f"gate-off: RESTORE FAILED: {exc}", file=sys.stderr)
        return 3
    except (TargetLocked, StaleJournal) as exc:
        print(f"gate-off: REFUSED: {exc}", file=sys.stderr)
        return 4
    if baseline:
        print(f"gate-off: BASELINE: {baseline.detail}", file=sys.stderr)
        if baseline.output_tail:
            print(baseline.output_tail, file=sys.stderr)
        return 2
    print(format_summary(results))
    return 0 if all(r.status == CAUGHT for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
