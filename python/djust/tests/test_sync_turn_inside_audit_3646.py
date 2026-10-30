"""#3646 review: every ``inside:`` disposition in ``HOOKS`` is checked by RUNNING the hook.

``HOOKS`` says where each documented override point runs. The structural check can only
prove that a caller exists and names the hook; it cannot prove which ``sync_turn_wrapper``
step is open when the hook body runs. The first version of the table asserted
``inside:render`` for hooks that a template tag calls, and a recording wrapper showed they
run with NO step open: the ``"render"`` step covers ``get_context_data()``, not the
template engine that runs afterwards.

Each scenario here drives a real view through the real HTTP entry (``View.as_view()``: GET
and POST event, the same ``run_sync_step`` helpers the other transports use) with a
recording hook installed on the class, and reads the step that is open when the hook body
runs (``djust.sync_turn._active_step``). ``test_every_inside_disposition_matches_what_ran``
then requires, for every ``inside:`` and ``unwrapped:template-time`` entry, that

* a scenario exists for it (a new ``inside:`` entry without one fails), and
* the steps that were actually open are a non-empty subset of the steps the entry claims
  (``template-time`` claims that no step is open).
"""

from __future__ import annotations

import contextlib
import functools
import json
import uuid
from typing import Any, Callable, Dict, List, Optional, Set

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.config import config as djust_config
from djust.decorators import event_handler
from djust.sync_turn import _active_step

pytestmark = [pytest.mark.django_db(transaction=True)]

MOD = __name__

#: ``(label, step open while the hook body ran)``
OBSERVED: List[tuple] = []


@contextlib.contextmanager
def _noop_scope():
    yield


def _wrapper(view, step, turn):
    return _noop_scope()


@pytest.fixture(autouse=True)
def recording(monkeypatch):
    previous = djust_config.get("sync_turn_wrapper")
    djust_config.set("sync_turn_wrapper", _wrapper)
    del OBSERVED[:]
    yield monkeypatch
    djust_config.set("sync_turn_wrapper", previous)
    del OBSERVED[:]


def watch(monkeypatch, cls: type, name: str, label: str) -> None:
    """Record the open step whenever ``cls.<name>`` runs (it is called, unchanged)."""
    owner = next(c for c in cls.__mro__ if name in c.__dict__)
    raw = owner.__dict__[name]
    if isinstance(raw, (staticmethod, classmethod)):
        inner = raw.__func__

        @functools.wraps(inner)
        def recorded_static(*args, **kwargs):
            OBSERVED.append((label, _active_step.get()))
            return inner(*args, **kwargs)

        monkeypatch.setattr(owner, name, type(raw)(recorded_static))
        return

    @functools.wraps(raw)
    def recorded(self, *args, **kwargs):
        OBSERVED.append((label, _active_step.get()))
        return raw(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, recorded)


def steps_of(label: str) -> Set[Optional[str]]:
    return {step for found, step in OBSERVED if found == label}


# --------------------------------------------------------------------------- #
# Drivers: the HTTP entry
# --------------------------------------------------------------------------- #


def _request(method: str, body: Optional[dict] = None, session=None, event: str = ""):
    factory = RequestFactory()
    if method == "GET":
        request = factory.get("/audit/")
    else:
        request = factory.post(
            "/audit/",
            data=json.dumps(body or {}),
            content_type="application/json",
            HTTP_X_DJUST_EVENT=event,
        )
    request.user = AnonymousUser()
    if session is None:
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
    else:
        request.session = session
    return request


def get(view_class: type):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        request = _request("GET")
        response = view_class.as_view()(request)
        return request, response


def post(view_class: type, event: str, params: Optional[dict] = None, *, after_get=True):
    """A POST event; with ``after_get`` the saved state of a prior GET is restored."""
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        session = None
        if after_get:
            first, _ = get(view_class)
            session = first.session
        request = _request("POST", {"event": event, "params": params or {}}, session, event)
        return view_class.as_view()(request)


SCENARIOS: Dict[str, Callable[[Any], None]] = {}


def scenario(*keys: str):
    def register(fn):
        for key in keys:
            SCENARIOS[key] = fn
        return fn

    return register


# --------------------------------------------------------------------------- #
# The data table: the handle_* overrides are called by @event_handler methods
# --------------------------------------------------------------------------- #

from djust.components.mixins.data_table import DataTableMixin  # noqa: E402


class TableView(DataTableMixin, LiveView):
    template = "<div dj-root><span>table</span></div>"
    table_columns = [{"key": "id", "label": "Id"}, {"key": "name", "label": "Name"}]
    table_server_mode = True
    table_import_preview = False

    def mount(self, request, **kwargs):
        self.init_table_state()
        self.table_rows = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
        self.refresh_table()

    def get_context_data(self, **kwargs):
        return {"rows": len(self.table_rows)}


_TABLE_EVENTS = {
    "DataTableMixin.handle_cell_edit": (
        "on_table_cell_edit",
        {"value": {"row_key": "1", "column": "name", "value": "x"}},
        "handle_cell_edit",
    ),
    "DataTableMixin.handle_row_save": ("on_table_row_save", {"value": "1"}, "handle_row_save"),
    "DataTableMixin.handle_bulk_action": (
        "on_table_bulk_action",
        {"value": "archive"},
        "handle_bulk_action",
    ),
    "DataTableMixin.handle_export": ("on_table_export", {"value": "csv"}, "handle_export"),
    "DataTableMixin.handle_row_drag": (
        "on_table_row_drag",
        {"value": {"old_index": 0, "new_index": 1}},
        "handle_row_drag",
    ),
    "DataTableMixin.handle_copy": ("on_table_copy", {"value": ["1"]}, "handle_copy"),
    "DataTableMixin.handle_import": (
        "on_table_import",
        {"value": {"format": "json", "data": '[{"id": "3", "name": "c"}]'}},
        "handle_import",
    ),
    "DataTableMixin.refresh_table_server": (
        "on_table_search",
        {"value": "q"},
        "refresh_table_server",
    ),
    "DataTableMixin.on_table_row_save": ("on_table_row_save", {"value": "1"}, "on_table_row_save"),
}


def _table_scenario(key):
    def run(mp):
        event, params, method = _TABLE_EVENTS[key]
        watch(mp, TableView, method, key)
        del OBSERVED[:]
        post(TableView, event, params, after_get=False)

    return run


for _key in _TABLE_EVENTS:
    SCENARIOS[_key] = _table_scenario(_key)


# --------------------------------------------------------------------------- #
# Draft mode, wizard
# --------------------------------------------------------------------------- #

from django import forms  # noqa: E402

from djust.drafts import DraftModeMixin  # noqa: E402
from djust.wizard import WizardMixin  # noqa: E402


class DraftView(DraftModeMixin, LiveView):
    template = "<div dj-root><span>draft</span></div>"

    def mount(self, request, **kwargs):
        pass

    @event_handler()
    def discard(self, **kwargs):
        self.clear_draft()


@scenario("DraftModeMixin.get_draft_key")
def _draft_key(mp):
    watch(mp, DraftView, "get_draft_key", "DraftModeMixin.get_draft_key")
    get(DraftView)
    post(DraftView, "discard", after_get=False)


class _WizardStep(forms.Form):
    note = forms.CharField(required=False)


class WizardView(WizardMixin, LiveView):
    wizard_steps = [{"name": "only", "title": "Only", "form_class": _WizardStep}]
    template = "<div dj-root><span>wizard</span></div>"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)

    def on_wizard_complete(self, all_data):
        pass


@scenario("WizardMixin.on_wizard_complete")
def _wizard(mp):
    watch(mp, WizardView, "on_wizard_complete", "WizardMixin.on_wizard_complete")
    post(WizardView, "submit_wizard", after_get=False)


# --------------------------------------------------------------------------- #
# Forms: mount / event hooks, and the template-time tags
# --------------------------------------------------------------------------- #

from djust.forms import FormMixin  # noqa: E402


class AuditForm(forms.Form):
    note = forms.CharField(required=False)


class FormPage(FormMixin, LiveView):
    form_class = AuditForm
    template = (
        "{% load live_tags %}<div dj-root>"
        "{% live_form view %}{% live_field view 'note' %}[{{ view|field_value:'note' }}]"
        "</div>"
    )

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


def _form_watch(mp, key, method):
    watch(mp, FormPage, method, key)


@scenario("FormMixin.get_form_class")
def _get_form_class(mp):
    _form_watch(mp, "FormMixin.get_form_class", "get_form_class")
    get(FormPage)
    post(FormPage, "validate_field", {"field_name": "note", "value": "x"}, after_get=False)


@scenario("FormMixin.get_form_kwargs")
def _get_form_kwargs(mp):
    _form_watch(mp, "FormMixin.get_form_kwargs", "get_form_kwargs")
    get(FormPage)
    post(FormPage, "validate_field", {"field_name": "note", "value": "x"}, after_get=False)


@scenario("FormMixin._create_form")
def _create_form(mp):
    _form_watch(mp, "FormMixin._create_form", "_create_form")
    get(FormPage)
    post(FormPage, "validate_field", {"field_name": "note", "value": "x"}, after_get=False)


@scenario("FormMixin._ensure_form_state")
def _ensure_form_state(mp):
    _form_watch(mp, "FormMixin._ensure_form_state", "_ensure_form_state")
    post(FormPage, "submit_form", {}, after_get=False)


@scenario("FormMixin.as_live")
def _as_live(mp):
    _form_watch(mp, "FormMixin.as_live", "as_live")
    get(FormPage)


@scenario("FormMixin.as_live_field")
def _as_live_field(mp):
    _form_watch(mp, "FormMixin.as_live_field", "as_live_field")
    get(FormPage)


@scenario("AdminTailwindAdapter.render_field")
def _render_field(mp):
    from djust.admin_ext.adapters import AdminTailwindAdapter

    watch(mp, AdminTailwindAdapter, "render_field", "AdminTailwindAdapter.render_field")
    with override_settings(LIVEVIEW_CONFIG={"css_framework": "admin_tailwind"}):
        djust_config.set("css_framework", "admin_tailwind")
        try:
            get(FormPage)
        finally:
            djust_config.set("css_framework", None)


@scenario("BaseAdapter._merge_widget_attrs")
def _merge_widget_attrs(mp):
    from djust.frameworks import BaseAdapter

    watch(mp, BaseAdapter, "_merge_widget_attrs", "BaseAdapter._merge_widget_attrs")
    get(FormPage)


# --------------------------------------------------------------------------- #
# A real WebSocket, for the hooks only a live mount reaches (presence, sticky reuse)
# --------------------------------------------------------------------------- #

import asyncio  # noqa: E402

from asgiref.sync import async_to_sync, sync_to_async  # noqa: E402


def _session_key() -> str:
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    return session.session_key


async def _ws_session(view_name: str, url: str, actions=(), settle: float = 0.4):
    """Mount ``view_name`` over a real socket, send each action, then disconnect."""
    from channels.testing import WebsocketCommunicator

    from djust.websocket import LiveViewConsumer

    key = await sync_to_async(_session_key)()

    class _ScopeSession:
        session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession()
    communicator.scope["user"] = AnonymousUser()
    connected, _ = await communicator.connect()
    assert connected
    await communicator.receive_json_from(timeout=3)

    async def until(*types):
        for _ in range(12):
            frame = await communicator.receive_json_from(timeout=10)
            if frame.get("type") in types:
                return frame
        raise AssertionError("no %s frame" % (types,))

    await communicator.send_json_to(
        {"type": "mount", "view": "%s.%s" % (MOD, view_name), "url": url}
    )
    await until("mount")
    for action in actions:
        await communicator.send_json_to(action)
        await until("patch", "html_update", "noop", "embedded_update", "mount", "error")
    await asyncio.sleep(settle)
    await communicator.disconnect()
    await asyncio.sleep(settle)


def ws_session(*args, **kwargs):
    with override_settings(LIVEVIEW_ALLOWED_MODULES=[MOD]):
        async_to_sync(_ws_session)(*args, **kwargs)


# --------------------------------------------------------------------------- #
# Presence (a live mount: the HTTP mount never tracks presence)
# --------------------------------------------------------------------------- #

from djust.presence import PresenceMixin  # noqa: E402


class PresencePage(PresenceMixin, LiveView):
    presence_key = "audit-room"
    template = "<div dj-root><span>presence</span></div>"

    def mount(self, request, **kwargs):
        self.track_presence(meta={})

    def get_presence_user_id(self):
        return "audit-user"

    def handle_presence_join(self, presence):
        pass

    def handle_presence_leave(self, presence):
        pass


@scenario("PresenceMixin.get_presence_user_id")
def _presence_user_id(mp):
    watch(mp, PresencePage, "get_presence_user_id", "PresenceMixin.get_presence_user_id")
    ws_session("PresencePage", "/presence/")


@scenario("PresenceMixin.handle_presence_join")
def _presence_join(mp):
    watch(mp, PresencePage, "handle_presence_join", "PresenceMixin.handle_presence_join")
    ws_session("PresencePage", "/presence/")


@scenario("PresenceMixin.handle_presence_leave")
def _presence_leave(mp):
    watch(mp, PresencePage, "handle_presence_leave", "PresenceMixin.handle_presence_leave")
    ws_session("PresencePage", "/presence/")


@scenario("PresenceMixin._on_presence_change")
def _presence_change(mp):
    watch(mp, PresencePage, "_on_presence_change", "PresenceMixin._on_presence_change")
    ws_session("PresencePage", "/presence/")


# --------------------------------------------------------------------------- #
# Components
# --------------------------------------------------------------------------- #

from djust.components.base import Component  # noqa: E402
from djust.components.base import LiveComponent as LegacyLiveComponent  # noqa: E402


class Counter(LegacyLiveComponent):
    template = "<span>{{ n }}</span>"

    def mount(self, **props):
        self.n = 0

    def get_context_data(self, **kwargs):
        return {"n": self.n}

    @event_handler()
    def poke(self, **kwargs):
        self.send_parent("poked", {"by": "audit"})


class Badge(Component):
    def _render_custom(self):
        return "<b>badge</b>"


class ComponentPage(LiveView):
    template = "<div dj-root>{{ counter }}{{ badge }}</div>"

    def mount(self, request, **kwargs):
        self.counter = Counter(component_id="counter")
        self.badge = Badge()

    def handle_component_event(self, component_id, event, data):
        pass

    @event_handler()
    def refresh(self, **kwargs):
        self.update_component("counter", n=3)


@scenario("LiveComponent.mount")
def _component_mount(mp):
    watch(mp, Counter, "mount", "LiveComponent.mount")
    get(ComponentPage)


@scenario("LiveComponent.update")
def _component_update(mp):
    watch(mp, Counter, "update", "LiveComponent.update")
    ws_session(
        "ComponentPage",
        "/components/",
        actions=[{"type": "event", "event": "refresh", "params": {}}],
    )


@scenario("ComponentMixin.handle_component_event")
def _handle_component_event(mp):
    watch(mp, ComponentPage, "handle_component_event", "ComponentMixin.handle_component_event")
    ws_session(
        "ComponentPage",
        "/components/",
        actions=[{"type": "event", "event": "poke", "params": {"component_id": "counter"}}],
    )


@scenario("Component.get_context_data")
def _component_context(mp):
    watch(mp, Counter, "get_context_data", "Component.get_context_data")
    get(ComponentPage)


@scenario("Component._render_custom")
def _component_render_custom(mp):
    watch(mp, Badge, "_render_custom", "Component._render_custom")
    get(ComponentPage)


from djust.components.descriptors.base import LiveComponent as DescriptorComponent  # noqa: E402
from djust.components.descriptors.base import TypedState  # noqa: E402


class AuditTabs(DescriptorComponent):
    class State(TypedState):
        active: str = "overview"

    template = "<nav>{{ active }}</nav>"

    class Meta:
        event = "audit_set"

    def _handle_event(self, state, value: str = "", **kwargs):
        state.active = value


class TabsPage(LiveView):
    template = "<div dj-root>{{ nav }}</div>"
    nav = AuditTabs()

    def mount(self, request, **kwargs):
        pass


@scenario("LiveComponent._handle_event")
def _component_handle_event(mp):
    watch(mp, AuditTabs, "_handle_event", "LiveComponent._handle_event")
    post(TabsPage, "audit_set", {"value": "billing"})


# --------------------------------------------------------------------------- #
# Context, sticky
# --------------------------------------------------------------------------- #


class RenderOnlyPage(LiveView):
    template = "<div dj-root><span>{{ note }}</span></div>"

    def mount(self, request, **kwargs):
        pass

    def get_render_only_context(self):
        return {"note": "render-only"}

    @event_handler()
    def again(self, **kwargs):
        pass


@scenario("ContextMixin.get_render_only_context")
def _render_only_context(mp):
    watch(mp, RenderOnlyPage, "get_render_only_context", "ContextMixin.get_render_only_context")
    get(RenderOnlyPage)
    post(RenderOnlyPage, "again")


class StickyInner(LiveView):
    sticky = True
    sticky_id = "audit-dock"
    template = "<div><span>dock</span></div>"

    def mount(self, request, **kwargs):
        pass

    def _on_sticky_update(self, changed):
        pass


class StickyHost(LiveView):
    template = (
        "{% load live_tags %}<div dj-root>"
        "{% live_render '" + MOD + ".StickyInner' sticky=True label=label %}</div>"
    )

    def mount(self, request, **kwargs):
        self.label = "a"

    def get_context_data(self, **kwargs):
        return {"view": self, "label": self.label}

    @event_handler()
    def relabel(self, **kwargs):
        self.label = "b"


@scenario("StickyChildRegistry._on_sticky_update")
def _sticky_update(mp):
    watch(mp, StickyInner, "_on_sticky_update", "StickyChildRegistry._on_sticky_update")
    ws_session(
        "StickyHost",
        "/sticky-host/",
        actions=[{"type": "event", "event": "relabel", "params": {}}],
    )


# --------------------------------------------------------------------------- #
# The admin site (a real change form and index page through the Django test client)
# --------------------------------------------------------------------------- #

from django.contrib.auth.models import Group  # noqa: E402
from django.test import Client  # noqa: E402
from django.urls import path  # noqa: E402

from djust.admin_ext import DjustAdminSite, DjustModelAdmin  # noqa: E402
from djust.admin_ext.plugins import AdminPlugin, AdminWidget  # noqa: E402


class AuditWidget(AdminWidget):
    widget_id = "audit-widget"
    label = "Audit"
    template_name = "djust_admin/_change_list_widgets.html"

    def get_context(self, request):
        return {}


class AuditPlugin(AdminPlugin):
    name = "audit-plugin"
    verbose_name = "Audit"

    def get_widgets(self):
        return [AuditWidget()]


audit_site = DjustAdminSite(name="sync_turn_audit_admin")
audit_site.register(Group, DjustModelAdmin)
audit_site.register_plugin(AuditPlugin)

urlpatterns = [path("audit-admin/", audit_site.urls)]


def admin_get(url: str):
    from django.contrib.auth import get_user_model

    User = get_user_model()
    token = uuid.uuid4().hex[:8]
    user = User.objects.create_superuser("audit-root-" + token, password="pw")
    group = Group.objects.create(name="audit-group-" + token)
    client = Client()
    client.force_login(user)
    with override_settings(
        ROOT_URLCONF=MOD, ALLOWED_HOSTS=["testserver"], LIVEVIEW_ALLOWED_MODULES=["djust", MOD]
    ):
        response = client.get(url.format(pk=group.pk))
    return response


@scenario("AdminWidget.get_context")
def _admin_widget(mp):
    watch(mp, AuditWidget, "get_context", "AdminWidget.get_context")
    admin_get("/audit-admin/")


@scenario("AdminFormMixin._create_form")
def _admin_create_form(mp):
    from djust.admin_ext.forms import AdminFormMixin

    watch(mp, AdminFormMixin, "_create_form", "AdminFormMixin._create_form")
    admin_get("/audit-admin/auth/group/{pk}/change/")


@scenario("AdminFormMixin.get_field_value")
def _admin_field_value(mp):
    from djust.admin_ext.forms import AdminFormMixin

    watch(mp, AdminFormMixin, "get_field_value", "AdminFormMixin.get_field_value")
    admin_get("/audit-admin/auth/group/{pk}/change/")
    # The ``field_value`` template filter calls the same override at template time.
    watch(mp, FormPage, "get_field_value", "AdminFormMixin.get_field_value")
    get(FormPage)


# --------------------------------------------------------------------------- #
# get_template (template-time), and the teardown hooks (covered by the lifecycle tests)
# --------------------------------------------------------------------------- #


@scenario("TemplateMixin.get_template")
def _get_template(mp):
    from djust.mixins.template import TemplateMixin

    watch(mp, TemplateMixin, "get_template", "TemplateMixin.get_template")
    get(RenderOnlyPage)


@scenario(
    "StickyChildRegistry._on_sticky_unmount",
    "(app)._cleanup_on_unregister",
    "UploadMixin._cleanup_uploads",
)
def _teardown_is_covered_by_the_lifecycle_tests(mp):
    raise NotImplementedError


#: ``lifecycle`` off the event loop, ``none`` on it: proven by the tests named here, which
#: drive SSE navigation / ``response.close()`` / a WebSocket sticky discard (a worker
#: thread: ``lifecycle``) and a WebSocket disconnect / the SSE stream's own close (the
#: loop: no step).
TEARDOWN_TESTS = (
    "test_an_sse_navigation_runs_the_teardown_hooks_inside_a_lifecycle_step",
    "test_an_sse_response_close_runs_the_teardown_hooks_inside_a_lifecycle_step",
    "test_a_sticky_child_its_auth_now_denies_is_discarded_inside_a_lifecycle_step",
    "test_a_websocket_disconnect_runs_the_teardown_hooks_on_the_loop_without_a_step",
    "test_an_sse_stream_close_runs_the_teardown_hooks_on_the_loop_without_a_step",
)
TEARDOWN_KEYS = {
    "StickyChildRegistry._on_sticky_unmount",
    "(app)._cleanup_on_unregister",
    "UploadMixin._cleanup_uploads",
}


# --------------------------------------------------------------------------- #
# The audit
# --------------------------------------------------------------------------- #

from djust.tests.test_sync_turn_structural_pin_3646 import HOOKS  # noqa: E402


def claimed(disposition: str) -> Set[str]:
    kind, _, rest = disposition.partition(":")
    if kind == "inside":
        return set(rest.partition("@")[0].split("|"))
    if disposition.startswith("unwrapped:template-time"):
        return {"none"}
    return set()


def as_names(steps: Set[Optional[str]]) -> Set[str]:
    return {"none" if step is None else step for step in steps}


def verdict(disposition: str, observed: Set[str]) -> Optional[str]:
    """None when the claim matches what ran, else why not."""
    if not observed:
        return "the hook never ran, so nothing was observed"
    if disposition.startswith("stepped:"):
        step = disposition.partition(":")[2]
        return None if step in observed else "claimed %s, observed %s" % (step, sorted(observed))
    want = claimed(disposition)
    if want != observed:
        return "claimed %s, observed %s" % (sorted(want), sorted(observed))
    return None


_AUDITED = sorted(
    key
    for key, disposition in HOOKS.items()
    if disposition.startswith(("inside:", "unwrapped:template-time"))
    or (disposition.startswith("stepped:") and key in SCENARIOS)
)


@pytest.mark.parametrize("key", _AUDITED)
def test_every_inside_disposition_matches_what_ran(recording, key):
    disposition = HOOKS[key]
    if key in TEARDOWN_KEYS:
        text = open(__file__.replace("inside_audit", "lifecycle_gaps")).read()
        for name in TEARDOWN_TESTS:
            assert "def %s" % name in text, name
        assert claimed(disposition) == {"lifecycle", "none"}, disposition
        return
    assert key in SCENARIOS, "%s has an inside:/template-time disposition and no scenario" % key
    del OBSERVED[:]
    SCENARIOS[key](recording)
    problem = verdict(disposition, as_names(steps_of(key)))
    assert problem is None, "%s -> %s: %s" % (key, disposition[:70], problem)


def test_the_audit_sees_a_step_when_one_is_open_and_none_when_it_is_not(recording):
    from djust.sync_turn import run_sync_step

    class Probe:
        def hook(self):
            OBSERVED.append(("probe", _active_step.get()))

    watch(recording, Probe, "hook", "probe")
    probe = Probe()
    probe.hook()
    run_sync_step(probe, "hook", probe.hook)
    assert as_names(steps_of("probe")) == {"none", "hook"}


@pytest.mark.parametrize(
    "disposition, observed",
    [
        ("inside:render@x.py::f", {"none"}),  # claims a step, none open (the original error)
        ("inside:render@x.py::f", {"render", "event"}),  # an observed step is not claimed
        ("inside:render|event@x.py::f", {"render"}),  # a claimed step never observed
        ("unwrapped:template-time:" + "x" * 30, {"render"}),
        ("stepped:event", {"none"}),
        ("inside:render@x.py::f", set()),  # never ran
    ],
)
def test_a_claim_that_does_not_match_what_ran_is_rejected(disposition, observed):
    assert verdict(disposition, observed)


def test_a_new_inside_disposition_without_a_scenario_fails():
    uncovered = [
        key
        for key, disposition in HOOKS.items()
        if disposition.startswith(("inside:", "unwrapped:template-time")) and key not in SCENARIOS
    ]
    assert uncovered == []


# --------------------------------------------------------------------------- #
# What the template-time category means for a user: a row read there has no scope
# --------------------------------------------------------------------------- #


class ChoiceForm(forms.Form):
    group = forms.ModelChoiceField(queryset=Group.objects.all(), required=False)


class ChoicePage(FormMixin, LiveView):
    form_class = ChoiceForm
    template = "{% load live_tags %}<div dj-root>{% live_field view 'group' %}</div>"

    def mount(self, request, **kwargs):
        super().mount(request, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["view"] = self
        return context


def test_a_row_read_by_a_template_time_form_field_runs_with_no_step_open(recording):
    """A ``ModelChoiceField`` queryset evaluated by ``{% live_field %}`` is a real row
    read, and it runs with no ``sync_turn_wrapper`` step open (turn-middleware.md,
    "Not wrapped: template-time form and tag hooks")."""
    from django.db.models.signals import post_init

    row = Group.objects.create(name="audit-choice-" + uuid.uuid4().hex[:8])

    def loaded(sender, instance, **kwargs):
        if instance.pk == row.pk:
            OBSERVED.append(("group_loaded", _active_step.get()))

    post_init.connect(loaded, sender=Group, weak=False)
    try:
        _request_, response = get(ChoicePage)
    finally:
        post_init.disconnect(loaded, sender=Group)

    assert row.name.encode() in response.content
    # The form is also built in mount() (a step); the read the tag does is the one with none.
    assert "none" in as_names(steps_of("group_loaded")), steps_of("group_loaded")
