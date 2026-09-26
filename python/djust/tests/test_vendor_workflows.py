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


def test_vendor_workflow_triggers_on_tailwind_and_version_inputs():
    # Tailwind scans admin_ext/**/*.py and the SBOM root version comes
    # from pyproject.toml, so both must re-run the vendor check.
    paths = set(_workflow("vendor.yml")[True]["pull_request"]["paths"])
    assert "pyproject.toml" in paths
    assert "python/djust/admin_ext/**" in paths


def _step(workflow, job, name_fragment):
    for step in _workflow(workflow)["jobs"][job]["steps"]:
        if name_fragment in step.get("name", ""):
            return step
    raise AssertionError("no step named %r" % name_fragment)


def test_canary_requires_exit_code_exactly_one():
    # osv-scanner exits 1 only when it found an advisory; 127/128 (missing
    # file, no packages read) must fail the canary rather than pass it.
    run = _step("vendor.yml", "vendor", "Canary")["run"]
    assert "rc=$?" in run
    assert '"$rc" -ne 1' in run
    assert "if osv-scanner" not in run


def test_osv_scanner_downloads_fail_on_http_errors():
    for workflow, job in (("vendor.yml", "vendor"), ("vendor-advisories-weekly.yml", "scan")):
        run = _step(workflow, job, "Install osv-scanner")["run"]
        assert "curl -fsSLO" in run


def test_weekly_scan_prefers_final_release_tags():
    run = _step("vendor-advisories-weekly.yml", "scan", "Latest tag")["run"]
    assert "versionsort.suffix=rc" in run


def test_weekly_scan_uses_the_ignore_policy():
    run = _step("vendor-advisories-weekly.yml", "scan", "Scan")["run"]
    assert "--config osv-scanner.toml" in run
