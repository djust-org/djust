"""ADR-034 C4 live surface: a fixed interactive dropdown across Back navigation.

Exercised by ``tests/playwright/test_interactive_navigation.py``. The view opts
into state snapshots, so leaving it with ``dj-navigate`` and coming back with
the browser's Back button restores the menu from the server-signed snapshot:
the same identity, its open/selected state, and the view's own state.

A reconnect normally restores from the server session first; the signed
snapshot is used when that session state is gone (another worker, expiry).
``forget_saved_state`` drops it, so the browser check exercises the signed
path. It is a demo harness endpoint, not an application pattern.
"""

from django.http import JsonResponse

from djust import LiveView
from djust.components.interactive import DropdownMenu


class InteractiveNavView(LiveView):
    template_name = "demos/interactive_nav.html"
    enable_state_snapshot = True

    menu = DropdownMenu(
        label="Project",
        items=[{"label": "Edit", "value": "edit"}, {"label": "Archive", "value": "archive"}],
    )

    def mount(self, request, **kwargs):
        self.result = "none"

    @menu.on.selected
    def on_menu_selected(self, component: DropdownMenu, value: str) -> None:
        self.result = value


def forget_saved_state(request):
    """Drop this demo page's server-saved state (demo harness only)."""
    key = "liveview_/demos/interactive-nav/"
    removed = [name for name in list(request.session.keys()) if name.startswith(key)]
    for name in removed:
        del request.session[name]
    return JsonResponse({"removed": removed})
