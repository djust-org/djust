"""
Tests for djust deploy CLI commands.

Uses Click's test runner and requests_mock for HTTP mocking.
"""

import json
import stat
import threading
import time
import urllib.parse
import urllib.request
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


@pytest.fixture(autouse=True)
def _short_oauth_timeout(monkeypatch):
    """Cap OAuth browser-flow waits at 5s during tests so a stuck test
    fails in seconds instead of wedging the suite for the full 300s
    production timeout."""
    monkeypatch.setattr("djust.deploy_cli._OAUTH_BROWSER_TIMEOUT_SECONDS", 5, raising=False)


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


def _simulate_browser(captured_url_holder: dict, code: str = "test-auth-code-123"):
    """Stand in for ``webbrowser.open()`` during tests of the OAuth flow.

    The real browser receives the auth URL, the user clicks
    "Authorize", and the IdP 302s to ``redirect_uri?code=...&state=...``.
    The CLI's local HTTP server captures that callback. Tests don't have
    a browser or a real IdP — so this helper:

      1. Records the URL that ``webbrowser.open`` was handed (for asserts).
      2. Parses ``redirect_uri`` and ``state`` out of it.
      3. Spawns a background thread that does a real GET against the
         CLI's loopback callback with ``?code=...&state=...``.

    The CLI then continues as if the IdP had redirected the user — no
    actual browser involved.
    """

    def _open(url, *_args, **_kwargs):
        captured_url_holder["url"] = url
        parsed = urllib.parse.urlparse(url)
        qs = dict(urllib.parse.parse_qsl(parsed.query))
        redirect_uri = qs["redirect_uri"]
        state = qs["state"]

        def _hit_callback():
            # Tiny delay so the CLI's HTTPServer is listening before
            # we GET it. handle_request() blocks on accept(), but the
            # test occasionally races otherwise.
            time.sleep(0.05)
            # urllib.request — not requests — so requests_mock's global
            # interception doesn't try to mock the loopback callback.
            try:
                urllib.request.urlopen(  # noqa: S310 — loopback URL only
                    f"{redirect_uri}?code={code}&state={state}",
                    timeout=5,
                ).read()
            except Exception:
                pass

        threading.Thread(target=_hit_callback, daemon=True).start()
        return True

    return _open


class TestLoginCommand:
    """The ``djust deploy login`` command exercises the full PKCE
    auth-code flow against a mocked OIDC IdP."""

    def test_browser_login_writes_oauth_credentials(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        # /o/token/ exchange returns access + refresh + id_token.
        # The id_token's payload section base64-decodes to {"email":...}.
        # Manually constructed since we don't sign it in tests — the CLI
        # decodes-without-verifying for display.
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "user@example.com"}).encode())
            .rstrip(b"=")
            .decode()
        )
        id_token = f"header.{payload}.sig"

        token_adapter = requests_mock.post(
            "https://djustlive.com/o/token/",
            json={
                "access_token": "oauth-access-xyz",
                "refresh_token": "oauth-refresh-xyz",
                "expires_in": 3600,
                "id_token": id_token,
                "token_type": "Bearer",
            },
            status_code=200,
        )

        captured = {}
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser(captured))

        result = runner.invoke(cli, ["login"])
        assert result.exit_code == 0, result.output

        # Credentials saved with bearer shape, not legacy DRF token.
        cred_file = creds_dir / "credentials"
        assert cred_file.exists()
        data = json.loads(cred_file.read_text())
        assert data["auth_scheme"] == "bearer"
        assert data["access_token"] == "oauth-access-xyz"
        assert data["refresh_token"] == "oauth-refresh-xyz"
        assert data["email"] == "user@example.com"
        assert data["server_url"] == "https://djustlive.com"
        assert "expires_at" in data

        # The token-exchange request used PKCE parameters.
        assert token_adapter.called
        sent = token_adapter.last_request.text
        assert "grant_type=authorization_code" in sent
        assert "code_verifier=" in sent
        assert "client_id=djust-cli" in sent

        # The auth URL handed to webbrowser.open included the right shape.
        url = captured["url"]
        assert url.startswith("https://djustlive.com/o/authorize/")
        assert "response_type=code" in url
        assert "code_challenge_method=S256" in url
        assert "client_id=djust-cli" in url

    def test_browser_login_prints_success_message(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "alice@example.com"}).encode())
            .rstrip(b"=")
            .decode()
        )
        requests_mock.post(
            "https://djustlive.com/o/token/",
            json={
                "access_token": "x",
                "refresh_token": "y",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
                "token_type": "Bearer",
            },
        )
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser({}))
        result = runner.invoke(cli, ["login"])
        assert "Logged in as alice@example.com" in result.output

    def test_browser_login_state_mismatch_aborts(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        """If the callback's state doesn't match what the CLI generated,
        we refuse the code. Defends against CSRF / cross-tab leaks."""

        def _bad_state_browser(url, *_args, **_kwargs):
            parsed = urllib.parse.urlparse(url)
            qs = dict(urllib.parse.parse_qsl(parsed.query))
            redirect_uri = qs["redirect_uri"]
            # Use a wrong state — the handler should reject this.

            def _hit():
                time.sleep(0.05)
                try:
                    urllib.request.urlopen(  # noqa: S310
                        f"{redirect_uri}?code=abc&state=wrong-state",
                        timeout=5,
                    ).read()
                except Exception:
                    pass

            threading.Thread(target=_hit, daemon=True).start()
            return True

        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _bad_state_browser)
        result = runner.invoke(cli, ["login"])
        assert result.exit_code != 0
        assert "state mismatch" in result.output.lower()
        # No creds written.
        assert not (creds_dir / "credentials").exists()

    def test_browser_login_token_endpoint_500_aborts(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        requests_mock.post("https://djustlive.com/o/token/", status_code=500, text="boom")
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser({}))
        result = runner.invoke(cli, ["login"])
        assert result.exit_code != 0
        assert "Token exchange failed" in result.output

    def test_browser_login_idp_returns_error_aborts(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        """User clicked 'Deny' on the consent screen → callback gets
        ``?error=access_denied`` instead of a code."""

        def _denying_browser(url, *_args, **_kwargs):
            parsed = urllib.parse.urlparse(url)
            qs = dict(urllib.parse.parse_qsl(parsed.query))

            def _hit():
                time.sleep(0.05)
                try:
                    urllib.request.urlopen(  # noqa: S310
                        f"{qs['redirect_uri']}?error=access_denied"
                        f"&error_description=User+denied+access",
                        timeout=5,
                    ).read()
                except Exception:
                    pass

            threading.Thread(target=_hit, daemon=True).start()
            return True

        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _denying_browser)
        result = runner.invoke(cli, ["login"])
        assert result.exit_code != 0
        assert "denied" in result.output.lower() or "access_denied" in result.output.lower()

    def test_browser_login_custom_server_flag(self, runner, creds_dir, requests_mock, monkeypatch):
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "u@u.com"}).encode())
            .rstrip(b"=")
            .decode()
        )
        requests_mock.post(
            "https://myserver.example.com/o/token/",
            json={
                "access_token": "t",
                "refresh_token": "r",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
            },
        )
        captured = {}
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser(captured))
        result = runner.invoke(cli, ["--server", "https://myserver.example.com", "login"])
        assert result.exit_code == 0, result.output
        assert captured["url"].startswith("https://myserver.example.com/o/authorize/")
        data = json.loads((creds_dir / "credentials").read_text())
        assert data["server_url"] == "https://myserver.example.com"


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

    def _mock_preconditions(self, requests_mock, server="https://djustlive.com", slug="my-project"):
        """Stub the new guided-deploy preconditions (/me/ + /projects/<slug>/)
        as success, so the existing deploy-API tests assert on the deploy
        POST itself and not on the precondition chain."""
        requests_mock.get(
            f"{server}/api/v1/me/",
            json={"username": "u", "email": "user@example.com", "is_staff": False},
            status_code=200,
        )
        requests_mock.get(
            f"{server}/api/v1/projects/{slug}/",
            json={"slug": slug, "name": slug, "owner_email": "user@example.com"},
            status_code=200,
        )

    def test_deploy_clean_git_calls_api(self, runner, creds_dir, saved_creds, requests_mock):
        self._mock_preconditions(requests_mock)
        requests_mock.post(
            "https://djustlive.com/api/v1/projects/my-project/environments/production/deploy/",
            text="Build started\nDone\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "my-project"])
        assert result.exit_code == 0, result.output

    def test_deploy_streams_log_output(self, runner, creds_dir, saved_creds, requests_mock):
        self._mock_preconditions(requests_mock)
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
        self._mock_preconditions(requests_mock, slug="slug-x")
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
        # --no-create makes the precondition check fail-fast on a 404
        # for the project lookup, so this test exercises the same
        # "missing project" path it always did.
        requests_mock.get(
            "https://djustlive.com/api/v1/me/",
            json={"username": "u", "email": "u@e.com", "is_staff": False},
            status_code=200,
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/bad-slug/",
            json={"detail": "Not found."},
            status_code=404,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "bad-slug", "--no-create"])
        assert result.exit_code != 0

    def test_deploy_server_flag_overrides_default(
        self, runner, creds_dir, saved_creds, requests_mock
    ):
        self._mock_preconditions(requests_mock, server="https://other-server.com", slug="proj")
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
        # Mock the new precondition endpoints + the deploy endpoint.
        requests_mock.get(
            "https://djustlive.com/api/v1/me/",
            json={"username": "u", "email": "u@e.com", "is_staff": False},
            status_code=200,
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/auto-slug/",
            json={"slug": "auto-slug", "name": "auto-slug", "owner_email": "u@e.com"},
            status_code=200,
        )
        adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/auto-slug/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy"])
        assert adapter.called, f"expected slug resolution from pyproject.toml; got {result.output}"


# ---------------------------------------------------------------------------
# Guided onboarding (login if needed, create project if 404)
# ---------------------------------------------------------------------------


class TestGuidedDeploy:
    """Tests for the precondition chain that turns `djust deploy` into a
    single-command end-to-end flow:

      - `_ensure_logged_in`: if no creds, prompt for email/password and
        save token; if creds exist but /me/ 401s, drop and re-prompt.
      - `_ensure_project_exists`: if /projects/<slug>/ 404s, prompt to
        create (or auto-create with --yes, or fail with --no-create).
    """

    def test_yes_flag_creates_project_without_prompting(
        self, runner, creds_dir, saved_creds, requests_mock
    ):
        # /me/ ok, /projects/<slug>/ 404, POST /projects/ → 201
        requests_mock.get(
            "https://djustlive.com/api/v1/me/",
            json={"username": "u", "email": "u@e.com", "is_staff": False},
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/new-app/",
            json={"detail": "Not found."},
            status_code=404,
        )
        create_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/",
            json={
                "slug": "new-app",
                "name": "new-app",
                "instance_slug": "abc1",
                "owner_email": "u@e.com",
                "status": "active",
                "environments": ["production"],
                "created_at": "2026-05-07T00:00:00Z",
            },
            status_code=201,
        )
        deploy_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/new-app/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "new-app", "--yes"])
        assert create_adapter.called, "expected POST /projects/ to be called"
        assert deploy_adapter.called, "expected the deploy POST to follow project creation"
        assert result.exit_code == 0, result.output
        # Verify the body sent to POST /projects/ has name=new-app
        sent_body = create_adapter.last_request.json()
        assert sent_body == {"name": "new-app"}

    def test_no_create_flag_fails_on_404(self, runner, creds_dir, saved_creds, requests_mock):
        requests_mock.get(
            "https://djustlive.com/api/v1/me/",
            json={"username": "u", "email": "u@e.com", "is_staff": False},
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/missing/",
            json={"detail": "Not found."},
            status_code=404,
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "missing", "--no-create"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "no-create" in result.output.lower()

    def test_revoked_token_triggers_re_login(
        self, runner, creds_dir, saved_creds, requests_mock, monkeypatch
    ):
        """/me/ returns 401 with a *legacy* DRF token — the OAuth
        refresh path doesn't apply, so the CLI falls through to a fresh
        browser login. Verifies the re-auth path still works."""
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "u@e.com"}).encode())
            .rstrip(b"=")
            .decode()
        )

        requests_mock.get("https://djustlive.com/api/v1/me/", status_code=401)
        requests_mock.post(
            "https://djustlive.com/o/token/",
            json={
                "access_token": "fresh-bearer",
                "refresh_token": "fresh-refresh",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
                "token_type": "Bearer",
            },
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/p/",
            json={"slug": "p", "name": "p", "owner_email": "u@e.com"},
        )
        deploy_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/p/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )

        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser({}))
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "p"])

        assert deploy_adapter.called
        # The re-auth path uses the fresh OAuth bearer, not the stale
        # legacy DRF token.
        sent_auth = deploy_adapter.last_request.headers.get("Authorization", "")
        assert "Bearer fresh-bearer" in sent_auth, sent_auth
        assert result.exit_code == 0, result.output

    def test_oauth_refresh_swaps_token_silently(self, runner, creds_dir, requests_mock):
        """Bearer creds with a fresh refresh_token: when /me/ 401s, the
        CLI uses the refresh_token to mint a new access_token without
        re-launching the browser. The user shouldn't notice."""
        # Pre-seed bearer-shape credentials.
        cred_file = creds_dir / "credentials"
        cred_file.write_text(
            json.dumps(
                {
                    "auth_scheme": "bearer",
                    "access_token": "expired-access",
                    "refresh_token": "valid-refresh",
                    "expires_at": 0,
                    "email": "u@e.com",
                    "server_url": "https://djustlive.com",
                }
            )
        )
        cred_file.chmod(0o600)

        # First /me/ call (with expired token) → 401.
        requests_mock.get("https://djustlive.com/api/v1/me/", status_code=401)
        # Refresh exchange: returns fresh access token.
        requests_mock.post(
            "https://djustlive.com/o/token/",
            json={
                "access_token": "refreshed-access",
                "refresh_token": "rotated-refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            },
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/p/",
            json={"slug": "p", "name": "p", "owner_email": "u@e.com"},
        )
        deploy_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/p/environments/production/deploy/",
            text="ok\n",
        )
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "p"])
        assert result.exit_code == 0, result.output
        # Deploy got the refreshed bearer.
        sent_auth = deploy_adapter.last_request.headers.get("Authorization", "")
        assert "Bearer refreshed-access" in sent_auth
        # And the rotated refresh_token landed on disk.
        saved = json.loads(cred_file.read_text())
        assert saved["refresh_token"] == "rotated-refresh"

    def test_no_credentials_at_all_prompts_login(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        """Brand new user — no ~/.djustlive/credentials. CLI should
        guide them through browser login inline, no separate
        ``djust deploy login`` step."""
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "newbie@example.com"}).encode())
            .rstrip(b"=")
            .decode()
        )

        requests_mock.post(
            "https://djustlive.com/o/token/",
            json={
                "access_token": "newbie-bearer",
                "refresh_token": "newbie-refresh",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
            },
        )
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/my-app/",
            json={
                "slug": "my-app",
                "name": "my-app",
                "owner_email": "newbie@example.com",
            },
        )
        deploy_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/my-app/environments/production/deploy/",
            text="ok\n",
            status_code=200,
        )
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser({}))
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "my-app"])
        assert deploy_adapter.called
        assert result.exit_code == 0, result.output
