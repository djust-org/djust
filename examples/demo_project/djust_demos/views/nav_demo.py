"""
Nav Demo (#1742) — dogfoods the v1.0.2 navigation arc end-to-end.

Two plain ``djust.LiveView`` subclasses (Page A / Page B) linked by
``dj-navigate``. This exercises three v1.0.2 paths in-house so a future
regression red-bars CI before a downstream consumer hits it:

- #1733 zero-wiring route map: the route map auto-derives from the URLconf
  (these views just live in ``urls.py``) and auto-emits via
  ``{% djust_client_config %}`` in the base template. NO ``live_session()``,
  NO ``get_route_map_script()``, NO context processor is needed — that
  absence IS the dogfood of #1733.
- #1737 SSR→hydration flash parity: both pages render server-side and must
  not wholesale-replace the ``[dj-view]`` children on first hydration.
- #1738 DjustHooks/dj-hook: a small canvas widget registered once in the
  shell (see ``demos/nav_demo_base.html``) mounts on Page A and survives the
  SPA nav to Page B (which also carries the widget).
"""

from djust import LiveView
from djust.decorators import event_handler


class NavDemoPageAView(LiveView):
    """Page A of the dj-navigate dogfood flow."""

    template_name = "demos/nav_demo_a.html"

    def mount(self, request, **kwargs):
        self.page = "A"
        self.clicks = 0

    @event_handler
    def bump(self):
        self.clicks += 1


class NavDemoPageBView(LiveView):
    """Page B of the dj-navigate dogfood flow."""

    template_name = "demos/nav_demo_b.html"

    def mount(self, request, **kwargs):
        self.page = "B"
        self.clicks = 0

    @event_handler
    def bump(self):
        self.clicks += 1
