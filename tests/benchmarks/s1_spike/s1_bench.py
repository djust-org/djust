"""S1 spike: per-phase cost of one WS event turn on an explicit view holding a
1,000-row list in declared state.

Run (from the worktree root):

    .venv/bin/python -m pytest tests/benchmarks/s1_spike/s1_bench.py -q -s -p no:cacheprovider
    S1_SWITCH=1 .venv/bin/python -m pytest tests/benchmarks/s1_spike/s1_bench.py -q -s -p no:cacheprovider

``S1_SWITCH=1`` turns on ``djust._state.ASSIGNMENT_TRACKING`` (the spike's
module-level switch, default off). ``S1_ROWS`` / ``S1_ROUNDS`` change the size.

Measurement only: medians over ``S1_ROUNDS`` rounds after a warm-up. The
phases are timed by wrapping the Python functions named in PHASES; the Rust
render/parse/diff/serialize figures are the extension's own
``get_render_timing()``.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List

import pytest

from djust import LiveView, event_handler
from djust.decorators import state

ROWS = int(os.environ.get("S1_ROWS", "1000"))
ROUNDS = int(os.environ.get("S1_ROUNDS", "200"))
WARMUP = 20
SWITCH = os.environ.get("S1_SWITCH") == "1"
PERSIST = os.environ.get("S1_PERSIST") or None  # "server" to add the session save
PROFILE = os.environ.get("S1_PROFILE") or ""  # a case name: cProfile its sync + render
PROFILERS: Dict[str, Any] = {}
PROFILE_CASE = [""]

TEMPLATE = """<div dj-root dj-view="{view}">
  <h1>{{{{ title }}}}</h1>
  <p class="count">Count: {{{{ count }}}}</p>
  <button dj-click="bump">+</button>
  <table>
    {{% for row in items %}}
    <tr id="row-{{{{ row.id }}}}"><td>{{{{ row.sku }}}}</td><td>{{{{ row.name }}}}</td>
      <td>{{{{ row.price }}}}</td><td>{{{{ row.qty }}}}</td></tr>
    {{% endfor %}}
  </table>
</div>"""

PATH = "/s1-bench/"


def make_items(n: int) -> List[Dict[str, Any]]:
    # Same row shape as tests/benchmarks/test_exposure_cost.py::make_items.
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


class TableView(LiveView):
    exposure_policy = "explicit"
    template = TEMPLATE.format(view=f"{__name__}.TableView")
    title = state("", persist=PERSIST)
    count = state(0, persist=PERSIST)
    items = state(default_factory=list, persist=PERSIST)

    def mount(self, request, **kwargs):
        self.title = "Inventory"
        self.count = 0
        self.items = make_items(ROWS)

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            title=self.title, count=self.count, items=self.items, **kwargs
        )

    @event_handler()
    def bump(self, **kwargs):
        """Case (a): one scalar field."""
        self.count += 1

    @event_handler()
    def edit_row_inplace(self, **kwargs):
        """Case (b), in place: invisible to assignment tracking."""
        self.items[ROWS // 2]["qty"] += 1

    @event_handler()
    def edit_row_reassign(self, **kwargs):
        """Case (b), by assignment: a new list with one new row dict."""
        items = list(self.items)
        row = dict(items[ROWS // 2])
        row["qty"] += 1
        items[ROWS // 2] = row
        self.items = items

    @event_handler()
    def edit_row_touch(self, **kwargs):
        """Case (b), in place plus the spike's explicit ``touch``."""
        self.items[ROWS // 2]["qty"] += 1
        touch = getattr(self, "touch", None)
        if touch is not None:
            touch("items")
        else:
            self.set_changed_keys("items")


# --------------------------------------------------------------------------
# phase timers
# --------------------------------------------------------------------------

ACC: Dict[str, float] = {}
CALLS: Dict[str, int] = {}
RUST: Dict[str, float] = {}


def _wrap(owner: Any, name: str, label: str, after=None):
    orig = getattr(owner, name)

    depth = [0]

    def timed(*args, **kwargs):
        # Only the outermost call is timed: ``_collect_sub_ids`` and
        # ``normalize_django_value`` recurse through the module global, and
        # timing every level would count the same time once per level.
        CALLS[label] = CALLS.get(label, 0) + 1
        if depth[0]:
            return orig(*args, **kwargs)
        depth[0] += 1
        t0 = time.perf_counter()
        try:
            return orig(*args, **kwargs)
        finally:
            ACC[label] = ACC.get(label, 0.0) + (time.perf_counter() - t0)
            depth[0] -= 1
            if after is not None:
                after(*args)

    setattr(owner, name, timed)
    return orig


@contextmanager
def _instrumented() -> Iterator[None]:
    import djust.websocket as ws
    import djust.mixins.rust_bridge as rb
    import djust._exposure_sessions as es

    restore = []

    def w(owner, name, label, after=None):
        if hasattr(owner, name):
            restore.append((owner, name, _wrap(owner, name, label, after)))

    def grab_rust(view, *a):
        timing = getattr(view, "_rust_render_timing", None) or {}
        for k, v in dict(timing).items():
            if isinstance(v, (int, float)):
                RUST[k] = RUST.get(k, 0.0) + float(v)

    bridge_cls = next(c for c in TableView.__mro__ if "_sync_state_to_rust" in c.__dict__)
    render_cls = next(c for c in TableView.__mro__ if "render_with_diff" in c.__dict__)
    w(ws, "_snapshot_assigns", "snapshot_assigns")
    if PROFILE:
        # Profile the sync and the whole render separately (one call each per
        # turn, on whatever thread runs them). Timings are inflated by the
        # profiler; use them for the split, not the absolute figure.
        import cProfile

        for owner, name in ((render_cls, "render_with_diff"),):  # includes the sync
            orig = getattr(owner, name)
            prof = PROFILERS.setdefault(name, cProfile.Profile())

            def profiled(*a, _orig=orig, _prof=prof, **k):
                if PROFILE_CASE[0] != PROFILE:
                    return _orig(*a, **k)
                try:
                    return _prof.runcall(_orig, *a, **k)
                except ValueError:  # another profiler active (nested call)
                    return _orig(*a, **k)

            setattr(owner, name, profiled)
            restore.append((owner, name, orig))
    w(bridge_cls, "_sync_state_to_rust", "sync_total")
    w(rb, "deep_fingerprint", "sync.deep_fingerprint")
    w(rb, "fingerprint_and_convert", "sync.fingerprint_and_convert")
    w(rb, "convert_for_render", "sync.convert_for_render")
    w(rb, "_collect_sub_ids", "sync.collect_sub_ids")
    w(rb, "normalize_django_value", "sync.normalize_django_value")
    w(render_cls, "render_with_diff", "render_with_diff_total", grab_rust)
    w(es, "save_server_state", "save_server_state")
    try:
        yield
    finally:
        for owner, name, orig in reversed(restore):
            setattr(owner, name, orig)


@contextmanager
def _ws_settings() -> Iterator[None]:
    from django.conf import settings
    from django.test import override_settings

    from djust._exposure_sessions import MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S
    from djust.config import config as djust_config

    config = {k: v for k, v in settings.DJUST_CONFIG.items() if not k.startswith("TENANT_")}
    liveview = dict(getattr(settings, "LIVEVIEW_CONFIG", {}))
    liveview["rate_limit"] = {"rate": 10**6, "burst": 10**6, "max_warnings": 10**6}
    try:
        with override_settings(
            LIVEVIEW_ALLOWED_MODULES=[__name__],
            DEBUG=False,
            DJUST_CONFIG=config,
            DJUST_TENANTS={},
            LIVEVIEW_CONFIG=liveview,
            DJUST_EXPLICIT_STATE_SAVE_TIMEOUT=MAX_EXPLICIT_STATE_SAVE_TIMEOUT_S,
        ):
            djust_config.reset()
            yield
    finally:
        djust_config.reset()


async def _connect(session_key: str):
    from channels.testing import WebsocketCommunicator
    from django.contrib.auth.models import AnonymousUser
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    comm = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    comm.scope["session"] = SessionStore(session_key)
    comm.scope["user"] = AnonymousUser()
    connected, _ = await comm.connect()
    assert connected
    await comm.receive_json_from()
    await comm.send_json_to({"type": "mount", "view": f"{__name__}.TableView", "url": PATH})
    frame = await comm.receive_json_from(timeout=30)
    assert frame["type"] == "mount", frame
    return comm, frame


async def _round(comm, event: str) -> Dict[str, Any]:
    await comm.send_json_to({"type": "event", "event": event, "params": {}})
    while True:
        frame = await comm.receive_json_from(timeout=30)
        if frame.get("type") in {"patch", "html_update", "error", "noop"}:
            return frame


def _median_ms(values: List[float]) -> float:
    return statistics.median(values) * 1000.0


CASES = ["bump", "edit_row_reassign", "edit_row_inplace", "edit_row_touch"]


@pytest.mark.django_db(transaction=True)
def test_s1_phase_costs():
    pytest.importorskip("channels")
    import djust._state as _state

    if SWITCH:
        assert hasattr(_state, "ASSIGNMENT_TRACKING"), "prototype switch missing"
        _state.ASSIGNMENT_TRACKING = True
    from django.contrib.sessions.backends.db import SessionStore

    session = SessionStore()
    session.create()
    loop = asyncio.new_event_loop()
    report: Dict[str, Any] = {
        "rows": ROWS,
        "rounds": ROUNDS,
        "switch": SWITCH,
        "persist": PERSIST,
        "cases": {},
    }
    try:
        with _ws_settings():
            comm, mount = loop.run_until_complete(_connect(session.session_key))
            assert f"SKU-{ROWS - 1:05d}" in mount["html"]
            with _instrumented():
                for case in CASES:
                    turn: List[float] = []
                    phases: Dict[str, List[float]] = {}
                    rust: Dict[str, List[float]] = {}
                    calls: Dict[str, int] = {}
                    frames: Dict[str, int] = {}
                    npatches: List[int] = []
                    PROFILE_CASE[0] = ""
                    for i in range(WARMUP + ROUNDS):
                        if i == WARMUP:
                            PROFILE_CASE[0] = case
                        ACC.clear()
                        CALLS.clear()
                        RUST.clear()
                        t0 = time.perf_counter()
                        frame = loop.run_until_complete(_round(comm, case))
                        dt = time.perf_counter() - t0
                        if i < WARMUP:
                            continue
                        turn.append(dt)
                        frames[frame["type"]] = frames.get(frame["type"], 0) + 1
                        if frame["type"] == "patch":
                            patches = frame.get("patches")
                            if isinstance(patches, str):
                                patches = json.loads(patches)
                            npatches.append(len(patches or []))
                        for k, v in ACC.items():
                            phases.setdefault(k, []).append(v)
                        for k, v in RUST.items():
                            rust.setdefault(k, []).append(v)
                        calls = dict(CALLS)
                    report["cases"][case] = {
                        "turn_ms_median": round(_median_ms(turn), 3),
                        "turn_ms_p90": round(sorted(turn)[int(len(turn) * 0.9)] * 1000, 3),
                        "frames": frames,
                        "patches_median": statistics.median(npatches) if npatches else None,
                        "phase_ms_median": {
                            k: round(_median_ms(v), 3) for k, v in sorted(phases.items())
                        },
                        "phase_calls_last_round": calls,
                        "rust_timing_median": {
                            k: round(statistics.median(v), 3) for k, v in sorted(rust.items())
                        },
                    }
            loop.run_until_complete(comm.disconnect())
    finally:
        loop.close()
        if SWITCH:
            _state.ASSIGNMENT_TRACKING = False
    if PROFILE:
        import io
        import pstats

        for name, prof in PROFILERS.items():
            buf = io.StringIO()
            pstats.Stats(prof, stream=buf).sort_stats("tottime").print_stats(22)
            print("\n==== cProfile %s, case %s, %d rounds ====" % (name, PROFILE, ROUNDS))
            print(buf.getvalue())
    out = os.environ.get("S1_OUT")
    text = json.dumps(report, indent=2)
    print("\n" + text)
    if out:
        with open(out, "w") as fh:
            fh.write(text + "\n")
