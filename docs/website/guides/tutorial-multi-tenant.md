---
title: "Tutorial: Build a multi-tenant SaaS dashboard"
slug: tutorial-multi-tenant
section: guides
order: 56
level: advanced
description: "Spin up a SaaS dashboard where every customer is on their own subdomain (acme.example.com, contoso.example.com), querysets are auto-scoped to the right tenant, and one user can never see another tenant's data — even if they tamper with URLs. Uses TenantScopedMixin for the hard part: defense against accidental leaks."
---

# Tutorial: Build a multi-tenant SaaS dashboard

Multi-tenancy is the single hardest invariant in a SaaS app: a
user from Acme Corp must never, under any circumstances, see a
single row that belongs to Contoso Ltd. The same code paths
(dashboard, search, billing) serve both tenants, but every query
has to filter by the current tenant and every state field has to
isolate per-tenant.

Roll-your-own multi-tenant starts as a `if request.user.tenant !=
obj.tenant: raise 403` sprinkle and ends as an audit nightmare —
one missed filter and a customer sees data they shouldn't. djust
ships `TenantScopedMixin` to make the safe pattern the default
and the unsafe pattern hard to write.

By the end of this tutorial you'll have:

- A SaaS dashboard at `acme.example.com/dashboard/` and
  `contoso.example.com/dashboard/` — same view, different data.
- **Subdomain-based tenant resolution**: a small resolver reads the
  request host, looks up the tenant, and the framework sets
  `self.tenant` before `mount()` runs.
- A **`get_tenant_queryset()` helper** that filters every model
  query by the current tenant, so the scoped query is the easy one
  to write.
- **Defense against URL tampering**: alice can't load
  `/dashboard/?account_id=999` to see contoso's account 999.

| You'll learn | Documented in |
|---|---|
| Tenant resolution with a custom resolver | [Multi-Tenant](multi-tenant.md) |
| `TenantScopedMixin` + `self.tenant` + `self.get_tenant_queryset(Model)` | [Multi-Tenant](multi-tenant.md) |
| A per-tenant membership check in `check_permissions()` | [Multi-Tenant](multi-tenant.md) |
| Three subtle leak vectors most homegrown impls hit | This tutorial |

> **Prerequisites:** [Quickstart](../getting-started/first-liveview.md), [authentication
> tutorial](tutorial-authentication.md), and a Django project
> with at least one model that should be tenant-scoped (an `Account`,
> `Project`, `Document`, etc.). Multiple subdomains pointed at your
> dev server (covered in Step 1).

---

## Step 1 — Tenant resolution + dev DNS

djust resolves the tenant from the request before `mount()` runs.
The built-in `subdomain` resolver returns just the subdomain string
(`"acme"`). This tutorial wants the tenant's display name and plan
as well, and a 404 for a subdomain that isn't a customer, so it uses
a small custom resolver that looks the tenant up:

```python
# myapp/tenancy.py
from djust.tenants.resolvers import TenantInfo

from .models import Tenant

TENANT_DOMAINS = ("example.com", "localhost")


def resolve_tenant(request):
    host = request.get_host().split(":")[0]
    for domain in TENANT_DOMAINS:
        if host.endswith("." + domain):
            slug = host[: -len(domain) - 1]
            break
    else:
        return None
    tenant = Tenant.objects.filter(slug=slug).first()
    if tenant is None:
        return None  # unknown subdomain: 404 before mount()
    return TenantInfo(
        tenant_id=tenant.slug,
        name=tenant.name,
        settings={"plan": tenant.plan},
        raw=tenant,
    )
```

```python
# settings.py
DJUST_CONFIG = {
    "TENANT_RESOLVER": "custom",
    "TENANT_CUSTOM_RESOLVER": "myapp.tenancy.resolve_tenant",
}

ALLOWED_HOSTS = [".example.com", ".localhost"]
```

`TENANT_REQUIRED` defaults to `True`, so when the resolver returns
`None` (`www.example.com`, or a subdomain with no `Tenant` row) the
view 404s before `mount()` is called.

For local dev, point the subdomains at localhost:

```bash
# /etc/hosts (or use dnsmasq for *.localhost on macOS / Linux)
127.0.0.1   acme.localhost
127.0.0.1   contoso.localhost
```

Visit `acme.localhost:8000` and `contoso.localhost:8000`. It's the
same Django process, but the resolver gives each request a different
tenant.

---

## Step 2 — The Tenant model + tenant-scoped models

```python
# myapp/models.py
from django.conf import settings
from django.db import models


class Tenant(models.Model):
    slug = models.SlugField(unique=True)  # "acme", "contoso"
    name = models.CharField(max_length=200)
    plan = models.CharField(max_length=20, default="free")


class Account(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE,
                               related_name="accounts")
    name = models.CharField(max_length=200)
    balance_cents = models.BigIntegerField(default=0)

    class Meta:
        # Indexes that always include the tenant make the scoped
        # queries cheap.
        indexes = [models.Index(fields=["tenant", "name"])]


class Membership(models.Model):
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=[
        ("admin", "Admin"), ("member", "Member"),
    ])

    class Meta:
        unique_together = [("tenant", "user")]
```

**Every tenant-scoped model has `tenant = ForeignKey(Tenant)`.**
The view below tells `TenantScopedMixin` to filter on
`tenant__slug`, because the resolved tenant's id is the slug. (The
mixin's default `tenant_field` is `tenant_id`, which suits a model
that stores the tenant id string directly.)

---

## Step 3 — The view, with TenantScopedMixin

```python
# myapp/views.py
from django.core.exceptions import PermissionDenied

from djust import LiveView, action, state
from djust.tenants import TenantScopedMixin

from .models import Account, Membership


class DashboardView(TenantScopedMixin, LiveView):
    template_name = "dashboard.html"
    login_required = True
    tenant_field = "tenant__slug"

    accounts = state(default_factory=list)
    selected_id = state(0)

    def check_permissions(self, request):
        # Is THIS user a member of THIS tenant? Without this check, a
        # logged-in user could visit any tenant's subdomain and see
        # that tenant's rows. check_permissions() runs before the
        # tenant is resolved on the WebSocket path, so resolve it here.
        tenant = self._tenant or self.resolve_tenant(request)
        if tenant is None or not Membership.objects.filter(
            tenant__slug=tenant.id, user=request.user,
        ).exists():
            raise PermissionDenied("Not a member of this tenant")
        return True

    def mount(self, request, **kwargs):
        # self.tenant was set by TenantScopedMixin before mount().
        self._refresh()

    def _refresh(self):
        # Filters by the current tenant:
        # Account.objects.filter(tenant__slug=self.tenant.id)
        qs = self.get_tenant_queryset(Account).order_by("name")
        self.accounts = [
            {"id": a.id, "name": a.name,
             "balance": f"{a.balance_cents / 100:.2f}"}
            for a in qs
        ]

    @action
    def select_account(self, account_id: int = 0, **kwargs):
        # Even with a tampered account_id, the lookup is scoped to the
        # current tenant: DoesNotExist for any account in another tenant.
        try:
            account = self.get_tenant_object(account_id, Account)
        except Account.DoesNotExist:
            return  # silently ignore; log and alert in prod
        self.selected_id = account.id
```

Three protective layers in this view:

1. **`login_required = True`**: page-level auth (covered
   in the [authentication tutorial](tutorial-authentication.md)).
2. **Membership check in `check_permissions()`**: is THIS user a
   member of THIS tenant? Without it, alice@acme could log into
   `contoso.localhost` and see Contoso's data. The framework can't
   infer this rule; it depends on your `Membership` model. Put it in
   `check_permissions()`, not `mount()` or `dispatch()`: it runs on
   every transport before `mount()`.
3. **`self.get_tenant_queryset(Account)` and
   `self.get_tenant_object(pk, Account)`**: every DB query for
   tenant-scoped models goes through these.

---

## Step 4 — The template

```html
<!-- myapp/templates/dashboard.html -->
<div dj-root>
<header class="dash-head">
  <h1>{{ tenant.name }} dashboard</h1>
  <p>{{ tenant.settings.plan|capfirst }} plan</p>
</header>

<section class="dash-accounts">
  <h2>Accounts</h2>
  <ul>
    {% for account in accounts %}
      <li dj-key="{{ account.id }}" class="account {% if account.id == selected_id %}is-selected{% endif %}">
        <button type="button" dj-click="select_account"
                data-account-id="{{ account.id }}">
          {{ account.name }}
          <span class="balance">${{ account.balance }}</span>
        </button>
      </li>
    {% endfor %}
  </ul>
</section>
</div>
```

`dj-root` on the outer `<div>` marks the reactive region. djust
stamps `dj-view` onto it when it renders the page, and the client
connects to that element; without it the page renders but never
connects, and clicking an account does nothing.

`tenant` is added to the template context by `TenantScopedMixin`
(it's the `TenantInfo` the resolver returned), so the dashboard
header renders `{{ tenant.name }}` and gets the right value for
whichever subdomain the user came in on.

---

## Three subtle leak vectors most homegrown impls hit

If you're writing your own multi-tenancy from scratch, these are
the three places you'll leak data:

### 1. The "I forgot the filter" leak

```python
# Looks fine. Returns ALL accounts across ALL tenants.
recent_accounts = Account.objects.order_by("-created_at")[:10]
```

The fix: query through `self.get_tenant_queryset(Account)` in views.
For code outside a view, `djust.tenants.TenantManager` scopes
`Model.objects` to the tenant bound by `TenantMiddleware`, and
returns no rows at all when no tenant is bound, so a forgotten
filter fails closed instead of leaking. See
[Multi-Tenant](multi-tenant.md).

### 2. The "ID-based URL tampering" leak

```python
# Looks like a normal handler. URL: /accounts/999/edit
def get_account(self, account_id: int = 0, **kwargs):
    return Account.objects.get(pk=account_id)  # ← any tenant's row
```

The fix: always go through `self.get_tenant_object(pk, Account)` (or
`self.get_tenant_queryset(Account).get(pk=...)`). Tampering with the
URL gets `DoesNotExist`, not a foreign tenant's row.

### 3. The "shared cache key" leak

```python
from django.core.cache import cache
from django.db.models import Sum

# Cache key without the tenant → every tenant gets the first one's total
def get_summary(self):
    summary = cache.get("dashboard_summary")
    if summary is None:
        summary = self.get_tenant_queryset(Account).aggregate(
            total=Sum("balance_cents"))
        cache.set("dashboard_summary", summary, 60)
    return summary
```

The fix: include the tenant in every cache key, for example
`f"tenant:{self.tenant.id}:dashboard_summary"`. djust doesn't add
it for you.

---

## What just happened, end to end

```
   Browser                                 Server
       │                                       │
       │ GET acme.example.com/dashboard/       │
       │ ─────────────────────────────────► resolve_tenant()
       │                                       │   → TenantInfo("acme")
       │                                       │ user authenticated? yes
       │                                       │ check_permissions()
       │                                       │   member of acme? yes
       │                                       │ DashboardView.mount()
       │                                       │   get_tenant_queryset(Account)
       │                                       │     → WHERE tenant.slug='acme'
       │                                       │   self.accounts = [...]
       │ ◄ render dashboard.html ──────────────│
       │                                       │
       │                                       │
   Other browser                              Server
       │                                       │
       │ GET contoso.example.com/dashboard/    │
       │ ─────────────────────────────────► resolve_tenant()
       │                                       │   → TenantInfo("contoso")
       │                                       │ ... membership check
       │                                       │ DashboardView.mount()
       │                                       │   get_tenant_queryset(Account)
       │                                       │     → WHERE tenant.slug='contoso'
       │                                       │ (different rows, same view code)
       │ ◄ render dashboard.html ──────────────│
```

Two tenants, one process, one set of view code. The
`get_tenant_queryset()` filter and the membership check are the
only guardrail code you write.

---

## Where to go next

- **Other resolvers:** subdomain isn't the only option. The
  built-in `subdomain`, `path` (`example.com/acme/`), `header`
  (`X-Tenant-ID: acme`) and `session` resolvers, and chains of them,
  are covered in the [Multi-Tenant guide](multi-tenant.md).
  Pick the one that matches your URL scheme.
- **View state:** view state is keyed by the session and the page
  path, not by tenant. If one session can reach several tenants on
  the **same URL** (a header or session resolver), put the tenant in
  the URL or re-derive tenant data in the handler. The [Multi-Tenant
  guide](multi-tenant.md) explains why.
- **Tenant-aware presence:** combine with the [presence
  tutorial](tutorial-presence.md). A view with both
  `TenantMixin` and `PresenceMixin` stores its presence under
  `tenant:<id>:<key>`, so two tenants viewing the "same" doc URL
  never see each other.
- **Audit log:** wrap every `@action` in a decorator that records
  `(actor, tenant, action, target_id, timestamp)` to a separate
  audit DB. The tenant context makes per-tenant audit trails
  trivial.
- **Cross-tenant admin views:** for ops/support staff, build a
  separate set of views that DON'T inherit `TenantScopedMixin`
  — those rare paths explicitly opt out of tenant scoping. Mark
  them clearly and audit them on every PR.

The four-line shape — `TenantScopedMixin` + `self.tenant` +
`self.get_tenant_queryset(Model)` + the membership check — is the
entire daily-driver multi-tenant API. Once `get_tenant_queryset` is
muscle memory, "did I forget to filter?" stops being a question
you ask yourself.
