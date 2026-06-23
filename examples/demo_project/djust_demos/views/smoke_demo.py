"""
Browser-smoke canary demo (#1849 / #1848) — runtime-break class guard.

Two runtime/wiring breaks shipped in the 1.0.7 upgrade that the pytest suite
+ HTTP-200 smoke both passed over (#1849):

1. A LiveView refused at WS mount (stale ``dj-view`` ref rode a boundary-less
   ``startswith`` allowlist) — the page rendered server-side but never went
   live, so ``dj-click`` did nothing.
2. An inline ``<script>`` placed INSIDE the dj-root registered a delegated
   click listener that the #1610 WS-mount morph re-created without executing,
   so the handler silently never registered (#1848).

This view + its template are the smallest faithful reproduction surface for
BOTH classes, exercised by ``tests/playwright/test_browser_smoke.py``:

- ``mount`` + a ``dj-click`` counter prove the LiveView actually mounts and
  the WS round-trip works (class 1).
- the template carries an inline ``<script>`` INSIDE the dj-root wiring a
  delegated tab toggle that flips ``.active`` (class 2). When #1848 is fixed
  the toggle works; if it regresses the smoke red-bars.

CSP note: the inline-in-dj-root ``<script>`` is INTENTIONAL here — it is the
bug-shape the canary guards. Production code should keep page JS out of the
dj-root (see CLAUDE.md CSP-strict defaults + #1848).
"""

from djust import LiveView
from djust.decorators import event_handler


class BrowserSmokeView(LiveView):
    """Minimal mounting LiveView for the browser-smoke canary."""

    template_name = "demos/browser_smoke.html"

    def mount(self, request, **kwargs):
        self.clicks = 0

    @event_handler
    def bump(self):
        self.clicks += 1
