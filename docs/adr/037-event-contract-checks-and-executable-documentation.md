# ADR-037: Shared event-contract checks and executable documentation

**Status**: Proposed
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

## Consequences and non-goals

The framework becomes easier for developers and AI agents to learn because one
contract is reflected in its code, checks, and examples. The cost is maintaining
shared metadata and real fixture coverage. Static checks cannot prove arbitrary
Python business logic, authorization correctness, every dynamic template, or visual
quality. This proposal makes those limits explicit rather than replacing browser
testing with a reassuring but incomplete green check.
