"""Built-in djust framework routes (dev/debug tooling).

Usage (from a project's urls.py):

    urlpatterns = [
        path("", include("djust.urls")),
        # ... your routes
    ]

Currently ships:
    - ``/__djust__/replay/<blob>`` — read-only bug-capture replay viewer
      (B7 iter B, #1562). See ``djust.bug_capture`` +
      ``docs/website/guides/bug-capture.md``.

DEBUG-gated at TWO layers, mirroring the pattern documented in
``djust.observability``'s module docstring: the ``urlpatterns`` list
below OMITS the route entirely when ``DEBUG=False`` and the production
opt-in isn't set, so a stray ``include("djust.urls")`` costs nothing at
import time in production; AND the view itself
(``djust.bug_capture_views.replay_view``) re-checks the identical gate,
so the route stays refused (404) even if some other codepath calls the
view directly, bypassing URL resolution. Both checks read
``settings.DJUST_BUG_CAPTURE_PROD_OPT_IN`` — the same explicit,
deliberately-ugly opt-in ``djust.bug_capture._enforce_prod_gate()`` uses
for *encoding* a capture, so encode and view share one mental model:
"opted into bug-capture in prod" is a single decision, not two.

``urlpatterns`` is computed once at import time from the settings that
are live at that moment (same as every other Django project urlconf —
Django caches the resolved URLconf module). Tests that need to exercise
both branches of the DEBUG gate reload this module under
``override_settings`` — see
``python/djust/tests/test_bug_capture_urls.py``.
"""

from django.conf import settings
from django.urls import path

from djust.bug_capture_views import replay_view

app_name = "djust"


def _prod_opt_in() -> bool:
    return getattr(settings, "DJUST_BUG_CAPTURE_PROD_OPT_IN", False) is True


urlpatterns = []

if settings.DEBUG or _prod_opt_in():
    urlpatterns.append(
        path("__djust__/replay/<str:blob>", replay_view, name="bug_capture_replay"),
    )

__all__ = ["urlpatterns"]
