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

import json
import re
from decimal import Decimal
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.test import Client
from django.urls import path

from djust.admin_ext import DjustAdminSite, DjustModelAdmin
from djust.config import config
from djust.tests.test_admin_page_render import _Socket, _session_cookie
from djust.tests.test_framework_classes_explicit_4185 import _post, _sse, _tab
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

urlpatterns = [path("x4218-admin/", SITE.urls)]

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
    previous = config.get("event_parameter_policy", "legacy")
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
    """One page session over one transport: renders, then events built from the page."""

    def __init__(self, transport: str, browser: Client, view: str, url: str):
        self.transport, self.browser, self.view, self.url = transport, browser, view, url
        self.answers: List[dict] = []

    async def html(self) -> str:
        page = await sync_to_async(self.browser.get)(self.url)
        assert page.status_code == 200
        self._tab = _tab(page)
        return page.content.decode()

    async def play(self, events: list) -> List[dict]:
        if self.transport == "websocket":
            async with _Socket(_session_cookie(self.browser)) as ws:
                await ws.mount(self.view, self.url)
                for name, params in events:
                    await ws.send({"type": "event", "event": name, "params": params})
                    self.answers.append(await _ws_answer(ws))
        elif self.transport == "sse":
            key = self.browser.cookies[settings.SESSION_COOKIE_NAME].value
            mounted, answers = await _sse(self.view, self.url, key, events)
            assert mounted["type"] == "mount", mounted
            self.answers.extend(answers)
        else:
            for name, params in events:
                answer = await sync_to_async(_post)(self.browser, self.url, name, params, self._tab)
                self.answers.append(answer)
        return self.answers


async def _ws_answer(ws) -> dict:
    for _ in range(30):
        frame = await ws.comm.receive_json_from(timeout=10)
        if frame.get("type") in _ANSWERS:
            return frame
    raise AssertionError("no answer frame")


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
