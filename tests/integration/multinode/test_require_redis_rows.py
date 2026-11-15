"""The switch that turns a skipped Redis row into a failure (#4136).

The Redis rows of the state-survival harness skip when ``redis-server`` or
``channels_redis`` is missing. The CI job that exists to run them sets
``DJUST_REQUIRE_REDIS_ROWS=1``, under which the same absence fails the row, so
the rows cannot go unexercised again without a red job.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.integration.multinode import conftest as multinode

ROOT = Path(__file__).resolve().parents[3]


def _outcome(environ=None):
    """What ``_need_redis`` did: ``"ran"``, ``"skip"`` or ``"fail"``, with its message.

    Catches both outcomes itself: a ``pytest.raises(Failed)`` that receives a
    Skipped instead lets it escape and turns the TEST into a skip, so a broken
    switch would read as a skip here too, not as a failure.
    """
    try:
        if environ is None:
            multinode._need_redis()
        else:
            multinode._need_redis(environ)
    except pytest.fail.Exception as exc:
        return "fail", str(exc)
    except pytest.skip.Exception as exc:
        return "skip", str(exc)
    return "ran", ""


@pytest.fixture
def missing(monkeypatch):
    monkeypatch.setattr(multinode, "_redis_missing", lambda: "channels_redis is not installed")


def test_missing_redis_skips_without_the_switch(missing):
    kind, message = _outcome({})
    assert kind == "skip"
    assert "channels_redis is not installed" in message


@pytest.mark.parametrize("value", ["", "0", "true", "yes"])
def test_only_the_value_1_turns_the_switch_on(missing, value):
    assert _outcome({multinode.REQUIRE_REDIS_ENV: value})[0] == "skip"


def test_missing_redis_fails_with_the_switch(missing):
    kind, message = _outcome({multinode.REQUIRE_REDIS_ENV: "1"})
    assert kind == "fail"
    assert "channels_redis is not installed" in message
    assert multinode.REQUIRE_REDIS_ENV in message


def test_the_switch_reads_the_process_environment(missing, monkeypatch):
    monkeypatch.setenv(multinode.REQUIRE_REDIS_ENV, "1")
    assert _outcome()[0] == "fail"
    monkeypatch.delenv(multinode.REQUIRE_REDIS_ENV)
    assert _outcome()[0] == "skip"


def test_redis_present_runs_with_or_without_the_switch(monkeypatch):
    monkeypatch.setattr(multinode, "_redis_missing", lambda: None)
    assert _outcome({})[0] == "ran"
    assert _outcome({multinode.REQUIRE_REDIS_ENV: "1"})[0] == "ran"


def test_redis_missing_names_each_absence(monkeypatch):
    monkeypatch.setattr(multinode.shutil, "which", lambda name: None)
    assert multinode._redis_missing() == "redis-server is not installed"
    monkeypatch.setattr(multinode.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(multinode.importlib.util, "find_spec", lambda name: None)
    assert multinode._redis_missing() == "channels_redis is not installed"


def test_the_ci_job_requires_the_rows_and_runs_the_harness():
    """The job that exists for the Redis rows sets the switch and runs the harness."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/test.yml").read_text())
    job = workflow["jobs"]["multinode-redis-rows"]
    steps = [step for step in job["steps"] if "run" in step]
    runs = [step for step in steps if "tests/integration/multinode" in step["run"]]
    assert runs, "no step of multinode-redis-rows runs tests/integration/multinode"
    for step in runs:
        assert step.get("env", {}).get(multinode.REQUIRE_REDIS_ENV) == "1", step
    # The packaged systemd service is stopped: the harness starts its own servers.
    install = [step for step in steps if "apt-get install" in step["run"]]
    assert install and "systemctl stop redis-server" in install[0]["run"], install
    # Non-gating until it has been green on a runner once (#4136), then promoted.
    assert job.get("continue-on-error") is True
    summary = workflow["jobs"]["test-summary"]
    assert "multinode-redis-rows" not in summary["needs"]
