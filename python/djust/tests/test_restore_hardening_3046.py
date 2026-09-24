"""#3046: snapshot / private-state restore hardening.

1. ``restore_components_snapshot`` applied every field with ``safe_setattr``,
   which still let a public name shadow a component METHOD (``render``, a
   handler). Such a key is now skipped.
2. ``_restore_private_state`` used a raw ``setattr`` for every ``_``-prefixed
   key, unscreened against ``DANGEROUS_ATTRIBUTES`` (unlike the public path).
"""

from typing import Any

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler
from djust.live_view import restore_components_snapshot


class Counter:
    """A plain registry component: fields live in the instance ``__dict__``."""

    def __init__(self) -> None:
        self.count = 0

    def render(self) -> str:
        return f"<i>{self.count}</i>"

    def bump(self, **kwargs: Any) -> None:
        self.count += 1


class Toggle(LiveComponent):
    class State(TypedState):
        active: str = ""

    @event_handler()
    def set_active(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value


class HardenPage(LiveView):
    template = "<div dj-root>{{ toggle }}</div>"
    toggle = Toggle(active="a")


def _view_with_counter():
    view = HardenPage()
    counter = Counter()
    view._components = {"counter": counter}
    return view, counter


def test_method_named_key_is_skipped_on_a_plain_component():
    view, counter = _view_with_counter()
    ok = restore_components_snapshot(
        view, {"counter": {"render": "pwned", "bump": 1, "count": 5}}, source="test"
    )
    assert ok is False  # a skipped key is reported, like a blocked one
    assert counter.count == 5  # the real field still applies
    assert "render" not in counter.__dict__
    assert "bump" not in counter.__dict__
    assert counter.render() == "<i>5</i>"


def test_handler_named_key_is_skipped_on_a_bound_component():
    view = HardenPage()
    ok = restore_components_snapshot(
        view, {"toggle": {"set_active": "x", "active": "b"}}, source="test"
    )
    assert ok is False
    assert view.toggle.state["active"] == "b"
    assert "set_active" not in view.toggle.state
    view.toggle.set_active(value="c")
    assert view.toggle.state["active"] == "c"


def test_clean_snapshot_still_reports_ok():
    view, counter = _view_with_counter()
    assert restore_components_snapshot(view, {"counter": {"count": 3}}, source="test") is True
    assert counter.count == 3


def test_private_restore_skips_dangerous_and_dunder_keys():
    view = HardenPage()
    original_class = view.__class__
    view._restore_private_state(
        {
            "__class__": dict,
            "__dict__": {},
            "__custom__": 1,
            "_note": "kept",
        }
    )
    assert view.__class__ is original_class
    assert "__custom__" not in view.__dict__
    assert view._note == "kept"
    assert "_note" in view._user_private_keys
