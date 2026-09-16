# Update Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One-line, cached, opt-out notice of newer releases and matching security advisories at `djust new`/`init`, dev-server start, and `manage.py check`.

**Architecture:** A pure module `djust.updates` (fetch, match, cache, gate, render) with three thin callers. All network goes through two functions that tests patch.

**Tech Stack:** `requests` (existing dep), `packaging` (installed with pip/uv; declare `packaging>=21` as a dependency), Django system checks.

**Spec:** `docs/superpowers/specs/2026-09-16-update-check-design.md`

## Global Constraints

- Worktree `/Users/tip/Dropbox/online_projects/ai/djust_project/djust-wt-updates`, branch `feat/update-check`. Run tests with `PYTHONPATH=$PWD/python:$PWD /Users/tip/Dropbox/online_projects/ai/djust_project/djust/.venv/bin/python -m pytest -p no:cacheprovider`.
- Logging with `%s`, no f-strings. No `print()` outside `cli.py`.
- Timeouts: 2 s per request. Cache TTL 24 h; failure backoff 1 h.
- Check id `djust.U001`. Settings key `DJUST_CONFIG["update_check"]`; env `DJUST_NO_UPDATE_CHECK`, `DJUST_CACHE_DIR`.
- Commits end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.

---

### Task 1: `djust.updates` core — matching, comparison, rendering

**Files:** Create `python/djust/updates.py`; Test `python/djust/tests/test_updates.py`; fixture `python/djust/tests/fixtures/advisories.json` (captured from the GitHub API).

**Produces:** `Advisory(ghsa_id, severity, range, patched)`, `parse_advisories(payload) -> list[Advisory]`, `advisories_for(version: str, advisories) -> list[Advisory]`, `is_newer(installed: str, latest: str) -> bool`, `UpdateStatus(installed, latest, advisories)` with `.message(install_hint) -> str | None`.

- [ ] Tests: parse the fixture (15 entries); `advisories_for("1.0.6")` → 14, `"1.1.1"` → 1, `"1.1.3"` → 0; `is_newer` for (`1.1.3`,`1.2.1`) True, (`1.2.0rc8`,`1.1.3`) False, (`1.2.0rc8`,`1.2.0`) True, (`1.1.3`,`1.2.0rc1`) False; messages for release-only, advisory-only, both (security line wins, names the higher target); no message when up to date.
- [ ] Implement; run; commit `feat: update-check core (matching, comparison, message)`.

### Task 2: cache and gate

**Produces:** `cache_path()`, `load_cache() -> dict`, `save_cache(dict)`, `should_check(environ, isatty, debug, config) -> bool`, `check(now=None, fetch=True) -> UpdateStatus | None`, `fetch_latest()`, `fetch_advisories()`, `check_in_background(callback)`.

- [ ] Tests (patch `requests.get`, `tmp_path` as `DJUST_CACHE_DIR`): fresh cache → no fetch; expired → fetch and rewrite; failure → `failed_at` written, second call within 1 h does not fetch; corrupt file → empty; `should_check` False for each gate; `check(fetch=False)` uses cache only; background thread calls callback and swallows exceptions.
- [ ] Implement; commit `feat: update-check cache, gates and fetchers`.

### Task 3: callers

**Files:** `python/djust/cli.py` (`cmd_new`, `cmd_init`), `python/djust/apps.py` (`ready`), new `python/djust/checks/updates.py` registered like the others, `pyproject.toml` (`packaging` dep), `python/djust/config.py` default `update_check: True` if defaults live there.

- [ ] Tests: `cmd_new` prints the message when `check` returns a status and nothing when `None`; `--help` does not trigger; hint chooses `uv tool upgrade djust` when `sys.argv[0]` contains `/tools/djust/`; `ready()` starts the background check only when `should_check`; system check emits `Warning` for advisories / `Info` for release from cache and never calls `fetch_*`.
- [ ] Implement; commit `feat: notify about updates at new/init, dev server start and manage.py check`.

### Task 4: docs and changelog

- [ ] `docs/website/guides/update-check.md`: what runs, what is sent, how to disable, the cache location. Link from `installation.md` (Troubleshooting → "keeping djust current"). `changelog.d/update-check.added.md`.
- [ ] Commit `docs: describe the update and advisory notice`.

### Task 5: verification

- [ ] Full suite across all roots. Gate-off pass on each task's fix.
- [ ] Live smoke: `DJUST_CACHE_DIR=$(mktemp -d) python -m djust new x --no-setup` with djust installed at 1.1.1 in a scratch venv prints the SECURITY line; with the worktree version prints nothing.
