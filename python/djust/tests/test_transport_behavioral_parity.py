"""Transport BEHAVIORAL-parity nets (Iter 0 / #1885, ADR-022).

The security nets in ``test_transport_parity_security.py`` pin the SECURITY
re-forks across transports (mount-URL traversal, import allowlist, view auth,
object permission, rate limit, origin/host). They do NOT pin *behavioral*
parity between the WS handlers and ``ViewRuntime`` — and the Stage-4 scoping for
the ViewRuntime convergence (ADR-022) found the runtime had already DRIFTED from
WS on exactly that axis:

* ``ViewRuntime.dispatch_mount`` did NOT run the post-mount object-permission
  check the WS ``handle_mount`` runs (a latent IDOR — not live yet because nothing
  mounts through the runtime, but it would go live the instant Iter 1 routes SSE
  through the runtime). Fixed in Iter 0 (``runtime.py`` dispatch_mount now routes
  through the shared ``enforce_object_permission`` chokepoint).

* The runtime drained only 3 of WS's 8 flush queues (push_events / navigation /
  deferred), silently dropping flash / page_metadata / layout / a11y / i18n on
  its one production user, ``url_change`` (a live #1646 instance). Fixed in Iter 0
  (the runtime now has a single ``_flush_all_pending`` that drains all 8 in WS's
  canonical order, called from both turn-end sites).

This file is the ENFORCEMENT net for those two fixes plus the wire-version pin:

1. **Flush-queue-count parity** — AST-compares the WS ``_flush_all_pending`` body
   to the runtime ``_flush_all_pending`` body and asserts they drain the SAME
   ordered set of ``_flush_*`` queues. A future drop of a queue from EITHER path
   re-forks this RED (mechanical drift detection, #1646 / #1125).

2. **Object-permission parity** — drives the REAL ``dispatch_mount`` against a
   view whose ``has_object_permission`` returns ``False`` and asserts the denied
   object is NEVER rendered or sent (only a ``permission_denied`` error frame is
   emitted, and ``view_instance`` is cleared). Gate-off (#1468): remove the
   ``enforce_object_permission`` call from ``dispatch_mount`` → this goes RED.

3. **Wire-version parity** — asserts ``dispatch_url_change`` stamps the
   transport-owned wire version via ``transport.next_client_version`` (already
   true post-#1858; pinned here so a future render-branch edit can't drop it).

4. **WS-only-behavior enumeration** — a count-test enumerating the mount + event
   behaviors that live ONLY on the WS path today (per ADR-022's refined
   sequencing). If a future iteration MOVES one of these onto the runtime, the
   pin trips and must be updated deliberately — keeping the WS↔runtime delta
   visible as the convergence proceeds (Iters 1-3).
"""

import ast
import pathlib
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from django.test import override_settings

from djust.live_view import LiveView

_PKG_DIR = pathlib.Path(__file__).resolve().parents[1]
_WEBSOCKET = _PKG_DIR / "websocket.py"
_RUNTIME = _PKG_DIR / "runtime.py"

# The dispatch_mount tests stub _instantiate_view, so the view path only needs
# to pass the shape + allowlist gate (is_view_path_allowed runs BEFORE
# instantiation, runtime.py:347). The view CLASSES live in this test module;
# allowing the ``djust.`` prefix admits a real dotted path under it.
_DENIED_VIEW_PATH = "djust.tests.test_transport_behavioral_parity._ObjectDeniedView"
_ALLOWED_VIEW_PATH = "djust.tests.test_transport_behavioral_parity._ObjectAllowedView"


# --------------------------------------------------------------------------- #
# Shared mock transport (mirrors test_runtime.MockTransport + next_client_version).
# --------------------------------------------------------------------------- #
class MockTransport:
    """Records outbound frames; ``next_client_version`` is identity (SSE-shape)."""

    def __init__(self, session_id: Optional[str] = None):
        self._session_id = session_id or str(uuid.uuid4())
        self.sent: List[Dict[str, Any]] = []
        self.errors: List[Dict[str, Any]] = []
        self.closed_with: Optional[int] = None
        self.version_calls: List = []

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def client_ip(self) -> Optional[str]:
        return None

    async def send(self, data: Dict[str, Any]) -> None:
        self.sent.append(data)

    async def send_error(self, error: str, **kwargs: Any) -> None:
        msg = {"type": "error", "error": error, **kwargs}
        self.errors.append(msg)
        self.sent.append(msg)

    async def close(self, code: int = 1000) -> None:
        self.closed_with = code

    def next_client_version(self, html: Optional[str], rust_version: int) -> int:
        self.version_calls.append((html, rust_version))
        return rust_version


# --------------------------------------------------------------------------- #
# AST helper — extract the ordered list of ``self._flush_*`` calls inside the
# ``_flush_all_pending`` method body of a given module.
# --------------------------------------------------------------------------- #
def _flush_calls_in_all_pending(path: pathlib.Path) -> List[str]:
    """Return the ordered ``_flush_*`` method names called inside the
    ``_flush_all_pending`` method of *path* (the single source of truth for that
    transport's turn-end drain). Catches both ``await self._flush_x()`` and the
    bare ``self._flush_x()`` shapes."""
    tree = ast.parse(path.read_text())
    target = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
            node.name == "_flush_all_pending"
        ):
            target = node
            break
    assert target is not None, f"_flush_all_pending not found in {path.name}"

    # Walk the body STATEMENTS in source order (ast.walk is breadth-first and
    # would reorder the calls, defeating the order-parity assertion). Each drain
    # is one statement: ``self._flush_x()`` or ``await self._flush_x()``.
    calls: List[str] = []
    for stmt in target.body:
        expr = stmt.value if isinstance(stmt, ast.Expr) else None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await):
            expr = stmt.value.value
        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute):
            func = expr.func
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "self"
                and func.attr.startswith("_flush_")
            ):
                calls.append(func.attr)
    return calls


# --------------------------------------------------------------------------- #
# Net 1 — flush-queue-count parity (#1885 / #1646 / #1125).
# --------------------------------------------------------------------------- #
class TestFlushQueueParity:
    def test_runtime_and_ws_flush_all_pending_drain_the_same_queues(self):
        """The runtime ``_flush_all_pending`` drains the SAME ordered queue set as
        WS ``_flush_all_pending``.

        Gate-off (#1468): drop any ``_flush_*`` line from the runtime's
        ``_flush_all_pending`` (e.g. delete ``await self._flush_flash()``) and this
        FAILS — the two ordered lists diverge. Recorded gate-off in the PR body:
        removing the 5 Iter-0 lines reproduces the pre-fix 3-of-8 state and the
        sets differ by exactly {flash, page_metadata, pending_layout,
        accessibility, i18n}.
        """
        ws_calls = _flush_calls_in_all_pending(_WEBSOCKET)
        rt_calls = _flush_calls_in_all_pending(_RUNTIME)

        # WS is the canonical source: 8 queues in a fixed order.
        expected = [
            "_flush_push_events",
            "_flush_flash",
            "_flush_page_metadata",
            "_flush_pending_layout",
            "_flush_deferred",
            "_flush_navigation",
            "_flush_accessibility",
            "_flush_i18n",
        ]
        assert ws_calls == expected, (
            "WS _flush_all_pending drifted from the canonical 8-queue order this "
            f"net pins. Got {ws_calls!r}. If WS changed deliberately, update the "
            "expected order here AND the runtime to match."
        )
        assert rt_calls == ws_calls, (
            "ViewRuntime._flush_all_pending drains a DIFFERENT queue set/order than "
            f"WS (#1646 parallel-path drift). runtime={rt_calls!r} ws={ws_calls!r}. "
            f"Missing from runtime: {set(ws_calls) - set(rt_calls)}; "
            f"extra on runtime: {set(rt_calls) - set(ws_calls)}."
        )

    def test_runtime_defines_all_eight_flush_methods(self):
        """Each of the 8 queue-flush methods the parity order names actually exists
        on ViewRuntime (so the order pin can't pass against phantom methods)."""
        from djust.runtime import ViewRuntime

        for name in (
            "_flush_push_events",
            "_flush_flash",
            "_flush_page_metadata",
            "_flush_pending_layout",
            "_flush_deferred",
            "_flush_navigation",
            "_flush_accessibility",
            "_flush_i18n",
            "_flush_all_pending",
        ):
            assert hasattr(ViewRuntime, name), f"ViewRuntime missing {name}"


# --------------------------------------------------------------------------- #
# Behavioral views for the runtime-driven nets.
# --------------------------------------------------------------------------- #
class _RuntimeRenderMixin:
    """Common rust/render short-circuits so the REAL dispatch_mount /
    dispatch_url_change run without needing a compiled Rust view + template."""

    def _initialize_temporary_assigns(self):
        pass

    def _initialize_rust_view(self, request):
        pass

    def _sync_state_to_rust(self):
        pass

    def render_with_diff(self):
        return ("<div>SECRET</div>", None, 1)

    def _strip_comments_and_whitespace(self, html):
        return html

    def _extract_liveview_content(self, html):
        return html


class _ObjectDeniedView(_RuntimeRenderMixin, LiveView):
    """Opts into the object-permission lifecycle and DENIES access."""

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return False

    def mount(self, request, **kwargs):
        self.mounted = True

    def handle_params(self, params, uri):
        self.handle_params_ran = True


class _ObjectAllowedView(_RuntimeRenderMixin, LiveView):
    """Opts in and ALLOWS access — the positive half (non-vacuous deny test)."""

    def get_object(self):
        return object()

    def has_object_permission(self, request, obj):
        return True

    def mount(self, request, **kwargs):
        self.mounted = True

    def handle_params(self, params, uri):
        self.handle_params_ran = True


def _runtime_with_view(view):
    """Build a ViewRuntime driving the REAL dispatch_mount against *view*.

    Short-circuits only view-loading + the pre-mount auth seam (the two seams
    the existing test_runtime.py mounts stub); the object-permission check, mount,
    handle_params, and render all run for real."""
    from djust.runtime import ViewRuntime

    transport = MockTransport()
    runtime = ViewRuntime(transport)
    runtime._instantiate_view = MagicMock(return_value=view)
    runtime._check_auth = AsyncMock(return_value=None)
    runtime._resolve_url_kwargs = MagicMock(return_value={})
    return runtime, transport


# --------------------------------------------------------------------------- #
# Net 2 — object-permission parity on dispatch_mount (#1885, gate-off #1468).
# --------------------------------------------------------------------------- #
class TestDispatchMountObjectPermission:
    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_denied_object_is_not_rendered_or_sent(self):
        """A view whose has_object_permission returns False is DENIED by the REAL
        dispatch_mount: no mount frame, no rendered HTML to the client, a
        permission_denied error frame, and view_instance cleared.

        Gate-off (#1468): remove the ``enforce_object_permission`` call from
        ``runtime.py:dispatch_mount`` and this FAILS — the denied view mounts,
        renders its SECRET html, and a mount frame carrying it reaches the client.
        Empirically verified pre-fix via the repro probe (BUG_A_PRESENT=True).
        """
        view = _ObjectDeniedView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {"type": "mount", "view": _DENIED_VIEW_PATH, "params": {}, "url": "/items/42/"}
        )

        # No mount frame, and no frame leaking the denied object's HTML.
        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert not mount_frames, "denied object produced a mount frame — IDOR (#10-#12)"
        leaked = [f for f in transport.sent if "SECRET" in str(f.get("html", ""))]
        assert not leaked, "denied object's rendered HTML reached the client"

        # The denial envelope is emitted and the view is torn down.
        assert transport.errors, "no permission_denied error frame emitted on denial"
        assert transport.errors[0].get("code") == "permission_denied"
        assert runtime.view_instance is None, "view_instance not cleared after denial"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_allowed_object_mounts_and_renders(self):
        """Positive half: an allowed object mounts + renders (so the deny test
        can't pass vacuously by failing every mount)."""
        view = _ObjectAllowedView()
        runtime, transport = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {
                "type": "mount",
                "view": _ALLOWED_VIEW_PATH,
                "params": {},
                "url": "/items/42/",
            }
        )

        mount_frames = [f for f in transport.sent if f.get("type") == "mount"]
        assert mount_frames, "allowed object failed to mount — object-perm over-denied"
        assert runtime.view_instance is view
        assert getattr(view, "handle_params_ran", False), "handle_params skipped on allow"

    @pytest.mark.asyncio
    @override_settings(LIVEVIEW_ALLOWED_MODULES=["djust."])
    async def test_object_perm_runs_after_mount_before_handle_params(self):
        """The object check runs AFTER mount() (so get_object can read URL-derived
        attrs) and BEFORE handle_params + render (so a denied object never renders)
        — the WS placement (websocket.py:2554-2573)."""
        view = _ObjectDeniedView()
        runtime, _ = _runtime_with_view(view)

        await runtime.dispatch_mount(
            {"type": "mount", "view": _DENIED_VIEW_PATH, "params": {}, "url": "/x/"}
        )

        assert getattr(view, "mounted", False), "mount() must run before the object check"
        assert not getattr(view, "handle_params_ran", False), (
            "handle_params ran on a denied object — the object check must short-"
            "circuit BEFORE handle_params"
        )


# --------------------------------------------------------------------------- #
# Net 3 — wire-version parity on dispatch_url_change (#1858 pin).
# --------------------------------------------------------------------------- #
class _UrlChangeView(_RuntimeRenderMixin, LiveView):
    def mount(self, request, **kwargs):
        pass

    def handle_params(self, params, uri):
        self.last_uri = uri


class TestWireVersionParity:
    @pytest.mark.asyncio
    async def test_url_change_stamps_version_via_transport(self):
        """dispatch_url_change stamps the wire version through
        ``transport.next_client_version`` (the consumer-owned counter on WS; the
        Rust version unchanged on SSE) — pinned post-#1858.

        Gate-off (#1468): send ``version`` straight from ``render_with_diff`` in
        ``_dispatch_url_change_inner`` instead of ``transport.next_client_version``
        and ``transport.version_calls`` stays empty → this FAILS.
        """
        from djust.runtime import ViewRuntime

        view = _UrlChangeView()
        transport = MockTransport()
        runtime = ViewRuntime(transport)
        runtime.view_instance = view

        await runtime.dispatch_url_change({"type": "url_change", "params": {}, "uri": "/p/"})

        assert transport.version_calls, (
            "dispatch_url_change did not stamp the wire version through "
            "transport.next_client_version (#1858 wire-version parity regression)"
        )
        # The frame's version is the one the transport returned.
        frames = [f for f in transport.sent if f.get("type") in ("patch", "html_update")]
        assert frames, "no patch/html_update frame from url_change"
        assert "version" in frames[0]


# --------------------------------------------------------------------------- #
# Net 4 — enumerate the KNOWN WS-only mount/event behaviors (ADR-022).
#
# These behaviors live ONLY on the WS handler path today (handle_mount /
# handle_event / receive in websocket.py) and are NOT yet on the runtime —
# Iters 1-3 of ADR-022 will migrate the runtime onto real transports and grow
# the transport-agnostic hooks for these. The pin makes the WS↔runtime delta
# VISIBLE: if a future iteration MOVES one of these onto the runtime, the
# corresponding count drops and this test trips — forcing a deliberate update
# (and a note in the convergence ADR) rather than a silent drift.
# --------------------------------------------------------------------------- #

# (marker substring → minimum occurrences expected in websocket.py today). Each
# is a WS-only mount/event mechanism the runtime does not implement (ADR-022
# "~16 WS-only behaviors": binary upload, presence, cursor, time-travel,
# sticky-child preservation, signed-snapshot restore, actor channel-layer,
# mount_batch, channels groups, ticks).
_WS_ONLY_MARKERS = {
    "handle_mount_batch": 1,  # mount_batch multiplexer (no runtime equivalent)
    "_send_sticky_update": 1,  # sticky-child preservation across live_redirect
    "_send_child_update": 1,  # sticky-child VDOM patches
    "group_add": 1,  # Channels groups (broadcast) — WS-only transport
    "channel_layer": 1,  # actor / broadcast channel layer — WS-only transport
}


class TestWsOnlyBehaviorEnumeration:
    def test_ws_only_behaviors_still_ws_only(self):
        """Each enumerated WS-only mount/event behavior is still present on the WS
        path and NOT (yet) on the runtime. If a convergence iteration moves one
        onto ViewRuntime, update this pin deliberately (and note it in ADR-022).

        This is a VISIBILITY pin, not a security gate — it tracks the WS↔runtime
        delta as Iters 1-3 proceed so 'moved to runtime' is never silent.
        """
        ws_src = _WEBSOCKET.read_text()
        rt_src = _RUNTIME.read_text()

        for marker, minimum in _WS_ONLY_MARKERS.items():
            ws_count = ws_src.count(marker)
            assert ws_count >= minimum, (
                f"WS-only behavior marker {marker!r} dropped from websocket.py "
                f"(found {ws_count}, expected >= {minimum}). If it MOVED to the "
                f"runtime as part of an ADR-022 convergence iteration, update "
                f"_WS_ONLY_MARKERS deliberately."
            )
            assert marker not in rt_src, (
                f"WS-only behavior {marker!r} now appears in runtime.py — a "
                f"convergence iteration moved it onto ViewRuntime. This is expected "
                f"during Iters 1-3, but update _WS_ONLY_MARKERS (and note the move "
                f"in ADR-022) so the WS↔runtime delta stays tracked."
            )

    def test_runtime_has_no_mount_batch(self):
        """ViewRuntime intentionally has no mount_batch multiplexer (ADR-022: the
        mount-batch + transport-terminating side effects are WS-only, #291). Pin it
        so a future addition is a deliberate, reviewed change."""
        from djust.runtime import ViewRuntime

        assert not hasattr(ViewRuntime, "dispatch_mount_batch")
        assert not hasattr(ViewRuntime, "handle_mount_batch")
