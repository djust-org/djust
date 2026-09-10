"""``ModalComponent``'s close controls emit ``dj-click="dismiss"`` — the
component must define that handler (#2748).

External report: opening a ``ModalComponent`` under the Bootstrap framework and
clicking the cross or the footer "Close" button raised
``No handler found for event: dismiss``. Every framework branch of the
component (``_render_bootstrap`` / ``_render_tailwind`` / ``_render_plain``)
wires its close controls to ``dismiss``, but the component only defined
``hide()`` — the same shape ``AlertComponent`` solves by defining ``dismiss()``
(``python/djust/components/ui/alert.py``).

Reproduce-first: the WS test drives a real ``WebsocketCommunicator`` end-to-end
(the ``component_id`` routing path, harness lifted from
``test_ws_event_flip_parity_1896.py``) and asserts the click closes the modal
instead of producing an ``error`` frame. The per-branch pin that every ``dj-click``
names a decorated, routed handler lives in the package-wide sweep in
``test_ui_dismiss_handlers_2756.py`` (#2756).
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.components.ui.modal import ModalComponent

_MODULE = "djust.tests.test_modal_dismiss_2748"
_FRAMEWORKS = ("bootstrap5", "tailwind", "plain")


# ---------------------------------------------------------------------------
# Harness (lifted from test_ws_event_flip_parity_1896.py)
# ---------------------------------------------------------------------------


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


class _ScopeSession:
    def __init__(self, key):
        self.session_key = key


async def _connect_and_mount(view_path: str, url: str = "/modal/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to({"type": "mount", "view": view_path, "url": url})
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


class ModalHostView(LiveView):
    """Parent hosting an open ``ModalComponent`` under ``component_id`` ``m1``."""

    template = (
        f'<div dj-root dj-view="{_MODULE}.ModalHostView" dj-id="0">'
        "<p>open={{ modal_open }}</p></div>"
    )

    def mount(self, request, **kwargs):
        self.modal = ModalComponent(component_id="m1", title="Confirm", body="Sure?", show=True)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["modal_open"] = "yes" if self.modal.show_modal else "no"
        return ctx


# ---------------------------------------------------------------------------
# Reproducer: the click the templates emit reaches a handler and closes the modal
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestModalDismissOverWebSocket:
    async def test_dismiss_click_closes_modal(self):
        """The ``dismiss`` event the close controls emit is handled by the
        component and the parent re-renders with the modal closed — not the
        ``No handler found for event: dismiss`` error frame of #2748."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, mount_frame = await _connect_and_mount(f"{_MODULE}.ModalHostView")
            assert "open=yes" in mount_frame.get("html", ""), mount_frame

            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "dismiss",
                    "params": {"component_id": "m1"},
                    "ref": 1,
                }
            )
            resp = await communicator.receive_json_from(timeout=3)

            assert resp.get("type") != "error", (
                f"#2748: the close control's 'dismiss' event must be handled; got {resp!r}"
            )
            assert resp.get("type") == "html_update", resp
            assert "open=no" in resp.get("html", ""), (
                f"dismiss must close the modal (parent re-render shows open=no); got {resp!r}"
            )

            await communicator.disconnect()

    async def test_unknown_event_still_errors(self):
        """GATE-OFF / contrast: an event the component does NOT define is still
        rejected with an error frame — proves the test above distinguishes a
        real handler from a vacuous pass."""
        from django.test import override_settings

        with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
            communicator, _ = await _connect_and_mount(f"{_MODULE}.ModalHostView")
            await communicator.send_json_to(
                {
                    "type": "event",
                    "event": "not_a_handler",
                    "params": {"component_id": "m1"},
                    "ref": 2,
                }
            )
            resp = await communicator.receive_json_from(timeout=3)
            assert resp.get("type") == "error", resp
            await communicator.disconnect()


# ---------------------------------------------------------------------------
# Render helper (the per-branch handler pin moved to the package-wide sweep in
# test_ui_dismiss_handlers_2756.py)
# ---------------------------------------------------------------------------


def _render_under(framework: str) -> str:
    from unittest.mock import patch

    from djust import config as config_module

    modal = ModalComponent(component_id="m1", title="T", body="B", show=True)
    with patch.object(config_module.config, "get", lambda key, default=None: framework):
        return str(modal.render())


class TestDismissDelegatesToHide:
    def test_dismiss_hides_the_modal(self):
        modal = ModalComponent(component_id="m1", title="T", body="B", show=True)
        assert modal.show_modal is True
        modal.dismiss()
        assert modal.show_modal is False
        assert modal.render() == ""


class TestFooterIsOverridableLikeTitleAndBody:
    """The footer is exposed the same way as ``title``/``body``: a mount
    kwarg, a context key, a ``set_footer`` setter, rendered by every branch."""

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_default_footer_is_the_close_button(self, framework):
        html = _render_under(framework)
        assert 'dj-click="dismiss" data-component-id="m1"' in html
        assert ">Close</button>" in html

    @pytest.mark.parametrize("framework", _FRAMEWORKS)
    def test_custom_footer_replaces_the_default(self, framework):
        from unittest.mock import patch

        from djust import config as config_module

        modal = ModalComponent(
            component_id="m1",
            title="T",
            body="B",
            show=True,
            footer='<button dj-click="confirm" data-component-id="m1">Yes</button>',
        )
        with patch.object(config_module.config, "get", lambda key, default=None: framework):
            html = str(modal.render())
        assert 'dj-click="confirm"' in html, framework
        assert ">Close</button>" not in html, framework

    def test_set_footer_and_context(self):
        modal = ModalComponent(component_id="m1", title="T", body="B")
        assert modal.get_context()["footer"] == ""
        modal.set_footer("<em>f</em>")
        assert modal.footer == "<em>f</em>"
        assert modal.get_context()["footer"] == "<em>f</em>"
