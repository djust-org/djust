"""Tests for scripts/check-handler-contracts.py — #1290."""

import pathlib
import subprocess
import sys
import tempfile
import textwrap

import pytest


def _write_file(directory, name, content):
    p = directory / name
    p.write_text(textwrap.dedent(content))
    return p


_SELF = pathlib.Path(__file__).resolve()
_LINTER = _SELF.parents[1] / "scripts" / "check-handler-contracts.py"


def _run(*, tag_files, handler_files, mixin_dir=None):
    """Run the linter via subprocess with given fixture files.

    Returns (exit_code, stdout).
    """
    args = [sys.executable, str(_LINTER)]
    for tf in tag_files:
        args.extend(["--tag-files", str(tf)])
    if mixin_dir is not None:
        args.extend(["--mixin-dir", str(mixin_dir)])
    else:
        # Point mixin-dir at an empty temp directory so we don't pick
        # up the real mixins during fixture tests.
        args.extend(["--mixin-dir", str(tag_files[0].parent)])
    for hf in handler_files:
        args.extend(["--component-files", str(hf)])
    result = subprocess.run(args, capture_output=True, text=True)
    return result.returncode, result.stdout


class TestHandlerContractsLinter:
    """Core checks for the handler-contracts cross-reference linter."""

    def test_all_matched_passes(self):
        """Exit 0 when every emit default has a matching handler."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag = _write_file(
                d,
                "tags.py",
                """
                def data_table(
                    sort_event="on_table_sort",
                    prev_event="on_table_prev",
                ):
                    pass
            """,
            )
            mixin = _write_file(
                d,
                "mixins.py",
                """
                def on_table_sort(self):
                    pass
                def on_table_prev(self):
                    pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag], handler_files=[mixin])
            assert exit_code == 0, f"expected exit 0, got {exit_code}: {stdout}"
            assert "OK" in stdout
            assert "2" in stdout  # 2 emit defaults

    def test_mismatch_fails(self):
        """Exit 1 when an emit default has no matching handler."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag = _write_file(
                d,
                "tags.py",
                """
                def data_table(
                    sort_event="on_table_sort",
                    prev_event="stale_event_name",
                ):
                    pass
            """,
            )
            mixin = _write_file(
                d,
                "mixins.py",
                """
                def on_table_sort(self):
                    pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag], handler_files=[mixin])
            assert exit_code == 1, f"expected exit 1, got {exit_code}: {stdout}"
            assert "stale_event_name" in stdout

    def test_mismatch_not_silenced_by_unrelated_whitelist(self):
        """Mismatch is flagged even when app-level events are whitelisted."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag = _write_file(
                d,
                "tags.py",
                """
                def data_table(
                    sort_event="on_table_sort",
                    prev_event="broken_handler",
                ):
                    pass
            """,
            )
            mixin = _write_file(
                d,
                "mixins.py",
                """
                def on_table_sort(self):
                    pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag], handler_files=[mixin])
            assert exit_code == 1, f"expected exit 1, got {exit_code}: {stdout}"
            assert "broken_handler" in stdout

    def test_empty_inputs(self):
        """No emit defaults and no handlers = clean pass."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag = _write_file(
                d,
                "tags.py",
                """
                def empty_tag():
                    pass
            """,
            )
            empty = _write_file(
                d,
                "empty.py",
                """
                pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag], handler_files=[empty])
            assert exit_code == 0, f"expected exit 0, got {exit_code}: {stdout}"
            assert "0" in stdout

    def test_kwonly_event_defaults(self):
        """Keyword-only args with _event suffixes are also checked."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag = _write_file(
                d,
                "tags.py",
                """
                def toast(*, dismiss_event="dismiss_toast"):
                    pass
            """,
            )
            mixin = _write_file(
                d,
                "mixins.py",
                """
                def dismiss_toast(self):
                    pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag], handler_files=[mixin])
            assert exit_code == 0, f"expected exit 0, got {exit_code}: {stdout}"

    def test_multiple_tag_files_aggregated(self):
        """Emit defaults from multiple tag files are all checked."""
        with tempfile.TemporaryDirectory() as tmp:
            d = pathlib.Path(tmp)
            tag1 = _write_file(
                d,
                "tags_a.py",
                """
                def comp1(click_event="on_click"):
                    pass
            """,
            )
            tag2 = _write_file(
                d,
                "tags_b.py",
                """
                def comp2(toggle_event="on_toggle"):
                    pass
            """,
            )
            mixin = _write_file(
                d,
                "mixins.py",
                """
                def on_click(self): pass
                def on_toggle(self): pass
            """,
            )
            exit_code, stdout = _run(tag_files=[tag1, tag2], handler_files=[mixin])
            assert exit_code == 0, f"expected exit 0, got {exit_code}: {stdout}"
            assert "2" in stdout

    @pytest.mark.slow
    def test_real_codebase_passes(self):
        """Smoke test: the real codebase passes the linter with no args."""
        result = subprocess.run(
            [sys.executable, str(_LINTER)],
            capture_output=True,
            text=True,
            cwd=str(_SELF.parents[1]),
        )
        assert result.returncode == 0, f"real codebase must pass: {result.stdout}{result.stderr}"
