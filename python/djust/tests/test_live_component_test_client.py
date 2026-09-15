"""Tests for ``LiveComponentTestClient``.

The client exists so a component can be tested without a parent view, which
previously required wrapping it in a throwaway ``LiveView``. It mirrors
``LiveViewTestClient``, so these pin the same contract: mount, drive an
event, read state, and fail loudly on a handler that does not exist.
"""

from __future__ import annotations

import pytest

from djust import LiveComponent
from djust.testing import LiveComponentTestClient, NoHandlerFoundError


class StarRating(LiveComponent):
    """The component from `tutorial-live-component`, reduced."""

    template_name = "star_rating.html"

    def mount(self, value: int = 0, max: int = 5) -> None:
        self.value = value
        self.max = max
        self.hover = 0

    def hover_star(self, n: int = 0) -> None:
        self.hover = n

    def commit_rating(self, n: int = 0) -> None:
        self.value = n

    def render(self) -> str:
        return f"<span>{self.value}/{self.max}</span>"


def test_mount_returns_self_so_construction_reads_as_one_expression():
    client = LiveComponentTestClient(StarRating).mount(value=3, max=5)
    assert isinstance(client, LiveComponentTestClient)
    assert client.get_state()["value"] == 3


def test_mount_passes_props_to_the_component():
    state = LiveComponentTestClient(StarRating).mount(value=2, max=10).get_state()
    assert state["value"] == 2
    assert state["max"] == 10


def test_event_changes_only_what_the_handler_touches():
    """The tutorial's central assertion: hovering must not commit."""
    client = LiveComponentTestClient(StarRating).mount(value=3, max=5)
    client.send_event("hover_star", n=4)

    state = client.get_state()
    assert state["hover"] == 4
    assert state["value"] == 3, "hovering must not change the committed value"


def test_commit_changes_the_value():
    client = LiveComponentTestClient(StarRating).mount(value=3)
    client.send_event("commit_rating", n=5)
    assert client.get_state()["value"] == 5


def test_missing_handler_raises():
    """A renamed handler must fail loudly.

    A silent no-op here would let a test keep passing while the feature is
    broken — the failure mode the view client was fixed for in #2823.
    """
    client = LiveComponentTestClient(StarRating).mount()
    with pytest.raises(NoHandlerFoundError):
        client.send_event("no_such_handler")


def test_send_event_reports_before_and_after_state():
    client = LiveComponentTestClient(StarRating).mount(value=3)
    result = client.send_event("commit_rating", n=4)

    assert result["success"] is True
    assert result["state_before"]["value"] == 3
    assert result["state_after"]["value"] == 4
    assert result["duration_ms"] >= 0


def test_get_state_excludes_private_attributes():
    """Underscore attributes are internal by the framework's convention."""

    class WithPrivate(LiveComponent):
        def mount(self, **kwargs) -> None:
            self.public_thing = 1
            self._secret = 2

    state = LiveComponentTestClient(WithPrivate).mount().get_state()
    assert "public_thing" in state
    assert "_secret" not in state


def test_unmounted_client_raises_rather_than_returning_empty():
    client = LiveComponentTestClient(StarRating)
    with pytest.raises(RuntimeError):
        client.get_state()
    with pytest.raises(RuntimeError):
        client.send_event("hover_star", n=1)


def test_render_delegates_to_the_component():
    client = LiveComponentTestClient(StarRating).mount(value=5, max=5)
    assert client.render() == "<span>5/5</span>"


def test_event_history_records_each_event():
    client = LiveComponentTestClient(StarRating).mount(value=1)
    client.send_event("hover_star", n=2)
    client.send_event("commit_rating", n=3)

    history = client.get_event_history()
    assert [e["event"] for e in history] == ["hover_star", "commit_rating"]


def test_assert_state_helpers():
    client = LiveComponentTestClient(StarRating).mount(value=3, max=5)
    client.assert_state_contains(value=3)
    client.assert_state(value=3, max=5, hover=0, component_id=client.get_state()["component_id"])


def test_assert_state_contains_fails_on_mismatch():
    client = LiveComponentTestClient(StarRating).mount(value=3)
    with pytest.raises(AssertionError):
        client.assert_state_contains(value=99)
