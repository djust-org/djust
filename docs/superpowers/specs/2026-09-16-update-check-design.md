# Update and security-advisory notice

Date: 2026-09-16
Status: approved design

## Goal

Tell developers, in one line and only where they are already reading output,
that a newer djust is available or that their installed version has a
published security advisory. Never prompt, never upgrade, never block.

## Data sources (verified 2026-09-16)

- Latest release: `https://pypi.org/pypi/djust/json` → `info.version`
  (~0.3 s, no auth).
- Advisories: `https://api.github.com/repos/djust-org/djust/security-advisories?state=published&per_page=100`
  (no auth, 60 requests/hour unauthenticated, which a 24-hour cache never
  approaches). Each entry has `ghsa_id`, `severity`, and
  `vulnerabilities[].vulnerable_version_range` / `patched_versions` in
  PEP 440 specifier form (`< 1.0.7`, `<= 1.1.1`). OSV was rejected: it lists 4
  of the 14 advisories affecting 1.0.6 and none of the 1.1.x ones.

## Behaviour

`djust.updates.check()` returns an `UpdateStatus` (installed, latest,
matching advisories) or `None` when the check is off, quiet, or has no data.
Callers print `status.message(install_hint)`:

```
djust 1.2.1 is available (you have 1.1.3): uv pip install -U djust
SECURITY: djust 1.1.1 has 2 published advisories (GHSA-xjw9-38cr-6372, GHSA-9395-2g46-rj3f): upgrade to 1.1.2 or later. https://github.com/djust-org/djust/security/advisories
```

The security line wins when both apply; a newer final release is named as the
upgrade target when it is higher than the advisory's patched version.

| Entry point | Behaviour | Hint |
|---|---|---|
| `djust new`, `djust init` | synchronous, before any work, 2 s cap | `uv tool upgrade djust` when `sys.argv[0]` is under a uv tool dir, otherwise `uvx djust@latest` |
| `DjustConfig.ready()` (dev server) | background daemon thread; logs at INFO on `djust.updates` | `uv pip install -U djust` |
| `manage.py check` | system check `djust.U001` (`Warning` for advisories, `Info` for a release) from cache only; never fetches | `uv pip install -U djust` |

Quiet when any of: `DJUST_NO_UPDATE_CHECK` set; `DJUST_CONFIG["update_check"]`
is `False`; `CI` set; stdout not a TTY (CLI and dev server only; the system
check ignores the TTY test); `settings.DEBUG` is `False` (Django entry points
only); `PYTEST_CURRENT_TEST` set; any fetch error, timeout, or bad payload.

Pre-releases: comparison uses `packaging.version.Version`. An installed
pre-release is only told about a higher version (pre or final); an installed
final is never told about a pre-release.

## Cache

`$DJUST_CACHE_DIR`, else `$XDG_CACHE_HOME/djust`, else `~/.cache/djust`
(`%LOCALAPPDATA%\djust` on Windows); file `updates.json`:

```json
{"checked_at": 1758000000, "latest": "1.2.1", "advisories": [{"ghsa_id": "...", "severity": "high", "range": "<= 1.1.1", "patched": "1.1.2"}]}
```

A successful fetch is reused for 24 hours. A failed fetch writes
`{"failed_at": ...}` and suppresses retries for 1 hour. A corrupt or
unwritable cache is treated as empty and never raises.

## Privacy

Two GETs, `User-Agent: djust/<installed version>`, nothing else sent. The
docs page states this.

## Non-goals

No auto-upgrade, no prompt, no daemon, no telemetry, no check in production
(`DEBUG=False`), no dependency on `platformdirs` (`requests` is already a base
dependency and is used).
