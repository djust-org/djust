"""URL configuration for the #1758 route-map auth-filter tests (ADR-021 Stage 2).

Covers every gating form the filter must detect, plus a public control:

* public LiveView (no auth) — always emitted;
* ``login_required = True`` class attr (djust convention);
* ``permission_required`` class attr (string);
* ``login_required(View.as_view())`` decorator wrap (``__wrapped__`` branch).

Referenced via ``@override_settings(ROOT_URLCONF="tests.route_map_auth_test_urls")``.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import path

from djust import LiveView


class PublicView(LiveView):
    template = '<div dj-root dj-view="tests.route_map_auth_test_urls.PublicView">public</div>'


class LoginAttrView(LiveView):
    """Gated via the djust ``login_required`` class attribute."""

    login_required = True
    template = '<div dj-root dj-view="tests.route_map_auth_test_urls.LoginAttrView">login</div>'


class PermAttrView(LiveView):
    """Gated via the djust ``permission_required`` class attribute."""

    permission_required = "auth.view_user"
    template = '<div dj-root dj-view="tests.route_map_auth_test_urls.PermAttrView">perm</div>'


class DecoratorGatedView(LiveView):
    """Gated by wrapping ``as_view()`` in Django's ``login_required`` decorator."""

    template = '<div dj-root dj-view="tests.route_map_auth_test_urls.DecoratorGatedView">deco</div>'


class MixinGatedView(LoginRequiredMixin, LiveView):
    """Gated via Django's stdlib ``LoginRequiredMixin`` (sets no class attr)."""

    template = '<div dj-root dj-view="tests.route_map_auth_test_urls.MixinGatedView">mixin</div>'


urlpatterns = [
    path("public/", PublicView.as_view(), name="public"),
    path("login-attr/", LoginAttrView.as_view(), name="login-attr"),
    path("perm-attr/", PermAttrView.as_view(), name="perm-attr"),
    path("deco/", login_required(DecoratorGatedView.as_view()), name="deco"),
    path("mixin/", MixinGatedView.as_view(), name="mixin"),
]
