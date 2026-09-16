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


@pytest.mark.parametrize("failed_step", range(4))
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
