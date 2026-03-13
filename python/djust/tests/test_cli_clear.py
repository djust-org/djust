"""
Regression tests for ``djust clear`` CLI command — issue #395 follow-up.

The original bug: ``djust clear --all`` called ``cleanup_expired(ttl=0)`` which,
after fix #395, means "never expire" rather than "delete everything".  The fix
adds ``delete_all()`` and wires ``--all`` to call it instead.

These tests verify that the CLI dispatches to the correct backend method so
future refactors cannot silently re-introduce the same breakage.
"""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from djust.cli import cmd_clear


def _make_args(all_flag=False, force=True):
    args = argparse.Namespace(all=all_flag, force=force)
    return args


def _make_backend(sessions=3):
    backend = MagicMock()
    backend.get_stats.return_value = {"total_sessions": sessions}
    backend.delete_all.return_value = sessions
    backend.cleanup_expired.return_value = 0
    return backend


@pytest.fixture()
def mock_backend():
    backend = _make_backend()
    with patch("djust.cli.setup_django"), patch(
        "djust.state_backend.get_backend", return_value=backend
    ):
        yield backend


class TestCmdClearAll:
    def test_all_flag_calls_delete_all(self, mock_backend):
        """--all must call delete_all(), not cleanup_expired()."""
        cmd_clear(_make_args(all_flag=True))
        mock_backend.delete_all.assert_called_once()

    def test_all_flag_does_not_call_cleanup_expired(self, mock_backend):
        """--all must NOT call cleanup_expired() — that would be the old buggy path."""
        cmd_clear(_make_args(all_flag=True))
        mock_backend.cleanup_expired.assert_not_called()

    def test_no_all_flag_calls_cleanup_expired(self, mock_backend):
        """Without --all, only expired sessions should be removed."""
        cmd_clear(_make_args(all_flag=False))
        mock_backend.cleanup_expired.assert_called_once()

    def test_no_all_flag_does_not_call_delete_all(self, mock_backend):
        """Without --all, delete_all() must NOT be called."""
        cmd_clear(_make_args(all_flag=False))
        mock_backend.delete_all.assert_not_called()

    def test_all_flag_reports_cleared_count(self, capsys, mock_backend):
        """Output should reflect the number returned by delete_all()."""
        mock_backend.delete_all.return_value = 7
        mock_backend.get_stats.return_value = {"total_sessions": 7}
        cmd_clear(_make_args(all_flag=True))
        captured = capsys.readouterr()
        assert "7" in captured.out

    def test_backend_error_exits_nonzero(self):
        """Errors from the backend should be caught and sys.exit(1) called."""

        backend = MagicMock()
        backend.get_stats.return_value = {"total_sessions": 1}
        backend.delete_all.side_effect = RuntimeError("boom")

        with patch("djust.cli.setup_django"), patch(
            "djust.state_backend.get_backend", return_value=backend
        ), pytest.raises(SystemExit) as exc_info:
            cmd_clear(_make_args(all_flag=True))

        assert exc_info.value.code == 1
