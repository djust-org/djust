"""
Tests for djust deploy CLI commands.

Uses Click's test runner and requests_mock for HTTP mocking.
"""

import json
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("click", reason="click not installed")
pytest.importorskip("requests", reason="requests not installed")
pytest.importorskip("requests_mock", reason="requests_mock not installed")

import click  # noqa: E402
from click.testing import CliRunner  # noqa: E402

from djust.deploy_cli import (  # noqa: E402
    cli,
    credentials_path,
    load_credentials,
    save_credentials,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def creds_dir(tmp_path, monkeypatch):
    """Redirect ~/.djustlive to a temp directory."""
    djustlive_dir = tmp_path / ".djustlive"
    djustlive_dir.mkdir()
    monkeypatch.setattr(
        "djust.deploy_cli.credentials_path",
        lambda: djustlive_dir / "credentials",
    )
    return djustlive_dir


@pytest.fixture()
def saved_creds(creds_dir):
    """Write valid credentials to the temp credentials file."""
    creds = {
        "token": "test-token-abc",
        "email": "user@example.com",
        "server_url": "https://djustlive.com",
    }
    cred_file = creds_dir / "credentials"
    cred_file.write_text(json.dumps(creds))
    cred_file.chmod(0o600)
    return creds


# ---------------------------------------------------------------------------
# Credential helper tests
# ---------------------------------------------------------------------------


class TestCredentialHelpers:
    def test_credentials_path_returns_correct_location(self):
        path = credentials_path()
        assert path.name == "credentials"
        assert path.parent.name == ".djustlive"
        assert path.parent.parent == Path.home()

    def test_save_credentials_creates_file(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        save_credentials("tok123", "a@b.com", "https://djustlive.com")
        assert cred_file.exists()

    def test_save_credentials_creates_parent_dir(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        save_credentials("tok123", "a@b.com", "https://djustlive.com")
        assert cred_file.parent.is_dir()

    def test_save_credentials_file_mode_0o600(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        save_credentials("tok123", "a@b.com", "https://djustlive.com")
        file_mode = stat.S_IMODE(cred_file.stat().st_mode)
        assert file_mode == 0o600

    def test_save_credentials_json_content(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        save_credentials("tok123", "a@b.com", "https://myserver.com")
        data = json.loads(cred_file.read_text())
        assert data == {
            "token": "tok123",
            "email": "a@b.com",
            "server_url": "https://myserver.com",
        }

    def test_save_credentials_overwrites_existing(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        save_credentials("tok1", "a@a.com", "https://s1.com")
        save_credentials("tok2", "b@b.com", "https://s2.com")
        data = json.loads(cred_file.read_text())
        assert data["token"] == "tok2"
        assert data["email"] == "b@b.com"

    def test_load_credentials_returns_dict(self, tmp_path, monkeypatch):
        cred_file = tmp_path / ".djustlive" / "credentials"
        cred_file.parent.mkdir(parents=True, exist_ok=True)
        cred_file.write_text(
            json.dumps({"token": "t", "email": "e@e.com", "server_url": "https://s"})
        )
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        creds = load_credentials()
        assert creds["token"] == "t"
        assert creds["email"] == "e@e.com"

    def test_load_credentials_missing_file_raises(self, tmp_path, monkeypatch):
        """load_credentials() raises ClickException when the credentials file does not exist.

        The exception message must contain "Not logged in" so that the CLI
        surfaces a clear, actionable error rather than a bare FileNotFoundError.
        """
        cred_file = tmp_path / ".djustlive" / "credentials"
        monkeypatch.setattr("djust.deploy_cli.credentials_path", lambda: cred_file)
        with pytest.raises(click.ClickException, match="Not logged in"):
            load_credentials()


# ---------------------------------------------------------------------------
# login command
# ---------------------------------------------------------------------------


class TestLoginCommand:
    def test_login_success_writes_credentials(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/auth/login/",
            json={"token": "server-token-xyz"},
            status_code=200,
        )
        result = runner.invoke(
            cli,
            ["login"],
            input="user@example.com\nsecretpass\n",
        )
        assert result.exit_code == 0, result.output
        cred_file = creds_dir / "credentials"
        assert cred_file.exists()
        data = json.loads(cred_file.read_text())
        assert data["token"] == "server-token-xyz"
        assert data["email"] == "user@example.com"
        assert data["server_url"] == "https://djustlive.com"

    def test_login_success_prints_success_message(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/auth/login/",
            json={"token": "server-token-xyz"},
            status_code=200,
        )
        result = runner.invoke(cli, ["login"], input="user@example.com\npass\n")
        assert "Logged in" in result.output or "logged in" in result.output.lower()

    def test_login_failure_401_does_not_write_credentials(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/auth/login/",
            json={"detail": "Invalid credentials"},
            status_code=401,
        )
        result = runner.invoke(cli, ["login"], input="user@example.com\nwrongpass\n")
        assert result.exit_code != 0
        cred_file = creds_dir / "credentials"
        assert not cred_file.exists()

    def test_login_failure_shows_error(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/auth/login/",
            json={"detail": "Invalid credentials"},
            status_code=401,
        )
        result = runner.invoke(cli, ["login"], input="user@example.com\nwrongpass\n")
        assert (
            "Invalid" in result.output
            or "invalid" in result.output.lower()
            or "error" in result.output.lower()
            or "failed" in result.output.lower()
        )

    def test_login_custom_server_flag(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://myserver.example.com/api/v1/auth/login/",
            json={"token": "custom-token"},
            status_code=200,
        )
        result = runner.invoke(
            cli,
            ["--server", "https://myserver.example.com", "login"],
            input="u@u.com\npass\n",
        )
        assert result.exit_code == 0, result.output
        data = json.loads((creds_dir / "credentials").read_text())
        assert data["server_url"] == "https://myserver.example.com"

    def test_login_server_env_var(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://env-server.com/api/v1/auth/login/",
            json={"token": "env-token"},
            status_code=200,
        )
        result = runner.invoke(
            cli,
            ["login"],
            input="u@u.com\npass\n",
            env={"DJUST_SERVER": "https://env-server.com"},
        )
        assert result.exit_code == 0, result.output

    def test_login_empty_email_prompts(self, runner, creds_dir, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/auth/login/",
            json={"token": "t"},
            status_code=200,
        )
        result = runner.invoke(cli, ["login"], input="test@test.com\npass\n")
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# logout command
# ---------------------------------------------------------------------------


class TestLogoutCommand:
    def test_logout_calls_delete_endpoint(self, runner, creds_dir, saved_creds, requests_mock):
        adapter = requests_mock.delete(
            "https://djustlive.com/api/v1/auth/logout/",
            status_code=204,
        )
        result = runner.invoke(cli, ["logout"])
        assert result.exit_code == 0, result.output
        assert adapter.called

    def test_logout_removes_credentials_file(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.delete(
            "https://djustlive.com/api/v1/auth/logout/",
            status_code=204,
        )
        runner.invoke(cli, ["logout"])
        assert not (creds_dir / "credentials").exists()

    def test_logout_prints_confirmation(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.delete(
            "https://djustlive.com/api/v1/auth/logout/",
            status_code=204,
        )
        result = runner.invoke(cli, ["logout"])
        assert "logged out" in result.output.lower() or "success" in result.output.lower()

    def test_logout_when_not_logged_in_gives_message(self, runner, creds_dir):
        result = runner.invoke(cli, ["logout"])
        # Should not crash — just inform the user
        assert result.exit_code == 0 or "not logged in" in result.output.lower()


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_no_project_calls_endpoint(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.get(
            "https://djustlive.com/api/v1/deployments/status/",
            json={"deployments": []},
            status_code=200,
        )
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, result.output

    def test_status_with_project_passes_param(self, runner, creds_dir, saved_creds, requests_mock):
        adapter = requests_mock.get(
            "https://djustlive.com/api/v1/deployments/status/",
            json={"project": "myapp", "state": "running"},
            status_code=200,
        )
        result = runner.invoke(cli, ["status", "myapp"])
        assert result.exit_code == 0, result.output
        assert adapter.last_request.qs.get("project") == ["myapp"]

    def test_status_prints_response(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.get(
            "https://djustlive.com/api/v1/deployments/status/",
            json={"state": "running", "project": "myapp"},
            status_code=200,
        )
        result = runner.invoke(cli, ["status"])
        assert "running" in result.output or "myapp" in result.output

    def test_status_not_logged_in_shows_error(self, runner, creds_dir):
        result = runner.invoke(cli, ["status"])
        assert result.exit_code != 0
        assert "not logged in" in result.output.lower() or "error" in result.output.lower()

    def test_status_sends_auth_header(self, runner, creds_dir, saved_creds, requests_mock):
        adapter = requests_mock.get(
            "https://djustlive.com/api/v1/deployments/status/",
            json={},
            status_code=200,
        )
        runner.invoke(cli, ["status"])
        assert "Token test-token-abc" in adapter.last_request.headers.get("Authorization", "")


# ---------------------------------------------------------------------------
# deploy command
# ---------------------------------------------------------------------------


class TestDeployCommand:
    def test_deploy_dirty_git_aborts(self, runner, creds_dir, saved_creds):
        with patch("djust.deploy_cli._check_git_clean") as mock_check:
            mock_check.side_effect = click.ClickException(
                "Working tree is not clean. Commit or stash changes before deploying."
            )
            result = runner.invoke(cli, ["deploy", "my-project"])
        assert result.exit_code != 0
        assert "not clean" in result.output.lower() or "clean" in result.output.lower()

    def test_deploy_clean_git_calls_api(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/projects/my-project/environments/production/deploy/",
            text="Build started\nDone\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "my-project"])
        assert result.exit_code == 0, result.output

    def test_deploy_streams_log_output(self, runner, creds_dir, saved_creds, requests_mock):
        log_lines = "Step 1/5: Installing dependencies\nStep 2/5: Collecting static\nDone!\n"
        requests_mock.post(
            "https://djustlive.com/api/v1/projects/my-project/environments/production/deploy/",
            text=log_lines,
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "my-project"])
        assert "Installing dependencies" in result.output or "Done" in result.output

    def test_deploy_sends_auth_header(self, runner, creds_dir, saved_creds, requests_mock):
        adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/slug-x/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            runner.invoke(cli, ["deploy", "slug-x"])
        assert "Token test-token-abc" in adapter.last_request.headers.get("Authorization", "")

    def test_deploy_not_logged_in_shows_error(self, runner, creds_dir):
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "my-project"])
        assert result.exit_code != 0

    def test_deploy_api_error_shows_message(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.post(
            "https://djustlive.com/api/v1/projects/bad-slug/environments/production/deploy/",
            json={"detail": "Project not found"},
            status_code=404,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "bad-slug"])
        assert result.exit_code != 0

    def test_deploy_server_flag_overrides_default(
        self, runner, creds_dir, saved_creds, requests_mock
    ):
        adapter = requests_mock.post(
            "https://other-server.com/api/v1/projects/proj/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            runner.invoke(
                cli,
                ["--server", "https://other-server.com", "deploy", "proj"],
            )
        assert adapter.called


# ---------------------------------------------------------------------------
# Project-slug resolution
# ---------------------------------------------------------------------------


class TestSlugResolution:
    """Tests for _resolve_project_slug and the explicit > pyproject > prompt
    precedence chain. Covers the "don't make me retype the slug every deploy"
    feature."""

    def test_explicit_arg_wins_over_pyproject(self, tmp_path):
        from djust.deploy_cli import _resolve_project_slug

        (tmp_path / "pyproject.toml").write_text('[tool.djust.deploy]\nproject = "from-file"\n')
        assert _resolve_project_slug("from-arg", tmp_path, interactive=False) == "from-arg"

    def test_pyproject_used_when_arg_missing(self, tmp_path):
        from djust.deploy_cli import _resolve_project_slug

        (tmp_path / "pyproject.toml").write_text('[tool.djust.deploy]\nproject = "saved-slug"\n')
        assert _resolve_project_slug(None, tmp_path, interactive=False) == "saved-slug"

    def test_missing_arg_and_no_pyproject_raises_when_non_interactive(self, tmp_path):
        from djust.deploy_cli import _resolve_project_slug

        with pytest.raises(click.ClickException):
            _resolve_project_slug(None, tmp_path, interactive=False)

    def test_pyproject_missing_djust_section_falls_through(self, tmp_path):
        """A pyproject.toml that exists but doesn't have the section
        should not error — the prompt path takes over."""
        from djust.deploy_cli import _resolve_project_slug

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "thing"\n')
        with pytest.raises(click.ClickException):
            _resolve_project_slug(None, tmp_path, interactive=False)

    def test_malformed_pyproject_falls_through(self, tmp_path):
        """A pyproject.toml that fails to parse should not crash the
        deploy — fall through to the prompt path."""
        from djust.deploy_cli import _resolve_project_slug

        (tmp_path / "pyproject.toml").write_text("not valid TOML at all !@#$%")
        with pytest.raises(click.ClickException):
            _resolve_project_slug(None, tmp_path, interactive=False)

    def test_save_appends_block_to_pyproject(self, tmp_path):
        from djust.deploy_cli import _save_slug_to_pyproject, _read_slug_from_pyproject

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "thing"\nversion = "0.1.0"\n')
        assert _save_slug_to_pyproject(tmp_path, "my-project") is True
        # Round-trips: the next read picks up the saved value.
        assert _read_slug_from_pyproject(tmp_path) == "my-project"
        # Original section preserved.
        contents = (tmp_path / "pyproject.toml").read_text()
        assert "[project]" in contents
        assert 'name = "thing"' in contents

    def test_save_returns_false_when_no_pyproject(self, tmp_path):
        from djust.deploy_cli import _save_slug_to_pyproject

        # tmp_path has no pyproject.toml — save should fail gracefully.
        assert _save_slug_to_pyproject(tmp_path, "x") is False

    def test_deploy_command_falls_through_to_pyproject(
        self, runner, creds_dir, saved_creds, requests_mock, tmp_path, monkeypatch
    ):
        """End-to-end: `djust deploy` with no arg reads from pyproject.toml."""
        (tmp_path / "pyproject.toml").write_text('[tool.djust.deploy]\nproject = "auto-slug"\n')
        monkeypatch.chdir(tmp_path)
        adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/auto-slug/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy"])
        assert adapter.called, f"expected slug resolution from pyproject.toml; got {result.output}"
