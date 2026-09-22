# ADR-038 completion plan

Snapshot of the gap analysis taken at `f9b256aa6` on 2026-09-22, used as the
work queue for finishing E1, E2, E3, E5 and E6. It is a plan, not evidence:
an item closes only with the implementation commit, its named tests and run
results recorded in the [ledger](../component-conventions-implementation.md).
Decisions D-a to D-q are adopted in ADR-038's *Completion decisions* section.

Every OPEN item below was checked against the code. Where I relied on the ledger alone, I say so. Nothing was edited. Paths are relative to `python/djust/` unless they start with `docs/` or `tests/js/`.

**Headline:**
- The log part of E1 really is closed. `KNOWN_OPEN = {}`, `RUNTIME_KNOWN_OPEN = {}`, and the `log_exposure_unreviewed.json` baseline is `{}`.
- What remains in E1:
  - **Outer error handlers that no protected scope covers:** the HTTP GET, the SSE stream GET, the WS `receive` catch-all and the constructor catch.
  - **Five browser, debug and presence destinations** that no inventory row covers.
  - **Destination-level tests** for rows that are only tested at function level.
- The largest real gaps are in E3 and E2:
  - **E3:** root background work, `url_change` and the four consumer-originated turns (NOTIFY, push, tick, hooks) do not persist state, refresh the signed snapshot, or re-authorize for explicit views.
  - **E2:** forms, uploads, interactive components, activities and the form template tags do not work in explicit mode, and nothing reports that.

---

## Constructor guard, and what activation (E6) changes

**Where it is.** `live_view.py:568` `LiveView._validate_exposure_configuration`, called first in `LiveView.__init__` (`:609`). It raises `ImproperlyConfigured`:
- for `"explicit"` ("not yet available");
- for any policy that is not exactly `"legacy"`;
- for any legacy view that declares `state(persist=…/client=True)`. It checks this by reading class `vars()` along the MRO, and evaluates nothing.

**What activation changes in code:**
1. **Guard.** Accept `"explicit"`. Keep rejecting invalid policies. Keep rejecting exposure grants on legacy views. Add config-time rejections for whatever E6 excludes; recommended: `use_actors=True` combined with explicit. Keep the runtime refusals at `runtime.py:2349` and `:3268` as a second line of defence.
2. **Hot class swap.** `hot_view_replacement.py:apply_class_swap` (~`:320`) assigns `__class__` without re-running the guard. In DEBUG this is a way around the guard today, so it must re-validate.
3. **Docstrings.** Remove the "not an enabled LiveView policy" wording (`_exposure.py:1-6`, `mixins/context.py:479`).
4. **Publication.** Make the `state()` grants public, add system checks (ADR-037), a changelog entry, and a client rollout note. The ledger says older clients do not understand `async_complete`.
5. **Tests.** Remove the `monkeypatch.setattr(LiveView, "_validate_exposure_configuration", lambda self: None)` bypass from 24 files: `test_exposure_{transport_diagnostics, runtime_log_diagnostics, sse_navigation, render_cache, mount_diagnostics, child_identity, presence_diagnostics, http_diagnostics, activity_notify_auth, child_lifecycle, event_diagnostics, actor_mount, child_reuse, child_pruning, consumer_hook_diagnostics, child_events, api_dispatch, child_context_failure, object_permission_diagnostics, http, runtime, callback_diagnostics, child_async, child_mount}.py`. After activation the bypass also skips the grant and invalid-policy checks, so leaving it in would hide regressions.

**Tests that assert the guard**, all in `tests/test_exposure_policy_guard.py`:
- **Must be rewritten** to assert that the explicit mount succeeds with the sentinel absent:
  - `test_explicit_policy_fails_before_any_legacy_initialization`
  - `test_inherited_explicit_policy_is_rejected`
  - `test_http_view_cannot_render_using_an_unsupported_policy`
  - `test_websocket_reports_unsupported_policy_without_mounting`
- **Stay as they are:**
  - `test_unknown_policy_is_not_treated_as_legacy`
  - `test_legacy_views_cannot_silently_ignore_exposure_grants`
  - `test_guard_does_not_evaluate_properties_or_factories`
  - `test_legacy_policy_preserves_current_context_behavior`

No other test or doc asserts the guard's messages. One strategy doc, `docs/strategy-sessions/2026-05-19-v1.1-readiness.md`, mentions them.

---

## E1: sink inventory and closure

**Acceptance text:** "Enumerate actual rendering, persistence, snapshot, browser-storage and diagnostic sinks, including actor and root-background paths. For each, map policy enforcement and a sentinel test at the destination; identify unsupported paths explicitly. No implicit context/attribute fallback or unclassified sink may remain at activation."

### Done, with evidence
- **Logs:** `test_log_exposure_pin.py` covers every exception-carrying log call in the package. Open tables and the baseline are empty (verified). Reproductions:
  - `test_exposure_runtime_log_diagnostics.py`, `test_exposure_consumer_hook_diagnostics.py`, `test_exposure_presence_diagnostics.py`
  - `test_exposure_pwa_diagnostics.py`, `test_exposure_object_permission_diagnostics.py`, `test_exposure_value_log_diagnostics.py`
  - `test_exposure_ownerless_log_diagnostics.py`, `test_exposure_root_async_diagnostics.py`
  - `test_notify_released_start_async_2946.py`
- **Runtime handler, render and callback catches:** `test_exposure_event_diagnostics.py`, `test_exposure_callback_diagnostics.py`.
- **Mount catches** (init, auth, mount, handle_params, initial render, actor render): `test_exposure_mount_diagnostics.py`.
- **Runtime-owned inbound WS/SSE messages** (outer boundary in `dispatch_message`): `test_exposure_transport_diagnostics.py`.
- **HTTP POST event failure:** `test_exposure_http_diagnostics.py`.
- **Render cache and backends:** `test_exposure_render_cache.py`. Bug-capture store: `test_exposure_debug.py::test_bug_capture_store_destination_holds_only_the_debug_projection`.
- **HTTP API assigns:** `test_exposure_api_dispatch.py`.
- **Direct state APIs:** `test_exposure_state_apis.py`. Signed snapshots: `test_exposure_snapshots.py`, `test_exposure_sse_navigation.py`.
- **Service-worker state cache:** `tests/js/exposure_sw_state_storage.test.js`.
- **Actors refused:** `test_exposure_actor_mount.py`, including `test_actor_event_refuses_after_a_late_policy_transition`.
- **Debug assigns, time travel, bug capture:** `test_exposure_debug.py`.

### Open, in dependency order

**E1-1. Protected entry scopes on transport entry points that have none (M).**
- Today `handle_exception` defaults to `expose_details=True`, and `_details_allowed` defaults to `True` outside any scope.
- The fix: open a `diagnostic_scope()` plus `restrict_diagnostics(view_or_class)` at each entry, or pass `expose_details=uses_legacy_exposure(...)`.
- Checked routes:
  - (a) **HTTP GET** `mixins/request.py:get` (`:139`) and `aget` (`:447`). Exceptions from mount, `handle_params`, `get_context_data` and render reach Django. Under DEBUG, Django's technical 500 page shows the exception and frame locals. Nothing tests this.
  - (b) **SSE stream GET** `sse.py:DjustSSEStreamView.get`, which calls `dispatch_mount` at `:588`. `dispatch_mount` is not decorated with `_runtime_diagnostic_scope`, so anything that escapes its local catches reaches Django.
  - (c) **SSE navigation replacement** `sse.py:SSESession._replace_view` (`:238`).
  - (d) **WS `receive` catch-all** `websocket.py:~2442`. It uses `handle_exception(e)` with details allowed. It receives everything that escapes the handlers that bypass `dispatch_message`: `mount_batch`, `live_redirect_mount` (→ `handle_mount` → `dispatch_mount`, `:2522`), `upload_register`/`upload_resume`, `presence_heartbeat`, `cursor_move`, `request_html` (re-renders the view; `_handle_request_html_locked`), time travel, `bug_capture_share`.
  - (e) **Constructor** `runtime.py:_instantiate_view` (~`:4827`) calls `handle_exception` with no `expose_details`. The class policy can be read before the instance exists.
- **Suggested tests:**
  - An `__init__`/mount/render sentinel under DEBUG, explicit versus a legacy control.
  - Assert the sentinel is absent from the response body (technical 500), the WS error frame, the SSE stream, the traceback ring and the logs.
  - Use a real `RequestFactory`/ASGI path, `WebsocketCommunicator` with `request_html` / `live_redirect_mount` / `mount_batch`, and the SSE GET.
- **Decision (D-a):** should explicit HTTP errors bypass Django's technical 500 page (return a generic 500 and log without values)? That also changes `got_request_exception`, which Sentry relies on. **Recommendation:** yes for nonlegacy views; still send the signal with a value-free exception.

**E1-2. Exception text that reaches a destination without passing a log call (S–M).** The pin cannot see this. Known cases:
- `pwa/mixins.py:_process_sync_queue` ~`:512`: `push_event("offline:sync_error", {"error": str(e)})`. The client frame is ungated; only the log next to it is gated. The sweep found this; it is not in the ledger. **Fix:** gate it like the log. **Test:** raise from `sync_queue.get_pending` and assert on the push frame.
- `pwa/mixins.py:552/592/632` `mark_failed(..., str(e))`. The ledger records this as not fixed, and `test_exposure_pwa_diagnostics` pins it as leaking. The queue looks like server memory (D1 allows that). **Decision:** classify as server memory in the inventory, or gate it. **Recommendation:** gate it (it is cheap) and add an inventory row.
- `decorators.py:action` stores `_action_state[name]["error"] = str(exc)`, which is rendered through the action provider (overlaps E2-4).
- **Suggested structural test:** extend the pin's AST scan to `str(exc)`/`repr(exc)`/f-string uses of a caught exception that flow into `push_event`, `JsonResponse`, `send*` or storage writes. It would still be a lexical check, like the existing client-frame scan.

**E1-3. Add inventory rows for destinations the sweep found unlisted (S each, M in total).**
- `static/djust/service-worker.js` **VDOM_CACHE** (`:245-254`; writer `src/03-websocket.js:706-717` `cacheVdom`). The mount HTML goes to CacheStorage on disk, 30-minute TTL, keyed by pathname, not cleared on logout. It is opt-in through `vdomCache`.
- **SHELL_CACHE** (`handleNavigate` `:87-133`). The HTML outside `<main>` is persisted with no TTL and no identity binding. Opt-in `instantShell`.
- **Observability SQL capture** (`observability/sql.py:_execute_wrapper` → `views.py:sql_queries`, and the MCP proxy `mcp/server.py:730`). It records raw query params, often derived from view attributes. DEBUG and localhost only.
- **Presence:**
  - `presence.py:update_cursor_position` rebroadcasts the private `_presence_meta` to peers on every `cursor_move`.
  - `track_presence` adds `username`/`user_id` automatically.
- **Excluded or legacy-only classes to list:** `simple_live_view.py` (reflective, no policy check at all; explicit silently ignored), `drafts` localStorage (template-author opt-in, like `{% cache %}`), handler metadata / `upload_configs` (configuration, not instance state).
- **Tests:** JS tests like `exposure_sw_state_storage.test.js` for the VDOM and shell caches; a DEBUG test for the `sql_queries` endpoint with a param sentinel; a WS presence test that a sentinel in `_presence_meta` is not rebroadcast unless declared.
- **Decisions:**
  - (D-b) VDOM and shell caches under explicit views. **Recommendation:** the server marks explicit pages `no-store` for the worker, so the client skips `cacheVdom`/shell capture until E5 decides TTL and logout behaviour.
  - (D-c) Presence meta. **Recommendation:** classify as D6 application output, since the app passes it to `track_presence`. Document the rebroadcast and have `track_presence` stop filling in `username` for explicit views.
  - (D-d) SQL params. **Recommendation:** redact params for nonlegacy owners.

**E1-4. Destination-level tests for rows now tested only at function level (S–M).**
- Debug: HTTP `_inject_debug` (`mixins/request.py:~980`) and the inline `window.DJUST_DEBUG_INFO` script (`mixins/post_processing.py:~310`). `test_exposure_debug` calls the projection functions directly.
- Context-derived sinks with no destination test: `render_embedded_child_html` (`websocket.py:529`), React props (`post_processing.py:261`), `renderers/native.py:169`.
- The inventory row "Root WS/SSE foreground frames: full destinations and debug/provider matrix still open" is still correct. Fold it into E5.

**E1-5. Close the root-background row (M).** The diagnostic half is done. The remaining parts (render failure, persistence, snapshot refresh) are E3-1; the row cannot close until E3-1 lands.

**E1-6. Inventory "Remaining routes" 1–5.**
- **Route 1 (backend writers):** done for render cache, observations, `session_utils`, the CLI and actors. Mount, navigation, teardown and reconnect writers were not re-traced by name after the render-cache fix. **Item:** a one-pass grep of `get_backend()`/`backend.set`, recorded in the inventory (S). I found no new writer, but did not do a full trace.
- **Route 2:** mostly covered by E1-1 and E1-3 above. The WebSocket-only mount background work is E3-2.
- **Route 3:** tests per E1-1 to E1-4.
- **Route 4:** equals E2.
- **Route 5:** the D6 cross-check is part of E5.

### Contradictions and stale text to fix (S, documentation only)
- **Inventory, "Consumer-local exception logging finding".** It still says "`_run_async_work` … open … `KNOWN_OPEN` authoritative". The runtime section of the same note, and the ledger, say those sites were converted. The code agrees with the latter (`KNOWN_OPEN = {}`).
- **Inventory "Exception logs" row.** It lists "foreground events" and "outer transport catches" as open:
  - Foreground events are closed (`test_exposure_event_diagnostics.py`).
  - Runtime-owned outer transport is closed (`test_exposure_transport_diagnostics.py`).
  - Only the routes in E1-1 remain.
- **Ledger "Implemented foundation" (~line 1406).** It says "Sticky-child and actor persistence integration remains pending". Sticky persistence was implemented later; actors are refused. It also says "E1 … actor/backend and browser-storage caller inventories are the next" in several older slices. These are superseded, and the ledger's own rule allows that, but the foundation bullet reads as current.
- **Test files the ledger and inventory never name:** `test_exposure_transport_diagnostics.py`, `test_exposure_http_diagnostics.py`, `test_exposure_child_identity.py`, `test_exposure_child_lifecycle.py`, `test_exposure_child_pruning.py`, `test_exposure_child_context_failure.py`, `test_exposure_object_permission_diagnostics.py`, `test_exposure_runtime_log_diagnostics.py`, `test_exposure_consumer_hook_diagnostics.py`, `test_exposure_presence_diagnostics.py`, `test_exposure_pwa_diagnostics.py`. E6 evidence review needs file-level citations.

---

## E2: provider contract closure

**Acceptance text:** "Complete bounded manifests and lifecycle tests for components, forms, actions, streams and uploads, including inheritance, dynamic context and invalidation. Resolve schema/codec needs and migration/expiry handling. Exercise deliberate ORM rendering without granting automatic persistence or client disclosure."

### Done, with evidence
- **Explicit base context** (`mixins/context.py:_get_explicit_context_data`, `:476-547`) provides only component descriptors, `_action_state` and `streams`, and checks collisions. Tests in `test_exposure_context.py`:
  - `test_base_context_does_not_discover_attributes_or_state`
  - `test_action_and_stream_context_are_explicit_framework_providers` (calls the context method directly, not a real dispatch)
  - `test_registered_components_are_bound_per_view_and_not_discovered_from_attrs`
  - `test_render_context_provider_collision_cannot_silently_replace_component`
  - `test_context_processors_are_render_only_and_preserve_view_precedence`
  - `test_processors_cannot_overwrite_reserved_providers`
  - `test_deliberate_models_render_natively_without_serializing_other_fields[django|rust]`
- **Contract, codec bounds, inherited grants:** `test_exposure_contract.py`. Schema rejection: `test_exposure_snapshots.py::test_schema_change_and_tampering_are_rejected`, `test_exposure_runtime.py::test_reconnect_rejects_invalid_envelopes_before_assignment`.

### Open, in dependency order

**E2-0. Decision: the manifest shape (decision, then M).** No provider manifest exists. `ExposureContract` covers only `state()` fields; `_djust_injects_context` in `forms.py:73` is only a type-check hint. **Recommendation:** an immutable `ProviderContract` (rendered / tracked / persisted / client fields plus codec) next to `FieldExposure` in `_exposure.py`, folded into the view's `ExposureContract` schema digest so that a provider change invalidates stored envelopes.

**E2-1. Framework mixin providers write context without registering (M).**
- Affected: `tenants/mixin.py:240` (`tenant`), `wizard.py:270` (`form_data`/`field_html` from `wizard_*`), `drafts.py:76`, `audio.py:101`, `pwa/mixins.py:136,:395`, plus `_sync_state_to_rust` adding `csrf_token`/`DATE_FORMAT` (`mixins/rust_bridge.py:717-742`).
- They are not in `_explicit_context_provider_keys`, so collisions with application keys are silent, and so are application `context[x] = …` writes after `super()`.
- **Test:** each mixin under explicit policy; a colliding application kwarg raises; the keys are absent from persisted, client and snapshot destinations.

**E2-2. Template tags that need a raw `view` in context, which explicit mode rejects (M).** I found these; they are not in the ledger.
- `templatetags/live_tags.py`: `live_form`/`live_field`/`live_errors`/`field_value`/`has_errors` take `view` as an argument.
- `DjActivityNode.render` (`:1219`) registers activities through `context.get("view")`. Under explicit it silently does not register, so the gating of hidden-activity events is skipped.
- `ColocatedHookNode._namespace` (`:893`) silently falls back to the global namespace.
- **Fix:** resolve through `get_active_parent_view()` (already used by `live_render`, `:1762`) or a bounded facade.
- **Test:** an explicit view using `{% dj_activity %}` still refuses events to a hidden activity; strict hook namespacing holds.
- Uncertain: whether the Rust renderer routes these tags through the same Python nodes. Check both renderers.

**E2-3. Forms, with ADR-035 (L).**
- Under explicit, `FormMixin` state (`form_data`, `field_errors`, `form_errors`, `is_valid`, messages, `model_pk`) is not provided, not persisted and not rejected. Templates silently render empty, and reconnect or POST loses the form and its model reference.
- **Items:** a form provider; declared persistence for input and errors, with password-type widgets never persisted or debugged; replace `model_pk` with an ADR-035 identity reference that is re-resolved under current permissions.
- **Tests:** explicit `validate_field`/`submit_form` over the runtime and HTTP POST; a `PasswordInput` sentinel absent from session, frames, snapshot and debug; reconnect revalidates.
- **Decision (D-e):** do form input and errors persist on the server by default? **Recommendation:** none by default; opt-in per field; sensitive widgets can never be persisted.

**E2-4. Actions (S–M).** Real `@action` dispatch under explicit. The `error` text is `str(exc)` (`decorators.py:~446`).
- **Decision (D-f):** render the raw error for explicit views? **Recommendation:** a generic message unless the handler raises a declared user-facing exception type.
- **Test:** an action that fails with a sentinel; the rendered HTML, frames and debug output show only the generic text.

**E2-5. Streams (S).** A real `self.stream(...)` insert/delete in an explicit runtime event, with Rust and Django renders compared.

**E2-6. Uploads (M).**
- No `uploads` context under explicit. `_get_upload_context` (`uploads/__init__.py:1487`) is dead code in both modes.
- The mount frame `upload_configs` (`runtime.py:3141-3150`) is configuration only.
- Entries in flight are lost on reconnect.
- **Items:** an upload provider from `get_upload_state()` that excludes or declares `writer_result` and `client_name`; a test on the mount frame and the rendered context.
- **Decision (D-g):** do entries in flight survive reconnect under explicit? **Recommendation:** no, for v1. The client re-registers, which matches ADR-038's "remount" posture.

**E2-7. Components (L).**
- ADR-034 interactive `ComponentDeclaration`s (for example `DropdownMenu`) never enter `_component_descriptors`: `_component_subscriptions.py:75-82` does not call `super().__set_name__`. They are silently missing from explicit context.
- A State-less `LiveComponent` provides the shared class-level descriptor (`components/base.py:1086-1089`).
- Components have no persistence contract, so their state resets on reconnect.
- Instance-assigned components are silently absent.
- **Tests:** nested components and subclass override; interactive bindings; a component event through the explicit runtime. This also gives the end-to-end reproduction the ledger deferred for `_render_scoped_component`.
- **Decision (D-h):** instance-assigned components. **Recommendation:** at first render in explicit mode, raise a diagnostic naming the attribute; no automatic discovery.

**E2-8. Invalidation and dynamic context (M).**
- `_snapshot_assigns` (`websocket.py:335`) still walks all of `__dict__` for explicit views. That is conservative, not a leak.
- `_sync_state_to_rust`'s `changed_keys` loop matches attribute names such as `_state_x`, not context keys.
- There is no dependency API. **Items:** an explicit-mode change detector keyed on declared fields and provider manifests; an invalidation API; add `_explicit_context_provider_keys` to `_FRAMEWORK_INTERNAL_ATTRS` (`live_view.py:118`).
- **Tests:** derived context re-renders when its dependency changes; an opaque dependency renders conservatively; no-op parity between Django and Rust.

**E2-9. Schema, codec, expiry (M).**
- Contract `version` is always 1 and applications cannot set it.
- Server `max_age` is fixed at 3600 (`_exposure_sessions.py:103` passes no value).
- The only codec is `json-primitives-v1` (`_exposure.py:24`), and there is no translation hook for old schemas.
- **Items:** a class-level contract version; a `DJUST_SERVER_STATE_MAX_AGE` setting; an optional `migrate_state(old_schema, values)` hook that runs before `prepare_restore`.
- **Decision (D-i):** which codecs come next? **Recommendation:** ship v1 with JSON primitives only; later add `Decimal`, `date`/`datetime`, `UUID` and model references (ADR-035 identity). Nothing that uses `repr`.
- **Decision (D-j):** old envelopes, including unindexed prototype envelopes. **Recommendation:** remount and let session expiry clean them up; no guessing.

**E2-10. Deliberate ORM rendering end to end (S–M).**
- Today the Rust side is tested through `DjustTemplateBackend.from_string`, not `_sync_state_to_rust`/`render_with_diff`, and nothing asserts on persistence or client output.
- **Test:** a model with password and unrendered-field sentinels rendered through explicit WS and HTTP. Assert both sentinels are absent from the session store, `server_state_adapter().load()`, frames, the snapshot and debug output.

---

## E3: ownership and lifecycle closure

**Acceptance text:** "Cover mount/parent-queued child work, descendant and repeated-instance routing, lazy/nonsticky and mixed policies, shell reconstruction, removal/re-addition, and root-background authorization and persistence. Test revocation and failed storage without stale delivery."

### Done, with evidence
- **Fresh eager sticky children:** `test_exposure_child_mount.py`. Reuse and identity: `test_exposure_child_reuse.py`, `test_exposure_child_identity.py`.
- **Routed child events:** `test_exposure_child_events.py`. Disposal and cancellation: `test_exposure_child_lifecycle.py`. Pruning: `test_exposure_child_pruning.py`.
- **Child background work routed from child events:** `test_exposure_child_async.py`, including `test_completion_reloads_authority_before_result_handler`.
- **Adapter-level same-type siblings:** `test_exposure_children.py::test_same_type_siblings_and_nested_slots_are_independent`.
- **NOTIFY-released activity event authorization:** `test_exposure_activity_notify_auth.py`.

### Open, in dependency order

**E3-1. Root background authorization and persistence (M–L).**
- Verified in `runtime.py:_execute_async_task` (`:5911`) and `_render_async_result` (`:6024`). For explicit roots there is:
  - no fresh authorization before the callback or before completion;
  - no `persist="server"` save after `handle_async_result`;
  - no `state_snapshot_signed` refresh.
- The same is true on the consumer's `_run_async_work` (`websocket.py:1184`).
- **Fix:** mirror the child pattern `_child_async._authorize`: `authorize_event(root, fresh_socket_request(root), runtime._explicit_mount_binding)` plus `enforce_object_permission` before and after the callback; then the explicit save used by `_persist_state_after_event` and `_persist_explicit_children_after_event` (150 ms deadline); then `_explicit_event_snapshot`.
- **Tests:** session deleted mid-task means no result render and no write; storage failure means a static error, no success frame, and full HTML on the next update; the token is refreshed after the background mutation; the same over SSE.
- **Decision (D-k):** what does a revoked background result do? **Recommendation:** drop it, send a static error and close with 4403, as foreground events do.

**E3-2. Other turns that mutate an explicit root outside the runtime's authorized-and-save path (M).** I verified none of them saves or refreshes the snapshot; `websocket.py` has no persist call anywhere.
- `runtime.py:dispatch_url_change` (`:4669`): no fresh `authorize_event`; object permission uses the mount-time `view.request`; no save, no token refresh. It also changes the route that the binding and pruning use.
- Consumer `server_push`, `db_notify` → `handle_info` and released activity events (`_dispatch_single_event` authorizes since the NOTIFY slice but does not save), `_run_tick`, and the presence and cursor hooks.
- **Tests:** per path, mutate a `persist="server"` field, then reconnect in a fresh runtime and assert restoration (or a documented non-persistence); revoke the session and assert no delivery.
- **Decision (D-l):** should server-originated turns (tick, push, NOTIFY) persist explicit state? **Recommendation:** yes, through one shared "post-turn explicit commit" helper, so every mutating turn uses the same save and token refresh.

**E3-3. Child work queued at mount or by the parent (M).**
- `_child_async.dispatch_child_work` is called only from routed child events (`runtime.py:4410-4417`).
- `start_async` queued in a child's `mount()`, or by a parent handler on a child, is not dispatched for explicit children. It sits until the child's next event or is lost; I did not confirm which by running it.
- **Fix:** capture child queues after mount render and after parent turns.
- **Test:** a child calls `start_async` in mount; its result arrives as a scoped `embedded_update` with authorization reloaded; no parent batch acknowledges it.

**E3-4. Descendant and repeated-instance routing (M).** No runtime test routes events to two explicit children of the same type, or to a grandchild. **Test:** two same-type explicit siblings plus a nested one; events, saves and background results land only on their target (identity digest checked).

**E3-5. Lazy, non-sticky and mixed policies (L).**
- Non-sticky and lazy explicit children mount with no identity and no persistence (`live_tags.py:2262-2330` covers only `sticky_kwarg and explicit_child`). Only their failure path is fixed (`test_exposure_child_context_failure.py`).
- **Decision (D-m):** support them in v1, or refuse at the tag? **Recommendation:** support transient (non-persisted) non-sticky explicit children with the identity check; refuse `lazy=True` on explicit children with a static tag error until a lazy-fill authorization path exists; refuse mixed explicit-under-legacy server persistence, as today.

**E3-6. Shell reconstruction on reconnect (M).** The ledger says: "storage preservation, not evidence of shell-instance reconstruction or shell-event routing on reconnect". Still open. **Test:** an explicit page-shell child survives a WS reconnect with restored state, and routes events.

**E3-7. Removal then re-addition in the same slot (S).** Removal is tested (`test_parent_render_removes_omitted_child_and_its_stored_state`). Re-adding a pruned slot is not. **Test:** render, remove, render again; the child gets a fresh mount, the pruned envelope is not resurrected, and the disposed instance cannot re-register.

**E3-8. Service-worker state keyed by pathname only (S).** Still `window.location.pathname` / `url.pathname` in `src/18-navigation.js:213, 267`. **Fix:** key by pathname plus query. **Test:** a JS test that `/orders?page=1` and `/orders?page=2` do not share a token.

---

## E5: end-to-end safety matrix

**Acceptance text:** "Run actual HTTP, WebSocket and SSE flows with Django/Rust rendering, browser reconnect/back navigation and cross-worker restoration. Assert sentinels at every destination under DEBUG, failures and forged/expired/cross-identity/old-schema restores. Include legacy coexistence and provider/no-op parity as those dependent APIs land."

### Done, as partial evidence only
- **Python/ASGI level:** `test_exposure_http.py`, `test_exposure_runtime.py` (a real `WebsocketCommunicator` reconnect: `test_real_websocket_persists_reconnects_and_refuses_deleted_session`), `test_exposure_sse_navigation.py`, `test_exposure_snapshots.py` (forged, expired, cross-identity, schema).
- **Browser evidence** is recorded only as temporary fixtures: SSE back and forward, WS/SSE overlap, and the child batch. Each bypassed the guard in its own process and none is committed.
- `tests/playwright/` is manual and non-blocking and contains nothing about exposure.

### Open

**E5-1. Committed live harness (L).**
- A test-only ASGI app that applies the guard bypass inside the harness process (never in production code); real Django sessions (DB and cache); two server processes sharing the session store and channel layer for cross-worker runs; Playwright driving WS and SSE.
- Precondition: E6's decision on whether the bypass may exist in `tests/`.

**E5-2. Matrix definition, checked in as a parametrized table (M).**
- Dimensions:
  - transport: HTTP GET/POST, WS, SSE;
  - renderer: Rust view render, Django-template child/tag render, and the `DjustTemplate` backend;
  - flow: mount, event, background, `url_change`, back/forward, reconnect, cross-worker reconnect;
  - failure: DEBUG on or off, handler/render/storage failure;
  - restore: forged, expired, cross-session/user/tenant, old-schema, extra-key, deleted object.
- Destinations: HTML, frames, state backends, session envelopes, signed tokens, CacheStorage (state, VDOM, shell), debug panel, time travel, logs, traceback ring.
- Also: legacy and explicit views on one page and socket (`mount_batch`), and provider parity per E2 provider.

**E5-3. Service-worker at-rest findings, belonging to E5 and decided before E6.**
- No state-cache TTL (`lookupCached(STATE_CACHE…)` ignores `ts`).
- Nothing sends `DJUST_CLEAR_STATE_CACHE`; I verified the only occurrence is the handler in `service-worker.js:340`.
- **Decision (D-n):**
  - TTL: enforce `ts` against the server's snapshot max age (`DJUST_STATE_SNAPSHOT_MAX_AGE`, default 3600) in the worker's lookup, and delete expired entries on read.
  - Logout: send `DJUST_CLEAR_STATE_CACHE` on any session or identity change the client can see (a server-sent clear frame on mount when the binding differs, and on `logout` navigation), and apply the same to the VDOM and shell caches.
- **Tests:** JS tests for expired lookup and logout clearing; a browser test in which user B cannot read user A's entry after logout.

**E5-4. Cross-worker restore (M).** Two processes: an event on worker 1, reconnect to worker 2, declared state restored; the legacy render cache is not consulted (explicit views bypass it); signed tokens verify across workers (shared `SECRET_KEY`/salt).

**E5-5. HTTP API parity (S).** `api/dispatch.py` creates a fresh view per request, mounts it and does not persist (verified). This is out of the automatic-persistence contract, but the matrix should state it.

---

## E6: activation review

**Acceptance text:** "Measure serialization/render cost and migration effort; publish supported backend/provider/codec boundaries and migration guidance. Review E1–E5 evidence and dependent API integration before removing the constructor guard. No zero-leakage or performance claim without evidence."

**Open items:**
- **E6-1. Benchmarks (M).** Explicit versus legacy render and event cost, including the lost shared render-cache reuse (inventory note) and session-envelope I/O. Use `tests/benchmarks/` (for example next to `test_request_path.py`).
- **E6-2. Migration tooling (M).** A values-redacted inventory of inferred names per view, which ADR-038's rollout section requires. It could be a management command, reusing the log-pin AST approach.
- **E6-3. Published boundaries (S–M).** Supported session backends (the DB, cached_db, cache and file allowlist), codec v1, provider support table, and the actor, lazy and mixed-policy exclusions.
- **E6-4. Guard change and test rewrite (S).** As described in the guard section above.
- **E6-5. Evidence review (S).** Cite every test file, including the ones currently unnamed.

**Decisions:**
- **(D-o) Actors.** ADR-038 D6 names "actor paths" as covered. **Recommendation:** exclude `use_actors` from explicit views in v1. Amend D6 explicitly, reject the combination in the guard at configuration time, and keep the runtime refusals.
- **(D-p) Client rollout.** **Recommendation:** require a minimum client version (the `async_complete` batch protocol) for explicit views, checked at mount.
- **(D-q) The `DjustLogSanitizerFilter` defect.** It applies only to the `djust` logger, not to child loggers. Outside ADR-038. **Recommendation:** track it separately; it does not block activation.

---

## ER prerequisites (note only)
ER depends on E6. The 94/160 reference inventory must be re-run after E2's provider work, because form, upload and component providers may add consumers of `_FRAMEWORK_INTERNAL_ATTRS` (see E2-8).

---

## Recommended execution order and sizes

1. **Documentation sweep (S):** the inventory and ledger contradictions under E1; name the unnamed test files.
2. **E1-1 (M), E1-2 (S–M):** close the uncovered error-handler routes first. They are cheap, and later E3 and E5 tests depend on value-free failure behaviour.
3. **E1-3 (M), E1-4 (S–M):** add the missing inventory rows and destination tests. Apply D-b and D-d before E5.
4. **E3-1 (M–L), then E3-2 (M):** one shared "post-turn explicit commit" helper (authorize, save, refresh the token). Root background work first, then `url_change`, tick, push and NOTIFY. This closes the E1 root-background row.
5. **E2-0 decision, then E2-1 (M), E2-2 (M):** the manifest type, mixin registration and view-less tags. These unblock the remaining E2 providers.
6. **E2-3 forms (L), with ADR-035; E2-6 uploads (M); E2-4 actions (S–M); E2-5 streams (S); E2-7 components (L), with ADR-034.**
7. **E2-8 invalidation (M), E2-9 schema/codec (M), E2-10 ORM (S–M).**
8. **E3-3 (M), E3-4 (M), E3-5 (L or S), E3-6 (M), E3-7 (S), E3-8 (S).** Child lifecycle; E3-5 is S if the recommended refusal is chosen, L if lazy children are supported.
9. **E5-1 harness (L), E5-2 matrix (M), E5-3 service-worker TTL and logout (M), E5-4 cross-worker (M), E5-5 (S).**
10. **E6-1 to E6-5,** with decisions D-o, D-p and D-q.

**Limits:**
- I did not run any code.
- The E2 findings came from a subagent reading the code; I spot-checked the form, context, activity and hook-namespace parts myself.
- Unconfirmed:
  - whether the Rust renderer invokes `DjActivityNode` and `ColocatedHookNode`;
  - whether child work queued at mount is dropped or only delayed;
  - whether the PWA sync queue has a persistent backend.
