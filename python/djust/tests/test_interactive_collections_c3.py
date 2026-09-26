"""ADR-034 C3: keyed collections of interactive dropdowns (gate 5's proof).

``rows = DropdownMenu.collection()`` declares the collection. Its members
come only from ``self.rows.sync([(key, DropdownMenu(...)), ...])``, and
``@rows.on.selected`` receives whichever member emitted. The owner's C3
decisions:

- Q1: ``sync()`` runs anywhere, including the collection's own callback.
- Q2: ``get()``, ``len()`` and iteration are the lookup API.
- Q4: each member is configured separately.
- Q5: declarations are copied, never bound.
- Q6: no size cap.

These tests cover D5's lifecycle:

- ordered pairs, and duplicate keys refused before anything changes;
- retained keys keep their state and take the new configuration;
- reordering never transfers state;
- removal and re-addition create a new lifetime, so stale events are refused;
- restore never resurrects a removed member.
"""

import json

import pytest
from asgiref.sync import async_to_sync
from django.test import RequestFactory

from djust import LiveView
from djust.components.interactive import DropdownMenu

CALLS: list = []
ROWS: dict = {}


def menu(label, *values, visibility="server", disabled=()):
    return DropdownMenu(
        label=label,
        items=[{"label": v.title(), "value": v, "disabled": v in disabled} for v in values],
        visibility=visibility,
    )


def default_rows():
    return [
        ("p-42", menu("Project 42", "details", "remove")),
        ("p-87", menu("Project 87", "details")),
    ]


class RowsPage(LiveView):
    template = (
        "<div dj-root>{% for row in rows.values %}<section>{{ row }}</section>{% endfor %}"
        "{{ fixed }}<p>{{ result }}</p><i>{{ calls }}</i></div>"
    )
    enable_state_snapshot = True
    rows = DropdownMenu.collection()
    fixed = DropdownMenu(label="Fixed", items=[{"label": "F", "value": "f"}])

    def mount(self, request, **kwargs):
        self.result = "none"
        self.calls = 0
        self.rows.sync(ROWS.get("rows") or default_rows())

    @rows.on.selected
    def on_rows_selected(self, component: DropdownMenu, value: str) -> None:
        CALLS.append(("rows", component.key, value, component))
        self.calls += 1
        self.result = "%s:%s" % (component.key, value)
        if value == "remove":
            # Q1: a collection's own callback may sync, even removing its member.
            self.rows.sync(
                [
                    (m.key, menu(m.label, "details", visibility=m.visibility))
                    for m in self.rows
                    if m.key != component.key
                ]
            )

    @fixed.on.selected
    def on_fixed_selected(self, component: DropdownMenu, value: str) -> None:
        CALLS.append(("fixed", component.key, value, component))


@pytest.fixture(autouse=True)
def _reset():
    CALLS.clear()
    ROWS.clear()
    yield
    CALLS.clear()
    ROWS.clear()


def mounted():
    view = RowsPage()
    view.mount(None)
    return view


# --------------------------------------------------------------------------
# The collection API (Q6, C3-Q2).
# --------------------------------------------------------------------------


def test_members_come_from_sync_in_order():
    view = mounted()
    keys = [member.key for member in view.rows.values]
    assert keys == ["p-42", "p-87"]
    assert [m.key for m in view.rows] == keys
    assert len(view.rows) == 2
    assert view.rows.get("p-42") is view.rows.values[0]
    assert view.rows.get("missing") is None
    assert view.rows.get(42) is None
    # Members are registered components with opaque identities, not their keys.
    for member in view.rows:
        assert view._components[member.component_id] is member
        assert member.component_id != member.key


def test_declaration_cannot_sync_and_views_are_isolated():
    with pytest.raises(RuntimeError):
        RowsPage.rows.sync(default_rows())
    first, second = mounted(), mounted()
    first.rows.get("p-42").open = True
    assert not second.rows.get("p-42").open
    assert first.rows is not second.rows
    assert first.rows.get("p-42") is not second.rows.get("p-42")


@pytest.mark.parametrize(
    "pairs, error",
    [
        ([("a", menu("A")), ("a", menu("B"))], ValueError),
        ([("", menu("A"))], TypeError),
        ([(1, menu("A"))], TypeError),
        ([("a", "not a menu")], TypeError),
        ([("a", menu("A"), "extra")], TypeError),
        (["ab"], TypeError),
        ("ab", TypeError),
    ],
)
def test_invalid_sync_changes_nothing(pairs, error):
    view = mounted()
    before = [(m.key, m.component_id) for m in view.rows]
    registry = dict(view._components)
    with pytest.raises(error):
        view.rows.sync(pairs)
    assert [(m.key, m.component_id) for m in view.rows] == before
    assert view._components == registry


# --------------------------------------------------------------------------
# Reconciliation (D5).
# --------------------------------------------------------------------------


def test_reorder_keeps_each_members_state_and_identity():
    view = mounted()
    a, b = view.rows.get("p-42"), view.rows.get("p-87")
    a.open = True
    view.rows.sync(
        [("p-87", menu("Project 87", "details")), ("p-42", menu("Project 42", "details"))]
    )
    assert [m.key for m in view.rows] == ["p-87", "p-42"]
    assert view.rows.get("p-42") is a and a.open
    assert view.rows.get("p-87") is b and not b.open


def test_retained_member_takes_new_configuration_and_drops_an_invalid_selection():
    view = mounted()
    member = view.rows.get("p-42")
    async_to_sync(member.select)(value="details")
    assert member.selected == "details"
    CALLS.clear()
    view.rows.sync([("p-42", menu("Renamed", "other")), ("p-87", menu("Project 87", "details"))])
    assert view.rows.get("p-42") is member
    assert (member.label, member.selected) == ("Renamed", "")
    assert CALLS == [], "normalizing a selection must not emit an output"


def test_changing_visibility_starts_a_new_member_lifetime():
    view = mounted()
    old = view.rows.get("p-42")
    view.rows.sync([("p-42", menu("Project 42", "details", visibility="client"))])
    new = view.rows.get("p-42")
    assert new is not old and new.visibility == "client"
    assert old.component_id not in view._components


def test_declarations_are_configuration_only_and_reusable():
    view = mounted()
    shared = menu("Shared", "details")
    view.rows.sync([("x", shared), ("y", shared)])
    x, y = view.rows.get("x"), view.rows.get("y")
    assert x is not shared and y is not shared and x is not y
    assert shared._parent is None and shared._collection is None
    assert shared.component_id not in view._components
    x.open = True
    assert not y.open


def test_removed_member_is_gone_and_readding_the_key_is_a_new_lifetime():
    view = mounted()
    old = view.rows.get("p-42")
    old_id = old.component_id
    view.rows.sync([("p-87", menu("Project 87", "details"))])
    assert view.rows.get("p-42") is None
    assert old_id not in view._components
    with pytest.raises(RuntimeError):
        old.open = True
    view.rows.sync(default_rows())
    readded = view.rows.get("p-42")
    assert readded is not old and readded.component_id != old_id
    assert not readded.open


# --------------------------------------------------------------------------
# Outputs: the member is the source (D1/D3), only the collection's callback.
# --------------------------------------------------------------------------


def test_selection_reaches_the_collection_callback_with_the_member():
    view = mounted()
    member = view.rows.get("p-87")
    async_to_sync(member.select)(value="details")
    assert [(kind, key, value) for kind, key, value, _ in CALLS] == [("rows", "p-87", "details")]
    assert CALLS[0][3] is member
    assert view.result == "p-87:details"


def test_toggled_observes_only_client_members():
    class ObservedRows(LiveView):
        template = "<div dj-root>{% for row in rows.values %}{{ row }}{% endfor %}</div>"
        rows = DropdownMenu.collection()

        @rows.on.toggled
        def on_rows_toggled(self, component: DropdownMenu, open: bool) -> None:
            CALLS.append((component.key, open))

    view = ObservedRows()
    view.rows.sync([("s", menu("S", "a")), ("c", menu("C", "a", visibility="client"))])
    server, client = view.rows.get("s"), view.rows.get("c")
    assert client._observes_toggle()
    html = client.render()
    assert "data-dj-observe-toggle" in html
    with pytest.raises(ValueError):
        async_to_sync(server.observe_toggle)(open=True, sequence=1, lifetime="obs_" + "0" * 32)
    async_to_sync(client.observe_toggle)(
        open=True, sequence=1, lifetime=client._observation_lifetime
    )
    assert CALLS == [("c", True)]


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_runtime_dispatch_routes_by_identity_and_refuses_stale_members():
    from djust.tests.test_runtime_child_routing_1892 import _make_runtime_with_view

    view = mounted()
    runtime, transport = _make_runtime_with_view(view)
    target = view.rows.get("p-42")
    old_id = target.component_id

    async def send(component_id, value):
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "select",
                "params": {"component_id": component_id, "value": value},
            }
        )

    await send(old_id, "details")
    assert [(k, key) for k, key, *_ in CALLS] == [("rows", "p-42")]
    # The callback removes its own member (Q1): it finishes, later events fail.
    await send(old_id, "remove")
    assert view.rows.get("p-42") is None
    calls = len(CALLS)
    transport.errors.clear()
    await send(old_id, "details")
    assert len(CALLS) == calls
    assert transport.errors, "a removed member's event must be refused"
    # Re-adding the key is a new lifetime: the old identity stays refused.
    view.rows.sync(default_rows())
    transport.errors.clear()
    await send(old_id, "details")
    assert len(CALLS) == calls and transport.errors
    # Disabled and unknown values never emit.
    await send(view.rows.get("p-87").component_id, "nope")
    assert len(CALLS) == calls


# --------------------------------------------------------------------------
# Persistence and restore (D5): server session yes, signed snapshot no.
# --------------------------------------------------------------------------


def _post(initial, event, params):
    request = RequestFactory().post(
        initial.path,
        data=json.dumps(params),
        content_type="application/json",
        HTTP_X_DJUST_EVENT=event,
    )
    request.user, request.session, request.tenant = initial.user, initial.session, None
    view = RowsPage()
    response = view.post(request)
    return response, view


@pytest.mark.django_db
def test_http_session_round_trip_keeps_members_state_and_never_resurrects():
    from djust.tests.test_exposure_runtime import make_request

    initial = make_request()
    RowsPage().get(initial)
    saved = initial.session["liveview_%s_components" % initial.path]
    assert [m["key"] for m in saved["rows"]["members"]] == ["p-42", "p-87"]
    assert "rows" not in initial.session["liveview_" + initial.path]
    ids = {m["key"]: m["binding_id"] for m in saved["rows"]["members"]}

    response, view = _post(initial, "toggle", {"component_id": ids["p-87"]})
    assert response.status_code == 200, response.content
    assert view.rows.get("p-87").open and view.rows.get("p-87").component_id == ids["p-87"]

    response, view = _post(initial, "select", {"component_id": ids["p-42"], "value": "remove"})
    assert response.status_code == 200, response.content
    assert view.rows.get("p-42") is None

    # The next request restores the latest membership: p-42 stays gone, and
    # its old identity is refused.
    response, view = _post(initial, "select", {"component_id": ids["p-42"], "value": "details"})
    assert response.status_code == 400
    assert view.rows.get("p-42") is None
    assert view.rows.get("p-87").open


@pytest.mark.django_db
def test_invalid_session_record_changes_nothing():
    view = mounted()
    before = [(m.key, m.component_id) for m in view.rows]
    record = view.rows._dump_session_collection()
    for damage in (
        {**record, "version": 2},
        {"version": 1, "members": [{**record["members"][0], "key": ""}]},
        {"version": 1, "members": [record["members"][0], record["members"][0]]},
        {"version": 1, "members": [{**record["members"][0], "items": [{"label": "x"}]}]},
        {"version": 1, "members": [{**record["members"][0], "binding_id": "forged"}]},
        {"version": 1, "members": [{**record["members"][0], "extra": 1}]},
        {
            "version": 1,
            "members": [{**record["members"][0], "binding_id": view.fixed.component_id}],
        },
    ):
        with pytest.raises(ValueError):
            view.rows._restore_session_collection(damage)
        assert [(m.key, m.component_id) for m in view.rows] == before


def test_views_with_collections_are_never_signed_for_back_navigation():
    view = mounted()
    assert view._capture_snapshot_state(strict=True) == {}
    # Debug capture (not signed) still sees member state.
    debug = view._capture_snapshot_state()
    assert set(debug["__components__"]) >= {m.component_id for m in view.rows}


# --------------------------------------------------------------------------
# Rendering in both engines and both policies; change detection.
# --------------------------------------------------------------------------


class ExplicitRowsPage(RowsPage):
    exposure_policy = "explicit"


@pytest.mark.django_db
@pytest.mark.parametrize("view_class", [RowsPage, ExplicitRowsPage])
@pytest.mark.parametrize("engine", ["django", "rust"])
def test_members_render_in_both_engines(view_class, engine):
    from django.template import Context, Engine

    from djust.tests.test_exposure_runtime import make_request

    view = view_class()
    view.request = make_request()
    view.mount(view.request)
    context = dict(view.get_context_data())
    source = "{% for row in rows.values %}<s>{{ row }}</s>{% endfor %}"
    if engine == "django":
        output = Engine().from_string(source).render(Context(context))
    else:
        from djust.template_backend import DjustTemplateBackend

        backend = DjustTemplateBackend(
            params={"NAME": "djust", "DIRS": [], "APP_DIRS": False, "OPTIONS": {}}
        )
        output = backend.from_string(source).render(context)
    for member in view.rows:
        assert 'data-component-id="%s"' % member.component_id in output
        assert member.label in output
    assert output.index("Project 42") < output.index("Project 87")
    assert "&lt;div" not in output


@pytest.mark.django_db
def test_member_state_and_membership_changes_render():
    from djust.tests.test_exposure_runtime import make_request

    request = make_request()
    view = RowsPage()
    response = view.get(request)
    assert response.status_code == 200
    view.request = request
    html, patches, _ = view.render_with_diff(request)
    view.rows.get("p-87").open = True
    html, patches, _ = view.render_with_diff(request)
    assert patches and "dj-dropdown-menu--open" in html
    view.rows.sync([("p-99", menu("Project 99", "details"))])
    html, patches, _ = view.render_with_diff(request)
    assert "Project 99" in html and "Project 42" not in html


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_restore_uses_the_latest_membership_and_rotates_lifetimes():
    """A reconnect restored from the session (mount skipped) brings back the
    last server membership: a member removed after the page loaded stays gone,
    and client members get a fresh observation lifetime."""
    from asgiref.sync import sync_to_async
    from channels.testing import WebsocketCommunicator
    from django.test import override_settings

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    ROWS["rows"] = default_rows() + [("p-c", menu("Client", "details", visibility="client"))]
    initial = await sync_to_async(make_request)()
    await sync_to_async(RowsPage().get)(initial)
    saved = initial.session["liveview_%s_components" % initial.path]["rows"]["members"]
    ids = {m["key"]: m["binding_id"] for m in saved}
    old_lifetime = next(m for m in saved if m["key"] == "p-c")["observation"]["lifetime"]
    response, _ = await sync_to_async(_post)(
        initial, "select", {"component_id": ids["p-42"], "value": "remove"}
    )
    assert response.status_code == 200
    await sync_to_async(initial.session.save)()

    with override_settings(LIVEVIEW_ALLOWED_MODULES=["djust.tests"], DJUST_TENANTS=None):
        socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
        socket.scope.update(session=initial.session, user=initial.user, tenant=None)
        assert (await socket.connect())[0]
        await socket.receive_json_from(timeout=3)
        try:
            await socket.send_json_to(
                {
                    "type": "mount",
                    "view": __name__ + ".RowsPage",
                    "url": initial.path,
                    "has_prerendered": True,
                }
            )
            frame = await socket.receive_json_from(timeout=5)
            assert frame["type"] == "mount", frame
            html = frame["html"]
            assert ids["p-42"] not in html and "Project 42" not in html
            assert ids["p-87"] in html and ids["p-c"] in html
            assert old_lifetime not in html, "a restored client member rotates its lifetime"

            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "select",
                    "ref": 1,
                    "params": {"component_id": ids["p-42"], "value": "details"},
                }
            )
            refused = await socket.receive_json_from(timeout=5)
            assert refused["type"] == "error", refused
            await socket.send_json_to(
                {
                    "type": "event",
                    "event": "select",
                    "ref": 2,
                    "params": {"component_id": ids["p-87"], "value": "details"},
                }
            )
            frames = []
            for _ in range(4):
                frames.append(await socket.receive_json_from(timeout=5))
                if frames[-1]["type"] in {"patch", "html_update", "noop", "error"}:
                    break
            assert "p-87:details" in json.dumps(frames), frames
        finally:
            await socket.disconnect()
