"""Save frequency, step 1: the seam where a socket turn's save is decided.

Every explicit turn over WebSocket and SSE saves through
``ViewRuntime.commit_explicit_turn``, which asks ``_save_policy.save_mode``
how. By default (no interval set) the answer is ``immediate``. These tests pin

* the set of call sites of ``commit_explicit_turn`` and that only it (and the
  flush) calls ``_save_explicit_root`` (derived from the source, so a new save path that
  bypasses the decision fails here);
* that both socket transports say they hold the view, and a transport that
  does not say is taken to hold nothing (its turns are never deferred);
* that an event turn asks the decision once, with its transport, over
  WebSocket and over SSE alike.
"""

from __future__ import annotations

import ast
import pathlib

import pytest
from django.urls import path as _path

import djust
from djust import LiveView, _save_policy
from djust.decorators import event_handler
from djust.runtime import SSESessionTransport, Transport, WSConsumerTransport
from djust.tests import _per_turn_harness as harness
from djust.tests import _save_frequency as sf

MODULE = __name__
PACKAGE = pathlib.Path(djust.__file__).parent

#: Where a turn's state reaches storage, by enclosing function. A new entry is
#: a new save path: decide its save mode in ``_save_policy`` before adding it.
COMMIT_CALL_SITES = frozenset(
    {
        ("runtime.py", "ViewRuntime._dispatch_event_render"),
        ("runtime.py", "ViewRuntime._dispatch_single_event_body"),
        ("runtime.py", "ViewRuntime._dispatch_component_event"),
        ("runtime.py", "ViewRuntime._dispatch_url_change_inner"),
        ("runtime.py", "ViewRuntime._schedule_explicit_catch_up.catch_up.complete"),
        ("runtime.py", "ViewRuntime._execute_explicit_async_task.complete"),
        ("runtime.py", "ViewRuntime._server_push_body"),
        ("runtime.py", "ViewRuntime._tick_body"),
        ("websocket.py", "LiveViewConsumer._dispatch_single_event_body"),
        # The commit of a consumer-owned background turn (tick, NOTIFY, a
        # push), shared by ``_render_background`` and the worker pool's
        # one-hop push turn (#4152).
        ("websocket.py", "LiveViewConsumer._commit_explicit_background_turn"),
    }
)


def _callers(attribute: str) -> list:
    """``(file, enclosing qualname)`` of every ``x.<attribute>(...)`` in the package."""
    found = []
    for source in sorted(PACKAGE.rglob("*.py")):
        if "tests" in source.relative_to(PACKAGE).parts:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"))
        stack: list = []

        def visit(node: ast.AST) -> None:
            scoped = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            if scoped:
                stack.append(node.name)
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attribute
            ):
                found.append((str(source.relative_to(PACKAGE)), ".".join(stack)))
            for child in ast.iter_child_nodes(node):
                visit(child)
            if scoped:
                stack.pop()

        visit(tree)
    return found


def test_the_commit_call_sites_are_the_pinned_set():
    sites = _callers("commit_explicit_turn")
    assert len(sites) == len(set(sites)), "one call per function: %r" % sorted(sites)
    assert set(sites) == COMMIT_CALL_SITES


def test_only_the_commit_and_the_flush_call_the_root_save():
    """The commit (an immediate turn) and the flush (a deferred one's write,
    step 3) are the only writers of a socket view's state."""
    assert sorted(_callers("_save_explicit_root")) == [
        ("runtime.py", "ViewRuntime._flush_locked"),
        ("runtime.py", "ViewRuntime.commit_explicit_turn"),
    ]


def test_both_socket_transports_hold_the_view():
    assert WSConsumerTransport.holds_view is True
    assert SSESessionTransport.holds_view is True
    assert _save_policy.transport_holds_view(WSConsumerTransport(object()))


def test_a_transport_that_does_not_say_holds_nothing():
    class Partial:
        pass

    assert _save_policy.transport_holds_view(Partial()) is False
    # Truthy is not enough: only True says so.
    Partial.holds_view = 1
    assert _save_policy.transport_holds_view(Partial()) is False
    # The protocol's own default.
    assert Transport.holds_view.fget(object()) is False  # type: ignore[attr-defined]


def test_every_answer_is_immediate_by_default():
    """With ``DJUST_STATE_SAVE_INTERVAL`` unset, whatever the view and turn."""
    for transport in (WSConsumerTransport(object()), object()):
        for source in ("event", "url_change", "async"):
            assert _save_policy.save_mode(object(), transport, source=source) == "immediate"
    assert _save_policy.MODES == {"immediate", "deferred"}


class SeamCounter(LiveView):
    exposure_policy = "explicit"
    template = "<div dj-root>n={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def inc(self, **kwargs):
        self.count += 1

    def get_context_data(self, **kwargs):
        return {"count": self.count}


urlpatterns = [_path(sf.PATH.strip("/") + "/", SeamCounter.as_view())]


@pytest.fixture
def asked(monkeypatch):
    calls = []
    real = _save_policy.save_mode

    def recording(view, transport, *, source, **kw):
        answer = real(view, transport, source=source, **kw)
        calls.append((type(view).__name__, type(transport).__name__, source, answer))
        return answer

    monkeypatch.setattr(_save_policy, "save_mode", recording)
    sf.one_tab(monkeypatch)
    return calls


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_a_websocket_event_asks_once_and_still_saves_before_its_frame(asked):
    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE):
        await sf.ws_run(SeamCounter, key, [("inc", None)])
    assert asked == [("SeamCounter", "WSConsumerTransport", "event", "immediate")]
    from asgiref.sync import sync_to_async

    envelope = await sync_to_async(sf.only_envelope)(key)
    assert envelope["values"]["count"] == 1


@pytest.mark.asyncio
@pytest.mark.django_db(transaction=True)
async def test_an_sse_event_asks_the_same_question(asked):
    from asgiref.sync import sync_to_async

    key = await harness.new_session_key_async()
    with harness.ws_settings(MODULE, ROOT_URLCONF=MODULE):
        await sf.sse_run(SeamCounter, key, [("inc", None)], MODULE)
    assert asked == [("SeamCounter", "SSESessionTransport", "event", "immediate")]
    envelope = await sync_to_async(sf.only_envelope)(key)
    assert envelope["values"]["count"] == 1
