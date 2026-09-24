"""#3038: the HTTP-POST fallback honours ``_skip_render`` like WS and SSE.

A handler that sets ``self._skip_render = True`` asked for no render this
turn. The WebSocket / SSE routes resolve that through
``websocket._resolve_skip_render``; the HTTP fallback rendered anyway, for view
events and ``component_id`` events, and never reset the flag.
"""

import json
from typing import Any

import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler


class Quiet(LiveComponent):
    class State(TypedState):
        count: int = 0

    template = "<p>{{ count }}</p>"

    @event_handler()
    def bump_quietly(self, **kwargs: Any) -> None:
        self.state.count += 1
        self._view._skip_render = True

    @event_handler()
    def bump(self, **kwargs: Any) -> None:
        self.state.count += 1


class SkipPage(LiveView):
    template = '<div dj-root dj-id="0"><h1>{{ title }}</h1>{{ quiet }}</div>'
    quiet = Quiet()

    def mount(self, request: Any, **kwargs: Any) -> None:
        self.title = "X"
        self.quiet.count = self.quiet.count

    @event_handler()
    def rename_quietly(self, value: str = "", **kwargs: Any) -> None:
        self.title = value
        self._skip_render = True
        self.page_title = "Renamed"

    @event_handler()
    def rename_forced(self, value: str = "", **kwargs: Any) -> None:
        self.title = value
        self._skip_render = True
        self.set_changed_keys("title")

    @event_handler()
    def rename(self, value: str = "", **kwargs: Any) -> None:
        self.title = value


PATH = "/skip-probe/"


def _get():
    request = RequestFactory().get(PATH)
    request.session = SessionStore()
    response = SkipPage.as_view()(request)
    assert response.status_code == 200
    return request.session


def _post(session, event, **params):
    request = RequestFactory().post(
        PATH,
        data=json.dumps({"event": event, "params": params}),
        content_type="application/json",
    )
    request.session = session
    response = SkipPage.as_view()(request)
    assert response.status_code == 200, response.content
    return json.loads(response.content)


@pytest.mark.django_db
class TestHttpSkipRender:
    def test_view_event_skip_answers_empty_patches(self):
        session = _get()
        body = _post(session, "rename_quietly", value="Y")
        assert body["patches"] == []
        assert "html" not in body
        # Nothing was rendered, so the client's VDOM cursor must not move.
        assert "version" not in body
        # Side channels still go out, as with the WebSocket noop.
        assert body["_page_metadata"] == [{"action": "title", "value": "Renamed"}]
        # The state change is saved; the next render shows it.
        assert session[f"liveview_{PATH}"]["title"] == "Y"
        follow_up = _post(session, "rename", value="Z")
        assert follow_up.get("patches") or follow_up.get("html"), follow_up

    def test_component_event_skip_answers_empty_patches(self):
        session = _get()
        body = _post(session, "bump_quietly", component_id="quiet")
        assert body == {"patches": []}
        rendered = _post(session, "bump", component_id="quiet")
        assert rendered.get("patches") or rendered.get("html"), rendered

    def test_skip_flag_is_consumed(self):
        """The flag is reset on the instance that handled the request, as
        ``_resolve_skip_render`` does on every other route (#2834)."""
        session = _get()
        request = RequestFactory().post(
            PATH,
            data=json.dumps({"event": "rename_quietly", "params": {"value": "Y"}}),
            content_type="application/json",
        )
        request.session = session
        view = SkipPage()
        view.setup(request)
        response = view.post(request)
        assert json.loads(response.content)["patches"] == []
        assert view._skip_render is False

    def test_force_full_html_still_wins(self):
        """``set_changed_keys`` (``_force_full_html``) beats ``_skip_render``
        on every route (#2834), including this one."""
        session = _get()
        body = _post(session, "rename_forced", value="Q")
        assert body.get("patches") or body.get("html"), body
