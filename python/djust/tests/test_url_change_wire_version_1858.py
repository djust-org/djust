"""Regression tests for #1858 — url_change/dj-patch frames stamp the consumer wire version.

Root cause (the #1788 PARALLEL-PATH TWIN, #1646): #1788 made
``LiveViewConsumer._next_version()`` (a per-connection monotonic counter) the single
source of truth for the outbound VDOM ``version`` and wired it into ``handle_mount`` and
``handle_event``. But ``url_change`` (from ``dj-patch`` clicks and browser popstate) is
delegated to ``ViewRuntime.dispatch_url_change`` (``runtime.py``), which stamped the wire
``version`` directly from the ``render_with_diff()`` return — the **Rust render counter** —
and ``WSConsumerTransport.send`` forwarded the frame verbatim with NO consumer-counter
rewrite.

The Rust render counter is already several renders ahead of the consumer counter on a real
session (the HTTP-GET SSR pre-render + the WS-mount hydration re-render each advance the
Rust counter, while ``_next_version()`` only counts frames the consumer has SENT). So the
first ``url_change`` frame carries e.g. ``version=8`` while the client baseline is ``v2``;
the client's ``clientVdomVersion === data.version - 1`` check fails → non-recoverable error
→ forced page reload. ``dj-click`` works because it stamps ``_next_version()``; ``dj-patch``
does not. **That asymmetry is the fingerprint** and is asserted directly below.

The fix: route the runtime's render-send frames through a transport-provided
``next_client_version(html, rust_version)`` hook. ``WSConsumerTransport`` returns
``consumer._next_version_armed(html)`` (consumer-owned counter + recovery arming, #1788 /
#1817); ``SSESessionTransport`` returns the Rust version unchanged (SSE has no consumer
counter, so its behavior is preserved).

REPRODUCTION FIDELITY: the divergence ONLY appears when the Rust counter is ahead of the
consumer counter. We force that with ``has_prerendered: True`` on mount — that path runs an
extra hydration ``render_with_diff()`` (``websocket.py`` mount, has_prerendered branch),
advancing the Rust counter while the mount frame still stamps the consumer baseline = 1.
A plain (non-prerendered) mount would leave the two counters coincidentally aligned and
give a FALSE PASS.
"""

from __future__ import annotations

import pytest
from asgiref.sync import sync_to_async

from djust import LiveView
from djust.decorators import event_handler


class _UrlChangeView(LiveView):
    """View with BOTH a dj-click handler (``bump``) and url-driven state (``handle_params``).

    ``bump`` is the control: it goes through ``handle_event`` and is already on the consumer
    counter (proves the asymmetry). ``handle_params`` drives the ``url_change`` path under
    test.
    """

    template = (
        '<div dj-view="djust.tests.test_url_change_wire_version_1858._UrlChangeView" '
        'dj-id="0">Count: {{ count }} / Tab: {{ tab }}</div>'
    )

    def mount(self, request, **kwargs):
        self.count = 0
        self.tab = "home"

    @event_handler()
    def bump(self, **kwargs):
        self.count += 1

    def handle_params(self, params, uri):
        # dj-patch / popstate drives this. Change a rendered var so the re-render
        # produces a real diff (a patch frame).
        self.tab = (
            params.get("tab", ["home"])[0]
            if isinstance(params.get("tab"), list)
            else params.get("tab", "home")
        )


async def _receive_until(communicator, wanted_type, *, tries=8, timeout=3):
    last = None
    for _ in range(tries):
        last = await communicator.receive_json_from(timeout=timeout)
        if last.get("type") == wanted_type:
            return last
    return last


async def _connect_and_mount_prerendered(view_suffix="_UrlChangeView", url="/tabs/"):
    """Harness lifted from test_ws_send_version_1788.py, with ``has_prerendered: True``.

    The prerendered mount path runs an extra hydration ``render_with_diff()`` so the Rust
    render counter ends up AHEAD of the consumer counter — the exact production divergence.
    Returns (communicator, mount_frame).
    """
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
        {
            "type": "mount",
            "view": f"{__name__}.{view_suffix}",
            "url": url,
            # Production fingerprint: client already has SSR pre-rendered content,
            # so the mount runs an extra hydration render — advancing the Rust counter.
            "has_prerendered": True,
        }
    )
    mount_frame = await _receive_until(communicator, "mount")
    assert mount_frame.get("type") == "mount", f"expected mount, got {mount_frame!r}"
    return communicator, mount_frame


def _v(frame):
    assert frame is not None, "expected a frame, got None"
    return frame.get("version")


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_url_change_stamps_consumer_version_not_rust_counter():
    """(a) THE load-bearing #1858 test.

    mount (prerendered, consumer baseline v=1) -> dj-click ``bump`` (consumer v=2) ->
    ``url_change`` must emit a frame at v=3 (consumer baseline + 1), NOT the Rust render
    counter. The client's ``clientVdomVersion === data.version - 1`` check must PASS, and
    a SECOND dj-click after the url_change must chain to v=4 (no collision) — proving the
    url_change frame ADVANCED the consumer counter rather than reading a parallel one.

    Gate-off: if the runtime stamps the Rust ``render_with_diff`` counter (pre-fix), the
    url_change does NOT advance ``_next_version()``, so the next dj-click COLLIDES with the
    url_change frame's version (the ``[1, 2, 3, 3]`` shape) and the chain assertion fails —
    proving the test exercises the fix.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_frame = await _connect_and_mount_prerendered()
        m = _v(mount_frame)
        assert m == 1, f"mount baseline must be the consumer counter = 1, got {m!r}"

        # CONTROL: dj-click goes through handle_event and is already on the consumer
        # counter (this is the half that works in the bug report).
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        click = await _receive_until(communicator, "patch")
        assert click.get("type") == "patch", f"dj-click should patch, got {click!r}"
        vc = _v(click)
        assert vc == m + 1, (
            f"CONTROL: dj-click must stamp consumer counter m+1 ({m + 1}), got {vc!r}. "
            "If this already fails, the control is broken (not the #1858 path)."
        )

        # SUBJECT: url_change (dj-patch / popstate). Must chain off the consumer counter.
        await communicator.send_json_to(
            {"type": "url_change", "params": {"tab": ["settings"]}, "uri": "/tabs/?tab=settings"}
        )
        uc = await _receive_until(communicator, "patch")
        assert uc.get("type") in ("patch", "html_update"), (
            f"url_change must emit a render frame, got {uc!r}"
        )
        assert uc.get("event_name") == "url_change", (
            f"the render frame must be the url_change frame, got {uc!r}"
        )
        vu = _v(uc)

        assert vu == vc + 1, (
            f"url_change must stamp the consumer counter (prev+1 = {vc + 1}), got {vu!r}. "
            "A Rust-counter value here IS the #1858 bug (the #1788 parallel-path twin): "
            "url_change was stamping render_with_diff()'s Rust render counter."
        )
        # The client check that forces the reload in the bug report:
        assert vc == vu - 1, (
            f"client check clientVdomVersion === data.version - 1 must PASS: prev={vc}, "
            f"emitted={vu}. A failure here is the non-recoverable error + forced reload."
        )

        # CHAIN GUARD (the part that is ALWAYS broken pre-fix regardless of the Rust/consumer
        # gap size): the url_change frame must have ADVANCED _next_version(), so the next
        # dj-click is vu+1 and does NOT collide with the url_change version. Pre-fix the
        # url_change leaves the consumer counter untouched → this click reuses vu (the
        # ``[1, 2, 3, 3]`` collision the recovery storm rides on).
        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 2})
        click2 = await _receive_until(communicator, "patch")
        v2 = _v(click2)
        assert v2 == vu + 1, (
            f"dj-click after url_change must chain to vu+1 ({vu + 1}), got {v2!r}. A collision "
            f"(v2 == vu == {vu}) means url_change did NOT advance the consumer counter — the "
            "#1858 drift: it stamped the parallel Rust counter instead of _next_version()."
        )

        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_version_monotonic_across_click_then_urlchange_then_click():
    """(b) DRIFT GUARD: the full sequence must be strictly m, m+1, m+2, m+3.

    mount -> dj-click -> url_change -> dj-click. A discontinuity at the url_change boundary
    means url_change drifted off the consumer counter (the entire #1858 risk).
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_frame = await _connect_and_mount_prerendered()
        m = _v(mount_frame)

        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        f1 = await _receive_until(communicator, "patch")

        await communicator.send_json_to(
            {"type": "url_change", "params": {"tab": ["a"]}, "uri": "/tabs/?tab=a"}
        )
        f2 = await _receive_until(communicator, "patch")

        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 2})
        f3 = await _receive_until(communicator, "patch")

        seq = [m, _v(f1), _v(f2), _v(f3)]
        assert seq == [m, m + 1, m + 2, m + 3], (
            "wire version sequence must be strictly monotonic across the url_change boundary "
            f"(no jump/reset). Expected {[m, m + 1, m + 2, m + 3]}, got {seq}. "
            "A discontinuity means url_change drifted off the consumer counter (#1858)."
        )

        await communicator.disconnect()


@pytest.mark.django_db
@pytest.mark.asyncio
async def test_url_change_arms_recovery_to_its_own_version():
    """(c) Recovery-arming discipline (#1817 carried to the url_change path).

    After a url_change frame, a ``request_html`` recovery must serve the SAME version the
    url_change frame carried — not a stale baseline. If url_change advanced the wire version
    WITHOUT arming recovery, ``request_html`` would return ``html_recovery`` stamped with
    the previous (stale) ``_recovery_version`` and the client would reset backwards.
    """
    from django.test import override_settings

    with override_settings(LIVEVIEW_ALLOWED_MODULES=[__name__]):
        communicator, mount_frame = await _connect_and_mount_prerendered()
        m = _v(mount_frame)
        assert m == 1, f"mount baseline must be the consumer counter = 1, got {m!r}"

        await communicator.send_json_to({"type": "event", "event": "bump", "params": {}, "ref": 1})
        click = await _receive_until(communicator, "patch")
        vc = _v(click)

        await communicator.send_json_to(
            {"type": "url_change", "params": {"tab": ["x"]}, "uri": "/tabs/?tab=x"}
        )
        uc = await _receive_until(communicator, "patch")
        vu = _v(uc)
        assert vu == vc + 1, f"url_change must be vc+1; got {vu!r} (precondition for recovery test)"

        # Client applyPatches() failed → request the recovery HTML.
        await communicator.send_json_to({"type": "request_html"})
        rec = await _receive_until(communicator, "html_recovery")
        assert rec.get("type") == "html_recovery", f"expected html_recovery, got {rec!r}"
        vr = _v(rec)
        assert vr == vu, (
            f"request_html recovery must serve the url_change frame's version ({vu}), got "
            f"{vr!r}. A stale value means url_change advanced the wire version WITHOUT arming "
            "recovery (#1817 drift on the runtime path)."
        )

        await communicator.disconnect()
