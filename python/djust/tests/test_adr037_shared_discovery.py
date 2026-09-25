"""ADR-037 D1: one discovery of the handlers a class declares.

``_parameter_metadata.declared_handlers`` is what dispatch's ``_event_methods``
resolves through, and what every check and tool that lists handlers calls. These
tests pin that the class-level answer equals the runtime's answer on an instance,
and that the retired independent derivations stay deleted.
"""

from __future__ import annotations

import gc

import pytest

from djust import LiveView
from djust._parameter_metadata import (
    _event_methods,
    component_stop,
    declared_handlers,
    view_stop,
)
from djust.decorators import event_handler, server_function


@pytest.fixture
def collect():
    """Unload classes defined in a test so no later check run sees them."""
    yield
    gc.collect()


def _names(cls, stop=view_stop, **kwargs):
    return [h.name for h in declared_handlers(cls, stop, **kwargs)]


def test_nearest_declaration_wins_and_shadows_a_farther_handler(collect):
    class Base:
        @event_handler
        def save(self):
            pass

        @event_handler
        def load(self):
            pass

    class Child(Base):
        # A plain attribute shadows the inherited handler, as dispatch resolves it.
        load = None

        @event_handler
        def save(self):
            pass

    handlers = {h.name: h for h in declared_handlers(Child)}
    assert list(handlers) == ["save"]
    assert handlers["save"].owner is Child
    assert handlers["save"].function is Child.__dict__["save"]


def test_inherited_handler_reports_its_declaring_class(collect):
    class Base:
        @event_handler
        def save(self):
            pass

    class Child(Base):
        pass

    (handler,) = declared_handlers(Child)
    assert handler.owner is Base


def test_static_and_class_methods_resolve_to_their_function(collect):
    class View:
        @staticmethod
        @event_handler
        def ping():
            pass

        @classmethod
        @event_handler
        def pong(cls):
            pass

    handlers = {h.name: h for h in declared_handlers(View)}
    assert set(handlers) == {"ping", "pong"}
    assert isinstance(handlers["ping"].member, staticmethod)
    assert handlers["ping"].function is View.__dict__["ping"].__func__


def test_private_names_and_undecorated_methods_are_not_handlers(collect):
    class View:
        @event_handler
        def _hidden(self):
            pass

        def helper(self):
            pass

        @event_handler
        def visible(self):
            pass

    assert _names(View) == ["visible"]


def test_server_functions_only_when_asked(collect):
    class View:
        @server_function
        def rpc(self):
            pass

        @event_handler
        def click(self):
            pass

    assert _names(View) == ["click"]
    assert sorted(_names(View, server_functions=True)) == ["click", "rpc"]


def test_stop_ends_the_walk(collect):
    class Framework:
        @event_handler
        def framework_handler(self):
            pass

    class App(Framework):
        @event_handler
        def app_handler(self):
            pass

    assert _names(App, lambda klass: klass is Framework) == ["app_handler"]


def test_component_stop_matches_the_framework_bases():
    from djust.components.base import LiveComponent

    assert component_stop(LiveComponent)
    assert not component_stop(object)


def test_class_discovery_equals_dispatch_on_an_instance(collect):
    class Mixin:
        @event_handler
        def from_mixin(self):
            pass

        @event_handler
        def overridden(self):
            pass

    class SharedDiscoveryView(Mixin, LiveView):
        template = "<div dj-root></div>"
        overridden = None

        @event_handler
        def own(self):
            pass

        @staticmethod
        @event_handler
        def static(self_less=None):
            pass

    view = SharedDiscoveryView()
    assert set(_event_methods(view)) == set(_names(SharedDiscoveryView))
    assert {"from_mixin", "own", "static"} <= set(_names(SharedDiscoveryView))
    assert "overridden" not in _names(SharedDiscoveryView)


def test_the_check_side_mirror_is_retired():
    from djust.checks import parameters

    assert not hasattr(parameters, "_declared_handlers")


def test_audit_lists_the_shared_discovery_minus_framework_surface(collect):
    from djust.management.commands.djust_audit import _get_handler_metadata

    class Framework:
        @event_handler
        def framework_handler(self):
            pass

        @event_handler
        def overridable(self):
            pass

    class AppBase(Framework):
        @event_handler
        def overridable(self):
            pass

    class App(AppBase):
        @event_handler
        def zeta(self):
            pass

        @event_handler
        def alpha(self):
            pass

    names = [name for name, _meta in _get_handler_metadata(App, base_classes=[Framework])]
    # Sorted; the framework's own handler is skipped; an application class's
    # override of it is the application's handler.
    assert names == ["alpha", "overridable", "zeta"]
    assert [n for n, _ in _get_handler_metadata(App)] == [
        "alpha",
        "framework_handler",
        "overridable",
        "zeta",
    ]
    assert all("event_handler" in meta for _n, meta in _get_handler_metadata(App))
