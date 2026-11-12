# djust 2.0: consolidated review

Date: 2026-11-11. Status: for owner review. It records no decision; the owner decisions it lists are open. Revised the same day with a review of the worklist itself (section "Review of the worklist").

This merges two independent lines of 2.0 research into one list, records where they disagree, and marks what has changed since they were written. It adds no new measurements.

## Sources

| Doc (all in `docs/reviews/` on `2.0`) | Author | What it is |
| --- | --- | --- |
| `2026-10-27-djust-2-strategy.md` | Codex (Astra) | Source review: eight optimization candidates, JS/component strategy, five adoption hypotheses |
| `2026-10-28-djust-2-next-priorities.md` | Codex | Five next priorities after the first fixes landed; became issues #3656–#3659 |
| `2026-10-28-djust-2-partial-root-followup.md` | Codex | Work log for the Django 6.1 partial-root and child-ownership fixes on `2.0` |
| `2026-10-27-djust-2-deep-analysis.md` | Claude | Round 1: whole-turn profile, adoption study, assumption audit, catalog inventory |
| `2026-10-28-djust-2-deep-analysis-round-2.md` | Claude | Round 2: eight follow-ups (doc snippets, browser cost, downstream usage, topology, delegation, security, tests, canon) |

Both lines sit on top of the settled direction in `docs/plans/2.0.0-plan.md` (ten items, 2026-10-19) and `docs/plans/2.0-delivery-plan.md` (phases I, B, C, M, F, T, R and gates G1–G4).

## State today

> Update, later on 2026-11-11: `main` was merged into `2.0` in #4010, so the branch-divergence bullets below describe the state before that merge.

Checked 2026-11-11 against `origin/2.0` (`5aac79c05`) and `origin/main` (`d628e2793`, 1.3.3).

- **`2.0` has not moved since 2026-10-29.** No PR has merged into it since. `main` is 187 commits ahead of the merge base (2026-10-28 01:07 UTC); `2.0` is 71 ahead.
- **Five fixes that came out of the Claude review are on `main` only**: #3651 (README `template_string`), #3653 and #3654 (`asgi.py` static files), #3652 (the test that executes documented snippets), #3661 (two low-severity security fixes). All merged after the merge base. The README on `2.0` still contains `template_string`.
- **Codex's first three fixes are on `2.0` only**: the bounded fingerprint walk, the unchanged-template-source check, and the opt-in hook `shouldUpdate()`. `main` has no `shouldUpdate`.
- **Four issues carry the Codex priorities** on the `v2.0.0` milestone, each with no comments: #3656 (widget contract), #3657 (explicit-mode benchmark), #3658 (slow consumers), #3659 (incremental Django workflow).
- **The Claude findings had no issues until today.** #3987–#4007 were filed on 2026-11-11 from this consolidation: ten on `v1.4.0` (section A) and eleven on `v2.0.0` (sections C and D).
- **`main` picked up part of one finding independently**: #3886 added `server_push()` and `tick_interval` over SSE; #3885 (push scopes and `db_notify` over SSE) is open.
- **Still present on both branches** (grep of the cited lines): the always-true focus guard in `12-vdom-patch.js`, the unbounded `asyncio.Queue()` in `sse.py`, `theme_head` linking the catalog `components.css`, `initReactCounters`. `dj-island` exists in neither. `pushEvent` on `2.0` still builds its own frame with no `ref`.
- **Not rechecked**: every timing and memory number. The Claude numbers were taken on `9a19cf95d`, before `2.0` was rewritten and before the fingerprint change; the Codex line references are to the rebased tree.

One observation from the backlog, by issue title only: about a third of the 47 open `v1.4.0` issues are differences between a component's Python render path and its native Rust handler (#3663, #3869, #3873, #3878, #3882, #3883, #3895, #3932 and others). Both reviews recommend removing those duplicate layers.

## Where the two agree

1. Keep the three flips (explicit exposure, strict parameters, one transport core).
2. Rust render speed does not predict turn speed. Python state work, persistence and browser scans dominate. Claude measured it: Rust render and diff is 1–30% of a server turn.
3. Benchmark explicit mode before it becomes the default. Claude measured 1.8–2x legacy at p50 in every topology.
4. Do not grow a first-party widget catalog. Keep a small native foundation and finish the hook contract for third-party widgets. No new island API, no Alpine-style companion, React only as an optional adapter.
5. The hook contract has the same gaps in both reports: `dj-update="ignore"` means different things in the Rust diff and the client morph; `pushEvent` has no ack, no reply and no HTTP fallback; `destroyed()` runs after detach; a late `import()` can outlive its host.
6. The client re-scans the whole page after every patch, and every hook's `updated()` fires on every frame.
7. The SSE outbound queue is unbounded.
8. Background work needs progress, cancel, reconnect and a retained result, through executor adapters and not a new scheduler.
9. Positioning should move from "no JavaScript" to Python-first with optional JavaScript.
10. Adoption claims are hypotheses until several outside Django teams build one feature each.
11. Delegating cold template behaviour to Django is right, as a maintenance saving.

## Where they differ

| Topic | Codex | Claude | Suggested resolution |
| --- | --- | --- | --- |
| What comes first | Baseline, small fixes, widget contract | The newcomer path: README runs in CI, in a browser, before any 2.0 work | Claude's ordering. The fixes exist on `main`; `2.0` lacks them. A browser check of the README flow is still missing on both. |
| 1.4 migration program | Keeps it; "migrate with diagnostics" is adoption item 5 | Shrink phases I, B, M, gate G1 and the codemod to the inventory command and a note; four outside repos import djust | **Owner decision.** This contradicts settled direction item 1 and gate G1. |
| Catalog | Consolidate render paths; no numbers | Round 1: delete about 60k lines. Round 2 corrected it: 70 of 205 tags are in use, three owner apps depend on about 25 | Use round 2: delete the 135 unused tags, duplicate layers and vendored bundles; decide the 25 against the two heaviest apps. |
| Chart.js updates | Shipped attribute gating as opt-in | Default should be attribute-gated; today it redraws on 54 of 54 unchanged turns | Flip the default on `2.0`, where breaking changes are allowed. |
| Widget contract timing | One 2.0 deliverable (#3656), proved with Chart.js and an editor | Ship the five contract fixes in 1.x as bug fixes, then adapters, capped at three | Split #3656: bug-class fixes to `main`, the semantic change to ignore and the adapter API to `2.0`. |
| HTTP as default | Publish a topology table with limits | Make HTTP plus SSE the zero-config default and WebSocket the upgrade | Partly settled already (direction item 3; #2560 put `channels` behind an extra at 2.0). Whether HTTP is the *default* is an owner decision. |
| Benchmark first or fix first | Measure, then choose (#3657) | Already measured; gives an order of attack | Treat the Claude tables as the first baseline. Narrow #3657 to a rerun on the current tip plus what was not covered: SSE, HTTP, a second node, 20/100/250 ms RTT on turns. |
| Process | Not covered | 64% of recent `main` pushes red, a tiering proposal, a canon move to `docs/patterns/` | Separate track; not a 2.0 gate. `main` has since added a merge queue and path-scoped skips (#3805, #3807), so re-measure before acting. |

Found by only one side:

- **Codex only:** the template cache has no size bound; partial rendering works on top-level nodes only; recovery HTML is serialized on every scoped update; patches are decoded and re-encoded; and the partial-root work on `2.0` left root-mode updates at 7.50 and 9.45 ms against 2.79 and 2.74 ms for ordinary paths at 1,000 rows, which the follow-up doc itself calls unfinished.
- **Claude only:** the four Python walks of one changed container; context-processor output re-sent every turn; the legacy msgpack snapshot after every render; the per-turn explicit ceremony; about 3.3 MB retained per view of a 50 KB page; the focus steal; `pushEvent` replies attributed to the wrong pending event; `persist="client"` lost on reconnect; in-flight `start_async` results lost on every reconnect; the downstream tag usage scan; the security probe set; the test and canon audits; and the external adoption numbers.

## Consolidated worklist

Sources: **X** = Codex, **C1** / **C2** = Claude round 1 / 2.

### A. Fix on `main` now (bugs, no API change)

| # | Item | Source | Issue |
| --- | --- | --- | --- |
| A1 | Focus steal: the `!focusState.id` guard is always true, so `[autofocus]` takes focus on every patch | C1 §2.8, C2 §2 | #3987 |
| A2 | `persist="client"` token is not sent on a reconnect mount | C2 §4 | #3988 |
| A3 | In-flight `start_async` result is dropped on reconnect; a persisted loading flag stays on `running` | C2 §4 | #3989 |
| A4 | `pushEvent` goes through `sendEvent`: ref, ack, reply promise, HTTP fallback | X, C1 §5 | #3990, #3656 |
| A5 | Extension scripts carry the CSP nonce | C1 §5 | #3991 |
| A6 | "No hook registered for Chart": init runs before deferred extensions register | C2 §2 | #3992 |
| A7 | `from djust import rate_limit` returns the module after `runtime` imports it | C2 §1 | #3993 |
| A8 | The eight documented snippets pinned as failing in #3652; the demo project's six 500s and seven first-click errors | C2 §1 | #3994 |
| A9 | Playwright check of the README "add to an existing project" flow in CI | C2 | #3995 |
| A10 | Context-processor keys re-sent and re-validated every turn (about 0.45 ms on every view) | C1 §2.2 | #3996 |

### B. Precondition on `2.0`

| # | Item | Source |
| --- | --- | --- |
| B1 | ~~Merge `main` into `2.0`~~ Done 2026-11-11 in #4010 (merge commit `832b2c67b`); `main` at `3686825ff` is an ancestor of `2.0` | this review |
| B2 | Rerun the explicit-mode baseline on the merged tip (#3657, narrowed as above) | X, C1, C2 |
| B3 | Resolve or accept the partial-root update overhead (about 2.7–3.4x at 1,000 rows) | X follow-up |

### C. 2.0 acceptance gates to add to the delivery plan's matrix

| # | Gate | Source | Issue |
| --- | --- | --- | --- |
| C1 | Explicit-mode turn cost has a stated target against legacy; the per-turn ceremony is cut (contract cached per class, no commit or snapshot when nothing is persisted, one session load) | C1 §2.4, C2 §4, X §4 and §6 | #3997, #3657 |
| C2 | Per-view memory is published as a function of page size, with a ceiling; one bounded digest replaces the two fingerprint trees | C1 §2.9, C2 §4 | #3998 |
| C3 | A one-cell change in a 1,000-row list has a server-time target; fingerprints are reused across the snapshot and the sync | C1 §2.1 | #3999 |
| C4 | Slow consumer: a defined memory bound and visible recovery on each transport | X, C2 §6 | #3658 |
| C5 | A topology table of what survives reconnect, restart and a second node, backed by tests (extends gate G3) | X, C1 §3.4, C2 §4 | #4000 |
| C6 | One widget contract across patch, full recovery, navigation and reconnect, proved in a browser with Chart.js and one editor | X, C1 §5 | #3656 |
| C7 | Browser work proportional to the change: `_scanScopedElements`, the fixed 39 `querySelectorAll` per turn, `SetText` without a `dj-id` | X §3, C1 §2.8, C2 §2 | #4001 |

### D. 2.0 removals and defaults

| # | Item | Source | Issue |
| --- | --- | --- | --- |
| D1 | Delete the 135 unused catalog tags, the duplicate `ui/`, descriptor and Rust handler layers, `react.py`, the vendored xterm, Leaflet and highlight.js | C2 §3 | #4002 |
| D2 | Stop `theme_head` auto-linking the 327 KB catalog stylesheet into every scaffolded project | C2 §3 | #4003 |
| D3 | Chart.js adapter updates on attribute change by default | X, C2 §2 | #4004 |
| D4 | Route the 12 cold filters to Django with the size caps re-added | C2 §5 | #4005 |
| D5 | Remove the inert `@optimistic` and `@client_state` or give them a real contract | X, plan §8 | #4006 |
| D6 | Reconcile or remove ADR-020 (`dj-island`), which is proposed and unimplemented | X, C1 §5 | #4006 |
| D7 | Lazy-load client modules by `dj-*` presence; stop calling the client tiny until then | C1 §4.4 | #4007 |

### E. Owner decisions

1. ~~Does the 1.4 migration program stay at its settled size (phases I, B, M, gate G1, the codemod), or shrink?~~ **Decided 2026-11-11: folded into 2.0.** No 1.4 release; what remains is the guide (#4022), the inventory fix (#4023) and the project's own apps (#4024).
2. Is HTTP plus SSE the zero-config default in 2.0, with WebSocket as the upgrade?
3. Which of the roughly 25 chrome and behaviour tags stay in core, and which move to an extra package?
4. Positioning before 2.0.0: drop "no JavaScript", "tiny client" and Rust-speed as headlines?
5. Release cadence and a stability statement.
6. Which three outside Django teams to recruit, and who asks them.
7. Do the test tiering and the canon move run alongside 2.0, or wait?
8. ~~S4: does `2.0` become the trunk now, with 1.3.x limited to security and serious fixes?~~ **Decided 2026-11-11: yes** (#4013). The `2.0` branch is deleted.
9. S2: does declared state persist by default, with in-memory-only as the opt-in?
10. S3: do component tags render through one path, with native versions only where a benchmark earns one?
11. Is further partial-root work paused until its threat model is written down?

### F. Deferred until the baseline says otherwise

Lazy recovery HTML, recursive render regions, encoded-patch reuse on the ordinary path, an N-region text fast path, a template-cache size bound, the standalone template-backend install path, the multiplayer demo, agent-legibility as a recurring gate.

## Review of the worklist

Added 2026-11-11, after the `main` → `2.0` merge (#4010). This is one reviewer's judgment, not a measurement. It asks whether the worklist above serves the goal ("interactive Django without leaving Django") and whether there is a simpler way to get the same results.

**Verdict.** The direction is right. The shape is not: several items tune around a structural cause that would be simpler to remove.

### How each part fits the goal

| Fit | Items |
| --- | --- |
| Fits; do as written | A1–A9, C4, C6, C7, D1, D2, D3, D5, D6, and the list/detail/form workflow (#3659) |
| Right symptom, wrong fix | C1, C2, C3 and A10: each optimizes change detection by comparison. See simplification 1. |
| Drifts from the goal | The shared-state and durable-job platform (delivery plan phases C and R), the 1.4 migration programme (phases I, B, M and gate G1), and D7 as written |
| Low priority, fine as written | D4 |

On D7: loading modules on demand adds load-order and CSP work for a download that still loses to htmx on size. Two static bundles, core and extras, get most of the benefit. Issue #3992 (a hook warning caused by init running before an extension registers) is the kind of bug dynamic loading multiplies.

### Four simplifications

**S1. Track changes by assignment, not by walking state.**
The four Python walks (C3), the two retained fingerprint trees (C2) and much of the explicit-mode cost (C1) exist because djust finds changes by comparing state before and after a handler. 2.0 already requires state to be declared with `state()`, and `StateProperty.__set__` in `python/djust/_state.py` already sees every assignment (it records the field name in `_reactive_state`). Using assignment as the change signal would remove most of C1–C3 instead of tuning them.

- Open problem: in-place mutation such as `self.items.append(x)`. Options are a tracked container type, or keeping the fingerprint walk in `DEBUG` only, to warn about a change the assignment signal missed.
- Not verified: every consumer of today's change detection (`rust_bridge.py`, `runtime.py`, `live_view.py` and `websocket.py` all read it). Needs a spike before anyone commits to it.

**S2. Declared state survives by default.**
Round 2 measured that only `persist="server"` survives a reconnect, a worker restart or a second worker. The default, plain `state()`, loses on all three. If declared state is stored every turn by default and in-memory-only becomes the opt-in, any worker can serve any event. That covers C5, plan port R3, gate G3 and the sticky-session concerns in one change, and WebSocket becomes a faster pipe instead of the place state lives.

- Cost: a store write per turn. Round 1 measured the session write at about 2.4 ms on a counter turn; it needs remeasuring with S1 in place, since S1 also tells the store what changed.

**S3. One render path per component tag.**
The native Rust twins of the component tags are 11,290 lines in `python/djust/components/rust_handlers.py` alone.

- About a third of the open `v1.4.0` issues, by title, are differences between a tag's Python output and its native output.
- `main`'s argument wrappers (`DjangoCheckedTagHandler`, `_TypedBlockOperands`) exist to make the twins accept what the Python tags accept.
- One of the three silent breaks in #4010 was those wrappers colliding with `2.0`'s exact-class layout check.

Render component tags through the existing `{% load %}` bridge (#2547) and keep a native version only where a benchmark earns it. This extends D1 and should come before most of the 1.4 backlog, because it retires the class those issues belong to.

**S4. One trunk.**
Two weeks of divergence cost 70 conflicted files and three breaks that merged with no conflict (#4010). Every deprecation `main` adds for a 2.0 removal is code `2.0` deletes again. Make `2.0` the trunk and limit 1.3.x to security and serious fixes. This also answers owner decision 1: without a second trunk there is little for a 1.4 bridge to bridge.

### What the worklist is missing

- **A threat-model page for the partial-root proof machinery.** It is the most intricate code touched in #4010 (`provenance.rs` is 1,781 lines, with `child_bindings.rs`, `provenance_edits.rs` and `lazy_provenance.rs` beside it), and its own work log records root-mode updates 2.7–3.4x slower than ordinary ones at 1,000 rows (item B3). No document read for this review states the threat it defends against in a paragraph. Until one does, pause further work on it; B3 cannot be judged without it.
- **Coexistence with htmx and Django 6 partials** as a tested path: a `dj-root` island on a page that otherwise uses htmx. Round 1 ranked it second by evidence; it has no issue.
- **Generic live views**, in the spirit of Django's class-based views (`LiveListView`, `LiveFormView`), so that #3659 ships as a feature and not as a tutorial. The pieces exist (`ModelFormMixin`, the data table, the scaffold generator); how far they already go was not checked.
- **A `django.tasks` adapter** in place of a djust-owned executor and storage interface (plan phases C0–C3). Round 1 proposed it; the delivery plan has not been changed to match.

### What this would change in the worklist

| Item | Change |
| --- | --- |
| C1, C2, C3, A10 | Hold the tuning work. Spike S1 first; whatever survives the spike is the real list. |
| C5 | Becomes the acceptance test for S2, not a document of today's gaps. |
| D1 | Widen to S3 and move ahead of the 1.4 component-parity issues. |
| D7 | Replace on-demand loading with a core/extras bundle split. |
| B3 | Blocked on the threat-model page. |
| Plan phases C and R4 | Replace with the `django.tasks` adapter unless a requirement it cannot meet is named. |
| Plan phases I, B, M, gate G1 | Fall away under S4, apart from the inventory command and a migration note. |

## Suggested next steps

1. Done 2026-11-11: E8 (one trunk, #4013) and E1 (the 1.4 work folded into 2.0).
2. Done 2026-11-11: `main` merged into `2.0` (#4010).
3. Done 2026-11-11: issues filed for sections A, C and D (#3987–#4007). Section B, the deferred list in F and the four simplifications have none.
4. Spike S1 (assignment-based change tracking) on the 1,000-row table case, before any work on C1–C3. One to two days should show whether the in-place-mutation problem is tractable.
5. Write the partial-root threat-model page, then decide B3.
6. Start S3 with the tags that have open parity issues, measuring the bridge against the native twin on each.
7. Rerun the baseline (B2) after the S1 spike and turn what remains of C1–C3 into numeric targets.
8. Fix section A on the trunk; none of it waits on the decisions above.
9. Fold the surviving sections into `docs/plans/2.0-delivery-plan.md`.

## Limits of this consolidation

- Reading and comparison only. No benchmark, browser run or test was repeated.
- "Still present" means the cited pattern still greps on that branch, not that the behaviour was reproduced.
- The private evidence archive behind the Claude reports was not reopened; this document cites the public copies and keeps downstream apps anonymous.
- Issue coverage was checked by keyword search, which can miss an issue filed under different wording.
- The review section is judgment. Its two source checks were `StateProperty.__set__` and file sizes on `origin/2.0` at `832b2c67b`; the "third of 1.4 issues" figure is a count by title.
