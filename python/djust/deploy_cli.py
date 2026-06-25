"""
djust deploy CLI — deployment commands for djustlive.com.

Entry point: djust-deploy
"""

import base64
import hashlib
import http.server
import json
import logging
import os
import re
import secrets
import subprocess
import tarfile
import threading
import time
import urllib.parse
import webbrowser
from pathlib import Path
from typing import Any, Optional, cast

import click
import requests

logger = logging.getLogger(__name__)

DEFAULT_SERVER = "https://djustlive.com"
_CREDS_DIR_NAME = ".djustlive"
_CREDS_FILE_NAME = "credentials"

# Public OAuth client registered on djustlive (accounts migration 0006).
# Hardcoded because the CLI is a public client per RFC 8252 — there's no
# secret to leak. PKCE provides the security here, not a confidential
# client_id. If you fork djustlive, register your own Application row
# and override this via DJUST_CLI_CLIENT_ID.
DJUST_CLI_CLIENT_ID = os.environ.get("DJUST_CLI_CLIENT_ID", "djust-cli")

# How long to wait for the user to complete the browser flow before
# giving up. Generous because users may need to sign up first, hunt
# through password managers, or do MFA. Tests override via env var so
# a stuck test fails fast instead of wedging the suite for 5 minutes.
_OAUTH_BROWSER_TIMEOUT_SECONDS = int(os.environ.get("DJUST_CLI_OAUTH_TIMEOUT", "300"))

# Default patterns to exclude from tarball, classified into five kinds so the
# matcher in ``_create_tarball`` can apply each correctly. Path-segment /
# basename anchored — NOT substring-matched (a ``venv`` token must not exclude
# ``venvironment.py``; see #1505).

# Excluded if a directory's basename EXACTLY equals one of these. os.walk
# pruning then drops the directory and everything beneath it.
EXCLUDE_DIR_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
        "logs",
        "media",
        "staticfiles",
    }
)

# Excluded if a directory's basename ENDS WITH one of these (e.g. the
# package-metadata dir ``mypkg.egg-info/``, whose name varies by package).
# Declared as a tuple so ``str.endswith`` accepts it directly.
EXCLUDE_DIR_SUFFIXES = (".egg-info",)

# Excluded if a file's name ENDS WITH one of these. Tuple for ``str.endswith``.
EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".log")

# Excluded if a file's name EXACTLY equals one of these. These have no
# legitimate suffixed variants, so exact matching is correct.
EXCLUDE_FILENAMES = frozenset({".DS_Store", "Thumbs.db"})

# Excluded if a file's name equals one of these stems OR is a suffixed
# variant of one (``stem + "."`` or ``stem + "-"``). ``.env`` and
# ``db.sqlite3`` are filename STEMS, not exact filenames: dotenv conventions
# produce ``.env.production`` / ``.env.local`` / ``.env-backup`` and SQLite
# produces ``db.sqlite3-wal`` / ``db.sqlite3-journal`` / ``db.sqlite3.bak``,
# all of which carry credentials or live data and MUST be excluded. The
# ``.`` / ``-`` discriminator prevents the #1505 over-match: a file named
# ``.environment`` is NOT a stem variant of ``.env`` and stays included.
EXCLUDE_FILENAME_STEMS = frozenset({".env", "db.sqlite3"})

# Above this packed size, ``deploy_dir`` warns before uploading and points at
# the largest included files. A clean djust app tarball is a few MB; anything
# this large is almost always build artifacts or local data that belongs in
# .gitignore — and the djustlive ingress 413s uploads past its body-size cap,
# so surfacing it here turns a raw nginx error into an actionable message.
TARBALL_WARN_BYTES = 50 * 1024 * 1024


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def credentials_path() -> Path:
    """Return the path to the credentials file (~/.djustlive/credentials)."""
    return Path.home() / _CREDS_DIR_NAME / _CREDS_FILE_NAME


def _write_credentials_file(data: dict) -> None:
    """Atomic 0o600 write helper. The file holds the OAuth refresh token
    and is therefore high-value — chmod the directory to 0o700 too."""
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))


def save_credentials(token: str, email: str, server_url: str) -> None:
    """Legacy DRF-token credential write (email + password login flow).

    Kept for backwards compatibility — existing test suites and any
    automation still calling ``/api/v1/auth/login/`` use this shape.
    The browser-login flow uses :func:`save_oauth_credentials` instead.
    """
    _write_credentials_file(
        {
            "token": token,
            "email": email,
            "server_url": server_url,
        }
    )


def save_oauth_credentials(
    *,
    access_token: str,
    refresh_token: str,
    expires_at: int,
    email: str,
    server_url: str,
) -> None:
    """Write OAuth credentials produced by the browser-login flow.

    ``expires_at`` is a unix timestamp (server-relative; we set it from
    ``time.time() + expires_in`` at token-receipt time). The refresh
    token survives ~30 days and is what gates "is this user still
    logged in" — once it expires, the next deploy re-launches the
    browser flow.
    """
    _write_credentials_file(
        {
            "auth_scheme": "bearer",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
            "email": email,
            "server_url": server_url,
        }
    )


def load_credentials() -> dict:
    """Load credentials from disk, raising ClickException if absent."""
    path = credentials_path()
    if not path.exists():
        raise click.ClickException("Not logged in. Run `djust deploy login` first.")
    return cast(dict, json.loads(path.read_text()))


def _api_headers(creds: dict) -> dict:
    """Build Authorization header from a credentials dict.

    Two formats accepted, kept compatible during the email/password →
    browser-login transition:

      - ``{"auth_scheme": "bearer", "access_token": ...}`` — new flow,
        sends ``Authorization: Bearer ...``. Authenticated server-side
        by ``oauth2_provider.contrib.rest_framework.OAuth2Authentication``.
      - ``{"token": ...}`` — legacy DRF token. Sent as
        ``Authorization: Token ...``. Recognized by DRF's
        ``TokenAuthentication``. New logins never produce this shape;
        the helper just doesn't break for users with stored creds from
        an older djust release.

    Single-arg ``token`` strings are accepted too for callers that
    haven't been migrated to pass the dict — wrap them as legacy DRF.
    """
    if isinstance(creds, str):
        creds = {"token": creds}

    if creds.get("auth_scheme") == "bearer":
        scheme = "Bearer"
        token = creds["access_token"]
    else:
        scheme = "Token"
        token = creds["token"]

    return {
        "Authorization": f"{scheme} {token}",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Project-slug resolution
# ---------------------------------------------------------------------------


def _pyproject_path(source_dir: Path) -> Path:
    return Path(source_dir) / "pyproject.toml"


def _read_slug_from_pyproject(source_dir: Path) -> Optional[str]:
    """Read ``[tool.djust.deploy].project`` from pyproject.toml.

    Returns None if the file or key is absent. Returns None on a
    malformed file rather than raising — the prompt path will then ask
    the user explicitly. Uses Python 3.11+ stdlib ``tomllib``.
    """
    path = _pyproject_path(source_dir)
    if not path.exists():
        return None
    try:
        import tomllib

        data = tomllib.loads(path.read_text())
    except Exception:
        return None
    slug = data.get("tool", {}).get("djust", {}).get("deploy", {}).get("project")
    if isinstance(slug, str) and slug.strip():
        return slug.strip()
    return None


def _save_slug_to_pyproject(source_dir: Path, slug: str) -> bool:
    """Idempotently set ``[tool.djust.deploy] project = "<slug>"`` in pyproject.toml.

    Returns True if the write succeeded, False if pyproject.toml is
    absent (most common reason — the project doesn't use it).

    Three cases:

      1. ``[tool.djust.deploy]`` exists with a ``project = …`` line —
         rewrite that line in place.
      2. ``[tool.djust.deploy]`` exists without a ``project`` key —
         insert one immediately under the table header.
      3. Neither — append a fresh block at the end.

    Idempotent across both server slug-uniquification (``my-app`` →
    ``my-app-2``) and repeated runs. Stays stdlib-only (no tomli_w
    dependency) — TOML rejects duplicate tables, so the prior
    append-only shape produced unparseable files on case 1.

    Implementation walks the file line-by-line (no regex with
    nested quantifiers) — a single backtrackable pattern across the
    whole file would be a ReDoS surface on hostile pyproject input.
    """
    path = _pyproject_path(source_dir)
    if not path.exists():
        return False
    try:
        original = path.read_text(encoding="utf-8")
    except OSError:
        return False

    new_line = f'project = "{slug}"\n'
    lines = original.splitlines(keepends=True)

    # Find the [tool.djust.deploy] header, if any. A TOML table lasts
    # from its header line until the next `[…]` header line (or EOF).
    table_start = -1
    table_end = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if table_start == -1:
            if stripped == "[tool.djust.deploy]":
                table_start = i
            continue
        # Already inside the table — bail at the next table header.
        if stripped.startswith("[") and stripped.endswith("]"):
            table_end = i
            break

    if table_start == -1:
        # Case 3: no table at all — append a fresh block.
        prefix = "" if not original or original.endswith("\n") else "\n"
        updated = original + (
            f"{prefix}\n# Auto-saved by `djust deploy` — the project slug to deploy to.\n"
            "[tool.djust.deploy]\n"
            f"{new_line}"
        )
    else:
        # Look for an existing `project = ...` line within the table.
        project_idx = -1
        for j in range(table_start + 1, table_end):
            stripped = lines[j].lstrip()
            if stripped.startswith("project") and "=" in stripped:
                # Match `project` only, not `project_x`.
                key_part = stripped.split("=", 1)[0].rstrip()
                if key_part == "project":
                    project_idx = j
                    break

        if project_idx >= 0:
            # Case 1: replace in place.
            lines[project_idx] = new_line
        else:
            # Case 2: insert right under the table header.
            lines.insert(table_start + 1, new_line)
        updated = "".join(lines)

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError:
        return False
    return True


def _resolve_project_slug(
    arg: Optional[str],
    source_dir: Path,
    *,
    interactive: bool = True,
) -> str:
    """Resolve the project slug for a deploy command.

    Order:

      1. The explicit CLI argument, if provided.
      2. ``[tool.djust.deploy].project`` in ``<source_dir>/pyproject.toml``.
      3. Interactive prompt — and offer to save the answer back so future
         runs find it in pyproject.toml.

    Raises ``click.ClickException`` if ``interactive=False`` and (1) and
    (2) both miss.
    """
    if arg:
        return arg
    saved = _read_slug_from_pyproject(source_dir)
    if saved:
        click.echo(f"Using project slug from pyproject.toml: {saved}")
        return saved
    if not interactive:
        raise click.ClickException(
            "No project slug provided and none found in pyproject.toml "
            "[tool.djust.deploy].project. Pass it as a positional argument."
        )
    slug: str = click.prompt(
        "Project slug to deploy to (will be saved to pyproject.toml)",
        type=str,
    ).strip()
    if not slug:
        raise click.ClickException("Empty slug — aborting.")
    if click.confirm(
        f"Save '{slug}' to {_pyproject_path(source_dir)} so you don't get asked next time?",
        default=True,
    ):
        if _save_slug_to_pyproject(source_dir, slug):
            click.echo(f"Saved [tool.djust.deploy].project = '{slug}'.")
        else:
            click.echo(
                "Couldn't save (no pyproject.toml in source directory). "
                "Pass the slug explicitly or create pyproject.toml first.",
                err=True,
            )
    return slug


# ---------------------------------------------------------------------------
# Guided onboarding preconditions
# ---------------------------------------------------------------------------


def _ensure_logged_in(server: str, *, interactive: bool = True) -> dict:
    """Return live credentials, prompting for login if there are none.

    Validates the saved token by calling ``GET /api/v1/me/``. If that
    returns 401 (token revoked / user deleted), the local credentials
    are stale — drop them and re-prompt.

    Behaviour matrix:

      | saved token | /me/ result | action                      |
      |-------------|-------------|-----------------------------|
      | none        | (n/a)       | prompt for login            |
      | yes         | 200         | use as-is                   |
      | yes         | 401         | drop, prompt for login      |
      | yes         | other       | use as-is + warn (don't    |
      |             |             | block on transient errors)  |

    Raises ``ClickException`` if ``interactive=False`` and saved
    credentials are missing or stale.
    """
    try:
        creds = load_credentials()
    except click.ClickException:
        if not interactive:
            raise
        click.echo("Not logged in to djustlive — let's fix that.")
        return _login_browser(server)

    # Validate against /me/. A 401 means the token's no good anymore.
    try:
        resp = requests.get(
            f"{server}/api/v1/me/",
            headers=_api_headers(creds),
            timeout=10,
        )
    except requests.RequestException as exc:
        # Don't block the deploy on a transient network blip — fall
        # through with the saved creds and let the deploy itself fail
        # if the network is genuinely broken.
        logger.debug("Could not reach /me/: %s — proceeding with saved creds", exc)
        return creds

    if resp.status_code == 200:
        return creds

    if resp.status_code == 401:
        # OAuth path: try refresh_token before forcing a fresh browser
        # login. Access tokens expire after 1h; refresh tokens after
        # 30d. The whole point is the user shouldn't notice.
        if creds.get("auth_scheme") == "bearer":
            refreshed = _refresh_oauth_token(server, creds.get("refresh_token", ""))
            if refreshed is not None:
                save_oauth_credentials(
                    access_token=refreshed["access_token"],
                    refresh_token=refreshed["refresh_token"],
                    expires_at=refreshed["expires_at"],
                    email=creds.get("email", ""),
                    server_url=server,
                )
                creds.update(refreshed)
                return creds

        if not interactive:
            raise click.ClickException(
                "Saved credentials are no longer valid (server returned 401). "
                "Run `djust deploy login` to re-authenticate."
            )
        click.echo("Saved credentials are no longer valid — please re-authenticate.")
        path = credentials_path()
        if path.exists():
            path.unlink()
        return _login_browser(server)

    # 5xx or other unexpected — log and proceed.
    logger.debug("/me/ returned %s; proceeding with saved creds", resp.status_code)
    return creds


def _ensure_project_exists(
    server: str,
    creds: dict,
    project_slug: str,
    *,
    source_dir: Path,
    no_create: bool = False,
    yes: bool = False,
) -> str:
    """Confirm ``project_slug`` exists on the server; offer to create otherwise.

    Returns the slug that the deploy should use — usually the input
    ``project_slug``, but a freshly-created project may come back with
    a uniquified slug (e.g. ``my-app-1`` if ``my-app`` was taken).

    Decisions:

      | server says       | --no-create | --yes  | result                |
      |-------------------|-------------|--------|-----------------------|
      | 200 (project ok)  | (n/a)       | (n/a)  | use slug as-is        |
      | 404               | True        | (n/a)  | raise (fail-fast)     |
      | 404               | False       | True   | create, no prompt     |
      | 404               | False       | False  | confirm, then create  |
      | other (5xx/etc)   | (n/a)       | (n/a)  | use slug + warn       |
    """
    try:
        resp = requests.get(
            f"{server}/api/v1/projects/{project_slug}/",
            headers=_api_headers(creds),
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.debug("project precondition GET failed: %s — proceeding", exc)
        return project_slug

    if resp.status_code == 200:
        return project_slug

    if resp.status_code != 404:
        # 5xx, 401 we already handled, etc. Don't second-guess; the
        # actual deploy will surface the real error if there's one.
        logger.debug(
            "project precondition GET returned %s; proceeding with slug %r",
            resp.status_code,
            project_slug,
        )
        return project_slug

    # 404 — project doesn't exist for this user. Decide what to do.
    if no_create:
        raise click.ClickException(
            f"Project '{project_slug}' not found on {server} and "
            "--no-create was passed. Create the project first or drop "
            "--no-create to be prompted."
        )

    if not yes and not click.confirm(
        f"Project '{project_slug}' doesn't exist on djustlive yet. Create it now?",
        default=True,
    ):
        raise click.ClickException("Aborted — project not created.")

    click.echo(f"Creating project '{project_slug}'...")
    try:
        resp = requests.post(
            f"{server}/api/v1/projects/",
            headers=_api_headers(creds),
            json={"name": project_slug},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Could not create project: {exc}") from exc

    if resp.status_code != 201:
        try:
            body = resp.json() if resp.content else {}
        except (ValueError, json.JSONDecodeError):
            body = {}
        detail = body.get("detail") or resp.text
        raise click.ClickException(f"Project creation failed ({resp.status_code}): {detail}")

    body = resp.json()
    actual_slug = body.get("slug") or project_slug
    click.echo(f"Project created: {actual_slug} (owner: {body.get('owner_email')})")

    # If the server-assigned slug differs (uniqueness collision), offer
    # to update the local pyproject.toml to match.
    if actual_slug != project_slug and (
        yes
        or click.confirm(
            f"Server assigned slug '{actual_slug}' (yours collided). "
            f"Update pyproject.toml to match?",
            default=True,
        )
    ):
        if _save_slug_to_pyproject(source_dir, actual_slug):
            click.echo(f"Updated pyproject.toml to use '{actual_slug}'.")

    return actual_slug


# ---------------------------------------------------------------------------
# Git check
# ---------------------------------------------------------------------------


def _check_git_clean() -> None:
    """Raise ClickException if the git working tree is dirty."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:
        raise click.ClickException("git not found. Make sure git is on your PATH.") from exc
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(f"git status failed: {exc.stderr.strip()}") from exc

    if result.stdout.strip():
        raise click.ClickException(
            "Working tree is not clean. Commit or stash changes before deploying."
        )


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--server",
    default=None,
    envvar="DJUST_SERVER",
    help="djustlive server URL (default: https://djustlive.com).",
    metavar="URL",
)
@click.pass_context
def cli(ctx: click.Context, server: Optional[str]) -> None:
    """djust deploy — manage deployments on djustlive.com."""
    ctx.ensure_object(dict)
    server_url = (server or DEFAULT_SERVER).rstrip("/")
    _require_secure_server(server_url)
    ctx.obj["server"] = server_url


def _require_secure_server(url: str) -> None:
    """Refuse cleartext server URLs except for loopback dev hosts.

    Tokens, refresh_tokens, and the PKCE code_verifier all flow over
    this URL — http:// would put them in cleartext on the wire. The
    one exception is local development against 127.0.0.1 / localhost,
    where there's no network path to MITM.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        return
    raise click.ClickException(
        f"--server / DJUST_SERVER must use https:// (got {url!r}). "
        "Use http:// only for 127.0.0.1 / localhost dev servers."
    )


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# OAuth 2.0 browser-login (Auth Code + PKCE + loopback redirect)
# ---------------------------------------------------------------------------
#
# Overall shape (RFC 6749 §4.1 + RFC 7636 + RFC 8252):
#
#   1. CLI binds an ephemeral port on 127.0.0.1 — RFC 8252 says the
#      authorization server MUST honor the loopback redirect on any
#      port, so we don't have to fight for a fixed one.
#   2. CLI generates a PKCE code_verifier (random ≥43 chars) and the
#      derived code_challenge = base64url(sha256(verifier)). The
#      challenge is sent to /o/authorize/; the verifier is held by the
#      CLI and presented to /o/token/. A man-in-the-middle on the
#      browser-redirect leg can't redeem the code without the verifier.
#   3. CLI also generates a `state` nonce (CSRF defense per RFC 6749
#      §10.12) — the callback handler refuses any redirect whose state
#      doesn't match.
#   4. Browser opens to /o/authorize/?response_type=code&...
#      djustlive's IdP authenticates the user and redirects to the
#      loopback URL with `?code=...&state=...`.
#   5. CLI exchanges the code at /o/token/ for an access + refresh
#      token, decodes the id_token to learn the user's email, and
#      writes the credentials.
#
# Why not device flow (RFC 8628)? Auth-code+loopback is the modern
# norm for desktop CLIs (gh, fly, heroku, vercel) and it's the path
# most users will encounter. A `--device` fallback for headless/SSH
# sessions is tracked as a follow-up.


def _pkce_pair() -> tuple[str, str]:
    """Return ``(code_verifier, code_challenge)`` for an S256 PKCE flow.

    RFC 7636 §4.1 requires the verifier to be 43-128 unreserved chars.
    ``secrets.token_urlsafe(64)`` produces 86 base64url-encoded chars,
    well inside the spec.
    """
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _decode_id_token_email(id_token: str) -> Optional[str]:
    """Pull the ``email`` claim out of an unverified JWT id_token.

    We **deliberately** don't verify the signature here. The id_token
    arrived over TLS from a token endpoint we just authenticated to
    via PKCE — the email is for display only ("Logged in as X.") and
    isn't a security boundary. Anything that needs trustworthy identity
    calls ``GET /api/v1/me/`` server-side.
    """
    try:
        _header, payload_b64, _sig = id_token.split(".")
        # base64url-decode with padding fixup.
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return cast(Optional[str], payload.get("email"))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.debug("Could not decode id_token email claim: %s", exc)
        return None


class _OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    """Single-shot HTTP handler that captures the OAuth code + state.

    Stashes results on the *server* instance (``server.captured``) so
    the launcher can read them out after the request thread completes.
    Suppresses the default ``BaseHTTPRequestHandler`` access logging —
    the user already sees CLI output and a stray "GET /callback HTTP/1.1"
    line in the middle of a deploy is noise.
    """

    server_version = "djust-cli-oauth/1.0"

    def log_message(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: D401
        return

    def do_GET(self) -> None:  # noqa: N802 — http.server convention
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = dict(urllib.parse.parse_qsl(parsed.query))
        self.server.captured = params  # type: ignore[attr-defined]

        if "error" in params:
            body = b"<h1>Login failed</h1><p>You can close this tab and check your terminal.</p>"
            status_code = 400
        else:
            body = (
                b"<h1>You're logged in.</h1>"
                b"<p>You can close this tab and return to your terminal.</p>"
            )
            status_code = 200

        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # Defense-in-depth for the auth code: keep ?code=… out of any
        # downstream Referer header or browser/proxy cache. RFC 8252 §8.10.
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def _login_browser(server: str) -> dict:
    """Authenticate via browser and store OAuth credentials.

    Returns the saved credentials dict (matching :func:`load_credentials`'s
    return shape, with ``auth_scheme="bearer"``).

    Failure modes surfaced as ClickException:

      - User declined the consent screen (``error=access_denied``)
      - state mismatch (CSRF: aborted-and-retried mid-flow, or attack)
      - token endpoint refused the code (network blip, code reuse)
      - browser timeout (``_OAUTH_BROWSER_TIMEOUT_SECONDS``)
    """
    code_verifier, code_challenge = _pkce_pair()
    state = secrets.token_urlsafe(32)

    # Let HTTPServer bind on a kernel-assigned port so we know which
    # port to advertise as redirect_uri. RFC 8252 §7.3 says the
    # authorization server must allow any loopback port — DOT 3.x
    # honors this when the registered URI uses 127.0.0.1.
    http_server = http.server.HTTPServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    http_server.captured = None  # type: ignore[attr-defined]
    port = http_server.server_port

    redirect_uri = f"http://127.0.0.1:{port}/callback"
    auth_url = f"{server}/o/authorize/?" + urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": DJUST_CLI_CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid email profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )

    click.echo("Opening browser to sign in to djustlive…")
    click.echo(f"  If it doesn't open, paste this into a browser:\n  {auth_url}")
    try:
        webbrowser.open(auth_url, new=2)
    except webbrowser.Error:
        # Headless box — user has the URL above; fall through.
        pass

    thread = threading.Thread(target=http_server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout=_OAUTH_BROWSER_TIMEOUT_SECONDS)
    timed_out = thread.is_alive()

    if timed_out:
        # Unblock the accept() loop so the thread exits cleanly. The
        # self-request returns a recognizable error param, so the
        # handler doesn't try to treat it as a real callback.
        try:
            requests.get(f"http://127.0.0.1:{port}/callback?error=timeout", timeout=1)
        except requests.RequestException:
            # Timeout-unblock self-request is best-effort. If it fails,
            # the server thread will exit when its accept() socket is
            # closed below — we just lose the clean-shutdown nicety.
            pass
        thread.join(timeout=2)

    try:
        http_server.server_close()
    except OSError:
        # Server already cleaned up by handle_request() returning. Not
        # an error we need to surface.
        pass

    if timed_out:
        raise click.ClickException(
            f"Timed out waiting for browser login (after {_OAUTH_BROWSER_TIMEOUT_SECONDS}s)."
        )

    params = http_server.captured  # type: ignore[attr-defined]
    if not params:
        raise click.ClickException("Browser login did not complete.")

    if "error" in params:
        raise click.ClickException(
            f"Browser login failed: {params.get('error_description') or params['error']}"
        )

    received_state = params.get("state") or ""
    if not secrets.compare_digest(received_state, state):
        # Either the user has multiple in-flight `djust deploy login`
        # tabs open or someone tried to ride the redirect — refuse it
        # either way. Constant-time compare to keep the state value
        # opaque under any future timing-side-channel surface.
        raise click.ClickException("OAuth state mismatch — login aborted.")

    code = params.get("code")
    if not code:
        raise click.ClickException("OAuth callback missing authorization code.")

    # Step 5: exchange the code for an access + refresh + id_token.
    try:
        resp = requests.post(
            f"{server}/o/token/",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": DJUST_CLI_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Token exchange request failed: {exc}") from exc

    if resp.status_code != 200:
        raise click.ClickException(f"Token exchange failed ({resp.status_code}): {resp.text}")

    payload = resp.json()
    access_token = payload.get("access_token")
    refresh_token = payload.get("refresh_token", "")
    expires_in = int(payload.get("expires_in", 3600))
    id_token = payload.get("id_token", "")

    if not access_token:
        raise click.ClickException("Token endpoint did not return an access_token.")

    email = _decode_id_token_email(id_token) or ""
    if not email:
        # Fall back to userinfo when the IdP didn't include id_token
        # (older deployments without DJUST_OIDC_RSA_KEY set).
        try:
            ui = requests.get(
                f"{server}/o/userinfo/",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10,
            )
            if ui.status_code == 200:
                email = ui.json().get("email", "") or ""
        except requests.RequestException:
            # userinfo is a display-only nicety. If the IdP doesn't
            # expose /o/userinfo/ or the call blips, the deploy still
            # works — we just don't echo "Logged in as <email>".
            pass

    expires_at = int(time.time()) + expires_in
    save_oauth_credentials(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        email=email,
        server_url=server,
    )

    click.echo(f"Logged in as {email or '<unknown>'}.")
    return {
        "auth_scheme": "bearer",
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "email": email,
        "server_url": server,
    }


def _refresh_oauth_token(server: str, refresh_token: str) -> Optional[dict]:
    """Try to refresh an expired access token. Returns new creds dict
    on success, ``None`` if the refresh token itself is no good (the
    caller must then prompt for a fresh browser login).
    """
    if not refresh_token:
        return None

    try:
        resp = requests.post(
            f"{server}/o/token/",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": DJUST_CLI_CLIENT_ID,
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.debug("refresh_token request failed: %s — skipping refresh", exc)
        return None

    if resp.status_code != 200:
        # Don't log resp.text — error bodies from /o/token/ can echo
        # back the rejected refresh_token in some IdP configs, and a
        # 5xx body could carry sensitive context. Status alone is enough
        # for "should I prompt for a fresh browser login?"
        logger.debug("refresh_token rejected (status=%s)", resp.status_code)
        return None

    payload = resp.json()
    access_token = payload.get("access_token")
    if not access_token:
        return None

    # Refresh-token rotation: the IdP issues a new refresh_token on
    # each refresh by default in DOT. Persist whichever one came back.
    new_refresh = payload.get("refresh_token", refresh_token)
    expires_in = int(payload.get("expires_in", 3600))
    expires_at = int(time.time()) + expires_in
    return {
        "access_token": access_token,
        "refresh_token": new_refresh,
        "expires_at": expires_at,
    }


# Public alias: anything that imports `_login_interactive` from older
# djust releases still resolves. Keep until the next major release; new
# code should call `_login_browser` directly.
_login_interactive = _login_browser
__all__ = [
    "cli",
    "credentials_path",
    "load_credentials",
    "save_credentials",
    "save_oauth_credentials",
    "_login_interactive",
]


@cli.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Log in to djustlive.com via your browser."""
    _login_browser(ctx.obj["server"])


# ---------------------------------------------------------------------------
# logout
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def logout(ctx: click.Context) -> None:
    """Log out and remove stored credentials."""
    try:
        creds = load_credentials()
    except click.ClickException:
        click.echo("Not logged in.")
        return

    server = creds.get("server_url", ctx.obj["server"])

    try:
        requests.delete(
            f"{server}/api/v1/auth/logout/",
            headers=_api_headers(creds),
            timeout=30,
        )
    except requests.RequestException as exc:
        logger.debug("Logout API call failed (proceeding to remove local credentials): %s", exc)

    path = credentials_path()
    if path.exists():
        path.unlink()

    click.echo("Logged out successfully.")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("project", required=False, default=None)
@click.pass_context
def status(ctx: click.Context, project: Optional[str]) -> None:
    """Show current deployment status. Optionally filter by PROJECT slug."""
    creds = load_credentials()
    server = ctx.obj["server"]

    params = {}
    if project:
        params["project"] = project

    try:
        resp = requests.get(
            f"{server}/api/v1/deployments/status/",
            headers=_api_headers(creds),
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Request failed: {exc}") from exc

    if resp.status_code != 200:
        raise click.ClickException(f"API error {resp.status_code}: {resp.text}")

    data = resp.json()
    click.echo(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Tarball helpers
# ---------------------------------------------------------------------------


def _is_excluded_dirname(name: str) -> bool:
    """A directory basename is excluded iff it exactly equals an
    EXCLUDE_DIR_NAMES entry or ends with an EXCLUDE_DIR_SUFFIXES entry."""
    return name in EXCLUDE_DIR_NAMES or name.endswith(EXCLUDE_DIR_SUFFIXES)


def _is_excluded_filename(name: str) -> bool:
    """A file basename is excluded iff it equals an EXCLUDE_FILENAMES entry,
    ends with an EXCLUDE_FILE_SUFFIXES entry, or is a stem-variant of an
    EXCLUDE_FILENAME_STEMS entry (``stem``, ``stem + "."*`` or ``stem + "-"*``
    — e.g. ``.env.production``, ``db.sqlite3-wal``). The ``.`` / ``-``
    discriminator keeps ``.environment`` in (not a ``.env`` variant; #1505)."""
    if name in EXCLUDE_FILENAMES or name.endswith(EXCLUDE_FILE_SUFFIXES):
        return True
    return any(
        name == stem or name.startswith(stem + ".") or name.startswith(stem + "-")
        for stem in EXCLUDE_FILENAME_STEMS
    )


def _is_excluded_relpath(relative: str) -> bool:
    """Apply the EXCLUDE_* rules to a POSIX relative path (as emitted by
    ``git ls-files``): excluded iff any directory segment is an excluded
    dirname OR the final segment is an excluded filename. This is the security
    net that keeps credentials / live DBs / bytecode out of the tarball even
    when the user has not gitignored them."""
    segments = relative.split("/")
    *dir_parts, base = segments
    if any(_is_excluded_dirname(d) for d in dir_parts):
        return True
    return _is_excluded_filename(base)


def _git_tracked_files(source_dir: Path) -> Optional[list]:
    """Return the working-tree files of ``source_dir`` minus gitignored paths,
    as POSIX relative paths, or ``None`` if ``source_dir`` is not a usable git
    work tree.

    ``git ls-files --cached --others --exclude-standard`` is exactly "tracked
    files ∪ untracked files, minus everything matched by .gitignore / global
    excludes" — i.e. the working tree as the user sees it, minus the junk they
    already told git to ignore. No commit is required (``--others`` reports
    untracked files directly), so a freshly-``git init``'d project works too.

    The presence of ``.git`` (a directory in a normal clone, a file in a
    worktree) gates this: a plain non-git directory returns ``None`` so the
    caller falls back to the os.walk path. Any git failure (git absent, bad
    repo) also falls back rather than aborting the deploy.
    """
    if not (source_dir / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=str(source_dir),
            capture_output=True,
            check=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return [
        chunk.decode("utf-8", "surrogateescape") for chunk in result.stdout.split(b"\0") if chunk
    ]


def _create_tarball(source_dir: Path, output_path: Path) -> None:
    """
    Create a tarball of source_dir excluding unwanted patterns.

    When ``source_dir`` is a git work tree, the file set comes from
    ``git ls-files`` (see :func:`_git_tracked_files`), so the user's existing
    ``.gitignore`` is the source of truth — a ``.venv-dev`` virtualenv, a
    ``scratch/`` build dir, or locally-built wheels they already ignore stay
    out of the tarball even though their names are not in EXCLUDE_DIR_NAMES.
    Otherwise (a non-git directory, or git unavailable) it falls back to an
    ``os.walk`` of the tree.

    In BOTH paths the EXCLUDE_* rules are applied as a security net via
    :func:`_is_excluded_dirname` / :func:`_is_excluded_filename`: exclusion is
    path-segment / basename anchored (NOT substring-matched), so credential /
    live-database files such as ``.env.production`` and ``db.sqlite3-wal`` are
    dropped even if the user forgot to gitignore them, while lookalikes such as
    ``venvironment.py`` and ``.environment`` are kept (#1505).

    Args:
        source_dir: Directory to tar
        output_path: Where to write the tarball
    """
    git_files = _git_tracked_files(source_dir)

    with tarfile.open(output_path, "w:gz") as tar:
        if git_files is not None:
            for relative in git_files:
                # Security net: EXCLUDE_* still applies to git-listed paths.
                if _is_excluded_relpath(relative):
                    continue
                file_path = source_dir / relative
                # Skip gitlinks (submodules), broken symlinks, and anything
                # git lists that is not a regular file on disk.
                if not file_path.is_file():
                    continue
                try:
                    tar.add(file_path, arcname=relative)
                except Exception as e:
                    logger.debug("Failed to add %s to tarball: %s", file_path, e)
            return

        for root, dirs, files in os.walk(source_dir):
            # Prune excluded directories in-place so os.walk does not descend
            # into them. Matched by exact basename or basename suffix.
            dirs[:] = [d for d in dirs if not _is_excluded_dirname(d)]

            for file in files:
                if _is_excluded_filename(file):
                    continue

                file_path = Path(root) / file
                relative = file_path.relative_to(source_dir)

                try:
                    tar.add(file_path, arcname=str(relative))
                except Exception as e:
                    logger.debug("Failed to add %s to tarball: %s", file_path, e)


def _tarball_size_warning(tarball_path: Path, threshold: Optional[int] = None) -> Optional[str]:
    """Return a human-readable warning if ``tarball_path`` is suspiciously
    large, else ``None``.

    "Large" means larger than ``threshold`` bytes (default
    :data:`TARBALL_WARN_BYTES`). The message reports the packed size and the
    largest included files (by uncompressed size) so the user can see exactly
    what to add to ``.gitignore``. Surfaced before upload so an oversized
    tarball produces an actionable hint instead of a raw nginx 413 page.
    """
    if threshold is None:
        threshold = TARBALL_WARN_BYTES
    size = tarball_path.stat().st_size
    if size <= threshold:
        return None

    try:
        with tarfile.open(tarball_path, "r:gz") as tar:
            largest = sorted(
                ((m.size, m.name) for m in tar.getmembers() if m.isfile()),
                reverse=True,
            )[:5]
    except (OSError, tarfile.TarError):
        largest = []

    lines = [
        f"Warning: deploy tarball is {size / 1_048_576:.1f} MB — unusually large "
        "and may exceed the server's upload limit.",
    ]
    if largest:
        lines.append("Largest files included:")
        lines.extend(f"  {sz / 1_048_576:6.1f} MB  {name}" for sz, name in largest)
    lines.append(
        "If any are build artifacts or local data, add them to .gitignore "
        "(the deploy honors it) and re-run."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# deploy status display (#1761) + deploy doctor preflight (#1760)
# ---------------------------------------------------------------------------


def _poll_display(data: dict) -> tuple:
    """Map a deployment-status poll response to ``(message, done, url)``.

    ``done`` is ``True`` when polling should stop. During a blue/green rollout
    the deployment row is ``active``/``deploying`` but ``serving_current`` is
    ``False`` — the OLD placement is still serving the env URL while the new
    rootfs cuts over (djustlive #517). In that window we surface "rolling out"
    and keep polling instead of reporting "active" / printing the URL, so users
    stop re-testing against stale code.

    ``serving_current`` is an additive field: older servers omit it, so a
    missing value is treated as ``True`` (fail-safe — today's behavior).
    """
    status = data.get("status")
    serving_current = data.get("serving_current", True)
    if status in ("active", "deploying") and not serving_current:
        return (
            "rolling out (new version built; old version still serving — waiting for cutover)",
            False,
            None,
        )
    url = data.get("container_url") if status in ("active", "deploying") else None
    done = status in ("active", "deploying", "failed", "cancelled", "superseded")
    return (status, done, url)


# Appended to every deploy-doctor warning so the user always learns the fix.
_DOCTOR_ENV_POINTER = (
    "the platform injects these at runtime — read them from the environment "
    "(e.g. os.environ['DATABASE_URL'], os.environ['SECRET_KEY'], "
    "os.environ['ALLOWED_HOSTS'])."
)


def _databases_region(settings_text: str) -> str:
    """Return the source text of the ``DATABASES = ...`` assignment's value.

    For a dict literal (`DATABASES = { ... }`) this brace-balances to the
    matching `}`; for a call form (`DATABASES = dj_database_url.config(...)`)
    it returns the rest of that logical line. Returns `""` when there is no
    `DATABASES` assignment. Scoping the env-read check to this region (rather
    than the whole file) keeps a `SECRET_KEY = os.environ[...]` elsewhere from
    masking a genuinely hardcoded `DATABASES` block (#1768).
    """
    m = re.search(r"^\s*DATABASES\s*=\s*", settings_text, re.MULTILINE)
    if not m:
        return ""
    start = m.end()
    if start < len(settings_text) and settings_text[start] == "{":
        depth = 0
        for i in range(start, len(settings_text)):
            ch = settings_text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return settings_text[start : i + 1]
        return settings_text[start:]  # unbalanced — fall back to the tail
    nl = settings_text.find("\n", start)
    return settings_text[start : nl if nl != -1 else len(settings_text)]


# Env-read signals: any of these inside the DATABASES region (or the
# DB-specific DATABASE_URL/dj_database_url tokens anywhere) mean the DB config
# is environment-derived, so the platform's injected values are honored.
_DB_ENV_READ_RE = re.compile(
    r"os\.environ|os\.getenv|\bgetenv\(|\benviron\[|\benviron\.get\(|\benv\(|\bconfig\(|decouple"
)


def _deploy_doctor_warnings(settings_text: str) -> list:
    """Statically inspect a Django settings module's source text and return
    human-readable WARNINGS (never errors) for settings that violate the
    platform's env-injection contract (#1760).

    These are grep-level heuristics over the source text, not an import of the
    settings module — the point is to catch the common "foreign app" footguns
    (hardcoded SECRET_KEY / ALLOWED_HOSTS, a DATABASES block that ignores
    DATABASE_URL, sqlite on the read-only rootfs) that otherwise deploy fine and
    only 500 at runtime in production.
    """
    warnings = []

    # SECRET_KEY assigned a string literal (not derived from os.environ/env()).
    m = re.search(r"^\s*SECRET_KEY\s*=\s*(.+)$", settings_text, re.MULTILINE)
    if m and m.group(1).strip()[:1] in ("'", '"'):
        warnings.append(
            "SECRET_KEY is a hardcoded literal; serving a dev key on a public host "
            "is a security risk — " + _DOCTOR_ENV_POINTER
        )

    # ALLOWED_HOSTS as a literal host list (not read from env).
    m = re.search(r"^\s*ALLOWED_HOSTS\s*=\s*(.+)$", settings_text, re.MULTILINE)
    if m:
        rhs = m.group(1).strip()
        if rhs.startswith("[") and ("'" in rhs or '"' in rhs):
            warnings.append(
                "ALLOWED_HOSTS is a hardcoded list not read from the environment; "
                "the platform host will raise DisallowedHost (400) — " + _DOCTOR_ENV_POINTER
            )

    # DATABASES that read no configuration from the environment. The canonical
    # DATABASE_URL / dj_database_url tokens count anywhere; other env reads
    # (individual os.environ['DB_*'] vars, python-decouple, etc.) count only
    # within the DATABASES region so an unrelated SECRET_KEY=os.environ[...]
    # elsewhere can't mask a hardcoded DB block (#1768).
    if re.search(r"^\s*DATABASES\s*=", settings_text, re.MULTILINE):
        reads_env = (
            "DATABASE_URL" in settings_text
            or "dj_database_url" in settings_text
            or _DB_ENV_READ_RE.search(_databases_region(settings_text)) is not None
        )
        if not reads_env:
            warnings.append(
                "DATABASES reads no configuration from the environment (neither "
                "DATABASE_URL nor os.environ); on a read-only app rootfs the first "
                "write 500s (OperationalError: readonly database) — " + _DOCTOR_ENV_POINTER
            )

    # sqlite DB, which lands under the (read-only) project dir on the platform.
    if re.search(r"ENGINE.*sqlite3", settings_text):
        warnings.append(
            "DATABASES uses sqlite3; the platform app rootfs is read-only, so a sqlite "
            "NAME under the project dir 500s on first write (use the injected DATABASE_URL "
            "/ a managed Postgres instead) — " + _DOCTOR_ENV_POINTER
        )

    return warnings


# Directories never worth scanning for a settings module.
_DOCTOR_SKIP_DIRS = frozenset(
    {".venv", "venv", "env", "site-packages", "node_modules", ".git", "__pycache__"}
)


def _find_settings_files(source_dir: Path) -> list:
    """Locate the project's primary Django settings file(s) under ``source_dir``.

    Prefers the module named by ``manage.py``'s ``DJANGO_SETTINGS_MODULE``
    default (the module the platform will actually load); falls back to globbing
    for ``settings.py`` (shallowest first). Returns ``[]`` when nothing is found.
    """
    source_dir = Path(source_dir)
    manage = source_dir / "manage.py"
    if manage.is_file():
        try:
            m = re.search(
                r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([\w.]+)['\"]",
                manage.read_text(encoding="utf-8", errors="replace"),
            )
        except OSError:
            m = None
        if m:
            rel = m.group(1).replace(".", "/")
            for cand in (source_dir / (rel + ".py"), source_dir / rel / "__init__.py"):
                if cand.is_file():
                    return [cand]

    return sorted(
        (
            p
            for p in source_dir.rglob("settings.py")
            if not any(part in _DOCTOR_SKIP_DIRS for part in p.parts)
        ),
        key=lambda p: len(p.parts),
    )


def _run_deploy_doctor(source_dir: Path) -> None:
    """Run the deploy doctor (#1760) over ``source_dir`` and print any warnings
    to stderr. Always non-blocking — a doctor failure must never stop a deploy.
    """
    try:
        for settings_file in _find_settings_files(source_dir):
            try:
                text = settings_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for warning in _deploy_doctor_warnings(text):
                click.echo(f"⚠ deploy doctor: {warning}", err=True)
            # Inspect only the primary settings module — checking dev-only
            # settings files would produce false positives.
            break
    except Exception:
        # The doctor is advisory; never let it block a deploy.
        logger.debug("deploy doctor failed; skipping", exc_info=True)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("project_slug", required=False)
@click.option("--yes", "-y", is_flag=True, help="Auto-accept all prompts (CI / scripted use).")
@click.option(
    "--no-create",
    is_flag=True,
    help="Fail if the project doesn't already exist instead of prompting to create it.",
)
@click.pass_context
def deploy(
    ctx: click.Context,
    project_slug: Optional[str],
    yes: bool,
    no_create: bool,
) -> None:
    """Deploy [PROJECT_SLUG] to production on djustlive.com.

    Walks first-time users through the full chain: log in → resolve
    slug (CLI arg → pyproject → prompt) → confirm project exists on
    server (or offer to create it) → deploy. Each step is skipped if
    its precondition is already met, so power users see only the
    deploy itself.

    Flags:
      --yes / -y    auto-accept every confirmation (CI / scripts)
      --no-create   fail-fast if the project doesn't exist server-side
    """
    _check_git_clean()
    server = ctx.obj["server"]

    creds = _ensure_logged_in(server, interactive=not no_create)

    project_slug = _resolve_project_slug(project_slug, Path.cwd(), interactive=not no_create)
    project_slug = _ensure_project_exists(
        server,
        creds,
        project_slug,
        source_dir=Path.cwd(),
        no_create=no_create,
        yes=yes,
    )

    # Preflight doctor (#1760): warn (never block) on settings that violate the
    # platform env contract before we ship them.
    _run_deploy_doctor(Path.cwd())

    url = f"{server}/api/v1/projects/{project_slug}/environments/production/deploy/"

    try:
        resp = requests.post(
            url,
            headers=_api_headers(creds),
            stream=True,
            timeout=300,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Request failed: {exc}") from exc

    if resp.status_code not in (200, 201, 202):
        raise click.ClickException(f"Deploy failed ({resp.status_code}): {resp.text}")

    for line in resp.iter_lines():
        if line:
            click.echo(line.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# deploy-dir
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("project_slug", required=False)
@click.option(
    "--dir",
    "source_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Directory to deploy (default: current working directory)",
)
@click.option("--yes", "-y", is_flag=True, help="Auto-accept all prompts (CI / scripted use).")
@click.option(
    "--no-create",
    is_flag=True,
    help="Fail if the project doesn't already exist instead of prompting to create it.",
)
@click.pass_context
def deploy_dir(
    ctx: click.Context,
    project_slug: Optional[str],
    source_dir: str,
    yes: bool,
    no_create: bool,
) -> None:
    """Deploy from a local directory (no git required).

    Same guided onboarding chain as ``deploy``: log in → resolve slug
    → confirm project exists (or offer to create) → deploy. Skips any
    step whose precondition is already satisfied.
    """
    server = ctx.obj["server"]

    creds = _ensure_logged_in(server, interactive=not no_create)

    project_slug = _resolve_project_slug(project_slug, Path(source_dir), interactive=not no_create)
    project_slug = _ensure_project_exists(
        server,
        creds,
        project_slug,
        source_dir=Path(source_dir),
        no_create=no_create,
        yes=yes,
    )

    # Preflight doctor (#1760): warn (never block) on settings that violate the
    # platform env contract before we ship them.
    _run_deploy_doctor(Path(source_dir))

    click.echo(f"Creating tarball from {source_dir}...")

    # Create tarball in temp location
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        tarball_path = Path(f.name)

    try:
        _create_tarball(Path(source_dir), tarball_path)
        click.echo(f"Tarball created: {tarball_path.stat().st_size} bytes")

        size_warning = _tarball_size_warning(tarball_path)
        if size_warning:
            click.echo(size_warning, err=True)

        url = f"{server}/api/v1/projects/{project_slug}/deploy/directory/"

        click.echo(f"Uploading to {server}...")
        upload_headers = _api_headers(creds)
        upload_headers["Content-Type"] = "application/octet-stream"
        with open(tarball_path, "rb") as f:
            resp = requests.post(
                url,
                headers=upload_headers,
                data=f,
                timeout=300,
            )

        if resp.status_code not in (200, 201, 202):
            raise click.ClickException(f"Deploy failed ({resp.status_code}): {resp.text}")

        result = resp.json()
        deployment_id = result.get("deployment_id")
        click.echo(f"Deployment triggered: {deployment_id}")

        # Poll for status
        status_url = f"{server}/api/v1/deployments/{deployment_id}/"
        for _ in range(60):  # 60 * 5s = 5 minutes max
            import time

            time.sleep(5)

            try:
                status_resp = requests.get(
                    status_url,
                    headers=_api_headers(creds),
                    timeout=30,
                )
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    # `deploying` means the k8s resources have been created and
                    # the readiness probe passed; the row may still flip to
                    # `active` later, but the app is already serving so we can
                    # return — UNLESS serving_current is False (#1761), meaning a
                    # blue/green rollout is still serving the OLD rootfs, in
                    # which case _poll_display reports "rolling out" + done=False
                    # so we keep polling until the new placement takes over.
                    message, done, container_url = _poll_display(data)
                    click.echo(f"Status: {message}")
                    if done:
                        if container_url:
                            click.echo(f"Application available at: {container_url}")
                        return
            except requests.RequestException:
                # Transient network error during status poll; the loop's
                # next iteration will retry. Logged at debug to avoid noise.
                logger.debug("status poll failed; retrying", exc_info=True)

        click.echo("Deployment timed out waiting for completion.")

    finally:
        if tarball_path.exists():
            tarball_path.unlink()
