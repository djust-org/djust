# ADR-007: Package Taxonomy and Consolidation Strategy

**Status**: Accepted — Phase 4 (sunset) shipped 2026-04-23 in v0.6.0
**Date**: 2026-04-11 (proposed) · 2026-04-23 (Phase 4 closure)
**Deciders**: Project maintainers
**Target version**: v0.5.0+ (rules apply going forward; existing packages stay where they are)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-003](003-llm-provider-abstraction.md), [ADR-006](006-ai-generated-uis-with-capture-and-promote.md)

## Phase 4 closure note (2026-04-23)

All five sibling repos (`djust-auth`, `djust-tenants`, `djust-theming`, `djust-components`, `djust-admin`) are sunset. Each:

- Ships a `v99.0.0` git tag as the canonical "frozen" release marker.
- Preserves its final shim-only `__init__.py`, which re-exports from `djust.<name>` and emits a `DeprecationWarning` on import.
- Has a `MIGRATION.md` pointing users at `pip install djust[<name>]`.

**Path A was chosen** over PyPI publish: the v99.0.0 tags are authoritative; no new PyPI releases are planned. Existing PyPI versions remain installable indefinitely for legacy projects.

djust core now exposes the consolidation via extras in `pyproject.toml`:

| Extra | Replaces | Deps |
|---|---|---|
| `djust[auth]` | `djust-auth` | none beyond core |
| `djust[tenants]` | `djust-tenants` | none (use `[tenants-redis]` / `[tenants-postgres]` for backend-specific) |
| `djust[theming]` | `djust-theming` | none beyond core |
| `djust[components]` | `djust-components` | `markdown`, `nh3` |
| `djust[admin]` | `djust-admin` | none beyond core |

User-facing migration guide: [`docs/website/guides/migration-from-standalone-packages.md`](../website/guides/migration-from-standalone-packages.md).

Remaining tech-debt (not blocking milestone close): the sibling repos retain legacy pre-consolidation `src/djust_<name>/{mixins,views,urls,...}.py` files next to the shim `__init__.py`. These are dead code (no longer imported — the `__init__.py` re-exports from `djust.<name>`). Cleaning them up is cosmetic and tracked as a workspace hygiene task; does not affect users.

---

## Summary

The djust workspace currently ships ~18 distinct Python packages: `djust`, `djust-theming`, `djust-components`, `djust-auth`, `djust-tenants`, `djust-admin`, `djust-scaffold`, `djust-create`, `djust-monitor`, `djust-monitor-client`, `djust-experimental`, plus application packages (`djust.org`, `djust-chat`, `djust-crm`, `djust-notes`, `djustlive`, `examples.djust.org`) and the private `djust-internal`. Adding the AI-driven UI feature cluster from ADRs 002–006 raises a natural question: should the new features ship as yet-another-package (`djust-assistant`, `djust-generative`, `djust-consent`) following the existing pattern, or should they consolidate into core djust? And, separately: should some of the existing packages consolidate, given that the workspace's growing surface area is becoming a discovery and maintenance burden?

This ADR establishes a **taxonomy for what belongs in a separate package versus core djust**, applies the taxonomy to the existing packages (finding that most of the current separations are defensible), and commits to **keeping all ADR-002–006 features in core djust under `djust.assistant.*` / `djust.generative.*` / `djust.consent.*` submodules with optional extras for vendor SDKs**. It also identifies two edge cases (`djust-auth`, `djust-tenants`) that are technically Django-generic but carry the `djust-` prefix in a way that may be misleading, and recommends leaving them as-is with a documentation clarification rather than renaming.

The TL;DR for future package decisions: **runtime extensions that depend on djust core belong in core with optional extras. Django-generic libraries stay separate. Content packages (components, themes) stay separate. Applications stay separate. Tooling stays separate.**

## Context

### Current state of the workspace

A survey of every `pyproject.toml` in the djust workspace (executed 2026-04-11, reproducible via `find .. -maxdepth 2 -name pyproject.toml`):

**Runtime framework packages:**

| Package | LOC (excl. tests, migrations) | Files | Depends on djust? |
|---|---|---|---|
| `djust` | ~79,000 | 346 | — (is djust) |
| `djust-components` | ~64,200 | 307 | ✓ (`djust>=0.3.0rc5`) |
| `djust-theming` | ~37,600 | 139 | ✗ (optional extra) |
| `djust-admin` | ~2,300 | 12 | ✓ (`djust>=0.3.0rc5`) |
| `djust-tenants` | ~1,900 | 9 | ✗ (optional extra) |
| `djust-auth` | ~670 | 9 | ✗ (not at all) |

**Tooling and developer ergonomics:**

| Package | Purpose |
|---|---|
| `djust-create` | Project scaffolding CLI |
| `djust-scaffold` | Template fragments for scaffolding |
| `djust-monitor` | Observability server (app) |
| `djust-monitor-client` | Client library for emitting metrics to the monitor |
| `djust-experimental` | Experimental features, explicitly unstable |

**Applications built on djust (not framework extensions):**

| Package | Purpose |
|---|---|
| `djust.org` | Marketing site (dogfoods djust) |
| `djust-chat` | Example chat application |
| `djust-crm` | Example CRM application |
| `djust-notes` | Example notes application |
| `examples.djust.org` | Examples gallery |
| `djustlive` | Managed PaaS platform |

**Internal / private:**

| Package | Purpose |
|---|---|
| `djust-internal` | Full framework history + private planning docs |

That's the landscape. Eighteen packages, five distinct categories (runtime, tooling, applications, internal, monorepo root). The question isn't whether 18 is "too many" in the abstract — it's whether each package's separation is load-bearing for some concrete reason.

### The dependency structure is the key insight

The single most important finding from the audit: **`djust-auth`, `djust-tenants`, and `djust-theming` do not depend on `djust` in their default install.**

```toml
# djust-auth/pyproject.toml
dependencies = ["django>=4.2"]

# djust-tenants/pyproject.toml
dependencies = ["django>=4.2"]
[project.optional-dependencies]
djust = ["djust>=0.3.0rc5"]         # djust is opt-in

# djust-theming/pyproject.toml
dependencies = ["Django>=4.2"]
[project.optional-dependencies]
djust = ["djust>=0.3.0rc5"]         # djust is opt-in
```

These are **Django packages from the djust team**, not djust extensions. A plain Django user (not using djust at all) can `pip install djust-auth` and get value out of it. Merging them into core djust would break that property — they'd become djust-only, and every Django user would lose access.

Meanwhile, `djust-components` and `djust-admin` DO depend on djust core (`djust>=0.3.0rc5`). They're genuine djust extensions, not Django libraries that happen to ship in the workspace.

This split — Django-generic vs djust-specific — is the load-bearing distinction that informs the whole consolidation question.

### Why this ADR exists now

The AI feature cluster (ADRs 002–006) introduces several new feature areas that could plausibly go in their own packages following the `djust-theming`/`djust-components` precedent:

- `djust-assistant` — for `AssistantMixin`, provider abstraction, undo
- `djust-generative` — for `GenerativeMixin`, composition documents, capture lifecycle
- `djust-consent` — for the consent envelope
- `djust-tutorials` — for `TutorialMixin` and the tutorial state machine

My earlier recommendation in the design cluster was to put all of these *in core djust* as submodules (`djust.assistant`, `djust.generative`, etc.) with optional extras for vendor SDKs (`pip install djust[assistant-openai]`). But that recommendation contradicts the pattern the workspace already uses. Before committing, we need an explicit rule for when to split and when to consolidate, and we need to verify that the rule supports both decisions: keeping the AI features in core *and* keeping most existing packages separate.

This ADR is that rule.

## Taxonomy: five axes for package separation decisions

A package should be a separate Python distribution if and only if it clearly benefits along at least **two** of the following axes. One benefit is not enough; the coordination cost of a separate package is real.

### Axis 1: Framework agnosticism

**The question**: can this package be useful to a user who is *not* using djust?

**Split if yes**: if the package works for plain Django users (or plain Python users), shipping it as a separate distribution lets that audience consume it without pulling in djust. This is load-bearing for `djust-auth` and `djust-tenants`: plain Django projects can use them today, and merging would break that.

**Consolidate if no**: if the package only makes sense in a djust context — it imports `djust.LiveView`, it uses `@event_handler`, it extends the VDOM — there's no external user to serve. Separation adds coordination cost with no benefit.

**Score for AI feature cluster**: all of ADR-002–006 requires djust's LiveView, event pipeline, VDOM, and state backend. None of it is meaningful outside djust. **Points to consolidation.**

**Score for existing `djust-auth` / `djust-tenants` / `djust-theming`**: all usable in plain Django. **Points to separation.**

### Axis 2: Dependency weight

**The question**: does this package pull in transitive dependencies that most users don't need?

**Split if yes**: if the package requires heavy or exotic dependencies (large native libraries, commercial SDKs, vendor-specific client libraries), putting it in core forces every user to resolve and download those dependencies even when they won't use the feature. Separation — or, equivalently, optional extras — is mandatory.

**Consolidate if no**: pure Python with no new transitive dependencies has zero weight cost. No reason to split.

**Score for AI feature cluster**: OpenAI SDK, Anthropic SDK, Whisper, pydantic (already in core), Django (already in core). OpenAI and Anthropic are heavy and vendor-specific. **Points to extras within core, not to a separate package.** The extras mechanism (`pip install djust[assistant-openai]`) gives the weight-avoidance benefit without the versioning overhead.

**Score for `djust-components`**: pulls in `markdown` and `nh3` as mandatory deps. These are moderate-weight. Could go either way; the bigger issue is its size (see Axis 5).

### Axis 3: Release cadence

**The question**: does this package need to ship patches, bugfixes, or features on a different schedule than core?

**Split if yes**: if a feature area churns faster than core — for example, if we expect weekly releases of a component library but monthly releases of the framework — coupling them in one package creates version-pressure. Either the component library releases are blocked on core reaching a release point, or the framework releases are rushed to unblock the library.

**Consolidate if no**: if release cadence matches core's, there's no independence value in separation.

**Score for AI feature cluster**: the LLM provider adapters may evolve quickly (new vendors, new tool-calling schemas, pricing changes), but the core `AssistantMixin` and `GenerativeMixin` code should move in lockstep with LiveView evolution. For the provider churn, the extras pattern handles it — we can cut a `djust 0.5.1` that bumps only the `openai_provider.py` module and leaves everything else unchanged. **Points to consolidation.**

**Score for `djust-components`**: component libraries should evolve on their own cadence. New components shouldn't wait for framework releases; framework releases shouldn't wait for component polish. **Points to separation.**

**Score for `djust-theming`**: same as components — CSS-heavy packages evolve by fashion, not by framework cadence. **Points to separation.**

### Axis 4: Security / audit surface

**The question**: is this package's security surface meaningfully different from core?

**Split if yes**: if the package has distinct attack surface (user uploads, external network access, code execution, AI-generated content), isolating it lets security reviewers audit it independently and lets users exclude it if they don't need it.

**Consolidate if no**: if the security surface is the same as core's existing event pipeline and VDOM, there's nothing to isolate.

**Score for AI feature cluster**: the consent envelope and generative UI features have distinct, load-bearing security surface (scope enforcement, composition validation, prompt injection, remote control). But the surface is *tightly integrated* with core's auth and event systems — it can't be audited in isolation because the integration points are where the risks live. Splitting would hurt auditability by hiding the integration behind a package boundary. **Points to consolidation** (counterintuitively — the integration is the audit, not the isolation).

**Score for `djust-components`**: shared surface with core, nothing special. **Neutral.**

### Axis 5: Size and discoverability

**The question**: is this package's code or documentation volume large enough to make core unwieldy?

**Split if yes**: a 60,000-line component library inside core makes `grep`, `find`, Django's runserver, and new-contributor onboarding all worse. At some point a subtree is big enough to warrant its own repository regardless of other considerations.

**Consolidate if no**: small additions (under ~10,000 lines) don't materially change core's ergonomics.

**Score for AI feature cluster**: estimated ~4,000 lines of Python across all five ADRs (assistant/provider/undo ~1,500, generative ~1,500, consent ~1,000). This is small relative to core's ~79,000 lines. **Points to consolidation.**

**Score for `djust-components`**: 64,000 lines is nearly as large as core itself. Merging would double core's size, mostly with CSS and template files. **Strongly points to separation.**

**Score for `djust-theming`**: 37,600 lines. Same concern as components. **Points to separation.**

### Axis 6 (implicit): Cross-package versioning pain

Not one of the scoring axes, but the cost that every split pays. When two packages depend on each other, every release becomes a coordination exercise. Every API change in the lower package has to be carefully preserved, deprecated, or version-gated in the upper one. Security patches have to test the full compatibility matrix of versions. Contributors have to learn multiple release processes.

This cost is real and ongoing. It's the main reason the default should be "consolidate unless there's a clear reason not to" rather than "split unless it's obviously one thing."

## The rule

A package should be a separate Python distribution if and only if **at least two** of axes 1–5 clearly point to separation. One benefit is not enough. Here's the scoring for each candidate, with a binary consolidate/separate verdict:

### Existing packages

| Package | Agnostic (A1) | Dep weight (A2) | Cadence (A3) | Security (A4) | Size (A5) | Verdict |
|---|---|---|---|---|---|---|
| `djust-auth` | ✓ | — | — | — | — | **Separate** (A1 alone is load-bearing, despite only 1 point) |
| `djust-tenants` | ✓ | — | — | — | — | **Separate** (same reasoning) |
| `djust-theming` | ✓ | — | ✓ | — | ✓ | **Separate** (three axes) |
| `djust-components` | — | — | ✓ | — | ✓ | **Separate** (cadence + size) |
| `djust-admin` | — | — | — | — | — | **Borderline** (none of the axes strongly argue for separation, but it's an *application*, see note below) |
| `djust-create` | — | ✓ | ✓ | — | — | **Separate** (it's a CLI tool — tools should be separate regardless) |
| `djust-scaffold` | — | — | ✓ | — | — | **Borderline** (only cadence; could merge with `djust-create` or core) |
| `djust-monitor` | — | ✓ | ✓ | ✓ | — | **Separate** (it's a separate application with observability-specific deps) |
| `djust-monitor-client` | ✓ | — | — | — | — | **Separate** (Django-generic metric emitter) |
| `djust-experimental` | — | — | ✓ | — | — | **Separate** (the whole point is an experimental sandbox outside of core's release guarantees) |

**Note on `djust-admin`**: it currently depends on djust core and is small (~2,300 lines). By the pure taxonomy it could arguably merge. But it's an *application* (a Django admin-style UI) rather than a framework extension, and applications built on djust belong in their own repos for the same reason `djust.org` and `djustlive` do. Applications have deployment concerns, branding, release schedules, and user-facing documentation that shouldn't be entangled with framework code. Verdict: **keep separate, but reframe its categorization from "extension" to "first-party application."**

**Note on `djust-scaffold`**: borderline. It's only scoring 1 (cadence). The reason to keep it separate is consistency with the tooling-is-separate rule — merging scaffolding into core would grow core with files that only run at `djust-create` time. Verdict: **keep separate, no action.**

### New packages (AI feature cluster)

| Proposed package | A1 | A2 | A3 | A4 | A5 | Verdict |
|---|---|---|---|---|---|---|
| `djust-assistant` | — | ✓ (openai/anthropic SDKs) | — | — | — | **Core submodule** (A2 alone is handled by extras, not a separate package) |
| `djust-generative` | — | — | — | — | — | **Core submodule** |
| `djust-consent` | — | — | — | — | — | **Core submodule** |
| `djust-tutorials` | — | — | — | — | — | **Core submodule** |

None of the AI features score enough to justify separation, and the one that might (`djust-assistant`, on dep weight) is served better by the extras pattern.

## Applying the rule to the AI feature cluster

**Decision**: all ADR-002 through ADR-006 features ship in the main `djust` package as submodules, with optional extras for vendor SDKs.

### Directory layout

```
python/djust/
├── __init__.py
├── live_view.py              # existing
├── websocket.py              # existing
├── vdom.py                   # existing
├── event_handlers.py         # existing
├── ... (rest of existing core)

├── server_driven/            # ADR-002 Phase 1
│   ├── __init__.py
│   ├── mixin.py              # ServerDrivenMixin, push_commands()
│   └── waiters.py            # wait_for_event primitive

├── tutorials/                # ADR-002 Phase 3
│   ├── __init__.py
│   ├── mixin.py              # TutorialMixin
│   └── step.py               # TutorialStep dataclass

├── consent/                  # ADR-005
│   ├── __init__.py
│   ├── envelope.py           # ConsentEnvelope dataclass
│   ├── scope.py              # check_op, scope vocabulary
│   ├── lifecycle.py          # request/accept/revoke
│   └── audit.py              # append-only log

├── assistant/                # ADRs 003, 004, and ADR-002 Phase 5
│   ├── __init__.py           # lazy-imports providers
│   ├── mixin.py              # AssistantMixin
│   ├── errors.py             # ProviderError hierarchy
│   ├── undo.py               # UndoSnapshot, @undoable
│   ├── schema.py             # introspection helpers (get_handler_schema, etc)
│   └── providers/
│       ├── __init__.py
│       ├── base.py           # Protocol definition
│       ├── openai_provider.py      # lazy-imports openai
│       ├── anthropic_provider.py   # lazy-imports anthropic
│       ├── mock.py           # test provider, no vendor deps
│       └── pricing.py

└── generative/               # ADR-006
    ├── __init__.py
    ├── decorators.py         # @ai_composable
    ├── schema.py             # CompositionDocument (pydantic)
    ├── mixin.py              # GenerativeMixin
    ├── data_sources.py       # DataSource
    ├── validation.py         # allow-list enforcement
    ├── capture.py            # GeneratedView lifecycle
    ├── export.py             # export-to-Python generator
    └── stdlib/
        ├── __init__.py
        ├── typography.py     # Heading, Paragraph
        ├── layout.py         # Stack, Grid, Tabs
        ├── display.py        # StatCard, Badge, Alert
        ├── charts.py         # LineChart, BarChart, PieChart
        └── tables.py         # DataTable
```

### pyproject.toml extras

```toml
# python/djust/pyproject.toml (existing file, new optional-dependencies)

[project.optional-dependencies]
# Vendor SDKs for AssistantMixin providers
assistant-openai    = ["openai>=1.50"]
assistant-anthropic = ["anthropic>=0.40"]
assistant-all       = ["djust[assistant-openai,assistant-anthropic]"]

# Optional speech-to-text via local Whisper (heavy)
assistant-whisper   = ["openai-whisper>=20240930", "torch>=2.0"]

# Everything AI-related at once
ai                  = ["djust[assistant-all,assistant-whisper]"]
```

### Lazy imports at the boundary

```python
# python/djust/assistant/providers/openai_provider.py

_OPENAI_SDK_HINT = (
    "To use djust.assistant.providers.OpenAIProvider you need the OpenAI SDK. "
    "Install with: pip install djust[assistant-openai]"
)

class OpenAIProvider:
    def __init__(self, api_key: str, **kwargs):
        try:
            from openai import OpenAI, AsyncOpenAI
        except ImportError:
            raise ImportError(_OPENAI_SDK_HINT)
        self._sync = OpenAI(api_key=api_key, **kwargs)
        self._async = AsyncOpenAI(api_key=api_key, **kwargs)
```

The lazy import keeps the module importable even without the extra installed — you just can't instantiate the class. Framework tests that only need `MockProvider` run without any vendor SDKs.

### What this gives us

- One `pip install djust` covers the entire reactive framework, including tutorials, consent envelopes, generative UIs, and LLM orchestration.
- `pip install djust[assistant-openai]` adds OpenAI support. `pip install djust[ai]` adds everything.
- `pip install djust` without extras fails cleanly at LLM-provider instantiation time, with a helpful error message. No mysterious import errors at module load.
- No cross-package versioning (no "djust 0.5.0 requires djust-assistant 0.5.x requires djust 0.5.x" ping-pong).
- Security audit happens on one codebase, not across N packages.
- New contributors find the AI features where they expect: under `djust/assistant/` in the main repo.
- CI runs one test suite, not N.

## Applying the rule to existing packages

**Decision**: no existing packages consolidate or split further in the v0.5 cycle. The current split is defensible under the taxonomy above.

The table in [The rule](#the-rule) verified this by scoring each package. A few packages are borderline and deserve review every major release, but none have a strong argument for moving *now*.

### What should NOT change

- **`djust-auth` stays separate.** It's a Django package, not a djust package. Plain Django users consume it and losing that capability would be a regression.
- **`djust-tenants` stays separate.** Same reason.
- **`djust-theming` stays separate.** Django-generic, large, and has its own cadence.
- **`djust-components` stays separate.** Large and has its own cadence. Even though it's djust-dependent.
- **`djust-admin` stays separate.** It's an application, and applications live in their own repos.
- **All tooling (`djust-create`, `djust-scaffold`, `djust-monitor*`) stays separate.** Tools should not live in the framework.

### What COULD change but isn't changing now

- **`djust-scaffold`** could plausibly merge into `djust-create`, since they're both scaffolding. Not changing because (a) no urgency, (b) they have different historic reasons for existing, (c) I don't know the full history well enough to know if merging would break a workflow.
- **`djust-admin`** could plausibly merge into the main `djust.org` application, since it's an application. Not changing for the same reasons.
- **`djust-monitor` and `djust-monitor-client`** are two packages in a pair because one emits metrics and the other collects them. That's the correct split — client libraries should be thin and server applications should be separate. Not changing.

### What WOULD be changing if we made a different decision here

If we'd decided "consolidate everything into core djust," we'd be proposing:

1. Merging 64,000 lines of `djust-components` into core. Double the package size. Make installs heavier. Make `grep` in core harder.
2. Breaking plain-Django users of `djust-auth`, `djust-tenants`, and `djust-theming` by forcing them to install djust just to use basic Django features.
3. Forcing every release cadence to match. Theming changes would wait for framework releases; framework releases would wait for theming to stabilize.
4. Creating a `djust` package that's impossibly diverse — a framework, a component library, a theming engine, an auth library, a tenancy library, all in one pip install.

This is clearly wrong. The current split, despite the coordination overhead, is doing real work.

### What if we made the OTHER different decision?

If we'd decided "split the AI features into `djust-assistant`, `djust-generative`, `djust-consent`, `djust-tutorials` following the existing pattern," we'd be proposing:

1. Four new packages to maintain, version, release, and test against each other.
2. Cross-package security review — the consent envelope and the assistant have to coordinate on auth, but they're in different packages with different release cadences.
3. Feature discovery friction — every user of djust has to know about `djust-assistant` to find `AssistantMixin`, and the docs have to explain that "djust supports AI, just install this other package."
4. Dependency resolution pain — `djust-generative` imports from `djust-assistant` imports from `djust`, and the resolver has to keep all three versions compatible.

Also clearly wrong, for different reasons. The AI features are tightly integrated with core and don't meet any of the separation criteria.

The taxonomy correctly splits these two cases. That's the whole reason to have the taxonomy.

## Edge case: Django-generic packages that carry the `djust-` prefix

`djust-auth`, `djust-tenants`, and `djust-theming` are Django packages, not djust packages. They work without djust installed. But their *name* implies otherwise.

This is confusing for users. A Django developer evaluating `djust-auth` has to read the README carefully to figure out whether it requires djust. A djust user evaluating whether to use it has to check that it integrates cleanly with LiveView. Both audiences pay a small comprehension tax because the name suggests one thing and the reality is another.

**Options considered**:

1. **Rename to `django-djust-auth`** — makes the Django-generic nature clear, preserves the brand association. But ugly and awkward.
2. **Rename to `djauth`, `djtenants`, `djtheming`** — drops the prefix entirely, but loses the brand association and creates a new search surface.
3. **Leave the names, add a clear README disclaimer** — zero rename cost, preserves search history, preserves PyPI versioning, preserves all existing users. The README of each package explicitly states "This is a Django package from the djust team. It does not require djust. If you want djust integration, install as `pip install djust-auth[djust]`."

**Decision**: option 3. The rename cost isn't worth the clarity benefit, especially for packages that are already stable (`djust-auth 0.3.0`, `djust-tenants 0.3.0`) and have existing users. The README disclaimer is a 3-line fix that achieves 95% of the clarity benefit.

**Action item**: update the three README files in v0.5.0 with the disclaimer. No version bump required.

## Rules for new packages going forward

When someone proposes a new package, they must score it on the five axes and get at least two clear benefits before creating a new distribution. The default is *submodule in core*, not *new package*.

Specifically:

1. **New runtime features** (tutorials, assistant, generative, consent, etc.) → core submodule under `djust.<feature>/`. Optional extras in core's `pyproject.toml` for vendor deps. No new package.
2. **New Django-generic libraries** (something useful to plain Django users, not just djust) → separate package, with optional djust integration via extras. Document clearly in the README that djust is optional.
3. **New applications** (examples, demos, dashboards) → separate repository, treated as first-party applications, never merged into core.
4. **New tooling** (CLIs, linters, formatters, debug tools) → separate package. Tools never live in runtime packages.
5. **New component libraries** (UI kits, domain-specific component sets) → separate package, dependent on djust core. May use the `djust-components-<scope>` naming convention if it's first-party.
6. **New theming / styling packages** → separate package, same reasoning as components.
7. **New experimental / unstable features** → live in `djust-experimental` rather than core, until they stabilize. Then graduate to core via ADR.

### How to avoid "the framework team ships a feature as a separate package to avoid review process"

A real concern with having rules like this is that they create incentives to game them. If core features go through an ADR and separate packages don't, someone might propose a "separate package" for a feature that's really a core extension, just to skip the ADR.

Mitigation: this ADR's rule applies to *the decision to create a new package*, which is itself now an ADR-requiring decision for anything djust-specific. If you're proposing a new djust-specific package, you must first ADR the decision to make it a separate package rather than a core submodule. That ADR must score against the five axes. Once the ADR is accepted, the package itself can go through normal development.

For Django-generic packages (that don't depend on djust), this ADR requirement doesn't apply — those are community packages that happen to live in the djust team's repo organization, and they should be allowed to move freely.

## Migration plan

**None.** No existing packages move. No new packages are created. The AI features land in core following the rules established above.

The entire implementation cost of this ADR is:

1. Update the three `djust-auth` / `djust-tenants` / `djust-theming` README files with a "This is a Django package, not a djust-only package" disclaimer. ~30 minutes total.
2. When the ADR-002 Phase 1 implementation lands, create the `djust/server_driven/`, `djust/tutorials/`, etc. subpackages per the directory layout above. ~10 minutes of boilerplate.
3. When the Phase 5 (AssistantMixin) implementation lands, add the `assistant-openai` and `assistant-anthropic` extras to `djust/pyproject.toml`. ~5 minutes.

That's it. The ADR is primarily a *decision document* that prevents future mistakes, not a *refactoring document* that changes the current state.

## Alternatives considered

### Alternative 1: Consolidate everything into core

Pull `djust-auth`, `djust-tenants`, `djust-theming`, `djust-components`, and `djust-admin` into core. One giant `djust` package.

**Rejected because**:
- Breaks Django-generic packages' ability to serve plain Django users
- Makes core install unreasonably heavy
- Doubles the core repo's LOC count for mostly-CSS content
- Coordinates release cadences that shouldn't be coordinated
- Hurts discoverability (everything in one namespace is worse than categorized namespaces)

### Alternative 2: Split AI features into separate packages following the existing pattern

Create `djust-assistant`, `djust-generative`, `djust-consent`, `djust-tutorials`. Four new distributions.

**Rejected because**:
- The AI features are tightly coupled to core's event dispatcher, VDOM, and auth systems — the integration surface is load-bearing for security and correctness
- Cross-package versioning is a pure cost with no offsetting benefit (the features aren't independently valuable or Django-generic)
- Discoverability suffers — "djust has AI" is a much stronger story than "djust has four extension packages, one of which adds AI"
- Security audit fragments across package boundaries exactly where the consent envelope needs to span them

### Alternative 3: Everything in a monorepo with namespace packages

Use Python namespace packages to ship one repository with multiple distributions that share a common `djust.*` namespace. Users see one logical package, maintainers see one repository.

**Rejected because**:
- Namespace packages are a maintenance footgun in practice — tooling support is inconsistent, test discovery is finicky, and installation ordering can produce subtle bugs.
- The promised benefit (one logical namespace) is already achievable via submodules in a single distribution.
- Adds complexity without removing coordination cost.

### Alternative 4: Keep all existing packages separate, also split new AI features

Status quo plus four new packages. Uniform treatment of all features.

**Rejected because**:
- Uniform treatment of dissimilar cases is not actually a value — it's just laziness disguised as consistency.
- The taxonomy correctly identifies that the existing separations serve real needs and the new splits wouldn't.
- Four more packages is real ongoing cost; if the benefit doesn't exist, don't pay it.

## Open questions

1. **Should `djust-auth` / `djust-tenants` / `djust-theming` eventually graduate out of the djust-team organization entirely and become purely community-maintained Django packages?** Maybe. They're already structurally Django-generic. If they develop a user base that's majority plain-Django, it might make sense to move them to a neutral organization. Not urgent; revisit when they're 1.0.
2. **Is there a size threshold at which a core submodule should be extracted into a separate package?** In this ADR, "large size" is one of the five axes but it's fuzzy. A concrete threshold would be useful. My lean: no submodule should exceed ~15,000 LOC within core. If `djust.generative` ever grows past that, it graduates to a separate package. This is a soft guideline, not a hard rule.
3. **Should `djust-experimental` have the same submodule / extras pattern?** Maybe. Today it's a separate package because experiments need their own release cadence. But an "experimental" namespace within core could serve the same purpose (`djust.experimental.<feature>`) while being easier to find. My lean: leave it as-is for now; reconsider if the experimental package accumulates more than a handful of features.
4. **What happens to this taxonomy if djust ships a hosted app-builder product?** A hosted product would introduce a new category: user-facing applications that end users build themselves. Those are definitely separate from the framework, but the boundary between "framework primitives" and "product internals" may need clarification. Revisit when such a product has a design.
5. **Do third-party extension authors need guidance?** Yes, but not as an ADR. A short developer-facing doc ("should my djust extension be a separate package?") that cites this ADR and walks through the taxonomy would be helpful. File as a follow-up documentation task.
6. **Should internal dependency relationships between submodules be enforced?** E.g. should `djust.generative.mixin` be allowed to import from `djust.consent.envelope`? In principle yes (they're in the same package), but uncontrolled cross-submodule imports make future package splits harder. My lean: adopt an import-linter rule that submodules can only import from a defined set of peers, enforced in CI. File as a follow-up.

## Revision after review (2026-04-11, same day)

The original analysis above was too conservative about existing packages. On review, the arguments for keeping `djust-auth`, `djust-tenants`, `djust-theming`, and `djust-components` separate do not hold up when examined individually. The taxonomy itself (the five axes) stays — but the scoring for those four packages changes, and so does the recommendation.

### What the original analysis got wrong

**1. "Django-generic" was a theoretical audience for `djust-auth` and `djust-tenants`.**

The original argument: these packages only depend on `django>=4.2`, so plain Django users can consume them, so they should stay separate. Technically true. But:

- `djust-auth` is a repository in a `djust-org` GitHub organization with a `djust-` PyPI name. A Django user searching for auth libraries hits `django-allauth`, `django-axes`, `django-two-factor-auth`, and about thirty others with Django-forward branding before landing on `djust-auth`. The plain-Django audience is near-zero in practice.
- The same is true for `djust-tenants`.
- A *property* that a package has (Django-generic) is different from a *usage pattern* that exists (plain Django users actually consuming it). The original analysis conflated them.

The right question isn't "could a plain Django user use this?" — it's "is there a measurable plain Django audience whose needs would be regressed by folding into core?" For these two packages, the answer is almost certainly no.

**2. "Large size" for `djust-theming` and `djust-components` wasn't load-bearing.**

The original argument: theming (~37K LOC) and components (~64K LOC) were too large to merge into core. But "large" relative to what?

- Core djust is already ~79K LOC.
- Django itself is ~400K LOC. `scikit-learn` is ~700K. `boto3` is ~500K.
- The real costs of large packages are install weight, grep noise, and pip resolution time. Install weight is solved by the extras pattern. Grep noise is solved by subdirectory organization. Pip resolution time is not meaningfully affected by packages under 200K LOC in 2026.

The size concern was real but the *cost* it was trying to prevent didn't survive scrutiny.

**3. "Release cadence" was a workflow concern misframed as a structural one.**

The original argument: theming and components need independent release cadence. But djust core can ship patch releases (`djust 0.5.1`) that bump a theming or components bugfix without touching anything else. The "independent cadence" argument was really "we don't want to cut a core release for every theming fix" — which is a workflow preference, not a framework-architecture constraint.

Monorepo teams ship this pattern every day. If anything, coordinated releases *reduce* user-facing coordination cost because version-compatibility matrices collapse to one dimension.

**4. Independent test suites and CI were a hidden cost I didn't count.**

Each of the four packages currently ships its own test suite, its own CI pipeline, its own release-drafter, its own CodeQL config, its own dependabot setup. That's four parallel pipelines doing the same work four times, plus four release processes a maintainer has to keep in their head.

Merging gets all of this for free: one test runner, one CI matrix, one release train, one security review, one dependabot config. I didn't weigh these merge benefits at all in the original analysis.

**5. Documentation fragmentation was another hidden cost.**

Theming has its own docs site, components has its own, the main djust docs reference them as external. Cross-references between framework features and components (or theming) have to go across site boundaries, which is friction for readers and fragility for link maintenance.

One consolidated docs site (the current `djust.org` documentation) with unified search, unified navigation, and unified cross-references is strictly better.

### Revised scoring for existing packages

Same five axes. Different verdicts for four packages:

| Package | A1 (agnostic) | A2 (deps) | A3 (cadence) | A4 (security) | A5 (size) | Original verdict | **Revised verdict** |
|---|---|---|---|---|---|---|---|
| `djust-auth` | theoretical only | — | — | — | — (670 LOC) | Separate | **Fold into `djust.auth`** |
| `djust-tenants` | theoretical only | — | — | — | — (1.9K LOC) | Separate | **Fold into `djust.tenants`** |
| `djust-theming` | weak | — | weak | — | medium (37.6K LOC) | Separate | **Fold into `djust.theming`** |
| `djust-components` | — | — | weak | — | large (64.2K LOC) | Separate | **Fold into `djust.components`** |
| `djust-admin` | — | — | — | — | small (2.3K LOC) | Separate (application) | **Still separate as application** |
| `djust-create` | — | ✓ | ✓ | — | — | Separate (tooling) | **Still separate as tooling** |
| `djust-scaffold` | — | — | ✓ | — | — | Separate (tooling) | **Still separate as tooling** |
| `djust-monitor` | — | ✓ | ✓ | ✓ | — | Separate (application + deps) | **Still separate as application** |
| `djust-monitor-client` | ✓ | — | — | — | — | Separate | **Still separate (pairs with server)** |
| `djust-experimental` | — | — | ✓ | — | — | Separate (experimental) | **Still separate** |

Four packages fold into core. One stays as an application. Five stay as tooling, applications, or experimental sandboxes. The package count drops from ~18 to ~14; the four that fold are the exact ones a new djust user would expect to get in a single install command.

### What the folded structure looks like

```
python/djust/
├── __init__.py
├── live_view.py              # existing core runtime
├── websocket.py
├── vdom.py
├── event_handlers.py
├── ... (rest of existing core)

├── auth/                     # formerly djust-auth (670 LOC)
├── tenants/                  # formerly djust-tenants (1.9K LOC)
├── theming/                  # formerly djust-theming (37.6K LOC)
├── components/               # formerly djust-components (64.2K LOC)

├── server_driven/            # ADR-002 Phase 1
├── tutorials/                # ADR-002 Phase 3
├── consent/                  # ADR-005
├── assistant/                # ADRs 003, 004, 002 Phase 5
│   └── providers/
└── generative/               # ADR-006
    └── stdlib/
```

Ten top-level subpackages: four absorbed from existing runtime packages, five new from the AI cluster, one reserved for experimental work that graduates later. The existing `djust-admin`, `djust-create`, `djust-scaffold`, `djust-monitor`, `djust-monitor-client`, and `djust-experimental` packages stay separate under the revised taxonomy.

`pyproject.toml` extras become:

```toml
[project.optional-dependencies]
# AI cluster extras (from the AI feature work)
assistant-openai    = ["openai>=1.50"]
assistant-anthropic = ["anthropic>=0.40"]
assistant-all       = ["djust[assistant-openai,assistant-anthropic]"]
assistant-whisper   = ["openai-whisper>=20240930", "torch>=2.0"]
ai                  = ["djust[assistant-all,assistant-whisper]"]

# New: runtime extras absorbing existing packages
auth                = []                               # no new deps — pure Python
auth-oauth          = ["django-allauth>=0.60"]         # formerly djust-auth[oauth]
tenants             = []                               # no new deps
tenants-redis       = ["redis>=5.0"]                   # formerly djust-tenants[redis]
tenants-postgres    = ["psycopg[binary]>=3.1"]         # formerly djust-tenants[postgres]
theming             = []                               # CSS-heavy, not dep-heavy
components          = ["markdown>=3.0", "nh3>=0.2"]    # formerly djust-components' hard deps

# Convenience bundles
standard            = ["djust[auth,tenants,theming,components]"]
all                 = ["djust[standard,ai]"]
```

Install patterns:

```bash
pip install djust                    # lean core only, no extras
pip install djust[standard]          # typical djust app bundle
pip install djust[all]               # everything including AI
pip install djust[auth,tenants]      # explicit minimal selection
pip install djust[ai]                # AI cluster only
```

### What doesn't fold and why

**`djust-admin` stays separate.** It's a Django admin replacement — a first-party *application* that runs on top of djust. Applications have deployment concerns, branding, templates, static assets, and product docs that shouldn't be entangled with framework code. The next time someone asks "is djust an application framework or a Django admin replacement?" we'd have to pick one answer. Keep it separate lets djust-admin evolve as a product while djust core evolves as infrastructure.

**`djust-create` and `djust-scaffold` stay separate.** CLI tooling belongs in tool packages, not runtime packages. This is consistent across the Python ecosystem — Django's runtime library doesn't ship `django-admin startproject` as part of the core library module, it ships it as a separate CLI entry point. Could conceivably merge the two tooling packages with each other (`djust-create` + `djust-scaffold` → `djust-tools`) but that's a separate cleanup, not part of this revision.

**`djust-monitor` stays separate.** It's an observability service — a separate deployable with its own dashboard, storage, and runtime. Same pattern as `grafana-agent` + `grafana`. The client library (`djust-monitor-client`) *could* fold into core as `djust[monitor-client]` since it's a thin metric emitter that's framework-ish in character, but the server stays separate either way. Leaving the client alone for now to avoid scope creep.

**`djust-experimental` stays separate.** The whole point is instability outside core's release guarantees. Merging would force us to extend stability guarantees to features that aren't ready. Features graduate out of `djust-experimental` via ADR when they stabilize.

**Applications stay separate.** `djust.org`, `djust-chat`, `djust-crm`, `djust-notes`, `djustlive`, and `examples.djust.org` are products built on djust, not framework pieces. They live in their own repos because they have their own deployment, branding, release schedules, and lifecycles.

### Migration plan

This is real refactoring work. Being honest about the cost:

**Phase 1 — `djust-auth` and `djust-tenants` (~2-3 days, v0.5.0).** Small packages, minimal test surface, straightforward moves. This is the proving ground for the migration process.

- Move `djust-auth/src/djust_auth/` → `djust/python/djust/auth/`
- Move `djust-tenants/src/djust_tenants/` → `djust/python/djust/tenants/`
- Update imports: rename `djust_auth` → `djust.auth`, `djust_tenants` → `djust.tenants`
- Move tests into core's test suite; unify fixtures
- Update `djust/pyproject.toml` with new extras (`auth`, `auth-oauth`, `tenants`, `tenants-redis`, `tenants-postgres`)
- Cut final versions of standalone `djust-auth 0.4.0` and `djust-tenants 0.4.0` as thin compatibility shims: each module re-exports from the new location and emits `DeprecationWarning` on import
- Document in CHANGELOG

**Phase 2 — `djust-theming` (~1-2 weeks, v0.5.1).** Medium-size move (37.6K LOC, 139 Python files). Real work but tractable.

- Move `djust-theming/djust_theming/` → `djust/python/djust/theming/`
- Update Django app config (`djust_theming.apps.DjustThemingConfig` → `djust.theming.apps.DjustThemingConfig`)
- Migrate CSS generator, design tokens, component CSS generator, gallery, context processors, template tags
- Test migration is the biggest chunk — theming has a substantial test suite that all needs to keep passing
- Add `theming` extra to core's `pyproject.toml`
- Compatibility shim on `djust-theming 0.5.0`

**Phase 3 — `djust-components` (~2-3 weeks, v0.5.2).** Largest move (64.2K LOC, 307 Python files). Most substantial phase.

- Move `djust-components/components/` → `djust/python/djust/components/`
- The `component_showcase/` directory is a demo app, not framework code — move it to `examples.djust.org` or delete
- CSS assets, icon libraries, template tag registration all need coordinated migration
- Test migration is the long pole
- Add `components` extra to core's `pyproject.toml` with its `markdown`, `nh3` deps
- Compatibility shim on `djust-components 0.5.0`

**Phase 4 — compatibility sunset (v0.6.0).** After one full minor cycle of compatibility:

- Archive the standalone `djust-auth`, `djust-tenants`, `djust-theming`, `djust-components` packages on PyPI (don't delete — users can still install them, but no new versions)
- Update their READMEs to direct new users to `pip install djust[auth]` / `djust[tenants]` / `djust[theming]` / `djust[components]`
- Remove the compat shims from the codebase
- Final release of each standalone as a "migration notice only" package

**Total effort**: ~4-6 weeks of focused work spread across the v0.5.x release train. Not trivial, but much less than most refactoring projects of this scope because the source code itself doesn't need to change — it's just being relocated and renamespaced.

### Benefits this unlocks

Three things that fall out of consolidation that the original analysis didn't weigh:

1. **Tests unify.** Each folded package currently has its own test suite that can't see core's fixtures, mock helpers, or test harness. Merging means components' tests get access to `LiveViewTestClient`, theming's tests get access to core's template-rendering fixtures, and everything runs in one `make test` invocation. Fewer tests duplicated; integration coverage naturally improves.
2. **CI collapses.** Four separate CI pipelines → one. Release drafter, CodeQL, dependabot, security audit — configured once, applied uniformly.
3. **Documentation consolidates.** Theming's docs, components' docs, auth's docs, and core's docs become one navigation tree with unified search and cross-references that actually work.

### What about the Django-generic audience?

For `djust-auth` and `djust-tenants`, the original argument was that folding breaks plain Django users. Under the revised plan, those users get a compat shim:

```python
# final djust-auth 0.4.0 release (compat shim)
# djust_auth/__init__.py
import warnings
warnings.warn(
    "djust-auth has been merged into the core djust package. "
    "Install 'djust[auth]' instead. Imports from 'djust_auth' continue to work "
    "in this version but will be removed in v0.6.0.",
    DeprecationWarning,
    stacklevel=2,
)
from djust.auth import *  # noqa: F401,F403
```

Plain Django users install `djust` (which is MIT-licensed, pure Python, no heavy deps without extras) and get the auth module. They pay the cost of one extra pip install versus before, but they get more: the module is part of a coherent framework, better tested, better documented, and maintained with the rest of the runtime.

For `djust-theming`, the same pattern. The plain-Django audience is small but nonzero, and they're well-served by a compat shim period followed by a clean migration.

### Why this revision is binding, not just suggested

The cost of leaving this half-done (some packages folded, some not; or folded for new features but not existing ones) is worse than either of the pure alternatives. A half-consolidated state is harder to reason about than either "everything separate" or "all runtime features in core." If we're going to fold AI features into core as submodules, consistency demands applying the same rule to comparable existing packages.

## Decision

**Recommendation**: accept this ADR as Proposed, including the revision section above. Apply immediately to:

1. **The AI feature cluster (v0.4.2 onward)** — all ADR-002 through ADR-006 features ship in core submodules with optional extras. Starts as soon as Phase 1 of ADR-002 is scheduled.
2. **`djust-auth` and `djust-tenants` (v0.5.0)** — fold into `djust.auth` and `djust.tenants`, ship compat shims, deprecation cycle.
3. **`djust-theming` (v0.5.1)** — fold into `djust.theming`, ship compat shim, deprecation cycle.
4. **`djust-components` (v0.5.2)** — fold into `djust.components`, ship compat shim, deprecation cycle.
5. **Compat sunset (v0.6.0)** — remove shims, archive standalone packages.

### Binding decisions

1. All ADR-002 through ADR-006 features ship in core djust under `djust.server_driven/`, `djust.tutorials/`, `djust.consent/`, `djust.assistant/`, and `djust.generative/` submodules.
2. Vendor SDKs (OpenAI, Anthropic, Whisper) are optional extras in core's `pyproject.toml`, not separate packages.
3. `djust-auth`, `djust-tenants`, `djust-theming`, and `djust-components` fold into core as optional extras over the v0.5.x release train per the migration plan.
4. `djust-admin`, `djust-create`, `djust-scaffold`, `djust-monitor`, `djust-monitor-client`, `djust-experimental`, and all application repos stay separate.
5. Future runtime features default to core submodules. Creating a new separate package for a djust-dependent feature requires its own ADR justifying the split.

### Non-binding guidance

- The folded subpackages should each be reviewed against a soft ~15,000 LOC ceiling within core. `djust.components` will exceed this at ~64K LOC — that's expected and acceptable for a content-heavy subpackage, but new large additions should be considered for extraction.
- Third-party extension authors get a short developer-facing doc explaining the taxonomy, not an ADR.
- Do not start the migration (Phase 1 of the folding plan) until ADR-002 Phase 1 has shipped in v0.4.2 and the AI cluster work has real usage data. The two projects should not compete for review bandwidth.

## Changelog

- **2026-04-11**: Initial draft. Proposed. Written after auditing the 18-package workspace.
- **2026-04-11**: Revised same-day after review feedback. Original analysis was too conservative; `djust-auth`, `djust-tenants`, `djust-theming`, and `djust-components` will fold into core as optional extras across v0.5.x. Taxonomy rule unchanged; scoring corrected; migration plan added.
