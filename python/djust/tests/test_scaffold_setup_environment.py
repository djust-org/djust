"""Scaffolding must not install into an active parent environment or hide errors."""

import argparse
import os
import subprocess
from unittest.mock import patch

import pytest
from django.core.management.base import CommandError

from djust import cli
from djust.management.commands.djust_new import Command
from djust.scaffolding import generator


def test_setup_targets_new_environment_with_another_environment_active(tmp_path, monkeypatch):
    parent_env = tmp_path / "parent-env"
    monkeypatch.setenv("VIRTUAL_ENV", str(parent_env))
    project = tmp_path / "child"
    project.mkdir()
    commands = []
    with (
        patch.object(generator.shutil, "which", return_value="uv"),
        patch.object(generator, "_run_cmd", side_effect=lambda cmd, cwd: commands.append(cmd)),
    ):
        generator._run_auto_setup(project)

    interpreter = str(
        project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    install = next(cmd for cmd in commands if "install" in cmd)
    assert "--python" in install
    assert install[install.index("--python") + 1] == interpreter
    assert all(cmd[0] == interpreter for cmd in commands if "manage.py" in cmd)
    assert os.environ["VIRTUAL_ENV"] == str(parent_env)


@pytest.mark.parametrize("failed_step", range(5))
def test_setup_stops_at_first_failed_command(tmp_path, capsys, failed_step):
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        if len(calls) == failed_step + 1:
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="setup failed")
        return subprocess.CompletedProcess(cmd, 0)

    with (
        patch.object(generator.shutil, "which", return_value="uv"),
        patch.object(generator.subprocess, "run", side_effect=run),
        pytest.raises(generator.ScaffoldSetupError, match="Generated files remain"),
    ):
        generator._run_auto_setup(tmp_path)
    assert len(calls) == failed_step + 1
    assert "Done!" not in capsys.readouterr().out


def test_missing_executable_fails_setup(tmp_path):
    with (
        patch.object(generator.subprocess, "run", side_effect=FileNotFoundError),
        pytest.raises(generator.ScaffoldSetupError, match="not found"),
    ):
        generator._run_cmd(["missing-tool"], tmp_path)


@pytest.mark.parametrize("entrypoint", [cli.cmd_new, cli.cmd_startproject])
def test_cli_reports_failed_setup_without_success_message(entrypoint, capsys):
    with (
        patch.object(
            generator,
            "generate_project",
            side_effect=generator.ScaffoldSetupError("install failed"),
        ),
        pytest.raises(SystemExit) as exc,
    ):
        entrypoint(argparse.Namespace(name="child"))
    assert exc.value.code == 1
    output = capsys.readouterr().out
    assert "install failed" in output
    assert "Created djust project" not in output
    assert "Next steps:" not in output


def test_management_command_reports_setup_failure():
    with (
        patch.object(
            generator,
            "generate_project",
            side_effect=generator.ScaffoldSetupError("install failed"),
        ),
        pytest.raises(CommandError, match="install failed"),
    ):
        Command().handle(
            app_name="child",
            with_auth=False,
            with_db=False,
            with_presence=False,
            with_streaming=False,
            no_setup=False,
        )


def test_generated_project_runs_without_activation(tmp_path):
    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    makefile = (project / "Makefile").read_text()
    assert "PYTHON = .venv/bin/python" in makefile
    assert "\t$(PYTHON) -m uvicorn child.asgi:application" in makefile
    assert "\tuvicorn " not in makefile
    assert "\tpython manage.py" not in makefile


def test_exported_python_variable_does_not_escape_the_project_environment(tmp_path):
    import shutil

    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    env = dict(os.environ, PYTHON="/usr/bin/python3")
    dry = subprocess.run(
        ["make", "-n", "dev"], cwd=project, env=env, capture_output=True, text=True
    ).stdout
    assert dry.startswith(".venv/bin/python -m uvicorn")


def test_requirements_floor_is_generating_version(tmp_path):
    from djust import __version__

    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    requirements = (project / "requirements.txt").read_text().splitlines()
    assert "djust>=%s" % __version__.split("+")[0] in requirements


def test_generated_asgi_uses_project_settings(tmp_path):
    project = generator.generate_project("child", target_dir=str(tmp_path), auto_setup=False)
    asgi = (project / "child" / "asgi.py").read_text()
    assert '"DJANGO_SETTINGS_MODULE", "child.settings"' in asgi


def test_setup_pins_python_and_runs_check_last(tmp_path):
    commands = []
    with (
        patch.object(generator.shutil, "which", return_value="uv"),
        patch.object(generator, "_run_cmd", side_effect=lambda cmd, cwd: commands.append(cmd)),
    ):
        generator._run_auto_setup(tmp_path)
    assert commands[0][:4] == ["uv", "venv", "--python", ">=3.10"]
    assert commands[-1][1:] == ["manage.py", "check"]


def test_next_steps_after_setup(monkeypatch):
    monkeypatch.setattr(generator.os, "name", "posix")
    assert generator.next_steps("child", setup_ran=True) == ["cd child", "make dev"]


def test_next_steps_without_setup_target_project_environment(monkeypatch):
    monkeypatch.setattr(generator.os, "name", "posix")
    steps = generator.next_steps("child", setup_ran=False)
    assert steps[0] == "cd child"
    assert "uv pip install --python .venv -r requirements.txt" in steps
    assert ".venv/bin/python manage.py migrate" in steps
    assert steps[-1] == "make dev"


def test_cli_success_prints_next_steps(capsys, monkeypatch):
    monkeypatch.setattr(generator.os, "name", "posix")
    with patch.object(generator, "generate_project"):
        cli.cmd_new(argparse.Namespace(name="child", no_setup=False))
    output = capsys.readouterr().out
    assert "  cd child\n  make dev\n" in output
    assert "http://127.0.0.1:8000/" in output
