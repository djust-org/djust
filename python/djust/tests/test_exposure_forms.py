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

import json

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust._exposure import ExposureConfigurationError, ExposureContract
from djust._exposure_sessions import server_state_adapter
from djust._exposure_snapshots import snapshot_codec
from djust.decorators import state
from djust.forms import FormMixin, persisted_form_input
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


class LegacyFormView(FormMixin, LiveView):
    form_class = SignupForm
    template = TEMPLATE


class PersistedFormView(FormMixin, LiveView):
    """Opts ``name`` into server persistence; declares a client snapshot field."""

    exposure_policy = "explicit"
    form_class = SignupForm
    template = TEMPLATE
    form_input = persisted_form_input("name")
    navigation = state("NAV", persist="client", client=True)


VIEWS = {"explicit": ExplicitFormView, "legacy": LegacyFormView}


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


async def mount(request, view_class):
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": PATH}
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
@pytest.mark.parametrize("policy", ["explicit", "legacy"])
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


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
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
    frames = [await comm.receive_json_from(timeout=5)]
    try:
        await comm.send_json_to({"type": "mount", "view": __name__ + "." + view_name, "url": PATH})
        frames.append(await comm.receive_json_from(timeout=5))
        for name, params in (
            ("validate_field", {"field": "name", "value": "Al"}),
            ("validate_field", {"field": "password", "value": PASSWORD}),
            ("validate_field", {"field": "password", "value": "BAD" + PASSWORD}),
            ("submit_form", {"name": "Al", "email": "not-an-email", "password": PASSWORD}),
            ("submit_form", {"name": "Al", "email": "bad", "password": "BAD" + PASSWORD}),
        ):
            await comm.send_json_to({"type": "event", "event": name, "params": params})
            frames.append(await comm.receive_json_from(timeout=5))
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


@pytest.mark.asyncio
async def test_legacy_password_control_renders_the_value(staged):
    """Legacy behavior is unchanged: the template echo reaches the frames."""
    request = await sync_to_async(make_request)()
    with override_settings(**WIRE_SETTINGS):
        frames = await websocket_frames(request.session.session_key, "LegacyFormView")
    assert frames[1]["type"] == "mount", frames[1]
    assert PASSWORD in json.dumps(frames)


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
async def test_reconnect_restores_opted_in_fields_and_resets_the_rest(staged):
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request, PersistedFormView)
    await event(runtime, "validate_field", field="name", value="Al")
    await event(runtime, "validate_field", field="email", value="bad")
    await event(runtime, "validate_field", field="password", value=PASSWORD)
    assert runtime.view_instance.field_errors["email"]

    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, transport = await mount(fresh, PersistedFormView)
    view = second.view_instance
    assert view.form_data == {"name": "Al", "email": "", "password": ""}
    assert view.field_errors == {} and view.form_errors == {} and view.is_valid is False
    html = mount_html(transport)
    assert 'value="Al"' in html
    assert EMAIL_ERROR not in html
    assert PASSWORD not in json.dumps(transport.sent)


@pytest.mark.asyncio
async def test_reconnect_without_opt_in_persists_nothing(staged):
    request = await sync_to_async(make_request)()
    runtime, _ = await mount(request, ExplicitFormView)
    await event(runtime, "validate_field", field="name", value="Al")
    assert runtime.view_instance.form_data["name"] == "Al"  # the event ran
    raw = await sync_to_async(SessionStore(request.session.session_key).load)()
    assert not any(key.startswith("_djust_explicit_") for key in raw)

    fresh = await sync_to_async(make_request)(request.session.session_key)
    second, _ = await mount(fresh, ExplicitFormView)
    assert second.view_instance.form_data["name"] == ""


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


@pytest.mark.parametrize("policy", ["explicit", "legacy"])
def test_raw_model_pk_is_re_resolved_only_for_legacy_views(staged, policy):
    user = User.objects.create(username="form-owner")
    view = VIEWS[policy]()
    view.model_pk = user.pk
    view.model_label = "auth.User"
    view._model_instance = None
    view._ensure_model_instance()
    if policy == "legacy":
        assert view._model_instance == user  # control: the unscoped lookup ran
    else:
        assert view._model_instance is None
