"""URL configuration for djust.theming critical-CSS tests.

Tests in ``python/djust/tests/test_theming_critical_css.py`` reference
``ROOT_URLCONF="tests.test_critical_css"`` via ``@override_settings``.
This file mounts the theming URLs at ``/djust-theming/`` to match the
test expectations.
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("djust-theming/", include("djust.theming.urls")),
]
