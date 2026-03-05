---
title: "djust-deploy CLI"
slug: djust-deploy
section: guides
order: 10
level: intermediate
description: "Deploy djust apps to djustlive.com from the command line"
---

# djust-deploy CLI

The `djust-deploy` command-line tool manages deployments to [djustlive.com](https://djustlive.com), the managed hosting platform for djust applications.

## Installation

The CLI requires the `deploy` optional dependency:

```bash
pip install djust[deploy]
```

This installs the `djust-deploy` entry point along with the `click` and `requests` packages.

## Authentication

### Login

```bash
djust-deploy login
```

Prompts for your email and password, authenticates against djustlive.com, and stores the API token locally at `~/.djustlive/credentials` (file mode `0600`).

### Logout

```bash
djust-deploy logout
```

Revokes the server session and removes the local credentials file.

## Commands

### `deploy`

Trigger a production deployment for a project:

```bash
djust-deploy deploy <project-slug>
```

Before deploying, the CLI verifies that your git working tree is clean. If you have uncommitted changes, the deploy is aborted — commit or stash first.

Build logs are streamed to stdout in real time.

### `status`

Check the current deployment state:

```bash
# All projects
djust-deploy status

# Specific project
djust-deploy status <project-slug>
```

Returns JSON with deployment details (state, timestamps, etc.).

## Custom Server

By default all commands target `https://djustlive.com`. To use a different server (e.g., a staging instance):

```bash
# Via flag
djust-deploy --server https://staging.djustlive.com deploy my-app

# Via environment variable
export DJUST_SERVER=https://staging.djustlive.com
djust-deploy deploy my-app
```

The server URL is saved with your credentials at login time. When you pass `--server` explicitly, it overrides the stored URL for that command.

## Credential Storage

Credentials are stored at `~/.djustlive/credentials` as JSON:

```json
{
  "token": "...",
  "email": "you@example.com",
  "server_url": "https://djustlive.com"
}
```

The directory is created with mode `0700` and the file with mode `0600` (owner-only access).
