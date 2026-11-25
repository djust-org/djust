"""Regression tests for #1284: ``_action_state`` survives a WS reconnect.

``_action_state`` (``{pending, error, result}`` per ``@action`` name) must not be a
framework-internal attribute, or it is excluded from the user-private save/restore cycle.
On an explicit view the cycle is the automatic private state.
"""

from djust import LiveView
from djust.decorators import action
from djust.testing import persisted_state


class _ActionView(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"

    @action
    def create_todo(self, title: str = "", **kwargs):
        if not title:
            raise ValueError("title required")
        return {"id": 1, "title": title}


def test_action_state_is_not_a_framework_attr() -> None:
    assert "_action_state" not in _ActionView()._framework_attrs


def test_an_action_error_is_persisted_with_the_private_state() -> None:
    view = _ActionView()
    view._snapshot_user_private_attrs()
    view.create_todo()  # raises inside; the decorator records it, it does not re-raise
    entry = persisted_state(view).automatic_private["_action_state"]["create_todo"]
    assert entry["error"], entry
    assert entry["pending"] is False


def test_an_action_result_is_persisted() -> None:
    view = _ActionView()
    view._snapshot_user_private_attrs()
    view.create_todo(title="hello")
    entry = persisted_state(view).automatic_private["_action_state"]["create_todo"]
    assert entry["error"] is None
    assert entry["result"]["title"] == "hello"
