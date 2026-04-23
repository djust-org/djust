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
| `djust-admin` | v99.0.0 | `djust[admin]` |

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
from djust_auth import RoleRequiredMixin
from djust_tenants import TenantMiddleware
from djust_theming.palette import Palette
from djust_components.data import TableComponent

# After
from djust.auth import RoleRequiredMixin
from djust.tenants import TenantMiddleware
from djust.theming.palette import Palette
from djust.components.data import TableComponent
```

A mechanical sed script handles the 90% case:

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
    -e 's/from djust_admin/from djust.admin/g' \
    -e 's/import djust_admin/import djust.admin/g' \
    {} +
```

Review the diffs (`grep -r 'djust_' . --include='*.py'` for stragglers) before deleting `.bak` files.

### 3. Update Django `INSTALLED_APPS`

The app labels change from `djust_<name>` to `djust.<name>`:

```python
# Before
INSTALLED_APPS = [
    "djust",
    "djust_auth",
    "djust_tenants",
    "djust_components",
]

# After
INSTALLED_APPS = [
    "djust",
    "djust.auth",
    "djust.tenants",
    "djust.components",
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
