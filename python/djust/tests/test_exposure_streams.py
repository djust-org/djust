"""ADR-038 E2-5: a real stream insert/delete inside explicit runtime events.

Mount and events run through ``ViewRuntime`` over DB sessions; only the staged
construction gate is bypassed. Each step compares the Rust view render with the
Django template engine over the same context, and every destination is searched
for the unrendered stream item field.
"""

import json
import re

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.template import Context, Engine
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import explicit_debug_projection
from djust._exposure_sessions import server_state_adapter
from djust._exposure_snapshots import snapshot_codec
from djust.decorators import state
from djust.observability.registry import register_view, unregister_view
from djust.observability.views import view_assigns
from djust.runtime import ViewRuntime
from djust.tests.test_exposure_schema_versions import make_request
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]

TEMPLATE = (
    '<div dj-root><ul dj-stream="items" id="items">'
    "{% for item in streams.items %}"
    '<li id="items-{{ item.id }}">{{ item.title }}</li>'
    "{% endfor %}</ul><span>{{ count }}</span></div>"
)
SECRET = "STREAM_SECRET_SENTINEL"


def item(pk, title):
    return {"id": pk, "title": title, "secret": f"{SECRET}_{pk}"}


class StreamView(LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE
    count = state(0, persist="server")
    mode = state("list", persist="client", client=True)

    def mount(self, request, **kwargs):
        self.count = 1
        self.stream("items", [item(1, "First")])

    def get_context_data(self, **kwargs):
        return super().get_context_data(count=self.count, **kwargs)

    @event_handler()
    def add_item(self):
        self.stream_insert("items", item(2, "Second"))
        self.count += 1

    @event_handler()
    def remove_item(self):
        self.stream_delete("items", 1)
        self.count += 1


class LegacyStreamView(StreamView):
    exposure_policy = "legacy"
    count = state(0)
    mode = state("list")


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


async def mount(request, view_class):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust"]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
        )
    assert not transport.errors, transport.errors
    return runtime, transport


def strip_ids(html):
    return re.sub(r' dj-id="[^"]*"', "", html)


def django_render(view):
    return Engine().from_string(TEMPLATE).render(Context(view.get_context_data()))


def rust_render(view):
    return strip_ids(view._rust_view.render())


async def run_stream_events(view_class):
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, view_class)
    view = runtime.view_instance
    mount_frame = next(f for f in transport.sent if f.get("type") == "mount")
    renders = [(strip_ids(mount_frame["html"]), rust_render(view), django_render(view))]
    frames = {}
    for event in ("add_item", "remove_item"):
        transport.sent.clear()
        await runtime.dispatch_event({"type": "event", "event": event, "params": {}})
        assert runtime.view_instance is view, transport.sent
        frames[event] = list(transport.sent)
        renders.append((None, rust_render(view), django_render(view)))
    return request, runtime, mount_frame, frames, renders


@pytest.mark.parametrize("view_class", [StreamView, LegacyStreamView])
async def test_stream_insert_and_delete_render_identically_in_rust_and_django(staged, view_class):
    _, _, mount_frame, frames, renders = await run_stream_events(view_class)
    expected = [
        '<ul dj-stream="items" id="items"><li id="items-1">First</li></ul><span>1</span>',
        '<ul dj-stream="items" id="items"><li id="items-1">First</li>'
        '<li id="items-2">Second</li></ul><span>2</span>',
        '<ul dj-stream="items" id="items"><li id="items-2">Second</li></ul><span>3</span>',
    ]
    for index, (frame_html, rust, django) in enumerate(renders):
        assert rust == django == f"<div dj-root>{expected[index]}</div>"
        if frame_html is not None:
            assert frame_html == expected[index]
    insert = next(f for f in frames["add_item"] if f.get("type") == "patch")
    assert any(
        p["type"] == "InsertChild" and p["node"]["attrs"]["id"] == "items-2"
        for p in insert["patches"]
    )
    delete = next(f for f in frames["remove_item"] if f.get("type") == "patch")
    assert any(p["type"] == "RemoveChild" for p in delete["patches"])


@pytest.mark.parametrize("view_class", [StreamView, LegacyStreamView])
async def test_explicit_and_legacy_stream_frames_match(staged, view_class):
    """Explicit mode changes no stream patch; the legacy run proves the path ran."""
    _, _, mount_frame, frames, _ = await run_stream_events(view_class)
    patches = {
        event: [f["patches"] for f in sent if f.get("type") == "patch"]
        for event, sent in frames.items()
    }
    assert patches["add_item"] and patches["remove_item"]
    if view_class is StreamView:
        _, _, _, legacy_frames, _ = await run_stream_events(LegacyStreamView)
        assert patches == {
            event: [f["patches"] for f in sent if f.get("type") == "patch"]
            for event, sent in legacy_frames.items()
        }


async def test_stream_item_fields_reach_no_explicit_destination(staged, settings):
    request, runtime, mount_frame, frames, _ = await run_stream_events(StreamView)
    view = runtime.view_instance
    # Positive control: the undeclared field is really on the stream items.
    assert SECRET in json.dumps(view.get_context_data()["streams"])

    # Frames, including the signed client snapshots the explicit view emits.
    wire = json.dumps([mount_frame, frames])
    assert SECRET not in wire
    tokens = [mount_frame["state_snapshot_signed"]] + [
        f["state_snapshot_signed"]
        for sent in frames.values()
        for f in sent
        if f.get("state_snapshot_signed")
    ]
    assert all(tokens) and len(tokens) == 3
    codec = await sync_to_async(snapshot_codec)(view, request)
    for token in tokens:
        assert SECRET not in token and "Second" not in token
        assert codec.restore(token) == {"mode": "list"}

    # Server persistence: the adapter's validated load and the raw session.
    fresh = await sync_to_async(make_request)(request.session.session_key)
    adapter = await sync_to_async(server_state_adapter)(view, fresh)
    assert await sync_to_async(adapter.load)() == {"count": 3}
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert raw  # the explicit envelope was written
    assert SECRET not in json.dumps(raw) and "Second" not in json.dumps(raw)

    # Debug destinations.
    assert explicit_debug_projection(view) == {"count": "[redacted]", "mode": "list"}
    for payload in (view.get_debug_info(), view.get_debug_update()):
        assert SECRET not in json.dumps(payload, default=str)
    settings.DEBUG = True
    register_view("exposure-stream-test", view)
    try:
        from django.test import RequestFactory

        response = await sync_to_async(view_assigns)(
            RequestFactory().get("/debug/", {"session_id": "exposure-stream-test"})
        )
    finally:
        unregister_view("exposure-stream-test")
    assert response.status_code == 200
    assert json.loads(response.content)["assigns"] == {"count": "[redacted]", "mode": "list"}
    assert SECRET.encode() not in response.content
