"""Structural pin: every exception-carrying log call in the WebSocket consumer
is classified (ADR-038 E1).

``handle_exception`` refuses to stringify an exception for a nonlegacy owner,
because undeclared state can occur in its message and traceback. A raw
``logger`` call does not consult that policy, so each one in ``websocket.py``
that carries exception data — the exception in its arguments,
``logger.exception``, or ``exc_info`` — must be one of:

- ``HELPER``: the policy-checked branch inside ``_log_view_hook_failure``;
- ``LEGACY_GATED``: reachable only for a legacy owner, by a guard that was read;
- ``FRAMEWORK_ONLY``: the logged exception cannot derive from application values;
- ``KNOWN_OPEN``: application code, not yet fixed. This list may only shrink.

A new site fails this test until it is classified. The usual fix is to log
through ``self._log_view_hook_failure(view, exc, msg, *args, traceback=...)``,
which keeps legacy output unchanged. Sites are keyed by enclosing function and
the first 48 characters of the message literal, so line drift does not matter.
The per-site evidence is in docs/adr/notes/038-exposure-sink-inventory.md.
"""

import ast
import pathlib

WEBSOCKET = pathlib.Path(__file__).resolve().parents[1] / "websocket.py"

HELPER = {
    ("_log_view_hook_failure", "<Name>"): "the policy-checked legacy branch itself",
}

LEGACY_GATED = {
    ("render_embedded_child_html", "Failed to render embedded child %s: %s"): (
        "a nonlegacy child raises a value-free ExposureError from None first"
    ),
    ("disconnect", "sticky %s _on_sticky_unmount during disconnect f"): (
        "nonlegacy children go to dispose_child_subtree before the hook runs"
    ),
    ("handle_live_redirect_mount", "sticky child _on_sticky_unmount raised"): (
        "nonlegacy children go to dispose_child_subtree before the hook runs"
    ),
    ("handle_live_redirect_mount", "sticky child _on_sticky_unmount failed during re"): (
        "nonlegacy children go to dispose_child_subtree before the hook runs"
    ),
    ("_handle_time_travel_jump_locked", "time_travel_jump: re-render failed"): (
        "restore_snapshot returns False for a nonlegacy view before re-rendering"
    ),
    (
        "_handle_time_travel_component_jump_locked",
        "time_travel_component_jump: re-render failed",
    ): "restore_component_snapshot returns False for a nonlegacy view first",
    ("_handle_forward_replay_locked", "forward_replay: failed to set branch_id"): (
        "reached only after replay_event, which returns False for a nonlegacy view"
    ),
    ("_handle_forward_replay_locked", "forward_replay: re-render failed"): (
        "replay_event returns False for a nonlegacy view before re-rendering"
    ),
}

FRAMEWORK_ONLY = {
    ("_find_sticky_slot_ids", "sticky-slot parse failed; returning empty set"): "slot markup parse",
    ("_clear_live_handles", "%s failed during teardown"): (
        "teardown step names; confirm no application hook runs there"
    ),
    ("_flush_accessibility", "Failed to flush accessibility announcements"): "frame send",
    ("_flush_accessibility", "Failed to flush focus command"): "frame send",
    ("_attach_debug_payload", "Failed to attach debug payload: %s"): "debug payload assembly",
    ("disconnect", "Error shutting down actor: %s"): "Rust actor shutdown",
    ("_handle_upload_resume", "upload_resume: failed to read session key: %s"): "session key read",
    ("_handle_upload_resume", "upload_resume: active-ref check failed"): "upload ref check",
    ("_send_frame", "Dropping outbound frame: WebSocket closed during"): "socket closed",
    ("_clear_template_caches", "Could not clear template cache for %s: %s"): "cache clear",
    ("hotreload", "Template not found for hot reload: %s"): "dev-only, file-derived",
    ("hotreload", "Failed to parse patches JSON: %s"): "dev-only, file-derived",
    ("hotreload", "Error generating patches for %s: %s"): "dev-only, file-derived",
    ("handle_live_redirect_mount", "Failed to clean up uploads for old view"): "upload cleanup",
    ("handle_live_redirect_mount", "sticky children staging failed; proceeding witho"): (
        "outer catch of the staging block; hooks inside it are legacy-gated"
    ),
}

KNOWN_OPEN = {
    ("_flush_pending_layout", "set_layout(%r) — template rendering raised; igno"): "layout render",
    ("_flush_deferred", "[djust] Deferred callback %s on %s raised; conti"): "deferred callback",
    ("_run_async_work", "[djust] Error in start_async callback '%s' on %s"): "async callback",
    ("_run_async_work", "[djust] Error in handle_async_result for task '%"): "async result",
    ("_dispatch_single_event", "Deferred-activity event %r on %s raised during d"): (
        "live for explicit views via db_notify's activity flush"
    ),
    ("_dispatch_single_event", "Waiter notification for deferred %r failed: %s"): "waiter",
    ("_dispatch_single_event", "Deferred-activity render failed for %s"): "activity render",
    ("_dispatch_single_event", "Deferred-activity HTML strip/extract failed for "): "activity",
    ("_mount_one", "mount_batch: _mount_one raised for view %s"): "mount_batch escape",
    ("_maybe_push_tt_event", "time_travel: failed to push event frame"): "not yet verified",
    ("handle_bug_capture_share", "bug_capture_share: failed to encode capture"): "debug tool",
    ("disconnect", "Error leaving db_notify group for %s: %s"): "guard not confirmed",
    ("disconnect", "Error cleaning up presence: %s"): "guard not confirmed",
    ("disconnect", "Error cleaning up uploads: %s"): "guard not confirmed",
    ("disconnect", "Error cancelling waiters: %s"): "guard not confirmed",
    ("disconnect", "Error cleaning up embedded children: %s"): "guard not confirmed",
}

_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}


def _exception_carrying_log_sites(source: str) -> set:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing_function(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    def bound_exception_names(node):
        names = set()
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.ExceptHandler) and node.name:
                names.add(node.name)
        return names

    sites = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id.endswith("logger")):
            continue
        if node.func.attr not in _LEVELS:
            continue
        exc_names = bound_exception_names(node)
        carries = (
            node.func.attr == "exception"
            or any(
                kw.arg == "exc_info"
                and not (isinstance(kw.value, ast.Constant) and kw.value.value in (None, False))
                for kw in node.keywords
            )
            or any(
                isinstance(sub, ast.Name) and sub.id in exc_names
                for arg in node.args[1:]
                for sub in ast.walk(arg)
            )
        )
        if not carries:
            continue
        first = node.args[0] if node.args else None
        message = (
            first.value[:48]
            if isinstance(first, ast.Constant) and isinstance(first.value, str)
            else f"<{type(first).__name__}>"
        )
        sites.add((enclosing_function(node), message))
    return sites


def test_classification_tables_do_not_overlap():
    tables = [HELPER, LEGACY_GATED, FRAMEWORK_ONLY, KNOWN_OPEN]
    seen = set()
    for table in tables:
        assert not (seen & table.keys()), seen & table.keys()
        seen |= table.keys()


def test_every_exception_carrying_consumer_log_call_is_classified():
    found = _exception_carrying_log_sites(WEBSOCKET.read_text())
    declared = HELPER.keys() | LEGACY_GATED.keys() | FRAMEWORK_ONLY.keys() | KNOWN_OPEN.keys()
    unclassified = sorted(found - declared)
    stale = sorted(declared - found)
    assert not unclassified, (
        "New exception-carrying log call(s) in websocket.py. Log through "
        "self._log_view_hook_failure(view, exc, msg, *args, traceback=...) or "
        f"classify with a reason: {unclassified}"
    )
    assert not stale, (
        "Classified site(s) no longer found — remove them (a fixed KNOWN_OPEN "
        f"entry should be deleted, not moved): {stale}"
    )


def test_scanner_detects_each_carrying_form():
    # Guards the scanner itself: if it stopped seeing a form, the pin above
    # would pass vacuously.
    sample = """
def f(self):
    try:
        pass
    except Exception as e:
        logger.warning("interp: %s", e)
        logger.exception("trace")
        logger.error("info", exc_info=True)
        logger.error("value-free")
        logger.error("none", exc_info=None)
"""
    assert _exception_carrying_log_sites(sample) == {
        ("f", "interp: %s"),
        ("f", "trace"),
        ("f", "info"),
    }
