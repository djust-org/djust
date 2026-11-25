"""#4307: the parameter-contract manifest grows with distinct contracts, not
rows, and a manifest the client already holds is not sent again.

Wire version 2 lists each distinct contract and handler table once; each owner
row names its table. A 500-row ``DropdownMenu`` collection used to repeat every
handler's contract per row, so past ~90 rows the manifest went over its 64 KB
limit and the page answered 500. An unchanged manifest goes out as its digest:
on the HTTP fallback when the client names it in ``X-Djust-Parameter-Contracts``,
and on the WebSocket and SSE when the connection last sent it in full.
"""

from __future__ import annotations

import json
import re
import uuid
import zlib
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from asgiref.sync import sync_to_async
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory
from django.urls import path

from djust import LiveView, state
from djust import _parameter_metadata as pm
from djust._parameter_contract import ContractError
from djust._parameter_metadata import (
    _handlers,
    elide_sent_manifest,
    expand_parameter_manifest,
    parameter_contract_manifest,
)
from djust.components.interactive import DropdownMenu
from djust.decorators import event_handler
from djust.tests import _per_turn_harness as harness
from djust.tests import http_fallback_matrix as matrix
from djust.tests.http_fallback_matrix import PAGE, post_event

MODULE = __name__

ITEMS = [{"label": "Edit", "value": "edit"}, {"label": "Archive", "value": "archive"}]
ROW_ITEMS = [{"label": "Details", "value": "details"}, {"label": "Rename", "value": "rename"}]

#: How many rows the ``Big`` view builds (a test may change it before a mount).
ROW_COUNT = [500]


class Big(LiveView):
    """The issue's page: a menu and a 500-row keyed collection of menus."""

    exposure_policy = "explicit"

    template = PAGE % (
        "{{ project_menu }}"
        "<ul>{% for menu in rows.values %}<li>{{ menu }}</li>{% endfor %}</ul>"
        '<p id="picked">{{ selected_action }}|{{ row_selection }}</p>'
    )
    login_required = False
    project_menu = DropdownMenu(label="Project", items=ITEMS)
    rows = DropdownMenu.collection()
    selected_action = state("", persist="server")
    row_selection = state("", persist="server")

    def mount(self, request, **kwargs):
        self._sync(ROW_COUNT[0])

    def _sync(self, count: int) -> None:
        self.rows.sync(
            [("r%03d" % i, DropdownMenu(label="r", items=ROW_ITEMS)) for i in range(count)]
        )

    @event_handler()
    def grow(self, **kwargs):
        self._sync(len(self.rows) + 1)

    @event_handler()
    def note(self, **kwargs):
        self.selected_action += "n"

    @rows.on.selected
    def on_rows_selected(self, component: DropdownMenu, value: str) -> None:
        self.row_selection = "%s:%s" % (component.key, value)


urlpatterns = [path("big/", Big.as_view()), *matrix.urlpatterns]

_ID = re.compile(r'data-component-id="([^"]+)"')
_BLOCK = re.compile(r"<script[^>]*data-djust-parameter-contracts>(.*?)</script>", re.S)
_TAB = re.compile(r'<meta name="djust-tab" content="([^"]+)">')


@pytest.fixture
def routes():
    ROW_COUNT[0] = 500
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE, ALLOWED_HOSTS=["*"]):
        yield


def _page_manifest(html: str) -> Any:
    return json.loads(_BLOCK.search(html).group(1))["contracts"]


# --------------------------------------------------------------------------- #
# The encoder
# --------------------------------------------------------------------------- #


class _Root:
    @event_handler()
    def note(self, **kwargs):
        pass


def _rows_root(count: int, order=range) -> Any:
    """A tree like a view with ``count`` DropdownMenu rows, without a request."""
    menus = {"r%03d" % i: DropdownMenu(label="r", items=ROW_ITEMS) for i in order(count)}
    root = _Root()
    root._components = menus
    root._child_views = {}
    return root


def _size(manifest: Dict[str, Any]) -> int:
    return len(json.dumps(manifest, separators=(",", ":")).encode())


def test_rows_share_one_table_and_the_manifest_grows_by_the_row_id():
    manifest = parameter_contract_manifest(_rows_root(500))
    assert manifest["version"] == 2
    assert len(manifest["owners"]) == 501
    # The root's table and the one every DropdownMenu row shares.
    assert len(manifest["handlers"]) == 2
    distinct = {
        json.dumps(record, sort_keys=True)
        for owner in (_Root(), DropdownMenu(label="r", items=ROW_ITEMS))
        for record in _handlers(owner).values()
    }
    assert len(manifest["contracts"]) <= len(distinct)
    assert _size(manifest) < 64 * 1024
    per_row = (_size(manifest) - _size(parameter_contract_manifest(_rows_root(400)))) / 100
    assert per_row < 100 + len("r000"), per_row


def test_the_v2_manifest_expands_to_each_owners_own_handlers():
    root = _rows_root(5)
    expanded = expand_parameter_manifest(parameter_contract_manifest(root))
    by_owner = {(o["view_id"], o["component_id"]): o["handlers"] for o in expanded}
    expected = {(None, None): _handlers(root)}
    expected.update({(None, key): _handlers(menu) for key, menu in root._components.items()})
    assert by_owner == expected


def test_the_digest_is_stable_and_follows_the_content():
    first = parameter_contract_manifest(_rows_root(50))
    assert parameter_contract_manifest(_rows_root(50))["digest"] == first["digest"]
    reverse = parameter_contract_manifest(_rows_root(50, lambda n: reversed(range(n))))
    assert reverse == first
    assert re.fullmatch(r"[0-9a-f]{16}", first["digest"])
    assert parameter_contract_manifest(_rows_root(51))["digest"] != first["digest"]

    class Other(_Root):
        @event_handler()
        def note(self, value: int = 0, **kwargs):
            pass

    changed = _rows_root(50)
    changed.__class__ = Other
    assert parameter_contract_manifest(changed)["digest"] != first["digest"]


def _owner(handlers: Dict[str, Any]) -> Dict[str, Any]:
    return {"view_id": None, "component_id": None, "handlers": handlers}


def _record(name: str = "value") -> Dict[str, Any]:
    return {
        "policy": "strict",
        "coerce_types": True,
        "parameters": [
            {
                "name": name,
                "type": "str",
                "kind": "positional_or_keyword",
                "required": True,
                "reduced_checking": False,
            }
        ],
    }


@pytest.mark.parametrize(
    "cap,value,owners",
    [
        ("MAX_OWNERS", 2, [_owner({"a": _record()}) for _ in range(3)]),
        ("MAX_CONTRACTS", 1, [_owner({"a": _record("x"), "b": _record("y")})]),
        ("MAX_HANDLER_TABLES", 1, [_owner({"a": _record()}), _owner({"b": _record()})]),
        ("MAX_HANDLERS", 2, [_owner({"a": _record()}), _owner({"b": _record(), "c": _record()})]),
        ("MAX_DECLARATION_BYTES", 50, [_owner({"secret_name": _record("secret_param")})]),
        ("MAX_MANIFEST_BYTES", 150, [_owner({"a": _record()})]),
    ],
)
def test_each_cap_raises_a_redacted_contract_error(monkeypatch, cap, value, owners):
    monkeypatch.setattr(pm, cap, value)
    for i, owner in enumerate(owners):
        owner["component_id"] = None if i == 0 else "secret_id_%d" % i
    with pytest.raises(ContractError) as raised:
        pm._encode_manifest(owners)
    assert "secret" not in str(raised.value)


def test_a_manifest_under_every_cap_encodes():
    assert pm._encode_manifest([_owner({"a": _record()})])["version"] == 2


# --------------------------------------------------------------------------- #
# HTTP: the issue's 500-row page
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
def test_a_500_row_page_renders_and_a_row_event_runs_over_http(routes):
    client = Client()
    got = client.get("/big/")
    assert got.status_code == 200, got.content[:300]
    html = got.content.decode()
    manifest = _page_manifest(html)
    assert manifest is not False and manifest["version"] == 2
    assert len(manifest["owners"]) == 502
    row_ids = _ID.findall(html)
    assert len(row_ids) == 501
    answer = post_event(
        client,
        "/big/",
        "select",
        {"component_id": row_ids[500], "value": "details"},
        HTTP_X_DJUST_TAB=_TAB.search(html).group(1),
        HTTP_X_DJUST_PARAMETER_CONTRACTS=manifest["digest"],
    )
    assert answer.status_code == 200, answer.content[:300]
    body = json.loads(answer.content)
    assert body.get("type") != "error", body
    assert "r499:details" in json.dumps(body)
    # The rows did not change, so the answer names the manifest the page holds.
    assert body["parameter_contracts"] == manifest["digest"]


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_500_row_page_mounts_and_a_row_event_runs_over_the_websocket(routes):
    key = await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Big, key)
    try:
        assert mount["parameter_contracts"]["version"] == 2
        assert len(mount["parameter_contracts"]["owners"]) == 502
        row_ids = _ID.findall(mount["html"])
        frame = await harness.event(
            comm, "select", {"component_id": row_ids[500], "value": "details"}
        )
        assert frame["type"] != "error", frame
        assert "r499:details" in json.dumps(frame)
    finally:
        await comm.disconnect()


# --------------------------------------------------------------------------- #
# HTTP: an unchanged manifest is answered with its digest
# --------------------------------------------------------------------------- #


def _counter_page() -> tuple[Client, str]:
    from djust.tests.http_fallback_matrix import meta_digest

    client = Client()
    page = client.get("/m/counter/").content.decode()
    assert meta_digest(page)
    return client, _page_manifest(page)["digest"]


def _counter_answer(header: str | None) -> tuple[Any, Dict[str, Any]]:
    client, _held = _counter_page()
    extra = {} if header is None else {"HTTP_X_DJUST_PARAMETER_CONTRACTS": header}
    response = post_event(client, "/m/counter/", "increment", {}, **extra)
    assert response.status_code == 200, response.content[:300]
    return response, json.loads(response.content)


@pytest.mark.django_db(transaction=True)
def test_a_counter_answer_carries_only_the_digest_the_client_holds(routes):
    _client, held = _counter_page()
    full_response, full = _counter_answer(None)
    response, answer = _counter_answer(held)
    assert answer["parameter_contracts"] == held
    assert answer["parameter_contract_view"] == full["parameter_contract_view"]
    assert "owners" not in json.dumps(answer)
    assert len(response.content) < len(full_response.content) - 600


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("header", [None, "1", "0" * 16, "not-a-digest"])
def test_any_other_header_gets_the_full_manifest(routes, header):
    _response, answer = _counter_answer(header)
    manifest = answer["parameter_contracts"]
    assert isinstance(manifest, dict) and manifest["version"] == 2


def test_a_tree_with_no_handlers_gets_no_field_without_the_header():
    from djust.mixins.request import _http_parameter_contract_fields

    class Bare:
        pass

    request = RequestFactory().post("/")
    assert _http_parameter_contract_fields(Bare(), request) == {}
    held = RequestFactory().post("/", HTTP_X_DJUST_PARAMETER_CONTRACTS="0" * 16)
    fields = _http_parameter_contract_fields(Bare(), held)
    assert fields["parameter_contracts"] is None


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_websocket_sends_an_unchanged_manifest_as_its_digest(routes):
    ROW_COUNT[0] = 3
    key = await sync_to_async(harness.new_session_key)()
    comm, mount = await harness.mount_view(Big, key)
    try:
        first = mount["parameter_contracts"]
        assert isinstance(first, dict)
        frame = await harness.event(comm, "note")
        assert frame["parameter_contracts"] == first["digest"]
        assert frame["parameter_contract_view"] == f"{MODULE}.Big"
        grown = await harness.event(comm, "grow")
        assert isinstance(grown["parameter_contracts"], dict)
        assert grown["parameter_contracts"]["digest"] != first["digest"]
        assert len(grown["parameter_contracts"]["owners"]) == len(first["owners"]) + 1
        again = await harness.event(comm, "note")
        assert again["parameter_contracts"] == grown["parameter_contracts"]["digest"]
    finally:
        await comm.disconnect()


@pytest.mark.asyncio
async def test_recovery_after_an_elided_frame_holds_the_full_manifest():
    from unittest.mock import AsyncMock

    from djust.websocket import LiveViewConsumer

    manifest = parameter_contract_manifest(_rows_root(3))
    consumer = LiveViewConsumer.__new__(LiveViewConsumer)
    consumer._parameter_contract_sent = None
    consumer._send_frame = AsyncMock()
    sent: List[Dict[str, Any]] = []
    for version in (1, 2):
        frame = {
            "type": "patch",
            "version": version,
            "parameter_contracts": manifest,
            "parameter_contract_view": "x.V",
        }
        consumer._recovery_version = version
        consumer._capture_recovery_contracts(frame)
        await consumer.send_json(frame)
        sent.append(json.loads(consumer._send_frame.call_args.kwargs["text_data"]))
        assert frame["parameter_contracts"] is manifest  # the caller's dict is untouched
    assert sent[0]["parameter_contracts"] == manifest
    assert sent[1]["parameter_contracts"] == manifest["digest"]
    held = json.loads(zlib.decompress(consumer._recovery_contracts))
    assert held["parameter_contracts"] == manifest


def test_elision_copies_and_reports_only_a_dict_manifest():
    holder = SimpleNamespace(_parameter_contract_sent=None)
    frame = {"type": "patch", "parameter_contracts": {"digest": "ab" * 8}}
    assert elide_sent_manifest(holder, frame) == (frame, "ab" * 8)
    holder._parameter_contract_sent = "ab" * 8
    out, sent = elide_sent_manifest(holder, frame)
    assert out == {"type": "patch", "parameter_contracts": "ab" * 8} and sent is None
    assert isinstance(frame["parameter_contracts"], dict)
    for value in (None, "x", {"version": 2}):
        same = {"parameter_contracts": value}
        assert elide_sent_manifest(holder, same) == (same, None)
    assert elide_sent_manifest(holder, {"type": "noop"}) == ({"type": "noop"}, None)


# --------------------------------------------------------------------------- #
# SSE
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_sse_sends_an_unchanged_manifest_as_its_digest(routes):
    from django.contrib.auth import get_user
    from django.utils.functional import SimpleLazyObject

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    from .test_sse_runtime_convergence_1887 import _drain

    ROW_COUNT[0] = 3

    def request_for(method: str, target: str, body: Any, key: str | None = None) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(target, data=body)
        else:
            made = factory.post(target, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        if key is None:
            made.session.create()
        made.session = SessionStore(made.session.session_key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    sid = str(uuid.uuid4())
    first = await sync_to_async(request_for)(
        "GET", f"/djust/sse/{sid}/", {"view": f"{MODULE}.Big", "_djust_url": "/big/"}
    )
    assert (await DjustSSEStreamView().get(first, session_id=sid)).status_code == 200
    key = first.session.session_key
    session = _sse_sessions[sid]

    async def event(name: str) -> Dict[str, Any]:
        body = {"type": "event", "event": name, "params": {}}
        posted = await sync_to_async(request_for)("POST", f"/djust/sse/{sid}/message/", body, key)
        await DjustSSEMessageView().post(posted, session_id=sid)
        frames = [f for f in _drain(session) if "parameter_contracts" in f]
        assert frames, name
        return frames[-1]

    try:
        (mount,) = [m for m in _drain(session) if m.get("type") == "mount"]
        manifest = mount["parameter_contracts"]
        assert isinstance(manifest, dict)
        assert (await event("note"))["parameter_contracts"] == manifest["digest"]
        grown = (await event("grow"))["parameter_contracts"]
        assert isinstance(grown, dict) and grown["digest"] != manifest["digest"]
        assert (await event("note"))["parameter_contracts"] == grown["digest"]
    finally:
        _sse_sessions.pop(sid, None)


@pytest.mark.asyncio
async def test_an_sse_push_from_another_loop_is_full_and_not_recorded(monkeypatch):
    import asyncio
    import threading

    from djust import multiloop
    from djust.sse import SSESession

    session = SSESession(str(uuid.uuid4()))
    session._loop = asyncio.get_running_loop()
    monkeypatch.setattr(multiloop, "is_multi_loop", lambda: True)
    manifest = {"version": 2, "digest": "cd" * 8}
    frame = {"type": "patch", "parameter_contracts": manifest}
    session.push(frame)
    assert session._parameter_contract_sent == "cd" * 8
    thread = threading.Thread(target=session.push, args=(dict(frame),))
    thread.start()
    thread.join()
    await asyncio.sleep(0)
    assert session._parameter_contract_sent is None
    queued = [session.queue.get_nowait() for _ in range(session.queue.qsize())]
    assert [q["parameter_contracts"] for q in queued] == [manifest, manifest]
    # Back on the loop, the next manifest goes out in full again.
    session.push(dict(frame))
    assert session.queue.get_nowait()["parameter_contracts"] == manifest
