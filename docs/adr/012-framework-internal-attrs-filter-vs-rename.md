# ADR-012 — Framework-internal attrs: filter, not rename

- **Status**: Accepted (2026-04-24)
- **Supersedes**: none
- **Related**: #762 (filter implementation, shipped v0.5.7), #962 (this decision)
- **Milestone**: v0.7.2 (close-without-code)

## Context

djust `LiveView` subclasses carry roughly 25 framework-set attributes on `self`
— things like `sync_safe`, `login_required`, `template_name`, `require_htmx`,
`_djust_decorators`, etc. Before v0.5.7 these leaked into `self.get_state()`,
inflating the reactive-state debug payload, the `debug_state_sizes()`
observability metric, and the client-side state mirror.

#762 (v0.5.7) addressed the leak with a non-breaking fix:

```python
# python/djust/live_view.py
_FRAMEWORK_INTERNAL_ATTRS: frozenset = frozenset(
    {"sync_safe", "login_required", "template_name", ...}  # ~25 entries
)

def get_state(self) -> dict:
    return {
        k: v for k, v in vars(self).items()
        if k not in _FRAMEWORK_INTERNAL_ATTRS and ...
    }
```

The filter approach ships; the attrs no longer leak. #962 asked whether
we should additionally **rename** these attributes to a `_*`-prefixed form
(e.g. `self.sync_safe` → `self._sync_safe`) as defense-in-depth.

## Decision

**Keep the filter. Do not rename.**

The filter shipped in #762 is sufficient for every known failure mode we
care about. Renaming would:

- **Break every user view that reads or sets** `self.login_required`,
  `self.template_name`, `self.sync_safe`, etc. These are documented
  first-class view attributes in our user-facing guides (auth guide,
  forms guide, caching guide). Django itself treats `template_name`
  as public API; renaming in djust creates a Django-adjacent surprise.

- **Have no net defense-in-depth benefit**. The filter is a single,
  centralized gate on the exact point where leakage matters
  (`get_state()` + downstream serializers). Prefix-renaming would
  distribute the "this attr is internal" signal across 25 attribute
  sites, and every new internal attr added in future would still
  need to be added to the filter OR renamed — the filter remains
  the source of truth regardless.

- **Not catch new classes of bugs**. The bug class that #762 solved
  was "framework attr silently appears in state payload." Renaming
  doesn't close that class; the filter does. Renaming would only
  prevent a user from writing `self.sync_safe = False` and
  *silently shadowing the framework default* — a legitimate
  override pattern we want to keep supported.

## Consequences

### Positive

- No user-code breakage. `self.login_required = True`, `self.template_name = "x.html"` etc. continue to work.
- One canonical source of truth for "framework-internal attr" — the `_FRAMEWORK_INTERNAL_ATTRS` frozenset.
- Mental model for view authors stays simple: public attrs are yours to read/write; `_`-prefixed attrs are framework internals (same Python convention).

### Negative

- The filter requires maintenance: any new framework-set attr must be added to `_FRAMEWORK_INTERNAL_ATTRS` at the time it's introduced. Missing it = re-introduces the v0.5.7 leak class. Existing CI does not enforce this — new attrs missed in review will only be caught if they produce observably wrong payloads.
- No hard barrier against `self.template_name = "something weird"` — user code can still overwrite the framework's intended value. This has always been the Django pattern (`View.template_name` is public) so the decision is consistent with the Django contract.

### Mitigation

Add `_FRAMEWORK_INTERNAL_ATTRS` to the PR review checklist in `docs/PULL_REQUEST_CHECKLIST.md` (or equivalent): *"If you added a new framework-set attribute on LiveView/LiveComponent, does it also appear in `_FRAMEWORK_INTERNAL_ATTRS`?"*. Not a CI gate — a review reminder. Low cost, closes the maintenance gap.

## Alternatives considered

### Alt 1: Rename all 25 attrs to `_*` and ship a compat-shim

- **Rejected**: The cost (every user's view breaks) grossly exceeds the benefit. We shipped the filter precisely to avoid this cost.

### Alt 2: `__slots__`-based enforcement

- **Rejected**: `LiveView` subclasses rely heavily on dynamic attribute assignment for reactive state (`self.my_counter = 0` in `mount()`). `__slots__` would break the primary user-facing pattern.

### Alt 3: Runtime `__setattr__` enforcement

- **Rejected**: Same cost as rename (breaks legitimate override patterns) without the static benefit.

### Alt 4: Deprecation path — emit warning when user writes to a framework attr without underscore prefix

- **Rejected for v0.7.x**: Would be noise for every existing user. Could reconsider for v1.0 if we gain a clearer picture of which attrs are accidentally shadowed in practice. Filed as a future idea, not tracked for current release scope.

## Decision log

- **2026-03-??**: #762 ships the `_FRAMEWORK_INTERNAL_ATTRS` filter (v0.5.7). Leakage class closed.
- **2026-03-??**: v0.5.7 milestone retro flags "should we also rename?" as an open question → filed as #962.
- **2026-04-24**: #962 closed without code — this ADR records the decision. ROADMAP v0.7.2 entry struck through.
