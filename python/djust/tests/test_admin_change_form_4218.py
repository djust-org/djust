"""#4218: the admin change form saves what the person typed, on every transport.

Typing in a field sent ``validate_field``, which ``AdminFormMixin`` overrode
without ``@event_handler``: the explicit-exposure admin view refused it, and
Save then reported "Changes saved" while saving the stored values unchanged.

These tests do not hand-write the payloads. They read the rendered page (the
field's ``dj-input`` / ``dj-change`` binding, the form's ``dj-submit`` and its
buttons) and build each event the way the client builds it
(``09-event-binding.js``: ``_handleDjInput`` / ``_handleDjChange`` /
``_handleDjSubmit``, legacy and strict), then send it over the WebSocket, SSE
or the HTTP fallback POST. The earlier admin tests sent ``update_field``, an
event the page never sends, which is how the refusal went unseen.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import pytest
from django import forms
from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import Client
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.config import config
from djust.tests.test_admin_page_render import _Socket, _session_cookie
from djust.tests.test_framework_classes_explicit_4185 import _post, _sse_request, _tab
from djust_rentals.models import Property

pytestmark = pytest.mark.admin

MODULE = __name__

REQUIRED = {
    "name": "Seed house",
    "address": "1 Seed St",
    "city": "Austin",
    "state": "TX",
    "zip_code": "78701",
    "property_type": "house",
    "bedrooms": "2",
    "bathrooms": "1.5",
    "square_feet": "900",
    "monthly_rent": "1000.00",
    "security_deposit": "500.00",
}


class PropertyAdmin(DjustModelAdmin):
    fields = list(REQUIRED) + ["pet_friendly"]


SITE = DjustAdminSite(name="change_form_4218_admin")
SITE.register(Property, PropertyAdmin)


class RadioForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = list(REQUIRED) + ["pet_friendly"]
        widgets = {"property_type": forms.RadioSelect}


class RadioAdmin(DjustModelAdmin):
    """A required choice field drawn as radios (Django's ``RadioSelect``)."""

    form = RadioForm


RADIO_SITE = DjustAdminSite(name="change_form_4218_radio")
RADIO_SITE.register(Property, RadioAdmin)


class GuardedForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = list(REQUIRED) + ["description"]


class GuardedAdmin(DjustModelAdmin):
    """``city`` is readonly; ``description`` is excluded, though the custom form declares it."""

    form = GuardedForm
    readonly_fields = ["city"]
    exclude = ["description"]


GUARDED_SITE = DjustAdminSite(name="change_form_4218_guarded")
GUARDED_SITE.register(Property, GuardedAdmin)


class HiddenForm(forms.ModelForm):
    class Meta:
        model = Property
        fields = "__all__"


class HiddenAdmin(DjustModelAdmin):
    """The form declares every field; the admin shows only the required ones."""

    form = HiddenForm
    fields = list(REQUIRED)


HIDDEN_SITE = DjustAdminSite(name="change_form_4218_hidden")
HIDDEN_SITE.register(Property, HiddenAdmin)


class RowCheckedDeleteAdmin(DjustModelAdmin):
    """A delete permission that reads the row."""

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.name == "protected":
            return False
        return super().has_delete_permission(request, obj)


ROW_DELETE_SITE = DjustAdminSite(name="change_form_4218_row_delete")
ROW_DELETE_SITE.register(Property, RowCheckedDeleteAdmin)

urlpatterns = [
    path("x4218-admin/", SITE.urls),
    path("x4218-radio/", RADIO_SITE.urls),
    path("x4218-guarded/", GUARDED_SITE.urls),
    path("x4218-hidden/", HIDDEN_SITE.urls),
    path("x4218-rowdelete/", ROW_DELETE_SITE.urls),
]
HIDDEN_LIST = "/x4218-hidden/djust_rentals/property/"
ROW_DELETE_LIST = "/x4218-rowdelete/djust_rentals/property/"
RADIO_LIST = "/x4218-radio/djust_rentals/property/"
GUARDED_LIST = "/x4218-guarded/djust_rentals/property/"
DELETE_VIEW = "djust.admin_ext.views.ModelDeleteView"

LIST = "/x4218-admin/djust_rentals/property/"
DETAIL_VIEW = "djust.admin_ext.views.ModelDetailView"
CREATE_VIEW = "djust.admin_ext.views.ModelCreateView"

#: What the person types: a text field, a choice field and a checkbox.
EDITS = {"name": "Typed name", "property_type": "condo", "pet_friendly": True}


@pytest.fixture
def site_urls(settings):
    from djust.routing import _reset_route_map_cache

    settings.ROOT_URLCONF = MODULE
    settings.ALLOWED_HOSTS = ["testserver"]
    settings.LIVEVIEW_ALLOWED_MODULES = ["djust", MODULE]
    _reset_route_map_cache()
    yield
    _reset_route_map_cache()


@pytest.fixture(params=["legacy", "strict"])
def policy(request):
    previous = config.get("event_parameter_policy", "strict")
    config.set("event_parameter_policy", request.param)
    yield request.param
    config.set("event_parameter_policy", previous)


# --------------------------------------------------------------------------- #
# A page model and the client's event construction
# --------------------------------------------------------------------------- #


class _Page(HTMLParser):
    """The form controls of a rendered page, with the attributes the client reads."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.controls: List[Dict[str, Any]] = []
        self.forms: List[Dict[str, str]] = []
        self.buttons: List[Dict[str, str]] = []
        self._select: Optional[Dict[str, Any]] = None
        self._textarea: Optional[Dict[str, Any]] = None
        self._contracts: Optional[List[str]] = None
        self.contracts: Dict[str, Any] = {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = {k: (v if v is not None else "") for k, v in attrs}
        if tag == "script" and "data-djust-parameter-contracts" in attrs:
            self._contracts = []
        elif tag == "form":
            self.forms.append(attrs)
        elif tag == "button":
            self.buttons.append(attrs)
        elif tag == "input" and attrs.get("name"):
            self.controls.append({"tag": "input", "attrs": attrs})
        elif tag == "select" and attrs.get("name"):
            self._select = {"tag": "select", "attrs": attrs, "options": []}
            self.controls.append(self._select)
        elif tag == "option" and self._select is not None:
            self._select["options"].append(attrs)
        elif tag == "textarea" and attrs.get("name"):
            self._textarea = {"tag": "textarea", "attrs": attrs, "text": ""}
            self.controls.append(self._textarea)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "script" and self._contracts is not None:
            block = json.loads("".join(self._contracts))
            (owner,) = [o for o in block["contracts"]["owners"] if o["view_id"] is None]
            self.contracts = owner["handlers"]
            self._contracts = None
        elif tag == "select":
            self._select = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._contracts is not None:
            self._contracts.append(data)
        if self._textarea is not None:
            self._textarea["text"] += data

    def control(self, name: str) -> Dict[str, Any]:
        found = [c for c in self.controls if c["attrs"]["name"] == name]
        assert len(found) == 1, (name, found)
        return found[0]

    def current(self, control: Dict[str, Any]) -> Any:
        """The control's value as the browser holds it (``_fieldValue``)."""
        attrs = control["attrs"]
        if control["tag"] == "textarea":
            return control["text"]
        if control["tag"] == "select":
            chosen = [o.get("value", "") for o in control["options"] if "selected" in o]
            if "multiple" in attrs:
                return chosen
            return (
                chosen[0]
                if chosen
                else (control["options"][0].get("value", "") if control["options"] else "")
            )
        if attrs.get("type") == "checkbox":
            return "checked" in attrs
        return attrs.get("value", "")


def _parse_handler(binding: str) -> Tuple[str, list]:
    """``parseEventHandler`` (08-event-parsing.js) for the shapes the admin emits."""
    binding = binding.strip()
    if "(" not in binding:
        return binding, []
    name, _, rest = binding.partition("(")
    inner = rest[: rest.rfind(")")].strip()
    args: list = []
    for token in [t.strip() for t in inner.split(",") if t.strip()]:
        if token[0] in "'\"" and token[-1] == token[0]:
            args.append(token[1:-1])
        elif token in ("true", "false"):
            args.append(token == "true")
        elif re.fullmatch(r"-?\d+", token):
            args.append(int(token))
        else:
            args.append(token)  # an unknown token travels as a string
    return name.strip(), args


def _contract(page: _Page, event: str) -> Dict[str, Any]:
    """The contract the page published for ``event`` (``_resolveParameterContract``)."""
    assert event in page.contracts, "the page publishes no contract for %s" % event
    return page.contracts[event]


def _strict(page: _Page, event: str, generated: dict, positional: list) -> dict:
    """``_strictBinding``: generated values only for declared names (all for ``**``)."""
    parameters = _contract(page, event)["parameters"]
    named = {
        p["name"] for p in parameters if p["kind"] in ("positional_or_keyword", "keyword_only")
    }
    open_ = any(p["kind"] == "var_keyword" for p in parameters)
    order = [p["name"] for p in parameters if p["kind"].startswith("positional")]
    params = {k: v for k, v in generated.items() if open_ or k in named}
    # The client refuses a value supplied both positionally and by name.
    assert not set(order[: len(positional)]) & set(params), (
        "the client rejects %s%r: a positional argument is also a generated value"
        % (event, positional)
    )
    if positional:
        params["_args"] = positional
    return params


def field_event(page: _Page, name: str, value: Any):
    """The event the client sends when the person sets ``name`` to ``value``."""
    control = page.control(name)
    attrs = control["attrs"]
    binding = attrs.get("dj-input") or attrs.get("dj-change")
    assert binding, "the field %s has no live binding: %s" % (name, attrs)
    event, args = _parse_handler(binding)
    generated = {"value": value, "field": attrs.get("data-field") or name}
    if _contract(page, event)["policy"] == "strict":
        return event, _strict(page, event, generated, args)
    params = dict(generated)
    if args:
        params["_args"] = args
    params["_target"] = name
    return event, params


def submit_event(page: _Page, typed: dict, button: Optional[str]):
    """The event the client sends when the person presses the submit button named ``button``."""
    forms = [f for f in page.forms if "dj-submit" in f]
    assert len(forms) == 1, "the change form has no single dj-submit form: %s" % forms
    event, _ = _parse_handler(forms[0]["dj-submit"])
    values: Dict[str, Any] = {}
    for control in page.controls:
        attrs = control["attrs"]
        name = attrs["name"]
        current = typed.get(name, page.current(control))
        if attrs.get("type") == "checkbox":
            if current:  # FormData: a checked box sends its value, an unchecked one nothing
                values[name] = attrs.get("value", "on")
        else:
            values[name] = current
    pressed = [b for b in page.buttons if b.get("type") == "submit" and b.get("name") == button]
    assert len(pressed) == 1, (button, page.buttons)
    submitter = pressed[0]
    if _contract(page, event)["policy"] == "strict":
        if submitter.get("name"):
            values[submitter["name"]] = submitter.get("value", "")
        return event, _strict(page, event, values, [])
    values["_target"] = submitter.get("name") or submitter.get("id") or None
    return event, values


# --------------------------------------------------------------------------- #
# Transports
# --------------------------------------------------------------------------- #


async def _staff(django_user_model) -> Client:
    user = await sync_to_async(django_user_model.objects.create_superuser)("root", password="pw")
    browser = Client()
    await sync_to_async(browser.force_login)(user)
    return browser


_ANSWERS = ("patch", "html_update", "noop", "error", "permission_denied", "navigate")


class _Run:
    """One page session over one transport: renders, then events built from the page.

    ``answers`` holds the answer to each event; ``navigation`` every ``live_redirect`` path
    the server sent (a ``navigation`` frame on the socket and the stream, ``_navigation`` on
    the HTTP fallback's answer)."""

    def __init__(self, transport: str, browser: Client, view: str, url: str):
        self.transport, self.browser, self.view, self.url = transport, browser, view, url
        self.answers: List[dict] = []
        self.navigation: List[str] = []

    async def html(self) -> str:
        page = await sync_to_async(self.browser.get)(self.url)
        assert page.status_code == 200
        self._tab = _tab(page)
        return page.content.decode()

    def _seen(self, frame: dict) -> bool:
        """Record a navigation frame; True when ``frame`` answers the event."""
        if frame.get("type") == "navigation":
            if frame.get("action") == "live_redirect":
                self.navigation.append(frame["path"])
            return False
        return frame.get("type") in _ANSWERS

    async def play(self, events: list) -> List[dict]:
        if self.transport == "websocket":
            async with _Socket(_session_cookie(self.browser)) as ws:
                await ws.mount(self.view, self.url)
                for item in events:
                    if callable(item):  # something else happens between events
                        await item()
                        continue
                    name, params = item
                    await ws.send({"type": "event", "event": name, "params": params})
                    self.answers.append(await self._ws_answer(ws))
        elif self.transport == "sse":
            await self._sse(events)
        else:
            for item in events:
                if callable(item):
                    await item()
                    continue
                name, params = item
                answer = await sync_to_async(_post)(self.browser, self.url, name, params, self._tab)
                self.navigation.extend(
                    n["path"]
                    for n in answer.get("_navigation", [])
                    if n["action"] == "live_redirect"
                )
                self.answers.append(answer)
        return self.answers

    async def _ws_answer(self, ws) -> dict:
        """The event's answer, then whatever else the server sent for that turn."""
        answer = None
        navigated = len(self.navigation)
        for _ in range(30):
            settled = answer is not None or len(self.navigation) > navigated
            if settled and await ws.comm.receive_nothing(timeout=0.3):
                # A live_redirect leaves the view: it may send no render at all.
                return answer or {"type": "navigation"}
            frame = await ws.comm.receive_json_from(timeout=10)
            if self._seen(frame) and answer is None:
                answer = frame
        raise AssertionError("no answer frame")

    async def _sse(self, events: list) -> None:
        from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

        key = self.browser.cookies[settings.SESSION_COOKIE_NAME].value
        sid = str(uuid.uuid4())
        opened = await sync_to_async(_sse_request)(
            "GET", f"/djust/sse/{sid}/", {"view": self.view, "_djust_url": self.url}, key
        )
        response = await DjustSSEStreamView().get(opened, session_id=sid)
        assert response.status_code == 200
        stream = response._iterator
        frames: "asyncio.Queue[dict]" = asyncio.Queue()

        async def read() -> None:
            async for chunk in stream:
                if chunk.startswith("data:"):
                    await frames.put(json.loads(chunk[len("data:") :]))

        reader = asyncio.ensure_future(read())
        try:
            mounted = await self._sse_next(frames, ("mount", "error"))
            assert mounted["type"] == "mount", mounted
            for item in events:
                if callable(item):
                    await item()
                    continue
                name, params = item
                posted = await sync_to_async(_sse_request)(
                    "POST",
                    f"/djust/sse/{sid}/message/",
                    {"type": "event", "event": name, "params": params},
                    key,
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
                self.answers.append(await self._sse_next(frames, _ANSWERS))
        finally:
            _sse_sessions.pop(sid, None)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            await stream.aclose()

    async def _sse_next(self, frames, types) -> dict:
        """The next frame of ``types``, then whatever else the server sent for that turn."""
        answer = None
        navigated = len(self.navigation)
        while True:
            timeout = 0.3 if answer is not None or len(self.navigation) > navigated else 10
            try:
                frame = await asyncio.wait_for(frames.get(), timeout)
            except asyncio.TimeoutError:
                if answer is None and len(self.navigation) > navigated:
                    return {"type": "navigation"}
                if answer is None:
                    raise AssertionError("no %s frame" % (types,))
                return answer
            if frame.get("type") == "navigation":
                self._seen(frame)
            elif answer is None and frame.get("type") in types:
                answer = frame


def _refusals(answers: List[dict]) -> List[dict]:
    return [a for a in answers if a.get("type") in ("error", "permission_denied") or a.get("error")]


def _text(answers: List[dict]) -> str:
    return json.dumps(answers)


async def _seed() -> Property:
    data = dict(REQUIRED)
    for key in ("bedrooms", "square_feet"):
        data[key] = int(data[key])
    for key in ("bathrooms", "monthly_rent", "security_deposit"):
        data[key] = Decimal(data[key])
    return await sync_to_async(Property.objects.create)(**data)


async def _saved(pk: int) -> Tuple[str, str, bool]:
    row = await sync_to_async(Property.objects.get)(pk=pk)
    return row.name, row.property_type, row.pet_friendly


TRANSPORTS = ["websocket", "sse", "http"]


# --------------------------------------------------------------------------- #
# #4218: typing reaches the form, and Save saves it
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_change_form_saves_what_was_typed(site_urls, django_user_model, transport, policy):
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    page = _Page(await run.html())
    events = [field_event(page, name, value) for name, value in EDITS.items()]
    events.append(submit_event(page, EDITS, "save_and_continue"))
    answers = await run.play(events)
    assert not _refusals(answers), answers
    assert await _saved(row.pk) == ("Typed name", "condo", True)
    assert "Changes saved successfully" in _text(answers[-1:])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_add_form_saves_what_was_typed(site_urls, django_user_model, transport, policy):
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, CREATE_VIEW, LIST + "add/")
    page = _Page(await run.html())
    typed = {**REQUIRED, **EDITS}
    events = [field_event(page, name, value) for name, value in typed.items()]
    events.append(submit_event(page, typed, "save_and_continue"))
    answers = await run.play(events)
    assert not _refusals(answers), answers
    rows = await sync_to_async(
        lambda: list(Property.objects.values_list("name", "property_type", "pet_friendly"))
    )()
    assert rows == [("Typed name", "condo", True)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_typed_field_is_validated(site_urls, django_user_model, transport, policy):
    """The typed value reaches the form and is validated: a blank required field reports it."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    page = _Page(await run.html())
    answers = await run.play([field_event(page, "city", "")])
    assert not _refusals(answers), answers
    assert "This field is required." in _text(answers)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_save_without_the_forms_values_fails_loudly(site_urls, django_user_model, transport):
    """A Save that carries none of the form's values saves nothing and says so."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    await run.html()
    answers = await run.play([("save_and_continue", {})])
    text = _text(answers)
    assert "Changes saved successfully" not in text
    assert "nothing was saved" in text, text[:2000]
    assert await _saved(row.pk) == ("Seed house", "house", False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_save_saves_the_values_it_carries(site_urls, django_user_model, transport, policy):
    """No field event arrived (a lost one, or none sent): Save alone saves the form on the page,
    and an unchecked checkbox (absent from the submission, as in a native post) saves False."""
    row = await _seed()
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(pet_friendly=True)
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    page = _Page(await run.html())
    typed = {"name": "Typed name", "property_type": "condo", "pet_friendly": False}
    answers = await run.play([submit_event(page, typed, "save_and_continue")])
    assert not _refusals(answers), answers
    assert await _saved(row.pk) == ("Typed name", "condo", False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "button, goes_to",
    [(None, "property/"), ("save_and_continue", None), ("save_and_add_another", "property/add/")],
)
async def test_each_button_goes_where_it_says(
    site_urls, django_user_model, policy, button, goes_to
):
    """The pressed button reaches ``save`` (its name=value under strict, ``_target`` under legacy)
    and the page goes where the button says."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run("http", browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    page = _Page(await run.html())
    answers = await run.play([submit_event(page, EDITS, button)])
    assert await _saved(row.pk) == ("Typed name", "condo", True)
    navigation = [
        frame["path"]
        for frame in answers[-1].get("_navigation", [])
        if frame.get("action") == "live_redirect"
    ]
    if goes_to is None:
        assert navigation == [] and "Changes saved successfully" in answers[-1]["html"], answers
    else:
        assert len(navigation) == 1 and navigation[0].endswith(goes_to), answers


# --------------------------------------------------------------------------- #
# Review of #4218: add-page navigation, readonly fields, radios, delete
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_add_then_continue_goes_to_the_new_rows_change_page(
    site_urls, django_user_model, transport, policy
):
    """As in Django: the add page does not stay an add page (its next Save would add again)."""
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, CREATE_VIEW, LIST + "add/")
    page = _Page(await run.html())
    typed = {**REQUIRED, **EDITS}
    await run.play([submit_event(page, typed, "save_and_continue")])
    row = await sync_to_async(Property.objects.get)()
    assert run.navigation == ["%s%d/change/" % (LIST, row.pk)], run.answers


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_continue_on_a_change_page_stays(site_urls, django_user_model, transport):
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (LIST, row.pk))
    page = _Page(await run.html())
    await run.play([submit_event(page, EDITS, "save_and_continue")])
    assert run.navigation == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_forged_frame_does_not_write_readonly_or_excluded_fields(
    site_urls, django_user_model, transport, policy
):
    row = await _seed()
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(description="kept")
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (GUARDED_LIST, row.pk))
    page = _Page(await run.html())
    names = {c["attrs"]["name"] for c in page.controls}
    assert "city" not in names and "description" not in names, names  # shown, not inputs
    _, save = submit_event(page, {"name": "Typed name"}, "save_and_continue")
    forged = [
        ("validate_field", {"field": "city", "value": "Forged"}),
        ("validate_field", {"field": "description", "value": "Forged"}),
        ("save_and_continue", {**save, "city": "Forged", "description": "Forged"}),
    ]
    answers = await run.play(forged)
    assert not _refusals(answers), answers
    saved = await sync_to_async(
        lambda: Property.objects.values_list("name", "city", "description").get(pk=row.pk)
    )()
    assert saved == ("Typed name", "Austin", "kept")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_radio_choice_field_renders_every_choice_and_saves(
    site_urls, django_user_model, transport
):
    """One radio per choice, the stored value checked; the form submits (a lone unchecked
    required radio blocked every Save). A zero is shown, not emptied."""
    row = await _seed()
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(bedrooms=0)
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (RADIO_LIST, row.pk))
    page = _Page(await run.html())
    radios = [c["attrs"] for c in page.controls if c["attrs"]["name"] == "property_type"]
    values = [a["value"] for a in radios if a["value"]]
    assert values == [v for v, _ in Property.PROPERTY_TYPES], radios
    assert [a["value"] for a in radios if "checked" in a] == ["house"], radios
    assert page.control("bedrooms")["attrs"]["value"] == "0"
    # The form's own submission: the checked radio's value, as FormData sends it.
    _, save = submit_event(page, {"name": "Typed name"}, "save_and_continue")
    save["property_type"] = "condo"
    answers = await run.play(
        [
            ("validate_field", {"field": "property_type", "value": "condo"}),
            ("save_and_continue", save),
        ]
    )
    assert not _refusals(answers), answers
    assert await _saved(row.pk) == ("Typed name", "condo", False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_delete_removes_the_row_and_goes_to_the_change_list(
    site_urls, django_user_model, transport
):
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DELETE_VIEW, "%s%d/delete/" % (LIST, row.pk))
    await run.html()
    answers = await run.play([("confirm_delete", {})])
    assert not _refusals(answers), answers
    assert not await sync_to_async(Property.objects.filter(pk=row.pk).exists)()
    assert run.navigation == [LIST], (run.navigation, answers)


# --------------------------------------------------------------------------- #
# Readonly, excluded and hidden fields are not form fields (Django's admin form)
# --------------------------------------------------------------------------- #


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_readonly_or_excluded_change_made_meanwhile_survives_a_save(
    site_urls, django_user_model, transport
):
    """The readonly ``city`` is text read from the row, not a form field, so a Save does not
    write the value the page was loaded with over a change made since."""
    row = await _seed()
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(description="kept")
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (GUARDED_LIST, row.pk))
    html = await run.html()
    page = _Page(html)
    assert "city" not in {c["attrs"]["name"] for c in page.controls}
    assert re.search(r"City</span>\s*<div[^>]*>\s*<p[^>]*>Austin</p>", html), "city not shown"
    _, save = submit_event(page, {"name": "Typed name"}, "save_and_continue")
    await sync_to_async(Property.objects.filter(pk=row.pk).update)(city="Dallas", description="new")
    answers = await run.play([("save_and_continue", save)])
    assert not _refusals(answers), answers
    saved = await sync_to_async(
        lambda: Property.objects.values_list("name", "city", "description").get(pk=row.pk)
    )()
    assert saved == ("Typed name", "Dallas", "new")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_an_add_page_with_a_required_readonly_field_saves(
    site_urls, django_user_model, transport
):
    """``city`` is required on the model and readonly in the admin: the add page has no input
    for it, and the Save is not refused for it."""
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, CREATE_VIEW, GUARDED_LIST + "add/")
    page = _Page(await run.html())
    typed = {k: v for k, v in REQUIRED.items() if k != "city"}
    _, save = submit_event(page, typed, "save_and_continue")
    answers = await run.play([("save_and_continue", save)])
    assert not _refusals(answers), answers
    assert await sync_to_async(lambda: list(Property.objects.values_list("name", "city")))() == [
        (REQUIRED["name"], "")
    ]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_forged_frame_does_not_write_a_field_the_admin_does_not_show(
    site_urls, django_user_model, transport, policy
):
    """The custom form declares ``status`` and ``description``; ``ModelAdmin.fields`` leaves
    them off the page, so they are not the client's to write."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (HIDDEN_LIST, row.pk))
    page = _Page(await run.html())
    names = {c["attrs"]["name"] for c in page.controls}
    assert not names & {"status", "description"}, names
    _, save = submit_event(page, {"name": "Typed name"}, "save_and_continue")
    answers = await run.play(
        [
            ("validate_field", {"field": "status", "value": "maintenance"}),
            ("save_and_continue", {**save, "status": "maintenance", "description": "forged"}),
        ]
    )
    assert not _refusals(answers), answers
    saved = await sync_to_async(
        lambda: Property.objects.values_list("name", "status", "description").get(pk=row.pk)
    )()
    assert saved == ("Typed name", "available", "")


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
@pytest.mark.parametrize("name, allowed", [("Seed house", True), ("protected", False)])
async def test_a_delete_permission_that_reads_the_row(
    site_urls, django_user_model, transport, name, allowed
):
    """``has_delete_permission(request, obj)`` gets the row on every transport (over the HTTP
    fallback it got the view's restored state)."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DELETE_VIEW, "%s%d/delete/" % (ROW_DELETE_LIST, row.pk))
    await run.html()

    async def rename() -> None:
        # What the permission reads changes after the page mounted: the row decides the delete.
        await sync_to_async(Property.objects.filter(pk=row.pk).update)(name=name)

    if transport == "http":
        await rename()
        response = await sync_to_async(run.browser.post)(
            run.url,
            data="{}",
            content_type="application/json",
            HTTP_X_DJUST_EVENT="confirm_delete",
            HTTP_X_DJUST_TAB=run._tab,
        )
        assert response.status_code in (200, 403), response.content[:300]
        refused = response.status_code == 403
        if not refused:
            run.navigation.extend(
                n["path"]
                for n in json.loads(response.content).get("_navigation", [])
                if n["action"] == "live_redirect"
            )
    else:
        refused = bool(_refusals(await run.play([rename, ("confirm_delete", {})])))
    exists = await sync_to_async(Property.objects.filter(pk=row.pk).exists)()
    if allowed:
        assert not refused and not exists
        assert run.navigation == [ROW_DELETE_LIST]
    else:
        assert refused and exists and run.navigation == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", TRANSPORTS)
async def test_a_save_carrying_only_fields_the_page_does_not_edit_saves_nothing(
    site_urls, django_user_model, transport
):
    """Values for readonly or hidden names are not the form's values: such a Save is a Save
    without them, and says so."""
    row = await _seed()
    browser = await _staff(django_user_model)
    run = _Run(transport, browser, DETAIL_VIEW, "%s%d/change/" % (HIDDEN_LIST, row.pk))
    await run.html()
    answers = await run.play([("save_and_continue", {"status": "maintenance"})])
    assert "nothing was saved" in _text(answers), answers
    assert await sync_to_async(lambda: Property.objects.get(pk=row.pk).status)() == "available"
