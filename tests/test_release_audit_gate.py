"""The pre-release security audit gates publishing, and its gates stay blocking.

Covers two things that no CI run exercises until a tag is pushed:

* the workflow wiring — release.yml / publish.yml call the audit and their
  publish jobs ``need`` it; the audit has no tag trigger of its own (it would
  run twice and race the release); no gating command is swallowed by
  ``|| true``; the summary fails on any upstream failure;
* ``scripts/pip-audit-gate.py``'s allowlist parsing and lock handling, which
  decide what the pip-audit gate ignores.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO / ".github" / "workflows"
AUDIT = "./.github/workflows/pre-release-security-audit.yml"


def _workflow(name: str) -> dict:
    data = yaml.safe_load((WORKFLOWS / name).read_text())
    # PyYAML reads the bare `on:` key as boolean True.
    data["on"] = data.pop(True, data.get("on"))
    return data


def _load_gate():
    spec = importlib.util.spec_from_file_location(
        "pip_audit_gate", REPO / "scripts" / "pip-audit-gate.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()


class TestWorkflowWiring:
    def test_audit_is_reusable_and_has_no_push_trigger(self):
        triggers = _workflow("pre-release-security-audit.yml")["on"]
        assert "workflow_call" in triggers
        assert "workflow_dispatch" in triggers
        assert "push" not in triggers

    @pytest.mark.parametrize(
        "workflow, publish_jobs",
        [
            ("release.yml", ["github-release", "pypi-publish"]),
            ("publish.yml", ["publish"]),
        ],
    )
    def test_publishing_jobs_need_the_audit(self, workflow, publish_jobs):
        jobs = _workflow(workflow)["jobs"]
        callers = [name for name, job in jobs.items() if job.get("uses") == AUDIT]
        assert callers == ["security-audit"]
        perms = jobs["security-audit"]["permissions"]
        assert perms == {"contents": "read", "issues": "write", "security-events": "write"}
        for name in publish_jobs:
            assert "security-audit" in jobs[name]["needs"], name

    @pytest.mark.parametrize("workflow", ["release.yml", "publish.yml"])
    def test_publish_downloads_only_distributions(self, workflow):
        # The audit's report artifacts share the run; a bare download of every
        # artifact would hand *.md / *.json files to the PyPI upload.
        for name, job in _workflow(workflow)["jobs"].items():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/download-artifact"):
                    with_ = step.get("with", {})
                    assert "pattern" in with_ or "name" in with_, (workflow, name, step.get("name"))

    def test_gating_commands_are_not_swallowed(self):
        text = (WORKFLOWS / "pre-release-security-audit.yml").read_text()
        for command in (
            "pip-audit-gate.py",
            "cargo audit --deny",
            "npm audit --audit-level",
            "codeql-alert-gate.sh",
        ):
            lines = [
                line
                for line in text.splitlines()
                if command in line and not line.strip().startswith("#")
            ]
            assert lines, command
            for line in lines:
                assert "|| true" not in line, line
        # Safety was removed in favour of pip-audit (stale free database).
        assert "safety check" not in text and "safety-report" not in text

    def test_summary_fails_when_any_scanner_fails(self):
        summary = _workflow("pre-release-security-audit.yml")["jobs"]["audit-summary"]
        needs = set(summary["needs"])
        assert needs == {"python-security", "rust-security", "js-security", "codeql", "codeql-gate"}
        last = summary["steps"][-1]
        assert last.get("if") == "always()"
        assert "toJSON(needs)" in last["env"]["RESULTS"]
        assert "exit 1" in last["run"]

    def test_prerelease_codeql_uses_the_reviewed_config(self):
        steps = _workflow("pre-release-security-audit.yml")["jobs"]["codeql"]["steps"]
        init = next(
            s for s in steps if str(s.get("uses", "")).startswith("github/codeql-action/init")
        )
        assert init["with"]["config-file"] == ".github/codeql/codeql-config.yml"


class TestAllowlist:
    TODAY = dt.date(2026, 9, 23)

    def _load(self, tmp_path, body):
        f = tmp_path / "allow.txt"
        f.write_text(body)
        return gate.load_allowlist(f, self.TODAY)

    def test_valid_entries_and_comments(self, tmp_path):
        ids, errors = self._load(
            tmp_path,
            "# header\n\nCVE-2026-1  # pkg 1.0: reason; review-by 2026-12-31\n"
            "PYSEC-2026-2 # pkg 2.0: reason; review-by 2026-09-23\n",
        )
        assert errors == []
        assert ids == ["CVE-2026-1", "PYSEC-2026-2"]

    @pytest.mark.parametrize(
        "line, fragment",
        [
            ("CVE-2026-1\n", "expected"),
            ("CVE-2026-1  # reason without a date\n", "no 'review-by"),
            ("CVE-2026-1  # reason; review-by 2026-09-22\n", "has passed"),
            ("CVE-2026-1  # reason; review-by 2026-02-30\n", "invalid review-by"),
        ],
    )
    def test_invalid_entries_are_errors(self, tmp_path, line, fragment):
        ids, errors = self._load(tmp_path, line)
        assert ids == []
        assert len(errors) == 1 and fragment in errors[0]

    def test_duplicate_is_an_error(self, tmp_path):
        entry = "CVE-2026-1  # r; review-by 2026-12-31\n"
        ids, errors = self._load(tmp_path, entry + entry)
        assert ids == ["CVE-2026-1"]
        assert "duplicate" in errors[0]

    def test_committed_allowlist_is_well_formed(self):
        # Format only: expiry is enforced by the gate at release time, not
        # here, so an entry reaching its review-by date blocks the release
        # rather than every unrelated PR.
        ids, errors = gate.load_allowlist(gate.DEFAULT_ALLOWLIST, dt.date(2000, 1, 1))
        assert errors == []


class TestLockHandling:
    def test_pins_drop_markers_and_keep_every_fork(self):
        text = (
            "autobahn==24.4.2 ; python_full_version < '3.11'\n"
            "autobahn==26.7.1 ; python_full_version >= '3.11'\n"
            "Django==5.2.17\n"
            "    # via channels\n"
            "-e .\n"
        )
        assert gate.pinned(text) == ["autobahn==24.4.2", "autobahn==26.7.1", "django==5.2.17"]

    def test_layers_never_repeat_a_package(self):
        pins = ["a==1", "a==2", "a==3", "b==1", "c==1", "c==2"]
        layers = gate.split_layers(pins)
        assert layers == [["a==1", "b==1", "c==1"], ["a==2", "c==2"], ["a==3"]]
        for layer in layers:
            names = [p.split("==")[0] for p in layer]
            assert len(names) == len(set(names))
