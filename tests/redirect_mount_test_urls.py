"""URL configuration for #1647 live_redirect_mount view-resolution tests.

Maps two djust LiveViews and one plain Django view so the server-side
``_resolve_view_path_from_url`` helper can be exercised against real URLconf
entries. Referenced via ``@override_settings(ROOT_URLCONF=...)``.
"""

from __future__ import annotations

from django.http import HttpResponse
from django.urls import path

from djust import LiveView


class RedirectSourceView(LiveView):
    template = (
        '<div dj-root dj-view="tests.redirect_mount_test_urls.RedirectSourceView">source</div>'
    )

    def mount(self, request, **kwargs):
        self.where = "source"


class RedirectTargetView(LiveView):
    template = (
        '<div dj-root dj-view="tests.redirect_mount_test_urls.RedirectTargetView">target</div>'
    )

    def mount(self, request, **kwargs):
        self.where = "target"


def plain_django_view(request):  # not a LiveView — helper must return None for this
    return HttpResponse("plain")


urlpatterns = [
    path("redirect-source/", RedirectSourceView.as_view(), name="redirect-source"),
    path("redirect-target/", RedirectTargetView.as_view(), name="redirect-target"),
    path("plain/", plain_django_view, name="plain"),
]
