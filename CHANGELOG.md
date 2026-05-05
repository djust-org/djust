# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`{% if %}` blocks now emit `dj-if` boundary markers — Foundation 1 of #1358.**
  Iter 1 of 3 toward the keyed VDOM diff for conditional subtrees
  (re-open of #256 Option A). At template-render time, every `{% if %}`
  block whose body contains element nodes is wrapped in HTML-comment
  boundary markers:
  ```html
  <!--dj-if id="if-<prefix>-N"-->...rendered body...<!--/dj-if-->
  ```
  Browsers ignore HTML comments, so this is **zero-observable-behavior**
  — markers are framework-internal metadata for the upcoming Iter 3
  (Rust VDOM differ) which uses them as keyed boundaries when
  conditionals flip.

  Marker shape: **Option B (pair per `Node::If`)**. Nested elif chains
  produce nested marker pairs (the parser already nests an inner
  `If(B)` inside the outer's `false_nodes`). Pure-text conditionals
  (text-only true/false bodies) skip emission — text positions are
  sibling-stable already; the legacy `<!--dj-if-->` placeholder for
  false-no-else (issue #295) is preserved unchanged. HTML attribute
  context (issue #380) skips emission. The `cond=` attribute is
  intentionally OMITTED for safety (condition strings could contain
  `--` or `>` that would close the comment early; Iter 3's differ
  keys off the `id` alone). `{% csrf_token %}` is treated as
  element-bearing (renders `<input type="hidden">`), so
  `{% if request.method == "POST" %}{% csrf_token %}{% endif %}`
  correctly emits the wrapping pair.

  ID generation: stable per-template counter `if-<prefix>-N` assigned
  at parse time via `parser::assign_if_marker_ids` walking the AST in
  document order. The `<prefix>` is an 8-hex-character source-derived
  hash (`parser::parse_with_source(tokens, source)`), so independently-
  parsed templates (`{% extends %}` parents, `{% include %}` partials,
  separately-loaded macros) get distinct prefixes and don't collide
  when their rendered HTML is composed in a single output buffer.
  Same source → same prefix → IDs are stable across re-renders. The
  `{% for %}{% if %}` pattern reuses the same id across loop iterations
  because the parser only sees one `Node::If`.

  VDOM parser (`crates/djust_vdom/src/parser.rs`) extended to preserve
  the new opening/closing markers as comment vnodes alongside the
  legacy `<!--dj-if-->` placeholder. The parser predicate accepts
  `dj-if`, `dj-if<space-or-tab>...`, and `/dj-if`; it rejects
  lookalikes like `dj-iffy`, `dj-if-extra`, `dj-ifid="..."`. Client-
  side `getNodeByPath` path-fallback (`12-vdom-patch.js`) now mirrors
  this predicate via the new `isDjIfComment` helper, keeping client
  and server in lock-step. Public `render_template` /
  `render_template_with_dirs` strip ALL `dj-if`-family markers via
  `strip_dj_if_markers` helper — preserves the existing contract that
  public rendering yields clean HTML.

  **What this enables (NOT in this PR):**
  - Iter 2 (Foundation 2): client patch applier learns `RemoveSubtree`
    / `InsertSubtree` patch types.
  - Iter 3 (Capability): Rust VDOM differ recognizes `dj-if`
    boundaries; emits subtree-level patches when conditionals flip.

  Regression suite (post Stage 11 fix), totals across 5 files:
  - 30 cases in `crates/djust_templates/tests/test_if_markers.rs`
    (15 element-bearing / elif / nested / for-if / attribute-context
    cases + 4 cross-template uniqueness cases under
    `cross_template_ids` + 3 csrf_token / variable / raw-input
    classifier cases + 8 stability/ordering cases).
  - 11 cases in `djust_vdom::parser::tests` (legacy placeholder +
    boundary markers + 7 lookalike-rejection / whitespace-tolerance /
    prefixed-id / close-marker boundary cases).
  - 4 cases in `parser::tests` for prefix-deriving functions
    (`parse_with_source` shape / source-distinctness /
    source-stability / token-fallback).
  - 25 cases in `python/tests/test_template_if_markers.py`
    across `TestElementBearingIfMarkers` / `TestPureTextSkip` /
    `TestPublicRenderTemplateStrips` / `TestIdStability` /
    `TestIdAssignment` / `TestAttributeContext` /
    `TestSiblingStability` / `TestIdPrefixUniqueness` /
    `TestCsrfTokenElementBearing`.
  - 20 cases in `tests/js/dj_if_comment_predicate.test.js`
    (predicate matrix + path-fallback integration).

- **Client VDOM patch dispatcher learns `RemoveSubtree` + `InsertSubtree`
  patch types — Foundation 2 of #1358.** Iter 2 of 3 toward keyed VDOM
  diff for conditional subtrees. The server doesn't emit these patch
  types yet (Iter 3 adds that), so this is **zero-observable-behavior**.
  When the upcoming Iter 3 differ recognizes `dj-if` boundaries (from
  Iter 1) and emits subtree-level patches on conditional flips, this
  dispatcher will route them correctly without a coordinated
  client+server release.

  Wire formats:
  - `{type: "RemoveSubtree", id: "if-<prefix>-N"}` — locates the
    `<!--dj-if id="...">` open marker via `TreeWalker`-backed scan,
    walks forward depth-counting opens/closes, removes the entire
    bracketed range (markers + inner content) inclusive.
  - `{type: "InsertSubtree", id: "...", html: "<!--dj-if-->...<!--/dj-if-->",
    path: [...], index: N, d: <parent dj-id?>}` — parses the
    server-emitted HTML fragment via a `<template>` element so any
    `<script>` tags inside are inert by spec, then inserts at
    `parent[index]` using the same path/d resolution other
    child-targeting patches use.

  New helpers (all reused from Iter 1's `isDjIfComment`):
  `_extractDjIfMarkerId`, `_findDjIfOpenMarker`, `_findDjIfCloseMarker`
  (depth-counter, handles arbitrary nesting),
  `_removeDjIfBracketedRange`, `_parseSubtreeHtml`, `applyRemoveSubtree`,
  `applyInsertSubtree`. Dispatched from `applySinglePatch` via a
  short-circuit ahead of the path/d resolution so subtree patches
  don't try to resolve a non-applicable path. 25 regression cases in
  `tests/js/dj_if_subtree_patches.test.js` covering
  `extractDjIfMarkerId` (5) + marker-pair finder + nesting (5) +
  `RemoveSubtree` positive / empty-inner / nested-outer-removes-inner /
  nested-inner-leaves-outer / id-not-found / root-pair / missing-id (7) +
  `InsertSubtree` parses-and-inserts-at-index / appends-on-out-of-range /
  inert-script-via-template / missing-html / unresolvable-parent (5) +
  `applySinglePatch` dispatch wiring (3).

  **What this enables (NOT in this PR):** Iter 3 (Capability): Rust
  VDOM differ recognizes `dj-if` boundaries from Iter 1; emits these
  patch types when conditionals flip.

- **Keyed VDOM diff for `{% if %}` conditional subtrees (#1358; closes
  #256 Option A; capability of v0.9.4-1).** Iter 3 of 3 — the iter that
  actually fixes the bug. After this PR, the long-standing class of
  `{% if %}`-breaks-VDOM-patching bugs that has plagued djust for over
  3 months is **eliminated**. The Rust VDOM differ now recognizes
  `<!--dj-if id="if-<prefix>-N"-->...<!--/dj-if-->` boundary markers
  (emitted by Iter 1 template renderer, PR #1363) as KEYED units in the
  diff algorithm.

  When conditionals flip, the differ emits the new patch types from
  Iter 2 (PR #1364):
  - **OLD has boundary id=X, NEW does not** → `RemoveSubtree { id: X }`.
    Client locates the marker pair by id (NOT by position) and removes
    the bracketed range.
  - **NEW has boundary id=Y, OLD does not** → `InsertSubtree { id: Y,
    path, d, index, html }`. Client parses the full marker-pair HTML
    (Shape A) via inert `<template>.innerHTML` and inserts at the
    parent / index resolved via the same path/d resolution other
    child-targeting patches use.
  - **Both have boundary id=Z** → recurse into the inner body element-
    by-element (markers themselves are static comments — text matches
    by construction). Standard intra-subtree diff fires for the inner
    content (SetText, SetAttr, InsertChild, etc.); NO subtree-replace.

  Position-based path tracking is BYPASSED within boundaries: the id
  normalizes positions, so adding or removing a boundary no longer
  cascades into mis-targeted patches in surrounding siblings. Non-
  boundary siblings are paired by relative position AMONG non-boundary
  siblings — the conditional's presence/absence doesn't shift their
  relative order.

  The 17.5%-error-rate tab-switch regression in NYC Claims (cited in
  #1358's body) no longer reproduces. The recovery-HTML / page-reload
  fallback path is no longer triggered by `{% if %}` flips.

  **Backwards-compatible:** Apps using the `d-none` workaround
  documented in CLAUDE.md and downstream repos continue to work
  identically — the workaround sidesteps `{% if %}` entirely. Apps
  using the legacy bare `<!--dj-if-->` placeholder for false-no-else
  conditionals (issue #295) take the existing diff path unchanged
  (those placeholders have NO id and don't trigger the new keyed
  pre-pass). The pre-pass only fires when at least one sibling list
  contains an id-bearing boundary marker.

  **Implementation in `crates/djust_vdom/src/diff.rs`:**
  - New helpers: `dj_if_open_id`, `is_dj_if_close`, `find_dj_if_pairs`
    (depth-counter for nested boundaries), `render_dj_if_boundary_html`
    (serializes boundary slice for `InsertSubtree.html`),
    `build_excluded_mask`.
  - New `dj_if_pre_pass` runs at `diff_children` entry; returns
    `Some(patches)` when boundaries are present (caller short-circuits
    its keyed/indexed diff), `None` otherwise (caller proceeds
    unchanged).
  - Predicates mirror parser-side at
    `crates/djust_vdom/src/parser.rs:494-499` and JS-side at
    `python/djust/static/djust/src/12-vdom-patch.js:38-43`.

  **Wire format** (locked by Iter 2):
  - `{type: "RemoveSubtree", id: "if-<prefix>-N"}`
  - `{type: "InsertSubtree", id: "...", path: [...], d: "<parent dj-id?>",
    index: N, html: "<!--dj-if id=...-->...<!--/dj-if-->"}`

  Limitation noted in code: when non-boundary siblings carry `dj-key`
  attributes AND reorder within their relative slot, the position-based
  pairing of non-boundary children can produce suboptimal patches.
  Production templates don't typically reorder elements across
  `{% if %}` boundaries; if a regression surfaces, the pre-pass can be
  extended to delegate non-boundary children to `diff_keyed_children`
  when any of them have keys.

  Out of scope (deferred to v0.10): wholesale-replace heuristic for
  same-id matched boundaries (e.g., when inner content differs by >X%);
  LIS within boundary bodies; relaxing `d-none` workaround
  documentation in CLAUDE.md / downstream repos.

  Regression suite: 14 new cases in
  `crates/djust_vdom/tests/test_dj_if_keyed_diff_1358.rs` covering
  the #1358 reproducer (tab-switch with different conditional
  branches), conditional flip-off, conditional flip-on, same-id inner
  text change (recurses, NOT subtree replace), same-id identical inner
  (0 patches), nested boundaries (inner flip leaves outer alone),
  sibling-shift regression (the original failure mode), empty boundary
  same id (0 patches), empty boundary different ids (Remove + Insert),
  JSON wire-format checks for `RemoveSubtree` / `InsertSubtree` /
  `d` omission when None, backward compat with legacy bare `<!--dj-if-->`
  placeholder, and end-to-end via `parse_html` (proves parser-side and
  differ-side predicates agree). All 219 djust_vdom tests pass (14 new +
  205 existing). All 4723 Python tests pass. All 1559 JS tests pass.

  Closes the capability half of v0.9.4-1 milestone (Iter 3 of 3).
  Foundation 1: PR #1363 (template markers). Foundation 2: PR #1364
  (client patch types).

### Changed

- **Bundled `client.js` and `debug-panel.js` are now eslint-clean (#1351).**
  The 393 pre-existing eslint warnings in `client.js` (and 32 in
  `debug-panel.js`) have been resolved across the ~70 source modules in
  `python/djust/static/djust/src/` and `src/debug/`. Breakdown of the
  fixes:
  * Auto-fixed: 222 (`prefer-const`, `no-var`) via
    `eslint --fix python/djust/static/djust/src/`.
  * Targeted disables: 116 in `client.js` sources + 25 in `debug-panel.js`
    sources (`security/detect-object-injection`, all on internal data
    structures — typed for-loop indices, controlled object-literal
    lookups, DOM-controlled keys like `field.name`. djust already
    validates against `UNSAFE_KEYS` for the real prototype-pollution
    attack surface).
  * Refactored: 16 `no-unused-vars` (mostly catch-error parameters
    `_`-prefixed; 2 functions inlined as dead code, 1 redundant
    parameter renamed). 1 `security/detect-non-literal-regexp` for
    server-controlled route patterns in `18-navigation.js`.
  * Cross-module guards: 4 `prefer-const` reverted to `let` for
    cross-file reassigned globals (`liveViewWS`, `clientVdomVersion`,
    `_eventRefCounter`, `_isBroadcastUpdate`) that ESLint's per-file
    scope incorrectly suggests as const — auto-fix had broken the
    transport-switch + broadcast paths until reverted.
  * ESLint config: catch-error parameters now respect
    `caughtErrorsIgnorePattern: "^_"`; concat-fragment source modules
    (`00-namespace.js`, `21-guard-close.js`, `src/debug/*.js`) are
    correctly identified as bundle inputs that don't parse standalone.
  The `--max-warnings 0` flag is now enforced on the eslint pre-commit
  hook (`.pre-commit-config.yaml`) — contributors no longer need
  `SKIP=build-js,eslint` to commit JS source changes. Unblocks the
  bundle rebuild deferred from PR #1357 (dj-transition fix #1348).

### Fixed

- **`dj-transition` now respects CSS `transition-duration` instead of a hard-coded 600ms fallback (#1348).**
  The fallback timeout is auto-derived from the element's computed
  `transition-duration` + `transition-delay` (longest pair across all
  transitioning properties) plus a 50ms grace window. For multi-property
  transitions, expected `transitionend` events are counted from
  `transition-property` and cleanup runs only after all have fired —
  otherwise the first-finishing property would cut off slower ones.
  `_FALLBACK_MS_DEFAULT` (600ms) is used only when computed-style
  reading fails or yields zero. Same auto-derivation extended to
  `dj-remove`. Source-only commit; bundle (`client.js`) rebuild
  deferred per #1351 (392 pre-existing eslint warnings block
  `--max-warnings 0`).

- **`db.notifications` exits cleanly on permanent failures instead of
  retrying forever.** Background — incident 2026-05-05: a 3.5-day-old
  djust deploy missing the optional `psycopg[binary]>=3.2` dependency
  had `_run()` retrying `_connect()` every 1 second forever (~302,000
  attempts), accumulating 15.4 GiB of anonymous heap from un-reaped
  asyncio Task / coroutine closure state. The kubelet hit memory
  pressure, transitioned to NodeNotReady for ~7 seconds, which was
  enough for cnpg to fail over the postgres-cluster primary →
  3-minute platform outage. Fix: when `_connect()` raises
  `DatabaseNotificationNotSupported` (missing psycopg or non-postgres
  engine), treat as PERMANENT — log once at WARNING with
  operator-actionable wording, set `_stopping = True`, fire
  `_ready_event`, return from the loop. Process restart re-enables
  once the cause is fixed. Transient failures
  (`ConnectionRefusedError`, `OSError`, timeout, etc.) retain their
  1-second-backoff retry behaviour. 2 regression tests in
  `python/djust/tests/test_notifications_permanent_failure.py`
  (`test_run_exits_immediately_on_permanent_failure`,
  `test_run_retries_transient_connect_failures`).

- **In-memory state backend no longer panics on concurrent same-session
  HTTP renders (#1353).** When two HTTP requests for the same
  ``(session, view_path)`` pair shared a cached ``RustLiveView`` (the
  in-memory backend returned the same Python reference on cache hits),
  concurrent ``&mut self`` Rust methods on the shared view would
  collide inside Rust's ``RefCell::borrow_mut`` and surface as
  ``RuntimeError: Already borrowed`` (NYC Claims observed 17.5%
  500-rate at concurrency 2). The race spanned more than the
  ``_sync_state_to_rust`` mutation calls — ``render()`` itself holds
  ``&mut self`` across template evaluation, and
  ``Context::resolve_dotted_via_getattr``
  (``crates/djust_core/src/context.rs``) wraps ``Python::with_gil`` so
  the embedded ``getattr`` can yield the GIL inside an active mutable
  borrow. Any peer thread entering an ``&mut self`` method during that
  window panicked. Fixed by switching ``InMemoryStateBackend.get()`` to
  return an isolated ``serialize_msgpack`` / ``deserialize_msgpack``
  clone of the cached view (option 2 of three suggested in the issue
  body), mirroring the ``RedisStateBackend`` contract — which already
  deserialized fresh on every read. With each caller holding its own
  ``RustLiveView`` instance, no two threads can share a Rust ``&mut
  self`` borrow and the race class is eliminated at the source. No
  Python-side lock is needed. New regression cases in
  ``TestInMemoryGetReturnsIsolatedView`` (4 cases — clone identity,
  state preservation, mutation isolation, concurrent get) and
  ``TestConcurrentRenderNoBorrowError`` (2 cases — concurrent render
  with GIL-yielding sidecar, concurrent update_state) in
  ``python/tests/test_rust_bridge_concurrent.py``.

- **State backend honours top-level ``DJUST_STATE_BACKEND`` /
  ``DJUST_REDIS_URL`` settings (#1354).** Previously
  ``BackendRegistry`` only consulted ``DJUST_CONFIG["STATE_BACKEND"]``,
  so projects configuring via top-level Django settings (e.g.
  ``DJUST_STATE_BACKEND = "redis://localhost:6379/0"``) were silently
  downgraded to in-memory with no warning. Now the registry layers
  top-level aliases on top of ``DJUST_CONFIG`` (``DJUST_CONFIG`` still
  wins when both are set — backwards-compatible). URL-shaped values
  (``redis://``, ``rediss://``, ``redis+sentinel://``) are
  auto-translated to ``backend_type="redis"`` plus ``REDIS_URL=<url>``;
  the prefix list lives in ``BackendRegistry._REDIS_URL_PREFIXES``.
  When ``DEBUG=False`` and the resolved backend is the default
  (in-memory), ``djust.utils.BackendRegistry.get`` now emits a
  ``logger.warning`` flagging the production misconfig — multi-process
  deployments lose state across replicas. ``unix://`` URLs are left as
  a TODO follow-up because the underlying ``redis-py`` client takes
  Unix sockets via a different parameter name. New regression cases in
  ``TestTopLevelStateBackendSetting`` (6 cases) and
  ``TestDjustConfigRegression`` (3 cases) in
  ``python/tests/test_state_backend_config.py``.

## [0.9.3rc2] - 2026-05-04

### Changed

- **CodeQL workflow now cancels superseded analyses on rapid PR pushes (#1340).**
  Added `concurrency: { group: ${{ github.workflow }}-${{ github.ref }},
  cancel-in-progress: true }` to `.github/workflows/codeql.yml`. The latest
  commit's analysis is what matters; older runs are obsolete and only add
  noise to the PR check list. Investigation in #1340 surfaced that the
  v0.9.3 drain's "stale CodeQL check-run" framing was a misdiagnosis — most
  "stale CodeQL fail" check-runs were real GitHub Advanced Security alerts,
  not stale leftovers. The `--admin` merge requirement comes from the
  1-approving-review rule (solo maintainer can't self-approve), not from
  CodeQL. This concurrency block reduces the run-list noise that fueled
  the misdiagnosis without changing merge behavior. Triage of the 8 real
  open CodeQL alerts (1 high-severity) tracked in #1343.

### Fixed

- **`_mount_one` now returns a consistent 5-tuple from every path (#1343).**
  The `except Exception` branch in `LiveViewConsumer._mount_one`
  (`websocket.py:2469`) returned a 4-tuple while every other path
  returned a 5-tuple `(ok, payload, err, nav, push_events)`. The single
  caller in `handle_mount_batch` unpacks 5 values; the mismatch raised
  `ValueError: not enough values to unpack`, masking the per-view error
  in the batch `failed[]` plumbing. Surfaced by CodeQL `py/mixed-tuple-returns`
  alert. Returns `[]` for `push_events` from the exception path. 1 regression
  test in `test_sw_advanced.py::TestMountBatch::test_mount_one_returns_5_tuple_on_unhandled_exception`.

- **`deploy_cli.py` no longer has a bare `except: pass` for transient
  status-poll errors (#1343).** Surfaced by CodeQL `py/empty-except` alert.
  Replaced with `logger.debug("status poll failed; retrying", exc_info=True)`
  + an explanatory comment. CLAUDE.md security rule #5 forbids bare
  `except: pass` framework-wide.

- **`python/djust/tests/` now included in `make test-python` + `check-test-coverage` target (#1339).**
  The Makefile's test targets used explicit pytest paths (`tests/ python/tests/`)
  which override pyproject.toml's testpaths, silently excluding `python/djust/tests/`
  (2,734 tests across 100+ files). Added the missing directory to test-python,
  test-python-parallel, and the background test target. New `make check-test-coverage`
  target prevents recurrence by verifying every test directory is collected by CI.
  Verified by `make check-test-coverage` and the 2,734 newly-collected existing tests.

- **@reactive now fails at class-definition time on classes missing ``update()`` (#1287).**
  The ``@reactive`` decorator previously guarded ``self.update()`` with
  ``hasattr(self, 'update')``, silently no-opping when the host class lacked
  the method. It now uses ``__set_name__`` to validate at class-definition
  time, raising ``TypeError`` with a clear message. The ``_ReactiveProperty``
  descriptor also calls ``update()`` automatically for both default and
  custom setters. 6 regression cases in
  ``test_decorator_reactive_requires_update.py``.

- **@background docstring now documents return-value contract (#1288).**
  The decorator's docstring mentions that handler return values are
  discarded and points users to ``@action`` + ``_action_state`` for
  result tracking. 2 regression cases in
  ``test_background_return_value_docs.py``.

- **@computed memoized cache is now thread-safe (#1289).**
  The ``@computed`` decorator's memoized form previously mutated the
  per-instance cache dict without synchronization, creating a race
  window between threads (e.g. a ``@background`` callback and template
  rendering). A per-instance ``threading.Lock`` now protects the
  check-then-act cache mutation. 3 regression cases in
  ``test_decorator_computed_thread_safety.py``.

- **New ``make check-handler-contracts`` linter (#1290).**
  ``scripts/check-handler-contracts.py`` cross-references template-tag
  ``_event`` emit defaults against component/mixin handler method names,
  catching #1275-class (stale/typo'd emit default) bugs at pre-push
  time. 44 emit defaults (26 framework, 18 app-level) validated clean.
  Added to pre-push hook. 7 test cases in
  ``test_check_handler_contracts.py``.

- **dj-form-pending now visible on WebSocket path (#1315).**
  ``sendEvent()`` was fire-and-forget — it returned ``true`` synchronously,
  causing ``handleEvent()`` to resolve immediately on the WebSocket path.
  ``_setFormPending(false)`` fired before any browser repaint, so the
  pending state (spinner, disabled inputs, hidden labels) was never visible.
  ``sendEvent()`` now returns a ``Promise`` that resolves when the server's
  response (patch/noop/error with matching ref) arrives, via a new
  ``_pendingEventResolvers`` Map alongside the existing pending-event
  tracking. All clear sites resolve pending resolvers on disconnect.
  2 regression cases in ``dj-form-pending.test.js`` (WebSocket path block).

- **@server_function no longer hard-codes auth check (#1316).**
  ``dispatch_server_function`` previously had an inline anonymous-user check
  that rejected all unauthenticated callers regardless of the view's
  ``login_required`` setting. The check is removed — ``check_view_auth``
  (view-level ``login_required`` / ``permission_required``) and
  ``check_handler_permission`` (handler-level ``@permission_required``) now
  govern auth, matching the ADR-008 contract. ``@server_function`` no longer
  requires authentication by default. 4 regression cases in
  ``test_server_functions.py``.

- **#1281 regression tests moved to ``python/tests/`` for CI coverage (#1325).**
  ``test_skip_render_private_state.py`` (9 tests) was in
  ``python/djust/tests/`` which is excluded from the explicit paths
  in ``make test-python`` and the CI workflow. Moved to ``python/tests/``
  so CI collects the tests on every run.

## [0.9.3rc1] - 2026-05-02

### Fixed

- **Private state mutations no longer cause false noop (#1281).**
  ``_snapshot_assigns()`` previously skipped all ``_``-prefixed attrs via
  ``k.startswith("_")``, so a handler that mutated only private state
  (e.g. ``self._orders``) produced identical pre/post snapshots → the
  render was skipped → the client received a noop frame. The filter now
  uses ``view_instance._framework_attrs`` membership (captured at
  ``__init__``) to distinguish framework-internal ``_``-prefixed attrs
  from user-defined private attrs set in ``mount()`` or event handlers.
  9 regression cases in ``test_skip_render_private_state.py``.

- **``_action_state`` now persists across WebSocket reconnects (#1284).**
  The ``@action`` decorator populates ``_action_state[action_name]`` with
  ``{pending, error, result}`` so templates can reference
  ``{{ action_name.error }}``. Previously ``_action_state`` was initialized
  before ``_framework_attrs`` capture in ``__init__``, putting it in the
  framework-internal set that ``_snapshot_user_private_attrs`` and
  ``_restore_private_state`` exclude. It now initializes after the capture,
  so the standard user-private save/restore cycle handles it automatically.
  6 regression cases in ``test_action_state_reconnect.py``.

- **Snapshot truncation warning for large lists/dicts (#1285).**
  ``_snapshot_assigns()`` now emits a one-shot ``logger.warning`` per view
  class when a list has ≥100 items or a dict has ≥50 keys. These containers
  have truncated content fingerprints (list: only ``(id, length)``; dict:
  only ``len(v)`` instead of a key tuple), so in-place mutations inside them
  are not detected by auto-diff. The warning tells developers to use
  ``set_changed_keys()`` or assign a new reference. 10 regression cases in
  ``test_snapshot_truncation_warning.py``.

- **Change-detection unified: identity snapshots now use ``_framework_attrs`` (#1286).**
  The push_commands-only auto-skip path (``#700``) had identity snapshots
  at lines 3001 and 3102 that still used ``k.startswith("_")`` to filter
  attrs, while ``_snapshot_assigns()`` was updated in #1281 to use
  ``_framework_attrs`` membership. This meant the two detection paths could
  disagree on whether private state changed. Both identity snapshots now
  use ``_framework_attrs``, closing the dual-path discrepancy (weakness #8
  from the lifecycle audit). 8 regression cases in
  ``test_change_detection_unified.py``.

## [0.9.2] - 2026-05-02

### Fixed

- **`markdown` and `nh3` moved from optional extras to core dependencies.**
  `djust.components.templatetags.djust_components` eagerly imports both
  packages at Django template engine startup, making them hard requirements
  for any project with djust in `INSTALLED_APPS`. Previously they were only
  in the `[components]` extra, causing `ModuleNotFoundError` when the extra
  wasn't explicitly installed. Caught during djustlive scaffold deployment
  to k8s.

This stable release rolls up all v0.9.2rc1 + rc2 fixes (audit-driven
Phase 1 work across audits A-G; 11 of 12 🔴 originals from the
"downstream consumer surfaced" cohort closed; #1281 deferred to v0.9.3
as documented known issue). See [0.9.2rc2] below for the full
audit-cohort delta.

## [0.9.2rc2] - 2026-05-01

### Fixed

- **`dj-transition` now accepts 1-token short form, matching documented
  grammar.** Closes #1273. The `dj-transition-group` docs at
  `43-dj-transition-group.js:22-23` advertise short form like
  `dj-transition-group="fade-in | fade-out"` where each half can be a
  1-token spec — but `_parseSpec` at `41-dj-transition.js:45-60`
  required 3 tokens, so the ENTER side was silently rejected and no
  animation fired. Fix: extend `_parseSpec` to accept 1-token form,
  return `{single: <class>}`; `_runTransition` handles single by
  applying the class on next frame and waiting for `transitionend`.
  2-token form remains rejected as ambiguous (matches `dj-remove`).
  2 new regression cases in `TestParseSpecAcceptsShortForm` +
  behavioral test (`tests/js/dj_transition.test.js`).

- **`AsyncResult` now serializes to a dict templates can navigate.**
  Closes #1274. `assign_async()` returns `AsyncResult` instances that
  templates expect to read as `{{ users.loading }}`,
  `{{ users.ok }}`, `{{ users.failed }}`, `{{ users.result }}`,
  `{{ users.error }}`. Before this fix, neither `normalize_django_value`
  nor `DjangoJSONEncoder.default` had an `AsyncResult` branch — the
  value fell through to `str()`, producing
  `"AsyncResult(loading=True, ...)"` which templates couldn't
  navigate. Result: every `assign_async` demo rendered blank. Fix:
  new `AsyncResult.to_dict()` method + register in both serializer
  paths; `normalize_django_value` recurses into the dict so a
  non-primitive `result` payload (Django Model, datetime, Decimal
  nested in result) is normalized too. 11 regression cases in
  `python/djust/tests/test_async_result_serializer.py`.

- **Form submit now flushes pending debounced `dj-input` handlers
  before dispatching.** Closes #1278. Text/email/password inputs
  with `dj-input` defaulted to 300ms debounce; a user who typed and
  immediately clicked submit raced the submit handler past the
  pending input events. Views that depended on server-side state
  populated by `dj-input` handlers (e.g., `WizardMixin`'s
  `wizard_step_data`) saw stale state at submit time. Fix:
  `debounce()` now exposes a `.flush()` method; new
  `_flushPendingDebouncesInForm(form)` iterates the form's
  `[dj-input]` descendants and flushes any pending wrappers;
  `_handleDjSubmit` calls it before reading FormData / dispatching.
  4 regression cases in `tests/js/dj_submit_debounce_flush.test.js`.

- **`dj-dialog` client-close (ESC/backdrop/`dialog.close()`) now
  syncs back to server.** Closes #1267. Previously `dj-dialog` was
  one-way (server→client); the user closing a dialog client-side
  left server state believing it was still open. Re-opening from
  the server became a no-op because re-asserting `dj-dialog="open"`
  wasn't a value change. Fix: new `dj-dialog-close-event="..."`
  attribute opts into a native `close` event listener that dispatches
  the configured event name to the server. Idempotent across
  re-syncs (WeakMap guard); reads attribute at fire time so morph
  updates take effect. 5 new regression cases in
  `tests/js/dj_dialog.test.js`.

- **SSE transport: EventSource and dispatch POST now send Django
  session cookie.** Closes #1277. Authenticated views over SSE failed
  `check_view_auth` on every mount because the EventSource GET (and
  the message POST) didn't carry credentials. Result: infinite
  mount→navigate loop on authenticated views. Fix:
  `03b-sse.js:69` opens EventSource with `{withCredentials: true}`;
  `03b-sse.js:367` `sendMessage` POST sets `credentials: 'include'`.
  2 new regression cases in `tests/js/sse-transport.test.js`.

- **`mount()` lifecycle: queued async work and push events are now
  drained after the mount frame.** Closes #1280 (`assign_async()` /
  `start_async()` called from `mount()` never resolved over WebSocket
  — view stayed at initial loading-state HTML forever) and #1283
  (`push_event()` called from `mount()` or `on_mount` hooks queued
  events that never reached the client). Both root at the same site:
  `LiveViewConsumer.handle_mount()` ended with `send_json(response)`
  without draining `_async_tasks` or `_pending_push_events`. The fix
  mirrors the established pattern in `handle_event()` /
  `_flush_deferred_activity_events()`: send the response frame, then
  drain push events, then dispatch async work. 3 regression cases in
  `TestHandleMountSourceShape`
  (`python/djust/tests/test_handle_mount_drains_queues.py`).

- **`data_table` integration restored over WebSocket — emit defaults
  renamed to match `on_table_*` mixin handler convention.** Closes
  #1275 (tag emitted 23 event names that didn't match any handler),
  #1291 (pagination handlers entirely missing from the mixin),
  #1279 (handlers mutate state but never refresh rows). Single root
  cause: the WS dispatcher does exact-match `getattr(view, event_name,
  None)` (`websocket_utils.py:173`), but the tag-emit defaults
  previously used bare `table_*` strings while DataTableMixin uses
  `on_*` Phoenix-style handler names — so every default WS interaction
  returned "no handler found". Fix: rename tag-emit defaults across 4
  files (92 lines: `templatetags/djust_components.py`,
  `mixins/data_table.py` class-level attrs + `_PRE_MOUNT_TABLE_CONTEXT`,
  `components/rust_handlers.py`, `templatetags/_forms.py`); add
  `on_table_prev` / `on_table_next` handlers (clamped to
  `[1, table_total_pages]`); call `refresh_table()` from
  sort/search/filter/page/prev/next handlers (selection handler
  deliberately exempt — UI state). 15 regression cases in
  `TestDataTableEmitToHandlerCrossReference`,
  `TestPaginationHandlersExist`, `TestRowAffectingHandlersCallRefresh`
  (`python/djust/tests/test_data_table_handler_contracts.py`).
  Subclasses that overrode `table_X_event` class attrs are unaffected
  — only the bare-default path was broken.

- **`@action` no longer re-raises after recording exception state.**
  Closes #1276. The decorator's docstring promised templates could
  read `{{ <name>.error }}` after an exception, but the implementation
  re-raised — the dispatcher's exception-frame path then bypassed the
  re-render and the template never saw the recorded `error` field.
  Fix: catch `Exception` (not `BaseException`), record state, log at
  ERROR level via `logger.exception`, return None. `BaseException`
  subclasses (`KeyboardInterrupt`, `SystemExit`, `GeneratorExit`)
  still propagate by Python convention. 8 new regression cases in
  `TestActionExceptionDoesNotPropagate` + `TestActionSuccessRecordsState`
  + `TestActionLazyInitializesActionState`
  (`python/djust/tests/test_action_decorator_contract.py`); 6 existing
  tests in `test_action_decorator.py` updated to the new contract.
  Docstring at `decorators.py:262-272` rewritten to match. **Behavior
  change**: code that wraps `@action` calls in `try/except` to handle
  the re-raise now sees a clean return. Mirror the old behavior by
  re-raising explicitly inside the handler.

### Documentation

- **Lifecycle Coverage Audit + Decorator/Tag Contract Audit
  (`docs/audits/lifecycle-2026-05.md`, `docs/audits/decorator-contract-2026-05.md`).**
  Two companion audit docs modeled on the v0.9.2-4 VDOM audit. Document
  the canonical state-type × lifecycle-hook matrix and the
  decorator/tag-name dispatch contract, surfaced from 10 downstream
  consumer bug reports (#1267, #1273-#1281). The lifecycle audit
  catalogues 8 ranked weaknesses including the central control-flow
  gaps in `mount()` (#1280, #1281). The decorator/tag audit catalogues
  8 weaknesses including the `data_table` tag emitting 23 event names
  that don't match any DataTableMixin handler (#1275 generalized).
  Each audit ships with a 4-phase improvement roadmap, test gaps,
  strategic observations, and a companion canon update for
  `CLAUDE.md` / `PR-checklist`. Pre-staged issues filed for each
  not-yet-tracked weakness (#1283-#1291). Audit-driven Phase 1 fixes
  blocking v0.9.2 stable will land in the v0.9.2-5 drain bucket;
  Phase 2/3 fixes targeted for v0.9.3.

- **Production Deployment guide extended with Tier 1/2/3 patterns
  (`docs/website/guides/deployment.md`).** Adds 8 new sections to the
  canonical deployment guide based on patterns surfaced from real-world
  djust deployments:
  - Channel Layer (cross-process push) — separate concern from
    `DJUST_STATE_BACKEND`, required when any view uses `push_to_view`,
    presence, or cursor tracking.
  - Database Connection Pooling — three-layer guidance (`CONN_MAX_AGE`,
    PgBouncer, RDS Proxy), with the LISTEN/NOTIFY caveat for
    transaction-mode pooling.
  - Celery Integration — broker choice (Redis vs SQS), pool choice
    (prefork vs gevent), beat-singleton invariant, gevent monkey-patch
    gotcha, queue-depth-based worker auto-scaling.
  - Static and Media Files — cloud-agnostic CDN options, S3 + CloudFront
    config, `ASGI_SERVE_STATIC=False` opt-out for offloading static-file
    serving from the ASGI server.
  - WebSocket stickiness on AWS ALB — the simpler "stick on Django
    `sessionid`" pattern as an alternative to a custom application-set
    cookie.
  - Sizing and Scaling Tiers — concrete vCPU/RAM recommendations indexed
    to concurrent active users (≤50, 50-500, >500), with explicit
    "when to escalate" triggers.
  - "What's Already Production-Ready in djust" — anti-recommendation
    list (Redis state, `channels_redis`, `sync_to_async`,
    `transaction.on_commit`, Origin check, HSTS) so users don't
    re-evaluate canonical patterns on every deployment.
  - Extended Gunicorn+Uvicorn workers section with concrete production
    CMD + flag rationale (`-w` sizing, `--timeout 120`, `--keep-alive 5`).
  Cloud-agnostic where possible; AWS as canonical example with
  PgBouncer / GCS / Cloudflare R2 noted in parallel.

### Developer Experience

- **Pipeline-template canon: Stage 7 self-applicability check for canon
  PRs (#1248).** New optional checklist item in
  `.pipeline-templates/{feature,bugfix}-state.json` Stage 7 fires when
  a PR adds new mandatory rules. Asks: (a) does the new rule
  false-positive on this PR's own diff? (b) would the new rule have
  caught the originating bug at the stage it adds? Both must be
  explicitly answered. v0.9.2-2 retro Action Tracker #206.
- **Pipeline-template canon: Stage 5/9/10 bundling check (#1251).**
  New mandatory checklist item runs `git diff --cached --stat`
  immediately before `git commit` and verifies the staged line counts
  match the planned scope. Catches the failure mode where
  `git add <file>` silently bundles pre-existing uncommitted
  modifications (the pattern that hit pipeline-skill commit `bf1a67f`,
  silently bundling 130 unintended lines). v0.9.2-2 retro Action
  Tracker #209.
- **Audit script: extract retro-marker regex to shared constants
  module (#1249).** Created `scripts/lib/retro_markers.py` with
  `RETRO_MARKER_REGEX`. The audit script
  (`scripts/audit-pipeline-bypass.py`) now imports the canonical
  constant rather than embedding the literal. Stage 14 `subagent_prompt`
  text in both pipeline templates references the script-canonical file
  rather than re-defining the regex. Single source of truth across
  consumers. v0.9.2-2 retro Action Tracker #207. 4 unit tests at
  `scripts/lib/test_retro_markers.py`.
- **Audit script: scan direct-to-main commits + `Audit-bypass-reason:`
  trailer support (#1250).** The retro-gate audit GHA previously
  scanned merged PRs only; direct commits to main bypassed it (e.g.,
  the v0.9.2-2 milestone-open commit `18e5b117`). The audit now also
  lists direct-to-main commits since the lookback window, filters out
  PR-squash commits via `(#NNN)` subject suffix, and honors an
  `Audit-bypass-reason: <text>` commit-message trailer for
  legitimate exemptions (e.g., docs-only ROADMAP updates per the
  pipeline-drain skill). v0.9.2-2 retro Action Tracker #208.

### Fixed

- **VDOM: mixed keyed/unkeyed children diff round-trip correctness
  (#1260).** Surfaced by proptest during v0.9.2rc1 pre-flight. The
  LIS optimization in `diff_keyed_children`
  (`crates/djust_vdom/src/diff.rs`) skipped emitting `MoveChild` patches
  for keyed children whose trivial-length-1 LIS made them appear
  "in place" — relying on other patches' implicit position shifts to
  land them at the correct absolute index. This works for fully-keyed
  sibling lists (where all moves coordinate via absolute indices) but
  breaks when unkeyed siblings are interleaved (their patches use
  positions relative to other unkeyed nodes only). The keyed child
  ended up stranded at an arbitrary index after all patches applied.
  Fix detects `has_unkeyed_siblings` upfront; in the mixed case, falls
  back to "always emit MoveChild when `old_idx != new_idx`" instead of
  the LIS-implicit-position optimization. The fully-keyed path is
  unchanged. Audit weakness #5/#6 (rated 🟡 with warnings only)
  upgraded to 🟠 by this fuzz finding; this is the actual fix.
  4 deterministic regression tests in
  `crates/djust_vdom/tests/test_mixed_keyed_unkeyed_reorder_1260.rs`
  + permanent proptest seed in `fuzz_test.proptest-regressions`.

## [0.9.2rc1] - 2026-05-01

First release candidate for `0.9.2`. Bundles three drain buckets shipped after `0.9.1` (2026-04-30): `0.9.2-1` (SSE transport DRY refactor — 5 issues, headlined by #1237), `0.9.2-2` (pipeline-template canon batch — 3 issues), `0.9.2-3` (VDOM correctness hardening Phase 1 — 5 issues). Plus the v0.9.2-3 audit doc (`docs/vdom/AUDIT-2026-04-30.md`). 13 issues closed across 5 PRs (#1238, #1239, #1241, #1242, #1246, #1247, #1257, #1258); 1 known issue surfaced during RC pre-flight (#1260 — fuzz-test mixed-keyed/unkeyed diff round-trip; deferred to v0.9.2-4 before stable).

### Fixed

- **VDOM: stale `cached_html` for `dj-update="ignore"` subtrees (#1252).**
  `splice_ignore_subtrees` (`crates/djust_vdom/src/lib.rs`) used to copy the
  old node's `cached_html` into the new node, which meant a conditional
  re-render that wraps an ignored subtree would keep serving the OLD
  cached HTML on subsequent diffs. The cache is now cleared (`= None`)
  during splice; `cache_ignore_subtree_html` recomputes lazily on the
  next render. 4 regression tests in
  `crates/djust_vdom/tests/test_ignore_subtree_invalidation_1252.rs`.

- **VDOM: `dj-id` template-injection defense-in-depth (#1253).** The
  Rust parser now validates user-supplied `dj-id` attribute values
  against base62 (`^[0-9a-zA-Z]+$`) before the server-side ID generator
  overwrites them. Malformed values (whitespace, special chars,
  Unicode tricks) are dropped with a debug-level `parser_trace!`
  warning. The server-generated ID always wins; this fix tightens the
  pre-overwrite read path so any error/log surface that touches the
  prior value sees a sanitized form. 4 regression tests in
  `crates/djust_vdom/tests/test_dj_id_validation_1253.rs`.

- **VDOM: duplicate `dj-key` and mixed-keyed-unkeyed warnings now fire
  at `tracing::warn!` (#1254).** Both warnings in
  `crates/djust_vdom/src/diff.rs` previously used `vdom_trace!()` (gated
  behind `DJUST_VDOM_TRACE=1`), so developers in production had no
  visibility into silent VDOM correctness issues. The mixed-keyed
  warning now fires with stable error code `DJE-050`; the duplicate-key
  warning with `DJE-051`. The previously-cited
  `https://djust.org/errors/DJE-050` URL — which didn't exist — has
  been removed. Structured logging (key passed via `{}` placeholder)
  ensures the warnings are not log-injection vulnerable. 4 regression
  tests in `crates/djust_vdom/tests/test_diff_warnings_1254.rs`.

- **VDOM JS: Web Components and custom elements no longer silently
  replaced with `<span>` (#1255).** The patcher's element-creation
  whitelist in `python/djust/static/djust/src/12-vdom-patch.js` was
  hardcoded to `ALLOWED_HTML_TAGS` + `SVG_TAGS`, rejecting Web Components
  (`<my-component>`, `<sl-button>`, `<model-viewer>`, etc.) and replacing
  them with a fallback `<span>`. The patcher now accepts any tag matching
  the HTML spec's custom-element rule (`tag.includes('-')`) and exposes
  a `window.djustAllowedTags` runtime-configurable hook for
  same-origin allowlist extensions. `<script>` and `<iframe>` remain
  blocked unchanged (`<script>` is not in the allowlist and lacks a
  hyphen; `<iframe>` is unaffected by this change since it's already
  in the existing `ALLOWED_HTML_TAGS` whitelist for legitimate use).
  7 regression tests in `tests/js/vdom_web_components_1255.test.js`.

- **VDOM: extended SVG attribute camelCase normalization (#1256).**
  The Rust parser's `normalize_svg_attribute()` table in
  `crates/djust_vdom/src/parser.rs` was missing modern SVG attributes
  (filter primitives, animation timing, gradient transforms, font-face
  metrics). Browsers' `setAttributeNS` is case-sensitive; without
  normalization, the unknown camelCase attrs were silently ignored,
  producing visually-broken SVG. 11 new attrs added; 11 regression
  tests in `crates/djust_vdom/tests/test_svg_attr_normalization_1256.rs`
  + 10 new cases on the existing in-module test.

### Added

- **Transport-agnostic `ViewRuntime` shared between WebSocket and SSE
  (#1237).** New `python/djust/runtime.py` module factors out
  view-lifecycle dispatch (`dispatch_mount`, `dispatch_event`,
  `dispatch_url_change`) so both transports share one code path for these
  message types. WebSocket's `handle_url_change` is now a thin shim over
  `ViewRuntime.dispatch_url_change`; SSE's new
  `POST /djust/sse/<session_id>/message/` endpoint dispatches identically.
  First slice of a multi-PR migration that will progressively move the
  remaining WS handlers (`handle_event`, `handle_mount`, `handle_mount_batch`)
  onto the shared runtime. Architecture decision documented in
  [ADR-016](docs/adr/016-transport-runtime-interface.md).
- **`LiveViewSSE.sendMessage(data)` — parity with `LiveViewWebSocket`
  (#1237).** Existing `liveViewWS.sendMessage(...)` call sites in
  `18-navigation.js`, `02-response-handler.js`, `13-lazy-hydration.js`,
  and `15-uploads.js` now work transparently when the SSE transport is
  active — no callsite-by-callsite branching. The existing `sendEvent`
  API is preserved (delegates to `sendMessage`). The legacy
  `POST /djust/sse/<sid>/event/` endpoint stays as a back-compat alias.

### Fixed

- **SSE: URL kwargs resolved from the mount-frame URL, not the SSE
  endpoint path (#1237).** Previously a view like
  `path("items/<int:pk>/", ItemView.as_view())` mounted with empty
  `kwargs` over SSE because `_sse_mount_view` resolved against
  `request.path` (the SSE endpoint URL `/djust/sse/<uuid>/`, not the
  page). The client now sends a WebSocket-shaped mount frame containing
  `url: window.location.pathname`, and the server resolves kwargs against
  that URL — matching the WebSocket transport exactly. The HTTP Referer
  header is deliberately not used for this; see
  `docs/sse-transport.md#why-not-the-referer-header` for why.
- **SSE: `LiveView.handle_params()` is now invoked after mount and on
  `url_change` (#1237).** Phoenix-parity contract: `handle_params(params,
  uri)` fires once after `mount()` and on every subsequent URL change.
  Previously SSE never called it, causing views that read URL state in
  `handle_params` (active tab, sort, page) to keep mount-time defaults
  regardless of query string.
- **SSE: `liveViewWS.sendMessage({type: 'url_change', ...})` no longer
  TypeErrors (#1237).** `_executePatch()` in `18-navigation.js` calls
  `sendMessage` for `dj-patch` URL updates; under SSE this previously
  crashed because `LiveViewSSE` had no `sendMessage` method. Now both
  transports expose the same outbound API. Eight other JS call sites
  (popstate, lazy-hydration, response-handler, uploads, navigation) are
  also unblocked.
- **Service Worker reconnection bridge no longer needlessly buffers SSE
  payloads (#1237).** `33-sw-registration.js` patches `sendMessage` to
  buffer payloads when the WebSocket is closed. With SSE now also
  exposing `sendMessage`, the patch was unconditionally applying — and
  because `ws.ws` is undefined on `LiveViewSSE`, every SSE payload was
  treated as "socket closed" and forwarded to the SW. The patch now
  short-circuits on `LiveViewSSE` instances since SSE uses `fetch()`
  directly and doesn't need the WS reconnection buffer.
- **`ViewRuntime.dispatch_mount` rejects `use_actors=True` views with a
  structured error envelope over SSE (#1240).** Closes plan-fidelity
  gap from #1237 — actor-based state management requires the
  channel-layer code in `websocket.py` which the runtime path doesn't
  traverse. Previously a `use_actors=True` mount over SSE would
  partially succeed and fail downstream with an opaque `AttributeError`.
  Now `dispatch_mount` short-circuits with a clear "use_actors is not
  supported over SSE; mount over WebSocket instead" envelope. ADR-016
  §Implementation notes promised this guard; PR #1239 deferred it to
  this follow-up.

### Developer Experience

- **Pipeline-bypass CI check — daily retro-gate audit (#1234).** New
  scheduled GHA `.github/workflows/retro-gate-audit.yml` runs
  `scripts/audit-pipeline-bypass.py` daily at 13:00 UTC against the most
  recent 50 merged PRs and surfaces any PR missing retro markers as
  workflow annotations. Part 2 of #1212 (part 1 was the audit script
  shipped in PR #1229). Manual `workflow_dispatch` trigger included
  for ad-hoc audits.
- **Isolated cargo-test target for `filter_registry::tests` (#1235).**
  The hot-path short-circuit tests for the `ANY_CUSTOM_FILTERS_REGISTERED`
  AtomicBool now live at
  `crates/djust_templates/tests/test_filter_registry_isolated.rs` (an
  integration-test binary). Cargo runs each integration-test file in
  its own process, so the process-global flag starts clean for every
  run — the previous `OnceLock` workaround that gated the in-module
  test on whether a prior test had already registered a filter is no
  longer needed. Carryover from #1180 item 4.
- **VDOM engine audit and v0.9.2-3 milestone (`docs/vdom/AUDIT-2026-04-30.md`).**
  Synthesizes architecture map, bug archaeology (14 historical bugs across
  7 themes), 10 ranked current-code weaknesses (3 🔴 / 7 🟡), test gaps,
  and a 4-phase improvement roadmap. Phase 1 (5 quick wins, #1252-#1256)
  opens as the v0.9.2-3 drain bucket; Phase 2 (correctness hardening)
  and Phase 3 (architectural — text-node djust_ids, unified focus state-
  machine) are deferred to later milestones.
- **Pipeline-template canon — Stage 4 + Stage 7 additions (#1243 +
  #1244).** Two mandatory checklist items added symmetrically to
  `.pipeline-templates/{feature,bugfix}-state.json`:
  - **Stage 4 VERIFY LITERAL API CONTRACTS** — for every literal API
    call in the plan (function names, kwargs, return shapes), grep
    for the existing convention before locking. Pattern from
    #1240/#1242 where the plan said `type="mount_error"` but
    convention was `error_type=`.
  - **Stage 7 WORKFLOW-HEADER CROSS-REF** — when changed files include
    `.github/workflows/*.yml` or any file with a runtime-behavior
    docstring, list every behavioural claim and verify each against
    actual step semantics. Pattern from #1241 where the workflow's
    header said "annotations not red runs" but `pipefail` made every
    flagged run red.
- **Pipeline-run Stage 14 retro-post — Write tool + `gh --body-file`
  (#1245).** Updates `.pipeline-templates/{feature,bugfix}-state.json`
  Stage 14 subagent_prompt to use Claude's `Write` tool to create
  `pr/feedback/retro-<N>.md` and `gh pr comment <N> --body-file <path>`
  to post — replacing the previous `cat > file <<EOF` + `--body "$(cat
  file)"` pattern that silently failed under zsh `set -o noclobber`
  (a common .zshrc safety guard). All 3 v0.9.2-1 implementation PRs
  (#1239, #1241, #1242) hit this and had their retros backfilled
  during the milestone retro audit; the new pattern is structural
  (sidesteps any shell-init quirk, not just noclobber) rather than a
  per-quirk patch.
- **Release-workflow dep-bump label gate (#1236).** New GHA
  `.github/workflows/check-release-workflow-deps.yml` runs on PRs
  modifying release-critical workflow files (`release.yml`, `publish.yml`,
  `release-drafter.yml`, `pre-release-security-audit.yml`) and fails
  unless the PR carries the `release-workflow-reviewed` label, forcing
  explicit human risk-review before merge. Triggered by PR #1233
  (action-gh-release v2 → v3) landing in the same window as the v0.9.1
  cut. The `release-workflow-reviewed` label was added to the repo
  alongside this workflow.

## [0.9.1] - 2026-04-30

Polish release on top of `0.9.0`. Five drain buckets shipped between the `0.9.0` GA bump and this tag (`0.9.1-1` through `0.9.1-5` under the new SemVer-pre-release-suffix milestone naming convention adopted 2026-04-30; equivalent to historical `v0.9.1`/`v0.9.2`/`v0.9.3`/`v0.9.4`/`v0.9.5` drain buckets under the old naming). Headlined by a real-bug VDOM fix (#1205), a broadcast-recovery fix (#1202), the Debug Panel UI (#1151), and a RichSelect ergonomics expansion (#1204).

### Added

- **RichSelect variant support, trigger tinting, and onclick parity (#1204).**
  Each option dict accepts an optional `variant` key that tints the row in the
  dropdown AND the trigger when that option is currently selected. Built-in
  variants align with `Badge`/`Button`/`Tag`/`Alert` vocabulary (`info`,
  `success`, `warning`, `danger`, `muted`, `primary`, `secondary`). New
  `variant_map` kwarg mirrors `Badge.status()` for value→variant mapping
  cases. Permissive variant-name regex (`^[a-z0-9][a-z0-9-]{0,31}$`) lets
  downstream projects ship custom variants by adding a matching
  `.rich-select-option--variant-<name>` CSS rule. Trigger now emits the
  open/close `onclick` handlers that `{% rich_select %}` template tag
  always emitted, eliminating the monkey-patch-rendered-HTML workaround
  programmatic consumers used to need.

- **`LiveViewTestClient.render_with_patches()` — VDOM-diff accessor for tests
  (#1208).** New public method on `djust.testing.LiveViewTestClient` that
  wraps `view_instance.render_with_diff()` and returns `(html, patches_list,
  version)` with the JSON patches parsed into a Python list. Empty list when
  no patches were produced. Reusable for any test that needs to assert on
  VDOM-diff invariants (e.g. "this noop event must produce zero patches").
  First user is the strengthened
  `test_normalize_idempotent_on_already_serialized` regression test in
  `tests/unit/test_list_model_diff_1205.py`, which now locks the #1206
  normalize-pass idempotency contract via an explicit `patches == []`
  assertion instead of the prior weaker "no exception" check.

### Fixed

- **JIT serializer silently degrades when context value is `list[Model]`
  (#1205, expanded by #1207).** When a view's `get_context_data` override sets
  `ctx["tasks"] = list(qs)` *after* calling `super().get_context_data()`,
  the JIT auto-serialization pipeline runs inside `super()` and never sees
  the user-added value. The raw `list[Model]` then flows through
  `_sync_state_to_rust`, where change-detection compares list elements via
  Python `==` — which delegates to `Model.__eq__` (pk-only). In-place field
  mutations (`is_active` toggle, `completed` flip) don't change `pk`, so
  the comparison returns equal, the key is never added to the diff-context,
  Rust never receives the new state, and the rendered HTML is byte-identical
  on every event despite confirmed DB writes. Symptom from the issue
  reporter: `patch_count: 0` on every event, `_debug.variables.tasks.value`
  shows only `__str__` strings.

  Initial fix (#1206): `_sync_state_to_rust` (in
  `python/djust/mixins/rust_bridge.py`) now runs a defensive normalize pass
  over `full_context` immediately after fetching it, converting any
  homogeneous `list[Model]` / `Model` / `QuerySet` value to dicts via
  `normalize_django_value`. After normalization, change-detection compares
  `list[dict] != list[dict]` element-wise via `dict.__eq__` (structural),
  correctly catching field mutations. Idempotent on already-serialized
  values. Also removed dead `_lazy_serialize_context` method from
  `python/djust/mixins/jit.py` (zero call sites — was misleadingly cited as
  the bug location in the issue).

  Shape coverage expansion (#1207): the initial fix only handled
  homogeneous `list[Model]`; PR-review surfaced two more shapes that escape
  change-detection — heterogeneous `[dict, Model]` (Model not first;
  `is_model_list` checked only `value[0]`) and nested `list[list[Model]]`
  (grouped tasks). Refactored the inline normalize loop into a recursive
  `_normalize_db_values` helper that scans the full list for any-position
  Model and recurses into nested lists with bounded depth
  (`_NORMALIZE_DEPTH_LIMIT = 3`). 9 regression cases in
  `tests/unit/test_list_model_diff_1205.py` lock down all shape variants:
  homogeneous `list[Model]`, single `Model`, raw `QuerySet`, heterogeneous
  `[dict, Model]`, nested `list[list[Model]]`, idempotency, empty list,
  and mixed-type list.

- **Broadcast renders now refresh `_recovery_html` (#1202).** `server_push`
  in `python/djust/websocket.py` was sending broadcast patches without
  updating `self._recovery_html` / `self._recovery_version` (the
  user-initiated event path did this; broadcasts were overlooked).
  Consequence: when a subsequent `applyPatches` on the client failed (the
  well-known `{% if %}`-shifts-DOM case), the client sent `request_html`
  expecting fresh HTML, but `handle_request_html` read a stale or
  `None`-valued `_recovery_html` and returned `recoverable: false`. The
  client then triggered a full-page reload. Sessions that only received
  broadcasts after mount (admin dashboards, real-time apps with Celery
  pushes) never populated `_recovery_html` via the normal path.
  PR #1203 mirrors the `handle_event` recovery-state-store at
  `websocket.py:3271`: before sending broadcast patches, `server_push`
  now stores the render output as `_recovery_html` / `_recovery_version`.
  Two regression cases in `tests/unit/test_server_push.py` lock the
  store-after-broadcast invariant + the no-patches branch.

### Process & tooling (internal)

The v0.9.1 arc shipped a substantial body of internal-tooling work that
doesn't change user-facing API but improves contributor and maintainer
ergonomics. Highlights for the audit trail (no migration needed):

- **Pre-push lints**: `scripts/check-no-dead-private-methods.py` (#1209),
  `scripts/check-no-comma-list-closes.py` (#1227),
  `scripts/audit-pipeline-bypass.py` (#1212).
- **Pipeline-template Stage 4 reproducer-first mandatory item** (#1210),
  **Stage 11 reviewer-prompt budget guidelines** (#1211), and the
  **two-commit shape canonicalization** + **3-clean-runs verification**
  (Action Tracker #181/#182) as structural gates.
- **CodeQL sanitizer model** for `djust.security.log_sanitizer.sanitize_for_log`
  (#1214) — closes the FP class PR #1201's 8 dismissals worked around.
- **CLAUDE.md "Bug-report triage" section** citing PR #1206 as the canonical
  case study for issue-reporter-analysis-not-equal-root-cause discipline (#1213).
- **20+ retro patterns canonicalized** across CLAUDE.md sections for
  v0.6.x–v0.8.x retro arcs (#1226), v0.9.4 retro arc (#1225).
- **Pre-commit `.pxd` exclude** prevents binary archive corruption (#1215).
- **Hot-reload auto-enable** via `DjustConfig.ready()` in DEBUG mode (#1190).
- **Debug Panel UI** for time-travel + forward-replay (#1151 / PR #1194,
  on top of v0.9.0's wire-protocol foundation).

## [0.9.0] - 2026-04-29

The "Time Travel" release — the biggest release since 0.3.0, two years of
work compressed into the v0.7 → v0.9 arc. Last release before the 1.0
testing arc.

The detailed per-rc breakdown is preserved in the [0.9.0rc1] through
[0.9.0rc5] sections below; this is the consolidated GA summary plus
post-rc5 additions.

### Added

- **Time-Travel Debugging — per-component scrubber, forward-replay, branched
  timelines.** Redux DevTools-class debugging for the server: every event
  captured, every component scrubbable, every counterfactual replay-able.
  Per-component time-travel (`time_travel_component_jump`) restores a single
  component's state without touching parent or siblings. Forward-replay
  (`replay_event` with optional `override_params`) re-runs a recorded event;
  if the cursor is not at the buffer tip OR override params are present, the
  framework allocates a fresh `branch_id` (`branch-N`) so the user sees
  exactly when they've forked the timeline. CSP-strict debug panel UI ships
  with branch indicator, replay buttons on every history row, and component
  expand-toggles. Closes #1041, #1042, #1151. Files:
  `python/djust/time_travel.py`, `python/djust/websocket.py`,
  `python/djust/static/djust/src/debug/09a-tab-time-travel.js`.

- **Server Actions — React 19 parity, Django-native.** The `@action` decorator
  exposes a pending/error/result triple via `self._action_state[name]` to
  templates. No more boilerplate `self.creating = True; try: ...; finally:`.
  The dispatch pipeline catches errors and exposes them structurally.
  Mirrors React 19 `useActionState` shape closely enough that React refugees
  can port mental models without translation.

- **Async Streams — token-by-token UI.** Three primitives (`stream_to`,
  `stream_append`, `stream_prune`) plus a `StreamingMixin`. Streaming
  infinite scroll, real-time feeds, and especially LLM output now have
  first-class support. Phoenix LiveView 1.0 parity, with Django ORM and
  the Django template language.

- **View Transitions API integration (Phase 2).** djust now wraps every
  patch in a CSS View Transition where supported (Chrome / Edge); CSS
  escape hatches (`view-transition-name`, `::view-transition-old/new`)
  work out of the box. Falls through to instant DOM updates on
  Firefox/Safari.

- **Sticky LiveViews + `{% live_render %}` auto-detect.** Embedded
  LiveViews survive `live_redirect` navigation: WebSocket stays open,
  state preserved, in-flight `start_async()` tasks keep running.
  Auto-detect pass scans new layouts for matching `[dj-sticky-slot]`
  elements and preserves children that map.

- **HVR auto-enabled in DEBUG (zero-config hot reload).** djust's own
  `DjustConfig.ready()` auto-calls `enable_hot_reload()` whenever
  `DEBUG=True` and `watchdog` is installed. Existing per-consumer calls
  keep working unchanged. Opt-out via
  `LIVEVIEW_CONFIG['hot_reload_auto_enable']: False`. Drop your
  `watchfiles` / `--reload` wrappers — HVR preserves view state, scroll
  position, and form input across edits.

- **Async render path (`streaming_render = True`) + lazy slots
  (`{% lazy %}`).** Views with multiple slow data sources opt in to a
  fully-async render path; slots resolve via `asyncio.as_completed` (out
  of template order, fastest-first). Sync rendering remains the default.

- **Rust template engine `{% live_render %}` parity (#1145).** The Rust
  engine now ships a registered handler for `{% live_render %}`. `lazy=True`
  users on `RustLiveView` no longer hit "no handler registered" errors;
  behaviour is byte-for-byte identical on Rust and Python paths.

- **`{% data_table %}` row-level navigation: a11y, keyboard, CSP-strict
  (#1111).** Row-clickable rows render `role="button"`, `tabindex="0"`,
  respond to Enter/Space, short-circuit nested controls via capture-phase.
  Inline `onclick` replaced with external module so `script-src 'self'`
  works out of the box. Defense-in-depth regex validates `data-href`
  against `javascript:` / `data:` URIs.

- **Time-travel wire-protocol additive fields.** `time_travel_state` ack
  frame and `time_travel_event` push frame both carry `branch_id` and
  related metadata; old clients ignore unknown keys (no flag day, no
  migration script).

- **Dedicated documentation site at
  [docs.djust.org](https://docs.djust.org).** Extracted from
  `djust.org/docs/` into a standalone Django site built with djust itself
  (dogfooding). Pulls markdown from this repository via a pinned git
  submodule, so docs always match a specific released version of the
  framework. Launch covers all 23 user-facing guides, 9 API reference
  pages (from `docs/ai/`), the full 20-page component catalog, the
  changelog with deep-linkable per-release anchors, and the migration
  guide — **60 pages total**. Source:
  [djust-org/docs.djust.org](https://github.com/djust-org/docs.djust.org).

- **`RichSelect` — per-option `variant` support and `variant_map`
  convenience kwarg.** Each option dict can carry a `variant` key
  (`info`/`success`/`warning`/`danger`/`muted`/`primary`/`secondary`)
  that tints the dropdown row AND the trigger when selected. The variant
  vocabulary matches `Badge`/`Button`/`Tag`/`Alert`. Status-picker
  convenience: `variant_map={"NEW": "info", "DONE": "success", ...}`.
  Variant names validated with a permissive regex; downstream projects
  add custom variants by shipping matching CSS. 7 CSS rule blocks,
  18 new unit tests, no breaking changes.

### Changed

- **CSP-strict defaults canonicalized for new client-side framework code
  (#1175).** New framework features emitting HTML must default to:
  external static JS modules (no inline `<script>`), no inline event
  handlers, marker class + delegated listener pattern. Reference modules:
  `data-table-row-click.js`, `50-lazy-fill.js`, `39-dj-track-static.js`.
  Strict-CSP deployments are now a design constraint, not an opt-in.

- **Theming cookie namespace for per-project isolation on shared domains
  (#1158).** Opt-in `LIVEVIEW_CONFIG['theme']['cookie_namespace']` so
  multiple djust projects on `localhost:80xx` don't overwrite each
  other's theme preferences.

- **Dev-deps include `markdown` and `nh3` (#1149).** `[components]` extra
  runtime deps are now also pulled in via
  `[project.optional-dependencies.dev]` so `uv sync --extra dev` brings
  them in alongside the rest of the test toolchain.

### Fixed

- **`server_push` recovery-state consistency (#1202).** Push-driven
  sessions previously left `_recovery_html`/`_recovery_version` unset, so
  a client `request_html` after a failed VDOM patch on a broadcast
  returned `recoverable=false` and force-reloaded the page. `server_push`
  now mirrors the `handle_event` pattern of populating recovery state
  immediately before dispatching broadcast patches.

- **Programmatic `RichSelect` class now emits the open/close interaction
  handlers** previously only rendered by the `{% rich_select %}`
  template tag. `onclick` / `onkeydown` (Enter + Space) toggle the
  dropdown; each option row closes the dropdown on click. Parity with
  the template-tag variant via the shared
  `_rich_select_resolve_variant` helper.

- **Theming cookie namespace polish (#1169).** Empty namespaced cookies
  no longer fall back to legacy unprefixed; whitespace-only namespace
  values rejected; write-side honours the namespace.

- **`{% data_table %}` row navigation polish (#1171).** Nested-control
  selector now includes `<details>`, `<summary>`, `<option>`. Test-hook
  namespace cleaned up. Server-side URL allowlist contract test added.

- **A075 system check: `{% live_render sticky=True lazy=True %}`
  collision (#1146).** Promoted from tag-eval-time `TemplateSyntaxError`
  to startup-time warning. Verbatim regions skipped; suppressible
  per-project.

- **Async iterator drain in `arender_chunks` (#1153).** Unawaited
  `_wait_for_one` coroutine warning fixed via explicit `_drain_iterator`
  after `_cancel_pending`.

- **Test-runtime hygiene (#1186, #1152).** Cross-runtime `dispatchEvent`
  warnings (happy-dom + undici) and view-transitions teardown noise
  filtered via narrow `onUnhandledError` patterns. Three consecutive
  `make test` runs exit 0 post-fix.

### Security

- **Code-scanning cleanup (4 fixes + 15 documented FP dismissals).**
  19 open CodeQL/Dependabot alerts addressed, including JS open-redirect
  defense-in-depth (`src/03-websocket.js`), postcss 8.5.9 → 8.5.10
  (CVE-grade XSS via unescaped `</style>`), empty `except` logging in
  `mixins/sticky.py`, and a duplicate `import asyncio` in
  `mixins/request.py`.

- **CSP-nonce-aware activator for `<dj-lazy-slot>` fills (#1147).**
  `{% live_render lazy=True %}` propagates `request.csp_nonce` onto both
  the `<template>` element and the inline `<script>` activator.
  Strict-CSP sites no longer silently fail to mount lazy children.

### Migration

Zero breaking changes from v0.8.x. All v0.7.x and v0.8.x APIs work
unchanged.

Recommended cleanup (optional):

```python
# Old — still works, but now redundant in DEBUG=True
class MyAppConfig(AppConfig):
    def ready(self):
        from djust import enable_hot_reload
        enable_hot_reload()

# New — djust handles it for you
class MyAppConfig(AppConfig):
    pass
```

Full migration notes: [`MIGRATION.md`](MIGRATION.md).

### Quality bar at GA

- 4080+ Python tests, 1486+ JavaScript tests — all green
- 0 open security alerts as of GA
- CSP-strict everywhere
- Wire-protocol back-compat: 0.9.0 servers send all new fields
  additively; 0.6.1+ clients ignore unknown keys

---

## [0.9.0rc5] - 2026-04-28

### Fixed

- **`server_push` now stores `_recovery_html` / `_recovery_version` after
  broadcast renders (#1202)** — push-driven sessions previously left recovery
  state unset, so a client `request_html` after a failed VDOM patch
  (e.g. `{% if %}` shifting DOM structure on a broadcast) returned
  `recoverable=false` and force-reloaded the page. `server_push` now mirrors
  the `handle_event` pattern of populating `_recovery_html` /
  `_recovery_version` immediately before dispatching the broadcast patches.
  Added 3 regression cases in `tests/unit/test_server_push.py`
  (single-push, multi-push refresh, no-op-push leaves recovery state intact).

### Security

- **Code-scanning cleanup batch (4 fixes + 15 false-positive dismissals)** —
  19 open CodeQL / Dependabot alerts addressed:
  - **JS open-redirect defense-in-depth (`src/03-websocket.js:519`)**: the
    fallback `window.location.href = nav.to` path now validates the
    target is a same-origin absolute path. Rejects protocol-relative
    URLs (`//evil.com`), absolute URLs to other origins, and
    `javascript:` / `data:` schemes. Closes CodeQL #2195.
  - **postcss bumped 8.5.9 → 8.5.10** in `package-lock.json` —
    transitive via vitest → vite. Closes Dependabot #90 (XSS via
    unescaped `</style>` in CSS stringify output, GHSA).
  - **Empty `except AttributeError: pass` in
    `mixins/sticky.py:210`** now logs at DEBUG with a comment
    explaining the expected case (read-only proxy children that
    can't accept a `request` attr). Closes CodeQL #2194.
  - **Duplicate `import asyncio` in `mixins/request.py:322`** removed
    — module already imports asyncio at line 5. Closes CodeQL #2267.
  - **15 false-positive dismissals** with documented reasoning:
    - 8× py/log-injection (#2254, #2253, #2239, #2238, #2237, #2236,
      #2235, #2183) — log calls already pass user-controlled input
      through `sanitize_for_log()` (the analyzer doesn't recognize
      the sanitizer).
    - 2× py/cyclic-import (#2231, #2230) — intentional lazy late-imports
      to break circular deps.
    - 1× py/not-named-self (#2268) — `as_view` is a `@classonlymethod`;
      `cls` is correct.
    - 2× py/unused-global-variable (#2272, #2175) — both are referenced
      multiple times (`_CUSTOM_FILTERS_BRIDGED` x4, `_GCS_CHUNK_MIN_SIZE` x3).
    - 1× py/catch-base-exception (#2273) — diagnostic CI script that
      must catch SystemExit subclasses; documented via `noqa: BLE001`.
    - 1× js/useless-assignment (#2174) — minified bundle artifact, not
      source; the 52 source modules in `static/djust/src/` are authoritative.

## [0.9.0rc4] - 2026-04-28

### Added

- **Debug Panel UI for time-travel — per-component scrubber, forward-replay
  button, branch indicator (PR-B for #1151)** — the user-facing UI built
  on top of the wire-protocol shipped in PR-A (#1193). Closes #1151.
  - **Branch indicator** at the top of the Time Travel tab — distinct
    badge styling for `main` (blue) vs branched timelines (orange,
    `branch-N` from forward-replay). Tracks the active `branch_id` from
    every server frame (both ack and event push).
  - **"X / max" event count** in the header so the user can see when
    they're approaching the configured `time_travel_max_events` cap.
  - **Forward-replay button** (`⏵ replay`) on every history row.
    Clicking sends a `forward_replay` frame with `from_index` set to
    that row's index; the server allocates a new `branch_id` if the
    replay diverges (non-tip cursor or override_params present).
  - **Per-component expand-toggle** (`▶ N comp`) on rows whose
    snapshot includes a `__components__` dict. Expanding reveals a
    sub-row for each component with its truncated state preview and
    `↶ comp` / `↷ comp` buttons that scrub a SINGLE component's state
    via `time_travel_component_jump` — leaves parent view + other
    components alone.
  - **CSP-strict**: zero inline event handlers, all interactivity via
    the existing delegated click handler on the panel root. Per
    CLAUDE.md canon #1175.
  - **Replay-hint** label appears in the header when
    `forward_replay_enabled` is true (cursor is not at the buffer tip).

  Files: `python/djust/static/djust/src/debug/09a-tab-time-travel.js`
  (rewrote from 156 LoC to 320 LoC), `python/djust/static/djust/debug-panel.css`
  (90 LoC of additive `.tt-branch*`/`.tt-comp-*`/`.tt-forward-replay`/
  `.tt-expand-toggle` rules), regenerated bundles
  `debug-panel.js` / `.min.js` / `.min.js.gz` / `.min.js.br` via
  `scripts/build-client.sh`. 23 new vitest cases in
  `tests/js/debug_panel_time_travel_ui.test.js` covering: backwards-
  compat ack frames, branch badge selection, count formatting, replay
  hint, expand-toggle visibility, component sub-row rendering, click
  dispatch for component-jump and forward-replay, override-params
  passthrough, branch_id update from event push frames, AND end-to-end
  delegated-click integration (real DOM clicks through
  `registerTimeTravelClickHandlers`).



- **Time-travel wire-protocol exposure for branched timelines + per-component
  scrubbing (PR-A for #1151)** — server-side surface that the v0.9.4 debug
  panel UI (PR-B, follow-up) consumes. The Python plumbing for per-component
  time-travel (#1041) and forward-replay through branched timelines (#1042)
  shipped in v0.9.0; this PR exposes the missing wire fields so the debug
  panel can drive both.
  - **`time_travel_state` ack frame**: 3 new additive fields — `branch_id`
    (defaults `"main"`; new branches allocated as `branch-{N}` on
    forward-replay from a non-tip cursor), `forward_replay_enabled` (true
    iff cursor is not at the tip — meaningful replay would produce a
    branch), `max_events` (the configured ring-buffer cap, so the UI
    can show "X / max"). Old clients ignore the new keys.
  - **`time_travel_event` per-event push frame**: 2 new additive fields —
    `branch_id` and a top-level `components` mirror of
    `entry.state_after.__components__` so the UI doesn't have to dig into
    the nested entry.
  - **New handler `time_travel_component_jump`**: scrubs a SINGLE
    component's state without touching the parent view or other
    components. Mirrors the existing `time_travel_jump` validation and
    re-render path; backed by a new `restore_component_snapshot()`
    helper in `python/djust/time_travel.py`.
  - **New handler `forward_replay`**: replays a recorded event with
    optional `override_params` and allocates a fresh branch id when
    the cursor is not at the buffer tip. Backed by the existing
    `replay_event()` (#1042) plus a new `next_branch_id()` allocator.
  - **Live-view init**: 2 new instance fields on `LiveView.__init__` —
    `_time_travel_branch_id` (default `"main"`) and
    `_time_travel_branch_counter` (default `0`). Both are inert when the
    buffer isn't allocated; zero memory cost for views that don't opt
    in to time-travel.

  Files: `python/djust/websocket.py` (dispatch arms + 2 handlers + ack
  builder), `python/djust/time_travel.py` (`restore_component_snapshot`,
  `next_branch_id`), `python/djust/live_view.py` (branch fields). 12 new
  cases (8 integration + 4 unit) covering ack-frame shape, replay-enabled
  semantics, component-only restore isolation, branch-id allocation,
  defensive defaults, override-params-at-tip branching, branch-id
  no-leak on replay failure, and `which="after"` component restore.
  PR-B (the debug panel UI consuming these fields) is the next v0.9.4 PR.

### Documentation

- **v0.9.4 process canon (closes #1185, closes #1143, closes #1144)** —
  three retro patterns from the v0.9.x arc canonicalized so the next
  drain doesn't repeat the same mistakes:
  - **#1185**: `docs/PULL_REQUEST_CHECKLIST.md` Closing-Keywords rule
    expanded to call out the parenthesized form `(closes #X, closes
    #Y)` explicitly. PR #1176 used it in the title and silently failed
    to close both issues. The checklist now names the failure mode and
    recommends always using PR-body lines for closing keywords.
  - **#1143**: `CLAUDE.md` "Process canonicalizations from v0.9.0
    retro arc" section added — Stage-4 first-principles grep before
    architecting. Lists 5 canonical grep targets (wire-protocol,
    state-snapshot, async dispatch, decorator composition, component
    lifecycle) so Plan stages cite file:line of the pattern being
    mirrored.
  - **#1144**: same section — branch-name verify reflex. Pre-commit
    one-liner that compares `git symbolic-ref --short HEAD` against
    the active state file's `branch_name` field, catching the silent
    "wrong-branch commit" failure observed twice in v0.9.0.

### Fixed

- **v0.9.4 test-infra polish (closes #1188, closes #1189)** — three
  small follow-ups bundled as one PR:
  - **#1188 🟡 #1**: narrowed `vitest.config.js` Pattern 2 filter to
    match only the diagnosed `Closing rpc` + `onUserConsoleLog` /
    `onConsoleLog` cause from PR #1187. Dropped the broader
    `stack.includes('view-transitions')` disjunct so future
    genuinely-different failure shapes in `view-transitions.test.js`
    can no longer be silently swallowed.
  - **#1188 🟡 #2**: added `gc.collect()` before the
    `_wait_for_one`-warning absence check in
    `tests/integration/test_chunks_overlap.py::test_cancel_does_not_leak_wait_for_one_warning`.
    The warning fires from CPython's coroutine GC, not explicit code;
    the prior test passed by accident of CPython's reference-counting
    timing. Forcing collection makes the assertion deterministic
    under PyPy / free-threaded / different GC modes.
  - **#1189**: bumped `test_large_template` wall-clock bound from
    100ms → 500ms with a comment explaining the test is a regression
    bound, not a benchmark. The prior tight bound flaked on busy CI
    runners (5-10ms typical local; 100ms+ under py3.13 free-threaded
    parallel suite load). Real perf tracking lives in
    pytest-benchmark, not this assertion.

### Changed

- **HVR auto-enabled in DEBUG (no AppConfig.ready() boilerplate
  required)** — djust's own `DjustConfig.ready()` now auto-calls
  `enable_hot_reload()` whenever `DEBUG=True` and `watchdog` is
  installed. Existing per-consumer `enable_hot_reload()` calls keep
  working unchanged (idempotent via `hot_reload_server.is_running()`).
  Opt out via `LIVEVIEW_CONFIG['hot_reload_auto_enable']: False` for
  projects that orchestrate the file watcher externally. Test runs
  auto-skip via `PYTEST_CURRENT_TEST` so pytest sessions don't spawn
  a watchdog thread per test. Files: `python/djust/apps.py` (auto-enable
  call appended to `ready()`), `python/djust/config.py`
  (new `hot_reload_auto_enable: True` default),
  `python/djust/__init__.py` (docstring update). 6 new cases covering
  auto-fire, opt-out config, pytest-env skip, idempotency, exception
  isolation, and other-setup completion (new file
  `python/djust/tests/test_auto_hot_reload.py`). Drops the one-line
  `enable_hot_reload()` call from
  `examples/demo_project/demo_app/apps.py`. Closes the friction
  observed across downstream consumers (docs.djust.org, djust.org,
  djustlive) that were either rolling their own `watchfiles`
  process-restart wrappers or silently missing the integration step
  altogether — the framework's HVR is strictly better than process
  restart (preserves view state, scroll position, form input across
  edits) but the consumer-side integration step was easy to skip.

## [0.9.0rc3] - 2026-04-28

### Fixed

- **v0.9.3 test-infra cleanup — suppress unhandled errors in JS + Python
  test runtimes (closes #1186, closes #1152, closes #1153)** —
  release-blocker for v0.9.0rc3. Three test-runtime warnings/errors that
  surfaced during local `make test` but never affected production
  behavior, all unblocking the canonical exit-0 gate:
  - **#1186 (P1)**: happy-dom + undici WebSocket `dispatchEvent`
    cross-pollination — undici fires a Node-side `Event` that
    happy-dom's `EventTarget.dispatchEvent` runtime check rejects (the
    two runtimes don't share a Web-platform `Event` prototype).
    Filtered via a new `onUnhandledError` hook in `vitest.config.js`
    matching a narrow message + stack pattern. Anything outside the
    pattern still re-throws.
  - **#1152 (P2)**: `view-transitions.test.js` non-deterministic
    teardown `EnvironmentTeardownError: Closing rpc while
    "onUserConsoleLog" was pending`. Stubs already yielded a microtask
    per CLAUDE.md retro #1113, so the diagnosis was RPC-timing
    teardown noise, not a stub regression. Filtered via the same
    `onUnhandledError` hook.
  - **#1153 (P2)**: real lifecycle bug in
    `python/djust/mixins/template.py` `arender_chunks`, not warning
    suppression. `task.cancel()` only signals cancellation — it
    doesn't unblock `done.get()` inside `asyncio.as_completed`'s
    internal `_wait_for_one`. When `arender_chunks` returned mid-loop
    on `emitter.cancelled`, the for-protocol's already-pulled
    coroutine plus any further iterator-yielded coroutines were GC'd
    unawaited and Python emitted
    `RuntimeWarning: coroutine '_wait_for_one' was never awaited`.
    Fix: explicit `_drain_iterator(as_completed_iter)` after
    `_cancel_pending()` so the iterator's queue empties cleanly.
    Regression test
    `test_cancel_does_not_leak_wait_for_one_warning` in
    `tests/integration/test_chunks_overlap.py` asserts no
    `_wait_for_one` warnings via `warnings.catch_warnings`
    (1 new case).
  - Three consecutive `make test` runs exit 0 post-fix (was
    non-deterministic 1-3 unhandled errors out of 1463 passing JS
    tests + 4047 passing Python tests).
- **`{% data_table %}` row navigation polish — 3 sub-items from PR #1170
  Stage 11 review (closes #1171)** — final v0.9.2 drain item; tightens
  the row-navigation client module that shipped in #1170:
  - **(a) Nested-control selector — add `<details>`/`<summary>`/`<option>`
    (R3).** `NESTED_CONTROL_SELECTOR` was 6 tags
    (`a, button, input, label, select, textarea`); missed three common
    interactive elements. Disclosure widgets (`<details>`) and `<select>`
    children (`<option>`) now suppress row navigation when the user
    toggles or selects them. Pure additive selector change, no
    behaviour change for existing markup.
  - **(b) Test-hook namespace refactor — drop `window.__djustRowClickNavigate`
    (R4).** Production code now dispatches through
    `window.djustDataTableRowClick.navigate`, which is also the property
    tests stub via direct assignment (vi.fn). The underscored magic
    global is gone — cleaner contract; the namespace was already
    exported for `bindRow` / `initAll` in #1170.
  - **(c) Server-side contract test for URL allowlist (R5).** New
    `tests/unit/test_data_table_url_allowlist_1171.py` parametrizes 6
    URL shapes (3 allowed, 3 hostile — `//evil.com`, `javascript:...`,
    `data:...`) and locks in the "render-doesn't-crash, wiring-is-stable"
    contract that the JS guard depends on. The actual open-redirect
    defense remains the regex in `data-table-row-click.js`; this Python
    test documents the server-side half of the boundary.
  - Test count delta: `tests/js/data_table_row_click.test.js` 14 → 17
    (+3); new `test_data_table_url_allowlist_1171.py` 7 cases.

### Changed

- **v0.9.2 hygiene group — Redis perf docstring softened, replay-rejection
  caplog assertions, descriptor-pattern auto-promotion gap documented,
  dev-env import regression guard (closes #1160, closes #1165)** — Stage 11
  follow-ups from the v0.9.1 retro arc, batched as a single chore PR:
  - **#1160**: rewrite `test_redis_serialization_performance` docstring
    in `tests/unit/test_state_backend.py` to match what the 100ms bound
    actually catches (catastrophic ~10× regressions, e.g. accidental
    JSON/pickle round-trip), not gradual perf drift. Points to
    `pytest-benchmark`-style median-based assertions for SLA-grade
    perf checks.
  - **#1165 (a)**: extend `TestReplayHandlerValidation` rejection-path
    tests in `tests/unit/test_time_travel.py` to assert via `caplog`
    that the `logger.warning(...)` record fires with the expected
    message (`"refused unregistered method"` / `"refused dunder/private
    event_name"`). Side-effect-only assertions previously stayed green
    if the warning silently regressed to a no-op.
  - **#1165 (b)**: document the descriptor-pattern auto-promotion gap
    in the `LiveComponent` docstring (`python/djust/components/base.py`)
    and in `docs/website/guides/components.md`. The framework's
    `_assign_component_ids` walker only inspects instance-level attrs,
    so descriptor components must be appended to `self._components` in
    `mount()` until auto-promotion ships. Time-travel snapshots and
    other walkers silently miss them otherwise.
  - **#1165 (c)**: add `scripts/check-dev-env-imports.py` and a paired
    pytest module (`tests/unit/test_dev_env_imports.py`, 2 new
    parametrized cases) that hard-fail (not skip) if
    `djust.components.components` or its `.markdown` submodule cannot
    import. Locks in the #1149 fix where missing `markdown`/`nh3`
    caused opaque pytest collection failures. Script is standalone for
    now; a follow-up PR can wire it into pre-commit / Makefile.
- **CSP-strict defaults canonicalized for new client-side framework code
  (closes #1175)** — adds explicit guidance in `CLAUDE.md`,
  `docs/PULL_REQUEST_CHECKLIST.md`, and `docs/guides/security.md` that
  any new framework feature emitting HTML must default to: external
  static JS modules (no inline `<script>` blocks), no inline event
  handlers (no `onclick=`/`onchange=`/`oninput=`), auto-bind via marker
  class + delegated listener on `document`/root, CSP nonce propagation
  only when genuinely required (lazy-fill case from #1147 is the
  canonical exception). Reference-module shapes documented (PR #1170
  `data-table-row-click.js`, PR #1138 `50-lazy-fill.js`, existing
  `39-dj-track-static.js`). v1.0 readiness — positions strict-CSP
  deployments as a design constraint, not an opt-in.

### Added

- **`{% data_table %}` row-level navigation: accessibility, keyboard,
  and CSP-strict layer (closes #1111)** — layers v0.9.1 quality
  additions onto the prior #1111 row-navigation scaffolding (which
  shipped `row_click_event` / `row_url` template-tag args, mixin
  defaults, and structural wiring). What's added:
  - **Accessibility**: every row-clickable `<tr>` now renders
    `role="button"`, `tabindex="0"`, and `cursor:pointer`. Screen
    readers announce the row as a button; keyboard users get focus.
  - **Keyboard activation**: Enter and Space on a focused row fire
    the configured action. Guarded by `document.activeElement === tr`
    so Space inside a nested input doesn't hijack the keystroke.
  - **Nested-control guard**: clicks inside `<a>`, `<button>`,
    `<input>`, `<label>`, `<select>`, `<textarea>` are short-circuited
    via capture-phase `stopImmediatePropagation`, so the row-level
    action never fires for those clicks. This is the integration
    point with `selectable=True` (per-row checkbox) and the
    cell-level link column (#1110).
  - **CSP-strict friendly**: the row_url path's previous inline
    `onclick="window.location=this.dataset.href"` is replaced by a
    new component JS module
    (`python/djust/components/static/djust_components/data-table-row-click.js`).
    No inline event handlers, no nonce plumbing — works under
    `script-src 'self'` out of the box.
  - **Defense-in-depth**: `data-href` values are regex-validated
    against `/^(https?:|\/|\.)/` before `window.location.assign`,
    so a hostile `javascript:` URI cannot execute even if it sneaks
    into the row dict.
  - **Multi-line template comments fixed**: the pre-existing
    `{# ... #}` row-nav and link-column doc comments were rendering
    as literal text in output because Django's `{# %}` is
    single-line-only. Converted to `{% comment %}...{% endcomment %}`.

  New cases in `TestRowClickAccessibility`,
  `TestRowClickableMarkerClass`, `TestRowClickAffordance`,
  `TestCSPInlineHandler`, `TestSelectableComposition`, `TestCSPNonce`
  (`tests/unit/test_data_table_row_navigation_1111.py`, 14 Python
  cases) plus 11 JS cases in `tests/js/data_table_row_click.test.js`
  cover: role + tabindex presence, marker class on/off, no-inline-
  onclick (CSP), checkbox cell composition, click navigation, nested
  `<a>`/`<input>` guard, Enter/Space activation, `activeElement`
  guard, javascript: URI rejection, dj-click composition (capture-phase
  stop), and bindRow idempotence. One pre-existing structural test
  in `python/tests/test_data_table_link_row_nav.py` was rewritten to
  assert the new `data-table-row-clickable` marker class instead of
  the removed inline `onclick`.

- **Theming cookie namespace for per-project isolation on shared
  domains (closes #1158)** — adds opt-in
  `LIVEVIEW_CONFIG['theme']['cookie_namespace']` setting so multiple
  djust projects on `localhost:80xx` (or any shared domain) don't
  overwrite each other's theme preferences. Browsers scope cookies by
  domain only — not by port — so the four `djust_theme*` cookies bleed
  across projects without this. PR #1013 already shipped
  `enable_client_override: False` as a workaround, but that breaks
  sites with a user-facing theme switcher; this is the missing piece
  for those sites. When `cookie_namespace="djust_org"` is set, the
  cookies become `djust_org_djust_theme`, `djust_org_djust_theme_preset`,
  `djust_org_djust_theme_pack`, `djust_org_djust_theme_layout`.
  Read path tries namespaced first, falls back to unprefixed once on
  upgrade so users keep their existing theme. Write path (`theme.js`)
  reads `window.__djust_theme_cookie_prefix` injected by
  `theme_head.html` and writes only the namespaced name when set. When
  unset (default), the legacy unprefixed names are used — existing
  deployments unaffected. 8 new regression cases in
  `tests/unit/test_theming_cookie_namespace_1158.py` cover namespaced
  precedence, unprefixed fallback, default back-compat, two-namespace
  isolation, all four cookies honour the namespace, and the
  `theme_head.html` + `theme.js` write-side wiring.
- **Rust template engine `{% live_render %}` lazy=True parity (closes
  #1145)** — the Rust template engine now ships a registered handler
  for `{% live_render %}`, closing the v0.9.0 PR-B (#1138) gap. Before
  this, production users on `RustLiveView` got a
  "no handler registered for tag: live_render" template error if they
  used `lazy=True`, forcing a fallback to the slower Django engine to
  use streaming. The Rust handler delegates to the existing Python
  implementation in `djust.templatetags.live_tags.live_render`, so
  behaviour is byte-for-byte identical on both paths — same
  `<dj-lazy-slot>` placeholder shape, same thunk-stash side effect on
  `parent._lazy_thunks`, same CSP nonce propagation, same
  `sticky=True + lazy=True` collision raise. The bridge required
  threading the raw Python sidecar (`request`, `view`) through to the
  custom-tag handler context: `crates/djust_core` exposes
  `Context::raw_py_objects()` for read access, and
  `crates/djust_templates::registry` adds
  `call_handler_with_py_sidecar` (a backward-compatible variant of
  `call_handler` — existing handlers ignore the extra Python objects
  in their dict). 8 parity regression cases in
  `tests/unit/test_rust_live_render_lazy_1145.py` cover lazy=True
  placeholder byte equivalence, lazy="visible" parity, thunk stash on
  the Rust path, CSP nonce parity, sticky+lazy collision, the
  inline-attribute `template = "..."` mode (the original failure
  surface from PR #1138 integration tests), and eager-mode
  regression-guard.
- **A075 system check: `{% live_render sticky=True lazy=True %}`
  collision (closes #1146)** — promotes the existing tag-eval-time
  `TemplateSyntaxError` to a startup-time warning so the misuse
  surfaces during `manage.py check` instead of waiting for a
  request to render the offending template. Sticky preservation
  requires the slot to exist at mount-frame time so the WebSocket
  reattach can `replaceWith` the stashed subtree; `lazy=True`
  defers slot rendering until after the parent shell flushes —
  the stash target doesn't exist when reattach runs. The check
  skips `{% verbatim %}...{% endverbatim %}` regions so
  docs/marketing pages showing the anti-pattern as a literal
  example don't false-positive (re-uses the `_strip_verbatim_blocks`
  helper from the v0.7.3 #1004 fix). Silenceable per-project via
  `DJUST_CONFIG = {"suppress_checks": ["A075"]}`. 8 regression
  cases in `TestA075StickyLazyCollision` cover collision firing,
  sticky-only / lazy-only silence, verbatim suppression, real-call
  next to verbatim example, config disable knob, and string-truthy
  kwarg shapes.

### Security

- **CSP-nonce-aware activator for `<dj-lazy-slot>` fills (closes
  #1147)** — `{% live_render lazy=True %}` now propagates
  `request.csp_nonce` (the Django convention set by `django-csp`
  middleware) onto BOTH the `<template id="djl-fill-X">` element
  AND the inline `<script>` activator that calls
  `window.djust.lazyFill(...)`. Sites with strict CSP
  (`script-src 'nonce-...'`, no `'unsafe-inline'`) previously had
  the activator silently rejected at parse time, and lazy children
  never mounted. The fix reads `getattr(request, 'csp_nonce', None)`
  via the existing `djust.utils.get_csp_nonce` helper — no
  additional configuration is required for any CSP middleware that
  follows the Django convention. When `request.csp_nonce` is absent
  or empty (the common case for sites without CSP middleware), no
  `nonce` attribute is emitted — backward-compatible for non-CSP
  deployments. The placeholder `<dj-lazy-slot>` also carries the
  nonce so client-side code can read it via `getAttribute('nonce')`
  if it ever needs to inject CSP-bound scripts under the same
  policy. 6 Python regression cases in `tests/unit/test_lazy_render_csp.py`
  + 3 JS cases in `tests/js/lazy_fill_csp.test.js` cover nonce
  propagation, backward compatibility (no nonce attr when
  `csp_nonce` is absent / empty / missing), and HTML-escaping
  defense-in-depth for hostile-middleware substitutes.

### Changed

- **Dev-deps include `markdown` and `nh3` (closes #1149)** — both
  packages are runtime deps of the `[components]` extra (see
  `python/djust/components/components/markdown.py`) and the
  components subpackage's `__init__.py` eagerly imports them via
  `from .markdown import Markdown`. Tests that import
  `djust.components.components` (directly or transitively) failed
  collection in clean checkouts that ran only `uv sync` without
  the `[components]` extra. The bisect agent in PR #1159 hit this
  on a fresh clone. Added both to
  `[project.optional-dependencies.dev]` so a single
  `uv sync --extra dev` brings them in alongside the rest of the
  test toolchain. No behaviour change for runtime users —
  `[components]` already lists both as runtime deps.

### Fixed

- **Theming cookie namespace polish — 4 sub-items (closes #1169)** —
  Stage 11 follow-ups from PR #1168 (the original cookie-namespace work
  for #1158):
  - **(a) Empty namespaced cookie no longer falls back to legacy.**
    `ThemeManager.get_state()` previously evaluated the namespaced
    cookie via `_read('<ns>_name') or None`, so an empty-string value
    (`""`) silently fell through to the unprefixed legacy cookie —
    re-opening the cross-project bleed path #1158 closed. The read
    now distinguishes `None` (cookie not in jar) from `""` (cookie set
    to empty), and only falls back in the former case.
  - **(b) `cookie_namespace` validated at config-load.** The value is
    interpolated directly into cookie names; whitespace, `=`, `;`, and
    non-ASCII characters previously produced malformed Set-Cookie
    headers (browsers reject or split such cookies).
    `_validate_cookie_namespace()` now raises `ImproperlyConfigured`
    at startup for any value outside `[A-Za-z0-9_-]+`.
  - **(c) JSDOM tests for the cookie WRITE side.** The 8 #1158
    Python tests only asserted on `theme.js` source-text patterns; new
    `tests/js/theming_cookie_namespace_write.test.js` loads the file
    in JSDOM, sets `window.__djust_theme_cookie_prefix`, fires
    `setPack`/`setPreset`/`setLayout`, and inspects `document.cookie`.
  - **(d) Legacy-cookie cleanup on first namespaced write.** When
    `cookie_namespace` is set, every theming-cookie write in
    `theme.js` now also emits `Max-Age=0` for the unprefixed legacy
    name. Stale legacy cookies left over from before namespace was
    configured no longer sit in the jar forever and bleed back if the
    namespace is later removed. Cleanup is inert when no prefix is
    configured (back-compat).

  3 new regression cases in `tests/unit/test_theming_cookie_namespace_1158.py`
  (1 for sub-item (a), 2 for sub-item (b)) plus 7 new JS cases in
  `tests/js/theming_cookie_namespace_write.test.js` (4 for sub-item (c),
  3 for sub-item (d)).

- **Tag-registry test isolation + sidecar bridge extension to block /
  assign tags (closes #1167)** — two Stage 11 follow-ups from PR #1166
  (which wired the raw-Python sidecar into ``Node::CustomTag``):
  - **Test isolation**: ``tests/unit/test_tag_registry.py`` previously
    used per-class ``setup_registry`` fixtures that re-registered the
    Python built-in handlers on teardown but did NOT clear the global
    Rust ``TAG_HANDLERS`` registry first. Transient handlers from the
    file (notably ``BrokenHandler`` registered for the ``broken`` tag
    in ``test_handler_exception_returns_error``) leaked into
    subsequent test files. ``test_assign_tag.py`` running after this
    file would see ``handler_exists("broken")`` == True; the parser
    dispatches ``handler_exists`` before ``assign_handler_exists``
    so ``{% broken %}`` was routed to the leaked CustomTag handler
    and ``test_non_dict_return_is_empty_merge`` failed with the leaked
    handler's exception. Fix: replace the per-class fixtures with one
    function-scoped autouse fixture that clears all three Rust
    registries (tag / block-tag / assign-tag) before AND after every
    test, then re-registers the built-ins from
    ``djust.template_tags._registered_handlers``. The file is now
    self-contained.
  - **Sidecar parity**: PR #1166's
    ``call_handler_with_py_sidecar`` only fired for ``Node::CustomTag``.
    Block tags (``Node::BlockCustomTag``) and assign tags
    (``Node::AssignTag``) didn't receive the ``request`` / ``view``
    sidecar, so a custom block or assign handler couldn't reach the
    parent view. Added ``call_block_handler_with_py_sidecar`` and
    ``call_assign_handler_with_py_sidecar`` mirroring the PR #1166
    pattern; the existing variants are kept as back-compat shims that
    delegate with ``None``. All five renderer call sites (1× block,
    4× assign — single-node, sibling-aware, collecting, and
    partial-render paths) forward ``context.raw_py_objects()``.

  New cases in ``TestBlockTagSidecar`` and ``TestAssignTagSidecar``
  (``tests/unit/test_tag_sidecar_parity_1167.py``, 6 Python cases)
  cover sidecar receipt of ``request`` and ``view`` per node type
  plus a back-compat regression per node type confirming legacy
  handlers that ignore the sidecar continue to work unchanged.

- **Custom filter bridge polish — 6 sub-items deferred from #1161
  (closes #1162)** — Stage 11 review of PR #1161 (which closed #1121
  by adding the eager Rust filter registry) flagged six follow-ups.
  All are addressed in this PR:
  1. **Hot-path Mutex perf**: ``is_custom_filter_safe`` and
     ``apply_custom_filter`` short-circuit on a new
     ``ANY_CUSTOM_FILTERS_REGISTERED`` ``AtomicBool`` so projects with
     no custom filters pay only an atomic load on every variable
     expansion's ``filter_specs.iter().any(...)`` loop, never a Mutex
     acquire. Acquire/Release ordering pairs the load with the store
     in ``register_custom_filter``.
  2. **Hardcoded ``autoescape=True`` plumbing (correction, #1180)**:
     ``apply_custom_filter`` now accepts an ``autoescape: bool``
     parameter that's set as a kwarg on the Python callable when the
     filter declares ``needs_autoescape=True``. The earlier wording
     here was inaccurate — only ``apply_custom_filter`` was widened;
     the upstream chain (``apply_filter_full`` in ``filters.rs`` and
     the renderer's three call sites at ``renderer.rs:287, 349, 1602``)
     was NOT threaded through. Future ``{% autoescape %}`` block
     tracking will need to update **~4 sites** to plumb the dynamic
     value end-to-end, not 1.
  3. **Unknown-filter test tightened**: assert ``RuntimeError`` type
     AND the canonical ``"Unknown filter:"`` message shape, not just
     ``pytest.raises(Exception)`` + substring on filter name only.
  4. **Dropped unused ``custom_filter_exists``**: dead public Rust
     function with no callers in the workspace; PyO3 macros suppress
     the dead-code warning so it would have rotted silently.
  5. **Fixture isolation comment**: the ``scope="module"`` autouse
     fixture in ``tests/unit/test_rust_custom_filters_1121.py`` now
     carries an explicit comment that this file is not safe to run
     in parallel with other Rust-filter-registry-touching tests.
  6. **Silent async filter handling**: an ``async def`` custom filter
     previously stringified the unawaited coroutine (``"<coroutine
     object ...>"``) into the rendered HTML with a "coroutine was
     never awaited" RuntimeWarning at GC. Now uses
     ``inspect.iscoroutine`` to detect and reject with a clear,
     actionable error and ``coro.close()`` to suppress the GC warning.

  New cases in ``TestNewBehavior_1162``
  (``tests/unit/test_rust_custom_filters_1121.py``, 2 Python cases)
  cover async-filter rejection (sub-item 6) and ``autoescape`` kwarg
  flow (sub-item 2). Two new Rust unit tests in
  ``filter_registry::tests`` cover the ``AtomicBool`` short-circuit
  pre-registration.

- **`replay_event` validates handler is `@event_handler`-decorated
  (closes #1148)** — defense-in-depth strengthening of the v0.9.0
  #1042 forward-replay path. The original guard rejected only
  dunder/private `event_name` (`startswith("_")`), which still
  admitted ANY public method on the view — helpers, inherited
  utilities, property getters — even though the dispatcher only
  ever invokes `@event_handler`-decorated methods. A hand-edited
  or malicious snapshot could replay e.g.
  `view.delete_all_records()` even when that method was never
  exposed to the dispatcher. The fix calls
  `djust.decorators.is_event_handler(handler)` after attribute
  resolution, mirroring the dispatcher's own acceptance criteria
  (see `websocket.py` ~ line 4389 server_push handler validation).
  Unregistered methods log a warning and return `None` instead of
  invoking. 3 regression cases in `TestReplayHandlerValidation`
  cover registered-handler success, unregistered-method
  rejection, and the existing dunder-rejection regression.

- **Rust template renderer rejects project-defined custom filters
  (closes #1121)** — Django projects registering custom filters via
  ``@register.filter`` in their ``templatetags/`` modules saw them work
  in the Python render path but fail under the Rust ``RustLiveView``
  render path with ``RuntimeError: Template error: Unknown filter:
  <name>``. The Rust engine's filter dispatch was a hardcoded match
  against Django's 57 built-in filter names with no fallback for
  project-level filters. The fix is a Python→Rust bridge mirroring
  the existing custom-tag-handler design (``crates/djust_templates/
  src/registry.rs``):
  - New ``crates/djust_templates/src/filter_registry.rs`` holds a
    process-wide ``Mutex<HashMap<String, FilterEntry>>`` of project
    filter callables + per-filter metadata (``is_safe``,
    ``needs_autoescape``).
  - The renderer's filter loop forwards an ``arg_was_quoted`` hint
    from the parser so the bridge can resolve bare-identifier args
    against the template context before calling Python — fixing the
    ``{{ my_dict|lookup:some_key }}`` shape from the issue body.
  - Both ``filter.is_safe`` and ``filter.needs_autoescape`` from the
    Django filter object are honoured: ``is_safe=True`` filters skip
    auto-escape; ``needs_autoescape=True`` filters receive
    ``autoescape=True`` as a kwarg.
  - ``python/djust/template_filters.py`` walks
    ``template.engines['django'].engine.template_libraries`` at the
    first LiveView render and bulk-registers every custom filter
    found. Built-in Django filter names are skipped (the Rust engine
    has native implementations of all 57). The bootstrap is
    idempotent — late-loaded apps' filters are picked up on
    subsequent renders.
  - Unknown filter names still raise the original
    ``Unknown filter: <name>`` error so typos and missing imports
    surface immediately. 10 regression cases in
    ``TestRustCustomFilters`` cover the lookup shape from the issue
    body, ``is_safe``, ``needs_autoescape``, quoted vs context-
    resolved args, plain-text auto-escape, and the full
    ``RustLiveView`` render path.

- **Test pollution: 6 flaky tests in full-suite pytest run (closes #1134)** —
  bisected two independent polluters that surfaced after v0.9.0 PR-A
  (#1135) added the `aget`/`ChunkEmitter` async-render path and after
  PR #998 added the `block_watchdog` test fixture:
  - **In-memory SQLite + Channels disconnect**: 5 tests
    (`test_websocket_origin_validation::TestConnectOriginValidation`'s
    4 accepting-handshake cases + `test_request_path::test_websocket_mount_counter`)
    failed during `communicator.disconnect()` because Channels'
    consumer dispatch invokes `aclose_old_connections()`, which
    iterates Django's connection cache and calls
    `close_if_unusable_or_obsolete()` → `get_autocommit()` →
    `ensure_connection()`. SQLite ignores `close()` for in-memory
    DBs (data-loss prevention), so a prior django_db-marked test
    leaves the connection wrapper with `.connection != None` in the
    thread-local; pytest-django's blocker then fires inside the
    consumer's cleanup. Marked the affected tests
    `@pytest.mark.django_db` so they participate in pytest-django's
    connection management.
  - **`sys.modules["djust.checks"]` rebind**: the
    `test_dev_server_watchdog_missing.py::test_check_hot_view_replacement_survives_without_watchdog`
    test deleted `djust.checks` from `sys.modules` and re-imported,
    creating a *new* module object while
    `test_static_security_checks.py` had already done
    `from djust.checks import check_configuration` at collection
    time. Subsequent
    `mock.patch("djust.checks._has_multiple_permission_groups", ...)`
    targeted the new module while the old `check_configuration` kept
    resolving names against the old module's `__dict__` — so the
    patch silently no-op'd and `test_a020_fires_with_multiple_groups`
    failed. Moved snapshot/restore of `djust.checks` and
    `djust.dev_server` into the `block_watchdog` fixture's setup/
    teardown so the eviction is local to the test's lifetime.
  - **Redis-serialization-performance 10ms wall bound**: relaxed the
    bound from 10ms to 100ms — under heavy full-suite load (GC
    pauses, scheduling jitter) the ideal-conditions 10ms ceiling
    was producing false positives. 100ms still catches "we
    accidentally serialized via JSON/pickle round-trip" regressions
    without the timing flake.

## [0.9.0rc2] - 2026-04-27

### Changed

- **`WizardMixin.as_live_field` auto-picks `dom_event` by widget class
  (closes #1156)** — previously the view-level ``wizard_input_event``
  attribute applied uniformly to every widget the wizard rendered. An
  author setting ``wizard_input_event = "dj-input"`` to capture
  unblurred text edits (per #1095) unintentionally also stamped
  ``dj-input`` on radios, selects, and checkboxes — which was
  semantically wrong (there's no keystroke stream to fire on) and pre-
  #1155 incurred a 300ms debounce stall on every click.

  ``as_live_field`` now inspects the field's widget class (walking the
  widget's MRO so any subclass of an enumerated builtin inherits the
  default automatically) and picks:

  - ``dj-change`` for click-fired widgets — ``RadioSelect``,
    ``CheckboxInput``, ``CheckboxSelectMultiple``, ``Select``, plus
    every Django Select subclass (``SelectMultiple``,
    ``NullBooleanSelect``) and any app's RadioSelect/Select subclass
    matched via MRO. They commit exactly one value per user
    interaction, no stream to batch.
  - ``wizard_input_event`` for text-stream widgets (``TextInput``,
    ``Textarea``, ``NumberInput``, ``EmailInput``, etc.) — preserves
    the #1095 contract for authors who need unblurred-text capture.
  - Caller-passed ``dom_event="..."`` still wins — the widget-aware
    default is a default, not a mandate.

  Apps that had implemented their own `as_live_field` override to do
  exactly this mapping can delete the override.

  New ``_CLICK_FIRED_WIDGET_CLASSES`` ClassVar (frozenset of widget
  class names) lets apps with custom commit-style widgets extend the
  dispatch without overriding ``as_live_field`` itself:

  ```python
  class MyWizard(WizardMixin, LiveView):
      _CLICK_FIRED_WIDGET_CLASSES = frozenset({
          *WizardMixin._CLICK_FIRED_WIDGET_CLASSES,
          "MyColorPickerWidget",
      })
  ```

  Files: ``python/djust/wizard.py`` (new ``_default_dom_event_for``
  helper + ``_CLICK_FIRED_WIDGET_CLASSES`` ClassVar, ~20 LoC; updated
  ``wizard_input_event`` docstring to clarify text-only scope).
  15 new cases in ``TestAsLiveFieldWidgetAwareDomEvent`` in
  ``tests/unit/test_wizard_mixin.py`` cover text/textarea/integer/email
  tracking ``wizard_input_event``, radio/select/checkbox/
  CheckboxSelectMultiple locked to ``dj-change``, caller-passed
  ``dom_event`` overrides, ClassVar extension for custom widgets, and
  MRO walk catching ``SelectMultiple`` / ``NullBooleanSelect`` /
  app-defined RadioSelect subclasses.

### Fixed

- **`dj-input` on click-fired widgets no longer incurs a 300ms debounce
  (closes #1154)** — `DEFAULT_RATE_LIMITS` in
  `python/djust/static/djust/src/08-event-parsing.js` was missing entries
  for `radio`, `checkbox`, `select-one`, and `select-multiple`. The input
  handler's fallback (`{ type: 'debounce', ms: 300 }`) kicked in for these
  widget types, so `WizardMixin.wizard_input_event = "dj-input"` — the
  class-wide setting recommended by #1095 — silently inserted 300ms of
  dead air between a radio click and the WS event being sent.

  Fix adds a new `passthrough` rate-limit type for click-fired widgets
  (they commit exactly one value per user interaction, no stream to
  batch) plus a branch in the input handler in `09-event-binding.js` that
  skips the rate-limit wrapper when `rateLimit.type === 'passthrough'`.
  Text/textarea fields retain their 300ms debounce unchanged. The
  defensive 300ms fallback for unknown widget types is intact.

  Real-world measurement from a wizard with a Yes/No radio and
  `wizard_input_event = "dj-input"`:

  | | click → WS send | total click → DOM |
  |---|---|---|
  | Before | 1104 ms | ~1150 ms |
  | After  | 1 ms    | ~75 ms |

  `dj-debounce`/`dj-throttle` explicit overrides on a radio still work —
  passthrough is the default, not a mandate. Files:
  `python/djust/static/djust/src/08-event-parsing.js` (4-line
  `DEFAULT_RATE_LIMITS` extension),
  `python/djust/static/djust/src/09-event-binding.js` (7-line
  `passthrough` branch + a one-line `Object.assign({}, …)` clone of the
  default before the override branches mutate it — without the clone,
  `dj-debounce`/`dj-throttle` on one element permanently flips the
  shared `DEFAULT_RATE_LIMITS` entry and pollutes every subsequently-
  bound element of the same type). 9 new cases in
  `tests/js/dj-input-click-widgets.test.js` lock in synchronous firing
  for radio/checkbox/select-one/select-multiple, continued debounce for
  text/textarea, that `dj-debounce` overrides still apply, and that an
  override on one radio does not leak into a sibling radio's wrapper
  (regression for the shared-state mutation).

## [0.9.0rc1] - 2026-04-27

### Added

- **Forward-replay through branched timeline (closes #1042, v0.9.0 P3)** —
  Redux DevTools "swap action" parity. Time-travel previously only
  scrubbed BACK through linear history; `replay_event(view, snapshot,
  override_params=None, record_replay=True)` now replays a recorded
  event from its `state_before` baseline either deterministically
  (original `params`) or with caller-supplied `override_params` to
  fork a branched timeline.

  Builds on #1041's per-component capture: replay restores via
  `restore_snapshot(view, snap, "before")` which dispatches to
  `view._components[id]` instances. So a handler that reads
  `self._components[id].value` during replay sees the CAPTURED value,
  not the live one. The test
  `test_replay_restores_component_state_before_invoking` locks this
  in.

  Branches are scrubbable: `record_replay=True` (default) appends the
  replay's new snapshot to the buffer so the branched timeline is
  itself navigable. `record_replay=False` runs a "dry" replay — view
  is mutated for preview, buffer is unchanged.

  Handler-missing path: returns `None` and logs a warning (handler
  was renamed since the snapshot was captured). Handler-raises path:
  the new snapshot's `error` field is set and the snapshot is still
  returned so the debug panel can show "this branch errored at step
  N".

  Files: `python/djust/time_travel.py` (~85 LoC: `replay_event`
  function + `__all__` extension). 7 new cases in `TestReplayEvent`
  in `tests/unit/test_time_travel.py` cover deterministic replay,
  branched timeline (override_params), buffer recording, dry replay,
  missing handler, handler exception, and component-state restoration
  during replay.

  v0.9.0 streaming + DevTools arc complete: PR-A foundation → PR-B
  `lazy=True` API → PR-C parallel render → #1041 component-level
  capture → #1042 forward-replay.

- **Component-level time-travel (closes #1041, v0.9.0 P3)** — extends
  the v0.6.1 time-travel ring buffer to capture per-component public
  state alongside the parent LiveView's state. Multi-component pages
  can now scrub back through history with each component's state
  faithfully restored.

  Snapshot format: `_capture_snapshot_state` adds a reserved
  `__components__` key holding a `{component_id: {field: value}}`
  nested dict. Components in `self._components` (registered by
  `_assign_component_ids`) each contribute their public state. The
  reserved key keeps component snapshots out of the parent's flat
  attr namespace and gives the time-travel debug panel a clean shape
  to render per-component scrubbers.

  Restoration: `time_travel.restore_snapshot` detects
  `__components__` in the snapshot and dispatches each
  `{component_id: state}` entry to the matching component in
  `view._components` via `safe_setattr`. Components absent from the
  snapshot keep their current state — components are first-class
  instances, not parent-scoped attrs, so the ghost-attr cleanup model
  used for parent state doesn't apply.

  Files: `python/djust/live_view.py` (~60 LoC: `_capture_components_snapshot`
  helper + `_capture_snapshot_state` extension); `python/djust/time_travel.py`
  (~40 LoC: `_COMPONENTS_SNAPSHOT_KEY` constant + per-component
  restoration phase). 7 new cases in `TestComponentLevelTimeTravel`
  in `tests/unit/test_time_travel.py` cover capture-with-components,
  capture-without-components, private/callable filtering, restoration
  dispatch, unknown-component-id handling, absent-component
  preservation, and snapshot/live disconnection (mirrors the
  parent-state aliasing fix from PR #1023's Stage 11 review).

- **Parallel lazy render via `asyncio.as_completed` (v0.9.0 PR-C, closes #1043)** —
  closes the v0.9.0 streaming arc. PR-B shipped sequential thunk
  invocation in `arender_chunks` Phase 5 (one thunk runs to
  completion before the next starts; total wall-clock time =
  sum of thunk durations). PR-C swaps the for-loop for
  `asyncio.as_completed` over the thunk-task set. All thunks start
  concurrently; chunks emerge in completion order rather than
  registration order. Total wall-clock time = max(thunk_durations).

  Client-side reconciliation is keyed by slot id (`data-target` on
  `<template id="djl-fill-X">`), so out-of-order chunk arrival is
  correct by construction — no client changes needed.

  Cancellation: when the emitter is cancelled mid-stream (client
  disconnect), all pending thunk tasks are cancelled via
  `task.cancel()`. Already-completed tasks whose results were not yet
  iterated are GC'd. Tasks already running through `sync_to_async` to
  a synchronous render function will complete (asyncio cancellation
  doesn't propagate into sync DB work) — the documented contract per
  ADR-015 §"Cancellation contract".

  Files: `python/djust/mixins/template.py` (~50 LoC swap from
  for-loop to `asyncio.as_completed`). 3 new wall-clock-sensitive
  tests in `tests/integration/test_chunks_overlap.py`:
  - Three thunks (100ms, 50ms, 25ms) registered in that order →
    chunks arrive in completion order (slot-c, slot-b, slot-a).
  - Three 50ms-each thunks → wall clock under 100ms (sequential
    baseline 150ms).
  - One thunk raises → others still emit their fills (no stall).

  Closes #1043. v0.9.0 streaming arc complete: PR-A (foundation) →
  PR-B (`lazy=True` user API + `as_view` dispatch) → PR-C (parallel
  render).

- **`{% live_render lazy=True %}` capability + `as_view` dispatch wiring (v0.9.0 PR-B, ADR-015)** —
  ships the user-facing API on top of PR-A's async render foundation.
  Three forms: `lazy=True` (parent-flush trigger, default), `lazy="visible"`
  (IntersectionObserver-deferred), `lazy=dict` (full control —
  `trigger`, `timeout_s`, `on_error`, `placeholder` keys).

  At template-render time the tag emits a `<dj-lazy-slot data-id="X"
  data-trigger="flush">` placeholder synchronously and registers a
  thunk on `parent._lazy_thunks`. `RequestMixin.aget` transfers the
  stash onto the `ChunkEmitter` after the sync render completes.
  Phase-5 of `arender_chunks` invokes thunks AFTER the body-close
  chunk, so `</body></html>` lands at the wire BEFORE any lazy fill —
  the browser sees a fully-painted page (with placeholder spinners)
  while lazy children render server-side.

  Wire format (post-`</html>` per ADR §"Wire format"):
  ```html
  <template id="djl-fill-X" data-target="X" data-status="ok">
    <div dj-view data-djust-embedded="X">…rendered child…</div>
  </template>
  <script>window.djust.lazyFill('X')</script>
  ```
  The new `python/djust/static/djust/src/50-lazy-fill.js` module's
  `window.djust.lazyFill(slotId)` function scans for matching
  `<dj-lazy-slot data-id="X">` and replaces it with the template's
  contents. Idempotent on double-fire. `data-trigger="visible"` defers
  the actual replacement until the slot enters the viewport via
  IntersectionObserver. `data-status="error"`/`"timeout"` wraps the
  fill in `<dj-error aria-live="polite">` for screen-reader
  announcement.

  **Sticky + lazy = `TemplateSyntaxError` at tag eval** — hard
  incompatibility per ADR §"Failure modes". Sticky preservation
  requires the slot to exist at mount-frame time so the WS reattach
  can `replaceWith` the stashed subtree; lazy renders the slot AFTER
  mount, so the stash-target doesn't exist when reattach runs.

  **`as_view()` dispatch wiring** — `LiveView.as_view` is now
  overridden so that classes with `streaming_render = True` return an
  async view callable (via `markcoroutinefunction`) that routes GET to
  `aget()` when in real ASGI context. This is the wiring that makes
  PR-A's foundation actually active end-to-end. WSGI deployments fall
  back to sync `dispatch` via `sync_to_async`, preserving the Phase-1
  cosmetic chunked response behavior. The ASGI/WSGI signal is
  `isinstance(request, ASGIRequest)` — accurate even when the sync
  test `Client` wraps the async view via `async_to_sync` (the
  earlier loop-presence check was fooled by that wrapping).

  Files: `python/djust/templatetags/live_tags.py` (~210 LoC `lazy=`
  branch with thunk closure), `python/djust/mixins/template.py` (~40
  LoC Phase-5 thunk loop), `python/djust/mixins/request.py`
  (~15 LoC thunk transfer + `_lazy_thunks` reset + ASGIRequest-aware
  `_is_asgi_context`), `python/djust/live_view.py` (~50 LoC `as_view`
  override). New: `python/djust/static/djust/src/50-lazy-fill.js`
  (~140 LoC client). 14 new cases in
  `tests/unit/test_live_render_lazy.py` cover validation, placeholder
  emit, thunk stash, thunk closure including error + timeout
  envelopes. 2 new integration cases in
  `tests/integration/test_lazy_streaming_flow.py` drive the full
  pipeline (sync render → thunk transfer → arender_chunks Phase 1-5 →
  consumer drain) and assert the body-close-before-fills wire-format
  ordering.

  Foundation for PR-C (`asyncio.as_completed` parallel render across
  thunks; closes #1043 umbrella).

- **Async render-path foundation: `aget()` + `ChunkEmitter` + `arender_chunks()` (v0.9.0 PR-A, ADR-015)** — first PR of the v0.9.0 P2 streaming arc (#1043). Closes the v0.6.1 retro #116 doc-claim debt: Phase 1 was a regex-split-after-render with no real TTFB win; Phase 2 PR-A introduces the actual async render path so `streaming_render = True` shell-flushes to the wire BEFORE `get_context_data()` runs.

  New module `python/djust/http_streaming.py` (~230 LoC) provides the `ChunkEmitter` class — a per-request bounded `asyncio.Queue` with backpressure, cancellation propagation via `request_token`, and a `register_thunk()` API surface that PR-B (`{% live_render lazy=True %}`) will hook into. The emitter exposes `__aiter__` for direct consumption by `StreamingHttpResponse`.

  New `async def aget()` on `RequestMixin` (~150 LoC) parallel to the existing sync `get()`. Wraps the sync render via `sync_to_async(self.get)` to produce the full HTML, then drives `arender_chunks()` to push chunks through the emitter. Returns a `StreamingHttpResponse` with `X-Djust-Streaming: 1` and `X-Djust-Streaming-Phase: 2` headers. ASGI disconnect watcher cancels the emitter when the client closes the connection.

  New `arender_chunks()` async coroutine on `TemplateMixin` (~135 LoC) splits the rendered HTML at `<div dj-root>` boundaries into 4 chunks (shell-open / body-open / body-content / body-close) and pushes each via `emitter.emit()` with `await asyncio.sleep(0)` boundaries so ASGI flushes the shell to the wire before the body chunks are queued. Cooperative cancellation via `ChunkEmitterCancelled`. Single-chunk fallback for fragment templates (no `<div dj-root>`).

  `streaming_render = False` (default) stays on the sync `HttpResponse` path. WSGI deployments fall back to the Phase-1 regex-split-after-render via `_make_streaming_response` per the documented graceful-degrade contract.

  Files: `python/djust/http_streaming.py` (new), `python/djust/mixins/request.py` (`aget()` + `_is_asgi_context()`), `python/djust/mixins/template.py` (`arender_chunks()`), `docs/adr/015-phase-2-streaming.md` (ADR promoted from `.pipeline-state/feat-streaming-phase2-1043-adr-draft.md`). 18 new test cases in `tests/unit/test_async_render_path.py` cover ChunkEmitter basics + backpressure + cancellation, `arender_chunks` 4-yield invariant + fragment fallback + mid-stream cancel, `aget` streaming response shape + redirect passthrough + non-streaming fallback, and `_get_queue_max_from_settings` defaulting.

  PR-B (`{% live_render lazy=True %}` user API) and PR-C (`asyncio.as_completed()` parallel render) ship on top of this foundation.

- **`{% live_render ... sticky=True %}` auto-detects preserved stickies (closes #1032, ADR-014)** —
  the v0.6.0 Sticky LiveViews work shipped Dashboard→Settings→Reports
  preservation but left a known limitation: returning to a page that
  declares the sticky inline (`Dashboard → Settings → Dashboard`)
  re-mounted the child instead of reattaching the survivor — audio
  playback and any in-flight state on the sticky child died.

  The v0.9.0 P1 1.0-blocker fix teaches the `{% live_render %}` template
  tag to consult the consumer's `_sticky_preserved` registry at render
  time. When a survivor exists for the resolved `sticky_id`, the tag
  re-registers the survivor onto the new parent, marks the id in a new
  `consumer._sticky_auto_reattached` set, and emits a `<dj-sticky-slot>`
  placeholder rather than a fresh subtree. The consumer's existing slot
  scan + the client's existing `replaceWith` reattach then complete the
  round-trip without ever calling `mount()` on the survivor again.

  No wire-protocol changes. No new transport (cookie/header/handshake)
  needed — the existing WS pipeline already carries survivor info to
  the exact moment the tag renders. Falls through to fresh-mount
  unchanged on the HTTP GET path (no `_ws_consumer` back-reference) and
  on first-navigation (empty `_sticky_preserved`).

  Files: `python/djust/templatetags/live_tags.py` (~30 LoC tag-side
  branch), `python/djust/websocket.py` (`_sticky_auto_reattached` set
  init/reset + slot-scan skip-on-claim, ~12 LoC),
  `docs/adr/014-sticky-liveview-autodetect.md` (new ADR).
  4 new cases in `TestStickyAutoDetect` in
  `tests/unit/test_live_render_tag.py` cover no-consumer, empty-preserved,
  preserved-for-our-id, and preserved-for-other-id paths. 2 new
  integration cases in `tests/integration/test_sticky_redirect_flow.py`
  drive the full Dashboard→Dashboard auto-reattach pipeline (tag emit
  + consumer slot-scan skip-on-claim + survivor in `survivors_final`)
  end-to-end through the existing `_FakeConsumer` rig.

## [0.8.7rc1] - 2026-04-26

### Fixed

- **`DataTableMixin.get_table_context()` post-mount missing `show_stats` key
  (closes #1118)** — Stage 11 review of PR #1117 surfaced that
  `show_stats` was present in `_PRE_MOUNT_TABLE_CONTEXT` (the empty-table
  default returned before `init_table_state()` runs) but missing from the
  post-mount return dict. A template containing `{% if show_stats %}` would
  silently fall back to the falsy default pre-mount and then raise
  `VariableDoesNotExist` post-mount once `init_table_state()` had populated
  real state. One-line fix adds `"show_stats": self.table_show_stats` to the
  post-mount dict, alongside the existing `printable` / `column_stats` keys.

  Files: `python/djust/components/mixins/data_table.py` (one-key addition
  in `get_table_context()`); 2 new cases in
  `PreMountGuardTest` in `python/tests/test_data_table_mixin_liveview.py`
  cover post-mount default-False and class-override-True paths. The
  pre-existing `test_pre_mount_default_has_required_template_keys`
  symmetry test now passes against the fixed dict — that's the regression
  lock-in for any future post-mount key additions.

### Changed

- **Process canonicalizations from the v0.8.6 retro arc folded into
  CLAUDE.md (closes #1122, #1123, #1124, #1125)** — Five Stage 11 / retro-tracker
  learnings from PRs #1115 / #1117 / #1119 / #1120 are now canonicalized as
  additions to the existing "Process canonicalizations" section in
  `CLAUDE.md`. Each rule names the source PR so the audit trail is preserved.

  Topics covered: split-foundation pattern for high-blast-radius features
  (PR-A foundation + PR-B capability — validated 3× across the View
  Transitions arc, #1122); pre-mount/post-mount keyset invariant test
  pattern for mixins with default-state dicts (#1123); CodeQL
  `js/tainted-format-string` self-review checkpoint — use
  `console.error('[label] msg %s:', val, errObj)` not template literals when
  the label derives from user-controlled DOM data (#1124); bulk
  dispatch-site refactor PRs need N tests for N sites + a count-test
  guarding the EXPECTED list against drift (#1125); format-string hygiene
  in test assertions when the assertion is itself an f-string referencing
  caught exceptions (PR #1120 retro).

  Docs-only change. No code or test surface modified.

## [0.8.6rc1] - 2026-04-26

### Changed

- **Process canonicalizations from the v0.8.5 → v0.8.6 retro arc folded into
  CLAUDE.md (closes #1100, #1101, #1103, #1104, #1106, #1108, #1109)** —
  Eight Stage 11 / retro-tracker learnings from the View Transitions PR-A →
  PR-B arc and the downstream-consumer gap-fix arc are now canonicalized as a single
  "Process canonicalizations" section in `CLAUDE.md`. Each rule names the
  source PR so the audit trail is preserved.

  Topics covered: completeness-grep after async-migration regex passes
  (#1100); ADR scope-estimation counts test-file callers (#1101); `is None`
  coalesce vs `kwargs.setdefault` for mixin kwarg-forwarding (#1103);
  mechanical-replacement PRs need N tests for N sites (#1104); CHANGELOG
  test-count phrasing for additions to existing files (#1106);
  `Iterable[T]` over `list[T]` for membership-check parameters (#1108);
  dynamic subclass via `type(name, bases, dict)` over class-attr mutation
  in test fixtures (#1109); microtask-faithful test stubs for
  `startViewTransition` / `MutationObserver` / `IntersectionObserver`
  (PR #1113 retro); batch-PR issue × file × test mapping table convention
  (PR #1115 retro).

  Docs-only change. No code or test surface modified.

### Added

- **`djust.C013` system check — stale collectstatic copy of `client.min.js`
  (closes #1088)** — anyone with `STATIC_ROOT` configured (typical
  production deployment behind WhiteNoise / nginx / a CDN) can ship a
  stale `client.min.js` after a djust wheel upgrade if they forget
  `collectstatic --clear`. The server runs new code; the browser loads
  old client.js → wire-protocol skew → mysterious VDOM patch failures.
  #1081 was reopened twice before the reporter root-caused this
  structurally-recurring trap.

  C013 compares the SHA-256 of `STATIC_ROOT/djust/client.min.js`
  against the wheel-bundled copy at `python/djust/static/djust/client.min.js`.
  When they diverge, emits a Django system warning at startup with the
  exact fix command. No-op when `STATIC_ROOT` is unset, when the
  collected file is absent (pre-collectstatic), or when content matches.
  Honors `DJUST_CONFIG = {"suppress_checks": ["C013"]}` for users who
  serve `client.min.js` from a CDN or custom build.

  Files: `python/djust/checks.py` (new `_check_stale_collected_client`,
  wired into `check_configuration`); 5 cases in `TestC013StaleCollectstatic`
  in `python/tests/test_checks.py` cover no-STATIC_ROOT skip,
  no-collected-file skip, matching-content quiet, diverged-content
  warning, suppress-via-DJUST_CONFIG silence.

### Fixed

- **`|date` and `|time` filters now debug-log on parse failure (closes
  #1090)** — both filters previously fell through silently to the
  original value when chrono failed to parse the input string. The
  #1081 4-round-reopen investigation would have collapsed to a
  5-minute diagnosis if a single line had been logged at parse-failure
  time. Now the failure is surfaced via `tracing::debug!` against
  target `djust.templates.filters` with the offending value, format
  string, and chrono error message.

  Enable via Python `LOGGING['loggers']['djust.templates.filters'] =
  {'level': 'DEBUG'}` or set `RUST_LOG=djust.templates.filters=debug`
  for the Rust-side `tracing` consumer. Behavior unchanged when the
  log target is disabled — just no longer a silent void.

  Files: `crates/djust_templates/Cargo.toml` (added `tracing`
  workspace dep), `crates/djust_templates/src/filters.rs` (`|date`
  arm at line ~248, `|time` arm at line ~284 — replaced
  `Err(_) => Ok(value.clone())` with `Err(e) => { tracing::debug!(...);
  Ok(value.clone()) }`).

- **`_flush_deferred_to_sse` legacy-view guard now has a regression
  test (closes #1093)** — Stage 13 review of PR #1091 flagged that the
  WS-side `hasattr` guard had a parallel test
  (`test_flush_deferred_handles_view_without_drain_method`) but the
  SSE-side did not. New `test_sse_flush_deferred_handles_view_without_drain_method`
  in `python/djust/tests/test_defer.py` mirrors the WS shape — a
  legacy view class without `_drain_deferred` must short-circuit
  cleanly without `AttributeError`.

- **Release wheel matrix expanded to cp313 + cp314 (closes #1089)** —
  `.github/workflows/release.yml` previously built only cp310/cp311/cp312
  wheels. Users on Python 3.13 or 3.14 fell back to source-compiling
  the sdist at `pip install` time, producing untested binaries whose
  runtime behavior could diverge from CI-tested cp312 (this was the
  root cause of #1081's first reopen — reporter on 3.14 hit a source-
  compiled `_rust.cpython-314-darwin.so`). Matrix now ships tested
  wheels for cp310–cp314 across Linux x86_64, macOS Intel + ARM, and
  Windows x86_64 (Windows still excludes 3.10 per the existing
  policy).

- **View Transitions API integration in `applyPatches` (PR-B / ADR-013)** —
  Opt-in via `<body dj-view-transitions>`. When the browser supports
  `document.startViewTransition()` AND the body attribute is present
  AND the user has not requested `prefers-reduced-motion: reduce`,
  every server-driven VDOM patch is wrapped in a View Transition: the
  browser captures a pre-state frame, runs our patch loop, captures
  the post-state, and animates between them.

  **Default cross-fade** for free, with one body-level attribute. **Shared-
  element morphs** via `view-transition-name` CSS — animate matching
  named elements between two completely different DOM trees (the
  "card flies into hero on detail page" pattern). **Custom animation
  timing/easing** via `::view-transition-old(name)` / `::view-transition-new(name)`
  pseudo-elements — designer-driven, no JS.

  **Browser support gate**: Chrome 111+, Edge 111+, Safari 18+. Firefox
  graceful-degrades — patches still apply, no animation. ~85% of
  current djust users get the polish; the remaining ~15% see no
  regression. Re-evaluated on every patch so dynamic mid-session
  opt-in via `document.body.setAttribute('dj-view-transitions', '')`
  works.

  **Failure path**: when the wrap callback throws, the wrapper logs at
  ERROR, calls `transition.skipTransition()` to abandon the animation,
  and returns false so the existing full-re-render fallback at
  `02-response-handler.js:109` fires. The async signature shipped in
  v0.8.5rc1 (PR-A) is what makes the callback's microtask semantics
  observable — the previous attempt (PR #1092) used a sync callback
  and silently lost the boolean return.

  **Why this matters**: View Transitions enables wizard step morphs,
  modal open/close animations, navigation-primitive page transitions
  (free polish for the `dj-prefetch` work shipped in v0.7.0), list
  reorders, and tab-switch cross-fades — without per-component
  animation code or runtime JS animation libraries.

  Files: `python/djust/static/djust/src/12-vdom-patch.js` adds
  `_shouldUseViewTransition()` gate and refactors `applyPatches` into
  a thin wrap-or-direct dispatcher; the existing patch-loop body
  becomes `_applyPatchesInner` (sync — no behavior change inside).
  Cleanup: `03-websocket.js` (2 sites) and `03b-sse.js` (1 site)
  drop the now-redundant outer `.catch()` on `handleMessage` calls
  — the queue wrapper from #1098 already has an internal `.catch()`,
  so the outer was dead code (Stage 11 nit from PR #1112).

  New test file `tests/js/view-transitions.test.js` covers all four
  `_shouldUseViewTransition` branches (API present, opt-in absent,
  opt-in present, reduced-motion), success/empty/wrap-throws paths,
  microtask-deferral correctness (DOM is unchanged before await),
  dynamic mid-session opt-in toggle, and direct-path parity.
  The vitest stub invokes the callback in a microtask via
  `await Promise.resolve()` to mirror real-browser semantics — NOT
  synchronously like the failed PR #1092 stub.

  ROADMAP Phoenix LiveView Parity Tracker `View Transitions API` →
  shipped. Quick Win #23 closed.

### Added

- **Async-tolerant `dj-hook` lifecycle dispatch (v0.8.6 enhancement
  cashing in PR-A async refactor)** — `dj-hook` lifecycle methods
  (`mounted`, `updated`, `beforeUpdate`, `destroyed`, `disconnected`,
  `reconnected`, `handleEvent`) may now be `async`. The dispatcher
  detects Promise return and chains `.catch` to log rejections via
  `console.error` — no Unhandled Promise Rejection in the browser
  console.

  ```javascript
  window.djust.hooks.UserAvatar = {
      async mounted() {
          const res = await fetch(`/api/profile/${this.el.dataset.userId}`);
          const profile = await res.json();
          this.el.querySelector('img').src = profile.avatar_url;
      },
  };
  ```

  **Fire-and-forget contract**: the dispatcher does NOT await user
  hooks. Lifecycle callbacks fire-and-forget so user I/O can't block
  the render loop. Sync hooks behave exactly as before — strictly
  additive, no API change for existing hook code.

  Implementation: new `_safeCallHook(fn, label, ...args)` helper in
  `python/djust/static/djust/src/19-hooks.js` wraps the existing
  try/catch sites for each lifecycle path. 9 sync sites refactored to
  use the helper (mounted×2, beforeUpdate, updated, destroyed×2,
  disconnected, reconnected, handleEvent). New file
  `tests/js/async_hooks.test.js` with 5 cases
  cover sync-unchanged behavior + async-Promise-rejection-logging +
  fire-and-forget timing contract.

- **`docs/website/guides/view-transitions.md` — comprehensive guide for
  the View Transitions API integration shipped in v0.8.6 PR #1113** —
  covers the `<body dj-view-transitions>` opt-in, browser support
  matrix (Chrome 111+, Edge 111+, Safari 18+, Firefox graceful
  degrade), `prefers-reduced-motion` accessibility bypass,
  shared-element transitions via `view-transition-name`, custom
  animation timing via `::view-transition-old(name)` /
  `::view-transition-new(name)` pseudo-elements, `await
  window.djust.applyPatches(...)` as public API for third-party JS,
  and a critical "JSDOM stub microtask correctness" section
  (mirroring the regression class that bit PR #1092). Linked from
  `_config.yaml` and `index.md` per the docs-nav convention.

- **`{% data_table %}` link column type (closes #1110)** — column dicts
  now accept a `link` key naming another row dict key that holds the
  href, and an optional `link_class` for the `<a>` element's CSS class:

  ```python
  table_columns = [
      {"key": "claim_number", "label": "Claim #", "link": "claim_url",
       "link_class": "claim-link"},
  ]
  # row dicts include both keys:
  {"claim_number": "2026PI000001", "claim_url": "/claims/1/", ...}
  ```

  Renders as:

  ```html
  <td><a href="/claims/1/" class="claim-link">2026PI000001</a></td>
  ```

  Falls through to plain text when `col.link` is unset — strict
  backwards-compat with pre-#1110 column dicts. Replaces the
  `_inject_link_column` regex post-process workaround downstream
  consumers had to maintain (e.g. downstream-consumer).

- **`{% data_table %}` row-level navigation: `row_click_event` + `row_url`
  (closes #1111)** — the entire `<tr>` becomes clickable for navigation.
  Two API shapes:

  **Option B (preferred — LiveView-idiomatic)**: `row_click_event` fires
  a djust event with `data-value=row[row_click_value_key]`. Default
  value key is `"id"`; override per-table for slug-based routing:

  ```python
  table_row_click_event = "open_claim"
  table_row_click_value_key = "uuid"
  ```

  ```python
  @event_handler()
  def open_claim(self, value: str = "", **kwargs):
      self.redirect(reverse("claims:detail", kwargs={"claim_id": value}))
  ```

  **Option A (static URL fallback)**: `row_url` names a row dict key
  containing the href; the `<tr>` gets `data-href` + an `onclick` that
  reads `this.dataset.href` and navigates:

  ```python
  table_row_url = "claim_url"
  ```

  Both options also wire `style="cursor:pointer"` on each `<tr>` for
  the affordance. `row_click_event` takes precedence when both are set.
  Mirrored in `DataTableMixin` via `table_row_click_event`,
  `table_row_click_value_key`, and `table_row_url` class attributes,
  threaded through `get_table_context()` and `_PRE_MOUNT_TABLE_CONTEXT`.

  **Security note for Option A (`row_url`)**: the URL flows into JS via
  `onclick="window.location=this.dataset.href"`. Only assign
  developer-controlled URLs (typically computed from `reverse()`);
  user-controlled strings could enable `javascript:` URI execution.
  **CSP note**: Option A requires `'unsafe-inline'` in `script-src`;
  prefer Option B (LiveView event) when CSP is strict. Option B is
  CSP-clean — the click is dispatched via the existing djust event
  pipeline, no inline JS executed.

  14 regression cases in `python/tests/test_data_table_link_row_nav.py`
  cover: link-column emits `<a>`; link_class flows through; no-link
  pre-#1110 compat; `row_click_event` adds `dj-click` to every `<tr>`;
  `row_click_value_key` overrides default `id`; absent `row_click_event`
  → no `<tr>` `dj-click` (compat); `row_url` adds `data-href` + JS;
  `row_click_event` precedence over `row_url`; mixin class-attr
  defaults; per-view override; pre-mount default + post-mount context
  + template-tag function include all 3 new keys.

### Fixed

- **`DataTableMixin` LiveView compatibility — pre-mount guard +
  `@event_handler()` decoration on all `on_table_*` methods (closes
  #1114)** — using `DataTableMixin` in a `LiveView` (rather than a
  `Component`) caused a blank/empty table on every page load even
  when `refresh_table_server()` correctly populated `self.table_rows`
  in `mount()`. Three compounding root causes:

  1. **BUG-06 pre-mount lifecycle**: djust's WebSocket consumer calls
     `get_context_data()` (which often calls `get_table_context()`)
     BEFORE `mount()` runs, to build the initial Rust VDOM snapshot.
     `init_table_state()` hadn't run yet, so `self.table_rows` didn't
     exist and `get_table_context()` raised `AttributeError`. djust
     caught it silently → empty initial VDOM → all subsequent VDOM
     patches diff against empty content → wrong renders.
  2. **Missing `@event_handler()` decoration**: `on_table_sort`,
     `on_table_search`, and 19 other handlers were plain methods.
     djust's default `event_security="strict"` rejected them — every
     consumer had to write wrapper boilerplate.
  3. **Documentation gap**: the API boundary between Component and
     LiveView use cases wasn't called out anywhere in the mixin's
     docstring.

  Fix: `get_table_context()` now guards on `hasattr(self, "table_rows")`
  and returns `_PRE_MOUNT_TABLE_CONTEXT` (a module-level minimal
  default with every key the `{% data_table %}` template tag reads —
  ~80 keys covering all 5 phases). All 21 `on_table_*` handlers now
  carry `@event_handler()` decoration. Mixin docstring expanded with a
  "LiveView vs Component lifecycle" note + recommended pattern for
  large datasets (pass queryset directly via `get_context_data()`,
  define `@event_handler()` methods on the view).

  Downstream impact: downstream-consumer PR #189 attempted migration and hit
  this; PR #191 reverted to native handlers. With this fix,
  `DataTableMixin` is usable from `LiveView` subclasses without
  per-handler boilerplate.

  8 regression cases in
  `python/tests/test_data_table_mixin_liveview.py` cover: pre-mount
  call doesn't raise; pre-mount returns the default; post-mount
  returns real state; pre-mount key set is a superset of post-mount
  (catches future post-mount additions that forgot to update the
  default); all 21 expected handlers have `_djust_decorators`
  metadata; handler count matches expected (catches future additions
  that forgot decoration); docstring mentions LiveView lifecycle and
  `@event_handler()` decoration (catches doc-rot).

- **`handleMessage` interleaving across `await` boundaries (closes #1098)** —
  PR-A (v0.8.5rc1) made `LiveViewWebSocket.handleMessage` and
  `LiveViewSSE.handleMessage` async without serializing the inbound
  frame queue. Two adjacent inbound frames could fire-and-forget
  `_handleMessageImpl` concurrently and interleave their
  `await handleServerResponse` calls — racing on shared state like
  `_pendingEventRefs` / `_tickBuffer` (`03-websocket.js:561-568` reads
  `.size` AFTER an `await`, so an in-flight second message could
  mutate the set between check and flush). Latent today; would have
  been meaningfully worse when PR-B (View Transitions wrap) widened
  the await window inside `applyPatches` itself.

  Fix: per-transport `_inflight` Promise chain. Each `handleMessage(data)`
  invocation chains onto the prior in-flight promise. Sequential drain
  across rapid-fire frames; no interleaving. Errors propagate through
  `.catch()` (logged via `console.error`) so the chain continues even
  when one frame rejects — a single bad frame doesn't poison the queue.

  Existing async `handleMessage` body renamed to `_handleMessageImpl`
  (private). New public `handleMessage(data)` is a thin wrapper that
  enqueues onto `this._inflight`. Both transports (WebSocket + SSE)
  apply the same pattern.

  New regression file `tests/js/handlemessage_serialization.test.js`
  covers: rapid-fire ordered drain (later messages with shorter delays
  must NOT finish first); throwing message doesn't poison the chain;
  returned promise resolves only after this frame drains; both WS and
  SSE expose `handleMessage` and `_handleMessageImpl` separately and
  serialize.

  Caller-side test migration: 4 existing test files updated to `await`
  the now-queued `handleMessage` calls (`dj-cloak`, `hvr`,
  `sse-transport`, `sw_advanced`) — same kind of un-awaited-call gap
  that Stage 11 caught on PR #1099. 1402 JS tests pass; 2080 Python
  tests pass.

  PR-B (View Transitions wrap) is now unblocked.

## [0.8.5rc1] - 2026-04-26

### Added

- **`WizardMixin.wizard_rendered_fields` opt-in skips `field_html` rendering
  for fields not in the list (closes #1097)** — `WizardMixin.get_context_data()`
  unconditionally pre-rendered `field_html` for **every** field on the
  current step's form, regardless of whether the template referenced that
  field. Wizards with conditional fields (e.g. owner-info hidden behind
  `is_vehicle_owner == "no"`) paid the rendering cost on every event for
  fields nobody ever sees. Reported impact on the downstream-consumer VPD wizard:
  115ms template render (threshold: 50ms), 47 VDOM patches per autofill —
  most for invisible inputs.

  New API (default behavior unchanged — `None` renders all):

  - **Class-level**: `wizard_rendered_fields = ["first_name", "vin", ...]`
    on the wizard view limits `field_html` to that subset across every step.
  - **Per-step override**: a step dict can include
    `{"name": "...", "form_class": ..., "rendered_fields": [...]}` to scope
    the filter to that step. Wins over the class-level default.

  `form_data`, `form_required`, and `form_choices` are NOT filtered — all
  fields remain part of validation/state. Only the (expensive) HTML rendering
  is opt-in skipped. Excluded field names produce no `field_html[fname]`
  entry; templates that reference them via `{{ field_html.unused|safe }}`
  render empty (the dict-key absence is intentional and visible).

  Files: `python/djust/wizard.py` (class attribute, per-step lookup +
  filter in `get_context_data()`); `python/tests/test_wizard_rendered_fields.py`
  with 8 cases in `DefaultRendersAllFieldsTest`,
  `ClassAttributeFiltersTest`, `PerStepOverrideTest`.

  Future direction: a smarter automatic template-scan (similar to the JIT
  serializer's used-field detection) could drive this without explicit
  developer wiring. This PR ships the explicit escape hatch first.

- **`WizardMixin.wizard_input_event` class attribute + `dom_event` kwarg on
  `as_live_field()` — configurable DOM event for live-field validation
  binding (closes #1095)** — `WizardMixin.as_live_field()` previously emitted
  `dj-change="<handler>"` unconditionally on text/textarea/select/checkbox/
  radio inputs. `dj-change` fires only on blur, so a user who edits a
  pre-filled field and clicks Next without tabbing away has their edit
  silently discarded. Wizards with autofill or pre-filled-from-database
  fields hit this routinely.

  New API: a class-level default and a per-call override.

  Class default::

      class MyWizard(WizardMixin, LiveView):
          wizard_input_event = "dj-input"   # default: "dj-change"

  Per-call override::

      view.as_live_field("email", dom_event="dj-input")

  ``dj-input`` fires on every keystroke (300ms client-side debounce
  already in `09-event-binding.js`), so edits land regardless of whether
  the user blurs first. Per-call kwarg wins over the class attribute.

  Default behavior unchanged (``"dj-change"``), so this is a strictly
  additive opt-in — existing wizards see no behavior change. Replaces
  the regex post-process workaround that downstream consumers (e.g.
  downstream-consumer PR #185) had to maintain.

  Files: `python/djust/wizard.py` (class attribute, `as_live_field`
  forwards `dom_event` through `kwargs.setdefault`), `python/djust/
  frameworks.py` (5 sites — `_render_input` text/textarea/select,
  `_render_checkbox`, `_render_radio` — read `kwargs.get("dom_event",
  "dj-change")` instead of hardcoding `"dj-change"`).

  14 regression cases in `python/tests/test_wizard_input_event.py`
  cover: default class attribute is `"dj-change"`; default rendering on
  text/textarea/select/checkbox/radio emits `dj-change`; per-call
  `dom_event="dj-input"` swaps to `dj-input` and removes `dj-change`;
  `wizard_input_event = "dj-input"` flows through `as_live_field()`;
  per-call kwarg overrides class attribute; `dom_event=None` coalesces
  to the class attr instead of producing `attrs[None]`.

- **`self.defer(callback, *args, **kwargs)` — Phoenix-style post-render
  callback scheduling** — new method on `AsyncWorkMixin` (and therefore on
  every `LiveView`) that schedules a callback to run **once, after the
  current render+patch cycle completes**. Phoenix `send(self(), :foo)` /
  React `useEffect` (post-render) parity. Fires synchronously in the same
  WebSocket message cycle (after `_send_update` returns) — so deferred
  callbacks observe the post-patch state. Use cases: telemetry emission
  after the user sees the change, post-render cleanup of temporary state,
  scheduling follow-up side effects without re-rendering.

  Differs from `start_async`: `defer` does NOT trigger a re-render after
  the callback returns (the caller would use `start_async` for that), and
  runs synchronously in the same WS frame rather than spawning a
  background thread. Append-only queue: every `defer()` call adds to a
  per-view list that is drained and cleared by
  `LiveViewConsumer._flush_deferred()` after every `_send_update()` call
  (10 sites in `python/djust/websocket.py`, mirroring the existing
  `_flush_push_events` / `_flush_flash` / `_flush_page_metadata` /
  `_flush_pending_layout` post-render-flush pattern).

  Async callbacks (`async def` or coroutine-returning) are awaited inline.
  Exception isolation: a failing deferred callback is logged at WARN
  with full traceback and execution continues to the next callback in the
  queue — a deferred callback's failure must not break the WebSocket
  connection or the user's interactive flow. 19 regression cases in
  `python/djust/tests/test_defer.py` cover queue mechanics
  (append/drain/clear), arg/kwarg passing, ordering, sync+async mix,
  exception isolation, edge cases (no `view_instance`, view without
  `AsyncWorkMixin`), drain-reentry contract (a callback that calls
  `defer(other)` enqueues `other` for the **next** drain — Phoenix-style,
  prevents unbounded loops), and SSE transport integration (mirror flush
  via `_flush_deferred_to_sse()` in `python/djust/sse.py`).

  Example::

      class CounterView(LiveView):
          @event_handler
          def increment(self, **kwargs):
              self.count += 1
              self.defer(self._record_metric, action="increment")

          def _record_metric(self, action: str):
              # Fires AFTER the patch reaches the client.
              metrics.increment(f"liveview.{action}", count=self.count)

  Phoenix LiveView Parity Tracker entry `self.defer()` (post-render) marked
  shipped in `ROADMAP.md`.

### Changed

- **VDOM `applyPatches` signature is now `async` (returns `Promise<boolean>`)** —
  foundational refactor preparing for View Transitions API integration
  (ADR-013). Previously `applyPatches(patches, rootEl)` returned `boolean`
  synchronously; now `async function applyPatches(patches, rootEl) ->
  Promise<boolean>`. The patch-loop body itself is unchanged — this is a
  signature-only migration. Direct caller migration covers six call sites
  across the client modules: `02-response-handler.js`, `03-websocket.js`,
  `03b-sse.js`, `11-event-handler.js`, `45-child-view.js`. Each `await`s
  `applyPatches` and propagates async upward — `handleServerResponse` is
  now `async`, `LiveViewWebSocket.handleMessage` and
  `LiveViewSSE.handleMessage` are now `async`, and the `EventSource`
  `onmessage` arrow callbacks (which cannot be `async` in their declared
  form) wrap their `handleMessage` invocations in `.catch()` to preserve
  unhandled-rejection visibility. `_applyScopedPatches`,
  `handleChildUpdate`, and `handleStickyUpdate` in `45-child-view.js` are
  also `async`.

  Why this signature change matters: `document.startViewTransition()`'s
  callback runs in a microtask after the browser captures the pre-patch
  frame, NOT synchronously, so any wrapping that schedules patches via
  `startViewTransition` requires the patch function to be awaitable. PR-A
  (this entry) is the foundation; PR-B will add the View Transitions
  wrap on top without further signature changes.

  No external API change for view authors — VDOM internals only.
  **Newly exposed public surface**: `window.djust.applyPatches` is now
  explicitly assigned via `globalThis.djust.applyPatches = applyPatches`
  at the end of `12-vdom-patch.js`. (Previously the function was
  reachable in test environments only by `eval`-host-scope hoisting,
  which async declarations don't honor under JSDOM.) Hook code that
  monkey-patches `applyPatches` should now address the namespace
  explicitly and treat the return value as a `Promise<boolean>`.

  Test surface migrated: 8 JS test files updated to `await`
  `applyPatches` / `handleMessage` / `handleServerResponse` calls and
  switch to `dom.window.djust.applyPatches`. 1396 JS tests pass; 4230
  Python tests pass; behavior parity with the previous sync signature
  confirmed by the existing patch test suite
  (`vdom_patch_errors.test.js`, `vdom_recovery.test.js`,
  `tab_switch_real_repro.test.js`, `event_sequencing.test.js`,
  `batch_insert_before_remove.test.js`, `vdom-autofocus.test.js`,
  `sse.test.js`).

### Fixed

- **`djust.T012` false positive on `{% include %}` partial templates (closes
  #1096)** — `T012` (template uses `dj-*` event directives but missing
  `dj-view`) fired unconditionally for any template containing `dj-click`,
  `dj-input`, etc., even when the file was an intentional fragment included
  from a parent LiveView root. Wizards with 15+ step partials produced a
  noisy 15-warning wall in `manage.py check`.

  Two opt-out paths now silence T012 for legitimate fragments:

  1. **Per-template marker**: add `{# djust:partial #}` (case-insensitive,
     whitespace flexible) anywhere in the template. The marker is the right
     choice when most fragments in a project don't need the check but a few
     full-page templates do.
  2. **Global suppression**: `DJUST_CONFIG = {"suppress_checks": ["T012"]}`
     in `settings.py`. Right when the project never uses T012's intended
     diagnostic (e.g. component-only architectures).

  T012's hint now mentions both options. Component templates (`dj-component`
  present) continue to bypass T012 as before — pre-existing behavior
  unchanged.

  Files: `python/djust/checks.py` (new `_DJ_PARTIAL_MARKER_RE`, T012 guard
  reads partial marker AND `_is_check_suppressed("djust.T012")` —
  previously the global suppression infrastructure existed but T012 wasn't
  wired in). New cases added to `TestT012EventDirectivesWithoutView` in
  `python/tests/test_checks.py` cover: partial marker silences T012;
  case-insensitive matching; global suppression via short ID (`"T012"`)
  and qualified ID (`"djust.T012"`); hint text mentions both opt-out
  paths.

- **`scripts/check-changelog-test-counts.py` regex missed `async def test_*`** —
  the test-counter pre-push hook's `PY_TEST_FN_RE` matched only `def test_*`,
  silently undercounting pytest-asyncio test files (any module-level
  `async def test_*` was invisible). Updated the pattern to
  `^[ \t]*(?:async\s+)?def\s+test_\w+\s*\(` so async tests are counted
  alongside sync tests. Surfaced via `tests/test_defer.py` (7 sync class-method
  tests + 7 module-level async tests = 14 total; pre-fix the hook reported 7
  and the CHANGELOG claim of "14 regression cases" tripped a false drift
  warning). Mechanical fix; no behavior change for files that don't use
  `async def test_*`.

## [0.8.4rc1] - 2026-04-26

### Fixed

- **Inheritance resolution doubled filter-arg quotes — `|date:"M d, Y"` rendered
  as `&quot;Apr 25, 2026&quot;` (closes #1081)** — `nodes_to_template_string`
  in `crates/djust_templates/src/inheritance.rs` was wrapping every filter arg
  in `\"…\"` when serializing the resolved-inheritance AST back to a template
  string. But `parse_filter_specs` deliberately preserves any surrounding quotes
  on literal args (the dep-tracking extractor needs them to disambiguate
  literals from bare-identifier variable references — see #787). So an arg
  parsed from `|date:"M d, Y"` came out of the parser as the string `"M d, Y"`
  (with the quote chars), and the round-trip wrapped it again to produce
  `|date:""M d, Y""`. Re-parsing the resolved template then stripped the outer
  pair, leaving the inner `"M d, Y"` as the format spec; chrono treats `"` as
  literal output characters in strftime-style formats, so the rendered date
  came out as `"Apr 25, 2026"`, then HTML-escape converted the `"` to
  `&quot;` — surfacing as `&quot;Apr 25, 2026&quot;` in the rendered DOM.
  The fix emits the arg verbatim (`|filter:{arg}`) since `parse_filter_specs`
  already preserves the source-form quotes; round-trip is now idempotent.
  Same fix applied to the `Node::InlineIf` branch (a `{{ x if cond else y |
  filter:"…" }}` chain has the same shape). 29 regression cases in
  `tests/unit/test_filter_literal_args_1081.py` + 3 in
  `crates/djust_templates/src/inheritance.rs` lock the round-trip invariant.
  Surfaced via PR #1086 against an actual 26,785-char inheritance-resolved
  template (`<style>` blocks with quoted CSS font names + the date filter).
  Failure mode was inheritance-resolution-specific: simple inline templates
  (no `{% extends %}`) never hit `nodes_to_template_string` and rendered
  correctly all along — which is why the simple-template regression suite
  passed but production templates with inheritance failed.

### Added

- **Regression tests locking literal filter-arg quote stripping (#1081)** — issue
  reported `{{ d|date:"M d, Y" }}` rendering as `&quot;Apr 25, 2026&quot;` (literal
  double-quotes wrapping the result) and `{{ x|default:"fallback" }}` rendering
  as `&quot;fallback&quot;`. Investigation across all renderer code paths
  confirmed the existing `strip_filter_arg_quotes` helper (landed v0.5.2rc1 via
  #787) is invoked at every filter-application site:
  `render_node_with_loader` (Variable + InlineIf nodes, both call sites at
  `crates/djust_templates/src/renderer.rs:271,328`) and `get_value` for inline
  filter chains (renderer.rs:1556 — inline `arg_str = arg_str[1..len-1]` strip).
  When the issue was reopened with a more specific reproduction path
  ("Django `DateField` from a model passes through the Rust context serializer
  before being filtered, output is inserted as JSON string value into VDOM"),
  re-tested the named path and confirmed `serialize_context`
  (`crates/djust_live/src/lib.rs:1776-1781`) returns the bare ISO string —
  `value.call_method0("isoformat")` is passed straight through `into_pyobject`
  with no `serde_json::to_string` or quote-wrapping. No reproducible code path
  produces the reported output on `main` (= v0.8.3rc1).
  New `tests/unit/test_filter_literal_args_1081.py` ships **24 cases** covering
  every literal-arg shape from the issue body, follow-up comments, and reopen:
  (1) `|date` with `"M d, Y"` / `"F j, Y"` / single-quoted format / dotted-path
  field access; (2) `|default` with simple word / multi-word / slash / em-dash /
  dash / "No" / single-quoted / truthy passthrough / None fallback; (3) chains
  (`|date:"…"|default:"…"`, `|default:"…"|upper`); (4) HTML attribute context
  (where any leftover literal quote would surface as `&quot;`); (5)
  `serialize_context` output shape — bare ISO string for `date` /
  `datetime` / list-of-dicts (the queryset+model+date path named in the
  reopen); (6) full `LiveView.render()` with Django Model + DateField via the
  JIT serializer; (7) `LiveView.render()` with list of Model instances
  (`_jit_serialize_queryset` / `_jit_serialize_model` path); (8)
  `render_with_diff` full + partial (the WS-update path the reopen described
  as inserting JSON-quoted values into the VDOM). Locks the invariant against
  future renderer / VDOM-patch / JIT-serializer / context-serializer refactors
  so the JSON-quoting class of bug cannot silently re-emerge.

### Changed

- **`ROADMAP.md` staleness sweep (post-v0.8.3rc1)** — verified each unchecked
  Priority Matrix row, Quick Wins bullet, Medium Effort bullet, Major Features
  bullet, and Phoenix LiveView Parity Tracker row against the codebase. Marked
  ~30 items with ✅ + strikethrough + the actual implementation path (e.g.
  `static/djust/src/26-js-commands.js` for JS Commands, `python/djust/streaming.py`
  for AI streaming primitives, `crates/djust_vdom/src/parser.rs` for keyed
  for-loop change tracking). Annotated the genuinely-pending items with
  `*(verified: no … references in tree)*` so the next person to triage doesn't
  re-discover the same false signals. Items confirmed shipped:
  JS Commands, Flash messages, `on_mount` hooks, Function components,
  `assign_async`/AsyncResult, Template fragments, Keyed for-loop change tracking,
  Temporary assigns, `dj_suspense`, Named slots with attributes, Server Actions
  (`@action`), Async Streams, Keep-Alive/`dj-activity`, WebSocket compression,
  `dj-track-static`, `dj-no-submit`, `page_loading` on push, `dj-sticky-scroll`,
  `dj-paste`, `dj-ignore-attrs`, `handle_params`, `handle_async`, Hot View
  Replacement, `dj-lock`, `dj-auto-recover`, `dj-cloak`, `dj-copy`,
  Scoped JS selectors, Component `update` callback, Nested components
  (`LiveComponent`), Targeted events (`dj-target`), Declarative assigns,
  Selective re-rendering (VDOM partial), `handle_info`, Animations
  (`dj-transition`), Transition groups (`dj-transition-group`), Exit
  animations (`dj-remove`), DOM mutation events (`dj-mutation`),
  Sticky scroll, CSP nonce, Viewport events, Direct-to-S3 uploads,
  Prefetch on hover/intent (`dj-prefetch`), Server functions (`@server_function`),
  Push navigate, Back/forward restoration, Paste event handling, Scroll into
  view, AI streaming primitives. Items confirmed genuinely-pending (with
  greppable evidence): View Transitions API, `used_input?`, `@rest` attribute
  spread, `self.defer(callback)`, Multi-tab sync (BroadcastChannel),
  Offline mutation queue, State undo/redo, Connection multiplexing,
  Portal rendering, Server-only components, Islands of interactivity,
  i18n live language switching. Docs-only change; no runtime behavior.

## [0.8.3rc1] - 2026-04-25

### Added

- **`make docs-lint` — sweep docs/**/*.md for stale cross-references (closes #1075)** —
  new `scripts/docs-lint.py` walks every markdown link in `docs/` (excluding
  the rendered `docs/website/` site dir), parses `[text](target.md)` patterns,
  and reports any whose relative target doesn't resolve. Mirrors `make
  roadmap-lint` from Action #142 — manual `make docs-lint`, with optional
  `VERBOSE=1` to list every stale ref. Also wired into `.pre-commit-config.yaml`
  as a pre-push hook so the stale-ref class can't regress.

### Fixed

- **53 stale .md cross-references across 16 files in docs/ (closes #1075)** —
  follow-up to #1010. Sweep found 53 broken refs across 16 files: 34
  relocatable (file moved to a different docs/ subdir; rewrote relative
  path), 12 marketing-cluster files that no longer exist (unlinked — kept
  link text without the `[](url)` syntax), 7 references to
  `forms/PYTHONIC_FORMS_IMPLEMENTATION.md` redirected to the canonical
  `docs/website/guides/forms.md`. Fixer script at
  `/tmp/scratch/fix_stale_md_refs.py` (one-shot; not committed). After fix:
  0 stale refs remaining.

## [0.8.2rc1] - 2026-04-25

### Added

- **`{% theme_css_link %}` cache-busting helper tag (v0.8.2 drain — Group T, closes #1012)** —
  Chrome's `Vary: Cookie` handling is unreliable for per-cookie dynamic CSS;
  after a pack switch the browser often serves the prior pack's stylesheet
  from its own HTTP cache and the page renders with stale palette. The new
  `{% theme_css_link %}` tag in `djust.theming.templatetags.theme_tags`
  emits `<link href="/_theming/theme.css?p=<pack>&m=<mode>&r=<preset>">`
  with cache-busting query params derived from the same `ThemeManager.get_state()`
  the view itself reads. Different pack/mode = different URL = guaranteed
  fresh fetch. Usage: `<link rel="stylesheet" href="{% theme_css_link %}">`.

- **`prose.css` for `@tailwindcss/typography` ↔ pack bridge (v0.8.2 drain — Group T, closes #1009)** —
  new `djust_theming/static/djust_theming/css/prose.css` ships pack-aware
  overrides for the typography plugin's `--tw-prose-*` variables. Opt in
  by adding `prose-djust` alongside `prose` on your `<article>`. Reads
  `--color-brand-*` tokens the active pack emits, so flipping packs at
  runtime updates prose without a stylesheet swap. Includes both light-mode
  and dark-mode invert variables. Pulled from docs.djust.org's reference
  implementation. ~95 lines.

- **`enable_client_override` flag for `LIVEVIEW_CONFIG['theme']` (v0.8.2 drain — Group T, closes #1013)** —
  `ThemeManager.get_state()` reads `djust_theme_pack` / `djust_theme_preset`
  cookies with priority over config defaults. Default behavior unchanged
  (back-compat `True`). Sites without a user-facing theme switcher can set
  `LIVEVIEW_CONFIG['theme']['enable_client_override']: False` to ignore
  cookie reads — prevents cross-project bleed on localhost where multiple
  djust apps share a cookie jar.

### Fixed

- **`.card` / `.alert` overflow:hidden for clean rounded corners (v0.8.2 drain — Group T, closes #1011)** —
  `djust_theming/static/djust_theming/css/components.css` `.card` and
  `.alert` selectors now set `overflow: hidden`. Without this, child
  borders (e.g. `.card-header { border-bottom: ... }`) cross the
  parent's rounded arc and produce a visible 1-2 px notch at the
  corners. Affects every theme pack.

- **`mount_batch` fallback for old-server compat (v0.8.1 reconcile drain — Group F, closes #1031)** —
  the `mount_batch` WebSocket frame was added in v0.6.0 (PR #970) for lazy-
  hydration efficiency. A v0.6.0+ client talking to a pre-v0.6.0 server
  previously got a generic `"Unknown message type: mount_batch"` error and
  the lazy-hydrated views never mounted. Now the client tracks the
  in-flight batch in `lazyHydrationManager.inFlightBatch`; if the
  websocket error handler sees `mount_batch` or `Unknown message type`
  in the error string, it invokes `handleMountBatchFallback()` which
  iterates the stashed mounts and falls back to per-view mount calls.
  Idempotent (clears `inFlightBatch` before iterating) so a late-arriving
  successful response can't double-trigger. 7 new JSDOM tests under
  `tests/js/mount-batch-fallback.test.js`.

### Security

- **Drop exception text from JSON-parse error responses (v0.8.1 reconcile drain — Group B, closes #1026)** —
  `python/djust/api/dispatch.py` (two sites at the API event-dispatch and
  server-function paths) was returning `f"Malformed JSON body: {exc}"` — a
  small but real stack-trace-style leak that could surface parser internals
  (offsets, snippets of the malformed input) to the client. Aligned to
  match `observability/views.py:401`'s existing pattern: log the exception
  server-side via `logger.exception(...)`, return a generic
  `"Malformed JSON body — see server logs"` message. The `invalid_json`
  error code is unchanged, so callers that branch on `error` keep working.

### Changed

- **WebSocket cache-write failures now log under `djustDebug` (v0.8.1 reconcile drain — Group B, closes #1030)** —
  `python/djust/static/djust/src/03-websocket.js:386` previously swallowed
  cache-put exceptions with a bare `catch (_e) {}`. Now logs the failure
  via `if (globalThis.djustDebug) console.log(...)` so developers can
  diagnose cache-write misses without polluting production console output.

- **Test infrastructure cleanup (v0.8.1 reconcile drain — Group A, closes #1027, #1028, #1034, #1036)** —
  four small test-quality refactors bundled in one PR:
  - **#1036**: `_assert_benchmark_under` and the per-segment budget constants
    (`TARGET_PER_EVENT_S`, `TARGET_LIST_UPDATE_S`, `TARGET_WS_MOUNT_S`) moved
    from `tests/benchmarks/test_request_path.py` into
    `tests/benchmarks/conftest.py` for shared scope across benchmark files.
  - **#1034**: replaced the `TARGET_LIST_UPDATE_S * 20` magic-number budget
    for the WS-mount benchmark with a named `TARGET_WS_MOUNT_S = 0.1`
    constant — rationale lives in the constant name, not the multiplier.
  - **#1028**: extracted the duplicated `_make_user` factory into
    `python/djust/tests/conftest.py` as `make_staff_user(...)`. Two test
    files (`test_admin_widgets_per_page.py`, `test_bulk_progress.py`) now
    import the shared factory.
  - **#1027**: replaced the `inspect.getsource`-based regression test in
    `test_stack_trace_exposure.py` with a behavior-level test that triggers
    a serialize-error via a sentinel-laden `RuntimeError` and asserts
    neither the sentinel nor the exception class name reach the response
    body. Defends against regressions even if the leak vector moves.

### Added

- **`make roadmap-lint` — mechanical ROADMAP-vs-codebase drift check (Action #142, closes #1057)** —
  `scripts/roadmap-lint.py` parses the "Not started" entries in `ROADMAP.md`,
  extracts grep-able tokens from each feature name, and reports entries
  whose tokens have zero hits in code paths (`python/`, `crates/`,
  `static/`, `scripts/`, `tests/`, `Makefile`). Pure mechanical check —
  for semantic auditing (LLM reads each entry, decides if the cited
  feature actually ships) use the `pipeline-roadmap-audit` skill instead.
  Exit code 0 unless drift exceeds threshold (25 suspect entries).
  Run via `make roadmap-lint` or `make roadmap-lint VERBOSE=1`.

- **Pre-push hook for `# noqa: F822` in `__all__` patterns (Action #146, closes #1061)** —
  `scripts/check-noqa-f822.sh` flags new `noqa: F822` annotations
  introduced in `python/**/*.py` or `tests/**/*.py` since the last push.
  Ruff silences `py/undefined-export` with `noqa: F822`, but CodeQL flags
  it as a security alert later — the canonical fix is a
  `TYPE_CHECKING`-conditional import (PR #924 pattern). Hook fires only
  on changed files (incremental); pass `--all` to scan the whole tree
  manually.

## [0.8.0rc1] - 2026-04-25

### Added

- **`@action` decorator — React 19 Server Actions equivalent (v0.8.0)** —
  mark a method as a Server Action and `_action_state[<method_name>]`
  is auto-populated with `{pending, error, result}` at handler
  entry/exit. Templates access the state via context injection: each
  action's name becomes a context variable. Pairs with the v0.8.0
  `dj-form-pending` attribute (PR #1023): `dj-form-pending` covers
  the in-flight client UX (during the network round-trip), `@action`
  covers the post-completion server state (after the handler
  returns). Together: React 19-level form ergonomics with zero
  per-handler wiring.

  ```python
  from djust import action

  class TodoView(LiveView):
      @action
      def create_todo(self, title: str = "", **kwargs):
          if not title:
              raise ValueError("Title is required")
          todo = Todo.objects.create(title=title, user=self.request.user)
          return {"created": todo.id}
  ```

  ```html
  {% if create_todo.error %}
      <div class="error">{{ create_todo.error }}</div>
  {% elif create_todo.result %}
      <div class="success">Todo {{ create_todo.result.created }} created!</div>
  {% endif %}
  ```

  Implementation:
  - New `@action` decorator in `djust.decorators`. Wraps the
    underlying `@event_handler` (every action is also an event
    handler — same dispatch path, parameter coercion, permissions,
    rate limits) and adds the action-state tracking layer.
  - On entry: `self._action_state[name] = {pending: True, error: None, result: None}`.
  - On success return: `{pending: False, error: None, result: <return_value>}`.
  - On exception: `{pending: False, error: str(exc) or exc.__class__.__name__, result: None}` and re-raises.
  - `LiveView.__init__` initializes `_action_state: Dict[str, Dict] = {}`.
  - `ContextMixin.get_context_data()` injects each action's state
    under its name (after the public-attribute walk + JIT
    serialization, so action names that collide with user-defined
    attrs win — actions are always the canonical reading of that
    name).
  - Re-running an action resets state (clears previous result on a
    failure retry, clears previous error on a success retry — the
    template never sees stale state alongside fresh state).
  - Both bare-form `@action` and called-form `@action(description=...)` supported.
  - New `is_action(func)` helper for runtime detection.
  - Exposed as top-level imports: `from djust import action, is_action`.

  Covered by **18 regression tests** in `tests/test_action_decorator.py`
  (decorator metadata + event-handler/action distinction, sync
  success / exception / re-raise / class-name fallback, multiple
  actions independent state, retry success-after-failure +
  failure-after-success, both decorator forms, end-to-end context
  injection via `ContextMixin.get_context_data()`).
- **`dj-form-pending` attribute — React 19 `useFormStatus` equivalent
  (v0.8.0)** — any element nested inside a `<form dj-submit>` can
  declare `dj-form-pending="hide|show|disabled"` and react
  automatically when the ancestor form's submit handler is in-flight.
  No prop drilling, no per-button wiring, no client-side state. The
  form itself gets a `data-djust-form-pending="true"` attribute while
  pending so CSS selectors (`form[data-djust-form-pending] .spinner`)
  can hook in without JS. Modes:

  - **`hide`** — element is hidden via the `hidden` attribute while
    pending (idle label that disappears during submit)
  - **`show`** — element is hidden by default and visible while
    pending (loading spinner / "Saving…" text)
  - **`disabled`** — `disabled = true` while pending; original
    disabled state restored on resolve. User-disabled elements stay
    disabled (the helper tracks pre-pending state in
    `data-djust-form-pending-was-disabled`).

  State is set BEFORE the network round-trip and cleared in a
  `finally` block so it always resolves regardless of error. Scope
  isolation: only `[dj-form-pending]` descendants of the actually-
  submitting form react; sibling `<form dj-submit>` forms on the
  same page are unaffected. Unknown modes are silently ignored
  (forward-compatible). Implemented in `09-event-binding.js` —
  `_setFormPending(form, pending)` helper + 1-line wiring into
  `_handleDjSubmit`. Bundle delta: ~80 B gzipped. Covered by **8 JS
  regression tests** in `tests/js/dj-form-pending.test.js`
  (data-djust-form-pending toggle, hide/show/disabled modes,
  user-disabled preservation, plain-form no-op, scope isolation,
  error-path cleanup, unknown-mode forward-compat).

## [0.7.4rc1] - 2026-04-25

### Documentation

- **Check-authoring guide + PR review checklist additions (v0.7.4,
  closes #1017, #1018, #1019, #1020)** — four retro follow-ups from
  v0.7.2 + v0.7.3 milestones bundled into a single docs PR. New file
  `docs/development/check-authoring.md` documents two reusable
  patterns surfaced during the v0.7.x check-refinement work:
  - **Whitespace-preserving redaction for line-number-aware regex
    scanners** (canonical: `_strip_verbatim_blocks` from PR #1014).
    Reusable for any future check that scans template source as raw
    text and needs to ignore a region (`{% verbatim %}`,
    `{% comment %}`, `<script>`, fenced markdown blocks). Replace
    body with whitespace, preserve newlines for line-number accuracy.
  - **Config-driven check scope helper extraction** (canonical:
    `_contrast_check_scope` / `_presets_to_check` from PR #1015).
    When a check's behavior depends on a user-configurable scope,
    extract the decision into a named helper so the four-branch
    test seam (default / opt-in-all / missing-scope-target /
    unknown-value) is testable without dragging in the full Django
    settings stack. Documents the safe-default contract: unknown
    config values fall back to the signal-preserving option.

  PR review checklist (`docs/PULL_REQUEST_CHECKLIST.md`) gains two
  new bullets:
  - **Misleading existing tests are part of the bug** — when fixing
    a check, audit existing tests for fixtures that exemplify the
    broken behavior; update them, don't just add new tests
    alongside. *Source: PR #1008 (issue #1003).*
  - **Framework-internal attrs filter sync** — new framework-set
    attrs on `LiveView` / `LiveComponent` must be added to
    `_FRAMEWORK_INTERNAL_ATTRS` to prevent leakage into
    `get_state()`. *Source: ADR-012 / issue #962 / PR #1002.*

### Fixed

- **py3.14 timing-sensitive CI flake class (v0.7.4, #1016)** —
  two tests intermittently failed on the py3.14 CI runner only:
  `python/tests/test_hotreload.py::TestHotReloadMessage::test_hotreload_slow_patch_warning`
  (PR #1001 caught it once; passed on rerun) and
  `python/tests/test_realtime_multiuser.py::TestPerformanceBaseline::test_broadcast_latency_scales[10]`
  (PR #990 caught it once; passed on rerun). py3.12/3.13 passed both
  attempts in both cases. Two distinct fixes, one PR:

  - **`test_hotreload_slow_patch_warning`**: the original mock used a
    fixed 6-element `times` array indexed by `time.time()` call
    count. py3.14 introduced extra `time.time()` calls inside the
    asyncio scheduler path (some `loop.time()` chains delegate
    down), so the call count drifted past the array on py3.14 only,
    leaving every subsequent call returning the last array value
    (0.15) — which kept the elapsed delta at 0 and prevented the
    slow-patch warning from firing. Replaced with a phase-based
    scheme: first two calls return 0.0 (start + render-start), every
    subsequent call returns 0.15 (render-end / total-end). The
    slow-patch threshold (>100 ms) is crossed deterministically
    regardless of how many extra `time.time()` calls the scheduler
    injects.
  - **`test_broadcast_latency_scales`**: the dispatch-overhead-only
    budget was 10 ms. Bumped to 30 ms to absorb py3.14 runner
    contention variance while still catching genuine regressions
    (the linear-scaling check in
    `test_presence_list_scales_linearly` still catches algorithmic
    O(n) regressions; this test only covers constant-time dispatch
    overhead). Observed 12× over-budget on py3.14 in PR #990 CI;
    cleanly under 30 ms on every other recorded run.

  No new dependencies; both fixes are pure test-code changes.

## [0.7.3rc1] - 2026-04-25

### Changed

- **`djust_theming.W001` contrast-checks the active preset only by
  default (v0.7.3, #1005)** — `check_preset_contrast` previously
  iterated `get_registry().list_presets().items()` and ran WCAG AA
  contrast checks on every registered preset × mode × token pair.
  With djust's 65+ built-in presets, that produced hundreds of
  warnings on every `manage.py check` / pod start (in one observed
  project: 491 issues → ~480 W001 noise + ~11 real). The S/N ratio
  was bad enough that the warnings got ignored, which is the
  opposite of what you want from a check. Fix: new
  `_contrast_check_scope()` helper reads `DJUST_THEMING.contrast_check_scope`
  (default: `"active"`) and the active scope iterates only the
  preset configured in `LIVEVIEW_CONFIG.theme.preset` — same setting
  `check_preset_valid` reads. Theme-pack authors who want the full
  exhaustive sweep opt in via:

  ```python
  DJUST_THEMING = {"contrast_check_scope": "all"}
  ```

  Unknown values fall back to `"active"` (signal-preserving). When
  the configured preset is missing from the registry, the check
  yields zero warnings — `check_preset_valid` already fires E002 for
  that misconfiguration, so we don't double-warn. **Behavior change
  for existing users**: dropping into the `"active"` default
  silences hundreds of warnings about presets the project never
  uses; real W001 hits on the active preset still surface as before.
  Covered by **4 new regression tests** (active-only default, opt-in
  all-scope, missing-active-preset edge case, unknown-scope-value
  fallback) plus **6 existing tests** updated to opt into the
  exhaustive scope (they exercise the loop body, not the scope
  selector).

### Fixed

- **`djust.A070` no longer false-positives on `{% verbatim %}`-wrapped
  `dj_activity` examples (v0.7.3, #1004)** — the A070 / A071 scanner
  walks template source as raw text. Templates that document the
  `{% dj_activity %}` tag — common pattern on docs / marketing pages
  that include literal example markup wrapped in `{% verbatim %}` so
  Django renders the example as-is — got flagged as real
  uninstrumented activity calls. Fix: new
  `_strip_verbatim_blocks(content)` helper redacts the BODY of every
  `{% verbatim %}...{% endverbatim %}` region (both unnamed and
  Django's named-form `{% verbatim foo %}...{% endverbatim foo %}`)
  before the regex scan. Newlines inside the region are preserved so
  line numbers from `match.start()` stay accurate for matches OUTSIDE
  the region. The scanner's existing iteration over
  `_DJ_ACTIVITY_TAG_RE` runs against the redacted source. Real
  uninstrumented `{% dj_activity %}` calls outside any verbatim block
  continue to fire A070 unchanged. Covered by **12 regression tests**
  in `python/tests/test_a070_verbatim_fp_1004.py` (7 helper-contract
  tests + 5 scanner-integration tests including the canonical docs
  case, mixed verbatim + real calls, named verbatim form, and line
  number preservation).
- **`djust.C011` now catches stale/placeholder `output.css`, not
  just totally-missing files (v0.7.3, #1003)** — `_check_missing_compiled_css`
  in `python/djust/checks.py` previously tested only
  `os.path.exists()`. A committed-but-stale `output.css` (e.g. a
  placeholder `/* Run tailwindcss ... */`) silently passed the
  check, the site rendered without any Tailwind utilities, and
  `manage.py check` emitted no warning. Reported by the
  docs.djust.org team after hitting it at launch — fresh-clone +
  `make dev` produced a broken page with zero warnings. Fix: new
  helper `_output_css_looks_built(path)` extends the contract to
  "the file exists AND looks built" — checks size > 10 KB AND a
  marker (`tailwindcss` banner OR `@layer` directive) in the first
  512 bytes. The existing `os.path.exists()` branch is replaced
  with the helper. Both checks must pass; a 50 KB hand-rolled
  stylesheet without Tailwind markers is correctly flagged. Warning
  message updated from "output.css not found" to "output.css is
  missing or stale" with a hint that placeholder files are the
  canonical failure mode. Covered by **5 new regression tests**
  (placeholder `/* Run tailwindcss... */`, empty 0-byte file,
  sub-10 KB file with banner, real built `>10 KB` Tailwind output,
  hand-rolled `@layer` stylesheet) plus **3 existing tests**
  updated to use realistic Tailwind output (~16 KB minified-style
  fixture instead of the 18-byte placeholder that exposed the
  original bug).

## [0.7.2rc1] - 2026-04-24

### Added

- **Inline radio buttons via `data-dj-inline` attribute (v0.7.2,
  #991)** — opt-in horizontal layout for `forms.RadioSelect` fields
  without writing any new Python. Users add
  `widget=forms.RadioSelect(attrs={"data-dj-inline": "true"})` to a
  `ChoiceField` and load `{% static 'djust/djust-forms.css' %}` once
  in their base template; the bundled stylesheet uses the CSS
  `:has()` parent selector (Selectors Level 4 — Chromium 105+,
  Safari 15.4+, Firefox 121+, all stable since 2023) to walk up from
  each marked `<input type="radio">` and lay out its containing
  wrapper as `inline-flex` with sensible spacing, full keyboard
  navigation, and the browser's native focus ring preserved.
  Composes with anything that renders a Django `RadioSelect`
  (plain `forms.Form`, `LiveViewForm`, ModelForms, Django admin,
  djust-theming form templates) — the same `[data-dj-inline]`
  selector targets both the stock `<ul><li>` markup and
  djust-theming's `<div>`-wrapped variant. Skip-able: don't link the
  CSS file → the attribute is inert. Override-able: write your own
  CSS rule keyed on `[data-dj-inline]` for any visual treatment
  (segmented controls, CSS Grid columns, etc.). New file:
  `python/djust/static/djust/djust-forms.css`. Documented in a new
  "Inline Radio Buttons" section of `docs/website/guides/forms.md`
  with the API, the why-data-attribute reasoning, and examples for
  customizing the visual treatment + multi-field forms. Covered by
  **12 regression tests** in `tests/test_inline_radios_991.py` (3
  Django-render contract tests + 5 CSS-ships-and-targets-correctly
  tests + 2 backwards-compat tests + 2 edge cases).

### Decisions

- **ADR-012: `_FRAMEWORK_INTERNAL_ATTRS` filter is the right tool;
  do NOT rename framework-internal attrs (v0.7.2, #962, close-without-code)** —
  v0.5.7 #762 added a `_FRAMEWORK_INTERNAL_ATTRS` frozenset in
  `python/djust/live_view.py` to prevent ~25 framework-set attrs
  (`sync_safe`, `login_required`, `template_name`, ...) from leaking
  into `get_state()` / reactive-state debug payloads. The v0.5.7
  retro filed #962 to decide whether to additionally *rename* those
  attrs to `_*`-prefixed form as defense-in-depth. Decision after a
  full review: keep the filter, don't rename. Rename would break
  every user view reading `self.login_required` /
  `self.template_name` (both first-class documented attrs; the
  latter is Django public API) without net defense-in-depth benefit
  — the filter is a single centralized gate at the exact leakage
  point. Mitigation for the filter's maintenance burden: the PR
  review checklist will remind authors to add new framework-set
  attrs to the frozenset at introduction time.
  See `docs/adr/012-framework-internal-attrs-filter-vs-rename.md`.

### Infrastructure

- **Weekly real-cloud CI matrix for upload writers (v0.7.2, #963)** —
  all v0.5.7 upload-writer tests mock the SDKs. Happy-path end-to-end
  verification against real AWS S3 / Google Cloud Storage / Azure
  Blob was missing; silent regressions in credential handling, SDK
  auth chain changes, or bucket permissions could reach production
  without detection. New workflow
  `.github/workflows/weekly-cloud-uploads.yml` runs every Monday at
  06:00 UTC (plus manual `workflow_dispatch`) against all three
  providers in parallel (fail-fast: false — each provider's outage
  is independent). Each matrix slot uploads a 1 MB blob, HEADs it,
  GETs it, and DELETEs it. Failure opens a `tech-debt` + new
  `cloud-integration` label issue via `actions/github-script@v7`
  with a diagnostic link to the run. Credentials come from GitHub
  encrypted secrets (`CLOUD_INT_AWS_*`, `CLOUD_INT_GCP_*`,
  `CLOUD_INT_AZURE_*`) so contributors' PRs never have access. The
  three provider-specific integration tests live under
  `tests/cloud_integration/` and **auto-skip** when
  `DJUST_CLOUD_INTEGRATION` isn't set — running the full test suite
  locally or in PR CI costs nothing. Cost: a few cents per provider
  per weekly run.

### Documentation

- **`key_template` UUID-prefix convention for `s3_events` (v0.7.2,
  #964)** — `djust.contrib.uploads.s3_events.parse_s3_event` extracts
  `upload_id` by finding the first UUID-shaped path segment in the
  S3 object key; apps whose `key_template` doesn't produce such a
  segment silently fall back to the full key as `upload_id`, and
  hooks registered against the UUID then don't fire. This was the
  #1 source of "my hook isn't being called" reports from v0.5.7+
  users. Fix: (a) the module docstring now documents the convention
  prominently with two recommended `key_template` shapes
  (`uploads/<uuid>/<filename>` and `<tenant>/<uuid>/<filename>`);
  (b) a `DEBUG` log entry fires on the
  `djust.contrib.uploads.s3_events` logger whenever fallback
  happens, naming the offending key — so enabling `DEBUG` logging
  once is enough to diagnose a silent hook; (c) a "Key-template
  convention for `s3_events`" section has been added to
  `docs/website/guides/uploads.md` with a debugging recipe and a
  pointer to the "custom upload-id routing" escape hatch (via
  `x-amz-meta-upload-id` / JWT / DB lookup). Covered by **3 new
  regression tests** in `tests/test_presigned_s3_820.py` (no-UUID
  fallback + DEBUG log, happy path emits no log, UUID segment
  position doesn't matter).

### Fixed

- **Rust renderer honors `__str__` key on serialized model dicts
  (v0.7.2, #968)** — `djust.serialization._serialize_model_safely`
  sets `"__str__": str(obj)` on every dict it produces so `{{ obj }}`
  in a Rust-engine template can match Django's default `str(obj)`
  semantics. The Rust `Value::Object` Display impl
  (`crates/djust_core/src/lib.rs`) previously ignored the key and
  emitted the literal `"[Object]"` for any dict. This broke FK
  display silently in LiveView templates — `{{ claim.claimant }}`
  (where `claimant` serializes to a nested dict) rendered as
  `[Object]` instead of the claimant's string representation, since
  the page still returned 200 the only way to notice was visual
  inspection. Reported by a downstream consumer
  prototype team who hit six occurrences in a single project. Fix:
  when the value is `Value::Object` and contains a
  `"__str__": Value::String(...)` entry, render the string. Non-model
  dicts (no `__str__`, or `__str__` not a string) keep the existing
  `"[Object]"` fallback. Plain Python objects with custom `__str__`
  were already correct (handled by `FromPyObject`). Covered by **5
  Rust unit tests** in `crates/djust_core/src/lib.rs::tests` and **13
  Python integration tests** in `tests/test_rust_renderer_str_key.py`
  (model dict, nested FK, HTML-auto-escape, dotted-access, plain-dict
  fallback, null/int `__str__` edge cases, empty-string `__str__`,
  backwards-compat for plain Python objects + lists + scalars).
- **`djust.dev_server` NameError on module load when `watchdog` is
  not installed (v0.7.2, #994)** — the `try/except ImportError` block
  at `dev_server.py:13-19` sets `WATCHDOG_AVAILABLE = False` but the
  class statement `class DjustFileChangeHandler(FileSystemEventHandler)`
  on line 25 referenced the symbol unconditionally. When watchdog is
  absent, class definition time crashes with `NameError: name
  'FileSystemEventHandler' is not defined`, which in turn breaks
  `python manage.py check` in any djust install without the `[dev]`
  extra (because `djust.checks.check_hot_view_replacement` imports
  `WATCHDOG_AVAILABLE` from `djust.dev_server`). Latent since at least
  v0.5.4rc1 — the pattern predates the v0.5.x refactor; only surfaces
  when an install omits watchdog. Fix: the `except ImportError` branch
  now defines stub `FileSystemEventHandler`, `FileSystemEvent`, and
  `Observer` classes purely to satisfy the class statements below at
  import time. `HotReloadServer.start()` already short-circuits on
  `WATCHDOG_AVAILABLE = False`, so the stubs are never instantiated in
  a running process. Covered by **3 regression tests** in
  `tests/test_dev_server_watchdog_missing.py` that block watchdog via
  a `sys.meta_path` finder and verify (a) `djust.dev_server` imports
  cleanly, (b) `HotReloadServer.start()` no-ops with the documented
  warning, (c) `djust.checks.check_hot_view_replacement`'s downstream
  import path survives.

## [0.7.1rc1] - 2026-04-24

### Added

- **`FORCE_SCRIPT_NAME` / sub-path mount support for the in-browser HTTP
  API client (v0.7.1, #987, closes Action Tracker #123)** — new template
  tag `{% djust_client_config %}` in `djust.templatetags.live_tags`
  emits `<meta name="djust-api-prefix" content="...">`. The content is
  derived via Django's `reverse()` so it honors both `FORCE_SCRIPT_NAME`
  and any custom `api_patterns(prefix=...)` mount. The djust client
  reads this meta tag once at bootstrap (`00-namespace.js`) and exposes
  two helpers: `window.djust.apiPrefix` (resolved prefix string) and
  `window.djust.apiUrl(path)` (prefix + path joiner with slash
  normalization). `djust.call()` (`48-server-functions.js`) now routes
  through `djust.apiUrl()` — the last remaining hardcoded `/djust/api/`
  reference in the client bundle is gone. Priority: explicit
  `window.djust.apiPrefix` > meta tag > compile-time default
  `/djust/api/`. Integrators mounting djust behind a reverse proxy
  prefix now only need to add `{% load live_tags %}{% djust_client_config %}`
  to their base template `<head>`; no JS patching required. Covered
  by **12 new tests** (5 Python in `test_client_config_tag.py`, 6 JS
  in `api_prefix.test.js`, 1 regression in `server_functions.test.js`
  asserting `djust.call` honors the meta tag under a forced script
  prefix). Bundle size delta: **+148 B gzipped** (50030 → 50178 B).
  Docs: "Sub-path deploys" section added to
  `docs/website/guides/server-functions.md` and
  `docs/website/guides/http-api.md`. Follow-up issue #992 filed for the
  same class of bug in `03b-sse.js:44` (SSE fallback transport, v0.7.2
  target).

## [0.7.0rc1] - 2026-04-24

### Added

- **Streaming Markdown `{% djust_markdown %}` (v0.7.0)** — server-side
  Markdown renderer built on `pulldown-cmark 0.12` with three safety
  guarantees wired in at the crate level: raw HTML in the source is
  escaped (`Options::ENABLE_HTML` is never set; because
  pulldown-cmark 0.12 still emits `Event::Html` / `Event::InlineHtml`
  when that flag is off, `sanitise_event` re-routes those events to
  `Event::Text` so the writer escapes them), `javascript:` /
  `vbscript:` / `data:` URL schemes in link/image destinations are
  rewritten to `#` (case-insensitive, leading-whitespace tolerant),
  and inputs larger than 10 MiB (per-call input cap, not a concurrency
  limiter) are returned as an escaped
  `<pre class="djust-md-toobig">` block without invoking the parser.
  A provisional-line splitter renders a partially-typed trailing line
  as escaped text inside `<p class="djust-md-provisional">`,
  eliminating mid-token flicker for streaming LLM output. Exposed
  three ways: the `{% djust_markdown expr [kwargs] %}` tag (registered
  via the existing Rust tag-handler registry), the Python helper
  `djust.render_markdown(src, **opts)` returning a `SafeString`, and
  the PyO3 function `djust._rust.render_markdown`. Kwargs:
  `provisional`, `tables`, `strikethrough`, `task_lists`. **Note on
  deviation from plan**: `autolinks` was dropped from the public surface
  — pulldown-cmark 0.12 does not expose a `GFM_AUTOLINK` /
  `ENABLE_AUTOLINK` options flag, so plain-text URLs stay as text unless
  wrapped in explicit `[text](url)` syntax. Will be reconsidered when
  the upstream parser is bumped. Covered by **24 Rust tests**
  (`crates/djust_templates/src/markdown.rs`, including regression cases
  for `vbscript:`, `data:`, mixed-case `JavaScript:`, leading-whitespace
  URLs, `<iframe>` escaping, image-src neutralisation, and the 10 MiB
  cap) and **14 Python tests** (`python/djust/tests/test_markdown.py` +
  `tests/unit/test_markdown_tag.py`), plus **3 A090 system-check tests** —
  41 total (24 Rust + 14 Python/tag + 3 A090). Demo at
  `/demos/markdown-stream/`; full write-up in
  [docs/website/guides/streaming-markdown.md](docs/website/guides/streaming-markdown.md).
- **Admin widgets & bulk-action progress (v0.7.0)** — two additions to
  `djust.admin_ext` close the most-requested gaps in the alternative
  reactive admin:
  - `DjustModelAdmin.change_form_widgets` / `change_list_widgets` class
    attributes accept any list of `LiveView` subclasses; each is
    embedded via `{% live_render %}` on the matching admin page.
    Permission filtering honours `permission_required` on the widget
    class. See
    [docs/website/guides/admin-widgets.md](docs/website/guides/admin-widgets.md).
  - `@admin_action_with_progress` (in `djust.admin_ext.progress`) turns
    any `DjustModelAdmin` action into a background daemon thread and
    redirects the user to a `BulkActionProgressWidget` page at
    `<admin>/djust-progress/<job_id>/`. The page polls the job every
    500 ms, re-renders the progress bar / message / log, and wires a
    Cancel button that atomically flips `done` and `cancelled`. Queryset
    is eagerly pinned to PKs before the thread starts (no lazy-eval
    foot-guns). **Cancellation is cooperative** — clicking Cancel flips
    `progress.cancelled = True`; the action body must periodically check
    `if progress.cancelled: return` to actually stop (Python cannot
    safely interrupt a running thread mid-statement).
  - **Server-side permission enforcement** — `@admin_action_with_progress(permissions=[...])`
    stamps `allowed_permissions` on the wrapped action; `ModelListView.run_action`
    now calls `request.user.has_perms(allowed)` before dispatching the
    action and raises `PermissionDenied` if the user lacks any declared
    perm. Closes the gap where `has_*_permission` returns True for any
    staff user.
  - **Bounded server state:** `_JOBS` is LRU-capped at `_MAX_JOBS = 500`
    (oldest entries evicted on insert once the cap is reached), and
    `Job.message` / `Job.error` are individually truncated to
    `_MAX_MESSAGE_CHARS = 4096` on each `progress.update(...)` call.
    `Job.error` is a generic user-facing string ("Action failed — see
    server logs for details"); the raw exception text lives only on the
    server-side `Job._error_raw` attribute and is always logged at ERROR
    level via `logger.exception` (logger name `djust.admin_ext.progress`).
  - **New setting:** `DJUST_ASGI_WORKERS` (default `1`) — declares the
    number of ASGI workers in the deployment. Gates the A073 system
    check (fires only when `DJUST_ASGI_WORKERS > 1`) so single-worker
    development stays silent.
  - **Defense-in-depth allowlist:** `DJUST_LIVE_RENDER_ALLOWED_MODULES`
    (optional) restricts the dotted-path module prefixes that
    `{% live_render %}` will resolve — any widget slot path outside the
    allowlist raises `TemplateSyntaxError` at render time.
  - Two new system checks: `djust.A072` (warning) fires if a non-
    `LiveView` class is registered in a widget slot; `djust.A073`
    (info, gated on `DJUST_ASGI_WORKERS > 1`) fires at startup if any
    admin site hosts a `@admin_action_with_progress`-decorated action,
    noting the v0.7.0 single-worker `_JOBS` limitation and pointing at
    the v0.7.1 channel-layer follow-up.
  - 25 new tests: `python/djust/tests/test_bulk_progress.py` (12) +
    `python/djust/tests/test_admin_widgets_per_page.py` (13); +A072/A073
    check tests in `python/tests/test_checks.py`.
- **`{% dj_activity %}` + `ActivityMixin` (v0.7.0)** — React 19.2
  `<Activity>` parity: pre-rendered hidden regions of a LiveView that
  preserve their local DOM state (form inputs, scroll, transient JS)
  across show/hide cycles. The new block tag
  ``{% dj_activity "name" visible=expr eager=expr %}...{% enddj_activity %}``
  emits a wrapper ``<div>`` carrying ``data-djust-activity``,
  ``data-djust-visible``, and — when not visible — the HTML ``hidden``
  attribute plus ``aria-hidden="true"``. The body is rendered
  unconditionally in every pass so local state isn't lost. ``ActivityMixin``
  (composed into ``LiveView`` AFTER ``StickyChildRegistry``, BEFORE
  ``View``) provides the server-side API: ``set_activity_visible(name,
  visible)``, ``is_activity_visible(name)``, declarative
  ``eager_activities: frozenset`` class attr, and an internal FIFO
  deferred-event queue (cap 100, overridable via
  ``activity_event_queue_cap``) drained by the WebSocket consumer after
  every ``handle_event`` / ``handle_info`` dispatch. Client runtime
  (``python/djust/static/djust/src/49-activity.js``) exposes
  ``window.djust.activityVisible(name)`` and dispatches a bubbling
  ``djust:activity-shown`` CustomEvent when a panel flips hidden →
  visible. The event-dispatch gate in ``11-event-handler.js`` drops
  events whose trigger sits inside a hidden non-eager activity
  client-side (stamping ``_activity`` on all other events for server-side
  deferral). The VDOM patcher in ``12-vdom-patch.js`` skips subtree
  patches targeting nodes inside a hidden non-eager activity so DOM
  state is preserved. Two new system checks: ``A070`` (Warning —
  missing ``name`` argument) and ``A071`` (Error — duplicate activity
  name within one template). See ``docs/website/guides/activity.md``
  for the full guide + ``{% if %}`` / ``{% live_render %}`` / sticky /
  ``dj-prefetch`` comparison matrix. Demo at
  ``examples/demo_project/djust_demos/views/activity_demo.py``.
- **Intent-Based Prefetch (`dj-prefetch`, v0.7.0)** — hover- and
  touch-driven navigation prefetch that complements the existing
  service-worker-mediated hover prefetch. Links opting in with
  ``<a dj-prefetch href="...">`` are prefetched after a 65 ms hover
  debounce (cancelled on ``mouseleave`` before the debounce fires) and
  immediately on ``touchstart`` — mobile users commit to a tap fast, so
  no debounce is applied there. Prefetch uses ``<link rel="prefetch"
  as="document">`` injection so the browser manages the cache lifecycle
  (falls back to low-priority ``fetch`` + ``AbortController`` when
  ``relList`` doesn't advertise ``'prefetch'``). Same-origin only;
  ``javascript:`` / ``data:`` URLs blocked; dedup'd per URL via a Set
  that ``window.djust._prefetch.clear()`` wipes on SPA navigation. Opt
  out per-link with ``dj-prefetch="false"``. Respects
  ``navigator.connection.saveData``. New client surface:
  ``window.djust._intentPrefetch`` for test/diagnostic access. Scope:
  client-side only — no new server endpoint. Contract: ``dj-prefetch``
  is intended for author-controlled navigation links only; don't put it
  on links that perform state-changing GETs (see the module header in
  ``python/djust/static/djust/src/22-prefetch.js`` for the full safety
  contract). See ``docs/website/guides/prefetch.md`` for the guide and
  the SW-hover-vs-intent comparison table.
- **Server Functions (`@server_function` / `djust.call()`, v0.7.0)** —
  same-origin browser RPC without VDOM re-render. Decorate a LiveView
  method with ``@server_function`` and invoke it from JavaScript as
  ``await djust.call('<view_slug>', '<fn>', {params})``; the return value
  is JSON-serialized straight back to the caller. The three primitives
  now split cleanly by intent:
    - ``@event_handler`` — WebSocket, triggers a VDOM re-render (UI
      interactions: click, submit, input).
    - ``@event_handler(expose_api=True)`` — HTTP (ADR-008), triggers a
      re-render AND exposes the handler to mobile / S2S / AI-agent
      callers via OpenAPI.
    - ``@server_function`` — HTTP, **no re-render**, no OpenAPI, no
      ``api_response`` / ``serialize=`` hooks. Designed exclusively for
      in-browser RPC; response envelope is the minimal
      ``{"result": <value>}``.
  Session-cookie auth + CSRF are both required unconditionally — no
  auth-class opt-out. Request body shape is strict: only an empty body,
  ``{}``, or ``{"params": {...}}`` are accepted; any other shape
  (flat objects, wrapped objects with sibling keys) returns
  ``400 invalid_body``. This deliberately removes the ambiguity where a
  caller's own field named ``params`` would be silently unwrapped and
  every sibling key dropped. The dispatcher reuses the ADR-008 pipeline
  unchanged: parameter coercion via ``validate_handler_params``,
  ``@permission_required`` gating via ``check_handler_permission``, and
  ``@rate_limit`` via the same LRU-capped ``_rate_buckets`` OrderedDict.
  Both sync and ``async def`` functions are supported via
  ``_call_possibly_async``. Stacking ``@event_handler`` and
  ``@server_function`` on the same method raises ``TypeError`` at
  decoration time — a function either re-renders the view or returns an
  RPC result, never both. New URL: ``POST /djust/api/call/<view_slug>/
  <function_name>/``, declared BEFORE the catch-all dispatch pattern so
  it can't be shadowed. New public surface:
  ``djust.decorators.server_function``, ``is_server_function``,
  ``djust.api.DjustServerFunctionView``, ``dispatch_server_function``
  (in ``python/djust/api/dispatch.py``), ``iter_server_functions``. New
  client module ``python/djust/static/djust/src/48-server-functions.js``
  (~40 LOC, ~430 B gzipped delta). Demo:
  ``examples/demo_project/djust_demos/`` adds a product-search view
  demonstrating both features end-to-end. See
  ``docs/website/guides/server-functions.md`` for the full API reference,
  error-code table, and comparison vs. ``@event_handler`` and
  ``@event_handler(expose_api=True)``.

## [0.6.1rc1] - 2026-04-24

### Added

- **Time-Travel Debugging (v0.6.1)** — dev-only debug-panel tab that
  records a state snapshot around every `@event_handler` dispatch
  (`state_before` / `state_after`), then lets developers scrub back
  through the timeline and jump to any past state. The server
  restores the snapshot via `safe_setattr` and re-renders through the
  normal VDOM patch pipeline. Opt-in per view
  (`time_travel_enabled = True` on the `LiveView` subclass); zero
  cost when disabled. Gated on `DEBUG=True` at the WebSocket consumer
  so production clients can't coerce a jump even if the class attr
  is left on. Per-view bounded ring buffer (default 100 events,
  configurable via `LIVEVIEW_CONFIG["time_travel_max_events"]`).
  New module `djust.time_travel` (`EventSnapshot`, `TimeTravelBuffer`,
  `record_event_start`, `record_event_end`, `restore_snapshot`). New
  inbound WS frame `time_travel_jump` + outbound `time_travel_state`
  ack, plus `time_travel_event` frames pushed after every recorded
  snapshot so the debug panel timeline populates incrementally
  (client CustomEvent `djust:time-travel-event`). Instrumentation wraps
  all three dispatch branches (actor, component, view handler) and
  records permission-denied / validation-failed events with an
  `error` marker. Component events record against the parent view
  in Phase 1 (full component-level time travel is a v0.6.2 follow-up).
  Ghost-attr cleanup in `restore_snapshot` removes public attributes
  not present in the target snapshot, so restoring `{a:1}` over
  `{a:5, b:10}` leaves `{a:1}` rather than `{a:1, b:10}`. New client
  events `djust:time-travel-state` and `djust:time-travel-event`
  (CustomEvents). New system checks `djust.C501` (info — global switch
  on) and `djust.C502` (error — non-positive `time_travel_max_events`).
  Beyond Redux DevTools: server-side so no client state store; beyond
  Phoenix LiveView's debug tools which are telemetry-only. See
  `docs/website/guides/time-travel-debugging.md`.
- **Streaming Initial Render (v0.6.1, Phase 1)** — opt-in chunked HTTP
  response for LiveView GET requests. Setting `streaming_render = True`
  on a LiveView class returns a `StreamingHttpResponse` that flushes the
  page in three chunks: shell-open (everything before `<div dj-root>`),
  main content (the `<div dj-root>...</div>` body), and shell-close
  (`</body></html>` + trailing markup). **Phase 1 is transport-layer only**
  — the server fully assembles the rendered HTML before streaming it; the
  benefit is HTTP/1.1 chunked transfer (no `Content-Length`, earlier TCP
  flush, compatibility with chunk-relaying proxies, avoiding gzip-buffer
  stalls). True server-side render overlap (browser parses shell while
  server computes main content) arrives with **Phase 2** (v0.6.2) alongside
  lazy-child streaming via `{% live_render lazy=True %}`. No client-side
  code changes; opt-in per view, backward-compatible default. Response
  emits `X-Djust-Streaming: 1` for observability and omits `Content-Length`.
  See `docs/website/guides/streaming-render.md`.
- **Hot View Replacement (HVR, v0.6.1)** — state-preserving Python code
  reload in development. When a LiveView module changes on disk, the dev
  server `importlib.reload()`s the module and swaps `__class__` in place on
  every live instance of the changed class, then re-renders via the
  existing VDOM diff path. Users keep form input, counter values, active
  tab, and scroll position — React Fast Refresh parity for djust. Gated on
  `DEBUG=True` + `LIVEVIEW_CONFIG["hvr_enabled"]` (default True). Falls
  back to full reload on a conservative state-compat heuristic (removed
  handlers, changed handler signatures, or slot layout drift). New system
  check `djust.C401` warns when HVR is enabled but `watchdog` is not
  installed. New client event `djust:hvr-applied` (CustomEvent). Zero cost
  in production.

  See `docs/website/guides/hot-view-replacement.md`.
## [0.6.0rc1] - 2026-04-23

### Documentation

- **CSS `@starting-style` guide section (v0.6.0)** — documents that
  browser-native `@starting-style` works unmodified with djust's VDOM
  insert path. No new djust attributes or JS — the feature is pure CSS.
  Guide section in `docs/website/guides/declarative-ux-attrs.md` includes
  a quick-start example, a side-by-side comparison vs `dj-transition`
  (browser support, runtime cost, per-element customization), interop
  notes with `dj-remove` for enter+exit coverage, and caveats around
  `@supports` gating for older browsers. ROADMAP parity-tracker row
  updated to ✅ Documented v0.6.0.

### Changed

- **Package consolidation sunset — ADR-007 Phase 4 closure (v0.6.0)** — the
  three-phase consolidation that started in v0.5.0 is now complete. The five
  sibling repos (`djust-auth`, `djust-tenants`, `djust-theming`,
  `djust-components`, `djust-admin`) are sunset at `v99.0.0` — each retains a
  shim-only `__init__.py` that re-exports from `djust.<name>` and emits a
  `DeprecationWarning`. Path A was chosen over PyPI publish: existing releases
  remain installable indefinitely for legacy projects; no new PyPI versions
  will ship. djust core now exposes the consolidation via
  `[project.optional-dependencies]` — `djust[auth]`, `djust[tenants]` (with
  `djust[tenants-redis]` and `djust[tenants-postgres]` backend-specific
  sub-extras), `djust[theming]`, `djust[components]`, `djust[admin]`. Two new
  extras (`auth`, `tenants`) added in this release; the others shipped in
  v0.5.0. ADR-007 status updated from "Proposed" → "Accepted + Phase 4
  complete". New migration guide:
  `docs/website/guides/migration-from-standalone-packages.md` (mechanical sed
  script + FAQ + edge cases). Cosmetic tech-debt: sibling repos retain dead
  pre-consolidation source files next to the shim — cleanup tracked
  separately; no user impact.

### Added

- **Request-path profiling harness (v0.6.0, investigative, ROADMAP Group 5
  P2)** — reproducible profile of the mount → event → VDOM diff → patch
  path. New `scripts/profile-request-path.py` (cProfile wrapper, optional
  py-spy hint, writes `artifacts/profile-<timestamp>.{txt,pstats}`; exits
  non-zero on target-miss for CI). New `tests/benchmarks/test_request_path.py`
  with eight pytest-benchmark cases across four groups (HTTP render,
  WebSocket mount, event dispatch, VDOM diff+patch) with hard assertions
  against the 2 ms per-event / 5 ms list-update budgets. New
  `docs/performance/v0.6.0-profile.md` reporting all measured timings
  (mount 0.07 ms, event 4 µs, VDOM diff 4 µs, list reorder 0.38 ms — all
  within targets by at least 5x). New `make profile` target wired to the
  harness (the prior `make profile` runtime-stats target is now
  `make profile-stats`). No optimizations were required; the profile
  confirms the existing Rust-side architecture is well under target.

- **Service Worker advanced features (v0.6.0)** — three SW-backed optimizations
  landed in one PR:
  - **VDOM patch cache**: per-URL HTML snapshots served instantly on popstate,
    then reconciled against the live WebSocket mount reply. Configurable via
    `DJUST_VDOM_CACHE_ENABLED` / `DJUST_VDOM_CACHE_TTL_SECONDS` /
    `DJUST_VDOM_CACHE_MAX_ENTRIES`. New system checks `djust.C301` / `C302` /
    `C303` guard config ranges.
  - **LiveView state snapshots**: opt-in per view via
    `enable_state_snapshot = True` on a `LiveView` subclass. Client captures
    JSON-serializable public state on `djust:before-navigate`; server restores
    via `_restore_snapshot(state)` in lieu of `mount()` when the user hits
    back. Views override `_should_restore_snapshot(request)` to reject stale
    snapshots. System check `djust.C304` warns when a snapshot-opt-in view
    declares attributes matching PII naming patterns.
  - **Mount batching**: when multiple `dj-lazy` LiveViews hydrate together,
    the client sends one `mount_batch` WebSocket frame instead of N separate
    `mount` frames. Server responds with one `mount_batch` carrying all
    rendered views; per-view failures are isolated in a `failed[]` array
    (atomicity relaxed so one bad view doesn't kill the batch). Opt out via
    `window.DJUST_USE_MOUNT_BATCH = false`.
  - New client module `46-state-snapshot.js` (~120 LOC); new senders on
    `djust._sw.cacheVdom/lookupVdom/captureState/lookupState`.
  - `registerServiceWorker({vdomCache: true, stateSnapshot: true})` gates
    the new behaviors alongside existing `instantShell` / `reconnectionBridge`
    options.

  See `docs/website/guides/service-worker.md`.
### Changed

- `LiveViewConsumer.handle_mount()` accepts new `state_snapshot` kwarg;
  dispatches to the snapshot-restore path when the view opts in and the
  payload's `view_slug` matches. New method `handle_mount_batch()` +
  `_mount_one()` collector seam enable the mount-batch path without
  regressing the single-view `mount` flow.

### Security

- State snapshots are JSON-only (no pickle). `safe_setattr` blocks dunder keys
  and private (`_`-prefixed) attributes during restoration. SW enforces a
  256 KB upper bound on `state_json` payloads; client clamps at 64 KB.
  System check `djust.C304` warns when snapshot-opt-in views declare
  attribute names matching `password|token|secret|api_key|pii`.

- **Sticky LiveViews (v0.6.0)** — Phoenix `live_render sticky: true` parity.
  Shipped across three PRs: #966 (Phase A — embedding primitive), #967 (Phase B —
  preservation across `live_redirect`), #969 (Phase C — ADR-011, user guide, demo app).
  Mark a LiveView class with `sticky = True` + `sticky_id` and embed it via
  `{% live_render "myapp.views.AudioPlayerView" sticky=True %}`. Destination
  layouts declare `<div dj-sticky-slot="<id>"></div>` at the re-attachment point;
  the same Python instance, DOM subtree, form values, scroll/focus, and background
  tasks all survive `live_redirect` navigation. Use case: app-shell widgets (audio
  players, sidebars, notification centers), wizard preview panes.

  **User-facing API**
  - `LiveView.sticky: bool = False` + `sticky_id: Optional[str] = None` class attrs.
  - `{% live_render "dotted.path" sticky=True %}` template tag (validates class
    opt-in at render time; `TemplateSyntaxError` on mismatch).
  - `[dj-sticky-slot="<id>"]` slot markers in destination layouts.
  - `djust:sticky-preserved` / `djust:sticky-unmounted` CustomEvents for
    lifecycle hooks (reasons: `server-unmount`, `no-slot`, `auth`).
  - `_on_sticky_unmount()` per-instance hook (default: cancels pending
    `start_async` tasks).

  **Wire protocol**
  - `child_update` (Phase A) — scoped VDOM patches for embedded non-sticky children.
  - `sticky_hold` (server→client, sent BEFORE `mount` on `live_redirect`) —
    enumerates surviving sticky_ids so the client reconciles its stash against
    the authoritative list. Ordering is load-bearing: the mount handler eagerly
    reattaches, so a late `sticky_hold` would reattach auth-revoked views.
  - `sticky_update` (server→client) — per-child VDOM patches scoped to
    `[dj-sticky-view="<id>"]` via a new `applyPatches(patches, rootEl)` variant
    in `12-vdom-patch.js` (when `rootEl` is non-null, node lookups / focus
    save-restore / autofocus queries all scope to that subtree).
  - Per-view VDOM version tracking via `clientVdomVersions: Map<view_id, number>`
    with `"__root"` sentinel for top-level patches.

  **Client-side**
  - `static/djust/src/45-child-view.js` — `stickyStash` Map; `stashStickySubtrees()`
    (detach on outbound nav), `reconcileStickyHold(views)` (drop non-authoritative),
    `reattachStickyAfterMount()` (replace `[dj-sticky-slot]` with stashed subtree
    via `replaceWith()` — DOM identity preserved), `handleStickyUpdate(msg)`
    (scoped patch apply), `clearStash()` (abnormal-close cleanup).
  - `18-navigation.js` calls `stashStickySubtrees()` BEFORE outbound
    `live_redirect_mount` (and before `popstate`-triggered redirects).
  - `03-websocket.js` onclose calls `clearStash()` on abnormal disconnect.
  - `[dj-root]` audit across `40-dj-layout.js`, `24-page-loading.js`, `12-vdom-patch.js`
    autofocus sites adds `:not([dj-sticky-root])` so sticky children don't
    masquerade as layout / page roots.

  **Security**
  - Per-sticky auth re-check via new `djust.auth.check_view_auth_lightweight(view, request) -> bool`;
    a sticky view whose permissions are revoked mid-session is unmounted on the
    next navigation.
  - `DJUST_LIVE_RENDER_ALLOWED_MODULES` prefix-allowlist gates dotted-path resolution
    (unset = permit-all, backward compatible).
  - `sticky_id` HTML-escaped via server-side `escape()` + `CSS.escape` on client-side
    selectors.
  - Client stash bounded by developer-authored content; idempotent `stashStickySubtrees`
    coalesces duplicates; cleared on abnormal WS close.
  - Inbound `sticky_update` / `sticky_hold` frames rejected by the consumer's
    allowlist (server-to-client only).

  **Testing (32 Python + 20 JSDOM + 6 integration)**
  - 11 Phase A tests in `tests/unit/test_live_render_tag.py` (HTML-parsed) +
    21 Phase B/C tests in `tests/unit/test_sticky_preserve.py`.
  - 7 Phase A tests in `tests/js/child_view.test.js` + 15 Phase B/C tests in
    `tests/js/sticky_preserve.test.js`.
  - 3 end-to-end tests in `tests/integration/test_sticky_redirect_flow.py`
    (Dashboard→Settings preservation, rapid A→B→A instance identity, no-slot
    reconcile path) + 3 demo-app smoke tests covering the full navigation
    cycle.
  - Phase C regression tests: `skipMountHtml` mount branch reattaches sticky
    subtrees (Fix F1); `disconnect()` drains `_sticky_preserved` so background
    tasks don't leak (Fix F2).

  **Documentation**
  - [ADR-011](docs/adr/011-sticky-liveviews.md) — wire protocol, DOM attributes,
    client/server flow diagrams, full security model + threat matrix, failure
    modes, relationship to v0.7.0 `dj-activity`.
  - [User guide](docs/website/guides/sticky-liveviews.md) — quick start,
    common patterns, limitations, debugging, FAQ.
  - Runnable demo app in `examples/demo_project/sticky_demo/` — Dashboard,
    Settings, Reports pages with sticky AudioPlayer + NotificationCenter widgets
    showing preservation + `no-slot` unmount.

- **FLIP list-reorder animations (v0.6.0 animations milestone finale)** —
  Opt-in per container via `dj-flip`. Declarative attribute on a list parent animates direct-child reorders using First-Last-Invert-Play. Tunables: `dj-flip-duration` (default 300ms, parsed via `Number` + `isFinite` + clamp `[0, 30000]` — trailing garbage rejects to fallback), `dj-flip-easing` (default `cubic-bezier(.2,.8,.2,1)`, strings containing `;"'<>` rejected to defeat CSS-property-breakout). Respects `prefers-reduced-motion`. Nested `[dj-flip]` isolated via `subtree: false`. Author-specified inline `transform` on children is preserved across the animation. Overlapping reorders are guarded against cache corruption via an in-flight-transition check. Works with keyed lists where items carry stable `id=` (Rust VDOM emits MoveChild). Lands in `static/djust/src/44-dj-flip.js` (~260 LOC). 12 JSDOM tests in `tests/js/dj_flip.test.js`.

- **`{% djust_skeleton %}` shimmer placeholder (v0.6.0 animations milestone finale)** —
  Template tag for placeholder blocks. Props: `shape` (line|circle|rect, whitelist-validated), `width`/`height` (regex-whitelisted against `^[\d.]+(px|em|rem|%|vh|vw|ch)?$`, invalid falls back to shape default), `count` (clamped to `[1, 100]`), `class_`. All values HTML-escaped via `build_tag()`. Shimmer `@keyframes` emitted once per render via `context.render_context`. Integrates with existing `dj-loading` shorthand and with `{% if async_pending %}` server blocks. 21 Python tests in `tests/unit/test_djust_skeleton_tag.py`.

## [0.5.7rc1] - 2026-04-23

### Added

- **Resumable uploads across WebSocket disconnects (v0.5.7 — closes #821)** —
  Long mobile uploads now survive network hiccups, backgrounded tabs, and
  brief WS drops. New `djust.uploads.resumable.ResumableUploadWriter` wraps
  any existing `UploadWriter` (S3 MPU, GCS, Azure, tempfile) and persists
  chunk-level state into a pluggable `UploadStateStore`. Two stores ship
  in core: `InMemoryUploadState` (default, single-process) and
  `RedisUploadState` (requires `djust[redis]`, multi-process / multi-host).
  New WS message `{"type":"upload_resume","ref":X}` returns
  `{"type":"upload_resumed","status":"resumed|not_found|locked","bytes_received":N,"chunks_received":[...]}`.
  New HTTP status endpoint `GET /djust/uploads/<upload_id>/status`
  (session-scoped, cross-user probes blocked). Client-side IndexedDB
  cache in `15-uploads.js` lets tabs resume uploads after reload if the
  file reference can be re-selected. State is capped at 16 KB per
  upload_id (run-length-compressed chunk ranges) with 24-hour default
  TTL. Opt-in per slot: `allow_upload("video", writer=S3Resumable,
  resumable=True)`. ~1,050 LOC net across `python/djust/uploads/`
  (`__init__.py` modified, `resumable.py`, `storage.py`, `views.py`
  added), `python/djust/websocket.py`, `python/djust/static/djust/src/15-uploads.js`
  (+ `03-websocket.js` dispatch), full wire-protocol spec +
  failure-mode + security analysis in `docs/adr/010-resumable-uploads.md`.
  44 unit tests in `python/djust/tests/test_resumable_uploads_821.py`
  (compaction, in-memory + fake-Redis roundtrip, writer lifecycle,
  resume resolution, TTL expiry via mock clock, concurrent-resume
  rejection, HTTP status view) plus 2 async WS handler cases in the
  same file, plus 9 JSDOM cases in
  `tests/js/upload_resume.test.js` (file-hint fingerprint, UUID
  round-trip, IDB shim roundtrip, cleanup on complete).
- **Upload writers — S3 pre-signed PUT URLs + first-class GCS/Azure backends (v0.5.7 — closes #820, #822)** —
  New `djust.contrib.uploads.s3_presigned` module lets clients upload directly to S3 via a pre-signed
  URL; djust only signs and observes completion via S3 event webhook. New
  `djust.contrib.uploads.gcs.GCSMultipartWriter` and `djust.contrib.uploads.azure.AzureBlockBlobWriter`
  ship as first-class `UploadWriter` subclasses with consistent error taxonomy
  (`UploadError`, `UploadNetworkError`, `UploadCredentialError`, `UploadQuotaError`,
  re-exported from `djust.uploads`). Client-side `djust.uploads.uploadPresigned(spec, file, hooks)`
  streams bytes straight to object storage via XHR (progress via `xhr.upload.onprogress`),
  bypassing the WS upload machinery. Optional extras: `djust[s3]`, `djust[gcs]`, `djust[azure]`.
  ~650 LOC + 50 regression tests (mocked SDKs) across
  `python/djust/tests/test_presigned_s3_820.py`, `python/djust/tests/test_gcs_upload_writer_822.py`,
  `python/djust/tests/test_azure_upload_writer_822.py`.
  See `docs/website/guides/uploads.md`.
- **Docs cleanup: 4 issues closed** — dj-remove no-CSS-transition gotcha (#902), dj-transition-group
  long-form precedence (#907), Django 5.1 + 5.2 classifiers in `pyproject.toml` (#912), new guide
  page for `dj-virtual` variable-height mode at `docs/website/guides/virtual-lists.md` (#952).
- **dj-virtual variable-height items via ResizeObserver — closes #797** —
  PR #796 shipped `dj-virtual` with fixed-height items only. This adds
  opt-in variable-height support via a new `dj-virtual-variable-height`
  boolean attribute. Implementation: ResizeObserver per rendered item
  feeds a `Map<index, number>` height cache; a lazily-computed prefix-sum
  array drives offset math and the virtual spacer total. Unmeasured items
  fall back to a configurable `dj-virtual-estimated-height` (default 50px).
  Fixed-height mode (`dj-virtual-item-height="N"`) is unchanged — tested
  explicitly as a regression guard. Updated `29-virtual-list.js` (~180 LOC
  net) and 4 new JSDOM cases in `tests/js/virtual_list.test.js` covering
  attribute activation, mixed-height prefix-sum math, RO-driven cache
  updates, and fixed-mode regression.
- **Tooling: CHANGELOG test-count validator — closes #908** — new
  `scripts/check-changelog-test-counts.py` parses phrases like
  `N JSDOM cases`, `N regression tests`, `N unit tests`,
  `N test cases`, `N parameterized cases` in the `[Unreleased]` section,
  resolves every backticked `tests/js/*.test.js` / `python/djust/tests/*.py`
  / `tests/unit/*.py` path inside the same bullet, counts test
  functions in each, and fails if the claim doesn't match reality.
  Delta phrases (`2 new cases`, `3 additional tests`) are deliberately
  skipped — they can't be verified without git history. Wired into
  `.pre-commit-config.yaml` as a local hook scoped to `^CHANGELOG\.md$`
  and exposed as `make check-changelog`. Self-tested by 7 cases in
  `tests/test_changelog_test_counts.py` covering match/mismatch,
  JSDOM-vs-py file resolution, multi-file summing, delta ignore, and
  missing-section tolerance.
- **Tooling: CodeQL triage script — closes #916** —
  `scripts/codeql-triage.sh [rule-id]` paginates
  `/repos/{owner}/{repo}/code-scanning/alerts?state=open` via `gh api`
  and emits a markdown triage doc grouped by `rule.id`, sorted within
  each group by file/line. Optional positional arg filters to a single
  rule for focused triage sessions. Turns the raw alert dump (noisy JSON)
  into something reviewable in a PR comment or a doc. Documented in
  `scripts/README.md`.
- **Tooling: CodeQL sanitizer MaD model — closes #934** — new
  extension pack at `.github/codeql/models/` (qlpack.yml +
  `djust-sanitizers.model.yml`) teaches CodeQL that
  `djust._log_utils.sanitize_for_log()` is a log-injection sanitizer.
  Referenced from `.github/codeql/codeql-config.yml` via a new `packs:`
  section. Closes the class of false-positive `py/log-injection` alerts
  we've been dismissing individually. Verification lands with the next
  main-branch CodeQL scan. See `.github/codeql/README.md` for the tuple
  shape, fallback plan (hand-written `LogInjectionFlowConfiguration`
  override), and links to CodeQL's data-extensions docs.

- **ADR-009: Mixin side-effect replay on WebSocket state restoration —
  closes #897** — formalizes the `_restore_<concept>()` pattern first
  shipped ad-hoc in PRs #891 (UploadMixin, #889) and #895 (PresenceMixin
  + NotificationMixin, #893 / #894). Codifies the serialization contract
  (JSON-only saved attrs), error handling (WARNING-level wrap, never
  kill the WS), convergence/idempotency requirement, naming convention
  (`_restore_<concept>`), and call ordering in `LiveViewConsumer`.
  Documents the rejected alternatives: don't-skip-mount (perf cost),
  snapshot-entire-managers (serialization complexity), pickle-to-session
  (security + format stability). New file:
  `docs/adr/009-mixin-side-effect-replay.md`.

### Fixed

- **Framework cleanup (closes #762, #890)** — djust.A010 / A011 system checks now recognize
  proxy-trusted deployments: when `SECURE_PROXY_SSL_HEADER` + `DJUST_TRUSTED_PROXIES` are both set,
  `ALLOWED_HOSTS=['*']` is accepted (supports AWS ALB, Cloudflare, Fly.io, and other L7 load
  balancers where task private IPs rotate). Also filters ~25 framework-internal attrs
  (`sync_safe`, `login_required`, `template_name`, `http_method_names`, `on_mount_count`,
  `page_meta`, etc.) from `LiveView.get_state()`, the WS `_snapshot_assigns` change-detection
  path, and the `_debug.state_sizes` observability payload — user's reactive state is no longer
  swamped by framework config. Non-breaking fix via a new `live_view._FRAMEWORK_INTERNAL_ATTRS`
  frozenset; attribute names unchanged. 14 new regression tests in
  `python/djust/tests/test_a010_proxy_trusted_890.py` and
  `python/djust/tests/test_get_state_filter_762.py`. Deployment guide updated with the
  proxy-trusted escape-hatch pattern.
- **JS-centric batch (closes #949, #951, #953)** — tag_input hidden-input payload now JSON-encoded
  instead of comma-separated, so tag values containing commas round-trip intact (#949). dj-virtual
  variable-height cache now keyed by `data-key` attribute (configurable via `dj-virtual-key-attr`),
  falling back to index when absent — cached heights survive item reorders (#951). Consolidated
  JSDOM test helpers at `tests/js/_helpers.js` (`createDom`, `nextFrame`, `fireDomContentLoaded`,
  `makeMessageEvent`, `mountAndWait`) and refactored 3 test files to use them (#953). 2 new Python
  regression tests (commas + quotes round-trip) and 3 new JSDOM cases (reorder survival, index
  fallback, custom key attribute). Guardrail added to `scripts/build-client.sh` to fail fast if
  `tests/js/_helpers.js` ever leaks into the production bundle.
- **Hygiene batch (closes #791, #794, #795, #818, #948)** — bumped `ruff-pre-commit` from v0.8.4 to
  v0.15.11 (#948) and applied `ruff format` to all resulting drift (#791 — expanded beyond the
  original 5 files due to modern-ruff disagreements; 19 files total across `python/djust/` and
  `tests/`). Added `logger.debug` notice in `components/suspense.py` when
  `{% dj_suspense await=X %}` receives a non-AsyncResult value so a typo surfaces during
  development (#794), simplified a redundant `or not value.ok` check near `suspense.py:138` given
  the AsyncResult mutually-exclusive-flag invariant (#795), wrapped the namespaced `data-hook`
  attribute value with `django.utils.html.escape()` for defense-in-depth in
  `templatetags/live_tags.py` (#818), and corrected stale test-count claims in two historical
  CHANGELOG bullets (`test_assign_async.py` 11 → 18, `test_suspense.py` 11 → 12) flagged by
  the #795 reviewer. No behavior change.

- **Security + cleanup: pre-existing test failures, redirect audit, dep ceilings, edge tests — closes #910, #921, #922, #935** —
  **#935**: fixed 3 stale test assertions that were checking for leaked
  exception-class names in API error responses. The implementations in
  `api/dispatch.py`, `observability/views.py` deliberately sanitize
  error payloads (don't echo `RuntimeError` / internal method names to
  clients; send to server logs instead). Tests now verify the sanitized
  contract (`"server logs"` in `error`, handler_name / session_id echo)
  rather than the leaked details. Fixes
  `test_api_response.py::test_dispatch_serialize_str_missing_method_returns_500`,
  `test_observability_eval_handler.py::test_eval_500_when_handler_raises`,
  and `test_observability_reset_view.py::test_reset_500_when_mount_raises`.
  **#921**: expanded open-redirect audit beyond PR #920 — `mixins/request.py`
  now validates `hook_redirect` returned by developer-defined `on_mount`
  hooks via `url_has_allowed_host_and_scheme`, falling back to `"/"` and
  logging a WARNING on unsafe targets. `auth/mixins.py`
  `LoginRequiredLiveViewMixin.dispatch` now validates the computed login
  URL as defense-in-depth against misconfigured `settings.LOGIN_URL`,
  falling back to `"/accounts/login/"`.
  **#922**: 7 new regression tests in
  `python/djust/tests/test_security_redirects_paths.py` —
  `javascript:` scheme rejection, HTTPS-to-HTTP downgrade, null-byte
  path-injection, uppercase/case-sensitive allowlist, hook_redirect
  off-site rejection, hook_redirect same-site acceptance, and off-site
  `LOGIN_URL` fallback.
  **#910**: added upper-bound ceilings to all runtime + dev dependencies in
  `pyproject.toml` (e.g. `requests>=2.28,<3`, `orjson>=3.11.6,<4`,
  `nh3>=0.2,<1`). Prevents uncontrolled major bumps during `uv lock`
  refresh (see PR #909 which caught Django 6.x resolving under `>=4.2`).
  Ceiling policy documented in a comment above `[project.dependencies]`.
  Verified with `uv lock` — only material change is `redis` 7.3 -> 6.4
  (stays under new `<7` ceiling).
- **UploadMixin defensive replay for schema-changed configs — closes #892** —
  `_restore_upload_configs` now wraps each per-slot `allow_upload(**cfg)`
  in try/except `TypeError`. On signature mismatch (kwarg added / renamed
  / removed between djust versions), logs a WARNING identifying the slot
  + the mismatched kwarg, then falls back to `allow_upload(slot_name)`
  — bare-minimum replay — so uploads for that slot still work with
  default config. One broken saved dict no longer kills replay for every
  other slot on the page. Each saved dict is now tagged with
  `_upload_configs_version = 1` for future explicit migrations.
  Regression tests in
  `tests/unit/test_mixin_replay_schema_cross_loop_892_896.py`.
- **NotificationMixin cross-loop restore — closes #896** —
  `_restore_listen_channels` now detects when the
  `PostgresNotifyListener` singleton is stranded on a closed event loop
  (server restart with fresh ASGI loop, test harness per-test loops,
  sticky-session LB cross-worker handoff) and calls a new
  `PostgresNotifyListener.reset_for_new_loop()` classmethod to drop the
  singleton before replay. The pre-check inspects
  `listener._loop.is_closed()`; a per-channel `except RuntimeError`
  branch handles the race where the loop closes between the pre-check
  and the `ensure_listening` call (resets and retries once). Prevents
  silent NOTIFY drops on cross-loop restore. Regression tests in
  `tests/unit/test_mixin_replay_schema_cross_loop_892_896.py`.



- **Observer JS — closes #879, #880, #881, #882** —
  **#879**: `37-dj-mutation.js` and `38-dj-sticky-scroll.js` document-level
  root observers now detect attribute REMOVAL on already-observed elements
  (via `attributes: true` + `attributeFilter: ['dj-mutation']` /
  `['dj-sticky-scroll']`) and call the module's teardown helper. Previously
  removing the attribute from an element left a stale `MutationObserver` +
  scroll listener attached.
  **#880**: documented the `Map`-vs-`WeakMap` choice in
  `39-dj-track-static.js` — the reconnect-diff iterates all tracked
  elements to compare snapshot URLs, and `WeakMap` does not support
  iteration; the `isConnected` check in `_checkStale` handles detached
  elements.
  **#881**: documented unconditional scroll-to-bottom on install in
  `38-dj-sticky-scroll.js` — matches Phoenix phx-auto-scroll / Ember
  scroll-into-view behavior (sticky-scroll is an "opt into bottom-pinning"
  attribute; authors want the initial view pinned to the most recent
  content: chat, log output).
  **#882**: regression test in `tests/js/dj_mutation.test.js` — no
  `dj-mutation-fire` CustomEvent fires when the element is removed before
  the debounce timer expires (existing `_tearDownDjMutation` path
  correctly clears the pending timer on removal).

### Tests

- **dj-transition-group follow-ups — closes #905, #906** —
  **#905** The VDOM `RemoveChild` integration test in
  `tests/js/dj_transition_group.test.js` waited 700 ms per run for the
  default dj-remove fallback timer. Pinned `dj-remove-duration="50"` on
  the child and reduced the wait to ~80 ms, dropping this file's
  wallclock from ~1.2 s to ~600 ms.
  **#906** Added a nested-group regression test — outer + inner
  `[dj-transition-group]` parents each install their own per-parent
  observer (`subtree:false`), so a new child appended to `inner` gets
  the inner group's enter/leave specs and is not clobbered by the
  outer's. Pins the subtree-scoping invariant relied on by the phase-2c
  implementation.

### Fixed

- **Mechanical cleanup — closes #914, #915** —
  **#914**: dropped redundant `ch == " "` clause in `_log_utils.sanitize_for_log` —
  ASCII space is already printable so the explicit check was dead.
  **#915**: bulk-applied `ruff format` (pinned pre-commit version 0.8.4) to 4 pre-drifted
  files (3 theming test files + `uploads.py`) to bring them to canonical form.
  No behavior change in either fix.

- **3 latent bugs caught by prior CodeQL-cleanup audits — closes #930, #932, #933** —
  **#930 FormArrayNode inner content**: `{% form_array %}...{% endform_array %}` parsed the block
  body into a nodelist via `parser.parse(("endform_array",))` but `FormArrayNode.render` never
  rendered that nodelist — users' inner template markup silently disappeared. Fixed by rendering
  the nodelist once per row with `row`, `row_index`, and `forloop` (dict shape:
  `{counter, counter0, first, last}`) pushed onto the template context; empty or whitespace-only
  blocks keep the original single-input-per-row default output, so existing users see no change.
  **#932 tag_input missing `name=` attribute**: `TagInput._render_custom` rendered a visible
  "type to add" `<input class="tag-input-field" placeholder="...">` with no `name=`, so form
  submissions silently dropped the tag list from POST data. Fixed by emitting a
  `<input type="hidden" name="<self.name>" value="<csv of tags>">` alongside the visible input
  whenever `self.name` is non-empty; hidden value is `html.escape`'d.
  **#933 gallery/registry.py dead discover_\*  path**: `discover_template_tags()` and
  `discover_component_classes()` were public helpers exported from
  `djust.components.gallery.__init__` but `get_gallery_data()` never called them — a developer
  adding a new `@register.tag` or `Component` subclass without updating the curated
  `EXAMPLES` / `CLASS_EXAMPLES` dicts had that new thing silently missing from the rendered
  gallery. Fixed by wiring both helpers into `get_gallery_data()` as a cross-check: any
  registered tag / component class missing an example entry emits a `logger.debug` warning
  naming the missing entries, and discovery failures are caught so the gallery never breaks
  at runtime. 14 regression tests across `python/djust/tests/test_form_array_930.py`,
  `python/djust/tests/test_tag_input_932.py`, `python/djust/tests/test_gallery_registry_933.py`
  (7 of which fail on main pre-fix; 2 added later under #949 for commas-in-values round-trip).
  No behavior change for non-broken inputs.
  (`python/djust/components/templatetags/djust_components.py`,
  `python/djust/components/components/tag_input.py`,
  `python/djust/components/gallery/registry.py`)

- **dj-remove follow-ups — closes #900, #901** — Extracted shared `_teardownState(el, state)` helper in `42-dj-remove.js` so `_finalizeRemoval` and `_cancelRemoval` no longer duplicate the clearTimeout + removeEventListener + observer.disconnect + _pendingRemovals.delete block (Stage 11 nit from PR #898). Added a debug warning (gated on `globalThis.djustDebug`) when `_parseRemoveSpec` encounters a 2-token value like `dj-remove="fade-out 300"` — previously silent fall-through. 2 new JSDOM regression cases in `tests/js/dj_remove.test.js` (12/12 passing).

- **dj-transition edge cases — closes #886, #887, #888** —
  **#886** `_parseSpec` in `41-dj-transition.js` now rejects comma, paren,
  and bracket separators up front (returns `null` and emits a debug
  warning gated on `globalThis.djustDebug`) instead of letting
  `classList.add` throw `InvalidCharacterError` at runtime — matches the
  dj-remove #901 loud-in-debug / silent-in-prod pattern.
  **#887** The `cleanup` callback (both `transitionend` handler and 600 ms
  fallback path) now guards with `el.isConnected` — if the element has
  been detached from the DOM before cleanup fires, we skip classList and
  listener work and just drop the `_djTransitionState` entry. Prevents
  any future `parentNode.X` access from NPE'ing on a detached node.
  **#888** Unskipped the two previously-flaky `transitionend` tests in
  `tests/js/dj_transition.test.js` by swapping timing-sensitive
  `setTimeout(..., 30)` waits for synchronous
  `el.dispatchEvent(new Event('transitionend'))` — deterministic under
  vitest parallel load. Added one new test covering the #886 parser
  rejection path. All 9 dj-transition tests pass deterministically.

## [0.5.6rc1] - 2026-04-23

### BREAKING CHANGES

- **Dropped Python 3.9 support** (`requires-python = ">=3.10"`). Python 3.9 reached end-of-life on 2025-10-05; the ecosystem has since moved on (orjson, pytest, python-dotenv, requests, and mcp have all dropped py3.9 support in versions that carry security fixes). Keeping py3.9 in the `requires-python` constraint kept 4 Dependabot alerts stuck open against the py3.9 resolution train — alerts which had no upstream patch available on py3.9. Closes Dependabot alerts #41 (orjson recursion DoS), #87 (pytest tmpdir race), #89 (python-dotenv symlink follow in `set_key`), #62 (requests insecure temp file reuse). Existing py3.9 users can continue installing djust v0.5.x from PyPI; v0.5.6+ requires py3.10+. Also bumped `[tool.ruff] target-version` to `py310` and `[tool.mypy] python_version` to `3.10`; collapsed the orjson / mcp conditional pins (previously carried a py3.9-stuck floor).

### Added

- **`dj-remove` — exit animations before element removal (v0.6.0)** — Phoenix `JS.hide` / `phx-remove` parity. When a VDOM patch, morph loop, or `dj-update` prune would physically remove an element carrying `dj-remove="..."`, djust delays the actual `removeChild()` until the CSS transition the attribute describes has played out (or a 600 ms fallback timer fires, overridable via `dj-remove-duration="N"`). Two forms: three-token `dj-remove="opacity-100 transition-opacity-300 opacity-0"` matches the `dj-transition` shape (start → active → end), and single-token `dj-remove="fade-out"` applies one class and waits for `transitionend`. If a subsequent patch strips the `dj-remove` attribute from a pending element, the pending removal cancels and the element stays mounted. Public hook `window.djust.maybeDeferRemoval(node)` is called from five removal sites in `12-vdom-patch.js`. Descendants of a `[dj-remove]` element are NOT independently deferred — they travel with their parent, matching Phoenix. New `static/djust/src/42-dj-remove.js`. 10 JSDOM cases in `tests/js/dj_remove.test.js`. Phase 2a of the v0.6.0 Animations & transitions work; FLIP / `dj-transition-group` / skeletons remain separate follow-ups.

- **`dj-transition-group` — orchestrate enter/leave animations for child lists (v0.6.0)** — React `<TransitionGroup>` / Vue `<transition-group>` parity. Authors mark a parent container and specify enter + leave specs once; djust wires those specs onto each child by setting `dj-transition` (enter) and `dj-remove` (leave) — re-using the already-shipped phase-1 / phase-2a runners (#885 / #898) rather than re-implementing the phase-cycling or removal-deferral machinery. Two forms: short `dj-transition-group="fade-in | fade-out"` (pipe-separated halves, each accepting the same 1- or 3-token shape as `dj-transition` / `dj-remove`), and long form with bare `dj-transition-group` plus `dj-group-enter` / `dj-group-leave` on the parent. Initial children get the leave spec only by default (so they animate out if later removed, but nothing animates in on first paint); opt them into first-paint enter animation via `dj-group-appear` on the parent. Never overwrites author-specified `dj-transition` or `dj-remove` on a child — escape hatch for per-item overrides. A per-parent `MutationObserver` picks up newly appended children; a document-level observer handles parents that arrive via VDOM patch or attribute mutation. New `static/djust/src/43-dj-transition-group.js`. 11 JSDOM cases in `tests/js/dj_transition_group.test.js` cover short-form parsing, invalid input, manual `_handleChildAdded`, respect for pre-existing per-child attrs, default leave-only initial wiring, `dj-group-appear` enter opt-in, post-mount append via observer, `_uninstall` disconnecting the per-parent observer, parent-removal auto-cleanup via the root observer, end-to-end VDOM `RemoveChild` deferral through the wired `dj-remove`, and cancel-on-strip uninstalling the per-parent observer when `dj-transition-group` is removed at runtime (symmetric with `dj-remove`). Phase 2c of the v0.6.0 Animations & transitions work; FLIP and skeletons remain separate follow-ups. (`python/djust/static/djust/src/43-dj-transition-group.js`)

### Fixed

- **Code-scanning cleanup: remaining ~35 `py/cyclic-import` notes + 7 misc note-level alerts**. Real refactor: extracted `ContextProviderMixin` from `live_view.py` to a new `_context_provider.py` module so `components/base.py` can import it without creating a module-level cycle back through `live_view -> serialization -> components/base`. `live_view.py` re-exports `ContextProviderMixin` for back-compat (existing user code importing `from djust.live_view import ContextProviderMixin` keeps working). Closes 3 real cyclic-import alerts (#2112, #2113, #2114). The remaining ~28 theming cyclic-import notes (in `manager.py`, `registry.py`, `theme_css_generator.py`, `pack_css_generator.py`, `theme_packs.py`, `manifest.py`, `css_generator.py`) are all `from ... import` statements INSIDE function bodies (or the module-level counterpart paired with such a lazy import) — deliberate cycle breakers where the runtime module graph is acyclic — dismissed with specific justification. Also fixed 3 `py/mixed-returns` via mechanical cleanup: `theming/inspector.py` (added 405 Method-Not-Allowed fallback), `admin_ext/views.py` (replaced bare `return` with `return None` in `run_action`), `management/commands/djust_audit.py` (explicit `return None` from all `handle()` branches). Dismissed 3 `py/unused-global-variable` false positives (lazy-init cache pattern in `components/icons.py:_icon_sets_cache`, `theming/theme_packs.py:_theme_imports_done`, `observability/log_handler.py:_installed_handler` — same pattern as `_psycopg` dismissed in #2104/#2105) and 1 `py/ineffectual-statement` false positive (`tutorials/mixin.py:371` — `await coro` is a real async effect, not an ineffectual expression). No behavior change; full Python suite passes (3428 passed, 15 skipped). (`python/djust/_context_provider.py`, `python/djust/live_view.py`, `python/djust/components/base.py`, `python/djust/theming/inspector.py`, `python/djust/admin_ext/views.py`, `python/djust/management/commands/djust_audit.py`)

- **Cleanup: 36 `py/empty-except` + 6 misc CodeQL note-severity alerts** — Narrowed over-broad `except Exception: pass` to specific exception types where the call surface was knowable, and added `logger.debug(...)` (with `import logging; logger = logging.getLogger(__name__)` where not already present) for optional-feature probes in `components/gallery/views.py` (optional `djust_theming` static CSS link), `components/icons.py` (optional `DJUST_COMPONENTS_ICON_SETS` setting), `auth/admin_views.py` (optional `django-allauth` OAuth stats, 2 sites), `auth/djust_admin.py` (optional allauth registry), and `mixins/context.py` (best-effort descriptor resolution). Annotated "skip invalid numeric input" sites with justification comments (+ `pass` → `continue` for clarity) across `components/templatetags/_charts.py` (4), `components/rust_handlers.py` (8), `components/components/{calendar_heatmap,heatmap,line_chart,source_citation}.py`, `components/descriptors/carousel.py`, `components/function_component.py` (2), `components/mixins/data_table.py` (3), `components/templatetags/djust_components.py` (2), and similar narrow/intentional catches in `checks.py`, `components/base.py` (optional `@event_handler` decoration), `mixins/waiters.py` (idempotent waiter removal), `observability/dry_run.py` (best-effort bulk-op count), `theming/management/commands/djust_theme.py`, and `theming/templatetags/theme_tags.py`. Re-export in `components/templatetags/djust_components.py` (`_get_field_type`, `_infer_columns`, `_queryset_to_rows` from `_forms`) made explicit via `__all__` (closes `py/unused-import` #2171). Deleted 3 JS unused-variable declarations: `decoder` in `components/static/djust_components/ttyd/ttyd_terminal.js:35`, `resolvedMode` in `theming/static/djust_theming/js/theme.js:416`, and `getCookie()` in `theming/static/djust_theming/js/theme.js:449`. Dismissed 2 `py/unused-global-variable` false positives (#2104, #2105 — `_psycopg` / `_psycopg_sql` in `db/notifications.py` are lazy module-level caches assigned via `global` inside `_ensure_psycopg()`; CodeQL's scope analyzer doesn't track global-write patterns). 4 note-level `py/cyclic-import` alerts (#2096, #2112-#2114) left for scanner rescan — expected to auto-close as PR #928's refactor propagates. No behavior change; full Python suite passes (3428 passed, 15 skipped).

- **Code-quality cleanup — ~66 CodeQL note-severity alerts** — mechanical fixes: deleted unused imports (treated re-exports with `__all__` + `# noqa: F401` preservation; replaced side-effect submodule imports with `importlib.import_module`), removed ~30 unused local variables across `rust_handlers.py`, `templatetags/djust_components.py`, `components/*.py`, and `templatetags/_forms.py` / `_advanced.py`, removed ~4 unused module-level names (`default_app_config` in `components/__init__.py`, `theming/__init__.py`, `admin_ext/__init__.py` — obsolete since Django 3.2 auto-discovery), simplified 3 `lambda vals: f(vals)` wrappers in `AGG_FUNCS` (pivot-table aggregations) to bare `sum` / `len`, deduped 2 `import json` / `import asyncio` occurrences in `function_component.py` / `mixins/data_table.py` / `db/notifications.py`, reconciled `import X` + `from X import Y` conflicts in `gallery/registry.py` and `templatetags/djust_components.py`, and removed ineffectual single-`...` statements in Protocol / abstract method bodies in `api/auth.py` and `tenants/audit.py`. No behavior change; full suite passes (3428). Plus 3 dismissed with justification: 2 × `py/catch-base-exception` in `async_work.py` (existing `# noqa: BLE001` comments + documented design intent of surfacing every failure via `AsyncResult.errored`), and 1 × `js/syntax-error` on `theming/templates/.../theme_head.html` (CodeQL's JS analyzer erroneously parsing a Django template as JavaScript).

- **Break `themes → _base → presets/theme_packs` cyclic import (873 CodeQL alerts) + add explicit `event.origin` check to service worker `message` handler** — CodeQL's `py/unsafe-cyclic-import` rule flagged 872 alerts across the theming subsystem: `themes/_base.py` imported dataclasses + shared style instances from `..presets` and `..theme_packs`, and those two modules re-imported each theme file under `.themes.*` at module load — a real cycle that happened to work only because `ColorScale` / `ThemeTokens` / etc. were defined earlier in `presets.py` than the theme imports. Extracted the pure data into two new dependency-free modules: `python/djust/theming/_types.py` (14 dataclass types: `ColorScale`, `ThemeTokens`, `SurfaceTreatment`, `ThemePreset`, `TypographyStyle`, `LayoutStyle`, `SurfaceStyle`, `IconStyle`, `AnimationStyle`, `InteractionStyle`, `DesignSystem`, `PatternStyle`, `IllustrationStyle`, `ThemePack` — stdlib imports only) and `python/djust/theming/_constants.py` (~60 shared style instances — `PATTERN_*`, `ILLUST_*`, `ICON_*`, `ANIM_*`, `INTERACT_*` at both the design-system and pack levels; depends only on `_types`). `themes/_base.py` now imports from those two modules, bypassing the cycle; `presets.py` and `theme_packs.py` import from the same new modules and re-export every type and instance under `__all__` for full backward compat (no theme author touches any import site). Also resolved the pre-existing shadow between two `InteractionStyle` class definitions (the narrow DS-level `InteractionStyle` at `theme_packs.py:150` was silently shadowed by the wider pack-level one at `:1374` — all `INTERACT_*` module-level instances relied on fields only the wider class had; unified on the superset definition in `_types.py`) and the `INTERACT_MINIMAL` / `INTERACT_PLAYFUL` name collision between the DS-level and pack-level bindings (kept the distinct runtime bindings via `_INTERACT_MINIMAL_DS` / `_INTERACT_PLAYFUL_DS`). Also tightened the service-worker `message` handler in `python/djust/static/djust/service-worker.js` with an explicit `event.origin !== self.location.origin` early return at the top of the listener, satisfying CodeQL's `js/missing-origin-check` rule (alert #2170 — follow-up to the source+scope check shipped in #925). 7 regression cases in `python/djust/tests/test_theming_imports_backcompat.py` cover: presets/theme_packs type exports still importable, shared instance exports still importable, `_base` re-exports identical object identity to `presets` / `theme_packs`, per-theme files (vercel used as smoke) still construct a full triple, lazy theme-pack registry still populates 71 packs + 73 design systems, and the DS-vs-pack `InteractionStyle` distinction for `minimal` / `playful` is preserved (DS `link_hover="underline"`, pack `button_click="ripple"` — both bindings round-trip). Expected alert closure: 872 × `py/unsafe-cyclic-import` + 1 × `js/missing-origin-check` = 873. (`python/djust/theming/_types.py`, `python/djust/theming/_constants.py`, `python/djust/theming/presets.py`, `python/djust/theming/theme_packs.py`, `python/djust/theming/themes/_base.py`, `python/djust/static/djust/service-worker.js`)

- **Dead conditional in `djust/theming/templatetags/theme_form_tags.py`** — the label-visibility check at line 88 had `isinstance(field.widget, template.library.InvalidTemplateLibrary if False else type(None))`. The `if False else type(None)` ternary always evaluated to `type(None)`, making the first operand unreachable dead code (CodeQL `py/constant-conditional-expression`). Dropped the dead branch; the isinstance check is now `isinstance(field.widget, type(None))` with a comment explaining the intent.

- **Close 21 `py/undefined-export` CodeQL alerts** — `djust/auth/__init__.py` and `djust/tenants/__init__.py` use a `__getattr__`-based lazy-import dispatcher to defer Django-ORM-dependent imports. CodeQL's static analysis doesn't recognize this pattern; names declared in `__all__` but only resolved via `__getattr__` were flagged. Added a `TYPE_CHECKING` block to each `__init__.py` with eager import statements gated behind `if TYPE_CHECKING:` — the imports execute only under static analysis (mypy, CodeQL, IDEs), never at runtime. The lazy-import runtime behavior is unchanged. New `python/djust/tests/test_lazy_import_resolution.py` (47 parameterized cases) regression-tests that every `__all__` entry resolves.

- **3 real bugs caught by CodeQL scanning (6 alerts closed)** — **`python/djust/components/gallery/views.py`** (`py/stack-trace-exposure`, 2 alerts): the gallery's per-variant render fallback interpolated the raw `Exception` repr into the HTML returned to the user (`f'<div ...>Render error: {exc}</div>'`), leaking internal template / class paths and error detail to any gallery viewer. Fixed to log via `logger.exception(...)` and return a generic `Render error — see server logs` message at both the `type == "tag"` template-render path and the `type == "class"` render-callable path. **`python/djust/theming/build_themes.py`** (`py/call-to-non-callable`, 1 alert): `BuildTimeGenerator.__init__` assigned the `generate_manifest: bool` constructor argument onto `self.generate_manifest`, which *shadowed the method of the same name* at `def generate_manifest(self, generated_files)`. Calling `self.generate_manifest(generated_files)` at line 521 from `build_all()` would have raised `TypeError: 'bool' object is not callable` on any invocation of the full build — the method was effectively unreachable. Renamed the attribute to `self._generate_manifest` (underscore = internal flag), updated the single consumer inside the method to match; the callable is now callable again. **`python/djust/theming/accessibility.py`** (`py/str-format/missing-named-argument`, 3 alerts): `AccessibilityValidator.generate_accessibility_report_html` passed an HTML+CSS string through `str.format(**kwargs)` where the embedded literal CSS braces (`body { font-family: ... }`) were being parsed by Python's format machinery as placeholder keys, raising `KeyError` / `ValueError` at runtime on the very first `{` it hit. Refactored to keep the CSS in a separate un-formatted string (`_css_styles`) and feed it as a single `{styles}` placeholder into the HTML template (`_html_template`); no double-brace escaping hazard, template semantics preserved. 4 regression cases in `python/djust/tests/test_codeql_bugfixes.py` cover: exception-message *not* reflected in either gallery render fallback; `generate_manifest(True)` calls the method (no TypeError); `generate_manifest(False)` short-circuits to `""`; HTML report renders end-to-end with both `<!DOCTYPE html>` *and* surviving CSS `font-family` tokens. (`python/djust/components/gallery/views.py`, `python/djust/theming/build_themes.py`, `python/djust/theming/accessibility.py`)

### Security

- **Client-side markdown preview: escape user input before markdown transforms — closes 1 CodeQL `js/xss-through-dom` alert (#1978, warning)** — `inlineFormat` in `python/djust/components/static/djust_components/markdown-textarea.js` applied regex-based markdown substitutions on raw user input and wrote the result into the preview pane via `innerHTML`, so a user typing `# <script>alert(1)</script>` into their textarea saw the raw `<script>` tag rendered in their own preview. Self-XSS in most deployments, but propagates to other users wherever a textarea's `data-raw` payload later lands in another user's view (shared drafts, admin review screens, collaborative editors). **Fix**: call `escapeHtml()` at the top of `inlineFormat` (before any regex transform — the markdown syntax chars `*`, `_`, `` ` ``, `[`, `]`, `(`, `)` are not in the escape set so the substitutions still match). Added `_sanitizeUrl()` that rewrites `javascript:`, `data:`, and `vbscript:` URL schemes (case-insensitive, leading-whitespace tolerant) to `#` in link targets, closing the `[click](javascript:alert(1))` attack surface. 11 JSDOM regression cases in `tests/js/markdown_textarea_xss.test.js` cover `<script>` / `<img onerror>` / `<b>` escaping in headings / paragraphs / lists, preserved `**bold**` / `*italic*` / `` `code` `` functionality, `javascript:` / `data:` / `VBScript:` URL rewriting, safe `https://` and relative URLs preserved, and fenced-code-block escaping still works. (`python/djust/components/static/djust_components/markdown-textarea.js`)

- **Service worker `postMessage` same-origin source check — closes 1 CodeQL `js/missing-origin-check` alert (#2106, warning)** — `python/djust/static/djust/service-worker.js` processed any incoming `message` event without inspecting `event.source`. Service workers are inherently same-origin (they cannot be loaded cross-origin, so `postMessage` from a cross-origin page can't reach the SW), but defense-in-depth: a compromised same-origin frame outside the SW scope could still reach the handler. **Fix**: two-layer gate before touching `event.data` — (1) reject messages whose `event.source` is missing or whose `event.source.type` is not `'window'` (rejects `worker` / `sharedworker` clients we don't expect), (2) reject WindowClient sources whose `url` doesn't start with `self.registration.scope`. 4 new JSDOM regression cases in `tests/js/service_worker.test.js` (new `describe` block "message origin check") cover no-source rejection, non-WindowClient rejection, out-of-scope URL rejection, and valid-WindowClient acceptance. Existing 12 SW tests unchanged — the pre-existing harness was updated to back-fill `type: 'window'` + a scope-valid `url` on caller-supplied source objects, preserving the exact inputs each test verifies. (`python/djust/static/djust/service-worker.js`)

- **Open-redirect + path-traversal hardening + dismiss `py/clear-text-*` CodeQL false-positives (7 alerts closed/dismissed)** — **Real (3 code fixes, closing 4 alerts):** `python/djust/auth/views.py` `SignupView.get_success_url` accepted any `next` POST param and passed it straight to `redirect()`, so a crafted form post could bounce newly-authenticated users to an attacker-controlled host — fixed by validating with Django's `url_has_allowed_host_and_scheme()` against the current request host (with `require_https=self.request.is_secure()`); off-site, protocol-relative (`//evil.com`), and scheme-different values all fall back to `settings.LOGIN_REDIRECT_URL`. `python/djust/admin_ext/views.py:admin_login_required` interpolated `request.path` directly into the login-redirect query string (`?next=<path>`), letting a path containing `&` / `#` / encoded control chars smuggle extra query params into the redirect — fixed with `urllib.parse.urlencode({"next": request.path})`. `python/djust/theming/gallery/storybook.py:get_component_template_source` joined an HTTP-accessible `component_name` URL kwarg into `_COMPONENTS_DIR / f"{name}.html"` with no validation — fixed with an allowlist regex `^[a-z0-9_-]+$` plus a resolved-path-under-base check so traversal payloads (`../../../etc/passwd`, `../secret`, `foo/bar`) return `""` instead of reading outside the components directory. **False-positives (4 dismissed):** `py/clear-text-storage-sensitive-data` + `py/clear-text-logging` alerts trace taint from `MEDICAL_THEME` / `LEGAL_THEME` constant imports in `theming/presets.py` — CodeQL's healthcare-PII heuristic matches the word "medical" / "legal" as identifiers, but the tainted values are CSS theme names (palette tokens, radii, font stacks), not healthcare or legal data. Dismissed on GitHub with "won't fix" and justification. 5 regression cases in `python/djust/tests/test_security_redirects_paths.py` cover off-site / same-site / protocol-relative redirect outcomes plus path-traversal rejection and known-valid component name round-trip. (`python/djust/auth/views.py`, `python/djust/admin_ext/views.py`, `python/djust/theming/gallery/storybook.py`)

- **Drop exception messages from API error responses — closes 8-10 CodeQL `py/stack-trace-exposure` alerts** — Stack traces and exception messages can reveal internal file paths, local variable names, DB schema details, and dependency versions, giving attackers a head-start on probing. Three call sites were rewritten to return generic messages and log the full traceback server-side via `logger.exception()` instead of echoing `str(e)` / `type(e).__name__: {e}` back in the JSON response body. **`python/djust/theming/inspector.py`** (3 sites at `theme_inspector_api` GET/POST + `theme_css_api`) — these endpoints are publicly accessible with no access gating, so this is real prod exposure. **`python/djust/observability/views.py`** (4 sites at `reset_view_state` mount failure, `eval_handler` invalid-JSON body, `eval_handler` TypeError, `eval_handler` catch-all) — DEBUG-gated dev tools, but CodeQL still flags the response content; consistent generic-message pattern closes the alerts and the full trace is still captured in the standard log stream. **`python/djust/api/dispatch.py:384`** — the `serialize_error` path's `str(exc)` dropped in favor of the same generic message the sibling `"handler_error"` / catch-all `"serialize_error"` branches already use. Added `logger = logging.getLogger(__name__)` to the two files that lacked one. 3 regression cases in `python/djust/tests/test_stack_trace_exposure.py` verify the sentinel exception message is not reflected in the response body. Two alerts on `python/djust/components/gallery/views.py:726,762` share the reflective-XSS cookie-flow surface cleared by PR #918 and may auto-close on rescan; if they don't, dismiss-with-justification is appropriate (allowlist-validated values, `escape()` already applied). (`python/djust/theming/inspector.py`, `python/djust/observability/views.py`, `python/djust/api/dispatch.py`)

- **Escape user input in gallery 404 responses & theme option fragments — closes 6 CodeQL `py/reflective-xss` alerts (error severity)** — Three real reflective-XSS sites in `python/djust/theming/gallery/views.py` (lines 276, 281, 306): `storybook_detail_view` and `storybook_category_view` echoed the user-controlled URL kwargs `component_name` / `category` into `HttpResponseNotFound(f"Unknown ...: {value}")` with `Content-Type: text/html`, so a visitor hitting `/storybook/<script>alert(1)</script>/` got the raw payload reflected in the 404 body. Fix: wrap the interpolations with `django.utils.html.escape()`. Three defense-in-depth sites in `python/djust/components/gallery/views.py` (lines 677, 726, 762 via `_resolve_theme`): cookie values (`gallery_ds`, `gallery_preset`) flow through an allowlist validator *before* being interpolated into `<option>` fragments, so the genuine attack surface is zero — but CodeQL's taint analyzer doesn't recognize the allowlist pattern. Added `escape()` on the cookie-derived values' HTML interpolation sites; on validated input this is a no-op (allowlist values are plain ASCII identifiers), and it clears the taint flag for the static analyzer. 4 regression cases in `python/djust/tests/test_gallery_xss.py` cover both the real-XSS 404 body escaping and the allowlist + escape behavior for malicious cookie values. (`python/djust/theming/gallery/views.py`, `python/djust/components/gallery/views.py`)

- **Sanitize user-controlled values in log calls — closes 9 CodeQL `py/log-injection` alerts** — Added `djust._log_utils.sanitize_for_log()`: strips CR/LF/TAB/control chars, replaces with `?`, truncates to 200 chars, always returns a string (None / non-string inputs become their `repr`). Applied at 5 call sites in `python/djust/api/dispatch.py` (wrapping `view_slug`, `handler_name`) and `python/djust/theming/gallery/component_registry.py` (wrapping `component_name`, `str(exc)`) — the sites where HTTP request data flows into `logger.exception` / `logger.debug` calls. Format strings unchanged; djust already uses `%s`-style lazy logging per CLAUDE.md. 8 unit tests in `python/djust/tests/test_log_sanitization.py`. No behavior change for non-malicious input.

- **Refresh `uv.lock` to pull in CVE-fix versions for 8 packages** — Addresses 23 open Dependabot alerts (13 unique CVEs). Bumps: Django 4.2.29 → 5.2.13 (CVE floor 4.2.30; tightened `pyproject.toml` ceiling to `<6` to keep the major-version jump out of a security-only PR), cryptography 46.0.5 → 46.0.7 (buffer overflow + DNS name constraints), orjson 3.11.5 → 3.11.8 (deep-recursion DoS, floor 3.11.6), requests 2.32.5 → 2.33.1 (insecure temp-file reuse, floor 2.33.0), Pygments 2.19.2 → 2.20.0 (GUID-matching ReDoS), pytest 8.4.2 → 9.0.3 (tmpdir vulnerability), black 25.11.0 → 26.3.1 (arbitrary file writes from unsanitized cache input, dev-only), python-dotenv 1.2.1 → 1.2.2 (symlink following in `set_key`). Full Python test suite passes (3428 cases); full JS suite passes (1264 cases). No app code or test changes; lockfile + `pyproject.toml` Django ceiling only. Also catches `Cargo.lock` up to the v0.5.5rc1 crate versions (stale at 0.5.3rc1 on origin/main).

### Changed

- **Drop `black` dev dependency; `ruff format` is now the canonical formatter** — Pre-commit config has used `ruff` + `ruff-format` hooks since v0.5.x; no `Makefile` / CI / import site references black. Removed `black>=24.10.0` / `black>=26.3.1` from the `dev` group in `pyproject.toml` and the `[tool.black]` config section. Ruff already has matching `line-length = 100` and `target-version = "py39"`. Permanently closes the Dependabot `black` CVE alert on the Python 3.9 resolution train (black 26.x dropped 3.9 so that alert couldn't be patched; dropping black removes the surface entirely).

## [0.5.4rc1] - 2026-04-22

### Fixed

- **`PresenceMixin` + `NotificationMixin` — side-effect replay on WS state restoration (#893, #894)** — Sibling bugs to #889, found via audit after the `UploadMixin` fix shipped in #891. Both issues share the same root cause: a mixin's `mount()`-called method has a process-wide side effect beyond setting instance attrs, and the WS consumer's state-restoration path (which skips `mount()`) never re-issues the side effect. **#893 (Presence)**: `track_presence()` calls `PresenceManager.join_presence(...)` as a per-process singleton registration; after restore, the restored user's presence is invisible to other users and `handle_presence_join` doesn't fire for the user's own join. **#894 (Notifications)**: `listen(channel)` calls `PostgresNotifyListener.instance().ensure_listening(channel)` which issues the Postgres `LISTEN channel` SQL statement on the current process; after a cross-process restore (server restart between HTTP and WS, sticky-session LB routing WS to a different worker, worker reshuffle under load) the destination process's listener has no subscriptions, so NOTIFYs never reach the restored view. **Fix** (mirrors PR #891): `PresenceMixin._restore_presence()` replays `join_presence` when `_presence_tracked=True`; `NotificationMixin._restore_listen_channels()` replays `ensure_listening` per channel (both convergent under replay — `PresenceManager.join_presence` overwrites the existing record with identical data so repeated calls are a no-op in effect; `ensure_listening` explicitly early-returns on known channels); WS consumer's state-restoration path calls both right after `_restore_private_state()`, alongside the existing `_restore_upload_configs()` call. All three methods are defensive: missing attributes / backend errors / per-item failures are logged at WARNING and swallowed — restoration must never kill the WebSocket. 11 regression cases in `tests/unit/test_mixin_restoration_893_894.py` cover both mixins' happy paths, no-op guards (not-tracked, missing user_id, empty channel set, missing attribute), exception handling (backend exception, per-channel failure, postgres unavailable), and an end-to-end session-round-trip test. (`python/djust/presence.py`, `python/djust/mixins/notifications.py`, `python/djust/websocket.py`)

- **`UploadMixin` — uploads broken after HTTP→WS state restoration (#889)** — Production-critical bug affecting every app using `UploadMixin` with the default pre-rendered HTTP→WS flow. The WS consumer's state-restoration path (`websocket.py:1540-1572`) skips `mount()` when pre-rendered session state exists, and the live `UploadManager` instance isn't JSON-serializable — so `_upload_manager` silently dropped by `_get_private_state()`, never restored, and any upload request hit `_handle_upload_register` with `"No uploads configured for this view"`. Fix: `allow_upload()` now also records each call as a JSON-serializable dict in `self._upload_configs_saved` (list of kwarg dicts with primitive values); the new `UploadMixin._restore_upload_configs()` method replays the saved calls; the WS consumer calls it at the end of the state-restoration path (right after `_restore_private_state`). Result: restored views behave identically to fresh-mount views. **Caveat**: `allow_upload(writer=CustomWriterClass)` — the writer class itself still can't round-trip through JSON; a warning is logged at replay time and the config falls back to the default buffered writer. Apps that rely on custom writers with session restoration need a follow-up design (out of scope for this fix). 10 regression cases in `tests/unit/test_upload_restoration_889.py` cover: call-list recording, writer-marker flag, multi-slot tracking, JSON round-trip survival, manager rebuild from the list, no-op on empty / missing list, idempotency across repeated restores, writer-fallback warning, and a full HTTP→session→WS-restore end-to-end scenario. (`python/djust/uploads.py`, `python/djust/websocket.py`)

### Added

- **`dj-transition` — declarative CSS enter/leave transitions (v0.6.0)** — Phoenix `JS.transition` parity. Three-phase class orchestration so template authors can drive CSS transitions without writing a `dj-hook`. Attribute value is three space-separated class tokens — phase 1 (start) applied synchronously, phases 2 (active) + 3 (end) applied on the next animation frame so the browser commits the start layout before the transition begins. `transitionend` removes the active class (phase 3 stays as the final-state). 600 ms fallback timeout covers the `display: none` / zero-duration corner cases where `transitionend` never fires. Any attribute-value change re-runs the sequence so authors can retrigger from JS. New `static/djust/src/41-dj-transition.js` (~120 LOC); document-level MutationObserver matches the `dj-dialog` / `dj-mutation` / `dj-sticky-scroll` registration pattern. 7 JSDOM cases in `tests/js/dj_transition.test.js` cover spec parsing, phase-1 synchronous application, next-frame phase-2/3 application, transitionend cleanup, fallback-timeout cleanup, global export, and re-trigger-on-attribute-change. This is phase 1 of the v0.6.0 Animations & transitions work; FLIP, `dj-remove`, `dj-transition-group`, and skeleton components will ship as separate follow-ups. (`python/djust/static/djust/src/41-dj-transition.js`)

  See `docs/website/guides/declarative-ux-attrs.md`.
## [0.5.3rc1] - 2026-04-22

### Added

- **Runtime layout switching — `self.set_layout(path)` (v0.6.0)** — Phoenix 1.1 parity. An event handler can swap the surrounding layout template (nav, sidebar, footer, wrapper markup) without a full page reload: inner state — form values, scroll position, focused element, `dj-hook` bookkeeping, third-party-widget references — is fully preserved because the live `[dj-root]` element is physically moved from the current body into the new layout rather than re-created. Server side: new `LayoutMixin` in `python/djust/mixins/layout.py` composed into the `LiveView` base, queuing at most one pending path (last-write-wins). WebSocket consumer: new `_flush_pending_layout()` wired at all nine `_flush_page_metadata` call sites; renders the layout template with the view's current `get_context_data()` and emits a `{"type": "layout", "path": ..., "html": ...}` frame. Graceful degradation: `TemplateDoesNotExist` or any render exception logs a warning and leaves the WS intact. Client side: new `static/djust/src/40-dj-layout.js` module registered for the `layout` WS frame — finds the `[dj-root]` / `[data-djust-root]` inside the incoming HTML, splices in the live root node, swaps `document.body`, and fires a `djust:layout-changed` CustomEvent on `document`. Handles missing-root payloads and empty HTML gracefully. Tests: 12 Python cases in `tests/unit/test_layout_switching.py` (mixin, consumer emit/noop/missing-template/no-mixin/view-none, LiveView composition) + 6 JSDOM cases in `tests/js/dj_layout.test.js` (root-identity preservation, CustomEvent dispatch, malformed-payload refusal, empty-html noop, `[dj-root]` fallback, global export). Full user guide at `docs/website/guides/layouts.md` (linked from `_config.yaml` and `index.md`). Known limitation: `<head>` tags are not merged — if a layout needs new stylesheets, add them to the initial layout's `<head>`. (`python/djust/mixins/layout.py`, `python/djust/mixins/__init__.py`, `python/djust/live_view.py`, `python/djust/websocket.py`, `python/djust/static/djust/src/03-websocket.js`, `python/djust/static/djust/src/40-dj-layout.js`)

## [0.5.2rc1] - 2026-04-22

### Added

- **WebSocket per-message compression toggle — `DJUST_WS_COMPRESSION` (v0.6.0)** — VDOM patches compress extremely well (repetitive HTML fragments + JSON structure → 60-80 % wire-size reduction via zlib). Uvicorn and Daphne both negotiate `permessage-deflate` with browsers out of the box, so the wire-level compression is already free in most deployments — this change adds the declarative config toggle + documentation so operators can verify it's active, reason about the ~64 KB per-connection zlib context cost, and disable it cleanly on extreme-connection-density deployments or when running behind a compressing CDN. New `websocket_compression` config key (default `True`) exposed via `djust.config.config`, bridged from a top-level `settings.DJUST_WS_COMPRESSION` for discoverability, and surfaced to the injected client bootstrap as `window.DJUST_WS_COMPRESSION` (application code can branch on it to skip manual `JSON.stringify` optimizations that only help without wire-level compression). 6 tests in `tests/unit/test_ws_compression_config.py` cover default, override to True/False, truthy/falsy coercion, and client-script emission. Deployment guide (`docs/website/guides/deployment.md`) gains a new "WebSocket per-message compression" section covering the memory tradeoff, CDN double-compression footgun, and Uvicorn/Daphne flags. (`python/djust/config.py`, `python/djust/mixins/post_processing.py`)

- **Declarative UX attributes — `dj-mutation`, `dj-sticky-scroll`, `dj-track-static` (v0.6.0)** — Three small client-side declarative attributes that replace boilerplate `dj-hook`s every production app tends to write. **`dj-mutation`** (new `static/djust/src/37-dj-mutation.js`, ~100 LOC) fires a `dj-mutation-fire` CustomEvent when the marked element's attributes or children change via MutationObserver, with `dj-mutation-attr="class,style"` for targeted attribute filters and `dj-mutation-debounce="N"` for burst coalescing (default 150 ms). **`dj-sticky-scroll`** (new `38-dj-sticky-scroll.js`, ~90 LOC) keeps a scrollable container pinned to the bottom when children are appended but backs off when the user scrolls up to read history and resumes when they return to the bottom — the canonical chat / log viewer UX with a 1 px sub-pixel tolerance. **`dj-track-static`** (new `39-dj-track-static.js`, ~90 LOC; Phoenix `phx-track-static` parity) snapshots tracked `<script src>` / `<link href>` values on page load and, on every subsequent `djust:ws-reconnected` event, diffs against the snapshot — dispatches `dj:stale-assets` CustomEvent on changed URLs, or calls `window.location.reload()` when the changed element carried `dj-track-static="reload"`. Without this last one, clients on long-lived WebSocket connections silently run stale JS after a deploy — zero-downtime on the server but broken behavior on connected clients. Supporting change in `03-websocket.js`: `onopen` now dispatches `document.dispatchEvent(new CustomEvent('djust:ws-reconnected'))` on every reconnect so application code (not just `dj-track-static`) can hook reconnects without touching internal WS state. Convenience Django template tag `{% djust_track_static %}` in `live_tags.py` emits the bare attribute for discoverability. All three attributes live-register via a document-level MutationObserver root (same pattern as `dj-dialog`) so VDOM morphs that inject or remove the marker re-wire observers automatically. 15 JSDOM test cases across `tests/js/dj_mutation.test.js`, `tests/js/dj_sticky_scroll.test.js`, `tests/js/dj_track_static.test.js`; 4 Python test cases in `tests/unit/test_djust_track_static_tag.py`. (`python/djust/static/djust/src/37-dj-mutation.js`, `38-dj-sticky-scroll.js`, `39-dj-track-static.js`, `03-websocket.js`, `python/djust/templatetags/live_tags.py`)

  See `docs/website/guides/declarative-ux-attrs.md`.
- **`djust.db.untrack(model)` — disconnect signal receivers wired by `@notify_on_save` (#809)** — Previously the only way to detach the `post_save` / `post_delete` receivers from a `@notify_on_save`-decorated model was to clear the entire `signals.receivers` list, which scorched unrelated test fixtures. `untrack()` now disconnects exactly the two receivers stashed on `model._djust_notify_receivers` and wipes the introspection attributes (`_djust_notify_channel`, `_djust_notify_receivers`) so a re-decoration goes through cleanly with a fresh channel. Returns `True` on success, `False` on a never-decorated model — idempotent, safe to call twice. Primarily for pytest teardowns in projects that decorate models at class-definition time. 5 tests in `tests/unit/test_db_notifications.py::TestUntrack`. Exported from `djust.db` and documented in the `djust.db` module docstring. (`python/djust/db/decorators.py`, `python/djust/db/__init__.py`)

  See `docs/website/guides/database-notifications.md`.
- **Pre-minified `client.js` distribution (v0.6.0 P1)** — Production now serves `client.min.js` (terser-minified) instead of the 35-module readable concat, with `.gz` and `.br` pre-compressed siblings built alongside it for whitenoise / nginx static serving. Measured impact: `client.js` 410 KB → `client.min.js` 146 KB raw → 39 KB gzip → 33 KB brotli (~92% reduction wire-size over the raw file). `DEBUG=True` continues to serve the readable `client.js` so stack traces point at meaningful line numbers and contributors can poke at source directly. An explicit `DJUST_CLIENT_JS_MINIFIED` setting (bool) overrides the `DEBUG` heuristic in either direction so operators can validate the minified file locally or keep the readable build in production if they want to debug in-situ. `scripts/build-client.sh` gained a `minify_and_compress` helper that runs terser (from `node_modules/.bin/terser` or PATH), then gzip `-9` and brotli `-q 11`; the step is skipped gracefully when terser isn't installed so contributors can still iterate on raw sources without `npm install`. Source-maps (`.min.js.map`) are emitted for production-side debugging. `djust.C012` system check now recognizes both `client.js` and `client.min.js` in manual-loading detection. 6 tests in `tests/unit/test_client_minified.py` cover build-artifact presence + size reduction, DEBUG-vs-production script selection, and the explicit override in both directions. (`scripts/build-client.sh`, `python/djust/mixins/post_processing.py`, `python/djust/checks.py`, `package.json`)

### Changed

- **Documented block-handler nesting + loader-access constraints (#803, #804)** — Two low-priority gaps deferred from PR #802 are now surfaced in both the Rust-side `register_block_tag_handler` docstring (`crates/djust_templates/src/registry.rs`) and the Python-side `.pyi` stub (`python/djust/_rust.pyi`). The "no parent-tag propagation" constraint (#804) means a nested block handler is not informed it sits inside a parent handler — pass a hint through `context` instead. The "no loader access from handlers" constraint (#803) means block handlers cannot call `{% render_template %}`-style loads — pre-render child templates in the view. Both constraints were silently-true before this change; surfacing them prevents surprise when handler authors reach for features the current dispatcher doesn't yet support. No runtime behavior change. (`crates/djust_templates/src/registry.rs`, `python/djust/_rust.pyi`)

### Fixed

- **`assign_async` concurrent same-name cancellation semantics (#793)** — Two rapid `assign_async("metrics", loader)` calls used to race: the first loader's worker thread could still be in-flight when the second call scheduled a new task, and when the slow loader finally completed, its `setattr(self, "metrics", AsyncResult.succeeded(stale))` clobbered the fresh `AsyncResult.pending()` that the second call had just written. `assign_async()` now maintains a per-attribute generation counter (`self._assign_async_gens[name]`) bumped on every call; each loader's runner closure captures the generation at creation time and short-circuits on both the success and error paths when a newer call has superseded it. The in-flight stale runner still completes (no mid-flight cancellation), but its result is discarded via a DEBUG log — the fresh pending state survives. 4 regression cases in `tests/unit/test_assign_async.py`: sync success-path, sync error-path, async-loader success-path, and a generation-counter sanity check. (`python/djust/mixins/async_work.py`)

- **Template dep-tracking: filter-arg bare identifiers (#787)** — `{{ value|default:fallback }}` now tracks `fallback` as a template dependency alongside `value`. Previously the dep-extractor walked filter chains but dropped all filter arguments, so a pattern like `{% if show %}{{ value|default:dynamic }}{% endif %}` would fail to re-render when only `dynamic` changed — the render cache classified the node as dep-clean and the partial-render pipeline skipped it. Literal filter args (`default:"none"`, `default:'none'`, `default:0`, `default:-1`) are correctly excluded from the dep set; only bare identifiers and dotted paths are tracked. Landed via a two-step: `parse_filter_specs` now preserves surrounding quotes on literal args so the extractor can distinguish literals from identifiers, and render-time filter application strips quotes via the new `strip_filter_arg_quotes` helper. No change to filter runtime semantics. 15 regression cases in `tests/unit/test_template_dep_tracking_787_806.py`. (`crates/djust_templates/src/parser.rs`, `crates/djust_templates/src/renderer.rs`)
- **Template for-iterables resolve through getattr walk (#806)** — `{% for x in foo.bar %}` now uses `Context::resolve` (which walks getattr through the raw-PyObject sidecar) with a fallback to `Context::get`, instead of only consulting the value-stack. Previously dotted iterables silently rendered as empty when the attribute was not a top-level dict key — affecting Django QuerySet relations (`user.orders`), dataclass attributes, and nested Python objects. Covered by two direct-access tests (nested attributes + relation stub) + existing top-level + empty-block + missing-attr regression tests. (`crates/djust_templates/src/renderer.rs`)

- **`send_pg_notify` payload size guard (#810)** — PostgreSQL caps `NOTIFY` payloads at 8000 bytes. `send_pg_notify()` now warns at 4KB (soft limit) and drops + error-logs at 7500 bytes (hard limit). (`python/djust/db/decorators.py`)
- **`PostgresNotifyListener.areset_for_tests()` awaits task cancellation (#811)** — The existing `reset_for_tests()` fire-and-forget cancel is now documented as such; new async variant awaits the cancelled task so async test teardowns don't race. (`python/djust/db/notifications.py`)
- **`db_notify` render-lock timeout documented (#813)** — 100ms timeout is best-effort under contention; dropped notifications do not queue. (`python/djust/websocket.py`)
- **Regression test: consumer handles views without `NotificationMixin` (#812)** — Locks in that `getattr(view, '_listen_channels', None)` + truthy gate handles both absent-attr and empty-set paths. (`tests/unit/test_db_notifications.py`)
- **`stream()` with `limit=N` pre-trims emitted inserts (#799)** — Server trims `items_list` to at-most `limit` before emitting inserts. (`python/djust/mixins/streams.py`)
- **`teardownVirtualList` restores original children (#798)** — Teardown now restores pre-virtualization children and removes the shell/spacer. (`python/djust/static/djust/src/29-virtual-list.js`)
- **`stream_prune` `.children` filter redundancy removed (#801)** — Cosmetic cleanup. (`python/djust/static/djust/src/17-streaming.js`)
- **`LiveViewTestClient.render_async()` invokes `handle_async_result` (#843)** — Test-client drain now mirrors the production WS consumer. (`python/djust/testing.py`)
- **`LiveViewTestClient.follow_redirect()` refuses to pick silently when multiple redirects queued (#844)** — Raises `AssertionError` with all queued paths. (`python/djust/testing.py`)
- **UploadWriter `close()` return validated as JSON-serializable (#825)** — Non-JSON returns caught at finalize time and abort the upload cleanly. (`python/djust/uploads.py`)
- **BufferedUploadWriter `write_chunk()` after `close()` raises (#823)** — `_finalized` flag now actively enforced; repeated `close()` is idempotent. (`python/djust/uploads.py`)
- **Upload-manager drops trailing chunks silently after abort (#824, partial)** — Fast-path at DEBUG log; `writer.abort()` called once. (`python/djust/uploads.py`)
- **Morph-path honors `dj-ignore-attrs` (#815)** — The VDOM morph loop at `python/djust/static/djust/src/12-vdom-patch.js:746-758` previously stripped and overwrote attributes without consulting `djust.isIgnoredAttr`. Attributes listed in `dj-ignore-attrs` would survive individual `SetAttr` patches (the guard added in PR #814) but could still get wiped during a full-element morph. The morph-path remove-loop and set-loop both now skip ignored attribute names. Two regression tests in `tests/js/ignore_attrs.test.js` cover remove-loop and set-loop preservation. (`python/djust/static/djust/src/12-vdom-patch.js`)

### Changed

- **`dj-ignore-attrs` CSV empty-token hardening (#816)** — `isIgnoredAttr` now skips empty tokens produced by double-comma (`"open,,close"`) or trailing-comma (`"open,"`) CSV values, and rejects empty attribute-name queries. Previously those edge cases could accidentally match an empty attribute name. Four regression tests in `tests/js/ignore_attrs.test.js` cover empty string, whitespace-only, double comma, and trailing comma. (`python/djust/static/djust/src/31-ignore-attrs.js`)

### Added

- **`djust_typecheck` — `{% firstof %}` / `{% cycle %}` / `{% blocktrans with %}` tag support (#850)** — The extractor now captures positional context-variable references in `{% firstof a b c %}` and `{% cycle a b c %}` (string literals and `as <name>` suffixes are correctly ignored), and the `with x=expr` (and `count x=expr`) clauses of `{% blocktrans %}` / `{% blocktranslate %}` produce both the template-local binding (`x`) and the reference (`expr`). Eliminates a class of false positives (blocktrans locals) and false negatives (firstof/cycle args). (`python/djust/management/commands/djust_typecheck.py`)

  See `docs/website/guides/typecheck.md`.
### Changed

- **`djust_typecheck` — walk MRO for parent-class `self.foo = ...` assigns (#851)** — `_extract_context_keys_from_ast` now iterates `cls.__mro__` (skipping `djust.*`, `djust_*`, `django.*`, `rest_framework.*`, and `builtins`), so a child view that relies on attributes set in a parent `mount()` no longer produces spurious "unresolved" reports. The filter drops Django's `View` / namespace-framework attrs (`request`, `head`, `kwargs`, `args`) that would otherwise surface from the base class. (`python/djust/management/commands/djust_typecheck.py`)
- **Shared class-introspection helpers (#852)** — `_walk_subclasses`, `_is_user_class`, and `_app_label_for_class` are now a single source of truth in the new `djust.management._introspect` module; `djust_audit` and `djust_typecheck` both import from it. No behavior change; purely a refactor to prevent drift as the set of management commands grows. `_introspect.walk_subclasses` also gained cycle-safety (diamond-inheritance deduplication) which the old recursive implementation lacked. (`python/djust/management/_introspect.py`, `python/djust/management/commands/djust_audit.py`, `python/djust/management/commands/djust_typecheck.py`)
- **Service worker + main-only middleware follow-ups to PR #826 (closes #827/#828/#829/#830)** —
  - **#828** — `DjustMainOnlyMiddleware` now early-returns on responses with `status_code >= 400`. Error pages render full-page layouts (status message, "go back" link, etc.); trimming them to `<main>` would strip that context from shell-navigation clients. Regression tests cover 4xx and 5xx.
  - **#830** — HTML response detection widened to include `application/xhtml+xml` in addition to `text/html`. Charset and boundary suffixes (`text/html; charset=utf-8; boundary=xyz`) are stripped before matching. Defensive test confirms `application/rss+xml` is still treated as non-HTML.
  - **#829** — `djust.registerServiceWorker()` is now idempotent. A second call returns the cached registration promise without re-running `initInstantShell` / `initReconnectionBridge`, so drain listeners and the WS `sendMessage` patch are applied at most once. Previous behavior caused buffered replays to double on repeat init.
  - **#827** — Documented the `<script>`-inside-`<main>` limitation of the instant-shell `innerHTML` swap at the top of `33-sw-registration.js`. The doc block was also corrected: `dj-click`/`dj-submit`/etc. work through **document-level event delegation** (not MutationObserver), and `dj-hook` now explicitly re-runs via a `djust.reinitAfterDOMUpdate(placeholder)` call after the swap — dj-hook content inside `<main>` actually works post-swap as a result (previous implementation silently skipped hook re-binding).

  Tests: 9 → 13 Python cases in `tests/unit/test_main_only_middleware.py`, +2 JS cases in `tests/js/service_worker.test.js` (12 total). (`python/djust/middleware.py`, `python/djust/static/djust/src/33-sw-registration.js`)

## [0.5.1rc4] - 2026-04-22

### Added

- **Transport-conditional API returns — `api_response()` convention + `@event_handler(expose_api=True, serialize=...)` override (v0.5.1 P2 follow-up to ADR-008)** — Handlers serving both WebSocket and HTTP API callers often have split needs: WS only wants state mutation (VDOM renders the UI), HTTP wants actual data in the response. Serializing query results on every WS keystroke is wasteful. Resolved with three-tier resolution on the HTTP path (zero overhead on WS): (1) per-handler `@event_handler(expose_api=True, serialize=<callable-or-str>)` wins when set; (2) otherwise the view's `api_response(self)` method is called (the DRY convention — one method, many handlers); (3) otherwise the handler's return value passes through unchanged. `serialize=` accepts a callable (arity-detected: `fn()` / `fn(view)` / `fn(view, result)`) or a method-name string resolved against the view at dispatch time. Async serializers and async `api_response()` are both awaited. `serialize=` without `expose_api=True` raises `TypeError` at decoration. Missing method or serializer exception → 500 `serialize_error` (details logged server-side only); `PermissionDenied` raised from either path surfaces as 403 (not 500). The `self._api_request = True` flag is set by dispatch **before `mount()` runs** so mount can branch on transport; it is retained as an escape hatch for code that needs transport awareness without the decorator plumbing. 28 tests in `python/djust/tests/test_api_response.py` cover unit-level resolution (passthrough, convention, per-handler override, arity detection, async paths, MRO-provided api_response, shadowed non-callable api_response, invalid spec types, staticmethod-via-string, callable class instances) and end-to-end dispatch integration (including PermissionDenied surfacing as 403 and the mount-time flag availability). Full guide in `docs/website/guides/http-api.md` under "Transport-conditional returns". (`python/djust/decorators.py`, `python/djust/api/dispatch.py`)

## [0.5.1rc3] - 2026-04-21

### Added

- **LiveView testing utilities (v0.5.1 P2)** — Seven new methods on `LiveViewTestClient` for Phoenix LiveViewTest parity: `assert_push_event(event_name, params=None)` verifies a handler queued a client-bound push event (payload match is subset-based so tests stay resilient to later payload additions); `assert_patch(path=None, params=None)` / `assert_redirect(path=None, params=None)` assert `live_patch` / `live_redirect` calls; `render_async()` drains pending `start_async` / `assign_async` tasks synchronously so subsequent assertions see their results; `follow_redirect()` resolves the queued redirect via Django's URL router and returns a new test client mounted on the destination view; `assert_stream_insert(stream_name, item=None)` verifies stream operations (item subset-match for dicts); `trigger_info(message)` synthetically delivers a `handle_info` message so pubsub / pg_notify handlers can be tested without real backend wiring. Full user-facing guide at `docs/website/guides/testing.md`. 21 new test cases. (`python/djust/testing.py`)
- **`dj-dialog` — native `<dialog>` modal integration (v0.5.1 P2)** — Declarative opt-in for the HTML `<dialog>` element's built-in modal behavior. Mark a `<dialog>` with `dj-dialog="open"` to call `showModal()` (backdrop, focus-trap, and Escape handling all browser-native); set `dj-dialog="close"` to call `close()`. A document-level `MutationObserver` watches for attribute changes and DOM insertions so VDOM morphs that swap `dj-dialog` work automatically without per-element re-registration. Idempotent — re-asserting `"open"` on an already-open dialog is a no-op; gracefully ignores non-`<dialog>` elements carrying the attribute. ~80 LOC JS in `python/djust/static/djust/src/35-dj-dialog.js`. 8 JSDOM tests in `tests/js/dj_dialog.test.js`.
- **Type-safe template validation — `manage.py djust_typecheck` (v0.5.1 P2, differentiator)** — Static analysis that reads every LiveView template, extracts every variable and tag reference, and reports names not covered by the view's declared context. "Declared context" is the union of public class attributes, `self.foo = ...` assignments anywhere in the class (AST-extracted — not run), `@property` methods, literal-dict keys returned from `get_context_data`, template-local bindings (`{% for %}` / `{% with %}` / `{% inputs_for as %}`), framework built-ins (`user`, `request`, `csrf_token`, `forloop`, `djust`, etc.), and anything listed in `settings.DJUST_TEMPLATE_GLOBALS`. Silencing: per-template pragma (`{# djust_typecheck: noqa name1, name2 #}`), per-view `strict_context = True` opt-in, or the project-wide globals setting. Flags: `--json`, `--strict`, `--app`, `--view`. Neither Phoenix nor React catches template-variable typos statically without an external type system — this is a genuine djust differentiator. 14 tests in `python/djust/tests/test_djust_typecheck.py`. Full guide at `docs/website/guides/typecheck.md`. (`python/djust/management/commands/djust_typecheck.py`)
- **Dev-mode error overlay (v0.5.1 P2)** — Next.js/Vite-style full-screen error panel that renders in the browser whenever a LiveView handler raises an exception and Django `DEBUG=True`. Displays the error message, the event that triggered the handler, the server-sent Python traceback, an optional hint, and validation details when present. Dismissal: Escape key, close button, or backdrop click. A second error replaces the current panel rather than stacking. All field values HTML-escaped to prevent traceback injection. Gated on `window.DEBUG_MODE` — production builds render nothing (Django also strips `traceback` / `debug_detail` / `hint` from the error frame in non-DEBUG mode, so there's nothing to leak). Exposes `window.djustErrorOverlay.show(detail)` / `.dismiss()` for manual invocation from devtools. 10 JSDOM tests in `tests/js/error_overlay.test.js`. Full guide at `docs/website/guides/error-overlay.md`. (`python/djust/static/djust/src/36-error-overlay.js`)
- **Nested formset helpers — `{% inputs_for %}` + `FormSetHelpersMixin` (v0.5.1 P2)** — djust-native support for Django formset / inline-formset patterns. Template side: `{% inputs_for formset as form %}...{% endinputs_for %}` iterates any `BaseFormSet` and exposes each bound child form with its per-row prefix intact so rendered inputs submit under the correct Django-expected names; loop metadata (`inputs_for_loop.counter`, `.counter0`, `.first`, `.last`) mirrors the `{% for %}` conventions. Server side: `djust.formsets.add_row(cls, data=..., prefix=...)` and `remove_row(cls, row_prefix, data=..., prefix=...)` handle management-form bookkeeping — `add_row` bumps `TOTAL_FORMS` (capped at `max_num` when set, `absolute_max` otherwise) and preserves existing row data; `remove_row` writes the standard `DELETE=on` flag so `formset.deleted_forms` picks it up on `save()`. `FormSetHelpersMixin` wires pre-baked `add_row` / `remove_row` event handlers to a `formset_classes = {"addresses": AddressFormSet}` declaration, with the formset name doubling as the prefix so multiple formsets on one view don't collide on management-form keys. Fails loud if `mount()` forgets to initialize `self._formset_data`. 16 tests in `python/djust/tests/test_formsets.py`. (`python/djust/formsets.py`, `python/djust/templatetags/djust_formsets.py`)

## [0.5.1rc2] - 2026-04-21

### Added

- **Scaffold CSS — reusable layout/utility pack in `djust.theming`** — `djust_theming/static/djust_theming/css/scaffold.css` gains ~729 lines of framework-generic scaffold covering typography, responsive grid utilities (`.grid-2/3/4`), hero section, flash messages (Django + LiveView), accessibility utilities (`.sr-only`), extended layout helpers (`.flex-center`, `.content-narrow/-wide`), stat-display variants, auth layout, live indicator dot, card-accent variants, code blocks, noise texture overlay, shared nav links, dashboard/centered grids, and the full `data-layout` switching system (sidebar, topbar, dashboard, centered, sidebar-topbar). All new rules use CSS-variable fallbacks so the scaffold works without a loaded theme; no hardcoded hex colors; `.container` max-width now reads `var(--container-width, 1200px)`. Pure-CSS addition — no Python/JS/test behavior changes. (PR #836)

  See `docs/website/guides/migration-from-standalone-packages.md`.
### Fixed

- **All 82 pre-existing test failures resolved (PR #841)** — The `make test` baseline went from `2135 passed, 61 failed, 21 errors` (which had blocked normal merges for the entire v0.5.1 milestone and forced `--admin` on every PR) to `2219 passed, 0 failed, 0 errors`. Four fix clusters:
  - **Test-infrastructure shims** (64 fixes) — added `tests/gallery_test_urls.py` and `tests/test_critical_css.py` URL-conf shims that theming tests reference via `@override_settings(ROOT_URLCONF=...)` but were never created; added `mcp[cli]>=1.2.0; python_version >= '3.10'` to dev deps so `djust.mcp` server tests stop throwing `ModuleNotFoundError`.
  - **Stale `@layer` test expectations** (4 fixes) — several theming CSS files (`components.css`, `layouts.css`, `pages.css`, critical-CSS generator) were intentionally unwrapped from `@layer` blocks for specificity reasons (documented in file headers); updated tests to match the current design using `@layer NAME {` block-syntax regex rather than substring match.
  - **Real code bugs** (3 fixes) — `ocean_deep` preset's internal `name` was `"ocean"` while its registry key was `"ocean_deep"`; one stray `text-align: left` in `components.css` `.tp-select-option` broke RTL support (changed to `text-align: start`); and the CSS prefix generator's hand-maintained `_COMPONENT_CLASSES` list had drifted from `components.css` — `.btn-edit`, `.btn-remove`, `.avatar`, `.breadcrumb`, `.dropdown` and many more weren't being prefixed when a custom `css_prefix` was set. Replaced with auto-extraction via regex over the static file; stays in sync automatically.
  - **Stale test assumption** (1 fix) — `test_list_same_content_no_render` encoded a wrong assumption about `_snapshot_assigns` (identity-based by design); rewrote to match the documented contract.
- **CSS prefix generator hardening** — Auto-extraction regex gained a negative lookbehind `(?<![\w])` to prevent capturing domain fragments inside data-URIs (previously `.org` and `.w3` in `http://www.w3.org/2000/svg` were mis-captured as class selectors, producing `http://www.dj-w3.dj-org/2000/svg` under prefix); compound state-class chains like `.wizard-step.completed` now correctly leave the trailing state class unprefixed (JS toggles state classes by bare name, so they must NOT get the prefix). Two new regression tests (`test_data_uri_domains_are_not_mis_prefixed`, `test_compound_state_classes_stay_unprefixed`) lock both in.

### Changed

- **ROADMAP.md audit correction** — Five entries marked as "v0.5.1 Not started" were actually shipped earlier: djust-theming fold (v0.5.0 PR #772), WizardMixin (PR #632), Error boundaries (v0.5.0 PR #773), and `dj-lazy` lazy LiveView hydration (PR #54). All marked with strikethrough + ✅ and a shipped-in PR pointer. Real v0.5.1 remainder after audit: LiveView testing utilities, type-safe template validation, error overlay, `inputs_for` nested formsets, native `<dialog>` (5 items instead of 8).

## [0.5.1rc1] - 2026-04-21

### Added

- **Form & submit polish batch (v0.5.1 P2)** — Three related form-UX primitives:
  - **`dj-no-submit="enter"`** — Prevent Enter-key form submission from text inputs. Fixes the #1 form UX annoyance where pressing Enter to confirm a field accidentally submits the whole form. Textareas (multi-line input), submit-button clicks, and modified keys (Shift+Enter, Ctrl+Enter) are unaffected. Supports comma-separated modes (currently only `"enter"`) for future expansion. Document-level keydown listener — DOM morphs don't need re-registration. (`python/djust/static/djust/src/34-form-polish.js`)
  - **`dj-trigger-action` + `self.trigger_submit(selector)`** — Bridge successful djust validation to a native HTML form POST. Essential for OAuth redirects, payment gateway handoffs, and anywhere the final step needs a real browser POST. The server calls `self.trigger_submit("#form-id")` after validation passes; the client receives the push event, verifies the target form carries `dj-trigger-action` (explicit opt-in — refusal is logged in debug mode), and calls the form's native `.submit()`. (`python/djust/mixins/push_events.py`, `python/djust/static/djust/src/34-form-polish.js`)
  - **`dj-loading="event_name"` shorthand** — Declarative scoped loading indicator: `<div dj-loading="search">Searching...</div>` shows only while the `search` event is in-flight. Previously required combining `dj-loading.show` + `dj-loading.for="event_name"` with an inline `style="display:none"`. The shorthand auto-hides the element on register (no inline style required) and treats the attribute value as both the event-scope and the implicit `.show` trigger. Coexists with the existing `dj-loading.*` modifier family. (`python/djust/static/djust/src/10-loading-states.js`)

  Tests: 11 JS test cases in `tests/js/form_polish.test.js` covering every happy path and failure mode; 4 Python tests in `python/djust/tests/test_trigger_submit.py` locking in the push-event shape. Client.js: 35 → 36 source modules (+~120 LOC JS, +~30 LOC Python). Scoped scoped-loading `dj-loading="event"` implementation reuses existing `globalLoadingManager` infrastructure — no duplication.

- **State & computation primitives batch (v0.5.1 P2)** — Four small related primitives for derived state, dirty tracking, stable IDs, and cross-component context sharing:
  - **Memoized `@computed("dep1", "dep2")`** — `@computed` now accepts an optional tuple of dependency attribute names. When given, the value is cached on the instance and only recomputed when any dep's identity or shallow content fingerprint changes (id + length + key subset matching ``_snapshot_assigns`` semantics). Plain `@computed` (no args) retains property semantics — recomputes every access. React `useMemo` equivalent. (`python/djust/decorators.py`)
  - **Automatic dirty tracking — `self.is_dirty` / `self.changed_fields` / `self.mark_clean()`** — Track which public view attributes have changed since a baseline captured after `mount()`. `changed_fields` returns a set of attr names that differ from the baseline; `is_dirty` is `bool(changed_fields)`; `mark_clean()` resets the baseline (call after a successful save). Use cases: "unsaved changes" warnings (`beforeunload`), conditional save buttons, optimized `handle_event` that skips work when nothing changed. Respects `static_assigns` and ignores private attrs. The WebSocket consumer and the HTTP API dispatch view both capture the baseline after mount. (`python/djust/live_view.py`, `python/djust/websocket.py`, `python/djust/api/dispatch.py`)
  - **Stable `self.unique_id(suffix="")`** — React 19 `useId` equivalent. Returns a deterministic per-view ID stable across renders of the same logical position. Useful for `aria-labelledby`, form field IDs, and any element that needs a consistent identifier across re-renders. Format: `djust-<viewslug>-<n>[-<suffix>]`. Counter resets via `reset_unique_ids()` at render boundaries. (`python/djust/live_view.py`)
  - **Component context sharing — `self.provide_context(key, value)` / `self.consume_context(key, default=None)`** — React Context API equivalent. A parent view or component exposes a value under `key`; descendants look it up with `consume_context`, walking the `_djust_context_parent` chain. Scoped per render tree; `clear_context_providers()` resets. (`python/djust/live_view.py`)
  See `docs/website/guides/state-primitives.md`.
- **Auto-generated HTTP API from `@event_handler` (v0.5.1 P1 HEADLINE, [ADR-008](docs/adr/008-auto-generated-http-api-from-event-handlers.md))** — Opt-in `@event_handler(expose_api=True)` exposes a handler at `POST /djust/api/<view_slug>/<handler_name>/` with an auto-generated OpenAPI 3.1 schema served at `/djust/api/openapi.json`. Unlocks non-browser callers (mobile, S2S, CLI, AI agents) without duplicating business logic — the HTTP transport is a thin adapter over the existing handler pipeline, reusing `validate_handler_params()`, `check_view_auth()`, `check_handler_permission()`, and the same `_snapshot_assigns()` / `_compute_changed_keys()` diff machinery the WebSocket path uses. One stack, one truth (manifesto #4). New package `djust.api` with `DjustAPIDispatchView` (dispatch view), `api_patterns()` (URL factory), `OpenAPISchemaView` (schema endpoint), `SessionAuth` + pluggable `BaseAuth` protocol (auth classes may opt out of CSRF via `csrf_exempt = True`), and a registry that walks `LiveView` subclasses with exposed handlers. `LiveView` gains two read-only contract attributes: `api_name` (stable URL slug) and `api_auth_classes` (auth class list). Response shape mirrors the WS assigns-diff: `{"result": <return>, "assigns": {<changed public attrs>}}`. Error shapes are structured with `error` / `message` / `details` — 400 validation, 401 unauth, 403 denied or CSRF fail, 404 unknown view/handler or handler not `expose_api=True`, 429 rate limit, 500 handler exception (exception messages logged server-side only, never leaked to the client). **Rate limiting:** HTTP uses a process-level LRU-capped token bucket keyed on `(caller, handler_name)` honoring the handler's `@rate_limit` settings; WebSocket continues to use its per-connection `ConnectionRateLimiter`. The two transports share rate/burst values but separate bucket storage — a caller using both draws from both independently (a shared-bucket refactor is tracked as a follow-up). `manage.py djust_audit` now lists every `expose_api=True` handler and flags any missing `@permission_required` — treat an exposed handler like `@csrf_exempt`. Out of scope per ADR-008: streaming responses, GraphQL batching, first-party token auth, Swagger UI hosting, per-handler URL customization. Full guide at `docs/website/guides/http-api.md`. (`python/djust/api/`, `python/djust/decorators.py`, `python/djust/live_view.py`, `python/djust/management/commands/djust_audit.py`)
- **Service worker core improvements — instant page shell + WebSocket reconnection bridge (v0.5.0 P3, opt-in)** — Two independent SW features that close the v0.5.0 milestone. Both are OFF by default; users opt in explicitly via `djust.registerServiceWorker({ instantShell: true, reconnectionBridge: true })` from their own init code. No auto-registration.
  - **Instant page shell.** The SW caches the first navigation's response split into a "shell" (everything outside `<main>`) and "main" (inside). Subsequent navigations serve the cached shell immediately with a `<main data-djust-shell-placeholder="1">` placeholder; the client then fetches the current URL with `X-Djust-Main-Only: 1` and swaps in the fresh `<main>` contents. Shell/main split uses a single non-greedy regex — nested `<main>` inside HTML comments or `</main>` inside `CDATA` are documented limitations (full HTML parser deferred). Server side honors the header via the new `djust.middleware.DjustMainOnlyMiddleware`, which extracts the first `<main>…</main>` inner HTML, updates `Content-Length`, and stamps `X-Djust-Main-Only-Response: 1`. The middleware only touches HTML responses; JSON / binary / streaming responses pass through unchanged. Ordering-safe — it can sit anywhere in `MIDDLEWARE` that sees the rendered response.
  - **WebSocket reconnection bridge.** Client-side wraps `LiveViewWebSocket.sendMessage` so that when `ws.readyState !== OPEN` the serialized payload is posted to the SW via `postMessage({type: 'DJUST_BUFFER', connectionId, payload})` instead of being dropped. The SW stores messages in an in-memory `Map` keyed by connection id, capped at 50 per connection (oldest dropped). On reconnect the client fires `DJUST_DRAIN`; the SW returns the buffered payloads and the client replays each via `ws.ws.send()`. Per-page-load connection ids isolate buffers across tabs. IndexedDB persistence and server-side sequence-dedup replay are deferred to v0.6 (best-effort replay today).
  - Files: `python/djust/static/djust/service-worker.js` (new, standalone — NOT bundled into `client.js`), `python/djust/static/djust/src/33-sw-registration.js` (new, concatenated into `client.js`), `python/djust/middleware.py` (new), `python/djust/config.py` (new `service_worker` defaults sub-dict), tests in `tests/js/service_worker.test.js` (10 cases) and `tests/unit/test_main_only_middleware.py` (7 cases), full guide at `docs/website/guides/service-worker.md`.
- **`UploadWriter` — raw upload byte-stream access for direct-to-S3 / GCS streaming (Phoenix 1.0 parity, v0.5.0 P2)** — New `UploadWriter` base class in `djust.uploads` with an `open()` → `write_chunk(bytes)` → `close() -> Any` / `abort(error)` lifecycle, wired into `allow_upload(name, writer=MyWriter)`. When a writer is configured, binary WebSocket chunks are piped straight to the writer without buffering to disk or RAM — zero temp file, zero `entry._chunks`. Writers are instantiated lazily per upload on the first chunk (so abandoned uploads never open an S3 multipart upload), opened exactly once, fed `write_chunk()` per client frame, and finalized via `close()` whose return value is stored on `UploadEntry.writer_result` and rendered in the upload-state context as `{{ entry.writer_result }}`. Any failure (open or write_chunk raised, `close()` raised, size-limit exceeded, client cancelled, WebSocket disconnected via `UploadManager.cleanup()`) routes through `abort(BaseException)` with the raw exception so writers can release server-side resources (e.g. `AbortMultipartUpload`); `abort()` is wrapped to swallow its own exceptions so a failing S3 cleanup never propagates into the request path. Includes `BufferedUploadWriter` helper that accumulates client-sent 64 KB chunks until a configurable `buffer_threshold` (default 5 MB — S3 MPU minimum part size except for the last) and calls `on_part(bytes, part_num)` so subclasses work with S3-aligned parts without worrying about raw client chunk size. Legacy (no-`writer=`) disk-buffered path is untouched byte-for-byte — backward compatible. Documented in `docs/website/guides/uploads.md` with a full S3 multipart example. (`python/djust/uploads.py`, `python/djust/websocket.py`)
- **`dj-ignore-attrs` — per-element client-owned attribute opt-out (Phoenix 1.1 `JS.ignore_attributes/1` parity, v0.5.0 P2)** — Mark specific HTML attributes as client-owned so VDOM `SetAttr` patches skip them. `<dialog dj-ignore-attrs="open">` prevents the server from resetting the `open` attribute that the browser manages; `<div dj-ignore-attrs="data-lib-state, aria-expanded">` protects third-party JS state. Comma-separated list with whitespace tolerance. The guard sits inside `applySinglePatch`'s `case 'SetAttr'` after the `UNSAFE_KEYS` check; the attribute write is skipped entirely (and `break`s out of the case) when the element opts out. `RemoveAttr` is intentionally unaffected. Implementation: `globalThis.djust.isIgnoredAttr(el, key)` helper (~20 lines JS) plus a three-line check in the patch site. (`python/djust/static/djust/src/31-ignore-attrs.js`, `python/djust/static/djust/src/12-vdom-patch.js`)
- **`{% colocated_hook %}` template tag + runtime extraction (Phoenix 1.1 `ColocatedHook` parity, v0.5.0 P2)** — Write hook JavaScript inline alongside the template that uses it, instead of in a separate file. `{% colocated_hook "Chart" %}hook.mounted = function() { renderChart(this.el); };{% endcolocated_hook %}` emits a `<script type="djust/hook" data-hook="Chart">` tag with a `/* COLOCATED HOOK: Chart */` auditor banner. The client runtime walks `script[type="djust/hook"]` elements on init and after each VDOM morph (`reinitAfterDOMUpdate`), registers each body as `window.djust.hooks[name]` via `new Function`, and marks the script with `data-djust-hook-registered="1"` so re-scans are idempotent. Optional namespacing via `DJUST_CONFIG = {"hook_namespacing": "strict"}` prefixes `data-hook` with `<view_module>.<view_qualname>` so two views can each define `Chart` without colliding; per-tag opt-out with `{% colocated_hook "X" global %}`. Namespacing is OFF by default for compat. Security: the body is template-author JS (same trust level as any other template JS); `</script>` / `</SCRIPT>` are escaped in the tag's `render()` to prevent premature tag close. Apps on strict CSP without `'unsafe-eval'` should continue using the traditional registration pattern. (`python/djust/static/djust/src/32-colocated-hooks.js`, `python/djust/templatetags/live_tags.py`, `python/djust/config.py`, `docs/website/guides/hooks.md`)
- **Database change notifications — PostgreSQL `LISTEN/NOTIFY` → LiveView push (v0.5.0 P1)** — Subscribe LiveViews to Postgres pg_notify channels so database changes push real-time updates to every connected user with zero explicit pub/sub wiring. Three APIs: `@notify_on_save(channel="orders")` model decorator hooks Django `post_save` / `post_delete` and emits `NOTIFY <channel>, <json>`; `self.listen("orders")` in `mount()` subscribes the view (joins a Channels group named `djust_db_notify_<channel>`); `def handle_info(self, message)` receives `{"type": "db_notify", "channel": ..., "payload": {"pk": ..., "event": "save"|"delete", "model": "app.Model"}}` and re-renders via the standard VDOM diff path. A process-wide `PostgresNotifyListener` owns one dedicated `psycopg.AsyncConnection` (outside Django's pool — long-lived LISTEN connections don't play nice with pgbouncer transaction pooling) and runs `async for notify in conn.notifies():`, bridging every NOTIFY into `channel_layer.group_send(...)`. Channel names are strictly validated (`^[a-z_][a-z0-9_]{0,62}$`) at registration and listen time — load-bearing because Postgres NOTIFY doesn't accept bind parameters for the channel identifier. `send_pg_notify(channel, payload)` is a public helper for Celery tasks / management commands. Non-postgres backends no-op gracefully (debug-logged); `self.listen()` raises `DatabaseNotificationNotSupported` when psycopg or a postgres backend isn't available. Known limitation: notifications emitted while the listener's TCP connection is dropped are lost — listener auto-reconnects with 1s backoff and re-issues LISTEN for all subscribed channels, and WS `mount()` re-fetch handles the client-side recovery case. Documented in `docs/website/guides/database-notifications.md`. (`python/djust/db/decorators.py`, `python/djust/db/notifications.py`, `python/djust/mixins/notifications.py`, `python/djust/websocket.py`)
- **PyO3 `getattr` fallback for model attribute access (v0.5.0 P1 — Rust template engine parity)** — Templates can now reference Django model instances passed through context without manual dict conversion. `{{ user.username }}` resolves via Python `getattr` when `user` is a raw Python object rather than a JSON-serialized dict. Implementation: Python's `_sync_state_to_rust()` builds a sidecar of non-JSON-friendly context values and forwards them via the new `RustLiveView.set_raw_py_values()` method; Rust's `Context::resolve()` tries the normal value-stack path first, then walks `getattr` on attached PyObjects one segment at a time. `PyAttributeError` (and any property-descriptor exceptions) are caught — missing attrs render as empty, matching Django's `TEMPLATE_STRING_IF_INVALID` default. `Value` stays `Serialize`-friendly (no `Value::PyObject` variant); sidecar lives outside the Value enum via `Arc<HashMap<String, PyObject>>` on `Context`. (`crates/djust_core/src/context.rs`, `crates/djust_live/src/lib.rs`, `python/djust/mixins/rust_bridge.py`)
- **`register_assign_tag_handler()` for context-mutating template tags (v0.5.0 P1 — Rust template engine parity)** — New tag-handler variety complementing `register_tag_handler` (emits HTML) and `register_block_tag_handler` (wraps content). An assign tag's `render(args, context)` method returns a `dict[str, Any]` that's merged into the template context for subsequent sibling nodes — no HTML output. Enables `{% assign slot var_name %}`-style patterns. Supported inside `{% for %}` loops (per-iteration mutation). Registered via `djust._rust.register_assign_tag_handler(name, handler)`. New `Node::AssignTag` variant; partial-renderer emits `"*"` wildcard dep so downstream nodes always re-render on context changes. (`crates/djust_templates/src/registry.rs`, `crates/djust_templates/src/parser.rs`, `crates/djust_templates/src/renderer.rs`)
  See `docs/website/guides/template-cheatsheet.md`.
- **`dj-virtual` — Virtual / windowed lists with DOM recycling (v0.5.0 P1)** — Render only the visible slice of a large list, recycling DOM nodes as the user scrolls. `<div dj-virtual="items" dj-virtual-item-height="48" dj-virtual-overscan="5" style="height: 600px; overflow: auto;">` keeps ~visible-plus-overscan children in the DOM even if the pool has 100K entries. Implementation: fixed-height windowing via `transform: translateY(...)` on an inner shell plus a hidden spacer for scrollbar length, scroll handler batched through `requestAnimationFrame`, real element identity preserved across scrolls for hook/framework compatibility. Integrates with the VDOM morph pipeline: new containers are picked up by `reinitAfterDOMUpdate`, and `djust.refreshVirtualList(el)` is available for explicit repaints. `djust.teardownVirtualList(el)` disconnects observers for unmounted containers. (`python/djust/static/djust/src/29-virtual-list.js`)
  See `docs/website/guides/large-lists.md`.
- **`dj-viewport-top` / `dj-viewport-bottom` — Bidirectional infinite scroll (Phoenix 1.0 parity, v0.5.0 P1)** — Fire server events when the first or last child of a stream container enters the viewport via `IntersectionObserver`. `<div dj-stream="messages" dj-viewport-top="load_older" dj-viewport-bottom="load_newer" dj-viewport-threshold="0.1">`. Once-per-entry firing (matches Phoenix) via a `data-dj-viewport-fired` sentinel; call `djust.resetViewport(container)` or replace the sentinel child to re-arm. New server-side `stream()` `limit=N` kwarg and `stream_prune(name, limit, edge)` method emit a `stream_prune` op that trims children from the opposite edge so chat apps, activity feeds and log viewers can stream bidirectionally without unbounded DOM growth. (`python/djust/static/djust/src/30-infinite-scroll.js`, `python/djust/static/djust/src/17-streaming.js`, `python/djust/mixins/streams.py`)
- **`assign_async` / `AsyncResult` (v0.5.0 P1)** — High-level async data loading inspired by Phoenix LiveView's `assign_async`. Call `self.assign_async("metrics", self._load_metrics)` in `mount()` (or any event handler); the attribute is set to `AsyncResult.pending()` immediately, the loader runs via the existing `start_async` infrastructure, and on completion the attribute becomes `AsyncResult.succeeded(result)` or `AsyncResult.errored(exc)`. Templates read the three mutually-exclusive states via `{% if metrics.loading %}…`, `{% if metrics.ok %}{{ metrics.result }}…`, `{% if metrics.failed %}{{ metrics.error }}…`. Sync and `async def` loaders are both supported; multiple calls in the same handler load concurrently. Cancellation piggybacks on `cancel_async("assign_async:<name>")`. (`python/djust/async_result.py`, `python/djust/mixins/async_work.py`)
- **`{% dj_suspense %}` block tag for template-level loading boundaries (v0.5.0 P1)** — Declarative counterpart to `assign_async`: wrap a section depending on one or more `AsyncResult` assigns, and the boundary emits a fallback while any are loading, an error div if any failed, or the body once all are `ok`. Explicit `await="metrics,chart"` syntax keeps the tag debuggable — no reflection magic. Fallback templates are loaded via Django's template loader; unspecified fallbacks render a minimal spinner. Nested suspense boundaries resolve independently. Registered alongside `{% call %}` in the Rust template engine — no parser/renderer changes. (`python/djust/components/suspense.py`, `python/djust/components/rust_handlers.py`)
  See `docs/website/guides/loading-states.md`.
- **Function components via `@component` decorator (v0.5.0 P1 batch)** — Stateless Python render functions registerable as template-invokable components. `@component def button(assigns): ...` is callable from templates via `{% call "button" variant="primary" %}Go{% endcall %}` (with `{% component %}` as a synonymous alias). Closes the middle ground between raw HTML and full `LiveComponent` classes for the ~80% of UI pieces (buttons, cards, badges, icons) that are stateless. `clear_components()` helper exposed for tests. (`python/djust/components/function_component.py`, `python/djust/__init__.py`)
- **Declarative component assigns and slots (Phoenix.Component parity)** — `Assign("variant", type=str, default="default", values=["primary", "danger"], required=True)` and `Slot("col", multiple=True)` DSL, declared on a `LiveComponent` class attribute (`assigns = [...]` / `slots = [...]`) or on function components via `@component(assigns=[...], slots=[...])`. Validation runs at mount/invoke: required-missing raises `AssignValidationError` in DEBUG and warns in production, type coercion (`str → int / bool / float`) is automatic, enum violations via `values=` raise. Child-class `assigns` extend (and override by name) parent declarations via MRO walk. (`python/djust/components/assigns.py`, `python/djust/components/base.py`)
  See `docs/website/guides/components.md`.
- **Named slots with attributes via `{% slot %}` / `{% render_slot %}` tags** — Parent templates pass named content blocks with attributes into components: `{% call "card" %}{% slot header label="Title" %}Header{% endslot %}Body{% endcall %}`. Multiple same-name slots collect into a list (essential for table columns, tab panels). Slots are exposed to the component as `assigns["slots"] = {name: [{"attrs": {...}, "content": "..."}, ...]}`. Non-slot content in the `{% call %}` body becomes `children` / `inner_block`. Implemented in pure Python via a sentinel-and-extract protocol — zero Rust parser/renderer changes. (`python/djust/components/function_component.py`)

### Fixed

- **Attribute-context HTML escaping parity with Django (v0.5.0 P1 — Rust template engine parity)** — Variables inside HTML attribute values now route through a dedicated `html_escape_attr()` that's guaranteed to escape `"` → `&quot;` and `'` → `&#x27;` (in addition to `&`/`<`/`>`). Detection reuses the existing `is_inside_html_tag_at()` parser helper — the per-`Node::Variable` `in_attr` flag is computed at parse time, so renderer cost is a bool check. `|safe` still bypasses escaping in both attribute and text contexts. Today's behaviour is unchanged (the base `html_escape` already covered quotes) — this refactor makes the parse-time classification visible to the renderer so future changes to the default escape can't accidentally break attribute values like `<a href="{{ url }}">` when `url` contains quotes. (`crates/djust_templates/src/parser.rs`, `crates/djust_templates/src/filters.rs`, `crates/djust_templates/src/renderer.rs`)
- **Inline conditional `{{ x if cond else y }}` now contributes deps to enclosing wrappers ([#783](https://github.com/djust-org/djust/issues/783), sibling bug)** — Same failure mode as nested `{% include %}`: `extract_from_nodes` had no arm for `Node::InlineIf`, so its `true_expr` / `condition` / `false_expr` variables were silently dropped from the dep set of any surrounding `{% if %}` / `{% for %}` / `{% with %}`. Changing the condition alone (e.g. `step_active` in `{% for s in steps %}<span class="{{ 'active' if step_active else 'idle' }}">`) produced `patches=[]` and stale HTML. Fix: `extract_from_nodes` now extracts non-literal variables from all three `InlineIf` expressions.
- **Nested `{% include %}` now propagates wildcard dep to enclosing wrappers ([#783](https://github.com/djust-org/djust/issues/783))** — Rust partial renderer reused the cached fragment of an `{% if %}` / `{% for %}` / `{% with %}` wrapping a nested `{% include %}`, because `extract_from_nodes` treated `Include` as having no variable references. When the included template referenced a context key that changed (e.g. `{{ field_html.first_name|safe }}`), the wrapper's dep set (`{current_step_name}`) did not intersect `changed_keys` (`{field_html}`), `needs_render` returned `false`, the cached HTML was reused, and the text-region fast-path compared byte-identical old/new HTML → `patches=[]` with `diff_ms: 0`. Manifested with deeply-nested `WizardMixin` templates (`{% extends %} → {% block %} → {% if current_step_name == "..." %} → {% include "step_*.html" %}`). Fix: `extract_from_nodes` now injects `"*"` into the variables map when it encounters a nested `Include` or `CustomTag`/`BlockCustomTag` during its walk, so wrapper deps include the wildcard and those nodes are always re-rendered. (`crates/djust_templates/src/parser.rs`)
- **`_force_full_html` now calls `set_changed_keys` so Rust partial renderer re-renders ([#783](https://github.com/djust-org/djust/issues/783))** — When `_force_full_html` was set, `_sync_state_to_rust()` cleared `prev_refs` to force all context to Rust, but the `set_changed_keys` call was gated by `if prev_refs` which evaluated to False after clearing. Rust's partial renderer saw no `changed_keys`, fell back to full render with empty `changed_indices`, and the text-region fast-path compared identical old/new HTML → zero patches. Fix: `set_changed_keys` is now called when `_force_full_html` is set regardless of `prev_refs`.

### Docs

- **ROADMAP correction: `temporary_assigns` is already implemented** — The v0.5.0 ROADMAP entry claiming `temporary_assigns` was "completely absent from djust today" was inaccurate. The feature has shipped in earlier releases (`LiveView._initialize_temporary_assigns` / `_reset_temporary_assigns`, wired into the render cycle and excluded from change tracking). This PR adds a dedicated regression test (`tests/unit/test_temporary_assigns.py`) — prior coverage was indirect — and strikes through the ROADMAP entry.

### Tests

- **Regression coverage for `temporary_assigns`** — `tests/unit/test_temporary_assigns.py` covers reset-after-render semantics, default-value cloning per type (list / dict / set / scalar), idempotent initialization, pre-existing-attribute preservation, instance-level override, and the empty-mapping no-op path.
- **Unit tests for `assign_async` / `AsyncResult`** — `tests/unit/test_assign_async.py` (18 tests) covers state-flag invariants, frozen dataclass immutability, pending-is-set-immediately, success & failure propagation, multi-concurrent scheduling, cancellation interop with `cancel_async`, sync and async loaders, args/kwargs forwarding, and the generation-counter / stale-loader regression cases added in #793.
- **Unit tests for `{% dj_suspense %}`** — `tests/unit/test_suspense.py` (12 tests) covers ok → body, loading → fallback, failed → error-div, HTML-escaped error messages, no-`await=` passthrough, unknown / non-`AsyncResult` refs defaulting to loading, default spinner, Django template fallback, template-error graceful degradation, nesting, and whitespace-tolerant comma-separated lists.
- **Regression suite for `|safe` HTML blob diff ([#783](https://github.com/djust-org/djust/issues/783))** — `tests/test_rust_vdom_safe_diff_783.py` exercises the WizardMixin-style pattern where `field_html` is derived in `get_context_data()` from an instance attribute. Covers dict reassignment, in-place nested mutation, the `_force_full_html` codepath, an `{% if %}` branch swap, a `{% extends %}`/`{% block %}` inheritance chain, and the exact NYC-Claims-style `{% extends %} + {% if %} + {% include %}` structure that originally exhibited the bug. All variants assert non-empty VDOM patches on state change.
- **Dep-extractor hardening ([#783](https://github.com/djust-org/djust/issues/783) follow-up, P0)** — Three-part hardening against silent dep-drop regressions in `crates/djust_templates/src/parser.rs::extract_from_nodes`:
  - **Rust unit tests for `extract_per_node_deps`** — table-driven assertions on representative AST shapes (simple Variable, If-wrapping-Include, For with tuple unpacking, With + body, InlineIf condition, nested For, Block recursion, plain Text). Explicit `"*"` wildcard membership checks for nested `Include` / `CustomTag` shapes.
  - **Node variant exhaustiveness check** — `sample_for_coverage` exhaustive match on `Node::*` + `sample_nodes()` constructor + `NO_VARS_VARIANTS` allow-list. Any new `Node` variant fails to compile until the match is updated, and at runtime every non-allow-listed variant must produce a non-empty dep set (real vars or `"*"` wildcard). Makes silent dep-drops on future additions impossible.
  - **Partial-render correctness harness (Python)** — `TestPartialRenderCorrectness` in `tests/test_rust_vdom_safe_diff_783.py`. Byte-equality oracle: for each of 6 wrapper shapes (no-wrapper, `{% if %}`, `{% for %}`, `{% with %}`, full #783 extends/if/include/safe chain, InlineIf-in-for), renders a mutation via the normal partial-render path then re-renders the same mutation with the Rust fragment cache cleared (`clear_fragment_cache()`) as a control. Any dep-miss that causes partial render to reuse a stale cached fragment diverges from the control and fails.
- **New PyO3 method `DjustLiveView.clear_fragment_cache` (test-only)** (`crates/djust_live/src/lib.rs`) — clears `node_html_cache`, `last_html`, `fragment_text_map`, `text_node_index` while preserving `last_vdom` so the diff baseline is unchanged. Exclusively supports the partial-render correctness harness above; not intended for application use.

## [0.5.0rc2] - 2026-04-20

### Added

- **Bootstrap 4 CSS framework adapter** — New `Bootstrap4Adapter` for projects using Bootstrap 4 (NYC Core Framework, government sites, legacy projects). Set `DJUST_CONFIG = {"css_framework": "bootstrap4"}`. Includes proper `custom-select`, `custom-control-*` classes for checkboxes/radios, and `form-group` wrappers.
  See `docs/website/guides/css-frameworks.md`.
- **Dedicated radio button classes** — Radio buttons now use `radio_class`, `radio_label_class`, and `radio_wrapper_class` config keys (with fallback to checkbox classes). Both Bootstrap 4 and 5 configs define radio-specific classes.
- **Select widget class support** — `ChoiceField` with `Select` widget uses `select_class` config key (e.g., `custom-select` for BS4, `form-select` for BS5) instead of the generic `field_class`.
- **Theme-to-framework CSS bridge** — New `{% theme_framework_overrides %}` template tag generates `<style>` overrides that map djust theme variables (`--primary`, `--border`, etc.) onto the active CSS framework's selectors (`.btn-primary`, `.form-control`, `.alert-*`, etc.). Switching themes now automatically re-styles Bootstrap 4/5 components.

  See `docs/website/guides/css-frameworks.md`.
### Fixed

- **Derived container context values now tracked by value equality ([#774](https://github.com/djust-org/djust/issues/774))** — The Rust state sync used `id()` comparison for all non-immutable context values, which is unreliable for containers (dict, list, tuple) due to CPython address reuse after GC. Derived values like `current_step = wizard_steps[step_index]` could be missed when the handler only changed `step_index`, causing Rust to render stale HTML. Fix: containers are now compared by value equality (like immutables already were), with previous values cached in `_prev_context_containers`. The optimization is preserved — unchanged containers are still skipped.

## [0.5.0rc1] - 2026-04-19

### Added

- **Package consolidation: all 5 runtime packages folded into djust** — One install, one version, one CHANGELOG. `pip install djust` stays lean; `pip install djust[all]` gets everything.
  - **Phase 1+2: `djust-auth` + `djust-tenants` → core** ([#770](https://github.com/djust-org/djust/pull/770)) — `djust-auth` (879 LOC) merged into `python/djust/auth/` package with lazy imports. `djust-tenants` missing modules (audit, middleware, managers, models, security) merged into existing `python/djust/tenants/`. Both are core — no extras needed. 27 new tests.
  - **Phase 3: `djust-admin` → `djust[admin]`** ([#771](https://github.com/djust-org/djust/pull/771)) — 3,878 LOC merged into `python/djust/admin_ext/` (avoids collision with `django.contrib.admin`). Views, forms, adapters, plugins, decorators, template tags, 7 HTML templates. 40 new tests.
  - **Phase 4: `djust-theming` → `djust[theming]`** ([#772](https://github.com/djust-org/djust/pull/772)) — 49,105 LOC merged into `python/djust/theming/`. CSS theming engine, design tokens, 96 HTML templates, 9 static files, management command (`djust_theme`), 4 template tag modules, gallery sub-package. 749+ tests.
  - **Phase 5: `djust-components` → `djust[components]`** ([#773](https://github.com/djust-org/djust/pull/773)) — ~100K LOC merged into `python/djust/components/`. 170+ UI component classes, 6 template tag modules, management command (`component_gallery`), descriptors, mixins, rust_handlers.py. Extra deps: `markdown>=3.0`, `nh3>=0.2`.

## [0.4.5rc2] - 2026-04-18

### Added

- **AI observability module: `djust.observability`** — DEBUG-gated, localhost-only HTTP endpoints that give external tooling (like the djust Python MCP and djust-browser-mcp) live visibility into framework state without in-process coupling. Ships as seven endpoints under `/_djust/observability/`: `health`, `view_assigns`, `last_traceback`, `log_tail`, `handler_timings`, `sql_queries`, `reset_view_state`, `eval_handler`. Each pairs with a matching MCP tool. Security model mirrors django-debug-toolbar (DEBUG=True + `LocalhostOnlyObservabilityMiddleware`). Requires `path("_djust/observability/", include("djust.observability.urls"))` in the project urls.py.
- **`get_view_assigns`** — Real server-side `self.*` state of the mounted LiveView for a given session. Complements browser-mcp's client-only `djust_state_diff` with the source of truth. Per-attr fallback tags non-serializable values with `{_repr, _type}` rather than an all-or-nothing blanket.
- **`get_last_traceback`** — Ring-buffered (50) exception log populated from `handle_exception()`. Replaces "can you paste the terminal?" for 80% of blind-debugging cases.
- **`tail_server_log`** — Ring-buffered (500) Django/djust log records with `since_ms` + `level` filters. `djust.*` captured at DEBUG+, `django.*` at WARNING+.
  See `docs/website/guides/mcp-server.md`.
- **`get_handler_timings`** — Per-handler rolling 100-sample distribution (min/max/avg/p50/p90/p99). Reuses existing `timing["handler"]` measurements; no extra perf counters.
- **`get_sql_queries_since`** — Per-event SQL capture via `connection.execute_wrappers`. Queries are tagged with `(session_id, event_id, handler_name)` + `stack_top` filtered to skip framework frames.
- **`reset_view_state`** — Replay `view.mount()` on a registered instance. Clears public attrs, re-invokes `mount(stashed_request, **stashed_kwargs)`. Useful between fixture replays.
- **`eval_handler`** — Dry-run a handler against a live view's current state. Returns `{before_assigns, after_assigns, delta, result}`. v2 `dry_run=True` installs a `DryRunContext` that blocks `Model.save`/`delete`, `QuerySet.update`/`delete`/`bulk_create`/`bulk_update`, `send_mail`/`send_mass_mail`, `requests.*`, and `urllib.request.urlopen` — first attempt raises `DryRunViolation` and the response surfaces `{blocked_side_effect}`. `dry_run_block=False` records without blocking. Process-wide lock serializes dry-runs.
- **`find_handlers_for_template(template_path)` in djust MCP** — Cross-references a template file against every view that uses it, returning dj-* handlers wired in the template and the diff against view handler methods. Catches dead bindings at author time (complements djust-browser-mcp's runtime `find_dead_bindings`).
  See `docs/guides/djust-audit.md`.
- **`seed_fixtures(fixture_paths)` in djust MCP** — Subprocess wrapper around `manage.py loaddata` for regression-fixture DB setup.

  See `docs/website/guides/mcp-server.md`.
### Fixed

- **`hotreload`: suppress empty-patch broadcasts on unrelated file changes ([#763](https://github.com/djust-org/djust/issues/763))** — When a Python file changes that doesn't affect the currently-mounted view, re-render produces zero patches. The old code still broadcast ~14 KB (empty patches + full `_debug` state dump) to every connected session. Early-return when `hotreload=True AND patches==[]`. Non-hot-reload empty patches still sent (loading-state clear ack needed).
- **`client.js`: guard 38 unguarded `console.log` calls ([#761](https://github.com/djust-org/djust/issues/761))** — Per `djust/CLAUDE.md` rule, no `console.log` without `if (globalThis.djustDebug)` guard. Introduced a `djLog` helper in `00-namespace.js` and replaced bare `console.log` → `djLog` across 12 client modules. `console.warn`/`console.error` untouched (real problems stay visible in prod).
- **Observability `DryRunContext._uninstall` logs setattr failures ([#759](https://github.com/djust-org/djust/issues/759))** — Silent `except Exception: pass` meant the process could run indefinitely with a wrapped `Model.save` if uninstall partially failed — catastrophic for a dev server. Replaced with a `logger.warning` so the failure is observable.

### Changed

- **`djust.observability` + eval_handler v2** — Side-effect blocking now covers QuerySet bulk writes ([#758](https://github.com/djust-org/djust/issues/758)): `QuerySet.update`/`delete`/`bulk_create`/`bulk_update` are patched alongside `Model.save`/`delete`, so a handler that does `Model.objects.filter(...).update(...)` correctly raises `DryRunViolation` instead of silently committing.
- **Observability dry_run tests tightened ([#760](https://github.com/djust-org/djust/issues/760))** — Two tests claimed to verify the record-but-allow contract but only checked detection. Now use `unittest.mock` to assert the original callable was actually invoked (`call_count == 1`) alongside the violation-recorded assertion.

## [0.4.5rc1] - 2026-04-17

### Changed

- **Text-region fast path now fires for `{% extends %}` templates** — The scanner that builds the VDOM text-node position index used to process the full pre-hydration HTML, but the VDOM is rooted at `[dj-root]`. On templates extending a base (with `<title>`, meta tags, scripts outside dj-root), the scanner counted text runs in `<head>` and trailing `<footer>`/`<script>` siblings that the VDOM didn't have — the count mismatched, the index was discarded, and every event fell through to a full html5ever parse (~10ms on the djust.org /examples/ page). Now the scanner is restricted to the dj-root element's interior via a balanced-tag walker. Rust render drops from ~14ms → ~2.8ms on extends templates; browser E2E (production, DEBUG=False) drops from 30ms → ~25ms avg, 18ms min.

  See `docs/website/core-concepts/templates.md`.
- **Text-region VDOM fast path** — Extends the existing text-fast-path to handle changes that differ only in a text span, even when the surrounding fragment contains tags. Computes byte-level common prefix/suffix on pre-hydration HTML; if the divergence is a single tag-free text run, locates the owning VDOM text node via a pre-built positional index (binary search on `(html_start, html_end, path, text, djust_id)` entries, built once per full-parse render and kept in sync through fast-path events by shifting downstream entries by the byte delta). Patches in place and skips html5ever entirely. For a counter click inside a `{% for %}` loop on a 309KB page, Rust render drops from ~12ms to ~2.7ms. UTF-8 safe (snaps to char boundaries), handles `<pre>`/`<code>`/`<textarea>` whitespace preservation and `<script>`/`<style>` raw-text element bodies correctly, bails to full parse on entity-offset mismatches.

- **`parse_html_fragment(html, context_tag)`** — New public entry point in `djust_vdom` that uses html5ever's `parse_fragment` with a parent-element context. Enables parsing isolated HTML fragments with correct tokenization for context-sensitive elements (`<tr>`, `<td>`, `<option>`), without resetting the dj-id counter. Scaffolding for future structural-fragment fast paths.

- **`collect_vdom_text_nodes` now skips comment nodes** — Previously collected `<!--dj-if-->` placeholders into the text-node list, shifting every subsequent ordinal by one and breaking any position-based patching. Text and comment VNodes both carry `text`, so an explicit `is_text()` filter was needed.

- **Partial template rendering ([#737](https://github.com/djust-org/djust/issues/737))** — Per-node dependency tracking at template parse time. On re-render, only template nodes whose context variable dependencies changed are re-rendered; unchanged nodes reuse cached HTML. For a single-variable change on a page with 50 template nodes, template render drops from ~1.4ms to ~0.1ms. Changed keys are passed from Python to Rust via `set_changed_keys()`, which merges across multiple sync calls. `{% include %}` and custom tags always re-render (wildcard dependency).

- **`{% extends %}` inheritance resolution caching** — Templates using `{% extends %}` now participate in partial rendering. Inheritance is resolved once via `OnceLock<ResolvedInheritance>` on the `Template` struct (shared via `TEMPLATE_CACHE`). Final merged nodes and their deps are cached, so subsequent renders skip both chain building and static parent nodes. Combined with partial rendering, extends templates go from full re-render (~14ms Rust) to partial render of changed nodes only (~0.02ms Rust).

- **Text-only VDOM fast path** — When all changed template fragments are plain text (no HTML tags), skip both html5ever parsing and VDOM diffing entirely. The old VDOM is mutated in-place via a fragment→text-node map built on first render, and SetText patches are produced directly. For counter-style updates: parse phase drops from ~12ms to ~0.001ms.

- **Block flattening for partial rendering** — `{% block %}` nodes left by Django's template engine are flattened to expose each child as a separate fragment. This enables the text fast path to activate on pages using `{% extends %}` where Django resolves blocks.

- **Faster change detection** — `_snapshot_assigns` uses identity + shallow fingerprints (id, length, content hash for list-of-dicts) instead of `copy.deepcopy`. Framework-internal keys (`csrf_token`, `kwargs`, `temporary_assigns`, `DATE_FORMAT`, `TIME_FORMAT`) and auto-generated `_count` keys are excluded from `set_changed_keys` to avoid spurious re-renders.

- **Optimized VNode parser** — Pre-sized attribute HashMap, eliminated redundant `to_lowercase()` call, removed form element debug output.

### Fixed

- **Derived immutable context values no longer go stale on partial re-render** — `_sync_state_to_rust` previously skipped id()-based change detection for immutable types (int/str/bool/bytes) to avoid false positives from Python's int cache, which meant derived values computed in `get_context_data` (e.g. `completed_count = sum(...)`, `total_count = len(...)`) were never synced to Rust when their sources changed. Partial rendering would then reuse the cached HTML for template nodes depending on those values, leaving counters stale after add/toggle/delete. Fixed by tracking previous VALUES for immutable keys and comparing by equality. Regression tests in `test_changed_tracking.py::TestDerivedImmutableSync`.

- **VDOM input value leak on name change** — When the patcher morphs an input into a different field (e.g., wizard step 1 name → step 2 email), the old field's typed value no longer leaks into the new field. Both `morphElement` and `SetAttr` patches now clear `.value` when the `name` attribute changes.

- **In-place dict mutation detection** — `_snapshot_assigns` now fingerprints list contents (id + dict values hash) to detect mutations like `todo['completed'] = True` that don't change the list's id or length. Falls back to id-only for unhashable values.

- **Derived context value detection** — When `_changed_keys` is set, the sync also checks non-immutable context values by id() to catch derived values (e.g., `products` from `_products_cache`) that change via private attributes.

## [0.4.4] - 2026-04-15

### Changed

- **Remove double `updateHooks()`/`bindModelElements()` scanning** — These were called in both `applyPatches()` and `reinitAfterDOMUpdate()`, scanning the full DOM twice per patch cycle. Removed from `applyPatches()`. Saves ~5ms per event.

- **Delegated scoped listeners (dj-window-*, dj-document-*)** — Replaced `querySelectorAll('*')` full DOM scan with a registry-based delegation pattern. Scoped elements are scanned once at mount time and registered in a Map. Event listeners on window/document dispatch to the registry. Handles dotted attribute variants (dj-window-keydown.escape).

- **Use `orjson.loads()` for patch JSON parsing** — 2-3x faster than stdlib `json.loads()` when orjson is installed. Falls back gracefully.

- **Gate debug payload behind panel open state** — `get_debug_update()` (dir + getattr + json.dumps per attribute) only runs when the debug panel is actually open, not on every event in DEBUG mode. Saves ~2-5ms per event. Panel sends `debug_panel_open`/`debug_panel_close` WS messages on toggle.

## [0.4.4rc1] - 2026-04-15

### Fixed

- **VDOM patch path traversal skips regular HTML comments ([#729](https://github.com/djust-org/djust/issues/729))** — The JS patcher was counting all HTML comment nodes during path traversal, but the Rust VDOM parser only preserves `<!--dj-if-->` placeholders. This caused every page with HTML comments in `dj-root` to fail VDOM patching and fall back to full HTML recovery.

- **Scroll to top on `dj-navigate` live_redirect** — `handleLiveRedirect()` now scrolls to the top of the page (or to anchor if URL has a hash) after `pushState`.

### Changed

- **Event delegation replaces per-element binding ([#730](https://github.com/djust-org/djust/issues/730))** — `bindLiveViewEvents()` no longer scans the DOM after every VDOM patch. Instead, one listener per event type is installed on the `dj-root` element via delegation (`e.target.closest('[dj-click]')`). This reduces client-side post-patch handling from ~56ms to ~30ms on large pages. Per-element rate limiting preserved via WeakMap.

### Added

- **Per-phase Rust timing in `render_with_diff()` ([#730](https://github.com/djust-org/djust/issues/730))** — Instrumentation measuring template render, html5ever parse, VDOM diff, and HTML serialization. Exposed to Python via `get_render_timing()` and propagated to WebSocket response performance metadata.

## [0.4.3] - 2026-04-14

### Fixed

- **`{% csrf_token %}` no longer renders poisoned `CSRF_TOKEN_NOT_PROVIDED` placeholder ([#696](https://github.com/djust-org/djust/issues/696))** — The Rust template engine now renders an empty string when no CSRF token is in context (instead of a placeholder that poisoned client.js's CSRF lookup). Python LiveView `_sync_state_to_rust()` now injects the real token from `get_token(request)`. Three-layer defense-in-depth fix merged as PR #708.

- **HTTP fallback POST no longer replaces page with logged-out render ([#705](https://github.com/djust-org/djust/issues/705))** — The POST handler now applies `_apply_context_processors()` before `render_with_diff()` so auth context (user, perms, messages) is available during re-render. Context processor cleanup uses `_processor_context()` context manager for guaranteed cleanup. Merged as PR #710 + #714 + #721.

- **Rust `|date` and `|time` filters honor Django `DATE_FORMAT`/`TIME_FORMAT` settings ([#713](https://github.com/djust-org/djust/issues/713))** — New `apply_filter_with_context()` checks the template context for format settings when no explicit format argument is given. Python injects Django settings into the Rust context during `_sync_state_to_rust()`. Merged as PR #714.

- **Rust `|date` filter now works on `DateField` values ([#719](https://github.com/djust-org/djust/issues/719))** — The `|date` filter previously only parsed RFC 3339 datetime strings. `DateField` values (bare dates like "2026-03-15") are now parsed via a `NaiveDate` fallback pinned to midnight UTC. Merged as PR #720.

- **CSRF token value HTML-escaped in Rust renderer ([#722](https://github.com/djust-org/djust/issues/722))** — The CSRF hidden input now uses the shared `filters::html_escape()` utility (escaping &, ", <, >, and single quotes) instead of a manual `.replace()` chain that missed single quotes. Defense-in-depth. Merged as PR #727.

- **Bare `except: pass` in CSRF injection now logs a warning ([#716](https://github.com/djust-org/djust/issues/716))** — The CSRF token injection in `_sync_state_to_rust()` previously swallowed all exceptions silently. Now logs via `djust.rust_bridge` logger with `exc_info=True`. Merged as PR #721.

### Changed

- **Context processor cleanup refactored to `_processor_context()` context manager ([#717](https://github.com/djust-org/djust/issues/717))** — Replaced the manual try/finally in the HTTP fallback POST handler with a reusable `@contextmanager` that guarantees cleanup of temporarily injected view attributes. Merged as PR #721 + #727.

- **Pre-existing test fixes** — `test_debug_state_sizes` corrected for `json.dumps(default=str)` behavior and `\uXXXX` escaping. `navigation.test.js` suppresses happy-dom/undici WebSocket mock `dispatchEvent` incompatibility.

### Added

- **Python integration tests for DATE_FORMAT settings injection ([#718](https://github.com/djust-org/djust/issues/718))** — 4 tests verifying `_sync_state_to_rust` injects DATE_FORMAT/TIME_FORMAT from Django settings. Merged as PR #721.

- **Negative tests for `|date` filter invalid input ([#725](https://github.com/djust-org/djust/issues/725))** — 4 Rust tests covering invalid dates, non-date strings, empty strings, and partial dates (filter returns original value per Django convention). Merged as PR #727.

  See `docs/guides/live-input.md`.
- **`format_date()` doc comment documenting Django compatibility ([#726](https://github.com/djust-org/djust/issues/726))** — Documents supported input formats (RFC 3339, YYYY-MM-DD) and unsupported types (epoch ints, locale strings). Merged as PR #727.

## [0.4.2] - 2026-04-13

### Fixed

- **Derived context vars synced when parent instance attr mutated in-place ([#703](https://github.com/djust-org/djust/issues/703))** — `_sync_state_to_rust()` now collects `id()`s of all sub-objects reachable from changed instance attrs and includes any derived context var whose `id()` appears in that set. Previously, context vars computed in `get_context_data()` that returned sub-objects of a mutated dict (e.g., `wizard_step_data.get("person", {})`) were skipped because their `id()` was unchanged, causing templates to render stale data. Depth-capped at 8 with cycle detection. 9 new regression tests.

- **`as_live_field()` now respects `widget.input_type` override for `type` attribute ([#683](https://github.com/djust-org/djust/issues/683) re-open)** — The initial #683 fix merged `widget.attrs` but `type` was still ignored because Django moves `type=` from `attrs` into `widget.input_type` during widget `__init__`. `_get_field_type()` now checks `widget.input_type` against the widget class's default and uses the override when they differ (e.g. `TextInput(attrs={"type": "tel"})` sets `input_type="tel"`). 4 new regression tests covering `type="tel"`, `type="url"`, `type="search"`, and the default `type="text"` fallback.

### Added

- **LiveComponent events now propagate to parent LiveView waiters ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b/1c follow-up)** — Closes the "known limitation" documented in the v0.4.2 tutorials guide: `await self.wait_for_event("foo")` on a LiveView now resolves when the matching handler fires on an embedded `LiveComponent`, not just when it fires on the view itself. Without this, a `TutorialStep(wait_for="submit", ...)` where `submit` is a handler on a child `FormComponent` would silently stall forever — the parent view's waiter would never resolve and the tour would hang. The fix is in the WebSocket consumer's `handle_event` component-event branch: after the component handler runs, the consumer now calls `self.view_instance._notify_waiters(event_name, notify_kwargs)` with the handler's kwargs + an injected `component_id` key, mirroring the notification that already happened in the main LiveView branch from Phase 1b. The `component_id` injection means apps can use the waiter's `predicate` argument to disambiguate events fired from multiple component instances: `wait_for_event("submit", predicate=lambda kw: kw.get("component_id") == "project_form")`. A notification failure is caught and logged via the `djust.websocket` logger so a buggy waiter/predicate can't break the component handler's observable behavior — the component's state mutations always happen even if the waiter notification raises. 5 new regression tests in `python/tests/test_waiter_component_propagation.py` covering: component event resolves parent waiter, `component_id` is injected into notify kwargs so predicates can filter by source, multiple parent waiters for the same event all resolve (fan-out), the non-component branch still notifies parent waiters (regression guard for the Phase 1b path), and a raising `_notify_waiters` is logged-and-swallowed rather than propagating. `docs/website/guides/tutorials.md` Limitations section updated to document the new behavior with a `component_id` predicate example.

### Documentation

- **Tutorial bubble must be placed outside `dj-root` ([#699](https://github.com/djust-org/djust/issues/699))** — If the `{% tutorial_bubble %}` tag is placed inside the `dj-root` container, morphdom recovery (which replaces the entire `dj-root` content on patch failure) destroys the bubble mid-tour, causing it to silently disappear. The tutorials guide now has a dedicated "Bubble Placement" section explaining the requirement, why it exists, and correct/incorrect examples. The simplest-possible example at the top of the guide is updated to show the bubble outside `dj-root`. The `tutorial_bubble` template tag docstring is also updated with this requirement.

- **`data-*` attribute naming convention documented in Events guide ([#623](https://github.com/djust-org/djust/issues/623))** — How `data-foo-bar` on an HTML element maps to `foo_bar` in the event handler's kwargs was undocumented. The Events guide now has a dedicated "Data Attribute Naming Convention" section covering: the dash-to-underscore rule, client-side type-hint suffixes (`:int`, `:float`, `:bool`, `:json`, `:list`), server-side Python type-hint coercion, the `dj-value-*` alternative, which internal `data-*` attributes are excluded, and a quick-reference table.

### Changed

- **System checks T002, V008, C003 now suppressible via `DJUST_CONFIG` ([#603](https://github.com/djust-org/djust/issues/603))** — These three informational checks fire on every `manage.py` invocation and are noisy for projects that deliberately don't use the checked features (daphne, explicit `dj-root`, non-primitive mount state). A new `suppress_checks` config key in `DJUST_CONFIG` (or `LIVEVIEW_CONFIG`) accepts a list of check IDs to silence: `DJUST_CONFIG = {"suppress_checks": ["T002", "V008", "C003"]}`. Both short IDs (`"T002"`) and fully-qualified IDs (`"djust.T002"`) are accepted, case-insensitive. Only the informational/advisory variants are suppressed — the C003 *Warning* (daphne misordered) still fires because it indicates a real misconfiguration. 7 new tests for the suppression mechanism.

- **`release-drafter/release-drafter` v6 → v7 + drop `pull_request` trigger** — v7 validates `target_commitish` against the GitHub releases API and rejects `refs/pull/<n>/merge` refs, which is what `github.ref` resolves to under a `pull_request` trigger. v6 silently tolerated this; v7 does not, causing every PR to fail with `Validation Failed: target_commitish invalid`. The fix is to drop the `pull_request` trigger — release-drafter is designed to track changes that have *landed* on the release branch, not comment on in-flight PRs, so `push: branches: [main]` is the right fit. Aligns with how Phoenix, Elixir, GitHub CLI, and other major projects wire release-drafter. Resolves the v7 bump that was deferred out of the v0.4.2 dependabot batch (#680).


- **Dependency batch carry-over (v0.4.2)** — Drains the dependabot backlog that was held behind the v0.4.1 release. Single consolidated PR so one CI run catches any inter-dep interactions:
  - **npm**: `vitest` / `@vitest/ui` / `@vitest/coverage-v8` 4.0.18 → 4.1.4 (patches + new test runner features), `jsdom` 29.0.1 → 29.0.2, `happy-dom` 20.8.4 → 20.8.9. Full JS suite remains green (1111 tests).
  - **Cargo**: `tokio` 1.50 → 1.51 (workspace), `uuid` 1.22 → 1.23, `proptest` 1.10 → 1.11 (djust_vdom), `indexmap` 2.13.0 → 2.14.0 (transitive pickup via cargo update). `cargo check --workspace` clean; `cargo test -p djust_vdom` passes all 42 proptest-driven tests on the new 1.11 runtime.
  - **GitHub Actions**: `actions/github-script` v8 → v9 (two workflows), `astral-sh/setup-uv` v6 → v7 (test workflow). Workflow syntax unchanged.
  - **Intentionally deferred**: `html5ever` 0.36 → 0.39 is a 3-minor-version jump that requires a matching `markup5ever_rcdom` 0.39 release which has not yet been published to crates.io (only git snapshots exist in the html5ever workspace). Using git deps in our published workspace would break `cargo publish` and leak unreleased upstream state, so this stays deferred until upstream publishes. `release-drafter/release-drafter` v6 → v7 was also deferred out of this chore batch because of a `target_commitish` validation incompatibility — shipped as a separate follow-up PR alongside this one.

  Closes 13 open dependabot PRs as superseded (#581, #582, #604, #606, #607, #609, #615, #616, #644, #645, #646, #647, #648).

### Fixed

- **`@background` natively supports `async def` handlers ([#697](https://github.com/djust-org/djust/issues/697))** — The `@background` decorator now detects `asyncio.iscoroutinefunction` and creates a native async closure so `_run_async_work` can `await` it directly on the event loop instead of routing through `sync_to_async`. The fragile `inspect.iscoroutine(result)` workaround from #692 is kept as a legacy fallback. 5 new regression tests.

- **`flush_push_events()` resolves callback dynamically on WS reconnect ([#698](https://github.com/djust-org/djust/issues/698))** — `PushEventMixin.flush_push_events()` now resolves the flush callback via `self._ws_consumer._flush_push_events` at call time instead of relying on a stored `_push_events_flush_callback` that was only wired during initial mount. After a WebSocket reconnect the view instance is restored from session but the stored callback was stale. The dynamic lookup always finds the current consumer. Legacy stored callback kept as fallback. 7 new tests.

- **push_commands-only handlers auto-skip VDOM re-render ([#700](https://github.com/djust-org/djust/issues/700))** — Handlers that only call `push_commands()` / `push_event()` without changing public state no longer trigger a VDOM re-render. The `_snapshot_assigns` deep-copy comparison could report false positives for views with non-copyable public attributes (querysets, file handles) because sentinel objects never compare equal. A new identity-based check (`id()` comparison before/after) detects whether any public attribute was actually rebound and auto-sets `_skip_render = True` when push events are pending but no state changed. 5 new tests.


- **System check V010 detects wrong TutorialMixin MRO ordering at startup ([#691](https://github.com/djust-org/djust/issues/691))** — Django's `View.__init__` does not call `super().__init__()`, so writing `class MyView(LiveView, TutorialMixin)` silently skips TutorialMixin's initialisation. A new `djust.V010` system check scans all LiveView subclasses at startup and emits an Error with a clear fix hint when TutorialMixin appears after a View-derived base in the class declaration. Suppressible via `DJUST_CONFIG = {"suppress_checks": ["V010"]}`. 5 new tests. Tutorials guide updated with correct ordering.

- **`@background async def` handlers now execute correctly ([#692](https://github.com/djust-org/djust/issues/692))** — `@background` wraps handlers in a sync closure; when the handler is `async def`, the closure returned an unawaited coroutine and the handler body never ran. The fix in `_run_async_work` (already on main via workaround) detects coroutine returns and awaits them. 11 new regression tests in `test_background_async.py` verify both sync and async handlers execute their bodies.

- **`push_commands` in `@background` tasks now flush mid-execution ([#693](https://github.com/djust-org/djust/issues/693))** — Push events queued by `push_commands` inside a `@background` handler only reached the client when the entire task completed. The `_flush_pending_push_events` callback mechanism (already on main) lets TutorialMixin and other background handlers flush events immediately. A new public `await self.flush_push_events()` method on PushEventMixin provides the same capability to any `@background` handler. 7 new tests in `test_push_flush_background.py`.

- **`get_context_data` no longer includes non-serializable class attributes ([#694](https://github.com/djust-org/djust/issues/694))** — The MRO walker in `ContextMixin.get_context_data()` added class-level attributes (like `tutorial_steps`) to the template context. Non-JSON-serializable values were silently converted to their `str()` repr, corrupting state on subsequent events. The fix skips class-level attributes that fail a JSON serialisability probe. Additionally, `TutorialMixin` now stores steps as `_tutorial_steps` (private) with a read-only `tutorial_steps` property, so they are excluded by both the `_` prefix convention and the serialisability check. 14 new tests.

- **Debug panel SVG attributes no longer double-escaped ([#613](https://github.com/djust-org/djust/issues/613))** — SVG attributes like `viewBox` and `path d` in the debug toolbar were rendered garbled because the Rust VDOM's `to_html()` method HTML-escaped text content inside `<script>` and `<style>` elements. Per the HTML spec, these are "raw text elements" whose content must be emitted verbatim — escaping `&` to `&amp;` or `<` to `&lt;` corrupts JavaScript/CSS code and causes double-escaping when the HTML is round-tripped through the VDOM pipeline (parse with html5ever which decodes entities, then re-serialize with `to_html()` which re-encodes them). The fix adds an `in_raw_text` flag to the internal `_to_html()` serializer that propagates through `<script>`/`<style>` children, skipping `html_escape()` for their text nodes. SVG attribute values in templates (which don't contain HTML special characters) were already correct but now have explicit regression tests. 4 new Rust unit tests, 3 new Rust integration tests (script/style/SVG roundtrip), 3 new Python regression tests (JS source validation, JSON injection check, VDOM roundtrip), and 3 new JS tests (tab icon SVGs, path d attributes, header button SVGs all verified in DOM).

- **`form.cleaned_data` Python types no longer serialize to null ([#628](https://github.com/djust-org/djust/issues/628))** — `datetime.date`, `datetime.datetime`, `datetime.time`, `Decimal`, and `UUID` values in `form.cleaned_data` stored in public view state are now properly serialized to their JSON representations (ISO strings, floats, strings) instead of silently becoming `null`. Both the `DjangoJSONEncoder` and `normalize_django_value()` already handled these types; 10 new regression tests confirm the behavior.

- **`set()` is now JSON-serializable as public state ([#626](https://github.com/djust-org/djust/issues/626))** — Storing a Python `set()` or `frozenset()` in public view state no longer crashes `json.dumps`. Sets are serialized as sorted lists (falling back to unsorted when elements aren't comparable). Both `DjangoJSONEncoder.default()` and `normalize_django_value()` now handle `set`/`frozenset`. 11 new regression tests.

- **`dict` state no longer corrupted to `list` after Rust state sync ([#612](https://github.com/djust-org/djust/issues/612))** — Round-tripping state through the Rust MessagePack serialization boundary could corrupt `dict` values into `list` because `#[serde(untagged)]` on the `Value` enum let `rmp_serde` match a msgpack map against the `List` variant before trying `Object`. The fix replaces the derived `Deserialize` with a custom visitor-based implementation that uses the deserializer's type hints (`visit_map` vs `visit_seq`) to correctly distinguish maps from arrays. 4 new Rust regression tests + 1 Python end-to-end msgpack round-trip test.

- **`as_live_field()` now merges `widget.attrs` into rendered HTML ([#683](https://github.com/djust-org/djust/issues/683))** — The `as_live_field()` method (and `{% live_field %}` tag) dropped any attributes defined on a Django widget's `attrs` dict — `type="email"`, `placeholder`, `pattern`, `min`/`max`, custom `data-*`, and any other HTML attributes were silently lost. The fix adds `_merge_widget_attrs()` to `BaseAdapter`, called from `_render_input`, `_render_checkbox`, and `_render_radio`, which merges `field.widget.attrs` into the output attributes with djust-specific keys (`dj-change`, `name`, `class`, etc.) taking precedence over widget defaults. Boolean `False`/`None` values in widget attrs are filtered out to avoid rendering `disabled="False"`. 17 new regression tests in `python/tests/test_live_field_widget_attrs.py` covering: EmailInput placeholder/type, pattern/min/max/step/title, djust attrs override clashing widget attrs, empty widget attrs, textarea rows/cols, checkbox data-attrs, radio data-attrs on each option, select data-attrs, and boolean True/False handling.

- **VDOM patcher guards against text nodes for 5 patch types ([#622](https://github.com/djust-org/djust/issues/622))** — The VDOM diff patcher called `setAttribute()`, `removeAttribute()`, `appendChild()`, `removeChild()`, and `replaceChild()` on `#text` nodes, which don't implement these methods. This crashed conditional rendering whenever a text node sat where the patcher expected an element (common in `{% if %}` blocks that switch between text and element content). The fix adds an `isElement(node)` guard at the top of each of the five patch-type branches in `12-vdom-patch.js` — when the target is a non-element node (text, comment, CDATA), the patch is skipped gracefully instead of throwing. 4 new JS tests in `tests/js/vdom_patch_errors.test.js` covering setAttribute, removeAttribute, appendChild, and replaceChild on text nodes.

- **Autofocus handling on dynamically inserted elements ([#617](https://github.com/djust-org/djust/issues/617))** — Dynamically inserted `<input autofocus>` elements didn't receive focus after a VDOM patch because the browser only honours the `autofocus` attribute on initial page load. The patcher now detects `autofocus` on newly inserted elements after each patch cycle and calls `.focus()` explicitly. 4 new JS tests in `tests/js/vdom-autofocus.test.js` covering single autofocus, multiple elements (last wins), elements without autofocus ignored, and no-op when no autofocus elements are present.

- **Private `_` attributes preserved across events and reconnects ([#627](https://github.com/djust-org/djust/issues/627), [#611](https://github.com/djust-org/djust/issues/611))** — Two related state-management bugs caused any attribute starting with `_` (the documented convention for private/internal state) to be silently wiped. The root cause was that session save used the output of `get_context_data()`, which by design strips `_`-prefixed attributes. For #627, every WebSocket event round-trip lost private state because `_save_state_to_session()` persisted only public context. For #611, the pre-rendered WS reconnect path restored session state but never included private attributes set during the HTTP GET mount. The fix adds two helpers — `_get_private_state()` (collects all `_`-prefixed instance attrs that aren't dunder or in the base-class exclusion set) and `_restore_private_state(state_dict)` — and wires them into `_save_state_to_session()` (now persists private state under a `_private_state` session key) and `_load_state_from_session()` / the reconnect path in `RequestMixin._restore_session_state()` (restores private attrs before the view resumes). 20 new regression tests in `python/tests/test_private_attr_preservation.py` covering: private attrs survive event dispatch, survive reconnect, survive multiple sequential events, coexist with public attrs, handle None/complex/nested values, are excluded for dunder attrs, are excluded for base-class internals, and round-trip through session save/load.

- **Layout flash on pre-rendered mount: defer `reinitAfterDOMUpdate` via `requestAnimationFrame` ([#619](https://github.com/djust-org/djust/pull/619), fixes [#618](https://github.com/djust-org/djust/issues/618))** — Carry-over bugfix from v0.4.1. When a page is pre-rendered via HTTP GET, the WebSocket mount used to call `reinitAfterDOMUpdate()` synchronously right after stamping `dj-id` attributes onto the existing DOM. That synchronous call triggered a full DOM traversal for event binding, which forced the browser to recalculate layout mid-paint — and on pages with large pre-rendered elements (e.g. big dashboard stat values) the elements briefly rendered at the wrong size before settling, producing a visible layout-flash on every initial load. The fix moves the post-mount block (reinit + `_mountReady` flag + form recovery + auto-recover) into a `runPostMount` closure and schedules it via `requestAnimationFrame(runPostMount)` when available, falling back to a synchronous call when `requestAnimationFrame` is unavailable (JSDOM tests, exotic non-browser environments). Event binding now happens *after* the browser finishes its current paint, eliminating the flash entirely. The ordering invariant (reinit → `_mountReady` → form recovery) is preserved inside the closure so `dj-mounted` handlers and recovered form inputs still see bound event listeners. The non-prerendered `data.html` innerHTML-replace branch is unchanged — it already invalidates layout via the full DOM swap so there's no pre-paint to protect. 8 new regression tests in `tests/js/mount-deferred-reinit.test.js` asserting: the rAF wrapper is present, the synchronous fallback is preserved, the closure is named `runPostMount` for stable debugging, `reinitAfterDOMUpdate()` runs before `_mountReady` inside the closure, `_mountReady` is set inside the closure (not synchronously), form recovery runs only on reconnect inside the closure, the non-prerendered branch calls reinit synchronously, and exactly one call-site of `reinitAfterDOMUpdate()` exists in the skipMountHtml branch (so a refactor that reintroduces the sync call would immediately flip red). Closes #619 as superseded and closes the original #618 bug report.

- **Scaffolded projects now default `DEBUG=False` and generate `.env.example` ([#637](https://github.com/djust-org/djust/issues/637))** — Carry-over bugfix from v0.4.1. Previously, `python -m djust startproject mysite` and `python -m djust new mysite` both generated a `settings.py` with `DEBUG = True` and `ALLOWED_HOSTS = ["*"]` as hardcoded literals. A developer who deployed the scaffolded output without remembering to flip those values ran production with full stack traces, the `django-insecure-<random>` default SECRET_KEY, and a wildcard host allowlist — the exact footgun that A001 (`DEBUG` enabled) and A014 (`ALLOWED_HOSTS` too permissive) flag in `djust_audit`. Now both scaffold paths (`cli.py`'s `cmd_startproject` and the higher-level `djust.scaffolding.generator.generate_project`) emit `DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")` and `ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]` — unconfigured deployments fail safe. The scaffold also writes a `.env.example` template alongside `.gitignore` (which already ignores `.env`) so local development picks up developer-friendly values via `cp .env.example .env` + whatever `.env` loader the developer uses. The `.env.example` includes `DEBUG=True`, a freshly-generated `SECRET_KEY` token (via `secrets.token_urlsafe(50)`), and `ALLOWED_HOSTS=localhost,127.0.0.1` so the local experience hasn't changed. 4 new regression tests in `python/tests/test_cli_scaffold.py` asserting: `DEBUG = True` is no longer literal, `DEBUG` reads from env with `"False"` fallback, `ALLOWED_HOSTS = ["*"]` is no longer literal, narrow `localhost,127.0.0.1` env default, `.env.example` exists with the three documented vars and a real (not template-placeholder) secret key, `.env` remains in `.gitignore` while `.env.example` does not. Closes #637.

### Added

- **`TutorialMixin` + `TutorialStep` + `{% tutorial_bubble %}` — declarative guided tours ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c)** — Capstone of ADR-002 Phase 1: a one-import, zero-JavaScript way for any djust app to ship a real guided tour, onboarding flow, or wizard. Apps declare the tour as a list of `TutorialStep` dataclasses on a `LiveView` that mixes in `TutorialMixin`; the framework runs the state machine as a `@background` task, pushing a highlight + narrate + focus chain at each step's target via `push_commands` (Phase 1a), then either `asyncio.sleep`'ing for auto-advance steps or `await`ing `wait_for_event` (Phase 1b) until the user actually fires the matching `@event_handler`. Four event handlers come for free — `start_tutorial`, `skip_tutorial`, `cancel_tutorial`, `restart_tutorial` — along with three instance attributes (`tutorial_running`, `tutorial_current_step`, `tutorial_total_steps`) for progress display in the view state. `TutorialStep` supports per-step `target` (CSS selector, required), `message` (narration text), `position` (`top`/`bottom`/`left`/`right` bubble hint), `wait_for` (handler name to suspend on), `timeout` (seconds — pairs with `wait_for` for bounded waits or used alone for auto-advance), `on_enter`/`on_exit` (optional extra `JSChain` pushes for per-step setup/teardown beyond the default highlight + narrate + focus), and `highlight_class`/`narrate_event` (override per-step CSS class and CustomEvent name when you need different visual treatment). Skip and cancel signals are raced against the wait via `asyncio.wait(..., return_when=FIRST_COMPLETED)` so either unblocks the current step immediately; WebSocket disconnect cancels the background task automatically so there's no lingering work, no leaked waiters, no stuck highlights. A new `{% tutorial_bubble %}` template tag renders a floating narration bubble that listens for `tour:narrate` CustomEvents at `document` level (dispatched at the step's target with `bubbles: true`), positions itself next to the target per the step's `position` hint, displays `step N / total` progress, and includes "Skip" and "Close" buttons pre-bound to the mixin's event handlers — the default bubble is marked `dj-update="ignore"` so morphdom doesn't clobber it during VDOM patches. The new client-side `src/28-tutorial-bubble.js` module (~140 lines, brings `client.js` to 30 modules) registers its listeners unconditionally at IIFE time, reads `detail.text`/`target`/`position`/`step`/`total` from the event, and updates the bubble's text + progress + position + visibility. The framework ships no CSS — apps style the bubble and highlight class themselves (the guide includes a minimal starter block). 26 new Python tests for the mixin covering TutorialStep dataclass (minimal, custom position, invalid position, empty target, empty message, wait_for+timeout, on_enter/on_exit), lifecycle (initial state, empty-steps no-op, single step, setup+cleanup chain order, multi-step order, idempotent start-while-running), `wait_for_event` integration (step suspends on user action, timeout advances silently, indefinite wait), skip/cancel paths (advance past current, abort loop, no-op when not running), `on_enter`/`on_exit` pushes, per-step highlight class override, and per-step narrate event override. 9 new Python tests for the `tutorial_bubble` template tag covering defaults, custom `css_class`/`event`/`position`, invalid-position fallback to `"bottom"`, skip+cancel button bindings, text/progress element classes, and XSS escaping of hostile `css_class` and `event` kwargs. 12 new JS tests in `tests/js/tutorial-bubble.test.js` covering listener registration, text content updates, progress text updates, show/hide via `data-visible`, default/custom position application, missing-target graceful handling, missing-bubble graceful handling, `tour:hide` event, and repeated updates on subsequent events. Zero new runtime dependencies — stdlib `asyncio` + `dataclasses` + Django's `format_html`. Full documentation in the new `docs/website/guides/tutorials.md` guide with the simplest-possible example, state-machine description, `TutorialStep` reference, `wait_for`/`timeout` combinations table, `on_enter`/`on_exit` patterns, the bubble template tag docs, a starter CSS block, four usage patterns (auto-advance walkthrough, interactive onboarding, mixed, branching with custom handlers), skip/cancel UX, disconnect cleanup, debugging tips, and honest limitations (LiveComponent events don't propagate to parent waiters yet, actor-mode views bypass the dispatch hook, handler validation failures prevent the waiter from resolving except via timeout, single-user only — multi-user broadcast is Phase 4 in v0.5.x).

- **`await self.wait_for_event(name, timeout=None, predicate=None)` async primitive ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b)** — Second half of the backend-driven UI Phase 1 primitives. Adds a new `WaiterMixin` (automatically included in `LiveView`) that lets a `@background` handler suspend until a specific `@event_handler` is called by the user, optionally filtered by a predicate, optionally bounded by a timeout. The returned dict is the kwargs that were passed to the matching handler. This is the primitive that makes "highlight this button, wait for the user to actually click it, then advance to the next step" work declaratively — required by `TutorialMixin` (Phase 1c) and by any server-driven flow that needs to pause mid-plan until real user input arrives. Implementation: ~180 lines in `python/djust/mixins/waiters.py`, a ~15-line hook in `python/djust/websocket.py` that calls `_notify_waiters` after every successful handler invocation, a ~10-line cleanup hook in the WebSocket `disconnect` path that cancels all pending waiters when the view tears down (so `@background` tasks unblock with `CancelledError` instead of leaking), and proper integration into `LiveView`'s MRO via `python/djust/mixins/__init__.py`. The notify pass runs AFTER the handler completes so waiters created during a handler call aren't self-notified (prevents re-entrancy surprises where `wait_for_event("X")` inside an `X` handler would resolve against itself). Multiple concurrent waiters for the same event name all resolve with the same kwargs dict when that event fires — fan-out patterns work without manual coordination. Waiters for different event names are fully independent. A predicate that raises is treated as "no match" and logged via the `djust.waiters` logger, so a buggy predicate can't crash the event pipeline or deadlock a background task. 18 new Python tests covering: basic resolution, kwargs copy semantics, no-op on unmatched names, predicate filtering, predicate-that-raises treated as False with warning log, predicate=None matches any kwargs, timeout raises `asyncio.TimeoutError`, expired waiters removed from registry, indefinite waits without timeout, concurrent waiters for same event all resolve, waiters for different events are independent, partial resolution (some predicates match, others don't), `_cancel_all_waiters` unblocks pending futures with `CancelledError` and clears the registry, task cancellation removes the waiter, and stability under mid-iteration waiter-list mutation. Full documentation in the existing `docs/website/guides/server-driven-ui.md` guide with signature, predicate examples, concurrency semantics, timeouts and cleanup, composition with `push_commands`, and honest limitations (no component-event support yet, actor mode bypasses the hook, validation failures prevent handler execution which means waiters never resolve except via timeout).

- **`LiveView.push_commands(chain)` + `djust:exec` client-side auto-executor ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a)** — First half of the backend-driven UI primitives proposed in ADR-002. Adds a one-line server-side helper `self.push_commands(chain)` that takes a `djust.js.JSChain` (shipped in v0.4.1 as the JS Commands fluent API) and pushes it to the current session as a `djust:exec` push event carrying the chain's JSON-serialized `ops` list. The client half is a new framework-provided `src/27-exec-listener.js` module that listens for `djust:push_event` CustomEvents on `window`, filters for `event === 'djust:exec'`, and runs the ops via `window.djust.js._executeOps(ops, document.body)` — the same function that runs inline `dj-click="[[...]]"` JSON chains and fluent-API `.exec()` calls from hook code. No hook registration, no template markup, no user setup required: the auto-executor ships bound with `client.js` and is active on every djust page automatically. The server-side helper is type-safe — it rejects anything that isn't a `JSChain` with a clear `TypeError` pointing at the `JS.*` factory methods, preventing raw ops-list smuggling through the `push_event` path. `push_commands` and `push_event` share the same queue and preserve ordering, so handlers can interleave "push a flash message, add a CSS class, fire analytics, run an animation" in one deterministic sequence. 23 new Python tests covering single-op chains, multi-op ordering, empty chains, JSON round-trip, immutability of chains after push, type validation against strings/dicts/lists/None, queue composition with `push_event`, and per-op factory parity across all 11 JS Commands. 13 new JS tests in `tests/js/exec-listener.test.js` covering listener registration, single-op execution, multi-op ordering, multiple-class `add_class`, `focus`, `dispatch` with detail, filtering for non-`djust:exec` events, malformed-payload rejection (missing `ops`, non-array `ops`, missing detail), error resilience (one bad op doesn't break the chain), multiple independent exec fires, and end-to-end integration with the fluent `window.djust.js` chain factory. Zero new runtime dependencies. Full documentation in `docs/website/guides/server-driven-ui.md` with patterns, debugging tips, and pointers to Phase 1b (`wait_for_event`) and Phase 1c (`TutorialMixin`) still to come in v0.4.2.

## [0.4.1] - 2026-04-11

### Added

- **JS Commands — client-side DOM commands chainable from templates, views, hooks, and JavaScript** — Closes the single biggest DX gap vs Phoenix LiveView 1.0. Eleven commands (`show`, `hide`, `toggle`, `add_class`, `remove_class`, `transition`, `dispatch`, `focus`, `set_attr`, `remove_attr`, `push`) that run locally without a server round-trip, plus a `push` escape hatch that mixes in server events when needed. Four equivalent entry points: (1) **Python helper `djust.js.JS`** — fluent chain builder that stringifies to a JSON command list, wrapped in `SafeString` for safe template embedding (`<button dj-click="{{ JS.show('#modal').add_class('active', to='#overlay') }}">Open</button>`). (2) **Client-side `window.djust.js`** — mirror of the Python API with `camelCase` method names for direct JavaScript use (`window.djust.js.show('#modal').addClass('active', {to: '#overlay'}).exec()`). (3) **Hook API** — every `dj-hook` instance now has a `this.js()` method returning a chain bound to the hook element (Phoenix 1.0 parity for programmable JS Commands from hook lifecycle callbacks). (4) **Attribute dispatcher** — `dj-click` (and other event-binding attributes) detect whether the attribute value is a JSON command list (`[[...]]`) and execute it locally; plain handler names still fire server events as before (zero breaking changes). All commands support scoped targets: `to=<selector>` (absolute `document.querySelectorAll`), `inner=<selector>` (scoped to origin element's descendants), `closest=<selector>` (walk up the DOM from origin) — a single `<button dj-click="{{ JS.hide(closest='.modal') }}">Close</button>` works in every modal with no per-instance IDs. The `push` command accepts `page_loading=True` to show the navigation-level loading bar while the event round-trips. Chains are **immutable** — every chain method returns a new `JSChain`, so reusing a base chain across multiple call sites never cross-contaminates. **37 new Python tests** (every command + target validation + chain immutability + HTML/SafeString integration + template rendering) and **30 new JS tests** (every command executing against real DOM + target resolution + chain fluency + attribute dispatcher + backwards-compat for plain event names + `parseCommandValue` edge cases). Zero new dependencies — the Python helper is stdlib-only and the JS interpreter is ~350 lines in a new `src/26-js-commands.js` module. Full guide in `docs/website/guides/js-commands.md` with examples for templates, hooks, chaining, and the "when to reach for what" decision tree.

- **`dj-paste` — paste event handling** — New attribute that fires a server event when the user pastes content into a bound element (`<textarea dj-paste="handle_paste">`). The client extracts structured payload from the `ClipboardEvent` in one pass: `text` (`clipboardData.getData('text/plain')`), `html` (`getData('text/html')` for rich paste from Word/Google Docs/web pages), `has_files` (`bool`), and `files` (list of `{name, type, size}` metadata dicts for every file in `clipboardData.files`). When the element also carries a `dj-upload="<slot>"` attribute, the clipboard's `FileList` is routed through the existing upload pipeline — image-paste → chat, CSV-paste → table, etc. — via a new `window.djust.uploads.queueClipboardFiles(element, fileList)` export. Participates in the standard interaction pipeline (`dj-confirm`, `dj-lock`). By default the browser's native paste still happens so hybrid editors feel natural; add `dj-paste-suppress` to intercept fully (useful when routing image paste to an upload slot without dumping a data URL into a `<div contenteditable>`). Positional args in the attribute syntax (`dj-paste="handle_paste('chat', 42)"`) forward via `kwargs["_args"]`. 11 new JS tests covering text extraction, HTML extraction, file metadata, suppress flag, missing `clipboardData`, double-bind protection, positional args, upload routing with and without a `dj-upload` slot, and graceful degradation when `getData('text/html')` throws. ~80 lines JS. Full guide in `docs/website/guides/dj-paste.md`.

- **`djust_audit --ast` — AST security anti-pattern scanner ([#660](https://github.com/djust-org/djust/issues/660))** — Adds a new mode to `djust_audit` that walks the project's Python source and Django templates looking for five specific security anti-patterns, each motivated by a live vulnerability or near-miss in the 2026-04-10 a downstream consumer penetration test. Seven stable finding codes `djust.X001`–`djust.X007`: **X001** (ERROR) — possible IDOR: `Model.objects.get(pk=...)` inside a DetailView / LiveView without a sibling `.filter(owner=request.user)` (or `user=`, `tenant=`, `organization=`, `team=`, `created_by=`, `author=`, `workspace=`) scoping the queryset. **X002** (WARN) — state-mutating `@event_handler` without any permission check (no class-level `login_required`/`permission_required`, no `@permission_required`/`@login_required`). **X003** (ERROR) — SQL string formatting: `.raw()` / `.extra()` / `cursor.execute()` passed an f-string, a `.format()` call, or a `"..." % ...` binary-op. **X004** (ERROR) — open redirect: `HttpResponseRedirect(request.GET[...])` / `redirect(...)` without an `url_has_allowed_host_and_scheme` or `is_safe_url` guard in the enclosing function. **X005** (ERROR) — unsafe `mark_safe` / `SafeString` wrapping an interpolated string (XSS risk). **X006** (WARN) — template uses `{{ var|safe }}` (regex scan of `.html` files). **X007** (WARN) — template uses `{% autoescape off %}`. Suppression via `# djust: noqa X001` on the offending line, or `{# djust: noqa X006 #}` inside templates. New CLI flags: `--ast`, `--ast-path <dir>`, `--ast-exclude <prefix> [...]`, `--ast-no-templates`. Supports `--json` and `--strict` (fail on warnings too). 52 new tests covering positive + negative cases for every checker, management-command integration, template scanning, and noqa suppression. Zero new runtime dependencies — stdlib `ast` + `re`. Full documentation in `docs/guides/djust-audit.md` and `docs/guides/error-codes.md#ast-anti-pattern-scanner-findings-x0xx`. Closes the v0.4.1 audit-enhancement batch (#657/#659/#660/#661 all shipped).

- **New consolidated `djust_audit` command guide** — `docs/guides/djust-audit.md` documents all five modes of the command (default introspection, `--permissions`, `--dump-permissions`, `--live`, `--ast`), every CLI flag, CI integration examples, and exit-code conventions. Cross-linked from `docs/guides/security.md`.

- **Error code reference expanded with 44 new codes** — `docs/guides/error-codes.md` now covers the A0xx static audit checks (7 codes: A001, A010, A011, A012, A014, A020, A030), the P0xx permissions-document findings (7 codes: P001–P007), and the L0xx runtime-probe findings (30 codes: L001–L091). Every code gets severity, cause, fix, and a reference to the related issue/PR.

- **`{% live_input %}` template tag — standalone state-bound form fields for non-Form views ([#650](https://github.com/djust-org/djust/issues/650))** — `FormMixin.as_live_field()` and `WizardMixin.as_live_field()` render form fields with proper CSS classes, `dj-input`/`dj-change` bindings, and framework-aware styling — but only for views backed by a Django `Form` class. This leaves non-form views (modals, inline panels, search boxes, settings pages, anywhere state lives directly on view attributes) without an equivalent helper. The new `{% live_input %}` tag fills this gap with a lightweight alternative that needs no `Form` class or `WizardMixin`. Supports 12 field types (`text`, `textarea`, `select`, `password`, `email`, `number`, `url`, `tel`, `search`, `hidden`, `checkbox`, `radio`), explicit `event=` override (defaults sensibly per type — `text` → `dj-input`, `select`/`radio`/`checkbox` → `dj-change`, `hidden` → none), `debounce=`/`throttle=` passthrough, framework CSS class resolution via `config.get_framework_class('field_class')`, HTML attribute passthrough with underscore-to-dash normalisation (`aria_label="Search"` → `aria-label="Search"`), and a tested XSS escape boundary via a new shared `djust._html.build_tag()` helper. Example: `{% live_input "text" handler="search" value=query debounce="300" placeholder="Search..." %}`. 56 new tests including an explicit XSS matrix across every field type and attribute. See `docs/guides/live-input.md` for the full setup guide.

- **`djust_audit --live <url>` — runtime security-header and CSWSH probe ([#661](https://github.com/djust-org/djust/issues/661))** — Adds a new mode to `djust_audit` that fetches a running deployment with stdlib `urllib` and validates security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP, CORP), cookies (HttpOnly, Secure, SameSite on session/CSRF cookies), information-disclosure paths (`/.git/config`, `/.env`, `/__debug__/`, `/robots.txt`, `/.well-known/security.txt`), and optionally probes the WebSocket endpoint with `Origin: https://evil.example` to verify the CSWSH defense from [#653](https://github.com/djust-org/djust/issues/653) is actually enforced end-to-end. This catches the class of production issues where the setting is correctly configured in `settings.py` but the response is stripped, rewritten, or never emitted by the time it reaches the client — a downstream consumer pentest caught a critical `Content-Security-Policy missing` case this way (`django-csp` was configured but the header was absent from production responses, stripped by an nginx ingress). 30 new stable finding codes `djust.L001`–`djust.L091` cover every check class so CI configs can suppress specific codes by number. New CLI flags: `--live <url>`, `--paths` (multi-URL), `--no-websocket-probe`, `--header 'Name: Value'` (for staging auth), `--skip-path-probes` (for WAF-protected environments). Supports `--json` and `--strict` (fail on warnings too). Zero new runtime dependencies — stdlib `urllib` for HTTP, optional `websockets` package for the WebSocket probe (skipped with an INFO finding if not installed).

  See `docs/guides/djust-audit.md`.
- **New static security checks in `djust_check` / `djust_audit` ([#659](https://github.com/djust-org/djust/issues/659))** — Seven new check IDs fire from `check_configuration` when Django runs `python manage.py check`: **A001** (ERROR) — WebSocket router not wrapped in `AllowedHostsOriginValidator` (static-analysis companion to #653 for existing apps built from older scaffolds). **A010** (ERROR) — `ALLOWED_HOSTS = ["*"]` in production. **A011** (ERROR) — `ALLOWED_HOSTS` mixes `"*"` with explicit hosts (the wildcard makes the explicit entries meaningless). **A012** (ERROR) — `USE_X_FORWARDED_HOST=True` combined with wildcard `ALLOWED_HOSTS` enables Host header injection. **A014** (ERROR) — `SECRET_KEY` starts with `django-insecure-` in production (scaffold default not overridden before deployment). **A020** (WARNING) — `LOGIN_REDIRECT_URL` is a single hardcoded path but the project has multiple auth groups/permissions (catches the "every role lands on the same dashboard" anti-pattern). **A030** (WARNING) — `django.contrib.admin` installed without a known brute-force protection package (`django-axes`, `django-defender`, etc.). Each check has essentially zero false-positive risk, has a `fix_hint` pointing at the remediation, and was motivated by the 2026-04-10 a downstream consumer pentest report. **Out of scope for this PR:** manifest scanning (k8s/helm/docker-compose env blocks) — deferred to a follow-up. Python-level `settings.py` values cover the common case.

- **`djust_audit --permissions permissions.yaml` — declarative permissions document for CI-level RBAC drift detection ([#657](https://github.com/djust-org/djust/issues/657))** — Adds a new flag to `djust_audit` that validates every LiveView against a committed, human-readable YAML document describing the expected auth configuration for each view. CI fails on any deviation (view declared public but has auth in code, permission list mismatch, undeclared view in strict mode, stale declaration, etc.). This closes a structural gap the existing audit couldn't catch: `djust_audit` today can tell "no auth" from "some auth", but not that `login_required=True` should have been `permission_required=['claims.view_supervisor']`. The permissions document IS the ground truth. Seven stable error codes (`djust.P001` through `djust.P007`) cover every deviation class. Also adds `--dump-permissions` to bootstrap a starter YAML from existing code, and `--strict` to fail CI on any finding. Full documentation in `docs/guides/permissions-document.md`. Motivated by a downstream consumer pentest finding 10/11 where every view had `login_required=True` set and djust_audit reported them all as protected, but the lowest-privilege authenticated user could ID-walk the entire database.

- **`WizardMixin` for multi-step LiveView form wizards** — General-purpose mixin managing step navigation, per-step validation, and data collection for guided form flows. Provides `next_step`, `prev_step`, `go_to_step`, `update_step_field`, `validate_field`, and `submit_wizard` event handlers. Template context includes step indicators, progress, form data/errors, and pre-rendered field HTML via `as_live_field()`. Re-validates all steps on submission to guard against tampered WebSocket replays. ([#632](https://github.com/djust-org/djust/pull/632))

  See `docs/website/guides/wizards.md`.
### Security

- **LOW: Nonce-based CSP support — drop `'unsafe-inline'` from `script-src` / `style-src`** — djust's inline `<script>` and `<style>` emissions (handler metadata bootstrap in `TemplateMixin._inject_handler_metadata`, `live_session` route map in `routing.get_route_map_script`, and the PWA template tags `djust_sw_register`, `djust_offline_indicator`, `djust_offline_styles`) now read `request.csp_nonce` when available (set by [django-csp](https://django-csp.readthedocs.io/) when `CSP_INCLUDE_NONCE_IN` covers the relevant directive) and emit a `nonce="..."` attribute on the tag. When no nonce is available (django-csp not installed, or `CSP_INCLUDE_NONCE_IN` not set), the tags emit without a nonce attribute — fully backward compatible with apps still allowing `'unsafe-inline'`. Apps that want strict CSP can now set `CSP_INCLUDE_NONCE_IN = ("script-src", "script-src-elem", "style-src", "style-src-elem")` in `settings.py`, drop `'unsafe-inline'` from `CSP_SCRIPT_SRC` / `CSP_STYLE_SRC`, and get strict CSP XSS protection across all djust-generated inline content. The PWA tags `djust_sw_register`, `djust_offline_indicator`, and `djust_offline_styles` now use `takes_context=True` to read the request from the template context — they still work with the same template syntax (`{% djust_sw_register %}` etc.) as long as a `RequestContext` is used (Django's default for template rendering). See `docs/guides/security.md` for the full setup. Reported via external penetration test 2026-04-10 (FINDING-W06). Closes the v0.4.1 security hardening batch (#653 / #654 / #655). ([#655](https://github.com/djust-org/djust/issues/655))

- **MEDIUM: Gate VDOM patch timing/performance metadata behind `DEBUG` / `DJUST_EXPOSE_TIMING`** — `LiveViewConsumer` previously attached `timing` (handler/render/total ms) and `performance` (full nested timing tree with handler and phase names) to every VDOM patch response unconditionally, regardless of `settings.DEBUG`. Combined with CSWSH ([#653](https://github.com/djust-org/djust/issues/653)) this let cross-origin attackers observe server-side code-path timings, enabling timing-based code-path differentiation (DB hit vs cache miss, valid vs invalid CSRF), internal handler/phase name disclosure, and load-based DoS scheduling. Now gated on a new helper `_should_expose_timing()` which returns True only when `settings.DEBUG` or the new `settings.DJUST_EXPOSE_TIMING` is True. **Upgrade notes:** production behavior change — existing clients that consumed `response.timing` / `response.performance` in production will no longer see those fields; opt in via `DJUST_EXPOSE_TIMING = True` in settings for staging/profiling. The browser debug panel is unaffected (it receives timing via the existing `_attach_debug_payload` path, which is already gated on `DEBUG`). Reported via external penetration test 2026-04-10. References: CWE-203, CWE-215, OWASP A09:2021. ([#654](https://github.com/djust-org/djust/issues/654))

- **HIGH: Validate WebSocket Origin header to prevent Cross-Site WebSocket Hijacking (CSWSH)** — `LiveViewConsumer.connect()` previously accepted the WebSocket handshake without validating the `Origin` header, and `DjustMiddlewareStack` did not wrap the router in an origin validator. A cross-origin attacker could mount any LiveView and dispatch any event from a victim's browser. Now the consumer rejects disallowed origins with close code 4403 before accepting the handshake, and `DjustMiddlewareStack` wraps its inner application in `channels.security.websocket.AllowedHostsOriginValidator` by default (defense in depth). Missing Origin is still allowed so non-browser clients (curl, test `WebsocketCommunicator`) continue to work. **Upgrade notes:** ensure `settings.ALLOWED_HOSTS` does NOT contain `*` in production; if you need to opt out for a specific stack, use `DjustMiddlewareStack(inner, validate_origin=False)` (not recommended). Reported via external penetration test 2026-04-10. ([#653](https://github.com/djust-org/djust/issues/653))

- **Enforce `login_required` on HTTP GET path** — Views with `login_required = True` rendered full HTML to unauthenticated users on the initial HTTP GET. The WebSocket connection was correctly rejected, but the pre-rendered page content was already visible. Now calls `check_view_auth()` before `mount()` on HTTP GET and returns 302 to `LOGIN_URL`. Also calls `handle_params()` after `mount()` on HTTP GET to match the WebSocket path's behavior, preventing state flash on URL-param-dependent views. ([#636](https://github.com/djust-org/djust/pull/636), fixes [#633](https://github.com/djust-org/djust/issues/633), [#634](https://github.com/djust-org/djust/issues/634))

### Fixed

- **Prevent `SynchronousOnlyOperation` in `PerformanceTracker.track_context_size`** — The tracker called `sys.getsizeof(str(context))`, which triggered `QuerySet.__repr__()` on any unevaluated querysets in the context dict. `__repr__` calls `list(self[:21])`, evaluating the queryset against the database — raising `SynchronousOnlyOperation` in the async WebSocket path. Now uses a shallow per-value `getsizeof` sum that does not invoke `__repr__`/`__str__` on values, so lazy objects stay lazy. Size estimates are now slightly less precise (don't include recursive inner size) but safe in async contexts. ([#651](https://github.com/djust-org/djust/pull/651), fixes [#649](https://github.com/djust-org/djust/issues/649))

- **Apply RemoveChild patches before batched InsertChild in same parent group** — `applyPatches` in `client.js:1379-1440` was filtering `InsertChild` patches out of each parent group and applying them via `DocumentFragment` before iterating the group for the `RemoveChild` patches in that same parent, violating the top-level Remove → Insert phase order. This was latent for keyed content (monotonic dj-ids meant removes still found targets by ID), but fired for `<!--dj-if-->` placeholder comments — they have no dj-id (only elements get IDs), so their removes fall back to index-based lookup, and by the time the removes ran, the batched inserts had already prepended the new content and shifted indices. The removes then deleted the just-inserted content, leaving empty tab content on multi-tab views (symptom: a downstream consumer tab switches showing blank content after the first switch). Fix: split each parent group into non-Insert vs Insert lists, apply all non-Insert patches first in their phase-sorted order, then batch the inserts. ([#643](https://github.com/djust-org/djust/pull/643), fixes [#641](https://github.com/djust-org/djust/issues/641), closes [#642](https://github.com/djust-org/djust/pull/642))

- **`dj-patch` on `<a>` tags uses href when attribute value is empty** — Boolean `dj-patch` on anchor elements (`<a href="?tab=docs" dj-patch>`) was resolving to the current URL instead of the href destination. Now falls back to `el.getAttribute('href')` when `dj-patch` is empty and the element is `<a>`. ([#640](https://github.com/djust-org/djust/pull/640))

- **Normalize Model instances in `render_full_template` before passing to Rust** — Django FK fields are class-level descriptors not present in `__dict__`. Rust's `FromPyObject` extracts `__dict__` which has `claimant_id=1` (raw FK int) instead of the related object. Now always calls `normalize_django_value()` on pre-serialized context so FK relationships are resolved via `getattr()` and traversable with dot notation (`{{ claim.claimant.first_name }}`). ([#639](https://github.com/djust-org/djust/pull/639))

- **Render Django Form/BoundField to SafeString HTML in template context** — `{{ form.field_name }}` rendered as empty string because the Rust renderer extracted `Form.__dict__` which doesn't contain computed `BoundField` attributes. Now pre-renders Form and BoundField objects to SafeString HTML via `widget.render()` in all four code paths (serialization, template serialization, template rendering, and LiveView state sync). ([#631](https://github.com/djust-org/djust/pull/631), fixes [#621](https://github.com/djust-org/djust/issues/621))

- **Correct `has_ids` attribute name in WebSocket mount response** — `websocket.py` checked for `"data-dj-id="` but the Rust renderer emits `"dj-id="` attributes. This caused `_stampDjIds()` to be skipped on pre-rendered pages, breaking VDOM patches for large content swaps (e.g. tab switching) while small patches still worked. The SSE path already had the correct check. ([#630](https://github.com/djust-org/djust/pull/630), fixes [#629](https://github.com/djust-org/djust/issues/629))

- **Sync input `.value` from attribute after innerHTML/VDOM patch** — When navigating backward in a multi-step wizard, text input values were not visually restored even though the server sent correct VDOM patches. `setAttribute('value', x)` only updates the HTML attribute (defaultValue), not the `.value` DOM property. Now syncs `.value` from the attribute in `preserveFormValues()`, broadcast patches, and `morphElement()`. Skips focused inputs, checkboxes, radios, and file inputs. ([#625](https://github.com/djust-org/djust/pull/625), fixes [#624](https://github.com/djust-org/djust/issues/624))

## [0.4.0] - 2026-03-27

### Security

- **Fix 25 CodeQL code-scanning alerts in client.js and debug-panel.js** — Added UNSAFE_KEYS guard to VDOM SetAttr/RemoveAttr patches (rejects `__proto__`, `constructor`, `prototype` keys), replaced direct property assignment with `Object.defineProperty()` in debug panel state cloning, converted template literal logs to format strings to prevent log injection, and added XSS suppression comments for trusted server-rendered HTML. ([#597](https://github.com/djust-org/djust/pull/597))

### Removed

- **`whitenoise` dependency** — djust's `ASGIStaticFilesHandler` in `djust.asgi.get_application()` already handles static file serving at the ASGI layer, making WhiteNoise middleware redundant. Removed `whitenoise` from dependencies, scaffolded projects, and the demo project. Removed system check `C006` (daphne without WhiteNoise). ([#584](https://github.com/djust-org/djust/issues/584))

### Added

- **`{% dj_flash %}` template tag in Rust renderer** — Registered `DjFlashTagHandler` so the flash container renders correctly when templates are processed by the Rust engine. Previously, the tag was only registered as a Django template tag and silently dropped by the Rust renderer. ([#590](https://github.com/djust-org/djust/pull/590))

  See `docs/website/guides/flash-messages.md`.
- **Navigation lifecycle events and CSS class** — `djust:navigate-start` / `djust:navigate-end` CustomEvents and `.djust-navigating` CSS class on `[dj-root]` during `dj-navigate` transitions. Enables CSS-only page transitions without monkey-patching `pageLoading`. ([#585](https://github.com/djust-org/djust/issues/585))

  See `docs/website/core-concepts/events.md`.
- **`manage.py djust_doctor` diagnostic command** -- checks Rust extension, Python/Django versions, Channels, Redis, templates, static files, routing, and ASGI server in one command. Supports `--json`, `--quiet`, `--check NAME`, and `--verbose` flags.

- **Enhanced VDOM patch error messages** -- patch failures now include patch type, `dj-id`, parent element info, and suggested causes (third-party DOM modification, `{% if %}` block changes). In `DEBUG_MODE`, a console group with full patch detail is shown. Batch failure summaries include which patch indices failed.

  See `docs/website/guides/flash-messages.md`.
- **DEBUG-mode enriched WebSocket errors** -- `send_error` includes `debug_detail` (unsanitized message), `traceback` (last 3 frames), and `hint` (actionable suggestion) when `settings.DEBUG=True`. `handle_mount` lists available LiveView classes when class lookup fails.

  See `docs/website/guides/error-overlay.md`.
- **Debug panel warning interceptor** -- intercepts `console.warn` calls matching `[LiveView]` prefix and surfaces them as a warning badge on the debug button. Configurable auto-open via `LIVEVIEW_CONFIG.debug_auto_open_on_error`.

  See `docs/website/advanced/debug-panel.md`.
- **Latency simulator in debug panel** -- test loading states and optimistic updates with simulated network delay. Presets (Off/50/100/200/500ms), custom value, jitter control, localStorage persistence, and visual badge on the debug button. Latency is injected on both WebSocket send and receive for full round-trip simulation. Only active when `DEBUG_MODE=true`.

  See `docs/website/advanced/debug-panel.md`.
- **Form recovery on reconnect** — After WebSocket reconnects, form fields with `dj-change` or `dj-input` automatically fire change events to restore server state. Compares DOM values against server-rendered defaults and only fires for fields that differ. Use `dj-no-recover` to opt out individual fields. Fields inside `dj-auto-recover` containers are skipped (custom handler takes precedence). Works over both WebSocket and SSE transports.

- **Reconnection backoff with jitter** — Exponential backoff with random jitter (AWS full-jitter strategy) prevents thundering herd on server restart. Min delay 500ms, max delay 30s, increased from 5 to 10 max attempts. Attempt count shown in reconnection banner (`dj-reconnecting-banner` CSS class) and exposed via `data-dj-reconnect-attempt` attribute and `--dj-reconnect-attempt` CSS custom property on `<body>`. Banner and attributes cleared on successful reconnect or intentional disconnect.

  See `docs/website/guides/reconnection.md`.
- **`page_title` / `page_meta` dynamic document metadata** — Update `document.title` and `<meta>` tags from any LiveView handler via property setters (`self.page_title = "..."`, `self.page_meta = {"description": "..."}`). Uses side-channel WebSocket messages (no VDOM diff needed). Supports `og:` and `twitter:` meta tags with correct `property` attribute. Works over both WebSocket and SSE transports.

- **`dj-copy` enhancements** — Selector-based copy (`dj-copy="#code-block"` copies the element's `textContent`), configurable feedback text (`dj-copy-feedback="Done!"`), CSS class feedback (`dj-copy-class` adds a custom class for 2s, default `dj-copied`), and optional server event (`dj-copy-event="copied"` fires after successful copy for analytics). Backward compatible with existing literal copy behavior.

- **`dj-auto-recover` attribute for reconnection recovery** — After WebSocket reconnects, elements with `dj-auto-recover="handler_name"` automatically fire a server event with serialized DOM state (form field values and `data-*` attributes from the container). Enables the server to restore custom state lost during disconnection. Does not fire on initial page load. Supports multiple independent recovery elements per page.

- **`dj-debounce` / `dj-throttle` HTML attributes** — Apply debounce or throttle to any `dj-*` event attribute (`dj-click`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`) directly in HTML: `<button dj-click="search" dj-debounce="300">`. Takes precedence over `data-debounce`/`data-throttle`. Supports `dj-debounce="blur"` to defer until element loses focus (Phoenix parity). `dj-debounce="0"` disables default debounce on `dj-input`. Each element gets its own independent timer.

- **Connection state CSS classes** — `dj-connected` and `dj-disconnected` classes are automatically applied to `<body>` based on WebSocket/SSE transport state. Enables CSS-driven UI feedback for connection status (e.g., dimming content, showing offline banners). Both classes are removed on intentional disconnect (TurboNav). Phoenix LiveView's `phx-connected`/`phx-disconnected` equivalent.

- **`dj-cloak` attribute for FOUC prevention** — Elements with `dj-cloak` are hidden (`display: none !important`) until the WebSocket/SSE mount response is received, preventing flash of unconnected content. CSS is injected automatically by client.js — no user stylesheet changes needed. Phoenix LiveView's `phx-no-feedback` equivalent.

- **Page loading bar for navigation transitions** — NProgress-style thin loading bar at the top of the page during TurboNav and `live_redirect` navigation. Always active by default. Exposed as `window.djust.pageLoading` with `start()`, `finish()`, and `enabled` for manual control. Disable via `window.djust.pageLoading.enabled = false` or CSS override.

  See `docs/website/guides/navigation.md`.
- **`dj-scroll-into-view` attribute for auto-scroll on render** — Elements with `dj-scroll-into-view` are automatically scrolled into view after DOM updates (mount, VDOM patch). Supports scroll behavior options: `""` (smooth/nearest, default), `"instant"`, `"center"`, `"start"`, `"end"`. One-shot per DOM node — uses WeakSet tracking so the same element isn't re-scrolled on every patch, but VDOM-replaced fresh nodes scroll correctly.

  See `docs/website/core-concepts/events.md`.
- **`dj-window-*` / `dj-document-*` event scoping** — Bind event listeners on `window` or `document` while using the declaring element for context extraction (component_id, dj-value-* params). Supports `dj-window-keydown`, `dj-window-keyup`, `dj-window-scroll`, `dj-window-click`, `dj-window-resize`, `dj-document-keydown`, `dj-document-keyup`, `dj-document-click`. Key modifier filtering (e.g., `dj-window-keydown.escape="close_modal"`) works the same as `dj-keydown`. Scroll and resize events default to 150ms throttle. Phoenix LiveView's `phx-window-*` equivalent, plus `dj-document-*` as a djust extension.

  See `docs/website/core-concepts/events.md`.
- **`dj-click-away` attribute** — Fire a server event when the user clicks outside an element: `<div dj-click-away="close_dropdown">`. Uses capture-phase document listener so `stopPropagation()` inside the element doesn't prevent detection. Supports `dj-confirm` for confirmation dialogs and `dj-value-*` params from the declaring element.

- **`dj-shortcut` attribute for declarative keyboard shortcuts** — Bind keyboard shortcuts on any element with modifier key support: `<div dj-shortcut="ctrl+k:open_search:prevent, escape:close_modal">`. Supports `ctrl`, `alt`, `shift`, `meta` modifiers, comma-separated multiple bindings, and `prevent` modifier to suppress browser defaults. Shortcuts are automatically skipped when the user is typing in form inputs (override with `dj-shortcut-in-input` attribute). Event params include `key`, `code`, and `shortcut` (the matched binding string).

- **`_target` param in form change/input events** — When multiple form fields share one `dj-change` or `dj-input` handler, the `_target` param now includes the triggering element's `name` (or `id`, or `null`), letting the server know which field changed. For `dj-submit`, includes the submitter button's name if available. Matches Phoenix LiveView's `_target` convention.

  See `docs/website/core-concepts/events.md`.
- **`dj-disable-with` attribute for submit buttons** — Automatically disable submit buttons during form submission and replace their text with a loading message: `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and gives instant visual feedback. Works with both `dj-submit` forms and `dj-click` buttons. Original text is restored after server response.

- **`dj-lock` attribute for concurrent event prevention** — Disable an element until its event handler response arrives from the server: `<button dj-click="save" dj-lock>Save</button>`. Prevents rapid double-clicks from triggering duplicate server events. For non-form elements (e.g., `<div>`), applies a `djust-locked` CSS class instead of the `disabled` property. All locked elements are unlocked on server response.

  See `docs/website/core-concepts/events.md`.
- **`dj-mounted` event for element lifecycle** — Fire a server event when an element with `dj-mounted="handler_name"` enters the DOM after a VDOM patch: `<div dj-mounted="on_chart_ready" dj-value-chart-type="bar">`. Does not fire on initial page load (only after subsequent patches). Includes `dj-value-*` params from the mounted element. Uses a WeakSet to prevent duplicate fires for the same DOM node.

  See `docs/website/core-concepts/events.md`.
- **Priority-aware event queue for broadcast and async updates** — Server-initiated broadcasts (`server_push`) and async completions (`_run_async_work`) are now tagged with `source="broadcast"` and `source="async"` respectively, and the client buffers them during pending user event round-trips (same as tick buffering from #560). `server_push` now acquires the render lock and yields to in-progress user events to prevent version interleaving. Client-side pending event tracking upgraded from single ref to `Set`-based tracking, supporting multiple concurrent pending events. Buffer flushes only when all pending events resolve.

- **`manage.py djust_gen_live` — Model-to-LiveView scaffolding generator** — Generate a complete CRUD LiveView scaffold from a model name and field definitions: `python manage.py djust_gen_live blog Post title:string body:text`. Creates views.py (with `@event_handler` CRUD operations), urls.py (using `live_session()` routing), HTML template (with `dj-*` directives), and tests.py. Supports `--dry-run`, `--force`, `--no-tests`, `--api` (JSON mode) options. Handles all Django field types including FK relationships. Search uses `Q` objects for OR logic across text fields.

  See `docs/guides/scaffolding.md`.
- **`on_mount` hooks for cross-cutting mount logic** — Module-level hooks that run on every LiveView mount, declared via `@on_mount` decorator and `on_mount` class attribute. Use cases: authentication checks, telemetry, tenant resolution, feature flags. Hooks run after auth checks, before `mount()`. Return a redirect URL string to halt the mount pipeline. Hooks are inherited via MRO (parent-first, deduplicated). Includes V009 system check for validation. Phoenix `on_mount` v0.17+ parity.

  See `docs/website/guides/on-mount-hooks.md`.
- **`put_flash(level, message)` and `clear_flash()` for ephemeral flash notifications** — Phoenix `put_flash` parity. Queue transient messages (info, success, warning, error) from any event handler; they are flushed to the client over WebSocket/SSE after each response. Includes `{% dj_flash %}` template tag with auto-dismiss and ARIA `role="status"` / `role="alert"` support. ([#568](https://github.com/djust-org/djust/pull/568))

  See `docs/website/guides/flash-messages.md`.
- **`handle_params` called on initial mount** — `handle_params(params, uri)` is now invoked after `mount()` on the initial WebSocket connect, not just on subsequent URL changes. This matches Phoenix LiveView's `handle_params/3` contract and eliminates the need to duplicate URL-parsing logic between `mount()` and `handle_params()`. Views that don't override `handle_params` are unaffected (default is a no-op).

  See `docs/website/core-concepts/liveview.md`.
- **`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs: `<button dj-click="delete" dj-value-id:int="{{ item.id }}" dj-value-type="soft">`. Supports type-hint suffixes (`:int`, `:float`, `:bool`, `:json`, `:list`), kebab-to-snake_case conversion, and prototype pollution prevention. Works with all event types: `dj-click`, `dj-submit`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`, `dj-blur`, `dj-focus`, `dj-poll`. Phoenix LiveView's `phx-value-*` equivalent.

  See `docs/website/core-concepts/events.md`.
### Fixed

- **`True`/`False`/`None` literals resolved as empty string in custom tag args** — `get_value()` didn't recognize Python boolean/None literals, so `{% tag show_labels=False %}` produced `show_labels=` (empty string) instead of `show_labels=False`. Now handles `True`/`true`, `False`/`false`, and `None`/`none` as literal values. ([#602](https://github.com/djust-org/djust/pull/602))
- **Flash and page_metadata not delivered over HTTP POST fallback** — `put_flash()` and `page_title`/`page_meta` side-channel commands were only flushed over WebSocket. HTTP POST responses now drain `_pending_flash` and `_pending_page_metadata` and include them as `_flash` and `_page_metadata` arrays in the JSON response. ([#590](https://github.com/djust-org/djust/pull/590))
- **Custom tag args containing lists/objects serialized as `[List]`/`[Object]`** — `Value::List` and `Value::Object` in custom tag arguments were stringified via the `Display` trait, destroying structured data before it reached Python handlers. Now serialized as JSON via `serde_json`. ([#589](https://github.com/djust-org/djust/issues/589))
- **Django filters not applied in custom tag arguments** — `{% tag key=var|length %}` rendered the literal string instead of the computed value because arg resolution used `context.get()` (plain lookup) instead of `get_value()` (filter-aware). ([#591](https://github.com/djust-org/djust/pull/591))
- **`{% if %}` inside HTML tag after `{{ variable }}` emits `<!--dj-if-->` comment** — `is_inside_html_tag()` only checked the immediately preceding token, missing tag context when `{{ variable }}` tokens appeared between the tag opening and `{% if %}`. Added `is_inside_html_tag_at()` that scans all preceding tokens. ([#580](https://github.com/djust-org/djust/issues/580))
- **Tick/event version mismatch silently drops user input** — Server-initiated ticks could collide with user events, causing VDOM version divergence that silently discarded patches. Added server-side `asyncio.Lock` to serialize tick and event render operations, priority yielding so ticks skip during user events, client-side tick patch buffering during pending event round-trips, and monotonic event ref tracking for request/response matching. ([#560](https://github.com/djust-org/djust/issues/560))

- **Focus lost during VDOM patches** — When the server pushed VDOM patches (e.g., updating a counter while the user was typing), the focused input/textarea lost focus, cursor position, selection range, and scroll position. Added `saveFocusState()` / `restoreFocusState()` around the `applyPatches()` cycle to capture and restore `activeElement`, `selectionStart`/`selectionEnd`, and `scrollTop`/`scrollLeft`. Element matching uses id → name → dj-id → positional index. Broadcast (remote) updates correctly skip focus restoration.

- **VDOM patching fails when `{% if %}` blocks add/remove DOM elements** — Comment node placeholders (`<!--dj-if-->`) emitted by the Rust template engine were excluded from client-side child index resolution (`getSignificantChildren` and `getNodeByPath`), causing path traversal errors and silent patch failures. Also added `#comment` handling to `createNodeFromVNode` so comment placeholders can be correctly created during `InsertChild` patches. ([#559](https://github.com/djust-org/djust/issues/559))

## [0.3.8] - 2026-03-19

### Fixed

- **Tick auto-refresh causes VDOM version mismatch, silently drops user events** — `_run_tick` always called `render_with_diff()` even when `handle_tick()` made no state changes, incrementing the VDOM version on every tick. When a user event interleaved with a tick, the client and server versions diverged, causing all subsequent patches to be silently discarded. Tick now uses `_snapshot_assigns` to skip render when no public assigns changed. ([#560](https://github.com/djust-org/djust/issues/560))
- **WS VDOM cache key collision across tabs** — All WebSocket LiveViews shared a single RustLiveView cache slot keyed by `/ws/live/`, causing multi-tab sessions to overwrite each other's compiled templates. Cache key now uses `request.path` (the actual page URL) so each view gets its own VDOM baseline. ([#561](https://github.com/djust-org/djust/pull/561))
- **Canvas `width`/`height` cleared during `html_update` morph** — `morphElement` removed attributes absent from server HTML, resetting canvas 2D contexts and blanking Chart.js charts. Canvas `width` and `height` are now preserved during attribute sync. ([#561](https://github.com/djust-org/djust/pull/561))
- **`_force_full_html` not checked in `handle_url_change`** — Views that set `_force_full_html = True` in `handle_params` (e.g., when `{% for %}` loop lengths change) still received VDOM patches instead of full HTML. The flag is now checked after `render_with_diff()` in both `handle_event` and `handle_url_change`. ([#559](https://github.com/djust-org/djust/issues/559), [#561](https://github.com/djust-org/djust/pull/561))

### Added

- **`dj-patch` on selects/inputs uses WS `url_change`** — Select and input elements with `dj-patch` now update via pushState + WebSocket `url_change` instead of full page reload. A delegated `document` change listener survives DOM replacement by morphdom. `dj-patch-reload` attribute remains as an opt-in escape hatch for full page navigation. ([#561](https://github.com/djust-org/djust/pull/561))

## [0.3.7] - 2026-03-16

### Fixed

- **FormMixin: serialization, event handling, and ModelForm support** — Fixed 6 issues blocking production use of `FormMixin` with `ModelForm` over WebSocket: added `@event_handler` to `submit_form()` and `validate_field()`; renamed `form_instance` to private `_form_instance` with backward-compatible property; store `model_pk`/`model_label` as public attributes for re-hydration after WS session restore; sync `form_data` from saved instance after `form_valid()`; use FK PK instead of related object; auto-populate `form_choices` with serializable tuples. ([#545](https://github.com/djust-org/djust/pull/545))
- **`dj-hook` elements not re-initialized after `html_update` or `html_recovery`** — When VDOM patches failed and djust fell back to full HTML replacement, `updateHooks()` was never called, leaving hook elements stale (charts showing old data, canvases empty). Added `updateHooks()` to all DOM replacement paths: `html_update`, `html_recovery`, TurboNav reinit, embedded view update, lazy hydration, and streaming updates. ([#548](https://github.com/djust-org/djust/pull/548))
- **`__version__` not updated by `make version`** — `make version` only updated `pyproject.toml` and `Cargo.toml` but not the hardcoded `__version__` in `__init__.py` files. `djust.__version__` now stays in sync with the package version. ([#547](https://github.com/djust-org/djust/issues/547))

### Changed

- **Extract `reinitAfterDOMUpdate()` to DRY up post-DOM-update calls** — The repeated pattern of `initReactCounters()` + `initTodoItems()` + `bindLiveViewEvents()` + `updateHooks()` across 10+ call sites is now a single function. New DOM replacement paths only need one call. ([#549](https://github.com/djust-org/djust/issues/549))
- **Extract `addEventContext()` to consolidate component/embedded view ID extraction** — The 8-line `getComponentId`/`getEmbeddedViewId` pattern appeared 4 times in event binding; now a single helper. ([#551](https://github.com/djust-org/djust/issues/551))
- **Extract `isWSConnected()` to replace WebSocket state guard chains** — The `liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN` pattern appeared across 4 files; now a single predicate. ([#552](https://github.com/djust-org/djust/issues/552))
- **Extract `clearOptimisticPending()` to consolidate CSS class cleanup** — The `querySelectorAll('.optimistic-pending')` removal loop appeared 4 times across 2 files; now a single function. ([#553](https://github.com/djust-org/djust/issues/553))
- **Standardize `DJUST_CONFIG` access via `get_djust_config()`** — Replaced 10+ inline `getattr(settings, "DJUST_CONFIG", {})` try/except blocks across tenants, PWA, and storage modules with a single `get_djust_config()` helper in `config.py`. ([#554](https://github.com/djust-org/djust/issues/554))
- **Extract generic `BackendRegistry` class** — The duplicated lazy-init / set / reset pattern in `state_backends/registry.py` and `backends/registry.py` now delegates to a shared `BackendRegistry` class in `utils.py`. ([#555](https://github.com/djust-org/djust/issues/555))
- **Extract `is_model_list()` helper** — The repeated `isinstance(value, list) and value and isinstance(value[0], models.Model)` check is now a single `is_model_list()` function in `utils.py`, used in `mixins/context.py` and `mixins/request.py`. ([#556](https://github.com/djust-org/djust/issues/556))

## [0.3.6] - 2026-03-14

### Breaking Changes

- **`model.id` now returns the native type, not a string** — `_serialize_model_safely()` previously wrapped `obj.pk` with `str()` when producing the `"id"` key, causing template comparisons like `{% if edit_id == todo.id %}` to fail silently when `edit_id` was an integer. `model.id` now matches `model.pk` and returns the native Python type (e.g. `int`, `UUID`). **Migration:** if your templates or event handlers compare `model.id` against string literals or string-typed variables, update them to use the native type. PR #262 fixed `.pk`; this PR (#472) completes the fix for `.id`.

### Fixed

- **Skip redundant `mount()` on WebSocket connect for pre-rendered pages** — When the client sends `has_prerendered=true` on WS connect and saved state exists in the session (written during the HTTP GET), the view's attributes are restored from session instead of re-running `mount()`. This eliminates the double page-load cost for views with expensive `mount()` implementations (e.g. directory scans, API calls). Falls back to calling `mount()` normally when no saved state is found. `_ensure_tenant()` is now called unconditionally before the restore/mount decision, fixing a regression where multi-tenant views had `self.tenant=None` on WS connect for pre-rendered pages. ([#542](https://github.com/djust-org/djust/pull/542))
- **`djust cache --all` now correctly clears all sessions on the Redis backend** — The CLI called `cleanup_expired(ttl=0)` to force-clear sessions, but the semantics of `ttl=0` changed in 0.3.5 to mean "never expire". The command now calls the explicit `delete_all()` method, which uses a Redis pipeline for an efficient single round-trip bulk delete. ([#409](https://github.com/djust-org/djust/pull/409))
- **`dj-params` attribute no longer silently dropped** — Between 0.3.2 and 0.3.6rc2, `dj-params` was removed from the client event-binding code. Templates using `dj-params='{"key": value}'` continued to fire click events but the server received `params: {}`. The attribute is now read and merged into the params object for backward compatibility. A `console.warn` is emitted in debug mode (`globalThis.djustDebug`) to notify developers to migrate. ([#469](https://github.com/djust-org/djust/pull/469))
- **Prefetch Set not cleared on SPA navigation** — The client-side `_prefetched` Set persisted across `live_redirect` navigations, preventing links on the new view from being prefetched. Added `clear()` to `window.djust._prefetch` and call it in `handleLiveRedirect()` so each SPA navigation starts with a fresh prefetch state. ([#402](https://github.com/djust-org/djust/pull/402))
- **Auto-reload on unrecoverable VDOM state** — When VDOM patch recovery fails because recovery HTML is unavailable (e.g. after server restart), the client now auto-reloads the page instead of showing a confusing error overlay. The server sends `recoverable: false` to signal the client. ([#421](https://github.com/djust-org/djust/pull/421))
- **`{% djust_pwa_head %}` and other custom tags with quoted arguments containing spaces now render correctly** — The Rust template lexer used `split_whitespace()` to tokenize tag arguments, which broke quoted values like `name="My App"` into separate tokens (`name="My` and `App"`). This caused the downstream Python handler to receive malformed arguments, silently returning empty output. Replaced with a quote-aware splitter (`split_tag_args`) that preserves quoted strings as single arguments. ([#419](https://github.com/djust-org/djust/pull/419))
- **`{% load %}` tags stripped during template inheritance, breaking inclusion tags** — The Rust parser treated `{% load %}` as `Node::Comment`, which `nodes_to_template_string()` discarded during inheritance reconstruction. When the resolved template was re-parsed, custom tags that relied on Django tag libraries (e.g. `{% djust_pwa_head %}`) could silently fail. Fixed by adding a dedicated `Node::Load` variant that preserves library names through reconstruction. Also improved `_render_django_tag()` error handling: failures now log a full traceback via `logger.exception()` and return a visible HTML comment instead of an empty string. ([#418](https://github.com/djust-org/djust/pull/418))
- **Checkbox/radio `checked` and `<option>` `selected` state not updated by VDOM patches** — `SetAttr` and `RemoveAttr` patches only called `setAttribute`/`removeAttribute`, which updates the HTML attribute but not the DOM property. After user interaction the browser separates the two, so server-driven state changes via `dj-click` had no visible effect on checkboxes, radios, or select options. Fixed by syncing the DOM property alongside the attribute. Also fixed `createNodeFromVNode` to set `.checked`/`.selected` when creating new elements. ([#422](https://github.com/djust-org/djust/pull/422))
- **`SESSION_TTL=0` breaks all event handling (no DOM patches)** — `cleanup_expired()` methods in both `InMemoryStateBackend` and `RedisStateBackend` now treat `TTL ≤ 0` as "never expire". Previously `SESSION_TTL=0` caused `cutoff = time.time() - 0`, making all sessions appear expired, deleting them immediately, and leaving no state for VDOM patches. ([#395](https://github.com/djust-org/djust/issues/395))
- **WebSocket session extraction crashes on Django Channels `LazyObject`** — Replaced `hasattr(scope_session, "session_key")` with `getattr(scope_session, "session_key", None)` in the consumer's request context builder. `hasattr()` on a Django Channels `LazyObject` can raise non-`AttributeError` exceptions during lazy evaluation, causing the consumer to crash silently. ([#396](https://github.com/djust-org/djust/issues/396))

### Deprecated

- **`dj-params` JSON blob attribute** — Use individual `data-*` attributes with optional type-coercion suffixes instead. `dj-params` will be removed in a future release.

  **Migration guide (0.3.2 → 0.3.6):**

  ```html
  <!-- Before (0.3.2) -->
  <button dj-click="start_edit" dj-params='{"todo_id": {{ todo.id }}}'>Edit</button>
  <button dj-click="set_filter" dj-params='{"filter_value": "all"}'>All</button>

  <!-- After (0.3.6+) -->
  <button dj-click="start_edit" data-todo-id:int="{{ todo.id }}">Edit</button>
  <button dj-click="set_filter" data-filter-value="all">All</button>
  ```

  Type-coercion suffixes: `:int`, `:float`, `:bool`, `:json`. Kebab-case attribute names are auto-converted to `snake_case` for server handler parameters.

### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Available via the `djust-deploy` entry point after installation. ([#437](https://github.com/djust-org/djust/pull/437))
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout
  - `--server` flag / `DJUST_SERVER` env var to override the default server URL (`https://djustlive.com`)
  See `docs/website/guides/djust-deploy.md`.
- **TypeScript type stubs updated** — `DjustStreamOp` now includes `"done"` and `"start"` operation types and an optional `mode` field (`"append" | "replace" | "prepend"`). `getActiveStreams()` return type changed from `Map` to `Record`.
  See `docs/website/guides/typecheck.md`.
- **`.flex-between` CSS utility class** — Added to demo project's `utilities.css` for laying out flex children horizontally with space-between. Use on card headers or any flex container that needs a title on the left and action widget on the right. ([#397](https://github.com/djust-org/djust/issues/397))
  See `docs/website/guides/css-frameworks.md`.
- **Debug toolbar state size visualization** — New "Size Breakdown" table in State tab shows per-variable memory and serialized byte sizes with human-readable formatting (B/KB/MB). Added `_debug_state_sizes()` method to `PostProcessingMixin` included in both mount and event debug payloads. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel TurboNav persistence** — Event, patch, network, and state history now persist across TurboNav navigation via sessionStorage (30s window). Panel state restores on next page if navigated within 30 seconds. ([#459](https://github.com/djust-org/djust/pull/459))
  See `docs/website/advanced/debug-panel.md`.
- **TurboNav integration guide** — Comprehensive guide covering setup, navigation lifecycle, inline script handling, known caveats, and design decisions: `docs/guides/turbonav-integration.md`. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel search extended to Network and State tabs** — The search bar in the debug panel now filters across all data tabs. The Network tab shows a `N / total` count label when a query narrows the message list (#530). The State tab filters history entries by trigger, event name, and serialized state content, with the same `N / total` count label (#520). Overlapping `nameFilter` and `searchQuery` on the Events tab now correctly apply AND semantics (#532). ([#541](https://github.com/djust-org/djust/pull/541))

  See `docs/website/advanced/debug-panel.md`.
## [0.3.6rc4] - 2026-03-13

### Fixed

- **Skip redundant `mount()` on WebSocket connect for pre-rendered pages** — When the client sends `has_prerendered=true` on WS connect and saved state exists in the session (written during the HTTP GET), the view's attributes are restored from session instead of re-running `mount()`. This eliminates the double page-load cost for views with expensive `mount()` implementations (e.g. directory scans, API calls). Falls back to calling `mount()` normally when no saved state is found. `_ensure_tenant()` is now called unconditionally before the restore/mount decision, fixing a regression where multi-tenant views had `self.tenant=None` on WS connect for pre-rendered pages. ([#542](https://github.com/djust-org/djust/pull/542))

## [0.3.6rc3] - 2026-03-13

### Breaking Changes

- **`model.id` now returns the native type, not a string** — `_serialize_model_safely()` previously wrapped `obj.pk` with `str()` when producing the `"id"` key, causing template comparisons like `{% if edit_id == todo.id %}` to fail silently when `edit_id` was an integer. `model.id` now matches `model.pk` and returns the native Python type (e.g. `int`, `UUID`). **Migration:** if your templates or event handlers compare `model.id` against string literals or string-typed variables, update them to use the native type. PR #262 fixed `.pk`; this PR (#472) completes the fix for `.id`.

### Fixed

- **`djust cache --all` now correctly clears all sessions on the Redis backend** — The CLI called `cleanup_expired(ttl=0)` to force-clear sessions, but the semantics of `ttl=0` changed in 0.3.5 to mean "never expire". The command now calls the explicit `delete_all()` method, which uses a Redis pipeline for an efficient single round-trip bulk delete. ([#409](https://github.com/djust-org/djust/pull/409))
- **`dj-params` attribute no longer silently dropped** — Between 0.3.2 and 0.3.6rc2, `dj-params` was removed from the client event-binding code. Templates using `dj-params='{"key": value}'` continued to fire click events but the server received `params: {}`. The attribute is now read and merged into the params object for backward compatibility. A `console.warn` is emitted in debug mode (`globalThis.djustDebug`) to notify developers to migrate. ([#469](https://github.com/djust-org/djust/pull/469))
- **Prefetch Set not cleared on SPA navigation** — The client-side `_prefetched` Set persisted across `live_redirect` navigations, preventing links on the new view from being prefetched. Added `clear()` to `window.djust._prefetch` and call it in `handleLiveRedirect()` so each SPA navigation starts with a fresh prefetch state. ([#402](https://github.com/djust-org/djust/pull/402))
- **Auto-reload on unrecoverable VDOM state** — When VDOM patch recovery fails because recovery HTML is unavailable (e.g. after server restart), the client now auto-reloads the page instead of showing a confusing error overlay. The server sends `recoverable: false` to signal the client. ([#421](https://github.com/djust-org/djust/pull/421))
- **`{% djust_pwa_head %}` and other custom tags with quoted arguments containing spaces now render correctly** — The Rust template lexer used `split_whitespace()` to tokenize tag arguments, which broke quoted values like `name="My App"` into separate tokens (`name="My` and `App"`). This caused the downstream Python handler to receive malformed arguments, silently returning empty output. Replaced with a quote-aware splitter (`split_tag_args`) that preserves quoted strings as single arguments. ([#419](https://github.com/djust-org/djust/pull/419))
- **`{% load %}` tags stripped during template inheritance, breaking inclusion tags** — The Rust parser treated `{% load %}` as `Node::Comment`, which `nodes_to_template_string()` discarded during inheritance reconstruction. When the resolved template was re-parsed, custom tags that relied on Django tag libraries (e.g. `{% djust_pwa_head %}`) could silently fail. Fixed by adding a dedicated `Node::Load` variant that preserves library names through reconstruction. Also improved `_render_django_tag()` error handling: failures now log a full traceback via `logger.exception()` and return a visible HTML comment instead of an empty string. ([#418](https://github.com/djust-org/djust/pull/418))
- **Checkbox/radio `checked` and `<option>` `selected` state not updated by VDOM patches** — `SetAttr` and `RemoveAttr` patches only called `setAttribute`/`removeAttribute`, which updates the HTML attribute but not the DOM property. After user interaction the browser separates the two, so server-driven state changes via `dj-click` had no visible effect on checkboxes, radios, or select options. Fixed by syncing the DOM property alongside the attribute. Also fixed `createNodeFromVNode` to set `.checked`/`.selected` when creating new elements. ([#422](https://github.com/djust-org/djust/pull/422))
- **`SESSION_TTL=0` breaks all event handling (no DOM patches)** — `cleanup_expired()` methods in both `InMemoryStateBackend` and `RedisStateBackend` now treat `TTL ≤ 0` as "never expire". Previously `SESSION_TTL=0` caused `cutoff = time.time() - 0`, making all sessions appear expired, deleting them immediately, and leaving no state for VDOM patches. ([#395](https://github.com/djust-org/djust/issues/395))
- **WebSocket session extraction crashes on Django Channels `LazyObject`** — Replaced `hasattr(scope_session, "session_key")` with `getattr(scope_session, "session_key", None)` in the consumer's request context builder. `hasattr()` on a Django Channels `LazyObject` can raise non-`AttributeError` exceptions during lazy evaluation, causing the consumer to crash silently. ([#396](https://github.com/djust-org/djust/issues/396))

### Deprecated

- **`dj-params` JSON blob attribute** — Use individual `data-*` attributes with optional type-coercion suffixes instead. `dj-params` will be removed in a future release.

  **Migration guide (0.3.2 → 0.3.6):**

  ```html
  <!-- Before (0.3.2) -->
  <button dj-click="start_edit" dj-params='{"todo_id": {{ todo.id }}}'>Edit</button>
  <button dj-click="set_filter" dj-params='{"filter_value": "all"}'>All</button>

  <!-- After (0.3.6+) -->
  <button dj-click="start_edit" data-todo-id:int="{{ todo.id }}">Edit</button>
  <button dj-click="set_filter" data-filter-value="all">All</button>
  ```

  Type-coercion suffixes: `:int`, `:float`, `:bool`, `:json`. Kebab-case attribute names are auto-converted to `snake_case` for server handler parameters.

### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Available via the `djust-deploy` entry point after installation. ([#437](https://github.com/djust-org/djust/pull/437))
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout
  - `--server` flag / `DJUST_SERVER` env var to override the default server URL (`https://djustlive.com`)
  See `docs/website/guides/djust-deploy.md`.
- **TypeScript type stubs updated** — `DjustStreamOp` now includes `"done"` and `"start"` operation types and an optional `mode` field (`"append" | "replace" | "prepend"`). `getActiveStreams()` return type changed from `Map` to `Record`.
  See `docs/website/guides/typecheck.md`.
- **`.flex-between` CSS utility class** — Added to demo project's `utilities.css` for laying out flex children horizontally with space-between. Use on card headers or any flex container that needs a title on the left and action widget on the right. ([#397](https://github.com/djust-org/djust/issues/397))
  See `docs/website/guides/css-frameworks.md`.
- **Debug toolbar state size visualization** — New "Size Breakdown" table in State tab shows per-variable memory and serialized byte sizes with human-readable formatting (B/KB/MB). Added `_debug_state_sizes()` method to `PostProcessingMixin` included in both mount and event debug payloads. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel TurboNav persistence** — Event, patch, network, and state history now persist across TurboNav navigation via sessionStorage (30s window). Panel state restores on next page if navigated within 30 seconds. ([#459](https://github.com/djust-org/djust/pull/459))
  See `docs/website/advanced/debug-panel.md`.
- **TurboNav integration guide** — Comprehensive guide covering setup, navigation lifecycle, inline script handling, known caveats, and design decisions: `docs/guides/turbonav-integration.md`. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel search extended to Network and State tabs** — The search bar in the debug panel now filters across all data tabs. The Network tab shows a `N / total` count label when a query narrows the message list (#530). The State tab filters history entries by trigger, event name, and serialized state content, with the same `N / total` count label (#520). Overlapping `nameFilter` and `searchQuery` on the Events tab now correctly apply AND semantics (#532). ([#541](https://github.com/djust-org/djust/pull/541))

  See `docs/website/advanced/debug-panel.md`.
## [0.3.5] - 2026-03-05


### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Install with `pip install djust[deploy]`. Available via the `djust-deploy` entry point:
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout

  See `docs/website/guides/djust-deploy.md`.
### Fixed

- **`dj-hook` elements now initialize after `dj-navigate` navigation** — `updateHooks()` is called after `live_redirect_mount` replaces DOM content via WebSocket and SSE mount handlers. Previously, hook lifecycle callbacks (`mounted()`, `destroyed()`) were skipped after client-side navigation, leaving hook-dependent elements (e.g., Chart.js canvases) uninitialized. ([#408](https://github.com/djust-org/djust/pull/408))
- **Event handler exceptions now logged with full traceback in production** — Previously, `handle_exception()` only logged the exception class name (e.g. `ValueError`) when `DEBUG=False`, hiding the error message and stack trace. Now logs type, message, and traceback at `ERROR` level regardless of `DEBUG` mode. Client responses remain generic in production. ([#415](https://github.com/djust-org/djust/pull/415))
- **DJE-053 no longer fires as a warning for idempotent event handlers** — When an `@event_handler` runs successfully but produces no DOM changes (e.g. toggle clicked in target state, debounced input with unchanged results, side-effect-only handlers), the empty diff is now silently dropped at `DEBUG` level rather than logged as a `WARNING`. This matches Phoenix LiveView behaviour. The `WARNING`-level DJE-053 is preserved for genuine VDOM failures (`patches=None`), which fall back to a full HTML update and risk losing event listeners. ([#415](https://github.com/djust-org/djust/pull/415))

## [0.3.5rc2] - 2026-03-04

### Fixed

- **VDOM patching with conditional `{% if %}` blocks** — `InsertChild` and `RemoveChild` patches now include `ref_d` and `child_d` fields for ID-based DOM resolution, preventing stale-index mis-targeting when `{% if %}` blocks add or remove elements that shift sibling positions. Falls back to index-based resolution for backwards compatibility. ([#410](https://github.com/djust-org/djust/issues/410))

## [0.3.5rc1] - 2026-02-26

### Added

- **Type stubs for Rust-injected LiveView methods** — `.pyi` stubs for `live_redirect`, `live_patch`, `push_event`, `stream`, and related methods so mypy/pyright catch typos at lint time. ([#390](https://github.com/djust-org/djust/pull/390))
  See `docs/website/guides/typecheck.md`.
- **Navigation Patterns guide** — Documents when to use `dj-navigate` vs `live_redirect` vs `live_patch`. ([#390](https://github.com/djust-org/djust/pull/390))
- **Testing guide** — Django testing best practices and pytest setup for djust applications. ([#390](https://github.com/djust-org/djust/pull/390))
  See `docs/website/api-reference/testing.md`.
- **System checks reference** — New `docs/system-checks.md` covering all 37 check IDs (C/V/S/T/Q) with severity, detection method, suppression patterns, and known false positives. ([#398](https://github.com/djust-org/djust/pull/398))

### Security

- **`mark_safe(f"...")` eliminated in core framework** — `components/base.py` now uses `format_html()` to avoid XSS risk in component rendering. ([#390](https://github.com/djust-org/djust/pull/390))
- **Exception details no longer exposed in production** — `render_template()` previously returned `f"<div>Error: {e}</div>"` unconditionally, leaking internal Rust template engine details. Now returns a generic message in production; error details are only shown when `settings.DEBUG = True`. ([#385](https://github.com/djust-org/djust/pull/385))
- **Playground XSS fixed** — Replaced `innerHTML` assignment with a sandboxed iframe for user-editable preview content. ([#384](https://github.com/djust-org/djust/pull/384))
- **Prototype pollution guard** — Added safeguards against prototype pollution in client-side JS. ([#384](https://github.com/djust-org/djust/pull/384))

### Fixed

- **`{% if %}` inside attribute values no longer shifts VDOM path indices** — Conditional attribute fragments were causing off-by-one errors in VDOM diffing. ([#390](https://github.com/djust-org/djust/pull/390))
- **`super().__init__()` added to component and backend subclasses** — `TenantAwareRedisBackend`, `TenantAwareMemoryBackend`, and several example components were missing `super().__init__()` calls, causing MRO issues. ([#386](https://github.com/djust-org/djust/pull/386))
- **Unused `escape` import removed from `data_table.py`** — CodeQL alert resolved. ([#387](https://github.com/djust-org/djust/pull/387))
- **`render_full_template` signature mismatch fixed** — `no_template_demo.py` override now correctly accepts `serialized_context`. ([#387](https://github.com/djust-org/djust/pull/387))
- **V004 false positives on lifecycle methods** — `handle_params()`, `handle_disconnect()`, `handle_connect()`, and `handle_event()` no longer incorrectly trigger the V004 system check. ([#398](https://github.com/djust-org/djust/pull/398))
- **T013 false positives for `{{ view_path }}`** — `dj-view="{{ view_path }}"` (Django template variable injection) is now correctly recognised as valid by T013. ([#398](https://github.com/djust-org/djust/pull/398))
- **V008 false positives for `-> str`-annotated functions** — Functions with primitive return-type annotations (e.g. `-> str`, `-> int`) no longer trigger V008 when their result is assigned in `mount()`. ([#398](https://github.com/djust-org/djust/pull/398))
- **Test isolation** — `test_checks.py` and `double_bind.test.js` no longer fail when run as part of the full suite. ([#390](https://github.com/djust-org/djust/pull/390))

## [0.3.4] - 2026-02-24

Stable release — promotes 0.3.3rc1 through 0.3.3rc3. All changes below were present in the RC series; this entry summarises them for the stable changelog.

### Added

- **6 new Django template tags in Rust renderer** — `{% widthratio %}`, `{% firstof %}`, `{% templatetag %}`, `{% spaceless %}`, `{% cycle %}`, `{% now %}`. ([#329](https://github.com/djust-org/djust/issues/329))
  See `docs/website/guides/template-cheatsheet.md`.
- **System checks `djust.T011` / `T012` / `T013`** — Warns at startup for unsupported Rust template tags, missing `dj-view`, and invalid `dj-view` paths. ([#293](https://github.com/djust-org/djust/issues/293), [#329](https://github.com/djust-org/djust/issues/329))
- **Deployment guides** — Railway, Render, and Fly.io. ([#247](https://github.com/djust-org/djust/issues/247))
- **Navigation and LiveView invariants documentation.** ([#304](https://github.com/djust-org/djust/issues/304), [#316](https://github.com/djust-org/djust/issues/316))

### Fixed

- **#380: `{% if %}` in HTML attribute values no longer emits `<!--dj-if-->` comment** — Produced malformed HTML (e.g. `class="btn <!--dj-if-->"`). Empty string is emitted instead; text-node VDOM anchor is unaffected. ([#381](https://github.com/djust-org/djust/pull/381))
- **#382: `{% elif %}` chains in attribute values propagate `in_tag_context`** — All elif nodes in a chain now inherit the outer `{% if %}`'s attribute context. ([#383](https://github.com/djust-org/djust/pull/383))
- **`{% if/else %}` branches miscounting div depth in template extraction.** ([#365](https://github.com/djust-org/djust/issues/365))
- **VDOM extraction used fully-merged `{% extends %}` document.** ([#366](https://github.com/djust-org/djust/issues/366))
- **`TypeError: Illegal invocation` in debug panel on Chrome/Edge.** ([#367](https://github.com/djust-org/djust/issues/367))
- **`dj-patch('/')` now correctly updates browser URL to root path.** ([#307](https://github.com/djust-org/djust/issues/307))
- **`live_patch` routing restored** — `handleNavigation` dispatch now fires correctly. ([#307](https://github.com/djust-org/djust/issues/307))
- **T003 false positives eliminated** — `{% include %}` check now examines the include path, not whole-file content. ([#331](https://github.com/djust-org/djust/issues/331))

## [0.3.3rc3] - 2026-02-24

### Fixed

- **#382: `{% elif %}` inside HTML attribute values propagates `in_tag_context`** — When `{% if a %}...{% elif b %}...{% endif %}` appears inside an attribute value and all conditions are false, the elif node previously emitted `<!--dj-if-->` (malformed HTML). Fixed by threading `in_tag_context` as a parameter into `parse_if_block()` so elif nodes inherit the outer if's attribute context. ([#382](https://github.com/djust-org/djust/issues/382))

## [0.3.3rc2] - 2026-02-24

### Fixed

- **`{% if/else %}` branches miscounting div depth in template extraction** — `_extract_liveview_root_with_wrapper` and the other extraction methods treated both branches of a `{% if/else %}` block as independent div opens, causing depth to never reach 0 when both branches opened a div sharing a single closing `</div>`. This caused the entire template to be returned as root, making the view non-reactive. Fixed with a shared `_find_closing_div_pos()` static method that uses a branch stack to restore depth at `{% else %}`/`{% elif %}` tags, so mutually-exclusive branches are counted as one open. ([#365](https://github.com/djust-org/djust/issues/365))
- **VDOM extraction used fully-merged `{% extends %}` document** — For inherited templates, `get_template()` extracted the VDOM root from the fully-resolved document (base HTML + inlined blocks), which contains surrounding HTML that the depth counter could trip over. Now prefers the child template source when it contains `dj-root`/`dj-view`, which holds exactly the block content needed. Also fixes the exception fallback path: the raw child source (containing `{% extends %}`) was incorrectly stored in `_full_template`, causing `render_full_template` to attempt rendering a non-standalone template. ([#366](https://github.com/djust-org/djust/issues/366))
- **`TypeError: Illegal invocation` in debug panel on Chrome/Edge** — `_hookExistingWebSocket` called native WebSocket getter/setter functions via `Function.prototype.call()` from external code, which fails V8's brand check on IDL-generated bindings. Fixed by using normal property access (`ws.onmessage`) and assignment (`ws.onmessage = handler`) instead of `desc.get/set.call(ws)`. ([#367](https://github.com/djust-org/djust/issues/367))

## [0.3.3rc1] - 2026-02-21

### Added

- **6 new Django template tags in Rust renderer** — Implemented `{% widthratio %}`, `{% firstof %}`, `{% templatetag %}`, `{% spaceless %}`, `{% cycle %}`, and `{% now %}` in the Rust template engine. These tags were previously rendered as HTML comments with warnings. ([#329](https://github.com/djust-org/djust/issues/329))
  See `docs/website/guides/template-cheatsheet.md`.
- **System check `djust.T011` for unsupported template tags** — Warns at startup when templates use Django tags not yet implemented in the Rust renderer (`ifchanged`, `regroup`, `resetcycle`, `lorem`, `debug`, `filter`, `autoescape`). Suppressible with `{# noqa: T011 #}`. ([#329](https://github.com/djust-org/djust/issues/329))
- **System check `djust.T012` for missing `dj-view`** — Detects templates that use `dj-*` event directives without a `dj-view` attribute, which would silently fail at runtime. ([#293](https://github.com/djust-org/djust/issues/293))
  See `docs/website/getting-started/first-liveview.md`.
- **System check `djust.T013` for invalid `dj-view` paths** — Detects empty or malformed `dj-view` attribute values. ([#293](https://github.com/djust-org/djust/issues/293))
  See `docs/website/getting-started/first-liveview.md`.
- **`{% now %}` supports 35+ Django date format specifiers** — Including `S` (ordinal suffix), `t` (days in month), `w`/`W` (weekday/week number), `L` (leap year), `c` (ISO 8601), `r` (RFC 2822), `U` (Unix timestamp), and Django's special `P` format (noon/midnight).
- **Deployment guides** — Added deployment documentation for Railway, Render, and Fly.io. ([#247](https://github.com/djust-org/djust/issues/247))
- **Navigation best practices documentation** — Documented `dj-patch` vs `dj-click` for client-side navigation, with `handle_params()` patterns. ([#304](https://github.com/djust-org/djust/issues/304))
  See `docs/guides/BEST_PRACTICES.md`.
- **LiveView invariants documentation** — Documented root container requirement and `**kwargs` convention for event handlers. ([#316](https://github.com/djust-org/djust/issues/316))

### Fixed

- **#380: `{% if %}` inside HTML attribute values no longer emits `<!--dj-if-->` comment** — When a `{% if %}` block with no else branch evaluates to false inside an HTML attribute value (e.g. `class="btn {% if active %}active{% endif %}"`), the Rust renderer now emits an empty string instead of the `<!--dj-if-->` VDOM placeholder. The placeholder is only meaningful as a DOM child node; inside an attribute it produced malformed HTML (e.g. `class="btn <!--dj-if-->"`). Text-node context is unaffected — the anchor comment is still emitted there for VDOM stability (fix for DJE-053 / #295).
- **False `{% if %}` blocks now emit `<!--dj-if-->` placeholder instead of empty string** — Gives the VDOM diffing engine a stable DOM anchor to target when the condition later becomes true, resolving DJE-053 / issue #295.
- **`dj-patch('/')` now correctly updates the browser URL to the root path** — Removed the `url.pathname !== '/'` guard in `bindNavigationDirectives` that prevented the browser URL from being updated when patching to `/`. The guard was silently ignoring root-path patches. ([#307](https://github.com/djust-org/djust/issues/307))
- **`live_patch` routing restored — `handleNavigation` dispatch now fires correctly** — Fixed dict merge order in `_flush_navigation` so `type: 'navigation'` is no longer overwritten by `**cmd`. Added an `action` field to carry the nav sub-type (`live_patch` / `live_redirect`); `handleNavigation` now dispatches on `data.action` instead of `data.type`. Previously the client `switch case 'navigation':` never matched because `type` was being overwritten with `"live_patch"`. **Note:** `data.action || data.type` fallback is kept for old JS clients that send messages without an `action` field — this fallback is planned for removal in the next minor release. ([#307](https://github.com/djust-org/djust/issues/307))
- **T003 false positives eliminated** — The `{% include %}` check now examines the include path instead of the whole file content, preventing false warnings on templates that include SVGs or modals alongside `dj-*` directives. ([#331](https://github.com/djust-org/djust/issues/331))

## [0.3.2] - 2026-02-18

### Added

- **TypeScript definitions (`djust.d.ts`)** — Comprehensive ambient TypeScript declaration file shipped with the Python package at `static/djust/djust.d.ts`. Covers: `window.djust` namespace, `LiveViewWebSocket` and `LiveViewSSE` transport classes, `DjustHook` lifecycle interface (`mounted`, `beforeUpdate`, `updated`, `destroyed`, `disconnected`, `reconnected`), `DjustHookContext` (`this.el`, `this.pushEvent`, `this.handleEvent`), `dj-model` binding types, streaming API types (`DjustStreamMessage`, `DjustStreamOp`), upload progress event types (`DjustUploadEntry`, `DjustUploadConfig`, `DjustUploadProgressEventDetail`), and the `djust:upload:progress` custom DOM event. Use via `/// <reference path="..." />` or add to `tsconfig.json`.
- **Python type stubs (`_rust.pyi`)** — PEP 561 compliant type stubs for the PyO3 Rust extension module (`djust._rust`). Covers all exported functions (`render_template`, `render_template_with_dirs`, `diff_html`, `resolve_template_inheritance`, `fast_json_dumps`, serialization helpers, tag handler registry) and classes (`RustLiveView`, `SessionActorHandle`, `SupervisorStatsPy`, and all 15 Rust UI components). Enables full IDE autocomplete and mypy type checking for the Rust extension.
- **SSE (Server-Sent Events) fallback transport** — djust now automatically falls back to SSE when WebSocket is unavailable (corporate proxies, enterprise firewalls). Architecture: `EventSource` for server→client push, HTTP POST for client→server events. Transport negotiation is automatic: WebSocket is tried first; SSE activates after all reconnect attempts fail. Register the endpoint with `path("djust/", include(djust.sse.sse_urlpatterns))` and include `03b-sse.js` in your template. Feature limitations: no binary file uploads, no presence tracking, no actor-based state. See `docs/sse-transport.md` for full setup guide.
- **Type stub files (.pyi) for LiveView and mixins** — Added PEP 561 compliant type stubs for `NavigationMixin`, `PushEventMixin`, `StreamsMixin`, `StreamingMixin`, and `LiveView` to enable IDE autocomplete and mypy type checking for runtime-injected methods like `live_redirect`, `live_patch`, `push_event`, `stream`, `stream_insert`, `stream_delete`, and `stream_to`. Includes `py.typed` marker file and comprehensive test suite.
- **`@background` decorator for async event handlers** — New decorator that automatically runs the entire event handler in a background thread via `start_async()`. Simplifies syntax for long-running operations (AI generation, API calls, file processing) without needing explicit callback splitting. Can be combined with other decorators like `@debounce`. Task name is automatically set to the handler's function name for cancellation tracking. ([#313](https://github.com/djust-org/djust/issues/313))
- **`start_async()` keeps loading state active during background work** — WebSocket responses include `async_pending` flag when a `start_async()` callback is running, preventing loading spinners from disappearing prematurely. Async completion responses include `event_name` so the client clears the correct loading state. Supports named tasks for tracking and cancellation via `cancel_async(name)`. Optional `handle_async_result(name, result, error)` callback for completion/error handling. ([#313](https://github.com/djust-org/djust/issues/313), [#314](https://github.com/djust-org/djust/pull/314))
  See `docs/website/guides/loading-states.md`.
- **`dj-loading.for` attribute** — Scope any `dj-loading.*` directive to a specific event name, regardless of DOM position. Allows spinners, disabled buttons, and other loading indicators anywhere in the page to react to a named event. ([#314](https://github.com/djust-org/djust/pull/314))
- **`AsyncWorkMixin` included in `LiveView` base class** — `start_async()` is now available on all LiveViews without explicit mixin import. ([#314](https://github.com/djust-org/djust/pull/314))
  See `docs/website/guides/loading-states.md`.
- **Loading state re-scan after DOM patches** — `scanAndRegister()` is called after every `bindLiveViewEvents()` so dynamically rendered elements (e.g., inside modals) get loading state registration. Stale entries for disconnected elements are cleaned up automatically. ([#314](https://github.com/djust-org/djust/pull/314))
  See `docs/website/guides/loading-states.md`.
- **System check `djust.T010` for dj-click navigation antipattern** — Detects elements using `dj-click` with navigation-related data attributes (`data-view`, `data-tab`, `data-page`, `data-section`). This pattern should use `dj-patch` instead for proper URL updates, browser history support, and bookmarkable views. Warning severity. ([#305](https://github.com/djust-org/djust/issues/305))
- **System check `djust.Q010` for navigation state in event handlers** — Heuristic INFO-level check that detects `@event_handler` methods setting navigation state variables (`self.active_view`, `self.current_tab`, etc.) without using `patch()` or `handle_params()`. Suggests converting to `dj-patch` pattern for URL updates and back-button support. Can be suppressed with `# noqa: Q010`. ([#305](https://github.com/djust-org/djust/issues/305))
- **Type stubs for Rust extension and LiveView** — Added `.pyi` type stub files for `_rust` module and `LiveView` class, enabling IDE autocomplete, mypy/pyright type checking, and catching typos like `live_navigate` (should be `live_patch`) at lint time. Includes `py.typed` marker for PEP 561 compliance and comprehensive documentation in `docs/TYPE_STUBS.md`.

### Deprecated

- **`data.type` fallback in `handleNavigation`** — The `data.action || data.type` fallback for pre-#307 clients (added for backwards compatibility in [#318](https://github.com/djust-org/djust/pull/318)) will be removed in the next minor release. Server now sends `data.action` on all navigation messages. Update any custom client code that sends navigation messages without an `action` field.

### Fixed

- **Silent `str()` coercion for non-serializable LiveView state** — Non-serializable objects stored in `self.*` during `mount()` (e.g., service instances, API clients) were silently converted to strings, causing confusing `AttributeError` on subsequent requests far from the root cause. `normalize_django_value()` now logs a warning before falling back with the type name, module, and guidance on how to fix. Opt-in strict mode (`DJUST_STRICT_SERIALIZATION = True`) raises `TypeError` instead of coercing, recommended for development. New static check `djust.V008` (AST-based) detects non-primitive assignments in `mount()` at development time. ([#292](https://github.com/djust-org/djust/issues/292))
- **System check S005 incorrectly warns on views with `login_required = False`** — The S005 security check now correctly distinguishes between intentionally public views (`login_required = False`) and views that haven't addressed authentication at all (`login_required = None`). Previously, views with `login_required = False` were incorrectly flagged as missing authentication due to a truthy test. The check now uses explicit `is not None` comparisons to distinguish intentional public access from unaddressed auth. ([#303](https://github.com/djust-org/djust/issues/303))
- **`|safe` filter rendering empty string for nested SafeString values** — When mark_safe() HTML was stored in lists of dicts or nested dicts, the |safe filter rendered an empty string instead of preserving the HTML. The _collect_safe_keys() function now recursively scans nested dicts and lists using dotted path notation (e.g., "items.0.content") to track all SafeString locations. Includes circular reference protection to prevent RecursionError on tree/graph structures. ([#317](https://github.com/djust-org/djust/issues/317))
- **VDOM diff incorrectly matching siblings when `{% if %}` removes nodes** — When `{% if %}` blocks evaluated to false and removed elements, siblings shifted left, causing `diff_indexed_children()` to incorrectly match unrelated nodes and generate wrong patches. The template engine now emits `<!--dj-if-->` placeholder comments when conditions are false (matching Phoenix LiveView's approach), maintaining consistent sibling positions. The VDOM diff detects placeholder-to-content transitions and generates `RemoveChild` + `InsertChild` patches instead of `Replace` patches for semantic consistency. Eliminates DJE-053 fallback to full HTML updates and removes need for `style='display:none'` workarounds. ([#295](https://github.com/djust-org/djust/issues/295))
- **Event listener leak causing duplicate WebSocket sends** — Single user actions were triggering the same event multiple times (e.g. `select_project` 5×, `mount` 3×) because listeners accumulated across VDOM patch/morph cycles without cleanup. Fixed four root causes: (1) `initReactCounters` now uses a `WeakSet` guard to skip already-initialized containers; (2) `createNodeFromVNode` no longer pre-marks elements as bound before `bindLiveViewEvents()` runs, eliminating a race where newly inserted elements were silently skipped; (3) `dj-click` handlers now read the attribute at fire-time rather than bind-time, so `morphElement` attribute updates take effect immediately; (4) three unguarded `console.log` calls in `12-vdom-patch.js` are now wrapped in `if (globalThis.djustDebug)`. The existing `WeakMap`-based deduplication in `bindLiveViewEvents()` (introduced in #312) correctly prevents re-binding when called repeatedly. ([#315](https://github.com/djust-org/djust/issues/315))
- **`dj-patch('/')` failed to update URL and `live_patch` routing broken** — Removed `url.pathname !== '/'` guard in `bindNavigationDirectives` so root-path navigation works. Fixed dict merge order in `_flush_navigation` so server sends `type='navigation'` instead of `type='live_patch'`. Updated `handleNavigation` to dispatch via `data.action` with `data.action || data.type` fallback for backwards compatibility. ([#318](https://github.com/djust-org/djust/pull/318))
- **52 unguarded `console.log` calls in client JS** — All `console.log` calls across 12 files in `static/djust/src/` (excluding the intentional debug panel in `src/debug/`) are now wrapped with `if (globalThis.djustDebug)`. Bare logging in production code leaks internal state to browser consoles and violates the `djust.Q003` system check. Files affected: `00-namespace.js`, `02-response-handler.js`, `03-websocket.js`, `04-cache.js`, `05-state-bus.js`, `06-draft-manager.js`, `07-form-data.js`, `09-event-binding.js`, `10-loading-states.js`, `11-event-handler.js`, `12-vdom-patch.js`, `13-lazy-hydration.js`.
- **dj-submit forms sent empty params when created by VDOM patches** — `createNodeFromVNode` now correctly collects `FormData` for submit events; replaced `data-liveview-*-bound` attribute tracking with `WeakMap` to prevent stale binding flags after DOM replacement ([#312](https://github.com/djust-org/djust/pull/312))

### Security

- **F-strings in logging calls** — Converted 9 logger calls to use %-style formatting (`logger.error("msg %s", val)`) instead of f-strings (`logger.error(f"msg {val}")`). F-strings defeat lazy evaluation, causing string interpolation before the log level check, potentially exposing sensitive data and wasting CPU. Affected files: `mixins/template.py`, `security/__init__.py`, `security/error_handling.py`, `template_tags/__init__.py`, `template_tags/static.py`, `template_tags/url.py`.

### Tests

- **Regression tests for `|safe` filter with nested dicts** — Added comprehensive tests verifying that `|safe` filter works correctly for HTML content in nested dict/list values, preventing issue [#317](https://github.com/djust-org/djust/issues/317) from recurring

## [0.3.2rc1] - 2026-02-15

### Fixed

- **Form data lost on `dj-submit`** — Client-only properties (`_targetElement`, `_optimisticUpdateId`, `_skipLoading`, `_djTargetSelector`) are now stripped from event params before serialization. Previously, `HTMLFormElement` references in params corrupted the JSON payload, overwriting form field data with the element's indexed children. ([#308](https://github.com/djust-org/djust/issues/308))
- **`@change` → `dj-change` in form adapters** — All three framework adapters (Bootstrap 5, Tailwind, Plain) rendered `@change="validate_field"` instead of `dj-change="validate_field"`, causing real-time field validation to silently fail. ([#310](https://github.com/djust-org/djust/pull/310))
- **`EmailField` rendered as `type="text"`** — `_get_field_type()` checked `CharField` before `EmailField` (which inherits from `CharField`), so email fields never got `type="email"`. Reordered the isinstance checks. ([#310](https://github.com/djust-org/djust/pull/310))

### Security

- **XSS in `FormMixin.render_field()`** — Removed `render_field()`, `_render_field_widget()`, and `_attrs_to_string()` from `FormMixin`. These methods used f-strings with no escaping to build HTML, allowing stored XSS via form field values. Use `as_live()` / `as_live_field()` (which delegate to framework adapters with proper `escape()`) instead. ([#310](https://github.com/djust-org/djust/pull/310))
- **Textarea content not escaped in adapters** — `_render_input()` passed raw textarea values to `_build_tag()` content without `escape()`. Added `escape(str(value))` for textarea content. ([#310](https://github.com/djust-org/djust/pull/310))

### Changed

- **Framework adapters deduplicated** — Created `BaseAdapter` with all shared rendering logic. `Bootstrap5Adapter`, `TailwindAdapter`, and `PlainAdapter` reduced from ~200 lines each to ~10 lines of class attributes. `frameworks.py` reduced from ~657 to ~349 lines. ([#310](https://github.com/djust-org/djust/pull/310))
- **`_model_instance` support for ModelForm editing** — `FormMixin.mount()` now reads field values from `_model_instance` if set and the form is a `ModelForm`. `_create_form()` passes `instance=` to the form constructor. ([#310](https://github.com/djust-org/djust/pull/310))

### Deprecated

- **`LiveViewForm`** — Emits `DeprecationWarning` on subclass. Adds no functionality over `django.forms.Form`. Will be removed in 0.4. ([#310](https://github.com/djust-org/djust/pull/310))

### Removed

- **`FormMixin.render_field()`** — Insecure (XSS via f-strings) and duplicated adapter logic. Use `as_live_field()` instead. ([#310](https://github.com/djust-org/djust/pull/310))
- **`form_field()` function** — Dead code, never called. Removed from `forms.py` and `__all__`. ([#310](https://github.com/djust-org/djust/pull/310))

## [0.3.1] - 2026-02-14

### Changed

- **3.8x faster rendering for large pages** — Optimized `get_context_data()` by replacing `dir(self)` iteration (~300 inherited Django View attributes, ~50ms) with targeted `__dict__` + MRO walk (<1ms). Added `dj-update="ignore"` optimization to Rust VDOM diff engine, skipping subtrees the client won't patch (240ms → 17ms). Combined with template-level optimizations, reduces event roundtrip from ~160ms to ~42ms on pages with large static content.

## [0.3.0] - 2026-02-14

### Added

- **`dj-confirm` attribute** — Declarative confirmation dialogs for event handlers. Add `dj-confirm="Are you sure?"` to any `dj-click` element to show a browser confirmation dialog before dispatching the event. ([#302](https://github.com/djust-org/djust/pull/302))

- **CSS Framework Support** — Comprehensive Tailwind CSS integration with three-part system: (1) System checks (`djust.C010`, `djust.C011`, `djust.C012`) automatically warn about Tailwind CDN in production, missing compiled CSS, and manual `client.js` loading. (2) Graceful fallback auto-injects Tailwind CDN in development mode when `output.css` is missing. (3) CLI helper command `python manage.py djust_setup_css tailwind` creates `input.css` with Tailwind v4 syntax, auto-detects template directories, finds Tailwind CLI, and builds CSS with optional `--watch` and `--minify` flags. Eliminates duplicate client.js race conditions and guides developers toward production-ready setup.

  See `docs/website/guides/css-frameworks.md`.
### Fixed

- **Server-side template processing now auto-infers dj-root from dj-view** — All template extraction methods (`_extract_liveview_content`, `_extract_liveview_root_with_wrapper`, `_extract_liveview_template_content`, `_strip_liveview_root_in_html`) now fall back to `[dj-view]` when `[dj-root]` is not present, matching the client-side `autoStampRootAttributes()` behavior introduced in PR #297. This fixes a bug where templates with only `dj-view` (no explicit `dj-root`) would fail to render correctly. ([#300](https://github.com/djust-org/djust/issues/300))
- **Client-side autoMount now correctly reads dj-view attribute** — Fixed `autoMount()` to use `getAttribute('dj-view')` instead of `container.dataset.djView`. The `dataset` API reads `data-*` attributes, but `dj-view` is not a data attribute, causing the attribute to be missed. ([#300](https://github.com/djust-org/djust/issues/300))
- **System check T002 downgraded from WARNING to INFO** — Since `dj-root` is now optional and auto-inferred from `dj-view` (per PR #297), the T002 check is now informational rather than a warning. The message now clarifies that auto-inference is working correctly. ([#300](https://github.com/djust-org/djust/issues/300))
- **Duplicate client.js loading race condition** — djust now automatically detects and warns (via `djust.C012` system check) when base or layout templates manually include `<script src="{% static 'djust/client.js' %}">`. Since the framework auto-injects `client.js`, manual loading causes double-initialization and console warnings. The check provides clear guidance to remove manual script tags.
- **Tailwind CDN in production** — New `djust.C010` system check warns when Tailwind CDN (`cdn.tailwindcss.com`) is detected in production templates (`DEBUG=False`). Provides actionable guidance to compile CSS with `djust_setup_css` command or Tailwind CLI. Prevents slow CDN performance and console warnings in production.

### Security

- **Pre-Release Security Audit Process** — Comprehensive security infrastructure to prevent vulnerabilities like the mount handler RCE (Issue #298) from reaching production. Includes 259 new security tests (Python + Rust) covering parameter injection, file upload attacks, URL injection, and XSS prevention across all contexts. Three GitHub workflows provide automated security scanning (bandit, safety, cargo-audit, npm audit, CodeQL), hot spot detection (auto-labels PRs touching security-sensitive code), and CI security test job requiring 85% coverage for security-sensitive modules. New pre-release security audit template with 7-phase checklist ensures comprehensive review before each release. Documentation updates establish mandatory security gates and review requirements for changes to hot spot files.

### Dependencies

- Bump happy-dom from 20.5.3 to 20.6.1 ([#289](https://github.com/djust-org/djust/pull/289))
- Bump tempfile from 3.24.0 to 3.25.0 ([#288](https://github.com/djust-org/djust/pull/288))

## [0.3.0rc5] - 2026-02-11

### Added

- **Automatic change tracking** — Phoenix-style render optimization. The framework automatically detects which context values changed between renders and only sends those to Rust's `update_state()`. Replaces the manual `static_assigns` API. Two-layer detection: snapshot comparison for instance attributes, `id()` reference comparison for computed values (e.g., `@lru_cache` results). Immutable types (`str`, `int`, `float`, `bool`, `None`, `bytes`, `tuple`, `frozenset`) skip `deepcopy` in snapshots.

### Removed

- **`static_assigns` class attribute** — Replaced by automatic change tracking. The framework now detects unchanged values automatically — no manual annotation needed.

## [0.3.0rc4] - 2026-02-11

### Added

- **All 57 Django template filters** — The Rust template engine now supports the complete set of Django built-in filters. Added 24 filters across two batches: `default_if_none`, `wordcount`, `wordwrap`, `striptags`, `addslashes`, `ljust`, `rjust`, `center`, `make_list`, `json_script`, `force_escape`, `escapejs`, `linenumbers`, `get_digit`, `iriencode`, `urlize`, `urlizetrunc`, `truncatechars_html`, `truncatewords_html`, `safeseq`, `escapeseq`, `unordered_list`, `phone2numeric`, `pprint`. ([#246](https://github.com/djust-org/djust/issues/246), [#254](https://github.com/djust-org/djust/issues/254))
  See `docs/website/guides/template-cheatsheet.md`.
- **Authentication & Authorization** — Opinionated, framework-enforced auth for LiveViews. View-level `login_required` and `permission_required` class attributes (plus `LoginRequiredMixin`/`PermissionRequiredMixin` for Django-familiar patterns). Custom auth logic via `check_permissions()` hook. Handler-level `@permission_required()` decorator for protecting individual event handlers. Auth checks run server-side before `mount()` and before handler dispatch — no client-side bypass possible. Integrates with `djust_audit` command (shows auth posture per view) and Django system checks (`djust.S005` warns on unprotected views with exposed state).
- **Navigation & URL State** — `live_patch()` updates URL query params without remount, `live_redirect()` navigates to a different view over the same WebSocket. Includes `handle_params()` callback, `live_session()` URL routing helper, and client-side `dj-patch`/`dj-navigate` directives with popstate handling. ([#236](https://github.com/djust-org/djust/pull/236))
- **Presence Tracking** — Real-time user presence with `PresenceMixin` and `PresenceManager`. Pluggable backends (in-memory and Redis). Includes `LiveCursorMixin` and `CursorTracker` for collaborative live cursor features. ([#236](https://github.com/djust-org/djust/pull/236))
  See `docs/website/guides/presence.md`.
- **Streaming** — `StreamingMixin` for real-time partial DOM updates (e.g., LLM token-by-token streaming). Provides `stream_to()`, `stream_insert()`, `stream_text()`, `stream_error()`, `stream_start()`/`stream_done()`, and `push_state()`. Batched at ~60fps to prevent flooding. ([#236](https://github.com/djust-org/djust/pull/236))
  See `docs/website/guides/streaming-markdown.md`.
- **File Uploads** — `UploadMixin` with binary WebSocket frame protocol for chunked file uploads. Includes progress tracking, magic bytes validation, file size/extension/MIME checking, and client-side `dj-upload`/`dj-upload-drop` directives. ([#236](https://github.com/djust-org/djust/pull/236))
  See `docs/website/guides/uploads.md`.
- **JS Hooks** — `dj-hook` attribute for client-side JavaScript lifecycle hooks (mounted, updated, destroyed, disconnected, reconnected). ([#236](https://github.com/djust-org/djust/pull/236))
- **Model Binding** — `dj-model` two-way data binding with `.lazy` and `.debounce-N` modifiers. Server-side `ModelBindingMixin` with security field blocklist and type coercion. ([#236](https://github.com/djust-org/djust/pull/236))
  See `docs/website/guides/model-binding.md`.
- **Client Directives** — `dj-confirm` confirmation dialogs, `dj-target` scoped updates, embedded view routing in event handlers. ([#236](https://github.com/djust-org/djust/pull/236))
- **Server-Push API** — Background tasks (Celery, management commands, cron jobs) can now push state updates to connected LiveView clients via `push_to_view()`. Includes per-view channel groups (auto-joined on mount), a sync/async public API (`push_to_view` / `apush_to_view`), and periodic `handle_tick()` for self-updating views. ([#230](https://github.com/djust-org/djust/issues/230))
- **Progressive Web App (PWA) Support** — Complete offline-first PWA implementation with service worker integration, IndexedDB/LocalStorage abstraction, optimistic UI updates, and offline-aware template directives. Includes comprehensive template tags (`{% djust_pwa_head %}`, `{% djust_pwa_manifest %}`), PWA mixins (`PWAMixin`, `OfflineMixin`, `SyncMixin`), and automatic synchronization when online. ([#235](https://github.com/djust-org/djust/pull/235))
  See `docs/website/guides/pwa.md`.
- **Multi-Tenant SaaS Support** — Production-ready multi-tenant architecture with flexible tenant resolution strategies (subdomain, path, header, session, custom, chained), automatic data isolation, tenant-aware state backends, and comprehensive template context injection. Includes `TenantMixin` and `TenantScopedMixin` for views. ([#235](https://github.com/djust-org/djust/pull/235))
- **`dj-poll` attribute** — Declarative polling for LiveView elements. Add `dj-poll="handler_name"` to any element to trigger the handler at regular intervals. Configurable via `dj-poll-interval` (default: 5000ms). Automatically pauses when the page is hidden and resumes on visibility change. ([#269](https://github.com/djust-org/djust/issues/269))
- **`DjustMiddlewareStack`** — New ASGI middleware for apps that don't use `django.contrib.auth`. Wraps WebSocket routes with session middleware only (no auth required). Updated `C005` system check to recognize both `AuthMiddlewareStack` and `DjustMiddlewareStack`. ([#265](https://github.com/djust-org/djust/issues/265))
- **System check `C006`** — Warns when `daphne` is in `INSTALLED_APPS` but `whitenoise` middleware is missing. ([#259](https://github.com/djust-org/djust/issues/259))
- **`startproject` / `startapp` / `new` CLI commands** — `python -m djust new myapp` creates a full project with optional features (`--with-auth`, `--with-db`, `--with-presence`, `--with-streaming`, `--from-schema`). Legacy `startproject` and `startapp` commands also available. ([#266](https://github.com/djust-org/djust/issues/266))
- **`djust mcp install` CLI command** — Automates MCP server setup for Claude Code, Cursor, and Windsurf. Tries `claude mcp add` first (canonical for Claude Code), falls back to writing `.mcp.json` directly. Merges with existing config, backs up malformed files, idempotent.
  See `docs/website/guides/mcp-server.md`.
- **Simplified root element** — `dj-view` is now the only required attribute on LiveView container elements. The client auto-stamps `dj-root` and `dj-liveview-root` at init time. Old three-attribute format still works. ([#258](https://github.com/djust-org/djust/issues/258))
- **Model `.pk` in templates** — `{{ model.pk }}` now works in Rust-rendered templates. Model serialization includes a `pk` key with the native primary key value. ([#262](https://github.com/djust-org/djust/issues/262))
  See `docs/website/guides/template-cheatsheet.md`.
- **Better Error Messages** — Improved error messages for common LiveView event handler mistakes (missing `@event_handler`, wrong method signature). ([#248](https://github.com/djust-org/djust/issues/248))
  See `docs/website/guides/flash-messages.md`.
- **`LiveViewSmokeTest` mixin** — Automated smoke and fuzz testing for LiveView classes. ([#251](https://github.com/djust-org/djust/pull/251))
- **MCP server** — `python manage.py djust_mcp` starts a Model Context Protocol server for AI assistant integration. Provides framework introspection, system checks, scaffolding, and validation tools. Used by `djust mcp install` to configure Claude Code, Cursor, and Windsurf.
  See `docs/website/guides/mcp-server.md`.
- **`djust_audit` management command** — Security audit showing auth posture, exposed state, and handler signatures per view.
- **`djust_check` management command** — Django system checks for project validation. Gains `--fix` flag for safe auto-fixes and `--format json` for enhanced output with fix hints.
- **`djust_schema` management command** — Extract and generate Django models from JSON schema files.
  See `docs/guides/djust-audit.md`.
- **`djust_ai_context` management command** — Generate AI-focused context files for LLM integrations.
  See `docs/guides/djust-audit.md`.
- **AI documentation** — `docs/ai/` with focused guides for events, forms, JIT, lifecycle, security, and templates. `docs/llms.txt` and `docs/llms-full.txt` for LLM context.
- **Auto-build client.js from src/ modules** — Pre-commit hook runs `build-client.sh` when `src/` files change. ([#211](https://github.com/djust-org/djust/issues/211))
- **Keyed-mutation fuzz test generator** — New proptest generator produces tree B by mutating tree A, exercising keyed diff paths more effectively. Proptest cases bumped from 500 to 1000. ([#216](https://github.com/djust-org/djust/issues/216), [#217](https://github.com/djust-org/djust/issues/217))

### Changed

- **BREAKING: `data-dj-*` prefix stripping** — Client-side `extractTypedParams()` now strips the `dj_` prefix from `data-dj-*` attributes. `data-dj-preset="dark"` sends `{preset: "dark"}` instead of `{dj_preset: "dark"}`. Update handler parameter names accordingly: `dj_foo` → `foo`.
- **State Backends** — Enhanced with tenant-aware isolation support (`TenantAwareRedisBackend`, `TenantAwareMemoryBackend`).

### Performance

- **Batched `sync_to_async` calls** — Event handler processing now uses 2 thread hops instead of 4, saving ~1-4ms per event. ([#277](https://github.com/djust-org/djust/issues/277))
- **Eliminated JSON encode/decode roundtrip** — Direct `normalize_django_value()` Python-to-Python type normalization replaces 17 `json.loads(json.dumps(...))` patterns. Saves 2-5ms per event for views with database objects. ([#279](https://github.com/djust-org/djust/issues/279))
- **Cached template variable extraction** — Rust `extract_template_variables()` results cached by content hash (SHA-256). Size-capped at 256 entries with automatic eviction. ([#280](https://github.com/djust-org/djust/issues/280))
- **Cached context processor resolution** — `resolve_context_processors()` results cached per settings configuration. Invalidated on `setting_changed` signal. ([#281](https://github.com/djust-org/djust/issues/281))
- **JIT short-circuit for non-DB views** — Views without QuerySets or Models in context skip the entire JIT serialization pipeline. Saves ~0.5ms per event for simple views. ([#278](https://github.com/djust-org/djust/issues/278))
- **Slimmer debug payload** — Event responses send only state variables; handler metadata moved to initial mount as static data. ~68% smaller debug payloads (~25KB → ~8KB per event).

### Fixed

- **Inline args on form events** — `dj-change`, `dj-input`, `dj-blur`, `dj-focus` now parse inline arguments (e.g., `dj-change="toggle(3)"`) before sending to server. Also fixed state change detection to use deep copy comparison, catching in-place mutations.
- **Error overlay on intentional disconnect** — Suppress "WebSocket Connection Failed" overlay during TurboNav navigation via `_intentionalDisconnect` flag.
- **VDOM patch failure recovery** — When VDOM patches fail, the client requests recovery HTML on demand instead of reloading the page. Uses DOM morphing to preserve event listeners and form state. ([#259](https://github.com/djust-org/djust/issues/259))
- **HTTP Fallback Protocol** — `post()` now accepts the HTTP fallback format where the event name is in the `X-Djust-Event` header and params are flat in the body JSON. ([#255](https://github.com/djust-org/djust/issues/255))
- **Debug panel HTTP-only mode** — POST responses include `_debug` payload when `DEBUG=True`, enabling the debug panel in HTTP-only mode. ([#267](https://github.com/djust-org/djust/issues/267))
- **Silent LiveView config failures** — Client JS now shows helpful `console.error` when no LiveView containers are found. Added system check `V005` for modules not in `LIVEVIEW_ALLOWED_MODULES`. ([#257](https://github.com/djust-org/djust/issues/257))
- **HTTP-only mode session state on GET** — `get()` now saves view state to the session immediately when `use_websocket: False`. ([#264](https://github.com/djust-org/djust/issues/264))
- **`use_websocket: False` client-side enforcement** — Setting now actually prevents WebSocket connections. ([#260](https://github.com/djust-org/djust/issues/260))
- **DOM morphing preserves event listeners** — `html_update` now uses morphdom-style DOM diffing instead of `innerHTML`. ([#236](https://github.com/djust-org/djust/pull/236))
- **Textarea newlines preserved** — Template whitespace stripping no longer collapses newlines inside `<textarea>` elements. ([#236](https://github.com/djust-org/djust/pull/236))
- **PresenceMixin crash without auth** — `track_presence()` now checks for `request.user` before accessing it. ([#236](https://github.com/djust-org/djust/pull/236))
- **`_skip_render` support in server_push** — `server_push()` now checks `_skip_render`, preventing phantom renders and VDOM version mismatches. ([#236](https://github.com/djust-org/djust/pull/236))
- **Client-side SetText mis-targets after keyed MoveChild** — MoveChild patches now include `child_d` for `data-dj-id` resolution. ([#225](https://github.com/djust-org/djust/issues/225))
- **VDOM diff/patch round-trip on keyed child reorder** — Patches now processed level-by-level (shallowest parent first). ([#212](https://github.com/djust-org/djust/issues/212))
- **apply_patches djust_id-based resolution** — Resolves parent nodes by `djust_id` instead of path-based traversal. ([#216](https://github.com/djust-org/djust/issues/216))
- **Diff engine keyed+unkeyed interleaving** — Emits `MoveChild` patches for unkeyed element children in keyed contexts. ([#219](https://github.com/djust-org/djust/issues/219))
- **Text node targeting after keyed moves** — `SetText` patches carry `djust_id` when available; `sync_ids` propagates IDs to text nodes. ([#221](https://github.com/djust-org/djust/issues/221))
- **Tag registry test pollution** — `clear_tag_handlers()` now restores built-in handlers in teardown. ([#261](https://github.com/djust-org/djust/issues/261))

### Security

- **HTTP POST handler dispatch gating** — `post()` now enforces the same security model as the WebSocket path: only `@event_handler`-decorated methods can be invoked. Validates event names with `is_safe_event_name()` to block dunders and private methods.
- **Auto-escaping in Rust template engine** — `SafeString` values propagated to Rust for proper auto-escaping.
- **HTML-escaped `urlize` and `unordered_list` filters** — Both filters now escape their output to prevent XSS. ([#254](https://github.com/djust-org/djust/issues/254))
- **Template tag XSS prevention** — All PWA template tags now use `format_html()` and `escape()` instead of `mark_safe()` with f-string interpolation.
- **Sync endpoint hardening** — Removed `@csrf_exempt` from `sync_endpoint_view`. Added authentication requirement, payload validation, and safe field extraction.
- **Silent exception elimination** — All `except: pass` patterns replaced with appropriate logging calls.
- **Production JS hardened** — All `console.log` calls guarded behind `djustDebug` flag.

### Removed

- **`_allowed_events` class attribute** — The backwards-compatibility escape hatch that allowed undecorated methods to be called via WebSocket or HTTP POST has been removed. All event handlers must now use the `@event_handler` decorator.

## [0.2.2] - 2026-02-01

### Fixed

- **Stale Closure Args on VDOM-Patched Elements** — After deleting a todo, the remaining button's click handler sent the wrong `_args` (stale closure from bind time) because `SetAttribute` patches updated the `dj-click` DOM attribute but not the listener closure. Event listeners now re-parse `dj-*` attributes from the DOM at event time. Also sets `dj-*` as DOM attributes in `createNodeFromVNode` and marks elements as bound to prevent duplicate listeners. ([#205](https://github.com/djust-org/djust/pull/205))
- **VDOM: Non-breaking Space Text Nodes Stripped** — Rust parser stripped `&nbsp;`-only text nodes (used in syntax highlighting) because `char::is_whitespace()` includes U+00A0. Now preserves `\u00A0` text nodes in parser, `to_html()`, and client-side path traversal. Also adds `sync_ids()` to prevent ID drift between server VDOM and client DOM after diffing, and 4-phase patch ordering matching Rust's `apply_patches()`. ([#199](https://github.com/djust-org/djust/pull/199))
- **CSRF Token Lookup on Formless Pages** — Pages without a `<form>` element failed to send CSRF tokens with WebSocket events. Token lookup now falls back to the `csrftoken` cookie. ([#210](https://github.com/djust-org/djust/pull/210))
- **Codegen Crash on Numeric Index Paths** — Template expressions like `{{ posts.0.url }}` produced paths starting with a numeric index (`0.url`), generating invalid Python (`obj.0`). Codegen now skips numeric-leading paths since list items are serialized individually.
- **JIT Serialization Pipeline** — Fixed multiple issues in JIT auto-serialization: ([#140](https://github.com/djust-org/djust/pull/140))
  - M2M `.all()` traversal now generates correct iteration code in codegen serializers
  - `@property` attributes are now serialized via Rust→Python codegen fallback when Rust can't access them
  - `list[Model]` context values (not just QuerySets) now receive full JIT optimization with `select_related`/`prefetch_related`
  - Nested dicts containing Model/QuerySet values are now deep-serialized recursively
  - `_djust_annotations` model class attribute for declaring computed annotations (e.g., `Count`) applied during query optimization
  - `{% include %}` templates are now inlined for variable extraction, so included template variables get JIT optimization
  - Rust template parser now correctly prefixes loop variable paths (e.g., `item.field` inside `{% for item in items %}`)
- **`{% include %}` After Cache Restore** — `template_dirs` was not included in msgpack serialization of `RustLiveView`. After a cache hit, the restored view had empty search paths, causing `{% include %}` tags to fail with "Template not found". Now calls `set_template_dirs()` on both WebSocket and HTTP cache-hit paths.
- **VDOM Replace Sibling Grouping** — Fixed `data-djust-replace` inserting children into wrong parent when the replace container has siblings. `groupPatchesByParent()` now uses the full path for child-operation patches, and `groupConsecutiveInserts()` checks parent identity before batching. ([#144](https://github.com/djust-org/djust/pull/144))
- **VDOM Replace Child Removal** — Fixed `data-djust-replace` not removing old children before inserting new ones, causing duplicate content on re-render. ([#142](https://github.com/djust-org/djust/pull/142), [#143](https://github.com/djust-org/djust/pull/143))
- **Context Processor Precedence** — View context now takes precedence over context processors. Previously, context processors could overwrite view-defined variables (e.g., Django's messages processor overwriting a view's `messages` variable).
- **VDOM Keyed Diff Insert Ordering** — Fixed `apply_patches` for keyed diff insert ordering where items were inserted in the wrong position. ([#154](https://github.com/djust-org/djust/pull/154))
- **VDOM MoveChild Resolution** — Fixed `MoveChild` in `apply_patch` by resolving children via `djust_id` instead of index. ([#150](https://github.com/djust-org/djust/pull/150))
- **Debug Toolbar: Received WebSocket Messages Not Captured** — Network tab now captures both sent and received WebSocket messages by intercepting the `onmessage` property setter (not just `addEventListener`). ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Toolbar: Events Tab Always Empty** — Events tab now populates by extracting event data from sent WebSocket messages and matching responses, replacing the broken `window.liveView` hook. ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Panel: Handler Discovery, Auto-loading, Tab Crashes** — Handler discovery now finds all public methods; `debug-panel.js` auto-loads; handler dict normalized to array; retroactive WebSocket hooking for late-loading panels. ([#191](https://github.com/djust-org/djust/pull/191), [#197](https://github.com/djust-org/djust/pull/197))

### Added

- **Debug Panel: Live Debug Payload** — When `DEBUG=True`, WebSocket event responses now include a `_debug` field with updated variables, handlers, patches, and performance metrics. ([#191](https://github.com/djust-org/djust/pull/191))
  See `docs/website/advanced/debug-panel.md`.
- **Debug Toolbar: Event Filtering** — Events tab filter controls to search by event/handler name and filter by status. ([#180](https://github.com/djust-org/djust/pull/180))
- **Debug Toolbar: Event Replay** — Replay button (⟳) that re-sends events through the WebSocket with original params. ([#181](https://github.com/djust-org/djust/pull/181))
- **Debug Toolbar: Scoped State Persistence** — Panel UI state scoped per view class via localStorage. ([#182](https://github.com/djust-org/djust/pull/182))
- **Debug Toolbar: Network Message Inspection** — Directional color coding and copy-to-clipboard for expanded payloads. ([#183](https://github.com/djust-org/djust/pull/183))
- **Debug Toolbar: Test Harness** — Integration tests against the actual `DjustDebugPanel` class. ([#185](https://github.com/djust-org/djust/pull/185))
- **VDOM Proptest/Fuzzing** — Property-based testing for the VDOM diff algorithm with `proptest`. ([#153](https://github.com/djust-org/djust/pull/153))
- **Duplicate Key Detection** — VDOM keyed diff now warns on duplicate keys. ([#149](https://github.com/djust-org/djust/pull/149))
- **Branding Assets** — Official logo variants (dark, light, icon, wordmark, transparent). ([#208](https://github.com/djust-org/djust/pull/208), [#213](https://github.com/djust-org/djust/pull/213))

### Deprecated

- **`@event` decorator alias** — The `@event` shorthand is deprecated in favor of `@event_handler`. `@event` will be removed in v0.3.0. A deprecation warning is emitted at import time. ([#141](https://github.com/djust-org/djust/pull/141))

### Changed

- **Internal: LiveView Mixin Extraction** — Refactored monolithic `live_view.py` into focused mixins: `RequestMixin`, `ContextMixin`, `JITMixin`, `TemplateMixin`, `RustBridgeMixin`, `ComponentMixin`, `LifecycleMixin`. No public API changes. ([#130](https://github.com/djust-org/djust/pull/130))
- **Internal: Module Splits** — Split `client.js` into source modules with concat build, extracted `websocket_utils.py`, `session_utils.py`, `serialization.py`, split `state_backend.py` into `state_backends` package, split `template_backend.py` into `template` package. ([#124](https://github.com/djust-org/djust/pull/124), [#125](https://github.com/djust-org/djust/pull/125), [#126](https://github.com/djust-org/djust/pull/126), [#128](https://github.com/djust-org/djust/pull/128), [#129](https://github.com/djust-org/djust/pull/129))
- **Dependencies** — Upgraded uuid 1.19→1.20, thiserror 1→2, bincode 1→2, happy-dom 20.3.7→20.4.0, actions/setup-python 5→6, actions/upload-artifact 4→6, actions/checkout 4→6, softprops/action-gh-release 1→2

## [0.2.1] - 2026-01-29

### Security

- **WebSocket Event Security Hardening** - Three-layer defense for WebSocket event dispatch: ([#104](https://github.com/djust-org/djust/pull/104))
  - **Event name guard** — regex pattern filter (`^[a-z][a-z0-9_]*$`) blocks private methods, dunders, and malformed names before `getattr()`
  - **`@event_handler` decorator allowlist** — only methods decorated with `@event_handler` (or listed in `_allowed_events`) are callable via WebSocket. Configurable via `event_security` setting (`"strict"` default, `"warn"`, `"open"`)
  - **Server-side rate limiting** — per-connection token bucket algorithm with configurable rate/burst. Per-handler `@rate_limit` decorator for expensive operations. Automatic disconnect after repeated violations (close code 4429)
  - **Per-IP connection limit** — process-level `IPConnectionTracker` enforces a maximum number of concurrent WebSocket connections per IP (default: 10) and a reconnection cooldown after rate-limit disconnects (default: 5 seconds). Configurable via `max_connections_per_ip` and `reconnect_cooldown` in `rate_limit` settings. Supports `X-Forwarded-For` header for proxied deployments. ([#108](https://github.com/djust-org/djust/issues/108), [#121](https://github.com/djust-org/djust/pull/121))
  - **Message size limit** — 64KB default (`max_message_size` setting)

### Documentation

- Added migration guide for `@event_handler` decorator requirement and strict mode upgrade path ([#105](https://github.com/djust-org/djust/issues/105), [#122](https://github.com/djust-org/djust/pull/122))
- Added `@event_handler` decorator to all example demo view handler methods

### Added

- `is_event_handler(func)` — check if a function is decorated with `@event_handler`
- `@rate_limit(rate, burst)` — per-handler server-side rate limiting decorator
- `_allowed_events` class attribute — escape hatch for bulk allowlisting without decorating each method
- `LIVEVIEW_CONFIG` settings: `event_security`, `rate_limit` (including `max_connections_per_ip`, `reconnect_cooldown`), `max_message_size`

## [0.2.0] - 2026-01-28

### Added

- **Template `and`/`or`/`in` Operators** - `{% if %}` conditions now support `and`, `or`, and `in` boolean/membership operators with correct precedence and chaining. ([#103](https://github.com/djust-org/djust/pull/103))

  See `docs/website/getting-started/installation.md`.
### Fixed

- **Pre-rendered DOM Whitespace Preservation** - WebSocket mount no longer replaces `innerHTML` when content was pre-rendered via HTTP GET. Instead, `data-dj-id` attributes are stamped onto existing DOM elements, preserving whitespace in code blocks and syntax-highlighted content. ([#99](https://github.com/djust-org/djust/pull/99))

- **VDOM Keyed Diffing** - Unkeyed children in keyed diffing contexts are now matched by relative position among unkeyed siblings, eliminating spurious insert+remove patch pairs when keyed children reorder. ([#95](https://github.com/djust-org/djust/pull/95), [#97](https://github.com/djust-org/djust/pull/97))

- **Event Handler Attributes Preserved** - `dj-*` event handler attributes are no longer removed during VDOM patching. ([#100](https://github.com/djust-org/djust/pull/100))

- **Model List Serialization** - Lists of Django Model instances are now properly serialized on GET requests. ([#103](https://github.com/djust-org/djust/pull/103))

- **Mount URL Path** - WebSocket mount requests now use the actual page URL instead of a hardcoded path. ([#95](https://github.com/djust-org/djust/pull/95))

### Changed

- **Dependencies** - Upgraded html5ever 0.27→0.36, markup5ever_rcdom 0.3→0.36, vitest 2.x→4.x, actions/download-artifact 4→7. ([#101](https://github.com/djust-org/djust/pull/101), [#102](https://github.com/djust-org/djust/pull/102), [#43](https://github.com/djust-org/djust/pull/43))

### Developer Experience

- **VDOM Debug Tracing** - `debug_vdom` Django config is now bridged to Rust VDOM tracing. Mixed keyed/unkeyed children emit developer warnings. ([#97](https://github.com/djust-org/djust/pull/97))

## [0.2.0a2] - 2026-01-27

### Changed

- **Internal: DRY Refactoring** - Reduced ~275 lines of duplicate code across the codebase through helper function extraction. These are internal improvements that don't affect the public API. ([#93](https://github.com/djust-org/djust/pull/93), [#94](https://github.com/djust-org/djust/pull/94))
  - `getComponentId()` - DOM traversal for component ID lookup (client.js)
  - `buildFormEventParams()` - Form event parameter building (client.js)
  - `send_error()` - WebSocket error response helper (websocket.py)
  - `_send_update()` - WebSocket patch/HTML response helper (websocket.py)
  - `_create_rust_instance()` - Rust component instantiation (base.py)
  - `_render_template_with_fallback()` - Template rendering with Rust→Django fallback (base.py)
  - `_make_metadata_decorator()` - Decorator factory for metadata-only decorators (decorators.py)

## [0.2.0a1] - 2026-01-26

### Changed

- **BREAKING: Event Binding Syntax** - Standardized all event bindings to use `dj-` prefix instead of `@` prefix. This affects all event attributes: `@click` → `dj-click`, `@input` → `dj-input`, `@change` → `dj-change`, `@submit` → `dj-submit`, `@blur` → `dj-blur`, `@focus` → `dj-focus`, `@keydown` → `dj-keydown`, `@keyup` → `dj-keyup`, `@loading.*` → `dj-loading.*`. Benefits: namespaced attributes, no conflicts with Vue/Alpine, no CSS selector escaping required. ([#68](https://github.com/djust-org/djust/issues/68))

- **BREAKING: Component Consolidation** - Removed legacy `python/djust/component.py`. Use `djust.Component` which now imports from `components/base.py`. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Method Rename** - `LiveComponent.get_context()` → `get_context_data()` for Django consistency. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Decorator Attributes Removed** - Deprecated decorator attributes removed: `_is_event_handler`, `_event_name`, `_debounce_seconds`, `_debounce_ms`, `_throttle_seconds`, `_throttle_ms`. Use `_djust_decorators` dict instead. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Data Attributes Renamed** - Standardized data attribute naming for consistency:
  - `dj-liveview-root` → `dj-root`
  - `data-live-view` → `dj-view`
  - `data-live-lazy` → `dj-lazy`
  - `data-dj` → `data-dj-id`
  ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: WebSocket Message Types** - Renamed message types for consistency:
  - `connected` → `connect`
  - `mounted` → `mount`
  - `hotreload.message` → `hotreload`
  ([#89](https://github.com/djust-org/djust/pull/89))

### Added

- **LiveComponent Methods** - Added missing methods to `LiveComponent`: `_set_parent_callback()`, `send_parent()`, `unmount()`. ([#89](https://github.com/djust-org/djust/pull/89))

- **Inline Template Support** - `LiveComponent` now supports inline `template` attribute for template strings, in addition to `template_name` for file-based templates. ([#89](https://github.com/djust-org/djust/pull/89))

  See `docs/website/guides/template-cheatsheet.md`.
- **Form Components Export** - `ForeignKeySelect` and `ManyToManySelect` are now exported from `djust.components`. ([#89](https://github.com/djust-org/djust/pull/89))

  See `docs/website/guides/components.md`.
### Fixed

- **`{% elif %}` Tag Support**: Template parser now correctly handles `{% elif %}` conditionals. Previously, elif branches fell through to the unknown tag handler and rendered all branches instead of just the matching one. ([#80](https://github.com/djust-org/djust/pull/80))

- **Template Include Fallback** - Component `render()` methods now fall back to Django templates when Rust template engine fails (e.g., for `{% include %}` tags). ([#89](https://github.com/djust-org/djust/pull/89))

## [0.1.8] - 2026-01-25

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks. When a child template overrides a block that is nested inside another block in the parent (e.g., `content` inside `body`), the override is now correctly applied. ([#71](https://github.com/djust-org/djust/pull/71))

## [0.1.7] - 2026-01-25

### Added

- **Tag Handler Registry**: Extensible system for custom Django template tags in Rust. Register Python callbacks for tags like `{% url %}` and `{% static %}` with ~100-500ns overhead per call. Built-in tags (if, for, block) remain zero-overhead native Rust. Includes ADR documenting architecture decisions. ([#65](https://github.com/djust-org/djust/pull/65))
- **Comparison Operators**: Template conditions now support `>`, `<`, `>=`, `<=` operators in addition to `==` and `!=`. ([#65](https://github.com/djust-org/djust/pull/65))
- **Enhanced `{% include %}` Tag**: Full support for `with` clause (pass variables) and `only` keyword (isolate context). ([#65](https://github.com/djust-org/djust/pull/65))
- **Performance Testing Infrastructure**: Comprehensive benchmarking with Criterion (Rust) and pytest-benchmark (Python). New Makefile commands: `make benchmark`, `make benchmark-quick`, `make benchmark-e2e`. Enables tracking performance across releases and detecting regressions. ([#69](https://github.com/djust-org/djust/pull/69))
- **Inline Handler Arguments**: Event handlers now support function-call syntax with arguments directly in the template attribute. Use `dj-click="handler('arg')"` instead of `dj-click="handler" data-value="arg"`. Supports strings, numbers, booleans, null, and multiple arguments. ([#67](https://github.com/djust-org/djust/pull/67))

### Fixed

- **Async Event Handlers**: WebSocket consumer now properly supports `async def` event handlers. Previously only synchronous handlers worked correctly. ([#63](https://github.com/djust-org/djust/pull/63))

### Performance

- Dashboard render: ~37µs (27,000 renders/sec)
- Tag handler overhead: ~100-500ns per call
- Template variable substitution: ~970ns
- 50-row data table: ~188µs

## [0.1.6] - 2026-01-24

### Added

- **`{% url %}` Tag Support**: Django's `{% url %}` template tag is now fully supported with automatic Python-side URL resolution. Supports named URLs, namespaced URLs, and positional/keyword arguments. ([#55](https://github.com/djust-org/djust/pull/55))
- **`{% include %}` Tag Support**: Fixed template include functionality by passing template directories to the Rust engine. Included templates are now correctly resolved from configured template paths. ([#55](https://github.com/djust-org/djust/pull/55))
- **`urlencode` Filter**: Added the `urlencode` filter for URL-safe encoding of strings. Supports encoding all characters or preserving safe characters. ([#55](https://github.com/djust-org/djust/pull/55))
- **Comparison Operators in `{% if %}` Tags**: Added support for `>`, `<`, `>=`, `<=` comparison operators in conditional expressions. ([#55](https://github.com/djust-org/djust/pull/55))
- **Auto-serialization for Django Types**: Context variables with Django types (datetime, date, time, Decimal, UUID, FieldFile) are now automatically serialized for Rust rendering. No manual JSON conversion required. ([#55](https://github.com/djust-org/djust/pull/55))
- **Lazy Hydration**: LiveView elements can now defer WebSocket connections until they enter the viewport or receive user interaction. Use `dj-lazy` attribute with modes: `viewport` (default), `click`, `hover`, or `idle`. Reduces memory usage by 20-40% per page for below-fold content. ([#54](https://github.com/djust-org/djust/pull/54))
- **TurboNav Integration**: LiveView now works seamlessly with Turbo-style client-side navigation. WebSocket connections are properly disconnected on navigation and reinitialized when returning to a page. ([#54](https://github.com/djust-org/djust/pull/54))

  See `docs/guides/turbonav-integration.md`.
### Changed

- **AST Optimization**: Template parser now merges adjacent Text nodes during AST optimization, reducing allocations and improving render time by 5-15%. Comment nodes are also removed during optimization as they produce no output. ([#54](https://github.com/djust-org/djust/pull/54))

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks (e.g., `docs_content` inside `content`). Block overrides are now recursively applied to merged content, ensuring deeply nested blocks are correctly resolved. ([#57](https://github.com/djust-org/djust/pull/57))
- **Form Validation First-Click Issue**: Added `parse_html_continue()` function to maintain ID counter continuity across parsing operations. Prevents ID collisions when inserting dynamically generated elements (like validation error messages) that caused first-click validation issues. ([#54](https://github.com/djust-org/djust/pull/54))
- **Whitespace Preservation**: Whitespace is now preserved inside `<pre>`, `<code>`, `<textarea>`, `<script>`, and `<style>` elements during both Rust parsing and client-side DOM patching. ([#54](https://github.com/djust-org/djust/pull/54))

### Security

- **pyo3 Upgrade**: Upgraded pyo3 from 0.22 to 0.24 to address RUSTSEC-2025-0020 (buffer overflow vulnerability in `PyString::from_object`). ([#55](https://github.com/djust-org/djust/pull/55))

## [0.1.5] - 2026-01-23

### Added

- **Context Processor Support**: LiveView now automatically applies Django context processors configured in `DjustTemplateBackend`. Variables like `GOOGLE_ANALYTICS_ID`, `user`, `messages`, etc. are now available in LiveView templates without manual passing. ([#26](https://github.com/djust-org/djust/pull/26))

### Fixed

- **VDOM Cache Key Path Awareness**: Cache keys now include URL path and query string hash, preventing render corruption when navigating between views with different template structures (e.g., `/emails/` vs `/emails/?sender=1`). ([#24](https://github.com/djust-org/djust/pull/24))

## [0.1.4] - 2026-01-22

### Added

- Initial public release
- LiveView reactive server-side rendering
- Rust-powered VDOM engine (10-100x faster than Django templates)
- WebSocket support for real-time updates
- 40+ UI components (Bootstrap 5 and Tailwind CSS)
- State management decorators (`@state`, `@computed`, `@debounce`, `@optimistic`)
- Form handling with real-time validation
- Testing utilities (`LiveViewTestClient`, snapshot testing)

## [0.1.3] - 2026-01-22

### Fixed

- Bug fixes and stability improvements

[Unreleased]: https://github.com/djust-org/djust/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/djust-org/djust/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/djust-org/djust/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/djust-org/djust/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/djust-org/djust/compare/v0.2.0a2...v0.2.0
[0.2.0a2]: https://github.com/djust-org/djust/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/djust-org/djust/compare/v0.1.8...v0.2.0a1
[0.1.8]: https://github.com/djust-org/djust/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/djust-org/djust/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/djust-org/djust/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/djust-org/djust/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/djust-org/djust/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/djust-org/djust/releases/tag/v0.1.3
