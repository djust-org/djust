"""Tests for scripts/ci_djust_check_demo.py — #1713.

The live dogfood (examples/demo_project) currently has 0 error-severity
findings, so the ``severity == "error"`` arm of the gate is never exercised
on the runner (rc4 retro finding #3 / Stage-11 note). These tests feed
SYNTHETIC ``djust_check --json`` payloads straight into the extracted
``evaluate()`` decision function so BOTH gate arms run end-to-end:

  * error-severity finding            -> exit 1 (error arm)
  * deprecated-attr T001/T014/T015    -> exit 1 (deprecated-attr ID-set arm)
  * clean (benign warnings/info only) -> exit 0

This synthetic-error-trigger IS the empirical canary (#252) for the
wrapper's gate arms: each ``_blocks`` test is tautology-guarded (Action
#1200 / #254) by also asserting a clean payload returns 0, so the gate
cannot pass by always-returning-1 for an unrelated reason.

The synthetic payloads match the EXACT shape ``djust_check --json`` emits
(``_output_json`` in python/djust/management/commands/djust_check.py):
``{"checks": [{"id", "severity", "category", "message", "hint"}],
   "summary": {"total", "errors", "warnings", "info"}}`` with severity
lowercased ("error" / "warning" / "info") and IDs like "djust.T001".
"""

import importlib.util
import pathlib

_SELF = pathlib.Path(__file__).resolve()
_SCRIPT = _SELF.parents[1] / "scripts" / "ci_djust_check_demo.py"

_spec = importlib.util.spec_from_file_location("ci_djust_check_demo", _SCRIPT)
ci_djust_check_demo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ci_djust_check_demo)

evaluate = ci_djust_check_demo.evaluate


def _check(check_id, severity, message="synthetic finding"):
    """Build one check dict in the exact --json shape."""
    return {
        "id": check_id,
        "severity": severity,
        "category": "templates",
        "message": message,
        "hint": "",
    }


def _payload(checks):
    """Wrap checks with a matching summary block (mirrors _output_json)."""
    return {
        "checks": checks,
        "summary": {
            "total": len(checks),
            "errors": sum(1 for c in checks if c["severity"] == "error"),
            "warnings": sum(1 for c in checks if c["severity"] == "warning"),
            "info": sum(1 for c in checks if c["severity"] == "info"),
        },
    }


def test_error_severity_blocks():
    """An error-severity finding fails the gate (error arm)."""
    payload = _payload(
        [
            _check("djust.S001", "error", "an error-severity finding"),
            _check("djust.V004", "info", "benign info"),
        ]
    )
    assert evaluate(payload) == 1


def test_deprecated_t001_blocks():
    """A T001 deprecated-@click finding at warning severity fails (ID-set arm)."""
    payload = _payload(
        [
            _check("djust.T001", "warning", "deprecated @click attribute"),
        ]
    )
    assert evaluate(payload) == 1


def test_deprecated_t014_blocks():
    """A T014 deprecated-data-dj-id finding at warning severity fails (ID-set arm)."""
    payload = _payload(
        [
            _check("djust.T014", "warning", "deprecated data-dj-id attribute"),
        ]
    )
    assert evaluate(payload) == 1


def test_deprecated_t015_blocks():
    """A T015 legacy-root-attribute finding at warning severity fails (ID-set arm)."""
    payload = _payload(
        [
            _check("djust.T015", "warning", "legacy root attribute"),
        ]
    )
    assert evaluate(payload) == 1


def test_clean_payload_passes():
    """Errors-free + no deprecated-attr findings -> exit 0 (tautology guard).

    Includes benign warning/info findings the demo legitimately carries
    (S005 public-view-without-auth, T012 partial fragments, V004 info) to
    prove the gate does NOT fail on intentional demo states.
    """
    payload = _payload(
        [
            _check("djust.S005", "warning", "public view without auth (intentional)"),
            _check("djust.T012", "warning", "partial fragment template"),
            _check("djust.V004", "info", "informational"),
        ]
    )
    assert evaluate(payload) == 0


def test_empty_payload_passes():
    """No checks at all -> exit 0."""
    assert evaluate(_payload([])) == 0
