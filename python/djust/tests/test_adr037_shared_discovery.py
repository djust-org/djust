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


def _dir_walk_metadata(view):
    """The retired runtime walk (ADR-037 row 9), kept as the test oracle."""
    from djust._parameter_metadata import handler_metadata

    metadata = {}
    for name in dir(view):
        if name.startswith("_"):
            continue
        try:
            method = getattr(view, name)
            if callable(method) and hasattr(method, "_djust_decorators"):
                metadata[name] = handler_metadata(method)
        except (AttributeError, TypeError):
            continue
    return metadata


def test_published_metadata_equals_the_dir_walk_on_framework_and_demo_views():
    from djust._parameter_metadata import handler_metadata, published_handlers
    from djust.checks.components import _routed_liveview_classes
    from djust.checks.utils import _walk_subclasses

    classes = set(_routed_liveview_classes()) | set(_walk_subclasses(LiveView)) | {LiveView}
    compared = 0
    for cls in sorted(classes, key=lambda c: (c.__module__, c.__qualname__)):
        try:
            view = cls()
        except Exception:  # noqa: BLE001 -- a view that needs mount arguments is skipped
            continue
        try:
            expected = _dir_walk_metadata(view)
        except Exception:  # noqa: BLE001 -- the walk ran a raising property; discovery does not
            continue
        actual = {name: handler_metadata(m) for name, m in published_handlers(view).items()}
        assert list(actual) == list(expected), cls
        assert actual == expected, cls
        compared += 1
    assert compared > 50


def test_debug_panel_lists_exactly_the_handlers_dispatch_resolves(collect):
    class DebugPanelView(LiveView):
        template = "<div dj-root></div>"

        @staticmethod
        @event_handler
        def ping(**kwargs):
            pass

        @event_handler
        def click(self, **kwargs):
            pass

        @event_handler(parameter_policy="strict")
        def pick(self, item_id: int = 3) -> None:
            pass

    view = DebugPanelView()
    handlers = view.get_debug_info()["handlers"]
    assert sorted(handlers) == sorted(_event_methods(view))
    assert "ping" in handlers  # A staticmethod handler was missing before row 10.
    assert handlers["pick"]["params"] == [
        {
            "name": "item_id",
            "kind": "positional_or_keyword",
            "type": "int",
            "required": False,
            "reduced_checking": False,
        }
    ]
