# ADRs 034–038: implementation sequence

This is an implementation ledger, not acceptance of the complete proposals.
The ADRs remain Proposed until their transport and security gates pass.

## D-a revised: Django-like error detail under DEBUG

The maintainer revised decision D-a on 2026-09-22. Under `DEBUG`, an explicit
view's errors now read like Django's everywhere: the technical 500 page,
detailed WebSocket and SSE error frames and dev overlay, full log lines with
tracebacks, and the traceback ring. In production they stay value-free.

One rule drives every gate: `_exposure_diagnostics.diagnostics_policy_allows(owner)`,
which is true for a legacy owner or when `settings.DEBUG` is on. It feeds
`restrict_diagnostics` (and so every `log_failure`/`log_failure_for`, the
protected HTTP entry and inherited scopes), the runtime's `expose_details`
callers, the `mount_batch`, `bug_capture_share` and HTTP POST gates, and the
explicit background and save-failure logs.

Debug tooling projections (debug panel, time travel, bug capture) and SQL
parameter capture are not error destinations and keep their redaction. ADR-038
D6's wording changes to match.

The diagnostics suites now assert the value-free contract under
`DEBUG = False`, with `DEBUG = True` cases asserting that the details appear.

## ADR-034 acceptance review — C4

This maps ADR-034's implementation gates and required integration coverage to
the evidence at the final revision. C1–C3 closure accounts, and C4's
acceptance progress, are in the checklist below. Owner decisions are
recorded in ADR-034 as "C1 decisions" (Q1–Q7), "C2 notes" and
"C3 decisions" (Q0–Q6, N1–N4).

**Gate 1: typing proof.** `tests/typing_component_bindings` imports the public
module. mypy and Pyright 1.1.408 reject 31 negative locations, including
inherited declarations, wrong sources, async callbacks, misspelled outputs and
the collection API. The positive files and runtime assertions pass.

**Gate 2: binding and dispatch.**
- `test_interactive_bindings.py`, `test_interactive_snapshots.py` and
  `test_interactive_observations.py` cover the fixed-binding lifecycle.
- `test_interactive_public_api_c1.py` covers the public import, V020, Q004 and
  the debug time-travel jump over a real WebSocket.
- `test_legacy_component_alias_3078.py` pins the fixed legacy alias.
- Registry-only lookup: unknown and stale identities are refused on every
  transport.

**Gate 3: dropdown pilot.**
- `tests/playwright/test_interactive_dropdown.py`: two same-type menus, forged
  selections, observations and reconnect reporting, on pinned WebSocket, SSE
  and HTTP-only.
- `test_adr034_delegated_rows.py`: the separate presentation-only delegated
  rows.

**Gate 4: documentation.**
- `guides/interactive-components.md` is new, in the nav next to Components,
  and has every D7 section:
  - ownership;
  - configuration, state, actions and outputs;
  - one instance, and two instances;
  - client visibility and observations;
  - delegated rows, and keyed collections;
  - persistence, keyboard and focus as implemented, and security.
- `core-concepts/components.md` gains "Choose the owner", and
  `docs/ai/components.md` gains an interactive section.
- `api-reference/components.md` carries the generated tables:
  `scripts/generate-interactive-reference.py`, `make interactive-reference`, a
  pre-commit hook, and `tests/test_generate_interactive_reference.py`.
- All marked "Available from djust 1.3" (not in the 1.3.0rc1 pre-release).
- `test_adr034_documented_examples.py` extracts every documented view with the
  doc-snippet extractor and drives it through a real GET and the HTTP
  fallback. That is six views across the guide and the AI reference. Since
  ADR-037 D2 these run as `python/djust/tests/test_doc_examples.py`
  (`doc_scenarios/adr034.py`).
- djust-docs `docs_verify`, pointed at this branch's `docs/` and djust:
  - links, symbols (709) and a11y pass;
  - the nav finding (`guides/accounts.md`) pre-dates this branch;
  - its symbol check lists the component-scoped decorators
    (`@project_menu.on.selected`) as advisory "not a known djust decorator"
    (recorded under ADR-037 D2).
- The djust-docs site, run locally on port 18462, serves the page at
  `/guides/interactive-components/`. It is linked from the Components page's
  nav and pager, all five sections and ten code blocks render, and the API
  reference renders the generated tables.
- Publication on the live sites happens at release (owner decision C4-Q4).
  The catalogue entry is pending under ADR-037 D2 (C4-Q3).

**Gate 5: stateful repetition.** C3's separate proof: 26 unit tests, the typing
fixtures, and `tests/playwright/test_interactive_collection.py`. Collection
docs were published only after it passed.

**Required integration coverage.**
- **Transports:** HTTP, WebSocket (plus actors refused via V020) and SSE in
  every browser matrix, with the transport checked. Real runs found and fixed
  three transport defects (C2) and the HTTP `djust:error` gap (C4).
- **Template backends:** Django and Rust (`test_interactive_collections_c3.py`,
  `test_interactive_bindings.py`).
- **Isolation:** one dropdown doesn't mutate another, or another user's view
  (C2 matrix; `test_interactive_acceptance.py` two-browser case).
- **Selections:** a selection runs only the matching callback, once, with the
  actual source; forged, disabled and unknown values never emit (C2, C3, C4
  matrices).
- **Collection lifecycle:** reorder, duplicate keys, removal and re-addition,
  reconnect and restored navigation (C3).
- **Nested ownership:** an action inside nested component markup names the
  nearest owner, never an ancestor (`tests/js/interactive-nested-ownership.test.js`).
  The server routes that identity only through the registry (gate 2).
- **Refusals:** direct invocation of a subscription callback and unknown scoped
  targets are refused, with no fallback to a view handler.
- **Rendering and errors:** sibling and view state changes render; async and
  failing callbacks behave as documented (C4).
- **Legacy:** plain components keep their handlers (C4 acceptance, #3078
  tests).
- **Delegated rows:** they carry the correct id after a reorder and refuse
  unauthorized ids.
- **Native visibility:** focus and dismissal through patches and reconnects;
  no traffic without an observation subscription; unchanged observers cause
  no render; duplicate, reordered and old-lifetime reports are dropped
  (C2, C4).
- **Signed Back navigation:** session and signed paths (C4).

**Final-revision runs.**
- Full Python suite at `868df85b4`, from a frozen detached worktree: 33,921
  passed, 949 skipped, 2 failed. Both failures are the known tag-reachability
  failures in `tests/test_changelog_tagged_sections.py` (they fail alone too).
- Full vitest: 2,305 passed across 209 files.
- Typing gate: mypy and Pyright 1.1.408 reject 31 negative locations.
- Browser matrices, on pinned WebSocket, SSE and HTTP-only, all pass:
  - dropdown;
  - collection;
  - acceptance;
  - navigation (WebSocket and SSE);
  - ADR-035's and ADR-036's.

## ADR-035 acceptance review — F2

This review maps each item in the ADR's "Scope and verification" list to its
evidence at the final revision. F1 and F2 closure accounts are in the
checklist below. Owner decisions Q1–Q6 and implementation choices N1–N6 are
recorded in the ADR.

**A no-custom-`mount()` edit example, a create form, a non-model form, and
hooks overridden independently and through inherited views.**
- `test_model_form_acceptance_adr035.py`: edit, create and non-model views,
  and the hooks declared on a view and inherited by a subclass.
- The demo `/demos/model-form/<pk>/` has no `mount()`.

**Binding for empty submission, initial values, prefixes, choices, related
fields and supported uploads; no double save or divergent validation paths.**
- The acceptance suite covers each of these. Every write is counted in SQL:
  one UPDATE per valid save, none when `form_valid` does not save, one INSERT
  for a create.
- `validate_field`, `submit_form` and restore all build the form through
  `get_form()`, the path the F1 order tests observe.

**Unauthorized/missing target, tampered identity, access revoked between
events, failing permission callbacks: no form mutation or data disclosure.**
- `test_model_form_lifecycle_adr035.py` covers HTTP, the shared runtime,
  WebSocket in normal and actor mode, SSE and restore. Every denial is
  asserted to show the same response or frame, with no form, no hook, no
  write and no record name.
- The acceptance suite adds deletion and membership removal between events,
  tampered payload ids, and callbacks raising, on HTTP and on WebSocket
  mount and event.
- The browser matrix covers forged ids and identical 403s.

**Real HTTP, WebSocket, reconnect and back-navigation tests; input and errors
kept as specified, while ORM objects and permission caches are not
persisted.**
- A WebSocket reconnect restored from the session skips `mount()` but still
  resolves and authorizes the object. Typed input survives, and is validated
  again before any save.
- Reconnecting after deletion or revocation is denied.
- Session contents are asserted free of `object`, `kwargs`, the
  configuration names, `_object` and the mount verdict.
- The browser matrix's Back navigation leaves a working form on all three
  transports.

**An authorization-order test that fails if form construction or validation
comes before authorization; a query-count test for within-dispatch reuse.**
- The lifecycle suite records lookup → permission → form on every transport.
- It counts object-lookup SQL: one per mount, one per event.
- Gate-offs: required-object off fails 8 tests, verdict reuse off fails 4,
  the legacy session filter off fails 4, and binding the mount parameters
  instead of the route fails 2.

**Django and Rust rendering, typing, legacy override compatibility and
browser-visible validation/save feedback.**
- `object` and `form_data` render identically in both engines under both
  policies.
- A mypy consumer test types `self.object` as `Optional[Group]`.
- Legacy `FormMixin`/`_create_form` behavior keeps its existing suites
  (`test_form_hooks_adr035.py` and the form suites) unchanged.
- `tests/playwright/test_model_form.py` checks the field error, the save
  message and the reloaded values in Chromium. Its canary fails 18 checks.
  Correction: its SSE and HTTP-only runs were WebSocket runs until ADR-034 C2
  pinned the transport. Real HTTP-only needed two fixes, recorded there. See
  F2 below.

**Documentation (Compatibility and migration: guide, AI reference and
migration recipe after the lifecycle gates).**
- The form guide gains "Editing one record with `ModelFormMixin`" and a
  migration recipe from `_model_instance`. `docs/ai/forms.md` gains the
  adapter section. Both are marked "Available from djust 1.3" (not in the
  1.3.0rc1 pre-release).
- The owner chose to publish with a version note (2026-09-25, recorded in
  the ADR). The existing `_model_instance` examples are unchanged.
- Generators and AI schema: pending under ADR-037 D2/D3 (see that entry).
  - Lifecycle checks are covered:
    - `djust.S013` is new.
    - `djust_typecheck`'s context manifest declares `object`.
    - The X008 IDOR audit does not flag adapter views, since they bind no
      id in `mount()`.
- `test_adr035_documented_examples.py` extracts both documents' Python with
  the doc-snippet checker's own extractor and executes it. It uses the
  guide's route block and the paired HTML, then checks author, other user
  and missing record over GET and the HTTP fallback. Since ADR-037 D2 this
  runs as `python/djust/tests/test_doc_examples.py` (`doc_scenarios/adr035.py`).
- djust-docs' `docs_verify`, pointed at this branch's `docs/` and djust:
  links, symbols (693) and a11y pass. The nav finding (`guides/accounts.md`)
  pre-dates this branch.
- Against the sibling checkout's older djust, the symbols check reports that
  `ModelFormMixin` is not exported. That clears with the docs submodule and
  djust bump at release.

**Final-revision runs.**
- ADR-035 suites: 64 Python tests (lifecycle 31, acceptance 30, documented
  examples 3). The browser matrix passes on all three transports.
- Full Python suite at `9dd6a4421`, run from a frozen detached worktree:
  33,855 passed, 949 skipped, 2 failed. Both failures are in
  `tests/test_changelog_tagged_sections.py` and fail alone too: release tags
  are reachable from this branch, so they are not caused by this change.
- No client JavaScript changed in ADR-035's slices.

## ADR-036 acceptance review — P3

This is the review the ADR's "Scope and acceptance gates" require before
strict mode is considered supported. Each gate is listed with its evidence at
the final revision. P1 and P2 closure accounts are in the checklist below.

**Freeze a valid/invalid conversion matrix.** The ADR lists: false-like
booleans, blank numbers, partial numbers, bool-as-int, overflow/resource
limits, date boundaries, nullable values, arrays and unsupported annotations.
- `test_parameter_contract.py`: 144 core cases, covering every listed class
  plus hostile conversion methods, cycles and subclass budgets.
- `test_parameter_contract_checks.py`: unsupported and unresolvable
  declarations are startup errors (V016).

**Real DOM extraction through wire dispatch, parity across every dispatch
path, `coerce_types=False`.**
- `strict_native_binding.test.js` (21 cases) drives real DOM events through
  the bundle.
- `test_strict_transport_parity.py` runs one 43-row matrix identically
  through eight paths. The paths are: the shared runtime, WebSocket normal
  and actor mode, SSE, both HTTP-fallback shapes, the exposed API and the
  test client. Its rows include `coerce_types=False` and exact `**` key order.
- `tests/playwright/test_strict_parameters.py` checks outbound payloads and
  handler results in Chromium over WebSocket, SSE and HTTP-only. Its canary
  against the pre-activation client fails 24 checks.
  Correction: until ADR-034 C2 pinned the transport, its SSE and HTTP-only runs
  were WebSocket runs. Re-run on the real transports, it passes unchanged.

**Missing/extra/duplicate arguments, keyword-only signatures, forms' open
payloads, client metadata separation, forged component injection.**
- The parity matrix rows.
- `test_trusted_dispatch_context.py` (68 cases, forged keys on every
  transport).
- The component lifecycle case in `test_producer_parameter_contracts.py`.

**Legacy precedence and values unchanged.**
- The legacy controls in the trusted-context, strict-binding and browser
  suites.
- The unchanged legacy suites in every full run.

**Invalid input never invokes application code; diagnostics expose no
sensitive payloads.**
- Every rejection row asserts that the handler was not called, and that
  `SECRET_INVALID` / forged values are absent from frames, responses and
  errors.
- Client rejections report only the handler name (browser matrix and the
  bundle cases).

**Execute the published examples under the intended policy.**
- The events guide's new "Typed event parameters (strict policy)" section and
  the AI events reference now carry strict examples.
- `test_adr036_documented_examples.py` extracts their Python with the
  doc-snippet checker's own extractor, executes it, and renders the paired
  HTML through a real GET. It checks the advertised strict contracts, then
  drives the documented valid and invalid payloads through the real HTTP
  fallback. Since ADR-037 D2 this runs as
  `python/djust/tests/test_doc_examples.py` (`doc_scenarios/adr036.py`).
- `adr036_documented_examples.test.js` mounts the same HTML blocks in the
  bundle under those contracts, and checks that the browser sends exactly
  those payloads.

**Published docs match.**
- djust-docs' `docs_verify`, run with `DJUST_DOCS_SOURCE` pointed at this
  branch's `docs/`: links, symbols (689) and a11y pass.
- Its one nav finding (`guides/accounts.md` absent from `_config.yaml`)
  pre-dates this branch.
- `docs_generate --check` could not run: djust-docs imports the sibling
  checkout's djust (1.2.0rc7), whose generated reference is stale and whose
  components import recurses there. That is a djust-docs environment issue,
  not content.
- Website delivery follows the branch's merge and the docs submodule bump.

**Final-revision runs.**
- Focused ADR-036 Python suites: 542 passed.
- Focused bundle suites: 130 passed.
- Playwright matrix: passes on all three transports.
- Full Python suite at `cf2ea4aa2`, run from a frozen detached worktree:
  33,791 passed, 949 skipped, 2 failed. Both failures are in
  `tests/test_changelog_tagged_sections.py` and fail alone too: release tags
  are reachable from this branch, so they are not caused by this change.
- Full vitest run at the same commit: 2,299 passed across 206 files.

## ADR-038 activation review — E6-5

This is the review E6 requires before the guard is removed. It covers every
gate on the completion branch (#2954), including evidence produced earlier on
`feat/components-catalogue`.

**E1: every automatic sink is classified, with a destination test.**
The sink inventory is the classification.

- **Logs.** `test_log_exposure_pin.py` pins every exception-carrying log call
  in the package. Its open tables and ratchet baseline are empty.
- **Entry points** (HTTP GET, SSE stream and navigation, the WebSocket
  catch-all, construction): `test_exposure_entry_scope_diagnostics.py`, 62
  cases.
- **Application sinks** (PWA, actions, presence, SQL capture):
  `test_exposure_pwa_sync_sinks.py`, `test_exposure_action_errors.py`,
  `test_exposure_presence_meta.py`, `test_exposure_sql_capture.py`.
- **Service-worker caches** (state, VDOM, shell): `exposure_sw_caches.test.js`,
  `exposure_sw_state_storage.test.js`, `test_exposure_sw_caches.py`.
- **Rows that had only function-level evidence** now have destination tests:
  - debug injection, at the HTML destination through E5;
  - embedded children, through the child suites' `embedded_update` frames;
  - React props and the native tree: `test_exposure_react_props.py`,
    `test_exposure_native_render.py`.
- **State-backend writers** were re-enumerated (remaining route 1). The
  inventory row names every writer; the only other match is a docstring.
- **Actors** are refused at configuration (D-o), and at runtime as a second
  line.

**E2: every provider is registered in a bounded manifest.** Components,
actions, streams, uploads, forms, tenant, wizard, drafts, audio, PWA, offline
and the Rust bridge keys all declare `ProviderContract`s, folded into the
schema digest. Evidence:

- `test_exposure_providers.py` and `test_exposure_provider_tags.py`;
- `test_exposure_forms.py` (D-e);
- `test_exposure_uploads.py` (D-g);
- `test_exposure_components.py` (D-h);
- `test_exposure_streams.py`;
- `test_exposure_invalidation.py`;
- `test_exposure_schema_versions.py` (D-i, D-j);
- `test_exposure_orm_render.py`.

Codec v1 is JSON primitives. Old envelopes remount or migrate through a
validated hook.

**E3: ownership and lifecycle.**

- Root background work, `url_change` and every server-originated consumer
  turn authorize fresh and commit before their frame (D-k, D-l):
  `test_exposure_root_background_turns.py`, `test_exposure_consumer_turns.py`.
- Child work queued at mount or by a parent turn runs under the child's owner:
  `test_exposure_child_queued_work.py`.
- Descendant and repeated-instance routing: `test_exposure_child_routing.py`.
- Transient non-sticky children and refused lazy children (D-m):
  `test_exposure_child_transient.py`.
- Shell reconstruction on reconnect: `test_exposure_child_reconnect.py`.
- Removal and re-addition: `test_exposure_child_readd.py`.
- Snapshot keys include the query string (E3-8).
- Revocation and failed storage never deliver a stale frame.

**E5: the end-to-end matrix.** `tests/playwright/test_exposure_matrix.py`
drives the explicit demo view and its legacy twin in Chromium, over WebSocket
and over SSE, through mount, events, background work, a DEBUG failure,
`url_change`, reconnect, cross-worker restore (a WebSocket mount of the same
session on a second uvicorn process sharing the database) and live
back-navigation.

- **Destinations checked:** HTML, every received frame, the flow's decoded
  session row and the server log.
- **Result:** passed on two local workers against this branch, in both error
  modes (D-a revised): with `DJUST_DEMO_DEBUG=0` for production, and the demo
  default under DEBUG, with `DJUST_DEMO_LOG_CONSOLE=1` so the server log is
  observed. Under DEBUG the explicit error frame must carry the detail; in
  production it must reach no frame, page or log. The undeclared and private
  sentinels never appear in either mode.
- **Controls:** the legacy twin's DEBUG frame carries the error sentinel (the
  harness control), and declaring the undeclared sentinel as persisted state
  fails the stored-session assertion on both transports.
- **Restore attacks** (forged, expired, cross-identity, old-schema and
  extra-key restores) are covered at the Python and ASGI level by
  `test_exposure_snapshots.py`, `test_exposure_runtime.py` and
  `test_exposure_schema_versions.py`, not re-driven in the browser.
- **CI:** the matrix runs as a non-blocking `exposure-matrix` job, following
  the browser-smoke promotion path.

The harness found two real defects, both fixed:
- the demo's tenant configuration without `TenantMiddleware` refused every
  explicit request;
- WebSocket mounts carried no tenant.

**E6: activation.**
- **Cost** (`docs/adr/notes/038-cost-measurement.md`): an explicit WebSocket
  event costs about 2× legacy, and an HTTP GET about 15% more.
- **Migration inventory:** the `djust_exposure_inventory` command.
- **Published boundaries:** the "Explicit exposure" guide
  (`docs/website/state/explicit-exposure.md`).
- **Guard change** (`4d9d2cfc4`): "explicit" is accepted. Unknown policies,
  grants on legacy views, and actors under explicit are still refused. Hot
  view replacement refuses policy changes and invalid configurations. Tests:
  `test_exposure_policy_guard.py`, `test_exposure_hot_swap.py`.

**Open questions, all decided** (see ADR-038's *Completion decisions* for
each reason):

- **Fixed after the first review:**
  - server-originated frames now refresh the client snapshot (D-r);
  - explicit views keep no upload resume records (D-g);
  - the PWA batch helpers report class names only (#2950);
  - work a child queues on a sibling is swept (E3-3);
  - `wrapper_template` failures follow the DEBUG contract.
- **Accepted and documented:** the warm shell cache can show legacy chrome
  (D-s); presence ids reach peers (D-v); ambiguous view ids are refused
  rather than checked at render (D-w).
- **Outside ADR-038, filed:** #2947, #2955 (fixed here), #2956, #2957,
  #2958, #2959, #2960, #2961.

**ER (retirement) is scheduled (D-z).** Every Step R target still serves legacy
views, which remain the default. The deletions start when a future major
release makes `explicit` the default, one PR per target, with the reference
inventory re-run.

## Child lifecycle closure — E3-3 to E3-7 and decision D-m

- **E3-3, queued child work.** `start_async` queued in an explicit child's
  `mount()`, or queued on a child by a parent handler, was *delayed*: it ran
  under the child's next routed event, or never if none came.
  `ViewRuntime._dispatch_explicit_child_queues` now sweeps the owned explicit
  tree after the mount frame and after every parent turn. Each child's work
  runs through `_child_async.dispatch_child_work`, with its own batch and
  re-authorization. The consumer's `_dispatch_async_work` calls the same sweep,
  which covers tick, push and NOTIFY turns.
  (`test_exposure_child_queued_work.py`, 5 tests.)
- **E3-4, descendant routing.** Only the root's direct children were routable,
  so any grandchild event got "Embedded view not found". Under an explicit root,
  `_explicit_descendant` now routes through the server-owned registry, and only
  when exactly one owned explicit descendant has that id. Ambiguous or unknown
  ids are refused. Same-type siblings and a grandchild keep separate events,
  saves, background results and storage keys.
  (`test_exposure_child_routing.py`, 5 tests.)
- **E3-5 / D-m, transient and lazy children.**
  - A non-sticky explicit child is transient: its identity is recorded and
    checked, it has no adapter and is never persisted, and it renders without a
    raw `view` in its context.
  - A non-sticky child that declares persisted fields is refused.
  - `lazy=True` on an explicit child raises a fixed-text `TemplateSyntaxError`
    before any child, context or placeholder is built.
  - Explicit server persistence under a legacy parent is refused through the
    tag.
  - (`test_exposure_child_transient.py`, 12 tests.)
- **E3-6, shell reconstruction.** Page-shell children outside `dj-root` were
  never rebuilt on any WebSocket mount, so their stored state was unreachable.
  Explicit `template_name` roots now render the full page once before the
  fragment, the same order the HTTP GET uses. That costs one more full render
  per explicit mount. (`test_exposure_child_reconnect.py`, 2 tests over the real
  consumer.)
- **E3-7, removal and re-addition.** A slot removed and then added again gets a
  fresh mount, the pruned envelope is not resurrected, and the disposed instance
  cannot re-register. No bug was found; the tests were mutation-checked against
  disabled prune disposal. (`test_exposure_child_readd.py`, 3 tests.)

Findings recorded, not fixed:
- Work a child's own handler queues on a sibling or descendant is not swept.
- View ids are flat on the client, so two same-type sticky children at
  different depths share an id; the server refuses to route it, but there is no
  render-time duplicate check.

## Server-originated turns dispatch their queued work (#2955)

`_tick_once`, `server_push` and `db_notify` never called
`_dispatch_async_work`, so `start_async` queued in `handle_tick`, a push handler
or `handle_info` was stranded under both policies. It is the #2946 class of
bug, on the other turns. Each turn now dispatches once its hook succeeds, and a
denied or failed turn dispatches nothing.
(`test_exposure_consumer_turns.py::test_start_async_from_a_server_originated_turn_runs`,
legacy and explicit, red without the fix.)

## Change detection and invalidation — E2-8

Invalidation under explicit already matches legacy, so this slice added no
code fix:
- `_snapshot_assigns` keeps its full `__dict__` walk as the conservative
  fallback. It only decides whether a turn renders, and is never stored or
  sent.
- `_sync_state_to_rust` compares every context key against the previous render,
  so derived keys reach Rust even though explicit changed keys are storage
  names.
- `set_changed_keys()`, in both forms, is documented as the explicit-mode
  invalidation API.

`test_exposure_invalidation.py` (22 tests) pins derived, opaque and
provider-tracked re-renders, both hatch forms, no-op parity (`["noop"]` under
both policies), identical frames across a six-turn sequence, and Django/Rust
child render equality. Seven deliberate breaks each failed their tests.

Finding filed as #2956: `is_dirty` and `changed_fields` never see `state()`
fields, because `_dirty_fingerprint` skips `_state_*` slots.

## Provider manifest and view-dependent tags — E2-0, E2-1, E2-2

**The manifest.** `ProviderContract` (`_exposure.py`) is an immutable record of
what a framework context provider renders, tracks, persists and exposes to the
client, with its codec. Every current provider persists and exposes nothing.
Mixins declare their contracts in `_djust_context_providers`. The view's
contracts are folded into the `ExposureContract` schema digest, so a provider
change invalidates stored envelopes; a contract with no providers keeps its
old digest. `ExplicitRenderContext` and `provide_context`
(`_exposure_providers.py`) record which provider owns each key.

**Keys registered.**
- Components, actions and streams are registered on `ContextMixin`, and
  `csrf_token` and the date/time formats on `RustBridgeMixin`.
- `TenantMixin`, `WizardMixin`, `DraftModeMixin`, `AudioMixin`, `PWAMixin` and
  `OfflineMixin`, which wrote context without registering, now register.
- Under explicit, an application kwarg, a later write (set, update, setdefault,
  pop, del, `|=` or clear) or a context-processor value that collides with a
  provider key raises. Legacy is unchanged.
- An `_action_state` entry for an undeclared action is refused.
- The wizard's flat `<field>_choices` aliases are not rendered under explicit,
  because they depend on runtime form fields and cannot be declared;
  `form_choices` remains.

**View-dependent tags.** `dj_activity`, `colocated_hook`, `live_form`,
`live_field`, `live_errors` and the `field_value`/`has_errors` filters resolve
a nonlegacy rendering view through `get_active_parent_view()` and never put
the raw view into context. The Rust renderer has **no handler for these tags**:
in a root LiveView template they fail with "Invalid block tag" under both
policies. They only run on Django's engine, which in practice means embedded
and sticky children, so they are tested there.

Evidence:
- `test_exposure_providers.py` (59 cases, 29 red on the base).
- `test_exposure_provider_tags.py` (9 cases, 4 red).
- The combined exposure suites passed 1,175 tests after the merge.

Findings recorded, not fixed:
- `{% dj_activity %}` cannot be used in any root template, because Rust has no
  handler for it.
- Sticky-child events have no activity gate under either policy.
- A legacy child's event re-render gets no `view`, so its activities are not
  re-registered.
- The form tags' output is HTML-escaped on Django's engine.
- The non-sticky explicit child's initial render still set a raw `view` in the
  child context; this was routed to the E3-5 slice.

## Schema versions, server-state lifetime and codec — E2-9, E2-5, E2-10

Decisions D-i and D-j.

- **Schema version.** A class-level `exposure_schema_version` (an int from 1 to
  2**31-1, read without evaluating properties) feeds the contract version,
  which the digest already covered. A bump rejects old server and snapshot
  envelopes, and the view remounts.
- **Envelope format.** Server envelopes move to format 2 and record
  `schema_version`. Format-1 envelopes remount.
- **Lifetime.** `DJUST_SERVER_STATE_MAX_AGE` (1 to 86400 seconds, default 3600)
  sets the envelope lifetime for root and child state. System check
  **djust.C020** validates it, and an invalid value fails closed at runtime.
- **Migration hook.** An opt-in `migrate_state(old_version, values)` translates
  an older root envelope before `prepare_restore`. Its output is validated
  exactly like fresh input (extra keys, missing keys and non-primitives are
  rejected). A raising hook remounts, with a log line naming only the class and
  the versions. Child envelopes and client snapshots do not migrate; they
  remount.
- **Codec.** v1 accepts JSON primitives only. `Decimal`, `datetime`, `UUID`,
  model instances, `repr`-able objects and tuples are rejected at save and at
  capture, never stringified.

Evidence:
- `test_exposure_schema_versions.py` has 53 cases, 36 red on the base; a
  `repr()` fallback made 12 of the 14 codec cases fail.
- `test_exposure_streams.py` (5 cases) runs real stream insert and delete
  through the explicit runtime. The Rust and Django renders agree, and the
  item's unrendered field is absent from frames, snapshots, server state and
  debug output.
- `test_exposure_orm_render.py` (2 cases) renders a `User` row over HTTP and
  the real WebSocket under DEBUG, with password and unrendered-field sentinels
  absent from every destination.
- Both of those test-only slices passed on unchanged code and were
  mutation-checked.

Finding recorded, not fixed: `_get_stream_operations()` has no callers, so
stream operations are never sent as frames under either policy; streams reach
the page as ordinary updates.

## Server-originated turns: authorization and persistence — E3-1/E3-2 slice

Decisions D-k and D-l. An explicit root's turns that arrive with no inbound
event request ran with the mount-time principal and never saved declared
state. That covered root `start_async` work on the runtime, `url_change`, and
the consumer's own turns: `handle_tick`, `server_push` handlers, `db_notify` →
`handle_info`, NOTIFY-released activity events and the background work they
start. A revoked session kept receiving renders, and a reconnect restored
stale state.

`ViewRuntime` gains the shared pieces, all used by the child path's pattern:

- `authorize_explicit_turn` reloads the supported server session and Django
  auth (`fresh_socket_request`), runs `authorize_event` against the mount
  binding and object permission, and re-checks that the owner is unchanged.
- `deny_explicit_turn` is the foreground denial (static error, close 4403).
- `commit_explicit_turn` makes a bounded save of the root's declared server
  fields and, optionally, the child tree, before any success frame.

A failed or timed-out save withholds the success frame and sends a static
`state_error`. This now applies to foreground events too, which previously
logged the failure and sent the frame anyway. The error carries a null
snapshot revocation, and the client removes the primary view's token on it; an
error frame can only revoke, never store (`tests/js/state_snapshot_signed.test.js`).

Background work authorizes before the callback and again before
`handle_async_result`, then commits before the result frame. `url_change`
authorizes inside the tenant context, re-checks object permission against the
fresh request instead of the mount-time one, and commits before rendering.
Consumer turns authorize under the render lock, not the explicit event lock,
because the foreground takes the explicit lock first and the reverse order
could deadlock. `_render_background` commits, and the released-event path,
which renders itself, commits the same way. Presence heartbeats and cursor
moves neither render nor persist and are unchanged: authorizing every cursor
move would cost a session load per mouse movement for no stored or rendered
effect.

**Not covered: client snapshot refresh on server-originated frames.** The
client stores tokens only from mounts and primary-view `source == "event"`
frames (`storeSignedSnapshot`). Background, tick, push and NOTIFY frames
therefore persist server state but do not refresh the client's signed
snapshot. A `persist="client"` field changed by such a turn is refreshed at
the next foreground event. Until then, back-navigation can offer the earlier
token. That is a restoration-correctness limit, not an exposure one: the
stale token can only hold values the client was already granted.

Evidence:
- `test_exposure_root_background_turns.py` has 6 tests: persistence across
  reconnect, revocation, failed background and foreground saves, `url_change`
  persistence, and `url_change` revocation.
- `test_exposure_consumer_turns.py` has 6 tests over real sockets: NOTIFY,
  push, tick, and a released event and its background work persisting across
  reconnect, plus NOTIFY revocation.
- Every test failed before its fix for the stated reason.
- The broad exposure, runtime, consumer and child set passed 1,773 tests. The
  full JS suite passed 2,150. mypy passed 1,067 files.

Existing tests changed with the contract:
- `test_exposure_consumer_hook_diagnostics.py` flipped views to explicit after
  a legacy mount. Fresh authorization needs the mount binding, so explicit
  views now mount explicit, and None/invalid policies (which cannot mount) are
  now refused before the hook.
- The task-cancellation tests on unmounted runtimes and consumers grant
  authority through a stub, since they exercise cancellation, not
  authorization.
- The storage-deadline pin gains `commit_explicit_turn`.
- The snapshot `identity` case now expects the `state_error` revocation
  instead of a noop.

## Application sinks — E1-2 and decisions D-c, D-d, D-f

Exception text or values reached destinations without passing a log call:

- **PWA.** The `offline:sync_error` push frame and the sync queue's stored
  `mark_failed` reason both carried `str(exc)`. Nonlegacy views now get a
  generic push and the exception class name. `SyncManager._perform_sync`
  returned exception text in the sync endpoint's JSON; it now returns
  `Batch sync error: <ClassName>` for every caller, which changes legacy output
  (it is a plain Django endpoint, not view state). The `IndexedDBStorage`
  docstring now says it is server memory.
- **Actions (D-f).** A failed `@action` recorded `str(exc)`, which templates
  render. Nonlegacy views record "Action failed" unless the handler raises the
  new `djust.decorators.ActionError`.
- **Presence (D-c).** `track_presence` no longer injects `username`/`user_id`
  for nonlegacy views. The docstrings state that meta is rebroadcast to peers.
- **SQL capture (D-d).** Parameters are redacted when the capture's owner is
  nonlegacy or the diagnostic scope is restricted. The runtime now passes the
  owner to `capture_for_event`.

The new helper `exception_details_allowed_for(owners)` is the non-log
counterpart of `log_failure_for`. Each fix failed first with a legacy control:
`test_exposure_pwa_sync_sinks.py` (5), `test_exposure_action_errors.py` (4),
`test_exposure_presence_meta.py` (2) and `test_exposure_sql_capture.py` (6).
`test_exposure_pwa_diagnostics.py` now asserts the class name instead of
pinning the leak.

Findings recorded, not fixed:
- SQL capture misses sync handlers entirely: their queries run on a worker
  thread's connection that has no wrapper.
- `_sync_create/update/delete_batch` still put `str(e)` into the endpoint's
  `errors`.
- Globally registered sync handlers are registered under the bare model name
  but looked up as `{action_type}_{model_name}`, so they are never called.
- The presence identity (`str(user.id)`) still reaches peers in the record id
  and the `cursor_move` `user_id`.
- SQL text built by interpolation is recorded verbatim.

## Cost measurement and migration inventory — E6-1/E6-2

`tests/benchmarks/test_exposure_cost.py` (benchmark-only) measures legacy
against explicit for the same view. `docs/adr/notes/038-cost-measurement.md`
has the numbers, the environment and the variance. Medians at `bc3a3d156`, on
an M2 Max:

| Path | Explicit vs legacy |
| --- | --- |
| WebSocket event | about 2× slower, +3.3 to 3.9 ms (fresh re-authorization plus the declared-state save of about 1.3 to 1.5 ms) |
| HTTP GET | about 15% slower |
| WebSocket mount | 11 to 16% faster (unexplained) |
| Repeated mount without the shared render cache | no measurable loss |

These were measured before the E3 slice, which adds a session load per
server-originated turn.

`djust_exposure_inventory` (`--view`, `--app`, `--json`) is a static,
values-redacted inventory. For each view it lists the names legacy mode would
export and their destinations, with a suggested explicit declaration. It never
instantiates a view or evaluates a property or factory
(`test_exposure_inventory_command.py`, 6 tests, which also cross-check a real
legacy view's `get_context_data`, `get_state` and private state).

Findings recorded, not fixed:
- A legacy view's `state()` backing slots (`_state_<name>`), `_reactive_state`
  and `_action_state` are saved in `liveview_<path>__private`.
- Legacy context includes `LiveView` configuration attributes (`template`,
  `login_required`, `use_actors`, `sticky`) because the class walk stops at
  `ContextMixin`.
- The WebSocket-built request carries no tenant, so explicit mounts fail
  closed under a header tenant resolver, which matters for E5 settings
  coverage.

## Service-worker caches — E1-3, E3-8 and decisions D-b, D-n

The worker keeps three caches on disk: the signed-snapshot state cache, the VDOM
(mount HTML) cache and the page-shell cache. The server now sends three
value-free signals (`security/service_worker.py`):

- **Eligibility (D-b).** Only a legacy page whose children are all legacy may
  be cached (`_exposure.service_worker_cache_eligible`). Unreadable children
  fail closed. Ineligible pages send `X-Djust-SW-Cache: no-store` on the HTTP
  response, which the worker checks before writing the shell, and
  `"sw_cache": "no-store"` on the mount frame, which the client checks before
  `cacheVdom`.
- **Identity (D-n).** Every mount frame carries `sw_identity`, a 128-bit
  `salted_hmac` of the session key and the authenticated user id, keyed on
  `SECRET_KEY`. The client clears all three caches when the marker changes,
  disappears (logout) or cannot be read. The worker runs VDOM and shell
  operations through the same ordered queue as state operations, so a clear
  always lands before the next user's first write.
- **Lifetime (D-n).** A mount frame carrying a snapshot also carries
  `state_snapshot_max_age`. The worker rejects older state entries on lookup
  and deletes them.

State and VDOM entries are now keyed by pathname plus query string (E3-8), so
`/orders?page=1` and `/orders?page=2` no longer share a token.

Identity clearing and the lifetime check apply to legacy pages too, since D-n
does not restrict them to explicit views. This addresses the legacy report in
#2948.

Evidence: `tests/js/exposure_sw_caches.test.js` runs the real client into the
real worker and asserts the stored bytes (8 cases, 7 red before the fix, with
an unchanged-identity control). `test_exposure_sw_caches.py` covers the digest,
the eligibility rule, the header and the mount fields (7 cases).

Finding recorded, not fixed: with a warm shell cache, the worker serves the
cached shell before any mount frame arrives. The first navigation after an
identity change can therefore still show the previous user's page chrome;
closing that needs the worker to check identity without going to the network.
The browser-level logout test waits for the E5 harness.

## Protected entry points — E1-1 and decision D-a

Five entry points ran explicit-view code with no protected scope, and
`handle_exception` defaults to allowing details:

- HTTP GET, sync and streaming;
- the SSE stream GET;
- SSE navigation replacement;
- the WebSocket `receive` catch-all, reached by `mount_batch`,
  `live_redirect_mount`, uploads, presence, `request_html`, time travel and
  bug-capture sharing;
- the runtime constructor catch.

Under DEBUG they leaked the exception and, over HTTP, frame locals through
Django's technical 500 page.

*Superseded for `DEBUG = True` by "D-a revised" above: these entry points now
show Django-like detail under DEBUG. What follows remains the production
(`DEBUG = False`) behaviour.*

The fixes:

- **HTTP.** `LiveView.as_view` wraps a nonlegacy class's callable in a scope
  restricted to the class (`_protect_http_entry`). `Http404` and
  `PermissionDenied` keep Django's handling. `BadRequest`-type exceptions
  become a plain 400. Everything else becomes the project's `handler500` page,
  with `got_request_exception` sent while a fresh value-free `ExposureError` is
  being handled, so error trackers still see an event.
- **SSE.** The stream GET and navigation replacement own scopes that watch the
  runtime's view.
- **WebSocket.** `receive` runs each message under `owned_diagnostic_scope`.
- **Constructor.** `_instantiate_view` passes
  `expose_details=uses_legacy_exposure(view_class)`.
- A legacy class keeps Django's own callable, the same object as before.

The guard's own configuration errors are framework-authored and value-free, so
they now raise `ExposureConfigurationError` (an `ImproperlyConfigured`
subclass) and pass through the protected entry. A misconfigured view still
tells the developer why under DEBUG.

Evidence: `test_exposure_entry_scope_diagnostics.py` has 62 cases under DEBUG,
covering responses, stream bytes, frames, logs, the traceback ring and the
`got_request_exception` payload. All 46 nonlegacy cases failed against the base
commit, and the 16 legacy controls pass on both.

Findings recorded, not fixed:
- Under DEBUG, an `Http404` message from an explicit view still reaches
  Django's technical 404 page, though without frame locals.
- `mixins/request.py:get` logs `wrapper_template` render failures with the
  exception text.
- The legacy snapshot-emit branch of `dispatch_mount` calls `logger.exception`
  directly.

## Runtime log-exposure pin — E1 slice

The pin, renamed `test_log_exposure_pin.py`, now covers `runtime.py` beside
`websocket.py`. Each of `runtime.py`'s 26 sites was classified by reading its
guard in place, not by proximity: 10 legacy-gated, 7 framework-only, 9 open
(listed in the inventory). Reintroducing the original `set_layout` log call
fails the pin naming the runtime site.

Classifying `runtime.py` exposed a blind spot in the pin's key. Two sites in
`on_mount_render_ready` log the identical message, so a (function, message)
key collapsed them into one entry — and they are guarded differently, one by
an `elif` condition and one inline. An unguarded duplicate would have been
invisible. Repeated keys now carry an ordinal in source order, pinned by a
scanner self-test. The consumer had no such duplicates. Across both modules,
19 application-code sites remain open (10 consumer, 9 runtime). *(This slice
first said 18 — a hand sum that dropped one; the counts below are corrected
from the pin tables.)*

The two deferred-callback twins followed — `ViewRuntime._flush_deferred` and the
consumer's copy, reached through `server_push` with `_skip_render`. Each
reproduced (runtime: explicit from mount; consumer: explicit/None/invalid) with
legacy controls passing. Beyond the exception, their `repr(callback)` fallback
logged a `functools.partial`'s bound arguments; the tests pin both channels.
17 open.

The consumer's `_flush_pending_layout` twin followed. Its first test run
passed for `None` and invalid policies before any fix — vacuously: those
policies fail closed in `get_context_data` before the layout render, and a
value-free line from that refusal satisfied the assertion. The test now
records that the patched render ran and covers only the two policies that can
reach it; `explicit` was red before the fix and green after. 16 open.

Mount wiring's presence-group setup calls the overridable `get_presence_key`
and logged its failure with the exception. Reproduced at mount (explicit from
mount; the test records that the hook ran), fixed with `log_failure` at the
original WARNING level inside the mount's already-restricted scope. 15 open
(8 consumer, 7 runtime).

The `full_html_update` Django signal is sent with `send`, so an application
receiver's exception reaches `on_render_emitted`'s catch, which logged it with
`exc_info` at DEBUG. Reproduced with a connected receiver that records its call
(explicit from mount), fixed with `log_failure` at DEBUG inside the render
turn's restricted scope. The signal's own payload was already safe: its
`context_snapshot` is the redacted debug projection for explicit views.
14 open (8 consumer, 6 runtime).

The WS and SSE `recheck_event_auth` catches are reclassified legacy-gated, not
fixed: the method's sole production caller, `_dispatch_event`, invokes it only
when `uses_legacy_exposure(view)`; explicit views take fresh event
authorization instead. (Separately noted: the re-check fails open — any error
returns `True` and the event proceeds. That is a deliberate defense-in-depth
choice for legacy views, outside this slice.) 12 open (8 consumer, 4 runtime).

## Shared log_failure primitive and runtime layout — E1 slice

Scanning beyond the consumer refuted the pin slice's claim about `runtime.py`:
the same AST rule finds 26 exception-carrying raw log calls there, plus 10 in
`time_travel.py`, 5 in `mixins/request.py`, 4 in `mixins/async_work.py`, 3 in
`mixins/sticky.py`, 2 in `live_view.py` and one each in `sse.py`,
`mixins/rust_bridge.py`, `mixins/activity.py` and `mixins/waiters.py`. A runtime
turn's diagnostic scope does not help them: only `handle_exception` consults
`diagnostics_allowed()`.

`_exposure_diagnostics.log_failure(log, exc, msg, *args, level=…, traceback=…)`
is now the logging counterpart of that gate: the call site's own message,
level and traceback wherever details are allowed, the value-free line
otherwise. Runtime sites call it directly, since the turn's scope already
tracks the owner. The consumer's `_log_view_hook_failure` now opens a scope,
restricts it to its two owners and delegates — which also corrected an
earlier inaccuracy: the helper had logged `untrack_presence` at ERROR where the
original catch used WARNING. A test now asserts the level.

First runtime site: `ViewRuntime._flush_pending_layout` logged a `set_layout`
render failure with `logger.exception`. Reproduced over the real consumer with
views explicit **from mount** — an earlier probe that flipped the policy
mid-session was discarded as vacuous, because fresh event authorization
rejected the event before the layout path. Explicit cases fail against the
original `runtime.py` and pass after; legacy controls pass throughout.

The same probe tested a structural hypothesis — that an explicit view's
exception escapes to `LiveViewConsumer.receive`'s unscoped outer catch and
reaches the client. It did not reproduce: the runtime's protected catch sent
the generic error first. No entry-point scope was added for a leak that could
not be shown.

## Consumer log-exposure pin — E1 slice

Fixing leak sites one at a time is a denylist: the next `logger.exception`
added to consumer code reopens the risk silently — the omission problem
ADR-038 cites against ADR-012. `test_log_exposure_pin.py` turns the
inventory's classification into a structural pin. It scans `websocket.py` for
every logging call that carries exception data and requires each to appear in
exactly one table — `HELPER`, `LEGACY_GATED` (guard read), `FRAMEWORK_ONLY` or
`KNOWN_OPEN` — with a reason, keyed by function and message literal so line
drift does not matter. `KNOWN_OPEN` holds the 16 application-code sites not yet
fixed and may only shrink.

The first shrink followed: of the five `disconnect` cleanups marked
unconfirmed, `untrack_presence` was a real leak (reproduced, 3 failing cases,
now routed through the helper); the upload cleanup is legacy-gated; the
NOTIFY group leave, waiter cancellation and child unregistration are
framework-only. `KNOWN_OPEN` is down to 11.

`_mount_one` followed, and it leaked to the client as well as the log: under
`DEBUG`, a nonlegacy view's `str(exc)` reached the batch's `failed[]` entry.
Its owner is the class the entry names, resolved by `resolve_view_class` and
fail-closed if unresolvable. `KNOWN_OPEN` is down to 10. This fix exposed a
limit of the pin: the site survives inside the legacy branch with the same key,
so the pin still passed with it listed as open. The pin catches unclassified
and stale sites, not mislabelled ones; moving an entry between tables remains a
reviewed edit.

Mutation-checked three ways: reverting the cursor fix fails the pin naming
the reintroduced site; renaming a message fails it as unclassified; fixing an
open site without deleting its entry fails it as stale. A self-test pins the
scanner against each carrying form, so the pin cannot pass by the scanner
going blind. Only `websocket.py` is pinned. *(Corrected: this slice first
said `runtime.py` routes its catches through `handle_exception`. It does not —
see the next slice.)*

## Tick and NOTIFY hook diagnostics — E1 slice

Recounting the consumer's exception-carrying log calls with an AST scan
(instead of a single-line regex) replaced the inventory's site list: the
regex had missed multi-line calls, including two production application
hooks. `_run_tick` logged `handle_tick` failures and `db_notify` logged
`handle_info` failures, each with the message and traceback, for any policy.
Both are reproduced over the real consumer — tick on its timer, NOTIFY through
the channel group — failing for explicit/None/invalid with legacy controls
passing, and both now go through `_log_view_hook_failure`. The helper now
takes the call site's own `msg`/`args`, so every converted site's legacy
output is unchanged by construction. The tick fix was also checked by
restoring the original catch alone: the three nonlegacy cases fail again.

Reproducing tick exposed an unrelated defect, recorded in the inventory and
not fixed here: a view whose `tick_interval` is shorter than its mount time
never ticks, because the loop wakes before the consumer's `view_instance` is
assigned and stops.

The inventory now classifies all 44 sites as fixed, legacy-gated (verified by
reading each guard), open, or framework-only. E1 remains open.

## Consumer hook diagnostics — E1 slice

`LiveViewConsumer.receive` dispatches `presence_heartbeat` and `cursor_move`
straight to view methods an application may override, and their catches
logged the stringified exception with no policy check — the leak
`handle_exception` exists to prevent. Reproduced over the real consumer: for
explicit, `None` and invalid policies the hook's sentinel reached the log
(6 of 8 cases failed; the legacy controls passed, proving the hooks ran).
Both now log through `_log_view_hook_failure`, which checks the hook's view
and the current owner at the logging boundary and emits the value-free line
for nonlegacy owners. All 8 cases pass; the 82 existing presence, cursor and
heartbeat tests are unchanged; mypy is clean.

`server_push` is fixed the same way: it runs an application handler from the
channel layer, and its `logger.exception` wrote the message and traceback.
Reproduced through `apush_to_view` (3 failing cases); the helper's
`traceback=True` keeps legacy output — message and traceback — unchanged, which
the legacy control asserts. The 89 existing push and broadcast tests pass.

The same pattern is wider. The consumer has 44 exception-logging sites that
bypass `handle_exception`; about twenty wrap application code — embedded child
and layout renders, deferred and async callbacks, sticky unmount hooks,
`mount_batch`, time travel, bug-capture share, `server_push`. The inventory's
*Consumer-local exception logging finding* lists them by line with a
framework-only classification for the rest. They are the next E1 slice and
each needs its own reproduction. E1 remains open.

## Actor caller inventory — E1 slice

The actor system has exactly two runtime entrances — the actor mount call
(`runtime.py:2883`) and the actor event call (`:3283`) — and every other actor
call is reachable only through them. Both are refused for nonlegacy views, so
actor render state is unreachable under explicit policy; the inventory now
classifies actors as **unsupported, refused at entry**.

The mount refusal already had a test. The event refusal (`:3268`) had none,
because it cannot be reached by mounting a nonlegacy view: only a late policy
transition after a legacy actor mount gets there.
`test_actor_event_refuses_after_a_late_policy_transition` does exactly that
over the real WebSocket consumer, for `explicit`, `None` and `invalid`, with a
`legacy` control that must reach the actor so the refusal cases cannot pass by
bypassing the actor branch. Removing the refusal fails all three nonlegacy
cases and leaves the control green.

With this, the three caller inventories the earlier E1 slices named — browser
storage, backends and actors — are classified. E1 itself stays open: the
runtime debug hooks, the outer transport and foreground-event diagnostic
catches, and the foreground frame matrix are still listed as open in the
inventory, and remaining routes 2-5 are not closed.

## Backend and store writers — E1 slice

The inventory's backend row claimed the three `backend.set` calls in
`RustBridgeMixin._initialize_rust_view` were the automatic Python writers.
Enumerating every `get_backend()` caller found more writers to the same
backend: the ADR-034 C2 observation claims and registrations
(`components/_interactive.py:234`, `:414`), plus `session_utils` and the CLI.
The observation writes are value-free — a `uuid4` lifetime key and an integer
sequence in both the memory and Redis backends — so the claim about
*application* data holds, and the row now names every writer.

Two destinations outside the state backend are now classified. The bug-capture
snapshot store (`bug_capture.py:180`), used above the inline limit, was never
exercised by an exposure test; `test_bug_capture_store_destination_holds_only_the_debug_projection`
forces the store path, asserts it was taken, and reads the stored bytes. It was
mutation-checked against a skipped store path (which would otherwise pass
vacuously) and against unprojected bytes reaching the store. Django's
`{% cache %}` fragment cache (`template_libraries.py:1489`) is developer-invoked
and stores rendered output derived from the template-context projection, so it
adds no separate exposure.

E1 remains open. The actor caller inventory was then the remaining named E1 task.

## HTTP API assigns — E1 slice

Working down the ratchet baseline surfaced an automatic sink the inventory
did not list at all: the ADR-008 HTTP API. `dispatch_api` answers
`{"result": …, "assigns": …}`. `result` is the handler's own response, outside
the automatic contract (D6). `assigns` was automatic and had no policy check:
`_public_assigns_snapshot_diff` returned every public attribute the handler
changed. Reproduced through `dispatch_api` with an explicit view whose handler
changed a declared `client=True` field, a declared server-only field and an
undeclared public attribute: the response carried the **undeclared** attribute
and omitted the declared client field — the inverse of D1. For a nonlegacy
view, `assigns` is now the diff of the `client` projection `get_state` uses,
taken before and after the handler; an unavailable projection yields no
fields. The inventory gains an HTTP API row.

The module's seven application-code log catches (handler, `server_function`,
view instantiation, response transform) now use `log_failure_for` — the view,
or the view class where instantiation failed. The handler case is reproduced;
the rest share the shape. Both new tests fail against the original
`dispatch.py`. The module moves from the ratchet baseline into the pin, with
its two malformed-JSON catches classified framework-only; the baseline is 205
sites in 69 modules.

`mixins/context.py` and `mixins/jit.py` are pinned with no code change: all
eight of their sites are unreachable for a nonlegacy view or framework-only.
`get_context_data` returns `_get_explicit_context_data` for a nonlegacy view
before its descriptor-resolution and JIT catches; `_apply_context_processors`
runs processors in an explicit branch without a catch and returns before the
logged legacy loop; every JIT serializer is reached only from
`get_context_data`'s legacy continuation or `_deep_serialize_dict`, itself
called only there; the processor-import catch reports a settings path. The
baseline is 197 sites in 67 modules.

`templatetags/live_tags.py` repeated the drift pattern. The sticky helper
`_render_sticky_child_html` already raised a value-free `ExposureError` when an
explicit child's `get_context_data` failed; its non-sticky twin in
`live_render` and the lazy renderer `_render_eager` instead logged the
exception with its traceback **and rendered the child with an empty context**,
for any policy. Reproduced for the non-sticky path through Django's `Template`
(the test records that the child's context ran): the explicit child fell back
silently. Both copies now raise the helper's `ExposureError` for an explicit
child; legacy children still fall back and log. The lazy copy imports its
names under aliases, since the enclosing `live_render` binds them only after
the lazy branch returns. The lazy thunk's outer catch — which also sees
template errors from an explicit child — logs through `log_failure_for` with
the child class as owner; the lazy path is converted without its own
reproduction. Lazy and non-sticky explicit children remain an open E3 area
beyond this failure path. Separately noted: the thunk renders a
`PermissionError`'s text into the page by design, a deliberate user-facing
channel left as is. `live_tags.py` is pinned (4 legacy-gated, 3
framework-only); the baseline is 189 sites in 66 modules.

Five more modules are pinned. `presence.py`'s `handle_presence_join` and
`handle_presence_leave` calls are application hooks that logged failures with
`logger.exception`; reproduced by driving `track_presence`/`untrack_presence`
on a view (both hooks recorded as run) and fixed with `log_failure_for`.
`mixins/template.py`'s `arender_chunks` thunk catch and page-shell sidecar
catch (built from template-context values) are converted without their own
reproductions. `observability/views.py`'s mount and handler catches follow
`_mutation_policy_gate`, which refuses nonlegacy views; `mixins/notifications.py`
and `updates.py` are framework-only. The ratchet flagged the converted
`mixins/template.py` sites as stale before they were classified — it working
as intended. The baseline is 167 sites in 61 modules.

Upload modules are classified (the `UploadWriter` catches wrap application
writer code that receives upload bytes and metadata, never view state — a
judgment recorded in the pin), and a further batch is pinned.
`pwa/mixins.py`'s eight offline-sync catches ran application
`sync_create/update/delete_<model>` handlers over client-queued data and logged
the exception text, which can echo that data; reproduced through
`_sync_create_actions` (red against the original) and converted with a
reusable converter that re-parses after each edit. `state_backends/redis.py`'s
(de)serialize catches handle the legacy view cache that explicit initialization
bypasses; its other catches, `template_tags/__init__.py`, `db/notifications.py`
and the component gallery are framework-only.

Recorded, not fixed: the PWA sync failure branch also persists `str(exc)` into
the offline sync queue (`mark_failed`) for any policy — a storage destination,
not a log, pinned by the test's assertion so a change is visible. The baseline
is 116 sites in 54 modules.

Thirteen more modules are pinned. Converted, because they run application
code with an owner in scope: the object-permission check in
`auth.core.enforce_object_permission` and its event-path twin in
`websocket_utils._validate_event_security` (both fail closed on a
non-`PermissionDenied` error from the developer's `get_object` /
`has_object_permission`, and logged its text — reproduced for the first, red
against the original, denial unchanged), the `@action` wrapper, the tutorial
steps and `SimpleLiveView.render_template`. Classified without change:
`handle_exception`'s detailed branch sits behind its own diagnostics gate;
`state_backends/memory.py`'s `get` serves the legacy view cache; the rest are
framework-only.

Left in the baseline on purpose: `components/base.py`, `components/suspense.py`,
`serialization._rehydrate_component` and `session_utils.dom_id_for` run
application code but have no reliable reference to their owning view, and an
owner that defaults to legacy would grant rather than restrict. They need an
owner threaded through, which is a design change, not a relabel. The baseline
is 95 sites in 41 modules.

The remaining 34 baseline modules are pinned as framework-only, each with a
reason: client-storage bridges, the deploy CLI, audit and check tooling, the
Django admin integration (outside the LiveView exposure policy), theming and
PWA endpoints, catalogue/gallery examples that render framework-shipped
components, cloud-upload plumbing. **Every exception-carrying log call in
`python/djust` is now classified in the pin, except 14 sites in 7 modules held
on purpose**: `components/base.py`, `components/suspense.py`,
`serialization._rehydrate_component` and `session_utils.dom_id_for` (no owner
reference), `template/rendering.py` (its JIT pair not yet traced to a legacy
guard), and `pwa/sync.py` / `pwa/utils.py` (below).

**PWA offline cache — resolved, not a browser sink.** `OfflineMixin.get_cached_or_fetch`
caches `list(queryset.values())` — every column of the model, with no field
allowlist — into `OfflineStorage`, whose `IndexedDBStorage` docstring says
"actual IndexedDB operations happen client-side (service worker)". Traced: its
`_get_js_bridge` is an in-process Python dict ("used for server-side state
tracking and testing"), no `push_event` or template ships the cached data, and
the only IndexedDB code in the client bundle is the resumable-upload module. So
the cache is server memory, which D1 permits; the docstring overstates a
client counterpart that does not exist. `pwa/utils.py` is pinned
framework-only. Still held, with corrected reasons: `pwa/sync.py` runs
application conflict resolvers with no view owner, and `template/rendering.py`
is djust's Django template backend, which JIT-serializes whatever context its
caller supplies and has no LiveView owner reference.

**Value-quoting catches outside any view helper.** `LiveComponent`
assign validation, the `dj_suspense` fallback and the stream `dom_id=`
factory fallback logged the offending value, template error or row repr.
They carry no owner reference but run inside a view's turn, so they now use
the turn-gated `log_failure` (`test_exposure_value_log_diagnostics.py`, red on
the old code for all three explicit cases). `serialization._rehydrate_component`
was already value-free (class path, kwarg names, exception type) and is pinned
framework-only. The unreviewed baseline is down to 5 sites in two modules,
both owner-less by construction: `pwa/sync.py` (application conflict
resolvers, the sync endpoint) and `template/rendering.py` (the Django template
backend's JIT serializer). Closing those needs an owner channel, not a log
edit — a decision, not a mechanical fix.

**Owner-less sites closed (#2951).** Decision: turn-gated `log_failure`, not
an owner parameter or unconditional redaction. `DjustTemplate` (djust's Django
template backend — LiveViews render through Rust, not through it) and the PWA
sync endpoint, batch sync and custom conflict resolvers serve ordinary Django
requests with no LiveView behind them, so outside a turn ADR-038 does not apply
and their output is unchanged; inside a turn (a handler calling
`render_to_string`) the turn's owner decides. An owner parameter had nothing
to carry, and `DjustTemplate.render` is fixed by Django's
`render(context, request)`; unconditional redaction would have changed logs for
apps that never opted in. `test_exposure_ownerless_log_diagnostics.py` runs all
five sites with no turn, a legacy turn and an explicit turn; the five explicit
cases fail against the original catches. **The unreviewed baseline is empty.**
The log dimension of E1 is closed apart from the two `_run_async_work` sites
held `KNOWN_OPEN` behind #2946.

Found reading `pwa/sync.py`, outside ADR-038 (not view state) and added to
#2950: `_perform_sync` also appends `f"Batch sync error: {str(e)}"` to the
result's `errors`, which `sync_endpoint_view` returns to the client in its JSON
response.

**#2946 fixed; the log pin has no open sites.** The consumer's
`_dispatch_single_event` now starts queued background work unconditionally, as
`ViewRuntime` has since #1887. It's split out as #2952 against `main` for the
1.2 release and cherry-picked here. That makes the two `_run_async_work` catches
reachable from the NOTIFY drain. Both now log through `_log_view_hook_failure`
with the view as owner. The released-event exposure test gains a `spawn` case
(red with the original catches), and the strict xfail is replaced by
`test_notify_released_start_async_2946.py`. `KNOWN_OPEN` is empty in both
consumer and runtime tables, and the package baseline is empty: **the log
dimension of E1 is closed.** E1 itself stays open for the non-log items in the
inventory.

A process note: the first conversion added `as exc` to `except` lines by line
number after an earlier edit had shifted them, producing
`except PermissionDenied as exc as exc:`. `mypy` caught it before any test ran.
The file was restored from HEAD, the uncommitted assigns fix re-applied, and
the conversion redone by re-parsing after every edit.

## Package-wide log ratchet — E1 slice

`time_travel.py` is pinned: 9 legacy-gated sites and 1 framework-only. The
three capture catches are unreachable for nonlegacy views because
`explicit_debug_projection` is non-`None` for every nonlegacy policy — invalid
and unknown policies return a value-free placeholder — so those views never
reach `_capture_snapshot_state`; the six restore/replay catches follow a guard
that returns `False` for nonlegacy views.

**Correction.** Earlier slices put the remaining unpinned surface at "~28
sites". That counted a hand-picked module list. A scan of every non-test module
under `python/djust` finds 286 exception-carrying log calls in 79 modules: 72
are in the ten pinned modules, and **214 in 70 modules are unclassified**, among
them `mixins/context.py`, `mixins/jit.py`, `api/dispatch.py` and
`templatetags/live_tags.py`, which handle view values directly.

Rather than leave those uncounted, they are frozen in a generated baseline
(`tests/fixtures/log_exposure_unreviewed.json`) under a ratchet, as ADR-023 did
for strict typing: a new exception-carrying log call anywhere outside the
pinned modules fails the pin, and a baselined site that disappears must be
deleted from the file, so it only shrinks. Mutation-checked both ways. E1
remains open: the 214 are unreviewed, not safe.

**Separate finding, outside ADR-038 (recorded, not fixed).**
`DjustLogSanitizerFilter`, the CWE-117 log-injection safety net that
`DjustConfig.ready()` installs, is attached to the `djust` *logger*. Python
applies logger filters only to records logged on that logger itself, not to
records from child loggers such as `djust.websocket` or `djust.runtime`, where
nearly all framework logging happens. Verified: a CRLF argument was sanitized
through `djust` and passed through raw from `djust.websocket`. Its docstring
promises every framework message is sanitized; only explicit per-call
`sanitize_for_log(...)` actually is. The fix needs a design choice (djust does
not own the application's handlers), so it is left for the maintainer.

## NOTIFY-released event diagnostics and a dropped start_async — E1 slice

Once a released event passes fresh authorization, the consumer's
`_dispatch_single_event` runs its handler, re-render and any `start_async`
work. Reproduced over the NOTIFY path (explicit from mount, legacy controls
passing, each case recording that its handler ran): the handler and re-render
catches logged the exception and traceback. Both, plus the waiter-notification
and strip/extract catches in the same function (converted, not independently
reproduced), now log through `_log_view_hook_failure`.

The `start_async` case exposed a functional defect, recorded and not fixed:
the dispatcher decides `has_async` from the legacy single-task `_async_pending`
field, but `start_async` writes `_async_tasks`, so `start_async` work from a
NOTIFY-released activity event is silently dropped — for legacy views too. A
strict-xfail test pins it; fixing the drop will fail that marker. Until then
the consumer's two `_run_async_work` log sites are unreachable from this drain
and stay open, to be fixed with it. 8 open (4 consumer, 4 runtime).

`dispatch_mount`'s `state_snapshot_signed` catch is reclassified legacy-gated,
now confirmed rather than inferred: before the branches only a settings read
runs, the legacy branch tests `legacy_exposure` before touching any view
attribute, and a nonlegacy view's only branch is wrapped in its own value-free
catch. Nothing application-derived can reach the outer catch for it.
7 open (4 consumer, 3 runtime).

Both post-event save catches now log through `log_failure`. For an explicit
view the save projects declared `persist="server"` values and, per
`_exposure_sessions`, "storage exceptions propagate" — so a storage error could
carry server-only data straight into `logger.exception`. Reproduced with the
session store's `aset` failing after mount — synthetic, but the one write the
legacy and explicit save paths share, and the test records that the save
reached it. The sticky-child save has the identical shape and is converted
without its own reproduction. 5 open (4 consumer, 1 runtime).

The consumer's `_maybe_push_tt_event` (DEBUG-only time-travel push) logged a
failed push with `logger.exception`. For explicit views the snapshot is already
the redacted debug projection, so the realistic exposure is small, but the
catch printed the traceback for any policy. Reproduced with the snapshot's
`to_dict` stubbed to raise (synthetic; the test records it was reached) and now
logged through `_log_view_hook_failure`. 4 open (3 consumer, 1 runtime): the two
`_run_async_work` sites blocked on the dropped-`start_async` defect,
`handle_bug_capture_share`, and scoped component render.

`handle_bug_capture_share` had two channels. A `ValueError`/`RuntimeError`
from its re-render went to the **client** as `str(exc)`; anything else went to
`logger.exception`. Reproduced both over the real consumer (explicit from
mount; each case records that the render raised). For a nonlegacy owner only
framework `ExposureError` text — value-free by construction, and a
`ValueError` subclass — still reaches the client; other exceptions become the
generic error and log through the helper. One acknowledged trade-off: the
production-gate `RuntimeError` also becomes generic for explicit views, which
only fires where bug capture is disabled anyway.

This exposed a gap in the pin: it scans logging calls, not client error frames
that interpolate an exception (`send_error("…%s" % exc)`). That pattern is not
yet inventoried. 3 open (2 consumer, 1 runtime).

That gap is now pinned for direct interpolation. An AST scan of `websocket.py`
and `runtime.py` for `send_error` / `_send_debug_error` / `send_json` / `send`
calls whose arguments name the caught exception finds four sites, all gated:
three time-travel `_send_debug_error` calls reached only after a restore or
replay that refuses nonlegacy views, and `handle_bug_capture_share`'s legacy
branch. A new such site fails the pin (mutation-checked in
`handle_cursor_move`). Indirect flows — `detail = str(exc)` passed later — are
not visible to the scan.

`_render_scoped_component` (ADR-032) logged a failed component render with
`exc_info` before its D6 fallback; its `try` covers `get_context_data` and the
component's template. Now `log_failure` at DEBUG. The evidence is unit-level:
the method runs inside a diagnostic scope restricted to a nonlegacy owner, as a
runtime turn sets it up, and the log honours it (failing before the fix, the
unrestricted control logging the sentinel). An end-to-end explicit-view
reproduction needs bound components under the explicit policy, which is E2.

`runtime.py` now has no open sites. The two that remain in the pinned modules
are the consumer's `_run_async_work` catches, unreachable from the NOTIFY
drain until the dropped-`start_async` defect is fixed. 2 open (2 consumer,
0 runtime). The other ~28 raw log sites in `time_travel.py`,
`mixins/request.py`, `mixins/async_work.py` and elsewhere are still unpinned.

Seven more modules are now pinned. Of their 13 exception-carrying log sites,
6 are legacy-gated by guards read in place (three sticky hooks, the time-travel
component snapshot, the activity drain and waiter predicates), 2 are
framework-only (time-travel buffer allocation, template hashing), and 5 were
open and are fixed:

- `assign_async`'s four runner catches logged a failed loader's exception text.
  They run as background tasks and `_execute_async_task` opens no diagnostic
  scope, so `log_failure` alone would still have leaked; they use the new
  `log_failure_for(log, owners, …)`, which opens a scope restricted to its
  owners. Reproduced end to end (explicit from mount; `data` declared so the
  errored result can be stored).
- `sse._flush_deferred_to_sse`, the third deferred-callback twin, with the same
  `repr(callback)` channel. Unit-level: the module function is driven directly
  over a view.

The consumer's `_log_view_hook_failure` now delegates to `log_failure_for`.
`mixins/async_work.py` and `sse.py` are pinned with empty tables, so a new raw
exception log there fails the pin (mutation-checked). Still unpinned:
`time_travel.py` (10) and `mixins/request.py` (5).

`mixins/request.py` is pinned, and its HTTP-POST event failure was a
**client-facing** leak: `post` built `f"...: {type(e).__name__}: {str(e)}"`,
logged it with `exc_info`, and under `DEBUG` returned it with
`traceback.format_exc()` and the posted `params` in a 500 response. The HTTP
path serves explicit views, so an explicit view's exception text and traceback
reached the browser. Reproduced through the real pipeline (`RequestFactory`, a
DB session, GET then POST) with a legacy control; a nonlegacy view now gets the
value-free log line and the generic response even under `DEBUG`. The client
frame scan could not see this — the value flows through a variable into
`JsonResponse` — which is the indirect-flow limit already recorded.

The first version of that fix added a local
`from .._exposure import uses_legacy_exposure` inside the `except`, which made
the name local to all of `post()` and raised `UnboundLocalError` on every POST;
the broad regression run caught it (24 unrelated failures) before commit. The
module-level import is used instead.

`_produce` (streamed rendering) is converted to `log_failure_for` without its
own reproduction: `arender_chunks` renders the view's templates with its
context. Only `time_travel.py` (10, DEBUG-only) remains unpinned.

## NOTIFY-released activity events — E3 slice

`ActivityMixin._queue_deferred_activity_event` queues an event sent to a
hidden activity without validation, on the documented contract that the full
auth stack runs when the event is dispatched. For an explicit view that stack
includes ADR-038's fresh `authorize_event`. The runtime's own drain satisfies
it — the runtime authorizes the turn first — but `db_notify` drains through the
consumer's `_dispatch_single_event`, outside any authorized turn, and that
dispatcher ran only `_validate_event_security`.

Reproduced over the real consumer (`test_exposure_activity_notify_auth.py`):
an explicit view queues an event on a hidden activity; a NOTIFY's
`handle_info` makes the activity visible. With the session intact the event is
released and dispatched (the control). With the session deleted between queue
and NOTIFY, the handler **still ran**. The consumer dispatcher now applies the
runtime's check for nonlegacy targets — `authorize_event` on the transport's
fresh request against the root's mount binding — with the runtime's
fail-closed outcome: no dispatch, the static "Event authorization failed"
error, close 4403. A target without that binding (anything but the mounted
root) is refused, and later events in the same drain are refused silently.
The deleted-session case fails against the original consumer and passes after.

This closes one revocation case on one path. E3 remains open: other paths
that release queued or deferred work outside an authorized turn have not been
audited the same way.

## Service-worker state storage — E1 slice

Closes the browser-storage row of [the sink inventory](notes/038-exposure-sink-inventory.md),
which the previous E1 slice named as next. The writer and reader chains are
cited there. The destination is CacheStorage, persisted to disk.

`tests/js/exposure_sw_state_storage.test.js` joins the real client bundle to the
real service worker through the actual `postMessage` bridge and asserts on the
bytes CacheStorage holds, with sentinels in every frame field outside the
token: only the signed token persists, verbatim, in a fixed four-key envelope;
child-view, background and error frames cannot reach storage; a `null`
revocation deletes the entry. Each of the three tests was mutation-checked —
persisting the whole frame, removing the eligibility gate, and disabling the
worker's forget each fail exactly their own test.

Three findings are recorded, not fixed: no at-rest TTL on state entries and no
logout/identity clearing (both **E5**, decided before **E6**), and pathname-only
keys that collapse query strings (**E3**). E1 remains open; the actor and
legacy/Rust backend caller inventories were then the remaining E1 closure tasks.

## Root and deferred background acknowledgements — E4 slice

Root event dispatch and deferred redispatch now capture named and legacy
background work before acknowledging the event. Noop and rendered responses
advertise an opaque batch token; completion is sent only after every captured
task settles. Dispatch verifies the captured root is still the runtime owner.
This reuses the child batch contract without treating intermediate task renders
as completion of the originating request.

The real SSE endpoint regression failed before the fix because named tasks had
no async acknowledgement metadata. Coverage now includes rendered and noop
responses plus direct deferred redispatch through the real session runtime.
The focused suite passed 30 tests. The full Python suite passed 30,044 tests
with 952 skipped before the final two deferred test cases were added; mypy
passed all 1,021 source files. Native browser clicks against a temporary local
server passed over both WebSocket and SSE: loading stayed active through two
task updates and cleared on batch completion. That browser fixture exercises
legacy root dispatch, not explicit-root authorization or cross-worker restore.

E4 remains open: separate component-dispatch and mount callers still use the
legacy queue drain. Root async authorization/persistence remains in E1/E3.
Queues created after batch capture, failure ordering and the remaining lifecycle
matrix require further coverage. No ADR is accepted by this slice and the
explicit-exposure construction guard remains in place.

## Component-route background acknowledgements — E4 slice

The separate component dispatch route now advertises the same captured batch
for noop, subtree patch and full-page responses. All three dispatch that exact
batch after the acknowledgement. Background queue/cancellation bookkeeping is
classified as framework-internal for change detection, preserving noop and
subtree rendering. Capturing an empty batch no longer creates an uninitialized
task queue (which could otherwise break later unnamed task scheduling).

The focused runtime, child-routing, SSE and batch suite passed 72 tests. Native
browser clicks against a temporary server passed over WebSocket and SSE: a
descriptor component's foreground subtree patch and both background patches
kept the button disabled; batch completion released it. No synthetic frames
were supplied by the browser harness. This verifies the legacy-root descriptor
component route, not explicit-root authorization or cross-worker restoration.

The full Python run passed 30,049 tests with 952 skipped; mypy passed 1,021
source files. A subsequently added unnamed-task regression passed with all
five batch tests; that extra test is not included in the full-run count.

Mount callers and legacy child dispatch remain on the lifecycle checklist;
this slice does not close E4 or activate explicit exposure.

## Legacy child-owned background dispatch — E3/E4 slice

Legacy embedded events now capture and acknowledge the selected child's task
batch rather than draining the root queue. Callbacks and result handlers run on
that child; successful results render an async embedded update and use the
existing sticky-child persistence policy. Registry identity and disposal/
generation checks before dispatch and after asynchronous boundaries suppress
late result handling and delivery for removed or replaced owners. Completion
still releases the acknowledged batch even when delivery is suppressed.

The new runtime tests verify child results, untouched parent queues, removed
owners and completion. Temporarily restoring the old parent dispatch caused
both tests to fail; the corrected tests pass. The affected runtime, async,
explicit-child and sticky-child regression set passed 455 tests, and mypy
passed 1,021 source files. This slice has no new live-browser or cross-worker
evidence and does not claim the legacy best-effort persistence policy is the
explicit exposure contract. The strict explicit-child path remains separate.

Mount-time work has no originating event request: do not attach its completion
to a later foreground request. Its ownership/failure matrix still needs review
before E4 closes, along with the remaining client fallbacks.

Additional bundled-client tests verify that mount-time async patches with no
event name cannot acknowledge a later foreground request, and that legacy
no-ref replies are accepted only when one request is outstanding. Both rules
pass for WS and SSE. The full JavaScript suite passed 2,015 tests in 188 files.
These are client frame tests, not live mount/reconnect deployment evidence.

## HTTP/cache correlation — E4 slice

HTTP fallback and cache-hit operations now participate in the shared request
registry. Their awaited operation owns completion locally; no server-supplied
ref is trusted to settle another request. Cleanup runs in finally, so an HTTP
failure or cache application failure releases only that operation's loading.
Teardown keepalive requests remain detached and never allocate foreground refs.

Both HTTP overlap tests failed before the fix: the first response cleared the
other request's loading, for success as well as failure. They now pass, as does
a real cache-hit path interleaved with pending HTTP work in the bundled client.
The focused HTTP/socket tests passed 49 cases before the cache case was added;
all three new HTTP/cache cases pass. These tests drive the actual bundle with
controlled fetch promises; they are not live Django/browser HTTP evidence.
The full rebuilt-client suite passed 2,018 tests in 189 files; bundle ESLint
passed with zero warnings. No Python implementation changed in this slice.

Remaining E4 audit items include page replacement while HTTP work is pending,
transport ownership of buffered unsolicited patches, and failure/recovery
ordering. Keep those separate from mount-time frames, which do not own a
foreground request. This slice does not close E4.

## Outgoing HTTP response ownership — E4 slice

HTTP fallback captures the root DOM owner, URL and navigation generation before
sending. Responses are discarded before application if those change while
waiting for headers or parsing the body. The navigation generation also covers
same-URL navigation that reuses the root node. Teardown remains detached and
the existing finally cleanup releases each local request.

Two replacement tests failed before the guard, demonstrating that stale page
metadata reached the new page from either await boundary. The final fixture
also covers the native before-navigate signal without changing the DOM or URL.
These controlled-fetch bundled-client tests do not establish live browser
navigation or cancellation of a fetch that never settles. Buffered socket
update ownership and navigation/disconnect cancellation remain E4 audit items.
The final rebuilt-client suite passed 2,021 tests in 189 files and bundle
ESLint passed without warnings. The earlier six fragment-test failures were a
window stub missing addEventListener, corrected without changing their asserts.

## Navigation cancellation of ordinary HTTP requests — E4 slice

Ordinary HTTP fallback now passes an AbortController signal to fetch and
tracks it until the operation exits. Native djust/Turbo navigation signals
and pagehide abort the outstanding ordinary requests. Their finally blocks
release request/loading ownership, and intentional aborts are not reported as
HTTP failures. Teardown keepalive sends are never registered for cancellation.

Three new tests failed before the signal was added. They exercise navigation,
Turbo navigation and page exit, asserting that the pending handler settles,
loading/ref state clears and a subsequent request has an un-aborted signal.
The tests use abort-aware fetch doubles, not a real browser network transfer.
The full rebuilt-client suite passed 2,024 tests in 189 files, including the
existing keepalive teardown regressions; bundle ESLint passed with no warnings.
Buffered socket-update ownership remains open; no ADR is accepted by this slice.

## Buffered socket-update ownership — E4 slice

Buffered server updates now carry client-only transport ownership in a WeakMap,
not a forgeable frame property. Disconnect/error cleanup and root/noop/embedded
reply drains consume only that transport's entries. Buffering/draining consults
its pending requests rather than blocking on requests from another connection
or HTTP operation. Existing contiguous-version consumption and deferred-frame
recovery markers are unchanged. Unknown error references cannot discard work.

Three new tests failed before the change: old disconnect/error erased the new
connection's buffer, and old pending requests prevented its drain. Additional
tests cover embedded acknowledgements and unknown error references. Tests drive
the actual bundle with controlled transports; this is not live reconnect or
cross-worker evidence. E4 still needs error/recovery ordering review and its
final evidence audit; the full ADR acceptance checklist remains authoritative.
The final rebuilt-client suite passed 2,029 tests in 189 files, including the
existing deferred-version/recovery tests; bundle ESLint passed without warnings.

## Same-connection error ordering — E4 slice

A valid event error now settles only its request and retains earlier buffered
server updates while another owned request is pending. When the final request
settles, including via an error, those updates drain through the existing
response/version checks. Disconnect still discards the disconnected owner's
buffer. This avoids losing state whose contiguous version was already consumed.

Two bundled-client regressions failed before the fix and now cover error then
noop and error then error, asserting both retained buffer state and eventual
visible metadata application. These are controlled frame tests, not live
transport/browser evidence. The E4 final evidence audit remains open.
The full rebuilt-client suite passed 2,031 tests in 189 files; bundle ESLint
passed without warnings. No Python implementation changed in this slice.

## Disconnect during buffered delivery — E4 audit finding

The acceptance audit reproduced stale delivery after disconnect inside the
first buffered update: root/noop/error/embedded drains had detached their entire
batch before application awaited. A shared drain now takes one frame at a time,
leaving later frames queued and owned so disconnect can discard them. It also
rechecks outstanding owned requests before taking the next frame.

Three failing-before tests cover noop, error and embedded acknowledgements,
disconnect from the first update's metadata callback, and assert that the
second update is never applied. This is actual bundled-client callback behavior
with controlled frames, not a live reconnect test. E4 remains open for the final
evidence audit and refreshed live backend/browser checks.
The rebuilt-client suite passed 2,034 tests in 189 files; bundle ESLint passed
without warnings. No Python implementation changed in this slice.

## Legacy async compatibility — E4 audit finding

Actual bundled-client tests exposed stuck scoped loading for old servers that
send async_pending without an opaque batch token. The client now retains those
legacy origins in transport-owned records and releases them on the matching
async event result, including deferred and embedded results. Modern tokenized
batches remain independent. Disconnect clears both types through shared cleanup.
The legacy protocol identifies completion by event name, not individual task;
it cannot provide modern per-task identity when names overlap.

Both WS/SSE regressions failed before the fix. The final rebuilt-client suite
passed 2,036 tests in 189 files and bundle ESLint passed without warnings.
The refreshed server regression set passed 455 tests at 8515822e2; subsequent
changes are client-only. Native browser overlap checks at that revision passed
for both WS and SSE: ref 1's patch retained loading and ref 2's noop released it.
That browser fixture bypasses the explicit construction guard only in its
temporary process; it is not evidence for production exposure activation.

## Exposure sink inventory and root diagnostics — E1 slice

[The sink inventory](notes/038-exposure-sink-inventory.md) maps current context,
rendering, persistence, snapshot/storage, actor and diagnostic destinations to
their producers, existing tests and remaining closure work. It is deliberately
not marked exhaustive; backend callers and full destination sentinels are open.

Its first reproduced defect was root background logging: nine cases leaked
callback/result-handler/task-name sentinels under explicit or invalid policies.
The fix emits value-free diagnostics for nonlegacy work and checks policy again
at the logging boundary. Added transition cases verify legacy-to-explicit
changes cannot reveal late failures. The exposure/async regression set passed
569 tests, and mypy passed 1,022 source files. These are staged sink tests,
not production explicit construction or root-background authorization evidence.

E1 remains open. Root async authorization/persistence remains E3; actor/backend
and browser-storage caller inventories are the next E1 closure tasks.

### Render-cache boundary

The backend trace reproduced transient render context retained in the memory
backend and legacy context restored into an explicit view with the same key,
for both HTTP and WebSocket initialization. Explicit renderers now remain
instance-owned and do not resolve/read/write the legacy backend. A policy
transition also replaces an existing legacy renderer rather than continuing
to mutate its cached object. Declared server persistence remains separate.

The three-root Python run passed 30,073 tests with 952 skipped before the
additional policy-transition regression. The final focused cache/context/HTTP
set passed 35 tests, including the transition's failing-before/passing-after
case. This is not actor, cross-worker, or browser acceptance. Actor mount remains
an uncovered route distinct from the already-refused actor event path; E1 stays
open. The loss of shared render-baseline reuse must be measured under E6.

### Staged actor mount refusal

The actor mount route is now refused for nonlegacy policies before lifecycle
hooks or transport registration, matching the existing actor-event refusal.
Six real-WebSocket single/batch cases first failed because lifecycle work ran;
the explicit-policy cases also entered actor dispatch. They now assert static
refusal, no lifecycle/actor call, surviving batch siblings, and a responsive
shared socket. The actor rendering/auth/mount/runtime regression set passed 65
tests. Actor support itself is still an acceptance blocker, not completed by
this safety gate. A separate cache-marker regression ensures the renderer's
policy flag is framework state rather than persisted application-private state.

After both changes, the full three-root Python suite passed 30,081 tests with
952 skipped (four workers), and full-package mypy passed 1,024 source files.
No JavaScript changed or browser acceptance was performed in these two slices.

## Mount diagnostic destinations — additional E1 evidence

`test_exposure_mount_diagnostics.py` first reproduced 30 leaks across five
mount failure stages, two DEBUG modes, and policy transitions, while ten legacy
cases preserved existing behavior. The shared error handler now has a redacted
mode that emits only a static log and generic frame without inspecting the
exception or writing the traceback ring. The runtime mount catches require
legacy policy at both entry and failure, including authorization and actor
render failure. Template-hash fallback logging also propagates nonlegacy
failures rather than exposing them before the outer boundary.

Independent review found another on-path bypass: a successful explicit-to-legacy
transition before a later template failure still enabled nested hash-fallback
logging. Two added regressions failed before correction. A turn-local diagnostic
scope now carries the initial restriction through nested/worker calls, with
tests for nested scopes, concurrent requests and cleanup after exceptions.

All 60 diagnostics cases pass. Turning off the actor catch's policy check made
both explicit-transition cases fail again. The final focused error/auth/runtime
set passed 142 tests; mypy passed 1,026 source files. Remaining
outer-transport/constructor/event diagnostic
routes are still open; neither E1 nor ADR-038 is accepted by this evidence.

The final unchanged code passed three consecutive full Python runs across all
three roots: 30,141 passed and 952 skipped in each run (four workers). The
independent read-only re-review found no further issues within the named mount
diagnostic scope after the policy-transition fix. No browser or Rust-suite
acceptance is inferred from these Python results.

## Current acceptance checklist

### Runtime event diagnostic slice

The root foreground/deferred handler catches and full-render catch now preserve
entry-time diagnostic restrictions and recheck policy after callbacks. Tests
cover log, traceback-ring and response-frame destinations, legacy behavior,
unprintable protected exceptions, and policy changes inside rendering itself.
The 68-case regression file passes; its initial matrix reproduced 24 leaks and
the independent review's render-transition matrix reproduced six more before
correction. Outer transport errors, constructor failures and other callback
catches remain E1 work. This slice does not accept ADR-038 or open its
production construction guard.

Final unchanged code passed three consecutive full Python runs across all three
roots: 30,209 passed and 952 skipped per run (four workers; 235.39s, 240.42s,
239.87s). Mypy passed 1,027 source files. Independent read-only re-review found
no further issues in this bounded slice. The earlier full run failed only the
new changelog fragment's required bullet format; that was corrected before these
three clean runs. No browser or Rust-suite acceptance is inferred.

### Callback diagnostic slice

The runtime's four waiter notification paths share a protected helper, and
time-travel/deferred-drain catches preserve entry and current owner restrictions.
Native waiter predicates and activity queue dispatch also protect their internal
catches without dropping pending waiters or preventing later queued work.
The regression suite first reproduced 24 outer callback leaks, one owner-replacement
leak and 12 native-mixin leaks. Independent review found three further nested-root
transition leaks; diagnostic scopes now watch the runtime-owned view slot so
nested catches see current policy and replacement. Restrictions observed after
successful callbacks persist through the rest of the notification/drain pass.

All 86 callback regressions pass; the focused runtime/mixin/exposure set passed
331 tests, and mypy passed 1,028 source files. Independent re-review ran 26 native
and scope tests successfully. Disabling owner-slot registration in-process made
all three nested-root predicate regressions fail with sentinel disclosure,
confirming the guard is exercised. Constructor, outer transport, layout,
persistence and other callback diagnostics remain E1 work; no ADR is accepted
or activated by this slice.

Final unchanged code passed three consecutive full Python runs across all three
roots: 30,295 passed and 952 skipped per run (four workers; 235.25s, 235.72s,
236.48s). Repository-pinned Ruff checks and formatting passed. No browser,
Rust-suite or website-delivery acceptance is inferred from these results.

### Shared inbound diagnostic boundary

Real WebSocket and SSE HTTP probes reproduced twelve protected disclosure cases
at outer error handlers while four legacy cases retained expected diagnostics.
`dispatch_message` now contains protected failures within its diagnostic scope,
sending generic correlated event errors rather than exposing details through
outer transport or Django error handling. Delivery and close failures remain
value-free, and cancellation still propagates.

Independent review found a nested-unwind gap: a handler observed as explicit
could later become legacy in a failing hook, and scope cleanup restored the outer
permission before the catch. Four real-transport regressions reproduced this;
protected exceptional unwinding now carries its restriction to the enclosing
scope without retaining it after the outermost exit. The transport regression
file has 65 cases, including both SSE endpoints, malformed direct calls,
correlation, delivery failure, cancellation and scope reset. The focused exposure
set passed 279 tests. Independent re-review passed 19 focused boundary/scope
cases with no further bounded findings. Direct mount/constructor, replacement,
WebSocket-only and already-swallowed hook errors remain E1 work; this is not
activation or completion of the four dependent ADRs.

The final unchanged inbound-boundary code passed three consecutive full Python
runs across all three roots: 30,360 passed and 952 skipped per run (four workers;
233.88s, 232.50s, 231.85s). Mypy passed 1,029 source files; repository-pinned lint
and formatting passed. These are Python/ASGI results, not browser, Rust-suite or
website-delivery acceptance.

Updated 2026-09-20. This section is the current work queue; the implementation
sections below are chronological evidence, not independent open-task lists.
An earlier "pending" statement may be superseded by a later implementation
section. The ADR decisions and acceptance sections remain authoritative: this
checklist groups their requirements, it does not reduce them.

The original four ADRs are 034–037. ADR-038 is their additional exposure-policy
prerequisite. **ADR-038's gates E1–E6 and ER are closed on the completion branch
(#2954): `exposure_policy="explicit"` is activated there, and ER is closed by
the written account in D-z, with the deletions scheduled for the major release
that makes `explicit` the default.
ADRs 034–037 are not accepted.**
No completion percentage or delivery date is inferred from commit/test counts.

### Completion rules

- A checked foundation means only the named boundary has evidence, not that its
  enclosing ADR is complete. An unchecked gate may already have partial code.
- Close a gate with implementation commit, named asserting tests, completed run
  results, and any required browser/deployment evidence. Record skipped coverage
  and limitations. Frame replay is not live backend/browser verification.
- Each implementation slice names one gate and its exit tests before editing.
  Record new findings against an existing gate; if none fits, explicitly amend
  this queue and explain the scope change. Do not silently broaden a slice.
- Security or correctness failures on the slice's actual path must be resolved
  before closing it. Independent findings stay visible in their own gate.
- Do not activate explicit exposure or publish proposed APIs as supported while
  their gates are open. Changing scope requires an explicit ADR decision, not
  checking off an unsupported case as if it passed.

### ADR-038 — explicit context and state exposure

Source: [decisions and acceptance](038-explicit-context-and-state-exposure.md).

- [x] Foundation: typed per-instance state and separate immutable exposure
  projections, bounded primitive validation, and guarded construction.
- [x] Foundation: server-session and signed-snapshot adapters, staged context,
  debug/direct-state boundaries, and staged HTTP/shared-runtime integration.
- [x] Foundation: eager sticky-child identity, authorization, restoration,
  persistence, disposal/pruning, and selected-child background dispatch.
  Evidence and limitations are in the corresponding sections below.
- [x] **E1 — exporter inventory and closure.** Enumerate actual rendering,
  persistence, snapshot, browser-storage and diagnostic sinks, including actor
  and root-background paths. For each, map policy enforcement and a sentinel
  test at the destination; identify unsupported paths explicitly. No implicit
  context/attribute fallback or unclassified sink may remain at activation.
- [x] **E2 — provider contract closure.** Complete bounded manifests and
  lifecycle tests for components, forms, actions, streams and uploads, including
  inheritance, dynamic context and invalidation. Resolve schema/codec needs and
  migration/expiry handling. Exercise deliberate ORM rendering without granting
  automatic persistence or client disclosure.
- [x] **E3 — ownership/lifecycle closure.** Cover mount/parent-queued child work,
  descendant and repeated-instance routing, lazy/nonsticky and mixed policies,
  shell reconstruction, removal/re-addition, and root-background authorization
  and persistence. Test revocation and failed storage without stale delivery.
- [x] **E4 — correlated transport lifecycle.** The bounded request-correlation
  milestone below passed its exit audit at cf73aeaf8. Acknowledgements/loading
  are request-owned and background work is distinct. E3 lifecycle authorization
  and E5 deployment/renderer acceptance remain separate open gates.
- [x] **E5 — end-to-end safety matrix.** Run actual HTTP, WebSocket and SSE
  flows with Django/Rust rendering, browser reconnect/back navigation and
  cross-worker restoration. Assert sentinels at every destination under DEBUG,
  failures and forged/expired/cross-identity/old-schema restores. Include legacy
  coexistence and provider/no-op parity as those dependent APIs land.
- [x] **E6 — activation review.** Measure serialization/render cost and migration
  effort; publish supported backend/provider/codec boundaries and migration
  guidance. Review E1–E5 evidence and dependent API integration before removing
  the constructor guard. No zero-leakage or performance claim without evidence.
- [x] **ER — retirement (closed by written account, D-z).** Every target survives
  activation because it still serves legacy views; ADR-038 D-z records why and
  schedules the deletions for the major release that makes `explicit` the
  default. Original gate text: Delete the implicit-exposure machinery the explicit
  policy replaces, per [ADR-038 Step R](038-explicit-context-and-state-exposure.md):
  `_FRAMEWORK_INTERNAL_ATTRS` (`live_view.py:105`) and its six consumers, the
  `get_context_data` attribute walk (`mixins/context.py:215`, `:242`), private
  persistence selection (`live_view.py:644`, `:853`, `:871`), the
  `serialization.py` sensitive-name floor as an implicit-walk backstop
  (`:60`, `:69`, `:78`, `:81`), and last the `"legacy"` arm (`_exposure.py:90`,
  `:97`, `:113`). One deletion PR per target, code and tests together; re-run the
  94-source/160-test reference inventory and record the delta. Any target NOT
  deleted is reported with its reason, as ADR-027 Step 5 did — a survivor
  contradicts the simplification premise and is not quietly dropped.

### ADR-036 — typed event parameters

Source: [decisions and acceptance](036-typed-event-parameter-contracts.md).

- [x] **P1 — canonical contract.** Implement signature-derived metadata and
  freeze the valid/invalid conversion matrix, including optional/collection and
  unsupported types, resource limits, duplicate/extra/missing values and
  framework-versus-application arguments.
  The staged [strict core](notes/036-strict-contract-core.md) now provides shared
  binding/conversion/metadata with 144 core regression cases. The focused strict
  and legacy/security set passes 377 tests. The subsequent
  [server integration](notes/036-strict-server-integration.md) adds opt-in policy
  resolution, canonical metadata and bound-call invocation through Python and
  Rust actor paths. Complete registration/check coverage, trusted argument
  separation and client/transport acceptance remain open; legacy is the default.
  Independent review ran the 144 core cases; final full Python validation passed
  30,504 tests with 952 skipped, and mypy passed 1,031 source files.
  Server integration re-review passed the then-current 188 core/integration cases;
  the final owner-default cache regression brings the focused suite to 190 passed.
  Independent source review found no further cache issues. The Rust target,
  mypy (1,032 files), security and repository hooks passed. Three consecutive
  full runs on the final unchanged implementation each passed 30,550 tests with
  952 skipped (249.29, 246.81 and 239.29 seconds, four workers). Earlier runs
  predate the cache fix and are not final evidence. Browser acceptance remains open.
  [Registration checks](notes/036-registration-checks.md) now report strict
  declaration problems at startup through the runtime's own resolvers:
  `djust.C022` (invalid project policy), `djust.V016` (unresolvable or
  unsupported annotation, missing annotation, reserved argument name, invalid
  handler policy), `djust.V017` (async strict handler on an actor view) and
  `djust.V018` (`params=` disagreeing with the signature). Contract compilation
  now resolves deferred annotations against the defining class body before
  module globals, names the failing parameter, and rejects keyword parameters
  named `view_id`/`component_id` or `_`-prefixed (D5). V007 skips strict
  handlers; legacy handlers report nothing new. Evidence: 32 cases in
  `test_parameter_contract_checks.py` (14 invalid declarations, each with its
  exact ID and message; valid declarations bind and run through
  `validate_handler_params`; every V016 case raises the same `ContractError` at
  dispatch). 6 of them fail against the previous resolver. The expanded focused
  set passes 596 tests; mypy passes 1,174 files. Full suite from a frozen
  detached worktree at b52a487f1 (four workers, 401.94 s): 33,682 passed, 949
  skipped, 2 failed. Both failures are in `tests/test_changelog_tagged_sections.py`
  and fail alone too: the local `v1.3.0rc1` tag is not an ancestor of this
  branch's base (fdfbc60ef), and the slice changes neither `CHANGELOG.md` nor
  that script. Still open in P1: trusted argument separation (component source
  injection, ADR-034 subscriptions) and client/transport acceptance. The
  legacy-code migration inventory and ADR-037 template-binding checks are also
  not done.
  [Trusted dispatch context](notes/036-trusted-dispatch-context.md) closes the
  server half of D5's argument separation. `FRAMEWORK_ARGUMENT_NAMES` and
  `TRANSPORT_METADATA_KEYS` are applied once in the shared strict validator:
  transport bookkeeping (`_cacheRequestId`, `_activity`) is dropped on every
  path, and an unconsumed `view_id`/`component_id` fails closed. Strict flat
  HTTP bodies reject unknown `_` keys instead of discarding them. Contracts can
  declare trusted parameters, bound only from server values. Staged ADR-034
  output callbacks bind their payload through that contract with the source
  `component` trusted, and V016 checks those payload contracts. Legacy mapping
  is unchanged. Evidence: 68 cases in `test_trusted_dispatch_context.py` (a
  forged/metadata matrix on the shared runtime, both HTTP shapes, exposed
  API, server functions, test client, replay, actor bridge, and real WS
  normal/actor and SSE sessions; legacy controls; forged ADR-034 source).
  25 of them fail against the previous code. The expanded focused set passes
  885 tests; mypy passes 1,175 files. Full suite from a frozen worktree at
  bf17b7d6e (four workers, 347.74 s): 33,750 passed, 949 skipped, and the same
  2 tag-reachability failures in `tests/test_changelog_tagged_sections.py`.
  Still open in P1 at that point: client/transport acceptance (strict native
  binders and browser verification). Also recorded there: the actor path
  fails closed on a root `view_id`, and the legacy descriptor alias takes
  `component_id` from the client.
  **P1 closed (2026-09-24).** The client/transport acceptance it lacked was
  delivered with P2 sub-slices (a)–(d):
  - owner-scoped contract delivery on every transport, including HTTP;
  - strict native collection under owner decisions Q1/Q2;
  - the 43-row eight-path parity matrix;
  - the real-browser matrix (see P2 (d)).

  Every element of this gate now has implementation, named tests and run
  results: signature-derived metadata; the frozen conversion matrix
  (optional, collection and unsupported types); resource limits;
  duplicate/extra/missing values; framework-versus-application arguments;
  registration checks. Remaining ADR-036 work is P2's open producer items,
  P3 acceptance and PR.
- [x] **P2 — wire/dispatch parity.** Route real DOM extraction and every server
  dispatch path through that contract. Verify forms' open payloads,
  keyword-only arguments, forged component injection, `coerce_types=False` and
  unchanged legacy behavior. Invalid input must never invoke application code.
  The [staged client collector](notes/036-strict-client-collection.md) now rejects
  malformed typed literals, normalized collisions, unsafe numeric values and
  reserved routing keys, and returns bounded detached JSON snapshots. Its 57
  parser tests pass; independent review verified the serialization-hook fix.
  It is not yet connected to native binders: scoped public contract delivery,
  wire-hint conflicts, generated-value conventions and real browser/transport
  integration remain open. Legacy bindings are unchanged.
  Three final unchanged-code JavaScript runs each passed 2,093 tests in 190
  files; 91 client-asset tests and zero-warning bundle ESLint also passed.
  Subsequent [owner-addressed mount manifests](notes/036-owner-contract-manifests.md)
  now reach actual WS (including actors) and SSE clients. Public contracts are
  separated by transport, mount path and registered owner address, not merged
  by handler name. Focused server coverage passes 61 cases; the browser-bundle
  mount fixture covers primary/additional mounts, navigation and invalid
  replacement metadata. Initial HTTP delivery, applied-render refresh,
  DOM-generation matching and native binder activation remain open.
  Three final full Python runs each passed 30,565 tests (952 skipped); three
  full JavaScript runs each passed 2,104 tests in 191 files. Asset checks (91),
  mypy (1,034 files) and zero-warning bundle ESLint also passed.
  Shared-runtime render responses now rebuild owner manifests, including
  explicit clears after strict-owner removal and redacted discovery failures.
  Normal WS and SSE endpoint tests assert these snapshots; actors, bespoke WS
  producers, child background frames and HTTP delivery remain open. Receivers
  now install snapshots after successful DOM application and before binding
  reinitialization. Transport-local receipt ordering survives buffered clones
  and prevents older replay from replacing newer root/child snapshots. Invalid
  snapshots fail closed without leaking pending child requests. Owner-generation
  matching, cached DOM updates, complete delivery and native binder activation
  remain open; this is not complete browser/transport acceptance.
  Applied-refresh verification: 27 bundle regressions; three full JavaScript
  runs each passed 2,131 tests (192 files), full Python passed 30,583 (952 skipped),
  and 91 asset checks plus zero-warning bundle ESLint passed.
  URL changes now use the transport render lock and reject owner replacement
  while waiting. Three final full Python runs each passed 30,576 tests with 952
  skipped; independent bounded review and normal pre-commit checks passed.
  Recovery now caches detached public contracts from the matching normal-runtime
  parent frame, serializes recovery under the render lock, rejects replaced
  owners and missing strict snapshots, and pairs fresh sticky-child HTML with
  fresh contracts. Cancellation waits for the render worker without delivering
  its result. Legacy-only recovery keeps its wire shape. Actor/bespoke producers
  remain incomplete; no strict native binder is activated by this change.
  Final recovery verification: 18 dedicated regressions, 76 expanded focused
  cases, full Python 30,601 passed (952 skipped), and mypy 1,037 files clean.
  Both legacy and explicit child background paths now use the shared contract
  helper under their existing event context. Runtime regressions cover callback
  and queue shapes, removed owners, unchanged legacy fields, and redacted
  discovery failures without losing background-batch completion. Actor/bespoke
  producers and HTTP delivery remain open.
  Child-background verification: 46 focused tests, final full Python 30,611
  passed (952 skipped), and mypy 1,037 files clean.
  Actor render results now carry snapshots captured inside Rust and retain full
  recovery HTML. The Python transport serializes actor dispatch, guards owner
  identity and cancellation, and forwards the captured metadata. Strict failure
  clears the unsent VDOM baseline; failed mounts release unregistered views.
  This covers legacy-exposure actors only: explicit actor support, bespoke
  producers, HTTP delivery, owner generations and native binding remain gated.
  Actor verification: 14 dedicated cases; final unchanged-code Python 30,625
  passed (952 skipped); Rust workspace and all 75 live-library tests passed;
  warnings-denied Rust lint and mypy (1,038 files) passed. Native browser
  acceptance is still open.
  The bespoke-producer audit also reproduced dropped full-HTML updates after
  actual Rust baseline loss in ticks, server pushes and database notifications.
  Those paths now deliver a root-content HTML fallback, arm recovery with the
  original raw HTML and advance the consumer-owned wire version. Three native
  Rust regression cases fail before the fix and pass afterward, including
  recovery replay; the expanded focused suite passes 43 tests.
  This fixes delivery, not contract capture: bespoke-producer metadata,
  cancellation and owner-generation acceptance remain open.
  The full Python run passed 30,627 tests (952 skipped) and failed only the new
  changelog entry's formatting check. After correcting that entry and a helper
  return annotation, all 81 final targeted tests passed; mypy passed 1,039 files.
  The full suite was not repeated after those corrections.
  Ticks, pushes, database notifications and both async-result branches now
  capture raw HTML, fallback content and public contracts in one worker under
  the render lock. Strict snapshots require the mounted runtime identity;
  legacy frames remain unchanged and strict removal sends explicit clears.
  Cancellation waits for the render worker and discards its unsent baseline.
  Metadata failures leave the previous recovery pair intact, suppress the
  frame, reset the unsent diff baseline and return redacted errors without
  invoking application result handlers again. Owner replacement is checked
  across lock and render waits. The 48-case matrix and 155-test expanded
  focused group pass; mypy passes 1,041 files. Deferred/debug/hot-reload
  producers and HTTP delivery remain open, along with native activation.
  Two full Python runs passed 30,676 tests (952 skipped). A subsequent review
  corrected unsolicited error labels to `source="async"`, preserving the
  existing foreground-request correlation contract. Final focused tests (155),
  client correlation tests (51) and mypy passed after that correction; the full
  suite was not repeated afterward. The shared helper also consumes the forced
  render flag on success, including async retries after a withheld render.
  Time-travel root/component jumps and forward replay now serialize restoration
  and rendering under the consumer lock, capture render-bound contracts, and
  reject stale owners across waits. Cancellation waits for workers before
  releasing the lock and withholds the DOM/cursor response. Their update/error
  source labels preserve foreground request correlation. The 39-case debug
  matrix, 175-test expanded Python group and four new bundled-client cases pass.
  This does not close replay argument-validation, hot-reload/deferred producers,
  HTTP delivery, native binding or explicit-exposure acceptance.
  Final debug-delivery verification passed 30,715 Python tests (952 skipped),
  2,135 JavaScript tests (192 files), and mypy over 1,043 files. Direct replay
  argument-binding probes still fail: strict numeric conversion is skipped and
  boolean-as-integer input reaches the handler. This is a concrete remaining
  P1/P2 dispatch gap, not acceptance of the complete replay route.
  The subsequent replay adapter resolves server-owned policy and validates strict
  calls before restoring state, using the canonical positional/keyword call plan.
  It preserves raw legacy values and original history parameters, awaits async
  handlers through Django's sync bridge, and refuses invocation after failed
  restoration. Invalid strict calls do not restore state, grow history or fork a
  branch. The synchronous API requires `sync_to_async` when called from async
  Python code for an async handler; the consumer already supplies that boundary.
  Replay-binding verification: 26 dedicated cases, 329 expanded focused cases,
  30,741 full-suite Python tests passed (952 skipped), and mypy 1,044 files clean.
  These close the reproduced replay-binding defect, not the remaining P1–P3
  checks, delivery, native-binding and browser-acceptance gates.
  Sub-slice (a), [HTTP delivery and binder resolution](notes/036-owner-contract-manifests.md#initial-page-and-http-fallback-delivery),
  commit 0fe319bd8:
  - The initial GET renders the root manifest into a JSON data block outside
    dj-root.
  - HTTP-fallback render responses, including `_skip_render`, carry the
    rendered tree's snapshot. An `X-Djust-Parameter-Contracts` request header
    turns omission into an explicit clear for a strict page scope. Discovery
    failure withholds the HTTP DOM update.
  - The client installs the page scope at init and Turbo navigation.
    `_resolveParameterContract` resolves a binding's owner (child, then
    component, then root) and handler against the transport `handleEvent`
    would use.
  - All-legacy pages and responses are unchanged. Native binders do not
    consume contracts yet: (b) waits on the collection-convention decision
    (generated values, `_target`).
  - Evidence: 10 Python and 11 bundle cases. Full Python suite from a frozen
    worktree: 33,760 passed, 949 skipped, and the 2 known tag-reachability
    failures. Full JavaScript suite: 2,275 tests in 204 files passed. Mypy
    passes 1,176 files. Guards: init-order, cross-IIFE, bundle ESLint and the
    doc size claims. The shipped gzip grew by 219 bytes, and the #2632
    call-site pin now counts 2 contract helpers in `11-event-handler.js`.
  Sub-slice (b), [strict native collection](notes/036-strict-client-collection.md#native-binder-activation-p2-sub-slice-b),
  commit 09c5f5ab8:
  - Implements the owner decisions Q1 (contract-aware generated values) and
    Q2 (no `_target` under strict), recorded with N1–N3 in ADR-036's
    completion decisions.
  - Every native binder resolves its owner-scoped contract before any lock,
    confirmation, disable-with, optimistic or loading effect.
  - A strict binding sends only `dj-value-*` plus the declared generated
    values. The client rejects generated-name collisions, conflicting wire
    hints and positional/named duplicates, through the value-free
    `djust:error` path.
  - Legacy and unlisted handlers keep their payloads. An invalid scope fails
    closed.
  - `dj-model`, hook `pushEvent` and `dj-auto-recover` are unchanged, and
    are recorded as open.
  - Evidence: 18 bundle cases in `strict_native_binding.test.js`. The full
    JavaScript suite passes (2,293 tests in 205 files). Full Python from a
    frozen worktree: 33,759 passed and 949 skipped, plus the 2 known
    tag-reachability failures. One more failure, the client-size manifest
    pin on a size figure in the new changelog fragment, was fixed afterwards
    and its module passes (24 tests). The full suite was not repeated for
    that one-line fragment edit.
  - The shipped gzip grew 2,275 bytes. The repository's 33 client-size claim
    lines, including the unminified one, were moved to the measured figures
    in `client-sizes.json`.
  - Compaction, at the owner's request:
    - `_strictEventParams` and the rejection report were merged into a
      single `_strictBinding`.
    - The contract checks moved into the collector's own attribute pass, as
      an `accept` hook, so `dj-value-*` names are parsed once.
    - The wire-hint table was shortened.
    - `dj-paste`'s legacy payload now reuses `_pastePayload`.
    - The collector's reserved names that the identifier rule already
      rejects were dropped.
    - Routing context is attached inside `_strictBinding`.

    Behaviour is unchanged: the 21 strict-binding cases and the full
    JavaScript suite pass. The shipped gzip went from 71,944 to 71,786
    bytes (-158). Of the remaining (b) growth, about 850 bytes is the
    previously dead, already-reviewed collector becoming reachable.
    Compaction cannot return the claims to the earlier figure without
    removing reviewed checks, so they stay at the rounded measured value.
  Sub-slice (c), [transport parity matrix](notes/036-strict-server-integration.md#transport-parity-matrix),
  commit 0afd54252:
  - A 43-row strict matrix runs identically through the shared runtime,
    real WebSocket (normal and actor mode), real SSE, both HTTP-fallback
    shapes, the exposed API and the test client.
  - Rows cover every supported type and its edge cases,
    `coerce_types=False`, positional-only and keyword-only binding, an open
    `**fields` form, extras and a forged source.
  - All paths agree on every row, and invalid rows never invoke the handler.
  - The strict collector now refuses `_`-prefixed `dj-value-*` names; forged
    `dj-value-component-id` and `dj-value-view-id` are covered.
  - Full Python suite from a frozen worktree: 33,767 passed and 949 skipped,
    plus the 2 known tag-reachability failures and one size-manifest pin.
    That pin was on a figure quoted in this ledger's (b) entry; it was
    reworded, and the module passes (24 tests). Full JavaScript suite: 2,296
    tests in 205 files passed.
  - Uploads are not event arguments: a file field sent to a strict `**`
    form handler is rejected in the browser. Async actor handlers remain a
    V017 rejection.
  Sub-slice (d), real-browser acceptance, `tests/playwright/test_strict_parameters.py`:
  - Surface: the demo view `/demos/strict-parameters/`, in Chromium over
    WebSocket, SSE and HTTP-only.
  - Asserts both what the browser sent and what the view received:
    - a typed strict click sends only its `dj-value-*` argument, never
      `data-*`;
    - a malformed literal sends nothing, applies no `dj-disable-with`, and
      raises one value-free `djust:error`, shown by the DEBUG overlay without
      the literal;
    - strict `dj-input` and `dj-submit` send only declared values, with no
      `_target`;
    - the legacy control still receives `dj-value-*` and `data-*`;
    - a hand-crafted forged `component` message never reaches the handler.
  - Result: passes on all three transports, run against the worktree's demo
    server on port 18436.
  - Canary: the same script against the pre-(b) client bundle fails 24
    checks, 8 per transport. The permissive parser sent `7x` as `7` and the
    handler ran; strict `dj-input`/`dj-submit` were refused by the server
    because of `field`/`_target`/extra fields.
  - **Correction (2026-09-25, ADR-034 C2).** The "SSE" and "HTTP-only"
    runs above did not use those transports. The page's own configuration
    script re-assigns `window.DJUST_USE_WEBSOCKET` after the test's init
    script, so all three runs used WebSocket. `tests/playwright/_transports.py`
    now pins the setting and checks which transport the page actually used.
    Re-run with real SSE and HTTP-only transports, the matrix passes on all
    three unchanged. With the old init script, the transport check fails for
    SSE and HTTP.
  - Full Python suite from a frozen worktree at 3724f4857: 33,767 passed and
    949 skipped, plus the 2 known tag-reachability failures and one real
    finding. The actor bridge delivered a `**` payload's keys in a Rust
    HashMap's order, not the payload's.
  - Fixed at the source: actor event params are an ordered `EventParams`
    (`IndexMap`) end to end. The parity matrix asserts exact order again. A
    64-key shuffled regression fails on the previous build and passes on the
    new one. `cargo test -p djust_live --no-default-features` passes 93
    tests, and clippy with `-D warnings` is clean.
    Mount state stays on its `HashMap` (commit 42e545a8d); only event and
    component-event params are ordered. After the compaction and this fix,
    the full Python suite from a frozen worktree at 42e545a8d passed 33,769
    tests, with 949 skipped and only the 2 known tag-reachability failures.
    The Playwright matrix passes again on the compacted client over all
    three transports.
  Owner decision R1, `dj-auto-recover` stays legacy (commit d8cd2b2cb):
  - A handler that a literal `dj-auto-recover` in the view's own template
    targets resolves to the legacy policy in dispatch and in the public
    manifest. The target is read from the server-owned template, so no
    client can claim the downgrade.
  - An explicit strict declaration on such a handler is the warning
    `djust.V019`.
  - Evidence: 7 cases in `test_recovery_handler_policy.py`, 5 of which fail
    without the change. They cover recovery in a strict project, the
    declared-strict warning, other handlers staying strict, the manifest,
    and the legacy project unchanged. Full Python suite from a frozen
    worktree: 33,776 passed and 949 skipped, plus the 2 known tag failures.
  - Follow-up: recovery targets are now also discovered from the HTML each
    render produced for the view instance, which covers `{% include %}`,
    `{% extends %}`, `{% if %}`-toggled forms and dynamic values.
    - Only parsed element attributes count. A test shows escaped client text
      that spells the attribute does not downgrade a strict handler.
    - The class-level template scan remains for renders Python does not see
      (actor renders, and a reconstructed HTTP instance before it renders)
      and for the V019 startup check.
    - Evidence: 5 new cases, 4 of which fail without it (12 in the file).
      Full Python suite from a frozen worktree at 8f614755d: 33,787 passed
      and 949 skipped, plus the 2 known tag failures.
  Producer coverage (the last P2 item):
  - An audit of every DOM-carrying frame type found two producers without a
    contract snapshot: the hot-reload patch and `StreamingMixin.push_state`.
    On a strict page each made the client invalidate its scope, and a reload
    could also advertise stale declarations.
  - Both now capture the snapshot with the render through the shared
    `render_contract_fields`. A strict session whose discovery fails reloads
    the page (hot reload) or withholds the frame (`push_state`); legacy
    sessions keep their frame shape.
  - The remaining producers were already covered, or change no owners.
  - Cached `@cache` hits leave the current snapshot authoritative.
  - Evidence: 6 real-WebSocket cases in `test_producer_parameter_contracts.py`
    (hot reload strict/legacy/failure, `push_state` strict/legacy, component
    creation → replacement → removal with dispatch after each). 3 of them
    fail before the fix. Existing hot-reload, streaming and #1788 suites
    pass (603 tests).
  - Full Python suite from a frozen worktree at 25f264478: 33,782 passed and
    949 skipped, plus the 2 known tag failures. Full JavaScript suite: 2,296
    tests in 205 files passed.

  **P2 closed (2026-09-25).**
  - Real DOM extraction and every server dispatch path go through the
    contract: sub-slices (a)–(d) and the producer coverage above.
  - Verified: forms' open payloads, keyword-only arguments, forged component
    injection, `coerce_types=False`, legacy behaviour unchanged, and invalid
    input never invoking application code. Evidence: the 43-row eight-path
    matrix, the browser matrix, and the forged-key suites.
  - Recorded, not open work:
    - `dj-auto-recover` stays legacy (R1);
    - `dj-model` sends the fixed `field`/`value` its framework handler
      declares;
    - uploads are not event arguments;
    - two root mounts of the same view path on one page are a known
      limitation (N3).
- [x] **P3 — acceptance.** Execute documented examples under their stated policy;
  verify redacted diagnostics and the ADR's complete conversion/parity matrix.
  Closed by the [acceptance review](#adr-036-acceptance-review--p3); ADR-036 is
  Accepted.
- [ ] **PR — retirement.** Delete the superseded coercion path per
  [ADR-036 Step R](036-typed-event-parameter-contracts.md): `coerce_parameter_types`
  (`validation.py:256`), `_coerce_value` (`:338`), `_coerce_single_value` (`:368`),
  with their tests. `validate_handler_params` (`:559`) is rewired, not removed.
  Fires only once strict is the default, which this ADR does not yet approve.
  Grep-verify that no second coercion implementation remains. Open and not
  triggered at acceptance: the targets serve every legacy-policy handler (see
  the Step R status in the ADR).

### ADR-035 — Django-native form and object lifecycle

Source: [decisions and acceptance](035-django-native-form-and-object-lifecycle.md).

- [x] Foundation: public form-construction hooks, empty binding, initial values,
  prefixes and legacy `_create_form` bridge; see `test_form_hooks_adr035.py`.
- [x] **F1 — managed object.** Implement resolve → authorize → bind, managed
  `self.object`, opt-in ModelForm integration and explicit application policy.
  Prove authorization precedes construction/validation and within-dispatch reuse
  avoids duplicate queries without persisting ORM objects or permission caches.
  Done 2026-09-25 under the owner's
  [lifecycle decisions](035-django-native-form-and-object-lifecycle.md#lifecycle-decisions-2026-09-25)
  (Q1–Q6, N1–N6). `djust.forms.ModelFormMixin` lives in `forms.py`. The
  required-object rule and the one-shot mount verdict are in ADR-017's shared
  check (`auth/core.py`). Route binding happens in `RequestMixin.get`/`post` and
  `ViewRuntime.dispatch_mount`. The legacy filters are in the context walk and
  the three legacy session saves. `djust.S013` is in `checks/security.py`.
  Evidence: `test_model_form_lifecycle_adr035.py` (31 tests) covers:
  - lifecycle order (lookup → permission → form) on HTTP GET/POST, the shared
    runtime, real WebSocket in normal and actor mode (the actor turn is asserted),
    real SSE, and a WebSocket restore that skips `mount()`;
  - exactly one object lookup (SQL) per mount and per event;
  - missing, filtered, revoked, forged mount parameter, other view's route and
    unrouted mounts, all denied with the same frame or response and no form;
  - revocation between events: no form, no `form_valid`, no write;
  - `self.object = form.save()`, and retarget / `None` / unbound raising
    `ValueError`;
  - nothing from the object or configuration reaching the legacy session;
  - `context_object_name`, sensitive fields not rendered under either policy;
  - configuration errors, and S013 with its suppressions.
  Limitation recorded in the ADR: `live_patch` within one view keeps the
  mounted target.
- [x] **F2 — form acceptance.** Exercise no-custom-mount edit, create and
  non-model examples; independent/inherited hooks; choices, relations, uploads,
  empty submission, validation and exactly-once save. Cover missing/tampered/
  revoked targets, callback failures, HTTP/WS reconnect/back navigation,
  Django/Rust rendering, typing and browser-visible input/errors/save feedback.
  Done 2026-09-25; acceptance review above
  ([ADR-035 acceptance review — F2](#adr-035-acceptance-review--f2)).
  `test_model_form_acceptance_adr035.py` (30 tests) counts every
  write in SQL and covers:
  - an edit view with no `mount()` saves exactly once;
  - a `form_valid` that doesn't save writes nothing (D5);
  - a `FormMixin` create form inserts one row;
  - a non-model form needs no lookup;
  - a denied edit never creates a row;
  - `get_initial`/`get_prefix`/`get_form_kwargs` work declared on the view and
    inherited by a subclass;
  - M2M (`Group.permissions`) and FK (`Permission.content_type`) initial values
    are primary keys, choices are listed, and both save;
  - an upload reaches the form through a `get_form_kwargs` override and is
    required by the form's validation;
  - an empty submission revalidates the current values;
  - blank and duplicate names show their errors, call `form_invalid`, and write
    nothing;
  - a payload `id`/`pk` does not retarget the save;
  - revoked, deleted and removed-membership targets are denied between events;
  - `get_queryset`/`has_object_permission` raising fail closed on HTTP and on
    WebSocket mount and event, without leaking the message;
  - reconnect keeps typed input (per-event session save), which is validated
    again before saving. A blank draft is refused and nothing is saved;
  - reconnect after deletion is denied;
  - `object`/`form_data` render the same in the Django and Rust engines under
    both policies;
  - mypy types `self.object` as `Optional[Group]` and rejects a wrong model.

  Browser: `tests/playwright/test_model_form.py` drives
  `/demos/model-form/<pk>/` (a demo `Product` editor with no `mount()`) over
  WebSocket, SSE and HTTP-only. It checks:
  - the initial render;
  - a field error with nothing saved;
  - the save message and the saved values after a fresh load;
  - a working form after leaving and using Back;
  - a forged `id`/`pk` event does not retarget the save;
  - filtered-out and forbidden products are the same 403 with no form.

  It passes on all three transports, against the worktree's demo server on
  port 18437. Canary: with the adapter's `instance` binding removed it fails
  18 checks, 6 per transport.

  **Correction (2026-09-25, ADR-034 C2).** As with ADR-036's matrix, the
  "SSE" and "HTTP-only" runs above actually used WebSocket, because the page's
  configuration script overrode the test's transport setting. With the
  transport pinned and checked (`tests/playwright/_transports.py`), SSE passed
  but real HTTP-only failed: the save stored the old values. Two HTTP-fallback
  fixes, recorded under ADR-034 C2, make it pass:
  - a render with no DOM change no longer resets the server's version;
  - HTTP events are now sent one at a time.

  Now it passes on all three real transports, five runs of five. Without event
  ordering it failed 3 runs of 4.
- [ ] **FR — retirement.** (Open; trigger met for adopting views. It is
  scheduled as its own deletion PR on ADR-027's playbook, landing after the
  deprecation window the ADR requires, because the targets still serve legacy
  `FormMixin` views. Nothing is deleted yet.) Delete the pre-hook object
  plumbing per
  [ADR-035 Step R](035-django-native-form-and-object-lifecycle.md):
  the `_model_instance` attribute (`forms.py:228`, used at `:342-344`,
  `:527-531`, plus the adapter's conflict guards at `:996` and `:1105`),
  `_ensure_model_instance()` (`:471-501`, called at `:463`, `:627`, `:686`) and
  the docstring example (`:213-220`), with their tests. `_create_form` (`:541`)
  is a bridge that stays; its removal is a separate later decision and is not
  counted as a saving here. Citations refreshed with F1.

### ADR-034 — component-scoped events and bindings

Source: [decisions and acceptance](034-component-scoped-events-and-bindings.md).

- [x] Foundation: native child lifecycle isolation and component-owned loading.
  This is not the proposed typed subscription API or repeated-request support.
- [x] **C1 — typed binding API.** Implement per-instance binding, declared outputs
  and subscriptions with positive/negative typing fixtures for inheritance,
  renames, misspellings, wrong sources and async callbacks. Route only through
  registered identities; reject direct client invocation of subscriptions and
  unknown targets without a view-handler fallback.
  The [initial isolated proof](notes/034-typing-proof.md) passed both type checkers;
  current fixtures exercise the real staged framework classes instead.
  Production now compiles private subscription declarations at LiveView class
  construction, revalidates inheritance/replacements, rejects duplicate/foreign
  bindings and conflicting transport decorators, and blocks direct callback
  invocation in all shared event-security modes. Actual HTTP fallback rejection
  is tested; see the staged compiler section of the proof document.
  The private concrete dropdown now binds real per-owner instances, emits typed
  outputs with trusted source injection, and passes real HTTP/WS/session reconnect
  tests. Snapshot change detection sees public binding state; unchanged closes
  produce no-op responses. The type fixtures now use these real classes: both
  mypy and Pyright reject all twenty-one negative locations. Mypy follows Django's
  installed source declarations, and the LiveView stub matches its runtime
  constructor, removing the inherited-Any gap without a new dependency or test
  waiver. Both debug scrubbers now restore the
  concrete state schema within the existing lifetime, without callbacks or ID
  changes; malformed component state is rejected before component mutation.
  Fixed bindings now also have a validated manifest in signed navigation
  snapshots, with real WS restore/dispatch and rejection coverage. Interactive
  resumes send current HTML rather than retaining historical controls. Browser
  signed-navigation/debug transport, actor lifecycle, public export and full
  browser acceptance remain open; see the proof document for evidence boundaries.
  **Closed 2026-09-25** under the owner's C1 decisions (recorded in ADR-034):
  - Q1/Q3: `djust.components.interactive` now exports exactly `DropdownMenu`,
    `ActionItem` and `SeparatorItem`. `djust.Q004` warns when one module imports
    both the interactive and the legacy `DropdownMenu`.
  - Q4: the output-authoring API stays private.
  - Q5: actor views stay unsupported, and `djust.V020` (Error) reports one that
    declares an interactive component at `manage.py check`.
  - The typing proof now imports the public module. mypy and Pyright 1.1.408
    reject all 21 negative locations, and the runtime assertions pass.
  - The debug transport is exercised over a real WebSocket.
    `time_travel_jump` restores both menus' pre-selection state and keeps their
    identities, emits no output, and the restored menu still dispatches to its
    own callback. A stale id is refused.
  - #3078 is fixed (`0463cdf20`): the legacy `Meta.event` alias resolves only
    its own component type and is pinned to the legacy policy.

  Evidence: `test_interactive_public_api_c1.py` (12 tests),
  `test_legacy_component_alias_3078.py` (14), plus the existing binding,
  snapshot and observation suites.

  Moved to C4, where the browser matrix lives: the in-browser signed
  back-navigation path (Service Worker capture and `live_redirect_mount`
  restore). The server half (signed manifest, real WebSocket restore) is
  already tested here.
- [x] **C2 — dropdown pilot and observations.** Implement the documented state
  owner, local mechanics and semantic outputs. Verify two same-type menus,
  source injection, valid/forged/disabled selections and callback rendering.
  Optional native-toggle observations must report actual visibility without
  blocking/rolling back UI; unchanged observers do no render/diff/patch.
  The private server contract now validates client-mode observations, source,
  subscription, lifetime and sequence; unchanged HTTP/WS observers return no-op
  while reactive observers render. Cursor state is separate from authoritative
  visibility. The browser listener, local selection dismissal, pending-report
  coalescing and reconnect reporting are staged in the client bundle
  (`696248975`, 8 cases in `tests/js/native-dropdown-observations.test.js`).
  They are not yet verified in a real browser.
  **Closed 2026-09-25.** `tests/playwright/test_interactive_dropdown.py` drives
  `/demos/interactive-dropdown/` (two server-owned menus of the same type, and
  three client-owned menus) in Chromium over WebSocket, SSE and HTTP-only. The
  transport each run really used is checked (`tests/playwright/_transports.py`).
  It verifies:
  - The two server menus open and select independently. Each selection runs
    only its own callback, once, and the callback's change renders.
  - The disabled item is not clickable. Hand-crafted disabled, unknown and
    stale-id selections, and a direct call to the output callback, change
    nothing. Socket runs raise `djust:error`; HTTP answers 4xx.
  - Client menus:
    - A toggle reports the actual visibility after a click, Escape, an outside
      click and a keyboard open.
    - The quiet observer's reports cause zero DOM mutations; the live
      observer's change renders.
    - The unobserved menu sends nothing.
    - Choosing an item dismisses the popover at once.
    - A server patch leaves an open popover open.
  - Over WebSocket, toggles while disconnected stay local, and after the
    reconnect each observed menu reports its current value once.

  Real SSE and HTTP-only runs exposed three transport bugs, all fixed:
  - **SSE mount.** It only stamped `dj-id`s onto the prerendered page, so
    every interactive event targeted a stale component identity ("Component
    not found"). It now morphs the page against the mount HTML through
    `_morphPrerenderedMount`, the helper it shares with the WebSocket mount
    (#1610; #1646 parity).
  - **HTTP zero-patch renders.** A render with no DOM change reset the
    server's diff baseline and restarted its version at 1. The client's
    version check then reloaded the page. A client-owned selection changes no
    markup, so every one hit this. The fallback now answers with an empty
    patch list and the new version, as the socket runtime's no-op does.
  - **HTTP event ordering.** Concurrent HTTP events each restored and saved
    the session, so one's changes were lost. They are now sent one at a time,
    in dispatch order, like frames on a socket. An event sent alone still goes
    out synchronously.

  Evidence and canaries:
  - Removing the SSE fix fails the SSE run. Restoring the zero-patch reset
    fails the HTTP runs of both this matrix and ADR-035's. Removing HTTP
    ordering failed ADR-035's HTTP run 3 times in 4; with it, 5 of 5 passed.
  - New tests: `tests/js/sse-mount-prerender-morph.test.js` (gate-off fails)
    and `test_http_zero_patch_version.py` (gate-off fails).
  - Existing vitest cases updated:
    - HTTP ordering: 1 case in `dj-input-click-widgets`, 1 in
      `http-request-correlation` (two parameterizations), and 2 in
      `native-dropdown-observations`.
    - SSE mount HTML: 1 case in `dj-cloak`.
    - The #1610/#1813 source pins now read the shared helper. One of them had
      matched the sticky-root exclusion only in a comment, and now pins
      `findPageViewContainer()`.
  - Out of scope, not fixed: ADR-038 E5's `tests/playwright/test_exposure_matrix.py`
    uses the same unpinned init script, so its SSE runs were WebSocket runs.
    It was not re-run here. Tracked as #3097.
  - **Owner decision (2026-09-25):** HTTP fallback events stay ordered. They
    are sent one at a time, as frames are on a socket, because of the
    lost-update evidence above. Independent state-backend claims now pass the former HTTP
  exception/retry failure and prevent stale session copies from replaying reports.
  Concurrent memory and actual Redis claims are tested; memory remains
  process-local. Missing/expired cursors fail closed until a fresh binding is
  rendered. Browser recovery for this boundary remains open. This is not C2
  acceptance or an exactly-once application callback guarantee.
- [x] **C3 — collection lifecycle.** Prove keyed repetition, reorder, duplicate
  keys, removal/re-addition, nesting, reconnect and restore. Include a separate
  authorized delegated-row example; do not present it as stateful repetition.
  **Closed 2026-09-25** (gate 5's separate proof), under the owner's C3
  decisions in ADR-034 (Q0–Q6, N1–N4). `DropdownMenu.collection()` is
  implemented in `components/_interactive.py`, with rendering, the explicit
  provider, session persistence and restore wired through
  `components.base.is_session_component`.
  Evidence:
  - `test_interactive_collections_c3.py` (26 tests):
    - sync order, `get`/`len`/iteration and `.values`;
    - invalid pairs, including duplicate keys, change nothing;
    - reordering keeps state and identity;
    - retained configuration updates, and an invalidated selection clears
      without an output;
    - a `visibility` change starts a new lifetime;
    - declarations are copied and reusable;
    - removal and re-addition give a new identity;
    - the member is the callback source, and `toggled` fires only for client
      members;
    - runtime dispatch refuses removed, re-added-old and unknown identities,
      including after the collection's own callback removes its member;
    - HTTP session round trip, and a WebSocket reconnect restore, keep
      identities and state and never resurrect a removed member;
    - an invalid session record changes nothing;
    - no signed snapshot for views with collections;
    - rendering in both engines and both policies;
    - membership and state changes render.
  - `test_adr034_delegated_rows.py` (2 tests) runs the ADR's own D5
    `RowActionsView` through the HTTP fallback: rows carry their own ids
    before and after a reorder, forged ids and actions are refused, and no
    component exists per row.
  - Typing proof: 31 negative locations (10 new for collections) rejected by
    mypy and Pyright 1.1.408; the positive and runtime assertions pass.
  - Browser: `tests/playwright/test_interactive_collection.py` on
    `/demos/interactive-collection/`, over pinned WebSocket, SSE and HTTP-only,
    checks:
    - independent rows, and the keyed callback firing once;
    - reordering keeps state with its row;
    - removal from the member's own callback;
    - the removed row's forged event is refused;
    - restore gives a new identity;
    - the client row's keyed observation.
    It passes on all three. Canary: with removed members kept registered and
    dispatchable, it fails on WebSocket and SSE. HTTP-only restores membership
    from the session on every request, so the leak cannot occur there.
  - Gate-offs in the unit suite: members not unregistered fails 3 tests;
    duplicate keys undetected fails 1; members matched by position fails 7;
    session restore not applied fails 2.
  Not yet published: collection docs and snippets go out with C4.
- [x] **C4 — acceptance and publication.** Run HTTP/WS and real browser tests
  with both template backends: focus/dismissal/default actions, user isolation,
  async/error behavior, duplicate/reordered observations and stale identities.
  Publish runnable examples, generated reference, website navigation and AI
  guidance together through D2 below; preserve legacy plain handlers.
  **Acceptance progress (2026-09-25; publication is still open).** Browser
  runs, each on pinned and checked transports:
  - `tests/playwright/test_interactive_acceptance.py` on
    `/demos/interactive-acceptance/`, over WebSocket, SSE and HTTP-only. It
    checks:
    - an async output callback is awaited;
    - a callback that raises reports `djust:error` on every transport;
    - on a socket the component's own change stays and the callback ran
      once, while a failed HTTP turn saves nothing (below);
    - the `close` action;
    - a duplicate report (same lifetime and sequence) and an old-lifetime
      report never reach the observer;
    - Enter opens a client popover, Escape closes it, and focus returns to
      the trigger;
    - a legacy plain `DropdownMenu` still works through its view handlers;
    - on the WebSocket run, a second browser context is isolated from the
      first.
    Three consecutive passes. One earlier WebSocket run, right after a server
    restart, failed its step 2 once; it did not recur in five later runs.
  - `tests/playwright/test_interactive_navigation.py`: C1's browser half of
    signed navigation, over WebSocket and SSE, in two paths:
    - **Session path.** Back restores the latest state from the server
      session: same identity, still open, selection and view state kept.
    - **Signed path.** The demo drops the page's server-saved state, so the
      restore uses the server-signed snapshot. The same identity comes back,
      no output fires, and the next selection works.
    - Canary: with the signed binding restore disabled, the signed path fails
      (the identity changes on a fresh mount).
    - Finding (pre-existing, not ADR-034): a legacy view's signed snapshot is
      issued with its mount frame only; events do not refresh it (explicit
      views do). So the signed path restores the mount-time state, while the
      session path restores the latest. Tracked as #3098.
    - The Service Worker's snapshot storage is replaced by an in-page store
      with the same bridge methods; capture, signing and restore are real.
  - Fix: the HTTP fallback reported a failed event only to the console. It
    now dispatches `djust:error` with the server's error body, as a socket
    error frame does (#1646). Test: `tests/js/http-fallback-error-event.test.js`
    (3 cases).
  - Transport semantics recorded: a failed HTTP turn answers 500 and saves no
    session state, so the next request sees the state from before it. A
    socket keeps the in-memory changes made before the exception. This is the
    existing "no force-save on a 500" behavior, not new.
  - The C2 and C3 matrices, and ADR-035's and ADR-036's, still pass on all
    three transports after the client change.
  **Closed 2026-09-25** with the documentation (owner decisions C4-Q1–Q4); see
  [ADR-034 acceptance review — C4](#adr-034-acceptance-review--c4).
- [x] **CR — retirement (expected empty).** Closed 2026-09-25, nothing retired.
  C1–C4 revealed no target. The #3078 legacy alias was fixed, not retired
  (C1-Q7). Recorded in ADR-034 Step R. [ADR-034 Step R](034-component-scoped-events-and-bindings.md)
  records that this ADR retires **no** existing code: `event=` is kept per ADR-033 D5,
  the string-routed alternative was rejected rather than shipped, and the existing
  `name=` / `toggle_event=` / item `event` arguments continue. This gate closes by
  confirming that still holds after C1-C4, or by naming a target C1-C4 revealed.
  It must not be closed by inventing one — this ADR is justified on developer-facing
  value, not on code removed.

### ADR-037 — checks and executable documentation

Source: [decisions and acceptance](037-event-contract-checks-and-executable-documentation.md).

- [x] **D1 — shared checks.** Use the same contract as runtime for ownership,
  arguments, injection and exposure checks. Test positive/negative fixtures,
  inherited/decorated handlers, native controls, intentional catch-alls,
  authorized ORM rendering, includes/shared/dynamic templates, locations,
  reasoned suppressions and machine-readable output. No mounts, handlers or
  querysets may execute during checking.

  **Built (2026-09-25).** The owner decisions are recorded in ADR-037: Q1–Q9, the
  djust-docs plan, N1, and the retirement table (rows 1–23 plus V020, Q004 and S013).
  - **One discovery.** `_parameter_metadata.declared_handlers` is the handler
    discovery. Dispatch's `_event_methods`, the V016–V019 checks, `djust_audit`, the
    AI schema, the debug panel, the API registry, hot view replacement, the runtime
    handler metadata, `LiveViewSmokeTest` and the interactive reference generator
    all use it.
  - **The scan.** `_template_bindings` compiles each LiveView and LiveComponent
    template without rendering it, follows `{% extends %}` and constant
    `{% include %}`, and parses the markup as HTML. `DIRECTIVES` there is the one
    Python description of what each `dj-*` directive sends. A test pins it against
    every `dj-*` attribute the client reads.
  - **The checks.** `checks/bindings.py` reports T019–T022, all Warnings. Findings
    carry owner, binding, expected and supplied. The coverage object reports
    checked, dynamic and unsupported bindings, with gaps. Suppression is local and
    needs a reason.
  - **Tests.**
    - `python/djust/tests/test_adr037_binding_checks.py` (23): names and
      ownership, legacy and strict arguments, literals, routing context, dynamic
      and outside-root bindings, includes/parents and shared templates, locations,
      suppression, decorated handlers, managed objects and authorized querysets.
      One fixture raises if anything runs during checking (mount, context,
      property, handler or queryset). Also `djust_check` JSON/text output and the
      client and schema pins.
    - `python/djust/tests/test_adr037_shared_discovery.py` (12): discovery, the
      row 9 oracle across framework and demo views, and the debug panel.
    - `python/djust/tests/test_find_handlers_for_template.py` (5).
    - `python/djust/tests/test_recovery_handler_policy.py` (17).

  **Retirement commits** (the branch's PR is the deletion PR):
  - row 1 (V007) `7cede1b3e`;
  - row 4 `79fbb77e3`;
  - row 5 `7b2fe4fe7`;
  - row 6 `4bd808540`;
  - row 9 `c64fc5867` (pin) and `15cfc2dc2`;
  - row 10 `2d7a75ad0`;
  - row 11 `9035a37f1`;
  - row 12 `de79d8a7d`;
  - row 13 `0b435e373`;
  - row 14 `17659940a`;
  - row 15 `9f67b2fa7`;
  - row 18 `f3768bbc9`;
  - row 20 `86d31d0db`.

  **Findings.**
  - The demo project's 85 T019 findings are all undecorated handlers on 33 views the
    URLconf does not route.
  - Row 20's browser test found two defects the stamp list does not decide:
    - `dj-paste` attached no owner context. Fixed here (owner decision); it is back
      in the directive table as an owner-context binding.
    - Over HTTP-only, every embedded-child event reaches the root view: #3104,
      not fixed here. The browser test holds those cases as strict expected
      failures.
  - `get_debug_info()` crashes on a property that raises something other than
    `AttributeError`: tracked as #3103, not fixed here.
  - Under the legacy policy, `dj-input`, `dj-change` and `dj-submit` send `field`
    and `_target`, so a closed legacy handler for them fails at runtime. T020 now
    reports those bindings; V007 was the blanket guard.
- [x] **D2 — executable documentation and catalogue.** Make examples canonical
  fixtures and deliberately break each test layer to prove its gate fails.
  Verify website navigation and report skipped fixtures. Include the originally
  reported code-snippet whitespace, checkbox appearance, dropdown items and
  missing menu handler, plus multi-menu interactions and visible server errors.
  A read-only audit of all 179 generated usage snippets found placeholder
  handlers referencing an uninitialized component in dropdown, modal and tabs.
  The shared generator now emits view-owned state and working handlers for these
  template tags, preserves slot content and includes the modal opener. Six tests
  execute the displayed Python and assert before/after output under Django and
  Rust rendering. An execution sweep then found four namesake import collisions
  (accordion, collapsible, carousel and sheet); examples now import the exact
  renderer demonstrated by the preview. Of 179 catalogue entries, all 178 view
  examples now mount and render without exceptions; server_event_toast documents
  a mixin rather than a view. Tests no longer silently skip generation failures.
  This is initial execution coverage, not full interaction acceptance or the
  proposed multi-instance API. The final focused catalogue suite passes 240 tests.
  The final full Python suite passes 30,583 tests with 952 skipped.
  The corrected usage sections were checked in an isolated browser catalogue;
  this does not establish publication on the user's running site or D2 closure.
  **Pending from ADR-035 (2026-09-25).** ADR-035 names "generators, and
  lifecycle checks" among the updates to make together under ADR-037. The
  generator side is not built yet:
  - `python/djust/schema.py`'s `"forms"` pattern (about lines 1241–1258)
    still teaches `_model_instance` in `mount()`.
  - Its `OPTIONAL_MIXINS` list has no `ModelFormMixin` entry.
  - The MCP `scaffold_view` tool (`mcp/server.py`, `form` feature) generates
    only a `FormMixin` view, with no edit variant.
  - `djust_gen_live` generates no forms, so nothing to change there.
  Each needs an executed fixture, as D2 requires. They land with the D2
  generator work, not as ADR-035 scope. **Done (D2):** the schema patterns,
  `OPTIONAL_MIXINS` and MCP `form_edit` are executed fixtures.

  **Pending from ADR-034 (2026-09-25):**
  - A catalogue entry for the interactive `DropdownMenu`, including a keyed
    collection (owner decision C4-Q3). The catalogue's usage snippets are
    generated, so the entry comes from the D2 generator, not by hand.
    **Done (D2):** the entry is its canonical example module rather than generated
    usage (see Built below).
  - djust-docs' symbol check reports component-scoped decorators
    (`@<menu>.on.selected`) as advisory "not a known djust decorator". Teach
    it the interactive subscription form. **In progress:** a djust-docs PR, plan
    Task 12.

  **Built (2026-09-25, branch `feat/adr-037-d2-d3`).** Owner decisions: the scope is
  the ADR surface plus drift, the harness uses Markdown markers, and delivery is
  checked on staged 1.3 docs (see the spec,
  `docs/superpowers/specs/2026-09-25-adr-037-d2-d3-design.md`).
  - **Harness.** `python/djust/tests/_doc_examples.py` reads the
    `<!-- djust-example: <id> scenario=<name> -->` or
    `<!-- djust-example: skip -- <reason> -->` marker above each Python block. It
    pairs each block with the next `html` block in its section and runs the named
    scenario from `python/djust/tests/doc_scenarios/` (`adr034`, `adr035`,
    `adr036` and `generated`). `COVERED` lists seven sections.
  - **Drift.** `test_doc_example_drift.py` fails on:
    - an unmarked block;
    - an unknown scenario;
    - a skip without a reason;
    - a marker attached to no block;
    - a duplicate id;
    - a missing heading.
  - **Report.** `scripts/doc-examples-report.py`:

    ```text
    covered file                                       executed  skipped unmarked
    docs/ai/components.md                                     1        0        0
    docs/ai/events.md                                         1        1        0
    docs/ai/forms.md                                          1        0        0
    docs/website/core-concepts/events.md                      2        1        0
    docs/website/guides/error-codes.md                        0        0        0
    docs/website/guides/forms.md                              1        1        0
    docs/website/guides/interactive-components.md             5        0        0
    docs/website: 665 Python blocks, 657 not executed (parse/import-checked only)
    ```
  - **Gate-offs.** Each layer (extractor, pairing, scenario driving and drift)
    goes red when it is reverted: `docs/adr/notes/037-d2-gate-offs.md`, produced by
    `scripts/doc-examples-gateoff.py`.
  - **Generators.** Their output runs through the same scenarios:
    - the `schema.py` forms patterns (a create form, plus the guide's
      `ModelFormMixin` edit view, pinned equal to it);
    - MCP `scaffold_view(features="form_edit")`;
    - the catalogue entry's source.
  - **Catalogue.** `/theme/components/interactive_dropdown_menu/` serves
    `DropdownMenuExample` (one menu, plus a keyed collection) inside the catalogue
    chrome. The browser pass is in `docs/adr/notes/037-d2-catalogue-browser.md`.
    Two menus stay open at once there, as documented.
- [ ] **D3 — final acceptance.** Run the ADR acceptance matrices at the final
  revision, complete migration/AI guidance, and verify actual website delivery
  rather than equating repository Markdown with publication. Record remaining
  static-analysis limits; only then change the relevant ADR status.
- [ ] **DR — retirement decision.** [ADR-037 Step R](037-event-contract-checks-and-executable-documentation.md)
  requires D1 to settle whether this ADR is consolidation or addition: enumerate every
  place that re-derives handler parameters, ownership or event names independently of
  the runtime contract, cited `file:line`, and mark each `RETIRE` (with a deletion PR)
  or `KEEP` (with the reason it is distinct). An empty enumeration is recorded plainly
  and the ADR claims no saving. Every `RETIRE` row carries a merged deletion PR before
  D3 acceptance.

### Completed milestone: E4 — request correlation

Owner: current task implementer. Status: exit checklist verified at cf73aeaf8.
This is a transport correctness slice, not permission to enable ADR-038.

The original `tests/js/request-correlation.test.js` reproducer reported four
failures and two passes: overlapping same-trigger replies cleared loading early
in WS/SSE; SSE supplied neither request refs nor an awaitable server completion.
Those regressions now pass, including the formerly unreachable duplicate-reply
assertions. The expanded suite covers cancellation and response variants.

Deliverable: one shared request register/acknowledge/cancel contract used by WS
and SSE. Inventory existing callers first: embedded responses, patch/HTML/no-op
responses, errors, disconnects, event dispatch, cache/HTTP fallbacks and loading.
Implementation must preserve their invariants rather than add parallel counters.

Exit checklist:

- [x] Distinct request identities and awaitable completion on the actual server
  reply; SSE POST acceptance alone does not complete the event.
- [x] Two requests from the same trigger remain pending after the first reply;
  out-of-order, duplicate and unknown refs cannot clear unrelated work.
- [x] Embedded, patch, HTML and no-op responses resolve only their own request;
  background frames do not acknowledge a foreground request.
- [x] Failure, disconnect, replacement transport and removed/morphed controls
  drain only owned work and settle promises without stranding loading state.
  Preserve documented `async_pending` and legacy no-ref/fallback behavior.
- [x] Focused failing-before/passing-after tests, rebuilt-client regressions,
  full JS suite, affected server wire tests and live backend/browser WS/SSE
  overlapping-request evidence pass; record exact revision and limitations.

Exit evidence:

- Request IDs, out-of-order/duplicate/unknown refs, WS/SSE promise completion,
  all response shapes, removed/morphed controls, modern/legacy async completion,
  transport replacement and buffered-update/error/disconnect ordering:
  tests/js/request-correlation.test.js and tests/js/event_sequencing.test.js.
- HTTP overlap, errors, cache hits, stale page effects and navigation abort:
  tests/js/http-request-correlation.test.js. Existing teardown and SSE suites
  preserve keepalive and tokenless compatibility. Mount-time async frames cannot
  acknowledge an unrelated foreground event.
- Runtime/root/deferred/component/child batches: test_sse_runtime_convergence_1887,
  test_component_scoped_render_2917, test_runtime_child_routing_1892,
  test_async_batch and test_exposure_child_async. Refreshed affected server set:
  455 passed at 8515822e2; no server code changed afterward.
- Final bundled-client run: 2,036 passed across 189 files at cf73aeaf8; lint and
  commit hooks passed. Native browser overlap at 8515822e2 passed over both
  actual WS and SSE connections (distinct refs, loading retained after first
  reply and released after second). Earlier root/component/child batch browser
  evidence is recorded in the corresponding sections. The later legacy fix
  is covered by bundled-client tests, not a claim of an old-server deployment.

Limits: legacy tokenless async work retains its pre-existing event-name
completion semantics, not modern per-task guarantees. Controlled frame/fetch
tests are not live network tests. Cross-worker restore, production explicit
authorization, complete renderer/provider combinations and publication remain
E3/E5/E6 and the dependent ADR gates; none is inferred from E4 completion.

Stop this slice when its exit checklist passes. Next is E1's sink inventory,
then E2/E3 closure and the E5 integration matrix. Implement P1–P3, F1–F2 and
C1–C4 against the guarded exposure foundation; complete D1/D2 alongside their
contracts. E5/E6 final activation and D3 follow the dependent integrations, so
the dependency order does not require a premature exposure release.

## Dependency order

1. ADR-038: typed per-instance state primitives, then separate rendering,
   server persistence, client snapshots and debug projections. Do not expose
   `exposure_policy="explicit"` until every automatic exporter consumes it.
2. ADR-036: shared strict parameter contracts, client collection metadata and
   equivalent validation across all dispatch paths.
3. ADR-035: authorized single-object form lifecycle, consuming the exposure
   contract. Additive public form-construction hooks can land independently.
4. ADR-034: typed component subscriptions, registry-only routing and optional
   client observations; verify two same-type components and stale identities.
5. ADR-037: shared checks and executable documentation throughout these stages;
   finish with catalogue/browser regression coverage and migration guidance.

Each ADR ends in a **retirement gate** (`ER`, `PR`, `FR`, `CR`, `DR`) on
ADR-027's `dormant-define -> wire -> flip -> delete` playbook, whose Step 5
shipped as #2628. The arc's case rests on replacing heuristic machinery, not
sitting beside it, so the deletions are scheduled work with their own PRs rather
than an assumed consequence. Two of the five are expected to retire nothing
(`CR`, and `DR` pending its D1 enumeration); they say so explicitly, because an
invented target would overstate the saving. A retirement gate is closed by a
merged deletion PR or by a written account of why a named target survived.

## Implemented foundation

- Typed `state()` descriptor with instance-owned deep-copied defaults and lazy
  `default_factory`. A consumer mypy regression asserts both accepted reads and
  rejected assignments; nested changes are checked against content fingerprints.
- Public form construction hooks, empty-mapping binding, Django field initial
  values, prefixes and the `_create_form` compatibility bridge. Mount, reset,
  validation, submission and cache reconstruction use the hooks.
- Internal immutable exposure contracts compiled from state descriptors, with
  separate server/client/snapshot/debug projections, bounded JSON primitives,
  detached schema-checked restoration, and explicit inherited exposure grants.
  Defaults and unrelated properties are not evaluated during compilation.
- Internal server-session adapter with sync/async parity, exact supported Django
  storage implementations, required session/user/tenant/route binding, envelope
  expiry, and a total envelope resource budget. Cookie sessions and unreviewed
  custom backends are rejected. The staged HTTP GET/POST path uses this adapter;
  shared-runtime HTTP/WebSocket integration is described below. Sticky-child
  persistence was integrated later (*Parent-driven child persistence*); actors
  are refused for nonlegacy views (*Actor caller inventory*, decision D-o).
- Debug integration now consumes the explicit projection in observability
  assigns, initial/event debug-panel variables and sizes, runtime no-patch
  context diagnostics, and time-travel recording. Time-travel parameters and
  errors are redacted; observational records cannot restore, scrub components,
  or replay handlers. Legacy debug behavior is preserved.
- The staged explicit base-context path now uses deliberate kwargs and the
  component descriptor registry, action state and stream rendering providers;
  it does not discover public/private attributes, configuration, properties or
  state declarations. Native deliberately supplied ORM objects remain renderer
  inputs. Context processors use a copied rendering dictionary, preserve view
  precedence, and reject reserved provider collisions instead of overwriting.
  This branch remains behind the construction guard; all remaining persistence
  paths must stop saving render context before it can be enabled.

These changes **do not enable explicit exposure at runtime**. LiveView rejects
`exposure_policy="explicit"` and nondefault state exposure grants until automatic
exporters enforce the contracts. Legacy exposure and event policies are unchanged.
Managed `self.object`, strict event mode, scoped subscriptions and no-op client
observations remain pending. No generators should use the proposed APIs yet.

### Server persistence adapter boundary

`python/djust/_exposure_sessions.py` reuses Django's opaque session handle and
server storage rather than storing Python state inside the Rust view backend.
Supported concrete stores are Django database, cached database, cache, and file
sessions. The gate checks actual implementation identity, not `SESSION_ENGINE`
text, inheritance, or a self-asserted confidentiality flag. Custom stores require
a reviewed adapter; they currently fail closed. Deployment still determines
whether file/cache storage is shared across workers and durable.

The adapter validates current session identity before and after lazy loading,
requires explicit user/tenant/route identifiers, rejects legacy dictionaries and
changed schemas, and never restores attributes itself. Its digest is not a
signature: storage authenticity comes from the supported server-side session.
Transport integration must derive identifiers from trusted request/routing state,
perform fresh authorization, and remount on rejected restores. Permission checks
and managed-object resolution are **not** implemented by this storage helper.
Envelope expiry is separate from login-session expiry; Django remains responsible
for session cleanup. No client-storage fallback exists on write failure.

The initial codec only accepts bounded, exact JSON primitives. Additional codecs,
framework provider manifests, authenticated client snapshots, migration inventory,
and all runtime exporter integrations remain required before ADR-038 acceptance.

### Debug integration boundary

Explicit debug output reads only client-permitted descriptors; other declared
fields are redacted without evaluating their values or reporting their sizes.
It never falls back to attribute walks, rendering context, or object `repr` on
codec/default-factory failure. Such failures produce an unavailable-state marker
without logging exception text (which itself may contain private values).

`test_exposure_debug.py` checks actual observability JSON, consumer debug/time-
travel payloads, and the runtime diagnostic signal. Its explicit-view fixtures
deliberately bypass construction: **normal explicit mounts are still rejected**.
This does not prove full transport or browser coverage. Handler metadata is
withheld in explicit debug mode pending the typed metadata contract, and
debug restore/replay requires a future separate authorized restoration contract.
Observability reset/eval endpoints now reject explicit or unknown policies with
a static 409 before clearing state, replaying mount, parsing eval parameters or
invoking handlers. These direct mutation endpoints do not supply current runtime
identity, object authorization and event locking; redacting their output would
not authorize their side effects. Explicit mutations must use the live runtime.
Legacy reset/eval behavior is unchanged.

Bug capture rejects historical legacy records when the current view is explicit.
Explicit observational records are projected again through current debug-field
permissions before encoding, preserving historical permitted values rather than
substituting current live state. Undeclared keys are dropped and non-client fields
redacted. Tests assert the decoded shareable capture, not just a helper result.
Caller-supplied patches and custom scrub callbacks remain deliberate application
inputs; this is not protection against application code intentionally exporting
data. Error tooling and other raw exporters still need a complete audit before
activation. Display redaction alone is not acceptance of all debugging surfaces.

### Explicit rendering context boundary

The base context does not automatically expose `state()` fields. Applications
must deliberately add their rendering inputs through `get_context_data()`.
Component rendering uses the existing descriptor registry and rejects stale
entries replaced by properties. Instance-assigned components are not discovered;
their future migration/provider contract still needs completion. Actions and
streams contribute rendering namespaces only. A raw `view` context key is
rejected; no compatibility facade is provided yet. Form/upload providers and
nested provider inheritance remain acceptance work.

`test_exposure_context.py` covers no-reflection sentinels, kwargs/cache isolation,
reserved-name collisions, context-processor precedence, per-view component
binding, stale registries and deliberate native-model rendering in Django and
Rust template backends. These are direct context/renderer tests with deliberately
uninitialized gated views, not full HTTP/WS explicit-policy coverage. The separate
HTTP and shared-runtime integration tests below use real initialized views;
full transport coverage is still required before removing the guard.

### HTTP persistence integration

Explicit HTTP GET/POST saves only descriptor-selected `persist="server"` values
through the server-session adapter, not render context, tracked private attrs,
or legacy component snapshots. Trusted identity derives from the session handle,
middleware user and tenant, and request path. Missing authentication middleware,
missing configured tenant resolution, and unsupported identity types fail closed.
TenantInfo and Django model tenants use stable identifiers, not object strings.

On explicit HTTP POST, request-level authorization and mount hooks run first.
`mount()` reconstructs transient server dependencies on the new HTTP view;
validated stored fields overlay its defaults. Rejected schema/identity/expiry or
legacy dictionaries leave the fresh mount state in place. Existing object-level
authorization runs before handler dispatch/render. This reconstruction lifecycle
is explicit-policy-only; legacy restore still skips mount as before. Managed
object/form lifecycle ordering still requires ADR-035, and no claim is made that
mount side effects are transactional or exactly once.

`test_exposure_http.py` bypasses only the construction guard and exercises actual
GET/POST, rendering, authentication and database session storage. It checks
sentinels at HTML/JSON and persisted destinations, declared-counter restoration,
cross-user/tenant and schema rejection, legacy-state isolation, denied requests,
and cookie-backend rejection. This does not prove complete WS/actor/sticky-child
parity or enable the policy. Server state currently uses the adapter's one-hour
lifetime; configurable schema-version/codec and provider persistence work remains.

### Shared-runtime persistence integration

The staged explicit runtime path reconstructs transient dependencies with mount,
then overlays validated server fields before existing object authorization and
rendering. Event saves use the server projection independently of the legacy
client-snapshot opt-in. Explicit views neither consume nor emit legacy signed
snapshots; the separate explicit client codec is described below.

`test_exposure_runtime.py` exercises real runtime dispatch with a recording
transport and database sessions: three fresh runtimes restore successive values,
invalid schemas/extra keys/legacy dictionaries remount, denied reconnects do not
restore or write, and internal sentinels stay out of emitted frames. This is not
an actual WebSocket connection or complete SSE endpoint test.

The existing event-save timeout and best-effort error handling are retained.
The constructor guard remains in place; this integration does not enable
explicit-mode apps.

### Fresh event authorization

Explicit runtime events now require a fresh trusted transport request and compare
its session/user/tenant/route against an immutable mount binding before dispatch.
View permission checks run regardless of legacy `reauth_on_event` settings;
identity changes, missing requests and provider exceptions discard the view and
close with a static denial, without exception text. TenantMixin resolvers are
rerun, not read from their mount cache. Existing object/handler authorization
still runs downstream. Legacy views retain their optional reauthorization path.

The socket adapter reloads a concrete supported server session and uses Django's
`get_user` rather than the scope's cached principal. SSE consumes the current
owner-checked POST request once, preserving the mounted route for persistence.
The runtime captures each request before waiting on its explicit-event lock;
authorization, handler/render and save are serialized, and temporary save-request
state is cleared afterwards. Saves use the authorized event request and recheck
its identity, not the mount request. Actor events are explicitly refused while
their separate integration remains unfinished.

Tests cover denied/throwing auth hooks, changed user/tenant, missing request,
logout, inactive users, changed passwords, the SSE route/session boundary and
concurrent SSE request isolation. Channels WebSocket connection tests prove
save/reconnect restoration and rejection after session deletion, both without
tenancy and with a configured session tenant resolver and TenantMixin.

### Tenant-bound request and query context

In explicit mode, TenantMixin stamps its resolved tenant onto the request and
scopes HTTP dispatch/GET/POST query context around that tenant. Runtime mount
resolves the explicit view's tenant before permission hooks, matching HTTP's
ordering. Runtime mount restores its caller's tenant context on all exits
(including legacy mounts); this prevents a resolved tenant from escaping the
mount operation. Legacy auth sequencing and HTTP TenantMixin behavior remain
unchanged.

Tests assert matching tenant identities in permission hooks, persistence and
rendering, context restoration after success and missing-required-tenant errors,
rejection of an old runtime after a session tenant switch, and HTTP remount
instead of cross-tenant state hydration. Session-resolver coverage does not prove
all tenant middleware/custom/header/path resolver combinations. Live browser,
complete SSE endpoint, actor/sticky-child/component persistence, explicit client
snapshot and remaining provider tests are still activation gates.

### Explicit signed client snapshots

The staged runtime mount path now emits a separately salted TimestampSigner
envelope containing only fields with `persist="client"` (which also requires
`client=True`). Raw client permission alone does not grant snapshot permission.
The envelope binds session/user/tenant/route identity, schema, destination and
creation time. Its total signed UTF-8 length, including signature overhead, is
bounded; nested values use the shared exact-JSON byte/node/depth constraints.
There is no compression, arbitrary encoder or repr fallback.

Restore verifies the signature, lifetime (including future timestamps), complete
field set, schema and identity before returning detached values. Invalid input
leaves fresh mount defaults intact. Client fields and independently validated
server fields overlay the reconstructed view before existing object authorization.
The global snapshot switch and application restore veto apply. An explicit
restore sends fresh HTML rather than assuming cached markup matches the combined
state. Codec failures invalidate the cached snapshot with an explicit null,
without exception values or a legacy fallback. Signing does not conceal permitted
client values.

Tests cover codec rejection cases and runtime mount/restore frames, including
master switch, veto, codec failure, and exclusion of server/render-only values.
Successful authorized top-level events refresh this token on patch, HTML and
no-render acknowledgement frames. Identity is rechecked before capture; failures,
missing client declarations or the disabled master switch emit null rather than
leave an older token cached. Denied/failed events do not publish refreshed state.
Explicit no-render acknowledgements precede queued navigation, so its capture
sees the updated token; legacy side-effect ordering is unchanged.

WebSocket and SSE clients share opaque-token storage, scoped to the primary view
for event responses. Null clears the old entry; unrelated, background and error
frames cannot replace it. Bundle tests cover byte-for-byte navigation capture;
runtime tests cover event-to-remount restoration and acknowledgement ordering.

The navigation cache now evicts the source URL when no current token exists,
rather than retaining an older service-worker entry. State writes, per-URL
eviction, lookup and whole-state-cache clearing run in receipt order, with
service-worker lifetime extension. Redirect capture records the source before
history changes; Back navigation captures/invalidates the page being left before
destination lookup. Regression tests include a deliberately delayed cache write.
A real-browser harness verifies the bundled WebSocket/SSE clients through actual
service-worker messages and CacheStorage (eight checks); its transport frames are
synthetic, not proof of Django authorization or complete page navigation.

SSE endpoint navigation now resolves the destination through URLconf, reconstructs
a page request from the current authenticated POST, and mounts through the shared
runtime. POST turns and render/result application are serialized. A discarded
runtime is detached before replacement, preventing its background results from
being applied to the next page. Lazy Django authentication is resolved off the
event loop on stream creation and both POST endpoints.

The client retains its snapshot-routing identity after EventSource open, accepts
navigation links without requiring a WebSocket, and replaces old HTML on navigation
even when incoming markup already has `dj-id` values. Failed replacements fall
back to HTTP. After a stream disconnect, route-changing navigation reconnects to
the current page, not the original EventSource URL.

A real Django/ASGI browser fixture verified an event changing `initial` to
`latest`, navigation to a second view, Back restoring `latest`, and Forward
restoring the second page. Server frames and request logs prove these used the
same SSE session without a destination-page HTTP load. A separate real stream
shutdown verified reconnect creates a stream for the current second-page route.
The fixture bypasses the explicit-policy constructor guard only in its own process.
An earlier empty `dj-navigate` fixture performed a full load and was rejected as
evidence; the corrected fixture uses explicit destination attributes.

Complete snapshot restoration across reconnect, background state changes,
remaining direct state APIs and provider/child contracts remain activation gates.
SSE happy-path browser evidence does not establish WebSocket, cross-worker,
sticky-child, or all failure-path browser parity. Explicit policy is still
constructor-gated and ADRs 034–038 remain Proposed.

## Direct state API boundaries

The staged explicit policy now routes direct state reads through the same
declaration compiler and bounded JSON projection as the transport adapters:

| API | Explicit-policy behavior |
| --- | --- |
| `get_state()` | Detached values for `client=True` declarations only |
| `_capture_snapshot_state(strict=...)` | Detached values for `persist="client"` declarations only; strict validation regardless of the legacy `strict` flag |
| `_get_private_state()` | Rejects inferred private persistence; use the bound server adapter |
| `_capture_components_snapshot()` | Rejects reflective component export; a descriptor is not a state-exposure grant |
| `_restore_snapshot()` / `_restore_private_state()` | Rejects raw dictionaries before inspection or assignment; use validated bound adapters |

Unknown or unreadable policies cannot select legacy reflection. Invalid values,
resource limits, inherited grants and factory errors fail closed without leaking
exception values or retrying with context, `__dict__`, or a fallback encoder.
Policy lookup distinguishes a genuinely absent declaration from a descriptor
raising `AttributeError`; the latter cannot select the legacy default, including
in debug projections.
Declaring a field for rendering does not make it raw client state or a snapshot.

The legacy sticky adapter also rejects explicit children before reading context
or writing a session, and rejects explicit/mixed-policy restoration before any
public assignments. This is a **temporary unsupported boundary**, not completion
of sticky-child support. Its dedicated bound provider adapter, mixed-policy
semantics and restoration tests remain required before explicit mode can ship.
The production constructor guard is unchanged. New direct-API tests use
uninitialized synthetic views only; existing transport and sticky regression
tests cover legacy compatibility.

The audio mixin's private-restore override rejects nonlegacy policy before
filtering the payload; calling the guarded base method afterwards is too late
because Python evaluates the filtered argument first. A hostile-payload
regression failed before this entry guard and passes with it.

The setattr structural net now matches the exact developer-returned dictionary
application in its lexical scope instead of pinning source line numbers.
Canaries permit blank-line shifts but reject a client-payload substitution,
an extra adjacent assignment and the same block in a different scope.

## Bound child storage foundation

`python/djust/_exposure_children.py` now supplies an internal child-slot adapter
over the existing server-session envelope. It binds the current session, user,
tenant and route, every parent contract/schema in the slot ancestry, and bounded
JSON mount inputs. The child contract supplies its own class/schema binding and
selects only `persist="server"` fields. Same-type siblings and nested slots have
distinct storage keys. Parent/class/schema/input changes encounter and reject
the previous envelope in the same logical slot, avoiding one orphan per version
or object. Only the mount-input digest is retained; arbitrary objects and encoder
fallbacks are rejected.

This adapter returns validated values, not hydrated views, and does not grant
authorization. Its inputs must come from current server rendering/registration,
not client event parameters. It reuses session rotation, expiry, exact backend
allowlists, bounded envelopes and synchronous/asynchronous persistence from the
parent adapter. It neither reads rendering context nor creates client snapshots.

**Integration is partial and still gated.** The legacy sticky guards remain in
place; fresh eager explicit mounts use the separate adapter described below.
The lifecycle work includes:

1. Derive ancestry, stable slots and mount inputs from the trusted render/child
   registry. Fresh eager nested ownership is implemented; repeat-instance and
   changed-identity reuse remain to be completed.
2. Run fresh request authorization and `mount()` to reconstruct dependencies,
   overlay the validated server projection, then enforce current object
   permission before rendering or dispatch. Legacy skip-mount behavior must not
   be reused for explicit children. This sequence is implemented for fresh eager
   sticky children; event and preserved-instance integration remains pending.
3. Recheck preserved/live-instance identity before reuse. Current-request view
   and object authorization on both render reuse paths is now implemented as
   described below; this is not yet complete identity/lifecycle integration.
4. Connect HTTP and shared-runtime save/restore, with fresh binding and event
   serialization; explicit server grants must not depend on the legacy
   browser-snapshot opt-in.
5. Prune removed provider slots, define mixed-policy subtree behavior and prove
   failure/remount behavior without partially applying state.

`test_exposure_children.py` exercises real server sessions, copied-envelope
attacks (not only missing keys), independent sibling/nested state, ancestor and
mount-input changes, schema changes, parent-envelope substitution, rotation,
expiry, invalid payloads, readable-backend refusal and storage failures.
These adapter tests do not prove actual sticky lifecycle or browser support.

### Existing-child authorization prerequisite

The real `live_render` registered-instance and preserved-child branches used to
return before the fresh-child authorization checks. Both now update the child's
request, check current view authorization and enforce current object permission
before rendering or registering a preserved child for client reattachment.
Unexpected predicate failures deny reuse with a static error. Allowed reuse keeps
the same mounted instance and re-fetches its authorized object.

`test_exposure_child_reuse.py` exercises actual template rendering, registry
lookup/registration and authorization. Its ten initial legacy tests failed before
the fix. The expanded suite covers legacy and construction-gate-bypassed explicit
fixtures, logout, revoked view/object permissions, broken predicates, current
request use and allowed preservation without re-mounting. These are server-render
tests, not browser reattachment or per-event transport proof.

This prerequisite is a correction to existing legacy behavior as well as staged
explicit behavior. It does not wire `ChildStateSession` into the child mount/save
paths, resolve changed slot/class/mount identity, or remove the explicit gate.

### Fresh eager child lifecycle

The staged explicit `live_render sticky=True` fresh-child path now compiles its
scope from actual registered ancestry and detached server-render mount inputs.
An invalid/cyclic/unregistered ancestry cannot claim another child's scope.
Server-persisted children currently require all-explicit ancestry; transient
children do not require a server session or upgrade their legacy ancestors.

After current view authorization, the path prepares the adapter, calls `mount()`
to reconstruct transient dependencies, and rechecks schema and ownership against
the pre-mount binding before applying any saved values. Rejected envelope schema,
identity or expiry leaves fresh defaults intact. Object authorization runs on the
restored state before registration or initial persistence. The initial save uses
only declared server fields, independently of legacy browser-snapshot opt-in.
The legacy skip-mount restore path is unchanged.

Explicit sticky rendering no longer inserts a raw `view` object into context.
Nested rendering obtains its parent through the framework's scoped active-parent
context. Explicit context errors fail with a static error rather than logging
private exception values and rendering an empty fallback context.

`test_exposure_child_mount.py` covers actual Django template rendering, a real
HTTP GET through the normal parent view path, real database sessions, nested
registry ownership, initial saves, mount-before-restore, declaration replacement
during mount, view denial before mount and object denial after restore. The raw
view sentinel, declaration-replacement and context-failure regressions each
failed before their fix; disabling the load changes the actual rendered count.
The first test setup initially lacked configured tenant middleware state; that
fixture error is not evidence of a product failure.

The Decimal restore-site inventory recognizes only the exact explicit child
assignment block as a JSON-primitives codec boundary; it does not exempt the
whole module. A canary detects extra assignments, and an actual child restore
proves legacy Decimal-tag-shaped dictionaries stay ordinary JSON. Applying the
legacy decoder on this path would silently change those values.

The fresh-mount slice does **not** complete child-event saving, changed-identity reuse, lazy child
mounts, non-sticky provider scopes, pruning, mixed-policy persistence, cross-worker
restoration or browser reconnect coverage. Those remain activation gates, and
the explicit constructor guard is still unchanged.

### Shared-runtime child events

The staged explicit routed-child event path now uses the root runtime's freshly
authorized event request and existing explicit event lock. Before invoking a
handler it verifies live registry ancestry, the mount-time declaration schema and
bound child identity, refreshes the child's request, and checks current child view
permissions. Existing handler/object authorization then runs against that request.

After the handler it revalidates root/child binding, declarations, view and object
permissions, and saves only the routed child's declared server fields through
the bound adapter. This is independent of legacy browser-snapshot opt-in. The
storage await retains the 150ms deadline; timeout cancellation or persistence
failure sends a static error and no successful embedded update. Handler and
explicit render errors likewise do not echo or log their exception values.
Embedded rendering uses the active-child context and rejects raw-view context.

These are not transaction/rollback guarantees: handler side effects have already
occurred when a post-handler check or save fails. A render failure can occur after
the state was saved. The response asks for recovery rather than reporting a
successful update; no automatic handler retry is introduced.

`test_exposure_child_events.py` exercises real shared-runtime mount/event
dispatch, database sessions, emitted frames and restoration in a fresh runtime.
Handler-entry counters make stale-scope and revoked-permission tests fail if a
handler is entered, even if it later raises. The initial five checks failed
before wiring, and the render-error sentinel failed before the explicit error
boundary was added. Tests also cover post-handler scope/view/object denial,
storage failure, error redaction and bounded timeout cancellation.

The timeout inventory pins the exact three runtime save methods, not just a
substring count. The explicit child storage cancellation test exercises a
stalled backend; legacy best-effort timeout behavior remains separately tested.

This is not complete child persistence: parent-driven mutations/HTTP sweeps,
lazy/non-sticky children, pruning, mixed-policy scopes,
real-browser reconnect and cross-worker evidence remain required. The production
construction gate remains closed.

### Explicit child reuse identity

Both eager registered reuse and navigation-preserved reattachment now compare
a private mount-time identity before retaining an explicit child. It includes
the exact Python child/ancestor classes, declaration schemas, registered ancestor
slots and their recorded inputs, the child's detached mount kwargs, and current
user, tenant, route path and session identity (including backend type). A digest is only an equality token,
not authorization: fresh view/object permission checks still run, followed by a
second identity check before reuse. Fresh mounts also recheck after mount and
object authorization. Routed child events use this identity even when no fields
have server-persistence grants.

Transient reuse does not create, read or save session data. Without an established
session key, state can be reused within its current root instance, not transferred
to another root. Mixed-policy transient ancestry is likewise root-instance-bound
because legacy mount inputs do not carry an explicit provider contract. Cookie
session identifiers can identify transient reuse; they do not grant server
persistence. Only the identifier's hash enters the private identity metadata.

A valid but changed identity remounts in the same slot. The replaced instance is
removed from its old registry and the navigation preservation map before its
sticky unmount hook runs. The ordinary registered replacement also invokes its
unregister cleanup hook. Hook failure cannot resurrect the child and is logged
without private exception text. Invalid identity inputs fail closed instead of
falling back to legacy reflection. Existing legacy reuse is unchanged.

The eager tag now rejects combined sticky/lazy options before reuse, closing a
preservation shortcut around the existing incompatibility rule. Tests exercise
native Django rendering, real database session identifiers, matching and changed
identities, scope compilation for nested ancestry, sessionless reuse, cleanup
failures, authorization-induced identity changes and the real post-render
preservation scan. That reuse slice did not establish browser reattachment, recursive subtree
teardown, repeated-instance routing, provider pruning, or cross-worker parity.
Those remain required before activation; the production constructor guard stays
closed.

The post-render scan only accepts explicit survivors already validated and
registered by the tag. Bare `dj-sticky-slot` markup cannot substitute for the
declared class/inputs check; a regression reproduced that bypass before the scan
was restricted. Legacy bare-slot preservation keeps its existing behavior.

### Owned subtree disposal and async cancellation

The gated explicit lifecycle now detaches registered descendants before running
descendant-first cleanup. It removes forward/reverse ownership references, drops
queued/deferred work, cancels waiters on their owning event loop, and runs upload,
unregister and sticky-unmount hooks as appropriate. An idempotent disposal marker
prevents reentrant hooks, repeated teardown and re-registration of disposed
instances. A malformed alias to a child owned by another parent is unlinked, not
followed into that other subtree. Cycles are traversed iteratively.

This path is wired into identity replacement, unregister, denied preservation,
post-render discard, WebSocket disconnect/redirect disposal and SSE
replacement/shutdown for explicit roots. Legacy lifecycle routing remains in
place; its sticky-unmount hook now calls the implemented cancellation method.
Unregistering an explicit child under a legacy root still uses explicit disposal.
Cleanup is best effort: a broken application hook cannot skip siblings or
descendants, and the helper logs static errors rather than private exception
values. This is not session-envelope pruning or an application rollback.

Inspection corrected an earlier overstatement: the previous sticky-unmount
hook looked for `cancel_async_all()`, but that method did not exist. Invoking the
hook alone did not prove cancellation. The method now exists, and both shared
runtime and WebSocket background dispatch track per-view task handles and release
them on completion. Cancellation clears queued work, requests cancellation on the
owning loop, and increments a generation checked by their shared callback runner.
Even a coroutine that swallows cancellation cannot deliver a stale result/error.
Running synchronous code cannot be preempted; its application side effects may
continue even though its completion handler/render is suppressed. Independently
created application tasks remain application-owned.

Tests exercise actual task dispatch/cancellation in both runners, worker-thread
teardown of loop-owned waiters, native tag replacement, WebSocket disconnect,
post-render discard, and real SSE message navigation/shutdown. They do not prove
browser or cross-worker behavior. Parent-driven persistence, removed-slot/session
pruning and scoped child background dispatch remain open: the routed-child event
path currently calls the root async dispatcher, not a child-specific dispatcher.
The remaining providers and ADR034–037 work are still part of the objective.
Explicit exposure remains unavailable to applications.

### Parent-driven child persistence

The staged explicit path now saves registered descendants after a successful
parent-event render, before its success frame, and after HTTP POST rendering.
An explicit parent's unchanged assign snapshot no longer implies its children
are unchanged: parent handlers can mutate a child directly. Deliberate
`_skip_render` still skips rendering, but saves the authorized child tree before
acknowledging the event. The existing production construction guard is unchanged.

The shared helper walks only the current owned registry, never arbitrary public
attributes. It checks exact mount identity, schema, request binding, ownership,
disposal status and current view/object authorization, including nested children.
Only declared server fields enter the batch. All captures must validate before
any session entry changes. The batch has a maximum of 256 descendants and one
aggregate root-contract JSON budget, in addition to each child's contract and
the existing ancestry limit. After projection and application authorization hooks,
the helper rechecks the captured scopes and registry membership before writing.
One backend flush stores the child batch; root persistence remains separate.

The runtime save retains the 150ms deadline. Failure emits a static error instead
of an update/noop acknowledgement. Because rendering may already have advanced
the server VDOM, a subsequent successful event is forced to send full HTML.
HTTP storage exceptions are also sanitized, including chained exception text;
the new failure regression first reproduced a backend sentinel in both DEBUG
JSON and logs. Failed or cancelled flushes restore the local session entries
and modified flag. This is **not** a transaction across the root, children and
application side effects. A backend may have committed before an error/timeout
was observed; restoring the in-memory session cannot undo that uncertain commit.
Synchronous application hooks cannot be preempted by asyncio cancellation.

Tests exercise real shared-runtime parent events, explicit no-render events,
actual HTTP POST plus fresh GET restoration, direct and nested scopes, a single
batch flush, no partial writes on authorization/identity/budget failures, total
budget enforcement, hook-driven registry changes, storage errors/timeouts,
private error redaction and full-HTML recovery after a withheld update.

Verification: the focused persistence/HTTP/timeout set passed 68 tests; the full
Python suite passed 30,004 tests with 952 skipped; mypy passed across 1,014 source
files. These are server/protocol tests, not browser or cross-worker evidence.

This is a **registry sweep, not rendered-slot reconciliation**. A child omitted
from a new template may still be registered; removing its runtime ownership and
stored envelope requires a successful-render inventory and scoped pruning index.
GET/reconnect-wide sweeps, scoped child background dispatch, lazy/nonsticky and
mixed-policy providers, repeated/nested event routing, browser/cross-worker
verification and the remaining ADR034–037 work still block activation.

### Render-aware eager child pruning

The staged eager-sticky provider now reconciles runtime ownership from completed
server renders. Only children invoked through the server component renderer are
candidates; markup alone cannot create or retain a registered child. The final
HTML decides which of those candidates survived rendering and page-shell/root
composition. Unregistered wrappers cannot hide a real candidate, and inert
`textarea`/`template` output does not keep a component alive.

Nested plans commit after the outer wrapped render succeeds. HTTP and shared
runtime calls also wrap subclass render overrides, so raising after a call to
the base renderer does not prematurely commit its removal plan. Full-page
renders and live-root renders have different authority: root updates preserve
known shell children, while a completed full render can remove them. The named
template inheritance fallback is explicitly a root fragment, not proof of a
completed shell. Runtime unregister/disposal clears region references as well
as the child registry.

Server child writes now maintain a bounded, versioned slot-path index. It stores
paths and shell membership, not arbitrary deletion keys or application values.
Pruning derives keys again from the current authorized request route. A malformed
index fails without deleting entries; a copied/forged slot path cannot select
another route's child key or a Django authentication key. The index has at most
256 paths, at most 16 ancestry levels, and the normal bounded JSON validation.
Single-child initial saves record their path, so a later completed render can
clean up state from a failed initial render. These initial writes are not made
transactional with the whole parent render.

Parent events, shared-runtime mounts and HTTP GET/POST sweep the reconciled
registry. Routed child events use the same post-render batch rather than a
separate pre-render child flush, so removed descendants are pruned before an
`embedded_update` success frame. The existing 150ms runtime storage deadline is
owned by the shared helper. Failed/cancelled flushes restore local staged keys
and the modified flag, not an uncertain remote commit or application effects.

A fresh root-only instance preserves previously indexed shell state it has not
rendered; this is storage preservation, **not** evidence of shell-instance
reconstruction or shell-event routing on reconnect. Full-render provenance lets
an owner that actually knew a shell remove it via explicit unregister. Unindexed
envelopes from older gated prototypes cannot be reconstructed by guessing hash
keys; migration/expiry handling remains an activation concern.

The focused tests include real Rust full-page/root renders, Django child renders,
parent and routed-child events, nested same-name slots, failed render overrides,
fragment fallbacks, shell preservation/removal, forged markup, inert output,
index corruption, route confinement and failed-prune local rollback. Verification:
488 focused tests passed; the full Python suite across all three roots passed
30,024 tests with 952 skips, and mypy passed for 1,017 source files. The first
full run exposed an outdated mount-order source pin; it now checks the
reconciliation wrapper and its delegation, while retaining the real second-mount
backend-clone regression. All 10 restore-contract tests also passed separately.
Source/security review found no further issue in this slice. This does not
enable explicit exposure, finish lazy/nonsticky or mixed-policy providers,
prove browser/cross-worker behavior, or complete ADR034–037.

### Scoped child background work: runtime boundary

The staged shared-runtime child-event path now drains the selected explicit
child's named/legacy task queue, not the root queue. Tasks are tracked on the
child, and callbacks use the existing shared sync/async callback runner.
Completion renders and persists the child before emitting an `embedded_update`
frame tagged `source="async"`. This does not temporarily replace the runtime's
root to make root-only methods operate on a child.

Before execution and completion, the worker reloads the supported server session
and Django authentication from the trusted mount identity. It does not reuse the
original POST as current authority or consume SSE's next POST request slot.
Root and ancestry authorization, object permissions, current registration and
generation are checked under the runtime/transport locks. Authorization hooks
can replace an owner themselves; a regression demonstrated stale result-handler
delivery until the post-hook identity check was added. Disposal, generation
change and root replacement suppress stale completion. Running synchronous
application side effects remain non-preemptible.

Callback failures may reach the owning application's `handle_async_result`, but
the framework emits only a static failure if unhandled or if a provider fails.
It does not log/export the callback exception. Focused tests cover sync, async
and coroutine-returning callbacks, both queues, independent root work, scoped
storage, cancellation, permission/session revocation, stale ownership and error
recovery/redaction. The exposure/async regression group passed 551 tests;
the full Python suite passed 30,037 tests with 952 skips. Mypy passed for
1,019 files. Source/security review also checked callback error handling,
post-hook ownership and the session-reload boundary.

The runtime review exposed a client gap addressed in the next section: SSE had
no `embedded_update` case, and WebSocket child completion consumed unrelated
last-event state. Full backend/browser verification, scoped loading, child work
queued from mount or parent handlers, and descendant/repeated-instance routing
remain required work. Legacy child dispatch and root background persistence
also remain separate paths requiring audit. Explicit exposure remains disabled.

### Scoped child client responses

WebSocket and SSE now use the same child-response handler and DOM morph core.
The existing WebSocket `handleEmbeddedUpdate` method delegates to that core.
The child wrapper stays in place, only its contents morph, and inserted-script
warnings and event rebinding still run. Missing targets or non-string HTML do
not consume another event's acknowledgement.

Background frames (`source="async"`) do not clear current event/loading state.
Normal referenced replies resolve the matching WebSocket event promise and
drain buffered patches when its pending-reference set becomes empty. They do
not clear a different newer event/trigger's pointer. A no-ref reply must match
the pending trigger's child scope and, when supplied, its event name. That scope
is captured before the morph, so a successful reply can remove its trigger
without losing the acknowledgement association.

The real built-client regression tests reproduced both missing SSE updates and
unrelated WebSocket loading teardown. A real-browser smoke fixture replayed
scoped frames through both transport handlers: the selected child showed its
completion while the other child's button remained disabled/pending. This is
client-frame replay, not a live backend-session/browser or cross-worker test.
All 1,965 JavaScript tests across 186 files passed, including the 13 focused
client regressions; ESLint and source/security review passed. Generated bundles
were rebuilt from source, not hand-edited.

That response fix did not itself change the globally keyed loading manager;
the next section describes component ownership there. Concurrent no-ref SSE
acknowledgements and multi-task background pending indicators still require
further work. Neither change activates explicit exposure.

### Component-owned loading state

Loading records now combine event name, nearest native embedded-view/component
wrapper, and triggering element. Existing `pendingEvents` remains an aggregate
set of names; it is not the ownership key. A `save` in one component therefore
does not disable another component's controls merely because it also handles
`save`. Stopping one scope preserves another scope's indicators and the page's
global loading class while work remains.

Ownership uses the wrapper DOM instance, not an ID string. A replacement wrapper
with the same ID does not inherit removed work. Nested components use their
nearest owner. Scanning removes disconnected scopes and reapplies pending state
to newly morphed controls in an existing scope. A reply can remove its trigger:
completion finds the original pending record instead of using detached DOM
ancestry. A no-trigger legacy page completion clears page-scoped work only.

All seven initial scope regressions failed against the old manager. The focused
group now passes 32 tests, including referenced same-handler replies through the
actual WebSocket client. A browser frame-replay fixture verified both buttons
disabled, then only the left enabled after its reply, then both enabled with no
pending work. This is not a live backend-session/browser test. The full
JavaScript suite passed 1,975 tests across 187 files; ESLint and source review
passed. Bundles were regenerated from source.

Records coalesce repeated dispatches from the same element within one scope;
they are not a per-request counter. Overlapping same-trigger requests, concurrent
no-ref SSE replies, error/disconnect draining and multi-task background loading
still need correlated lifecycle coverage before the broader ADR is accepted.

### Foreground request correlation: WS and SSE

Both transports now register requests in the same reference sequence and record
the owning transport. Ordinary SSE sends return a server-response promise;
accepting the HTTP POST is not completion. Teardown keepalive sends retain the
existing fire-and-forget contract because the outgoing page cannot await its
stream. Unknown/duplicate references never fall back to last-event pointers.
No-ref compatibility can acknowledge one outstanding request, but does not guess
between several. A background frame cannot acknowledge a foreground request.

Loading consults outstanding requests before clearing a coalesced trigger or
page scope. Replies can arrive out of order or after removal of the child owner.
Targeted errors and failed SSE POSTs cancel only their reference; disconnect
settles that transport's promises without consuming a replacement transport's
requests. Cached/HTTP behavior and teardown tests remain in the full suite.

Verification: the original six-test reproducer failed before the change; the
expanded request tests and full JavaScript suite pass (2,001 tests, 188 files).
Selected actual SSE endpoint/runtime and child-async server tests pass (55 tests).
ESLint and whitespace checks pass. Generated clients were rebuilt from source.
Live browser checks against an ephemeral Django backend observed a patch then
no-op with loading true then false for WS; SSE returned ref 2 before ref 1 and
still preserved loading until both completed. The temporary server reused the
staged exposure fixture with its constructor bypass; this is not production
activation, cross-worker coverage, or a complete provider/browser matrix.

E4 remains open: background-task completion identity/multiplicity, ambiguous
legacy no-ref overlap, and the remaining lifecycle matrix are not proven by
foreground acknowledgements. In particular, acknowledging `async_pending`
still separates the foreground promise from loading retained for later work;
it does not establish per-task completion tracking. E1–E3 and ADR034–037 remain
open, and explicit exposure is still unavailable to applications.

### Owned child background batches

The staged explicit-child path captures its queued work before sending the
foreground acknowledgement, then dispatches that captured batch after the send.
`AsyncBatch` in `python/djust/_async_batch.py` assigns an opaque token, advertises
`async_pending: true` plus `async_batch` on the acknowledgement, and emits
`{"type": "async_complete", "async_batch": token}` once every task settles.
Task done callbacks cover cancellation before coroutine entry; an owner removed
before dispatch discards its captured callbacks and releases its token. The
completion contains no results, callback names, rendered state or authority.

The client associates the token with the acknowledged request's transport and
trigger. Foreground promises resolve normally, while batch records keep loading
active independently. Duplicate/unknown completions and another transport's
completion cannot release owned work. Background errors carry `source="async"`
and the token; they do not cancel newer foreground events. The final completion
still waits for all tasks, including handled failures. Disconnect clears owned
batch records as well as foreground requests.

The real browser fixture uncovered a separate load-bearing ownership bug:
`live_render` stamps routing hints on individual controls, not just wrappers.
Treating the nearest `data-djust-embedded` hint as an owner selected the button
itself; morphing removed that hint and stopped loading reapplication. The manager
now prefers the actual `[dj-view][data-djust-embedded]` or component wrapper,
retaining its legacy marker-only fallback. New WS/SSE regressions fail before
this fix. Both live backend/browser fixtures now stay disabled through the
acknowledgement and two background updates, and enable only on batch completion.

Verification: full Python suite 30,042 passed, 952 skipped; full JavaScript suite
2,011 passed across 188 files; mypy passed 1,021 source files; Ruff and generated
bundle ESLint passed. The initial batch test failed on the missing pending flag;
the initial six client batch tests failed before implementation. Tests also
cover pre-start cancellation, distinct tokens, repeated discard, empty batches,
multiple tasks, cross-transport completion, and errors with a newer request.
Browser fixtures use real WS/SSE transports but a test-only explicit constructor
bypass. Temporary tabs and servers were closed.

This is **not complete E4 or ADR-038 acceptance**. Root runtime background work,
deferred/component paths, mount/parent-queued child work, and the full legacy
no-ref/replacement matrix still require integration. Older clients do not know
the batch-completion message: activation requires a compatible client rollout,
not only a server update. The explicit constructor guard remains closed and
no proposed API is advertised as supported.

## Readiness audit

The milestone-audit checklist was applied to the two foundation boundaries.
There are no database schema changes, new Celery tasks, or auditlog registrations
in this slice. Existing state decorators, private field storage, context
descriptor discovery, form caches and legacy overrides were identified before
implementation. No new third-party runtime dependencies are introduced.

The remaining exposure work must inventory actual sinks, not just callers:
HTTP sessions, WebSocket/actor persistence, signed snapshots, reconnect/live
navigation, embedded state, time-travel, debug output and component providers.
Server-only data cannot be placed in readable signed cookies. Registration and
restore must fail closed without turning an unavailable provider into a legacy
reflection fallback. These are implementation gates, not completed guarantees.

## Verification boundaries

The subtree-lifecycle slice completed 29,986 Python tests with 952 skipped across
all three roots (four workers), 29 focused lifecycle tests, and a 260-test async/
dispatch regression set. Full-package mypy passed 1,013 source files.
Unregister and deterministic cross-thread waiter tests failed before their fixes.
The initial full run stopped on lifecycle-unaware mock fixtures and the old SSE
expectation that abandoned work returned normally; corrected tests still assert
successful recovery frames or cancellation with no stale delivery, respectively.
The final full run verifies the frozen implementation. This is native server/
transport evidence, not browser or cross-worker verification.

The identity-aware reuse slice completed 29,957 Python tests with 952 skipped
across all three roots (four workers). Focused child identity/event/mount/reuse
coverage passed 114 tests, and full-package mypy passed 1,011 source files.
The changed-input tests and bare-slot reattachment test failed before their fixes.
An initial full run had two new fixture errors (monkeypatching an absent optional
method); after correction, the final run above verified the frozen implementation.
Native templates, shared-runtime events and the post-render transport hook were
exercised, not a live browser or cross-worker deployment.

The shared-runtime child-event slice completed 29,896 Python tests with 952
skipped across all three roots (four workers), 12 focused event tests and 21
combined legacy/explicit timeout tests. Full-package mypy passed 1,009 source
files. The prior full run's single failure was the old two-site timeout inventory,
now replaced by the exact method inventory described above. These tests use real
runtime dispatch and database sessions with a recording transport, not a browser
connection or a cross-worker deployment.

The fresh eager-child slice completed 29,884 Python tests with 952 skipped
across all three roots (four workers). Focused mount/Decimal-inventory coverage
passed 119 tests, the earlier mount/reuse/adapter/legacy-restore set passed 81,
and full-package mypy passed 1,008 source files. An initial full run's single
failure identified the codec inventory assumption corrected above. The actual
HTTP GET and native template tests do not establish browser or cross-worker
coverage, and the production explicit gate remains closed.

The child-reuse authorization slice completed 29,866 Python tests with 952
skipped across all three roots (four workers), 24 focused reuse tests and
full-package mypy across 1,007 source files. This verifies the stated native
server-render paths, not browser reattachment or completed explicit persistence.

The bound-child adapter slice completed 29,842 Python tests with 952 skipped
across all three roots (four workers), 81 focused child/session checks and
full-package mypy across 1,006 source files. No lifecycle or browser activation
is inferred from those adapter-level results.

The direct-state API slice completed the full Python run across all three roots:
29,809 passed and 952 skipped (four workers; benchmarks disabled under xdist).
The focused direct-state/structural tests passed 62 cases, and full-package mypy
passed all 1,004 source files. The preceding full run's only two failures were
obsolete line-number whitelist entries, replaced by the exact AST matcher and
mutation canaries above. Audio's pre-filter rejection also has failing-before,
passing-after evidence. No JavaScript or Rust implementation changed in this
slice; these Python results do not establish browser or Rust-suite coverage.

The SSE slice's full Python run at `e5230a5b0` across all three roots completed
with 29,761 passed and 952 skipped (four workers; benchmarks disabled under xdist).
An earlier run exposed an obsolete SSE no-op-lock expectation and a source pin
predating the explicit-policy mount-HTML rule. The lock test now asserts actual
serialization, and the mount pin preserves the legacy-only rule. All 1,952
JavaScript tests pass, as do full-package mypy and pre-commit checks. These counts
do not establish ADR acceptance, performance guarantees, or Rust-suite coverage.

The earlier DB-session import-ban correction remains narrowly scoped: the imported
class is used only in the explicit adapter's concrete implementation allowlist,
not instantiated. A cache-only explicit save/restore test forbids database access.

Tests are in `python/djust/tests/test_state_descriptor_contract.py`,
`test_state_descriptor_typing.py`, `test_form_hooks_adr035.py`,
`test_exposure_contract.py`, `test_exposure_policy_guard.py`, and
`test_exposure_sessions.py`, `test_exposure_debug.py`, and
`test_exposure_context.py`, and `test_exposure_http.py`. Run these with
the existing form, decorator, state and WebSocket regression suites. This slice
now also includes `test_exposure_sse_navigation.py` and bundled JavaScript SSE
navigation tests. The browser evidence above covers only the stated sequences;
it does not establish cross-worker or complete explicit-exposure parity.
