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


# ---------------------------------------------------------------------------
# #2839 — prose containing "it (" / "test (" must not count as a JS test.
# The pre-#2839 regex matched any whitespace-preceded it|test before '(',
# so a comment sentence like "...block below it (`requiredKey`)..." counted
# as a test and a CORRECT changelog claim was rejected as drift.
# ---------------------------------------------------------------------------


def test_js_prose_it_paren_is_not_a_test(tmp_path: Path) -> None:
    """The exact #2839 repro: prose 'below it (…)' in a docstring is not a test.

    The file has 4 it() blocks; the fragment's claim of 4 is correct and must
    pass. The pre-fix regex counted the docstring sentence as a fifth test and
    rejected the correct claim.
    """
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-01-01\n- prior\n",
            "changelog.d/2839.fixed.md": textwrap.dedent(
                """\
                - **Dotted keydown (#2839).** 4 JSDOM cases in
                  `tests/js/prose_repro_2839.test.js`.
                """
            ),
            "tests/js/prose_repro_2839.test.js": textwrap.dedent(
                """\
                /**
                 * Validates the dotted keydown path. The modifier-parsing
                 * block below it (`requiredKey`) is dead code by construction,
                 * so only the attribute-name scan matters here.
                 */
                describe('dotted keydown', () => {
                  it('enter fires', () => {});
                  it('escape fires', () => {});
                  it('space fires', () => {});
                  it('plain key still works', () => {});
                });
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_js_prose_cannot_inflate_a_wrong_claim(tmp_path: Path) -> None:
    """False-negative direction: a prose 'it (' must not make a wrong claim pass.

    The file has 4 real it() blocks; the fragment wrongly claims 5. The
    pre-fix regex counted the comment sentence "worth it (see below)" as a
    fifth test, so the wrong claim slipped through. It must fail.
    """
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-01-01\n- prior\n",
            "changelog.d/2839.fixed.md": textwrap.dedent(
                """\
                - **Foo (#2839).** 5 JSDOM cases in
                  `tests/js/prose_inflate_2839.test.js`.
                """
            ),
            "tests/js/prose_inflate_2839.test.js": textwrap.dedent(
                """\
                describe('foo', () => {
                  it('a', () => {});
                  it('b', () => {});
                  it('c', () => {});
                  it('d', () => {});
                });
                // Paying the cost once is worth it (see below).
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "file has 4" in result.stderr


def test_js_prose_in_comment_shapes_is_not_a_test(tmp_path: Path) -> None:
    """Comment prose shapes from the real corpus must not count as tests.

    Mirrors the eight phantom matches the corpus scan found (// trailing
    comments, block-comment docstrings, mid-line prose after a statement).
    Two real it() blocks; claim 2 must pass.
    """
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-01-01\n- prior\n",
            "changelog.d/2839.fixed.md": textwrap.dedent(
                """\
                - **Foo (#2839).** 2 JSDOM cases in
                  `tests/js/prose_comment_shapes_2839.test.js`.
                """
            ),
            "tests/js/prose_comment_shapes_2839.test.js": textwrap.dedent(
                """\
                /**
                 * Setup: the server's VDOM child indices skip it (Rust drops
                 * it) but the client could keep it (MoveSubtree if-outer-0).
                 */
                describe('foo', () => {
                  it('a', () => {});
                  it('b', () => {});
                  // Late for this window — and, for the cases above, shadows it (#2135).
                  const h = createHarness();
                  // A real <section> inserted before the outer moves it (depth handled).
                  // Paying it (see #2659) twice is fine.
                });
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr


def test_js_each_declaration_counts_once(tmp_path: Path) -> None:
    """it.each(...) is one test declaration — it must count.

    The pre-#2839 regex missed it.each entirely (the modifier group only
    allowed .only/.skip), undercounting files that use it — the same
    correct-claim-rejected failure, from the other direction. One it.each
    counts once, matching the Python side's convention of counting a
    parametrize() function as one test. Arbitrary nesting indent still counts.
    """
    _write_tree(
        tmp_path,
        {
            "CHANGELOG.md": "# Changelog\n\n## [0.1.0] - 2026-01-01\n- prior\n",
            "changelog.d/2839.fixed.md": textwrap.dedent(
                """\
                - **Foo (#2839).** 3 JSDOM cases in
                  `tests/js/each_declaration_2839.test.js`.
                """
            ),
            "tests/js/each_declaration_2839.test.js": textwrap.dedent(
                """\
                describe('foo', () => {
                  it.each(['a', 'b', 'c'])('handles %s', () => {});
                  it('plain one', () => {});
                    it('deeper-nested one', () => {});
                });
                """
            ),
        },
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
