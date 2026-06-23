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


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    """Belt-and-suspenders: if a test reaches `_login_browser` without
    explicitly mocking `webbrowser.open`, this default no-op stops the
    user's real browser from popping up to djustlive.com mid-suite.

    Tests that need to simulate the OAuth flow do `monkeypatch.setattr(
    "djust.deploy_cli.webbrowser.open", _simulate_browser(...))` —
    that later setattr wins, so the simulator still drives the flow.
    """
    monkeypatch.setattr("djust.deploy_cli.webbrowser.open", lambda *a, **kw: True)


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

    # Completion signal so tests reading captured_url_holder can wait
    # for the callback thread to finish populating it. Without this the
    # main test thread occasionally asserts BEFORE the daemon thread
    # has stashed response_headers / response_status — Python 3.13's
    # scheduling tickles this race in CI.
    done = threading.Event()
    captured_url_holder["done"] = done

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
                resp = urllib.request.urlopen(  # noqa: S310 — loopback URL only
                    f"{redirect_uri}?code={code}&state={state}",
                    timeout=5,
                )
                resp.read()
                # Stash response headers + status so tests can assert
                # on the callback HTML's defense-in-depth headers.
                captured_url_holder["response_status"] = resp.status
                captured_url_holder["response_headers"] = dict(resp.headers.items())
            except Exception:
                pass
            finally:
                done.set()

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

    def test_callback_response_has_security_headers(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        """The loopback callback HTML response must carry
        Referrer-Policy: no-referrer + Cache-Control: no-store so the
        ?code=…&state=… URL doesn't leak into Referer headers or
        browser/proxy caches. RFC 8252 §8.10."""
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "u@e.com"}).encode())
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
            },
        )
        captured = {}
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser(captured))
        result = runner.invoke(cli, ["login"])
        assert result.exit_code == 0, result.output

        # Wait for the callback thread to finish populating the response
        # headers — the daemon thread in _simulate_browser may still be
        # writing them when runner.invoke returns. (Race observed under
        # py3.13's scheduling.)
        assert captured["done"].wait(timeout=5), "callback thread did not finish in 5s"

        headers = captured.get("response_headers") or {}
        assert headers.get("Referrer-Policy") == "no-referrer", headers
        cache = headers.get("Cache-Control") or ""
        assert "no-store" in cache, headers

    def test_http_server_url_rejected(self, runner, creds_dir):
        """Cleartext --server (non-loopback) is refused — tokens and the
        PKCE code_verifier would otherwise flow over the wire in the
        clear."""
        result = runner.invoke(cli, ["--server", "http://djustlive.com", "login"])
        assert result.exit_code != 0
        assert "https" in result.output.lower()

    def test_http_loopback_url_allowed(self, runner, creds_dir, requests_mock, monkeypatch):
        """http://127.0.0.1 / http://localhost stays allowed for local
        IdP development."""
        import base64

        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "u@e.com"}).encode())
            .rstrip(b"=")
            .decode()
        )
        requests_mock.post(
            "http://127.0.0.1:8000/o/token/",
            json={
                "access_token": "x",
                "refresh_token": "y",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
            },
        )
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser({}))
        result = runner.invoke(cli, ["--server", "http://127.0.0.1:8000", "login"])
        assert result.exit_code == 0, result.output


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

    def test_save_is_idempotent_under_server_uniquification(self, tmp_path):
        """Server slug-uniquification (`my-app` → `my-app-2`) triggers
        a SECOND call to `_save_slug_to_pyproject`. The result must be
        a parseable pyproject.toml with the latest slug — the prior
        append-only writer left a duplicate `[tool.djust.deploy]`
        table, which `tomllib` rejects with TOMLDecodeError."""
        import tomllib

        from djust.deploy_cli import _save_slug_to_pyproject, _read_slug_from_pyproject

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "thing"\n')

        # First save: appends the table.
        assert _save_slug_to_pyproject(tmp_path, "my-app") is True
        # Second save (server uniquified): must replace, not append.
        assert _save_slug_to_pyproject(tmp_path, "my-app-2") is True

        contents = (tmp_path / "pyproject.toml").read_text()
        # Parses cleanly — no duplicate-table TOMLDecodeError.
        data = tomllib.loads(contents)
        assert data["tool"]["djust"]["deploy"]["project"] == "my-app-2"
        # And the round-trip via _read_slug_from_pyproject sees the new value.
        assert _read_slug_from_pyproject(tmp_path) == "my-app-2"
        # Exactly one [tool.djust.deploy] header in the file.
        assert contents.count("[tool.djust.deploy]") == 1

    def test_save_replaces_in_place_when_table_already_present(self, tmp_path):
        """If the user hand-wrote `[tool.djust.deploy] project = "old"`
        before any deploy, the writer must replace that line — not
        append a sibling table."""
        import tomllib

        from djust.deploy_cli import _save_slug_to_pyproject

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "thing"\n\n[tool.djust.deploy]\nproject = "old-slug"\n'
        )
        assert _save_slug_to_pyproject(tmp_path, "new-slug") is True
        contents = (tmp_path / "pyproject.toml").read_text()
        data = tomllib.loads(contents)
        assert data["tool"]["djust"]["deploy"]["project"] == "new-slug"
        assert "old-slug" not in contents

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

    def test_no_create_with_no_slug_and_no_pyproject_fails(
        self, runner, creds_dir, saved_creds, requests_mock, tmp_path, monkeypatch
    ):
        """`deploy --no-create` with no slug arg and no pyproject must
        fail fast rather than prompt. Regression for the same op-precedence
        class as the _ensure_logged_in fix — _resolve_project_slug also
        needs `interactive=not no_create` propagated from both call
        sites (deploy and deploy_dir)."""
        # Empty tmp dir as cwd — no pyproject.toml, so slug must come
        # from the (missing) CLI arg or fail.
        monkeypatch.chdir(tmp_path)
        requests_mock.get(
            "https://djustlive.com/api/v1/me/",
            json={"username": "u", "email": "u@e.com", "is_staff": False},
        )
        with patch("djust.deploy_cli._check_git_clean"):
            # No positional slug; --no-create.
            result = runner.invoke(cli, ["deploy", "--no-create"])
        assert result.exit_code != 0
        # The non-interactive failure message mentions pyproject — that's
        # the unique signal the slug-resolution path gave up early.
        assert "pyproject" in result.output.lower() or "slug" in result.output.lower()

    def test_no_create_with_no_creds_fails_non_interactively(
        self, runner, creds_dir, requests_mock
    ):
        """`deploy --no-create` with no saved credentials must fail
        fast rather than launch a browser. Regression for an
        operator-precedence bug where ``interactive=not no_create or True``
        always evaluated truthy and silently launched the OAuth flow."""
        # No creds on disk; --no-create should refuse to bootstrap
        # interactively. We do NOT mock /o/token/ — if the CLI tries to
        # launch a browser flow, the test harness will hang or error,
        # which is itself a fail.
        with (
            patch("djust.deploy_cli._check_git_clean"),
            patch("djust.deploy_cli.webbrowser.open") as mock_open,
        ):
            result = runner.invoke(cli, ["deploy", "p", "--no-create"])
        assert result.exit_code != 0
        # And the browser was never opened.
        assert not mock_open.called, (
            "--no-create should not launch a browser; got mock_open.call_args_list="
            f"{mock_open.call_args_list}"
        )

    def test_stale_refresh_token_falls_back_to_browser(
        self, runner, creds_dir, requests_mock, monkeypatch
    ):
        """Bearer creds + dead refresh_token: /o/token/ returns 400 on
        the refresh attempt; the CLI must launch a fresh browser flow
        rather than give up. End-to-end coverage of the refresh-fallback
        path."""
        import base64

        cred_file = creds_dir / "credentials"
        cred_file.write_text(
            json.dumps(
                {
                    "auth_scheme": "bearer",
                    "access_token": "expired-access",
                    "refresh_token": "long-dead-refresh",
                    "expires_at": 0,
                    "email": "u@e.com",
                    "server_url": "https://djustlive.com",
                }
            )
        )
        cred_file.chmod(0o600)

        # /me/ refuses the expired access token.
        requests_mock.get("https://djustlive.com/api/v1/me/", status_code=401)

        # /o/token/ matches BOTH the refresh attempt AND the subsequent
        # auth-code exchange. requests_mock dispatches by body content
        # via a single adapter that branches on grant_type.
        payload = (
            base64.urlsafe_b64encode(json.dumps({"email": "u@e.com"}).encode())
            .rstrip(b"=")
            .decode()
        )

        def _token_handler(request, _ctx):
            body = dict(urllib.parse.parse_qsl(request.text or ""))
            if body.get("grant_type") == "refresh_token":
                _ctx.status_code = 400
                return {"error": "invalid_grant"}
            # authorization_code branch — fresh browser login succeeded.
            _ctx.status_code = 200
            return {
                "access_token": "post-fallback-access",
                "refresh_token": "post-fallback-refresh",
                "expires_in": 3600,
                "id_token": f"a.{payload}.s",
                "token_type": "Bearer",
            }

        requests_mock.post("https://djustlive.com/o/token/", json=_token_handler)
        requests_mock.get(
            "https://djustlive.com/api/v1/projects/p/",
            json={"slug": "p", "name": "p", "owner_email": "u@e.com"},
        )
        deploy_adapter = requests_mock.post(
            "https://djustlive.com/api/v1/projects/p/environments/production/deploy/",
            text="ok\n",
        )

        captured = {}
        monkeypatch.setattr("djust.deploy_cli.webbrowser.open", _simulate_browser(captured))
        with patch("djust.deploy_cli._check_git_clean"):
            result = runner.invoke(cli, ["deploy", "p"])
        assert result.exit_code == 0, result.output
        # The browser flow ran (refresh failed → fallback path executed).
        assert "url" in captured, "expected browser to be launched after refresh failure"
        assert captured["url"].startswith("https://djustlive.com/o/authorize/")
        # Final deploy used the post-fallback access token, not the
        # rotated value from a refresh attempt that never succeeded.
        sent_auth = deploy_adapter.last_request.headers.get("Authorization", "")
        assert "Bearer post-fallback-access" in sent_auth, sent_auth


class TestCreateTarball:
    """Regression tests for _create_tarball exclude matching.

    Exclusion is path-segment / basename anchored (NOT substring-matched):
    the five typed exclude groups (EXCLUDE_DIR_NAMES, EXCLUDE_DIR_SUFFIXES,
    EXCLUDE_FILE_SUFFIXES, EXCLUDE_FILENAMES, EXCLUDE_FILENAME_STEMS) replaced
    the flat substring-matched TARBALL_EXCLUDES constant. See #1505.
    """

    def _build_source(self, tmp_path, layout):
        """Create files described by ``layout`` (POSIX relative paths)."""
        src = tmp_path / "src"
        src.mkdir()
        for rel in layout:
            target = src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        return src

    def test_create_tarball_uses_exclude_dir_names_constant(self, tmp_path, monkeypatch):
        """_create_tarball must consult EXCLUDE_DIR_NAMES, not a hardcoded list.

        Gate-off check: if _create_tarball used a hardcoded list, monkeypatching
        the constant would have no effect and ``sentineldir/keep.py`` would still
        appear in the second tarball.
        """
        import tarfile

        from djust.deploy_cli import EXCLUDE_DIR_NAMES, _create_tarball

        src = self._build_source(tmp_path, ["app.py", "sentineldir/keep.py"])

        # First call: real constant — sentineldir is NOT excluded.
        out1 = tmp_path / "out1.tar.gz"
        _create_tarball(src, out1)
        with tarfile.open(out1, "r:gz") as tar:
            names1 = set(tar.getnames())
        assert "app.py" in names1
        assert "sentineldir/keep.py" in names1

        # Monkeypatch the constant to add the sentinel directory.
        monkeypatch.setattr(
            "djust.deploy_cli.EXCLUDE_DIR_NAMES",
            EXCLUDE_DIR_NAMES | {"sentineldir"},
        )

        # Second call: sentineldir is now excluded, app.py still present.
        out2 = tmp_path / "out2.tar.gz"
        _create_tarball(src, out2)
        with tarfile.open(out2, "r:gz") as tar:
            names2 = set(tar.getnames())
        assert "app.py" in names2
        assert "sentineldir/keep.py" not in names2

    def test_create_tarball_excludes_canonical_set(self, tmp_path):
        """The canonical exclude set is applied — locks in the behavior-change
        entries (.hg/.svn/logs/media/staticfiles + working .log exclusion)."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        layout = [
            "keep.py",
            ".hg/store.db",
            ".svn/entries",
            "logs/app.txt",
            "media/upload.bin",
            "staticfiles/main.css",
            "app.log",
            "__pycache__/mod.pyc",
        ]
        src = self._build_source(tmp_path, layout)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "keep.py" in names
        for excluded in (
            ".hg/store.db",
            ".svn/entries",
            "logs/app.txt",
            "media/upload.bin",
            "staticfiles/main.css",
            "app.log",
            "__pycache__/mod.pyc",
        ):
            assert excluded not in names, f"{excluded} should be excluded"

    def test_exclude_groups_have_expected_kinds(self):
        """The five typed exclude groups have the kinds the matcher requires:
        suffix groups must be tuples (str.endswith rejects frozenset); name
        groups are sets for O(1) membership."""
        from djust.deploy_cli import (
            EXCLUDE_DIR_NAMES,
            EXCLUDE_DIR_SUFFIXES,
            EXCLUDE_FILE_SUFFIXES,
            EXCLUDE_FILENAME_STEMS,
            EXCLUDE_FILENAMES,
        )

        # Suffix groups must be tuples so ``str.endswith(group)`` works.
        assert isinstance(EXCLUDE_DIR_SUFFIXES, tuple)
        assert isinstance(EXCLUDE_FILE_SUFFIXES, tuple)
        # Name groups are sets/frozensets for membership tests.
        assert isinstance(EXCLUDE_DIR_NAMES, (set, frozenset))
        assert isinstance(EXCLUDE_FILENAMES, (set, frozenset))
        assert isinstance(EXCLUDE_FILENAME_STEMS, (set, frozenset))
        # All five groups are non-empty.
        assert EXCLUDE_DIR_NAMES
        assert EXCLUDE_DIR_SUFFIXES
        assert EXCLUDE_FILE_SUFFIXES
        assert EXCLUDE_FILENAMES
        assert EXCLUDE_FILENAME_STEMS
        # Total entry count is preserved (25 entries pre-split): splitting
        # .env / db.sqlite3 out of EXCLUDE_FILENAMES into the new
        # EXCLUDE_FILENAME_STEMS group moves 2 entries but keeps the total.
        total = (
            len(EXCLUDE_DIR_NAMES)
            + len(EXCLUDE_DIR_SUFFIXES)
            + len(EXCLUDE_FILE_SUFFIXES)
            + len(EXCLUDE_FILENAMES)
            + len(EXCLUDE_FILENAME_STEMS)
        )
        assert total == 25, total

    def test_create_tarball_does_not_over_exclude_lookalike_names(self, tmp_path):
        """#1505: substring matching wrongly dropped files whose names merely
        *contained* an exclude token (``venv`` in ``venvironment.py``). Anchored
        matching keeps every legitimately-named path."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        layout = [
            "app.py",
            "venvironment.py",  # contains 'venv'
            "distance.py",  # contains 'dist'
            "media_helper.py",  # contains 'media'
            "mybuild_config.py",  # contains 'build'
            "staticfiles_conf.py",  # contains 'staticfiles'
            "logsink.py",  # contains 'logs'
            "media_player/core.py",  # dir 'media_player' contains 'media'
        ]
        src = self._build_source(tmp_path, layout)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        for present in layout:
            assert present in names, f"{present} was wrongly excluded (#1505 over-match)"

    def test_create_tarball_still_excludes_genuine_artifacts(self, tmp_path):
        """All genuine exclusions across the four typed groups still work after
        the anchored-matching fix."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        layout = [
            "keep.py",
            ".git/config",
            ".hg/store.db",
            ".svn/entries",
            ".venv/bin/python",
            "node_modules/pkg/index.js",
            "__pycache__/m.pyc",
            ".pytest_cache/CACHEDIR.TAG",
            ".mypy_cache/3.11/x.data.json",
            ".ruff_cache/content",
            ".idea/workspace.xml",
            ".vscode/settings.json",
            "dist/wheel.whl",
            "build/lib/x.py",
            "logs/app.txt",
            "media/u.bin",
            "staticfiles/m.css",
            "mypkg.egg-info/PKG-INFO",  # dir-name suffix
            "mod.pyc",  # file suffix
            "a.pyo",  # file suffix
            "app.log",  # file suffix
            ".env",  # exact filename
            ".DS_Store",  # exact filename, top-level
            "src/.DS_Store",  # exact filename, nested
            "Thumbs.db",  # exact filename
            "db.sqlite3",  # exact filename
        ]
        src = self._build_source(tmp_path, layout)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "keep.py" in names
        for excluded in layout:
            if excluded == "keep.py":
                continue
            assert excluded not in names, f"{excluded} should be excluded"

    def test_create_tarball_excludes_sensitive_filename_stem_variants(self, tmp_path):
        """#1505 follow-up (SECURITY): ``.env`` and ``db.sqlite3`` are filename
        STEMS, not exact filenames. Their suffixed variants — ``.env.production``,
        ``.env.local``, ``.env.staging``, ``db.sqlite3-wal``, ``db.sqlite3.bak`` —
        carry credentials / live data and MUST be excluded. Anchored exact-only
        matching regressed this; the stem-match rule restores it."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        layout = [
            "keep.py",
            # MUST be excluded — sensitive stem variants.
            ".env.production",
            ".env.local",
            ".env.staging",
            "config/.env.production",  # stem variant in a subdir
            "db.sqlite3-wal",
            "db.sqlite3-journal",
            "db.sqlite3.bak",
            # MUST still be excluded — bare stems (no regression).
            ".env",
            "db.sqlite3",
        ]
        src = self._build_source(tmp_path, layout)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "keep.py" in names
        for excluded in layout:
            if excluded == "keep.py":
                continue
            assert excluded not in names, (
                f"{excluded} is a sensitive stem variant and must be excluded"
            )

    def test_create_tarball_does_not_over_exclude_env_lookalikes(self, tmp_path):
        """#1505 over-match must stay fixed: a bare ``file.startswith('.env')``
        would wrongly drop ``.environment`` (a file whose name merely starts
        with ``.env``). The ``stem + '.'`` / ``stem + '-'`` discriminator keeps
        such lookalikes in the tarball."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        layout = [
            "app.py",
            ".environment",  # starts with '.env' but is NOT a .env variant
            "environment_helper.py",  # normal file, contains 'environment'
        ]
        src = self._build_source(tmp_path, layout)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        for present in layout:
            assert present in names, f"{present} was wrongly excluded — #1505 over-match regressed"

    def test_create_tarball_empty_source(self, tmp_path):
        """An empty source directory produces an empty tarball without crashing."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        src = tmp_path / "src"
        src.mkdir()
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            assert tar.getnames() == []

    def test_create_tarball_file_named_like_excluded_dir(self, tmp_path):
        """A *file* named ``dist`` or ``logs`` at top level is now INCLUDED —
        only directories with those basenames are pruned (#1505)."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        src = self._build_source(tmp_path, ["keep.py", "dist", "logs"])
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "keep.py" in names
        assert "dist" in names
        assert "logs" in names


class TestCreateTarballGitignore:
    """`_create_tarball` honors ``.gitignore`` when the source dir is a git repo.

    The hardcoded EXCLUDE_* list only knows a fixed set of dir/file names, so
    project-specific bloat with non-standard names (a ``.venv-dev`` virtualenv,
    a ``scratch/`` dir, locally-built wheels) sailed straight into the tarball
    even though the user had already gitignored it — producing a 152 MB upload
    that the server's 50 MB ingress cap 413'd. When the source is a git work
    tree, the file set is taken from ``git ls-files --cached --others
    --exclude-standard`` (the working tree minus ignored paths), so the user's
    existing ``.gitignore`` is the source of truth. The EXCLUDE_* rules still
    apply as a security net so credentials / live DBs never ship even if the
    user forgot to ignore them.
    """

    def _git_repo(self, tmp_path, layout, gitignore=""):
        """Create a git repo (no commit needed) with files + a .gitignore.

        ``git ls-files --others --exclude-standard`` reports untracked,
        non-ignored files without any commit, so the repo stays uncommitted.
        """
        import subprocess

        src = tmp_path / "repo"
        src.mkdir()
        if gitignore:
            (src / ".gitignore").write_text(gitignore)
        for rel in layout:
            target = src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
        subprocess.run(["git", "init", "-q"], cwd=src, check=True)
        return src

    def test_gitignored_paths_excluded(self, tmp_path):
        """Paths matched by .gitignore are dropped; app code is kept. This is
        the max-companion case: ``.venv-dev`` and ``scratch`` are gitignored but
        NOT in EXCLUDE_DIR_NAMES, so only .gitignore-respect drops them."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        src = self._git_repo(
            tmp_path,
            layout=[
                "app.py",
                "pkg/mod.py",
                ".venv-dev/lib/python3.12/site-packages/playwright/node",
                "scratch/shot.png",
                "mobile-app/wheels/djust.whl",
            ],
            gitignore=".venv-dev/\nscratch/\nmobile-app/wheels/\n",
        )
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "app.py" in names
        assert "pkg/mod.py" in names
        assert ".venv-dev/lib/python3.12/site-packages/playwright/node" not in names
        assert "scratch/shot.png" not in names
        assert "mobile-app/wheels/djust.whl" not in names

    def test_credentials_excluded_even_when_not_gitignored(self, tmp_path):
        """SECURITY net: even in git mode, the EXCLUDE_* rules still drop a
        ``.env`` variant / live SQLite DB / ``.pyc`` that the user forgot to
        gitignore, so secrets never ship just because git would list them."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        src = self._git_repo(
            tmp_path,
            layout=["app.py", ".env.production", "db.sqlite3", "m.pyc"],
            gitignore="",  # nothing ignored — rely entirely on the security net
        )
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "app.py" in names
        assert ".env.production" not in names
        assert "db.sqlite3" not in names
        assert "m.pyc" not in names

    def test_non_git_source_uses_walker_fallback(self, tmp_path):
        """A directory that is NOT a git repo keeps the os.walk behavior: a
        stray ``.gitignore`` is inert (no git to interpret it), so a listed-but-
        not-EXCLUDE_* path is still included. Guards against the git path
        leaking into non-git deploys (`djust deploy-dir` on a plain folder)."""
        import tarfile

        from djust.deploy_cli import _create_tarball

        src = tmp_path / "plain"
        src.mkdir()
        (src / ".gitignore").write_text("keepme/\n")
        (src / "app.py").write_text("x")
        (src / "keepme").mkdir()
        (src / "keepme" / "data.txt").write_text("x")

        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        with tarfile.open(out, "r:gz") as tar:
            names = set(tar.getnames())

        assert "app.py" in names
        # No .git → .gitignore is not consulted → keepme/ is still shipped.
        assert "keepme/data.txt" in names


class TestTarballSizeWarning:
    """`_tarball_size_warning` flags oversized tarballs before upload so a 413
    becomes an actionable message instead of a raw nginx error page."""

    def _tarball(self, tmp_path, files):
        """Build a real .tar.gz from ``{relpath: byte_length}``."""
        from djust.deploy_cli import _create_tarball

        src = tmp_path / "src"
        src.mkdir()
        for rel, length in files.items():
            target = src / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * length)
        out = tmp_path / "out.tar.gz"
        _create_tarball(src, out)
        return out

    def test_no_warning_under_threshold(self, tmp_path):
        from djust.deploy_cli import _tarball_size_warning

        out = self._tarball(tmp_path, {"app.py": 100})
        assert _tarball_size_warning(out, threshold=10_000_000) is None

    def test_warning_lists_largest_members(self, tmp_path):
        from djust.deploy_cli import _tarball_size_warning

        out = self._tarball(
            tmp_path,
            {"small.py": 10, "big.bin": 4096, "mid.txt": 512},
        )
        msg = _tarball_size_warning(out, threshold=0)
        assert msg is not None
        # Mentions the size, the offending file, and a gitignore hint.
        assert "big.bin" in msg
        assert "MB" in msg
        assert ".gitignore" in msg
        # Largest member is listed before the smaller ones.
        assert msg.index("big.bin") < msg.index("mid.txt")

    def test_warning_uses_module_default_threshold(self, tmp_path):
        """With no explicit threshold the module constant TARBALL_WARN_BYTES is
        used; a tiny tarball is well under it and stays silent."""
        from djust.deploy_cli import TARBALL_WARN_BYTES, _tarball_size_warning

        assert TARBALL_WARN_BYTES >= 10 * 1024 * 1024
        out = self._tarball(tmp_path, {"app.py": 100})
        assert _tarball_size_warning(out) is None


# ---------------------------------------------------------------------------
# #1761 — serving_current → "rolling out" vs "active"
# ---------------------------------------------------------------------------


class TestPollDisplay:
    """`_poll_display` maps a deployment-status poll response to
    (message, done, url). During blue/green the deployment row is
    active/deploying but `serving_current` is False (the old placement still
    serves) — the CLI shows "rolling out" and keeps polling until it flips True.
    `serving_current` is additive (djustlive #517): older servers omit it, so a
    missing field is treated as True (no behavior change)."""

    def test_active_serving_current_true_is_done_with_url(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display(
            {
                "status": "active",
                "serving_current": True,
                "container_url": "https://x.djustlive.com",
            }
        )
        assert msg == "active"
        assert done is True
        assert url == "https://x.djustlive.com"

    def test_active_serving_current_false_is_rolling_out_not_done(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display(
            {
                "status": "active",
                "serving_current": False,
                "container_url": "https://x.djustlive.com",
            }
        )
        assert "rolling out" in msg
        assert done is False
        # No URL surfaced while still serving stale code.
        assert url is None

    def test_missing_serving_current_is_back_compat_active(self):
        """Older servers omit the field → fail-safe True → today's behavior."""
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display(
            {"status": "active", "container_url": "https://x.djustlive.com"}
        )
        assert msg == "active"
        assert done is True
        assert url == "https://x.djustlive.com"

    def test_deploying_serving_current_false_keeps_polling(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display({"status": "deploying", "serving_current": False})
        assert "rolling out" in msg
        assert done is False
        assert url is None

    def test_failed_is_terminal_without_url(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display({"status": "failed"})
        assert msg == "failed"
        assert done is True
        assert url is None

    def test_cancelled_and_superseded_are_terminal(self):
        from djust.deploy_cli import _poll_display

        for st in ("cancelled", "superseded"):
            msg, done, url = _poll_display({"status": st})
            assert msg == st
            assert done is True
            assert url is None

    def test_active_without_container_url_is_done_no_url(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display({"status": "active", "serving_current": True})
        assert msg == "active"
        assert done is True
        assert url is None

    def test_pending_is_not_terminal(self):
        from djust.deploy_cli import _poll_display

        msg, done, url = _poll_display({"status": "pending"})
        assert msg == "pending"
        assert done is False
        assert url is None


# ---------------------------------------------------------------------------
# #1760 — deploy doctor preflight
# ---------------------------------------------------------------------------


class TestDeployDoctor:
    """`_deploy_doctor_warnings(settings_text)` statically inspects a Django
    settings module and WARNS (never errors) when it hardcodes values the
    platform injects via env. Each check has a positive (warns) and negative
    (silent) case."""

    def test_clean_env_driven_settings_produce_no_warnings(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "import os\n"
            "import dj_database_url\n"
            "SECRET_KEY = os.environ['SECRET_KEY']\n"
            "ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',')\n"
            "DATABASES = {'default': dj_database_url.config()}\n"
        )
        assert _deploy_doctor_warnings(text) == []

    def test_hardcoded_secret_key_warns(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = "SECRET_KEY = 'django-insecure-abc123'\n"
        warns = _deploy_doctor_warnings(text)
        assert any("SECRET_KEY" in w for w in warns)

    def test_secret_key_from_env_does_not_warn(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = "SECRET_KEY = os.environ['SECRET_KEY']\n"
        assert not any("SECRET_KEY" in w for w in _deploy_doctor_warnings(text))

    def test_literal_allowed_hosts_warns(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = "ALLOWED_HOSTS = ['example.com', 'www.example.com']\n"
        assert any("ALLOWED_HOSTS" in w for w in _deploy_doctor_warnings(text))

    def test_allowed_hosts_from_env_does_not_warn(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = "ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '').split(',')\n"
        assert not any("ALLOWED_HOSTS" in w for w in _deploy_doctor_warnings(text))

    def test_databases_without_database_url_warns(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.postgresql',\n"
            "        'NAME': 'mydb', 'USER': 'me', 'HOST': 'localhost',\n"
            "    }\n"
            "}\n"
        )
        assert any("DATABASE_URL" in w or "DATABASES" in w for w in _deploy_doctor_warnings(text))

    def test_databases_reading_database_url_does_not_warn(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "import dj_database_url\n"
            "DATABASES = {'default': dj_database_url.config(default=os.environ['DATABASE_URL'])}\n"
        )
        assert not any(
            "DATABASE_URL" in w or "DATABASES" in w for w in _deploy_doctor_warnings(text)
        )

    def test_databases_from_individual_env_vars_does_not_warn(self):
        """#1768: a DATABASES dict built from individual os.environ['DB_*'] vars
        (not DATABASE_URL) IS environment-derived and must NOT warn."""
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "import os\n"
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.postgresql',\n"
            "        'NAME': os.environ['DB_NAME'],\n"
            "        'USER': os.environ['DB_USER'],\n"
            "        'HOST': os.environ.get('DB_HOST', 'localhost'),\n"
            "    }\n"
            "}\n"
        )
        assert not any(
            "DATABASE_URL" in w or "DATABASES" in w for w in _deploy_doctor_warnings(text)
        )

    def test_databases_hardcoded_still_warns_despite_env_secret_key(self):
        """#1768 region-scoping guard: a SECRET_KEY read from env elsewhere must
        NOT mask a genuinely hardcoded DATABASES block — the env-read check is
        scoped to the DATABASES region."""
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "import os\n"
            "SECRET_KEY = os.environ['SECRET_KEY']\n"
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.postgresql',\n"
            "        'NAME': 'mydb', 'USER': 'me', 'HOST': 'localhost',\n"
            "    }\n"
            "}\n"
        )
        assert any("DATABASES" in w for w in _deploy_doctor_warnings(text))

    def test_sqlite_under_project_dir_warns(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = (
            "DATABASES = {\n"
            "    'default': {\n"
            "        'ENGINE': 'django.db.backends.sqlite3',\n"
            "        'NAME': BASE_DIR / 'db.sqlite3',\n"
            "    }\n"
            "}\n"
        )
        warns = _deploy_doctor_warnings(text)
        assert any("sqlite" in w.lower() or "read-only" in w.lower() for w in warns)

    def test_warnings_carry_env_pointer(self):
        from djust.deploy_cli import _deploy_doctor_warnings

        text = "SECRET_KEY = 'literal'\n"
        warns = _deploy_doctor_warnings(text)
        assert warns
        # Every warning points the user at the env-injection contract.
        assert all("env" in w.lower() for w in warns)

    def test_find_settings_files_resolves_manage_py_default(self, tmp_path):
        from djust.deploy_cli import _find_settings_files

        (tmp_path / "manage.py").write_text(
            "import os\nos.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproj.settings')\n"
        )
        pkg = tmp_path / "myproj"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        settings = pkg / "settings.py"
        settings.write_text("SECRET_KEY = 'x'\n")
        found = _find_settings_files(tmp_path)
        assert settings in found

    def test_find_settings_files_glob_fallback(self, tmp_path):
        from djust.deploy_cli import _find_settings_files

        pkg = tmp_path / "app"
        pkg.mkdir()
        settings = pkg / "settings.py"
        settings.write_text("SECRET_KEY = 'x'\n")
        found = _find_settings_files(tmp_path)
        assert settings in found

    def test_find_settings_files_missing_returns_empty(self, tmp_path):
        from djust.deploy_cli import _find_settings_files

        assert _find_settings_files(tmp_path) == []

    def test_run_deploy_doctor_prints_warnings_to_stderr(self, tmp_path, capsys):
        from djust.deploy_cli import _run_deploy_doctor

        (tmp_path / "manage.py").write_text(
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'p.settings')\n"
        )
        pkg = tmp_path / "p"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "settings.py").write_text("SECRET_KEY = 'django-insecure-literal'\n")
        _run_deploy_doctor(tmp_path)
        err = capsys.readouterr().err
        assert "SECRET_KEY" in err

    def test_run_deploy_doctor_silent_when_no_settings(self, tmp_path, capsys):
        from djust.deploy_cli import _run_deploy_doctor

        _run_deploy_doctor(tmp_path)
        assert capsys.readouterr().err == ""
