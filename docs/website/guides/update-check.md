# Update and security notices

djust tells you, in one line, when a newer release is available or when the
version you have installed has a published security advisory. It never
prompts, never upgrades anything, and never blocks.

```text
djust 1.2.1 is available (you have 1.1.3): uv pip install -U djust
```

```text
SECURITY: djust 1.1.1 has 1 published advisory (GHSA-xjw9-38cr-6372): upgrade to 1.1.2 or later: uv pip install -U djust. https://github.com/djust-org/djust/security/advisories
```

## Where it appears

| Entry point | Behaviour |
| --- | --- |
| `djust new`, `djust init` | Checked before any work, with a 2-second cap. The hint is `uv tool upgrade djust` for a `uv tool` install, otherwise `uvx djust@latest`. |
| Development server start | Checked on a background thread; the line is logged on the `django.djust.updates` logger (warning for an advisory, info for a release), which Django's default logging shows on the console. |
| `manage.py check` | Reported as `djust.U001`: a warning for an advisory, an informational message for a release. The check reads the cached result and never fetches; only a running development server refreshes the cache. |

## What it sends

Two requests, carrying only `User-Agent: djust/<installed version>` and
`Accept: application/json`:

- `https://pypi.org/pypi/djust/json` for the latest release.
- `https://api.github.com/repos/djust-org/djust/security-advisories?state=published&per_page=100`
  for published advisories and their version ranges.

The result is cached for 24 hours in `~/.cache/djust/updates.json`
(`$XDG_CACHE_HOME/djust`, or `%LOCALAPPDATA%\djust` on Windows; override with
`DJUST_CACHE_DIR`). A failed request is silent and not retried for an hour.

## When it stays quiet

- `DEBUG = False` (the dev server and `manage.py check` never check in production).
- The `CI` environment variable is set.
- Standard output is not a terminal (`djust new` and `djust init`).
- The process is not a development server: `manage.py migrate`, `check` and
  other management commands never fetch.
- Any network error, timeout, or unexpected response.
- Under pytest.

Pre-releases are compared sensibly: a final release is never told about a
pre-release, and a pre-release only hears about something higher.

## Turning it off

In a project:

```python
DJUST_CONFIG = {"update_check": False}
```

For the CLI, or everywhere:

```bash
export DJUST_NO_UPDATE_CHECK=1
```

To silence only the system check, add `"djust.U001"` to
`SILENCED_SYSTEM_CHECKS`.
