"""Tests for #2821 — ``LiveViewTestClient.mount()`` must actually simulate the
WebSocket mount process, so WS-gated setup code gets exercised under test.

Reproduces the issue's own repro: a view that gates a branch on
``hasattr(self, "_websocket_session_id")`` — the pattern djust's own
``#1612`` presence guard documents (``python/djust/presence.py``) — previously
always reported ``"http-prerender"`` under ``LiveViewTestClient.mount()``,
because ``mount()`` called ``view_instance.mount(request, **params)`` directly
and never stamped the WS identity attributes the real mount path
(``ViewRuntime.dispatch_mount``, ``runtime.py:2033-2038``) sets.

Fix: ``mount()`` now stamps ``_websocket_session_id`` / ``_websocket_path`` /
``_websocket_query_string`` / ``_djust_mount_view_path`` on the view instance
BEFORE calling its ``mount()``, by default (``via_websocket=True``), and
records which branch was taken as ``client.via_websocket`` so a test can
assert it explicitly rather than the branch being implied.
"""

from __future__ import annotations

from djust import LiveView
from djust.testing import LiveViewTestClient


class WsAwareView(LiveView):
    """The pattern the framework's own #1612 presence guard documents."""

    template = "<div>branch={{ branch }}</div>"

    def mount(self, request, **kwargs):
        self.branch = "websocket" if hasattr(self, "_websocket_session_id") else "http-prerender"

    def get_context_data(self, **kwargs):
        return {"branch": self.branch}


def test_mount_default_takes_the_websocket_branch():
    """The issue's own repro: default ``mount()`` must report the WS branch,
    not http-prerender. Red before the fix (reported 'http-prerender')."""
    client = LiveViewTestClient(WsAwareView).mount()

    assert client.view_instance.branch == "websocket"
    assert hasattr(client.view_instance, "_websocket_session_id")


def test_mount_stamps_all_four_ws_identity_attrs():
    """Mirrors ALL FOUR attributes ``ViewRuntime.dispatch_mount`` stamps
    (runtime.py:2033-2038), not just the one gated on in the repro — a test
    exercising the VDOM cache-key path (mixins/rust_bridge.py:354, keyed on
    ``_websocket_path`` / ``_websocket_query_string``) needs these too."""
    client = LiveViewTestClient(WsAwareView).mount()
    view = client.view_instance

    assert hasattr(view, "_websocket_session_id")
    assert view._websocket_session_id  # non-empty
    assert hasattr(view, "_websocket_path")
    assert hasattr(view, "_websocket_query_string")
    assert hasattr(view, "_djust_mount_view_path")
    assert view._djust_mount_view_path == f"{WsAwareView.__module__}.{WsAwareView.__qualname__}"


def test_mount_via_websocket_false_takes_the_http_prerender_branch():
    """Escape hatch: a test that specifically wants to cover the cold
    HTTP-prerender path can opt out explicitly."""
    client = LiveViewTestClient(WsAwareView).mount(via_websocket=False)

    assert client.view_instance.branch == "http-prerender"
    assert not hasattr(client.view_instance, "_websocket_session_id")
    assert not hasattr(client.view_instance, "_websocket_path")
    assert not hasattr(client.view_instance, "_websocket_query_string")
    assert not hasattr(client.view_instance, "_djust_mount_view_path")


def test_mount_records_which_branch_was_taken_on_the_client():
    """The branch must be OBSERVABLE, not implied (issue's closing point) —
    and it must live on the CLIENT, not the view instance, so it never leaks
    into template context / get_state()."""
    ws_client = LiveViewTestClient(WsAwareView).mount()
    assert ws_client.via_websocket is True

    http_client = LiveViewTestClient(WsAwareView).mount(via_websocket=False)
    assert http_client.via_websocket is False

    # Never leaks into public state / template context.
    assert "via_websocket" not in ws_client.get_state()


def test_websocket_session_id_is_stable_across_repeated_mounts_on_same_client():
    """The synthesized id must stay IDENTICAL across repeated mount() calls on
    the SAME client (mirrors a real connection's session_id being stable for
    the connection's lifetime) — not re-randomized per call."""
    client = LiveViewTestClient(WsAwareView)
    client.mount()
    first_id = client.view_instance._websocket_session_id

    client.mount()  # re-mount (e.g. simulating a reconnect scenario)
    second_id = client.view_instance._websocket_session_id

    assert first_id == second_id


# --- gate-off self-test (#1468/#2135) ---------------------------------------
# Names the test that goes red when ONLY the stamping mechanism is removed:
# `test_mount_default_takes_the_websocket_branch` and
# `test_mount_stamps_all_four_ws_identity_attrs` directly assert on the
# presence of the stamped attributes, so commenting out the `if via_websocket:`
# block in `LiveViewTestClient.mount()` fails both immediately. The
# `via_websocket=False` test is the independent mechanism guarding against a
# fix that stamps unconditionally (no escape hatch) — verified separately.


class TestPresenceIdentityUnderTheClient:
    """Regression from the #2821 review.

    Enabling the WS branch makes a view's ``mount()``-time ``track_presence()``
    actually run under ``LiveViewTestClient`` — that is the point of the fix —
    but it also exposed the fallback identity. With the DEFAULT
    ``presence_unique_per_connection = False``, ``get_presence_user_id()``
    returns ``f"anon_{session_key}"``, and an UNSAVED session has
    ``session_key is None``: every client collapsed to the single identity
    ``anon_None``, so two clients counted as ONE presence — the false-green
    class this PR exists to kill.
    """

    KEY = "testclient-review:{room}"

    @classmethod
    def _view(cls):
        from djust.presence import PresenceMixin

        key = cls.KEY

        class PresenceView(PresenceMixin, LiveView):
            template = "<div>x</div>"
            presence_key = key
            # Deliberately the default: presence_unique_per_connection = False

            def mount(self, request, room="r", **kwargs):
                self.room = room
                if hasattr(self, "_websocket_session_id"):
                    self.track_presence()

            def get_context_data(self, **kwargs):
                return {}

        return PresenceView

    def test_two_clients_are_two_distinct_presences(self):
        from djust.presence import PresenceManager

        view_cls = self._view()
        key = self.KEY.format(room="r")
        a = b = None
        try:
            a = LiveViewTestClient(view_cls).mount(room="r").view_instance
            b = LiveViewTestClient(view_cls).mount(room="r").view_instance

            assert a._presence_tracked is True, "presence must run on the WS branch"
            assert a._presence_user_id, "identity must not be empty"
            assert a._presence_user_id != "anon_None", (
                "an unsaved session collapses the identity to 'anon_None'"
            )
            assert b._presence_user_id != "anon_None"
            assert a._presence_user_id != b._presence_user_id, (
                "two clients must not share one presence identity"
            )
            assert len(PresenceManager.list_presences(key)) == 2, (
                "two clients must count as two presences, not one"
            )
        finally:
            for v in (a, b):
                uid = getattr(v, "_presence_user_id", None) if v else None
                if uid:
                    PresenceManager.leave_presence(key, uid)

    def test_distinct_clients_get_distinct_synthetic_ws_ids(self):
        """``id(self)`` is recycled by CPython, so a destroyed client's
        synthetic id could be handed to a new one and collide on the VDOM cache
        key (process-wide state backend)."""
        view_cls = self._view()
        a = LiveViewTestClient(view_cls)
        b = LiveViewTestClient(view_cls)
        assert a._synthetic_ws_session_id != b._synthetic_ws_session_id

    def test_a_client_keeps_one_synthetic_id_across_remounts(self):
        """The other half: a same-client remount SHOULD reuse the id (a
        same-client remount sharing the cache key is production-faithful)."""
        view_cls = self._view()
        client = LiveViewTestClient(view_cls)
        first = client._synthetic_ws_session_id
        client.mount(room="r")
        client.mount(room="r")
        assert client._synthetic_ws_session_id == first
