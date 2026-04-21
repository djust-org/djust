"""URL configuration for djust.theming gallery / storybook / editor tests.

Tests reference ``ROOT_URLCONF="tests.gallery_test_urls"`` via
``@override_settings`` — this file mounts the theming URLs at
``/theming/`` so ``reverse("djust_theming:gallery")`` resolves to
``/theming/gallery/`` as the tests expect.

See also ``tests/test_critical_css.py`` for a critical-CSS-specific mount.
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("theming/", include("djust.theming.urls")),
]
