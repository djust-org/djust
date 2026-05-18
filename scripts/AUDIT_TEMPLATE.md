# Audit-Script Template — `scripts/check-*.py`

> Codifies the structural shape shared by the `scripts/check-*.py` audit
> family (`check-adr-status.py`, `check-doc-snippets.py`,
> `check-lockfile-versions.py`). Closes #1515.

## When to use

This is the canonical shape for a **mechanical, no-network, CI-fast
repo-invariant audit** — a script that compares strings/files already on
disk, finds drift, and fails the build deterministically. It is *not* for
anything that calls git, `gh`, `uv`, `cargo`, or the network: those are
slow, non-deterministic, and belong in a different tool class.

Examples already in the repo:

| Audit | What it pins |
|-------|--------------|
| `check-adr-status.py` | ADR Status/version-line consistency |
| `check-doc-snippets.py` | Fenced Python doc snippets parse/resolve + version/size claims |
| `check-lockfile-versions.py` | Lockfile self-entries match their manifests |

**Extending an existing audit** (adding a new sub-check to a script that
already exists — e.g. #1509 added `check_security_style()` to
`check-doc-snippets.py`) reuses that audit's wiring: only section 2's test
additions and section 4's discipline checklist apply. Section 1 (the
script skeleton) and section 3 (the four wiring edits) are for a
*brand-new* audit.

---

## 1. The script skeleton — `scripts/check-<name>.py`

Copy this file, replace every `<...>` placeholder and `#NNNN` issue
number, and fill in the constants / helpers / `run()` body.

```python
#!/usr/bin/env python3
"""
<One-line summary of what this audit pins> — closes #NNNN.

<A paragraph describing the drift class this catches and, if relevant,
the incident/issue that motivated it.>

This audit is mechanical and self-contained — it does NOT call git, gh,
or the network (keeps it CI-fast and deterministic).

<Describe each check/rule. Use "hard (sets exit 1)" vs "soft (WARNING
only, does not set exit 1)" labels so the contract is unambiguous.>

Usage:
    python3 scripts/check-<name>.py
    python3 scripts/check-<name>.py --<input> path/to/thing
    python3 scripts/check-<name>.py --verbose
    make check-<name>

Exit code:
    0 — no drift (hard rules clean; warnings allowed)
    1 — drift found (>=1 hard rule fails)
    2 — usage error (an explicitly-passed input is missing/unparseable)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
# stdlib-only — add re / ast / tomllib as the audit needs. No network,
# no git, no gh, no third-party imports.

ROOT = Path(__file__).resolve().parents[1]

# --- constants ---
# Module-level constants for regexes, token lists, tunable thresholds.
# Anything a future maintainer might want to adjust lives here, documented.


def run(<explicit path/data args>) -> tuple[int, str]:
    """Core logic exposed for testing.

    Takes explicit path/data arguments — never reads sys.argv, never
    calls sys.exit. Returns (exit_code, message).
    """
    errors: list[str] = []
    warnings: list[str] = []

    # ... per-item checks; append human-readable strings to errors/warnings.

    lines: list[str] = []
    for w in warnings:
        lines.append(f"WARNING: {w}")

    if errors:
        lines.append(f"Found {len(errors)} <thing> issue(s):")
        for e in errors:
            lines.append(f"  {e}")
        return 1, "\n".join(lines)

    lines.append(
        "OK — <what was verified>"
        + (f" ({len(warnings)} warning(s))" if warnings else "")
    )
    return 0, "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--<input>",
        default=None,
        help="Path to <input> (default: <repo>/<default path>)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Print each parsed value before the verdict "
        "(or: a documented no-op for parity with other linters)",
    )
    return p


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    input_path = (
        Path(args.input) if args.input else (ROOT / "<default path>")
    )

    # An explicitly-passed input that does not exist is a usage error (2).
    if not input_path.exists():
        print(f"ERROR: <input> not found: {input_path}")
        sys.exit(2)

    exit_code, msg = run(input_path)
    print(msg)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
```

---

## 2. The test skeleton — `tests/test_check_<name>.py`

Copy this file, replace `<Name>` / `<name>` / `#NNNN`, and fill in each
`# FILL IN` marker.

```python
"""Tests for scripts/check-<name>.py — #NNNN.

Subprocess-driven against the script with --<flag> path overrides. Each
`*_fails` test is tautology-guarded (Action #1200 / #254) — it asserts
BOTH the exit code AND a specific substring in the message, so it cannot
pass if the script merely exits 1 for an unrelated reason.
"""

import pathlib
import subprocess
import sys
import tempfile
import textwrap

import pytest

_SELF = pathlib.Path(__file__).resolve()
_REPO = _SELF.parents[1]
_LINTER = _REPO / "scripts" / "check-<name>.py"


def _write(directory, name, content):
    p = directory / name
    p.write_text(textwrap.dedent(content).lstrip("\n"))
    return p


def _run(*, input=None):
    """Run the linter via subprocess with explicit path overrides.

    Returns (exit_code, stdout).
    """
    args = [sys.executable, str(_LINTER)]
    if input is not None:
        args += ["--input", str(input)]
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=str(_REPO)
    )
    return result.returncode, result.stdout


class TestCheck<Name>:
    """Core checks for the <name> audit."""

    def test_clean_input_passes(self):
        """# FILL IN — a well-formed input → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            inp = _write(d, "input", "")  # FILL IN
            code, out = _run(input=inp)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_drift_fails(self):
        """# FILL IN — a drifted input → exit 1, naming the drift.

        Tautology-guarded: asserts the exit code AND a message substring.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            inp = _write(d, "input", "")  # FILL IN — inject the drift
            code, out = _run(input=inp)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "<substring>" in out  # FILL IN

    def test_missing_input_usage_error(self):
        """An explicitly-passed --input that does not exist → exit 2."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            code, out = _run(input=d / "nonexistent")
            assert code == 2, f"expected exit 2, got {code}: {out}"
            assert "not found" in out


@pytest.mark.slow
def test_real_repo_passes():
    """Dogfood gate (Action #1060): the real repo passes with no overrides."""
    result = subprocess.run(
        [sys.executable, str(_LINTER)],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )
    assert result.returncode == 0, f"real repo must pass: {result.stdout}"
    assert "OK" in result.stdout
```

---

## 3. Wiring checklist — 4 edits, none optional

A brand-new audit needs all four. Skipping any one means the audit does
not run somewhere it should.

- [ ] **`Makefile`** — add a `.PHONY` line + a target. The `help`
      target's awk parses `^[a-zA-Z_0-9-]+:.*?##`, so any target with a
      `## ` comment auto-appears in `make help`. Copy verbatim:

      ```makefile
      .PHONY: check-<name>
      check-<name>: ## <help text> (closes #NNNN)
      	@.venv/bin/python scripts/check-<name>.py $(if $(VERBOSE),--verbose,)
      ```

      (The recipe line is a real tab, not spaces.)

- [ ] **`.pre-commit-config.yaml`** — add a `repo: local` hook block.
      Use `language: python` for a script that needs no repo on the path;
      use `language: system` + `entry: bash -c '...'` when the script
      needs `PYTHONPATH=.` (e.g. import-resolution against the package).
      `files:` must scope to the audit's *inputs* so an unrelated commit
      does not trigger it. `pass_filenames: false` (the script discovers
      its own inputs). Copy verbatim:

      ```yaml
        # <one-line description>. See scripts/check-<name>.py and
        # tests/test_check_<name>.py. Closes #NNNN.
        - repo: local
          hooks:
            - id: check-<name>
              name: check <name> (#NNNN)
              entry: python scripts/check-<name>.py
              language: python
              files: ^<regex scoping to the inputs>$
              pass_filenames: false
      ```

      `system` + `bash -c` variant (when `PYTHONPATH=.` is needed):

      ```yaml
              entry: bash -c 'PYTHONPATH=. .venv/bin/python scripts/check-<name>.py'
              language: system
      ```

- [ ] **`.github/workflows/test.yml`** — add a step in the
      `python-tests` job, grouped with the other audit steps (after the
      ruff step). Copy verbatim:

      ```yaml
            - name: <one-line description> (#NNNN)
              run: .venv/bin/python scripts/check-<name>.py
      ```

- [ ] **`scripts/README.md`** — add a short entry under `## Other
      Scripts` describing what the audit pins, the usage line(s), and the
      closing issue number.

---

## 4. Discipline checklist — do not skip

Non-file conventions every audit must honour. These are the recurring
failure modes the audit family has hit; the checklist locks the fixes.

- [ ] **Exit codes** — `0` clean / `1` drift / `2` usage error, and the
      module docstring's `Exit code:` block documents all three.
- [ ] **No network, no git, no `gh`** — stdlib-only imports; the
      docstring states this explicitly.
- [ ] **`run()` shape** — takes explicit args, returns `(int, str)`,
      never calls `sys.exit`, never reads `sys.argv`. All `sys.exit` /
      argv handling lives in `main()`.
- [ ] **Tautology-guarded `*_fails` tests** (Action #1200 / #254) —
      every failing-case test asserts BOTH the exit code AND a message
      substring, so it cannot pass for an unrelated reason.
- [ ] **Gate-off self-test** (Action #254) — after the tests pass,
      temporarily disable the new check; re-run; confirm at least one
      `*_fails` test fails. Restore. This proves the tests exercise the
      new code, not pre-existing behaviour.
- [ ] **`@pytest.mark.slow` dogfood test** (Action #1060) — a real-repo
      test that runs the script with no overrides and asserts exit 0.
- [ ] **Dogfood before commit** (Action #1060) — run the script against
      the real repo and confirm it exits 0 (or its first job is the
      cleanup that makes it exit 0). A new audit that ships red on day
      one is a false-positive generator.
- [ ] **Symbol-migration grep** (Action #1100 / #1391) — if a helper's
      signature changes, grep every call site across `scripts/` and
      `tests/` and update them in the same pass.
