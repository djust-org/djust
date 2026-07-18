"""Regression pin: Makefile cargo targets route PYO3_PYTHON through
``scripts/embeddable-python.sh`` (closes #2082).

#2072/#2080 fixed the pre-commit ``cargo-test`` hook, which used to export
``PYO3_PYTHON="$(bash scripts/run-with-venv-python.sh --print)"`` -- the
project venv's interpreter. When the venv's BASE interpreter is uv's
python-build-standalone, any PyO3 test binary that embeds CPython (``cargo
test``/``cargo bench``/``cargo clippy``, which also embeds an interpreter via
build-time checks) fails bootstrap deterministically: ``Fatal Python error:
init_fs_encoding`` with a baked ``sys.prefix = '/install'``.

#2082 is the SAME fault, still present in ``Makefile``'s cargo-invoking
targets (``test`` (combined), ``test-rust``, ``lint``, ``benchmark-rust``),
which exported ``PYO3_PYTHON=$(PYTHON)`` -- the raw (unfiltered) project venv
interpreter, mirroring the exact pre-#2080 shape. The fix mirrors #2080
verbatim (#1077 lift-from-reference): a Make variable
``EMBEDDABLE_PYTHON = $(shell bash scripts/embeddable-python.sh)`` resolves a
safe interpreter, and every ``PYO3_PYTHON=`` assignment in the Makefile now
reads ``$(EMBEDDABLE_PYTHON)``.

``EMBEDDABLE_PYTHON`` is declared with recursive (``=``) assignment
deliberately, NOT eager (``:=``): the resolver script must run only when a
recipe line that actually references ``$(EMBEDDABLE_PYTHON)`` is expanded
(i.e. only for the cargo-invoking targets), not at PARSE time for every
``make`` invocation (including cheap targets like ``make help``).

Two test classes here:

1. Grep-pins against the literal Makefile text (``test_no_raw_pyo3_python_*``,
   ``test_every_pyo3_python_assignment_*``, ``test_embeddable_python_variable_*``)
   -- catch a future edit that reintroduces ``PYO3_PYTHON=$(PYTHON)`` or
   flips the variable to eager ``:=``.

2. An EMPIRICAL dry-run proof (``test_help_target_does_not_invoke_resolver*``,
   ``test_test_rust_target_does_invoke_resolver*``) that the wiring is
   load-bearing, not decorative (#1859/#1860 anti-drift-pin canon): it copies
   the real Makefile into an isolated temp directory alongside a STUB
   ``scripts/embeddable-python.sh`` that writes a marker file and prints a
   distinctive path, then runs ``make -n`` against two targets:

   - ``make -n help`` must NEVER invoke the stub (parse-time isolation --
     the load-bearing claim of the ``=`` vs ``:=`` choice).
   - ``make -n test-rust`` MUST invoke the stub, and the printed (dry-run)
     recipe line must contain the stub's distinctive resolved path -- proving
     ``PYO3_PYTHON`` is actually sourced from ``$(EMBEDDABLE_PYTHON)``, not
     just textually adjacent to it.

   This is the gate-off self-test (#1468) applied to a Makefile pin: if the
   marker-file logic weren't wired, both assertions would trivially pass with
   no marker ever created -- so the ``test_test_rust_target_does_invoke_*``
   test's assertion that the marker DOES exist is the non-tautological half.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
MAKEFILE = REPO_ROOT / "Makefile"

SUBPROCESS_TIMEOUT = 30

# The Makefile's cargo-invoking recipe lines that set PYO3_PYTHON. Any new
# cargo-invoking target added in the future must be counted here too (#1125
# bulk dispatch-site count-test pattern) -- a silent omission would leave a
# 7th unfixed PYO3_PYTHON=$(PYTHON) site that this file's other assertions
# would still catch, but bumping this count is the conscious acknowledgment.
EXPECTED_PYO3_PYTHON_ASSIGNMENT_COUNT = 6

STUB_EMBEDDABLE_PYTHON_SH = """#!/bin/bash
# Test stub for scripts/embeddable-python.sh -- records that it was invoked
# and prints a distinctive, unmistakable fake interpreter path.
set -euo pipefail
: "${MARKER_FILE:?MARKER_FILE env var must be set}"
echo "stub-invoked" >> "$MARKER_FILE"
printf '%s\\n' "/marker/stub-embeddable-python"
"""


def _makefile_text() -> str:
    return MAKEFILE.read_text()


# ---------------------------------------------------------------------------
# 1. Grep-pins against the literal Makefile text.
# ---------------------------------------------------------------------------


def test_embeddable_python_variable_is_defined_via_the_resolver_script() -> None:
    text = _makefile_text()
    match = re.search(
        r"^EMBEDDABLE_PYTHON\s*(=|:=)\s*\$\(shell bash scripts/embeddable-python\.sh\)\s*$",
        text,
        re.MULTILINE,
    )
    assert match is not None, (
        "Makefile must define "
        "'EMBEDDABLE_PYTHON = $(shell bash scripts/embeddable-python.sh)' -- "
        "not found (#2082)"
    )


def test_embeddable_python_variable_uses_recursive_not_eager_assignment() -> None:
    """`:=` would run scripts/embeddable-python.sh at PARSE time for EVERY
    `make` invocation, including `make help` -- see the empirical dry-run
    proof below for the load-bearing consequence."""
    text = _makefile_text()
    match = re.search(r"^EMBEDDABLE_PYTHON\s*(=|:=)\s*\$\(shell", text, re.MULTILINE)
    assert match is not None, "EMBEDDABLE_PYTHON definition not found"
    assert match.group(1) == "=", (
        f"EMBEDDABLE_PYTHON must use recursive '=' assignment, found "
        f"{match.group(1)!r} -- eager ':=' invokes the resolver at PARSE "
        f"TIME for every `make` invocation (#2082)"
    )


def test_no_raw_pyo3_python_dollar_python_remains() -> None:
    """#2072/#2082: `PYO3_PYTHON=$(PYTHON)` is the exact broken shape --
    the raw project venv interpreter, unfiltered for embeddability. Any
    reintroduction of this literal string is a regression."""
    text = _makefile_text()
    assert "PYO3_PYTHON=$(PYTHON)" not in text, (
        "found 'PYO3_PYTHON=$(PYTHON)' in Makefile -- this reintroduces "
        "#2072's init_fs_encoding crash on a uv-standalone venv; use "
        "PYO3_PYTHON=$(EMBEDDABLE_PYTHON) instead (#2082)"
    )


def test_every_pyo3_python_assignment_uses_embeddable_python() -> None:
    text = _makefile_text()
    assignments = re.findall(r"PYO3_PYTHON=(\S+)", text)
    assert len(assignments) == EXPECTED_PYO3_PYTHON_ASSIGNMENT_COUNT, (
        f"expected exactly {EXPECTED_PYO3_PYTHON_ASSIGNMENT_COUNT} "
        f"PYO3_PYTHON= assignments (test, test-rust x2, lint, "
        f"benchmark-rust x2), found {len(assignments)}: {assignments}. "
        f"If you added/removed a cargo-invoking target, update "
        f"EXPECTED_PYO3_PYTHON_ASSIGNMENT_COUNT deliberately (#1125)."
    )
    for value in assignments:
        assert value == "$(EMBEDDABLE_PYTHON)", (
            f"PYO3_PYTHON assigned {value!r}, expected '$(EMBEDDABLE_PYTHON)' (#2082)"
        )


# ---------------------------------------------------------------------------
# 2. Empirical dry-run proof: parse-time isolation + recipe-time wiring.
# ---------------------------------------------------------------------------


def _isolated_makefile_dir(tmp_path: Path) -> Path:
    """Copy the real Makefile into an isolated dir alongside a STUB
    scripts/embeddable-python.sh, so `make -n <target>` can be run without
    touching the real repo or depending on this dev machine's actual venv
    shape."""
    (tmp_path / "Makefile").write_text(_makefile_text())
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    stub = scripts_dir / "embeddable-python.sh"
    stub.write_text(STUB_EMBEDDABLE_PYTHON_SH)
    st = stub.stat()
    stub.chmod(st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return tmp_path


def _run_make_dry_run(
    cwd: Path, target: str, marker_file: Path
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MARKER_FILE"] = str(marker_file)
    return subprocess.run(
        # PYTHON is overridden on the command line so the `ifndef PYTHON`
        # guard short-circuits and the (unrelated, unstubbed)
        # scripts/run-with-venv-python.sh is never invoked in the isolated
        # dir. sys.executable guarantees a real, portable interpreter path.
        ["make", "-n", target, f"PYTHON={sys.executable}"],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=env,
        timeout=SUBPROCESS_TIMEOUT,
    )


@pytest.fixture()
def isolated_makefile(tmp_path: Path) -> Path:
    if shutil.which("make") is None:
        pytest.skip("make not available on PATH")
    return _isolated_makefile_dir(tmp_path)


def test_help_target_does_not_invoke_resolver_at_parse_time(
    isolated_makefile: Path, tmp_path: Path
) -> None:
    """Parse-time isolation: `make help` never references
    $(EMBEDDABLE_PYTHON), so the resolver script must NOT run for it. A
    future regression from `=` to `:=` would make EVERY `make` invocation
    -- including `make help` -- run the resolver at parse time."""
    marker = tmp_path / "marker-help.log"
    result = _run_make_dry_run(isolated_makefile, "help", marker)
    assert result.returncode == 0, (
        f"dry-run 'make help' failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert not marker.exists(), (
        f"scripts/embeddable-python.sh (stub) was invoked while dry-running "
        f"'make help' -- EMBEDDABLE_PYTHON must be lazily (recursively) "
        f"expanded, not evaluated at parse time (#2082). "
        f"stdout={result.stdout!r}"
    )


def test_test_rust_target_does_invoke_resolver_and_uses_its_output(
    isolated_makefile: Path, tmp_path: Path
) -> None:
    """Gate-off proof (#1468): confirm `test-rust` actually references
    $(EMBEDDABLE_PYTHON) -- not merely textually adjacent to it (#1859
    decorative-pin trap) -- by requiring the stub's distinctive resolved
    path to appear in the dry-run recipe output."""
    marker = tmp_path / "marker-test-rust.log"
    result = _run_make_dry_run(isolated_makefile, "test-rust", marker)
    assert result.returncode == 0, (
        f"dry-run 'make test-rust' failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert marker.exists(), (
        f"scripts/embeddable-python.sh (stub) was NEVER invoked while "
        f"dry-running 'make test-rust' -- PYO3_PYTHON is not actually wired "
        f"to $(EMBEDDABLE_PYTHON) (#2082). stdout={result.stdout!r}"
    )
    assert "PYO3_PYTHON=/marker/stub-embeddable-python" in result.stdout, (
        f"'make test-rust' dry-run recipe did not contain the stub's "
        f"resolved interpreter path -- PYO3_PYTHON is not sourced from "
        f"$(EMBEDDABLE_PYTHON) (#2082). stdout={result.stdout!r}"
    )


def test_lint_and_benchmark_rust_targets_also_invoke_resolver(
    isolated_makefile: Path, tmp_path: Path
) -> None:
    """Companion to the test-rust proof above for the other two
    cargo-invoking targets (#1104: N similar sites need N tests)."""
    for target in ("lint", "benchmark-rust"):
        marker = tmp_path / f"marker-{target}.log"
        result = _run_make_dry_run(isolated_makefile, target, marker)
        assert result.returncode == 0, (
            f"dry-run 'make {target}' failed: stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert marker.exists(), (
            f"scripts/embeddable-python.sh (stub) was NEVER invoked while "
            f"dry-running 'make {target}' (#2082). stdout={result.stdout!r}"
        )
        assert "PYO3_PYTHON=/marker/stub-embeddable-python" in result.stdout, (
            f"'make {target}' dry-run recipe did not contain the stub's "
            f"resolved interpreter path (#2082). stdout={result.stdout!r}"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
