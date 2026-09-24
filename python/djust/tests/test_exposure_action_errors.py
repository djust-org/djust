"""ADR-038 E2-4 / D-f: ``@action`` error text under explicit exposure.

``@action`` records a failure as ``_action_state[name]["error"]``, which the
action context provider renders as ``{{ name.error }}``. It stored
``str(exc)``, so exception text (which can carry view state or query values)
reached the rendered HTML and the patch frame. For nonlegacy views it now
stores a generic message, unless the handler raised the declared user-facing
``ActionError``. Legacy views are unchanged.

Driven through a real runtime mount and event dispatch; the assertions read
the WebSocket frames and the view's rendered HTML.
"""

import asyncio
import json

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import ActionError, action

from .test_exposure_runtime import make_request, mount

SENTINEL = "ACTION_ERROR_SENTINEL"
USER_TEXT = "ACTION_USER_MESSAGE_SENTINEL"
CALLS = []


class ActionErrorView(LiveView):
    exposure_policy = "legacy"
    template = "<div dj-root><p id='err'>[{{ save.error }}]</p></div>"

    @action
    def save(self, kind: str = "plain"):
        CALLS.append(kind)
        if kind == "user":
            raise ActionError(USER_TEXT)
        raise ValueError(SENTINEL)


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["plain", "user"])
@pytest.mark.parametrize("policy", ["legacy", "explicit"])
async def test_action_error_text_reaching_render(monkeypatch, kind, policy):
    CALLS.clear()
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)
    monkeypatch.setattr(ActionErrorView, "exposure_policy", policy)
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, ActionErrorView, view=__name__ + ".ActionErrorView")
    transport.sent.clear()
    await runtime.dispatch_event(
        {"type": "event", "event": "save", "params": {"kind": kind}, "ref": 9}
    )
    for _ in range(3):
        await asyncio.sleep(0)
    assert CALLS == [kind], "the action never ran; the test would be vacuous"
    assert not any(frame.get("type") == "error" for frame in transport.sent), transport.sent
    frames = json.dumps(transport.sent)
    html = await sync_to_async(runtime.view_instance.render)()
    legacy = policy == "legacy"
    if kind == "user":
        # A declared user-facing message is shown under every policy.
        assert USER_TEXT in frames
        assert "[" + USER_TEXT + "]" in html
    else:
        assert (SENTINEL in frames) == legacy
        assert (SENTINEL in html) == legacy
        if not legacy:
            assert "Action failed" in frames
            assert "[Action failed]" in html
