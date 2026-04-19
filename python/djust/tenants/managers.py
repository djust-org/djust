"""Tenant-aware model managers that auto-filter by current tenant."""

from django.db import models

from .middleware import get_current_tenant


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

    def __init__(self, *args, tenant_field="tenant", **kwargs):
        """Initialize manager with tenant field name.

        Args:
            tenant_field (str): Name of the FK field to tenant model
        """
        super().__init__(*args, **kwargs)
        self.tenant_field = tenant_field

    def get_queryset(self):
        """Return queryset filtered by current tenant."""
        qs = super().get_queryset()

        # Get current tenant from thread-local
        tenant = get_current_tenant()

        if tenant:
            # Filter by tenant
            filter_kwargs = {self.tenant_field: tenant.raw}
            return qs.filter(**filter_kwargs)

        # No tenant set — strict mode returns empty queryset (safe default)
        if self._get_strict_mode():
            return qs.none()

        return qs

    def _get_strict_mode(self):
        """Check if strict mode is enabled (default: True).

        When True, queries without a tenant return empty results.
        When False, queries without a tenant return all records (backwards compat).
        """
        from django.conf import settings

        config = getattr(settings, "DJUST_TENANTS", {})
        return config.get("STRICT_MODE", True)

    def unscoped(self, reason=""):
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
    """

    def __init__(self, *args, tenant_field="tenant", **kwargs):
        super().__init__(*args, **kwargs)
        self._tenant_field = tenant_field

    def _filter_by_tenant(self):
        """Apply tenant filter to queryset."""
        tenant = get_current_tenant()
        if tenant:
            filter_kwargs = {self._tenant_field: tenant.raw}
            return self.filter(**filter_kwargs)
        return self

    def _chain(self, **kwargs):
        """Override _chain to maintain tenant filtering."""
        clone = super()._chain(**kwargs)
        # Apply tenant filter to cloned queryset
        return clone._filter_by_tenant()

    @classmethod
    def as_manager(cls, tenant_field="tenant"):
        """Create manager from this queryset.

        Args:
            tenant_field (str): Name of FK field to tenant model

        Returns:
            Manager: Manager instance
        """
        manager = models.Manager.from_queryset(cls)()
        manager._tenant_field = tenant_field
        return manager
