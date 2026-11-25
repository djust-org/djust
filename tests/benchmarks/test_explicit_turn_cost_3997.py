"""Complete event turns, explicit exposure with and without the save, on the same views (#3997).

What is timed is one event turn through the real consumer, end to end: the
event frame goes into a ``WebsocketCommunicator``, ``ViewRuntime.dispatch_event``
authorizes, runs the handler, takes the change-detection snapshots, builds the
context, syncs state to Rust, renders, diffs, saves, and the timer stops when
the reply frame with the event's ``ref`` comes back. The same class body is
measured under two settings, each a subclass that differs ONLY in the class
attributes named:

=================  ===========================================================
``explicit``       ``exposure_policy = "explicit"`` as shipped: declared
                   ``state()`` and ``get_context_data()`` reach the template,
                   and the view's state is saved server-side (#4107).
``explicit_nosave`` ``explicit`` with ``automatic_state = False`` (#4128): the
                   exposure work without the automatic save, so the cost can be
                   split into "exposure" and "saving".
``explicit_interval`` ``explicit`` with ``state_save_interval = 60`` (#4164):
                   the save is deferred to the flush (``DJUST_STATE_SAVE_INTERVAL``),
                   so the turn pays the identity check and the change mark,
                   not the write. The timer does not fire within a run; the
                   close flushes.
=================  ===========================================================

Three shapes:

* ``counter`` - one integer and a button.
* ``table500`` - 500 rows of dicts; the event changes one cell of one row.
* ``dashboard`` - 30 context values: 24 scalars, a list of 20 model instances,
  a queryset built in ``get_context_data()``, a dict, and two class-level
  components; the event changes one scalar.

Rounds are interleaved (every setting runs one turn per round, in an order that
rotates), so drift on a loaded machine lands on every setting alike, and the
statistic is the MEDIAN per setting. There is no latency assertion: the repo
does not assert on wall clock (#2156, #1795). Each turn asserts only that it
answered with a patch, and every setting must produce the same frames as
``explicit`` - the comparison is of one view, not of two that render differently.

Run (a RELEASE ``_rust`` build is required; a debug build inverts the result)::

    make benchmark-turn
    # or
    DJUST_TURN_BENCH_ROUNDS=40 nice -n 10 .venv/bin/python -m pytest \\
        tests/benchmarks/test_explicit_turn_cost_3997.py -q -s -p no:cacheprovider

``DJUST_TURN_BENCH_JSON=<path>`` also writes the medians as JSON. Without
``DJUST_TURN_BENCH_ROUNDS`` a short smoke run (3 rounds) checks that every
setting answers and agrees, and prints its (meaningless) numbers.
"""

from __future__ import annotations

import json
import os
import statistics
import time
from typing import Any, Dict, List

import pytest

from djust import LiveView, event_handler
from djust.components.descriptors.base import LiveComponent, TypedState
from djust.decorators import state
from djust.tests import _per_turn_harness as harness

MODULE = __name__
SETTINGS = ("explicit", "explicit_nosave", "explicit_interval")
ROWS = 500
WARMUP = 3

pytestmark = [pytest.mark.benchmark, pytest.mark.django_db(transaction=True)]


# --- the class bodies ------------------------------------------------------------------


class CounterBody(LiveView):
    exposure_policy = "explicit"  # every concrete class below states its own
    template = '<div dj-root><p>Count: {{ count }}</p><button dj-click="inc">+</button></div>'
    count = state(0)

    def _get_context_processors(self):
        return []

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1


def table_rows(n: int) -> List[Dict[str, Any]]:
    return [
        {
            "id": i,
            "sku": f"SKU-{i:05d}",
            "name": f"Catalogue item number {i}",
            "price": round(i * 3.75, 2),
            "qty": i % 7,
            "active": i % 3 != 0,
            "tags": ["alpha", "beta", f"t{i % 5}"],
        }
        for i in range(n)
    ]


class TableBody(LiveView):
    exposure_policy = "explicit"
    template = (
        '<div dj-root><h1>{{ title }}</h1><button dj-click="inc">+</button><table>'
        '{% for row in items %}<tr id="row-{{ row.id }}"><td>{{ row.sku }}</td>'
        "<td>{{ row.name }}</td><td>{{ row.price }}</td><td>{{ row.qty }}</td></tr>"
        "{% endfor %}</table></div>"
    )
    title = state("")
    items = state(default_factory=list)

    def _get_context_processors(self):
        return []

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.items = table_rows(ROWS)

    @event_handler()
    def inc(self, **kwargs):
        self.items[ROWS // 2]["qty"] += 1


class Tabs(LiveComponent):
    class State(TypedState):
        active: str = "overview"

    template = "<nav>{{ active }}</nav>"


class Badge(LiveComponent):
    class State(TypedState):
        label: str = "new"
        n: int = 0

    template = "<em>{{ label }} {{ n }}</em>"


#: 24 scalar fields: strings, ints, floats and bools in turn.
SCALARS = [f"s{i:02d}" for i in range(24)]


def _scalar_default(i: int) -> Any:
    return (f"value {i}", i, i * 1.5, i % 2 == 0)[i % 4]


def _dashboard_template() -> str:
    scalars = "".join("<b>{{ %s }}</b>" % name for name in SCALARS)
    return (
        '<div dj-root><button dj-click="inc">+</button>' + scalars + "{{ tabs }}{{ badge }}"
        "<ul>{% for p in products %}<li>{{ p.name }} {{ p.price }} {{ p.stock }}</li>"
        "{% endfor %}</ul><ol>{% for p in recent %}<li>{{ p.name }}</li>{% endfor %}</ol>"
        "<i>{{ filters.category }} {{ filters.page }}</i></div>"
    )


class DashboardBody(LiveView):
    exposure_policy = "explicit"
    template = _dashboard_template()
    products = state(default_factory=list)
    filters = state(default_factory=dict)
    tabs = Tabs()
    badge = Badge()
    locals().update({name: state(_scalar_default(i)) for i, name in enumerate(SCALARS)})

    def _get_context_processors(self):
        return []

    def mount(self, request, **kwargs):
        from demo_app.models import Product

        self.products = list(Product.objects.order_by("id")[:20])
        self.filters = {"category": "tools", "page": 1, "sort": "name"}

    def get_context_data(self, **kwargs):
        from demo_app.models import Product

        # Added after super(), as an application does.
        context = super().get_context_data(**kwargs)
        context["recent"] = Product.objects.order_by("-id")[:5]
        return context

    @event_handler()
    def inc(self, **kwargs):
        self.s01 += 1


def _concrete(body: type, setting: str) -> type:
    attrs: Dict[str, Any] = {"__module__": MODULE}
    attrs["exposure_policy"] = "explicit"
    attrs["automatic_state"] = setting != "explicit_nosave"
    # 0: every turn saved before its reply; 60: deferred to the flush (#4164).
    attrs["state_save_interval"] = 60 if setting == "explicit_interval" else 0
    name = f"{body.__name__[: -len('Body')]}_{setting}"
    made = type(name, (body,), attrs)
    globals()[name] = made
    return made


SHAPES: Dict[str, Dict[str, type]] = {
    shape: {setting: _concrete(body, setting) for setting in SETTINGS}
    for shape, body in (
        ("counter", CounterBody),
        ("table500", TableBody),
        ("dashboard", DashboardBody),
    )
}


# --- the measurement ---------------------------------------------------------------------


def _seed_products() -> None:
    from decimal import Decimal

    from demo_app.models import Product

    if Product.objects.count() < 20:
        Product.objects.bulk_create(
            Product(name=f"Product {i}", category="tools", price=Decimal(i) + Decimal("0.99"))
            for i in range(20)
        )


def _comparable(frame: Dict[str, Any]) -> Any:
    """What the page receives. An explicit frame also names its view and carries
    ``state_snapshot_signed``; those are envelope, not page content."""
    out = {key: frame.get(key) for key in ("type", "patches", "html")}
    return json.dumps(out, sort_keys=True, default=str)


_REF = [0]


async def _turn(comm: Any) -> Dict[str, Any]:
    """Send one ``inc`` and wait for the frame that answers it.

    Not ``harness.event``: its ``receive_until`` polls every 10 ms, which
    quantizes every turn to the poll interval. This blocks on the socket.
    """
    _REF[0] += 1
    ref = _REF[0]
    await comm.send_json_to({"type": "event", "event": "inc", "params": {}, "ref": ref})
    while True:
        frame: Dict[str, Any] = await comm.receive_json_from(timeout=30)
        if frame.get("ref") == ref and frame.get("type") in harness.REPLY_TYPES:
            return frame


async def _measure(shape: str, rounds: int) -> Dict[str, List[float]]:
    views = SHAPES[shape]
    comms: Dict[str, Any] = {}
    frames: Dict[str, List[str]] = {s: [] for s in SETTINGS}
    times: Dict[str, List[float]] = {s: [] for s in SETTINGS}
    pages: Dict[str, str] = {}
    for setting in SETTINGS:
        key = await harness.new_session_key_async()
        comm, mount = await harness.mount_view(views[setting], key)
        comms[setting] = comm
        pages[setting] = mount["html"].replace(views[setting].__name__, "<view>")
    try:
        for n in range(WARMUP + rounds):
            order = SETTINGS[n % len(SETTINGS) :] + SETTINGS[: n % len(SETTINGS)]
            for setting in order:
                start = time.perf_counter()
                frame = await _turn(comms[setting])
                elapsed = time.perf_counter() - start
                assert frame["type"] == "patch", (shape, setting, frame)
                if n >= WARMUP:
                    times[setting].append(elapsed)
                if n < WARMUP + 2:
                    frames[setting].append(_comparable(frame))
    finally:
        for comm in comms.values():
            await comm.disconnect()
    for setting in SETTINGS[1:]:
        assert pages[setting] == pages["explicit"], (shape, setting, "mount")
        assert frames[setting] == frames["explicit"], (shape, setting)
    return times


def run_all(rounds: int) -> Dict[str, Dict[str, float]]:
    """Medians in milliseconds per shape and setting."""
    from asgiref.sync import async_to_sync

    out: Dict[str, Dict[str, float]] = {}
    with harness.ws_settings(MODULE):
        for shape in SHAPES:
            times = async_to_sync(_measure)(shape, rounds)
            out[shape] = {s: statistics.median(times[s]) * 1000 for s in SETTINGS}
            out[shape]["rounds"] = float(len(times["explicit"]))
    return out


def format_table(medians: Dict[str, Dict[str, float]]) -> str:
    lines = [
        "shape       explicit ms  nosave ms  interval ms  nosave/explicit  interval/explicit"
        "  rounds",
    ]
    for shape, row in medians.items():
        explicit, nosave = row["explicit"], row["explicit_nosave"]
        interval = row["explicit_interval"]
        lines.append(
            f"{shape:<10} {explicit:11.3f}  {nosave:9.3f}  {interval:11.3f}"
            f"  {nosave / explicit:15.2f}  {interval / explicit:17.2f}  {int(row['rounds'])}"
        )
    return "\n".join(lines)


def test_explicit_turn_cost_table():
    _seed_products()
    rounds = int(os.environ.get("DJUST_TURN_BENCH_ROUNDS", "3"))
    medians = run_all(rounds)
    print("\n" + format_table(medians))
    target = os.environ.get("DJUST_TURN_BENCH_JSON")
    if target:
        with open(target, "w") as handle:
            json.dump(medians, handle, indent=2, sort_keys=True)
    assert set(medians) == set(SHAPES)
