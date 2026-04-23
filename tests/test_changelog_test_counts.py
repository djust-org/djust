"""Self-test for ``scripts/check-changelog-test-counts.py`` (closes #908).

Feeds the validator a synthetic CHANGELOG + synthetic test files and
verifies that drift is detected and that correct counts pass.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-changelog-test-counts.py"


def _write_tree(tmp: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def _run(tmp: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp / "CHANGELOG.md")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_matching_counts_pass(tmp_path: Path) -> None:
    """CHANGELOG claim matches file reality — exit 0, no stderr noise."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 3 regression tests in `tests/unit/test_foo.py`.

                ## [0.1.0] - 2026-01-01
                - previous
                """
            ),
            "tests/unit/test_foo.py": textwrap.dedent(
                """\
                def test_one(): pass
                def test_two(): pass
                def test_three(): pass
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "drift" not in result.stderr


def test_mismatched_count_fails(tmp_path: Path) -> None:
    """CHANGELOG claim disagrees with reality — exit 1 with a diff."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 5 regression tests in `tests/unit/test_foo.py`.

                ## [0.1.0] - 2026-01-01
                """
            ),
            "tests/unit/test_foo.py": "def test_one(): pass\n",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "drift" in result.stderr
    assert "CHANGELOG says 5" in result.stderr
    assert "file has 1" in result.stderr


def test_delta_phrase_is_ignored(tmp_path: Path) -> None:
    """'2 new JSDOM cases' is a delta, not a total — not validated."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 2 new JSDOM cases in `tests/js/foo.test.js` (12/12 passing).

                ## [0.1.0] - 2026-01-01
                """
            ),
            "tests/js/foo.test.js": "\n".join(f"it('case{i}', () => {{}});" for i in range(12)),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_jsdom_phrase_checks_js_file(tmp_path: Path) -> None:
    """'9 JSDOM cases' phrase resolves against the named .test.js file."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 9 JSDOM cases in `tests/js/foo.test.js`.

                ## [0.1.0] - 2026-01-01
                """
            ),
            "tests/js/foo.test.js": "\n".join(f"it('case{i}', () => {{}});" for i in range(7)),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "tests/js/foo.test.js" in result.stderr
    assert "CHANGELOG says 9" in result.stderr
    assert "file has 7" in result.stderr


def test_multiple_files_summed(tmp_path: Path) -> None:
    """'12 regression tests across a.py, b.py, c.py' sums across listed files."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 12 regression tests across `python/djust/tests/test_a.py`,
                  `python/djust/tests/test_b.py` and `python/djust/tests/test_c.py`.

                ## [0.1.0] - 2026-01-01
                """
            ),
            "python/djust/tests/test_a.py": (
                "def test_a1(): pass\ndef test_a2(): pass\ndef test_a3(): pass\n"
                "def test_a4(): pass\n"
            ),
            "python/djust/tests/test_b.py": (
                "def test_b1(): pass\ndef test_b2(): pass\ndef test_b3(): pass\n"
                "def test_b4(): pass\n"
            ),
            "python/djust/tests/test_c.py": (
                "def test_c1(): pass\ndef test_c2(): pass\ndef test_c3(): pass\n"
                "def test_c4(): pass\n"
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_missing_unreleased_section_is_ok(tmp_path: Path) -> None:
    """No [Unreleased] heading — nothing to validate — exit 0."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-01-01\n- prior\n",
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_count_phrase_without_file_path_is_skipped(tmp_path: Path) -> None:
    """'5 unit tests' with no file reference can't be validated — exit 0."""
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": textwrap.dedent(
                """\
                # Changelog

                ## [Unreleased]

                ### Fixed

                - **foo** — 5 unit tests cover this path.

                ## [0.1.0] - 2026-01-01
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
