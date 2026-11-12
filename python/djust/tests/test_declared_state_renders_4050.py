"""#4050: a declared ``state()`` field is template context by default.

Under explicit exposure (the default) a view no longer hands each declared
field over in ``get_context_data()``. Nothing else is granted: the client
state payload still needs ``client=True``, a reconnect still needs
``persist=``, and an undeclared attribute still renders empty.

Every render path goes through the explicit base context
(``ContextMixin._get_explicit_context_data``), so the cases below drive the
real paths: the HTTP GET, the runtime mount/event, a real
``WebsocketCommunicator`` socket, an embedded child and a component.
"""

import json
import logging
import re
import warnings
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from djust import LiveView, event_handler
from djust._exposure import ExposureConfigurationError
from djust.components.descriptors.base import LiveComponent
from djust.decorators import state
from djust.mixins.context import rendered_state_names
from djust.presence import PresenceMixin
from djust.tests.test_exposure_runtime import make_request
from djust.websocket import LiveViewConsumer

from ._ws_frames import drain_extra, has_type, receive_until

SENTINEL = "RENDERED_NOT_CLIENT_SENTINEL"


# ------------------------------------------------------------------ #
# Views
# ------------------------------------------------------------------ #


class Counter(LiveView):
    """The README counter: no get_context_data() at all."""

    exposure_policy = "explicit"

    template = "<div dj-root><p>[{{ count }}]</p><i>{{ undeclared }}</i></div>"
    count = state(0)

    def mount(self, request, **kwargs):
        self.undeclared = "UNDECLARED_SENTINEL"

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class Overrides(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ count }}][{{ label }}][{{ total }}]</div>"
    count = state(1)
    label = state("declared")
    total = state(3)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(count="from-kwargs", **kwargs)
        context["label"] = "from-get-context-data"
        return context


class NoSuper(LiveView):
    """A get_context_data() that does not build on super() supplies no field."""

    exposure_policy = "explicit"

    template = "<div dj-root>[{{ count }}][{{ extra }}]</div>"
    count = state(4)

    def get_context_data(self, **kwargs):
        return {"extra": "x"}


class OptOut(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ shown }}][{{ hidden }}]</div>"
    shown = state("SHOWN")
    hidden = state("HIDDEN_SENTINEL", render=False)


class Grants(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ secret_ish }}][{{ visible }}][{{ saved }}]</div>"
    secret_ish = state(SENTINEL)
    visible = state("VISIBLE", client=True)
    saved = state(0, persist="server")


class Parent(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ a }}][{{ b }}]</div>"
    a = state("parent-a")
    b = state("parent-b")


class Child(Parent):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ a }}][{{ b }}][{{ c }}]</div>"
    b = state("child-b", render=False)  # the nearest declaration wins
    c = state("child-c")


class Reserved(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ user }}]</div>"
    request = state("REQUEST_FIELD")
    view = state("VIEW_FIELD")
    kwargs = state("KWARGS_FIELD")
    template_name = state("TEMPLATE_NAME_FIELD")
    streams = state("STREAMS_FIELD")
    user = state("DECLARED_USER")


class DeclaredPresence(PresenceMixin, LiveView):
    exposure_policy = "explicit"
    presence_key = "declared-state-renders-4050"
    template = "<div dj-root>[{{ online_count }}][{{ n }}]</div>"
    online_count = state(7)
    n = state(1)


class Badge(LiveComponent):
    template = '<i class="badge">[{{ count }}][{{ own }}]</i>'

    def mount(self, **kwargs: Any) -> None:
        self.own = "component-own"

    def get_context_data(self) -> dict:
        return {"own": self.own}


class WithComponent(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ count }}]{{ badge }}</div>"
    count = state("VIEW_COUNT")
    badge = Badge()


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class LegacyCounter(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>[{{ count }}][{{ plain }}]</div>"
        count = state(5)

        def mount(self, request, **kwargs):
            self.plain = "legacy-plain"


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


def _get(view_class):
    response = view_class().get(make_request())
    assert response.status_code == 200
    return re.sub(r' dj-id="[^"]*"', "", response.content.decode())


def _context(view_class, **attrs):
    view = view_class()
    view.request = make_request()
    for name, value in attrs.items():
        setattr(view, name, value)
    return dict(view.get_context_data())


# ------------------------------------------------------------------ #
# The plain case and get_context_data() precedence
# ------------------------------------------------------------------ #


@pytest.mark.django_db
def test_a_declared_field_renders_with_no_get_context_data():
    html = _get(Counter)
    assert "<p>[0]</p>" in html, html
    # The undeclared attribute is still withheld: explicit exposure's point.
    assert "UNDECLARED_SENTINEL" not in html
    assert "<i></i>" in html


@pytest.mark.django_db
def test_an_undeclared_attribute_is_still_withheld():
    """Holds with or without #4050: the gate-off sibling of the plain case."""
    view = Counter()
    view.request = make_request()
    view.mount(view.request)
    assert "undeclared" not in view.get_context_data()
    assert "UNDECLARED_SENTINEL" not in _get(Counter)


@pytest.mark.django_db
def test_get_context_data_overrides_what_a_declared_field_renders_as():
    html = _get(Overrides)
    # kwargs to super() and a write after super() both win; the rest renders.
    assert "[from-kwargs][from-get-context-data][3]" in html, html


@pytest.mark.django_db
def test_a_get_context_data_without_super_supplies_no_declared_field():
    assert "[][x]" in _get(NoSuper)


@pytest.mark.django_db
def test_render_false_keeps_a_declared_field_out_of_the_template():
    html = _get(OptOut)
    assert "[SHOWN][]" in html, html
    assert "HIDDEN_SENTINEL" not in html
    assert rendered_state_names(OptOut) == ("shown",)


def test_render_false_is_refused_on_a_legacy_view():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)

        class LegacyOptOut(LiveView):
            exposure_policy = "legacy"
            hidden = state("x", render=False)

    with pytest.raises(ExposureConfigurationError, match="render=False"):
        LegacyOptOut()


def test_render_must_be_a_boolean():
    with pytest.raises(TypeError, match="render"):
        state(0, render="no")


def test_render_is_not_part_of_the_exposure_schema():
    """Opting out narrows; it is not a grant and does not change stored envelopes."""
    from djust._exposure import ExposureContract

    class A(LiveView):
        exposure_policy = "explicit"
        saved = state(0, persist="server")

    class B(LiveView):
        exposure_policy = "explicit"
        saved = state(0, persist="server", render=False)

    B.__qualname__ = A.__qualname__
    assert ExposureContract.from_view_class(A).schema == ExposureContract.from_view_class(B).schema


@pytest.mark.django_db
def test_a_legacy_view_is_unchanged():
    html = _get(LegacyCounter)
    assert "[5][legacy-plain]" in html, html


# ------------------------------------------------------------------ #
# Collisions
# ------------------------------------------------------------------ #


@pytest.mark.django_db
def test_parent_and_child_declarations_resolve_by_the_nearest_declaration():
    assert "[parent-a][parent-b]" in _get(Parent)
    assert "[parent-a][][child-c]" in _get(Child)
    assert rendered_state_names(Child) == ("a", "c")


@pytest.mark.django_db
def test_reserved_names_never_render_through_a_declaration():
    names = rendered_state_names(Reserved)
    # request, view, kwargs and template_name are framework names the state
    # contract refuses; streams belongs to the streams provider.
    assert names == ("user",)
    context = _context(Reserved)
    assert set(context) == {"user"}, context
    for value in ("REQUEST_FIELD", "VIEW_FIELD", "KWARGS_FIELD", "TEMPLATE_NAME_FIELD"):
        assert value not in json.dumps(context, default=str)


@pytest.mark.django_db
def test_a_declared_field_wins_over_a_context_processor_as_view_context_does(rf):
    view = Reserved()
    view.request = make_request()
    view._get_context_processors = lambda: []
    view._get_resolved_processors = lambda paths: [lambda request: {"user": "PROCESSOR_USER"}]
    rendered = view._apply_context_processors(view.get_context_data(), rf.get("/"))
    assert rendered["user"] == "DECLARED_USER"


def test_a_provider_owned_name_is_rendered_by_its_provider_not_the_declaration():
    # online_count belongs to PresenceMixin's provider, which reads the
    # declared value itself (test_exposure_framework_mixins pins the render).
    assert rendered_state_names(DeclaredPresence) == ("n",)


def test_the_names_are_cached_on_the_class_not_walked_per_render():
    rendered_state_names(Counter)
    assert Counter.__dict__["_djust_rendered_state_cache"] == ("count",)


# ------------------------------------------------------------------ #
# The browser still gets only client=True; the render is HTML
# ------------------------------------------------------------------ #


@pytest.mark.django_db
def test_a_rendered_field_is_absent_from_the_client_state_payload():
    view = Grants()
    view.request = make_request()
    assert set(view.get_context_data()) == {"secret_ish", "visible", "saved"}
    assert view.get_state() == {"visible": "VISIBLE"}


SOCKET_SETTINGS = dict(DEBUG=False, DJUST_TENANTS=None, DJUST_CONFIG={})


async def _connect(request, view_class):
    socket = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    socket.scope["session"] = SessionStore(request.session.session_key)
    socket.scope["user"] = AnonymousUser()
    assert (await socket.connect())[0]
    await socket.receive_json_from()
    await socket.send_json_to(
        {"type": "mount", "view": __name__ + "." + view_class.__name__, "url": request.path}
    )
    frame = await socket.receive_json_from()
    assert frame["type"] == "mount", frame
    return socket, frame


def _without_markup(frame):
    """The frame minus the HTML it carries: what the client gets as data."""
    return {key: value for key, value in frame.items() if key not in ("html", "patches")}


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_websocket_mount_and_event_render_a_declared_field():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SOCKET_SETTINGS):
        request = await sync_to_async(make_request)()
        socket, mount = await _connect(request, SocketCounter)
        try:
            assert "[0]" in mount["html"], mount
            assert SENTINEL not in json.dumps(_without_markup(mount))
            await socket.send_json_to({"type": "event", "event": "increment", "params": {}})
            frames = await receive_until(socket, has_type("patch", "html_update"))
            frames += await drain_extra(socket)
            updates = [f for f in frames if f.get("type") in ("patch", "html_update")]
            assert updates, frames
            # The DOM is patched with the new value of the declared field.
            assert "[1]" in json.dumps(updates), updates
            # Rendering is not exposure: no frame carries the field as data.
            for frame in frames:
                assert SENTINEL not in json.dumps(_without_markup(frame)), frame
        finally:
            await socket.disconnect()


class SocketCounter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>[{{ count }}]</p><b>{{ secret_ish }}</b></div>"
    count = state(0)
    secret_ish = state(SENTINEL)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


# ------------------------------------------------------------------ #
# Owners: children and components get their own fields only
# ------------------------------------------------------------------ #


class EmbeddedChild(LiveView):
    exposure_policy = "explicit"
    template = "<section>child[{{ count }}][{{ parent_only }}]</section>"
    count = state("CHILD_COUNT")


class EmbeddingParent(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>parent[{{ count }}][{{ parent_only }}]</div>"
    count = state("PARENT_COUNT")
    parent_only = state("PARENT_ONLY")


@pytest.mark.django_db
def test_an_embedded_child_renders_its_own_declared_fields_only(rf, settings):
    from django.template import Context, Template

    from djust.tests.test_exposure_child_mount import make_request as child_request

    settings.DJUST_LIVE_RENDER_ALLOWED_MODULES = [__name__]
    parent = EmbeddingParent()
    request = child_request(rf, SessionStore())
    parent.request = request
    html = Template(
        "{% load live_tags %}{% live_render "
        '"djust.tests.test_declared_state_renders_4050.EmbeddedChild" %}'
    ).render(Context({"view": parent, "request": request}))
    assert "child[CHILD_COUNT][]" in html, html
    assert dict(parent.get_context_data()) == {
        "count": "PARENT_COUNT",
        "parent_only": "PARENT_ONLY",
    }


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_component_does_not_receive_the_views_declared_fields():
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".WithComponent", "url": request.path}
        )
    html = [f for f in transport.sent if f.get("type") == "mount"][-1]["html"]
    html = re.sub(r' dj-id="[^"]*"', "", html)
    assert "[VIEW_COUNT]" in html, html
    # The component renders what its own get_context_data() returns.
    assert "[][component-own]" in html, html


# ------------------------------------------------------------------ #
# Diagnostics: the DEBUG warning and djust.T027
# ------------------------------------------------------------------ #


@pytest.fixture
def fresh_reports():
    from djust import _exposure_unsupplied

    saved = dict(_exposure_unsupplied._REPORTED)
    _exposure_unsupplied._REPORTED.clear()
    yield
    _exposure_unsupplied._REPORTED.clear()
    _exposure_unsupplied._REPORTED.update(saved)


def _warned(caplog, view_class):
    return sorted(
        record.getMessage().split("{{ ")[1].split(" }}")[0]
        for record in caplog.records
        if record.name == "djust.exposure" and view_class.__qualname__ in record.getMessage()
    )


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_warning_treats_a_declared_field_as_supplied(caplog, fresh_reports):
    with caplog.at_level(logging.WARNING, logger="djust.exposure"):
        _get(Counter)
        _get(NoSuper)
        _get(OptOut)
    # The undeclared attribute still warns; the declared field does not.
    assert _warned(caplog, Counter) == ["undeclared"]
    # A declared field that did not arrive is still named, with the reason.
    no_super = [r.getMessage() for r in caplog.records if "NoSuper" in r.getMessage()]
    assert _warned(caplog, NoSuper) == ["count"] and "super()" in no_super[0]
    opt_out = [r.getMessage() for r in caplog.records if "OptOut" in r.getMessage()]
    assert _warned(caplog, OptOut) == ["hidden"] and "render=False" in opt_out[0]


def _t027(view_class):
    from djust.checks.templates import check_unsupplied_template_names

    label = "%s.%s" % (view_class.__module__, view_class.__qualname__)
    return sorted(
        message.msg.split("reads '")[1].split("'")[0]
        for message in check_unsupplied_template_names(None)
        if message.msg.startswith(label + " ")
    )


class StaticSplat(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>[{{ count }}][{{ extra }}]</div>"
    count = state(0)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "extra": 1}


def test_t027_treats_a_declared_field_as_supplied():
    assert _t027(Counter) == ["undeclared"]
    assert _t027(StaticSplat) == []
    assert _t027(Overrides) == []
    # Statically provable gaps are still reported.
    assert _t027(NoSuper) == ["count"]
    assert _t027(OptOut) == ["hidden"]


# ------------------------------------------------------------------ #
# Cost: change detection still forwards only what changed
# ------------------------------------------------------------------ #


class Rows(LiveView):
    exposure_policy = "explicit"
    template = (
        "<div dj-root><p>{{ count }}</p>{% for row in items %}<i>{{ row.n }}</i>{% endfor %}</div>"
    )
    count = state(0)
    items = state(default_factory=lambda: [{"n": i} for i in range(50)])

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class _RecordingRustView:
    def __init__(self, inner):
        self._inner = inner
        self.updates = []

    def update_state(self, context):
        self.updates.append(sorted(context))
        return self._inner.update_state(context)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_event_forwards_only_the_changed_declared_field_to_rust():
    from djust.runtime import ViewRuntime
    from djust.tests.test_runtime_state_save_tt_1894 import MockTransport

    request = await sync_to_async(make_request)()
    transport = MockTransport()
    transport.build_request = lambda: request

    async def fresh_event_request(view):
        return await sync_to_async(make_request)(request.session.session_key)

    transport.explicit_event_request = fresh_event_request
    runtime = ViewRuntime(transport)
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        await runtime.dispatch_mount(
            {"type": "mount", "view": __name__ + ".Rows", "url": request.path}
        )
        view = runtime.view_instance
        recorder = _RecordingRustView(view._rust_view)
        view._rust_view = recorder
        await runtime.dispatch_event({"type": "event", "event": "increment", "params": {}})
    assert not transport.errors, transport.errors
    assert recorder.updates, "the event did not sync state to the renderer"
    sent = {key for update in recorder.updates for key in update}
    assert "count" in sent and "items" not in sent, recorder.updates


# ------------------------------------------------------------------ #
# A declared field the template does not print goes nowhere
# ------------------------------------------------------------------ #

UNPRINTED = "UNPRINTED_DECLARED_SENTINEL"


class Unprinted(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root><p>[{{ count }}]</p></div>"
    count = state(0, persist="server")
    note = state(UNPRINTED)

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self.note = UNPRINTED + "-changed"


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_unprinted_declared_field_reaches_no_frame_session_or_state():
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__], **SOCKET_SETTINGS):
        request = await sync_to_async(make_request)()
        socket, mount = await _connect(request, Unprinted)
        try:
            frames = [mount]
            await socket.send_json_to({"type": "event", "event": "increment", "params": {}})
            frames += await receive_until(socket, has_type("patch", "html_update"))
            frames += await drain_extra(socket)
        finally:
            await socket.disconnect()
    assert any("[1]" in json.dumps(f) for f in frames), frames
    for frame in frames:
        # Every field of every frame, the markup included.
        assert UNPRINTED not in json.dumps(frame), frame
    stored = await sync_to_async(lambda: SessionStore(request.session.session_key).load())()
    assert UNPRINTED not in json.dumps(stored, default=str)


@pytest.mark.django_db
def test_an_unprinted_declared_field_is_render_context_but_not_client_state():
    view = Unprinted()
    view.request = make_request()
    assert view.get_context_data()["note"] == UNPRINTED
    assert view.get_state() == {}


# ------------------------------------------------------------------ #
# user / perms: djust.V026 and the DEBUG shadow warning
# ------------------------------------------------------------------ #


class DeclaresUser(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ user }}</div>"
    user = state("SELECTED_USER")


class DeclaresMessages(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>{{ messages }}</div>"
    messages = state(default_factory=list)


class DeclaresUserHidden(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root></div>"
    user = state("SELECTED_USER", render=False)
    perms = state(None, render=False)


with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)

    class LegacyDeclaresUser(LiveView):
        exposure_policy = "legacy"
        template = "<div dj-root>{{ user }}</div>"
        user = state("SELECTED_USER")


class DeclaresPerms(DeclaresUser):
    exposure_policy = "explicit"
    perms = state(None)


def _v026(view_class):
    from djust.checks.components import check_declared_authorization_names

    label = "%s.%s " % (view_class.__module__, view_class.__qualname__)
    return [m for m in check_declared_authorization_names(None) if m.msg.startswith(label)]


def test_v026_flags_a_declared_user_or_perms_field():
    (message,) = _v026(DeclaresUser)
    assert message.id == "djust.V026" and "'user'" in message.msg
    assert "selected_user" in message.hint and "render=False" in message.fix_hint
    # The inherited user is reported once, on the class that declares it.
    assert [m.msg.split("'")[1] for m in _v026(DeclaresPerms)] == ["perms"]


def test_v026_is_quiet_for_messages_render_false_and_legacy_views(settings):
    assert _v026(DeclaresMessages) == []
    assert _v026(DeclaresUserHidden) == []
    assert _v026(LegacyDeclaresUser) == []
    settings.DJUST_CONFIG = {"suppress_checks": ["V026"]}
    assert _v026(DeclaresUser) == []


def _apply_processors(view_class, supplied):
    view = view_class()
    view.request = make_request()
    view._get_context_processors = lambda: []
    view._get_resolved_processors = lambda paths: [lambda request: dict(supplied)]
    return view._apply_context_processors(view.get_context_data(), view.request)


@pytest.fixture
def fresh_shadow_reports():
    from djust import _exposure_unsupplied

    saved = dict(_exposure_unsupplied._SHADOW_REPORTED)
    _exposure_unsupplied._SHADOW_REPORTED.clear()
    yield
    _exposure_unsupplied._SHADOW_REPORTED.clear()
    _exposure_unsupplied._SHADOW_REPORTED.update(saved)


def _shadow_records(caplog):
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "djust.exposure" and "replaces the" in r.getMessage()
    ]


@pytest.mark.django_db
@override_settings(DEBUG=True)
def test_debug_warns_once_when_a_declared_field_shadows_user(caplog, fresh_shadow_reports):
    supplied = {"user": "REQUEST_USER_SENTINEL", "messages": ["m"]}
    with caplog.at_level(logging.WARNING, logger="djust.exposure"):
        assert _apply_processors(DeclaresUser, supplied)["user"] == "SELECTED_USER"
        _apply_processors(DeclaresUser, supplied)
        _apply_processors(DeclaresMessages, supplied)
    (message,) = _shadow_records(caplog)
    assert "DeclaresUser" in message and "user" in message and "V026" in message
    assert "SENTINEL" not in message and "SELECTED_USER" not in message


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_no_shadow_warning_without_debug(caplog, fresh_shadow_reports):
    with caplog.at_level(logging.WARNING, logger="djust.exposure"):
        _apply_processors(DeclaresUser, {"user": "REQUEST_USER"})
    assert _shadow_records(caplog) == []


# ------------------------------------------------------------------ #
# T027 reads djust's own get_context_data() overrides
# ------------------------------------------------------------------ #


def test_t027_base_reach_reads_a_shipped_views_override():
    from djust.admin_ext.progress import BulkActionProgressWidget
    from djust.checks.templates import _get_context_data_reaches_base

    class Sub(BulkActionProgressWidget):
        exposure_policy = "explicit"
        extra = state(1)

    # The shipped widget builds a new dict, so no declared field arrives.
    assert _get_context_data_reaches_base(Sub) is False
    assert _get_context_data_reaches_base(Counter) is True
    assert _get_context_data_reaches_base(StaticSplat) is True
    assert _get_context_data_reaches_base(DeclaredPresence) is True
