"""CLI settings resolution and standalone discovery."""

import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest
from djust import cli


def test_ai_is_listed_in_djust_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["djust", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "AI capability discovery" in capsys.readouterr().out


def test_ai_suggest_outside_a_project(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.setattr(sys, "argv", ["djust", "ai", "suggest", "data table", "--json"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["name"] == "data_table"


def test_ai_inventory_outside_a_project_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    assert cli.cmd_ai(["inventory"]) == 2
    err = capsys.readouterr().err
    assert "manage.py" in err and "DJANGO_SETTINGS_MODULE" in err


def test_ai_delegates_to_manage_py(tmp_path, monkeypatch):
    manage = tmp_path / "manage.py"
    manage.write_text("")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(subprocess, "run", run)
    assert cli.cmd_ai(["inventory", "--json"]) == 7
    assert calls == [
        ([sys.executable, str(manage), "djust_ai", "inventory", "--json"], {"cwd": str(tmp_path)})
    ]


def test_ai_uses_settings_module_in_process(monkeypatch):
    import django
    from django.core import management

    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "test.settings")
    monkeypatch.setattr(django, "setup", lambda: None)
    calls = []
    monkeypatch.setattr(management, "call_command", lambda *args: calls.append(args))
    assert cli.cmd_ai(["suggest", "x"]) == 0
    assert calls == [("djust_ai", "suggest", "x")]


def test_ai_manifest_runs_without_django_in_a_subprocess(tmp_path):
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [sys.executable, "-m", "djust", "ai", "manifest"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "## UI components" in result.stdout


@pytest.mark.parametrize("subcommand,code", [("inventory", 2), ("manifest", 0), ("suggest", 0)])
def test_ai_bad_settings_falls_back_only_for_static_commands(
    subcommand, code, tmp_path, monkeypatch, capsys
):
    import django

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "missing.settings")

    def bad_setup():
        raise ImportError("missing settings")

    monkeypatch.setattr(django, "setup", bad_setup)
    rest = [subcommand] + (["data table"] if subcommand == "suggest" else [])
    assert cli.cmd_ai(rest) == code
    assert "could not load settings" in capsys.readouterr().err
