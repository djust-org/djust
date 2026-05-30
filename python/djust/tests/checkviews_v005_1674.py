"""Fixture for the #1674 V005 URL-routed discovery tests.

Provides a `LiveView` subclass that is reachable ONLY via a URL route (this
module also serves as a synthetic ``ROOT_URLCONF``), so a test can verify that
``check_liveviews`` / ``_routed_liveview_classes`` discover URL-routed views —
not just classes already imported into the ``__subclasses__`` graph.
"""

from __future__ import annotations

from django.urls import path

from djust import LiveView


class RoutedAllowlistView(LiveView):
    """A trivial URL-routed LiveView used to exercise V005 discovery."""

    template_name = "checkviews_v005_routed.html"


# This module doubles as a ROOT_URLCONF for override_settings(...).
urlpatterns = [
    path("routed-1674/", RoutedAllowlistView.as_view(), name="routed_1674"),
]
