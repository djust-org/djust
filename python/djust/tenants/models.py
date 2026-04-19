"""Django models for djust.tenants."""

from django.db import models


class AuditLog(models.Model):
    """Audit log entry for tenant operations."""

    timestamp = models.FloatField()
    event_type = models.CharField(max_length=100)
    action = models.CharField(max_length=200)
    tenant_id = models.CharField(max_length=100, blank=True, default="")
    user_id = models.CharField(max_length=100, blank=True, default="")
    resource = models.CharField(max_length=200, blank=True, default="")
    detail = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    severity = models.CharField(max_length=20, default="info")

    class Meta:
        app_label = "djust_tenants"
        ordering = ["-timestamp"]

    def __str__(self) -> str:
        return f"[{self.severity}] {self.event_type}: {self.action}"
