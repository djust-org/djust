# Strip `examples/demo_project` down to a test harness

**Status**: Proposed
**Target version**: v0.5.1 or v0.5.2 (opportunistic — fits cleanly between other DX work)
**Owner**: TBD
**Effort**: ~2 hours on the critical path, ~3-4 hours with docs/polish
**Related**: `djust-scaffold` (sibling project — the real starter template)

---

## Context

`examples/demo_project/` inside the core djust repo is misnamed. It plays two
distinct roles:

1. **Test-harness + dev-server fixture** — `settings.py`, `urls.py`, `asgi.py`,
   `wsgi.py`, `manage.py`. Load-bearing for the pytest suite (`pyproject.toml`
   sets `DJANGO_SETTINGS_MODULE = "demo_project.settings"`), the playwright CI
   workflow, and the Makefile's `make start` dev server. This IS maintained
   (I just fixed `djust_theming.E001` in it during PR #838).
2. **12 pseudo-demo apps** — `demo_app`, `djust_homepage`, `djust_demos`,
   `djust_forms`, `djust_tests`, `djust_docs`, `djust_rentals`,
   `djust_shared` (and associated templates + static). These are NOT
   maintained and accumulate bit-rot.

The **real** user-facing starter template lives in the sibling repo
`djust-scaffold` — that's what new users should clone to learn djust. The
presence of an unmaintained "demo" inside the framework repo creates a bad
first impression (stale views, dead links, drifted styling) and a maintenance
tax with no payoff.

## Goal

Split the two roles cleanly:

- Move the test-harness bits to `tests/test_project/` to match their actual role
  (internal fixture, not user-facing example).
- Delete the 12 unmaintained demo apps.
- Point users at `djust-scaffold` for the real starter experience.

## What the exploration established (ground truth)

Dependencies on the 12 apps, from a full grep-based audit:

### Test-code imports from demo apps (4 files, 2 models) — **REAL coupling**

| File | Imports | Action |
|---|---|---|
| `tests/unit/test_demo_views.py:41-229` | `djust_demos.views.tenant_demo.TenantDemoView`, `DEMO_TENANTS` | Refactor to inline fixture — tests multi-tenant state isolation; doesn't need the demo view specifically |
| `tests/test_query_optimizer.py:29` | `djust_rentals.models.{Lease, Property, Tenant, MaintenanceRequest, Payment}` | Move models to `tests/test_project/test_rentals/models.py` (or inline into the test as dynamically-created `class Meta: app_label = 'test_rentals'` models) |
| `tests/test_query_optimizer_integration.py:30` | Same as above | Same |
| `tests/manual_verification_reset_form.py:38` | `demo_app.views.forms_demo.ProfileFormView` | Copy view definition inline (non-CI file, low priority) |
| `python/tests/test_liveview_jit_integration.py:19` | Same rentals models | Already `pytest.mark.skip` fallback — no-op |

### conftest.py (`tests/conftest.py:11-17, 69-83`) — **Mechanical**

- sys.path injection referencing `examples/demo_project` — rename path only
- `cleanup_session_cache()` fixture — uses djust internals, transferable as-is

### settings.py hard-coded demo references — **5 blockers**

| Line | Reference | Fix |
|---|---|---|
| 37-58 | `INSTALLED_APPS` lists 8 demo apps | Keep only `djust`, `djust.theming`, `djust.admin_ext`, `channels`, contrib; add `test_rentals` if we move the models there |
| 73 | `ROOT_URLCONF = 'demo_project.urls'` | Rename to `test_project.urls` |
| 86 | `'demo_app.context_processors.navbar'` | Delete — tests don't need navbar |
| 119-122 | `STATICFILES_DIRS` includes `demo_app/static` | Delete demo_app entry |
| 144-150 | `LIVEVIEW_ALLOWED_MODULES` lists 5 demo modules | Replace with the modules actually mounted by tests (likely none; a wildcard or a single test module) |

### urls.py demo app includes (`demo_project/urls.py:34-42`)

| Path | Demo app | Test-critical? |
|---|---|---|
| `/` | `djust_homepage.urls` | No — no tests call `reverse('djust_homepage:...')` |
| `/demos/` | `djust_demos.urls` | No |
| `/forms/` | `djust_forms.urls` | No — Makefile info-only |
| `/tests/` | `djust_tests.urls` | **YES** — playwright hardcodes `http://localhost:8002/tests/loading/`, `/cache/`, `/draft-mode/` |
| `/docs/`, `/rentals/` | n/a | No |

→ Must preserve `/tests/loading/`, `/tests/cache/`, `/tests/draft-mode/` routes in the new `test_project` — or delete the playwright tests that hit them.

### Makefile targets referencing `examples/demo_project` (8 targets)

`start`, `start-bg`, `migrate`, `migrations`, `db-reset`, `shell`, test targets. All mechanical path updates.

### GitHub workflow (`.github/workflows/test.yml`) — playwright-tests

Hardcodes `cd examples/demo_project` in 4 places (migrate, server start, log paths, pid path). Mechanical sed replace to `tests/test_project`.

### Docs — 4 files reference it, none structurally

`docs/TESTING.md`, `docs/BEST_PRACTICES_AI.md`, `docs/JIT_SERIALIZATION_PATTERN.md`, `docs/TEMPLATE_RESOLUTION.md:26`. Informational updates only.

### Templates & static (demo_app) — **SAFE to delete**

Zero test-code references to template paths or static paths from demo apps. Programmatic rendering only.

## Plan

### Step 1 — Scaffold `tests/test_project/` (30 min)

Create a minimal Django project matching the role:

```
tests/test_project/
├── manage.py                     # Adjusted path for BASE_DIR
├── test_project/
│   ├── __init__.py
│   ├── settings.py               # Stripped settings — no demo apps, no demo_app.context_processors
│   ├── urls.py                   # Stripped urls — only test-critical includes
│   ├── asgi.py
│   └── wsgi.py
├── test_rentals/                 # Minimal rentals models for query-optimizer tests
│   ├── __init__.py
│   ├── apps.py
│   └── models.py                 # Property, Tenant, Lease, MaintenanceRequest, Payment
└── test_playwright_views/        # Minimal views for /tests/loading/ etc.
    ├── __init__.py
    ├── apps.py
    ├── urls.py
    └── views.py                  # LoadingAttributeView, CacheView, DraftModeView
```

Principles:
- Settings file has zero demo-app references
- `INSTALLED_APPS` limited to djust core + `test_rentals` + `test_playwright_views`
- Templates inline (no `DIRS`) — tests use programmatic rendering
- `LIVEVIEW_ALLOWED_MODULES = ["test_playwright_views.views"]`

### Step 2 — Port the 5 test-critical pieces (60 min)

1. `tests/test_query_optimizer.py` + `tests/test_query_optimizer_integration.py`: update imports from `djust_rentals.models` → `test_rentals.models`.
2. `tests/unit/test_demo_views.py`: refactor to use an inline `TenantDemoView` defined in the test file (or a `test_playwright_views/tenant_view.py`). Preserve the tenant-isolation assertions.
3. `tests/playwright/test_loading_attribute.py`, `test_cache_decorator.py`, `test_draft_mode.py`: port the exact views they exercise into `test_playwright_views/views.py`. Only the views the playwright tests actually hit — not the whole `djust_tests` app.
4. `python/tests/test_liveview_jit_integration.py`: update the conditional import path (still `pytest.mark.skip` fallback).
5. `tests/manual_verification_reset_form.py`: inline `ProfileFormView` or mark the file as deprecated.

### Step 3 — Flip the switches (15 min)

Mechanical updates in this order (so tests keep working after each change):

1. `pyproject.toml`:
   ```toml
   pythonpath = [".", "tests/test_project"]
   DJANGO_SETTINGS_MODULE = "test_project.settings"
   ```
2. `tests/conftest.py`: update sys.path path.
3. `Makefile`: replace `examples/demo_project` → `tests/test_project` across all targets (sed). Update `demo_project.asgi` → `test_project.asgi` and `--app-dir` paths.
4. `.github/workflows/test.yml`: replace `examples/demo_project` → `tests/test_project` in playwright-tests job (4 sites).

### Step 4 — Verify, then delete (20 min)

1. Run `make test` — all tests pass on the new `tests/test_project/`.
2. Run `make start` — dev server comes up on :8002.
3. Run playwright tests locally if possible (or rely on CI).
4. If all green: delete `examples/demo_project/` entirely.
5. Update `.gitignore` if any demo-project entries were in there.

### Step 5 — Docs & README (30 min)

1. Update `djust/README.md`: replace any "cd examples/demo_project && make start" snippets with "Run `make start` from the repo root (internal test server). For a real starter project, see the `djust-scaffold` repo."
2. Update `djust/CLAUDE.md` ("Manual Integration Testing" section) — rewrite the examples/demo_project references.
3. Update `docs/TESTING.md`, `docs/BEST_PRACTICES_AI.md`, `docs/JIT_SERIALIZATION_PATTERN.md`, `docs/TEMPLATE_RESOLUTION.md` — informational.
4. Add a link from the README to `djust-scaffold`.

### Step 6 — Single PR

One squash-merged PR with an explicit "BREAKING: internal test path moved; no user-facing API change" line in the description. Not truly breaking for end users (they use `djust-scaffold` or their own projects, not our `examples/demo_project`), but worth flagging for anyone running scripts against `examples/demo_project/db.sqlite3` or similar.

## Critical files

**Moves**: `examples/demo_project/demo_project/{settings,urls,asgi,wsgi}.py` → `tests/test_project/test_project/*.py`

**Creates**: `tests/test_project/test_rentals/{apps,models}.py`, `tests/test_project/test_playwright_views/{urls,views}.py`

**Deletes**: `examples/demo_project/` (entire tree — ~12 apps, templates, static, db.sqlite3)

**Edits** (mechanical path updates): `pyproject.toml`, `tests/conftest.py`, `Makefile`, `.github/workflows/test.yml`, `tests/test_query_optimizer*.py`, `tests/unit/test_demo_views.py`, `tests/manual_verification_reset_form.py`, `python/tests/test_liveview_jit_integration.py`

**Doc updates**: `README.md`, `CLAUDE.md`, `docs/TESTING.md`, `docs/BEST_PRACTICES_AI.md`, `docs/JIT_SERIALIZATION_PATTERN.md`, `docs/TEMPLATE_RESOLUTION.md`

## Verification

1. `make test` — full suite green, no regressions (expect same pre-existing failures as main).
2. `make start` — dev server starts, `http://localhost:8002/tests/loading/` serves the loading-attribute playwright fixture.
3. `gh pr checks` on the PR — playwright-tests green (with the E001 fix from #838 already in place).
4. `ls examples/` — either empty or contains only ACTUALLY-maintained examples (none today).
5. `make djust-install` still works in the workspace root.
6. `djust-scaffold` still installs cleanly and references the latest djust version.

## Out of scope

- Improving `djust-scaffold` itself — this plan only stops pretending the core repo has a demo.
- Extracting migrations history from `djust_rentals` (no migration files today; in-memory SQLite).
- Renaming the djust-scaffold repo or restructuring its README — separate discussion.
- Creating user-facing examples under `examples/` — deliberately leave empty or delete the directory; point at `djust-scaffold` and the docs site.

## Why this is worth doing

- **Stops a lie**: "examples/demo_project/" reads as "a demo of djust" to every new contributor; it's not.
- **Reduces surface area**: ~12 apps × N files of code you're not touching → gone. Smaller repo, faster CI checkout.
- **Clearer story**: scaffold repo is for users, test_project is internal. One purpose per tree.
- **Pays off future work**: Any time we add a test that needs a project fixture (API tests, session tests, routing tests), `tests/test_project/` is the obvious home — no pressure to bolt onto an unmaintained demo.

## Risks

1. **Playwright tests depend on specific URLs** — mitigated by porting the exact views into `test_playwright_views`, not the entire `djust_tests` app.
2. **Someone's local muscle memory runs `cd examples/demo_project && make migrate`** — mitigated by the README update + a short migration note in CHANGELOG.
3. **djust-internal or djust.org repos might reference `examples/demo_project`** — quick grep of sibling repos before merging; update as needed.
