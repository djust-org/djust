"""``dj-auto-recover`` handlers under the strict parameter policy (#4178).

1.x delivered a recovery container's form values and ``data-*`` attributes as
``_form_values`` / ``_data_attrs``. Strict signatures cannot declare ``_``
names, so dispatch forced every recovery target to the legacy policy
(ADR-036 R1). In 2.0 the two dictionaries are generated values (ADR-036 Q1),
declared like any other argument::

    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
        ...

The browser sends only the names the handler declares, and the server binds
and validates them with the handler's strict contract, under the same
resource limits as every event argument.

The reconnect tests run the built client in jsdom against the real server
(``_auto_recover_bridge``): a ``WebsocketCommunicator`` on ``LiveViewConsumer``,
the SSE stream and message views, and the view's own page POST.
"""

from __future__ import annotations

import json
import sys
import types
from typing import Any, Optional

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.core.checks import ERROR, WARNING
from django.test import RequestFactory, override_settings

from djust import LiveView, state
from djust.components.descriptors.base import LiveComponent, TypedState
from djust._parameter_contract import ContractError, ParameterContract, ParameterError
from djust.decorators import event_handler
from djust.websocket import LiveViewConsumer

from ._auto_recover_bridge import Browser, SSEServer, http_answer, ws_connect, ws_forward
from ._ws_frames import drain_extra, has_type, receive_until

FORM = """<div dj-root>
<form dj-auto-recover="restore" data-step="2" data-mode="edit">
  <input name="title" value="">
  <textarea name="body"></textarea>
  <input type="radio" name="tone" value="calm" checked><input type="radio" name="tone" value="loud">
  <input type="checkbox" name="tags" value="a"><input type="checkbox" name="tags" value="b">
  <input type="checkbox" name="urgent">
  <select name="size"><option value="s">s</option><option value="l">l</option></select>
  <input type="password" name="secret" value="">
</form>
<p id="restored">{{ title }}|{{ body }}|{{ tone }}|{{ tags|join:"," }}|{% if urgent %}urgent{% endif %}|{{ size }}|{{ step }}|{{ mode }}</p>
</div>"""

RESTORED = "Draft|Long text|loud|b|urgent|l|2|edit"

#: What the user typed before the connection dropped.
TYPE_INTO_FORM = """
document.querySelector('[name=title]').value = 'Draft';
document.querySelector('[name=body]').value = 'Long text';
document.querySelectorAll('[name=tone]')[1].checked = true;
document.querySelectorAll('[name=tags]')[1].checked = true;
document.querySelector('[name=urgent]').checked = true;
document.querySelector('[name=size]').value = 'l';
document.querySelector('[name=secret]').value = 'hunter2';
null
"""

READ_RESTORED = "document.querySelector('#restored').textContent"

#: The values the client reads from the form above (no password, #4078).
EXPECTED_FORM = {
    "title": "Draft",
    "body": "Long text",
    "tone": "loud",
    "tags": ["b"],
    "urgent": True,
    "size": "l",
}
EXPECTED_DATA = {"step": "2", "mode": "edit"}

CALLS: list = []


class _Base(LiveView):
    """Explicit exposure (the default): every rendered name is a declared ``state()``."""

    exposure_policy = "explicit"
    template = FORM
    login_required = False
    title = state("")
    body = state("")
    tone = state("")
    tags = state(default_factory=list)
    urgent = state(False)
    size = state("")
    step = state("")
    mode = state("")

    def _apply(self, form_values: dict, data_attrs: dict) -> None:
        self.title = form_values.get("title", "")
        self.body = form_values.get("body", "")
        self.tone = form_values.get("tone", "")
        self.tags = list(form_values.get("tags", []))
        self.urgent = bool(form_values.get("urgent", False))
        self.size = form_values.get("size", "")
        self.step = data_attrs.get("step", "")
        self.mode = data_attrs.get("mode", "")


class StrictRecovery(_Base):
    """The 2.0 spelling."""

    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]) -> None:
        CALLS.append(("strict", form_values, data_attrs))
        self._apply(form_values, data_attrs)


class FormValuesOnly(_Base):
    """A handler that declares one of the two receives only that one."""

    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any]) -> None:
        CALLS.append(("form_values_only", form_values))
        self._apply(form_values, {})


class OpenRecovery(_Base):
    """A ``**`` catch-all receives every generated value (ADR-036 Q1)."""

    @event_handler(parameter_policy="strict")
    def restore(self, **values: Any) -> None:
        CALLS.append(("open", sorted(values)))
        self._apply(values["form_values"], values["data_attrs"])


class ProjectStrict(_Base):
    """No handler declaration: the project's strict policy applies."""

    @event_handler()
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]) -> None:
        CALLS.append(("project", form_values, data_attrs))
        self._apply(form_values, data_attrs)


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    from djust import sse

    monkeypatch.setattr(sse, "_SESSION_LINGER_S", 0)
    CALLS.clear()
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], DEBUG=True):
        yield
    CALLS.clear()


def _page(view_class: type) -> tuple[str, SessionStore]:
    session = SessionStore()
    session.create()
    request = RequestFactory().get("/4178/")
    request.user, request.session, request.tenant = AnonymousUser(), session, None
    response = view_class.as_view()(request)
    assert response.status_code == 200
    return response.content.decode(), session


async def _recover(
    transport: str,
    view_class: type,
    type_into: str = TYPE_INTO_FORM,
    read: str = READ_RESTORED,
) -> tuple[dict, str, Browser]:
    """Type into the page, lose the connection, reconnect.

    Returns the recovery event's params as the client sent them, the restored
    paragraph's text, and the page (the caller closes it).
    """
    html, session = await sync_to_async(_page)(view_class)
    browser = await Browser.open(html, transport)
    try:
        if transport == "ws":
            socket_id, socket = await ws_connect(browser, session, LiveViewConsumer)
            await browser.eval(type_into)
            await browser.eval("window.djust.liveViewInstance.reconnectDelay = 1; null")
            # The server side goes away; the client reconnects on its timer.
            await socket.disconnect()
            await browser.tell(op="ws_close", id=socket_id, code=1006)
            socket_id, socket = await ws_connect(browser, session, LiveViewConsumer)
            frame = await browser.expect(
                lambda m: m["op"] == "ws_send" and json.loads(m["data"]).get("type") == "event",
                "the recovery event",
            )
            await ws_forward(browser, socket_id, socket, frame)
            await socket.disconnect()
            params = json.loads(frame["data"])["params"]
        elif transport == "sse":
            server = SSEServer(browser, session)
            try:
                await server.open_stream()
                await server.answer_posts(lambda body: body.get("type") == "mount")
                await browser.eval(type_into)
                # The stream drops; the page's EventSource opens it again.
                await server.drop()
                await server.open_stream()
                event = await server.answer_posts(lambda body: body.get("type") == "event")
            finally:
                await server.drop()
            params = event["params"]
        else:
            # The page POST has no reconnect, so the client never runs
            # recovery on it; fire it as a reconnect would.
            await browser.eval(type_into)
            await browser.eval(
                "window.djust._isReconnect = true; window.djust._processAutoRecover(); null"
            )
            fetch = await http_answer(browser, view_class, session)
            assert fetch["headers"]["X-Djust-Event"] == "restore"
            params = json.loads(fetch["body"])
        return params, await browser.eval(read), browser
    except BaseException:
        await browser.close()
        raise


def _application(params: dict) -> dict:
    """The payload without transport bookkeeping (``_cacheRequestId``)."""
    return {k: v for k, v in params.items() if k != "_cacheRequestId"}


def _errors(browser: Browser) -> list:
    return [log for log in browser.logs if log["level"] in ("error", "jsdomError")]


# --- the real client, per transport ------------------------------------------


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["ws", "sse", "http"])
async def test_a_strict_recovery_handler_restores_a_multi_field_form(transport):
    params, text, browser = await _recover(transport, StrictRecovery)
    try:
        # Public names only: nothing a strict contract refuses, no password.
        assert _application(params) == {"form_values": EXPECTED_FORM, "data_attrs": EXPECTED_DATA}
        assert CALLS == [("strict", EXPECTED_FORM, EXPECTED_DATA)]
        # The handler's declared state() fields reached the page.
        assert text == RESTORED
        assert not _errors(browser)
    finally:
        await browser.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_only_the_declared_recovery_values_are_sent(transport):
    params, text, browser = await _recover(transport, FormValuesOnly)
    try:
        assert _application(params) == {"form_values": EXPECTED_FORM}
        assert CALLS == [("form_values_only", EXPECTED_FORM)]
        assert text == "Draft|Long text|loud|b|urgent|l||"
    finally:
        await browser.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_a_catch_all_receives_both_recovery_values():
    params, text, browser = await _recover("ws", OpenRecovery)
    try:
        assert _application(params) == {"form_values": EXPECTED_FORM, "data_attrs": EXPECTED_DATA}
        assert CALLS == [("open", ["data_attrs", "form_values"])]
        assert text == RESTORED
    finally:
        await browser.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_the_strict_project_policy_needs_no_handler_declaration():
    with override_settings(LIVEVIEW_CONFIG={"event_parameter_policy": "strict"}):
        params, text, browser = await _recover("ws", ProjectStrict)
    try:
        assert _application(params) == {"form_values": EXPECTED_FORM, "data_attrs": EXPECTED_DATA}
        assert CALLS == [("project", EXPECTED_FORM, EXPECTED_DATA)]
        assert text == RESTORED
    finally:
        await browser.close()


# --- a recovery container inside a LiveComponent ------------------------------


class RecoveryEditor(LiveComponent):
    """A component whose own markup holds the recovery container."""

    class State(TypedState):
        label: str = "editor"

    template = '<form dj-auto-recover="restore" data-step="2"><input name="title" value=""></form>'

    @event_handler()
    def rename(self, value: str = "", **kwargs: Any) -> None:
        self.state.label = value


class StrictRecoveryInComponent(LiveView):
    """The page's strict handler; the container sits in the component's wrapper."""

    exposure_policy = "explicit"
    login_required = False
    template = '<div dj-root>{{ editor }}<p id="restored">{{ title }}|{{ step }}</p></div>'
    editor = RecoveryEditor()
    title = state("")
    step = state("")

    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]) -> None:
        CALLS.append(("component", form_values, data_attrs))
        self.title = form_values.get("title", "")
        self.step = data_attrs.get("step", "")


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["ws", "sse"])
async def test_a_recovery_container_inside_a_component_reaches_the_page_handler(transport):
    """The event carries no component context, so the server routes it to the
    page's view; the client sends the strict values that view's contract takes,
    not the 1.x envelope the component's (unlisting) contract would imply."""
    html, _session = await sync_to_async(_page)(StrictRecoveryInComponent)
    assert "data-component-id" in html  # the component's real wrapper markup
    params, text, browser = await _recover(
        transport,
        StrictRecoveryInComponent,
        type_into="document.querySelector('[name=title]').value = 'Draft'; null",
    )
    try:
        assert await browser.eval(
            "!!document.querySelector('[dj-auto-recover]').closest('[data-component-id]')"
        )
        assert _application(params) == {
            "form_values": {"title": "Draft"},
            "data_attrs": {"step": "2"},
        }
        assert CALLS == [("component", {"title": "Draft"}, {"step": "2"})]
        assert text == "Draft|2"
        assert not _errors(browser)
    finally:
        await browser.close()


# --- untrusted input: the server binds what any client may send --------------


async def _socket(view_class: type) -> WebsocketCommunicator:
    _html, session = await sync_to_async(_page)(view_class)
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope.update(session=session, user=AnonymousUser(), tenant=None)
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": "/4178/"}
    )
    assert (await socket.receive_json_from())["type"] == "mount"
    await drain_extra(socket)
    return socket


async def _send(socket: WebsocketCommunicator, params: Any) -> Optional[str]:
    """The error a recovery event gets, or None when the handler ran."""
    await socket.send_json_to({"type": "event", "event": "restore", "params": params})
    frames = await receive_until(socket, has_type("error", "patch", "html_update", "noop"))
    frames += await drain_extra(socket)
    errors = [f.get("error") for f in frames if f.get("type") == "error"]
    return errors[0] if errors else None


def _nested(depth: int) -> Any:
    value: Any = "x"
    for _ in range(depth):
        value = {"k": value}
    return value


UNTRUSTED = {
    "the 1.x envelope": {"_form_values": {"title": "x"}, "_data_attrs": {}},
    "a list for the form values": {"form_values": ["title"], "data_attrs": {}},
    "a string for the form values": {"form_values": "title=x", "data_attrs": {}},
    "a number in the data attributes": {"form_values": {}, "data_attrs": {"step": 2}},
    "an object in the data attributes": {"form_values": {}, "data_attrs": {"step": {"a": "b"}}},
    "a list in the data attributes": {"form_values": {}, "data_attrs": {"step": ["2"]}},
    "too many controls": {"form_values": {"f%d" % i: "" for i in range(1025)}, "data_attrs": {}},
    "too much text": {"form_values": {"body": "x" * 65537}, "data_attrs": {}},
    "nesting past the depth limit": {"form_values": {"deep": _nested(40)}, "data_attrs": {}},
    "a missing declared value": {"form_values": {}},
    "an undeclared extra value": {"form_values": {}, "data_attrs": {}, "admin": True},
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
@pytest.mark.parametrize("case", sorted(UNTRUSTED))
async def test_untrusted_recovery_values_are_refused_before_the_handler(case):
    socket = await _socket(StrictRecovery)
    try:
        assert await _send(socket, UNTRUSTED[case]), case
        assert CALLS == [], case
        # The view still answers a valid recovery.
        assert await _send(socket, {"form_values": {"title": "ok"}, "data_attrs": {}}) is None
        assert CALLS == [("strict", {"title": "ok"}, {})]
    finally:
        await socket.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_nested_form_values_within_the_limits_reach_an_any_value():
    """``dict[str, Any]`` checks the keys and the resource limits, not the values."""
    socket = await _socket(StrictRecovery)
    try:
        nested = {"title": "t", "deep": _nested(10), "tags": ["a", "b"]}
        assert await _send(socket, {"form_values": nested, "data_attrs": {"step": "1"}}) is None
        assert CALLS == [("strict", nested, {"step": "1"})]
    finally:
        await socket.disconnect()


# --- the contract type -------------------------------------------------------


def _contract(annotation: Any) -> ParameterContract:
    def handler(value):
        pass

    handler.__annotations__ = {"value": annotation}
    return ParameterContract.compile(handler)


def test_dict_str_t_converts_each_value_like_a_list_member():
    contract = _contract(dict[str, int])
    assert contract.metadata()[0]["type"] == "dict[str, int]"
    assert contract.metadata()[0]["reduced_checking"] is False
    assert contract.bind({"value": {"a": "1", "b": 2}}).arguments == {"value": {"a": 1, "b": 2}}
    for bad in ({"a": "x"}, ["1"], "a=1", {"a": [1]}):
        with pytest.raises(ParameterError):
            contract.bind({"value": bad})
    with pytest.raises(ParameterError):
        contract.bind({"value": {"a": "1"}}, coerce=False)
    assert _contract(dict[str, Any]).metadata()[0]["reduced_checking"] is True
    nested = _contract(dict[str, list[str]])
    assert nested.bind({"value": {"a": ["x"]}}).arguments == {"value": {"a": ["x"]}}


def test_dict_keys_must_be_strings():
    with pytest.raises(ContractError):
        _contract(dict[int, str])
    with pytest.raises(ContractError):
        _contract(dict)
    # A Python caller (not JSON) can pass a non-string key; it is refused.
    with pytest.raises(ParameterError):
        _contract(dict[str, Any]).bind({"value": {1: "x"}})


# --- the upgrade check (djust.V031) -------------------------------------------

_VIEW = """
from typing import Any
from djust import LiveView
from djust.decorators import event_handler

class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'
%s
"""

OLD_DECLARED = """
    @event_handler()
    def restore(self, _form_values=None, _data_attrs=None, **kwargs):
        pass
"""
OLD_KWARGS = """
    @event_handler()
    def restore(self, **kwargs):
        values = kwargs.get("_form_values", {})
"""
NEW = """
    @event_handler()
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
        pass
"""
NEW_SIGNATURE = "def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str])"


def _check(handler_source: str, **liveview_config: Any) -> list:
    """The V016 / V019 / V031 messages about one view built from ``handler_source``."""
    from djust.checks.parameters import check_event_parameter_contracts

    name = "djust_4178_check_%d" % id(handler_source)
    module = types.ModuleType(name)
    module.__file__ = __file__  # inspect.getsource reads the module's file
    sys.modules[name] = module
    try:
        exec(compile(_VIEW % handler_source, "<4178>", "exec"), module.__dict__)  # noqa: S102
        # The "under legacy" cases state the project policy; strict is the default (#4189).
        with override_settings(
            LIVEVIEW_CONFIG={"event_parameter_policy": "legacy", **liveview_config}
        ):
            messages = check_event_parameter_contracts(None)
    finally:
        del sys.modules[name]
        module.Page.abstract = True  # out of later checks' subclass walk
    return [
        m for m in messages if m.id in ("djust.V016", "djust.V019", "djust.V031") and name in m.msg
    ]


@pytest.mark.parametrize("handler", [OLD_DECLARED, OLD_KWARGS], ids=["declared", "kwargs"])
def test_v031_warns_about_the_envelope_under_legacy(handler, monkeypatch):
    _source_for(monkeypatch, handler)
    found = _check(handler)
    assert [(m.id, m.level) for m in found] == [("djust.V031", WARNING)]
    assert "2.0 removes it" in found[0].msg
    assert NEW_SIGNATURE in found[0].hint


@pytest.mark.parametrize("handler", [OLD_DECLARED, OLD_KWARGS], ids=["declared", "kwargs"])
def test_v031_is_an_error_for_the_envelope_under_strict(handler, monkeypatch):
    _source_for(monkeypatch, handler)
    found = _check(handler, event_parameter_policy="strict")
    # One message naming the replacement, not also V016's reserved-name error.
    assert [(m.id, m.level) for m in found] == [("djust.V031", ERROR)]
    assert "strict dispatch never delivers" in found[0].msg
    assert NEW_SIGNATURE in found[0].hint


def test_v031_is_quiet_for_the_2_0_spelling_under_strict():
    assert _check(NEW, event_parameter_policy="strict") == []


def test_v031_is_an_error_for_the_2_0_spelling_under_legacy():
    found = _check(NEW)
    assert [(m.id, m.level) for m in found] == [("djust.V031", ERROR)]
    assert "legacy parameter policy" in found[0].msg


def test_v031_ignores_a_read_in_a_handler_no_recovery_binding_targets(monkeypatch):
    other = OLD_KWARGS.replace("def restore", "def other")
    _source_for(monkeypatch, other)
    assert _check(other, event_parameter_policy="strict") == []


def _check_module(source: str, monkeypatch, **liveview_config: Any) -> list:
    """V016 / V031 messages about the classes ``source`` defines (its own module)."""
    from djust.checks.parameters import check_event_parameter_contracts

    _source_for(monkeypatch, source)
    name = "djust_4178_module_%d" % id(source)
    module = types.ModuleType(name)
    module.__file__ = __file__
    sys.modules[name] = module
    try:
        exec(compile(source, "<4178>", "exec"), module.__dict__)  # noqa: S102
        # The "under legacy" cases state the project policy; strict is the default (#4189).
        with override_settings(
            LIVEVIEW_CONFIG={"event_parameter_policy": "legacy", **liveview_config}
        ):
            messages = check_event_parameter_contracts(None)
    finally:
        del sys.modules[name]
        for value in list(module.__dict__.values()):
            if isinstance(value, type) and value.__module__ == name:
                value.abstract = True  # out of later checks' subclass walk
    return [
        (m.id, m.level, m.msg)
        for m in messages
        if m.id in ("djust.V016", "djust.V031") and name in m.msg
    ]


_HEAD = """
from typing import Any
from djust import LiveView
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import event_handler
"""

COMPUTED = (
    _HEAD
    + """
class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><form dj-auto-recover="{{ handler }}"></form></div>'

    @event_handler()
    def restore(self, **kwargs):
        values = kwargs.get("_form_values", {})
"""
)


@pytest.mark.parametrize(
    "config, level", [({}, WARNING), ({"event_parameter_policy": "strict"}, ERROR)]
)
def test_v031_reports_a_computed_target_that_reads_the_envelope(config, level, monkeypatch):
    """dj-auto-recover="{{ handler }}" cannot be resolved statically; a handler
    reading _form_values from **kwargs is a recovery handler by that read."""
    found = _check_module(COMPUTED, monkeypatch, **config)
    assert [(i, lvl) for i, lvl, _ in found] == [("djust.V031", level)]
    assert "possible target of a computed dj-auto-recover" in found[0][2]
    assert "reads _form_values from **kwargs" in found[0][2]


def test_v031_ignores_a_handler_a_computed_target_may_name_but_that_reads_nothing(monkeypatch):
    source = COMPUTED.replace('values = kwargs.get("_form_values", {})', "pass")
    assert _check_module(source, monkeypatch, event_parameter_policy="strict") == []


POSITIONAL = (
    _HEAD
    + """
class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'

    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any], /, data_attrs: dict[str, str]):
        pass
"""
)


def test_v031_reports_positional_only_recovery_parameters_under_strict(monkeypatch):
    """The browser sends the values by name, so a positional-only one never binds."""
    found = _check_module(POSITIONAL, monkeypatch)
    assert [(i, lvl) for i, lvl, _ in found] == [("djust.V031", ERROR)]
    assert "declares form_values positional-only" in found[0][2]


INHERITED = (
    _HEAD
    + """
class Base(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root></div>'

    @event_handler()
    def restore(self, _form_values=None, **kwargs):
        pass

class First(Base):
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'

class Second(Base):
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'
"""
)


@pytest.mark.parametrize("config", [{}, {"event_parameter_policy": "strict"}])
def test_v031_reports_an_inherited_handler_once(config, monkeypatch):
    """One message per declaration, not one per view whose template targets it,
    and no V016 for its "_" names under any of them."""
    found = _check_module(INHERITED, monkeypatch, **config)
    assert [i for i, _lvl, _msg in found] == ["djust.V031"]
    assert "this dj-auto-recover handler declares _form_values" in found[0][2]


NOT_A_TARGET = (
    _HEAD
    + """
class Page(LiveView):
    exposure_policy = "explicit"
    template = '<div dj-root><form dj-auto-recover="restore"></form></div>'

    @event_handler()
    def restore(self, form_values: dict[str, Any], data_attrs: dict[str, str]):
        pass

    @event_handler()
    def other(self, _form_values=None, **kwargs):
        pass

class Editor(LiveComponent):
    class State(TypedState):
        label: str = ""

    template = '<form dj-auto-recover="save"></form>'

    @event_handler()
    def save(self, _form_values=None, **kwargs):
        pass
"""
)


def test_v031_leaves_untargeted_and_component_handlers_to_v016(monkeypatch):
    """A "_" parameter on a handler no recovery binding targets, or on a
    LiveComponent (recovery events reach the page's view), is V016's reserved
    name under strict, and nothing under legacy."""
    strict = _check_module(NOT_A_TARGET, monkeypatch, event_parameter_policy="strict")
    # "<module>.Page.other(): ..." -> "Page.other"
    assert sorted((i, m.split("(")[0].split(".", 1)[1]) for i, _lvl, m in strict) == [
        ("djust.V016", "Editor.save"),
        ("djust.V016", "Page.other"),
    ]
    legacy = _check_module(NOT_A_TARGET, monkeypatch)
    # Only the 2.0 spelling under legacy, on the real target.
    assert [(i, lvl) for i, lvl, _ in legacy] == [("djust.V031", ERROR)]
    assert ".restore()" in legacy[0][2]


def test_v019_is_never_emitted():
    """The R1 downgrade it reported is gone (#4178); the ID is retired."""
    strict_old = OLD_DECLARED.replace(
        "@event_handler()", '@event_handler(parameter_policy="strict")'
    )
    assert [m.id for m in _check(strict_old)] == ["djust.V031"]


def _source_for(monkeypatch, handler_source: str) -> None:
    """``inspect.getsource`` of an exec'd handler: the source it was built from."""
    import inspect

    real = inspect.getsource

    def getsource(obj):
        if getattr(obj, "__code__", None) is not None and obj.__code__.co_filename == "<4178>":
            return handler_source
        return real(obj)

    monkeypatch.setattr(inspect, "getsource", getsource)


# --- dispatch policy and the template binding check --------------------------


def test_a_recovery_target_keeps_its_own_policy():
    """R1 is gone: a dj-auto-recover target is not downgraded to legacy."""
    from djust._parameter_metadata import parameter_contract_manifest

    with override_settings(LIVEVIEW_CONFIG={}):
        manifest = parameter_contract_manifest(StrictRecovery())
    root = next(o for o in manifest["owners"] if o["component_id"] is None and o["view_id"] is None)
    contract = root["handlers"]["restore"]
    assert contract["policy"] == "strict"
    assert [(p["name"], p["type"]) for p in contract["parameters"]] == [
        ("form_values", "dict[str, Any]"),
        ("data_attrs", "dict[str, str]"),
    ]


def _binding_findings(view_class: type) -> list:
    from djust._template_bindings import django_engine
    from djust.checks.bindings import _messages, scan_owner

    report = scan_owner(view_class, "view", django_engine(), {})
    return [(m.id, m.msg) for m in _messages([report])]


class StrictRecoveryWithPage(_Base):
    @event_handler(parameter_policy="strict")
    def restore(self, form_values: dict[str, Any], page: int) -> None:
        pass


def test_the_binding_check_knows_what_a_strict_recovery_binding_sends():
    """No T020 "requires 'form_values', which this binding never sends"."""
    assert _binding_findings(StrictRecovery) == []
    found = _binding_findings(StrictRecoveryWithPage)
    assert [i for i, _ in found] == ["djust.T020"]
    assert "requires 'page'" in found[0][1]


def test_dict_values_count_toward_the_event_resource_limits():
    """The same bounds as every argument: keys and values are visited."""
    contract = _contract(dict[str, str])
    contract.bind({"value": {"k%d" % i: "" for i in range(1024)}})
    with pytest.raises(ParameterError, match="oversized"):
        contract.bind({"value": {"k%d" % i: "" for i in range(1025)}})
    with pytest.raises(ParameterError, match="text resource"):
        contract.bind({"value": {"body": "x" * 65537}})
    with pytest.raises(ParameterError, match="text resource"):
        # Keys are text too.
        contract.bind({"value": {"k" * 65537: ""}})
