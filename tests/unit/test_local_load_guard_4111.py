"""The local load guard's rules (#4111), one test per rule.

Every test points the guard at a temporary home directory: nothing here reads
or writes the developer's own ``~/.djust``.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from tests import local_load_guard as guard


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "_LOCK_HANDLE", None)
    monkeypatch.setenv("DJUST_LOCAL_MAX_ITEMS", "100")
    yield tmp_path
    if guard._LOCK_HANDLE is not None:
        guard._LOCK_HANDLE.close()


def _approve(home, seconds_ahead: float) -> None:
    directory = home / ".djust"
    directory.mkdir(exist_ok=True)
    (directory / guard.APPROVAL_NAME).write_text(str(time.time() + seconds_ahead))


def test_ci_is_unaffected_and_the_variable_no_longer_bypasses():
    assert not guard.active({"CI": "true"})
    assert guard.active({})
    assert guard.active({"DJUST_FULL_LOCAL": "1"}), "DJUST_FULL_LOCAL alone must not bypass"


def test_a_targeted_run_needs_nothing(home):
    assert guard.over_limit_refusal(100, home) is None


def test_a_run_over_the_limit_without_approval_is_refused_with_the_ways_out(home, monkeypatch):
    monkeypatch.setenv("DJUST_FULL_LOCAL", "1")
    message = guard.over_limit_refusal(101, home)
    assert message is not None
    for way_out in ("make test-early", "pytest path/to/test_file.py", "gh pr checks"):
        assert way_out in message
    assert guard.APPROVAL_NAME in message and "no longer bypasses" in message


@pytest.mark.parametrize(
    "content",
    ["", "not a time", str(time.time() - 60), str(time.time() + guard.MAX_APPROVAL_SECONDS + 600)],
)
def test_an_expired_malformed_or_unbounded_approval_does_not_count(home, content):
    directory = home / ".djust"
    directory.mkdir()
    (directory / guard.APPROVAL_NAME).write_text(content)
    assert guard.approval_until(home) is None
    assert guard.over_limit_refusal(101, home) is not None


def test_a_valid_approval_lets_one_run_through_and_takes_the_lock(home):
    _approve(home, 3600)
    assert guard.approval_until(home) is not None
    assert guard.over_limit_refusal(101, home) is None
    assert guard._LOCK_HANDLE is not None
    # The same process asks again (one call per xdist worker): still allowed.
    assert guard.over_limit_refusal(101, home) is None


def test_a_second_heavy_run_refuses_while_one_holds_the_lock(home):
    _approve(home, 3600)
    other_run = guard.acquire_full_run_lock(home)
    assert other_run is not None
    try:
        message = guard.over_limit_refusal(101, home)
        assert message is not None and "already in progress" in message
        assert guard._LOCK_HANDLE is None
    finally:
        other_run.close()
    # Once the other run has ended, this one may start.
    assert guard.over_limit_refusal(101, home) is None


def test_the_early_tier_is_exempt():
    assert guard._early_only(SimpleNamespace(option=SimpleNamespace(early_only=True)))
    assert not guard._early_only(SimpleNamespace(option=SimpleNamespace()))


def test_the_worker_cap_lifts_only_for_an_approved_run(home, monkeypatch):
    monkeypatch.setattr(guard.Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("CI", raising=False)
    config = SimpleNamespace(option=SimpleNamespace(maxprocesses=8))
    guard.pytest_cmdline_main(config)
    assert config.option.maxprocesses == guard.DEFAULT_MAX_WORKERS
    _approve(home, 3600)
    config = SimpleNamespace(option=SimpleNamespace(maxprocesses=8))
    guard.pytest_cmdline_main(config)
    assert config.option.maxprocesses == 8
