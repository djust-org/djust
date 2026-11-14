"""``djust clear`` and ``djust stats`` act on the view state saved in sessions (#4087).

Since 2.0 a view's state is kept in the session (automatic per-tab state,
#4107, and declared ``persist="server"`` fields), not in the state backend,
whose view cache nothing fills any more. The two commands report and remove
that saved state; the rest of each session (the login, the application's own
keys) is left alone.
"""

from __future__ import annotations

import argparse
import time
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust._automatic_state import _KEY_PREFIX
from djust.cli import cmd_clear, cmd_stats

pytestmark = [pytest.mark.django_db]


def _session(states: dict) -> str:
    session = SessionStore()
    session["app_key"] = "keep me"
    for name, expires in states.items():
        session[_KEY_PREFIX + name] = {"expires": expires, "values": {"n": 1}}
    session.save()
    return session.session_key


@pytest.fixture
def sessions():
    now = int(time.time())
    with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.db"):
        live = _session({"a": now + 600, "b": now - 5})
        other = _session({"c": now + 600})
        plain = _session({})
        with patch("djust.cli.setup_django"):
            yield live, other, plain


def _keys(key: str) -> set:
    return set(SessionStore(key).keys())


def test_clear_removes_only_expired_saved_state(sessions, capsys):
    live, other, plain = sessions
    cmd_clear(argparse.Namespace(all=False, force=True))
    assert _keys(live) == {"app_key", _KEY_PREFIX + "a"}
    assert _keys(other) == {"app_key", _KEY_PREFIX + "c"}
    assert _keys(plain) == {"app_key"}
    assert "Removed 1 saved view state(s) from 1 session(s)" in capsys.readouterr().out


def test_clear_all_removes_every_saved_state_and_keeps_the_rest(sessions, capsys):
    live, other, plain = sessions
    cmd_clear(argparse.Namespace(all=True, force=True))
    for key in (live, other, plain):
        assert _keys(key) == {"app_key"}
    assert "Removed 3 saved view state(s) from 2 session(s)" in capsys.readouterr().out


def test_stats_reports_saved_state(sessions, capsys):
    cmd_stats(argparse.Namespace())
    out = capsys.readouterr().out
    assert "Sessions with saved state: 2" in out
    assert "Saved view states: 3" in out
    assert "State Backend Statistics" in out


def test_an_engine_that_cannot_list_sessions_says_so(capsys):
    with (
        override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cache"),
        patch("djust.cli.setup_django"),
    ):
        cmd_stats(argparse.Namespace())
        cmd_clear(argparse.Namespace(all=True, force=True))
    out = capsys.readouterr().out
    assert out.count("cannot list its sessions") == 2
