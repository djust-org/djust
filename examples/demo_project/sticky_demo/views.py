"""Sticky LiveViews demo — app-shell pattern with two persistent widgets.

The demo wires up three pages (Dashboard, Settings, Reports) that all
embed the same layout. Two sticky LiveViews — ``AudioPlayerView`` and
``NotificationCenterView`` — survive ``live_redirect`` navigation
between Dashboard and Settings (both pages declare slots for both
stickies). The Reports page declares a slot ONLY for
``notification-center``, so navigating there demonstrates the
``djust:sticky-unmounted reason='no-slot'`` path for the audio player.

Run:
    cd examples/demo_project && make start
    Visit http://localhost:8002/sticky_demo/dashboard/
"""

from __future__ import annotations

import itertools
from typing import Any, Dict, List

from djust import LiveView
from djust.decorators import event_handler


# ---------------------------------------------------------------------------
# Sticky LiveViews
# ---------------------------------------------------------------------------


class AudioPlayerView(LiveView):
    """Persistent audio player. Survives Dashboard ↔ Settings navigation.

    Real-world wiring would stream an ``<audio>`` element — we keep the
    demo HTML-only so it runs without media assets. The ``is_playing``
    attribute is the state that must survive the nav.
    """

    sticky = True
    sticky_id = "audio-player"
    template_name = "sticky_demo/audio_player.html"

    def mount(self, request, **kwargs):
        self.track_title = "Demo Track — Ambient 1"
        self.is_playing = False
        self.elapsed_seconds = 0

    @event_handler
    def toggle_play(self, **kwargs):
        self.is_playing = not self.is_playing

    @event_handler
    def next_track(self, **kwargs):
        self.track_title = f"Demo Track — Ambient {(self.elapsed_seconds % 5) + 1}"
        self.elapsed_seconds += 1


# Module-level id counter — stable across instance lifetime; each
# notification gets a unique id for dismissal routing.
_notification_id_counter = itertools.count(1)


class NotificationCenterView(LiveView):
    """Persistent notification bell. Queue must survive navigation.

    Seeds a few notifications on mount so the demo has state to
    preserve. A real app would push notifications via a channel group
    or a pg_notify listener.
    """

    sticky = True
    sticky_id = "notification-center"
    template_name = "sticky_demo/notification_center.html"

    def mount(self, request, **kwargs):
        self.notifications: List[Dict[str, Any]] = [
            {"id": next(_notification_id_counter), "text": "Welcome to the sticky demo"},
            {
                "id": next(_notification_id_counter),
                "text": "Navigate to Settings — this widget persists",
            },
            {
                "id": next(_notification_id_counter),
                "text": "The Reports page has no audio slot — watch the player unmount",
            },
        ]

    @event_handler
    def dismiss(self, id: str = "", **kwargs):
        try:
            target = int(id)
        except (TypeError, ValueError):
            return
        self.notifications = [n for n in self.notifications if n["id"] != target]

    @event_handler
    def add_demo(self, **kwargs):
        self.notifications.append(
            {
                "id": next(_notification_id_counter),
                "text": f"Ad-hoc notification #{len(self.notifications) + 1}",
            }
        )


# ---------------------------------------------------------------------------
# Non-sticky pages
# ---------------------------------------------------------------------------


class DashboardView(LiveView):
    """Dashboard — embeds BOTH stickies and shows some metrics."""

    template_name = "sticky_demo/dashboard.html"

    def mount(self, request, **kwargs):
        self.metric_visitors = 42
        self.metric_orders = 7

    @event_handler
    def refresh_metrics(self, **kwargs):
        self.metric_visitors += 3
        self.metric_orders += 1


class SettingsView(LiveView):
    """Settings page — same stickies, different primary content."""

    template_name = "sticky_demo/settings.html"

    def mount(self, request, **kwargs):
        self.theme = "light"

    @event_handler
    def set_theme(self, value: str = "light", **kwargs):
        if value in ("light", "dark"):
            self.theme = value


class ReportsView(LiveView):
    """Reports page — embeds ONLY the notification center.

    The audio-player sticky will be dropped with
    ``reason='no-slot'`` because this page does not declare
    ``dj-sticky-slot="audio-player"``. Useful for exercising the
    unmount-on-missing-slot path end-to-end.
    """

    template_name = "sticky_demo/reports.html"

    def mount(self, request, **kwargs):
        self.rows = [
            {"month": "Jan", "revenue": 1200},
            {"month": "Feb", "revenue": 1640},
            {"month": "Mar", "revenue": 2100},
        ]
