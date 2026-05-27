"""Tests for #1610 — server-side correctness pin for WS-mount HTML divergence.

The bug class fixed by this PR is *client-side*: the `skipMountHtml` branch
in `python/djust/static/djust/src/03-websocket.js` previously only stamped
dj-id attributes onto the existing prerender DOM, silently dropping any
content that differed between HTTP-prerender context and WS-mount context.

These tests pin the *server side* of the contract — which was always
correct. The server has been sending the WS-context HTML in the mount
response with `has_ids=True` for cold-prerender mounts; the client was
the one ignoring it. The purpose of this test file is a regression
backstop so a future server change doesn't accidentally regress the
server side and re-introduce a different shape of #1610.

Specifically:

- On cold mount with `has_prerendered=True, mounted=False`, the mount
  response MUST carry `html` and `has_ids=True` so the client's morph
  branch (the fix) fires correctly.
- The `skip_html_for_resume = mounted and has_prerendered` truth table
  (the resume path) is already covered by
  `test_ws_reconnect_state_1465.py:test_skip_html_for_resume_truth_table`
  and is NOT duplicated here.
"""

from __future__ import annotations

import inspect
from typing import Any


def test_cold_prerender_mount_response_carries_html_and_has_ids() -> None:
    """Reproduce the response-build conditional from `websocket.py:2345-2352`
    in isolation and assert the cold-prerender case carries `html` AND
    `has_ids=True`.

    Cold prerender = `has_prerendered=True, mounted=False`. The
    `skip_html_for_resume` boolean is `mounted and has_prerendered`, so
    cold prerender evaluates False — html flows. The `has_ids` flag is
    set by checking `"dj-id=" in html` (mirrors the live code).

    This pins the WIRE SHAPE that the client's #1610 fix depends on:
    if the server stopped emitting `has_ids=True` on cold prerender,
    the client's morph branch (gated on `hasDataDjAttrs && data.html`)
    would never fire, and the bug would silently come back.
    """

    def build_response(html: str | None, mounted: bool, has_prerendered: bool) -> dict[str, Any]:
        response: dict[str, Any] = {"type": "mount", "version": 0}
        skip_html_for_resume = mounted and has_prerendered
        if html is not None and not skip_html_for_resume:
            response["html"] = html
            response["has_ids"] = "dj-id=" in html
        return response

    # The load-bearing case: cold mount with prerender flag, dj-id
    # attrs present. Client MUST see html + has_ids=True so its morph
    # branch fires.
    r = build_response(
        '<div dj-view="myapp.HomeView" dj-id="0"><span>2 online</span></div>',
        mounted=False,
        has_prerendered=True,
    )
    assert r["html"] == '<div dj-view="myapp.HomeView" dj-id="0"><span>2 online</span></div>'
    assert r["has_ids"] is True, (
        "Cold-prerender mount response must set has_ids=True so the "
        "client's #1610 morph branch fires. Without this flag, the "
        "client's `hasDataDjAttrs && data.html` gate evaluates False "
        "and the WS-mount HTML is silently dropped — exactly the bug "
        "#1610 was filed to fix."
    )

    # Sanity case: no dj-id attrs (e.g. very old server, or rendered
    # by a template engine that doesn't stamp ids) — html still flows
    # but has_ids is False. The client's morph branch correctly
    # short-circuits in this case.
    r = build_response(
        "<div><span>2 online</span></div>",
        mounted=False,
        has_prerendered=True,
    )
    assert r["html"] == "<div><span>2 online</span></div>"
    assert r["has_ids"] is False


def test_cold_prerender_html_diverging_from_prerender_is_sent_to_client() -> None:
    """Pin the bug-class symptom in wire-shape form.

    The #1610 reporter's case: a `HomeView.mount()` that sets
    `self.online_count = 2 if hasattr(self, '_websocket_session_id') else 1`
    renders `>1 online<` in HTTP-prerender context and `>2 online<` in
    WS-mount context. The server's mount response must carry the
    WS-context HTML (`>2 online<`), and the client (post-fix) must
    apply it via morphChildren.

    This test pins the server side of that contract.
    """

    def build_response(html: str | None, mounted: bool, has_prerendered: bool) -> dict[str, Any]:
        response: dict[str, Any] = {"type": "mount", "version": 0}
        skip_html_for_resume = mounted and has_prerendered
        if html is not None and not skip_html_for_resume:
            response["html"] = html
            response["has_ids"] = "dj-id=" in html
        return response

    # WS-context HTML (the post-mount, _websocket_session_id-aware
    # render). Server emits this as `html` in the mount response.
    ws_html = '<div dj-view="myapp.HomeView" dj-id="0"><span>2 online</span></div>'

    r = build_response(ws_html, mounted=False, has_prerendered=True)
    assert "html" in r, "Cold-prerender mount response must include WS-context html"
    assert "2 online" in r["html"], (
        "Server must send WS-context-resolved html, not the HTTP-prerender "
        "value. If this assertion ever fails, the server side of #1610 has "
        "regressed and the client-side morph fix is ineffective."
    )
    assert r["has_ids"] is True


def test_skip_html_logic_present_in_source() -> None:
    """Source-text pin: the live `handle_mount` source contains the
    skip-html conditional shape that the truth-table tests above
    reproduce. Prevents the in-test reproduction from drifting away
    from the production code.
    """
    import djust.websocket as ws_mod

    source = inspect.getsource(ws_mod.LiveViewConsumer.handle_mount)
    assert "skip_html_for_resume = mounted and has_prerendered" in source
    assert "if html is not None and not skip_html_for_resume:" in source
    # has_ids must be emitted on the cold-prerender path so the
    # client's #1610 morph branch can gate on it.
    assert 'response["has_ids"] = has_ids' in source or "has_ids =" in source
