"""LVN-I PR-2 gate test: ``ViewRuntime`` accepts a ``renderer_factory`` kwarg.

Tests for ADR-019 Iteration I, PR-2. This is the plumbing PR — the
``renderer_factory`` field exists on ``ViewRuntime`` but is not yet
consulted at dispatch time (that's PR-3's handshake wiring). Defaults
to ``None`` for full back-compat with existing call sites.

See:
- ``docs/adr/019-liveview-native.md`` §"Three layers" §1
- ``docs/adr/016-transport-runtime-interface.md`` (`ViewRuntime` baseline)
"""

from __future__ import annotations

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-runtime-renderer",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()

from unittest.mock import MagicMock


class TestViewRuntimeRendererFactoryParam:
    """``ViewRuntime.__init__`` accepts ``renderer_factory`` as a kwarg
    and stores it on the instance for PR-3 (handshake) to consume.
    """

    def test_default_is_none_for_back_compat(self):
        from djust.runtime import ViewRuntime

        runtime = ViewRuntime(transport=MagicMock())
        assert runtime.renderer_factory is None

    def test_factory_is_stored_when_provided(self):
        from djust.renderers import HtmlRenderer
        from djust.runtime import ViewRuntime

        runtime = ViewRuntime(transport=MagicMock(), renderer_factory=HtmlRenderer)
        assert runtime.renderer_factory is HtmlRenderer

    def test_existing_callers_unchanged(self):
        """SSE + WS construction sites pass ``transport`` positionally and
        ``rate_limiter`` / ``scope`` as kwargs. The new ``renderer_factory``
        kwarg must not break them.
        """
        from djust.runtime import ConnectionRateLimiter, ViewRuntime

        runtime = ViewRuntime(
            transport=MagicMock(),
            scope={"client": ("127.0.0.1", 12345)},
            rate_limiter=ConnectionRateLimiter(),
        )
        assert runtime.renderer_factory is None
        assert runtime.scope == {"client": ("127.0.0.1", 12345)}
