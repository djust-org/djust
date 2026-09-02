# ADR-027: Template variable resolution follows Django's lookup rules at one sink

**Status**: Proposed — implementation is the v1.2.0-2 row-2 issue (dormant-define → wire → flip → delete)
**Target version**: v1.2.0
**Date**: 2026-09-02
**Citations**: `file:line` references pinned to `main` at 5835bf97 (2026-09-02)
**Deciders**: Project maintainers
**Related**:
- [ADR-024](024-template-callable-auto-call.md) — auto-call in the sidecar walk; this ADR keeps every rule it added and retires the walk's terminal conversion
- [ADR-022](022-v1.1-code-quality-single-path-convergence.md) — the dormant-define → wire → flip playbook this follows
- #2535 (this ADR); open instances #2502 #2504 #2505 #2509 #2513 #2516 #2528; security pins #2506 #2507; wire/panic pins #2510 #2514 #2484 #2481; #2517 (scoreboard); #2532 (benchmark — its Stage-4 spike is the cost model below); #2536 (JIT list re-query, adjacent, not moved)

## Summary

djust converts the whole template context into a Rust `Value` tree before rendering and reaches
object attributes afterwards through a by-name sidecar that enumerates container shapes. That
conversion layer is where the v1.2.0-2 arc's open issues live. This ADR decides that an object with
no `Value` variant becomes a **live-object handle** (`Encoded::live`) resolved lazily at lookup
time by exactly Django's `_resolve_lookup` rules, at **one sink** in `crates/djust_core/src/context.rs`;
that Django models, managers and querysets **stay eager** (floored dict as the value, a by-name
proxied handle for misses); that the serialization floor holds at the lazy sink; and that the
implementation lands as dormant-define → wire → flip → delete behind a characterization-test net.
A maintainer can act on it by starting Step 1 of §Sequencing.

## Context

### The eager-conversion class

Every context value becomes a `djust_core::Value` (`crates/djust_core/src/lib.rs:220`; the
`Encoded` catch-all is `:409`) through `impl FromPyObject for Value` (`:2956`). The fallback block,
in order: the datetime spellings, a djust proxy's `__djust_serialize__` (`:3055`), a raw `Model`
routed through `normalize_django_value` (`:3073-3089`, #1986), `opaque_value` → `Value::Encoded`
(`:3111`), and last the **`__dict__` bulk-dump arm** (`:3123-3127`), reached exactly when
`opaque_gate` (`:3180`) declines a *truthy, non-iterable object with public attributes* (`:3228`).
The plain path (`DjustTemplateBackend`) converts the whole context in
`snapshot_context_to_value_hashmap` (`crates/djust_live/src/lib.rs:1813`, from `render_template`
`:1889` and `render_template_with_dirs` `:1945`); the LiveView path converts through `update_state`
(`:379`) after Python's `normalize_django_value` (`python/djust/serialization.py:1497`) has already
`str()`'d anything `crosses_as_encoded` would not carry (`:1853`).

The **sidecar** is keyed by TOP-LEVEL name only (`Context.raw_py_objects`,
`crates/djust_core/src/context.rs:198`): set by `set_raw_py_values` (`djust_live/src/lib.rs:420`)
from `_sync_state_to_rust` (`python/djust/mixins/rust_bridge.py:1010-1084`: everything not
`_JSON_FRIENDLY` `:1012-1024`, not a `BaseForm` `:1059`, plus the top-level models captured at
`:717-719`, each wrapped by `_protect_sidecar_value` `:1081-1084`), and on the plain path by
`entry_sidecar` → `build_render_sidecar` (`djust_live/src/lib.rs:1867-1871` → `serialization.py:1029`,
with the container descent `_protect_sidecar_tree` `:1125` capped at `_SIDECAR_MAX_DEPTH = 12`
`:1122`). The page-shell render wires **no** sidecar on either branch
(`python/djust/mixins/template.py:1079-1081`) — #2513.

**The walk** is `Context::resolve_without_builtins` (`context.rs:908`): value stack first (`get`
`:556` → `lookup_segment` `:95`: mapping key, `Encoded::attrs`, integer index), then `dict_view`
(`:699`) and `string_index` (`:672`), then the sidecar: head-name lookup with the alias fallback
(`:938-988`, #2375/#2501) and the per-segment loop (`:1000-1095`) transcribing Django's steps 1–3
with Django's three catch sets (step 1 `:1268`, step 2 inline at `:1040-1043`, step 3 `:1288`;
the `dir()` re-raise probe is `name_exists_on` `:1307`), `maybe_call` after every segment
(`:1140`, ADR-024), `propagate_lookup_error` honouring `silent_variable_failure` (`:1243`, `:1254`),
and `protect_sidecar` after every materialisation (`:1121`). The terminal is
`current.extract::<Value>()` (`:1096`) — **the walk ends by re-entering the eager conversion**,
which is where rows A, I and T below get their `__dict__`-dump answers.

**The custom-tag sink.** `crates/djust_templates/src/registry.rs` builds a handler's `py_context`
from the eager `Value`s (`:529-535`; an `Encoded` crosses as its `display` string,
`crates/djust_core/src/lib.rs:3674`) and then injects the sidecar wholesale (`:540-546`; twins `:649-667`, `:868-884`) —
the #2509 exposure.

### What is wrong today, per path (probe, 2026-09-01, rerun 2026-09-02)

Each row renders one source through Django's engine, the plain djust path, and an emulation of
the LiveView path (`update_state(normalize_django_value(ctx))` + `set_raw_py_values` of the
non-JSON-friendly values, the `_sync_state_to_rust` sequence); every case in its own subprocess.

| # | shape | Django | djust plain | djust LiveView | wrong? | fixed by |
|---|---|---|---|---|---|---|
| A | `{{ o.keep }}`, `keep.do_not_call_in_templates=True` (#2502) | `<bound method …>` | `{'do_not_call_in_templates': True}` | same | both | handle |
| B/C/D | nested object; `{{ d.1 }}` on `{1:…}`; `{{ x.0 }}` on list / `{"0":…}` / `{0:…}` | ok | ok | ok | no | — |
| E1–E4 | `{{ u.password }}` direct / via method / via attr / in `{% for %}` | the hash | `''` | `''` | **no — the floor is deliberate**, permanent pins | must hold under every option |
| F1/F2/F4 | property raising `AttributeError` / `KeyError` / `RuntimeError` | raises | raises (type wrapped) | raises, type kept | wrapping only (v1.2.0-3) | — |
| F3 | raise with `silent_variable_failure` | `''` | `''` | `''` | no (#2508) | — |
| G | `{% if x\|default_if_none:y %}`, `x` undefined (#2528) | `yes` | `no` | `no` | both | `Walked::Invalid` (§Decision e) |
| H | reference cycle `{{ x }}` (#2516) | `1` | **SIGSEGV** | `1` | plain | handle (nothing walks `__dict__`) |
| I | `{{ o }}` bare plain object | `<Plain object at …>` | `{'inst_attr': …}` | `<Plain object at …>` | plain; the two djust paths disagree | handle |
| J / J2 | `{{ callable }}` lambda; `{{ var.callable }}` in a dict | `foo bar` | `<function …>` | `None` | both | handle |
| K / K2 | `Doodad.__call__` → `{{ d.the_value }}`; same with `alters_data` | `42` / `''` | same | same | no | — |
| L | `{{ d.items }}` where the dict has key `items` | `the-key` | ok | ok | no (#2334) | — |
| M | `{% for x in p\|slice:':2' %}{{ x.cls_attr }}` with a top-level `x` (#2505) | `class-level,class-level,` | `,,` | `,,` | both | handle |
| N / N2 | `{% for r in rows\|slice:':1' %}` / `{% for r in dd.values %}` `{{ r.cls_attr }}` (#2504) | `class-level,` | `,` | `,` | both | handle |
| O | object whose `__str__` returns a `SafeString` | `<b>s</b>` | escaped | escaped | both | `Encoded.safe` (§Security 5) |
| P | `test_subscriptable_class`: a `list` subclass CLASS with `do_not_call_in_templates` | ok | **SIGSEGV** | **SIGSEGV** | both (1 of the 7 #2517 crashes) | handle + the step-1 metaclass guard |
| Q | a class object `{{ k }}` | Django CALLS it | `<class …>` | `None` | both | handle |
| R / S / W | `__getitem__` raising `RuntimeError`; silent raise under `{% if %}`; numpy-style `ValueError` | ok | ok | ok | no (#2506) | — |
| T | `{% if o %}/{{ o\|length }}` plain object | `T/0` | `T/1` (counts `__dict__`) | `T/38` (counts `str(o)`) | both, differently | handle |
| U | `{{ u.username }}` | `alice` | `alice` | `alice` | no | — |
| V | generator in the context, `{% for i in g %}` | `12` | `<generator …>` | same | both | handle (consumed once at the for-sink, as Django) |

**Two segfault mechanisms, one cell each.** H crashes in *conversion*: `public_dict_attrs`
(`lib.rs:2004`) recurses in Rust with no visited set, and there is no `RecursionError` because the
recursion is not Python's. P crashes in the *walk*: step 1 is `current.get_item(part)`
(`context.rs:1024`), i.e. `PyObject_GetItem`, which honours `__class_getitem__`, so
`MyClass["class_property"]` yields a `types.GenericAlias` and converting that to `Value` segfaults.
Django never gets there: `_resolve_lookup` opens step 1 with
`if not hasattr(type(current), "__getitem__"): raise TypeError` (`django/template/base.py:889-891`).
The other five #2517 crashes are recursive-extends/include shapes — the #2521/#2531 class, not
resolution; this ADR does not claim them.

**The #2532 premise correction.** ADR-024 named two channels; there are three. Top-level
`QuerySet`/`Model`/`list[Model]` values are serialised **eagerly in Python by the JIT**
(`python/djust/mixins/context.py:205-291`: `is_model_list` `:220`, the re-query `filter(pk__in=…)`
`:275`, the generated per-row serializer `:288-291`); on the LiveView path a `list` is
`_JSON_FRIENDLY` (`rust_bridge.py:1020`) and never enters the sidecar. The #2532 spike (real
`WebsocketCommunicator`, 50×6 rows, debug build, shape only) measured all four `list_*` variants
at **zero** sidecar crossings; the sidecar's traffic is nested opaque objects — a presenter's
`{% for row in page.rows %}{{ row.comments.count }}` walked the alias fallback 357 times with 50
`COUNT(*)` queries per render. The JIT channel's own defects (a re-query per event; a discarded
in-memory row mutation) are #2536 and are not moved here. On the plain path
`_protect_sidecar_tree` does descend lists; the spike's zero is a LiveView-path fact.

### What else consumes the `Value` tree (what must stay materialised)

| consumer | breaks under a live handle? | must stay materialised |
|---|---|---|
| msgpack snapshot — `serialize_msgpack`/`deserialize_msgpack` (`djust_live/src/lib.rs:1246`, `:1281`); the memory backend round-trips on every `get` (`state_backends/memory.py:118`), Redis on read (`redis.py:250`) | No: `raw_py_values` is already `None` after a round trip (`:1304`) and re-attached per render from `full_context` (`rust_bridge.py:1026`). A handle is transient in exactly the same way. | the model's floored dict; the `Encoded` facts pinned by #2481/#2484 (`crates/djust_core/tests/test_encoded_wire_positions_2471_2472.rs`, `TestTheStateRoundTripKeepsTheAttributes`, `TestTheWireDecision`) |
| `changed_keys` partial re-render (`renderer.rs:1313`, keyed on top-level names from `parser.rs:1256`) | No: change detection is Python-side (`rust_bridge.py:812-908`: container `!=` `:875`/`:902`, immutables, else `id()` `:887`) | models: `_normalize_db_values` (`:87`) turns a Model into a dict so container `!=` detects a field mutation on a stable `id()` — an evidence-backed reason models stay eager |
| JIT / client state (`optimization/codegen.py:201-206`, `:328-329`; `mixins/context.py`) | No — and the handle must not admit `list[Model]`: that path never touches Rust resolution today, so a handle design that admitted lists would ADD crossings where there are none | model dicts (they ARE the client payload) |
| custom-tag bridge (`registry.rs:529`, `:540`) | changes shape: a handle would cross back as the live object — the #2509 chokepoint | — |
| `Encoded` structural equality (`lib.rs:851`; `Value` itself deliberately has no `PartialEq`, `:841`; template `==` is `renderer.rs:4184`) | No, provided the handle is ignored and facts are compared | — |

### Django's rules, from source (`django==5.2.16`, `django/template/base.py`)

`Variable.__init__` refuses a segment starting with `_` (`:845-849`). `_resolve_lookup` (`:876`),
per segment: **step 1** the metaclass guard then `current[bit]`, catching `(TypeError,
AttributeError, KeyError, ValueError, IndexError)` (`:889-896`); **step 2** `getattr(current, bit)`
catching `(TypeError, AttributeError)` with the `bit in dir(current)` re-raise (`:903-906`);
**step 3** `current[int(bit)]` → `VariableDoesNotExist` (`:909-916`). Then if `callable(current)`:
`do_not_call_in_templates` → as is (`:921`); `alters_data` → `string_if_invalid` (`:923`); else
call, a `TypeError` → the `signature(current).bind()` probe (`:926-940`). Outer `except Exception`:
`silent_variable_failure` → `string_if_invalid`, else re-raise (`:950`). `FilterExpression.resolve`
(`:720`) turns `VariableDoesNotExist` into `None` under `ignore_failures` (`:725`), which `{% if %}`
(`defaulttags.py:886`), `{% for %}` (`:194`), `{% cycle %}` (`:153`), `{% firstof %}` (`:271`) and
`{% regroup %}` (`:365`, `:368`) all pass. `Context.__getitem__` walks `reversed(self.dicts)`
(`context.py:83-85`): the innermost binding wins, with no by-name fallback to an outer object —
exactly #2505's divergence. `render_value_in_context` (`base.py:1050`) ends in `conditional_escape`
(`:1061`): SafeData decides escaping at the boundary (row O). In Django,
`do_not_call_in_templates` is stamped on `Choices` enums (`db/models/enums.py:85`) and related
managers (`fields/related_descriptors.py:697`, `:1101`) — **not** on `ModelBase`; a model class in
a context is instantiated like any class (falsified below; ADR-024's "covers Model classes" row
was mistaken on that point).

### The GIL, re-verified

`grep -rn "detach(" crates/` finds exactly two sites: `render_markdown_py`
(`djust_live/src/lib.rs:1610`) and `fast_json_dumps` (`:2017`). Every render entry runs under
`Python::attach`, and the walk opens its own (`context.rs:990`). The eager tree buys no GIL-free
render; lazy lookups add per-segment dispatch on a thread that already holds the GIL — the cost
today's sidecar walk already pays for every miss. #2532 measures it.

### The scoreboard (`make django-template-suite`, `Makefile:189`; output `.django-src/last-run.txt`)

461 OK / 222 FAIL / 364 ERROR of 1047 (44.03%), 7 crashes. Bucketed by whether resolution moves
them: ~300 ERRORs are unsupported tags (v1.2.0-3); ~30 are `nodelist`/loaders/relative extends;
~45 FAILs are parse-time `TemplateSyntaxError`s (v1.2.0-3 diagnostics); 12 FAILs are the engine
option `string_if_invalid="INVALID"` (enabled by this sink's *invalid* outcome, not closed by it);
~120 are other tags' semantics; **6 are resolution semantics** (`test_basic_syntax37/38` = rows
J/J2, `test_if_tag_badarg03` = row G, `test_callables.test_callable`/`test_alters_data` — a
callable object at the ROOT segment answered from the `__dict__` dump before the walk runs,
`test_if_tag_not_in_02` to be confirmed at Stage 5). **Honest expectation for the flip: ~7 cells
of 1047 plus 1 crash.** The scoreboard is the ratchet (it must not drop) and the tie-breaker; the
headline gain is the retired class and the LiveView-path parity the suite cannot see (it runs the
plain path only — "reached through the plain-backend path only, not the LiveView path",
`docs/TEMPLATE_BACKEND.md:192`).

## Decision Drivers

- Django-template compatibility as the project goal (ADR-024): ported templates and Django muscle memory Just Work, on **all three** djust paths, which today disagree with each other (rows I, T, J).
- The v1.1.0-13 non-convergence rule: value-by-value fixes in this class have stopped converging (#2501 → #2508 → #2509; #2142 chain-shaped), so the shape of the fix must change.
- The serialization floor (`docs/SECURE_DEFAULTS.md` §1) must hold at every sink; no option may fail open (#2506) or auto-call a mutator (#2507).
- No wire pin moves: the msgpack shape (#2484/#2481) and the client JSON model dict are contracts with deployed clients.
- Cost is scored by #2532, not argued.

## Options Considered

**A — a live-object handle resolved lazily at ONE sink.** `Encoded` gains
`live: Option<Arc<Py<PyAny>>>` (`Arc` because pyo3 0.29 without `py-clone` — `Cargo.toml:23` — has
no `Py<T>: Clone`, and `Value: Clone` is derived, `lib.rs:219`; the shape `raw_py_objects` already
uses, `context.rs:198`). An object with no variant converts to `Encoded { live: Some(h),
type_name, truthy, len, iterable, display: str(o), … }` — the facts `opaque_gate` already measures
(`lib.rs:3184-3190`) — and `items`/`attrs` are no longer walked at conversion. The `__dict__` arm
and the `:3228` decline are deleted. The sink: today's loop body (`context.rs:1000-1095`) becomes
`walk_live(py, obj, parts) -> Walked` where `Walked` is `Value | Invalid | Object(handle)`, called
from `lookup_segment` on a handle-bearing `Encoded`, from the model-miss path, and from the
`{% for %}` / `|length` / truthiness / `Display` readers through accessors that materialise via the
handle when present and fall back to the stored facts after a round trip. Fixes A, H, I, J, J2, M,
N, N2, P, Q, T, V and `test_callables` ×2. Cost: per-segment dispatch for opaque objects only;
dicts, lists, scalars and model dicts stay on the value stack, and `list[Model]` never leaves the
JIT channel — the #2532 correction strengthens A rather than weakening it, because a handle design
that admitted lists would ADD crossings where the spike measured none. Risk: `{{ o }}` on the plain path
flips from a dict repr to `str(o)` (a convergence on the LiveView path's and Django's answer, row
I); the ~18 `Value::Encoded` readers in `renderer.rs` and ~25 in `filters.rs` must route through
the accessors (count-canary, #1125); a generator is consumed once (Django parity, row V).

**B — keep eager conversion, keep patching the sidecar per shape.** A visited set in
`public_dict_attrs` (#2516), decline the bulk-dump arm for routines (#2502), carry raw objects
through filters (#2504), a frame-origin bit (#2505), a page-shell sidecar (#2513),
generator/dataclass arms in `_protect_sidecar_tree` (#2509). Each cell separately; rows J/J2/Q/T/V
need yet more arms. Five more PRs of a chain already at ~40 issues. **Rejected** under "when
value-by-value fixes stop converging, change the shape of the fix".

**C — hybrid: models eager, everything else lazy.** A for opaque objects; models keep
`_normalize_db_values` → floored dict as the value AND a by-name proxied handle for misses; dict
values still convert by the same rules, so J2 is fixed under C too. C = A + "models stay eager",
and every consumer in the table above wants the model dict. **Taken as A's resolution rule with
C's storage rule**, folded into sub-decision (b) rather than presented as a third design.

## Decision

**(a) A live-object handle at one sink — yes.** The handle lives IN the value (`Encoded::live`),
not beside it by name: the by-name sidecar is the mechanism behind #2504/#2505 (a bound name has
no raw object) and #2513 (an entry point must remember to attach one). The sink is `walk_live` in
`context.rs`; `Context::resolve` (`:871`) stays the public entry, and `get_value` (`renderer.rs:3906`)
/ `get_value_safe` (`:3932`) and every tag operand already end there. Django's exact rules, in
order: the `hasattr(type(current), "__getitem__")` guard then dict key, then attribute (with the
`dir()` re-raise), then integer index, each with Django's catch set; auto-call unless
`do_not_call_in_templates`; `alters_data` → invalid; `silent_variable_failure` → invalid; anything
else propagates. The terminal conversion of a plain object is `str(o)` plus facts (rows I, T).
Explicit non-goal: `Value::Object` never carries a handle.

**(b) What stays eager.** (1) Django models, managers and querysets: floored dict as the value
(`rust_bridge.py:87` on the LiveView path; `lib.rs:3073-3089` on the plain path), by-name proxied
handle for misses — for the client JSON shape, msgpack rolling-deploy compatibility, container
`!=` change detection, and the floor's materialised form. (2) `dict`/`list`/`tuple`/`set`/scalars/
`Decimal`/`datetime`/`bytes`: as today. (3) `Component`/`LiveComponent`: a handle whose `display`
is `str(c)` — both `__str__`s return `self.render()` (`components/base.py:392`, `:854`), so
`{{ c }}` and `{{ c.render }}` cannot diverge — with the SafeData bit set; this closes #2513
without wiring a sidecar into the page shell, and `render` stays deliberately unstamped
(`_template_guards.py:52`). (4) A **list of models** is served entirely by the JIT channel and
stays there; the handle is for objects the JIT skips.

**(c) Security posture — the floor holds at the lazy sink; models keep the eager path.** Both
halves. Every materialisation still routes through `protect_sidecar` (`context.rs:1121`) →
`_protect_sidecar_value` (`serialization.py:1260`) → `_SidecarModelProxy.__getattr__`
(`:1340`: `_`-prefix, `_SENSITIVE_MODEL_METHODS` `:66`, `_field_is_serializable` `:640` with the
unconditional denylist precedence, `_field_type_excluded_for` `:190`). Rows E1–E4 stay `''`.

**(d) GIL and cost.** No new cost class (verified above). The implementation row is scored by
#2532's five buckets, thresholds on the median (v1.0.6 rule), non-gating until runner-stable
(#1534): the four `list_*` variants must stay at **0** bucket-2 crossings; `presenter_control` /
`presenter_reverse` are reported before/after — the only variants where the sidecar carries
traffic. `tests/benchmarks/test_template_render.py` (13 dict-fixture cases) must not move.

**(e) The sink returns *invalid* as a distinct outcome.** Row G / #2528: `evaluate_condition`
(`renderer.rs:3479`) resolves operands through `get_value`, which substitutes `Value::Missing`
(= `string_if_invalid`) before the filter chain; Django substitutes `None` under
`ignore_failures=True`. `Walked::Invalid` has two consumers from the dormant-define step: `{{ }}`
→ `Missing`; `{% if %}`/`{% for %}`/`{% cycle %}`/`{% firstof %}`/`{% regroup %}` → `None`, with
`evaluate_condition_for_if`'s existing `VariableDoesNotExist → false` arm (`:3472-3474`) as the
second. This is also what makes the 12 `string_if_invalid="INVALID"` cells implementable later.

**The handle is transient, like `raw_py_values`.** Skipped by `Serialize` (the `ENCODED_TAG`
payload is unchanged), ignored by `PartialEq for Encoded`, dropped by `deserialize_msgpack`,
re-attached on every render. No wire pin moves.

## Security

1. **The floor at the lazy sink.** Unchanged mechanism, stronger claim: with the by-name sidecar narrowed to proxied models, every model handle is a `_SidecarModelProxy` by construction, and `protect_sidecar` after every segment covers a model reached through a plain object or a method result. Pins: `TestTheSerializationFloorStillHolds`, plus rows E1–E4 on the handle path with the flag ON.
2. **`alters_data` / `do_not_call_in_templates`.** `maybe_call` (`context.rs:1140`) unchanged; the `TemplateMutatorGuard` re-stamp (`_template_guards.py:58`, #2507) is what makes a component handle safe. Row Q is a NEW auto-call surface and must be stated plainly: an arbitrary class in a context is instantiated exactly as Django instantiates it, **including a model class** (an unsaved instance; no query, no write). Only `Choices` enums and related managers carry Django's marker.
3. **Exceptions never fail open.** The three catch sets and `silent_variable_failure` are unchanged; the new step-1 metaclass guard raises `TypeError` INTO step 1's own catch set, so it cannot widen a swallow. Pins: `TestAGuardExpressionCannotFailOpen`, `TestDjangosExceptionSetsAtEverySegment`, plus handle-path twins.
4. **The tag-bridge sink (#2509).** A handle is admitted to `py_context` (`registry.rs:529`) only if its object is not a `Model`/`Manager`/`QuerySet` — those never become handles. The residual exposure (a plain object holding a model as an attribute, readable by a HANDLER but not by a template) is today's documented limit, pinned by `test_the_limit_of_the_build_time_pass_is_pinned_not_assumed` (`test_sidecar_on_all_render_paths_2501.py:567`). Not widened.
5. **SafeData across the handle boundary.** Today `_collect_safe_keys` (`rust_bridge.py:168`) marks TOP-LEVEL keys and `mark_safe_keys` (`djust_live/src/lib.rs:409`) replaces per render. A handle's `display` is computed at the sink, so the mark travels WITH the value: `Encoded.safe = isinstance(str(o), SafeData) or hasattr(o, "__html__")` (row O; what a `Component` handle needs), read where `runtime_safe` is read now (`renderer.rs:1442`). Never inferred from the text; never sticky across renders (#2300).
6. **DoS.** The `OPAQUE_ITEM_CAP` walk (`lib.rs:3210-3218`) moves from conversion to the for-sink: an infinite re-iterable still stops at the cap; a one-shot iterator is consumed once. Cycles (row H) cannot recurse because nothing walks `__dict__` any more — the structural cure #2516 names.

## Sequencing (dormant-define → wire → flip → delete; the implementation row)

**Step 1 — characterize the net against the CURRENT sidecar.** One new file,
`python/tests/test_resolve_like_django_2535.py`, each case parametrised over the three paths,
asserting `django == djust`, `xfail(strict=True)` where the table says wrong-today (the #2502 pin
pattern, `test_object_attribute_resolution_2501.py:222`), so the flip turns each red and forces
the marker off. Rows: A, H (subprocess, `returncode == -11` today), I, J, J2, M, N, N2, O, P
(subprocess), Q, T, V, `test_callables` ×2, G. Gate-off siblings (#1468): each Django side is
non-trivial (the lambda IS called — a counter). Permanent pins, listed by class, not re-written:
`TestAGuardExpressionCannotFailOpen`, `TestDjangosExceptionSetsAtEverySegment`,
`TestTheSerializationFloorStillHolds`, `TestComponentMutatorsAreNeverAutoCalled`,
`TestMutatorsRefusedOnTheLiveViewPath` (`test_sidecar_on_all_render_paths_2501.py`);
`TestAutoCallGuards`, `TestSilentVariableFailure` (`test_object_attribute_resolution_2501.py`);
`TestTheSnapshotContractIsPinned` (`test_dict_mutation_during_iteration_panic_2510.py`);
`TestTheWireDecision` (`test_none_missing_state_round_trip_2484.py`);
`TestTheStateRoundTripKeepsTheAttributes` (`test_encoded_attributes_2481.py`); the Rust wire
snapshot `test_encoded_wire_positions_2471_2472.rs`. The step-1 metaclass guard (row P) and a
`__dict__`-walk visited set (row H) may land as interim fixes; both are deleted code under the
flip and must be marked so.

**Step 2 — dormant-define.** `walk_live` extracted from `context.rs:1000-1095` with the SAME
behaviour (0 diffs in the existing suite); `Encoded::live` added, always `None`; the accessors
added and every `Value::Encoded` reader routed through them, pinned by a count-canary over the
`renderer.rs`/`filters.rs` match sites (the `TestTheSinkHasExactlyTheReadersItClaims` pattern,
`test_encoded_attributes_2481.py:638`); `Walked::Invalid` defined; a structural pin that nothing
sets `live` yet.

**Step 3 — wire, behind a flag, off.** `LIVEVIEW_CONFIG["template_resolve_lazy"]` mirrors
`template_auto_call` (`config.py:233`, reader `:644`; carried into `Context` like `auto_call`,
`context.rs:205`, and every constructor that starts a fresh frame — `:255`, `:270` — must carry it
as `Clone` does at `:235`). With the flag ON: `FromPyObject`'s fallback produces handle-bearing
`Encoded`s; `crosses_as_encoded` returns the live object for the shapes the deleted decline used
to refuse; `lookup_segment` consults the handle; `evaluate_condition` uses `Walked::Invalid → None`.
The step-1 net runs with the flag ON as a second CI job — the parity proof, and where the #1898
convergence-dividend list starts.

**Step 4 — flip.** Flag default ON; the scoreboard ratchet (#2522) must not drop and should gain
the ~7 cells + 1 crash; #2532's table before/after in the PR body; the `xfail(strict=True)` markers
come off. Two-gate treatment: worktree-isolated adversarial review with gate-off, plus CI.
Downstream browser check per #1849 (djust.org, docs.djust.org): the `{{ o }}` flip for presenter
objects is the thing to look for.

**Step 5 — delete.** `public_dict_attrs`, `has_public_dict_attrs`, the `lib.rs:3228` decline, the
alias fallback (`context.rs:938-988`; `Context::aliases`' XSS `is_safe` use stays — a different
consumer), `build_render_sidecar`/`_protect_sidecar_tree`/`_SIDECAR_MAX_DEPTH`, the
`_JSON_FRIENDLY` filter; the by-name sidecar narrows to the top-level models of
`rust_bridge.py:717-719`. The flag stays one release as a kill-switch (ADR-024 §4), removal at 2.0.
Symbol-removal grep across every test root (#1391). #2509 closes with a pointer;
#2502/#2504/#2505/#2513/#2516 close by the red `xfail`s.

## Consequences

- **Positive.** One statement of Django's lookup rules, reached from every operand site, on all three djust paths; the plain and LiveView paths stop disagreeing on `{{ o }}` / `|length` / truthiness (rows I, T); six open issues close by pointer; ~7 scoreboard cells and one crash; a tag-handler context that can only hold floored values; the `__dict__` recursion class cannot recur.
- **Behaviour changes to declare (CHANGELOG "Behavior change" sub-bullets at the flip).** `{{ o }}` on the plain path renders `str(o)`, not the attribute dict; a class in a context is instantiated (Django parity, including model classes); a generator is consumed by `{% for %}`; a callable at any segment is called (rows J/J2 — was a repr / `None`).
- **Negative / risks.** Presenter objects on downstream sites that relied on the dict repr; `Encoded` reader-sweep completeness (the count-canary is the defence); the unmeasured cost on the `@property` fixture and `presenter_control`'s one-off list extraction (`page.rows` still converts a list of raw models per render; memoising it is a follow-up); #2532 must land first — it is the row above this one.

## Non-goals

`string_if_invalid` as an engine option (enabled, not delivered); parse-time `TemplateSyntaxError`s
and unwrapped exception types (v1.2.0-3 diagnostics); `{% load %}`, the six unsupported tags,
i18n; the recursive-extends/include crashes (#2521/#2531 — same arc, different sink); the JIT list
channel's per-event re-query and discarded-mutation defects (#2536); a `Value::Object` handle.

## Appendix — falsification tests performed (#1867)

Each "always / never / exactly" claim above, with the probe that tried to disprove it. Probe
scripts and raw output live in the planning scratchpad; every result was reproduced on 2026-09-02
with the main checkout's `.venv` (`django==5.2.16`).

| claim | probe | result |
|---|---|---|
| Only two `detach(` sites; the render holds the GIL | `grep -rn "detach(" crates/` | exactly `djust_live/src/lib.rs:1610` and `:2017` |
| The floor holds on both djust paths for every reach (E1–E4) | 34-shape probe, rows E1–E4 | `''` on plain and LiveView; Django prints the hash |
| The two djust paths disagree on `{{ o }}` and `\|length` | rows I, T | plain `{'inst_attr': …}` / `T/1`; LiveView `<Plain object …>` / `T/38` |
| H crashes in conversion, P in the walk | `probe2.py` bisection | `render_template('{{ x }}', cycle())` dies before rendering; for P, converting `{'class_var': MyClass}` and `build_render_sidecar` both return, `render_template('{{ class_var.class_property }}')` dies, and `render_template('{{ a }}', {'a': MyClass['class_property']})` dies on its own |
| Both crashes reproduce today | probe rerun, subprocess per case | rows H and P `rc=-11`; the other 32 rows match the table |
| `Value` has no `PartialEq`; `Encoded` has a manual one | read `lib.rs:841-851` | confirmed; the plan's "`PartialEq for Value` at `:950`" was wrong and is corrected here |
| A `list[Model]` never enters the LiveView sidecar | read `rust_bridge.py:1012-1030` and `:717-719` | `list` is `_JSON_FRIENDLY`; only `isinstance(_val, Model)` is captured; consistent with the #2532 spike's 0 crossings |
| Django never reaches `__class_getitem__` at step 1 | read `base.py:889-891` | the `hasattr(type(current), "__getitem__")` guard precedes `current[bit]` |
| `{% if %}` operands use `ignore_failures=True` | grep `defaulttags.py` | `:886` (`if`), `:194`, `:153`, `:271`, `:365`/`:368` |
| `do_not_call_in_templates` is on `ModelBase`, so `{{ SomeModel }}` stays uncalled (the plan's claim) | grep Django for the marker; render `{{ M }}` / `{{ M.pk }}` with `M = User` through Django's engine | **refuted**: `hasattr(ModelBase, …)` is `False`; `{{ M.pk }}` renders `None` — the class was instantiated. The marker lives on `Choices` (`enums.py:85`) and related managers (`related_descriptors.py:697`, `:1101`). Security §2 states the surface accordingly |
| `Component.__str__` and `.render()` agree | read `components/base.py:392`, `:854` | both bodies are `return self.render()` |
| `Py<T>` is not `Clone` in this workspace | `Cargo.toml:23` (no features); pyo3 0.29 `Cargo.toml:165` `py-clone = []` | `Arc` is required, as `context.rs:198` already does |
| The walk's terminal re-enters the eager conversion | read `context.rs:1096` | `Ok(current.extract::<Value>().ok())` |
| The page shell wires no sidecar | read `mixins/template.py:1079-1081` | stated in the source comment; #2513 |
