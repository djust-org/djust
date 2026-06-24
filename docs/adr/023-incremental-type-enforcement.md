# ADR-023: Incremental Type Enforcement — mypy Gate + Strict Islands

**Status**: Accepted — foundation shipped 2026-06-24 (this PR); the strict-island ratchet is ongoing.

**Shipped in**: v1.1 (foundation — the enforced gate + the initial strict-island set + the `_rust.pyi` boundary). The per-module ratchet continues post-1.0 across the v1.1 code-quality arc.

**Relates to**: [ADR-022](022-v1.1-code-quality-single-path-convergence.md) (v1.1 code-quality headline). Type enforcement is a code-quality groundwork item under that arc — it makes a whole class of contract drift mechanically detectable, the same discipline ADR-022's anti-drift parity nets apply to transport paths.

## Context

djust ships a [PEP 561](https://peps.python.org/pep-0561/) `py.typed` marker
(`python/djust/py.typed`) — meaning **downstream consumers type-check their code
against djust's published type hints**. The developer-facing public API
(`live_view`, `component`, `decorators`, `forms`) is the surface those consumers
import, and the `djust._rust` PyO3 module is the serialization/wire boundary
their data crosses. Shipping `py.typed` is a promise: the types are real and
maintained.

Three facts, measured against the codebase at the time of this ADR, showed the
promise was only half-kept:

1. **A strict mypy config existed but was DEAD.** `pyproject.toml` declared
   `[tool.mypy]` with `disallow_untyped_defs = true` + `warn_return_any = true`,
   yet **mypy was invoked nowhere** — not in `Makefile`, not in CI
   (`.github/workflows/test.yml`), not in `.pre-commit-config.yaml`. A gate that
   never runs enforces nothing; the strict config was aspirational decoration.

2. **The legacy baseline is ~8.4k errors.** Running the *declared* strict config
   over the package (`mypy python/djust`) reported **8,421 errors across 659
   files**: **6,814 `[no-untyped-def]`** (missing annotations — the ratchet
   target), **~750 `[import-untyped]`** (third-party / Django / PyYAML modules
   with no stubs), and **~700 genuine type errors**
   (`[assignment]`/`[attr-defined]`/`[arg-type]`/`[union-attr]`/`[no-any-return]`/`[misc]`).
   Return-annotation coverage was ~42% and uneven (e.g. `runtime.py` ~83% vs
   `decorators.py` ~26%). Flipping the declared strict config ON globally would
   make the gate **permanently red** — which is why it was never wired.

3. **The consumer-facing liability is real.** A type bug in a strict-typed
   public-API signature (or a stale `_rust.pyi` entry) silently breaks the
   type-checking of *every downstream project* that imports djust. No gate meant
   no protection on the exact surface `py.typed` exposes.

This aligns directly with two djust manifesto convictions:
**AI-Ready by Design** (a well-typed, gated framework means AI writes app logic,
not type contracts — less surface area for generated code to get wrong) and
**Developer First** (clear, trustworthy types are part of the developer
experience we own).

## Decision

Adopt **incremental type enforcement**: a mypy gate that is **green today**,
**genuinely strict on a meaningful subset**, and **ratchet-ready** to expand
module-by-module. Four parts:

### 1. Lenient global default + strict islands (`pyproject.toml [tool.mypy]`)

- The **global default is lenient** so the gate passes against the legacy
  baseline: `ignore_missing_imports = true` (kills the third-party / Django /
  PyYAML `import-untyped` noise) and `ignore_errors = true` (parks the ~700
  legacy real errors + the ~6.8k missing-annotation errors). This is the
  canonical mypy incremental-adoption pattern — **NOT a blanket free pass**: the
  strict islands below flip `ignore_errors` back OFF and enforce every error
  class.
- **Strict islands** are per-module `[[tool.mypy.overrides]]` blocks setting
  `ignore_errors = false` + `disallow_untyped_defs = true` +
  `disallow_incomplete_defs = true` + `warn_return_any = true`. Each island is
  verified to pass clean (0 errors) under those rules. The initial set
  (22 modules) leads with the **security boundary** (the trust surface) and the
  **PyO3 wire boundary**, plus DoS-surface, validation, and well-annotated leaf
  modules:

  | Island group | Modules |
  |---|---|
  | Security boundary | `djust.security.*` (attribute_guard, error_handling, event_guard, json_script, log_sanitizer, mount, state_snapshot, `__init__`) |
  | PyO3 wire boundary | `djust._rust` (typed by `_rust.pyi`) |
  | DoS / rate surface | `djust.rate_limit` |
  | Input validation / audit | `djust.validation`, `djust.permissions` |
  | Well-annotated leaves | `djust.markdown`, `djust.schema`, `djust.signals`, `djust.async_result`, `djust.test_isolation`, `djust._client_ip`, `djust._log_utils`, `djust._html`, `djust._view_resolution`, `djust._deprecation`, `djust._context_provider` |

  Third-party libs consumed *by* an island but lacking stubs (e.g. PyYAML) get a
  narrow `[[tool.mypy.overrides]] module = ["yaml"]` with
  `ignore_missing_imports = true` — so the island stays green without weakening
  its own def/return strictness.

### 2. `python/djust/_rust.pyi` — the PEP 561 stub for the PyO3 module

The compiled `djust._rust` (PyO3) module is typed by `_rust.pyi`, declaring the
Python-facing surface — template/VDOM entry points (`render_template`,
`render_template_with_dirs`, `diff_html`, `resolve_template_inheritance`),
`render_markdown`, serialization helpers (`serialize_queryset`,
`serialize_context`, …), the tag/filter registry functions, the actor handles
(`create_session_actor`, `SessionActorHandle`), and `RustLiveView` + the Rust
component classes. Real param/return types are used where derivable from the
Rust signatures; `Any` only where the Rust return is genuinely dynamic. The
stub's top-level names are pinned to **exactly match** the compiled module's
runtime exports, and a strict island (`djust.markdown`) imports through it — so
the **highest-value typing (the wire/serialization boundary) is type-checked**,
not merely declared.

### 3. The gate is ENFORCED (CI + Makefile + pre-commit)

- **CI (blocking merge gate):** a `Run mypy type-check` step in the
  `python-tests` job of `.github/workflows/test.yml`, with **no
  `continue-on-error`**. `python-tests` is in the `test-summary` aggregate
  gate's AND-condition, so a mypy failure → step fails → `python-tests` fails →
  the AND-condition is false → `exit 1`. This is a **new merge gate (#1236
  governance)**. Per the #1534 "green-on-first-run → gate" rule it ships gating
  because it is green by construction.
- **`make typecheck`** runs `mypy python/djust`; it is wired into `make check`.
- A scoped **pre-commit hook** (`.pre-commit-config.yaml`) runs mypy on
  `python/djust/**.py{,i}` changes (warm runs ~0.3s thanks to mypy's cache).

### 4. The ratchet

Expanding enforcement is **one module per PR**: pick the next module, fix its
annotations and any real type errors it surfaces, add it to a strict-island
override, confirm `mypy python/djust` stays green. **Prioritise the
developer-facing public API** — `live_view`, `component`, `decorators`,
`forms` — because `py.typed` exposes those to consumers, with `_rust.pyi`
holding the boundary. Suggested coverage milestones:

- **M1 (this PR):** security + wire boundary + leaf modules (22 modules / ~90
  functions / ~2.8% of package LOC enforced strict).
- **M2:** the public-API quartet (`live_view`, `component`, `decorators`,
  `forms`) — the consumer-facing surface.
- **M3:** the dispatch/runtime core (`runtime`, `websocket`, `sse`,
  `streaming`) — alongside / after the ADR-022 ViewRuntime convergence settles
  (converging first avoids annotating code that's about to move).
- **M4+:** mixins, components, theming, the long tail.

## Consequences

- **No big-bang.** The legacy ~8.4k errors are parked, not fixed in one
  unmergeable PR. The gate is green from day one.
- **Each strict-flip is a small, reviewable PR.** Flipping a module to strict
  forces its `~700`-real-error share to be triaged in that PR — real type bugs
  get fixed as a side effect of widening coverage (this PR already fixed a
  handful — see below).
- **Regression-proof for the enforced surface.** A new un-annotated def, a
  wrong-typed return, or an `Any` leak in any strict island fails the gate. A
  module NOT yet on an island stays lenient — verified empirically (an injected
  type error in a strict island goes RED; the same error in a lenient module
  stays GREEN), so the gate is real, not cosmetic.
- **The consumer promise is now partially backed by a gate** and grows with the
  ratchet — the public-API quartet (M2) is the next high-value flip.
- **Cost:** mypy is added to the `python-tests` CI critical path (~seconds,
  cached) and to pre-commit on Python changes (~0.3s warm). One existing test
  (`test_type_stubs.py::test_mypy_catches_missing_required_arg`) was made
  config-independent (it now pins an isolated empty mypy config) so it asserts
  the stub's contract rather than inheriting the lenient gate config.

### Real type bugs fixed to clean the initial islands

- `security/attribute_guard.py`: `DANGEROUS_ATTRIBUTES` was annotated `Set[str]`
  (mutable) but holds a `frozenset` — corrected to `frozenset[str]`, matching the
  membership-only, never-mutated security-denylist intent.
- `security/log_sanitizer.py`: `sanitize_dict_for_log`'s `result` dict held
  heterogeneous values (redacted strings, nested dicts, item lists) under an
  inferred `dict[str, str]` — annotated `dict[str, Any]`.
- `security/state_snapshot.py`, `permissions.py`: `[no-any-return]` from
  untyped-dependency calls (`TimestampSigner.sign`, `yaml.safe_dump`) — narrowed
  to `str` at the boundary.
- Plus annotation gaps in `rate_limit`, `_context_provider`, `schema`,
  `permissions`, `test_isolation` (missing return/param annotations,
  `var-annotated` hints).
