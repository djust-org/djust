"""#3078: the legacy ``Meta.event`` alias resolves only its own component type.

``LiveComponent._make_event_handler`` registers a view-level alias such as
``toggle_dropdown``. The client names the target with ``component_id``. The
alias must resolve it only against the view class's declared descriptors of
that component type (server-owned routing): never another component type,
never an arbitrary view attribute.

On 1.1 the runtime routes any event carrying ``component_id`` to
``view._components`` before a view handler, so the alias is exercised
directly here rather than through the runtime.
"""

import pytest

from djust import LiveView
from djust.components.descriptors import Dropdown, Modal

READS: list = []


class MixedPage(LiveView):
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
