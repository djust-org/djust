---
title: "Migrating from django-tenants"
slug: migrating-from-django-tenants
section: guides
order: 7.5
level: advanced
description: "Move from schema-per-tenant (django-tenants, deprecated under djust) to the supported row-level djust.tenants strategy — mental model, data migration, code, settings, rollout"
---

# Migrating from django-tenants to `djust.tenants`

[django-tenants](https://github.com/django-tenants/django-tenants) (schema-per-tenant via `SET search_path`) is **deprecated** as a multi-tenancy strategy for djust applications. See [Multi-Tenant Applications → Choosing Your Multi-Tenancy Strategy](multi-tenant.md) for *why* (the short version: every WebSocket event re-enters `TenantMainMiddleware` → `SET search_path`, and LiveView's hot path amplifies that into a Postgres connection storm — [#1556](https://github.com/djust-org/djust/issues/1556) was a real production 503).

This guide is the migration *recipe*: how to move an existing schema-per-tenant deploy onto the supported row-level [`djust.tenants`](multi-tenant.md) strategy, where every tenant-scoped row carries a `tenant_id` column and querysets filter on it.

> **Scope.** This guide assumes you have an existing django-tenants deploy (a `TENANT_MODEL`, a `Domain` model, `SHARED_APPS`/`TENANT_APPS`, `TenantMainMiddleware`, and a `DATABASE_ROUTERS` entry). If you are starting a *new* application, skip this entirely — just read the [Multi-Tenant guide](multi-tenant.md) and adopt `djust.tenants` directly.

---

## 1. Mental-model translation

The two strategies isolate tenants at different layers. Schema-per-tenant gives each tenant its own Postgres schema and switches the active schema per request; row-level keeps one schema and filters every query by a `tenant_id` column.

| django-tenants (schema-per-tenant) | `djust.tenants` (row-level) |
|---|---|
| Postgres **schema** per tenant (`SET search_path`) | A `tenant_id` **column** on each tenant-scoped table |
| `TenantMainMiddleware` (switches schema per request) | [`djust.tenants.middleware.TenantMiddleware`](#3-code-migration) (resolves `request.tenant`, no schema switch) |
| `SHARED_APPS` + `TENANT_APPS` split | **One unified `INSTALLED_APPS`** — every app lives in the single shared schema |
| `Domain` model + `TENANT_MODEL` table mapping host → schema | A **resolver** (`DJUST_CONFIG['TENANT_RESOLVER']`) mapping request → tenant id |
| `DATABASE_ROUTERS` routing to the tenant schema | **No router** — one schema, ordinary querysets filtered by `tenant_id` |
| `connection.set_tenant(...)` / `schema_context(...)` | `get_current_tenant()` thread-local + `request.tenant` (a `TenantInfo`) |
| Migrations run per-schema (`migrate_schemas`) | Ordinary Django `migrate` — one schema, one migration history |

The tenant object your code sees after migration is a `djust.tenants.resolvers.TenantInfo` with `.id`, `.name`, `.settings`, and `.raw` (the original model instance, if your resolver attaches one). Where django-tenants code read `connection.tenant`, post-migration code reads `request.tenant` or calls `get_current_tenant()`.

---

## 2. Schema-to-row data migration recipe

The one-time data move copies every tenant's schema-qualified rows into the shared (`public`) schema, stamping each row with the right `tenant_id`. Do this offline (or in a maintenance window) against a backup-verified database.

### 2a. Decide the `tenant_id` value

Pick a stable identifier that your resolver will also produce at request time — typically the django-tenants schema name or the tenant's `Domain` subdomain. That string becomes the value written into every `tenant_id` column.

### 2b. Add the columns first (nullable), backfill, then enforce

Run this as an ordinary Django migration (the model-side change is covered in [section 3](#3-code-migration)). Add `tenant_id` **nullable** first so the column can exist before it is populated:

```python
# yourapp/migrations/00XX_add_tenant_id.py
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("yourapp", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="project",
            name="tenant_id",
            # nullable for the backfill window; tightened in a later migration
            field=models.CharField(max_length=100, null=True, db_index=True),
        ),
    ]
```

### 2c. Copy schema-qualified rows into the shared schema

The core move is, per tenant schema, an `INSERT ... SELECT` into the shared-schema table with the schema name written as the literal `tenant_id`. Run this once per tenant schema (generate the statements from your tenant list):

```sql
-- For each tenant schema "acme", copy its rows into public.yourapp_project,
-- stamping tenant_id = 'acme'. Exclude the PK so new ids are assigned in the
-- shared table (see 2d for FK preservation when you must keep ids).
INSERT INTO public.yourapp_project (name, created_at, tenant_id)
SELECT name, created_at, 'acme'
FROM   acme.yourapp_project;
```

Wrap the whole sweep in a transaction so a failure on tenant N rolls back the partial copy:

```sql
BEGIN;
-- ... one INSERT...SELECT block per tenant schema ...
COMMIT;
```

### 2d. Foreign keys, unique constraints, sequences, indexes

These are where schema-per-tenant assumptions break under one shared schema. Handle each deliberately:

- **Foreign keys.** If you let the shared table assign fresh PKs (2c), you must remap FKs to the new ids. The robust approach is to migrate tables in dependency order, keeping a per-tenant `old_pk → new_pk` map (a temp table per migrated table) and rewriting child FKs through it. If you instead **preserve original ids** (`INSERT` including the PK column), original FK values stay valid — but then PKs collide across tenants, so the shared table's PK must be a **composite** or you must offset/renumber ids per tenant before the copy. Choose one strategy per table and apply it consistently to that table's whole FK subtree.
- **Cross-tenant unique constraints.** A column that was `unique=True` *within a schema* (e.g. `Project.slug`) is no longer globally unique once all tenants share a table. Replace the single-column unique with a **composite unique on `(tenant_id, <field>)`**:

  <!-- doc-snippet-check: skip -->
  ```python
  class Meta:
      constraints = [
          models.UniqueConstraint(
              fields=["tenant_id", "slug"], name="uniq_project_tenant_slug"
          ),
      ]
  ```

  Audit *every* `unique=True` / `unique_together` on tenant-scoped models and prefix it with `tenant_id`, or the first cross-tenant duplicate will fail the copy.
- **Sequences.** When you let the shared table assign new PKs, Postgres advances the shared sequence as you `INSERT` — nothing to do. When you preserve ids, advance the sequence past the maximum copied id afterward (`SELECT setval(pg_get_serial_sequence('public.yourapp_project','id'), (SELECT MAX(id) FROM public.yourapp_project));`) so future inserts don't collide.
- **Indexes.** Add a `tenant_id` index (and composite `(tenant_id, <hot-filter-col>)` indexes for your common query shapes). Every read now filters on `tenant_id`; without the index, full-table scans replace the schema's per-tenant locality. `db_index=True` on the field (2b) covers the single-column case.

### 2e. Tighten the column after backfill

Once every row has a `tenant_id`, make the column non-nullable in a follow-up migration:

```python
operations = [
    migrations.AlterField(
        model_name="project",
        name="tenant_id",
        field=models.CharField(max_length=100, db_index=True),
    ),
]
```

---

## 3. Code migration

### 3a. Add `tenant_id` to models

Give every tenant-scoped model a `tenant_id`. A shared abstract base keeps it consistent:

```python
from django.db import models


class TenantScopedModel(models.Model):
    tenant_id = models.CharField(max_length=100, db_index=True)

    class Meta:
        abstract = True


class Project(TenantScopedModel):
    name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
```

### 3b. Filter querysets by tenant

Three options, from most explicit to most automatic — all use the **real** `djust.tenants` API:

**Option A — explicit filter.** Read the current tenant and filter:

```python
from djust.tenants import get_current_tenant

tenant = get_current_tenant()          # a TenantInfo, or None
projects = Project.objects.filter(tenant_id=tenant.id)
```

**Option B — `TenantScopedMixin`.** Subclass it on your LiveView and call `get_tenant_queryset()`. Note the method is **`get_tenant_queryset`** (there is no `tenant_queryset` method):

```python
from djust import LiveView
from djust.tenants import TenantScopedMixin


class ProjectListView(TenantScopedMixin, LiveView):
    template_name = "projects.html"
    model = Project                     # used by get_tenant_queryset()
    tenant_field = "tenant_id"          # default; matches the column above

    def mount(self, request, **kwargs):
        # filtered to request.tenant; empty queryset if no tenant resolved
        self.projects = self.get_tenant_queryset()

    def get_context_data(self, **kwargs):
        return {"projects": self.projects}
```

`TenantScopedMixin` also provides `create_for_tenant(**fields)` (stamps `tenant_id` automatically) and `get_tenant_object(pk)` (tenant-scoped single-object lookup). It filters on `self.tenant_field` (default `"tenant_id"`).

**Option C — a tenant-aware manager.** Move the filtering onto the model so *all* queries are scoped by the thread-local tenant. `TenantManager` / `TenantQuerySet` filter by `tenant.raw` (the original model instance) on a relational `tenant` FK by default — pass `tenant_field` to point at your column:

```python
from djust.tenants import TenantQuerySet


class Project(TenantScopedModel):
    name = models.CharField(max_length=100)

    objects = TenantQuerySet.as_manager(tenant_field="tenant_id")
```

> **Manager note.** `TenantManager`/`TenantQuerySet` filter by `get_current_tenant().raw`, so they fit best when your resolver attaches the tenant *model instance* as `TenantInfo.raw` and `tenant_field` points at that relation. For the string-`tenant_id` shape used in this guide, Option B (`get_tenant_queryset()`, which filters by `TenantInfo.id`) is the most direct fit. To bypass scoping deliberately, `TenantManager` exposes `unscoped(reason="...")`.

### 3c. Swap the middleware

Replace `TenantMainMiddleware` with `djust.tenants.middleware.TenantMiddleware` (settings diff in [section 4](#4-settings-migration)). After the swap, `request.tenant` is a `TenantInfo` (or `None`), and `get_current_tenant()` returns the same object from thread-local storage anywhere in the request. Anywhere your old code read `connection.tenant` or used `schema_context(...)`, read `request.tenant` / `get_current_tenant()` instead.

---

## 4. Settings migration

The diff below is the typical shape. Yours will differ in app names, but the *moves* are the same: collapse the app split, swap the middleware, drop the router, set a resolver.

```python
# settings.py

# --- INSTALLED_APPS: collapse SHARED_APPS + TENANT_APPS into one list ---
# REMOVE the django-tenants split:
#   SHARED_APPS = [...]
#   TENANT_APPS = [...]
#   INSTALLED_APPS = list(SHARED_APPS) + [a for a in TENANT_APPS if a not in SHARED_APPS]
# REPLACE with a single ordinary list:
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    # ... your apps, once each ...
    "yourapp",
    # 'django_tenants' may stay installed during rollout (see section 5)
]

# --- MIDDLEWARE: swap the middleware ---
MIDDLEWARE = [
    # REMOVE:  'django_tenants.middleware.main.TenantMainMiddleware',
    "djust.tenants.middleware.TenantMiddleware",  # ADD (near the top)
    "django.middleware.security.SecurityMiddleware",
    # ... the rest unchanged ...
]

# --- REMOVE the schema router entirely ---
# DELETE: DATABASE_ROUTERS = ['django_tenants.routers.TenantSyncRouter']
# (also remove the 'django_tenants.postgresql_backend' ENGINE override —
#  use the standard 'django.db.backends.postgresql')

# --- ADD the djust.tenants resolver config ---
DJUST_CONFIG = {
    # one of: 'subdomain', 'path', 'header', 'session', 'custom'
    # (a list like ['header', 'subdomain'] chains resolvers in order)
    "TENANT_RESOLVER": "subdomain",
    "TENANT_SUBDOMAIN_EXCLUDE": ["www", "api", "admin"],
    "TENANT_MAIN_DOMAIN": "example.com",
    "TENANT_REQUIRED": True,          # 404 if no tenant resolved
    "TENANT_CONTEXT_NAME": "tenant",  # name in template context
}
```

Resolver-specific keys (`TENANT_HEADER`, `TENANT_SESSION_KEY`, `TENANT_PATH_POSITION`, `TENANT_PATH_EXCLUDE`, `TENANT_CUSTOM_RESOLVER`, `TENANT_DEFAULT`) are documented in the [Multi-Tenant guide](multi-tenant.md). The `Domain`-model → resolver mapping is the key conceptual swap: instead of a row in a `Domain` table mapping a host to a schema, the resolver derives the tenant id from the request (subdomain, path segment, header, or session). If your old `Domain` table held richer per-tenant data, keep that model and write a `'custom'` resolver (`TENANT_CUSTOM_RESOLVER = 'yourapp.tenants.resolve'`) that looks the tenant up and returns a `TenantInfo` (you can attach the model instance via `TenantInfo(tenant_id=..., raw=domain.tenant)`).

---

## 5. Rollout strategy

### Big-bang vs tenant-by-tenant

- **Big-bang** (one maintenance window): run the full data migration (section 2), deploy the new code + settings, cut over. Simplest to reason about; requires downtime sized to the largest tenant's copy.
- **Tenant-by-tenant** (dual-run): keep both stacks live, migrate one tenant's rows at a time into the shared schema, and route that tenant's traffic to the row-level path while others stay on schema-per-tenant. More moving parts, near-zero downtime. Only worth it if a single window is unacceptable.

### Keeping django-tenants installed during rollout without C014 firing

During a tenant-by-tenant rollout `django_tenants` is still in `INSTALLED_APPS`, so djust's **C014** system check will fire (it triggers when `django_tenants` is installed **OR** `TENANT_MODEL` is set, **and** `ASGI_APPLICATION` is set, **and** `TENANT_LIMIT_SET_CALLS` is unset/`False`). Two options for the rollout window:

```python
# Option 1 — suppress the check while you finish migrating:
DJUST_CONFIG = {
    # ... resolver config ...
    "suppress_checks": ["C014"],
}

# Option 2 — keep the stopgap that C014 asks for, in case any traffic is
# still on the django-tenants path:
TENANT_LIMIT_SET_CALLS = True
```

Suppress C014 only while the migration is *in progress*; remove the suppression (and `django_tenants` from `INSTALLED_APPS`) once the last tenant is on the row-level path, so the check protects you again if django-tenants is ever reintroduced.

### Verifying isolation

After cutting a tenant over, verify no rows leak across `tenant_id` boundaries before trusting the path (the [canary test in section 7](#7-canary-test-no-cross-tenant-row-leaks) automates this) — manually, resolve as tenant A and confirm a tenant-B object is invisible:

```python
from djust.tenants import set_current_tenant
from djust.tenants.resolvers import TenantInfo

set_current_tenant(TenantInfo(tenant_id="acme"))
assert not Project.objects.filter(tenant_id="globex").exists() or \
    Project.objects.filter(tenant_id="acme").filter(name="globex-only-name").count() == 0
```

---

## 6. What doesn't translate

Row-level isolation satisfies the typical "tenant A cannot see tenant B's data" requirement, but it is **not** the same guarantee as separate schemas. If you have a **hard-compliance requirement for physical/schema-level isolation** — a contractual or regulatory mandate that tenant data live in physically separate schemas or databases, audited as such — row-level `tenant_id` filtering does **not** meet it.

In that case, do **not** silently stay on the deprecated django-tenants path. [Open an issue](https://github.com/djust-org/djust/issues) describing the compliance requirement so the maintainers can engage on a supported path, rather than relying on an integration that is no longer actively tested against new framework features. Staying on a deprecated path because migration is inconvenient is the failure mode this guide exists to prevent; staying on it because of a genuine isolation requirement is a conversation to have upstream.

---

## 7. Canary test — no cross-tenant row leaks

Add a regression test that fails loudly if any tenant-scoped query returns a row belonging to another tenant. This catches a missing `tenant_id` filter on a queryset, a forgotten `unique` → composite migration, or a resolver returning the wrong id. The snippet below uses only verified `djust.tenants` symbols:

<!-- doc-snippet-check: skip -->
```python
# tests/test_tenant_isolation.py
import pytest
from djust.tenants import set_current_tenant
from djust.tenants.resolvers import TenantInfo

from yourapp.models import Project


@pytest.fixture
def two_tenants(db):
    Project.objects.create(tenant_id="acme", name="Acme Roadmap")
    Project.objects.create(tenant_id="globex", name="Globex Roadmap")
    yield
    set_current_tenant(None)  # clear thread-local between tests


@pytest.mark.django_db
def test_no_cross_tenant_rows(two_tenants):
    # Acting as 'acme', a tenant-scoped query must never surface 'globex' rows.
    set_current_tenant(TenantInfo(tenant_id="acme"))
    visible = Project.objects.filter(tenant_id="acme")
    assert visible.count() == 1
    assert all(p.tenant_id == "acme" for p in visible)
    # The other tenant's row exists, but is invisible to this scope.
    assert not visible.filter(name="Globex Roadmap").exists()


@pytest.mark.django_db
def test_scoped_mixin_isolates(two_tenants):
    # If you use TenantScopedMixin.get_tenant_queryset(), it filters on
    # TenantInfo.id via the mixin's tenant_field (default 'tenant_id').
    from djust.tenants import TenantScopedMixin

    class _Probe(TenantScopedMixin):
        model = Project
        # bypass request resolution for the test
        def __init__(self):
            self._tenant = TenantInfo(tenant_id="acme")
            self._tenant_resolved = True

    rows = _Probe().get_tenant_queryset()
    assert rows.count() == 1
    assert not rows.filter(tenant_id="globex").exists()
```

> The `_Probe` subclass sets `_tenant` directly only to skip request-based resolution inside a unit test; in production `TenantScopedMixin` populates `self._tenant` automatically from `request.tenant`.

Run it after every tenant cutover (and keep it in CI) — a row leak is the one failure mode row-level isolation can have, and a canary makes it impossible to ship silently.

---

## See also

- [Multi-Tenant Applications](multi-tenant.md) — the full `djust.tenants` reference (resolvers, state-backend isolation, presence, `TenantMixin`/`TenantScopedMixin`).
- The `C014` startup system check (`djust.C014`) warns when a django-tenants + ASGI deploy is missing `TENANT_LIMIT_SET_CALLS`, and its hint links back to this guide.
