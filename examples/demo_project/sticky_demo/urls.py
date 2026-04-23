"""URL routes for the sticky_demo app.

All three pages share the same layout and embed the sticky widgets; the
routes are primary-content-only views. Use ``live_redirect`` from links
inside each template to navigate without full reloads — that is the
path sticky preservation activates on.
"""

from django.urls import path

from .views import DashboardView, ReportsView, SettingsView

app_name = "sticky_demo"

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path("reports/", ReportsView.as_view(), name="reports"),
]
