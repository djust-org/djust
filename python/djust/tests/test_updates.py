"""The update and security-advisory notice (djust.updates)."""

import json
from pathlib import Path

import pytest

from djust import updates

FIXTURE = Path(__file__).parent / "fixtures" / "advisories.json"
ADVISORIES = updates.parse_advisories(json.loads(FIXTURE.read_text()))


# --- Task 1: matching, comparison, rendering --------------------------------


def test_fixture_parses_every_published_advisory():
    assert len(ADVISORIES) == 15
    first = ADVISORIES[0]
    assert first.ghsa_id == "GHSA-xjw9-38cr-6372"
    assert first.severity == "high"
    assert first.range == "<= 1.1.1"
    assert first.patched == "1.1.2"


@pytest.mark.parametrize("version, count", [("1.0.6", 14), ("1.1.1", 1), ("1.1.3", 0)])
def test_advisories_matching_an_installed_version(version, count):
    assert len(updates.advisories_for(version, ADVISORIES)) == count


def test_malformed_advisory_entries_are_skipped():
    payload = [{"ghsa_id": "GHSA-x", "severity": "low", "vulnerabilities": [{}]}, {"nope": 1}]
    assert updates.parse_advisories(payload) == []


@pytest.mark.parametrize(
    "installed, latest, newer",
    [
        ("1.1.3", "1.2.1", True),
        ("1.2.1", "1.2.1", False),
        ("1.2.0rc8", "1.1.3", False),
        ("1.2.0rc8", "1.2.0", True),
        ("1.2.0rc8", "1.2.0rc9", True),
        ("1.1.3", "1.2.0rc1", False),
        ("1.1.3", "garbage", False),
    ],
)
def test_is_newer(installed, latest, newer):
    assert updates.is_newer(installed, latest) is newer


def test_message_for_a_newer_release():
    status = updates.UpdateStatus(installed="1.1.3", latest="1.2.1", advisories=[])
    assert (
        status.message("uv pip install -U djust")
        == "djust 1.2.1 is available (you have 1.1.3): uv pip install -U djust"
    )


def test_message_for_advisories_names_them_and_the_patched_version():
    status = updates.UpdateStatus(
        installed="1.1.1", latest="1.1.1", advisories=updates.advisories_for("1.1.1", ADVISORIES)
    )
    message = status.message("uv pip install -U djust")
    assert message.startswith(
        "SECURITY: djust 1.1.1 has 1 published advisory (GHSA-xjw9-38cr-6372)"
    )
    assert "upgrade to 1.1.2 or later: uv pip install -U djust" in message
    assert "https://github.com/djust-org/djust/security/advisories" in message


def test_security_message_wins_and_targets_the_higher_version():
    status = updates.UpdateStatus(
        installed="1.0.6", latest="1.2.1", advisories=updates.advisories_for("1.0.6", ADVISORIES)
    )
    message = status.message("uv pip install -U djust")
    assert message.startswith("SECURITY: djust 1.0.6 has 14 published advisories")
    assert "upgrade to 1.2.1 or later" in message


def test_no_message_when_current():
    assert updates.UpdateStatus("1.2.1", "1.2.1", []).message("x") is None


# --- Task 2: cache and gate ----------------------------------------------------

from unittest.mock import patch  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DJUST_CACHE_DIR", str(tmp_path))
    for key in ("CI", "DJUST_NO_UPDATE_CHECK"):
        monkeypatch.delenv(key, raising=False)
    return {"DJUST_CACHE_DIR": str(tmp_path)}


def fetchers(latest="1.2.1"):
    return (
        patch.object(updates, "fetch_latest", return_value=latest),
        patch.object(updates, "fetch_advisories", return_value=ADVISORIES),
    )


def test_fresh_cache_skips_the_network(env):
    updates.save_cache({"checked_at": 1000, "latest": "9.9.9", "advisories": []}, env)
    fl, fa = fetchers()
    with fl as latest, fa:
        status = updates.check(now=1000 + 3600, environ=env, installed="1.1.3")
    assert not latest.called
    assert status.latest == "9.9.9"


def test_expired_cache_fetches_and_rewrites(env):
    updates.save_cache({"checked_at": 0, "latest": "0.0.1", "advisories": []}, env)
    fl, fa = fetchers()
    with fl, fa:
        status = updates.check(now=10**6, environ=env, installed="1.1.1")
    assert status.latest == "1.2.1"
    assert [a.ghsa_id for a in status.advisories] == ["GHSA-xjw9-38cr-6372"]
    cached = updates.load_cache(env)
    assert cached["checked_at"] == 10**6 and len(cached["advisories"]) == 15


def test_failed_fetch_backs_off_for_an_hour(env):
    with patch.object(updates, "fetch_latest", side_effect=OSError("down")) as fetch:
        assert updates.check(now=5000, environ=env, installed="1.1.3") is None
        assert updates.check(now=5000 + 1800, environ=env, installed="1.1.3") is None
        assert fetch.call_count == 1
        updates.check(now=5000 + 3601, environ=env, installed="1.1.3")
        assert fetch.call_count == 2
    assert "failed_at" in updates.load_cache(env)


def test_corrupt_cache_is_treated_as_empty(env, tmp_path):
    (tmp_path / "updates.json").write_text("{not json")
    assert updates.load_cache(env) == {}
    assert updates.check(fetch=False, environ=env, installed="1.1.3") is None


def test_cache_only_check_never_fetches(env):
    with patch.object(updates, "fetch_latest") as fetch:
        assert updates.check(fetch=False, environ=env, installed="1.1.3") is None
    assert not fetch.called


@pytest.mark.parametrize(
    "kwargs",
    [
        {"environ": {"CI": "1"}},
        {"environ": {"DJUST_NO_UPDATE_CHECK": "1"}},
        {"environ": {"PYTEST_CURRENT_TEST": "x"}},
        {"environ": {}, "isatty": False},
        {"environ": {}, "debug": False},
        {"environ": {}, "config": {"update_check": False}},
    ],
)
def test_gate_is_off(kwargs):
    assert updates.should_check(**kwargs) is False


def test_gate_is_on_by_default():
    assert updates.should_check(environ={}, isatty=True, debug=True, config={}) is True


def test_background_check_reports_and_swallows_errors(env):
    seen = []
    fl, fa = fetchers()
    with fl, fa:
        updates.check_in_background(seen.append).join(5)
    with patch.object(updates, "check", side_effect=RuntimeError("boom")):
        updates.check_in_background(seen.append).join(5)
    assert len(seen) == 1 and seen[0].latest == "1.2.1"


@pytest.mark.parametrize(
    "argv0, hint",
    [
        ("/Users/x/.local/share/uv/tools/djust/bin/djust", "uv tool upgrade djust"),
        ("/tmp/uv-cache/archive-v0/abc/bin/djust", "uvx djust@latest"),
        (r"C:\Users\x\AppData\Roaming\uv\tools\djust\Scripts\djust.exe", "uv tool upgrade djust"),
    ],
)
def test_cli_install_hint(argv0, hint):
    assert updates.cli_install_hint(argv0) == hint


# --- Task 3: callers -----------------------------------------------------------

import argparse  # noqa: E402

from django.core.checks import Info, Warning  # noqa: E402


def _status(advisories=False):
    advs = updates.advisories_for("1.1.1", ADVISORIES) if advisories else []
    return updates.UpdateStatus(installed="1.1.1", latest="1.2.1", advisories=advs)


def test_cli_new_prints_the_notice_first(capsys, monkeypatch):
    from djust import cli
    from djust.scaffolding import generator

    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check", lambda **kw: _status())
    monkeypatch.setattr(updates, "cli_install_hint", lambda: "uvx djust@latest")
    with patch.object(generator, "generate_project"):
        cli.cmd_new(argparse.Namespace(name="child", no_setup=True))
    out = capsys.readouterr().out
    assert out.startswith("djust 1.2.1 is available (you have 1.1.1): uvx djust@latest\n")


def test_cli_new_is_silent_when_gated_off(capsys, monkeypatch):
    from djust import cli
    from djust.scaffolding import generator

    monkeypatch.setattr(updates, "should_check", lambda **kw: False)
    with patch.object(updates, "check") as check, patch.object(generator, "generate_project"):
        cli.cmd_new(argparse.Namespace(name="child", no_setup=True))
    assert not check.called
    assert "available" not in capsys.readouterr().out


def test_cli_init_prints_the_notice(tmp_path, monkeypatch, capsys):
    from djust import cli

    monkeypatch.chdir(tmp_path)  # no manage.py -> init refuses, but the notice comes first
    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check", lambda **kw: _status(advisories=True))
    cli.cmd_init(argparse.Namespace(settings=None, dry_run=True, no_install=True, force=False))
    assert capsys.readouterr().out.startswith("SECURITY: djust 1.1.1")


def test_ready_starts_the_background_check_only_when_allowed(settings, monkeypatch):
    from djust import apps

    settings.DEBUG = True
    started = []
    monkeypatch.setattr(updates, "check_in_background", lambda cb: started.append(cb))
    monkeypatch.setattr(updates, "should_check", lambda **kw: kw.get("debug") is True)
    apps._start_update_notice()
    assert len(started) == 1
    settings.DEBUG = False
    apps._start_update_notice()
    assert len(started) == 1


def test_ready_logs_the_message(settings, monkeypatch, caplog):
    from djust import apps

    settings.DEBUG = True
    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check_in_background", lambda cb: cb(_status()))
    with caplog.at_level("INFO", logger="djust.updates"):
        apps._start_update_notice()
    assert "djust 1.2.1 is available (you have 1.1.1): uv pip install -U djust" in caplog.text


def test_system_check_reads_cache_only(monkeypatch):
    from djust.checks import updates as check_module

    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    calls = []

    def fake_check(**kw):
        calls.append(kw)
        return _status(advisories=True)

    monkeypatch.setattr(updates, "check", fake_check)
    with patch.object(updates, "fetch_latest") as fetch:
        messages = check_module.check_updates(None)
    assert calls == [{"fetch": False}]
    assert not fetch.called
    assert len(messages) == 1
    assert isinstance(messages[0], Warning) and messages[0].id == "djust.U001"
    assert messages[0].msg.startswith("SECURITY")


def test_system_check_is_info_for_a_plain_release(monkeypatch):
    from djust.checks import updates as check_module

    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check", lambda **kw: _status())
    (message,) = check_module.check_updates(None)
    assert isinstance(message, Info) and not isinstance(message, Warning)


def test_system_check_respects_the_gate_and_empty_cache(monkeypatch):
    from djust.checks import updates as check_module

    monkeypatch.setattr(updates, "should_check", lambda **kw: False)
    assert check_module.check_updates(None) == []
    monkeypatch.setattr(updates, "should_check", lambda **kw: True)
    monkeypatch.setattr(updates, "check", lambda **kw: None)
    assert check_module.check_updates(None) == []
