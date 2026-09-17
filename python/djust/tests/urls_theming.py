"""A URLconf that mounts the theming gallery, for tests.

The test project's own ``ROOT_URLCONF`` does not include ``djust.theming.urls``,
so the gallery templates' ``{% url 'djust_theming:gallery' %}`` topbar links
raise ``NoReverseMatch`` before anything under test gets a chance to run. This
is the smallest urlconf that makes those names resolve; tests point at it with
``override_settings(ROOT_URLCONF=...)``.
"""

from django.urls import include, path

urlpatterns = [
    path("theme/", include("djust.theming.urls")),
]
