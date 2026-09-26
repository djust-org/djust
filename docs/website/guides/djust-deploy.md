---
title: "djust-deploy CLI"
slug: djust-deploy
section: guides
order: 10
level: intermediate
description: "Deploy djust apps to djustlive.com from the command line"
---

# djust-deploy CLI

The deploy CLI manages deployments to [djustlive.com](https://djustlive.com), the managed hosting platform for djust applications.

## Installation

The CLI ships with djust. Its `click` and `requests` dependencies are base dependencies:

```bash
pip install djust
```

`pip install djust[deploy]` still works as a no-op alias for older install scripts. Two entry points are available: `djust deploy ...` (recommended) and the standalone `djust-deploy ...`. `djust deploy login`, `djust deploy logout`, `djust deploy status` and `djust deploy logs` work as written; for deploying, see [Which deploy command runs](#which-deploy-command-runs). The examples below use the standalone `djust-deploy <command>` form, which exposes every command and option directly.

## Authentication

### Login

```bash
djust-deploy login
```

Opens your browser to sign in to djustlive.com (OAuth with PKCE, using a local loopback callback). When you approve, the CLI stores an access token and a refresh token at `~/.djustlive/credentials` (file mode `0600`). If the refresh token has expired, the next deploy re-launches the browser flow.

### Logout

```bash
djust-deploy logout
```

Revokes the server session and removes the local credentials file.

## Commands

### Global options

- `--server URL`: the djustlive server to talk to (default `https://djustlive.com`). Also read from the `DJUST_SERVER` environment variable. Pass it before the command name, e.g. `djust-deploy --server URL deploy-dir`. With `djust deploy`, set `DJUST_SERVER` instead.

### Which deploy command runs

`djust deploy` picks the command for you:

```bash
djust deploy                      # deploy-dir: upload the current directory
djust deploy <project-slug>       # deploy-dir with an explicit slug
djust deploy <slug> --from-git    # deploy: git-based deploy (the flag may come first)
```

`login`, `logout`, `status` and `logs` are commands, not slugs: `djust deploy logs` shows a build log, and `djust deploy logs --from-git` is an error. To deploy a project whose slug is one of these words, name the command, as in `djust deploy deploy-dir logs` (or `djust-deploy deploy logs` for a git deploy).

### `deploy-dir`

Deploy from a local directory (no git required). The CLI tarballs the directory, uploads it, and reports the deployment status:

```bash
djust-deploy deploy-dir [<project-slug>] [--dir DIR] [--yes] [--no-create]
```

- `--dir DIR`: directory to deploy (default: the current directory).
- `--yes` / `-y`: auto-accept all prompts (CI and scripted use).
- `--no-create`: fail if the project doesn't already exist, instead of offering to create it.

### `deploy`

Trigger a git-based production deployment for a project:

```bash
djust-deploy deploy [<project-slug>] [--yes] [--no-create]
```

Before deploying, the CLI verifies that your git working tree is clean. If you have uncommitted changes, the deploy is aborted — commit or stash first. It also runs a preflight settings check that warns (never blocks) about settings that break the platform's environment contract.

`--yes` and `--no-create` behave as for `deploy-dir`. Build logs are streamed to stdout in real time.

### Project slug resolution

For both deploy commands the slug is optional. When you omit it, the CLI uses `[tool.djust.deploy].project` from `pyproject.toml`, and otherwise prompts for it (offering to save the answer to `pyproject.toml`). Both commands also log you in first if needed, and offer to create the project on the server if it doesn't exist yet.

### `status`

Check the current deployment state:

```bash
# All projects
djust-deploy status

# Specific project
djust-deploy status <project-slug>
```

Returns JSON with deployment details (state, timestamps, etc.).

### `logs`

Print the build/deploy log of a deployment: the clone, build and rollout lines the djustlive dashboard shows for it.

```bash
# The latest deployment of the project in pyproject.toml
djust-deploy logs

# The latest deployment of another project
djust-deploy logs <project-slug>

# A specific deployment
djust-deploy logs [<project-slug>] --deployment <deployment-id>

# Keep printing new lines until the deployment finishes
djust-deploy logs <project-slug> --follow
```

- The slug resolves as for the deploy commands, except that the CLI never prompts for it: with no argument and no `[tool.djust.deploy].project`, it fails and asks for the slug.
- `--deployment ID`: show that deployment instead of the most recent one. `status` lists deployment ids.
- `--follow` / `-f`: poll until the deployment reaches a final status. Exits 1 if the deployment failed or was cancelled, and 0 if it is active or was superseded, so a script can run `djust deploy logs --follow` after a deploy.

Connection errors, timeouts and 5xx responses are retried up to five times in a row with backoff, so a long `--follow` survives the server restarting. An expired login is refreshed each time it expires. Ctrl-C stops the command with exit status 130.

Log lines go to stdout as `<timestamp> <LEVEL> <message>`. The deployment's id, status and any error message go to stderr. These logs cover the build and the rollout, not the output of your running app.

## Credential Storage

Credentials are stored at `~/.djustlive/credentials` as JSON:

```json
{
  "auth_scheme": "bearer",
  "access_token": "...",
  "refresh_token": "...",
  "expires_at": 1760000000,
  "email": "you@example.com",
  "server_url": "https://djustlive.com"
}
```

The directory is created with mode `0700` and the file with mode `0600` (owner-only access).
