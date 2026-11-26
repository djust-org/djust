"""#4177: an explicit wizard renders the flat ``<field>_choices`` names.

``WizardMixin`` exposes each choice field of the current step as
``form_choices.<field>`` and as a flat ``<field>_choices`` name. The flat names
were provided only on a legacy view; an explicit view rendered them empty. They
now render on every view, with no template change, over the page GET, the HTTP
POST fallback, a WebSocket and SSE.

The wizard below is a real multi-step one: the borough choices belong to its
second step, so they appear only after ``next_step`` validated the first.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from asgiref.sync import sync_to_async
from django import forms
from django.contrib.auth import get_user
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, RequestFactory, override_settings
from django.urls import path
from django.utils.functional import SimpleLazyObject

from djust import sse
from djust.decorators import state
from djust.live_view import LiveView
from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions
from djust.testing import LiveViewTestClient
from djust.tests._frames import frame_matches, legacy_type
from djust.wizard import WizardMixin

MOD = __name__
URL = "/wizard-4177/"


class ContactForm(forms.Form):
    email = forms.EmailField()


class AddressForm(forms.Form):
    street = forms.CharField()
    borough = forms.ChoiceField(
        choices=[("", "Pick one"), ("bk", "Brooklyn"), ("qn", "Queens")],
    )


TEMPLATE = (
    "<div dj-root>{{ current_step.name }}"
    "|flat:{% for c in borough_choices %}[{{ c.label }}]{% endfor %}"
    "|nested:{% for c in form_choices.borough %}({{ c.value }}){% endfor %}"
    "</div>"
)


class Moving(WizardMixin, LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE
    wizard_steps = [
        {"name": "contact", "title": "Contact", "form_class": ContactForm},
        {"name": "address", "title": "Address", "form_class": AddressForm},
        {"name": "done", "title": "Done"},
    ]


urlpatterns = [path(URL.strip("/") + "/", Moving.as_view())]

#: What the second step renders: the labels through the flat name, the values
#: through ``form_choices``. The empty placeholder choice is left out of both.
FLAT = "|flat:[Brooklyn][Queens]|"
NESTED = "|nested:(bk)(qn)"


@pytest.fixture
def wizard_urls():
    with override_settings(
        ROOT_URLCONF=MOD,
        ALLOWED_HOSTS=["testserver"],
        LIVEVIEW_ALLOWED_MODULES=["djust", MOD],
        DEBUG=False,
    ):
        yield


# --------------------------------------------------------------------------
# The context, on the view itself
# --------------------------------------------------------------------------


def _on_second_step(view_class=Moving):
    client = LiveViewTestClient(view_class)
    client.mount()
    result = client.send_event("next_step", email="a@example.com")
    assert result["success"], result
    assert client.view_instance.wizard_step_index == 1
    return client


@pytest.mark.django_db
def test_an_explicit_wizard_renders_both_spellings_of_the_choices():
    client = _on_second_step()
    context = client.view_instance.get_context_data()
    expected = [{"value": "bk", "label": "Brooklyn"}, {"value": "qn", "label": "Queens"}]
    assert context["borough_choices"] == expected
    assert context["form_choices"]["borough"] == expected
    html = client.render()
    assert FLAT in html and NESTED in html, html


@pytest.mark.django_db
def test_the_flat_names_follow_the_current_step():
    client = LiveViewTestClient(Moving)
    client.mount()
    assert "borough_choices" not in client.view_instance.get_context_data()
    assert "|flat:|" in client.render()


class _DynamicForm(forms.Form):
    """A choice field added in ``__init__``: no class can declare its name."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["size"] = forms.ChoiceField(choices=[("s", "Small"), ("l", "Large")])


class StepsFromTheRecord(WizardMixin, LiveView):
    """Steps from ``get_wizard_steps()`` (#4011), not the class attribute."""

    exposure_policy = "explicit"
    template = "<div dj-root>{% for c in size_choices %}[{{ c.label }}]{% endfor %}</div>"

    def get_wizard_steps(self):
        return [{"name": "pick", "title": "Pick", "form_class": _DynamicForm}]


@pytest.mark.django_db
def test_a_field_known_only_at_run_time_has_its_flat_name():
    client = LiveViewTestClient(StepsFromTheRecord)
    client.mount()
    assert "[Small][Large]" in client.render()


class _FormNamedField(forms.Form):
    form = forms.ChoiceField(choices=[("x", "X")])


class FieldNamedForm(WizardMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"
    wizard_steps = [{"name": "s", "title": "S", "form_class": _FormNamedField}]


@pytest.mark.django_db
def test_a_flat_name_never_replaces_a_name_the_context_already_has():
    """A field called ``form`` would be ``form_choices``: the wizard's own name stays."""
    client = LiveViewTestClient(FieldNamedForm)
    client.mount()
    context = client.view_instance.get_context_data()
    assert context["form_choices"] == {"form": [{"value": "x", "label": "X"}]}


class _StatusForm(forms.Form):
    status = forms.ChoiceField(choices=[("open", "Open"), ("closed", "Closed")])


class DeclaresTheName(WizardMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ status_choices }}</div>"
    wizard_steps = [{"name": "s", "title": "S", "form_class": _StatusForm}]
    status_choices = state(default="declared")


class ReplacesTheName(WizardMixin, LiveView):
    """What an explicit wizard had to do before #4177 keeps working."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ status_choices }}</div>"
    wizard_steps = [{"name": "s", "title": "S", "form_class": _StatusForm}]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["status_choices"] = "mine"
        return context


@pytest.mark.django_db
@pytest.mark.parametrize(
    "view_class, expected", [(DeclaresTheName, "declared"), (ReplacesTheName, "mine")]
)
def test_the_views_own_value_for_a_flat_name_wins(view_class, expected):
    client = LiveViewTestClient(view_class)
    client.mount()
    assert client.view_instance.get_context_data()["status_choices"] == expected
    assert ">%s<" % expected in client.render()


# --------------------------------------------------------------------------
# Transports: the page GET and HTTP POST fallback, a WebSocket, SSE
# --------------------------------------------------------------------------


@pytest.mark.django_db
def test_over_the_http_fallback(wizard_urls):
    browser = Client()
    page = browser.get(URL)
    assert page.status_code == 200
    assert "contact|flat:|nested:" in page.content.decode()
    answer = browser.post(
        URL,
        data=json.dumps({"email": "a@example.com"}),
        content_type="application/json",
        HTTP_X_DJUST_EVENT="next_step",
    )
    assert answer.status_code == 200, answer.content[:300]
    body = json.dumps(answer.json())
    assert "Brooklyn" in body and "Queens" in body, body
    assert "[Brooklyn][Queens]" in body and "(bk)(qn)" in body, body


def _frames_text(frames) -> str:
    return json.dumps(frames)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_over_a_websocket(wizard_urls):
    from channels.testing import WebsocketCommunicator

    from djust.tests.test_exposure_runtime import make_request
    from djust.websocket import LiveViewConsumer

    request = await sync_to_async(make_request)()
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=request.session, user=None, tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    try:
        await socket.send_json_to(
            {"type": "mount", "view": MOD + ".Moving", "url": URL, "params": {}}
        )
        mounted = await socket.receive_json_from()
        assert mounted["type"] == "mount", mounted
        await socket.send_json_to(
            {"type": "event", "event": "next_step", "params": {"email": "a@example.com"}, "ref": 1}
        )
        frames = []
        for _ in range(8):
            frame = await socket.receive_json_from(timeout=10)
            frames.append(frame)
            if legacy_type(frame) in {"patch", "html_update", "error", "noop"}:
                break
        assert not any(f.get("type") == "error" for f in frames), frames
        text = _frames_text(frames)
        assert "[Brooklyn][Queens]" in text and "(bk)(qn)" in text, text
    finally:
        await socket.disconnect()


def _sse_request(method, url, body, key):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get(url, data=body)
    else:
        request = factory.post(url, data=json.dumps(body), content_type="application/json")
    request.session = SessionStore(key)
    request.user = SimpleLazyObject(lambda: get_user(request))
    request.tenant = None
    return request


def _fresh_key():
    store = SessionStore()
    store.create()
    return store.session_key


async def _sse_frame(stream, *types, timeout=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=deadline - loop.time())
        if chunk.startswith("data:"):
            frame = json.loads(chunk[len("data:") :])
            if frame_matches(frame, types):
                return frame


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_over_sse(wizard_urls, monkeypatch):
    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    _sse_sessions.clear()
    key = await sync_to_async(_fresh_key)()
    sid = str(uuid.uuid4())
    request = await sync_to_async(_sse_request)(
        "GET", f"/djust/sse/{sid}/", {"view": MOD + ".Moving", "_djust_url": URL}, key
    )
    stream = (await DjustSSEStreamView().get(request, session_id=sid))._iterator
    try:
        await _sse_frame(stream, "sse_connect")
        mounted = await _sse_frame(stream, "mount", "error")
        assert mounted["type"] == "mount", mounted
        message = await sync_to_async(_sse_request)(
            "POST",
            f"/djust/sse/{sid}/message/",
            {"type": "event", "event": "next_step", "params": {"email": "a@example.com"}},
            key,
        )
        response = await DjustSSEMessageView().post(message, session_id=sid)
        assert response.status_code < 400, response.content[:300]
        frame = await _sse_frame(stream, "patch", "html_update", "error")
        assert frame["type"] != "error", frame
        text = _frames_text(frame)
        assert "[Brooklyn][Queens]" in text and "(bk)(qn)" in text, text
    finally:
        await stream.aclose()
        _sse_sessions.clear()
