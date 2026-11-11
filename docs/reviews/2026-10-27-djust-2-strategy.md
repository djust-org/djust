# djust 2.0: source review and product direction

Review date: 2026-10-27. Original 2.0 tip: `087271af5`. Main used for the rebase: `694a6b0e8`. The rebased series ends at `e3ef2a8d2`; runtime validation covers integration-fix commit `287f19d9c`. This report is a subsequent documentation-only change. Source references below refer to that rebased tree plus the integration fixes. This is an architecture/product review with selected measurements, not a complete security audit or production capacity certification.

## Recommendation

Make djust the easiest way to add reliable, interactive features to an existing Django application. Keep Django responsible for business state, permissions, forms and persistence. Let the browser own transient interaction. Make mature JavaScript widgets easy to embed without requiring an entire frontend application.

**Build a small, excellent native component foundation and a strong integration contract. Do not build a competing general-purpose JavaScript component ecosystem, rewrite the catalog in React, or introduce a new client state framework to justify existing APIs.**

The best performance goal is also straightforward: **a small change should do a small amount of work throughout the whole turn.** djust already has sophisticated Rust optimizations. Python state walks, full-page serialization, persistence and browser lifecycle scans can still make a one-patch response expensive.

The existing 2.0 direction is largely sound. Explicit exposure, strict parameters, transport convergence, fewer duplicate component renderers and clearer state lifetimes address real complexity. The risk is making the release depend on simultaneously building a generalized distributed state/job platform, replacing the transport protocol, migrating every component abstraction and expanding the widget catalog. Keep the settled outcomes, but deliver the smallest contracts and adapters that prove them in real applications.

## What is already here

This review does not recommend rediscovering existing work:

- Retained Rust rendering, component-scoped rendering, text-region fast paths, keyed reconciliation, virtual lists, adaptive loop caching and per-render bridge/include caches already exist.
- Main adds fused state conversion/fingerprinting, template-source caches, turn middleware, worker-pool improvements and a legacy stateless-session option. The rebase preserves them.
- Hooks, typed hook values/targets, local JS commands, extension sockets and an optional Chart.js adapter already exist. ADR-025 is accepted. `dj-island` in ADR-020 remains proposed.
- The 2.0 delivery plan already addresses strict exposure, migration tooling, transport parity, shared state, durable jobs, reconnect recovery and representative downstream applications. Several are planned outcomes, not implemented capabilities.
- Optional client loading, component consolidation and patch-only rendering have already been proposed or deliberately deferred. Their remaining value comes from completing and measuring them, not from writing another proposal.

The checked-in client manifest was regenerated during the rebase: **83,805 bytes (81.8 KiB) gzip**, 321,325 bytes minified, 60 source files. Source-file count and unminified size are not the browser download cost. This runtime figure also excludes optional component/widget assets loaded by particular pages.

## Highest-value optimization opportunities

The first two are concrete, localized code issues. The larger architectural opportunities have verified mechanisms but unmeasured end-to-end impact.

### 1. Make the fingerprint budget actually bound work

`deep_fingerprint` advertises a default 20,000-node work budget. After it is exhausted, the walker returns a truncated identity for each remaining value, but container loops still visit every remaining sibling and allocate a fingerprint entry. A wide 500,000-element list therefore still produces 500,000 entries.

**Evidence:** [change_detection.py](../../python/djust/change_detection.py), lines 145–201 and 205–254. Runtime pre/post snapshots at [runtime.py](../../python/djust/runtime.py), lines 4927–4938 and 5057–5062; render context fingerprinting in [rust_bridge.py](../../python/djust/mixins/rust_bridge.py), around lines 946 and 1071.

A narrow local probe against the rebased source on Python 3.12.9 produced:

| Input length | Budget | Fingerprint entries | Median of three runs |
| --- | ---: | ---: | ---: |
| 20,000 | 20,000 | 20,000 | 8.03 ms |
| 100,000 | 20,000 | 100,000 | 32.26 ms |
| 500,000 | 20,000 | 500,000 | 168.92 ms |

This establishes the missing cost bound, not a production speedup or a controlled comparison with another revision. Other validation was running on the host, so timings are illustrative; output cardinality is the decisive evidence. Main's exact-builtin fast path reduces overhead but does not fix the bound. Reproduction script and source-hashed output are retained in `scratch/djust-2-review/fingerprint_probe.py` and `fingerprint-probe-final.jsonl` in this worktree.

**Proposal:** stop sibling traversal once the budget is exhausted, using an explicit conservative remainder/invalidation contract. Alternatively provide a supported versioned or immutable collection boundary for large data. Preserve detection guarantees; silently overlooking more mutations is not an optimization. Move expensive post-handler snapshots off the event loop where ordering permits.

**Prove it:** sweep wide and nested collections through 1,000–1,000,000 entries; measure allocations, elapsed time and event-loop lag. Exercise mutations on both sides of the cutoff, reorder, equal-length replacements and explicit invalidation. A bounded implementation must plateau after the budget.

### 2. Stop invalidating retained caches for an unchanged template property

`TemplateMixin.render_with_diff` calls `update_template` whenever `template` is a property, even when the returned source is unchanged. Rust's `update_template` clears the prior HTML, fragments, text indexes and loop cache unconditionally.

**Evidence:** [template.py](../../python/djust/mixins/template.py), lines 2029–2035; [Rust LiveView](../../crates/djust_live/src/lib.rs), lines 650–659.

**Proposal:** compare source before invalidation, ideally at the Rust boundary so all callers benefit. A real source change must still invalidate everything it invalidates today; registry-generation checks remain independent.

**Prove it:** compare an identical fixed template and property template around a large unchanged list while a scalar changes. Record cache hits and render phases, then change the actual source and verify byte-correct recovery. Priority depends on real use of template properties, but the change is much smaller than a renderer redesign.

### 3. Make browser update work proportional to the changed DOM

A successful nonempty patch response invokes unscoped `reinitAfterDOMUpdate`. That performs multiple discovery passes. Even where a scope is available, hook updates remain global: every surviving hook receives `updated()` regardless of changed inputs. The Chart.js adapter re-reads values and calls `chart.update()` each time. The general hooks guide even teaches destroying and recreating a chart on update.

**Evidence:** [response handler](../../python/djust/static/djust/src/02-response-handler.js), line 324; [event binding](../../python/djust/static/djust/src/09-event-binding.js), lines 1756 and 2338–2360; [hooks](../../python/djust/static/djust/src/19-hooks.js), lines 272–323; [Chart.js adapter](../../python/djust/static/djust/ext/dj-chart.js), lines 108–124. A minimal probe of the actual hook source mounted once and called `updated()` twice despite zero DOM or attribute changes. It did not measure Chart.js or a browser page.

**Proposal:** return mutation metadata from patch application: inserted/removed roots, changed attributes and text-only updates. Use it for discovery and changed-prop notifications. First compare raw inputs or revisions to avoid unchanged adapter updates. Parsed-value caching needs an ownership contract: current reads return fresh JSON objects, and handing one shared cached mutable object to a widget would change semantics. Define immutability or cloning before adding such a cache.

Do not merely pass a smaller root to today's `updateHooks`: its cleanup compares the scoped discovery set against the global active-hook registry and could destroy unrelated hooks. Scoping, identity and disposal must be designed together.

**Prove it:** real-browser pages with 100/1,000/10,000 nodes and several charts/editors; repeatedly update an unrelated counter. Measure discovery CPU, callback count, JSON parses, redraws, long tasks and input latency. Include removal, replacement, scoped handlers and virtualized nodes.

### 4. Establish performance on the explicit state model that 2.0 will require

The optimized single-worker-hop server-push path is currently gated on legacy exposure. Explicit views retain additional authorization/commit choreography and do not use that path. The ordinary runtime also decodes Rust patch JSON and later encodes it again; the optimized push path can splice already encoded patches.

**Evidence:** [websocket.py](../../python/djust/websocket.py), lines 5927–5938, 6041–6063 and 6185–6193; [runtime.py](../../python/djust/runtime.py), around lines 7817–7819. Main's improvements do not remove this eligibility distinction.

**Proposal:** benchmark explicit mode now, before retiring legacy mode. Then group adjacent synchronous work and encode once within the shared core, preserving fresh authorization, scoped ownership and commit-before-success. Do not remove these guarantees to recover throughput.

**Prove it:** compare identical workloads using legacy, explicit without persistence and explicit with persistence across WS/SSE/HTTP; hold client count, worker topology and payloads constant. Record thread hops, event-loop lag, p95 latency and sustained updates. Historical legacy capacity results do not establish 2.0 capacity.

### 5. Separate ordinary patch delivery from recovery HTML generation

Even scoped component updates serialize the entire parent tree to HTML. The text-region path can clone the whole VDOM before modifying one text node. Small wire output therefore does not imply small server work.

**Evidence:** [Rust LiveView](../../crates/djust_live/src/lib.rs), lines 1268–1277, 1866–1869 and 5184–5193. The earlier [template-pipeline profile](../performance/template-pipeline-2026-09-13.md) already identified full HTML as a major remaining cost and deliberately deferred its removal because recovery/hydration need it. Those historical timings were not rerun here.

**Proposal:** investigate a versioned retained VDOM with lazy recovery-HTML materialization, and validate-then-mutate or structural sharing for text changes. Preserve current recovery behavior and memory accounting. Avoid adding another permanently retained full-page copy.

**Prove it:** constant one-text or one-component updates inside 10 KiB/100 KiB/1 MiB pages. Measure all phases and RSS, then force recovery after every update, template change and reconnect. This is a larger design change; benchmark before committing to it.

### 6. Avoid unnecessary persistence and snapshot work on no-op turns

Explicit commit/capture occurs before the render no-op decision. Views with declared persisted fields can project and save those fields even when the handler leaves them unchanged; client snapshots can also be captured and signed. Views with no persistence declarations already avoid actual adapter writes.

**Evidence:** [runtime.py](../../python/djust/runtime.py), lines 5035–5038 versus 5057–5062; [_exposure_sessions.py](../../python/djust/_exposure_sessions.py), lines 191–195 and 397–411; [_exposure_snapshots.py](../../python/djust/_exposure_snapshots.py), lines 64–78.

**Proposal:** track dirty versions by persistence destination, with deliberate TTL/lease refresh behavior and conditional writes. A render no-op is not necessarily a persistence no-op; child state, side effects, identity/schema changes and expiry matter. The new legacy `view_state_in_session=False` option does not solve this explicit-mode case.

**Prove it:** no-op, render-only and persistent-state events with 0/1/64 KiB state against realistic backend latency. Count writes/bytes and test conflicts, expiry and multiple tabs. Keep commit-before-success.

### 7. Bound caches by memory as well as lifetime

The compiled-template cache is a process-global map keyed by namespace and source. Backend teardown can remove a namespace, but that does not bound the number or total bytes of distinct templates retained during a long-lived backend; namespace zero is excluded from that cleanup.

**Evidence:** [Rust LiveView](../../crates/djust_live/src/lib.rs), line 143, lines 3162–3186 and 3232–3241.

**Proposal:** byte/entry limits, measured hit rates and safe eviction of cache-owned handles while active views retain their own references. Keep runtime values in context rather than generating high-cardinality template source.

**Prove it:** render/drop many distinct sources in one persistent namespace and compare RSS/cardinality with a fixed catalog. Verify registry generations, AST identity, finalizer re-entry and retained views. This is a workload-dependent retention concern, not evidence of a leak in every app.

### 8. Improve render-region granularity only after measuring the surrounding overhead

General partial rendering still operates on supplied top-level template nodes. A large outer wrapper can make a small dependency change rerender a large region. Component-scoped rendering already solves narrower cases.

**Evidence:** [renderer.rs](../../crates/djust_templates/src/renderer.rs), lines 2542–2578; [ADR-032](../adr/032-component-scoped-rendering.md). This is a known follow-up, not an undiscovered feature gap.

**Proposal:** conservative stable regions or recursive dependency caching, compared against explicit component boundaries. Custom tags, dynamic includes and whole-context consumers make naive static dependency filtering unsafe.

**Prove it:** inherited, nested real templates with custom tags and a small changing region; require HTML/patch/recovery equivalence. If Python walks and client scans dominate, fix those first.

### An additional operational omission: bounded slow-consumer behavior

SSE creates an unbounded `asyncio.Queue` and pushes with `put_nowait`. Session count and ingress rate controls do not bound queued outbound bytes when a live consumer drains slowly.

**Evidence:** [sse.py](../../python/djust/sse.py), line 460 and lines 910–939. This is a verified queue shape; no production exhaustion was reproduced.

The common transport design should specify byte/message high-water marks, admission control, cancellation and resynchronization. Render state may sometimes coalesce to a fresh versioned snapshot; ordered effects and accepted operation results cannot simply be dropped. Relative patches cannot be discarded arbitrarily without breaking the client's base version. Add a slow-reader test with a defined memory bound and visible recovery behavior.

## JavaScript and component strategy

### Keep three distinct responsibilities

| Responsibility | What djust should own |
| --- | --- |
| Native browser UI | Accessible server-rendered markup, Django forms/errors, ordinary buttons/actions, disclosure/dialog/popover, focus/loading/reconnect behavior and local JS commands |
| Reusable stateful UI behavior | Typed component state, validated inputs, scoped events and explicit outputs; application meaning and authorization remain with the view/server |
| Specialist widgets | A small adapter API to mature editors, charts, maps, complex grids and existing company components |

Consolidate duplicated **rendering implementations**, not every behavioral abstraction. Django tags do not by themselves replace typed component event/state ownership. The current interactive-components guide already distinguishes browser-local state, view actions and reusable typed behavior; make that the canonical entry point.

Duplication is visible today across `components/ui/button.py`, `button_simple.py`, `components/components/button.py`, `dj_button`, `DjButtonHandler` and `theme_button`, with different argument names and behavior. Main has already consolidated some other Rust handlers. Do not repeat the old plan's 192/197 duplication count as a current measurement.

### Harden existing hooks before adding another island system

There is already a workable integration pattern: a hook on a host with live prop attributes, and an ignored inner element that the widget owns. The official Chart.js canvas adapter intentionally does not use ignore: canvas preservation already works, and ignoring the host would freeze its props during full morphs. Apply ownership rules to the library's actual DOM behavior. Ownership currently differs across update paths:

- Rust `dj-update="ignore"` diffs host attributes and skips descendants ([diff.rs](../../crates/djust_vdom/src/diff.rs), lines 224–232).
- Full client morph skips the ignored element entirely, including host attributes ([12-vdom-patch.js](../../python/djust/static/djust/src/12-vdom-patch.js), lines 1347–1350).
- Hook events construct their own WS/SSE frames, without the ordinary event acknowledgment/ref contract or an HTTP fallback in this method ([19-hooks.js](../../python/djust/static/djust/src/19-hooks.js), lines 89–107). This does not bypass server authorization; it is a client transport/UX inconsistency. The transport plan already tracks it.
- Hook lifecycle callbacks may be asynchronous and are not awaited. A lazy import may finish after its host was removed. Current destroyed-hook discovery occurs after patching, whereas the old island proposal promises pre-detach cleanup.

Settle one contract covering initial mount, changed props, keyed movement, replacement, removal, full HTML recovery, navigation, reconnect and late async completion. Prefer expressing it through existing hooks and a single public event API. A children-only ownership mode could be justified; a second overlapping lifecycle API needs stronger justification than an old ADR.

The source also contradicts parts of ADR-020: generic `dj-on-<CustomEvent>` routing is not implemented, and accepting a custom-element tag name does not make light-DOM descendants opaque. Shadow DOM and light DOM need different integration treatment. The existing `djust.react`/`ReactMixin` machinery renders wrappers, but the shipped client contains Counter-specific handling rather than a general React root lifecycle. Reconcile or deprecate that partial surface when delivering a supported adapter.

### State belongs where the interaction happens

- Server: permissions, accepted domain state, saved records, authoritative validation.
- Widget/browser: focus, cursor/selection, hover, open menu, drag preview, viewport/zoom and pending edit draft.
- Boundary: explicit serializable props downward; named, validated intent events upward; acknowledgments, accepted values and errors back.

Do not resend arbitrary model dumps as props or automatically forward every custom event payload. Do not reset an editor from a complete server snapshot on every keypress. Collaborative editing is a separate consistency problem; ordinary LiveView events are not a CRDT.

### Use existing ecosystems selectively

1. Prefer a library's plain JavaScript API when it meets the need. A React runtime is unnecessary for many editors, maps and charts.
2. Offer an optional React adapter for an existing React design system or a React-only widget. Keep its dependencies out of core and use the same ownership/event contract as plain JS adapters.
3. Support Web Components deliberately: property versus attribute assignment, composed events, theming, form behavior and light/shadow DOM. Do not promise universal compatibility from a hyphenated tag name.
4. Build one polished reference integration at a time. Chart.js exists; use it to prove no-op updates and lifecycle behavior. Then an editor and an existing React component. Avoid launching five framework-adapter maintenance tracks without demonstrated demand.

Primary external evidence supports this shape:

- Phoenix separates local, DOM-patch-aware [JS commands](https://phoenix-live-view.hexdocs.pm/Phoenix.LiveView.JS.html) from [hooks and ignored widget regions](https://phoenix-live-view.hexdocs.pm/js-interop.html).
- React supports multiple partial-page roots and requires cleanup when another renderer removes the container: [createRoot](https://react.dev/reference/react-dom/client/createRoot). Arbitrary Django HTML is not automatically valid React hydration output.
- Lit documents [Shadow DOM boundaries](https://lit.dev/docs/components/shadow-dom/) and [event composition/retargeting](https://lit.dev/docs/components/events/).
- Tiptap supports [plain JavaScript integration](https://tiptap.dev/docs/editor/getting-started/install/vanilla-javascript). Its [setContent](https://tiptap.dev/docs/editor/api/commands/content/set-content) update behavior illustrates why adapters must prevent server-update/client-event feedback loops.
- [TanStack Table](https://tanstack.com/table/v8/docs/overview) supplies headless logic, not a finished accessible table product. [Chart.js](https://www.chartjs.org/docs/latest/getting-started/integration.html) supports selective feature loading. These are different tradeoffs, not a universal component backend.
- Accessibility remains real work for both native and adapted widgets; the [W3C combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/) shows the breadth of keyboard/focus/selection behavior involved.

## Capabilities most likely to unlock adoption

These are ranked product hypotheses, not claims supported by user interviews or market-share data. Validate them with developers using existing Django applications.

| Priority | Complete user outcome | Why it may matter more than another API |
| --- | --- | --- |
| 1 | Add an interactive form/table/detail workflow to an existing Django page | Incremental adoption has a smaller commitment than rebuilding an app. Provide URL-backed filters/sort/pagination, validation, permissions and loading/error states together. Many primitives already exist; the missing deliverable is a coherent path. |
| 2 | Deploy, restart, reconnect and keep the correct authorized state | Predictable operational behavior lowers adoption risk. Publish tested topology/resource limits and make fallback limitations visible. Deliver the existing shared-state/job plan through minimal adapters. |
| 3 | Embed a rich editor/chart/map/company widget without replacing Django | Removes a frequent class of architectural blockers while preserving an optional frontend build. Measure integration effort and lifecycle failures. |
| 4 | Run a background operation with progress, cancel, reconnect and a retained result | Useful for reports, imports and AI workflows. djust already has async/streaming primitives; the differentiated product is their reliable end-to-end behavior. Use existing task executors through a small adapter rather than building a scheduler platform. |
| 5 | Migrate to 2.0 with actionable diagnostics and executable examples | One canonical component path, accurate strict-mode examples, removal warnings and real-app compatibility CI are adoption features. Existing plans already call for these. |

For AI-heavy applications, emphasize cancellation, backpressure, partial output, errors and durable completion. Token streaming alone is already a primitive and is not a sufficient product strategy.

For deployment, define supported modes with real limits. A development memory backend can be simple; a multi-worker production configuration needs a documented ownership/recovery model. Do not imply that introducing Redis by itself provides operation deduplication, durable jobs or exactly-once external effects.

For diagnostics, build on existing turn/timing hooks. Expose safe production measurements of queue wait, handler/DB, persistence, render phases, payload bytes, recovery frequency, client patch/lifecycle time and per-session memory. This review found development observability surfaces; it did not establish that all production metrics integrations are absent.

## Assumptions to challenge

| Assumption | Better working hypothesis / consequence |
| --- | --- |
| Rust rendering speed predicts application speed | Measure a whole user turn. Python state work, DB/storage, network RTT and browser work may dominate. |
| Rust should reimplement every Django template behavior | Keep Django as the semantic source for cold, complex behavior and optimize measured hot paths. The 2.0 cold-filter delegation already follows this direction; preserve compatibility rather than maximizing the percentage written in Rust. |
| One small DOM patch means little work | The current pipeline can walk state, render/serialize the page and rescan browser nodes for that patch. |
| Django users want no JavaScript under any circumstances | They may want no mandatory frontend application or duplicated business logic. Preserve the no-build default while supporting optional integrations. |
| A larger built-in component catalog brings adoption | A few complete workflows and compatibility with existing widgets may unlock more applications. Test this with users. |
| Consolidating components means reducing everything to template tags | Consolidate markup production while preserving useful typed behavior and ownership. |
| Hooks or custom elements make interoperability solved | Ownership, cleanup, input composition, error acknowledgments, versioning and navigation are the hard contracts. |
| Legacy throughput carries over to explicit exposure | Important optimized paths have different eligibility. Benchmark the future default directly. |
| More tests or passing unit tests prove real widgets work | Test actual browser interaction, stale responses, focus/IME, disconnects, full recovery and disposal. Source pins alone cannot prove these. |
| Inert public names are harmless compatibility | They teach capabilities that do not exist. `@optimistic` and `@client_state` are explicitly inert; remove them with replacements or define a real reconciliation contract. Do not confuse the working descriptor `optimistic_rules` with the inert decorator. |
| Durable jobs require djust to become a general workflow engine | Own UI-facing operation/result contracts and executor adapters; let mature systems own scheduling/execution where appropriate. |
| A full TypeScript/framework rewrite is the necessary client cleanup | Explicit ES-module boundaries and typed adapter contracts can improve maintainability while preserving the current distributable bundle. Measure before expanding scope. |

The marketing promise should become something like: **“Interactive Django applications, with Python-first business logic and optional JavaScript where it helps.”** That is a recommendation, not a request to change branding in this task. The existing README's categorical “no JavaScript to write” is attractive for the default path but needs an equally clear advanced integration story.

## Suggested sequence and acceptance evidence

**First: freeze a baseline on the future defaults.** Use explicit exposure and strict parameters, with no-persistence and server-persistence variants. Measure small forms, a 1,000-row table, a multi-widget dashboard, a push stream and an editor-like draft workflow. Record p50/p95, event-loop lag, CPU, allocations/RSS per connected view, wire bytes and browser input/patch time. Test 20/100/250 ms network RTT rather than only localhost. Separate application DB/API time from framework overhead.

**Then: small fixes with clear falsifiers.** Fingerprint work bounds; unchanged-template invalidation; changed-input widget updates. Each needs a regression test plus a representative benchmark. Do not promise a percentage until measured.

**Then: finish one integration contract.** Same ownership and lifecycle across patches, full recovery, navigation and reconnect. One event-sending path with correlated completion/errors and strict server validation. Use Chart.js, one rich editor and one existing React component to prove it. Include keyboard/focus/IME, two instances, async-load cancellation and strict CSP.

**Then: deliver the already-settled operational contracts in narrow slices.** Shared authorized state, retained results, executor adapters, cursors and bounded queues. Keep lifetime/retry/cancel semantics explicit. Run representative downstream apps and node-restart/slow-consumer/uncertain-POST scenarios before release claims.

**Only after those measurements: choose the larger render redesign.** Lazy recovery HTML, recursive regions, dirty persistence and encoded-patch reuse may be valuable, but should compete on measured user-visible impact and implementation risk.

For adoption evidence, recruit several Django teams to add one real feature. Measure time to first working feature, code/config they had to write, framework fixes required, successful deployment and whether they choose djust for a second feature. This distinguishes an onboarding problem from a missing-widget or reliability problem better than component counts.

## Rebase and verification record

The local worktree is `/Users/tip/.codex/worktrees/djust-2-review/djust`, branch `codex/djust-2-review`. `codex/djust-2-pre-rebase-20261027` preserves the original 2.0 tip. Main is an ancestor of the rebased branch. The remote `2.0` branch was not rewritten or pushed.

Conflicts were resolved by retaining main's session-state policy, sync-turn wrappers, JIT caches and template-library registration behavior while applying 2.0's removals. Generated client artifacts were rebuilt from merged source. Version fields stay at `2.0.0.dev0` with main's dependency updates.

Runtime checks exposed two automatic-merge incompatibilities: a stale `JIT_AVAILABLE` guard in main's new object serializer and an import of the state-backend module removed by 2.0. Both were corrected. The transport parity fixture now sends same-origin HTTPS requests under main's stricter origin policy. No proposed performance or product features above were implemented.

Validation results are recorded in the accompanying session note and local `context/terminal/` logs. The worktree has its own frozen-lockfile Python 3.12.9/Django 5.2.17 environment and matching Rust extension; imports were verified to resolve here. The full JavaScript suite passed 3,671 tests across 259 files. Focused rebase integration checks passed 109 tests with one expected failure for HTTP `start_async` delivery, an acknowledged existing gap. Lockfile/SBOM version checks and targeted lint passed. The broad non-nightly Python run covered 49,830 cases: 49,203 passed, 622 skipped, four expected failures and one CI-shard collection-harness failure. The nested collector inherited the run's `PYTEST_ADDOPTS` selection; the complete shard-harness file then passed all 13 tests with those inherited options absent. No code changed between these runs. This is not represented as a single uninterrupted green full-suite run. `make test-rust` passed the workspace, LiveView and no-default-features checks (2,491 passing test executions across feature configurations, six ignored). Documentation checks passed. Nightly, browser and full remote CI/release gates were not run.

No downstream deployment, remote push, browser acceptance sweep, whole-system load test or complete security audit was performed. Local timing probes are deliberately narrower than those claims.
