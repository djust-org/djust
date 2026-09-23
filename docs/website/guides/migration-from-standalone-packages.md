# Migrating from standalone djust packages

**TL;DR:** Replace `pip install djust-auth` (or any sibling package) with `pip install djust[auth]`. Imports from `djust_auth` become `djust.auth`. Whether the legacy package warns you depends on which release you have installed; see [What changed](#what-changed).

## Who this applies to

You're using one or more of the legacy standalone packages:

| Standalone | Frozen at (git tag) | Latest on PyPI | Replacement |
|---|---|---|---|
| `djust-auth` | v99.0.0 | 0.3.0 | `djust[auth]` |
| `djust-tenants` | v99.0.0 | 0.3.0 | `djust[tenants]` (+ `djust[tenants-redis]` or `djust[tenants-postgres]` for backend libraries) |
| `djust-theming` | v99.0.0 | 99.0.0 | `djust[theming]` |
| `djust-components` | v99.0.0 | 99.0.0 | `djust[components]` |
| `djust-admin` | v99.0.0 | 0.3.0 | `djust[admin]` (module is `djust.admin_ext` — see note below) |

The standalone repos were frozen with a `v99.0.0` **git tag** on 2026-04-23 ([ADR-007](../../adr/007-package-taxonomy-and-consolidation.md) Phase 4, "Path A"). On PyPI, `djust-theming` and `djust-components` have a 99.0.0 shim release; `djust-auth`, `djust-tenants` and `djust-admin` stop at 0.3.0, which is the old standalone code, not a shim. Existing PyPI versions stay installable, but no new releases will ship. All future work happens in `djust[<name>]` extras.

## What changed

The consolidation that started in v0.5.0 moved each sibling package's source into `python/djust/<name>/` inside the core djust repository. As of v0.6.0:

- **Source of truth** is `djust.<name>` (e.g., `djust.auth`, `djust.tenants`).
- **Installation** is via extras (`pip install djust[auth]`).
- **Legacy imports** (`from djust_auth import X`) behave differently depending on what's installed. The v99.0.0 shim (the git tag, or PyPI for `djust-theming` / `djust-components`) re-exports from `djust.<name>` and emits a `DeprecationWarning`. The 0.3.0 PyPI releases of `djust-auth`, `djust-tenants` and `djust-admin` are the old standalone code: they import the old implementation and emit **no** warning.
- **Direct upgrades** of the standalone packages no longer happen. `pip install djust-auth --upgrade` stays at 0.3.0 (and `djust-theming` / `djust-components` at 99.0.0).

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

For `djust-tenants` users: the sub-extras only install a backend library.

```toml
# Redis client for the tenant-aware presence backend (PRESENCE_BACKEND='tenant_redis'):
"djust[tenants-redis]>=0.6.0"

# psycopg 3 (djust.tenants itself is row-level; there is no schema-per-tenant mode):
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
from djust.admin_ext.views import AdminBaseMixin   # note: module renamed to `admin_ext`; the old AdminLiveViewMixin is now AdminBaseMixin
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

The v99.0.0 shims (`djust-theming` / `djust-components` from PyPI, or any package installed from the git tag) keep old imports working but emit a `DeprecationWarning`. Running with `python -W error::DeprecationWarning` surfaces missed import sites for those:

```bash
python -W error::DeprecationWarning -m pytest
```

The 0.3.0 PyPI releases of `djust-auth`, `djust-tenants` and `djust-admin` emit no warning, so this won't find their imports. Use the `grep -r 'djust_' . --include='*.py'` check from step 2 for those, and fix every hit before removing the old standalone pins.

### 5. Remove the old standalone packages

```bash
pip uninstall djust-auth djust-tenants djust-theming djust-components djust-admin
pip install -e .[auth,tenants,theming,components,admin]
```

Verify with `pip list | grep djust` — only `djust` should remain.

## FAQ

### Will the standalone packages stop working?

**No.** Existing PyPI releases stay installable (99.0.0 for `djust-theming` / `djust-components`, 0.3.0 for `djust-auth`, `djust-tenants` and `djust-admin`). Projects that never migrate will continue to install, but the 0.3.0 packages are the old code and receive no fixes. Where the `DeprecationWarning` shim is installed, it's a nudge, not a hard deadline.

### Can I use `djust.auth` without installing the `auth` extra?

Technically yes — `python/djust/auth/` ships inside the core wheel. The extra exists mainly for discoverability (`djust[auth]` is self-documenting in `pyproject.toml`) and for future-proofing if `auth` gains its own optional dependencies. For now, `djust[auth]` has no deps beyond core djust.

### Why is the extra `djust[admin]` but the module `djust.admin_ext`?

Django already ships `django.contrib.admin`. A module named `djust.admin` would collide at import time with any project that mixes `django.contrib.admin` and `djust` (which is nearly all of them). `djust.admin_ext` (ext = "extensions") is the safe name that avoids the collision. The extra is still spelled `djust[admin]` because the package-level name is short and intuitive; only the Python module picks up the `_ext` suffix.

### Why isn't `djust.tenants` in `INSTALLED_APPS`?

`djust.tenants` is a library, not a Django app — it ships `TenantMiddleware`, resolvers, mixins and managers, with no admin or management commands of its own. Register the middleware in `MIDDLEWARE`; leave `INSTALLED_APPS` alone. (In the old `djust-tenants` package, `djust_tenants` was registered as a Django app with an AppConfig; that AppConfig was removed during consolidation.)

One exception: `DatabaseAuditBackend` needs the `AuditLog` model (app label `djust_tenants`), and no installed app provides that label after consolidation, so it can't work. Use `LoggingAuditBackend` or `CallbackAuditBackend` instead.

### What about the `djust.tenants` backend deps?

Some tenant backends need a backend-specific library (Redis, psycopg). Use the sub-extras:

- `djust[tenants-redis]` — pulls in `redis>=5.0.0,<9`.
- `djust[tenants-postgres]` — pulls in `psycopg[binary]>=3.1,<4`.

Or install the library yourself if you already have it pinned at a different version.

### Why "Path A" (tag-only, no PyPI publish)?

Path A avoids publishing new PyPI releases whose only content is a `DeprecationWarning`: the v99.0.0 git tags are the canonical frozen releases. In practice `djust-theming` and `djust-components` did get a 99.0.0 shim on PyPI, while `djust-auth`, `djust-tenants` and `djust-admin` did not (PyPI still ends at 0.3.0). Existing installations stay untouched; new projects discover the extras via `djust[...]`.

### My sibling repo's `src/djust_<name>/` still has real source files (`mixins.py`, `views.py`, ...) — aren't they dead code?

Yes — that's tracked tech-debt. The `__init__.py` shim re-exports from `djust.<name>` and the other files aren't imported from anywhere. A future housekeeping pass will delete them; today they're harmless.

## See also

- [ADR-007: Package taxonomy and consolidation strategy](../../adr/007-package-taxonomy-and-consolidation.md)
- `docs/website/releases/v0.5.0.md` — the original consolidation phase
- Each sibling repo's `MIGRATION.md` for historical context
