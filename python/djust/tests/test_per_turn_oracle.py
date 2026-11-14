"""The per-turn shortcuts change what a turn costs, never what it sends (#3996, #3997, #3999).

A differential oracle. Every case below is one view and one sequence of turns,
run twice in the same process: once with every shortcut in
:mod:`djust._per_turn` turned OFF (the code path ``main`` had before them) and
once with all of them on. The frames the client would receive must be equal,
byte for byte, over a real ``WebsocketCommunicator``, over the SSE endpoints,
and over a GET plus the page-POST fallback.

The sequences are the ways state changes: a rebind, an in-place change to a
container, a nested change, a model field written under a stable ``id()``,
``temporary_assigns``, a private attribute behind a derived value, a
component's state, an embedded child, and state changed in ``handle_params``,
``handle_info``, a server push, a background result and a tick.

One difference is intended: a context processor value that changes between
turns now renders (it used to be stored and left stale). It cannot be an
equality case, since the old path's frames are the stale ones.
``test_a_time_varying_processor_on_the_scoped_component_path`` states the
difference exactly, and ``test_per_turn_invalidation.py`` pins the rest of it.

Also covered: a ``FormMixin`` validation and submit turn, and stream inserts
and deletes. NOT covered: an upload (its progress frames need the binary upload
protocol), presence, time travel, the actor path and hot reload.

Is the oracle blind? ``test_a_shortcut_that_served_a_stale_value_fails_the_oracle``
breaks each shortcut on purpose (a contract cache that never re-checks, change
detection that calls everything unchanged, a walk that reports no ids) and
checks that the comparison then fails.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Callable, Dict, List, Tuple

import pytest
from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django import forms
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.test import RequestFactory
from django.utils.functional import SimpleLazyObject
from django.utils.safestring import mark_safe

from djust import LiveView, _per_turn, event_handler
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.forms import FormMixin
from djust.tests import _per_turn_harness as harness
from djust.tests._ws_frames import drain_extra, has_type, receive_until

pytestmark = [pytest.mark.asyncio, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def _one_tab(monkeypatch):
    """Every page load here is the same tab (#4106): the GET's id is the one
    the harness's client sends back, and the page bytes are repeatable."""
    from djust import _automatic_state

    monkeypatch.setattr(_automatic_state, "new_tab_id", lambda: harness.TAB)


MODULE = __name__
RAN: List[str] = []


def rows(n: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": i,
            "name": f"item {i}",
            "qty": i % 7,
            "active": i % 3 != 0,
            "tags": ["alpha", f"t{i}"],
        }
        for i in range(n)
    ]


TABLE = (
    '<table>{% for row in items %}<tr id="row-{{ row.id }}"><td>{{ row.name }}</td>'
    "<td>{{ row.qty }}</td><td>{% for t in row.tags %}{{ t }} {% endfor %}</td></tr>"
    "{% endfor %}</table>"
)


class _Base(LiveView):
    exposure_policy = "explicit"  # every subclass states its own; the guard reads the base too

    def _get_context_processors(self):
        return []


# --- counters under each persistence choice -------------------------------------------


class Counter(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><p>Count: {{ count }}</p><i>{{ label }}</i></div>"
    count = state(0)
    label = state("a")

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    @event_handler()
    def relabel(self, value: str = "b", **kwargs):
        self.label = value

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler()
    def same(self, **kwargs):
        self.count = self.count

    @event_handler()
    def forced(self, **kwargs):
        self.set_changed_keys()

    @event_handler()
    def skipped(self, **kwargs):
        self.count += 1
        self._skip_render = True


class ServerCounter(Counter):
    count = state(0, persist="server")
    label = state("a", persist="server")


class ClientCounter(Counter):
    count = state(0, persist="client", client=True)
    label = state("a", persist="client", client=True)


class LegacyCounter(_Base):
    exposure_policy = "legacy"
    template = "<div dj-root><p>Count: {{ count }}</p><i>{{ label }}</i></div>"

    def mount(self, request, **kwargs):
        self.count = 0
        self.label = "a"

    inc = Counter.__dict__["inc"]
    relabel = Counter.__dict__["relabel"]
    nothing = Counter.__dict__["nothing"]
    same = Counter.__dict__["same"]
    forced = Counter.__dict__["forced"]
    skipped = Counter.__dict__["skipped"]


COUNTER_SCRIPT = [
    ("event", "inc"),
    ("event", "nothing"),
    ("event", "relabel", {"value": "b"}),
    ("event", "same"),
    ("event", "inc"),
    ("event", "forced"),
    ("event", "skipped"),
    ("event", "inc"),
]


# --- a list of plain rows: every way of changing it -----------------------------------


class Table(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><h1>{{ title }}</h1>" + TABLE + "</div>"
    title = state("")
    items = state(default_factory=list)

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.items = rows(40)

    @event_handler()
    def cell(self, **kwargs):
        self.items[7]["qty"] += 1

    @event_handler()
    def nested(self, **kwargs):
        self.items[3]["tags"].append("more")

    @event_handler()
    def append(self, **kwargs):
        self.items.append(
            {"id": len(self.items), "name": "new", "qty": 0, "active": True, "tags": []}
        )

    @event_handler()
    def rebind(self, **kwargs):
        self.items = [dict(row) for row in self.items[:-2]]

    @event_handler()
    def swap(self, **kwargs):
        self.items[0], self.items[1] = self.items[1], self.items[0]

    @event_handler()
    def retitle(self, **kwargs):
        self.title += "!"

    @event_handler()
    def both(self, **kwargs):
        self.title += "?"
        self.items[1]["name"] = "renamed"

    @event_handler()
    def nothing(self, **kwargs):
        pass

    @event_handler()
    def clear(self, **kwargs):
        self.items.clear()


class LegacyTable(_Base):
    exposure_policy = "legacy"
    template = Table.template

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.items = rows(40)

    for _name in (
        "cell",
        "nested",
        "append",
        "rebind",
        "swap",
        "retitle",
        "both",
        "nothing",
        "clear",
    ):
        locals()[_name] = Table.__dict__[_name]
    del _name


TABLE_SCRIPT = [
    ("event", "cell"),
    ("event", "retitle"),
    ("event", "nested"),
    ("event", "nothing"),
    ("event", "append"),
    ("event", "both"),
    ("event", "swap"),
    ("event", "rebind"),
    ("event", "cell"),
    ("event", "clear"),
    ("event", "append"),
]


# --- derived context values that share objects, and scalars, with the list ------------


class Derived(_Base):
    """Context values that ARE objects inside the changed attribute, or equal
    ones: what ``_collect_sub_ids`` exists to find (#703)."""

    exposure_policy = "explicit"
    template = (
        "<div dj-root><p>{{ first.name }}/{{ first.qty }}</p><p>{{ flag }}{{ zero }}{{ word }}</p>"
        "<p>{{ total }}</p><ul>{% for r in active %}<li>{{ r.name }}</li>{% endfor %}</ul>"
        "<b>{% for t in first_tags %}{{ t }},{% endfor %}</b>"
        + TABLE.replace("items", "alias")
        + "</div>"
    )
    items = state(default_factory=list)
    other = state(0)

    def mount(self, request, **kwargs):
        self.items = rows(12)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["alias"] = self.items
        context["first"] = self.items[0] if self.items else {}
        context["first_tags"] = self.items[0]["tags"] if self.items else []
        context["flag"] = True
        context["zero"] = 0
        context["word"] = "alpha"
        context["total"] = sum(r["qty"] for r in self.items)
        context["active"] = [r for r in self.items if r["active"]]
        return context

    @event_handler()
    def first_cell(self, **kwargs):
        self.items[0]["qty"] += 5

    @event_handler()
    def first_tag(self, **kwargs):
        self.items[0]["tags"].append("z")

    @event_handler()
    def last_cell(self, **kwargs):
        self.items[-1]["qty"] += 1

    @event_handler()
    def deactivate(self, **kwargs):
        self.items[1]["active"] = False

    @event_handler()
    def drop_first(self, **kwargs):
        del self.items[0]

    @event_handler()
    def touch_other(self, **kwargs):
        self.other += 1


DERIVED_SCRIPT = [
    ("event", "first_cell"),
    ("event", "last_cell"),
    ("event", "first_tag"),
    ("event", "touch_other"),
    ("event", "deactivate"),
    ("event", "drop_first"),
    ("event", "first_cell"),
]


# --- containers that are NOT plain: the ids under them still matter --------------------


class Thing:
    def __init__(self, label: str) -> None:
        self.label = label


class Opaque(_Base):
    exposure_policy = "legacy"
    template = (
        "<div dj-root><p>{{ current.label }}</p><b>{{ things|length }}</b><i>{{ n }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.things = [Thing("a"), Thing("b")]
        self.n = 0

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["current"] = self.things[0]
        return context

    @event_handler()
    def write_and_append(self, **kwargs):
        self.things[0].label += "!"
        self.things.append(Thing("c"))

    @event_handler()
    def write_only(self, **kwargs):
        self.things[0].label += "?"

    @event_handler()
    def write_and_name(self, **kwargs):
        self.things[0].label += "#"
        self.set_changed_keys("things")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def replace_first(self, **kwargs):
        self.things[0] = Thing("fresh")


OPAQUE_SCRIPT = [
    ("event", "write_and_append"),
    ("event", "write_only"),
    ("event", "bump"),
    ("event", "write_and_name"),
    ("event", "replace_first"),
    ("event", "write_and_append"),
]


# --- a model: a field written under a stable id() --------------------------------------


class Models(_Base):
    exposure_policy = "legacy"
    template = (
        "<div dj-root><p>{{ who.first_name }}</p><ul>{% for p in people %}"
        "<li>{{ p.first_name }}</li>{% endfor %}</ul><i>{{ n }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.who = User.objects.get(username="oracle-a")
        self.people = list(User.objects.filter(username__startswith="oracle-").order_by("username"))
        self.n = 0

    @event_handler()
    def write_field(self, **kwargs):
        self.who.first_name += "x"

    @event_handler()
    def write_field_and_name(self, **kwargs):
        self.who.first_name += "y"
        self.set_changed_keys("who")

    @event_handler()
    def write_in_list(self, **kwargs):
        self.people[1].first_name += "z"

    @event_handler()
    def reload(self, **kwargs):
        self.who = User.objects.get(username="oracle-b")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


MODELS_SCRIPT = [
    ("event", "write_field"),
    ("event", "bump"),
    ("event", "write_field_and_name"),
    ("event", "write_in_list"),
    ("event", "reload"),
    ("event", "bump"),
]


# --- temporary_assigns and a private attribute behind a derived value -----------------


class Feed(_Base):
    exposure_policy = "legacy"
    temporary_assigns = {"rows": []}
    template = (
        '<div dj-root><ul id="feed" dj-update="append">{% for r in rows %}'
        '<li id="r-{{ r.id }}">{{ r.text }}</li>{% endfor %}</ul><i>{{ n }}</i></div>'
    )

    def mount(self, request, **kwargs):
        self.rows = [{"id": 1, "text": "one"}]
        self.n = 1

    @event_handler()
    def add(self, **kwargs):
        self.n += 1
        self.rows = [{"id": self.n, "text": f"row {self.n}"}]

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


FEED_SCRIPT = [("event", "add"), ("event", "bump"), ("event", "add"), ("event", "add")]


class Private(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><ul>{% for p in products %}<li>{{ p.name }}</li>{% endfor %}</ul><i>{{ n }}</i></div>"
    n = state(0)

    def mount(self, request, **kwargs):
        self._cache = [{"name": "a"}, {"name": "b"}]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["products"] = self._cache
        return context

    @event_handler()
    def rename(self, **kwargs):
        self._cache[0]["name"] += "!"

    @event_handler()
    def replace(self, **kwargs):
        self._cache = [{"name": "c"}]

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


PRIVATE_SCRIPT = [("event", "rename"), ("event", "bump"), ("event", "replace"), ("event", "rename")]


# --- a component and an embedded child ------------------------------------------------


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"
        seen: tuple = ()

    template = "<p>{{ active }}<b>{{ component_id }}</b>{% for s in seen %}{{ s }}{% endfor %}</p>"

    @event_handler()
    def select(self, value: str = "", **kwargs: Any) -> None:
        self.state.active = value

    @event_handler()
    def remember(self, value: str = "", **kwargs: Any) -> None:
        # The first call gives this instance its own list; later ones change
        # it in place, with no dirty flag (#2900).
        if isinstance(self.state.seen, tuple):
            self.state.seen = [value]
        else:
            self.state.seen.append(value)


class ComponentPage(_Base):
    exposure_policy = "legacy"
    template = "<div dj-root><h1>{{ title }}</h1>{{ nav }}<i>{{ note }}</i></div>"
    nav = Tabs()

    def mount(self, request, **kwargs):
        self.title = "Page"
        self.note = ""

    @event_handler()
    def rename(self, value: str = "", **kwargs):
        self.title = value

    @event_handler()
    def both(self, **kwargs):
        self.nav.state.active = "both"
        self.title = "Both"


COMPONENT_SCRIPT = [
    ("event", "select", {"component_id": "nav", "value": "billing"}),
    ("event", "rename", {"value": "Renamed"}),
    ("event", "remember", {"component_id": "nav", "value": "x"}),
    ("event", "remember", {"component_id": "nav", "value": "y"}),
    ("event", "select", {"component_id": "nav", "value": "billing"}),
    ("event", "both"),
    ("event", "select", {"component_id": "nav", "value": "last"}),
]


class Child(LiveView):
    exposure_policy = "explicit"
    sticky = True
    sticky_id = "menu"
    count = state(1, persist="server")
    template = "<div>Child={{ count }}</div>"

    @event_handler()
    def increment(self, **kwargs):
        self.count += 1


class Parent(_Base):
    exposure_policy = "explicit"
    title = state("parent")
    template = (
        "<div dj-root><h1>{{ title }}</h1>{% load live_tags %}{% live_render "
        f'"{MODULE}.Child" sticky=True %}}</div>'
    )

    @event_handler()
    def retitle(self, **kwargs):
        self.title += "!"

    @event_handler()
    def change_child(self, **kwargs):
        self._get_child_view("menu").count = 40


CHILD_SCRIPT = [
    ("event", "increment", {"view_id": "menu"}),
    ("event", "retitle"),
    ("event", "change_child"),
    ("event", "increment", {"view_id": "menu"}),
]


# --- state changed outside an event handler -------------------------------------------


class Lifecycle(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><p>{{ page }}</p><b>{{ count }}</b>" + TABLE + "</div>"
    page = state(1)
    count = state(0, persist="server")
    items = state(default_factory=list)
    _listen_channels = frozenset({"per_turn_oracle"})

    def mount(self, request, **kwargs):
        self.items = rows(5)

    def handle_params(self, params, uri):
        self.page = int(params.get("page", 1))
        if self.page > 5:
            self.items[0]["name"] = f"page {self.page}"

    def handle_info(self, message):
        RAN.append("notify")
        self.items[1]["qty"] = message["payload"]["qty"]

    def handle_push(self, count=0, **kwargs):
        RAN.append("push")
        self.count = count
        self.items[2]["tags"].append("pushed")

    @event_handler()
    def spawn(self, **kwargs):
        self.start_async(self._work)

    def _work(self):
        return 13

    def handle_async_result(self, name, result=None, error=None):
        RAN.append("result")
        self.count = result
        self.items.append({"id": 99, "name": "async", "qty": 1, "active": True, "tags": []})


LIFECYCLE_SCRIPT = [
    ("url", {"page": "3"}),
    ("url", {"page": "7"}),
    ("notify", {"qty": 50}),
    ("push", "handle_push", {"count": 4}),
    ("async", "spawn"),
    ("url", {"page": "8"}),
]


class Ticker(_Base):
    exposure_policy = "explicit"
    template = "<div dj-root><b>{{ ticks }}</b>" + TABLE + "</div>"
    ticks = state(0)
    items = state(default_factory=list)
    tick_interval = 100

    def mount(self, request, **kwargs):
        self.items = rows(4)

    def handle_tick(self):
        if self.ticks < 3:
            self.ticks += 1
            self.items[self.ticks]["qty"] += 10


TICK_SCRIPT = [("ticks", 3)]


# --- context processors whose values do not change between turns ----------------------


def constant_processor(request):
    return {
        "site_name": "Oracle",
        "safe_chunk": mark_safe("<u>safe</u>"),
        "levels": {"low": 1, "high": [2, 3]},
        "lazy_word": SimpleLazyObject(lambda: "lazy"),
        "opaque": Thing("opaque"),
        "request_path": request.path,
    }


class Processors(Counter):
    template = (
        "<div dj-root><p>Count: {{ count }}</p><i>{{ label }}</i><p>{{ site_name }}{{ safe_chunk }}</p>"
        "<p>{{ levels.low }}{% for h in levels.high %}{{ h }}{% endfor %}</p>"
        "<p>{{ lazy_word }}{{ opaque.label }}{{ request_path }}{{ user }}</p>"
        "<p>{% if count %}{{ safe_chunk }}{{ site_name }}{% endif %}{{ theme_mode }}</p></div>"
    )
    count = state(0)
    label = state("a")

    def _get_context_processors(self):
        return [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
            "djust.theming.context_processors.theme_context",
            MODULE + ".constant_processor",
        ]


# --- a form and a stream --------------------------------------------------------------


class SignupForm(forms.Form):
    name = forms.CharField(max_length=5, required=False)
    email = forms.EmailField()


class FormView(FormMixin, _Base):
    exposure_policy = "explicit"
    form_class = SignupForm
    template = (
        '<div dj-root><form dj-submit="submit_form">'
        '<input name="name" value="{{ form_data.name }}">'
        '<input name="email" value="{{ form_data.email }}">'
        "{% for e in field_errors.name %}<i>name:{{ e }}</i>{% endfor %}"
        "{% for e in field_errors.email %}<i>email:{{ e }}</i>{% endfor %}"
        "<p>valid={{ is_valid }}</p></form></div>"
    )


FORM_SCRIPT = [
    ("event", "validate_field", {"field": "name", "value": "TOOLONG"}),
    ("event", "validate_field", {"field": "name", "value": "Al"}),
    ("event", "validate_field", {"field": "email", "value": "nope"}),
    ("event", "submit_form", {"name": "Al", "email": "not-an-email"}),
    ("event", "submit_form", {"name": "Al", "email": "al@example.com"}),
]


class StreamView(_Base):
    exposure_policy = "legacy"
    template = (
        '<div dj-root><div id="list" dj-stream="rows">'
        '{% for r in streams.rows %}<div id="rows-{{ r.id }}">{{ r.body }}</div>{% endfor %}'
        "</div><i>{{ n }}</i></div>"
    )

    def mount(self, request, **kwargs):
        self.stream("rows", [{"id": i, "body": f"B{i}"} for i in range(3)])
        self.n = 0

    @event_handler()
    def insert(self, **kwargs):
        self.n += 1
        self.stream_insert("rows", {"id": 100 + self.n, "body": f"NEW{self.n}"})

    @event_handler()
    def insert_only(self, **kwargs):
        self.stream_insert("rows", {"id": 500, "body": "ONLY"})

    @event_handler()
    def remove(self, **kwargs):
        self.stream_delete("rows", {"id": 1, "body": "B1"})

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1


STREAM_SCRIPT = [
    ("event", "insert"),
    ("event", "insert_only"),
    ("event", "bump"),
    ("event", "remove"),
    ("event", "insert"),
]


# --- what a socket holds between turns (#3998) ----------------------------------------


class ScalarTypes(_Base):
    """One slot whose value changes its TYPE: ``1``, ``True``, ``1.0``, ``"1"``.

    The compact fingerprint keeps a ``str`` / ``int`` / ``None`` leaf as the
    value itself. ``True == 1 == 1.0``, so the types that compare equal across
    types must keep their tag, or a turn that swaps one for another renders
    nothing.
    """

    exposure_policy = "explicit"
    template = (
        "<div dj-root>{% for v in items %}<b>{{ v }}</b>{% endfor %}"
        "<i>{{ box.k }}</i>{% for k, v in keyed.items %}<u>{{ k }}={{ v }}</u>{% endfor %}</div>"
    )
    items = state(default_factory=list)
    box = state(default_factory=dict)
    keyed = state(default_factory=dict)

    VALUES = {
        "one": 1,
        "true": True,
        "float": 1.0,
        "str": "1",
        "none": None,
        "zero": 0,
        "false": False,
        "empty": "",
    }

    def mount(self, request, **kwargs):
        self.items = [1, "x"]
        self.box = {"k": 1}
        self.keyed = {1: "a"}

    @event_handler()
    def become(self, to: str = "one", **kwargs):
        value = self.VALUES[to]
        self.items[0] = value
        self.box["k"] = value

    @event_handler()
    def rekey(self, to: str = "one", **kwargs):
        self.keyed = {self.VALUES[to]: "a"}


SCALAR_SCRIPT: List[Tuple[Any, ...]] = [
    ("event", "become", {"to": name})
    for name in ("true", "float", "one", "str", "none", "zero", "false", "empty", "one")
] + [("event", "rekey", {"to": name}) for name in ("true", "float", "str", "one")]


class Dirty(_Base):
    """Dirty tracking beside a list changed in place, saved and changed back.

    The dirty baseline and the last render's fingerprint of ``items`` are one
    object while they are equal (#3998), so every way they come apart and back
    together is a turn here: a row edited, the edit undone, ``mark_clean``, a
    row appended and removed, an equal list assigned, and changes made by a
    push and by ``handle_info`` between events.
    """

    exposure_policy = "explicit"
    template = (
        "<div dj-root><p>{{ dirty }}</p><u>{% for f in changed %}{{ f }} {% endfor %}</u>"
        "<i>{{ n }}</i>" + TABLE + "</div>"
    )
    items = state(default_factory=list)
    n = state(0)
    _listen_channels = frozenset({"per_turn_oracle"})

    def mount(self, request, **kwargs):
        self.items = rows(6)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Only the view's own two fields: ``changed_fields`` also lists
        # ``request`` whenever the transport bound a new request object, and
        # whether it did is not something two runs agree on.
        changed = sorted(self.changed_fields & {"items", "n"})
        context["dirty"] = bool(changed)
        context["changed"] = changed
        return context

    @event_handler()
    def touch(self, row: int = 0, **kwargs):
        self.items[row]["name"] += "!"

    @event_handler()
    def undo(self, row: int = 0, **kwargs):
        self.items[row]["name"] = self.items[row]["name"].rstrip("!")

    @event_handler()
    def bump(self, **kwargs):
        self.n += 1

    @event_handler()
    def save(self, **kwargs):
        self.mark_clean()

    @event_handler()
    def touch_and_save(self, row: int = 0, **kwargs):
        # The baseline is taken BEFORE this turn's render: the fingerprint the
        # last render left is of the rows as they were, and must not be it.
        self.items[row]["name"] += "?"
        self.mark_clean()

    @event_handler()
    def grow(self, **kwargs):
        self.items.append({"id": 99, "name": "new", "qty": 1, "active": True, "tags": []})

    @event_handler()
    def shrink(self, **kwargs):
        self.items.pop()

    @event_handler()
    def same_again(self, **kwargs):
        self.items = [dict(row, tags=list(row["tags"])) for row in self.items]

    @event_handler()
    def head(self, **kwargs):
        self.items.insert(0, {"id": 98, "name": "head", "qty": 2, "active": False, "tags": ["h"]})

    def handle_info(self, message):
        RAN.append("notify")
        self.items[1]["qty"] = message["payload"]["qty"]

    def handle_push(self, row=0, **kwargs):
        RAN.append("push")
        self.items[row]["tags"].append("pushed")


DIRTY_SCRIPT = [
    ("event", "bump"),
    ("event", "touch"),
    ("event", "bump"),
    ("event", "undo"),
    ("event", "touch", {"row": 3}),
    ("event", "save"),
    ("event", "bump"),
    ("event", "undo", {"row": 3}),
    ("notify", {"qty": 50}),
    ("event", "bump"),
    ("push", "handle_push", {"row": 2}),
    ("event", "grow"),
    ("event", "shrink"),
    ("event", "same_again"),
    ("event", "save"),
    ("event", "head"),
    ("event", "touch", {"row": 5}),
    ("notify", {"qty": 51}),
    ("event", "save"),
    ("event", "bump"),
    ("event", "touch_and_save", {"row": 1}),
    ("event", "bump"),
    ("event", "touch", {"row": 1}),
    ("event", "touch_and_save", {"row": 4}),
]


# --- running a case -------------------------------------------------------------------

Script = List[Tuple[Any, ...]]

#: How many frames / responses each passing comparison covered (for the report).
FRAMES_COMPARED: List[int] = []

WS_CASES: List[Tuple[str, type, Script]] = [
    ("counter", Counter, COUNTER_SCRIPT),
    ("counter_server", ServerCounter, COUNTER_SCRIPT),
    ("counter_client", ClientCounter, COUNTER_SCRIPT),
    ("counter_legacy", LegacyCounter, COUNTER_SCRIPT),
    ("table", Table, TABLE_SCRIPT),
    ("table_legacy", LegacyTable, TABLE_SCRIPT),
    ("derived", Derived, DERIVED_SCRIPT),
    ("opaque", Opaque, OPAQUE_SCRIPT),
    ("models", Models, MODELS_SCRIPT),
    ("temporary_assigns", Feed, FEED_SCRIPT),
    ("private_attribute", Private, PRIVATE_SCRIPT),
    ("component", ComponentPage, COMPONENT_SCRIPT),
    ("embedded_child", Parent, CHILD_SCRIPT),
    ("lifecycle", Lifecycle, LIFECYCLE_SCRIPT),
    ("tick", Ticker, TICK_SCRIPT),
    ("processors", Processors, COUNTER_SCRIPT),
    ("form", FormView, FORM_SCRIPT),
    ("stream", StreamView, STREAM_SCRIPT),
    ("scalar_types", ScalarTypes, SCALAR_SCRIPT),
    ("dirty_tracking", Dirty, DIRTY_SCRIPT),
]


def _normalise(frame: Dict[str, Any]) -> Dict[str, Any]:
    """A frame as compared: a signed token is replaced by what it signs.

    The token carries a signing timestamp, a ``created`` second and the
    session's binding, which differ between two runs; the state it signs must
    not.
    """
    token = frame.get("state_snapshot_signed")
    if isinstance(token, str):
        from djust._exposure_snapshots import _SALT

        payload = json.loads(signing.TimestampSigner(salt=_SALT).unsign(token))
        payload.pop("created")
        payload.pop("binding")  # names the run's own session
        frame = {**frame, "state_snapshot_signed": payload}
    # Per-connection identifiers and the background batch token, random for
    # every run. The token's presence is kept.
    frame = {k: v for k, v in frame.items() if k not in ("session_id", "sw_identity")}
    if "async_batch" in frame:
        frame["async_batch"] = "<token>"
    return frame


async def _wait_ran(label: str, count: int = 1) -> None:
    from djust.tests._ws_frames import WAIT_S

    loop = asyncio.get_running_loop()
    deadline = loop.time() + WAIT_S
    while RAN.count(label) < count:
        assert loop.time() < deadline, f"{label!r} never ran: {RAN}"
        await asyncio.sleep(0.01)


_UPDATE = has_type("patch", "html_update", "noop", "error")


async def _users() -> None:
    def make() -> None:
        for name in ("a", "b", "c"):
            User.objects.get_or_create(username=f"oracle-{name}", defaults={"first_name": name})

    await sync_to_async(make)()


async def run_ws(view_class: type, script: Script) -> List[Dict[str, Any]]:
    """Mount *view_class* over a real socket, run *script*, return every frame."""
    RAN.clear()
    await _users()
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE, DJUST_LIVE_RENDER_ALLOWED_MODULES=[MODULE]):
        comm, mount_frame = await harness.mount_view(view_class, key)
        frames = [mount_frame]
        try:
            for step in script:
                kind = step[0]
                if kind == "event":
                    frames += await harness.event_frames(
                        comm, step[1], step[2] if len(step) > 2 else None
                    )
                elif kind == "url":
                    query = "&".join(f"{k}={v}" for k, v in step[1].items())
                    await comm.send_json_to(
                        {"type": "url_change", "params": step[1], "uri": f"{harness.PATH}?{query}"}
                    )
                    frames += await receive_until(comm, _UPDATE, what="the url_change reply")
                elif kind == "notify":
                    await get_channel_layer().group_send(
                        "djust_db_notify_per_turn_oracle",
                        {"type": "db_notify", "channel": "per_turn_oracle", "payload": step[1]},
                    )
                    await _wait_ran("notify", RAN.count("notify") + 1)
                    frames += await receive_until(comm, _UPDATE, what="the notify frame")
                elif kind == "push":
                    from djust.push import apush_to_view

                    await apush_to_view(
                        f"{MODULE}.{view_class.__name__}", handler=step[1], payload=step[2]
                    )
                    await _wait_ran("push", RAN.count("push") + 1)
                    frames += await receive_until(comm, _UPDATE, what="the push frame")
                elif kind == "async":
                    frames += await harness.event_frames(comm, step[1])
                    frames += await receive_until(
                        comm,
                        lambda got: any(f.get("source") == "async" for f in got),
                        what="the background result",
                    )
                elif kind == "ticks":
                    frames += await receive_until(
                        comm,
                        lambda got: sum(f.get("source") == "tick" for f in got) >= step[1],
                        what="the tick frames",
                    )
                else:  # pragma: no cover - a typo in a script
                    raise AssertionError(kind)
            frames += await drain_extra(comm)
        finally:
            await comm.disconnect()
    return [_normalise(frame) for frame in frames]


class _Warnings(logging.Handler):
    """Every WARNING-or-worse record a ``djust`` logger emits during a run."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(f"{record.name}: {record.getMessage()}")


async def observe(run: Callable[[], Any]) -> List[Any]:
    """What a run sent, followed by what it logged at WARNING or above.

    The log is part of what is compared: a value that wrongly reached the
    stored state shows as a "non-serializable value" warning (#1786) before it
    shows in any frame.
    """
    from djust import _automatic_state

    # Its once-per-process reports would be in the first run's log only.
    _automatic_state._NOTICED.clear()
    handler = _Warnings()
    logger = logging.getLogger("djust")
    logger.addHandler(handler)
    try:
        frames = await run()
    finally:
        logger.removeHandler(handler)
    return list(frames) + [{"warnings": handler.messages}]


async def both(run: Callable[[], Any], monkeypatch: Any, *off: str) -> Tuple[Any, Any]:
    """*run* with the shortcuts named in *off* (default: all) turned off, then on."""
    with harness.shortcuts(monkeypatch, *(off or tuple(_per_turn.ALL))):
        before = await observe(run)
    with harness.shortcuts(monkeypatch):
        after = await observe(run)
    return before, after


def as_bytes(frames: Any) -> bytes:
    return json.dumps(frames, sort_keys=True, default=str).encode()


@pytest.mark.parametrize("label,view_class,script", WS_CASES, ids=[case[0] for case in WS_CASES])
async def test_websocket_frames_are_identical(monkeypatch, label, view_class, script):
    before, after = await both(lambda: run_ws(view_class, script), monkeypatch)
    assert [f.get("type") for f in after] == [f.get("type") for f in before]
    for index, (old, new) in enumerate(zip(before, after)):
        assert as_bytes(new) == as_bytes(old), f"frame {index} of {label} differs"
    # Not vacuous: the script produced renders, not only acknowledgements.
    assert any(f.get("type") in ("patch", "html_update") for f in after[1:]), after
    FRAMES_COMPARED.append(len(after) - 1)


#: The cases each shortcut is also turned off alone on.
ALONE_LABELS = (
    "table",
    "derived",
    "processors",
    "counter_client",
    "lifecycle",
    "tick",
    "scalar_types",
    "dirty_tracking",
)
ALONE_CASES = [case for case in WS_CASES if case[0] in ALONE_LABELS]


@pytest.mark.parametrize("shortcut", sorted(_per_turn.ALL))
@pytest.mark.parametrize(
    "label,view_class,script", ALONE_CASES, ids=[case[0] for case in ALONE_CASES]
)
async def test_each_shortcut_alone_changes_no_frame(
    monkeypatch, shortcut, label, view_class, script
):
    """One shortcut off against all on: no pair of them hides a difference."""
    before, after = await both(lambda: run_ws(view_class, script), monkeypatch, shortcut)
    assert as_bytes(after) == as_bytes(before)


# --- GET and the page-POST fallback ---------------------------------------------------


class ServerTable(Table):
    """The fallback restores state from the session, so the table is persisted."""

    title = state("", persist="server")
    items = state(default_factory=list, persist="server")


class ServerDerived(Derived):
    items = state(default_factory=list, persist="server")
    other = state(0, persist="server")


HTTP_CASES = [
    ("counter", Counter, COUNTER_SCRIPT),
    ("counter_server", ServerCounter, COUNTER_SCRIPT),
    ("counter_legacy", LegacyCounter, COUNTER_SCRIPT),
    ("table", ServerTable, TABLE_SCRIPT),
    ("derived", ServerDerived, DERIVED_SCRIPT),
    ("processors", Processors, COUNTER_SCRIPT),
]


def run_http(view_class: type, script: Script, echo: bool = False) -> List[Any]:
    """The page GET, then each event of *script* as a fallback POST.

    With *echo*, each POST sends the render token the page (then the previous
    answer) carried, as the client does, so the answers are patches wherever
    the page is reproducible (#4108).
    """
    from djust.tests.http_fallback_matrix import DigestEcho, meta_digest

    def request(session: Any, event_name: Any = None, params: Any = None, token: Any = None) -> Any:
        factory = RequestFactory()
        if event_name is None:
            made = factory.get(harness.PATH)
        else:
            headers = {"HTTP_X_DJUST_RENDER_DIGEST": token} if token else {}
            made = factory.post(
                harness.PATH,
                data=json.dumps(params or {}),
                content_type="application/json",
                HTTP_X_DJUST_EVENT=event_name,
                HTTP_X_DJUST_TAB=harness.TAB,
                **headers,
            )
        made.user, made.tenant, made.session = AnonymousUser(), None, session
        return made

    session = SessionStore()
    session.create()
    out: List[Any] = []
    with harness.ws_settings(MODULE):
        response = view_class.as_view()(request(session))

        # The page carries a freshly masked CSRF token, and it and every answer
        # carry a render digest bound to this run's session (#4108); nothing
        # else varies.
        def stable(text: str) -> str:
            # The digest half is bound to the session; the shape half is the
            # render's own and must not differ between the two runs.
            text = re.sub(r'(<meta name="djust-csrf-token" content=")[^"]*', r"\1<token>", text)
            text = re.sub(
                r'(<meta name="djust-render-digest" content=")[^".]*', r"\1<digest>", text
            )
            return re.sub(r'("render_digest": ")[^".]*', r"\1<digest>", text)

        page_text = response.content.decode()
        tokens = DigestEcho(meta_digest(page_text))
        out.append([response.status_code, stable(page_text)])
        for step in script:
            if step[0] != "event":
                continue
            sent = tokens.to_send() if echo else None
            posted = request(
                SessionStore(session.session_key),
                step[1],
                step[2] if len(step) > 2 else None,
                sent,
            )
            response = view_class.as_view()(posted)
            body = response.content.decode()
            if response.status_code == 200:
                tokens.note(json.loads(body), sent)
            out.append([response.status_code, stable(body)])
    return out


#: The cases whose every rendered fallback answer is patches once the client
#: echoes the render token: their state is persisted and every event renders,
#: so each request reproduces the page. The others answer with patches while
#: the page is what the client shows, and with the whole page after an event
#: left state that is not persisted or changed state without rendering it.
HTTP_ALWAYS_PATCHED = {"table", "derived"}


@pytest.mark.parametrize("echo", [False, True], ids=["whole-page", "patches"])
@pytest.mark.parametrize(
    "label,view_class,script", HTTP_CASES, ids=[case[0] for case in HTTP_CASES]
)
async def test_get_and_fallback_post_are_identical(monkeypatch, label, view_class, script, echo):
    before, after = await both(
        sync_to_async(lambda: run_http(view_class, script, echo)), monkeypatch
    )
    assert [status for status, _ in after[:-1]] == [200] * (len(after) - 1), after
    for index, (old, new) in enumerate(zip(before, after)):
        assert new == old, f"response {index} of {label} differs"
    assert len({body for _, body in after[:-1]}) > 2, "the fallback answers never changed"
    # What each answer brought: the whole page, patches, or no DOM change (a
    # skipped render, a render that changed nothing).
    answers = [json.loads(body) for _, body in after[1:-1]]
    kinds = [
        "html" if "html" in answer else "patches" if answer.get("patches") else "none"
        for answer in answers
    ]
    rendered = [kind for kind in kinds if kind != "none"]
    if not echo:
        assert set(rendered) == {"html"}, kinds
    else:
        # Not vacuous: the patch path was taken, in both runs.
        assert rendered[0] == "patches", (label, kinds)
        if label in HTTP_ALWAYS_PATCHED:
            assert set(rendered) == {"patches"}, (label, kinds)
    FRAMES_COMPARED.append(len(after) - 1)


# --- SSE -------------------------------------------------------------------------------


async def run_sse(view_class: type, script: Script) -> List[Any]:
    from django.contrib.auth import get_user

    from djust.sse import DjustSSEMessageView, DjustSSEStreamView, _sse_sessions

    def request_for(method: str, url: str, body: Dict[str, Any], key: Any = None) -> Any:
        factory = RequestFactory()
        if method == "GET":
            made = factory.get(url, data=body)
        else:
            made = factory.post(url, data=json.dumps(body), content_type="application/json")
        made.session = SessionStore(key)
        if key is None:
            made.session.create()
        made.session = SessionStore(made.session.session_key)
        made.user = SimpleLazyObject(lambda: get_user(made))
        made.tenant = None
        return made

    def drain(session: Any) -> List[Any]:
        got = []
        while not session.queue.empty():
            frame = session.queue.get_nowait()
            if frame is not None:
                got.append(frame)
        return got

    sid = str(uuid.uuid4())
    frames: List[Any] = []
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        first = await sync_to_async(request_for)(
            "GET",
            f"/djust/sse/{sid}/",
            {
                "view": f"{MODULE}.{view_class.__name__}",
                "_djust_url": f"/sse-{view_class.__name__}/",
            },
        )
        response = await DjustSSEStreamView().get(first, session_id=sid)
        assert response.status_code == 200
        session = _sse_sessions[sid]
        try:
            frames += drain(session)
            for step in script:
                if step[0] != "event":
                    continue
                body = {
                    "type": "event",
                    "event": step[1],
                    "params": step[2] if len(step) > 2 else {},
                }
                posted = await sync_to_async(request_for)(
                    "POST", f"/djust/sse/{sid}/message/", body, first.session.session_key
                )
                answer = await DjustSSEMessageView().post(posted, session_id=sid)
                assert answer.status_code in (200, 202, 204), answer.content
                frames += drain(session)
        finally:
            _sse_sessions.pop(sid, None)
    return [_normalise(dict(frame)) if isinstance(frame, dict) else frame for frame in frames]


SSE_CASES = [
    ("counter", Counter, COUNTER_SCRIPT),
    ("counter_client", ClientCounter, COUNTER_SCRIPT),
    ("table", Table, TABLE_SCRIPT),
    ("derived", Derived, DERIVED_SCRIPT),
    ("processors", Processors, COUNTER_SCRIPT),
]

from django.urls import path as _path  # noqa: E402

urlpatterns = [_path(f"sse-{case[1].__name__}/", case[1].as_view()) for case in SSE_CASES]


@pytest.mark.parametrize("label,view_class,script", SSE_CASES, ids=[case[0] for case in SSE_CASES])
async def test_sse_frames_are_identical(monkeypatch, label, view_class, script):
    before, after = await both(lambda: run_sse(view_class, script), monkeypatch)
    assert as_bytes(after) == as_bytes(before)
    assert sum("patch" in as_bytes(f).decode() for f in after) > 1, after
    FRAMES_COMPARED.append(len(after) - 1)


# --- the intended difference, on the scoped component path ----------------------------

CLOCK = {"n": 0, "calls": 0}


def clock_processor(request):
    """A fresh plain value on every call, as a time string would be."""
    CLOCK["calls"] += 1
    CLOCK["n"] += 1
    return {"now": f"T{CLOCK['n']}"}


class ClockPage(_Base):
    exposure_policy = "legacy"
    template = "<div dj-root><h1>{{ now }}</h1>{{ nav }}<i>{{ title }}</i></div>"
    nav = Tabs()

    def mount(self, request, **kwargs):
        self.title = "t"

    def _get_context_processors(self):
        return [MODULE + ".clock_processor"]

    @event_handler()
    def rename(self, value: str = "", **kwargs):
        self.title = value


CLOCK_SCRIPT = [
    ("event", "select", {"component_id": "nav", "value": "a"}),
    ("event", "select", {"component_id": "nav", "value": "b"}),
    ("event", "rename", {"value": "r"}),
    ("event", "select", {"component_id": "nav", "value": "c"}),
]


async def test_a_time_varying_processor_on_the_scoped_component_path(monkeypatch):
    """What changes, exactly, when a processor returns a new value every turn.

    Old path: a component event sends the component's own scoped patch and the
    page keeps the processor value of the last page render. New path: every
    turn's frame carries the current value. A component event cannot do that
    with a scoped patch, so it takes the page render, which on the
    ``component_id`` route is a full ``html_update`` frame (the route's
    existing answer whenever a scoped patch is not exact). Each turn still
    sends exactly one frame: no turn triggers another.
    """

    async def run() -> List[Dict[str, Any]]:
        CLOCK["n"] = CLOCK["calls"] = 0
        return (await observe(lambda: run_ws(ClockPage, CLOCK_SCRIPT)))[:-1]

    with harness.shortcuts(monkeypatch, "request_scoped_changes"):
        old = await run()
        old_calls = CLOCK["calls"]
    with harness.shortcuts(monkeypatch):
        new = await run()
        new_calls = CLOCK["calls"]

    def shown(frame: Dict[str, Any]) -> List[str]:
        return re.findall(r"T\d+", as_bytes([frame.get("patches"), frame.get("html")]).decode())

    events = [step[1] for step in CLOCK_SCRIPT]
    assert len(old) == len(new) == 1 + len(events)
    assert [f["event_name"] for f in new[1:]] == [f["event_name"] for f in old[1:]] == events
    # Old: component events are scoped patches that never mention the value;
    # the one view event renders the page and happens to carry it.
    assert [f["type"] for f in old[1:]] == ["patch"] * len(events)
    assert [shown(f) for f in old[1:]] == [[], [], ["T2"], []]
    # New: every frame carries the value its own processor run returned, and a
    # component event pays for that with the page's HTML.
    assert [f["type"] for f in new[1:]] == ["html_update", "html_update", "patch", "html_update"]
    assert [shown(f) for f in new[1:]] == [["T2"], ["T3"], ["T4"], ["T5"]]
    # Processor runs. Old: the mount and the view event; a scoped patch ran
    # none. New: the mount and ONE per event, because the page render that
    # follows the scoped check takes the check's outputs instead of running
    # the processors a second time.
    assert old_calls == 2
    assert new_calls == 1 + len(events)


async def test_the_page_render_after_a_refused_scoped_patch_reuses_the_processor_run(monkeypatch):
    """The gate-off for that reuse: without the recorded outputs, a component
    event whose scoped patch is refused runs every processor twice."""
    from djust.mixins import context

    async def calls() -> int:
        CLOCK["n"] = CLOCK["calls"] = 0
        await run_ws(ClockPage, CLOCK_SCRIPT)
        return CLOCK["calls"]

    with harness.shortcuts(monkeypatch):
        reused = await calls()
        with monkeypatch.context() as patch:
            patch.setattr(context, "PROCESSOR_REPLAY_ATTR", "_djust_processor_replay_unused")
            twice = await calls()
    component_events = sum(step[1] == "select" for step in CLOCK_SCRIPT)
    assert twice - reused == component_events


# --- the oracle can fail ---------------------------------------------------------------


async def test_the_comparison_is_deterministic(monkeypatch):
    """Two runs of the same mode are equal, so a difference is never noise."""
    with harness.shortcuts(monkeypatch):
        first = await run_ws(ClientCounter, COUNTER_SCRIPT)
        second = await run_ws(ClientCounter, COUNTER_SCRIPT)
    assert as_bytes(first) == as_bytes(second)


FLIPS = {"n": 0}


def flipping_processor(request):
    FLIPS["n"] += 1
    return {"levels": {"low": FLIPS["n"], "high": []}, "site_name": "Oracle"}


class Flipping(Processors):
    """A processor value that changes on every turn."""

    count = state(0)
    label = state("a")

    def _get_context_processors(self):
        return [MODULE + ".flipping_processor"]


class Redeclared(ClientCounter):
    """Its ``label`` grant is taken away after the class is created."""

    count = state(0, persist="client", client=True)
    label = state("a", persist="client", client=True)


async def _redeclared_run() -> Any:
    granted = state("a", persist="client", client=True)
    granted.__set_name__(Redeclared, "label")
    Redeclared.label = granted
    frames = await run_ws(Redeclared, [("event", "inc")])
    plain = state("a")
    plain.__set_name__(Redeclared, "label")
    Redeclared.label = plain
    try:
        return frames + await run_ws(Redeclared, [("event", "inc")])
    finally:
        Redeclared.label = granted


def _break_contract_cache(patch: Any) -> None:
    from djust import _exposure

    patch.setattr(_exposure._ClassFacts, "fresh", lambda self: True)


def _break_snapshot_single_hop(patch: Any) -> None:
    from djust import runtime

    patch.setattr(runtime, "_mints_no_client_snapshot", lambda view: True)


def _break_request_scoped_changes(patch: Any) -> None:
    from djust.mixins import rust_bridge

    real = rust_bridge.fingerprint_and_convert

    def stale(value: Any, prefix: str, *args: Any) -> Any:
        fingerprint, truncated, converted = real(value, prefix, *args)
        return ("the same" if prefix == "levels" else fingerprint), truncated, converted

    patch.setattr(rust_bridge, "fingerprint_and_convert", stale)


def _break_serializable_by_type(patch: Any) -> None:
    from djust.mixins import context

    real = context._is_json_serializable

    def wrong(value: Any) -> bool:
        return True if isinstance(value, Thing) else real(value)

    patch.setattr(context, "_is_json_serializable", wrong)


def _break_plain_sub_ids(patch: Any) -> None:
    from djust.mixins import rust_bridge

    # As if a container holding live objects had been taken for plain data.
    patch.setattr(rust_bridge, "_collect_sub_ids", lambda *args, **kwargs: None)


async def _reconnect_run() -> Any:
    """Two events, then a new consumer on the same session (#4107): the second
    mount frame shows what the first connection saved."""
    RAN.clear()
    key = await harness.new_session_key_async()
    frames: List[Any] = []
    with harness.ws_settings(MODULE):
        comm, mount_frame = await harness.mount_view(Counter, key)
        frames.append(mount_frame)
        try:
            frames += await harness.event_frames(comm, "inc")
            frames += await harness.event_frames(comm, "nothing")
            frames += await harness.event_frames(comm, "inc")
        finally:
            await comm.disconnect()
        again, mount_frame = await harness.mount_view(Counter, key)
        frames.append(mount_frame)
        await again.disconnect()
    return [_normalise(frame) for frame in frames]


async def test_a_reconnect_sees_the_same_saved_state(monkeypatch):
    before, after = await both(_reconnect_run, monkeypatch)
    assert as_bytes(before) == as_bytes(after)
    assert "Count: 2" in json.dumps(after)


def _break_unchanged_save_skip(patch: Any) -> None:
    from djust import _automatic_state

    # As if every state encoded to what the store already holds.
    real = _automatic_state.project

    def stale(view: Any) -> Any:
        state = real(view)
        state.text = "[{},{},false]"
        return state

    patch.setattr(_automatic_state, "project", stale)


def _break_compact_fingerprints(patch: Any) -> None:
    from djust import change_detection, state_pass

    # As if ``bool`` had been made a bare leaf like ``int``: True == 1.
    wrong = change_detection._BARE | {bool, float}
    patch.setattr(change_detection, "_BARE", wrong)
    patch.setattr(state_pass, "_BARE", wrong)


def _break_shared_fingerprints(patch: Any) -> None:
    from djust.mixins import rust_bridge

    # As if a stored fingerprint were reused without being equal: every key
    # that has one reads as unchanged since the last sync.
    def stale(fp: Any, held: Any, baseline_entry: Any) -> Any:
        if held is not rust_bridge._MISSING:
            return held, True
        return fp, False

    patch.setattr(rust_bridge, "_reuse_fingerprint", stale)


SABOTAGE = {
    "unchanged_save_skip": (_break_unchanged_save_skip, _reconnect_run),
    "contract_cache": (_break_contract_cache, _redeclared_run),
    "snapshot_single_hop": (
        _break_snapshot_single_hop,
        lambda: run_ws(ClientCounter, COUNTER_SCRIPT),
    ),
    "request_scoped_changes": (
        _break_request_scoped_changes,
        lambda: run_ws(Flipping, [("event", "inc"), ("event", "inc")]),
    ),
    "serializable_by_type": (
        _break_serializable_by_type,
        lambda: run_ws(Processors, COUNTER_SCRIPT),
    ),
    "plain_sub_ids": (_break_plain_sub_ids, lambda: run_ws(Opaque, OPAQUE_SCRIPT)),
    "compact_fingerprints": (
        _break_compact_fingerprints,
        lambda: run_ws(ScalarTypes, SCALAR_SCRIPT),
    ),
    "shared_fingerprints": (_break_shared_fingerprints, lambda: run_ws(Derived, DERIVED_SCRIPT)),
}


def test_every_shortcut_has_a_sabotage():
    assert set(SABOTAGE) == set(_per_turn.ALL)


@pytest.mark.parametrize("shortcut", sorted(SABOTAGE))
async def test_a_shortcut_that_served_a_stale_value_fails_the_oracle(monkeypatch, shortcut):
    """Each shortcut, broken so that it reuses or skips what it must not, changes
    a frame one of the cases above compares. A comparison that stayed equal here
    would mean the oracle cannot see that shortcut go wrong."""
    sabotage, run = SABOTAGE[shortcut]
    with harness.shortcuts(monkeypatch):
        FLIPS["n"] = 0
        honest = await observe(run)
        with monkeypatch.context() as patch:
            sabotage(patch)
            FLIPS["n"] = 0
            broken = await observe(run)
    assert as_bytes(honest) != as_bytes(broken), f"breaking {shortcut} changed no frame"
