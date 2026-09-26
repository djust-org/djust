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
    ("_scope_session_key", "failed to read the session key: %s"): "session key read",
    ("_handle_upload_resume", "upload_resume: re-attaching the upload failed"): (
        "upload manager re-attach; writers hold upload bytes and metadata, never view state"
    ),
    ("_send_frame", "Dropping outbound frame: peer closed the WebSock"): "socket closed",
    ("close", "WebSocket close skipped: peer already closed (%r"): "socket closed",
    ("_handle_upload_resume", "upload_resume: active-ref check failed"): "upload ref check",
    ("_send_frame", "Dropping outbound frame: WebSocket closed during"): "socket closed",
    ("_clear_template_caches", "Could not clear template cache for %s: %s"): "cache clear",
    ("hotreload", "Template not found for hot reload: %s"): "dev-only, file-derived",
    ("hotreload", "Failed to parse patches JSON: %s"): "dev-only, file-derived",
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

KNOWN_OPEN = {}


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
    ("_settle_cancelled_async", "Runtime: error settling cancelled async task for"): (
        "inside `if uses_legacy_exposure(view)`"
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
    ("_join_listen_channels", "Error joining db_notify group for %s: %s"): (
        "channel-layer group_add"
    ),
    ("_leave_view_groups", "Error leaving channel group %s: %s"): "channel-layer group_discard",
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
            ("_jit_serialize_queryset", "[JIT ERROR] Serialization failed for '%s': %s"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
            ("replacer", "Failed to read included template %s: %s"): (
                "JIT serialization is reached only from get_context_data's legacy continuation"
            ),
        },
    ),
    "templatetags/live_tags.py": (
        {
            ("_render_eager", "live_render lazy: child %s.get_context_data rais"): (
                "an explicit child raises a value-free ExposureError first"
            ),
            ("_render_sticky_child_html", "live_render: child %s.get_context_data raised; r"): (
                "an explicit child raises a value-free ExposureError first"
            ),
            ("live_render", "live_render: child %s.get_context_data raised; r"): (
                "an explicit child raises a value-free ExposureError first"
            ),
            ("live_render", "live_render: sticky restore for %r failed; mount"): (
                "inside `if sticky_kwarg and not explicit_child`"
            ),
            ("_discard_sticky_child", "sticky child %r _on_sticky_unmount raised"): (
                "inside `if callable(hook) and uses_legacy_exposure(child)`"
            ),
        },
        {
            ("_resolve_css_class", "config.get_framework_class lookup failed: %s"): (
                "framework config lookup"
            ),
            ("live_render", "live_render: live-instance lookup for sticky %r "): (
                "child registry lookup"
            ),
            ("render", "dj_activity: _register_activity failed for %s"): (
                "activity registration with a template-supplied name"
            ),
            ("_discard_sticky_child", "live_render: unregistering sticky child %r faile"): (
                "_unregister_child disposes nonlegacy children and catches legacy hooks itself"
            ),
        },
    ),
    "presence.py": (
        {},
        {
            ("_broadcast_presence_change", "PresenceMixin._broadcast_presence_change: push_t"): (
                "channel-layer broadcast"
            ),
            ("_refresh_online_count", "PresenceMixin._refresh_online_count: %s"): (
                "presence count refresh"
            ),
            ("_restore_presence", "PresenceMixin._restore_presence: failed to re-re"): (
                "presence backend restore"
            ),
            ("get_presence_key", "Presence key format error: %s. Using unformatted"): (
                "format error names the missing key, not values"
            ),
        },
    ),
    "mixins/template.py": (
        {},
        {
            ("get_template", "[LiveView] Template inheritance resolution faile"): (
                "template inheritance resolution over template files"
            ),
        },
    ),
    "mixins/notifications.py": (
        {},
        {
            ("_restore_listen_channels", "NotificationMixin._restore_listen_channels: cros"): (
                "PostgreSQL listener and channel names"
            ),
            ("_restore_listen_channels", "NotificationMixin._restore_listen_channels: fail"): (
                "PostgreSQL listener and channel names"
            ),
            ("_restore_listen_channels", "NotificationMixin._restore_listen_channels: fail", 1): (
                "PostgreSQL listener and channel names"
            ),
            ("_restore_listen_channels", "NotificationMixin._restore_listen_channels: retr"): (
                "PostgreSQL listener and channel names"
            ),
            ("listen", "listen(%s): failed to start pg listener — %s"): (
                "PostgreSQL listener and channel names"
            ),
        },
    ),
    "observability/views.py": (
        {
            ("eval_handler", "eval_handler: handler call failed (TypeError) ha"): (
                "_mutation_policy_gate refuses a nonlegacy view before mount or the handler runs"
            ),
            ("eval_handler", "eval_handler: handler raised handler=%s"): (
                "_mutation_policy_gate refuses a nonlegacy view before mount or the handler runs"
            ),
            ("reset_view_state", "reset_view_state: mount() raised for session %s"): (
                "_mutation_policy_gate refuses a nonlegacy view before mount or the handler runs"
            ),
        },
        {
            ("eval_handler", "eval_handler: invalid JSON body"): ("request body parse"),
        },
    ),
    "updates.py": (
        {},
        {
            ("check", "update check failed"): ("package update check"),
            ("refresh", "update check failed"): ("package update check"),
            ("run", "background update check failed"): ("package update check"),
            ("save_cache", "update-check cache is not writable"): ("package update check"),
        },
    ),
    "uploads/__init__.py": (
        {},
        {
            ("_add_chunk_via_writer", "UploadWriter instantiation failed for upload %s:"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("_add_chunk_via_writer", "UploadWriter.open() raised for upload %s: %s"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("_add_chunk_via_writer", "UploadWriter.write_chunk() raised for upload %s:"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("_finalize_writer", "UploadWriter.close() raised for upload %s: %s"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("_finalize_writer", "UploadWriter.close() return not JSON-serializabl"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("_restore_upload_configs", "UploadMixin._restore_upload_configs: fallback al"): (
                "restores saved upload slot configuration"
            ),
            ("_restore_upload_configs", "UploadMixin._restore_upload_configs: saved confi"): (
                "restores saved upload slot configuration"
            ),
            ("_safe_abort_writer", "UploadWriter.abort() raised for upload %s"): (
                "application UploadWriter code, but it receives upload bytes and metadata, never view state"
            ),
            ("cleanup", "suspending resumable upload %s failed"): (
                "ResumableUploadWriter suspend/park: upload bytes and metadata, never view state"
            ),
        },
    ),
    "uploads/resumable.py": (
        {},
        {
            ("__init__", "ResumableUploadWriter: state store %s is unavail"): (
                "resumable-upload state store"
            ),
            ("_delete_state_entry", "ResumableUploadWriter: state delete failed for %"): (
                "resumable-upload state store"
            ),
            ("_persist_chunk_progress", "ResumableUploadWriter: chunk state update failed"): (
                "resumable-upload state store"
            ),
            ("_persist_initial_state", "ResumableUploadWriter: state store write failed "): (
                "resumable-upload state store"
            ),
            ("resolve_resume_request", "resolve_resume_request: state store read failed "): (
                "resumable-upload state store"
            ),
            ("_abort_parked", "aborting suspended upload %s raised"): (
                "UploadWriter.abort() on a parked upload: upload bytes and metadata, never view state"
            ),
        },
    ),
    "pwa/mixins.py": (),
    "template_tags/__init__.py": (
        {},
        {
            ("_install_i18n_hooks", "Could not import the #2558 i18n hooks: %s"): (
                "tag handler registration and imports"
            ),
            ("_install_library_loader", "Could not import the library loader: %s"): (
                "tag handler registration and imports"
            ),
            ("_register_builtins", "Could not import built-in handlers: %s"): (
                "tag handler registration and imports"
            ),
            ("decorator", "Could not register assign tag handler '%s': Rust"): (
                "tag handler registration and imports"
            ),
            ("decorator", "Could not register tag handler '%s': Rust extens"): (
                "tag handler registration and imports"
            ),
            ("decorator", "Failed to register assign tag handler '%s': %s"): (
                "tag handler registration and imports"
            ),
            ("decorator", "Failed to register tag handler '%s': %s"): (
                "tag handler registration and imports"
            ),
            ("reregister_builtins", "Could not re-register built-in tag handler '%s':"): (
                "tag handler registration and imports"
            ),
        },
    ),
    "db/notifications.py": (
        {},
        {
            ("_run", "pg listener connect failed: %s — retrying in 1s"): (
                "PostgreSQL listener lifecycle"
            ),
            ("_run", "pg listener connection close failed"): ("PostgreSQL listener lifecycle"),
            ("_run", "pg listener disabled (permanent failure): %s — f"): (
                "PostgreSQL listener lifecycle"
            ),
            ("_run", "pg listener lost connection: %s — reconnecting i"): (
                "PostgreSQL listener lifecycle"
            ),
            ("areset_for_tests", "areset_for_tests: awaited task raised"): (
                "PostgreSQL listener lifecycle"
            ),
            ("reset_for_tests", "reset_for_tests: task cancel raised"): (
                "PostgreSQL listener lifecycle"
            ),
        },
    ),
    "components/gallery/views.py": (
        {},
        {
            ("_get_theme_css", "component gallery could not generate theme CSS f"): (
                "component gallery renders framework-shipped examples"
            ),
            ("_get_theme_options", "component gallery could not enumerate theme opti"): (
                "component gallery renders framework-shipped examples"
            ),
            ("_project_theme_defaults", "component gallery could not resolve the project "): (
                "component gallery renders framework-shipped examples"
            ),
            ("_render_component_cards", "gallery class render failed for variant %s"): (
                "component gallery renders framework-shipped examples"
            ),
            ("_render_component_cards", "gallery template render failed for variant %s"): (
                "component gallery renders framework-shipped examples"
            ),
            ("_render_head", "Optional djust_theming base CSS link unavailable"): (
                "component gallery renders framework-shipped examples"
            ),
            ("_render_head", "Theming asset version unavailable, linking style"): (
                "component gallery renders framework-shipped examples"
            ),
        },
    ),
    "state_backends/redis.py": (
        {
            ("get", "Failed to deserialize from Redis key '%s': %s"): (
                "legacy view-cache (de)serialization; explicit initialization bypasses the backend"
            ),
            ("set", "Failed to serialize to Redis key '%s': %s"): (
                "legacy view-cache (de)serialization; explicit initialization bypasses the backend"
            ),
        },
        {
            ("__init__", "Failed to connect to Redis: %s"): (
                "Redis connection, compression and stats"
            ),
            ("_compress", "Compression failed, storing uncompressed: %s"): (
                "Redis connection, compression and stats"
            ),
            ("_decompress", "Decompression failed: %s"): (
                "Redis connection, compression and stats"
            ),
            ("delete_all", "delete_all failed for prefix %s"): (
                "Redis connection, compression and stats"
            ),
            ("get_memory_stats", "Failed to get Redis memory stats: %s"): (
                "Redis connection, compression and stats"
            ),
            ("get_stats", "Failed to get Redis stats: %s"): (
                "Redis connection, compression and stats"
            ),
            ("health_check", "Redis health check failed: %s"): (
                "Redis connection, compression and stats"
            ),
        },
    ),
    "auth/core.py": (
        {},
        {
            ("_check_django_access_mixins", "check_view_auth: could not restore .request on %"): (
                "restores the mount request attribute"
            ),
        },
    ),
    "websocket_utils.py": (),
    "decorators.py": (),
    "tutorials/mixin.py": (),
    "simple_live_view.py": (),
    "security/error_handling.py": (
        {
            ("handle_exception", "%s%s: %s: %s"): (
                "the detailed branch follows the `expose_details is True and diagnostics_allowed()` gate"
            ),
            ("handle_exception", "%s%s: %s: %s", 1): (
                "the detailed branch follows the `expose_details is True and diagnostics_allowed()` gate"
            ),
            ("handle_exception", "%s%s: %s: %s", 2): (
                "the detailed branch follows the `expose_details is True and diagnostics_allowed()` gate"
            ),
        },
        {},
    ),
    "state_backends/memory.py": (
        {
            ("get", "InMemoryStateBackend.get: serialize/deserialize "): (
                "legacy view cache; explicit initialization bypasses the backend"
            ),
        },
        {
            ("health_check", "InMemory health check failed: %s"): ("backend health check"),
        },
    ),
    "db/decorators.py": (
        {},
        {
            ("_on_delete", "notify_on_save: failed to emit NOTIFY for %s pk="): (
                "NOTIFY emit on model delete"
            ),
            ("_on_save", "notify_on_save: failed to emit NOTIFY for %s pk="): (
                "NOTIFY emit on model save"
            ),
        },
    ),
    "template_filters.py": (
        {},
        {
            (
                "_ensure_custom_filters_bridged",
                "Failed to bridge Django custom filters to Rust t",
            ): ("filter bridging"),
            ("bridge_library_filters", "Failed to bridge custom filter '%s' to Rust regi"): (
                "filter bridging"
            ),
            (
                "_restore_missing_bridged_filters",
                "Failed to re-bridge custom filter '%s' to Rust r",
            ): ("filter bridging (#3208)"),
        },
    ),
    "forms.py": (
        {},
        {
            ("_ensure_model_instance", "Failed to re-hydrate model instance (label=%s, p"): (
                "re-hydration names a model label and pk, not state"
            ),
        },
    ),
    "hot_view_replacement.py": (
        {},
        {
            ("reload_module_if_liveview", "HVR: importlib.reload failed for %s"): (
                "dev-only module reload"
            ),
        },
    ),
    "tenants/backends.py": (
        {},
        {
            ("__init__", "TenantAwareRedisBackend failed to connect: %s"): ("Redis connection"),
        },
    ),
    "tenants/resolvers.py": (
        {},
        {
            ("_get_resolver", "Failed to import custom tenant resolver %s: %s"): (
                "resolver import path from settings"
            ),
        },
    ),
    "__init__.py": (
        {},
        {
            ("_dispatch", "[HotReload] HVR module reload failed"): "dev hot-reload server",
            (
                "enable_hot_reload",
                "[HotReload] Failed to start hot reload server: %",
            ): "dev hot-reload server",
            (
                "on_file_change",
                "[HotReload] Error broadcasting reload: %s",
            ): "dev hot-reload server",
            (
                "on_file_change",
                "[HotReload] template-library cache invalidation ",
            ): "dev hot-reload server",
        },
    ),
    "admin_ext/forms.py": (
        {},
        {
            (
                "get_field_info",
                "Failed to get field info for %s",
            ): "Django admin field metadata; outside the LiveView exposure policy",
            (
                "get_field_options",
                "Failed to get field options for %s",
            ): "Django admin field metadata; outside the LiveView exposure policy",
            (
                "get_field_value",
                "Failed to get field value for %s",
            ): "Django admin field metadata; outside the LiveView exposure policy",
        },
    ),
    "admin_ext/options.py": (
        {},
        {
            (
                "get_field_display_name",
                "Failed to get verbose name for %s",
            ): "Django admin field metadata; outside the LiveView exposure policy",
            (
                "get_queryset",
                "Failed to resolve FK for %s",
            ): "Django admin field metadata; outside the LiveView exposure policy",
        },
    ),
    "admin_ext/progress.py": (
        {},
        {
            (
                "_run",
                "admin_action_with_progress: %s failed",
            ): "Django admin action progress; outside the LiveView exposure policy",
            (
                "wrapper",
                "Failed to pin queryset PKs",
            ): "Django admin action progress; outside the LiveView exposure policy",
            (
                "wrapper",
                "Failed to reverse changelist URL",
            ): "Django admin action progress; outside the LiveView exposure policy",
            (
                "wrapper",
                "Failed to reverse djust_progress URL for %s",
            ): "Django admin action progress; outside the LiveView exposure policy",
        },
    ),
    "admin_ext/sites.py": (
        {},
        {
            ("get_plugin_nav", "Failed to reverse URL for %s"): "admin URL reversal",
        },
    ),
    "admin_ext/views.py": (
        {},
        {
            (
                "get_context_data",
                "Failed to build filter for %s",
            ): "Django admin filter building; outside the LiveView exposure policy",
        },
    ),
    "audit_ast.py": (
        {},
        {
            ("run_ast_audit", "Scanner failed on %s: %s"): "static audit scanner",
            ("scan_python_source", "Cannot read %s: %s"): "static audit scanner",
        },
    ),
    "audit_live.py": (
        {},
        {
            ("probe_paths", "path probe failed for %s: %s"): "live security-audit probes",
            ("probe_paths", "path probe failed for %s: %s", 1): "live security-audit probes",
            (
                "probe_websocket_origin",
                "websocket close after CSWSH probe failed: %s",
            ): "live security-audit probes",
            (
                "probe_websocket_origin",
                "websocket probe rejected at handshake: %s",
            ): "live security-audit probes",
        },
    ),
    "auth/admin_views.py": (
        {},
        {
            (
                "_get_providers",
                "Per-provider active-user stats unavailable: %s",
            ): "admin OAuth statistics",
            (
                "get_context_data",
                "OAuth stats unavailable (allauth probe failed): ",
            ): "admin OAuth statistics",
        },
    ),
    "auth/djust_admin.py": (
        {},
        {
            ("get_context", "OAuth provider/user stats unavailable: %s"): "admin OAuth statistics",
        },
    ),
    "backends/redis.py": (
        {},
        {
            (
                "__init__",
                "RedisPresenceBackend failed to connect: %s",
            ): "presence backend connection",
        },
    ),
    "bug_capture_store.py": (
        {},
        {
            (
                "_assert_server_requires_auth",
                "RedisSnapshotStore: probe connection close faile",
            ): "snapshot-store connection probe",
        },
    ),
    "checks/integrations.py": (
        {},
        {
            (
                "check_admin_widgets",
                "LiveView import failed; skipping admin widget au",
            ): "system-check imports",
            (
                "check_admin_widgets",
                "admin_ext not importable; skipping A072/A073",
            ): "system-check imports",
        },
    ),
    "checks/templates.py": (
        {},
        {
            (
                "check_undefined_template_vars",
                "T018: failed to statically check template contex",
            ): "static template check",
        },
    ),
    "cli.py": (
        {},
        {
            ("cmd_replay", "django.setup() skipped for `djust replay`: %s"): "CLI bootstrap",
        },
    ),
    "components/gallery/registry.py": (
        {},
        {
            (
                "get_gallery_data",
                "gallery: discover_component_classes() failed: %s",
            ): "component gallery discovery",
            (
                "get_gallery_data",
                "gallery: discover_template_tags() failed: %s",
            ): "component gallery discovery",
        },
    ),
    "components/icons.py": (
        {},
        {
            (
                "_get_icon_sets",
                "Custom DJUST_COMPONENTS_ICON_SETS unavailable: %",
            ): "icon-set settings",
        },
    ),
    "contrib/uploads/gcs.py": (
        {},
        {
            (
                "abort",
                "GCSMultipartWriter.abort: DELETE session failed ",
            ): "cloud upload session cleanup",
        },
    ),
    "contrib/uploads/s3_events.py": (
        {},
        {
            (
                "_fire_hook",
                "on_upload_complete hook raised for upload_id=%s ",
            ): "upload-complete hook; receives upload metadata, never view state",
        },
    ),
    "deploy_cli.py": (
        {},
        {
            (
                "_create_tarball",
                "Failed to add %s to tarball: %s",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_create_tarball",
                "Failed to add %s to tarball: %s",
                1,
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_decode_id_token_email",
                "Could not decode id_token email claim: %s",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_ensure_logged_in",
                "Could not reach /me/: %s — proceeding with saved",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_ensure_project_exists",
                "project precondition GET failed: %s — proceeding",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_refresh_oauth_token",
                "refresh_token request failed: %s — skipping refr",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "_run_deploy_doctor",
                "deploy doctor failed; skipping",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "deploy_dir",
                "status poll failed; retrying",
            ): "deploy CLI: HTTP calls and tarball assembly",
            (
                "logout",
                "Logout API call failed (proceeding to remove loc",
            ): "deploy CLI: HTTP calls and tarball assembly",
        },
    ),
    "observability/dry_run.py": (
        {},
        {
            (
                "_uninstall",
                "DryRunContext failed to restore %s.%s: %s",
            ): "dry-run attribute restore",
        },
    ),
    "pwa/manifest.py": (
        {},
        {
            ("_get_default_config", "Error loading PWA config: %s"): "PWA manifest generation",
            ("manifest_view", "Error generating manifest: %s"): "PWA manifest generation",
        },
    ),
    "pwa/service_worker.py": (
        {},
        {
            (
                "_get_default_config",
                "Error loading service worker config: %s",
            ): "service-worker script generation",
            (
                "service_worker_view",
                "Error generating service worker: %s",
            ): "service-worker script generation",
        },
    ),
    "pwa/storage.py": (
        {},
        {
            (
                "_deserialize",
                "Failed to deserialize storage data: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "_update_action_status",
                "Failed to update action status: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "add",
                "Failed to add action to sync queue: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "cleanup_expired",
                "IndexedDB cleanup error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "clear",
                "IndexedDB clear error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "clear",
                "localStorage clear error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "delete",
                "IndexedDB delete error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "delete",
                "localStorage delete error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            ("get", "IndexedDB get error: %s"): "client-storage bridge and sync-queue bookkeeping",
            (
                "get",
                "localStorage get error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "get_pending",
                "Failed to get pending actions: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "keys",
                "IndexedDB keys error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "keys",
                "localStorage keys error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "remove_completed",
                "Failed to remove completed actions: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "retry_action",
                "Failed to retry action %s: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            ("set", "IndexedDB set error: %s"): "client-storage bridge and sync-queue bookkeeping",
            (
                "set",
                "localStorage set error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "size",
                "IndexedDB size error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
            (
                "size",
                "localStorage size error: %s",
            ): "client-storage bridge and sync-queue bookkeeping",
        },
    ),
    "scaffolding/generator.py": (
        {},
        {
            ("_run_cmd", "Command failed: %s\nstdout: %s\nstderr: %s"): "scaffolding subprocess",
        },
    ),
    "template_tags/pwa.py": (
        {},
        {
            (
                "_render_django_tag",
                "Error rendering {%% %s %%}",
            ): "PWA tag rendered with an empty context",
        },
    ),
    "theming/context_processors.py": (
        {},
        {
            (
                "theme_context",
                "skipping theme-context cache write on %s request",
            ): "theme context cache",
        },
    ),
    "theming/gallery/catalogue.py": (
        {},
        {
            (
                "_render_template_examples",
                "Could not render template component %s: %s",
            ): "catalogue renders framework-shipped examples",
        },
    ),
    "theming/gallery/component_registry.py": (
        {},
        {
            (
                "render_python_component_example",
                "Could not render component %s: %s",
            ): "catalogue renders framework-shipped examples",
        },
    ),
    "theming/high_contrast.py": (
        {},
        {
            ("<module>", "  %s: Error - %s"): "accessibility report script",
        },
    ),
    "theming/inspector.py": (
        {},
        {
            ("theme_css_api", "theme inspector API failed"): "theme inspector dev API",
            ("theme_inspector_api", "theme inspector API failed"): "theme inspector dev API",
            ("theme_inspector_api", "theme inspector API failed", 1): "theme inspector dev API",
        },
    ),
    "theming/rust_handlers.py": (
        {},
        {
            (
                "register_with_rust_engine",
                "Rust extension unavailable; theme tags work via ",
            ): "theme tag registration",
        },
    ),
    "uploads/storage.py": (
        {},
        {
            ("delete", "Redis delete failed for upload %s: %s"): "upload state store",
            ("update", "Redis pipeline update failed for upload %s; fall"): "upload state store",
        },
    ),
    "uploads/views.py": (
        {},
        {
            ("get", "UploadStatusView: state store read failed for %s"): "upload status store read",
        },
    ),
    "pwa/utils.py": (
        {},
        {
            (
                "cleanup_old_data",
                "Cleanup failed: %s",
            ): "server-side offline-state utilities with fixed merge strategies; the cache is in-process server memory",
            (
                "cleanup_old_data",
                "Error checking key %s during cleanup: %s",
            ): "server-side offline-state utilities with fixed merge strategies; the cache is in-process server memory",
            (
                "compress_state",
                "Failed to compress state: %s",
            ): "server-side offline-state utilities with fixed merge strategies; the cache is in-process server memory",
            (
                "decompress_state",
                "Failed to decompress state: %s",
            ): "server-side offline-state utilities with fixed merge strategies; the cache is in-process server memory",
            (
                "merge_offline_changes",
                "Merge failed: %s",
            ): "server-side offline-state utilities with fixed merge strategies; the cache is in-process server memory",
        },
    ),
    "serialization.py": (
        {},
        {
            (
                "_rehydrate_component",
                "state round trip: %s(**state) refused the saved ",
            ): "value-free: logs the class path, the saved kwarg names and the exception type only",
        },
    ),
    "mixins/request.py": (
        {
            ("post", "<Name>"): (
                "legacy or DEBUG only (D-a revised): in production a nonlegacy view "
                "returns the generic response first"
            ),
        },
        {
            ("_inject_debug", "Failed to inject debug info"): "debug payload assembly",
            ("_watch_disconnect", "is_disconnected() raised; halting watcher"): "ASGI probe",
        },
    ),
    # assign_async's runners and the SSE deferred flush now log through
    # log_failure_for; nothing raw remains.
    "mixins/async_work.py": (),
    "sse.py": (),
    "observability/middleware.py": (
        {},
        {
            ("has_valid_token", "Observability token unavailable; refusing reques"): (
                "token derivation from settings (e.g. an empty SECRET_KEY); no view exists"
            ),
        },
    ),
    "auth/accounts/backends/allauth.py": (
        {},
        {
            ("providers", "Listing allauth social providers failed; showing"): (
                "allauth provider configuration on the plain-Django login page; no LiveView exists"
            ),
        },
    ),
    "checks/accounts.py": (
        {},
        {
            ("check_accounts", "djust.%s account check raised; skipping it"): (
                "startup system check over settings; no view exists"
            ),
        },
    ),
    "security/csrf.py": (
        {},
        {
            ("bind_csrf_cookie", "Could not bind the browser CSRF secret to a rebu"): (
                "Django CSRF middleware on a rebuilt socket request, before any view mounts"
            ),
        },
    ),
    "multiloop.py": (
        {},
        {
            ("_run", "djust serve: event loop %d failed"): (
                "the `djust serve --loops` launcher thread (#3128): a uvicorn server that "
                "crashed; no view exists"
            ),
        },
    ),
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
