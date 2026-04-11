# ADR-007: Package Taxonomy and Consolidation Strategy

**Status**: Proposed
**Date**: 2026-04-11
**Deciders**: Project maintainers
**Target version**: v0.5.0+ (rules apply going forward; existing packages stay where they are)
**Related**: [ADR-002](002-backend-driven-ui-automation.md), [ADR-003](003-llm-provider-abstraction.md), [ADR-006](006-ai-generated-uis-with-capture-and-promote.md)

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
4. **What happens to this taxonomy if djust becomes the framework behind djust Studio (per the internal monetization plan)?** Studio introduces a new category: user-facing applications that end users build. Those are definitely separate from the framework, but the boundary between "framework primitives" and "Studio internals" may need clarification. Revisit once Studio has a design.
5. **Do third-party extension authors need guidance?** Yes, but not as an ADR. A short developer-facing doc ("should my djust extension be a separate package?") that cites this ADR and walks through the taxonomy would be helpful. File as a follow-up documentation task.
6. **Should internal dependency relationships between submodules be enforced?** E.g. should `djust.generative.mixin` be allowed to import from `djust.consent.envelope`? In principle yes (they're in the same package), but uncontrolled cross-submodule imports make future package splits harder. My lean: adopt an import-linter rule that submodules can only import from a defined set of peers, enforced in CI. File as a follow-up.

## Decision

**Recommendation**: accept this ADR as Proposed, apply immediately to the AI feature cluster implementation (Phase 1 of ADR-002 starts now for v0.4.2).

### Binding decisions

1. All ADR-002 through ADR-006 features ship in core djust under `djust.server_driven/`, `djust.tutorials/`, `djust.consent/`, `djust.assistant/`, and `djust.generative/` submodules.
2. Vendor SDKs (OpenAI, Anthropic, Whisper) are optional extras in core's `pyproject.toml`, not separate packages.
3. No existing packages move. No existing packages are renamed. No existing packages are split.
4. New Django-generic packages may continue to exist in the djust workspace but must have their Django-generic nature prominently documented.
5. Future runtime features default to core submodules. Creating a new separate package for a djust-dependent feature requires its own ADR.

### Non-binding guidance

- `djust-auth`, `djust-tenants`, and `djust-theming` get README disclaimers in v0.5.0 clarifying that they're Django packages.
- Submodules should stay under ~15,000 LOC within core; larger subtrees should be reviewed for extraction.
- Third-party extension authors get a short developer-facing doc explaining the taxonomy, not an ADR.

## Changelog

- **2026-04-11**: Initial draft. Proposed. Written after auditing the 18-package workspace and finding that the existing splits are mostly defensible, while the proposed AI-feature splits would have been mistakes.
