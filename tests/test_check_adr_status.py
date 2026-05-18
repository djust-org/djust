"""Tests for scripts/check-adr-status.py — #1501.

Mirrors tests/test_check_handler_contracts.py: temp-dir fixtures driven
through the script via subprocess with the `--adr-dir` override. Each
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
_LINTER = _SELF.parents[1] / "scripts" / "check-adr-status.py"


def _write_adr(directory, name, content):
    p = directory / name
    p.write_text(textwrap.dedent(content).lstrip("\n"))
    return p


def _run(adr_dir):
    """Run the audit via subprocess against an ADR directory.

    Returns (exit_code, stdout).
    """
    result = subprocess.run(
        [sys.executable, str(_LINTER), "--adr-dir", str(adr_dir)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout


class TestCheckADRStatus:
    """Core checks for the ADR status/version-line audit."""

    def test_known_good_accepted_passes(self):
        """Accepted ADR with a `Shipped in:` line → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "020-good.md",
                """
                # ADR-020: Good Accepted ADR

                **Status**: Accepted — shipped 2026-04-27 in v0.9.0
                **Date**: 2026-04-27
                **Shipped in**: v0.9.0 (PR #1128)
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_known_good_deferred_passes(self):
        """Deferred ADR with a `post-1.0` version line → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "021-good-deferred.md",
                """
                # ADR-021: Good Deferred ADR

                **Status**: Deferred — post-1.0 (roadmap-committed)
                **Date**: 2026-04-11
                **Target version**: post-1.0 (deferred — lands with `AssistantMixin`; see Status)
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_known_good_no_version_line_passes(self):
        """Accepted ADR with no version line (001/009/010/011 shape)."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "022-no-version.md",
                """
                # ADR-022: No Version Line

                **Status**: Accepted
                **Date**: 2026-04-22
                **Deciders**: Project maintainers
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_accepted_with_target_version_line_fails(self):
        """Accepted ADR still labelled `Target version:` → exit 1.

        The exact pre-#1493 ADR-008 stale shape.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "023-stale-accepted.md",
                """
                # ADR-023: Stale Accepted ADR

                **Status**: Accepted — shipped 2026-04-21 in v0.5.1 (PR #835)
                **Date**: 2026-04-20
                **Target version**: v0.7.0 (candidate; could merge with ADR-N)
                """,
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "ADR-023" in out
            assert "Shipped in" in out

    def test_deferred_with_concrete_version_fails(self):
        """Deferred ADR with a bare concrete `Target version` → exit 1.

        The pre-#1493 ADR-003 stale shape.
        """
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "024-stale-deferred.md",
                """
                # ADR-024: Stale Deferred ADR

                **Status**: Deferred — post-1.0 (AI/server-driven arc)
                **Date**: 2026-04-11
                **Target version**: v0.5.x (lands with `AssistantMixin`)
                """,
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "ADR-024" in out
            assert "post-1.0" in out or "deferred" in out

    def test_partially_accepted_requires_both_tokens(self):
        """Partially Accepted: missing the deferred token → exit 1;
        with both tokens → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "025-partial-bad.md",
                """
                # ADR-025: Partially Accepted, Bad Version Line

                **Status**: Partially Accepted — Phase 1 shipped in v0.4.2
                **Date**: 2026-04-11
                **Target version**: v0.4.2 (MVP)
                """,
            )
            code, out = _run(d)
            assert code == 1, f"expected exit 1, got {code}: {out}"
            assert "ADR-025" in out
            assert "Partially Accepted" in out

        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "026-partial-good.md",
                """
                # ADR-026: Partially Accepted, Good Version Line

                **Status**: Partially Accepted — Phase 1 shipped in v0.4.2
                **Date**: 2026-04-11
                **Target version**: v0.4.2 (Phases 1a-1c shipped); Phases 4-5 deferred post-1.0
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_milestone_line_accepted_passes(self):
        """The ADR-012 `**Milestone**:` bullet-header shape → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "027-milestone.md",
                """
                # ADR-027 — Milestone Header Style

                - **Status**: Accepted (2026-04-24)
                - **Supersedes**: none
                - **Milestone**: v0.7.2 (close-without-code)
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "OK" in out

    def test_proposed_but_shipped_warns_not_fails(self):
        """Proposed ADR with a shipped marker → exit 0 + WARNING line."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            _write_adr(
                d,
                "028-proposed-shipped.md",
                """
                # ADR-028: Proposed But Body Says Shipped

                **Status**: Proposed
                **Date**: 2026-05-01

                ## Context

                This feature shipped in PR #1234 already.
                """,
            )
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "WARNING" in out
            assert "ADR-028" in out

    def test_empty_adr_dir(self):
        """Empty temp dir → exit 0, 0 ADRs scanned."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            code, out = _run(d)
            assert code == 0, f"expected exit 0, got {code}: {out}"
            assert "0 ADRs scanned" in out

    def test_missing_adr_dir_usage_error(self):
        """A non-existent --adr-dir → exit 2 usage error."""
        result = subprocess.run(
            [sys.executable, str(_LINTER), "--adr-dir", "/nonexistent/path/xyz"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2, f"expected exit 2, got {result.returncode}: {result.stdout}"
        assert "not found" in result.stdout

    @pytest.mark.slow
    def test_real_adr_dir_passes(self):
        """Dogfood gate (Action #1060): the real docs/adr/ passes.

        FAILS until the #1493 edits land — this pins that the #1493
        cleanup actually satisfies the #1501 audit.
        """
        result = subprocess.run(
            [sys.executable, str(_LINTER)],
            capture_output=True,
            text=True,
            cwd=str(_SELF.parents[1]),
        )
        assert result.returncode == 0, f"real docs/adr/ must pass: {result.stdout}{result.stderr}"
