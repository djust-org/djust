"""Real-``LiveViewConsumer`` net for ``_tick_once`` honoring ``_skip_render`` (#2822).

Before this fix, ``_tick_once`` (``websocket.py:4229``) unconditionally called
``_snapshot_assigns(self.view_instance)`` before AND after ``handle_tick``,
never reading ``_skip_render`` anywhere — unlike the event paths
(``websocket.py`` ``server_push``/``db_notify``, ``runtime.py``
``dispatch_event``), which read the flag right after the handler runs and skip
the render (and, on the runtime path, the second/only ``_snapshot_assigns``
call) entirely. A tick handler that KNOWS it changed nothing (the common
"early return for a non-host session" shape) had no way to avoid paying for
the expensive ``deep_fingerprint`` walk twice per tick.

The actual perf claim in #2822 is the ``_snapshot_assigns`` CALL COUNT, not a
timing number (timing assertions are flaky — see the outlier-sensitivity
canon in CLAUDE.md) — so these tests patch ``djust.websocket._snapshot_assigns``
with a counting wrapper and assert on the count directly, per the issue's own
suggested assertion.

Harness (``_consumer_with_view``) lifted verbatim from
``test_ws_mount_flip_parity_1911.py`` (#1077 lift-from-reference) — that file's
own docstring explains why ``_tick_once`` is driven directly rather than over a
real socket + real timer: waiting on a real ``tick_interval`` timer is a
wall-clock race that failed 2 of 5 runs on an unmodified ``main`` (#2124). The
harness still uses a real, un-mocked ``LiveViewConsumer`` + real ``LiveView``
subclass — only the socket/timer are skipped, not the object under test.
"""

from __future__ import annotations

import pytest

from djust import LiveView

_ALLOWED = "djust.tests.test_tick_skip_render_2822"


def _consumer_with_view(view_class):
    """A ``LiveViewConsumer`` with ``view_class`` mounted and its sends captured.

    Lifted verbatim from ``test_ws_mount_flip_parity_1911.py::_consumer_with_view``.
    """
    from djust.websocket import LiveViewConsumer

    consumer = LiveViewConsumer()
    consumer.scope = {"session": None, "user": None}
    consumer.sent = []

    async def _capture(payload):
        consumer.sent.append(payload)

    consumer.send_json = _capture

    view = view_class()
    view.mount(None)
    # Establish the real mount's diff baseline so these tests exercise patches,
    # rather than the full-HTML fallback used when no baseline is available.
    view.render_with_diff()
    consumer.view_instance = view
    return consumer


def _patch_snapshot_call_counter(monkeypatch):
    """Wrap ``djust.websocket._snapshot_assigns`` with a call counter, returning
    a ``[0]``-style single-element list so the caller can read the live count
    (a plain int can't be mutated through a closure without ``nonlocal`` noise
    at every call site)."""
    import djust.websocket as ws_module

    original = ws_module._snapshot_assigns
    count = [0]

    def counting_snapshot(view):
        count[0] += 1
        return original(view)

    monkeypatch.setattr(ws_module, "_snapshot_assigns", counting_snapshot)
    return count


# ---------------------------------------------------------------------------
# Test views
# ---------------------------------------------------------------------------


class SkipRenderTickView(LiveView):
    """The #2822 non-host-session shape: handle_tick early-returns having set
    ``_skip_render = True`` and touched no public attr."""

    tick_interval = 50
    template = f'<div dj-root dj-view="{_ALLOWED}.SkipRenderTickView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def handle_tick(self):
        self._skip_render = True

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class MutatingTickView(LiveView):
    """Control: handle_tick changes public state and does NOT set
    ``_skip_render`` — must render normally (both snapshots taken)."""

    tick_interval = 50
    template = f'<div dj-root dj-view="{_ALLOWED}.MutatingTickView" dj-id="0">c={{{{ c }}}}</div>'

    def mount(self, request, **kwargs):
        self.c = 0

    def handle_tick(self):
        self.c += 1

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class SkipRenderDespiteMutationTickView(LiveView):
    """``_skip_render`` set ALONGSIDE a real state mutation — mirrors the event
    paths' semantics (server_push/db_notify/runtime dispatch_event all check
    ``_skip_render`` unconditionally, before any change-detection), so an
    explicit skip request wins even though the state did change."""

    tick_interval = 50
    template = (
        f'<div dj-root dj-view="{_ALLOWED}.SkipRenderDespiteMutationTickView" dj-id="0">'
        "c={{ c }}</div>"
    )

    def mount(self, request, **kwargs):
        self.c = 0

    def handle_tick(self):
        self.c += 1
        self._skip_render = True

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class ForceFullHtmlWinsOverSkipRenderTickView(LiveView):
    """Both ``_skip_render`` and ``_force_full_html`` (#1981, via
    ``set_changed_keys()``) set in the same tick — ``_force_full_html`` must
    win: the render must still happen."""

    tick_interval = 50
    template = (
        f'<div dj-root dj-view="{_ALLOWED}.ForceFullHtmlWinsOverSkipRenderTickView" dj-id="0">'
        "c={{ c }}</div>"
    )

    def mount(self, request, **kwargs):
        self.c = 0

    def handle_tick(self):
        self._skip_render = True
        self.set_changed_keys()  # forces _force_full_html = True

    def get_context_data(self, **kwargs):
        return {"c": self.c}


class ConsumedSkipRenderTickView(LiveView):
    """``_skip_render`` set on tick 1 only — tick 2's ``handle_tick`` mutates
    state and does NOT touch ``_skip_render``. Proves the flag is consumed
    (reset to False) rather than leaking into the next tick and silently
    suppressing an unrelated later render."""

    tick_interval = 50
    template = (
        f'<div dj-root dj-view="{_ALLOWED}.ConsumedSkipRenderTickView" dj-id="0">'
        "c={{ c }}</div>"
    )

    def mount(self, request, **kwargs):
        self.c = 0
        self._calls = 0

    def handle_tick(self):
        self._calls += 1
        if self._calls == 1:
            self._skip_render = True
        else:
            self.c += 1

    def get_context_data(self, **kwargs):
        return {"c": self.c}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@pytest.mark.asyncio
class TestTickHonorsSkipRender:
    async def test_skip_render_suppresses_the_tick_frame(self):
        """The observable symptom half of #2822: a tick handler that sets
        _skip_render must not send a patch/tick frame."""
        consumer = _consumer_with_view(SkipRenderTickView)

        sent = await consumer._tick_once()

        assert sent is False, "a tick with _skip_render=True must not send a frame"
        tick_frames = [f for f in consumer.sent if f.get("source") == "tick"]
        assert not tick_frames, (
            "a _skip_render=True tick must not emit a source='tick' frame; got "
            f"{[(f.get('type'), f.get('source')) for f in consumer.sent]}"
        )

    async def test_skip_render_calls_snapshot_assigns_at_most_once(self, monkeypatch):
        """The actual perf claim in #2822: with _skip_render set,
        _snapshot_assigns must be called AT MOST ONCE for the tick (the
        unavoidable pre-handler snapshot), never the pre- AND post- pair.

        Direct verification of the issue's own suggested assertion, rather
        than inferring it from timing (#1795 / #1830 — never gate on
        wall-clock).
        """
        count = _patch_snapshot_call_counter(monkeypatch)
        consumer = _consumer_with_view(SkipRenderTickView)

        await consumer._tick_once()

        assert count[0] == 1, (
            "_snapshot_assigns must be called at most once (pre-handler only) "
            f"when _skip_render is set; was called {count[0]} times"
        )

    async def test_gate_off_mutating_tick_without_skip_render_calls_snapshot_twice(
        self, monkeypatch
    ):
        """GATE-OFF / contrast (#1468): the call-count assertion above is not
        vacuous — a normal state-changing tick (no _skip_render) legitimately
        calls _snapshot_assigns TWICE (pre + post) and DOES send a frame."""
        count = _patch_snapshot_call_counter(monkeypatch)
        consumer = _consumer_with_view(MutatingTickView)

        sent = await consumer._tick_once()

        assert sent is True, "a state-changing tick without _skip_render must render"
        assert count[0] == 2, (
            f"a normal (non-skip) tick must still take both snapshots; was called {count[0]} times"
        )
        tick_frames = [f for f in consumer.sent if f.get("source") == "tick"]
        assert tick_frames, "a state-changing tick must emit a source='tick' frame"

    async def test_skip_render_wins_even_when_state_also_changed(self):
        """_skip_render is an explicit developer directive and must suppress
        the render even though handle_tick ALSO mutated public state — mirrors
        the event paths (server_push/db_notify/runtime dispatch_event), which
        check _skip_render unconditionally before any change-detection."""
        consumer = _consumer_with_view(SkipRenderDespiteMutationTickView)

        sent = await consumer._tick_once()

        assert sent is False, "_skip_render must win over a real state mutation"
        assert consumer.view_instance.c == 1, "the mutation itself must still have happened"
        tick_frames = [f for f in consumer.sent if f.get("source") == "tick"]
        assert not tick_frames

    async def test_force_full_html_wins_over_skip_render(self):
        """#1981's _force_full_html must not be silently dropped by a
        concurrently-set _skip_render — the explicit "render everything"
        request wins."""
        consumer = _consumer_with_view(ForceFullHtmlWinsOverSkipRenderTickView)

        sent = await consumer._tick_once()

        assert sent is True, "_force_full_html must force a render even when _skip_render is set"
        tick_frames = [f for f in consumer.sent if f.get("source") == "tick"]
        assert tick_frames, (
            "_force_full_html must still emit a source='tick' frame when "
            f"_skip_render is also set; got "
            f"{[(f.get('type'), f.get('source')) for f in consumer.sent]}"
        )

    async def test_skip_render_is_consumed_not_leaked_to_the_next_tick(self):
        """GATE-OFF-shaped regression net (#1468): if _skip_render were read
        but never reset, a one-shot skip would silently suppress every
        subsequent tick's render forever. Tick 1 sets the flag and is
        skipped; tick 2 mutates state and does NOT set the flag, and must
        render normally."""
        consumer = _consumer_with_view(ConsumedSkipRenderTickView)

        first_sent = await consumer._tick_once()
        assert first_sent is False, "tick 1 (flag set) must be skipped"

        second_sent = await consumer._tick_once()
        assert second_sent is True, (
            "tick 2 (flag not re-set, state changed) must render — a leaked "
            "_skip_render from tick 1 would wrongly suppress this"
        )
        tick_frames = [f for f in consumer.sent if f.get("source") == "tick"]
        assert tick_frames, "tick 2 must have emitted a source='tick' frame"
        assert consumer.view_instance.c == 1
