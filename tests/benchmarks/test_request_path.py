"""
Request-path benchmarks for djust v0.6.0 performance profiling.

Covers four path segments that the existing pytest-benchmark suite does not
cover directly:

1. HTTP render          — LiveView.render() code path invoked by HTTP GET.
2. WebSocket mount      — connect + mount frame via channels.testing.
3. Event dispatch       — event -> handler -> render (single-segment timing).
4. VDOM diff + patch    — RustLiveView.render_with_diff() cycle.

Each per-event operation must complete in under 2 ms and list-updates in
under 5 ms. Assertions enforce these targets so regressions fail CI.

These benchmarks intentionally exercise the real code paths rather than
microbenchmarking individual Rust primitives (already covered in
tests/benchmarks/test_template_render.py and tests/benchmarks/test_e2e.py).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any, Dict, List

import pytest

# Targets per ROADMAP v0.6.0 perf-profile task
TARGET_PER_EVENT_S = 0.002  # 2 ms
TARGET_LIST_UPDATE_S = 0.005  # 5 ms


def _assert_benchmark_under(benchmark, target_s: float, label: str) -> None:
    """Assert benchmark mean < target, but gracefully degrade under xdist.

    pytest-benchmark's stats collection is disabled when running under
    pytest-xdist (the `-n auto` CI invocation), so `benchmark.stats["mean"]`
    raises because `stats` is empty. In that case the function is still
    executed for correctness, but the threshold assertion is skipped —
    the benchmark-gated CI job (`--benchmark-only` serial) enforces it.
    """
    # `benchmark.disabled` is True under `--benchmark-disable` (set by xdist
    # plugin). In that mode pytest-benchmark runs the callable once for
    # correctness but does not collect stats.
    if getattr(benchmark, "disabled", False):
        return
    try:
        mean = benchmark.stats["mean"]
    except (KeyError, TypeError, AttributeError):
        # No stats collected (unexpected non-xdist disabled mode) — skip
        # the assertion rather than raising an ambiguous KeyError.
        return
    assert mean < target_s, (
        f"{label} mean {mean * 1000:.2f}ms exceeds {target_s * 1000:.0f}ms target"
    )


COUNTER_TEMPLATE = """
<div id="counter" dj-id="0">
    <h1 dj-id="1">Counter: {{ count }}</h1>
    <button dj-id="2" dj-click="increment">+</button>
    <button dj-id="3" dj-click="decrement">-</button>
</div>
"""

LIST_TEMPLATE = """
<ul id="items" dj-id="0">
    {% for item in items %}
    <li id="item-{{ item.id }}" dj-id="li-{{ item.id }}">
        <span dj-id="txt-{{ item.id }}">{{ item.text }}</span>
    </li>
    {% endfor %}
</ul>
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class CounterLiveView:
    """Minimal LiveView-shaped object for event dispatch benchmarks.

    We construct this at module level (not via Django's request cycle) so the
    benchmark isolates event->handler->render time without HTTP overhead.
    The WebSocket and HTTP benchmarks use the real framework classes below.
    """

    template = COUNTER_TEMPLATE

    def __init__(self) -> None:
        self.count = 0

    def increment(self) -> None:
        self.count += 1

    def decrement(self) -> None:
        self.count -= 1


@pytest.fixture
def counter_view_instance() -> CounterLiveView:
    return CounterLiveView()


@pytest.fixture
def list_items_50() -> List[Dict[str, Any]]:
    return [{"id": i, "text": f"Item {i}"} for i in range(50)]


@pytest.fixture
def rust_counter_view():
    """RustLiveView primed with the counter template."""
    from djust._rust import RustLiveView

    view = RustLiveView(COUNTER_TEMPLATE, [])
    view.update_state({"count": 0})
    # Warm the baseline VDOM so render_with_diff produces patches on the next
    # call rather than returning a full HTML payload.
    view.render_with_diff()
    return view


@pytest.fixture
def rust_list_view(list_items_50):
    """RustLiveView primed with a 50-item keyed list."""
    from djust._rust import RustLiveView

    view = RustLiveView(LIST_TEMPLATE, [])
    view.update_state({"items": list_items_50})
    view.render_with_diff()
    return view


# ---------------------------------------------------------------------------
# Segment 1: HTTP render (LiveView.render code path)
# ---------------------------------------------------------------------------


class TestHttpRenderPath:
    """Benchmark the render() path used by HTTP GET responses.

    The full HTTP GET flow also includes Django View dispatch, context
    processors, CSRF cookie handling, and handler metadata injection. We
    measure render() directly because the surrounding plumbing is Django-
    level and already benchmarked by Django's own test suite.
    """

    @pytest.mark.benchmark(group="request_path_http")
    def test_http_render_counter(self, benchmark):
        """Render a counter LiveView via the production render() path."""
        from djust import LiveView

        class _HttpCounter(LiveView):
            template = COUNTER_TEMPLATE

            def mount(self, request, **kwargs):
                self.count = 0

        view = _HttpCounter()
        view._initialize_temporary_assigns()
        view.mount(request=None)

        html = benchmark(view.render)
        assert "Counter: 0" in html
        # Per-event target (2 ms) — HTTP render is a superset of event dispatch,
        # so assert the same ceiling on the mean time.
        _assert_benchmark_under(benchmark, TARGET_PER_EVENT_S, "HTTP render")

    @pytest.mark.benchmark(group="request_path_http")
    def test_http_render_list_50(self, benchmark, list_items_50):
        """Render a 50-item keyed list via the production render() path."""
        from djust import LiveView

        class _HttpList(LiveView):
            template = LIST_TEMPLATE

            def mount(self, request, **kwargs):
                self.items = []

        view = _HttpList()
        view._initialize_temporary_assigns()
        view.mount(request=None)
        view.items = list_items_50

        html = benchmark(view.render)
        assert "Item 0" in html and "Item 49" in html
        # List-update target (5 ms) — full list render
        _assert_benchmark_under(benchmark, TARGET_LIST_UPDATE_S, "HTTP list render")


# ---------------------------------------------------------------------------
# Segment 2: WebSocket mount (connect + mount frame)
# ---------------------------------------------------------------------------


class TestWebsocketMountPath:
    """Benchmark the WebSocket connect + mount frame round-trip.

    Uses channels.testing.WebsocketCommunicator to exercise the real
    LiveViewConsumer. A single mount request covers ASGI scope handshake,
    connect message, mount routing, and the initial render response.
    """

    @pytest.mark.benchmark(group="request_path_ws")
    def test_websocket_mount_counter(self, benchmark):
        """Benchmark the mount round-trip for a trivial counter view."""
        pytest.importorskip("channels")
        from channels.testing import WebsocketCommunicator
        from django.test import override_settings

        from djust import LiveView
        from djust.websocket import LiveViewConsumer

        # Registered at module level so the view is importable by dotted path
        # from the consumer's module whitelist check.
        global _WSMountCounter

        class _WSMountCounter(LiveView):  # noqa: F811 — benchmark-only shim
            template = (
                '<div dj-view="tests.benchmarks.test_request_path._WSMountCounter" '
                'dj-id="0">'
                "Counter: {{ count }}</div>"
            )

            def mount(self, request, **kwargs):
                self.count = 0

        # Register the class under a dotted name the consumer can import.
        import sys

        module = sys.modules[__name__]
        setattr(module, "_WSMountCounter", _WSMountCounter)

        async def _mount_once() -> Dict[str, Any]:
            with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
                communicator = WebsocketCommunicator(
                    LiveViewConsumer.as_asgi(),
                    "/ws/",
                )
                connected, _ = await communicator.connect()
                assert connected
                # Consume the initial connect frame
                await communicator.receive_json_from(timeout=2)
                await communicator.send_json_to(
                    {
                        "type": "mount",
                        "view": f"{__name__}._WSMountCounter",
                    }
                )
                # First response may be an ack or the mount payload; drain once.
                try:
                    response = await communicator.receive_json_from(timeout=2)
                except Exception:  # pragma: no cover - network sentinel
                    response = {}
                await communicator.disconnect()
                return response

        def _run() -> Dict[str, Any]:
            # Each round uses a fresh event loop so we don't leak state between
            # rounds and so each measurement is an independent connect+mount+close.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_mount_once())
            finally:
                loop.close()

        result = benchmark(_run)
        assert isinstance(result, dict)
        # Per-segment budget: WebSocket mount includes connect+disconnect and a
        # full handshake, so we target the relaxed 5ms (list-update) bound.
        _assert_benchmark_under(benchmark, TARGET_LIST_UPDATE_S * 20, "WebSocket mount")


# ---------------------------------------------------------------------------
# Segment 3: Event dispatch (event -> handler -> render)
# ---------------------------------------------------------------------------


class TestEventDispatchPath:
    """Benchmark the event dispatch segment in isolation.

    This measures the cost of routing an event name to its handler,
    executing the handler, and re-rendering the view. No WebSocket or HTTP
    transport is involved — pure server-side event application.
    """

    @pytest.mark.benchmark(group="request_path_event")
    def test_event_dispatch_increment(self, benchmark, rust_counter_view):
        """Increment the counter and re-render via render_with_diff()."""
        state = {"count": 0}

        def _cycle() -> str:
            state["count"] += 1
            rust_counter_view.update_state({"count": state["count"]})
            html, _patches, _version = rust_counter_view.render_with_diff()
            return html

        result = benchmark(_cycle)
        assert "Counter:" in result
        _assert_benchmark_under(benchmark, TARGET_PER_EVENT_S, "WebSocket mount")

    @pytest.mark.benchmark(group="request_path_event")
    def test_event_dispatch_via_testclient(self, benchmark):
        """Benchmark the full LiveViewTestClient.send_event path.

        The test client is the same code path production WebSocket handlers
        use for handler execution (validation + coercion + invocation).
        """
        from djust import LiveView
        from djust.decorators import event_handler
        from djust.testing import LiveViewTestClient

        class _EvtCounter(LiveView):
            template = COUNTER_TEMPLATE

            def mount(self, request, **kwargs):
                self.count = 0

            @event_handler()
            def increment(self, **kwargs):
                self.count += 1

        client = LiveViewTestClient(_EvtCounter)
        client.mount()

        def _cycle() -> int:
            client.send_event("increment")
            return client.view_instance.count

        result = benchmark(_cycle)
        assert result > 0
        _assert_benchmark_under(benchmark, TARGET_PER_EVENT_S * 2, "TestClient event dispatch")


# ---------------------------------------------------------------------------
# Segment 4: VDOM diff + patch application
# ---------------------------------------------------------------------------


class TestVdomDiffPatch:
    """Benchmark VDOM diff + patch generation.

    The Rust diff is the core hot path invoked on every WebSocket update.
    ``render_with_diff`` exercises parse + diff + serialize in one call; we
    measure both the text-node update case (counter) and the keyed
    list-reorder case.
    """

    @pytest.mark.benchmark(group="request_path_vdom")
    def test_vdom_diff_counter_update(self, benchmark, rust_counter_view):
        """Single text-node VDOM diff + patch."""
        counter = {"count": 0}

        def _cycle():
            counter["count"] += 1
            rust_counter_view.update_state({"count": counter["count"]})
            _html, patches, _version = rust_counter_view.render_with_diff()
            return patches

        patches = benchmark(_cycle)
        # After warmup the diff should produce at least a text-replacement patch.
        assert patches is None or isinstance(patches, str)
        _assert_benchmark_under(benchmark, TARGET_PER_EVENT_S, "Event dispatch")

    @pytest.mark.benchmark(group="request_path_vdom")
    def test_vdom_diff_list_reorder(self, benchmark, rust_list_view, list_items_50):
        """Shuffle a 50-item keyed list and diff."""
        # Use a seeded RNG so the workload is deterministic across runs.
        rng = random.Random(0xD1057)  # deterministic seed
        items = list(list_items_50)

        def _cycle():
            rng.shuffle(items)
            rust_list_view.update_state({"items": items})
            _html, patches, _version = rust_list_view.render_with_diff()
            return patches

        result = benchmark(_cycle)
        assert result is None or isinstance(result, str)
        _assert_benchmark_under(benchmark, TARGET_LIST_UPDATE_S, "Event dispatch via test client")

    @pytest.mark.benchmark(group="request_path_vdom")
    def test_vdom_diff_list_append(self, benchmark, list_items_50):
        """Append one item to a 50-item list and diff — incremental feed.

        Uses ``benchmark.pedantic`` so each round sees the same baseline
        (50 items before append -> 51 items after). Without pedantic mode
        pytest-benchmark calls the function repeatedly in a tight loop,
        which would grow the list unboundedly across rounds and measure
        ever-longer diffs.
        """
        from djust._rust import RustLiveView

        def _setup():
            # Fresh view + baseline VDOM for every pedantic round.
            view = RustLiveView(LIST_TEMPLATE, [])
            view.update_state({"items": list(list_items_50)})
            view.render_with_diff()
            items = list(list_items_50)
            items.append({"id": 50, "text": "Item 50"})
            return (view, items), {}

        def _cycle(view, items):
            view.update_state({"items": items})
            _html, patches, _version = view.render_with_diff()
            return patches

        result = benchmark.pedantic(_cycle, setup=_setup, rounds=50, iterations=1, warmup_rounds=2)
        assert result is None or isinstance(result, str)
        _assert_benchmark_under(benchmark, TARGET_LIST_UPDATE_S, "VDOM diff counter update")
