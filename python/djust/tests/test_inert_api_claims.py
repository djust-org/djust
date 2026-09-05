"""No doc or code may prescribe a deleted or inert API (#2679, #2680, #2656).

This exists because a hand-audit failed. The first pass at #2679/#2680 claimed
to have corrected "the four places" that taught the deleted `StateBus` and the
deleted `djustSecurity` global. Review found **21** — including two LIVE
`.semgrep` rules whose remediation text told a developer hitting a real XSS or
prototype-pollution finding to call `djustSecurity.safeSetInnerHTML()`, a
`window.StateBus.subscribe(...)` snippet against a class that no longer exists,
`README.md`, and four demo-project files.

Counting by hand is the thing that broke, so the fix is mechanical (#1859: a
pin nobody can drift past beats a promise of completeness). The next doc that
teaches `@client_state` as a working feature fails this test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "check-inert-api-claims.py"


def test_no_docs_or_code_prescribe_a_deleted_or_inert_api() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, (
        "docs or code prescribe an API that does not do what they say:\n\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_the_checker_actually_catches_a_reintroduction(tmp_path: Path) -> None:
    """The guard is load-bearing, not decorative (#1859).

    A green run above means nothing unless the checker fails on the shape it
    claims to catch, so re-introduce one and confirm it is reported.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import importlib.util

        spec = importlib.util.spec_from_file_location("_inert_check", SCRIPT)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)

    # The exact pre-fix shape from .semgrep/djust-security.yaml.
    assert mod.DELETED_GLOBAL_CALL.search("Use djustSecurity.safeSetInnerHTML() instead")
    # The exact pre-fix shape from STATE_MANAGEMENT_API.md.
    assert mod.DELETED_STATEBUS_USE.search("window.StateBus.subscribe('temperature', cb)")
    # A doc teaching the inert decorator.
    assert mod.INERT_DECORATOR_USE.search('@client_state(keys=["filter"])')
    assert mod.INERT_DECORATOR_USE.search("from djust.decorators import client_state")

    # And it must NOT fire on the corrections themselves, or every fix is a
    # violation and the guard is unusable.
    assert not mod.DELETED_GLOBAL_CALL.search("There is no `djustSecurity` global.")
    assert mod.INERT_MARKER.search("(inert — no client impl, #2656)")
