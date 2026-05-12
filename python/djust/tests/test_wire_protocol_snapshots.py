"""Wire-protocol JSON snapshot tests (#1448 — starter PR, mirrors PR #1444 Rust side).

Pins the canonical JSON shape of the 8 highest-value WebSocket frames
djust emits server-side. Each test constructs the frame dict matching
the exact emit-site at `python/djust/websocket.py` and asserts the
serialized output matches a literal string. A field rename or default-
value change at the emit site silently breaks deployed clients running
older bundles — this catches it at test time.

This is a starter — 8 of ~30+ shapes. Follow-up issue tracks the rest:
mount_batch, child_update, sticky_update, i18n, accessibility, focus,
embedded_update, upload_registered/resumed/progress, reload, hvr-applied,
sticky_hold, html_update, connect, rate_limit_exceeded, pong, navigate,
presence_event, streaming.{patch,html_update,stream}, plus inbound shapes.

The test discipline mirrors `crates/djust_vdom/tests/wire_protocol_snapshot.rs`
(PR #1444):
- Build the frame the same way the emit site builds it.
- `json.dumps` with sorted keys = False (matches Django/Channels default).
- Compare against a literal `expected` string. Any drift in the JSON shape
  fails the test loudly.

References:
- PR #1444 (Rust Patch/VNode snapshot pinning — the canonical pattern)
- websocket.py line citations in each test docstring
"""

import json


def _emit(d: dict) -> str:
    """Emit-shape JSON. Matches `Channels`'s `send_json` default
    (which calls `json.dumps(obj)` without `separators=` — so output
    contains the default `", "` and `": "` separators).
    """
    return json.dumps(d)


# =============================================================================
# 1. push_event — websocket.py:436-442
# =============================================================================


def test_push_event_envelope_pins_canonical_shape():
    """`{"type": "push_event", "event": <str>, "payload": <obj>}`."""
    frame = {
        "type": "push_event",
        "event": "user-action",
        "payload": {"id": 42, "name": "alice"},
    }
    expected = (
        '{"type": "push_event", "event": "user-action", "payload": {"id": 42, "name": "alice"}}'
    )
    assert _emit(frame) == expected


def test_push_event_with_empty_payload():
    """push_event with payload={} — verifies empty-dict serialization."""
    frame = {"type": "push_event", "event": "ping", "payload": {}}
    expected = '{"type": "push_event", "event": "ping", "payload": {}}'
    assert _emit(frame) == expected


# =============================================================================
# 2. flash — websocket.py:457-462
# =============================================================================


def test_flash_command_spread_into_top_level():
    """`{"type": "flash", **cmd}` — cmd keys are spread into the
    top-level object (not nested). Pins this spread shape so a future
    refactor that nested cmd under a "data" key would break loudly."""
    cmd = {"level": "info", "message": "Saved!"}
    frame = {"type": "flash", **cmd}
    expected = '{"type": "flash", "level": "info", "message": "Saved!"}'
    assert _emit(frame) == expected


# =============================================================================
# 3. page_metadata — websocket.py:478-487
# =============================================================================


def test_page_metadata_command_spread_into_top_level():
    """`{"type": "page_metadata", **cmd}` — same shape as flash.
    Emit site: websocket.py:478-487 (dict literal begins at line 481)."""
    cmd = {"title": "New Page Title"}
    frame = {"type": "page_metadata", **cmd}
    expected = '{"type": "page_metadata", "title": "New Page Title"}'
    assert _emit(frame) == expected


# =============================================================================
# 4. patch — websocket.py:1074-1090 (non-binary path)
# =============================================================================


def test_patch_envelope_minimal():
    """Minimal patch frame: type + patches + version. The patch list
    itself can be any JSON-serializable shape — pin the envelope, not
    the inner Rust-emitted Patch (which `wire_protocol_snapshot.rs`
    already pins on the Rust side)."""
    response = {
        "type": "patch",
        "patches": [{"op": "SetAttr", "d": "abc", "k": "class", "v": "hi"}],
        "version": 7,
    }
    expected = (
        '{"type": "patch", "patches": [{"op": "SetAttr", "d": "abc", "k": "class", "v": "hi"}], '
        '"version": 7}'
    )
    assert _emit(response) == expected


def test_patch_envelope_with_html_fallback():
    """When patch-compression-fallback triggers, the response gets an
    extra `html` key (websocket.py:1086-1087). Pin the order: html
    appears AFTER version. (Python 3.7+ preserves dict insertion order,
    and the emit site inserts html AFTER the initial dict literal.)"""
    response = {
        "type": "patch",
        "patches": [],
        "version": 1,
    }
    response["html"] = "<div>fallback</div>"
    expected = '{"type": "patch", "patches": [], "version": 1, "html": "<div>fallback</div>"}'
    assert _emit(response) == expected


# =============================================================================
# 5. mount — websocket.py:2283-2300
# =============================================================================


def test_mount_envelope_minimal():
    """Pins the mount frame's key ORDER: type, session_id, view, version.
    Public state (when present) is appended via `response["public_state"] = ...`
    AFTER the initial dict literal, so order is critical."""
    response = {
        "type": "mount",
        "session_id": "sess-abc",
        "view": "myapp.views.MyView",
        "version": 0,
    }
    expected = (
        '{"type": "mount", "session_id": "sess-abc", "view": "myapp.views.MyView", "version": 0}'
    )
    assert _emit(response) == expected


def test_mount_envelope_with_public_state():
    """When the view opts into state-snapshot (`enable_state_snapshot=True`),
    `public_state` is appended after `version`. Pin that order."""
    response = {
        "type": "mount",
        "session_id": "sess-abc",
        "view": "myapp.views.MyView",
        "version": 0,
    }
    response["public_state"] = {"count": 5}
    expected = (
        '{"type": "mount", "session_id": "sess-abc", "view": "myapp.views.MyView", '
        '"version": 0, "public_state": {"count": 5}}'
    )
    assert _emit(response) == expected


# =============================================================================
# 6. layout — websocket.py:547-551
# =============================================================================


def test_layout_envelope():
    """`{"type": "layout", "path": ..., "html": ...}`."""
    frame = {
        "type": "layout",
        "path": "/dashboard",
        "html": "<main>Dashboard</main>",
    }
    expected = '{"type": "layout", "path": "/dashboard", "html": "<main>Dashboard</main>"}'
    assert _emit(frame) == expected


# =============================================================================
# 7. navigation — websocket.py:712-720
# =============================================================================


def test_navigation_promotes_inner_type_to_action():
    """`navigation` is special: the inner command's `type` is promoted
    to `action` (so it doesn't collide with the outer `type: navigation`).
    Other inner-command keys spread into the top level. Pin this
    promotion shape."""
    cmd = {"type": "live_patch", "to": "/new-route", "replace": True}
    action = cmd.get("type")
    payload = {k: v for k, v in cmd.items() if k != "type"}
    frame = {"type": "navigation", "action": action, **payload}
    expected = '{"type": "navigation", "action": "live_patch", "to": "/new-route", "replace": true}'
    assert _emit(frame) == expected


# =============================================================================
# 8. error — websocket.py:794
# =============================================================================


def test_error_envelope_minimal():
    """`{"type": "error", "error": <str>}`. Additional non-debug keys
    from `context` are spread on top (lines 795-797). Debug-only keys
    are gated behind `DEBUG`."""
    response = {"type": "error", "error": "Permission denied"}
    expected = '{"type": "error", "error": "Permission denied"}'
    assert _emit(response) == expected


def test_error_envelope_with_context_keys():
    """When context provides additional non-debug keys, they appear
    AFTER `error` in insertion order (lines 795-797 do `response[k] = v`)."""
    response = {"type": "error", "error": "validation_failed"}
    response["field"] = "email"
    response["code"] = "invalid"
    expected = (
        '{"type": "error", "error": "validation_failed", "field": "email", "code": "invalid"}'
    )
    assert _emit(response) == expected


# =============================================================================
# 9. mount_batch — websocket.py:2609-2617 (#1456 Batch 1)
# =============================================================================


def test_mount_batch_envelope_minimal():
    """Multi-view batched mount. Pins order: type, session_id, views, failed.
    Optional `navigate` is appended AFTER failed (websocket.py:2615-2616)."""
    response = {
        "type": "mount_batch",
        "session_id": "sess-xyz",
        "views": [{"view_id": "v1", "html": "<div/>"}],
        "failed": [],
    }
    expected = (
        '{"type": "mount_batch", "session_id": "sess-xyz", '
        '"views": [{"view_id": "v1", "html": "<div/>"}], "failed": []}'
    )
    assert _emit(response) == expected


def test_mount_batch_envelope_with_navigate():
    """Optional `navigate` key appended after `failed`."""
    response = {
        "type": "mount_batch",
        "session_id": "sess-xyz",
        "views": [],
        "failed": [],
    }
    response["navigate"] = [{"to": "/dashboard"}]
    expected = (
        '{"type": "mount_batch", "session_id": "sess-xyz", "views": [], "failed": [], '
        '"navigate": [{"to": "/dashboard"}]}'
    )
    assert _emit(response) == expected


# =============================================================================
# 10. child_update — websocket.py:654-661 (#1456 Batch 1)
# =============================================================================


def test_child_update_envelope():
    """Embedded LiveComponent child VDOM patch frame.
    `{"type": "child_update", "view_id": <str>, "patches": [...], "version": <int>}`."""
    frame = {
        "type": "child_update",
        "view_id": "child-abc",
        "patches": [{"op": "SetAttr", "d": "n1", "k": "class", "v": "active"}],
        "version": 3,
    }
    expected = (
        '{"type": "child_update", "view_id": "child-abc", '
        '"patches": [{"op": "SetAttr", "d": "n1", "k": "class", "v": "active"}], '
        '"version": 3}'
    )
    assert _emit(frame) == expected


# =============================================================================
# 11. sticky_update — websocket.py:687-694 (#1456 Batch 1)
# =============================================================================


def test_sticky_update_envelope():
    """Sticky-child VDOM patch frame. Same shape as child_update but with
    distinct `type` (sticky path vs embedded path)."""
    frame = {
        "type": "sticky_update",
        "view_id": "sticky-feed",
        "patches": [],
        "version": 1,
    }
    expected = '{"type": "sticky_update", "view_id": "sticky-feed", "patches": [], "version": 1}'
    assert _emit(frame) == expected


# =============================================================================
# 12. sticky_hold — websocket.py:2399-2403 (#1456 Batch 1)
# =============================================================================


def test_sticky_hold_envelope():
    """Pre-mount frame instructing the client which sticky views to preserve.
    `{"type": "sticky_hold", "views": [<view_id>, ...]}`. Empty list is
    meaningful — tells the client to drop everything in its stash."""
    frame = {"type": "sticky_hold", "views": ["sticky-feed", "sticky-chat"]}
    expected = '{"type": "sticky_hold", "views": ["sticky-feed", "sticky-chat"]}'
    assert _emit(frame) == expected


def test_sticky_hold_envelope_empty_list():
    """Empty `views` list is wire-meaningful (drop-all signal). Pin separately
    so the empty case is locked in."""
    frame = {"type": "sticky_hold", "views": []}
    expected = '{"type": "sticky_hold", "views": []}'
    assert _emit(frame) == expected


# =============================================================================
# 13. embedded_update — websocket.py:3259-3265 (#1456 Batch 1)
# =============================================================================


def test_embedded_update_envelope():
    """Embedded child view full-HTML update (vs the patch-shape child_update).
    `{"type": "embedded_update", "view_id": <str>, "html": <str>, "event_name": <str>}`.
    Distinct from child_update — this is the full-HTML fallback path used when
    VDOM patching would mismatch (component path issue)."""
    frame = {
        "type": "embedded_update",
        "view_id": "embed-1",
        "html": "<section>updated</section>",
        "event_name": "click",
    }
    expected = (
        '{"type": "embedded_update", "view_id": "embed-1", '
        '"html": "<section>updated</section>", "event_name": "click"}'
    )
    assert _emit(frame) == expected
