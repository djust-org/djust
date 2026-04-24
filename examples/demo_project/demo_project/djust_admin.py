"""Demo djust admin registrations — exercises v0.7.0 widget slots +
@admin_action_with_progress.

Wires up:

- A ``DjustModelAdmin`` for ``djust_rentals.MaintenanceRequest``
- A ``@admin_action_with_progress``-decorated bulk action ("close_selected")
- A per-page ``change_form_widgets`` slot embedding a demo LiveView

This is imported by ``autodiscover`` (see ``demo_project.urls`` /
``demo_project.apps``) and demonstrates the docs/guide example end to end.
"""

from __future__ import annotations

import logging
import time

from djust import LiveView
from djust.admin_ext import (
    DjustAdminSite,
    DjustModelAdmin,
    admin_action_with_progress,
)

logger = logging.getLogger(__name__)


# Per-project admin site (does not conflict with Django's stock admin).
djust_admin_site = DjustAdminSite(name="djust_admin_demo")


class DemoProgressViewWidget(LiveView):
    """Tiny LiveView that renders next to the change form body."""

    template_name = "djust_admin/demo/progress_widget.html"
    label = "Change summary"
    size = "md"

    def mount(self, request, object_id=None, **kwargs):
        self.request = request
        self.object_id = object_id

    def get_context_data(self, **kwargs):
        return {"object_id": self.object_id}


try:
    from djust_rentals.models import MaintenanceRequest
except Exception:
    logger.debug(
        "djust_rentals.MaintenanceRequest not importable; demo admin skipped", exc_info=True
    )
    MaintenanceRequest = None


if MaintenanceRequest is not None:

    @djust_admin_site.register(MaintenanceRequest)
    class MaintenanceRequestDjustAdmin(DjustModelAdmin):
        """Demo admin that embeds a widget and exposes a background action."""

        list_display = ["__str__", "status"]
        list_filter = ["status"]
        search_fields = ["description"]

        # Per-page widget slots (v0.7.0). Each entry is a LiveView subclass.
        change_form_widgets = [DemoProgressViewWidget]
        change_list_widgets = [DemoProgressViewWidget]

        actions = ["close_selected"]

        @admin_action_with_progress(description="Close selected maintenance requests")
        def close_selected(self, request, queryset, progress):
            """Close each selected MaintenanceRequest, reporting progress.

            Runs in a daemon thread; progress is pushed to the
            BulkActionProgressWidget via ``progress.update(...)``. This
            action is **cooperatively cancellable** — we check
            ``progress.cancelled`` before each row so the user clicking
            Cancel on the progress page actually stops the loop.
            """
            total = queryset.count()
            progress.update(current=0, total=total, message="Starting…")
            for i, req in enumerate(queryset.iterator(), start=1):
                if progress.cancelled:
                    progress.update(message="Cancelled by user.")
                    return
                # Simulated work so the demo progress bar is visible.
                time.sleep(0.1)
                req.status = "closed"
                req.save(update_fields=["status"])
                progress.update(
                    current=i,
                    total=total,
                    message=f"Closed request #{req.pk}",
                )
            progress.update(message="All done.")
