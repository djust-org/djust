"""The harness's views (#4000): one per kind of state the table reports on.

Each view renders a ``<pre id="probe">`` holding a JSON snapshot of what it
holds, and nothing else dynamic, so a mount's HTML, an HTTP answer's HTML and a
WebSocket answer's full HTML or ``SetText`` patch all say what survived.
``pid`` names the process that rendered.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

from django import forms
from django.contrib.auth import get_user_model, login, logout
from django.http import HttpResponse, JsonResponse

from djust import LiveView
from djust.components.base import LiveComponent
from djust.components.interactive import DropdownMenu
from djust.components.mixins.base import TypedState
from djust.decorators import event_handler, state
from djust.forms import FormMixin, persisted_form_input
from djust.presence import PresenceMixin
from djust.wizard import WizardMixin

PROBE = '<pre id="probe">{{ probe }}</pre>'


def _render_probe(context: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    values = dict(values)
    values["pid"] = os.getpid()
    context["probe"] = json.dumps(values, sort_keys=True)
    return context


class _Probed:
    """``get_context_data`` renders ``snapshot()`` into the probe."""

    def snapshot(self) -> Dict[str, Any]:  # pragma: no cover - overridden
        return {}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return _render_probe(context, self.snapshot())


class CounterView(_Probed, LiveView):
    """One field of each kind: server-persisted, client-persisted, plain
    ``state()``, an ordinary attribute, a private one, and one opted out of
    saving (#4107)."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"

    count = state(0, persist="server")
    client_count = state(0, persist="client", client=True)
    plain_state = state(0)
    not_saved = state(0, persist=False)

    def mount(self, request, **kwargs):
        self.plain_attr = 0
        self._private_attr = 0
        self._mounted_pid = os.getpid()
        self._restored = False

    def handle_restore(self, request):
        self._restored = True

    def snapshot(self):
        return {
            "count": self.count,
            "client_count": self.client_count,
            "plain_state": self.plain_state,
            "plain_attr": self.plain_attr,
            "not_saved": self.not_saved,
            "private_attr": self._private_attr,
            "restored": self._restored,
            "mounted_pid": self._mounted_pid,
        }

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self.client_count += 1
        self.plain_state += 1
        self.plain_attr += 1
        self.not_saved += 1
        self._private_attr += 1

    @event_handler()
    def probe(self, **kwargs):
        self.set_changed_keys(["probe"])


class SwitchedOffView(_Probed, LiveView):
    """The counter's kinds of state on a view that saves nothing (#4128)."""

    exposure_policy = "explicit"
    automatic_state = False
    template = "<div dj-root>" + PROBE + "</div>"

    count = state(0, persist="server")
    plain_state = state(0)

    def mount(self, request, **kwargs):
        self.plain_attr = 0
        self._private_attr = 0
        self._mounted_pid = os.getpid()

    def snapshot(self):
        return {
            "count": self.count,
            "plain_state": self.plain_state,
            "plain_attr": self.plain_attr,
            "private_attr": self._private_attr,
            "mounted_pid": self._mounted_pid,
        }

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1
        self.plain_state += 1
        self.plain_attr += 1
        self._private_attr += 1


class LegacyCounterView(_Probed, LiveView):
    """A view still on the 1.x policy (``exposure_policy = "legacy"``)."""

    exposure_policy = "legacy"
    template = "<div dj-root>" + PROBE + "</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    def snapshot(self):
        return {"count": self.count}

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class ItemView(_Probed, LiveView):
    """URL kwargs, the query string through ``handle_params``, and a persisted
    field the URL does not carry."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"

    page = state(1, persist="server")
    hits = state(0, persist="server")

    def mount(self, request, pk=0, **kwargs):
        self.pk = pk

    def handle_params(self, params, uri):
        self.page = int(params.get("page", 1))

    def snapshot(self):
        return {"pk": self.pk, "page": self.page, "hits": self.hits}

    @event_handler()
    def hit(self, **kwargs):
        self.hits += 1


class HeldView(_Probed, LiveView):
    """A model ``mount()`` loads, and a value a handler sets that
    ``handle_params()`` would default (#4107)."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"

    def mount(self, request, **kwargs):
        self.member = get_user_model().objects.order_by("pk").first()
        self.tab = "a"
        self.count = 0

    def handle_params(self, params, uri):
        self.tab = params.get("tab", "a")

    def snapshot(self):
        return {"member": type(self.member).__name__, "tab": self.tab, "count": self.count}

    @event_handler()
    def pick(self, value: str = "b", **kwargs):
        self.tab = value
        self.count += 1

    @event_handler()
    def noop(self, **kwargs):
        self.set_changed_keys(["probe"])


class SignupForm(forms.Form):
    name = forms.CharField(min_length=3)
    email = forms.EmailField()


class FormView(_Probed, FormMixin, LiveView):
    """FormMixin mid-validation; ``name``'s input opted into persistence."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    form_class = SignupForm
    form_input = persisted_form_input("name")

    def snapshot(self):
        return {
            "form_data": dict(self.form_data),
            "field_errors": sorted(self.field_errors),
        }


class StepOne(forms.Form):
    first = forms.CharField()


class StepTwo(forms.Form):
    second = forms.CharField()


class WizardView(_Probed, WizardMixin, LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    wizard_steps = [
        {"name": "one", "title": "One", "form_class": StepOne},
        {"name": "two", "title": "Two", "form_class": StepTwo},
    ]

    def snapshot(self):
        return {
            "step": self.wizard_step_index,
            "data": self.wizard_step_data,
        }


class WhoView(_Probed, LiveView):
    """Who the request's user is, read at mount and again on each event."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    login_required = True

    def mount(self, request, **kwargs):
        self.who = request.user.username
        self.seen = request.user.username
        self.n = 0

    def snapshot(self):
        return {"who": self.who, "seen": self.seen, "n": self.n}

    @event_handler()
    def look(self, **kwargs):
        self.n += 1
        self.seen = self.request.user.username if self.request.user.is_authenticated else ""


class Bump(LiveComponent):
    template = "<span>{{ n }}</span>"

    class State(TypedState):
        n: int = 0

    @event_handler()
    def bump(self, **kwargs):
        self.state.n += 1


class ComponentView(_Probed, LiveView):
    """A LiveComponent with state, and an ADR-034 interactive dropdown."""

    exposure_policy = "explicit"
    template = "<div dj-root>{{ bumper }}{{ menu }}" + PROBE + "</div>"
    bumper = Bump()
    menu = DropdownMenu(label="Pick", items=[{"label": "Edit", "value": "edit"}])

    def snapshot(self):
        return {
            "n": self.bumper.state.n,
            "bump_id": self.bumper.component_id,
            "menu_open": self.menu.open,
            "menu_id": self.menu.component_id,
        }


class PushView(_Probed, LiveView):
    """Receives ``push_to_view`` from another process."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"

    def mount(self, request, **kwargs):
        self.received = []

    def snapshot(self):
        return {"received": self.received}

    def handle_push(self, value: int = 0, **kwargs):
        self.received = self.received + [value]

    @event_handler()
    def probe(self, **kwargs):
        self.set_changed_keys(["probe"])


class RoomView(_Probed, PresenceMixin, LiveView):
    """Presence across processes."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    presence_key = "mn-room"

    def mount(self, request, **kwargs):
        self.track_presence(meta={})

    def snapshot(self):
        return {"online": self.presence_count()}


class AsyncView(_Probed, LiveView):
    """Background work that is in flight when the socket moves."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    result = state("", persist="server")
    running = state(False, persist="server")

    def snapshot(self):
        return {"result": self.result, "running": self.running}

    @event_handler()
    def start(self, **kwargs):
        self.running = True
        self.start_async(self._work, name="work")

    def _work(self):
        from tests.integration.multinode.app import serve

        serve.mark("WORK_STARTED")
        serve.WORK_RELEASE.wait()
        self.result = "done"
        self.running = False
        serve.mark("WORK_FINISHED")


class TickView(_Probed, LiveView):
    """A periodic ``handle_tick``."""

    exposure_policy = "explicit"
    template = "<div dj-root>" + PROBE + "</div>"
    tick_interval = 20
    ticks_saved = state(0, persist="server")

    def mount(self, request, **kwargs):
        self.ticks = 0

    def snapshot(self):
        return {"ticks": self.ticks, "ticks_saved": self.ticks_saved}

    def handle_tick(self):
        self.ticks += 1
        self.ticks_saved += 1


class StreamView(_Probed, LiveView):
    """A ``stream()`` with a row inserted after mount."""

    exposure_policy = "explicit"
    template = (
        '<div dj-root><ul id="rows" dj-stream="rows">{% for row in streams.rows %}'
        '<li id="rows-{{ row.id }}">{{ row.id }}</li>{% endfor %}</ul>' + PROBE + "</div>"
    )

    def mount(self, request, **kwargs):
        self.next_id = 2
        self.stream("rows", [{"id": 1}], dom_id=lambda row: "rows-%d" % row["id"])

    def snapshot(self):
        return {"next_id": self.next_id}

    @event_handler()
    def add(self, **kwargs):
        self.stream_insert("rows", {"id": self.next_id})
        self.next_id += 1


# --------------------------------------------------------------------------
# Plain Django views the harness drives: login, logout, push, stats, gates.
# --------------------------------------------------------------------------


def login_view(request):
    user = get_user_model().objects.get(username=request.GET["user"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponse("ok")


def logout_view(request):
    logout(request)
    return HttpResponse("ok")


def push_view(request):
    from djust.push import push_to_view

    push_to_view(
        "tests.integration.multinode.app.views.PushView",
        handler="handle_push",
        payload={"value": int(request.GET["value"])},
    )
    return HttpResponse("ok")


def stats_view(request):
    from tests.integration.multinode.app import serve

    counts = dict(serve.NODE_STATS)
    if request.GET.get("reset"):
        serve.reset_stats()
    return JsonResponse(counts)


def session_keys_view(request):
    """The names of the keys in the caller's own session (never the values)."""
    return JsonResponse({"keys": sorted(str(key) for key in request.session.keys())})


def gate_view(request):
    from tests.integration.multinode.app import serve

    serve.SAVE_GATE.set()
    return HttpResponse("ok")


def release_view(request):
    from tests.integration.multinode.app import serve

    serve.WORK_RELEASE.set()
    return HttpResponse("ok")
