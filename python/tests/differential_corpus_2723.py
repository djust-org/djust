"""The differential's expensive runs, shared across the tests that read them (#2723).

Two subprocess runs of ``scripts/filter-parity-differential.py`` dominate the
suite tail: the full corpus SWEEP (``script out.json`` — ~415,000 cells
rendered through both engines, ~60 s) and the MANIFEST (``--manifest --json``,
~25,000 renders, ~3 s). ``test_differential_reachability_manifest_2345.py``
reads both many times over and ``test_refusal_collapsed_agreement_2454.py``
reads the sweep once more; before #2723 every reader ran its own copy of the
identical subprocess.

:class:`CorpusCache` runs each DISTINCT input once. The key is the script's
TEXT (a mutated copy at a fresh ``tmp_path`` with the same edits is the same
input), the ``_rust`` build the subprocess would load, and the argv. The
``corpus`` fixture in ``conftest.py`` roots it under the SESSION's pytest
basetemp, so xdist workers of one session share entries and a later session
starts clean. A mutated copy is a different text and keeps its own run.

Always a SUBPROCESS, never an import of the script: it calls
``settings.configure`` and appends a ``Library`` to Django's default engine at
import time, which would mutate the global filter registry for every other
test in the session.
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable

REPO = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "filter-parity-differential.py"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "python"), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
    )
    return env


def run_manifest(script: pathlib.Path = SCRIPT, *args: str) -> dict:
    """The manifest the tool emits, as data."""
    proc = subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [sys.executable, str(script), "--manifest", "--json", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
    )
    assert proc.returncode == 0, f"the manifest run failed:\n{proc.stderr[-4000:]}"
    return json.loads(proc.stdout)


def run_sweep(script: pathlib.Path, out: pathlib.Path) -> None:
    """The full corpus sweep, written to `out` — the results file `--compare` reads."""
    subprocess.run(  # noqa: S603 — a repo file, argv list, no shell
        [sys.executable, str(script), str(out)],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=str(REPO),
        check=True,
    )


@functools.lru_cache(maxsize=1)
def _build_digest() -> str:
    """The same digest the script records as `@@build`: the compiled `_rust`."""
    from djust import _rust

    return hashlib.sha256(pathlib.Path(_rust.__file__).read_bytes()).hexdigest()[:16]


class CorpusCache:
    """One subprocess run per DISTINCT input, shared across tests and xdist workers.

    The key is the script's TEXT (not its path — a mutated copy at a fresh
    `tmp_path` with the same edits is the same input), the `_rust` build the
    subprocess would load, and the argv. An entry is written under `root`
    with an atomic rename, behind a lock file so two workers that reach the
    same key at once start one run rather than two, and the second reads the
    first's result. A process that has read an entry keeps it in memory — the
    sweep is ~76 MB of JSON and is read by five cases.

    `runs` counts the subprocesses THIS process started, which is what the
    cache's own tests assert on.
    """

    def __init__(self, root: pathlib.Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._memo: dict[str, dict] = {}
        self.runs = 0

    @staticmethod
    def key(script: pathlib.Path, *args: str) -> str:
        digest = hashlib.sha256(script.read_bytes())
        digest.update(b"\0build=" + _build_digest().encode())
        for arg in args:
            digest.update(b"\0arg=" + arg.encode())
        return digest.hexdigest()[:24]

    def _entry(self, key: str, compute: Callable[[pathlib.Path], None]) -> dict:
        if key in self._memo:
            return self._memo[key]
        entry = self.root / f"{key}.json"
        if not entry.exists():
            with open(self.root / f"{key}.lock", "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if not entry.exists():
                    # Written to a per-process name and renamed: a reader never
                    # sees a half-written entry, whichever worker wrote it.
                    partial = self.root / f"{key}.{os.getpid()}.partial"
                    compute(partial)
                    self.runs += 1
                    os.replace(partial, entry)
        data = json.loads(entry.read_text(encoding="utf-8"))
        self._memo[key] = data
        return data

    def manifest(self, script: pathlib.Path = SCRIPT, *args: str) -> dict:
        """`run_manifest(script, *args)`, once per distinct input."""

        def compute(partial: pathlib.Path) -> None:
            partial.write_text(json.dumps(run_manifest(script, *args)), encoding="utf-8")

        return self._entry(self.key(script, "--manifest", "--json", *args), compute)

    def sweep(self, script: pathlib.Path = SCRIPT) -> dict:
        """The results file of a full sweep of `script`, once per distinct input."""
        return self._entry(self.key(script, "<sweep>"), lambda partial: run_sweep(script, partial))
