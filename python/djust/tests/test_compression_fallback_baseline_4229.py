"""#4229: a patch-compression ``html_update`` keeps the diff baseline.

When one event yields more than 100 patches and the page's HTML is smaller,
``_render_and_send`` sends the whole page instead. It used to call
``_rust_view.reset()`` as well, so the NEXT event had nothing to diff against
and was answered with the whole page again, however small its change (and, on
SSE, with the Rust version restarted at 1, which the client's consecutive
version check refuses). ``render_with_diff`` has already stored the tree the
HTML was serialized from, ``dj-id``s included, so the page the client shows
after the frame is the baseline: the next event is a ``patch``.

Driven over every transport that shares ``_render_and_send`` (a real
``WebsocketCommunicator`` and SSE) and over the page-POST fallback, which has
the same fallback for a renderer that lives across requests. The client side
(``tests/js/compression_fallback_baseline_4229.test.js``) applies the same
frame shapes.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any, List, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import LiveView
from djust.decorators import event_handler, state
from djust.tests._frames import frame_matches, legacy_type
from djust.tests.http_fallback_matrix import PAGE, meta_digest, post_event

MODULE = __name__
ROWS = 150


class Grid(LiveView):
    """``shift`` changes every row (150 ``SetText``); ``touch`` changes one text."""

    exposure_policy = "explicit"
    template = PAGE % (
        '\n<ul>{% for n in numbers %}<li>{{ n }}</li>{% endfor %}</ul>\n<p class="note">{{ note }}</p>\n'
    )
    offset = state(0, persist="server")
    note = state("start", persist="server")

    def get_context_data(self, **kwargs: Any) -> dict:
        return super().get_context_data(numbers=[self.offset + i for i in range(ROWS)], **kwargs)

    @event_handler
    def shift(self, **kwargs: Any) -> None:
        self.offset += 1000

    @event_handler
    def touch(self, value: str = "", **kwargs: Any) -> None:
        self.note = value


class GridAfter(Grid):
    """``Grid`` as the script leaves it, mounted fresh: the DOM the client must end with."""

    offset = state(1000, persist="server")
    note = state("again", persist="server")


urlpatterns = [path("grid-4229/", Grid.as_view())]

SETTINGS = {
    "ROOT_URLCONF": MODULE,
    "ALLOWED_HOSTS": ["*"],
    "DEBUG": False,
    "LIVEVIEW_ALLOWED_MODULES": ["djust", MODULE],
}

_ANSWERS = ("patch", "html_update", "noop", "error")
_SCRIPT = [("shift", {}), ("touch", {"value": "after"}), ("touch", {"value": "again"})]


@pytest.fixture(autouse=True)
def _routes():
    with override_settings(**SETTINGS):
        yield


def _assert_fallback_then_patches(answers: List[dict]) -> None:
    kinds = [legacy_type(answer) for answer in answers]
    assert kinds == ["html_update", "patch", "patch"], [
        (a["type"], len(a.get("patches") or [])) for a in answers
    ]
    fallback, after, again = answers
    assert ">1149</li>" in fallback["html"]
    # One text changed: one patch, not the page.
    assert after["patches"] == [{**after["patches"][0], "type": "SetText", "text": "after"}], after[
        "patches"
    ]
    assert len(again["patches"]) == 1 and again["patches"][0]["text"] == "again"
    # The client checks each frame is the successor of the one before.
    assert after["version"] == fallback["version"] + 1, answers
    assert again["version"] == after["version"] + 1, answers


async def _ws_answers(
    script: List[Tuple[str, dict]], view: str = "Grid"
) -> Tuple[dict, List[dict]]:
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator

    from djust.testing import with_live_session
    from djust.websocket import LiveViewConsumer

    comm = with_live_session(WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/"))
    connected, _ = await comm.connect()
    assert connected
    answers: List[dict] = []
    try:
        await comm.receive_json_from(timeout=10)  # connect frame
        await comm.send_json_to({"type": "mount", "view": f"{MODULE}.{view}", "url": "/grid-4229/"})
        mounted = await _until(comm, ("mount",))
        for ref, (name, params) in enumerate(script, start=1):
            await comm.send_json_to({"type": "event", "event": name, "params": params, "ref": ref})
            answers.append(await _until(comm, _ANSWERS))
    finally:
        await comm.disconnect()
    return mounted, answers


async def _until(comm: Any, types: Tuple[str, ...]) -> dict:
    for _ in range(30):
        frame = await comm.receive_json_from(timeout=10)
        assert frame.get("type") != "error", frame
        if frame_matches(frame, types):
            return frame
    raise AssertionError("no %s frame" % (types,))


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_the_event_after_a_compression_fallback_is_a_patch():
    mounted, answers = await _ws_answers(_SCRIPT)
    _assert_fallback_then_patches(answers)
    assert answers[0]["version"] == mounted["version"] + 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_websocket_gate_off_a_small_change_alone_is_a_patch_and_shift_is_the_fallback():
    """The contrast: ``touch`` patches on its own and ``shift`` alone is the
    whole page, so the test above measures what follows the fallback."""
    _mounted, answers = await _ws_answers([("touch", {"value": "after"})])
    assert [legacy_type(a) for a in answers] == ["patch"]
    _mounted, answers = await _ws_answers([("shift", {})])
    assert [legacy_type(a) for a in answers] == ["html_update"]


def _sse_request(method: str, url: str, body: Any, key: str) -> Any:
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


async def _sse_frame(stream: Any, *types: str) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame_matches(frame, types):
                return frame


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_sse_the_event_after_a_compression_fallback_is_a_patch_with_the_next_version():
    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    session = await sync_to_async(SessionStore)()
    await sync_to_async(session.create)()
    key = session.session_key
    sid = str(uuid.uuid4())
    opened = await sync_to_async(_sse_request)(
        "GET", f"/djust/sse/{sid}/", {"view": f"{MODULE}.Grid", "_djust_url": "/grid-4229/"}, key
    )
    response = await DjustSSEStreamView().get(opened, session_id=sid)
    assert response.status_code == 200
    stream = response._iterator
    answers: List[dict] = []
    try:
        await _sse_frame(stream, "sse_connect")
        mounted = await _sse_frame(stream, "mount", "error")
        assert mounted["type"] == "mount", mounted
        for name, params in _SCRIPT:
            posted = await sync_to_async(_sse_request)(
                "POST",
                f"/djust/sse/{sid}/message/",
                {"type": "event", "event": name, "params": params},
                key,
            )
            answer = await DjustSSEMessageView().post(posted, session_id=sid)
            assert answer.status_code in (200, 202, 204), answer.content
            answers.append(await _sse_frame(stream, *_ANSWERS))
    finally:
        _sse_sessions.pop(sid, None)
        await stream.aclose()
    _assert_fallback_then_patches(answers)


# --------------------------------------------------------------------------- #
# Page-POST fallback
# --------------------------------------------------------------------------- #


def _factory_request(method: str, key: str, event: str = "", params: Any = None) -> Any:
    factory = RequestFactory()
    if method == "GET":
        request = factory.get("/grid-4229/")
    else:
        request = factory.post(
            "/grid-4229/",
            data=json.dumps(params or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(key)
    return request


@pytest.mark.django_db(transaction=True)
def test_http_a_renderer_kept_across_requests_patches_after_the_whole_page():
    """A view object that has rendered before (``reused_renderer``) answers
    more than 100 patches with the whole page; the renderer keeps that page as
    its baseline, so the next answer is patches numbered after it."""
    session = SessionStore()
    session.create()
    key = str(session.session_key)
    view = Grid()
    assert view.get(_factory_request("GET", key)).status_code == 200

    answers = []
    for name, params in _SCRIPT:
        response = view.post(_factory_request("POST", key, name, params))
        assert response.status_code == 200, response.content[:300]
        answers.append(json.loads(response.content))

    whole, after, again = answers
    assert "patches" not in whole and ">1149</li>" in whole["html"]
    assert "html" not in after, after
    assert [p["text"] for p in after["patches"]] == ["after"], after["patches"]
    assert [p["text"] for p in again["patches"]] == ["again"], again["patches"]
    assert after["version"] == whole["version"] + 1
    assert again["version"] == after["version"] + 1


@pytest.mark.django_db(transaction=True)
def test_http_page_post_patches_after_a_compression_whole_page():
    """Each request on ``as_view()`` builds a fresh renderer: the whole-page
    answer names its render's digest, and echoing it gets patches."""
    client = Client()
    page = client.get("/grid-4229/")
    assert page.status_code == 200
    digest = meta_digest(page.content.decode())
    assert digest

    def send(event: str, params: dict, token: str) -> dict:
        response = post_event(client, "/grid-4229/", event, params, token)
        assert response.status_code == 200, response.content[:300]
        return json.loads(response.content)

    whole = send("shift", {}, digest)
    assert "patches" not in whole and ">1149</li>" in whole["html"]
    # The token matched: the client must not count this answer as a miss.
    assert whole["whole_page"] == "size"
    after = send("touch", {"value": "after"}, whole["render_digest"])
    assert "html" not in after, after
    assert [p["text"] for p in after["patches"]] == ["after"], after["patches"]


@pytest.mark.django_db(transaction=True)
def test_http_a_whole_page_for_a_refused_token_is_not_marked_as_chosen_for_size():
    """Gate-off: without a matching token the whole page is a real miss."""
    client = Client()
    assert client.get("/grid-4229/").status_code == 200
    response = post_event(client, "/grid-4229/", "shift", {}, None)
    answer = json.loads(response.content)
    assert ">1149</li>" in answer["html"] and "whole_page" not in answer
    response = post_event(client, "/grid-4229/", "touch", {"value": "x"}, "A" * 22 + "." + "0" * 16)
    assert "whole_page" not in json.loads(response.content)


# --------------------------------------------------------------------------- #
# The client replay fixture
# --------------------------------------------------------------------------- #

FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "tests"
    / "js"
    / "fixtures"
    / "compression_fallback_4229.json"
)
WRITE_ENV = "DJUST_WRITE_COMPRESSION_FALLBACK_FIXTURE"
_FRAME_KEYS = ("type", "html", "patches", "version", "event_name", "source", "ref")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_committed_client_fixture_is_current():
    """``tests/js/compression_fallback_baseline_4229.test.js`` replays these
    WebSocket frames with the built client and compares its DOM with
    ``fresh``, a fresh mount of the state the script ends in.

    Generated, never edited: ``DJUST_WRITE_COMPRESSION_FALLBACK_FIXTURE=1``
    and run this test.
    """
    mounted, answers = await _ws_answers(_SCRIPT)
    fresh, _ = await _ws_answers([], view="GridAfter")
    document = {
        "_generated_by": "python/djust/tests/test_compression_fallback_baseline_4229.py",
        "mount": {key: mounted[key] for key in ("html", "version")},
        "frames": [{k: a[k] for k in _FRAME_KEYS if k in a} for a in answers],
        "fresh": fresh["html"],
    }
    text = json.dumps(document, indent=1, sort_keys=True) + "\n"
    if os.environ.get(WRITE_ENV):
        FIXTURE.write_text(text, encoding="utf-8")
    assert FIXTURE.exists(), "generate the fixture: see this test's docstring"
    assert json.loads(FIXTURE.read_text(encoding="utf-8")) == json.loads(text), (
        "tests/js/fixtures/compression_fallback_4229.json is stale: regenerate it"
    )
