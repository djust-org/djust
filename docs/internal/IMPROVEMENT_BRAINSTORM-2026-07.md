# djust improvement brainstorm — July 2026

A grounded brainstorm of framework improvements, written at the v1.1.0rc8 cut
while the open-issue backlog is nearly drained (6 open issues). Every idea
cites the evidence that motivated it — session arcs, filed issues, or
downstream-consumer lessons (`DJUST_LESSONS.md`, USAI build) — so future
triage can judge each on its provenance, not its prose. Manifesto filter
applied throughout: no idea below adds a build step, an npm dependency, or a
second source of truth.

**How to use this doc**: pick ideas into ROADMAP drain buckets (`v1.1.0-N`)
or file as issues; strike through ideas as they ship or get rejected, with a
one-line reason. This is a living scratchpad, not a commitment.

---

## Priority shortlist (highest leverage / evidence-strength first)

| # | Idea | Category | Effort | Why now |
|---|------|----------|--------|---------|
| 1 | Style-vocabulary validation at theme registration | Theming | S | Turns a silent-no-op class into startup errors — caught live in PR #2056 review |
| 2 | `#1848` durable fix: classic-`<script>` re-execution on mount morph (or loud system check) | Client | M | Recurring downstream trap; bit djust.org's own examples page |
| 3 | WS-path parity system check for HTTP-only mixins | Core | M | The single most-reported downstream footgun (TenantMixin class) |
| 4 | Official adapters milestone (`dj-chart`, `dj-sortable`, `dj-editor`) | Ecosystem | L | ADR-025 sockets shipped; adapters were the explicitly deferred payoff |
| 5 | WCAG contrast gate extended to ALL presets | Theming | S–M | 5/68 themes are gated today; the machinery already exists |
| 6 | `make doctor` — dev-environment self-diagnosis | DX | M | PyO3/venv env drift caused three incidents in one week on one machine |
| 7 | Loop render+parse cache: graduate `#1970` flag to default-ON | Performance | S | Shipped OFF by design (soak); measured 16–22% render_with_diff wins |
| 8 | Reference-surface manifest + djust.org catalog drift check | Docs | M | The hub silently missed an entire feature family (JS commands) for months |

---

## 1. Extension ecosystem (post-ADR-025)

### 1.1 Official adapters — the deferred "C" milestone
**Problem**: integrating Chart.js/Sortable/Tiptap/Leaflet requires the same
hand-written dance every time (dj-hook + `dj-update="ignore"` + teardown on
`destroyed()` + `dj-mutation` bridging), and the #1724 canvas-teardown class
shows people get it wrong.
**Evidence**: ADR-025 explicitly deferred adapters until the sockets proved
out; both sockets shipped in v1.1.0rc8 (PRs #2051/#2052) with recipes docs.
**Proposal**: one-file, no-build adapter modules shipped in the wheel
(`dj-chart`, `dj-sortable`, `dj-editor` first), enabled via
`DJUST_CONFIG["extensions"]`; user brings the library, djust ships the
morph-safe glue (a pre-written hook + pre-registered `JS.ext` commands).
Each adapter ≤ ~80 lines + its own test file; each is an ongoing maintenance
commitment, so start with exactly one (Chart.js — the known pain) and let
demand justify the rest.
**Effort**: L (as a milestone) / M (single pilot adapter).

### 1.2 `#1848` durable fix — inline `<script>` inside dj-root
**Problem**: page JS inside the reactive root silently never executes after
the mount morph — no error, no warning; the "reload works, navigation
doesn't" trap. Documented as a workaround (put JS in a block outside
dj-root), but the framework still lets you walk into it.
**Evidence**: issue #1848; djust.org's own examples page shipped this bug
during the 1.0.7 upgrade; hooks.md now carries a whole section warning about
it.
**Proposal**: either (a) re-execute classic scripts encountered during the
mount morph (mirroring what `live_redirect` already does per #1635/#1650), or
(b) if (a) is too risky, a client-side dev-mode console.error + a Django
system check (template scan for `<script>` inside `dj-root` blocks) so the
trap is at least loud. Do (b) immediately regardless; (a) needs an ADR-sized
look at double-execution semantics.
**Effort**: S for (b), M for (a).

### 1.3 Hook values v2 — enumeration + write-back
**Problem**: `this.values` is named-key-only (no `ownKeys` trap — `Object.keys`
/spread silently yield `{}`) and read-only. Both were deliberate v1 scope
cuts, now documented; the docs example nudges users toward
`renderChart(this.el, this.values)`, which works, but the natural next step
(`{...this.values}`) doesn't.
**Evidence**: PR #2052 review Minors; the caveat sentence shipped in
hooks.md; reserved-names follow-up is #2055.
**Proposal**: add `ownKeys`/`getOwnPropertyDescriptor` traps (filter
`el.attributes` on the `dj-hook-value-` prefix, camelize — ~10 lines), and
consider opt-in write-back (`this.values.x = y` sets the attribute) matching
Stimulus semantics. Ship the traps first; write-back needs a think about
morph-conflict semantics (a client-written attribute the server then morphs).
**Effort**: S (traps) / M (write-back).

---

## 2. Framework core — WS/HTTP lifecycle parity

### 2.1 System check for HTTP-only mixins on LiveViews
**Problem**: any mixin hooking `dispatch()`/`get()`/`post()` (TenantMixin,
rate limiters, custom auth) silently does nothing on the WebSocket path —
`handle_mount` calls `view.mount()` directly. Downstream symptom pattern:
`self._tenant = None` in handlers, empty querysets, writes that no-op.
**Evidence**: the single most-documented gotcha across downstream consumers
(djust-monitor, USAI build — see workspace `DJUST_LESSONS`); the ADR-022
convergence made the mount path uniform, which makes this now *checkable*.
**Proposal**: a `V0xx` system check: for each registered LiveView, walk the
MRO for classes overriding `dispatch`/`get`/`post` that aren't django.views
bases, and warn "X.dispatch will not run on WebSocket mounts — move setup to
`mount()` or `run_pre_mount_auth`-style hooks". Plus a documented,
supported `ws_setup(request)` extension point if one doesn't already fall out
of `run_pre_mount_auth`.
**Effort**: M. High leverage — converts a silent production bug class into a
startup warning.

### 2.2 Sticky-child LiveView state persistence (`#1471`)
**Problem**: LiveComponents persist across reconnects via the parent-save
path; sticky-child LiveViews (`{% live_render %}`) do not — an acknowledged
architectural gap.
**Evidence**: #1471 (filed during the #1467 investigation); the
LiveComponent-vs-sticky-child routing distinction is canonized in CLAUDE.md.
**Proposal**: extend the session-save block to serialize sticky-child state
keyed by `view_id`, restoring in `StickyChildRegistry` on re-mount. Needs the
same signed-snapshot treatment as parent state (ADR-018 ground).
**Effort**: L. Prerequisite reading: ADR-018.

---

## 3. Client bundle & performance

### 3.1 Bundle diet via opt-in module splitting
**Problem**: the minified bundle is ~58 KB gz / ~48.6 KB br and every page
pays for every feature — virtual lists, uploads, cursor overlay, tutorial
bubble, colocated hooks — whether used or not. (The CLAUDE.md "~37 KB gz
pre-minified target for v0.6.0" long predates the current feature set and
should be retired or restated.)
**Proposal**: keep ONE file as the default (manifesto: no build step), but
generate a second artifact at framework-build time: `client-core.min.js`
(transport, VDOM, events, loading, navigation) plus per-feature modules
loadable via `{% djust_client_config %}`-driven `<script>` tags. Purely
additive; the fat bundle stays the default. Measure first: a per-module
gz-size manifest emitted by `build-client.sh` so the split is data-driven.
**Effort**: M–L. Do the measurement manifest (S) regardless.

### 3.2 Graduate the loop render+parse cache to default-ON
**Problem**: `LIVEVIEW_CONFIG['loop_render_cache_enabled']` (#1967/#1969/#1970)
ships default-OFF per split-foundation discipline, leaving measured
15–22% `render_with_diff` wins on the table for everyone who doesn't know
the flag exists.
**Evidence**: CHANGELOG v1.1.0rc5 entries; byte-identity ON==OFF proven
across the full template matrix with gate-off verification.
**Proposal**: flip the default in the next minor after one more release of
soak; keep the flag as an opt-OUT for one release; add a CHANGELOG migration
note. Precondition: re-run the byte-identity suite + benchmarks at flip time.
**Effort**: S (the work was done up front; this is a soak-then-flip).

### 3.3 Stop committing compressed bundle siblings (`#2054`)
**Problem**: `.gz`/`.br` artifacts churn between contributor machines
(toolchain drift), polluting every JS-touching PR diff.
**Evidence**: #2054; observed in PR #2051 (brotli +998 B with byte-identical
source).
**Proposal**: generate compressed siblings at wheel-build/release time only;
pre-commit hook stops regenerating them. One decision to make: whether any
dev-server path serves the committed `.gz` directly (grep suggests
WhiteNoise handles compression downstream anyway).
**Effort**: S.

---

## 4. Theming (post five-themes build)

### 4.1 Style-vocabulary validation at registration
**Problem**: design-system style strings are free-form — `card_hover="glow"`
compiled, tested green, and *visually appeared to work* (via the parallel
animation path) while being a silent no-op on its own seam. Only a human
tracing the CSS generator's if/elif chain caught it.
**Evidence**: PR #2056 review (Important finding); `_types.py` documents
vocabularies that nothing enforces.
**Proposal**: single source of truth per field (a `VALID_*` frozenset next to
each style dataclass), enforced in `__post_init__` (or a `check_styles()`
called by registration + a system check for user themes). The #2056 class
becomes an ImportError at authoring time. Also fixes the doc/enforcement
drift for `icon.weight` etc.
**Effort**: S. Highest ratio on this list.

### 4.2 WCAG gate for ALL presets
**Problem**: the contrast matrix (6 pairs × 2 modes, AA) now guards 5 of 68
presets; the other 63 have no automated accessibility floor, and some legacy
palettes likely fail.
**Evidence**: `test_theming_new_themes_v11.py` built the machinery; the
five-theme build caught 8 misses in freshly-authored palettes — the odds the
legacy 63 are all clean are low.
**Proposal**: run the matrix over all presets as a report first (not a
gate); fix or explicitly exempt (with a documented `a11y_exemptions` list +
reasons) the failures; then promote to a gating test. Pair with the existing
`high_contrast` machinery for auto-suggested fixes.
**Effort**: S to report, M to drive to zero.

### 4.3 Theme authoring pipeline: seed → scaffold → validate
**Problem**: authoring a theme means hand-writing ~224 lines × 36 tokens × 2
modes; contrast tuning is trial-and-error unless you know to script the
`AccessibilityValidator`.
**Evidence**: the five-themes build did exactly this by hand; a
`create-theme`/`validate-theme` command surface already exists
(`djust_theme` management command; `test_theming_create_theme_command.py`).
**Proposal**: extend the scaffold to accept a 4–5 color seed
(`--primary "#e35" --bg warm --mode dark`) and generate a full module with
WCAG-adjusted derived tokens (nudging lightness until pairs clear 4.5, the
exact loop used manually in PR #2056); `validate` runs vocabulary + contrast
+ registration checks on a user theme file.
**Effort**: M. Makes "epic theme in 10 minutes" a marketing line.

### 4.4 Visual regression harness for the gallery
**Problem**: JSDOM tests pin CSS *content*, not rendered pixels; the class of
"token renders but looks wrong" (or the #1988 flex-crush class) is only
caught by eyeballs.
**Evidence**: repeated "real-browser pixel verification is a recommended
manual follow-up" notes in CHANGELOG entries; the djust-browser MCP +
playwright infrastructure already exists in CI (browser-smoke, playwright
jobs).
**Proposal**: a nightly (not per-PR) playwright job: render the gallery's
component storybook for every preset × mode, screenshot, and diff against
committed baselines with a tolerance. Start with the 5 QA-combo presets to
size the flake rate before scaling to 68.
**Effort**: M–L (baseline management is the real cost).

---

## 5. Testing, CI, and dev-environment robustness

### 5.1 Root-cause the regroup xdist pollution (`#2053`)
**Problem**: 9 `test_regroup_tag.py` tests fail under some pytest-xdist
orderings on at least one dev machine (pass in isolation, CI green) — the
suspected global assign-tag-handler registry clobber makes local full-suite
runs untrustworthy, which erodes the pre-push gate's authority.
**Evidence**: #2053; reproduced on clean main during the ADR-025 arc.
**Proposal**: bisect the polluting sibling (worker logs / random-order), then
isolate: re-register the regroup handler in test setup, or give the Rust
registry a test-scoped reset. Apply the #182 three-clean-runs gate to the fix.
**Effort**: M (bisection is the unknown).

### 5.2 `make doctor` — dev-environment self-diagnosis
**Problem**: local environment drift silently breaks trust in local gates.
One machine, one week: `djust.pth` repointed at a deleted worktree (import
breakage + stale `.so`), then embedded-PyO3 test binaries failing
`init_fs_encoding` bootstrap, then pyo3-ffi failing to even *compile*
(SIGABRT in the build probe) — while CI stayed green throughout.
**Evidence**: this session + the worktree-subagent memory; pre-push hook
hardcodes `.venv/bin/python` and fails in worktrees (#1796).
**Proposal**: a `make doctor` target that checks and reports: `djust.pth`
points into THIS checkout; `import djust` works from the venv; `.so` mtime vs
newest crate source (stale-extension detection); embedded-Python smoke
(`cargo test -p djust_templates --test <one PyO3 test>`); node_modules
present; hook health. Each failure prints its one-line fix (usually
`uv run maturin develop`). Cheap insurance against hours of misdiagnosis.
**Effort**: M.

### 5.3 Known-flake quarantine protocol
**Problem**: the release gate keeps tripping over two *documented*
machine-only failures (regroup xdist, PyO3 env), forcing per-release manual
re-derivation of "is this the known flake or a real regression?"
**Evidence**: the rc7 cut (Rust=1 rode the gate unnoticed), the rc8 cut
(same test, now compile-time), this session's repeated standalone-re-run
ritual.
**Proposal**: a `KNOWN_FLAKES.md` (or pytest marker + cargo skip-list) with
per-entry: symptom signature, proof-of-environment procedure (the standalone
re-run), linked issue, expiry date. `make test` prints "N failures match
known-flake signatures (see KNOWN_FLAKES.md)" instead of a bare red. Expiry
dates force periodic re-justification so the list can't rot into a
suppression dump.
**Effort**: S.

### 5.4 Cross-theme QA matrix breadth
**Problem**: `test_theming_cross_theme_qa.py` runs 5 curated preset×design
combos; 68 presets exist. New themes get zero QA-suite coverage unless they
hand-add themselves (the five-themes PR wrote a parallel suite instead).
**Proposal**: keep the curated 5 for per-PR speed; add a
`pytest -m all_theme_qa` parametrization over every registered preset that
runs nightly/weekly in CI only. Merge the new-themes test's
registration/contrast checks into it so future themes are covered by
construction.
**Effort**: S–M.

---

## 6. Observability & debugging

### 6.1 Finish the bug-capture arc (`#1562`, `#1561`)
**Problem**: iter A (capture) shipped; the replay viewer (iter B) and the
Redis store + CLI + PII scrub (iter C) are the two oldest open issues in the
repo.
**Proposal**: schedule iter B into the next drain bucket — a read-only replay
viewer is the piece that makes captures actionable for downstream users
filing framework bugs (it directly feeds the "bit-exact runnable repro"
canon requirement).
**Effort**: M (B), L (C).

### 6.2 Debug-panel: WS mount-refusal + silent-gate surfacing
**Problem**: several documented traps are *silent* precisely because their
failure surface is a WS frame the user never sees (mount refusals from the
view allowlist, `dj-view` stale refs, event routing to a missing handler).
**Evidence**: the 1.0.7 upgrade's "click does nothing, no error" diagnosis
recipe in canon; the F22 mount-refusal incident.
**Proposal**: the (now dockable, PR #2040) debug panel gains a "framework
events" lane showing mount refusals, unknown-event errors, dropped frames,
and unknown `ext.*` commands — everything the framework already knows went
wrong but only logs server-side. Dev-mode only.
**Effort**: M.

---

## 7. The deferred reactivity question (gap "A")

### 7.1 Minimal ephemeral client state — an ADR-worthy revisit, not a default yes
**Problem**: the 20% of Alpine's use cases not covered by JS commands +
dj-* behaviors (multi-element coordinated ephemeral state: wizards with
client-only steps, complex disclosure groups) still push some users toward
hand-rolled hook state or (worse) bolting Alpine on and fighting the morph.
**Evidence**: the ADR-025 gap survey; the morph-conflict bug class
(#1988/#1989/#2033/#1724) that motivated rejecting full Alpine remains the
constraint.
**Proposal**: do NOT build yet. Write the ADR only when a concrete downstream
app demonstrates the need twice: scope would be a `dj-data`/`dj-show`/
`dj-class` trio, property references only (no expression language), state
stored in attributes so the morph can be made aware of it by construction.
Record here so the next "should we add Alpine?" conversation starts from
this analysis instead of zero.
**Effort**: ADR first; implementation M if ever approved.

---

## 8. Docs & adoption

### 8.1 Reference-surface manifest → djust.org drift check
**Problem**: djust.org's `/docs/directives/` DB catalog silently lacked the
entire JS-commands family for months, and the two new hook attributes until
manually noticed; nothing detects framework-surface vs marketing-reference
drift.
**Evidence**: djust.org PR #52 closed the gap by hand this week.
**Proposal**: the framework emits a machine-readable surface manifest
(directive names, JS.* commands, view-API names — mostly derivable from
existing registries + the d.ts); djust.org CI diffs its reference fixture
against the manifest of the pinned djust version and fails with a list of
missing/stale entries. Kills this drift class permanently (#1646, applied to
docs).
**Effort**: M (cross-repo).

### 8.2 Modernize hooks-era docs (`#2055`)
Already filed: hooks.md still teaches `JSON.parse(this.el.dataset.*)` in its
flagship examples after the typed-values section obsoleted it; reserved-name
shadowing needs a documented list; the shipped `djust.d.ts` header still says
`@version 0.3.4`.
**Effort**: S.

### 8.3 "Build an app in 30 minutes with an AI pair" flagship tutorial
**Problem**: "AI-Ready by Design" is manifesto point 3, but no doc actually
demonstrates the claim end-to-end; the pieces (secure defaults, system
checks, `djust new`, typed hooks, `djust.d.ts` for editor AI) all exist.
**Proposal**: one long-form tutorial (docs.djust.org + a blog cross-post)
building a real app with an AI assistant, showcasing exactly the guardrails
that make generated code trustable — system checks catching the AI's
mistakes on camera is the differentiating content.
**Effort**: M (content, not code).

---

## Explicitly not proposed

- **Bundling Alpine/Stimulus/htmx** — re-litigated and rejected in ADR-025's
  build-vs-integrate analysis (cross-maintainer morph coupling; manifesto).
- **A CSS framework/design opinions** — manifesto point 7.
- **Kubernetes-style config surface for theming** — 68 presets × packs ×
  design systems is already a large matrix; validation (4.1) before growth.
