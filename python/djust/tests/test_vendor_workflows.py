from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO = Path(__file__).resolve().parents[3]


def _workflow(name):
    return yaml.safe_load((REPO / ".github" / "workflows" / name).read_text())


def test_vendor_workflow_triggers_on_every_input():
    on = _workflow("vendor.yml")[True]  # PyYAML parses the key "on" as True
    paths = set(on["pull_request"]["paths"])
    assert {
        "js/vendor/**",
        "Cargo.lock",
        "crates/*/Cargo.toml",
        "python/djust/djust.cdx.json",
        "python/djust/**/djust_assets.json",
        "python/djust/assets/**",
        "osv-scanner.toml",
    } <= paths


def test_vendor_workflow_runs_check_canary_and_scan():
    steps = " ".join(
        str(s.get("run", "")) for s in _workflow("vendor.yml")["jobs"]["vendor"]["steps"]
    )
    assert "make vendor-check" in steps
    assert "vendor_canary_sbom.py" in steps
    assert "osv-scanner" in steps


def test_weekly_scan_is_scheduled():
    assert "schedule" in _workflow("vendor-advisories-weekly.yml")[True]
