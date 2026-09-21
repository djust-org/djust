"""ADR-034 class-time subscription contracts (private staging API)."""

import inspect
import json
from unittest.mock import AsyncMock

import pytest

from djust import LiveView, event_handler
from djust.decorators import server_function
from djust._component_subscriptions import (
    ComponentDeclaration,
    OutputContract,
    compile_subscriptions,
    subscribe,
)


class Menu:
    pass


SELECTED = OutputContract("selected", (("value", str),))
TOGGLED = OutputContract("toggled", (("open", bool),))


def declaration():
    return ComponentDeclaration(Menu, (SELECTED, TOGGLED))


def selected(source):
    return lambda callback: subscribe(source, SELECTED, callback)


def test_subscriptions_compile_on_liveview_class_without_instantiating():
    class Page(LiveView):
        menu = declaration()

        @selected(menu)
        def renamed(self, component: Menu, value: str) -> None:
            pass

    bindings = compile_subscriptions(Page)
    assert len(bindings) == 1
    assert (bindings[0].component, bindings[0].output, bindings[0].callback) == (
        "menu",
        "selected",
        "renamed",
    )
    assert Page._component_subscriptions == bindings
    assert not hasattr(Page.renamed, "__wrapped__")
    assert Page.renamed.alters_data
    assert list(inspect.signature(Page.renamed).parameters) == ["self", "component", "value"]


def test_two_components_and_async_callback_preserve_symbols():
    class Page(LiveView):
        menu = declaration()
        other = declaration()

        @selected(menu)
        async def first(self, *, component: Menu, value: str) -> None:
            pass

        @selected(other)
        def second(self, component: Menu, value: str, optional: int = 1) -> None:
            pass

    assert {binding.component for binding in compile_subscriptions(Page)} == {"menu", "other"}
    assert inspect.iscoroutinefunction(Page.first)


def test_inherited_callback_override_is_revalidated_and_template_guarded():
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    class Child(Parent):
        def callback(self, component: Menu, value: str) -> None:
            pass

    assert Child.callback.alters_data
    assert compile_subscriptions(Child) == compile_subscriptions(Parent)
    with pytest.raises(TypeError, match="ChildBad.menu.selected.*callback"):

        class ChildBad(Parent):
            def callback(self, component: Menu, value: int) -> None:
                pass


def test_duplicate_pair_rejected_but_redecorated_override_allowed():
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    class Child(Parent):
        @selected(Parent.menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    assert len(compile_subscriptions(Child)) == 1
    with pytest.raises(TypeError, match="Duplicate subscription"):

        class Bad(Parent):
            @selected(Parent.menu)
            def second(self, component: Menu, value: str) -> None:
                pass


def test_alias_and_unrelated_reuse_rejected():
    source = declaration()
    with pytest.raises((TypeError, RuntimeError), match="aliased|__set_name__"):

        class Alias(LiveView):
            first = source
            second = source

    class Owner(LiveView):
        menu = declaration()

    with pytest.raises((TypeError, RuntimeError), match="reused|__set_name__"):

        class Foreign(LiveView):
            menu = Owner.menu

    with pytest.raises(TypeError, match="ForeignCallback.*foreign"):

        class ForeignCallback(LiveView):
            @selected(Owner.menu)
            def callback(self, component: Menu, value: str) -> None:
                pass


def test_replacing_declaration_revalidates_output_and_source():
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    class Child(Parent):
        menu = declaration()

    assert len(compile_subscriptions(Child)) == 1
    with pytest.raises(TypeError, match="Missing.menu.selected"):

        class Missing(Parent):
            menu = ComponentDeclaration(Menu, (TOGGLED,))

    with pytest.raises(TypeError, match="Wrong.menu.selected"):

        class Wrong(Parent):
            menu = ComponentDeclaration(str, (SELECTED,))

    with pytest.raises(TypeError, match="Removed.menu.selected"):

        class Removed(Parent):
            menu = None


@pytest.mark.parametrize(
    "decorator", [event_handler(), event_handler(expose_api=True), server_function]
)
@pytest.mark.parametrize("outer", [True, False])
def test_transport_decorator_conflicts_in_both_orders(decorator, outer):
    source = declaration()

    def callback(self, component: Menu, value: str) -> None:
        pass

    with pytest.raises(TypeError, match="subscription"):
        if outer:
            decorator(selected(source)(callback))
        else:
            selected(source)(decorator(callback))


@pytest.mark.parametrize("mode", ["open", "warn", "strict"])
def test_subscription_rejected_by_transport_even_in_permissive_mode(mode, monkeypatch):
    from djust.websocket_utils import _check_event_security
    from djust.config import config

    class Page(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    monkeypatch.setattr(
        config, "get", lambda key, default=None: mode if key == "event_security" else default
    )
    assert "subscription" in _check_event_security(Page.callback, Page(), "callback")


@pytest.mark.parametrize("kind", ["staticmethod", "classmethod", "property", "none"])
def test_non_instance_method_replacement_rejected(kind):
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    def replacement(self, component: Menu, value: str) -> None:
        pass

    replacements = {
        "staticmethod": staticmethod(replacement),
        "classmethod": classmethod(replacement),
        "property": property(replacement),
        "none": None,
    }
    with pytest.raises(TypeError, match="callback.*instance method"):
        type("Bad", (Parent,), {"callback": replacements[kind]})


def test_foreign_output_token_and_duplicate_token_rejected():
    source = declaration()

    def callback(self, component: Menu, value: str) -> None:
        pass

    with pytest.raises(TypeError, match="declared output"):
        subscribe(source, OutputContract("selected", (("value", str),)), callback)
    subscribe(source, SELECTED, callback)
    with pytest.raises(TypeError, match="Duplicate subscription"):
        subscribe(source, SELECTED, callback)


def test_untyped_callback_gets_actionable_class_time_error():
    with pytest.raises(TypeError, match="Untyped.menu.selected.*callback.*value: str"):

        class Untyped(LiveView):
            menu = declaration()

            @selected(menu)
            def callback(self, component, value):
                pass


@pytest.mark.parametrize("mode", ["open", "warn", "strict"])
@pytest.mark.asyncio
async def test_shared_dispatch_security_rejects_subscription_before_invocation(mode, monkeypatch):
    from djust.websocket_utils import _validate_event_security
    from djust.config import config

    class Page(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            raise AssertionError("A client must not reach an output callback")

    transport = AsyncMock()
    monkeypatch.setattr(
        config, "get", lambda key, default=None: mode if key == "event_security" else default
    )
    assert await _validate_event_security(transport, "callback", Page(), None) is None
    transport.send_error.assert_awaited_once()


@pytest.mark.django_db
def test_actual_http_fallback_rejects_direct_callback():
    from django.test import RequestFactory
    from djust.tests.test_exposure_runtime import make_request

    class Page(LiveView):
        template = "<div dj-root>subscription safety</div>"
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            raise AssertionError("A client must not reach an output callback")

    initial = make_request()
    Page().get(initial)
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(
            {
                "event": "callback",
                "params": {"component": "forged", "value": "edit"},
            }
        ),
        content_type="application/json",
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    response = Page().post(request)
    assert response.status_code == 400
    assert b"Event handler not found" in response.content


@pytest.mark.parametrize(
    "signature",
    [
        "self, component: Menu",
        "self, component: Menu, val: str",
        "self, component: Menu, value: str, required: bool",
        "self, component: Menu, value: str, /",
        "self, component: str, value: str",
    ],
)
def test_invalid_signatures_fail_at_class_construction(signature):
    namespace = {"Menu": Menu}
    exec(f"def callback({signature}) -> None:\n    pass", namespace)
    source = declaration()
    callback = selected(source)(namespace["callback"])
    with pytest.raises(TypeError, match="Bad.menu.selected.*callback"):
        type("Bad", (LiveView,), {"menu": source, "callback": callback})


def test_class_inspection_does_not_execute_properties_or_descriptors():
    class Bomb:
        def __get__(self, instance, owner=None):
            raise AssertionError("Class compiler must not evaluate descriptors")

    class Page(LiveView):
        trap = Bomb()
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    assert len(compile_subscriptions(Page)) == 1


def test_multilevel_override_and_declaration_replacement_retain_subscription():
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    class Middle(Parent):
        menu = declaration()

        def callback(self, component: Menu, value: str) -> None:
            pass

    class Child(Middle):
        menu = declaration()

    assert compile_subscriptions(Child) == compile_subscriptions(Parent)
    assert not any(isinstance(value, Menu) for value in vars(Child).values())


@pytest.mark.parametrize(
    "name,payload",
    [
        ("", ()),
        ("_private", ()),
        ("selected", (("component", str),)),
        ("selected", (("value", str), ("value", int))),
        ("selected", (("value", "str"),)),
        ("selected", [("value", str)]),
    ],
)
def test_invalid_output_contracts_rejected(name, payload):
    with pytest.raises(TypeError):
        OutputContract(name, payload)


def test_contract_collections_are_immutable():
    with pytest.raises(TypeError, match="immutable"):
        ComponentDeclaration(Menu, [SELECTED])
    with pytest.raises(TypeError, match="Duplicate output"):
        ComponentDeclaration(Menu, (SELECTED, SELECTED))
    source = declaration()
    with pytest.raises(AttributeError):
        source.outputs = ()
    with pytest.raises(AttributeError):
        source.component_type = str


def test_invalid_class_does_not_partially_stamp_valid_overrides():
    class Parent(LiveView):
        menu = declaration()

        @selected(menu)
        def callback(self, component: Menu, value: str) -> None:
            pass

    def good(self, component: Menu, value: str) -> None:
        pass

    other = declaration()

    def invalid(self, component: Menu, value: int) -> None:
        pass

    with pytest.raises(TypeError):
        type(
            "Bad",
            (Parent,),
            {"callback": good, "other": other, "invalid": selected(other)(invalid)},
        )
    assert not hasattr(good, "alters_data")


def test_failed_annotation_resolution_is_redacted():
    def callback(self, component: "SECRET_UNRESOLVED", value: str) -> None:  # noqa: F821
        pass

    source = declaration()
    with pytest.raises(TypeError) as error:
        type("Bad", (LiveView,), {"menu": source, "callback": selected(source)(callback)})
    assert "SECRET_UNRESOLVED" not in str(error.value)
    assert "Bad.menu.selected callback callback" in str(error.value)
