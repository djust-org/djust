"""#3078: the ``Meta.event`` alias resolves only its own component type.

``LiveComponent._make_event_handler`` registers a view-level alias such as
``toggle_dropdown``. The client names the target with ``component_id``. The
alias must resolve it only against the view class's declared descriptors of
that component type (ADR-034's server-owned routing rule): never another
component type, never an arbitrary view attribute. It follows the project's
parameter policy; under strict, ``component_id`` is a framework-supplied
argument (#4185, ``test_framework_classes_explicit_4185.py``).
"""

import pytest

from djust import LiveView
from djust.components.descriptors import Dropdown, Modal
from djust.config import config

READS: list = []


class MixedPage(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0"><p>{{ menu.is_open }}</p><p>{{ dialog.is_open }}</p></div>'
    menu = Dropdown()
    dialog = Modal()

    @property
    def secret(self):
        READS.append("secret")
        return Modal()

    def mount(self, request, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _clear():
    READS.clear()
    yield
    READS.clear()


def test_alias_drives_its_own_component():
    view = MixedPage()
    view.toggle_dropdown(component_id="menu")
    assert view.menu.is_open is True
    assert view.dialog.is_open is False


def test_alias_cannot_drive_another_component_type():
    view = MixedPage()
    view.toggle_dropdown(component_id="dialog")
    assert view.dialog.is_open is False
    assert view.menu.is_open is False


@pytest.mark.parametrize("target", ["secret", "template", "mount", "__class__", "", ["menu"], 7])
def test_alias_never_reads_an_arbitrary_view_attribute(target):
    view = MixedPage()
    view.toggle_dropdown(component_id=target)
    assert READS == []
    # An empty id auto-resolves the single Dropdown; nothing else resolves.
    assert view.menu.is_open is (target == "")
    assert view.dialog.is_open is False


def test_auto_resolution_needs_exactly_one_instance_of_the_type():
    class TwoMenus(LiveView):
        exposure_policy = "explicit"
        first = Dropdown()
        second = Dropdown()

    view = TwoMenus()
    view.toggle_dropdown()
    assert (view.first.is_open, view.second.is_open) == (False, False)
    view.toggle_dropdown(component_id="second")
    assert (view.first.is_open, view.second.is_open) == (False, True)


def test_a_subclass_that_replaces_the_declaration_is_not_resolved():
    class Replaced(MixedPage):
        menu = "not a component"

    view = Replaced()
    view.toggle_dropdown(component_id="menu")
    assert view.menu == "not a component"


def test_alias_follows_the_project_policy_and_declares_component_id():
    from djust.validation import declared_framework_arguments

    metadata = MixedPage.toggle_dropdown._djust_decorators["event_handler"]
    assert metadata["parameter_policy"] is None
    assert declared_framework_arguments(MixedPage.toggle_dropdown) == {"component_id"}


@pytest.mark.parametrize("policy", ["legacy", "strict"])
def test_alias_validates_under_either_project_policy(policy):
    """The binding every transport applies before the alias runs (the socket
    and SSE runtime, and the HTTP fallback: ``validate_handler_params``)."""
    from djust.validation import validate_handler_params, validated_call_arguments

    previous = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", policy)
    try:
        view = MixedPage()
        for target in ("dialog", "menu"):
            result = validate_handler_params(
                view.toggle_dropdown, {"component_id": target}, "toggle_dropdown"
            )
            assert result["valid"], result
            args, kwargs = validated_call_arguments(result)
            view.toggle_dropdown(*args, **kwargs)
    finally:
        config.set("event_parameter_policy", previous)
    assert view.dialog.is_open is False
    assert view.menu.is_open is True
