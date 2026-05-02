"""Regression tests for #1284 — _action_state persistence across WS reconnect.

When a WS disconnect/reconnect cycle occurs, ``_action_state`` (populated by
the ``@action`` decorator with ``{pending, error, result}`` per action name)
was not persisted because it was initialized BEFORE ``_framework_attrs`` capture
in ``__init__``, putting it in the framework-internal set excluded from
``_snapshot_user_private_attrs`` / ``_restore_private_state``.

The fix moves ``_action_state`` initialization to AFTER ``_framework_attrs``
capture so it participates in the standard user-private save/restore cycle.
"""

from djust import LiveView
from djust.decorators import action


class _ActionView(LiveView):
    """View with an @action handler that populates _action_state."""

    @action
    def create_todo(self, title: str = "", **kwargs):
        if not title:
            raise ValueError("title required")
        return {"id": 1, "title": title}


class TestActionStateInPrivateKeys:
    """_action_state appears in _user_private_keys and is save/restore-able."""

    def test_action_state_in_user_private_keys(self):
        view = _ActionView()
        view._snapshot_user_private_attrs()
        assert "_action_state" in view._user_private_keys, (
            "#1284: _action_state must be in _user_private_keys so it is persisted"
        )

    def test_action_state_not_in_framework_attrs(self):
        view = _ActionView()
        assert "_action_state" not in view._framework_attrs, (
            "#1284: _action_state must NOT be in _framework_attrs; "
            "otherwise _snapshot_user_private_attrs and _restore_private_state skip it"
        )

    def test_action_state_saved_by_get_private_state(self):
        view = _ActionView()
        view._snapshot_user_private_attrs()
        # Simulate an action handler run
        view._action_state["create_todo"] = {
            "pending": False,
            "error": None,
            "result": {"id": 1, "title": "hello"},
        }
        private = view._get_private_state()
        assert "_action_state" in private
        assert private["_action_state"]["create_todo"]["result"]["title"] == "hello"

    def test_action_state_restored_by_restore_private_state(self):
        view = _ActionView()
        view._snapshot_user_private_attrs()

        saved = {
            "_action_state": {
                "create_todo": {
                    "pending": False,
                    "error": "title required",
                    "result": None,
                }
            },
            "_other_private": "value",
        }
        view._restore_private_state(saved)

        assert "_action_state" in view._user_private_keys
        assert view._action_state["create_todo"]["error"] == "title required"

    def test_action_state_persisted_after_mount(self):
        """End-to-end: snapshot, simulate handler, save → restore → state intact."""
        view1 = _ActionView()
        view1._snapshot_user_private_attrs()
        # Simulate @action handler populating state
        view1._action_state["create_todo"] = {
            "pending": False,
            "error": None,
            "result": {"id": 1, "title": "hello"},
        }
        saved = view1._get_private_state()

        # Simulate reconnect: fresh view, restore
        view2 = _ActionView()
        view2._restore_private_state(saved)

        assert "_action_state" in view2._user_private_keys
        assert view2._action_state["create_todo"]["result"]["id"] == 1

    def test_empty_action_state_saved_and_restored(self):
        """Empty _action_state (no actions run yet) still persists correctly."""
        view = _ActionView()
        view._snapshot_user_private_attrs()

        saved = view._get_private_state()
        assert "_action_state" in saved
        assert saved["_action_state"] == {}

        view2 = _ActionView()
        view2._restore_private_state({"_action_state": {}})
        assert view2._action_state == {}
