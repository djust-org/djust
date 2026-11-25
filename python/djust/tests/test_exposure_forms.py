"""ADR-038 E2-3: ``FormMixin`` under the explicit policy (decision D-e).

* A registered ``djust.forms`` provider renders the form's keys, so explicit
  ``validate_field``/``submit_form`` render their errors.
* Nothing is persisted by default. ``persisted_form_input(...)`` opts named,
  non-sensitive fields into server persistence across reconnect and HTTP POST.
* A password-widget (or otherwise sensitive) field can never be opted in, and
  its value never reaches the session, frames, snapshot or debug output.
* ``model_pk`` is not persisted, and an explicit view never re-resolves a raw
  primary key without the view's own ``mount()``.

Only the staged construction gate is bypassed. Runtime cases use the real
``ViewRuntime`` (DB sessions); wire cases use the real WebSocket consumer and
the real HTTP POST fallback. The Rust renderer has no handler for the
``{% live_form %}`` family, so templates use plain variables.
"""

from typing import Any
import contextlib
import json
import re
import uuid

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import AnonymousUser, Group, Permission, User
from django.contrib.contenttypes.models import ContentType
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings
from django.urls import path

from djust import LiveView
from djust._exposure import ExposureConfigurationError, ExposureContract
from djust._exposure_sessions import server_state_adapter
from djust._exposure_snapshots import snapshot_codec
from djust.decorators import event_handler, state
from djust.forms import FormMixin, ModelFormMixin, persisted_form_input
from djust.runtime import ViewRuntime
from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

pytestmark = [pytest.mark.django_db(transaction=True)]

PASSWORD = "FORM_PASSWORD_SENTINEL"
NAME_ERROR = "Ensure this value has at most 5 characters"
EMAIL_ERROR = "Enter a valid email address."
PATH = "/forms-explicit/"


class SignupForm(forms.Form):
    name = forms.CharField(max_length=5, required=False)
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)

    def clean_password(self):
        value = self.cleaned_data["password"]
        if value.startswith("BAD"):
            # An error that echoes the raw value, as custom validators can.
            raise forms.ValidationError("Rejected %(value)s", params={"value": value})
        return value


TEMPLATE = (
    '<div dj-root><form dj-submit="submit_form">'
    '<input name="name" value="{{ form_data.name }}">'
    '<input name="email" value="{{ form_data.email }}">'
    '<input type="password" name="password" value="{{ form_data.password }}">'
    "{% for e in field_errors.name %}<i>name:{{ e }}</i>{% endfor %}"
    "{% for e in field_errors.email %}<i>email:{{ e }}</i>{% endfor %}"
    "{% for e in field_errors.password %}<i>password:{{ e }}</i>{% endfor %}"
    "<p>valid={{ is_valid }}</p></form></div>"
)


class ExplicitFormView(FormMixin, LiveView):
    exposure_policy = "explicit"
    form_class = SignupForm
    template = TEMPLATE


class PersistedFormView(FormMixin, LiveView):
    """Opts ``name`` into server persistence; declares a client snapshot field."""

    exposure_policy = "explicit"
    form_class = SignupForm
    template = TEMPLATE
    form_input = persisted_form_input("name")
    navigation = state("NAV", persist="client", client=True)


VIEWS = {"explicit": ExplicitFormView}


@pytest.fixture
def staged(monkeypatch):
    monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)


def make_request(session_key=None):
    request = RequestFactory().get(PATH)
    request.user = AnonymousUser()
    request.tenant = None
    request.session = SessionStore(session_key)
    if session_key is None:
        request.session.create()
    return request


async def mount(request, view_class, tab=None):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": __name__ + "." + view_class.__name__,
                "url": PATH,
                "tab": tab,
            }
        )
    assert not transport.errors, transport.errors
    assert any(frame.get("type") == "mount" for frame in transport.sent), transport.sent
    return runtime, transport


async def event(runtime, handler, /, **params):
    await runtime.dispatch_event({"type": "event", "event": handler, "params": params})


def mount_html(transport):
    return next(frame for frame in transport.sent if frame.get("type") == "mount")["html"]


# ---------------------------------------------------------------------------
# 1. The provider: explicit validate_field / submit_form render their errors.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit"])
async def test_validate_field_and_submit_form_render_errors_over_the_runtime(staged, policy):
    request = await sync_to_async(make_request)()
    runtime, transport = await mount(request, VIEWS[policy])
    before = len(transport.sent)

    await event(runtime, "validate_field", field="name", value="TOOLONG")
    after_validate = json.dumps(transport.sent[before:])
    assert "name:" + NAME_ERROR in after_validate
    assert runtime.view_instance.field_errors["name"]

    before = len(transport.sent)
    await event(runtime, "submit_form", name="Al", email="not-an-email", password="pw")
    after_submit = json.dumps(transport.sent[before:])
    assert "email:" + EMAIL_ERROR in after_submit
    assert runtime.view_instance.is_valid is False


def post(session, event_name, params):
    request = RequestFactory().post(
        PATH, {"event": event_name, "params": params}, content_type="application/json"
    )
    request.session = session
    request.user = AnonymousUser()
    request.tenant = None
    return request


def get(session):
    request = RequestFactory().get(PATH)
    request.session = session
    request.user = AnonymousUser()
    request.tenant = None
    return request


@pytest.mark.parametrize("policy", ["explicit"])
def test_validate_field_and_submit_form_render_errors_over_http_post(staged, policy):
    view_class = VIEWS[policy]
    session = SessionStore()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        assert view_class.as_view()(get(session)).status_code == 200
        response = view_class.as_view()(
            post(
                SessionStore(session.session_key),
                "validate_field",
                {"field": "name", "value": "TOOLONG"},
            )
        )
        assert response.status_code == 200, response.content
        assert ("name:" + NAME_ERROR).encode() in response.content
        response = view_class.as_view()(
            post(
                SessionStore(session.session_key),
                "submit_form",
                {"name": "Al", "email": "not-an-email", "password": "pw"},
            )
        )
        assert response.status_code == 200, response.content
        assert ("email:" + EMAIL_ERROR).encode() in response.content


# ---------------------------------------------------------------------------
# 2. A PasswordInput value never reaches a destination, even when re-rendered.
# ---------------------------------------------------------------------------


async def websocket_frames(session_key, view_name):
    """Mount and send the password-bearing events over the real consumer."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    frames = [await comm.receive_json_from()]
    try:
        await comm.send_json_to({"type": "mount", "view": __name__ + "." + view_name, "url": PATH})
        frames.append(await comm.receive_json_from())
        for name, params in (
            ("validate_field", {"field": "name", "value": "Al"}),
            ("validate_field", {"field": "password", "value": PASSWORD}),
            ("validate_field", {"field": "password", "value": "BAD" + PASSWORD}),
            ("submit_form", {"name": "Al", "email": "not-an-email", "password": PASSWORD}),
            ("submit_form", {"name": "Al", "email": "bad", "password": "BAD" + PASSWORD}),
        ):
            await comm.send_json_to({"type": "event", "event": name, "params": params})
            frames.append(await comm.receive_json_from())
    finally:
        await comm.disconnect()
    return frames


WIRE_SETTINGS = {
    "LIVEVIEW_ALLOWED_MODULES": [__name__],
    "DEBUG": True,
    "DJUST_CONFIG": {},
    "DJUST_TENANTS": {},
}


@pytest.mark.asyncio
async def test_password_value_never_reaches_session_frames_snapshot_or_debug(staged):
    request = await sync_to_async(make_request)()
    with override_settings(**WIRE_SETTINGS):
        frames = await websocket_frames(request.session.session_key, "PersistedFormView")
    mount_frame = frames[1]
    assert mount_frame["type"] == "mount", mount_frame
    wire = json.dumps(frames)
    # Positive controls: the invalid form really re-rendered, with the debug
    # payload attached, and the password field's own error still renders.
    assert "email:" + EMAIL_ERROR in wire
    assert "password:" in wire
    assert all("_debug" in frame for frame in frames[2:]), frames
    assert PASSWORD not in wire

    # The signed snapshot holds only the declared client field.
    token = frames[-1]["state_snapshot_signed"]
    assert PASSWORD not in token
    fresh = await sync_to_async(make_request)(request.session.session_key)
    probe = PersistedFormView.__new__(PersistedFormView)
    codec = await sync_to_async(snapshot_codec)(probe, fresh)
    assert codec.restore(token) == {"navigation": "NAV"}

    # Server persistence holds the opted-in field only.
    adapter = await sync_to_async(server_state_adapter)(probe, fresh)
    assert await sync_to_async(adapter.load)() == {"form_input": {"name": "Al"}}
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert "Al" in json.dumps(raw)  # positive control: the envelope was written
    assert PASSWORD not in json.dumps(raw)

    # Debug output of a live instance that holds the password in form_data.
    runtime, transport = await mount(await sync_to_async(make_request)(), PersistedFormView)
    await event(runtime, "submit_form", name="Al", email="bad", password="BAD" + PASSWORD)
    view = runtime.view_instance
    assert view.form_data["password"] == "BAD" + PASSWORD  # positive control
    with override_settings(DEBUG=True):
        debug_info = await sync_to_async(view.get_debug_info)()
        debug_update = await sync_to_async(view.get_debug_update)()
    for payload in (debug_info, debug_update, transport.sent):
        assert PASSWORD not in json.dumps(payload, default=str)


def test_password_value_never_reaches_http_post_response_or_session(staged):
    session = SessionStore()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        assert PersistedFormView.as_view()(get(session)).status_code == 200
        response = PersistedFormView.as_view()(
            post(
                SessionStore(session.session_key),
                "submit_form",
                {"name": "Al", "email": "bad", "password": "BAD" + PASSWORD},
            )
        )
    assert response.status_code == 200, response.content
    assert ("email:" + EMAIL_ERROR).encode() in response.content
    assert PASSWORD.encode() not in response.content
    raw = SessionStore(session.session_key).load()
    assert PASSWORD not in json.dumps(raw)


# ---------------------------------------------------------------------------
# 3. Opting a sensitive field into persistence is a configuration error.
# ---------------------------------------------------------------------------


def test_opting_a_password_field_into_persistence_raises_at_class_definition():
    with pytest.raises(ExposureConfigurationError):

        class Bad(FormMixin, LiveView):
            exposure_policy = "explicit"
            form_class = SignupForm
            form_input = persisted_form_input("name", "password")


def test_opting_a_configured_sensitive_field_into_persistence_raises(settings):
    class TokenForm(forms.Form):
        api_token = forms.CharField()

    settings.DJUST_SENSITIVE_FIELDS = ["api_token"]
    with pytest.raises(ExposureConfigurationError):

        class Bad(FormMixin, LiveView):
            exposure_policy = "explicit"
            form_class = TokenForm
            form_input = persisted_form_input("api_token")


def test_opting_an_unknown_field_into_persistence_raises():
    with pytest.raises(ExposureConfigurationError):

        class Bad(FormMixin, LiveView):
            exposure_policy = "explicit"
            form_class = SignupForm
            form_input = persisted_form_input("nickname")


def test_a_dynamic_form_with_an_opted_in_password_field_refuses_to_mount(staged, rf):
    class Dynamic(FormMixin, LiveView):
        exposure_policy = "explicit"
        template = TEMPLATE
        form_input = persisted_form_input("password")

        def get_form_class(self):
            return SignupForm

    view = Dynamic()
    request = make_request()
    with pytest.raises(ExposureConfigurationError):
        view.mount(request)


def test_persisted_form_input_is_a_server_state_field_in_the_contract():
    contract = ExposureContract.from_view_class(PersistedFormView)
    assert contract.fields["form_input"].persist == "server"
    assert contract.fields["form_input"].client is False
    providers = {p.name: p for p in contract.providers}
    assert "form_data" in providers["djust.forms"].rendered
    assert "model_pk" not in providers["djust.forms"].rendered


# ---------------------------------------------------------------------------
# 4. Reconnect: opted-in fields restore; everything else resets.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconnect_restores_form_input_and_errors_but_no_sensitive_field(staged):
    """#4107: form input and errors are saved with the view's state; a sensitive
    field never is, opted in or not."""
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request, PersistedFormView, tab="tab-forms-0001")
    await event(runtime, "validate_field", field="name", value="Al")
    await event(runtime, "validate_field", field="email", value="bad")
    await event(runtime, "validate_field", field="password", value=PASSWORD)
    assert runtime.view_instance.field_errors["email"]

    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, transport = await mount(fresh, PersistedFormView, tab="tab-forms-0001")
    view = second.view_instance
    assert view.form_data == {"name": "Al", "email": "bad", "password": ""}
    assert view.field_errors["email"] and view.is_valid is False
    html = mount_html(transport)
    assert 'value="Al"' in html
    assert PASSWORD not in json.dumps(transport.sent)
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert PASSWORD not in json.dumps(raw, default=str)


@pytest.mark.asyncio
async def test_reconnect_without_opt_in_restores_the_input_from_the_automatic_state(staged):
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request, ExplicitFormView, tab="tab-forms-0001")
    await event(runtime, "validate_field", field="name", value="Al")
    assert runtime.view_instance.form_data["name"] == "Al"  # the event ran
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert not any(key.startswith("_djust_explicit_") for key in raw)

    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, _ = await mount(fresh, ExplicitFormView, tab="tab-forms-0001")
    assert second.view_instance.form_data["name"] == "Al"  # #4107


def test_http_post_restores_opted_in_fields_across_requests(staged):
    session = SessionStore()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        assert PersistedFormView.as_view()(get(session)).status_code == 200
        first = post(
            SessionStore(session.session_key), "validate_field", {"field": "name", "value": "Al"}
        )
        assert PersistedFormView.as_view()(first).status_code == 200
        second = post(
            SessionStore(session.session_key), "validate_field", {"field": "email", "value": "bad"}
        )
        response = PersistedFormView.as_view()(second)
    assert response.status_code == 200, response.content
    # The second request never sent ``name``: it survived only through restore.
    assert server_state_adapter(PersistedFormView(), second).load() == {
        "form_input": {"name": "Al"}
    }


# ---------------------------------------------------------------------------
# 5. model_pk: never persisted, never re-resolved from a raw key when explicit.
# ---------------------------------------------------------------------------


def test_raw_model_pk_is_never_re_resolved(staged):
    user = User.objects.create(username="form-owner")
    view = VIEWS["explicit"]()
    view.model_pk = user.pk
    view.model_label = "auth.User"
    view._model_instance = None
    view._form_instance = None
    view.form_instance
    assert view._model_instance is None


# ---------------------------------------------------------------------------
# 6. LiveView.render() (the test client's render) resolves ``view`` for form
#    tags, as render_with_diff() does (#4151).
# ---------------------------------------------------------------------------

FIELD_TEMPLATE = '{% load live_tags %}<div dj-root>{% live_field view "name" %}</div>'


class ExplicitFieldView(ExplicitFormView):
    template = FIELD_TEMPLATE


FIELD_VIEWS = {"explicit": ExplicitFieldView}


@pytest.mark.parametrize("policy", ["explicit"])
def test_test_client_render_resolves_view_for_form_tags(staged, policy):
    from djust.testing import LiveViewTestClient

    client = LiveViewTestClient(FIELD_VIEWS[policy])
    client.mount()
    client.send_event("validate_field", field="name", value="toolong")
    html = client.render()
    assert "does not have as_live_field" not in html
    assert re.search(r'<input[^>]*name="name"[^>]*value="toolong"', html), html
    assert NAME_ERROR in html


# ---------------------------------------------------------------------------
# 7. ModelFormMixin ``object_recheck = "writes"`` reuses the authorized record
#    and a real change to the record still forces the full
#    check (#4153). The explicit render caches the record's @property values on
#    the instance (``_djust_prop_cache``); that is not a change to the record.
# ---------------------------------------------------------------------------

RECHECKS: list = []
#: Whether the record's groups are prefetched / its ``codename`` deferred, for the test
#: that sets it.
PREFETCH: list = [False]
DEFER: list = [False]


class PermissionForm(forms.ModelForm):
    class Meta:
        model = Permission
        fields = ["name"]


class ExplicitRecheckView(ModelFormMixin, LiveView):
    exposure_policy = "explicit"
    template = (
        '<div dj-root><h1>{{ object.name }}</h1><input name="name" value="{{ form_data.name }}">'
        "</div>"
    )
    model = Permission
    form_class = PermissionForm
    object_recheck = "writes"
    object_read_only_events = frozenset({"touch"})
    login_required = True

    def get_queryset(self):
        RECHECKS.append("lookup")
        queryset = super().get_queryset()
        if DEFER[0]:
            queryset = queryset.defer("codename")
        return queryset.prefetch_related("group_set") if PREFETCH[0] else queryset

    def has_object_permission(self, request, obj):
        RECHECKS.append("permission")
        return True

    @event_handler()
    def touch(self, change: str = "", other: Any = None, **kwargs):
        """Declared read-only (so it reuses), and changes the record as asked: the NEXT
        read-only event must see the change and check the record in full."""
        obj = self.object
        if change == "field":
            obj.name = obj.name + " (edited)"
        elif change == "deferred":
            assert "codename" not in obj.__dict__
            obj.codename  # noqa: B018 - loads the deferred field
            assert "codename" in obj.__dict__
        elif change == "fk":
            obj.content_type_id = other
        else:
            assert change == "none", change


RECHECK_VIEWS = {"explicit": ExplicitRecheckView}

urlpatterns = [
    path("recheck-explicit/<int:pk>/", ExplicitRecheckView.as_view()),
]


def _recheck_world():
    tag = uuid.uuid4().hex[:8]
    user = User.objects.create_user(username="recheck-" + tag)
    own_type, other_type = (
        ContentType.objects.get_for_model(User),
        ContentType.objects.get_for_model(Group),
    )
    record = Permission.objects.create(
        name="Can recheck " + tag, codename="recheck_" + tag, content_type=own_type
    )
    return user, record, {"fk": other_type.pk}


@contextlib.asynccontextmanager
async def _recheck_socket(policy, user, record):
    from channels.testing import WebsocketCommunicator

    from djust.testing import live_session
    from djust.websocket import LiveViewConsumer

    session = await sync_to_async(live_session)(user)
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=user, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    view = RECHECK_VIEWS[policy]
    await socket.send_json_to(
        {
            "type": "mount",
            "view": __name__ + "." + view.__name__,
            "url": "/recheck-%s/%s/" % (policy, record.pk),
            "params": {},
        }
    )
    mounted = await socket.receive_json_from()
    assert mounted["type"] == "mount", mounted
    try:
        yield socket
    finally:
        await socket.disconnect()


async def _recheck_event(socket, ref, event, params):
    await socket.send_json_to({"type": "event", "event": event, "params": params, "ref": ref})
    frames = []
    for _ in range(6):
        frame = await socket.receive_json_from()
        frames.append(frame)
        if frame.get("type") in {"patch", "html_update", "error", "noop", "navigate"}:
            break
    assert not any(frame.get("type") == "error" for frame in frames), frames
    return frames


@pytest.fixture
def recheck_urls():
    RECHECKS.clear()
    PREFETCH[0] = DEFER[0] = False
    with override_settings(
        ROOT_URLCONF=__name__, DJUST_TENANTS=None, LIVEVIEW_ALLOWED_MODULES=[__name__]
    ):
        yield
    RECHECKS.clear()
    PREFETCH[0] = DEFER[0] = False


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit"])
@pytest.mark.parametrize("change", ["none", "field", "fk"])
async def test_a_read_only_event_reuses_the_record_until_it_changes(recheck_urls, policy, change):
    user, record, others = await sync_to_async(_recheck_world)()
    async with _recheck_socket(policy, user, record) as socket:
        # The record authorized at mount is reused: no lookup, no permission check.
        RECHECKS.clear()
        await _recheck_event(socket, 1, "validate_field", {"field": "name", "value": "a"})
        await _recheck_event(socket, 2, "touch", {"change": change, "other": others.get(change)})
        assert RECHECKS == [], (policy, change, RECHECKS)
        # The next read-only event: an unchanged record is reused again, a changed one
        # is resolved and authorized in full.
        await _recheck_event(socket, 3, "validate_field", {"field": "name", "value": "ab"})
        expected = [] if change == "none" else ["lookup", "permission"]
        assert RECHECKS == expected, (policy, change, RECHECKS)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit"])
async def test_a_write_resolves_and_authorizes_the_record_while_it_is_reusable(
    recheck_urls, policy
):
    user, record, _ = await sync_to_async(_recheck_world)()
    async with _recheck_socket(policy, user, record) as socket:
        RECHECKS.clear()
        await _recheck_event(socket, 1, "validate_field", {"field": "name", "value": "a"})
        assert RECHECKS == [], RECHECKS
        await _recheck_event(socket, 2, "submit_form", {"name": "Saved " + policy})
        assert RECHECKS == ["lookup", "permission"], RECHECKS


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit"])
async def test_a_record_with_prefetched_relations_is_never_reused(recheck_urls, policy):
    """The fingerprint covers many-to-many data only through Django's prefetch cache, and a
    prefetched ``QuerySet`` compares by identity: such a record is checked in full on every
    event, so an m2m change can never be missed (the conservative side of #4153)."""
    PREFETCH[0] = True
    user, record, _ = await sync_to_async(_recheck_world)()
    async with _recheck_socket(policy, user, record) as socket:
        for ref in range(2):
            RECHECKS.clear()
            await _recheck_event(socket, ref, "validate_field", {"field": "name", "value": "a"})
            assert RECHECKS == ["lookup", "permission"], RECHECKS


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["explicit"])
async def test_loading_a_deferred_field_is_a_change_to_the_record(recheck_urls, policy):
    """A deferred field loaded since the record was authorized forces the full check. The
    render leaves ``codename`` deferred (the template does not read it; on an explicit view
    too since #4290), so the record is reused until a handler loads it."""
    DEFER[0] = True
    user, record, _ = await sync_to_async(_recheck_world)()
    async with _recheck_socket(policy, user, record) as socket:
        RECHECKS.clear()
        await _recheck_event(socket, 1, "validate_field", {"field": "name", "value": "a"})
        await _recheck_event(socket, 2, "touch", {"change": "deferred"})
        assert RECHECKS == [], RECHECKS
        await _recheck_event(socket, 3, "validate_field", {"field": "name", "value": "ab"})
        assert RECHECKS == ["lookup", "permission"], RECHECKS


def _authorized(policy, user, record):
    """A view of ``policy`` that resolved and authorized ``record`` for ``request``."""
    from djust.auth.core import enforce_object_permission

    request = RequestFactory().get("/recheck-%s/%s/" % (policy, record.pk))
    request.user, request.tenant = user, None
    request.session = SessionStore()
    request.session.create()
    view = RECHECK_VIEWS[policy]()
    view.setup(request, pk=record.pk)
    view._djust_bind_route_kwargs({"pk": record.pk})
    view.mount(request, pk=record.pk)
    enforce_object_permission(view, request)
    assert view._djust_reuses_authorized_object(view.validate_field, request) is True
    return view, request


def _another(request, **changes):
    from copy import copy

    other = copy(request)
    for name, value in changes.items():
        setattr(other, name, value)
    return other


@pytest.mark.parametrize("policy", ["explicit"])
def test_reuse_is_only_for_the_same_user_session_and_tenant(recheck_urls, policy):
    from djust.tenants.resolvers import TenantInfo

    user, record, _ = _recheck_world()
    view, request = _authorized(policy, user, record)
    reuses = view._djust_reuses_authorized_object
    handler = view.validate_field

    stranger = User.objects.create_user(username="stranger-" + uuid.uuid4().hex[:8])
    assert reuses(handler, _another(request, user=stranger)) is False
    assert reuses(handler, _another(request, user=AnonymousUser())) is False

    other_session = SessionStore()
    other_session.create()
    assert reuses(handler, _another(request, session=other_session)) is False
    # The same session, loaded again (as every explicit turn does), is the same session.
    assert reuses(handler, _another(request, session=SessionStore(request.session.session_key)))

    assert reuses(handler, _another(request, tenant=TenantInfo("acme"))) is False
    assert reuses(handler, _another(request, tenant=Group.objects.create(name="t"))) is False
    assert reuses(handler, _another(request, tenant=object())) is False


@pytest.mark.parametrize("policy", ["explicit"])
def test_a_tenant_of_the_same_identity_is_the_same_tenant(recheck_urls, policy):
    """The stamp compares a tenant by identity, not by object: a model tenant re-read
    for the next turn (a new instance), or a ``TenantInfo`` resolved again, is the same
    tenant, and a different one never is."""
    from djust.auth.core import enforce_object_permission
    from djust.tenants.resolvers import TenantInfo

    user, record, _ = _recheck_world()
    tenant_row = Group.objects.create(name="tenant-" + uuid.uuid4().hex[:8])
    for tenant, same, different in [
        (TenantInfo("acme"), TenantInfo("acme"), TenantInfo("globex")),
        (tenant_row, Group.objects.get(pk=tenant_row.pk), Group.objects.create(name="other")),
    ]:
        view, request = _authorized(policy, user, record)
        request.tenant = tenant
        enforce_object_permission(view, request)  # authorized again, for this tenant
        reuses = view._djust_reuses_authorized_object
        assert reuses(view.validate_field, _another(request, tenant=same)) is True
        assert reuses(view.validate_field, _another(request, tenant=different)) is False
        assert reuses(view.validate_field, _another(request, tenant=None)) is False

    # A tenant of no known identity matches nothing, not even itself.
    unknown = object()
    view, request = _authorized(policy, user, record)
    request.tenant = unknown
    enforce_object_permission(view, request)
    assert view._djust_reuses_authorized_object(view.validate_field, request) is False


@pytest.mark.parametrize("policy", ["explicit"])
def test_a_write_never_reuses_the_record(recheck_urls, policy):
    user, record, _ = _recheck_world()
    view, request = _authorized(policy, user, record)
    for name in ("submit_form", "reset_form"):
        assert view._djust_reuses_authorized_object(getattr(view, name), request) is False


@pytest.mark.parametrize("policy", ["explicit"])
def test_the_render_cache_on_the_record_is_not_a_change(recheck_urls, policy):
    """What the explicit render stores on the instance (``_djust_prop_cache``) is ignored;
    the field it sits beside is not."""
    from djust.serialization import DjangoJSONEncoder

    user, record, _ = _recheck_world()
    view, request = _authorized(policy, user, record)
    before = set(vars(view.object))
    DjangoJSONEncoder()._add_property_values(view.object, {})
    added = set(vars(view.object)) - before
    assert added == {"_djust_prop_cache"}, added
    assert view._djust_reuses_authorized_object(view.validate_field, request) is True
    view.object.name += " (edited)"
    assert view._djust_reuses_authorized_object(view.validate_field, request) is False
