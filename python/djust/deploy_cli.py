"""
djust deploy CLI — deployment commands for djustlive.com.

Entry point: djust-deploy
"""

import json
import logging
import os
import subprocess
import tarfile
from pathlib import Path
from typing import Optional

import click
import requests

logger = logging.getLogger(__name__)

DEFAULT_SERVER = "https://djustlive.com"
_CREDS_DIR_NAME = ".djustlive"
_CREDS_FILE_NAME = "credentials"

# Default patterns to exclude from tarball
TARBALL_EXCLUDES = [
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    ".env",
    "node_modules",
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.egg-info",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".DS_Store",
    "Thumbs.db",
    "db.sqlite3",
    "*.log",
    "logs",
    "media",
    "staticfiles",
]


# ---------------------------------------------------------------------------
# Credential helpers
# ---------------------------------------------------------------------------


def credentials_path() -> Path:
    """Return the path to the credentials file (~/.djustlive/credentials)."""
    return Path.home() / _CREDS_DIR_NAME / _CREDS_FILE_NAME


def save_credentials(token: str, email: str, server_url: str) -> None:
    """Write credentials to disk with mode 0o600 (created atomically)."""
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"token": token, "email": email, "server_url": server_url}
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(json.dumps(data))


def load_credentials() -> dict:
    """Load credentials from disk, raising ClickException if absent."""
    path = credentials_path()
    if not path.exists():
        raise click.ClickException("Not logged in. Run `djust-deploy login` first.")
    return json.loads(path.read_text())


def _api_headers(token: str) -> dict:
    return {
        "Authorization": f"Token {token}",
        "Content-Type": "application/json",
    }


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
    ctx.obj["server"] = (server or DEFAULT_SERVER).rstrip("/")


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Log in to djustlive.com and store credentials."""
    server = ctx.obj["server"]
    email = click.prompt("Email")
    password = click.prompt("Password", hide_input=True)

    try:
        resp = requests.post(
            f"{server}/api/v1/auth/login/",
            json={"email": email, "password": password},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Request failed: {exc}") from exc

    if resp.status_code != 200:
        try:
            body = resp.json() if resp.content else {}
        except (ValueError, json.JSONDecodeError):
            body = {}
        detail = body.get("detail") or body.get("non_field_errors") or resp.text
        raise click.ClickException(f"Login failed: {detail}")

    token = resp.json().get("token")
    if not token:
        raise click.ClickException("Server did not return a token.")

    save_credentials(token, email, server)
    click.echo(f"Logged in as {email}.")


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
    token = creds["token"]

    try:
        requests.delete(
            f"{server}/api/v1/auth/logout/",
            headers=_api_headers(token),
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
    token = creds["token"]

    params = {}
    if project:
        params["project"] = project

    try:
        resp = requests.get(
            f"{server}/api/v1/deployments/status/",
            headers=_api_headers(token),
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


def _create_tarball(source_dir: Path, output_path: Path) -> None:
    """
    Create a tarball of source_dir excluding unwanted patterns.

    Args:
        source_dir: Directory to tar
        output_path: Where to write the tarball
    """
    with tarfile.open(output_path, "w:gz") as tar:
        for root, dirs, files in os.walk(source_dir):
            # Modify dirs in-place to exclude patterns
            dirs[:] = [
                d
                for d in dirs
                if not any(
                    pattern in d
                    for pattern in [
                        ".git",
                        ".venv",
                        "venv",
                        "node_modules",
                        "__pycache__",
                        ".pytest_cache",
                        ".mypy_cache",
                        ".ruff_cache",
                        ".egg-info",
                        "dist",
                        "build",
                        ".idea",
                        ".vscode",
                    ]
                )
            ]

            for file in files:
                file_path = Path(root) / file
                relative = file_path.relative_to(source_dir)

                # Skip excluded files
                if any(
                    pattern in str(relative)
                    for pattern in [
                        ".pyc",
                        ".pyo",
                        ".DS_Store",
                        "Thumbs.db",
                        "db.sqlite3",
                        ".env",
                        "*.log",
                    ]
                ):
                    continue

                try:
                    tar.add(file_path, arcname=str(relative))
                except Exception as e:
                    logger.debug("Failed to add %s to tarball: %s", file_path, e)


# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("project_slug")
@click.pass_context
def deploy(ctx: click.Context, project_slug: str) -> None:
    """Deploy PROJECT_SLUG to production on djustlive.com."""
    _check_git_clean()

    creds = load_credentials()
    server = ctx.obj["server"]
    token = creds["token"]

    url = f"{server}/api/v1/projects/{project_slug}/environments/production/deploy/"

    try:
        resp = requests.post(
            url,
            headers=_api_headers(token),
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
@click.argument("project_slug")
@click.option(
    "--dir",
    "source_dir",
    type=click.Path(exists=True, file_okay=False, dir_okay=True),
    default=".",
    help="Directory to deploy (default: current working directory)",
)
@click.pass_context
def deploy_dir(ctx: click.Context, project_slug: str, source_dir: str) -> None:
    """Deploy from a local directory (no git required)."""
    creds = load_credentials()
    server = ctx.obj["server"]
    token = creds["token"]

    click.echo(f"Creating tarball from {source_dir}...")

    # Create tarball in temp location
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as f:
        tarball_path = Path(f.name)

    try:
        _create_tarball(Path(source_dir), tarball_path)
        click.echo(f"Tarball created: {tarball_path.stat().st_size} bytes")

        url = f"{server}/api/v1/projects/{project_slug}/deploy/directory/"

        click.echo(f"Uploading to {server}...")
        with open(tarball_path, "rb") as f:
            resp = requests.post(
                url,
                headers={
                    "Authorization": f"Token {token}",
                    "Content-Type": "application/octet-stream",
                },
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
                    headers=_api_headers(token),
                    timeout=30,
                )
                if status_resp.status_code == 200:
                    data = status_resp.json()
                    status = data.get("status")
                    click.echo(f"Status: {status}")

                    if status in ("active", "failed", "cancelled", "superseded"):
                        if status == "active":
                            container_url = data.get("container_url")
                            if container_url:
                                click.echo(f"Application available at: {container_url}")
                        return
            except requests.RequestException:
                pass

        click.echo("Deployment timed out waiting for completion.")

    finally:
        if tarball_path.exists():
            tarball_path.unlink()
