"""URL configuration for #1733 auto-derived route-map tests (ADR-021 Stage 1).

Exercises ``djust.routing.build_route_map_from_urlconf`` against a realistic
URLconf: bare LiveViews, a ``<int:id>``-parameterised LiveView, a
``login_required``-wrapped LiveView (Django CBV ``as_view()`` wrapped by a
decorator that exposes ``__wrapped__.view_class``), a plain Django view (must
be ignored), and a nested ``include()`` so the recursive prefix-accumulation
is covered.

Referenced via ``@override_settings(ROOT_URLCONF=...)``.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.urls import include, path

from djust import LiveView


class DashboardView(LiveView):
    template = '<div dj-root dj-view="tests.route_map_test_urls.DashboardView">dash</div>'

    def mount(self, request, **kwargs):
        self.where = "dashboard"


class ItemDetailView(LiveView):
    template = '<div dj-root dj-view="tests.route_map_test_urls.ItemDetailView">item</div>'

    def mount(self, request, **kwargs):
        self.item_id = kwargs.get("id")


class ProtectedView(LiveView):
    template = '<div dj-root dj-view="tests.route_map_test_urls.ProtectedView">secret</div>'

    def mount(self, request, **kwargs):
        self.where = "protected"


class NestedView(LiveView):
    template = '<div dj-root dj-view="tests.route_map_test_urls.NestedView">nested</div>'

    def mount(self, request, **kwargs):
        self.where = "nested"


def plain_django_view(request):  # not a LiveView — must NOT appear in the map
    return HttpResponse("plain")


# A nested URLconf included under a prefix to exercise recursive descent +
# prefix accumulation.
_nested_patterns = [
    path("deep/", NestedView.as_view(), name="nested"),
]

urlpatterns = [
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("items/<int:id>/", ItemDetailView.as_view(), name="item-detail"),
    # login_required wraps as_view(); the wrapper exposes the original view
    # via ``__wrapped__.view_class`` (functools.wraps), which is the second
    # resolution branch the route-map walker must handle.
    path("secret/", login_required(ProtectedView.as_view()), name="protected"),
    path("plain/", plain_django_view, name="plain"),
    path("section/", include(_nested_patterns)),
]
