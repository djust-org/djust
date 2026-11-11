# djust 2.0: next priorities

Review date: 2026-10-28. This is a follow-up to the [2.0 source review](2026-10-27-djust-2-strategy.md), based on the worktree at `2c4c6ef`. Priorities for adoption are hypotheses to validate with Django developers, not conclusions from user research. The earlier review is a historical snapshot; its rebase and verification status predates the subsequent implementation and push.

## Already completed

The fingerprint budget now bounds traversal and conservatively invalidates truncated comparisons. Unchanged dynamic template sources preserve the prior HTML and text index while retaining correctness for changed sources and render environments. Hooks have an opt-in `shouldUpdate()` and Chart.js can opt into attribute-based update skipping. The [benchmark report](../performance/2026-10-27-bounded-state-and-widget-updates.md) records the measurements and limits: these are targeted microbenchmarks, not application latency or capacity results. In particular, the widget benchmark used a simulated chart, and opted-in callback skipping does not eliminate global browser discovery.

The rebased `2.0` branch was pushed at `2c4c6ef` after a full integration run. That is historical evidence for that commit, not a claim about subsequent changes.

## 1. Establish a reliable widget integration contract

**Recommendation:** Make the existing hook mechanism the supported boundary for specialist JavaScript widgets. Define DOM ownership, props, event delivery, acknowledgment and cleanup consistently across ordinary patches and full HTML recovery. Preserve server-side validation and authorization.

The current paths differ: Rust patching preserves ignored descendants while allowing host attributes to change, whereas full client morphing skips the ignored host entirely ([Rust diff](../../crates/djust_vdom/src/diff.rs), [client morph](../../python/djust/static/djust/src/12-vdom-patch.js)). Hook `pushEvent()` constructs its own WS/SSE messages and does not provide the ordinary acknowledgment/ref contract or an HTTP fallback through that method ([hooks](../../python/djust/static/djust/src/19-hooks.js)). These are specific contract gaps, not evidence that every integration fails.

**First slice:** Specify the contract and fix one end-to-end path using the existing Chart.js integration and a rich-text editor as reference consumers. Test in a real browser with two widget instances, changed and unchanged props, keyed movement, replacement, removal, full recovery, navigation and reconnect. Cover focus, selection and IME input; asynchronous initialization completing after removal; validation failures and correlated event outcomes; and strict CSP. Exercise supported transports explicitly. Record what happens to unsaved local edits on recovery.

Keep native accessible controls, forms, and typed stateful components. Integrate established libraries for editors, charts, maps and complex grids instead of starting a broad competing widget catalog. Prefer plain JavaScript adapters when suitable; an optional React adapter should follow a demonstrated need from an existing React component or design system. A new island API should need a clear reason beyond what the existing hook contract can provide.

## 2. Measure complete turns on the 2.0 state model

**Recommendation:** In parallel with the widget contract, establish a repeatable baseline using explicit exposure and strict parameters. The optimized server-push path is currently gated on legacy exposure ([WebSocket implementation](../../python/djust/websocket.py)); historical legacy capacity figures therefore do not establish explicit-mode capacity.

Compare no-persistence and server-persistence configurations and applicable WS, SSE and HTTP workloads. Include a small form, a 1,000-row table, a multi-widget dashboard, a push stream and an editor-like draft. Measure p50/p95 latency, event-loop lag, CPU, allocations and memory per connected view, wire bytes and browser patch/input time. Include 20, 100 and 250 ms simulated RTT; separate application database/API time from framework work. State machine, topology, versions, sample sizes and workload must be recorded so results can be repeated.

Use the resulting profile to select work on Python state traversal, serialization, persistence, browser discovery or rendering. The goal is a small user-visible change doing a proportionate amount of work, with no relaxation of authorization or commit-before-success guarantees.

## 3. Bound slow-consumer resource use

The SSE session currently creates an unbounded outbound queue ([SSE implementation](../../python/djust/sse.py)). This is a verified queue shape, not a reproduced production exhaustion event.

Define message and/or byte limits, admission and cancellation behavior, and how a slow client recovers. Relative patches cannot be discarded arbitrarily because they depend on a particular client base state; ordered effects and accepted operation results need explicit delivery semantics. A versioned snapshot or resynchronization may be appropriate for coalescible state, but must be designed and tested.

**Acceptance:** A slow-reader test demonstrates a defined memory bound and visible recovery, including ordered effects, reconnect and cancellation. Specify behavior for each supported transport rather than assuming SSE behavior applies everywhere.

## 4. Deliver and validate one complete Django workflow

Connect existing primitives into a documented, tested path for adding an interactive list/detail/form workflow to an existing Django application: URL-backed filters and pagination, validation, permissions, loading and errors, deployment and reconnect. This is a coherent product outcome rather than a claim that these primitives are missing.

Ask several Django teams to use it for a real feature. Measure time to first working feature, configuration and code required, problems encountered, deployment success and whether they choose djust again. Treat the ordering of adoption opportunities as provisional until this evidence exists.

## 5. Choose larger optimizations from the baseline

Candidates include scope-aware browser discovery, skipping unnecessary persistence on unchanged turns, lazy recovery HTML and reducing whole-tree work for small updates. Opt-in `shouldUpdate()` avoids some widget callbacks but [response handling](../../python/djust/static/djust/src/02-response-handler.js) still triggers broader reinitialization. Do not simply narrow the existing hook scan: its cleanup reconciles against the global hook registry and could destroy unrelated hooks. Scope, identity, movement and disposal must be designed together.

Prioritize by measured end-to-end impact and implementation risk. Preserve recovery correctness, session expiry and ownership semantics, and test no-op, changes, removal, reconnect and failure paths as appropriate. The full analysis and additional candidates, including cache memory bounds, are in the [original review](2026-10-27-djust-2-strategy.md).
