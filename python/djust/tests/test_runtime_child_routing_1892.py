"""Direct-runtime tests for the embedded-child routing subsystems
(ADR-022 Iter 2 Phase 2.1, issue #1892).

Phase 2.1 ports the three transport-agnostic child-routing subsystems the WS
``_handle_event_inner`` has into :class:`djust.runtime.ViewRuntime` so the
runtime's ``dispatch_event`` spine routes:

* ``view_id`` → a sticky/embedded child LiveView (scoped ``embedded_update``);
* ``component_id`` → a child LiveComponent (full-HTML ``html_update`` /
  ``component_event``); per #1467 the handler runs on the COMPONENT while the
  frame is scoped to the parent (``target_view`` is NOT reassigned);
* the embedded child's template render (single-sourced via
  :func:`djust.websocket.render_embedded_child_html`).

These tests drive ``runtime.dispatch_event`` DIRECTLY against a MockTransport
(no WS consumer, no SSE session) — proving the runtime gained the routing
behavior independently of any transport. WS routing is unchanged (Phase 2.1
ADDs the runtime copy; Phase 2.3 deletes the WS copy), so the existing WS
child-routing tests still own the WS path.

Each subsystem has a **reproduce-first / gate-off** pair (#1468): a positive
test that fails without the runtime handling, and a gate-off test proving the
security-critical guard (component-handler validation, view_id
log-sanitization, embedded-error escape) is load-bearing.
"""

from __future__ import annotations

import contextlib
import uuid
from typing import Any, Dict, List, Optional

import django
import pytest
from django.conf import settings

# Configure Django before importing anything that needs settings.
if not settings.configured:
    settings.configure(
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
            "django.contrib.sessions",
        ],
        SECRET_KEY="test-secret-key-runtime-child-routing",
        SESSION_ENGINE="django.contrib.sessions.backends.db",
        USE_TZ=True,
        DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
        TEMPLATES=[
            {
                "BACKEND": "django.template.backends.django.DjangoTemplates",
                "DIRS": [],
                "APP_DIRS": False,
                "OPTIONS": {"builtins": ["djust.templatetags.live_tags"]},
            }
        ],
    )
    django.setup()

from djust import LiveView  # noqa: E402
from djust.components.base import LiveComponent  # noqa: E402
from djust.decorators import event_handler, state  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402
from djust._parameter_metadata import expand_parameter_manifest


# ------------------------------------------------------------------ #
# Test transport — captures send() calls (mirrors test_runtime.py)
# ------------------------------------------------------------------ #


class MockTransport:
    """Minimal Transport implementation that records all outbound frames."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self._client_ip: Optional[str] = None
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return self._client_ip

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        # SSE-style: pass the Rust version straight through (the runtime's
        # transport-blind default; WS overrides with its consumer-owned counter).
        return rust_version

    def build_request(self) -> Optional[Any]:
        return None

    def on_view_mounted(self, view_instance: Any) -> None:
        pass

    @contextlib.asynccontextmanager
    async def event_context(self, view: Any):
        """No-op event context (#1899): the mock has no consumer lock to borrow —
        the runtime wraps the event handler+render in ``transport.event_context``,
        so the mock must provide one."""
        yield


def _frame_types(transport: MockTransport) -> List[str]:
    return [f.get("type") for f in transport.sent]


# ------------------------------------------------------------------ #
# Fixtures — real LiveViews + a real LiveComponent
# ------------------------------------------------------------------ #


class _StickyChildView(LiveView):
    """A sticky child whose ``bump`` handler mutates state and re-renders."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "child"
    template = "<div>count={{ count }}</div>"
    count = state(0)

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, amount: int = 1, **kwargs):
        self.count += amount


class _BrokenChildView(LiveView):
    """A child whose template render raises with an attacker-controlled-shaped
    message so the embedded-error escape/DEBUG-gate can be exercised."""

    exposure_policy = "explicit"
    sticky = True
    sticky_id = "broken"
    count = state(0)

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_template(self):  # noqa: D401 - test hook
        raise ValueError("boom --><script>alert(1)</script>")


class _ParentView(LiveView):
    """A parent page that hosts both a sticky child and a LiveComponent."""

    exposure_policy = "explicit"
    template = '<div dj-root dj-id="0"><h1>parent={{ parent_count }}</h1></div>'
    parent_count = state(0)

    def mount(self, request, **kwargs):
        self.parent_count = 0


class _ParentWithChild(_ParentView):
    """The parent that renders the sticky child through ``{% live_render %}``.

    An explicit child is authorized by the mount that rendered it, so the
    child is mounted by the parent's own template, not registered by hand.
    """

    exposure_policy = "explicit"
    template = (
        '<div dj-root dj-id="0"><h1>parent={{ parent_count }}</h1>{% load live_tags %}'
        '{% live_render "djust.tests.test_runtime_child_routing_1892._StickyChildView" sticky=True %}</div>'
    )


class _ClickComponent(LiveComponent):
    """Component with a state-mutating ``item_clicked`` handler."""

    def __init__(self, component_id: str) -> None:
        super().__init__(component_id=component_id)
        self.last_click: Optional[Dict[str, Any]] = None

    @event_handler()
    def item_clicked(self, item_id: int = 0, **kwargs) -> None:
        self.last_click = {"item_id": item_id, **kwargs}


def _make_runtime_with_view(view: LiveView) -> tuple[ViewRuntime, MockTransport]:
    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime.view_instance = view
    return runtime, transport


async def _mounted_runtime(view: LiveView) -> tuple[ViewRuntime, MockTransport]:
    """A runtime that really mounted ``view`` on a saved session (explicit turns need an owner)."""
    from djust.tests._live_session import amount_on_runtime

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    await amount_on_runtime(runtime, view, url="/x/")
    return runtime, transport


def _make_sticky_parent() -> tuple[_ParentView, _StickyChildView]:
    """Hand-registered parent and child, for tests that never dispatch an explicit turn."""
    parent = _ParentView()
    parent.mount(None)
    child = _StickyChildView()
    child.mount(None)
    parent._register_child("child-1", child)
    return parent, child


async def _mounted_sticky_parent() -> tuple[
    _ParentView, _StickyChildView, ViewRuntime, MockTransport
]:
    """Mount the parent on a runtime; its template mounts the sticky child."""
    parent = _ParentWithChild()
    runtime, transport = await _mounted_runtime(parent)
    child = parent._get_child_view("child")
    assert isinstance(child, _StickyChildView)
    return parent, child, runtime, transport


# ------------------------------------------------------------------ #
# Subsystem 1 — view_id sticky-child routing
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
class TestRuntimeStickyChildRouting:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("removed", [False, True])
    async def test_background_work_belongs_to_selected_child(self, monkeypatch, removed):
        import asyncio

        parent, child, runtime, transport = await _mounted_sticky_parent()
        runtime._parameter_contract_view = "tests.Parent"
        release = asyncio.Event()
        started = asyncio.Event()
        results = []
        parent.start_async(lambda: results.append("parent"), name="parent")

        @event_handler
        def begin(self):
            async def work():
                started.set()
                await release.wait()
                return 9

            self.start_async(work, name="child")

        def handle_result(self, name, result=None, error=None):
            results.append(name)
            self.count = result

        monkeypatch.setattr(_StickyChildView, "begin", begin, raising=False)
        monkeypatch.setattr(_StickyChildView, "handle_async_result", handle_result, raising=False)
        try:
            await runtime.dispatch_event(
                {"type": "event", "event": "begin", "params": {"view_id": "child"}, "ref": 12}
            )
            ack = next(f for f in transport.sent if f.get("ref") == 12)
            assert ack["async_pending"] is True
            token = ack["async_batch"]
            await asyncio.wait_for(started.wait(), 1)
            assert results == []
            assert "parent" in parent._async_tasks
            if removed:
                monkeypatch.setattr(parent, "_child_views", {})
            release.set()
            for _ in range(100):
                if any(f["type"] == "async_complete" for f in transport.sent):
                    break
                await asyncio.sleep(0.01)
            assert {"type": "async_complete", "async_batch": token} in transport.sent
            updates = [f for f in transport.sent if f.get("source") == "async"]
            if removed:
                assert results == []
                # An explicit owner reports the lost child value-free and renders nothing.
                assert [u["code"] for u in updates] == ["async_error"]
                assert all(u["type"] != "embedded_update" for u in updates)
            else:
                assert results == ["child"]
                assert len(updates) == 1
                assert updates[0]["type"] == "embedded_update"
                assert updates[0]["view_id"] == "child"
                assert "count=9" in updates[0]["html"]
                owner = next(
                    owner
                    for owner in expand_parameter_manifest(updates[0]["parameter_contracts"])
                    if owner["view_id"] == "child"
                )
                assert owner["handlers"]["begin"]["policy"] == "strict"
                assert updates[0]["parameter_contract_view"] == "tests.Parent"
        finally:
            release.set()
            handles = tuple(getattr(child, "_async_task_handles", ()))
            if handles:
                await asyncio.wait_for(asyncio.gather(*handles, return_exceptions=True), 3)

    @pytest.mark.asyncio
    async def test_view_id_routes_to_child_and_emits_embedded_update(self):
        """A ``view_id``-targeted event mutates the CHILD and emits a scoped
        ``embedded_update`` frame — NOT a top-level patch/html frame.

        Reproduce-first: without the runtime's sticky-child routing the event
        would resolve a handler on the PARENT (which has none), so this would
        emit an error / mis-route rather than an embedded_update.
        """
        parent, child, runtime, transport = await _mounted_sticky_parent()

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": "child", "amount": 3},
                "ref": 7,
            }
        )

        # The CHILD's state mutated, the PARENT's did not (target not reassigned
        # to parent; per-child target).
        assert child.count == 3
        assert parent.parent_count == 0

        assert "embedded_update" in _frame_types(transport), _frame_types(transport)
        frame = next(f for f in transport.sent if f.get("type") == "embedded_update")
        assert frame["view_id"] == "child"
        assert frame["event_name"] == "bump"
        assert "count=3" in frame["html"]
        assert frame["ref"] == 7  # #560 ref echo
        # No top-level patch/html_update leaked for a child-scoped event.
        assert "patch" not in _frame_types(transport)
        assert "html_update" not in _frame_types(transport)

    @pytest.mark.asyncio
    async def test_unknown_view_id_errors_without_echoing_id(self):
        """A bogus ``view_id`` returns an ``Embedded view not found`` error and
        NEVER echoes the client-supplied id into the user-facing error string
        (security: ``sanitize_for_log`` only, in the structured ``extra``)."""
        parent, _child, runtime, transport = await _mounted_sticky_parent()

        payload = "<script>alert(1)</script>"
        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": payload},
            }
        )

        assert len(transport.errors) == 1, transport.sent
        err = transport.errors[0]
        # The user-facing error string is the generic message — the raw,
        # attacker-controlled id is NOT interpolated into it.
        assert err["error"] == "Embedded view not found"
        assert payload not in err["error"]

    @pytest.mark.asyncio
    async def test_gate_off_view_id_not_sanitized_would_leak(self):
        """GATE-OFF (#1468): the view_id is delivered ONLY via the structured
        ``extra`` (sanitized), never the message body. This pins that the
        log-sanitization boundary is the contract — if a future edit moved the
        raw id into ``error=`` the previous test would go red. Here we assert
        the positive shape that makes the security guard load-bearing.
        """
        parent, _child, runtime, transport = await _mounted_sticky_parent()

        await runtime.dispatch_event(
            {"type": "event", "event": "bump", "params": {"view_id": "nope-99"}}
        )

        err = transport.errors[0]
        # The error envelope's user-facing field must not carry the raw id;
        # the id only travels in the sanitized structured ``extra``.
        assert "nope-99" not in err["error"]
        assert err.get("extra", {}).get("view_id") == "nope-99"


# ------------------------------------------------------------------ #
# Subsystem 2 — component_id LiveComponent routing
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
class TestRuntimeComponentRouting:
    @pytest.mark.asyncio
    async def test_component_id_routes_to_component_handler(self):
        """A ``component_id``-targeted event runs the COMPONENT's handler and
        emits a parent-scoped full-HTML ``component_event`` frame.

        Reproduce-first: without component routing the runtime would validate
        ``bump`` against the PARENT view (no such handler) and error out.
        """
        parent = _ParentView()
        parent.mount(None)
        component = _ClickComponent(component_id="comp-1")
        parent._components["comp-1"] = component

        runtime, transport = await _mounted_runtime(parent)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "item_clicked",
                "params": {"component_id": "comp-1", "item_id": 42},
                "ref": 9,
            }
        )

        # The COMPONENT handler ran (component_id stripped from handler params).
        assert component.last_click == {"item_id": 42}

        # A full-HTML frame scoped to the PARENT was emitted (component VDOM is
        # separate from the parent's).
        assert "html_update" in _frame_types(transport), _frame_types(transport)
        frame = next(f for f in transport.sent if f.get("type") == "html_update")
        assert frame["event_name"] == "item_clicked"
        assert frame["source"] == "event"
        assert frame["ref"] == 9

    @pytest.mark.asyncio
    async def test_component_id_does_not_reassign_target_view(self):
        """Per #1467: ``component_id`` routing does NOT reassign target_view.
        The parent's waiters resolve with ``component_id`` injected."""
        import asyncio

        parent = _ParentView()
        parent.mount(None)
        component = _ClickComponent(component_id="comp-2")
        parent._components["comp-2"] = component
        runtime, _transport = await _mounted_runtime(parent)

        async def wait_for_click():
            return await parent.wait_for_event("item_clicked", timeout=2.0)

        task = asyncio.create_task(wait_for_click())
        await asyncio.sleep(0.01)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "item_clicked",
                "params": {"component_id": "comp-2", "item_id": 5},
            }
        )

        resolved = await task
        assert resolved["item_id"] == 5
        # component_id injected into the PARENT's waiter kwargs.
        assert resolved["component_id"] == "comp-2"

    @pytest.mark.asyncio
    async def test_missing_component_errors(self):
        """An unknown ``component_id`` returns a ``Component not found`` error."""
        parent = _ParentView()
        parent.mount(None)
        runtime, transport = await _mounted_runtime(parent)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "item_clicked",
                "params": {"component_id": "ghost", "item_id": 1},
            }
        )

        assert len(transport.errors) == 1, transport.sent
        assert "Component not found" in transport.errors[0]["error"]

    @pytest.mark.asyncio
    async def test_gate_off_handler_validated_against_component_not_parent(self):
        """GATE-OFF (#1468): the component-handler validation invariant.

        The handler ``item_clicked`` lives ONLY on the component, NOT the
        parent. The routing validates the handler against the COMPONENT — proven
        by the handler actually firing. If the runtime (incorrectly) validated
        against the PARENT, ``_validate_event_security`` would reject
        (handler-not-found) and the component side effect would never happen.

        We assert the parent genuinely lacks the handler (the gate-off premise)
        AND that the component side effect fired (the guard is load-bearing).
        """
        parent = _ParentView()
        parent.mount(None)
        component = _ClickComponent(component_id="comp-3")
        parent._components["comp-3"] = component
        runtime, transport = await _mounted_runtime(parent)

        # Premise: the parent has no ``item_clicked`` handler — so validating
        # against the parent (the gate-off shape) WOULD fail.
        assert getattr(parent, "item_clicked", None) is None

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "item_clicked",
                "params": {"component_id": "comp-3", "item_id": 11},
            }
        )

        # No security/handler-not-found error, and the component handler ran:
        # validation went against the component, not the parent.
        assert transport.errors == [], transport.errors
        assert component.last_click == {"item_id": 11}


# ------------------------------------------------------------------ #
# Subsystem 3 — embedded-child render (single-sourced helper)
# ------------------------------------------------------------------ #


@pytest.mark.django_db(transaction=True)
class TestRuntimeEmbeddedRender:
    @pytest.mark.asyncio
    async def test_embedded_render_uses_single_sourced_helper(self):
        """The runtime renders the child via the same module-level
        :func:`render_embedded_child_html` the WS path uses (one impl, no
        parallel copy — the #1646 cure for the embedded-render subsystem)."""
        from djust import websocket as ws_mod

        parent, child, runtime, transport = await _mounted_sticky_parent()

        calls: List[Any] = []
        original = ws_mod.render_embedded_child_html

        def _spy(child_view):
            calls.append(child_view)
            return original(child_view)

        ws_mod.render_embedded_child_html = _spy
        try:
            await runtime.dispatch_event(
                {
                    "type": "event",
                    "event": "bump",
                    "params": {"view_id": "child", "amount": 1},
                }
            )
        finally:
            ws_mod.render_embedded_child_html = original

        assert calls == [child]  # runtime delegated to the single-sourced helper
        frame = next(f for f in transport.sent if f.get("type") == "embedded_update")
        assert "count=1" in frame["html"]

    def test_an_explicit_embedded_render_failure_is_value_free(self):
        """An explicit child's render failure raises a value-free error (#1646, D-a).

        The legacy path returned an escaped HTML comment (``-->`` breakout,
        CWE-79) and detail under DEBUG. An explicit child never turns the
        exception text into page markup: the helper raises a fixed error that
        names neither the message nor its markup, under DEBUG too.
        """
        from django.test import override_settings

        from djust._exposure import ExposureError
        from djust.websocket import render_embedded_child_html

        child = _BrokenChildView()
        child.mount(None)

        for debug in (True, False):
            with override_settings(DEBUG=debug), pytest.raises(ExposureError) as refused:
                render_embedded_child_html(child)
            assert str(refused.value) == "Explicit child rendering unavailable"
            assert "boom" not in str(refused.value)
            assert "<script>" not in str(refused.value)
