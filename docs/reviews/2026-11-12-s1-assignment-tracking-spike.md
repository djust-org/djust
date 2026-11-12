# S1 spike: detect state changes by assignment

A time-boxed spike on simplification S1 of the
[consolidated 2.0 review](2026-11-11-djust-2-consolidated.md): should djust 2.0
find state changes from assignment (`StateProperty.__set__`) instead of
comparing state before and after each event?

Measured 2026-11-11. Source citations are to `main` at `372b01dc3`. The
prototype was built on `a4de60001`; none of the cited files differ between the
two. The prototype is on the branch `spike/s1-assignment-tracking`
(`dd1e33378`) and is not for merging.

## Recommendation

**Adopt in part.** Use assignment as the change signal for declared `state()`
fields in the two places where a field name is enough, keep comparison for the
things assignment cannot name, and answer in-place mutation by fingerprinting
only the container fields a handler read, with reassignment or an explicit
`touch()` as the way to skip that walk.

The two places:

1. **The render gate** (did this handler change anything?). Declared slots
   stop being walked before and after the handler. Everything that is not a
   declared slot is still snapshotted the old way, which is cheap.
2. **The Rust state sync**, for a context value that *is* the object a
   declared field holds (`items=self.items`). It was changed exactly when the
   field was assigned, so it is neither fingerprinted nor is a fingerprint of
   it kept between turns.

What S1 cannot do alone, and the reason it is "in part": under explicit
exposure the render context does not come from state names. It is whatever the
view's `get_context_data` returns (`mixins/context.py:897`), so a set of
changed field names does not say which context keys changed. A derived value
(`visible=[r for r in self.items if r["active"]]`) still has to be compared.
The assigned-names set also knows nothing about undeclared attributes, form and
wizard provider keys, component state, action state or streams.

### The numbers behind it

One WebSocket event turn (send the event, receive the reply frame) on an
explicit view holding a 1,000-row list in declared state. Median of 200 rounds
per run; three runs each; the figure is the middle run.

| Case | Today | Switch on | What the switch-on figure needs |
| --- | --- | --- | --- |
| (a) handler changes one scalar field | 22.6 ms | 4.7 ms | nothing |
| (b) handler changes one row, by assigning a new list | 34.1 ms | 12.3 ms | nothing |
| (b) handler changes one row in place | 34.2 ms | not rendered (`noop`, 1.9 ms) | see below |
| (b) in place, then `self.touch("items")` | 34.7 ms | 12.1 ms | the developer writes `touch` |
| (b) in place, read-fingerprint mode | 34.2 ms | 22.8 ms | nothing |

The same view with 10 rows takes 3.1 ms today and 2.9 ms with the switch on
(case a), so about 3 ms of every turn is fixed and S1 does not touch it.

The machine was shared with other agents' builds for the whole session (load
average between 7 and 22 on 12 cores). Run-to-run spread of the medians was
22.3–22.7 ms and 4.7–4.9 ms for case (a), 33.5–34.9 ms and 11.8–13.1 ms for
case (b) by assignment. One earlier switch-on run taken during a load spike
gave 14.7 ms for case (a); it is not in the table.

## 1. Consumers of change detection

Found by searching for the sinks (`_snapshot_assigns`, `_compute_changed_keys`,
`_changed_keys`, `deep_fingerprint`, `fingerprints_by_content`,
`fingerprint_and_convert`, `_prev_context_*`, `_dirty_baseline`,
`_reactive_state`, `_force_full_html`, `_skip_render`, `_stream_epoch`), not for
expected callers.

One fact frames the whole table. `_reactive_state`, the set
`StateProperty.__set__` adds to (`_state.py:76-81`), is not read by any
change-detection code today. It only grows and is never cleared per turn. The
other uses of the name are in the private-attribute bookkeeping
(`live_view.py:1306`, `:1334`, `:1367`, `:1417`).

"Names enough?" asks whether a set of declared field names recorded at
assignment would satisfy the consumer.

### Explicit exposure (and both policies)

| Site | Path | What it needs | Names enough? |
| --- | --- | --- | --- |
| `websocket.py:414` `_snapshot_assigns`; walk at `:475` | every gate below | a comparable snapshot of every non-framework attribute: scalars by value, containers by deep fingerprint, other objects by `id()`; plus the stream epoch (`:495-497`) | No. It also covers undeclared attributes, provider keys, component state and streams |
| `runtime.py:5078` (pre), `:5199-5203` (post) | runtime event spine, WebSocket and SSE | did anything change; then `view._changed_keys`, a set of storage names such as `_state_count` | Yes for declared fields; no for the rest |
| `runtime.py:5195-5197` | same | explicit child views force a render because the snapshot cannot see child state | Not a name question |
| `runtime.py:5079`, `:5216-5218` | same, push-only auto-skip | identity of every attribute before and after | Yes for reassignment; an in-place change is already invisible to it |
| `runtime.py:5303`, `:6630` `_scoped_component_for` | scoped component render | that exactly one changed key is a component slot | No. Component slots are not `state()` fields |
| `runtime.py:5464`, `:5521-5525`; `websocket.py:2229`, `:2278-2282` | deferred re-dispatch, both twins | same as the spine | Same as the spine |
| `runtime.py:6506-6510`, `:6608-6609` | component event | did the parent or the component State change | No (component State) |
| `runtime.py:3186-3187` `_context_changed_besides`; `:8236-8241` | scoped component render | compares the derived context to the retained `_prev_context_*` values; writes a fingerprint back | No. It compares context values |
| `runtime.py:9343`, `:9352`; `websocket.py:6781`, `:6791`, `:6802`, `:6845` | tick, SSE and WebSocket | did anything change | Yes for declared fields; no for the rest |
| `mixins/request.py:2169`, `:2287` | HTTP fallback | did anything change, for one dropdown observation only; otherwise the fallback always renders | Same as the spine |
| `api/dispatch.py:436-437`, `:499-505` | API dispatch | for explicit views, the before and after `client=True` projection compared by value; the snapshot at `:436` is taken and not used | Yes for reassignment. The comparison is by value, so it also sees in-place change |
| `mixins/rust_bridge.py:1048` to `:1211` `_sync_state_to_rust` | every render | which **context keys** changed: per key, new key, dirty flag, container fingerprint against `_prev_context_fingerprints` (`:1148-1150`, `:1084`), scalar by value, else `id()`; retains three dicts between turns (`:1196-1211`) | Only for a context value that is a declared field's object. Not for derived values, provider keys or context-processor keys |
| `mixins/rust_bridge.py:1107-1110` `_collect_sub_ids` (`:210`) | same, when `_changed_keys` is set | the `id()` of every object reachable from each changed attribute, so that a context value sharing an inner object is re-sent | Replaced by the identity rule for declared values; not otherwise |
| `mixins/rust_bridge.py:1489` | same | theming's forced context keys (`theming/mixins.py:244`) | Not a state question |
| `mixins/rust_bridge.py:773` `set_changed_keys`; `components/base.py:1448` | the existing hatch | marks keys and forces a full-HTML render | It is itself a name signal |
| `live_view.py:1140`, `:1190`, `:1200`; captured at mount, `runtime.py:4212-4213` | dirty tracking (`changed_fields` `:1221`, `is_dirty` `:1241`) | a retained fingerprint of every public field since the last `mark_clean()` | Partly. Names assigned since `mark_clean()` would do, with different semantics: assign-and-revert would read as dirty |
| `decorators.py:876-879` | `@computed` cache key | a fingerprint of each dependency | Partly. A per-field assignment counter would do for declared dependencies |
| `_exposure_sessions.py:191-195`; called from `runtime.py:5176`, `mixins/request.py:2253` | server-state save | nothing today: it projects and saves every declared server field on every turn | A changed-names set could gate the save. Not prototyped |
| `mixins/streams.py:50` | streams | bumps `_stream_epoch`, which the snapshot reads | Not a state question |

Two producers do not record on assignment today and would have to. The form
provider's `PersistedFormInput.__set__` (`forms.py:123-134`) overrides the
descriptor's `__set__` without calling it. Provider keys such as `form_data`
and the wizard step are plain attributes.

### Legacy-only sites (ignored, as instructed)

These are reachable only when `uses_legacy_exposure(view)` is true and go away
with legacy exposure:

- `runtime.py:5167-5172`: the legacy state save after an event.
- `runtime.py:5231` with `runtime.py:7386-7393`: the legacy no-op snapshot,
  which takes a third `_snapshot_assigns`.
- `runtime.py:5291`: the legacy event snapshot.
- `runtime.py:6515-6516` and `:6627`: the component-event legacy snapshot.
- `websocket.py:2299-2300`: the legacy save in the WebSocket re-dispatch twin
  (the explicit branch is `:2288`).
- `mixins/request.py:2219`: the legacy session save in the HTTP fallback.
- `api/dispatch.py:506-509`: the snapshot diff in API dispatch.
- `time_travel.py:170-172`: `_capture_snapshot_state`; explicit views use the
  debug projection at `:167-169`.

### How many times one list is walked today

For a declared list of dicts that the handler changes and the context passes
through, one event turn on the runtime spine walks it four times in Python:

1. the pre-handler snapshot (`runtime.py:5078`),
2. the post-handler snapshot (`runtime.py:5199`),
3. `_collect_sub_ids` (`mixins/rust_bridge.py:1110`),
4. the sync fingerprint, fused with the conversion for Rust because the key is
   being sent (`mixins/rust_bridge.py:1208`).

A turn that does not change the list still pays three: 1, 2 and a fingerprint
without the conversion (`mixins/rust_bridge.py:1149`). The phase
breakdown below matches this: two `_snapshot_assigns` calls and one
fingerprint call in case (a); two snapshot calls, 11,001 `_collect_sub_ids`
calls and one fused walk in case (b).

## 2. Cost today

### Method

- Script: `tests/benchmarks/s1_spike/s1_bench.py` on the spike branch. It
  reuses the WebSocket harness and the row shape of
  `tests/benchmarks/test_exposure_cost.py` (a real `LiveViewConsumer` through
  `WebsocketCommunicator`, database-backed session, `DEBUG=False`, rate limit
  lifted), with 1,000 rows in place of 50.
- The view declares `title`, `count` and `items` with `state()` and no
  `persist`, and returns all three from `get_context_data`. The template
  renders four cells for each row; the page is 116,150 characters.
- A row has seven fields, one of them a three-item list. The whole list is
  18,001 fingerprint nodes, under the 20,000-node budget
  (`change_detection.py:43`), so the fingerprint is complete and not truncated.
  Its JSON form is 146,317 bytes.
- 20 warm-up rounds, then 200 measured rounds per case; medians. Three runs
  with the switch off and three with it on, interleaved.
- Phases are timed by wrapping the Python functions named below. Recursive
  functions are timed at the outermost call only. The Rust figures are the
  extension's own `get_render_timing()`.
- Release build of the extension (`maturin develop --release`,
  ``Finished `release` profile [optimized]``). The built file is 10.3 MB, not
  the roughly 6 MB the setup notes expect; it is not a debug build.
- Apple M2 Max, 12 cores, Python 3.12.13, shared with other builds.

### Phase breakdown, switch off (first of three runs)

| Phase | (a) one scalar | (b) one row |
| --- | --- | --- |
| Whole turn | 22.29 ms | 33.50 ms |
| `_snapshot_assigns`, two calls | 10.59 ms | 11.27 ms |
| `_sync_state_to_rust`, total | 5.11 ms | 13.91 ms |
| of which fingerprint (`deep_fingerprint` or the fused walk) | 3.33 ms | 4.69 ms |
| of which `_collect_sub_ids` | 0.00 ms | 3.47 ms |
| of which everything else in the sync | 1.78 ms | 5.75 ms |
| `render_with_diff`, total, including the sync above | 6.84 ms | 17.61 ms |
| of which Rust (`get_render_timing` total) | 1.14 ms | 3.05 ms |
| Rust split: template render / HTML parse / serialize | 0.02 / 0.80 / 0.30 ms | 1.91 / 0.80 / 0.30 ms |
| `save_server_state` (nothing to persist) | 0.19 ms | 0.19 ms |
| Not attributed (turn minus snapshot, render and save) | 4.67 ms | 4.43 ms |

Case (b) in place measures the same as case (b) by assignment (34.1 ms against
33.5 ms in this run), because today both are found by the same walks.

Three things scale with the size of the state and are not change detection:

- **Sending the list to Rust.** A `cProfile` run of case (b) puts
  `RustLiveView.update_state` at 3.8 ms a call (0.189 s over 50 calls) and the
  extension's `render_with_diff` at 3.5 ms a call. Profiler figures are
  inflated for Python frames, less so for these two built-in calls.
- **Rust parsing the full HTML** on every turn: 0.80 ms even in case (a),
  where no row changed.
- **About 2.7 ms that I could not attribute.** The unattributed part is 4.7 ms
  at 1,000 rows and 2.0 ms at 10 rows with the switch off, and 2.0 ms at
  1,000 rows with it on. It scales with state size today and disappears with
  the walks. I did not find where it is spent; the allocation of three
  18,001-node tuple trees per turn is the obvious suspect and is not verified.

### Retained memory

`deep_fingerprint` of the 1,000-row list allocates 1,643,392 bytes
(`tracemalloc`), eleven times the list's JSON size. Two copies are kept for
every mounted view: one in `_prev_context_fingerprints`
(`mixins/rust_bridge.py:1207-1211`) and one in `_dirty_baseline`
(`live_view.py:1200`, captured at mount). I measured one tree and did not
measure a mounted view's total.

## 3. Prototype

### What it does

Behind `djust._state.ASSIGNMENT_TRACKING`, default off, for explicit views
only:

- `StateProperty.__set__` records the field name in two sets on the view: one
  cleared by the event spine before each handler, one cleared by each Rust
  sync. Assigning an equal scalar records nothing. Assigning a container always
  records, including the same object, so `self.items = self.items` reads as
  "I changed this in place".
- The runtime event spine (`_dispatch_event_render`) snapshots everything
  except declared slots, and renders if that snapshot changed or any name was
  assigned.
- `_sync_state_to_rust` treats a structural context value that is the object
  held by a declared slot as changed exactly when its field was assigned since
  the last sync. It takes no fingerprint of it, keeps none, and skips
  `_collect_sub_ids`. Derived containers and scalars are compared as before.
- `LiveView.touch(*names)` records names by hand.

Only the runtime event spine's gate was converted. The deferred re-dispatch
twins, component events, tick, the HTTP fallback and API dispatch still use
the old snapshot, while the sync change applies on every path.

### Phase breakdown, switch on (first of three runs)

| Phase | (a) one scalar | (b) one row, by assignment |
| --- | --- | --- |
| Whole turn | 4.72 ms | 12.28 ms |
| `_snapshot_assigns`, two calls, declared slots skipped | 0.06 ms | 0.07 ms |
| `_sync_state_to_rust`, total | 0.81 ms | 5.76 ms |
| of which the conversion walk for Rust | 0.00 ms | 0.95 ms |
| `render_with_diff`, total | 2.50 ms | 9.39 ms |
| of which Rust | 1.11 ms | 3.02 ms |
| Not attributed | 2.00 ms | 2.64 ms |

What is left of case (b) is the cost of treating the list as one value: one
Python walk to convert it, `update_state` marshalling 1,000 rows into Rust,
and Rust re-rendering the loop. None of it is change detection, and a field
name cannot reduce it.

### What broke

The test files for explicit exposure and state
(`python/djust/tests/test_exposure_*.py`, `test_state*.py`, 79 files) and the
23 files the consumers table points to were run once per prototype state. The
list is `tests/benchmarks/s1_spike/s1-testfiles.txt` on the spike branch: 1,942
tests.

**Switch on, final prototype: 1 failed, 1,934 passed, 7 skipped.**

| Class | Count | Tests |
| --- | --- | --- |
| In-place mutation | 0 | See the note below |
| A consumer that needs more than a name | 0 | Because the prototype kept the old snapshot for everything that is not a declared slot |
| A test that pins the old mechanism | 1 | `test_stream_only_parity_3639.py::TestCallSiteInventory::test_the_call_sites_are_exactly_the_ones_this_file_covers`, an inventory of `_snapshot_assigns` call sites. It fails with the switch off too, because the prototype adds call sites |
| A real bug in djust | 0 | Two side findings are listed under "Found and not fixed" |

Failures in earlier prototype states, all fixed in the prototype:

- Four tests that spy on `_snapshot_assigns` with a one-argument function
  (`test_event_loop_offload_3074.py::test_pre_event_snapshot_runs_with_the_handler`
  and `test_event_loop_ceiling_3095.py::test_an_event_frame_makes_no_connection_check_hop_with_the_pool`,
  two parameters each) failed with the switch **off**, because the first
  prototype always passed a second argument. Class: a test that pins the old
  mechanism. After that fix the full list was not rerun with the switch off;
  the four affected files were (89 passed, 1 failed, the inventory test).
- `test_exposure_invalidation.py::test_declared_state_change_is_detected_under_its_storage_name`
  failed because the prototype's bookkeeping set was visible to the unfiltered
  snapshot. Class: a prototype bug.
- Ten tests in `test_exposure_invalidation.py` failed for one run on a
  `KeyError` I introduced in the spine. Class: a prototype bug.

**In-place mutation produced no failure, and that is a gap in the tests, not
evidence of safety.** 183 of the tests reached the assignment-tracked spine and
371 reached the assignment-tracked sync (counted by a trace file during the
run before the bookkeeping fix). None of them mutates a declared `state()`
field in place and expects a render. `test_in_place_mutation_2664.py` declares
no `state()` field at all. The benchmark is the evidence: with the switch on,
the in-place handler is answered `noop` 200 times out of 200.

**Names only: 11 failed.** To see which consumers need more than a name, the
same list was run with the residual snapshot turned off, so the gate used the
assigned names and nothing else. Ten tests then received `noop` where they
expect a render, plus the inventory test:

| What changed without a `state()` assignment | Tests |
| --- | --- |
| An undeclared plain attribute read by `get_context_data` | `test_exposure_invalidation.py::test_opaque_attribute_dependency_rerenders_conservatively[explicit]`, `::test_policies_produce_identical_render_frames`, `test_exposure_entry_scope_diagnostics.py::test_ws_request_html_failure` (two parameters) |
| A provider's tracked key | `test_exposure_invalidation.py::test_provider_tracked_key_change_rerenders_its_keys[explicit]`, `::test_provider_tracked_key_in_place_mutation_rerenders[explicit]` |
| Form state | `test_exposure_forms.py::test_password_value_never_reaches_session_frames_snapshot_or_debug`, `::test_validate_field_and_submit_form_render_errors_over_the_runtime[explicit]` |
| Action state | `test_exposure_action_errors.py::test_action_error_text_reaching_render` (two parameters) |

So the residual snapshot is required, not optional. It cost 0.06 ms in the
benchmark view, which has no undeclared attributes of any size; a view that
keeps a large undeclared container would still pay to walk it.

## 4. In-place mutation

### How common it is in this repository

Counted with `tests/benchmarks/s1_spike/s1_mutation_count.py`, an AST scan for
writes through `self.<public name>` in view-like classes: mutating container
methods, subscript assignment and deletion, and the nested forms.

| Tree | View-like classes | Classes that mutate in place | In-place sites | Assignment sites |
| --- | --- | --- | --- | --- |
| `examples/` | 137 | 24 | 64 | 1,070 |
| `python/djust/tests/`, `python/tests/`, `tests/` | 2,206 | 46 | 68 | 1,956 |

Of the 142 classes that declare a `state()` field, one mutates a declared
field in place, at one site. Almost all in-place mutation in the repository is
on plain attributes of legacy views, which become declared fields in 2.0. In
the examples that is about one view in six.

The scan undercounts. It does not follow an alias
(`rows = self.items; rows.append(x)`), a helper function, or a view class
built by `type()`. It does not read the Markdown documentation.

### The four options

All four were run through the benchmark; (i), (ii) and (iii) are selected in
the prototype by `DJUST_S1_MUTATION`. Figures for (i), (ii) and (iii) are one
run of 200 rounds each.

| Option | (a) one scalar | (b) in place | (b) by assignment | In-place change shown? |
| --- | --- | --- | --- | --- |
| Today (middle of three runs) | 22.6 ms | 34.2 ms | 34.1 ms | yes |
| None: `touch` or reassign (middle of three runs) | 4.7 ms | 12.1 ms with `touch` | 12.3 ms | only with `touch` |
| (i) tracked containers | 4.8 ms | 26.1 ms | 28.0 ms | yes |
| (ii) old walk kept, to warn | 15.9 ms | 13.5 ms, `noop` and a warning | 22.9 ms | no; warned |
| (iii) fingerprint what the handler read | 4.7 ms | 22.8 ms | 16.2 ms | yes |

**(i) Tracked container types returned by the descriptor.**
`python/djust/_s1_tracked.py` on the spike branch: `list` and `dict`
subclasses whose mutating methods record the field, built by copying the
value on assignment.

- Cost: slower than today's walks for the row cases in this prototype. The
  sync rises from 5.8 ms to 19.3 ms because the subclasses fall off the
  exact-type fast paths in the conversion for Rust, on top of the copy on
  every assignment. A real implementation would have to teach every exact-type
  check about the subclasses.
- What broke: three more tests, all on exact-type checks in the exposure
  layer. `test_exposure_child_mount.py::test_explicit_restore_keeps_legacy_decimal_tag_shapes_as_plain_json`
  raises "Unsupported state value; an explicit codec is required";
  `test_exposure_debug.py::test_bug_capture_reprojects_history_without_reading_current_state`
  and `::test_bug_capture_store_destination_holds_only_the_debug_projection`
  raise "Declared state value is missing".
- What it misses: any object that is not a `list` or `dict` (a set, a
  dataclass, a model instance); a list shared by two fields.
- What a developer has to learn: assignment copies. After
  `rows = build(); self.items = rows`, a later `rows.append(x)` changes a list
  the view no longer holds. `type(self.items) is list` is false. Both are
  surprising and neither fails loudly.

**(ii) Keep the fingerprint walk in `DEBUG` only, to warn.** In the prototype
the old pre and post snapshots still run and a warning is logged when a
declared slot changed without an assignment. The benchmark logged it on every
in-place round ("state field(s) items changed without an assignment; the page
was not updated").

- Cost: nothing in production; today's two snapshot walks in development
  (about 10 ms a turn on this view).
- What it misses: any handler path not exercised in development; a field over
  the fingerprint budget, where a truncated fingerprint cannot prove a change;
  a change inside a non-container object, which today's walk does not see
  either. In production the page is silently stale.
- What a developer has to learn: a new rule (reassign or `touch`), enforced
  only by a development-time warning.

**(iii) Fingerprint only container fields the handler read.**
`StateProperty.__get__` fingerprints a container field the first time a
handler reads it; after the handler, fields that were read and not assigned are
fingerprinted again and compared.

- Cost: nothing for a handler that reads no container field (case a, 4.7 ms).
  Two walks and a comparison for a handler that reads one and does not assign
  it (22.8 ms against 34.2 ms today). One walk for a handler that reads and
  then assigns (16.2 ms against 12.3 ms). A handler that only reads a large
  list, for a total say, pays the two walks for nothing; I did not measure
  that case separately.
- What broke: nothing beyond the inventory test.
- What it misses: a mutation through a reference taken outside the handler,
  such as a list also held on a private attribute or captured by a background
  task; a mutation made outside any handler window. The prototype arms it on
  the runtime event spine only. Tick, server push and async results would each
  need the same window.
- What a developer has to learn: nothing for the common case.
  `self.items.append(x)` keeps working. To skip the walk on a large field,
  assign it or call `touch`.

**(iv) An explicit `self.touch("items")` or reassignment rule with a system
check.** `touch` is in the prototype. The system check was not built; the
AST scan above is the same analysis.

- Cost: the best numbers (12.1 ms).
- What a check would miss: aliases, helper functions and mutation from another
  module, which is where the scan undercounts.
- What a developer has to learn: a rule that Django itself does not have, with
  silent staleness when it is forgotten in a place the check cannot see. One
  example view in six would need editing.

### Which one

(iii) as the default, with (iv)'s `touch` and reassignment as the documented
way to avoid the walk on a large field. It keeps today's behaviour for the
pattern the examples use, costs nothing on turns that do not read a container,
and is cheaper than today even when it does walk. (i) is slower than what it
replaces and changes what assignment means. (ii) and (iv) alone trade a
measured cost for stale pages nobody is told about.

## 5. What remains of C1–C3 and A10

- **C3** (one-cell change in a 1,000-row list). The walks go: 34.1 ms to
  12.3 ms by assignment, 22.8 ms in place under (iii). The fingerprint-reuse
  half of C3 has nothing left to reuse for a declared pass-through field. What
  survives is the remaining 12 ms, which is the price of re-sending and
  re-rendering the whole field for one cell: about 1 ms converting the list,
  about 3.8 ms in `update_state`, about 3 ms in Rust, about 2.6 ms fixed. A
  target below that needs a row-level update, which is a different piece of
  work from S1.
- **C2** (per-view memory; two retained fingerprint trees). One of the two
  trees is no longer kept for a declared pass-through value (1.6 MB for this
  list). `_dirty_baseline` is untouched by the prototype; it can be replaced
  by "names assigned since `mark_clean()`" if the change in meaning is
  accepted. Derived containers still keep a fingerprint. C2 shrinks to the
  dirty baseline and derived values.
- **C1** (explicit-mode turn cost and per-turn ceremony). Mostly untouched.
  The fixed part of a turn was 3.1 ms at 10 rows with the switch off and
  2.9 ms with it on. The one thing S1 gives C1 is a set that could gate the
  server-state save, which today runs on every turn. That gate was not built.
- **A10** (context-processor keys re-sent every turn). Untouched. Those keys
  are not state and nothing assigns them. The sync's fixed cost at 10 rows was
  0.76 ms with the switch off and 0.72 ms with it on. A10 stays as filed.

## What a real implementation needs that the prototype skipped

1. **Every turn path, converted together.** The gate was changed on the
   runtime event spine only and the sync on every path. On an unconverted path
   the old gate sees an in-place change to a pass-through list and renders,
   and the new sync then does not send it. The deferred re-dispatch twins,
   component events, tick, server push, async results, the HTTP fallback and
   API dispatch all need the same treatment.
2. **Recording at every write.** `PersistedFormInput.__set__` and any other
   descriptor that overrides `__set__`; state restored by writing a slot
   directly after a reconnect, a session restore or time travel.
3. **Threads.** `__set__` can run on a background thread while the spine
   clears the per-turn set. The prototype takes no lock.
4. **Dirty tracking and `@computed`.** Both still fingerprint. A per-field
   assignment counter would serve `@computed` dependencies.
5. **Derived context values.** Either keep the fingerprint for them, as the
   prototype does, or let a view declare what a derived value depends on.
6. **Decisions on meaning.** Whether assigning an equal value is a change.
   Whether reading a defaulted field in a handler should still force a render,
   as it does today by accident (below). What `changed_fields` means.
7. **Interplay not exercised.** `temporary_assigns`, `static_assigns`,
   `set_changed_keys`, `_force_full_html` and the 20,000-node budget, which
   stops applying to a pass-through field.
8. **Tests.** Nothing in the files run pins an in-place change to a declared
   field through the event spine. Those tests have to exist before the walks
   are removed.

## Found and not fixed

- **Reading a defaulted `state()` field counts as a change today.** The first
  read stores the default in the slot (`_state.py:73`), and the next snapshot
  reports the new slot as changed. Confirmed with a two-field view: reading
  both fields reported `_state_n` and `_state_rows` as changed.
- **`_reactive_state` is itself snapshotted.** It is not in
  `_FRAMEWORK_INTERNAL_ATTRS`, so the first assignment to any field also
  reports `_reactive_state` as changed. Confirmed in the same probe.
- **A 1,000-row list cannot be declared `persist="server"` with default
  limits.** With `persist="server"` on the three fields every turn of the
  benchmark returned an `error` frame, switch on or off. The projection raises
  "State projection exceeds its encoded size limit"; the default is 65,536
  bytes (`_exposure.py:184`) and the list is 146,317 bytes as JSON. A 100-row
  list projects. This matters to simplification S2, which would persist
  declared state by default.
- **API dispatch takes a snapshot it does not use** for explicit views
  (`api/dispatch.py:436`; the result is read only in the legacy branch at
  `:506-509`).
- **The release extension is 10.3 MB**, not the roughly 6 MB that setup notes
  use to tell a release build from a debug one.

## Limits

- One view shape, one machine, one session, on a machine busy with other
  builds. No absolute figure here is a target.
- Only the WebSocket runtime event spine was measured. Tick, SSE, the HTTP
  fallback, component events and API dispatch were read, not timed.
- The view passes its state straight through to the context, which is the
  best case for the identity rule. A view whose context is mostly derived
  containers would keep most of today's sync cost. That was not measured.
- No view with a large undeclared attribute, a component, a form or a stream
  was measured, so the residual snapshot's cost is known only for a view that
  has none.
- Models and querysets in state were not measured. They are leaves to the
  fingerprint today, compared by `id()`.
- The unattributed 2.7 ms was not located.
- Retained memory was measured for one fingerprint tree, not for a mounted
  view.
- The test run covered the 102 files listed, once per prototype state, not
  the whole suite. The consumers table was compiled by search and reading; the
  line citations were checked against `372b01dc3`, and a consumer reached
  through a name I did not search for would be missing.
- The system check in option (iv) and the save gate for C1 were described,
  not built.
- Browser behaviour was not checked. Every figure is server time to the reply
  frame.
