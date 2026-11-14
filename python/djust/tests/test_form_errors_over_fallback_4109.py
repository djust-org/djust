"""Field-by-field form validation over the page-POST fallback and the socket (#4109).

A ``FormMixin`` view is validated one field at a time, the way ``dj-change``
does it, over a real ``WebsocketCommunicator`` and over the page GET plus
fallback POSTs (sent as the client sends them, with the page load's tab id).
After EVERY step the root the server rendered is compared between the two
transports: an error shown for one field must still be shown after the next
field is validated, and a corrected field must lose its error, on both.

The form's input and errors are kept by the view's automatic server-side
state (#4107); the secret-field exclusions of that store are pinned in
``test_automatic_state_4107.py``.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import Client, override_settings

from djust.tests import http_fallback_matrix as matrix

pytestmark = [pytest.mark.django_db(transaction=True)]

SETTINGS = {
    "ROOT_URLCONF": matrix.__name__,
    "LIVEVIEW_ALLOWED_MODULES": ["djust"],
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
}

URL = "/m/form/"
VIEW = "%s.ContactForm" % matrix.__name__

#: (event params, the fields that show an error after it)
STEPS: list[tuple[dict, set[str]]] = [
    ({"field": "email", "value": "not-an-email"}, {"email"}),
    ({"field": "name", "value": "Al"}, {"email", "name"}),
    ({"field": "city", "value": "Lyon"}, {"email", "name"}),
    ({"field": "name", "value": "Alice"}, {"email"}),
    ({"field": "email", "value": "alice@example.com"}, set()),
    ({"field": "city", "value": ""}, {"city"}),
    ({"field": "name", "value": "Bo"}, {"city", "name"}),
]

_ROW = re.compile(r'<input [^>]*name="(\w+)"[^>]*/><div class="err">(.*?)</div>')


def _fields_with_errors(html: str) -> set[str]:
    return {name for name, errors in _ROW.findall(matrix.normalize(html) or "") if errors}


def _fallback(log: matrix.RenderLog, echo: bool) -> list[str]:
    events = [("validate_field", params) for params, _ in STEPS]
    return matrix.run_fallback(URL, events, log, echo=echo)["html_after"]


async def _socket(log: matrix.RenderLog) -> list[str]:
    """The root rendered after each event over a real socket."""
    from channels.db import database_sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser

    from djust.websocket import LiveViewConsumer

    def page() -> Any:
        client = Client()
        assert client.get(URL).status_code == 200
        return client.session

    session = await database_sync_to_async(page)()
    comm = WebsocketCommunicator(
        LiveViewConsumer.as_asgi(),
        "/ws/",
        headers=[(b"host", b"testserver"), (b"origin", b"http://testserver")],
    )
    comm.scope["session"] = session
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected

    async def until(types: tuple[str, ...]) -> dict:
        for _ in range(20):
            frame = json.loads(await comm.receive_from(timeout=30))
            if frame.get("type") in types:
                return frame
        raise AssertionError("no frame of %r" % (types,))

    await comm.send_json_to(
        {"type": "mount", "view": VIEW, "url": URL, "params": {}, "has_prerendered": True}
    )
    assert (await until(("mount", "error")))["type"] == "mount"
    after: list[str] = []
    for ref, (params, _) in enumerate(STEPS, start=1):
        before = len(log.htmls)
        await comm.send_json_to(
            {"type": "event", "event": "validate_field", "params": params, "ref": ref}
        )
        frame = await until(("patch", "html_update", "noop", "error"))
        assert frame["type"] != "error", frame
        assert len(log.htmls) > before, "the event rendered nothing"
        after.append(log.htmls[-1])
    await comm.disconnect()
    return after


@pytest.mark.asyncio
@pytest.mark.parametrize("echo", [True, False], ids=["patches", "whole-pages"])
async def test_each_validation_step_shows_the_same_errors_over_both_transports(monkeypatch, echo):
    with override_settings(**SETTINGS):
        log = matrix.RenderLog(monkeypatch)
        fallback = await sync_to_async(_fallback)(log, echo)
        socket = await _socket(log)
    assert len(fallback) == len(socket) == len(STEPS)
    for step, ((params, expected), over_http, over_socket) in enumerate(
        zip(STEPS, fallback, socket), start=1
    ):
        # Not only equal: the errors the step should show are there.
        assert _fields_with_errors(over_socket) == expected, (step, params)
        assert matrix.normalize(over_http) == matrix.normalize(over_socket), (step, params)
