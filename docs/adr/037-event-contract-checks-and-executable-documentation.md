# ADR-037: Shared event-contract checks and executable documentation

**Status**: Accepted — delivery verified on a local djust-docs build pinned to 1.3.0rc3 that renders the 1.3 branch's docs (2026-09-25; the docs published with 1.3.0rc3 failed djust-docs' nav gate on the unlisted accounts guide, fixed in #3138; production docs.djust.org follows 1.3.0). D1–D3 and Step R are closed, with evidence in the [implementation tracker](component-conventions-implementation.md#adr-037--checks-and-executable-documentation). Step R's deletion PRs are #3122 (rows 1–23) and the ADR-037 D3 PR (rows 24–26). The static-analysis limits at acceptance are recorded below.
**Date**: 2026-09-19
**Deciders**: Project maintainers
**Evidence baseline**: `0d1aeb882` on `feat/components-catalogue`.
**Related**:

- [ADR-034](034-component-scoped-events-and-bindings.md): typed component scopes and optional notifications.
- [ADR-035](035-django-native-form-and-object-lifecycle.md): managed form/object lifecycle.
- [ADR-036](036-typed-event-parameter-contracts.md): canonical argument contracts and migration.
- [ADR-023](023-incremental-type-enforcement.md): static Python type enforcement.
- [ADR-038](038-explicit-context-and-state-exposure.md): declared context and persistence projections.

## Summary

Use the existing Django/djust check infrastructure to validate the same event
contracts the runtime uses. Make examples executable fixtures shared by catalogue
demos, website documentation, and AI guidance. Report the limits of static analysis
instead of presenting an unchecked dynamic binding as verified.

This is an extension and consolidation of existing tools, not a proposal to create
another `djust_check` command. New checks, coverage reporting, and documentation
fixture integration remain unimplemented.

## Context

The catalogue's missing `toggle_menu` host handler was visible only after clicking
the demo. Other reported failures included whitespace loss, incorrect styling,
and missing dropdown items. They require different checks: static wiring analysis
can catch some routing problems, but not every rendered or interactive failure.

At the evidence baseline:

- [Django system checks](../../python/djust/checks/__init__.py) and
  [djust_check](../../python/djust/management/commands/djust_check.py) already exist.
- [V004](../../python/djust/checks/components.py) heuristically notices methods
  missing an event decorator. V007 recommends `**kwargs` on every handler.
- [djust_typecheck](../../python/djust/management/commands/djust_typecheck.py) and
  T018 check template context names, not a complete event/argument binding graph.
- [check-handler-contracts.py](../../scripts/check-handler-contracts.py) checks
  framework tag event defaults against method names; this is not a complete
  application-template ownership analysis.
- [check-doc-snippets.py](../../scripts/check-doc-snippets.py) checks syntax and
  imports for selected docs. It explicitly does not execute the snippets.
- [AI event guidance](../ai/events.md) recommends generic data attributes, default
  values, and catch-all kwargs. These can conflict with the proposed strict
  contracts. The catalogue, guide, and runtime must not teach different defaults.

## Decision

### D1. Reuse one contract model and the existing commands

Extend the registered Django checks used by `manage.py check` and `djust_check`.
Do not introduce a parallel scanner with independently maintained handler names,
parameter rules, or output lists. Reuse ADR-036's resolved handler contract and
ADR-034's declared component actions, outputs, and observation routes.

The model must describe public arguments, required/defaulted values, types,
trusted framework injection, open versus closed payloads, ownership, and version
availability. Inherited and decorated methods use their effective callable
contracts, not just function-name regexes or the outer wrapper's `**kwargs`.

Normal Django application discovery/import is allowed. The checks themselves must
not run `mount()`, execute handlers, evaluate querysets, or perform network calls
to infer a contract. Dynamic fixtures belong in explicit tests, not startup checks.

### D2. Check ownership before checking the method name

Associate each template with its effective view or component. Follow statically
resolvable inheritance/includes and retain source locations. A generic template
may have several owners; validate each known binding rather than guessing one.

Resolve literal event bindings using the intended owner:

- A view-owned action must resolve to a permitted client-callable view handler.
- A scoped component action must resolve within that bound component. A similarly
  named host method is not a successful substitute.
- A component semantic output must resolve through its declared subscription,
  not be treated as another public browser-callable method.
- An optional client observation from ADR-034 needs no host callback when no
  subscription exists; it should produce no notification binding in that case.
- Native browser-only controls need no server handler merely because they toggle
  visibility. Do not flag their intentional lack of a server event.

Use template structure and source maps rather than a single regex over HTML.
Component-generated markup needs declared metadata and rendered fixture tests;
do not execute arbitrary Python renderers inside a system check to inspect it.

### D3. Validate parameters without recommending catch-alls everywhere

For statically understood bindings, check:

- Handler existence and exposure/decorator eligibility.
- Required argument presence, known unexpected arguments, and duplicate normalized
  names, accounting for standard event-provided values such as input `value`.
- Explicit wire-type conflicts and invalid literal values under the resolved
  parameter policy. Unknown template expression types remain unknown.
- Whether a client payload attempts to provide a trusted injected argument.
- Known form fields and component output signatures when their schemas are
  available without constructing request-dependent application objects.

Retire V007's blanket "add `**kwargs`" advice. A closed signature is encouraged,
not suspicious. Explicit `**form_data` and intentional passthrough handlers remain
valid, but the diagnostic output must say that unknown arguments cannot be fully
checked. Catch-alls do not excuse known conflicting or misspelled named inputs.

Defaults represent genuinely optional values, not a way to hide missing IDs.
Registration checks report unresolved annotations or unsupported strict types
before an end user triggers them. Ordinary mypy/Pyright checks remain necessary
for typed Python component references; this does not replace them.

### D4. Give actionable diagnostics and honest coverage

Use the existing diagnostic families and allocate unused stable IDs during
implementation. The feedback's illustrative E001/E002/E003 labels are not newly
assigned check IDs. Do not collide with or silently reinterpret existing IDs.

Each finding includes source file/line, owning view/component, binding/handler,
expected and supplied arguments where known, severity, and a correction. Provide
the same facts in the existing machine-readable output for editors and agents.
An IDE integration can render them; merely adding a system check does not itself
create editor red squiggles.

Report each discovered binding as checked, dynamic/unresolved, or unsupported,
and summarize counts. A check that skipped a dynamic include must not claim that
the page's event graph is complete. Recognized literal contradictions can be
errors; heuristic or unresolved cases need honest diagnostics and targeted tests,
not fabricated certainty or universal startup failures.

Suppressions must be local and carry a reason. CI pins the coverage of canonical
examples and requires fixtures for intentionally dynamic bindings. A new skipped
canonical example is a regression even when there are no reported static errors.
Do not add an unconditional autofix that creates an empty handler or `**kwargs`
just to make a warning disappear.

### D5. Distinguish rendering, persistence, and disclosure checks

Do not ban every public model or QuerySet. The native context pipeline supports
authorized ORM values for rendering, while public snapshot persistence has
different restrictions. [Existing snapshot regression tests](../../python/djust/tests/test_state_snapshot_orm_early_validation.py)
explicitly distinguish these paths.

Checks should identify the actual risk: unsupported persistence of a live ORM
object, a service object exposed as state, missing sensitive-field exclusions, or
an authorization gap. Keep heuristic serialization warnings distinct from proven
errors. ADR-035's managed `self.object` needs explicit framework metadata so it is
not incorrectly flagged as an ordinary public snapshot field. For ADR-038 explicit
views, derive these checks from declarations, not attribute-name heuristics.

### D6. Publish runnable examples from tested fixtures

Keep canonical Python/HTML examples in importable test/example fixtures in the
repository. Generate or extract the corresponding code blocks and API tables for
the website, catalogue, and AI references. Prose can differ by audience; method
names, signatures, imports, and supported versions must not.

The fixture contract includes:

- View/component classes, template, URL registration, and required settings.
- The target release/policy, dependencies, sample data, and expected events/output.
- Authorization/validation behavior relevant to the example.
- Browser expectations where appearance, focus, timing, or native UI is claimed.

Classify documentation blocks explicitly as runnable example, fragment, proposed
API, or intentional anti-pattern. Proposed ADR snippets may be syntax-checked
without resolving nonexistent future imports. Do not publish them as current
usage. Fragments must point to a complete fixture where appropriate; a blanket
skip marker is not proof that the example works.

Build checks compare extracted docs against fixtures and fail on drift. Test
environments declare their required optional dependencies rather than silently
skipping an example because a package is missing. Use isolated uv-managed test
environments when multiple worktrees would otherwise import different checkouts.

### D7. Use layered tests and verify the actual website

| Layer | What it establishes |
| --- | --- |
| Syntax/import and Python type checks | Symbols and typed Python contracts are valid for the stated release. |
| Django contract checks | Statically resolvable template routes and arguments agree with their owners. |
| Runtime tests | Validation, authorization, lifecycle, and dispatch work over the relevant paths. |
| Render/browser tests | The user sees content and can interact correctly, with focus/accessibility and no server errors. |
| Published-site checks | The correct examples are present and discoverable in the intended site's navigation. |

None substitutes for the next. Initial HTTP 200 or a catalogue-wide static scan
does not prove that every control works.

The first regression fixtures should reproduce the reported catalogue failures:
code whitespace survives rendering; checkbox checked/disabled/focus states have
the intended styling; dropdown options exist; opening/selecting works; two menus
do not mutate each other; optional client notifications follow ADR-034's no-op
contract. Compare supported themes/modes explicitly and label the tested subset.

Browser checks should capture visible results and server/client errors, not only
inspect handler presence. Restore/reconnect behavior needs a real reconnect test.
For native popovers/dialogs, test default-action interaction with `dj-click`,
keyboard operation, dismissal, focus restoration, and preservation through patches.

## Alternatives considered

| Alternative | Assessment |
| --- | --- |
| Browser tests alone | Necessary for behavior, too late and costly for every obvious wiring typo. |
| A new regex linter with its own schemas | Easy to start, but duplicates contracts and misidentifies component ownership. |
| More handwritten prose/examples | Useful explanations, but no mechanical protection against divergent signatures. |
| One contract model, existing checks, and fixture-backed docs | Chosen: early actionable diagnostics plus measured runtime/browser coverage. |

## Rollout and compatibility

1. Inventory existing checks/fixtures and establish checked versus unresolved
   coverage. Preserve current check IDs, output consumers, and suppression behavior.
2. Add shared contract extraction and high-confidence view-owned event checks.
   Integrate with ADR-036's strict-policy migration rather than rejecting legacy
   argument conventions indiscriminately.
3. Add component action/output/observation ownership from ADR-034 and managed form
   metadata from ADR-035. Keep unresolved dynamic cases visible until covered.
4. Convert a small canonical fixture set and its website/AI references together;
   verify the actual site and navigation before broadening the catalogue sweep.
5. Make the verified contract/fixture checks CI gates, then expand coverage without
   claiming untested components or modes are clean.

This ADR does not promise comprehensive checks in one release. New error-level
checks require demonstrated high confidence and migration guidance; heuristics
must not suddenly prevent existing applications from starting.

## Scope and acceptance gates

At the baseline, lexical references to
`djust_check|check_liveviews|djust_typecheck|V007|check_handler_contracts` occur in
**12 source files and 17 test files** across Python/JavaScript sources and the
three test roots. These are reference counts, not complete implementation scope.

Acceptance requires negative tests for missing handlers, wrong ownership, missing
and extra arguments, invalid literals, inherited/decorated methods, and forged
injection. Include positive tests for intentional catch-alls, native controls,
unobserved outputs, managed form objects, and authorized QuerySet rendering.

Test includes/inheritance, shared templates, dynamic bindings, source locations,
suppression reasons, and machine-readable output. Prove the checker does not run
application mounts/handlers or evaluate querysets. A canonical fixture deliberately
broken in each test layer must fail its corresponding gate; skipped fixtures and
website navigation omissions must be visible.

## Retirement (Step R — delete)

This ADR is framed above as "an extension and consolidation of existing tools,
not a proposal to create another `djust_check` command". Consolidation earns a
delete gate on ADR-027's playbook; an extension does not. Which of the two this
is cannot be settled from the proposal alone, so **D1 must decide it and record
the answer here** rather than leaving it implied.

**Required at D1**, before any check ships:

1. Enumerate the contract logic the shared checks would replace — any place that
   re-derives handler parameters, ownership or event names independently of the
   runtime contract, cited `file:line`.
2. For each, state `RETIRE` with a deletion PR, or `KEEP` with the reason it is
   genuinely distinct from the shared contract.
3. If the enumeration is empty, record that plainly: this ADR is then an
   addition, justified on the checks' value, and claims no saving.

Documentation fixtures are a separate question. Examples that become executable
fixtures may retire hand-maintained duplicates in the catalogue and website; D2
names those files or records that there were none.

**Exit conditions.** The D1 table above, filled in, with every `RETIRE` row
carrying a merged deletion PR before D3 acceptance.

**Documentation fixtures (D2, 2026-09-25).** The fixtures retired three
hand-maintained duplicates: `test_adr034_documented_examples.py`,
`test_adr035_documented_examples.py` and `test_adr036_documented_examples.py`.
Each re-implemented the same extractor, pairing, loader and page driver. They now
run as scenarios in `python/djust/tests/doc_scenarios/` on one harness,
`python/djust/tests/_doc_examples.py`. The catalogue's interactive `DropdownMenu`
entry has no copy to retire: its usage section is the source of the canonical
example module (`djust.components.interactive_examples`), which the harness
executes.

### D1 retirement table (2026-09-25)

**Answer: consolidation.** The enumeration is not empty, so this ADR claims a
saving: every `RETIRE` row below moves onto one shared discovery in
`python/djust/_parameter_metadata.py`, which the runtime and the checks both use.
Each retirement is its own commit on the ADR branch, and the branch's PR is the
deletion PR at merge.

All rows are owner decisions (2026-09-25).

| # | Location at `2553ae410` | What it derives | Decision | Reason |
| --- | --- | --- | --- | --- |
| 1 | `python/djust/checks/components.py:474–510` (V007) | Handler signatures, recommending `**kwargs` on each | RETIRE | D3: a closed signature is encouraged. `djust.V007` is retired and never reused. |
| 2 | `python/djust/checks/components.py:378–405` (V004) | Undecorated methods whose names look like handlers | KEEP | It finds methods no template references, which the binding checks cannot see. Info level. |
| 3 | `scripts/check-handler-contracts.py` | Framework tag event defaults against handler names (hardcoded `_APP_LEVEL_EVENTS`) | KEEP | It checks framework-internal tag markup, which needs declared tag metadata (D2) that does not exist yet. It retires once tags declare their events. |
| 4 | `python/djust/testing.py:1451–1523` `_get_handlers` | Handler names and parameters through `inspect.signature`, including undecorated methods | RETIRE | Onto the shared discovery and contract metadata. Its consumers are checked for a public-contract break first. |
| 5 | `python/djust/management/commands/djust_audit.py:58–93` `_get_handler_metadata` (also used by `schema.py:1619`) | Handler names through `dir()` and `getattr` | RETIRE | Onto the shared discovery. |
| 6 | `python/djust/checks/parameters.py:38–63` `_declared_handlers` | A class-level mirror of the runtime's `_event_methods` | RETIRE | The runtime and the checks call one function. |
| 7 | `python/djust/audit_ast.py:524`, `python/djust/checks/security.py:584` | Event-handler decorators in source ASTs | KEEP | Security audits read source without importing it, by design. |
| 8 | `python/djust/validation.py` legacy coercion | Legacy parameter handling | — | Cross-reference only: ADR-036's PR gate. |
| 9 | `python/djust/mixins/handlers.py:30–80` | Runtime handler metadata through `dir(self)`, with its own strict-contract override | RETIRE | Onto the shared discovery and one shared `handler_metadata()`. |
| 10 | `python/djust/mixins/post_processing.py:105–135` | Debug-panel handler list through `dir()` and the legacy signature derivation | RETIRE | It shows the wrong parameters for strict handlers. |
| 11 | `python/djust/api/registry.py:30–60`, `:170–195` | Exposed handlers and server functions through `dir()` | RETIRE | Onto the shared discovery. |
| 12 | `python/djust/hot_view_replacement.py:112–127` `_list_event_handlers` | A class's own handlers | RETIRE | Onto the shared discovery limited to that class. |
| 13 | `python/djust/mcp/server.py:356–470` `find_handlers_for_template` | Its own `dj-*` regex and basename template matching | RETIRE | Onto the D1 binding extractor and template-owner resolution. |
| 14 | `python/djust/theming/gallery/catalogue.py:454–466` `component_events` | A `dj-*` regex over rendered HTML | RETIRE | Onto the D1 binding extractor. |
| 15 | `scripts/generate-interactive-reference.py:113` | Handler decorators on interactive classes | RETIRE | Onto the shared discovery. |
| 16 | `python/djust/checks/templates.py:118`, `:260` (T012, T002) | Whether any event directive is present | KEEP | They never resolve a name. |
| 17 | `python/djust/websocket.py:4997` | The server-push gate for one resolved name | KEEP | A security rule on an already-resolved handler, not discovery. |
| 18 | `python/djust/validation.py:66–101` `recovery_handler_names` | `dj-auto-recover` targets by a regex over the view's own template source | RETIRE | Onto the template scan, which follows includes and parents and keeps every `{% if %}` branch. More handlers are legacy-forced from mount. |
| 19 | `python/djust/validation.py:103` `note_rendered_recovery_targets` | `dj-auto-recover` targets in each render's HTML | KEEP | Different input (the render, including computed targets) on the hot path. A test pins it to the binding parser. Since #3127 it runs only for a view whose template the row-18 scan cannot fully see, so user HTML in a fully scanned template cannot add a target. |
| 20 | `python/djust/templatetags/live_tags.py:1375` `_LIVE_RENDER_EVENT_ATTRS` | A hand-written list of event directives for the embedded-child stamp | RETIRE | Derived from `DIRECTIVES` (directives whose client binding attaches owner context) plus `dj-hook`. |
| 21 | `python/djust/schema.py:21` `DIRECTIVES` | The AI schema's directive table | KEEP | Prose for AI guidance. A test pins its event-directive names and generated parameters to `_template_bindings.DIRECTIVES`. |
| 22 | `python/djust/checks/templates.py:985–1000` (T010) | `dj-click` with navigation data attributes | KEEP | A heuristic, not name resolution. |
| 23 | `python/djust/components/gallery/views.py:589` | The catalogue preview's JS shim reading `dj-click` | KEEP | Revisited at D2: the interactive catalogue entry is served by its own example view (`djust.components.interactive_examples`), so the shim is not involved. |
| 24 | `python/djust/mcp/server.py:970` `validate_view` | Handler signatures, warning that an event handler "should accept `**kwargs`" | RETIRE | D3: a closed signature is encouraged; `manage.py check` T020 compares bindings with handlers. |
| 25 | `python/djust/mcp/server.py:1145` `detect_common_issues` (`missing_kwargs`) | The same rule, as an AI-facing lint | RETIRE | Same reason as row 24. |
| 26 | `python/djust/schema.py` `BEST_PRACTICES` (the `event_handlers` rules, `event_handler_signature`, pitfall 3) and `docs/ai/events.md:3` | AI guidance stating the V007 rule as a requirement | RETIRE | The guidance now states the T020 contract, including the legacy `field`/`_target` case. |
| V020 | `python/djust/checks/components.py:1665` | Interactive declarations on actor views | KEEP | It reads the runtime's own declarations and enforces ADR-034 decision Q5. |
| Q004 | `python/djust/checks/quality.py:213` | Imports of both `DropdownMenu` classes | KEEP | Import hygiene; no handler names, parameters or ownership. |
| S013 | `python/djust/checks/security.py:979` | Edit views with no row scoping or object permission | KEEP | An authorization policy the binding checks cannot express. |

Rows 24–26 were found at D3 planning (2026-09-25). The D1 enumeration covered checks and dispatch, but not the MCP tools or the AI schema's prose. Their deletion PR is the ADR-037 D3 PR.

Row 4's terms: `_get_handlers` keeps its name as a thin adapter over the shared
discovery. Undecorated methods stop being fuzzed, since dispatch refuses them.
Strict handlers fuzz with their contract metadata. Components and server
functions stay excluded, as before.

Row 9's published metadata deliberately still includes public methods that
carry djust decorator metadata without `@event_handler` (a `@debounce` alone),
as the retired `dir()` walk did: `declared_handlers(..., decorated=True)`. A test
keeps the walk as an oracle and pins equality on every framework and demo view.

Row 20's verification (`tests/playwright/test_embedded_directives.py`, pinned
transports): `dj-shortcut` and `dj-click-away` inside an embedded child reach the
child through the stamped wrapper. It found two defects the list does not decide:

- `dj-paste` attached no owner context. Fixed on this branch (owner decision).
- Over HTTP-only, every event from an embedded child reached the root view
  (#3104). Since the #3104 follow-up the HTTP fallback refuses such an event
  ("Embedded view not found"), as the socket runtime refuses an unknown
  `view_id`, and the test expects the refusal. Routing it to the child over
  HTTP is still open in #3104.

Row 13's output (N1): `find_handlers_for_template` keeps its JSON keys, computed
from the D1 extractor and real loader resolution (includes and parents). It gains
a `coverage` object and a per-binding `status`.

Examined and not rows: these read the metadata of one already-resolved handler.
They consume the runtime contract rather than deriving it:

- `python/djust/validation.py` (policy, coercion and signature of the handler being called)
- `python/djust/rate_limit.py` (`@rate_limit` settings of the called handler)
- `python/djust/auth/core.py` (`@permission_required` of the called handler)
- `python/djust/api/dispatch.py` (`expose_api` / `@server_function` metadata of the routed handler)
- `python/djust/websocket_utils.py` (event security and coercion of the resolved handler)
- `python/djust/mixins/request.py` (the HTTP fallback's resolved handler)
- `python/djust/runtime.py` (`@cache` and decorator metadata of the dispatched method)
- `python/djust/time_travel.py` (whether a replayed handler is an event handler)

## D1 decisions (2026-09-25)

Owner decisions on the public output and markers this ADR left open.

| # | Question | Decision |
| --- | --- | --- |
| Q1 | Binding check IDs | `djust.T019` (a binding names no handler on its owner, or names a component output or subscription callback), `T020` (missing, unexpected or duplicate argument), `T021` (invalid literal or wire-type conflict), `T022` (a payload supplies a trusted framework argument). All Warnings in 1.3. |
| Q2 | Coverage | `djust_check --format json` gains a top-level `coverage` object: totals and per-template entries with file, line and binding status. Text output prints one summary line. |
| Q3 | Finding fields | `--format json` findings gain optional `owner`, `binding`, `expected` and `supplied`. The legacy `--json` output is unchanged. |
| Q4 | Suppression | The new IDs require a reason: `{# noqa: T019 -- <reason> #}` (Python: `# noqa: T019 -- <reason>`). A bare `noqa` does not suppress them, and the diagnostic says why. Existing IDs keep their behavior. |
| Q5 | Documentation blocks | `<!-- doc-snippet-check: fragment of=<path>#<heading> -->` and `<!-- doc-snippet-check: proposed -->` join `skip` and `anti-pattern`. Unmarked examples are executed. A drift report counts unexecuted blocks; it does not fail in 1.3. |
| Q6 | AI schema | `schema.py` teaches `ModelFormMixin[Model]` for edits and `FormMixin` with a `ModelForm` for creates, lists `ModelFormMixin` in `OPTIONAL_MIXINS`, and adds an interactive `DropdownMenu` pattern. Each example is executed by a test. |
| Q7 | MCP scaffold | A separate `edit` feature for `scaffold_view` generates a `ModelFormMixin[Model]` view. Existing features are unchanged. The output is executed by a test. |
| Q8 | Catalogue entry | A new `"view"` entry kind backed by `components/gallery/live_views.py`; its usage snippet is generated from the class. |
| Q9 | Form field checks | Only against a static `form_class`'s `base_fields`. A dynamic `get_form_class()` is reported as dynamic. |
| DD | djust-docs symbol check | Public `djust.components.interactive.outputs_of(cls) -> tuple[str, ...]` in djust, with typing-proof coverage and docs. djust-docs learns `@<name>.on.<output>` on a separate branch that merges after the djust release shipping `outputs_of`. |

## Static-analysis limits at acceptance (2026-09-25)

The binding checks report what they could not decide; they do not guess. At
acceptance, a binding is left **unsupported** or **dynamic** (it is listed in
`manage.py djust_check` coverage, never silently passed) in these cases:

- **Outside the live root** — unsupported (`python/djust/checks/bindings.py:184`).
- **Inside markup a component or an embedded child view owns**, seen from the
  host template — dynamic (`bindings.py:186`). The owner's own template is
  checked separately.
- **A name or value the template computes** (`dj-click="{{ action }}"`, a JS
  command list) — dynamic (`bindings.py:192`).
- **A component directive that always reaches its host view** — dynamic
  (`bindings.py:197`).
- **An owner that resolves attributes dynamically** (`__getattr__`) — dynamic
  (`bindings.py:206`).

Some owners are **not scanned at all**. Each is listed in the coverage report with
its reason:

- a view that overrides `get_template()` or `get_template_names()`: "the template is
  chosen at runtime" (`python/djust/checks/bindings.py:140-144`);
- an owner with no `template` or `template_name` (`bindings.py:146-147`);
- an owner whose own template cannot be compiled (`bindings.py:149-150`).

Whole templates or regions become **gaps**:

- `{% extends %}` with a variable, or nested past `_MAX_DEPTH`
  (`python/djust/_template_bindings.py:364-365`, reported as "names a dynamic
  template");
- `{% include %}` with a variable or filters, or nested past `_MAX_DEPTH`
  (`_template_bindings.py:465-466`);
- a template that fails to load or parse (`_template_bindings.py:378`).

Only templates that belong to a `LiveView` or `LiveComponent` class are scanned. A
template another view renders with `render()` or a third-party tag's markup is out
of reach.

Two limits outside the checks, recorded at D2 and D3:

- **Executable documentation covers the ADR surface only.** Of `docs/website`'s
  665 Python blocks, 8 run as fixtures, 467 are parse- and import-checked
  (`scripts/check-doc-snippets.py` reads `guides/*.md` only), and 190 are not
  checked at all (`scripts/doc-examples-report.py`).
- **Over HTTP-only, an embedded child's events do not reach the child** (#3104).
  The HTTP fallback refuses them instead of running them on the root view: an
  HTTP request registers its children only while it renders, after dispatch,
  and under new process-wide `child_N` ids, so no child the client addressed
  exists to route to. `tests/playwright/test_embedded_directives.py` expects
  the refusal.

## Consequences and non-goals

The framework becomes easier for developers and AI agents to learn because one
contract is reflected in its code, checks, and examples. The cost is maintaining
shared metadata and real fixture coverage. Static checks cannot prove arbitrary
Python business logic, authorization correctness, every dynamic template, or visual
quality. This proposal makes those limits explicit rather than replacing browser
testing with a reassuring but incomplete green check.
