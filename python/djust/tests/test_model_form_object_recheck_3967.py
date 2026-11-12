"""#3967: ``ModelFormMixin`` read-only events and a lookup from params.

ADR-017 re-resolves and re-authorizes the record before EVERY event. That is right for an
event that saves, and costs a query per keystroke for ``validate_field``. Two opt-ins, and
one guarantee that no setting weakens:

* ``object_recheck = "writes"``: only an event declared read-only (the framework's
  ``validate_field``, plus ``object_read_only_events``) reuses the record authorized earlier
  on the connection. Every other event, whatever it is called and whatever it does, gets the
  full check. The tests below run the write events under BOTH settings.
* ``get_object_lookup()`` / ``object_optional``: the record comes from a query parameter
  kept in ``handle_params``, and "no record selected" is a state, not a denial.

Every transport that carries events is exercised for what it can reach: a real WebSocket
session (the consumer's own security chain), ``LiveViewTestClient`` (its mirror of it), and
the HTTP POST fallback (a fresh view per request, which can never reuse anything).
"""

import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust.decorators import event_handler
from djust.forms import ModelFormMixin
from djust.testing import LiveViewTestClient

EVENTS: list = []
REVOKED: set = set()
DENIED = "Access denied for this object."


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ["name"]


class _Recording(ModelFormMixin[Group], LiveView):
    """Records every lookup and permission check; ``REVOKED`` ids lose access."""

    exposure_policy = "legacy"
    template = (
        '<div dj-root><h1>{{ object.name }}</h1><input name="name" '
        'value="{{ form_data.name }}"><i>{{ field_errors.name.0 }}</i>'
        "<p>{{ success_message }}</p></div>"
    )
    model = Group
    form_class = GroupForm
    login_required = True

    def get_queryset(self):
        EVENTS.append(("lookup",))
        return super().get_queryset().filter(user=self.request.user)

    def has_object_permission(self, request, obj):
        EVENTS.append(("permission", obj.pk))
        return obj.pk not in REVOKED

    def form_valid(self, form):
        EVENTS.append(("form_valid", form.instance.pk))
        self.object = form.save()
        self.success_message = "Saved " + self.object.name


class EveryEvent(_Recording):
    """The default: every event re-resolves and re-authorizes."""


class Writes(_Recording):
    object_recheck = "writes"

    object_read_only_events = frozenset({"peek"})

    @event_handler()
    def peek(self, **kwargs):
        """Declared read-only: shows nothing but the authorized record."""
        EVENTS.append(("peek",))

    @event_handler()
    def rename(self, name="", **kwargs):
        """A handler of the app's, not declared read-only: it writes, so it is a write."""
        EVENTS.append(("rename", self.object.pk))
        self.object.name = name
        self.object.save()

    @event_handler()
    def mutate_in_memory(self, name="", **kwargs):
        """Changes the record without saving it, as an app handler might."""
        self.object.name = name


class WritesOverridden(Writes):
    """Overrides ``validate_field``: it is not the framework's read-only one any more."""

    @event_handler
    def validate_field(self, field="", field_name="", value=None, **kwargs):
        EVENTS.append(("custom_validate", self.object.pk))
        self.object.name = value or ""
        self.object.save()


class Queue(_Recording):
    """The record comes from ``?open=<id>``, kept by ``handle_params``."""

    object_optional = True
    object_recheck = "writes"
    template = _Recording.template.replace("<h1>", "<h1>[{{ open_id }}]")

    def mount(self, request, **kwargs):
        self.open_id = None
        super().mount(request, **kwargs)

    def handle_params(self, params, uri):
        self.open_id = params.get("open") or None

    def get_object_lookup(self):
        return {self.pk_url_kwarg: getattr(self, "open_id", None)}


class QueueEveryEvent(Queue):
    object_recheck = "every_event"


class QueueRequired(Queue):
    """A lookup from params WITHOUT ``object_optional``: no selection is a denial."""

    object_optional = False


class QueueMountSelects(Queue):
    """``mount()`` itself selects a record (``mount_selects``); ``handle_params`` then
    replaces it with the one the URL names. The object checked must be the LAST one."""

    mount_selects = None

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)
        self.open_id = type(self).mount_selects


urlpatterns = [
    path("queue-mount/", QueueMountSelects.as_view()),
    path("every/<int:pk>/", EveryEvent.as_view()),
    path("writes/<int:pk>/", Writes.as_view()),
    path("overridden/<int:pk>/", WritesOverridden.as_view()),
    path("queue/", Queue.as_view()),
    path("queue-every/", QueueEveryEvent.as_view()),
    path("queue-required/", QueueRequired.as_view()),
]

ROUTES = {
    EveryEvent: "/every/%s/",
    Writes: "/writes/%s/",
    WritesOverridden: "/overridden/%s/",
}


@pytest.fixture(autouse=True)
def _levers():
    EVENTS.clear()
    REVOKED.clear()
    with override_settings(
        ROOT_URLCONF=__name__, DJUST_TENANTS=None, LIVEVIEW_ALLOWED_MODULES=[__name__]
    ):
        yield
    EVENTS.clear()
    REVOKED.clear()


def _world():
    from django.contrib.auth import get_user_model

    tag = uuid.uuid4().hex[:8]
    alice = get_user_model().objects.create_user(username="alice-" + tag)
    mine = Group.objects.create(name="Mine " + tag)
    theirs = Group.objects.create(name="Theirs " + tag)
    alice.groups.add(mine)
    return alice, mine, theirs


@pytest.fixture
def sql(monkeypatch):
    """Every SQL statement, from whichever thread and connection runs it."""
    from django.db.backends import utils

    statements: list = []
    original = utils.CursorWrapper._execute

    def record(self, query, params, *args):
        statements.append(query)
        return original(self, query, params, *args)

    monkeypatch.setattr(utils.CursorWrapper, "_execute", record)
    return statements


def _kinds(*kinds):
    return [event[0] for event in EVENTS if event[0] in kinds]


async def _open(view_class, user, url, params=None):
    from channels.testing import WebsocketCommunicator
    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {
            "type": "mount",
            "view": __name__ + "." + view_class.__name__,
            "url": url,
            "params": params or {},
        }
    )
    return socket, await socket.receive_json_from()


async def _send(socket, message):
    await socket.send_json_to(message)
    frames = []
    for _ in range(6):
        frame = await socket.receive_json_from()
        frames.append(frame)
        if frame.get("type") in {"patch", "html_update", "error", "noop", "navigate"}:
            break
    return frames


async def _event(socket, ref, event, params):
    return await _send(socket, {"type": "event", "event": event, "params": params, "ref": ref})


async def _url_change(socket, url, params):
    return await _send(socket, {"type": "url_change", "params": params, "uri": url})


def _denied(frames):
    return frames[-1].get("type") == "error" and frames[-1].get("code") == "permission_denied"


async def _name(group):
    """The name stored in the database now. Not ``group.refresh_from_db()``: that would update
    ``group.name`` itself, and ``_name(g) == g.name`` would compare a value with itself."""
    return await sync_to_async(lambda: Group.objects.get(pk=group.pk).name)()


# --------------------------------------------------------------------------
# object_recheck: the default is unchanged
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_default_resolves_and_authorizes_before_every_event_including_validate_field():
    alice, mine, _ = await sync_to_async(_world)()
    socket, mounted = await _open(EveryEvent, alice, ROUTES[EveryEvent] % mine.pk)
    try:
        assert mounted["type"] == "mount", mounted
        for ref in range(3):
            EVENTS.clear()
            frames = await _event(socket, ref, "validate_field", {"field": "name", "value": "x"})
            assert not _denied(frames), frames
            assert _kinds("lookup", "permission") == ["lookup", "permission"], EVENTS
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------
# object_recheck = "writes": read-only events reuse, everything else re-checks
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_validate_field_reuses_the_authorized_record_and_runs_no_query(sql):
    alice, mine, _ = await sync_to_async(_world)()
    socket, mounted = await _open(Writes, alice, ROUTES[Writes] % mine.pk)
    try:
        assert mounted["type"] == "mount", mounted
        EVENTS.clear()
        sql.clear()
        for ref in range(5):
            frames = await _event(
                socket, ref, "validate_field", {"field": "name", "value": "Typing" * ref}
            )
            assert not _denied(frames), frames
        assert _kinds("lookup", "permission") == [], EVENTS
        assert [q for q in sql if "auth_group" in q] == [], sql
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_validate_field_still_validates_while_the_record_is_reused():
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(Writes, alice, ROUTES[Writes] % mine.pk)
    try:
        frames = await _event(socket, 1, "validate_field", {"field": "name", "value": ""})
        assert "required" in json.dumps(frames).lower(), frames
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("view_class", [EveryEvent, Writes])
async def test_submit_form_resolves_and_authorizes_again_under_every_setting(view_class):
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(view_class, alice, ROUTES[view_class] % mine.pk)
    try:
        # A read-only event first, so that under "writes" a verdict is on hand to reuse.
        await _event(socket, 1, "validate_field", {"field": "name", "value": "Typed"})
        EVENTS.clear()
        frames = await _event(socket, 2, "submit_form", {"name": "Saved once"})
        assert not _denied(frames), frames
        assert _kinds("lookup", "permission", "form_valid") == [
            "lookup",
            "permission",
            "form_valid",
        ], EVENTS
        assert await _name(mine) == "Saved once"
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "event, params, handler_wrote",
    [
        ("submit_form", {"name": "Hijack"}, "form_valid"),
        ("reset_form", {}, None),
        ("rename", {"name": "Hijack"}, "rename"),
    ],
)
@pytest.mark.parametrize("view_class", [EveryEvent, Writes])
async def test_a_write_by_a_user_whose_access_was_revoked_is_denied_under_every_setting(
    view_class, event, params, handler_wrote
):
    """The guarantee: revoked between a read-only event and a write, the write is refused."""
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(view_class, alice, ROUTES[view_class] % mine.pk)
    try:
        await _event(socket, 1, "validate_field", {"field": "name", "value": "Typed"})
        REVOKED.add(mine.pk)
        EVENTS.clear()
        frames = await _event(socket, 2, event, params)
        if event == "rename" and view_class is EveryEvent:
            # The default view has no ``rename`` handler at all: refused as unknown.
            assert frames[-1]["type"] == "error", frames
            return
        assert _denied(frames), frames
        assert frames[-1].get("error") == DENIED, frames
        assert handler_wrote is None or _kinds(handler_wrote) == [], EVENTS
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_revocation_is_seen_by_the_next_event_that_is_not_read_only():
    """The documented trade of "writes": read-only events keep working until a write."""
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(Writes, alice, ROUTES[Writes] % mine.pk)
    try:
        REVOKED.add(mine.pk)
        frames = await _event(socket, 1, "validate_field", {"field": "name", "value": "x"})
        assert not _denied(frames), frames
        frames = await _event(socket, 2, "submit_form", {"name": "x"})
        assert _denied(frames), frames
        # And the denial leaves nothing to reuse: the next read-only event re-checks too.
        EVENTS.clear()
        frames = await _event(socket, 3, "validate_field", {"field": "name", "value": "x"})
        assert _denied(frames), frames
        assert _kinds("lookup") == ["lookup"], EVENTS
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_handler_declared_read_only_reuses_and_one_that_is_not_does_not():
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(Writes, alice, ROUTES[Writes] % mine.pk)
    try:
        EVENTS.clear()
        await _event(socket, 1, "peek", {})
        assert _kinds("lookup", "permission", "peek") == ["peek"], EVENTS
        EVENTS.clear()
        await _event(socket, 2, "rename", {"name": "Renamed"})
        assert _kinds("lookup", "permission", "rename") == ["lookup", "permission", "rename"]
        assert await _name(mine) == "Renamed"
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_overridden_validate_field_is_a_write_and_is_checked_in_full():
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(WritesOverridden, alice, ROUTES[WritesOverridden] % mine.pk)
    try:
        REVOKED.add(mine.pk)
        EVENTS.clear()
        frames = await _event(socket, 1, "validate_field", {"field": "name", "value": "Hijack"})
        assert _denied(frames), frames
        assert _kinds("custom_validate") == [], EVENTS
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "event, params",
    [
        ("submit_form", {"name": "Saved, so changed"}),  # validation and save edit the record
        ("mutate_in_memory", {"name": "Edited, not saved"}),
    ],
)
async def test_a_record_changed_in_memory_is_not_reused(event, params):
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(Writes, alice, ROUTES[Writes] % mine.pk)
    try:
        await _event(socket, 1, event, params)
        EVENTS.clear()
        await _event(socket, 2, "validate_field", {"field": "name", "value": "x"})
        assert _kinds("lookup", "permission") == ["lookup", "permission"], EVENTS
        # ...and the fresh record is trusted again.
        EVENTS.clear()
        await _event(socket, 3, "validate_field", {"field": "name", "value": "y"})
        assert _kinds("lookup", "permission") == [], EVENTS
    finally:
        await socket.disconnect()


# --------------------------------------------------------------------------
# The reuse decision itself, against the other things it depends on
# --------------------------------------------------------------------------


def _mounted(view_class, user, pk, **route):
    request = RequestFactory().get("/")
    request.user, request.tenant = user, None
    view = view_class()
    view.setup(request, pk=pk)
    view._djust_bind_route_kwargs({"pk": pk, **route})
    view.mount(request, pk=pk)
    from djust.auth.core import enforce_object_permission

    enforce_object_permission(view, request)
    return view, request


@pytest.mark.django_db
def test_reuse_is_refused_for_another_user_another_lookup_or_a_changed_record():
    from django.contrib.auth import get_user_model

    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)
    handler = view.validate_field
    assert view._djust_reuses_authorized_object(handler, request) is True

    other = RequestFactory().get("/")
    other.user = get_user_model().objects.create_user(username="eve-" + uuid.uuid4().hex[:6])
    assert view._djust_reuses_authorized_object(handler, other) is False

    view.kwargs = {"pk": mine.pk + 1}
    assert view._djust_reuses_authorized_object(handler, request) is False
    view.kwargs = {"pk": mine.pk}
    assert view._djust_reuses_authorized_object(handler, request) is True

    view.object.name = "changed in memory"
    assert view._djust_reuses_authorized_object(handler, request) is False


@pytest.mark.django_db
def test_reuse_is_refused_for_every_handler_that_is_not_read_only():
    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)
    for name in ("submit_form", "reset_form", "rename", "mutate_in_memory"):
        assert view._djust_reuses_authorized_object(getattr(view, name), request) is False, name
    assert view._djust_reuses_authorized_object(view.peek, request) is True


@pytest.mark.django_db
def test_reuse_is_refused_without_a_stamp_so_a_restored_view_resolves_first():
    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)
    assert view._djust_reuses_authorized_object(view.validate_field, request) is True
    # A view that never resolved a record (or lost its stamp) has nothing to reuse.
    view._djust_object_stamp = None
    assert view._djust_reuses_authorized_object(view.validate_field, request) is False


@pytest.mark.django_db
def test_invalidating_the_object_cache_ends_reuse_with_the_stamp_still_in_place():
    """``_invalidate_object_cache()`` clears ``_object`` and nothing else: the stamp it leaves
    behind must not let the next read-only event run without a record."""
    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)
    assert view._djust_object_stamp is not None
    view._invalidate_object_cache()
    assert view._djust_object_stamp is not None
    assert view._djust_reuses_authorized_object(view.validate_field, request) is False


@pytest.mark.django_db
def test_the_default_mode_never_reuses():
    alice, mine, _ = _world()
    view, request = _mounted(EveryEvent, alice, mine.pk)
    assert view._djust_reuses_authorized_object(view.validate_field, request) is False


@pytest.mark.django_db
def test_a_hook_that_raises_means_a_full_check(monkeypatch):
    from djust.auth.core import check_object_permission_for_event

    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)

    def boom(handler, request):
        raise RuntimeError("hook failed")

    monkeypatch.setattr(view, "_djust_reuses_authorized_object", boom)
    EVENTS.clear()
    check_object_permission_for_event(view, request, view.validate_field)
    assert _kinds("lookup", "permission") == ["lookup", "permission"]


@pytest.mark.django_db
def test_the_test_client_follows_the_same_rule_as_the_consumer():
    alice, mine, _ = _world()
    client = LiveViewTestClient(Queue, user=alice)
    client.mount(query_params={"open": str(mine.pk)})
    assert client.send_event("validate_field", field="name", value="a")["success"]
    first = _kinds("lookup", "permission")
    assert first == ["lookup", "permission"], EVENTS  # nothing authorized yet: the full check
    EVENTS.clear()
    assert client.send_event("validate_field", field="name", value="ab")["success"]
    assert _kinds("lookup", "permission") == [], EVENTS  # reused
    result = client.send_event("submit_form", name="Saved in the client")
    assert result["success"], result
    assert _kinds("lookup", "permission", "form_valid") == ["lookup", "permission", "form_valid"]
    REVOKED.add(mine.pk)
    EVENTS.clear()
    result = client.send_event("submit_form", name="Hijack")
    assert result["success"] is False and result["code"] == "permission_denied", result
    assert Group.objects.get(pk=mine.pk).name == "Saved in the client"


@pytest.mark.django_db
def test_http_post_fallback_resolves_a_fresh_view_each_request():
    """No connection, no stamp: the fallback re-checks even under "writes"."""
    from django.contrib.sessions.backends.db import SessionStore

    alice, mine, _ = _world()
    session = SessionStore()
    session.create()
    for _ in range(2):
        EVENTS.clear()
        request = RequestFactory().post(
            ROUTES[Writes] % mine.pk,
            data=json.dumps({"event": "validate_field", "params": {"field": "name", "value": "x"}}),
            content_type="application/json",
        )
        request.user, request.session, request.tenant = alice, session, None
        response = Writes.as_view()(request, pk=mine.pk)
        assert response.status_code == 200, response.content
        assert _kinds("lookup") == ["lookup"], EVENTS


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------


def test_a_bad_recheck_mode_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured, match="object_recheck"):

        class Bad(ModelFormMixin[Group], LiveView):
            exposure_policy = "legacy"
            object_recheck = "sometimes"


@pytest.mark.parametrize("value", ["validate_field", [1, 2], {"ok", 3}, 5])
def test_read_only_events_must_be_a_collection_of_names(value):
    with pytest.raises(ImproperlyConfigured, match="object_read_only_events"):

        class Bad(ModelFormMixin[Group], LiveView):
            exposure_policy = "legacy"
            object_read_only_events = value


def test_the_new_settings_are_configuration_not_template_state():
    from djust.forms import _MODEL_FORM_CONFIGURATION

    assert {"object_recheck", "object_read_only_events", "object_optional"} <= (
        _MODEL_FORM_CONFIGURATION
    )


# --------------------------------------------------------------------------
# A lookup from params, and "no record selected"
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_nothing_selected_mounts_with_no_object_and_no_form():
    alice, mine, theirs = await sync_to_async(_world)()
    socket, mounted = await _open(Queue, alice, "/queue/")
    try:
        assert mounted["type"] == "mount", mounted
        body = json.dumps(mounted)
        assert mine.name not in body and theirs.name not in body
        assert _kinds("permission") == [], EVENTS
        # A submission with no record selected is refused and writes nothing.
        frames = await _event(socket, 1, "submit_form", {"name": "Created from nothing"})
        assert frames[-1]["type"] == "error", frames
        assert not await sync_to_async(Group.objects.filter(name="Created from nothing").exists)()
        # So is validating a field of a form that does not exist...
        frames = await _event(socket, 2, "validate_field", {"field": "name", "value": "Junk"})
        assert frames[-1]["type"] == "error", frames
        # ...and what was sent with nothing open is not kept: selecting a record afterwards
        # starts from ITS values (a submit that adds nothing saves them back unchanged).
        frames = await _url_change(socket, "/queue/?open=%s" % mine.pk, {"open": str(mine.pk)})
        assert not _denied(frames), frames
        frames = await _event(socket, 3, "submit_form", {})
        assert not _denied(frames), frames
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_record_selected_by_a_query_parameter_is_authorized_and_edited():
    alice, mine, _ = await sync_to_async(_world)()
    socket, mounted = await _open(Queue, alice, "/queue/?open=%s" % mine.pk, {"open": str(mine.pk)})
    try:
        assert mounted["type"] == "mount", mounted
        assert mine.name in json.dumps(mounted)
        assert _kinds("lookup") == ["lookup"] and _kinds("permission") == ["permission"], EVENTS
        EVENTS.clear()
        frames = await _event(socket, 1, "validate_field", {"field": "name", "value": "Typing"})
        assert not _denied(frames), frames
        assert _kinds("lookup", "permission") == [], EVENTS  # "writes": reused
        frames = await _event(socket, 2, "submit_form", {"name": "Saved via params"})
        assert "Saved via params" in json.dumps(frames), frames
        assert _kinds("lookup", "permission") == ["lookup", "permission"], EVENTS
        assert await _name(mine) == "Saved via params"
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", ["filtered", "missing", "not_a_key", "revoked"])
async def test_a_selection_that_does_not_resolve_is_the_same_denial_as_a_routed_one(case, caplog):
    alice, mine, theirs = await sync_to_async(_world)()
    chosen = {
        "filtered": str(theirs.pk),
        "missing": str(theirs.pk + 1000),
        "not_a_key": "abc",
        "revoked": str(mine.pk),
    }[case]
    if case == "revoked":
        REVOKED.add(mine.pk)
    with caplog.at_level("ERROR", logger="djust"):
        socket, frame = await _open(Queue, alice, "/queue/?open=" + chosen, {"open": chosen})
    try:
        assert frame["type"] == "error", frame
        assert frame.get("code") == "permission_denied", frame
        assert frame.get("error") == DENIED, frame
        assert mine.name not in json.dumps(frame) and theirs.name not in json.dumps(frame)
        # A malformed key is "not found", not a crash the framework has to catch and log.
        assert [r for r in caplog.records if r.levelname == "ERROR"] == [], caplog.text
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_the_record_authorized_is_the_one_handle_params_selected_not_mounts():
    """``mount()`` selects a forbidden record and ``handle_params`` an allowed one: the
    verdict for the first must not deny (or, reversed, allow) the second."""
    alice, mine, theirs = await sync_to_async(_world)()
    QueueMountSelects.mount_selects = str(theirs.pk)
    try:
        socket, frame = await _open(
            QueueMountSelects, alice, "/queue-mount/?open=%s" % mine.pk, {"open": str(mine.pk)}
        )
        try:
            assert frame["type"] == "mount", frame
            assert mine.name in json.dumps(frame) and theirs.name not in json.dumps(frame)
        finally:
            await socket.disconnect()
    finally:
        QueueMountSelects.mount_selects = None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_record_allowed_at_mount_does_not_authorize_the_one_params_then_select():
    alice, mine, theirs = await sync_to_async(_world)()
    QueueMountSelects.mount_selects = str(mine.pk)
    try:
        socket, frame = await _open(
            QueueMountSelects,
            alice,
            "/queue-mount/?open=%s" % theirs.pk,
            {"open": str(theirs.pk)},
        )
        try:
            assert frame["type"] == "error" and frame.get("code") == "permission_denied", frame
            assert mine.name not in json.dumps(frame) and theirs.name not in json.dumps(frame)
        finally:
            await socket.disconnect()
    finally:
        QueueMountSelects.mount_selects = None


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_without_object_optional_no_selection_is_a_denial():
    alice, mine, _ = await sync_to_async(_world)()
    socket, frame = await _open(QueueRequired, alice, "/queue-required/")
    try:
        assert frame["type"] == "error" and frame.get("code") == "permission_denied", frame
        assert frame.get("error") == DENIED, frame
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_switching_the_selection_never_carries_one_records_input_to_another():
    alice, mine, _ = await sync_to_async(_world)()
    second = await sync_to_async(Group.objects.create)(name="Second " + uuid.uuid4().hex[:6])
    await sync_to_async(alice.groups.add)(second)
    socket, _ = await _open(Queue, alice, "/queue/?open=%s" % mine.pk, {"open": str(mine.pk)})
    try:
        await _event(socket, 1, "validate_field", {"field": "name", "value": "Unsaved edit of one"})
        frames = await _url_change(socket, "/queue/?open=%s" % second.pk, {"open": str(second.pk)})
        assert not _denied(frames), frames
        assert second.name in json.dumps(frames), frames
        # A submit that adds nothing binds the form state as it stands. If the first
        # record's input had followed the selection, this would save it onto the second.
        EVENTS.clear()
        frames = await _event(socket, 2, "submit_form", {})
        assert not _denied(frames), frames
        assert EVENTS == [
            ("lookup",),
            ("permission", second.pk),
            ("form_valid", second.pk),
        ], EVENTS
        assert await _name(second) == second.name
        assert await _name(mine) == mine.name
        # And the second record is edited as itself.
        await _event(socket, 3, "submit_form", {"name": "Second, renamed"})
        assert await _name(second) == "Second, renamed"
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_selection_changed_to_a_forbidden_record_is_denied_and_reuses_nothing():
    alice, mine, theirs = await sync_to_async(_world)()
    socket, _ = await _open(Queue, alice, "/queue/?open=%s" % mine.pk, {"open": str(mine.pk)})
    try:
        await _event(socket, 1, "validate_field", {"field": "name", "value": "x"})
        frames = await _url_change(socket, "/queue/?open=%s" % theirs.pk, {"open": str(theirs.pk)})
        assert _denied(frames), frames
        # The old record's verdict was for another lookup: nothing is reused for this one.
        EVENTS.clear()
        frames = await _event(socket, 2, "validate_field", {"field": "name", "value": "x"})
        assert _denied(frames), frames
        frames = await _event(socket, 3, "submit_form", {"name": "Hijack"})
        assert _denied(frames), frames
        assert await _name(theirs) == theirs.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_deselecting_returns_to_no_record_and_no_form():
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(Queue, alice, "/queue/?open=%s" % mine.pk, {"open": str(mine.pk)})
    try:
        frames = await _url_change(socket, "/queue/", {})
        assert not _denied(frames), frames
        assert mine.name not in json.dumps(frames), frames
        frames = await _event(socket, 1, "submit_form", {"name": "Nothing open"})
        assert frames[-1]["type"] == "error", frames
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_every_event_still_applies_to_a_params_selected_record_by_default():
    alice, mine, _ = await sync_to_async(_world)()
    socket, _ = await _open(
        QueueEveryEvent, alice, "/queue-every/?open=%s" % mine.pk, {"open": str(mine.pk)}
    )
    try:
        EVENTS.clear()
        await _event(socket, 1, "validate_field", {"field": "name", "value": "x"})
        assert _kinds("lookup", "permission") == ["lookup", "permission"], EVENTS
    finally:
        await socket.disconnect()


@pytest.mark.django_db
def test_http_get_selects_by_query_parameter_after_handle_params():
    """The HTTP GET path: mount, handle_params, then the object check, then the render."""
    from django.contrib.sessions.backends.db import SessionStore

    alice, mine, theirs = _world()

    def get(query):
        request = RequestFactory().get("/queue/" + query)
        request.user, request.tenant, request.session = alice, None, SessionStore()
        request.session.create()
        return Queue.as_view()(request)

    assert get("").status_code == 200
    page = get("?open=%s" % mine.pk)
    assert page.status_code == 200 and mine.name in page.content.decode()
    assert get("?open=%s" % theirs.pk).status_code == 403
    assert get("?open=abc").status_code == 403


class QueueSnapshot(Queue):
    """Saved to the session between requests, so a socket can restore it."""


urlpatterns.append(path("queue-snapshot/", QueueSnapshot.as_view()))


async def _restore(view_class, url_path, user, session, selected, typed_for):
    """GET and a typed ``validate_field`` over HTTP for record ``typed_for``, then a socket restore
    of the same session path with ``selected`` chosen in the mount params. Returns the open socket
    (the caller disconnects it) and the mount frame."""
    from channels.testing import WebsocketCommunicator
    from djust.websocket import LiveViewConsumer

    def http(method, body=None):
        url = "%s?open=%s" % (url_path, typed_for.pk)
        if method == "get":
            request = RequestFactory().get(url)
        else:
            request = RequestFactory().post(
                url, data=json.dumps(body), content_type="application/json"
            )
        request.user, request.session, request.tenant = user, session, None
        return view_class.as_view()(request)

    assert (await sync_to_async(http)("get")).status_code == 200
    typed = {"event": "validate_field", "params": {"field": "name", "value": "TYPED-FOR-A"}}
    assert (await sync_to_async(http)("post", typed)).status_code == 200
    await sync_to_async(session.save)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {
            "type": "mount",
            "view": __name__ + "." + view_class.__name__,
            "url": url_path,
            "params": {"open": str(selected.pk)},
            "has_prerendered": True,
        }
    )
    return socket, await socket.receive_json_from()


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_restored_view_does_not_bind_one_records_input_to_another():
    """The saved state is keyed by path, so the same session holds whichever record was open
    last. Input typed for A, then a restore with B selected: B starts from B's own values."""
    from django.contrib.sessions.backends.db import SessionStore

    alice, a, _ = await sync_to_async(_world)()
    b = await sync_to_async(Group.objects.create)(name="Bee " + uuid.uuid4().hex[:6])
    await sync_to_async(alice.groups.add)(b)
    session = await sync_to_async(SessionStore)()
    await sync_to_async(session.create)()
    socket, frame = await _restore(QueueSnapshot, "/queue-snapshot/", alice, session, b, a)
    try:
        assert frame["type"] == "mount", frame
        assert "TYPED-FOR-A" not in json.dumps(frame), frame
        frames = await _event(socket, 1, "submit_form", {})
        assert not _denied(frames), frames
        assert await _name(b) == b.name
        assert await _name(a) == a.name
    finally:
        await socket.disconnect()


@pytest.mark.django_db
def test_the_record_key_is_saved_with_the_form_input_it_describes():
    alice, mine, _ = _world()
    view, _request = _mounted(Writes, alice, mine.pk)
    assert view.form_object_key == "auth.Group:%s" % mine.pk


class WritesOverridePeek(Writes):
    """Overrides the LISTED handler (without re-declaring the list): it may write now."""

    @event_handler()
    def peek(self, **kwargs):
        EVENTS.append(("peek-override", self.object.pk))
        self.object.name = "WRITTEN-AFTER-REVOKE"
        self.object.save()


class WritesRelisted(Writes):
    """Re-declares the list: the override is vouched for by the subclass that wrote it."""

    object_read_only_events = frozenset({"peek"})

    @event_handler()
    def peek(self, **kwargs):
        EVENTS.append(("peek-relisted",))


urlpatterns.append(path("override-peek/<int:pk>/", WritesOverridePeek.as_view()))


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_override_of_a_listed_handler_is_a_write_and_is_checked_in_full():
    alice, mine, _ = await sync_to_async(_world)()
    socket, mounted = await _open(WritesOverridePeek, alice, "/override-peek/%s/" % mine.pk)
    try:
        assert mounted["type"] == "mount", mounted
        REVOKED.add(mine.pk)
        EVENTS.clear()
        frames = await _event(socket, 1, "peek", {})
        assert _denied(frames), frames
        assert _kinds("peek-override") == [], EVENTS
        assert await _name(mine) == mine.name
    finally:
        await socket.disconnect()


@pytest.mark.django_db
def test_a_subclass_that_redeclares_the_list_vouches_for_its_own_override():
    alice, mine, _ = _world()
    view, request = _mounted(WritesRelisted, alice, mine.pk)
    assert view._djust_reuses_authorized_object(view.peek, request) is True
    other, request = _mounted(WritesOverridePeek, alice, mine.pk)
    assert other._djust_reuses_authorized_object(other.peek, request) is False


def test_a_listed_name_that_is_not_a_method_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured, match="not a method"):

        class Typo(ModelFormMixin[Group], LiveView):
            exposure_policy = "legacy"
            object_read_only_events = frozenset({"no_such_handler"})


def test_the_fingerprint_sees_an_in_place_change_to_a_mutable_value():
    class Row:
        pass

    row = Row()
    row.name, row.data = "n", {"roles": ["viewer"]}
    before = ModelFormMixin._fingerprint(row)
    row.data["roles"].append("admin")
    assert ModelFormMixin._fingerprint(row) != before
    row.data["roles"].pop()
    assert ModelFormMixin._fingerprint(row) == before


def test_a_lookup_that_raises_attribute_error_is_a_required_object():
    class Eager(ModelFormMixin[Group], LiveView):
        exposure_policy = "legacy"
        object_optional = True

        def get_object_lookup(self):
            return {"pk": self.never_set}  # read before anything set it

    assert Eager()._djust_object_required is True


def _drop_the_saved_key(view):
    """What a restore that did not carry ``form_object_key`` leaves: form input, no record key."""
    view.__dict__.pop("form_object_key", None)


@pytest.mark.django_db
def test_restored_input_without_a_record_key_is_kept_for_a_view_the_route_selects():
    from djust.auth.core import enforce_object_permission

    alice, mine, _ = _world()
    view, request = _mounted(Writes, alice, mine.pk)
    view.form_data = {"name": "restored input"}
    _drop_the_saved_key(view)
    enforce_object_permission(view, request)
    assert view.form_data == {"name": "restored input"}
    assert view.form_object_key == "auth.Group:%s" % mine.pk


@pytest.mark.django_db
def test_restored_input_without_a_record_key_is_not_trusted_for_a_params_selected_view():
    from djust.auth.core import enforce_object_permission

    alice, mine, _ = _world()
    request = RequestFactory().get("/")
    request.user, request.tenant = alice, None
    view = Queue()
    view.setup(request)
    view._djust_bind_route_kwargs({})
    view.mount(request)
    view.open_id = str(mine.pk)
    enforce_object_permission(view, request)
    assert view.form_data == {"name": mine.name}
    view.form_data = {"name": "input typed for some other record"}
    _drop_the_saved_key(view)
    enforce_object_permission(view, request)
    assert view.form_data == {"name": mine.name}
    assert view.form_object_key == "auth.Group:%s" % mine.pk
