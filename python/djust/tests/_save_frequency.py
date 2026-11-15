"""Shared drivers for the save-frequency tests: one view, driven over a real
``WebsocketCommunicator``, the SSE endpoints and stateless fallback POSTs."""

from __future__ import annotations

import json
import uuid
from typing import Any, List, Optional, Tuple

from asgiref.sync import sync_to_async
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from djust import _automatic_state
from djust.tests import _per_turn_harness as harness

PATH = harness.PATH
TAB = harness.TAB


def stored_envelopes(key: str) -> dict:
    """Every automatic-state entry the session row holds now."""
    session = SessionStore(key)
    return {name: value for name, value in session.items() if name.startswith("_djust_auto_")}


def only_envelope(key: str) -> dict:
    found = stored_envelopes(key)
    assert len(found) == 1, list(found)
    return next(iter(found.values()))


def fallback_request(key: Optional[str], method: str = "GET", event: str = "", params: Any = None):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(PATH)
    else:
        request = factory.post(
            PATH,
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
            HTTP_X_DJUST_TAB=TAB,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    if key is None:
        request.session.create()
    return request


def page_get(view_class: type) -> str:
    """A page GET of ``view_class``; returns the new session key."""
    request = fallback_request(None)
    assert view_class().get(request).status_code == 200
    return str(request.session.session_key)


def fallback_post(view_class: type, key: str, event: str, params: Any = None) -> Any:
    view = view_class()
    response = view.post(fallback_request(key, "POST", event, params))
    assert response.status_code == 200, response.content
    return view


async def ws_run(view_class: type, key: str, events: List[Tuple[str, Any]]) -> List[dict]:
    """Mount over a real consumer, send ``events``, disconnect; the reply frames."""
    comm, _mount = await harness.mount_view(view_class, key)
    replies = []
    try:
        for name, params in events:
            replies.append(await harness.event(comm, name, params))
    finally:
        await comm.disconnect()
    return replies


async def sse_run(view_class: type, key: str, events: List[Tuple[str, Any]], module: str) -> None:
    """Open an SSE stream for ``view_class`` on session ``key`` and POST ``events``."""
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    def request_for(method: str, target: str, body: Any) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    first = await sync_to_async(request_for)(
        "GET",
        f"/djust/sse/{sid}/",
        {"view": f"{module}.{view_class.__name__}", "_djust_url": PATH, "_djust_tab": TAB},
    )
    response = await DjustSSEStreamView().get(first, session_id=sid)
    assert response.status_code == 200
    try:
        for name, params in events:
            posted = await sync_to_async(request_for)(
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": name, "params": params or {}},
            )
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
    finally:
        _sse_sessions.pop(sid, None)


def one_tab(monkeypatch: Any) -> None:
    """Every page load is the harness's tab (the client sends it back)."""
    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: TAB)
