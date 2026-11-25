"""An ordinary Django model: nothing in this file knows about djust."""

from django.conf import settings
from django.db import models


class TicketQuerySet(models.QuerySet):
    def visible_to(self, user):
        """Staff with ``change_ticket`` see every ticket; everyone else sees their own."""
        if user.has_perm("djust_workflow.change_ticket"):
            return self
        return self.filter(created_by=user)


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        CLOSED = "closed", "Closed"

    class Priority(models.TextChoices):
        NORMAL = "normal", "Normal"
        URGENT = "urgent", "Urgent"

    title = models.CharField(max_length=120)
    body = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.NORMAL)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = TicketQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return self.title
