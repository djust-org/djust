"""Structural pin: every exception-carrying log call in the WebSocket consumer
and the view runtime is classified (ADR-038 E1).

``handle_exception`` refuses to stringify an exception for a nonlegacy owner,
because undeclared state can occur in its message and traceback. A raw
``logger`` call does not consult that policy — a runtime turn's diagnostic
scope does not change that — so each one in ``websocket.py`` and
``runtime.py`` that carries exception data — the exception in its arguments,
``logger.exception``, or ``exc_info`` — must be one of:

- ``HELPER``: a raw call that is itself the policy gate (none today: the
  consumer helper now delegates to ``_exposure_diagnostics.log_failure``);
- ``LEGACY_GATED``: reachable only for a legacy owner, by a guard that was read;
- ``FRAMEWORK_ONLY``: the logged exception cannot derive from application values;
- ``KNOWN_OPEN``: application code, not yet fixed. This list may only shrink.

A new site fails this test until it is classified. The usual fix is to log
through ``_exposure_diagnostics.log_failure(logger, exc, msg, *args, level=...,
traceback=...)`` inside a runtime turn, or the consumer's
``self._log_view_hook_failure(view, exc, ...)`` outside one; both keep the
legacy message, level and traceback unchanged. Sites are keyed by enclosing function and
the first 48 characters of the message literal, so line drift does not matter.
The per-site evidence is in docs/adr/notes/038-exposure-sink-inventory.md.
"""

import ast
import pathlib

import pytest

PACKAGE = pathlib.Path(__file__).resolve().parents[1]

HELPER: dict = {}

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
    ("disconnect", "Error cleaning up uploads: %s"): (
        "runs only when not explicit_disposed; a nonlegacy view is disposed instead"
    ),
    ("_mount_one", "mount_batch: _mount_one raised for view %s"): (
        "legacy branch only; the owner is the resolved class, fail-closed if unresolvable"
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
    ("disconnect", "Error leaving db_notify group for %s: %s"): (
        "channel-layer group_discard; the channel name is an identifier, not state"
    ),
    ("disconnect", "Error cancelling waiters: %s"): (
        "_cancel_all_waiters only schedules future cancellation; no hook runs"
    ),
    ("disconnect", "Error cleaning up embedded children: %s"): (
        "_unregister_child disposes nonlegacy children and catches legacy hooks itself"
    ),
}

KNOWN_OPEN = {
    ("_run_async_work", "[djust] Error in start_async callback '%s' on %s"): (
        "unreachable from the NOTIFY drain until its has_async drop is fixed; fix both together"
    ),
    ("_run_async_work", "[djust] Error in handle_async_result for task '%"): (
        "unreachable from the NOTIFY drain until its has_async drop is fixed; fix both together"
    ),
}


RUNTIME_LEGACY_GATED = {
    ("_flush_deferred_activity_events", "dj_activity: runtime deferred-event flush raised"): (
        "inside `if diagnostics_allowed()` after restricting to both owners"
    ),
    ("_dispatch_single_event", "Runtime deferred-activity event %r on %s raised "): (
        "inside `if diagnostics_allowed()` after restricting to the target view"
    ),
    ("_notify_waiters_safely", "<Name>"): (
        "inside `if diagnostics_allowed()` after restricting to both owners"
    ),
    ("_push_tt_event", "Runtime: time_travel on_event_recorded hook fail"): (
        "inside `if diagnostics_allowed()` after restricting to both owners"
    ),
    ("_execute_async_task", "Runtime: error in start_async callback '%s' on %"): (
        "inside `if legacy_diagnostics and uses_legacy_exposure(view)`"
    ),
    ("_execute_async_task", "Runtime: error in handle_async_result for task '"): (
        "inside `if legacy_diagnostics and uses_legacy_exposure(view)`"
    ),
    ("on_mount_render_ready", "sticky child _on_sticky_unmount raised"): (
        "inside `elif ... and uses_legacy_exposure(child)` (reattach collision)"
    ),
    ("on_mount_render_ready", "sticky child _on_sticky_unmount raised", 1): (
        "inside `if uses_legacy_exposure(child)`"
    ),
    ("dispatch_actor_event", "dj_activity: deferred-event flush raised (actor "): (
        "actor events are refused for nonlegacy views before this transport hook"
    ),
    ("dispatch_mount", "state_snapshot _restore_snapshot failed for %s; "): (
        "restore runs only when `opt_in and legacy_exposure`"
    ),
    ("recheck_event_auth", "reauth_on_event re-check skipped (non-fatal, WS)"): (
        "its sole caller, _dispatch_event, runs it only when uses_legacy_exposure(view)"
    ),
    ("recheck_event_auth", "reauth_on_event re-check skipped (non-fatal, SSE"): (
        "its sole caller, _dispatch_event, runs it only when uses_legacy_exposure(view)"
    ),
    ("dispatch_mount", "Failed to emit state_snapshot_signed for %s; pro"): (
        "a nonlegacy view reaches only the explicit branch, wrapped in its own value-free catch"
    ),
}

RUNTIME_FRAMEWORK_ONLY = {
    ("on_view_mounted", "Error joining db_notify group for %s: %s"): "channel-layer group_add",
    ("on_render_emitted", "DJE-053 diagnostic emit failed"): "diagnostic formatting",
    ("on_event_frame", "on_event_frame debug decoration failed"): (
        "debug payload built from the redacted debug projection"
    ),
    ("on_view_instantiated", "Failed to register view in observability registr"): "registry",
    ("on_mount_render_ready", "failed to emit sticky_hold frame before mount"): "frame send",
    ("_flush_accessibility", "Failed to flush accessibility announcements"): "frame send",
    ("_flush_accessibility", "Failed to flush focus command"): "frame send",
}

RUNTIME_KNOWN_OPEN = {}

# Smaller modules. An empty table still pins the module: a new exception-
# carrying log call there fails the test until it is classified.
MIXIN_TABLES = {
    "live_view.py": (
        {
            (
                "_capture_components_snapshot",
                "time_travel: component snapshot failed for id=%s",
            ): (
                "_capture_snapshot_state returns the explicit projection before "
                "reaching the component snapshot"
            ),
        },
        {
            ("__init__", "time_travel: failed to allocate buffer"): "buffer allocation",
        },
    ),
    "mixins/sticky.py": (
        {
            ("_on_sticky_unmount", "sticky _on_sticky_unmount: cancel_async_all() fa"): (
                "inside `if uses_legacy_exposure(self)`"
            ),
            ("_preserve_sticky_children", "sticky child %s _on_sticky_unmount raised"): (
                "nonlegacy auth-denied children go to dispose_child_subtree first"
            ),
            ("_unregister_child", "child view %s _cleanup_on_unregister raised"): (
                "nonlegacy children go to dispose_child_subtree and return first"
            ),
        },
    ),
    "mixins/rust_bridge.py": (
        {},
        {
            (
                "_get_cached_template_hash_slot",
                "[LiveView] compute_template_hash failed; cache k",
            ): "template hashing",
        },
    ),
    "mixins/activity.py": (
        {
            (
                "_flush_deferred_activity_events_inner",
                "dj_activity: deferred event %r on %s raised duri",
            ): "inside `if diagnostics_allowed()` after restricting to both owners",
        },
    ),
    "mixins/waiters.py": (
        {
            ("_notify_waiters_inner", "wait_for_event predicate for %r raised %r — trea"): (
                "inside `if diagnostics_allowed()` after restricting to the view"
            ),
        },
    ),
    "time_travel.py": (
        {
            ("record_event_end", "time_travel: _capture_snapshot_state failed (aft"): (
                "nonlegacy or non-restorable records return early with the debug projection"
            ),
            ("record_event_end", "time_travel: error coercion failed"): (
                "nonlegacy or non-restorable records return early with the debug projection"
            ),
            ("record_event_start", "time_travel: _capture_snapshot_state failed (bef"): (
                "explicit_debug_projection is non-None for every nonlegacy policy, so they never reach the capture"
            ),
            ("replay_event", "time_travel: dry replay handler %s raised"): (
                "returns False for a nonlegacy view before replaying"
            ),
            ("replay_event", "time_travel: replay handler %s raised"): (
                "returns False for a nonlegacy view before replaying"
            ),
            ("restore_component_snapshot", "time_travel: component restore failed for id=%s "): (
                "returns False for a nonlegacy view before restoring"
            ),
            ("restore_snapshot", "time_travel: component restore failed for id=%s "): (
                "returns False for a nonlegacy view before restoring"
            ),
            ("restore_snapshot", "time_travel: ghost-attr cleanup failed for key=%"): (
                "returns False for a nonlegacy view before restoring"
            ),
            ("restore_snapshot", "time_travel: restore failed for key=%s"): (
                "returns False for a nonlegacy view before restoring"
            ),
        },
        {
            ("next_branch_id", "time_travel: failed to increment branch counter"): (
                "branch counter"
            ),
        },
    ),
    "api/dispatch.py": (
        {},
        {
            ("dispatch_api", "djust API: malformed JSON body for %s"): "request body parse",
            (
                "dispatch_server_function",
                "djust API: malformed JSON body for %s",
            ): "request body parse",
        },
    ),
    "mixins/context.py": (
        {
            ("get_context_data", "Descriptor resolution failed for %s: %s"): (
                "get_context_data returns _get_explicit_context_data for a nonlegacy view before this"
            ),
            ("get_context_data", "JIT auto-serialization failed: %s"): (
                "get_context_data returns _get_explicit_context_data for a nonlegacy view before this"
            ),
            ("_apply_context_processors", "Failed to apply context processor %s: %s"): (
                "the explicit branch runs processors without this catch and returns first"
            ),
        },
        {
            ("_get_resolved_processors", "Failed to import context processor %s: %s"): (
                "processor import path from settings"
            ),
        },
    ),
    "mixins/jit.py": (
        {
            ("_get_template_content", "Could not load template for JIT: %s"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
            ("_jit_serialize_model", "JIT serialization failed for %s: %s"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
            ("_jit_serialize_queryset", "[JIT ERROR] Serialization failed for '%s': %s\nTr"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
            ("replacer", "Failed to read included template %s: %s"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
        },
    ),
    "mixins/request.py": (
        {
            ("post", "<Name>"): (
                "legacy branch only: a nonlegacy view returns the generic response first"
            ),
        },
        {
            ("_inject_debug", "Failed to inject debug info"): "debug payload assembly",
            ("_watch_disconnect", "is_disconnected() raised; halting watcher"): "ASGI probe",
            ("get", "Failed to render wrapper_template '%s': %s"): (
                "its only input is the already-rendered page HTML the client receives"
            ),
        },
    ),
    # assign_async's runners and the SSE deferred flush now log through
    # log_failure_for; nothing raw remains.
    "mixins/async_work.py": (),
    "sse.py": (),
}

PINNED = {
    "websocket.py": (HELPER, LEGACY_GATED, FRAMEWORK_ONLY, KNOWN_OPEN),
    "runtime.py": (RUNTIME_LEGACY_GATED, RUNTIME_FRAMEWORK_ONLY, RUNTIME_KNOWN_OPEN),
    **MIXIN_TABLES,
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

    found = []
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
        found.append((node.lineno, enclosing_function(node), message))
    # A repeated (function, message) pair would otherwise collapse into one
    # key and hide an unguarded duplicate; the second and later occurrences,
    # in source order, carry an ordinal.
    sites: set = set()
    seen: dict = {}
    for _lineno, function, message in sorted(found):
        ordinal = seen.get((function, message), 0)
        seen[(function, message)] = ordinal + 1
        sites.add((function, message) if ordinal == 0 else (function, message, ordinal))
    return sites


@pytest.mark.parametrize("module", sorted(PINNED))
def test_classification_tables_do_not_overlap(module):
    seen: set = set()
    for table in PINNED[module]:
        assert not (seen & table.keys()), seen & table.keys()
        seen |= table.keys()


@pytest.mark.parametrize("module", sorted(PINNED))
def test_every_exception_carrying_log_call_is_classified(module):
    found = _exception_carrying_log_sites((PACKAGE / module).read_text())
    declared: set = set().union(set(), *(table.keys() for table in PINNED[module]))
    unclassified = sorted(found - declared, key=str)
    stale = sorted(declared - found, key=str)
    assert not unclassified, (
        f"New exception-carrying log call(s) in {module}. Log through "
        "_exposure_diagnostics.log_failure (or the consumer's "
        "_log_view_hook_failure), or classify with a reason: "
        f"{unclassified}"
    )
    assert not stale, (
        f"Classified site(s) no longer found in {module} — remove them (a fixed "
        f"KNOWN_OPEN entry should be deleted, not moved): {stale}"
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


def test_scanner_keeps_repeated_messages_apart():
    # Two identical messages in one function must stay two sites, so one
    # guarded and one unguarded occurrence cannot hide behind a single key.
    sample = """
def f(self):
    try:
        pass
    except Exception:
        logger.exception("same")
    try:
        pass
    except Exception:
        logger.exception("same")
"""
    assert _exception_carrying_log_sites(sample) == {("f", "same"), ("f", "same", 1)}


# --------------------------------------------------------------------------
# Client frames that interpolate an exception reach the browser, not just the
# log. Direct interpolation (the exception name inside the send call's
# arguments) is pinned the same way; an indirect flow such as
# ``detail = str(exc)`` passed later is not visible to this scan.
# --------------------------------------------------------------------------

CLIENT_FRAME_SENDERS = {"send_error", "_send_debug_error", "send_json", "send"}

CLIENT_FRAME_LEGACY_GATED = {
    "websocket.py": {
        ("_handle_time_travel_jump_locked", "_send_debug_error"): (
            "re-render runs only after restore_snapshot, which refuses a nonlegacy view"
        ),
        ("_handle_time_travel_component_jump_locked", "_send_debug_error"): (
            "re-render runs only after restore_component_snapshot, which refuses nonlegacy"
        ),
        ("_handle_forward_replay_locked", "_send_debug_error"): (
            "re-render runs only after replay_event, which refuses a nonlegacy view"
        ),
        ("handle_bug_capture_share", "send_error"): (
            "inside `if uses_legacy_exposure(view) or isinstance(exc, ExposureError)`"
        ),
    },
    "runtime.py": {},
}


def _exception_interpolating_client_frames(source: str) -> set:
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def enclosing(node, kinds):
        while node in parents:
            node = parents[node]
            if isinstance(node, kinds):
                return node
        return None

    sites = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in CLIENT_FRAME_SENDERS:
            continue
        names = set()
        cursor = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, ast.ExceptHandler) and cursor.name:
                names.add(cursor.name)
        if not names:
            continue
        values = list(node.args) + [kw.value for kw in node.keywords]
        if any(
            isinstance(sub, ast.Name) and sub.id in names for v in values for sub in ast.walk(v)
        ):
            fn = enclosing(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            sites.add((fn.name if fn else "<module>", node.func.attr))
    return sites


@pytest.mark.parametrize("module", sorted(CLIENT_FRAME_LEGACY_GATED))
def test_every_exception_interpolating_client_frame_is_classified(module):
    found = _exception_interpolating_client_frames((PACKAGE / module).read_text())
    declared = set(CLIENT_FRAME_LEGACY_GATED[module])
    assert found == declared, (
        f"{module}: client frames interpolating an exception must be gated for "
        f"nonlegacy owners and classified here. New: {sorted(found - declared)}; "
        f"stale: {sorted(declared - found)}"
    )


def test_client_frame_scanner_sees_direct_interpolation():
    sample = """
async def f(self):
    try:
        pass
    except Exception as exc:
        await self.send_error("x: %s" % exc)
        await self.send_error("generic")
"""
    assert _exception_interpolating_client_frames(sample) == {("f", "send_error")}


# --------------------------------------------------------------------------
# Package-wide ratchet. Modules outside PINNED hold exception-carrying log
# calls nobody has classified yet; they are frozen in a generated baseline so a
# NEW site anywhere in the package fails, and the baseline can only shrink.
# --------------------------------------------------------------------------

UNREVIEWED = PACKAGE / "tests" / "fixtures" / "log_exposure_unreviewed.json"


def _package_modules():
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if rel.startswith("tests/") or "/tests/" in rel or "/migrations/" in rel:
            continue
        yield rel, path


def test_package_wide_ratchet_every_site_is_pinned_or_baselined():
    import json

    baseline = {
        module: {tuple(key) for key in keys}
        for module, keys in json.loads(UNREVIEWED.read_text())["modules"].items()
    }
    both = sorted(set(baseline) & set(PINNED))
    assert not both, f"A module is either pinned or baselined, not both: {both}"

    new, stale = [], []
    seen = set()
    for rel, path in _package_modules():
        if rel in PINNED:
            continue
        seen.add(rel)
        found = _exception_carrying_log_sites(path.read_text())
        allowed = baseline.get(rel, set())
        new += [(rel, *key) for key in sorted(found - allowed, key=str)]
        stale += [(rel, *key) for key in sorted(allowed - found, key=str)]
    stale += [(module, "<module no longer exists>") for module in sorted(set(baseline) - seen)]
    assert not new, (
        "New exception-carrying log call(s) outside the pinned modules. Log "
        "through _exposure_diagnostics.log_failure / log_failure_for, or pin the "
        f"module with a classification: {new}"
    )
    assert not stale, (
        "Baselined site(s) no longer found. Remove them from "
        f"{UNREVIEWED.name}; the baseline only shrinks: {stale}"
    )
