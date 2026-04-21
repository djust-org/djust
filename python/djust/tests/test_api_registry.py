"""Tests for djust.api.registry (ADR-008)."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from djust.api.registry import (
    _derive_slug,
    _has_exposed_handler,
    get_api_view_registry,
    iter_exposed_handlers,
    register_api_view,
    reset_registry,
    resolve_api_view,
)
from djust.decorators import event_handler
from djust.live_view import LiveView


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


def test_explicit_api_name_wins_over_derivation():
    class V(LiveView):
        api_name = "myapp.inventory"

        @event_handler(expose_api=True)
        def ping(self, **kwargs):
            return "pong"

    assert _derive_slug(V) == "myapp.inventory"


def test_derived_slug_uses_app_label_and_class_name():
    class AnotherView(LiveView):
        @event_handler(expose_api=True)
        def noop(self, **kwargs):
            return None

    # __module__ at test collection is "python.djust.tests.test_api_registry"
    slug = _derive_slug(AnotherView)
    assert slug.endswith(".anotherview")


def test_has_exposed_handler_detects_flag():
    class Exposed(LiveView):
        @event_handler(expose_api=True)
        def go(self, **kwargs):
            return 1

    class Hidden(LiveView):
        @event_handler
        def go(self, **kwargs):
            return 1

    assert _has_exposed_handler(Exposed) is True
    assert _has_exposed_handler(Hidden) is False


def test_duplicate_slug_raises():
    class A(LiveView):
        api_name = "dup"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return 1

    class B(LiveView):
        api_name = "dup"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return 2

    with pytest.raises(ImproperlyConfigured):
        resolve_api_view("dup")


def test_resolve_returns_none_for_unknown_slug():
    class Solo(LiveView):
        api_name = "registry.solo"

        @event_handler(expose_api=True)
        def h(self, **kwargs):
            return "ok"

    assert resolve_api_view("registry.solo") is Solo
    assert resolve_api_view("does-not-exist") is None


def test_register_api_view_duplicate_raises():
    class X(LiveView):
        pass

    class Y(LiveView):
        pass

    register_api_view("registry.explicit", X)
    # Same slug, same class — idempotent.
    register_api_view("registry.explicit", X)
    with pytest.raises(ImproperlyConfigured):
        register_api_view("registry.explicit", Y)


def test_iter_exposed_handlers_yields_expected_tuple():
    class ExposedView(LiveView):
        api_name = "registry.exposed"

        @event_handler(expose_api=True)
        def a(self, **kwargs):
            return "a"

        @event_handler
        def b(self, **kwargs):
            return "b"

    seen = [
        (slug, vc.__name__, hn)
        for (slug, vc, hn, _) in iter_exposed_handlers()
        if slug == "registry.exposed"
    ]
    assert ("registry.exposed", "ExposedView", "a") in seen
    assert not any(hn == "b" for (_, _, hn) in seen)


def test_registry_only_contains_views_with_exposed_handlers():
    class OnlyInternal(LiveView):
        api_name = "registry.internal"

        @event_handler
        def private(self, **kwargs):
            return None

    assert "registry.internal" not in get_api_view_registry()
