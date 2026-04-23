# Migrating from standalone djust packages

**TL;DR:** Replace `pip install djust-auth` (or any sibling package) with `pip install djust[auth]`. Imports from `djust_auth` become `djust.auth`. A `DeprecationWarning` reminder is emitted from the legacy package until you migrate.

## Who this applies to

You're using one or more of the legacy standalone packages:

| Standalone | Sunset | Replacement |
|---|---|---|
| `djust-auth` | v99.0.0 | `djust[auth]` |
| `djust-tenants` | v99.0.0 | `djust[tenants]` (+ `djust[tenants-redis]` or `djust[tenants-postgres]` for backends) |
| `djust-theming` | v99.0.0 | `djust[theming]` |
| `djust-components` | v99.0.0 | `djust[components]` |
| `djust-admin` | v99.0.0 | `djust[admin]` (module is `djust.admin_ext` — see note below) |

All standalone packages were frozen at `v99.0.0` on 2026-04-23 ([ADR-007](../../adr/007-package-taxonomy-and-consolidation.md) Phase 4). They stay installable from PyPI forever for legacy projects, but no new releases will ship. All future work happens in `djust[<name>]` extras.

## What changed

The consolidation that started in v0.5.0 moved each sibling package's source into `python/djust/<name>/` inside the core djust repository. As of v0.6.0:

- **Source of truth** is `djust.<name>` (e.g., `djust.auth`, `djust.tenants`).
- **Installation** is via extras (`pip install djust[auth]`).
- **Legacy imports** (`from djust_auth import X`) still work — the standalone packages retain a compat shim that re-exports from `djust.<name>` and emits a `DeprecationWarning`.
- **Direct upgrades** of the standalone packages no longer happen. `pip install djust-auth --upgrade` stays at v99.0.0.

## Migration steps

### 1. Update your dependency declaration

**Before (in `pyproject.toml`):**

```toml
dependencies = [
    "djust>=0.5.0",
    "djust-auth>=0.3.0",
    "djust-tenants>=0.3.0",
    "djust-theming>=0.3.0",
]
```

**After:**

```toml
dependencies = [
    "djust[auth,tenants,theming]>=0.6.0",
]
```

You can list multiple extras in a single `djust[a,b,c]` brace. Remove the old standalone pins.

For `djust-tenants` users: pick the backend-specific extra that matches your tenant resolver.

```toml
# Redis-backed tenant resolver:
"djust[tenants-redis]>=0.6.0"

# PostgreSQL schema-per-tenant:
"djust[tenants-postgres]>=0.6.0"
```

### 2. Update imports

```python
# Before
from djust_auth import LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin
from djust_tenants import TenantMiddleware
from djust_theming import PaletteGenerator, ThemeManager
from djust_components.data import TableComponent
from djust_admin import AdminLiveViewMixin

# After
from djust.auth import LoginRequiredLiveViewMixin, PermissionRequiredLiveViewMixin
from djust.tenants import TenantMiddleware
from djust.theming import PaletteGenerator, ThemeManager
from djust.components.data import TableComponent
from djust.admin_ext import AdminLiveViewMixin   # note: module renamed to `admin_ext`
```

**Module name caveat — `djust.admin_ext`:** the extra is spelled `djust[admin]` but the Python module is `djust.admin_ext` (to avoid colliding with Django's `django.contrib.admin`). All your `from djust_admin import ...` lines become `from djust.admin_ext import ...`, not `from djust.admin import ...`.

A mechanical sed script handles the 90% case. Note that `djust_admin` → `djust.admin_ext` (not `djust.admin` — Django's `django.contrib.admin` would collide):

```bash
# Back up first!
find . -name "*.py" -exec sed -i.bak \
    -e 's/from djust_auth/from djust.auth/g' \
    -e 's/import djust_auth/import djust.auth/g' \
    -e 's/from djust_tenants/from djust.tenants/g' \
    -e 's/import djust_tenants/import djust.tenants/g' \
    -e 's/from djust_theming/from djust.theming/g' \
    -e 's/import djust_theming/import djust.theming/g' \
    -e 's/from djust_components/from djust.components/g' \
    -e 's/import djust_components/import djust.components/g' \
    -e 's/from djust_admin/from djust.admin_ext/g' \
    -e 's/import djust_admin/import djust.admin_ext/g' \
    {} +
```

Review the diffs (`grep -r 'djust_' . --include='*.py'` for stragglers) before deleting `.bak` files.

### 3. Update Django `INSTALLED_APPS`

Not every consolidated package is a full Django app. Only the ones with an `AppConfig` need to be registered:

| Consolidated module | Is a Django app? | `INSTALLED_APPS` entry |
|---|---|---|
| `djust.auth` | ✅ yes | `"djust.auth"` |
| `djust.tenants` | ❌ no (library only — middleware + helpers) | _do not add_ |
| `djust.theming` | ✅ yes | `"djust.theming"` |
| `djust.components` | ✅ yes | `"djust.components"` |
| `djust.admin_ext` | ✅ yes | `"djust.admin_ext"` (note: `_ext` suffix) |

```python
# Before
INSTALLED_APPS = [
    "djust",
    "djust_auth",
    "djust_tenants",         # was registered as app in old packaging
    "djust_theming",
    "djust_components",
    "djust_admin",
]

# After
INSTALLED_APPS = [
    "djust",
    "djust.auth",
    # djust.tenants — NOT an app; drop this line. Register middleware instead.
    "djust.theming",
    "djust.components",
    "djust.admin_ext",       # note: admin_ext, not admin
]

MIDDLEWARE = [
    # ... standard Django middleware ...
    "djust.tenants.TenantMiddleware",   # new location (was djust_tenants.TenantMiddleware)
    # ... rest of MIDDLEWARE ...
]
```

If you reference the app label in code (e.g., for migrations or signal routing), update those references too. Use `django-admin showmigrations` after the change to verify nothing is orphaned.

### 4. Run the test suite

The shipped shims keep old imports working but emit a `DeprecationWarning`. Running with `python -W error::DeprecationWarning` surfaces any missed import site:

```bash
python -W error::DeprecationWarning -m pytest
```

Fix any `DeprecationWarning` hits before removing the old standalone pins.

### 5. Remove the old standalone packages

```bash
pip uninstall djust-auth djust-tenants djust-theming djust-components djust-admin
pip install -e .[auth,tenants,theming,components,admin]
```

Verify with `pip list | grep djust` — only `djust` should remain.

## FAQ

### Will the standalone packages stop working?

**No.** `v99.0.0` is the frozen release and stays on PyPI. Projects that never migrate will continue to install and work, indefinitely. The `DeprecationWarning` is a nudge, not a hard deadline.

### Can I use `djust.auth` without installing the `auth` extra?

Technically yes — `python/djust/auth/` ships inside the core wheel. The extra exists mainly for discoverability (`djust[auth]` is self-documenting in `pyproject.toml`) and for future-proofing if `auth` gains its own optional dependencies. For now, `djust[auth]` has no deps beyond core djust.

### Why is the extra `djust[admin]` but the module `djust.admin_ext`?

Django already ships `django.contrib.admin`. A module named `djust.admin` would collide at import time with any project that mixes `django.contrib.admin` and `djust` (which is nearly all of them). `djust.admin_ext` (ext = "extensions") is the safe name that avoids the collision. The extra is still spelled `djust[admin]` because the package-level name is short and intuitive; only the Python module picks up the `_ext` suffix.

### Why isn't `djust.tenants` in `INSTALLED_APPS`?

`djust.tenants` is a library, not a Django app — it ships `TenantMiddleware` and schema-isolation helpers but doesn't register models, admin, or management commands of its own. Register the middleware in `MIDDLEWARE`; leave `INSTALLED_APPS` alone. (In the old `djust-tenants` package, `djust_tenants` was registered as a Django app with an AppConfig; that AppConfig was removed during consolidation because nothing in the module needed it.)

### What about the `djust.tenants` backend deps?

Some tenant resolvers need a backend-specific library (Redis, psycopg). Use the sub-extras:

- `djust[tenants-redis]` — pulls in `redis>=5.0.0,<7`.
- `djust[tenants-postgres]` — pulls in `psycopg[binary]>=3.1,<4`.

Or install the library yourself if you already have it pinned at a different version.

### Why "Path A" (tag-only, no PyPI publish)?

Path A avoids publishing 5 new PyPI releases whose only content is a `DeprecationWarning`. Existing installations stay untouched; new projects discover the extras via `djust[...]`. Less noise, same safety.

### My sibling repo's `src/djust_<name>/` still has real source files (`mixins.py`, `views.py`, ...) — aren't they dead code?

Yes — that's tracked tech-debt. The `__init__.py` shim re-exports from `djust.<name>` and the other files aren't imported from anywhere. A future housekeeping pass will delete them; today they're harmless.

## See also

- [ADR-007: Package taxonomy and consolidation strategy](../../adr/007-package-taxonomy-and-consolidation.md)
- `docs/website/releases/v0.5.0.md` — the original consolidation phase
- Each sibling repo's `MIGRATION.md` for historical context
