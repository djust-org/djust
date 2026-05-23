"""LVN-I PR-3 gate test: handshake-driven renderer selection.

Tests for ADR-019 Iteration I, PR-3. The ``LiveViewConsumer`` reads
``?platform=`` from the WS scope's query string, looks the value up in
the ``RENDERERS`` registry, and passes the resolved factory to
``ViewRuntime``. Unknown / missing values fall through to the
HtmlRenderer default at dispatch.

See:
- ``docs/adr/019-liveview-native.md`` §"Three layers" §1-§2
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
        SECRET_KEY="test-secret-key-renderer-handshake",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
    )
    django.setup()


class TestRendererRegistry:
    def test_html_is_registered(self):
        from djust.renderers import RENDERERS, HtmlRenderer

        assert RENDERERS["html"] is HtmlRenderer

    def test_get_renderer_factory_returns_html(self):
        from djust.renderers import HtmlRenderer, get_renderer_factory

        assert get_renderer_factory("html") is HtmlRenderer

    def test_get_renderer_factory_none_for_missing(self):
        from djust.renderers import get_renderer_factory

        assert get_renderer_factory(None) is None
        assert get_renderer_factory("") is None

    def test_get_renderer_factory_none_for_unknown(self):
        """Unknown platform values fall through to None (not raise) —
        the WS handshake never errors on a typo; renders HTML instead.
        """
        from djust.renderers import get_renderer_factory

        # NOTE: ``swiftui`` and ``compose`` ARE registered as of LVN-II
        # PR-2 (scaffold). See test_native_renderer_scaffold.py for
        # those positive cases.
        assert get_renderer_factory("not-a-real-platform") is None
        assert get_renderer_factory("react-native") is None  # never registered


class TestRegistryFactoryParameterShape:
    """Sanity-check the registry returns things that look like the
    ``Renderer`` Protocol — factory(view) → instance with ``output_format``.
    """

    def test_html_factory_constructs_renderer(self):
        from unittest.mock import MagicMock

        from djust.renderers import Renderer, get_renderer_factory

        factory = get_renderer_factory("html")
        renderer = factory(view=MagicMock())
        assert isinstance(renderer, Renderer)
        assert renderer.output_format == "html"
