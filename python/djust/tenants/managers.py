"""Tenant-aware model managers that auto-filter by current tenant."""

from typing import Any

from django.db import models

from .middleware import get_current_tenant


def _strict_mode_enabled() -> bool:
    """Return whether STRICT_MODE (fail-closed) is enabled (default: True).

    SECURITY (Finding #6): the default is fail-CLOSED. When no tenant is bound
    to the current context, tenant-scoped querysets return ``.none()`` so a
    missing tenant can never leak another tenant's rows. STRICT_MODE=False
    restores the legacy fail-OPEN behaviour (unfiltered) and is dangerous —
    djust system check ``S006`` flags it.

    When True, queries without a tenant return empty results.
    When False, queries without a tenant return all records (backwards compat).
    """
    from django.conf import settings

    config = getattr(settings, "DJUST_TENANTS", {}) or {}
    enabled: bool = config.get("STRICT_MODE", True)
    return enabled


def _scope_to_tenant(queryset: Any, tenant_field: str) -> Any:
    """Filter *queryset* to the tenant bound on the current context.

    The single source of truth for tenant scoping, shared by both
    :class:`TenantManager` and the manager produced by
    :meth:`TenantQuerySet.as_manager` so the two paths cannot drift
    (parallel-path-drift guard).

    Behaviour:
    - tenant bound      → ``queryset.filter(<tenant_field>=tenant.raw)``
    - no tenant + strict → ``queryset.none()``  (fail-closed, the safe default)
    - no tenant + lax    → ``queryset``          (fail-open, STRICT_MODE=False only)
    """
    tenant = get_current_tenant()
    if tenant:
        return queryset.filter(**{tenant_field: tenant.raw})

    # No tenant bound to this context. On the live (WS/SSE) path this used to
    # silently return all rows (cross-tenant disclosure). Fail closed unless
    # the operator has explicitly opted out via STRICT_MODE=False.
    if _strict_mode_enabled():
        return queryset.none()
    return queryset


class TenantManager(models.Manager):
    """Manager that automatically filters by current tenant.

    Usage:
        class Project(models.Model):
            tenant = models.ForeignKey('Organization', on_delete=models.CASCADE)
            name = models.CharField(max_length=200)

            objects = TenantManager()  # Auto-filters by tenant

        # In view with request.tenant set:
        projects = Project.objects.all()  # Automatically filtered

        # To bypass tenant filtering:
        all_projects = Project.objects.unscoped()
    """

    def __init__(self, *args: Any, tenant_field: str = "tenant", **kwargs: Any) -> None:
        """Initialize manager with tenant field name.

        Args:
            tenant_field (str): Name of the FK field to tenant model
        """
        super().__init__(*args, **kwargs)
        self.tenant_field = tenant_field

    def get_queryset(self) -> Any:
        """Return queryset filtered by current tenant (fail-closed under strict mode)."""
        return _scope_to_tenant(super().get_queryset(), self.tenant_field)

    def _get_strict_mode(self) -> bool:
        """Check if strict mode is enabled (default: True).

        When True, queries without a tenant return empty results.
        When False, queries without a tenant return all records (backwards compat).
        """
        return _strict_mode_enabled()

    def unscoped(self, reason: str = "") -> Any:
        """Return unfiltered queryset (bypass tenant filtering).

        Args:
            reason (str): Audit trail reason for bypassing tenant filtering.

        Usage:
            # Get all projects across all tenants
            all_projects = Project.objects.unscoped(reason="admin report")
        """
        return super().get_queryset()


class TenantQuerySet(models.QuerySet):
    """QuerySet that automatically filters by current tenant.

    Usage:
        class Project(models.Model):
            tenant = models.ForeignKey('Organization', on_delete=models.CASCADE)
            name = models.CharField(max_length=200)

            objects = TenantQuerySet.as_manager(tenant_field='tenant')

        # Supports chainable queries:
        active_projects = Project.objects.filter(is_active=True).order_by('name')

    SECURITY / behaviour notes (Finding #6):

    - Scoping is applied ONCE, in the manager's ``get_queryset()`` (see
      :meth:`as_manager`), NOT in ``_chain``. The previous ``_chain`` override
      re-entered ``self.filter(...)`` → ``_chain`` → ``_filter_by_tenant`` →
      ``self.filter(...)`` and raised ``RecursionError`` whenever a tenant was
      bound. Doing it in ``get_queryset`` filters the base queryset once and
      lets all downstream chaining (``.filter()``, ``.all()``, ``.order_by()``)
      narrow within the already-tenant-scoped set — so ``Model.objects.all()``
      is scoped too (it was unfiltered before).
    - When no tenant is bound, the manager returns ``.none()`` under STRICT_MODE
      (the default, fail-CLOSED), matching :class:`TenantManager`. The old
      ``_filter_by_tenant`` returned ``self`` unconditionally (fail-OPEN) and
      never consulted STRICT_MODE — the core of the live-path disclosure bug.
    """

    def __init__(self, *args: Any, tenant_field: str = "tenant", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._tenant_field = tenant_field

    @classmethod
    def as_manager(cls, tenant_field: str = "tenant") -> Any:
        """Create a tenant-scoping manager from this queryset.

        The returned manager applies the tenant filter (fail-closed under
        STRICT_MODE) in ``get_queryset`` — the single scoping point — so it
        cannot recurse and ``.all()`` is scoped.

        Args:
            tenant_field (str): Name of FK field to tenant model

        Returns:
            Manager: Manager instance
        """

        class _TenantQuerySetManager(models.Manager.from_queryset(cls)):  # type: ignore[misc]
            # Carried so the queryset clones (which copy _tenant_field via
            # ``_clone``) and the manager agree on the FK field name.
            _tenant_field = tenant_field

            def get_queryset(self) -> Any:
                qs = super().get_queryset()
                # Keep the field name on the queryset so chained clones built
                # from this queryset retain it.
                qs._tenant_field = self._tenant_field
                return _scope_to_tenant(qs, self._tenant_field)

            def unscoped(self, reason: str = "") -> Any:
                """Return an unfiltered queryset (bypass tenant scoping).

                The documented escape hatch for deliberate cross-tenant reads —
                available on both the ``TenantManager`` and the
                ``TenantQuerySet.as_manager()`` variants so the migration /
                S006 guidance applies uniformly. ``reason`` is an audit-trail
                string. Scoping lives only in ``get_queryset`` above, so the
                un-overridden base queryset here is genuinely unfiltered.

                Args:
                    reason (str): Audit-trail reason for bypassing the filter.
                """
                qs = super().get_queryset()
                qs._tenant_field = self._tenant_field
                return qs

        manager = _TenantQuerySetManager()
        manager._tenant_field = tenant_field
        return manager
