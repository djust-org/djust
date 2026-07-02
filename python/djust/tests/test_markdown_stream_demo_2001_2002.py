"""Regression tests for #2001 + #2002 — the markdown-stream demo must actually
stream, and ``cancel_async`` must match the real task name.

Both bugs lived in ``examples/demo_project/djust_demos/views/markdown_stream_demo.py``:

#2002 — a sync ``@background`` handler that did ``self.llm_output += ch`` in a
loop did NOT stream. ``_run_async_work`` (``djust/websocket.py``) awaits the
callback to completion and only calls ``render_with_diff()`` AFTER it returns,
so the client saw ONE frame at the end, not one per token. The fix pushes each
token explicitly with ``await self.stream_to(...)``.

#2001 — ``reset()`` called ``cancel_async("md_stream")`` but ``@background``
registered the task under ``func.__name__`` == ``"_stream_chars"``, so the
cancel silently no-op'd (``name in self._async_tasks`` was ``False``). The fix
schedules the task with an explicit ``name="md_stream"`` matching the cancel.

Reproduction fidelity (#1650): the streaming assertions drive a real
``WebsocketCommunicator`` against ``LiveViewConsumer.as_asgi()`` and count the
actual outbound ``type: "stream"`` frames — the SAME path production uses
(``handle_event`` → ``_dispatch_async_work`` → ``_run_async_work`` →
``StreamingMixin.stream_to`` → ``send_json``). No proxy.

Gate-off (#1468 / #1815): ``_PlainMutationView`` reproduces the OLD broken
shape (mutate-only, no ``stream_to``) on the same real path; it must produce
ZERO ``stream`` frames. That is the in-suite sibling proving the ">1 stream op"
assertion actually distinguishes streaming from plain mutation — if the demo
regresses to plain mutation, the streaming test drops to 0 stream frames.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler

_MODULE = __name__


class _StreamStreamsView(LiveView):
    """Mirrors the FIXED demo: async handler that pushes each token via stream_to."""

    template = (
        f'<div dj-view="{_MODULE}._StreamStreamsView" dj-id="0">'
        '<article dj-stream="md_stream" dj-update="ignore" dj-id="1">{{ llm_output }}</article>'
        '<span dj-id="2">{{ streaming }}</span>'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.llm_output = ""
        self.streaming = False

    @event_handler()
    def start_stream(self, **kwargs):
        if self.streaming:
            return
        self.llm_output = ""
        self.streaming = True
        # Explicit name matches reset()'s cancel_async("md_stream") (#2001).
        self.start_async(self._stream_chars, name="md_stream")

    @event_handler()
    def reset(self, **kwargs):
        self.cancel_async("md_stream")
        self.streaming = False
        self.llm_output = ""

    async def _stream_chars(self):
        # Small fixed reply so the test is fast; the demo streams a longer one.
        # Sleep > MIN_STREAM_INTERVAL_S (~16ms) so each op sends immediately
        # instead of being collapsed by the 60fps stream batcher. stream_done
        # is the drain sentinel (mirrors the demo's finally-settle shape).
        await self.stream_start("md_stream")
        try:
            for ch in "abcd":
                if not self.streaming:
                    break
                self.llm_output += ch
                await self.stream_to("md_stream", html=f"<p>{self.llm_output}</p>")
                await asyncio.sleep(0.02)  # yield so reset can interrupt
            self.streaming = False
        finally:
            await self.stream_to("md_stream", html=f"<p>{self.llm_output}</p>")
            await self.stream_done("md_stream")


class _PlainMutationView(LiveView):
    """Gate-off sibling: the OLD broken shape — mutate only, never stream.

    Async so it runs on the same real ``_run_async_work`` path, but with NO
    ``stream_to`` call. Must emit ZERO ``stream`` frames — the whole reply
    arrives in the single end-of-callback render.
    """

    template = (
        f'<div dj-view="{_MODULE}._PlainMutationView" dj-id="0">'
        '<article dj-id="1">{{ llm_output }}</article>'
        "</div>"
    )

    def mount(self, request, **kwargs):
        self.llm_output = ""
        self.streaming = False

    @event_handler()
    def start_stream(self, **kwargs):
        self.llm_output = ""
        self.streaming = True
        self.start_async(self._stream_chars, name="md_stream")

    async def _stream_chars(self):
        # Faithful reproduction of the #2002 bug shape: mutate a public attr in
        # a loop with NO stream_to call. Nothing reaches the client until the
        # single end-of-callback render.
        for ch in "abcd":
            if not self.streaming:
                return
            self.llm_output += ch  # plain mutation — no stream op
            await asyncio.sleep(0.02)
        self.streaming = False


# ── WebsocketCommunicator harness (lifted from test_ws_send_version_1788.py) ──


async def _connect_and_mount(view_suffix, url="/mdstream/"):
    pytest.importorskip("channels")
    from channels.testing import WebsocketCommunicator
    from django.contrib.sessions.backends.db import SessionStore

    from djust.websocket import LiveViewConsumer

    def _create_session():
        s = SessionStore()
        s.create()
        return s.session_key

    session_key = await sync_to_async(_create_session)()

    class _ScopeSession:
        def __init__(self, key):
            self.session_key = key

    communicator = WebsocketCommunicator(LiveViewConsumer.as_asgi(), "/ws/")
    communicator.scope["session"] = _ScopeSession(session_key)

    connected, _ = await communicator.connect()
    assert connected, "WebsocketCommunicator must connect"
    await communicator.receive_json_from(timeout=2)  # drain connect frame

    await communicator.send_json_to(
        {"type": "mount", "view": f"{_MODULE}.{view_suffix}", "url": url}
    )
    # Drain until the mount frame.
    for _ in range(6):
        frame = await communicator.receive_json_from(timeout=3)
        if frame.get("type") == "mount":
            return communicator, frame
    raise AssertionError("never received a mount frame")


def _is_done_op(frame):
    return frame.get("type") == "stream" and frame.get("ops", [{}])[0].get("op") == "done"


async def _drain_until_done(communicator, tries=40, timeout=3):
    """Collect frames until the ``stream_done`` sentinel arrives.

    Stops AT the ``done`` op — never lets ``receive_json_from`` time out, which
    would cancel the app future (asgiref ``receive_output`` cancels on timeout)
    and break a later ``disconnect``. Deterministic + load-independent: each
    receive blocks for the next frame the stream is actively producing.
    """
    frames = []
    for _ in range(tries):
        frame = await communicator.receive_json_from(timeout=timeout)
        frames.append(frame)
        if _is_done_op(frame):
            return frames
    raise AssertionError("never received a stream_done sentinel")


async def _drain_quiet(communicator, settle=0.4):
    """Drain every currently-queued frame WITHOUT a cancelling timeout.

    ``receive_nothing`` polls the output queue and never cancels the app (unlike
    ``receive_output``'s timeout path). Used for the plain-mutation repro, which
    emits NO stream sentinel — sleep long enough for the ~80ms of background
    work to finish, then drain whatever was queued.
    """
    await asyncio.sleep(settle)
    frames = []
    while not await communicator.receive_nothing(timeout=0.05):
        frames.append(await communicator.receive_json_from(timeout=1))
    return frames


_CONTENT_OPS = {"replace", "text", "append", "prepend"}


def _content_stream_ops(frames):
    return [
        f
        for f in frames
        if f.get("type") == "stream" and f.get("ops", [{}])[0].get("op") in _CONTENT_OPS
    ]


# ── #2002: the fixed demo genuinely streams (MORE THAN ONE stream op) ──


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_fixed_pattern_sends_multiple_stream_ops():
    """The explicit-stream_to handler emits one ``stream`` frame per token.

    Drives the real WS path and counts ``type == "stream"`` outbound frames.
    Asserts MORE THAN ONE (the core #2002 claim: mutate-and-return revealed the
    whole reply in a single frame; explicit streaming sends many).
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator, _ = await _connect_and_mount("_StreamStreamsView")
        await communicator.send_json_to(
            {"type": "event", "event": "start_stream", "params": {}, "ref": 1}
        )
        frames = await _drain_until_done(communicator)
        content_ops = _content_stream_ops(frames)
        assert len(content_ops) > 1, (
            "the fixed demo must send MORE THAN ONE content stream op across a "
            f"multi-token run (#2002); got {len(content_ops)}. Frame types seen: "
            f"{[f.get('type') for f in frames]}"
        )
        # Each content stream op targets the dj-stream article.
        for sf in content_ops:
            assert sf["ops"][0]["target"] == "[dj-stream='md_stream']"
        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_plain_mutation_does_not_stream_gate_off():
    """Gate-off sibling: the OLD mutate-only shape emits ZERO stream frames.

    Proves the ">1 stream op" assertion above is non-tautological: revert the
    handler to plain mutation (no ``stream_to``) and the stream-frame count
    drops to 0 — exactly the #2002 bug. The whole reply arrives only in the
    single end-of-callback render frame.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator, _ = await _connect_and_mount("_PlainMutationView")
        await communicator.send_json_to(
            {"type": "event", "event": "start_stream", "params": {}, "ref": 1}
        )
        frames = await _drain_quiet(communicator)
        stream_frames = [f for f in frames if f.get("type") == "stream"]
        assert len(stream_frames) == 0, (
            "plain attribute mutation must NOT produce any stream ops (#2002 root "
            f"cause); got {len(stream_frames)}. If this ever goes >0, the fixed "
            "demo's streaming assertion is no longer distinguishing streaming "
            "from plain mutation."
        )
        # The final output still reaches the client — but via a single render
        # frame (patch/html_update), not incremental stream ops.
        render_frames = [f for f in frames if f.get("type") in ("patch", "html_update")]
        assert render_frames, "the mutate-only handler must still send at least one render"
        await communicator.disconnect()


# ── #2002 point 4: stream target marked dj-update="ignore" is excluded from
#    the main diff, so stream ops and render_with_diff don't both write it ──


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_stream_target_is_dj_update_ignore_excluded_from_main_diff():
    """The stream target carries ``dj-update="ignore"`` so the event-completion
    ``render_with_diff()`` does not re-write the region the stream ops own.

    After the stream completes, ``_run_async_work`` calls ``render_with_diff()``.
    The article is ``dj-update="ignore"``, so the client patcher
    (12-vdom-patch.js:910-911) skips it — the streamed content is authoritative
    and is not duplicated/overwritten by a competing main-render patch.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[_MODULE]):
        communicator, _ = await _connect_and_mount("_StreamStreamsView")
        await communicator.send_json_to(
            {"type": "event", "event": "start_stream", "params": {}, "ref": 1}
        )
        frames = await _drain_until_done(communicator)
        await communicator.disconnect()

    # The stream ops are the only writes carrying the accumulated reply; the
    # article is dj-update="ignore" in the template, verified structurally.
    assert 'dj-update="ignore"' in _StreamStreamsView.template
    assert 'dj-stream="md_stream"' in _StreamStreamsView.template
    content_ops = _content_stream_ops(frames)
    # The last content stream op carries the full accumulated reply.
    assert content_ops, "expected stream ops for the accumulated reply"
    assert content_ops[-1]["ops"][0]["html"].endswith("abcd</p>")


# ── #2001: cancel_async name-match semantics ──


def _mixin_view():
    """Bare AsyncWorkMixin-bearing view for cancel_async unit tests."""
    return LiveView.__new__(LiveView)


def test_cancel_async_mismatched_name_is_a_noop():
    """A name that matches no scheduled task leaves the scheduled task intact.

    This is the exact #2001 failure mode: the old demo scheduled ``_stream_chars``
    but cancelled ``"md_stream"`` — a silent no-op. Documents current framework
    behavior so the demo's name-match fix is meaningful.
    """
    view = _mixin_view()
    sentinel = lambda: None  # noqa: E731 — trivial callable
    view.start_async(sentinel, name="_stream_chars")
    assert "_stream_chars" in view._async_tasks

    # Wrong name — must NOT remove the scheduled task.
    view.cancel_async("md_stream")
    assert "_stream_chars" in view._async_tasks, (
        "cancel_async with a mismatched name must be a no-op on the scheduled "
        "task dict (#2001) — this is why the old demo's cancel silently failed"
    )


def test_cancel_async_matching_name_removes_scheduled_task():
    """The matching name removes the scheduled task (the fixed behavior)."""
    view = _mixin_view()
    sentinel = lambda: None  # noqa: E731
    view.start_async(sentinel, name="md_stream")
    assert "md_stream" in view._async_tasks

    view.cancel_async("md_stream")
    assert "md_stream" not in view._async_tasks
    assert "md_stream" in view._async_cancelled  # running-task re-render suppressed


# ── Source-pin tying the framework tests to the ACTUAL shipped demo ──


def _demo_source():
    repo_root = Path(__file__).resolve().parents[3]
    demo = (
        repo_root
        / "examples"
        / "demo_project"
        / "djust_demos"
        / "views"
        / "markdown_stream_demo.py"
    )
    assert demo.exists(), f"demo view not found at {demo}"
    return demo.read_text()


def test_demo_streams_explicitly_and_names_match():
    """Pin the shipped demo: async handler, explicit stream_to, matched names.

    Mechanically catches a regression back to the two shipped bugs:
    - a sync ``_stream_chars`` with no ``stream_to`` (the non-streaming #2002 bug)
    - a ``cancel_async`` whose name differs from the ``start_async`` name (#2001)
    """
    src = _demo_source()

    assert re.search(r"async def _stream_chars", src), (
        "the demo handler must be async so it streams and is interruptible (#2001/#2002)"
    )
    assert "self.stream_to(" in src, (
        "the demo must push tokens explicitly via stream_to — plain mutation does "
        "not stream (#2002)"
    )
    # The old broken sync-sleep shape must be gone.
    assert "time.sleep" not in src, "the demo must not use blocking time.sleep (#2001)"
    # start_async name and cancel_async name must be the SAME symbol so they
    # cannot drift into the #2001 mismatch.
    assert "name=_STREAM" in src and "cancel_async(_STREAM)" in src, (
        "start_async(name=...) and cancel_async(...) must use the same _STREAM "
        "constant so the cancel can never silently miss the task (#2001)"
    )
    assert re.search(r'_STREAM\s*=\s*"md_stream"', src)
