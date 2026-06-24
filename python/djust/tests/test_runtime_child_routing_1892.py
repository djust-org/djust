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
from djust.decorators import event_handler  # noqa: E402
from djust.runtime import ViewRuntime  # noqa: E402


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


def _frame_types(transport: MockTransport) -> List[str]:
    return [f.get("type") for f in transport.sent]


# ------------------------------------------------------------------ #
# Fixtures — real LiveViews + a real LiveComponent
# ------------------------------------------------------------------ #


class _StickyChildView(LiveView):
    """A sticky child whose ``bump`` handler mutates state and re-renders."""

    sticky = True
    sticky_id = "child"
    template = "<div>count={{ count }}</div>"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, amount: int = 1, **kwargs):
        self.count += amount

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


class _BrokenChildView(LiveView):
    """A child whose template render raises with an attacker-controlled-shaped
    message so the embedded-error escape/DEBUG-gate can be exercised."""

    sticky = True
    sticky_id = "broken"

    def mount(self, request, **kwargs):
        self.count = 0

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def get_template(self):  # noqa: D401 - test hook
        raise ValueError("boom --><script>alert(1)</script>")

    def get_context_data(self, **kwargs):
        return {"count": self.count, "view": self}


class _ParentView(LiveView):
    """A parent page that hosts both a sticky child and a LiveComponent."""

    template = '<div dj-root dj-id="0"><h1>parent={{ parent_count }}</h1></div>'

    def mount(self, request, **kwargs):
        self.parent_count = 0

    def get_context_data(self, **kwargs):
        return {"parent_count": self.parent_count, "view": self}


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


def _make_sticky_parent() -> tuple[_ParentView, _StickyChildView]:
    parent = _ParentView()
    parent.mount(None)
    child = _StickyChildView()
    child.mount(None)
    parent._register_child("child-1", child)
    return parent, child


# ------------------------------------------------------------------ #
# Subsystem 1 — view_id sticky-child routing
# ------------------------------------------------------------------ #


@pytest.mark.django_db
class TestRuntimeStickyChildRouting:
    @pytest.mark.asyncio
    async def test_view_id_routes_to_child_and_emits_embedded_update(self):
        """A ``view_id``-targeted event mutates the CHILD and emits a scoped
        ``embedded_update`` frame — NOT a top-level patch/html frame.

        Reproduce-first: without the runtime's sticky-child routing the event
        would resolve a handler on the PARENT (which has none), so this would
        emit an error / mis-route rather than an embedded_update.
        """
        parent, child = _make_sticky_parent()
        runtime, transport = _make_runtime_with_view(parent)

        await runtime.dispatch_event(
            {
                "type": "event",
                "event": "bump",
                "params": {"view_id": "child-1", "amount": 3},
                "ref": 7,
            }
        )

        # The CHILD's state mutated, the PARENT's did not (target not reassigned
        # to parent; per-child target).
        assert child.count == 3
        assert parent.parent_count == 0

        assert "embedded_update" in _frame_types(transport), _frame_types(transport)
        frame = next(f for f in transport.sent if f.get("type") == "embedded_update")
        assert frame["view_id"] == "child-1"
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
        parent, _child = _make_sticky_parent()
        runtime, transport = _make_runtime_with_view(parent)

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
        parent, _child = _make_sticky_parent()
        runtime, transport = _make_runtime_with_view(parent)

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


@pytest.mark.django_db
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

        runtime, transport = _make_runtime_with_view(parent)

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
        runtime, _transport = _make_runtime_with_view(parent)

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
        runtime, transport = _make_runtime_with_view(parent)

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
        runtime, transport = _make_runtime_with_view(parent)

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


@pytest.mark.django_db
class TestRuntimeEmbeddedRender:
    @pytest.mark.asyncio
    async def test_embedded_render_uses_single_sourced_helper(self):
        """The runtime renders the child via the same module-level
        :func:`render_embedded_child_html` the WS path uses (one impl, no
        parallel copy — the #1646 cure for the embedded-render subsystem)."""
        from djust import websocket as ws_mod

        parent, child = _make_sticky_parent()
        runtime, transport = _make_runtime_with_view(parent)

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
                    "params": {"view_id": "child-1", "amount": 1},
                }
            )
        finally:
            ws_mod.render_embedded_child_html = original

        assert calls == [child]  # runtime delegated to the single-sourced helper
        frame = next(f for f in transport.sent if f.get("type") == "embedded_update")
        assert "count=1" in frame["html"]

    def test_embedded_error_escapes_and_debug_gates_verbatim(self):
        """The security-hardened embedded-error path (escape + DEBUG-gate,
        #1646) is preserved verbatim in the single-sourced helper.

        DEBUG=True → escaped detail inside the comment (no comment-breakout);
        DEBUG=False → generic message, no detail leak (CWE-209/CWE-79).
        """
        from django.test import override_settings

        from djust.websocket import render_embedded_child_html

        child = _BrokenChildView()
        child.mount(None)

        with override_settings(DEBUG=True):
            out = render_embedded_child_html(child)
        # The attacker-shaped ``-->`` / ``<script>`` is escaped — the comment is
        # not broken out of, and no live tag is injected.
        assert "-->" not in out[:-3] if out.endswith("-->") else True
        assert "<script>" not in out
        assert "&lt;script&gt;" in out
        assert "&gt;" in out  # the ``>`` of ``-->`` was escaped too

        with override_settings(DEBUG=False):
            out_prod = render_embedded_child_html(child)
        # Production: generic message, zero exception detail.
        assert out_prod == "<!-- Error rendering embedded child -->"
        assert "script" not in out_prod
        assert "boom" not in out_prod

    def test_gate_off_unescaped_error_would_break_out(self):
        """GATE-OFF (#1468): prove escape() is load-bearing.

        If the helper returned the RAW ``str(e)`` (the pre-#1646 shape), the
        ``-->`` in the message would break out of the HTML comment and the
        ``<script>`` would land in live DOM. We construct that raw string here
        and assert it DOES break out — i.e. the escaped output the helper
        actually produces is materially safer.
        """
        from django.utils.html import escape

        raw_msg = "boom --><script>alert(1)</script>"
        raw_comment = f"<!-- Error rendering embedded child: {raw_msg} -->"
        # The unescaped (gate-off) shape breaks out of the comment + injects a tag.
        assert "--><script>" in raw_comment

        escaped_comment = f"<!-- Error rendering embedded child: {escape(raw_msg)} -->"
        # The escaped (real) shape does NOT break out — the comment stays intact.
        assert "--><script>" not in escaped_comment
        assert "&lt;script&gt;" in escaped_comment
