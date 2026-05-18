---
title: "Accessibility (ARIA / WCAG)"
slug: accessibility
section: guides
order: 12.95
level: intermediate
description: "djust's 1.0 accessibility posture — built-in component ARIA, the Y system checks, theming color-contrast validation, and what's not yet covered"
---

# Accessibility (ARIA / WCAG)

Accessibility is a 1.0 quality gate for djust. A reactive framework that
renders HTML on your behalf is responsible for emitting *correct* markup —
roles, states, and accessible names that assistive technology can announce.
This guide covers what djust ships for accessibility today, how to catch
regressions in your own templates with the `Y` system checks, and — honestly —
what is not yet covered.

There is nothing to install or enable. The component ARIA support is built into
the component library; the `Y` checks run automatically as part of
`manage.py check`.

## djust's 1.0 accessibility posture

djust takes the position that the framework should be *correct to assistive
technology* out of the box, while leaving styling and design entirely to you
(manifesto principle 7 — *opinionated where it matters, flexible where it
doesn't*). Concretely, for 1.0:

- The **interactive component library** emits the ARIA roles, states, and
  accessible names a keyboard or screen-reader user needs (see the table
  below). These are *add-only* markup attributes — no class was renamed and no
  element was added, removed, or reparented, so your existing CSS and JS
  selectors are unaffected.
- A new **`Y` system-check category** scans *your* project templates for the
  highest-value, lowest-false-positive accessibility defects and reports them
  at `manage.py check` time.
- **Theming color-contrast validation** (shipped earlier) checks every theme
  combination against WCAG 2.1 contrast, focus-visibility, and motion-safety
  requirements.

What 1.0 deliberately does **not** claim is full WCAG 2.1 AA conformance for an
arbitrary app — that depends on *your* content, copy, and design choices, which
djust does not control. See [Known limitations](#known-limitations--not-yet-covered)
for the framework-side gaps that are explicitly deferred.

This is **unit 4 of the v1.0.0 (Release Readiness) milestone**.

## Built-in component ARIA

The component library ships with ARIA markup on its interactive and feedback
components. The table below is the accurate, per-component summary of what each
component emits as of 1.0 — these are *guarantees*, each backed by a test in
`python/djust/components/tests/test_component_aria.py`.

| Component | Roles / ARIA emitted |
|-----------|----------------------|
| **`{% modal %}`** | Dialog gets `role="dialog"` + `aria-modal="true"`; when a title is set, `aria-labelledby` points at the `<h3>` title's derived `id`; the close button carries `aria-label="Close"`. |
| **`{% tabs %}`** | Nav is `role="tablist"`; each tab button is `role="tab"` with `aria-selected="true"`/`"false"` and `aria-controls` pointing at its panel; the active pane is `role="tabpanel"` with `aria-labelledby` pointing back at its tab; decorative tab icons are `aria-hidden="true"`. Tab and panel `id`s are derived deterministically from the `id` kwarg so they stay VDOM-stable. |
| **`{% accordion %}`** | Each trigger button carries `aria-expanded` (reflecting open/closed) and `aria-controls` pointing at its panel; the open panel is `role="region"` with an `id` and `aria-labelledby` pointing back at its trigger; the chevron glyph is `aria-hidden="true"`. |
| **`{% dropdown %}`** | Trigger button carries `aria-haspopup="menu"`, `aria-expanded` (reflecting open state), and `aria-controls` pointing at the menu; the open menu `<div>` is `role="menu"`. |
| **`{% alert %}`** | Container gets `role="alert"` for `error`/`danger`/`warning` types (assertive) or `role="status"` for `info`/`success` (polite); the type icon is `aria-hidden="true"`; the dismiss button carries `aria-label="Dismiss"`. |
| **`{% pagination %}`** | The `<nav>` carries `aria-label="Pagination"`; the active page button gets `aria-current="page"`; every page button gets an `aria-label="Page N"`; the prev/next arrow buttons get `aria-label="Previous page"`/`"Next page"`; the ellipsis is `aria-hidden="true"`. |
| **`{% data_table %}`** | Already well-instrumented (`role="grid"`, `aria-sort`, `aria-busy`, `aria-label` on search/checkboxes). New in 1.0: sortable column headers are keyboard-focusable (`tabindex="0"`) so a keyboard user can reach the sort affordance; the sort-direction glyph is wrapped in a decorative `<span aria-hidden="true">`. |
| **`{% toast %}`** | Each toast carries `role="alert"` + `aria-live="assertive"` for `error` toasts, or `role="status"` + `aria-live="polite"` otherwise, so screen readers announce it without a focus change; the type icon is `aria-hidden="true"`; the dismiss button carries `aria-label="Dismiss"`. |

A note on `id` collisions: `modal`, `tabs`, and `accordion` derive their ARIA
pairing `id`s from the `id` kwarg the tags already accept (defaulting to
`"modal"`, `"tabs"`, `"accordion"`). If you place **two** unnamed instances of
the same component on one page, give each a distinct `id=` — the same
pre-existing limitation that the plain `id` attribute has always had.

```html
{% modal id="confirm-delete" title="Delete this record?" %}
  This cannot be undone.
{% endmodal %}

{% modal id="confirm-publish" title="Publish now?" %}
  Your post will go live immediately.
{% endmodal %}
```

## The `Y` accessibility system checks

djust's [system checks](../../system-checks.md) catch misconfigurations at
startup. The new **`Y` category** (mnemonic: a11**Y**) scans your project's
template files for accessibility defects. The checks run automatically as part
of `manage.py check` and `manage.py djust_check`.

The category ships with four checks — deliberately the lowest-ambiguity
defects, so the regex heuristics carry near-zero false positives. The category
is extensible by design: adding more checks later (heading order, missing
`lang` attribute, redundant `role`) is a single-function-body change.

### Y001 — interactive element missing an accessible name

**Severity:** Warning

Flags an interactive `<button>` or `<a href>` whose visible content is
*icon-only* — an HTML entity (`&times;`), an `<svg>`, or an `<i>`/`<span>` icon
wrapper — and which has no `aria-label`, `aria-labelledby`, or `title`. A
screen-reader user hears *nothing* for such a control.

```html
<!-- Flagged by Y001 — the screen reader announces nothing -->
<button dj-click="close">&times;</button>

<!-- Not flagged — explicit accessible name -->
<button dj-click="close" aria-label="Close">&times;</button>

<!-- Not flagged — has visible text -->
<button dj-click="close">Close</button>
```

A `<a>` is only treated as an interactive control when it carries an `href` (a
bare `<a>` is an anchor target, not a control). Inner content containing a
`{{ variable }}` or `{% tag %}` is conservatively treated as *may resolve to a
label at render time* and is **not** flagged.

### Y002 — `<img>` missing an `alt` attribute

**Severity:** Warning

Flags an `<img>` tag with no `alt` attribute at all (WCAG 1.1.1, Level A).
`alt=""` is the WCAG-correct way to mark a *decorative* image and is **not**
flagged — only a complete absence of the `alt` token is.

```html
<!-- Flagged by Y002 -->
<img src="/static/logo.png">

<!-- Not flagged — informative alt text -->
<img src="/static/logo.png" alt="djust logo">

<!-- Not flagged — explicitly decorative -->
<img src="/static/divider.png" alt="">
```

An `<img>` whose attributes are injected dynamically (`{% ... %}` / `{{ ... }}`)
is treated as *`alt` may be present* and is not flagged.

### Y003 — form control missing an associated label

**Severity:** Warning

Flags an `<input>`, `<select>`, or `<textarea>` form control that has no
associated label (WCAG 1.3.1 / 3.3.2, Level A). A screen-reader user cannot
identify an unlabelled field. A control counts as labelled if any of the
following is true: a `<label for>` references its `id`, the control is wrapped
in a `<label>`, or it carries an `aria-label` or `aria-labelledby`.

```html
<!-- Flagged by Y003 — no label of any kind -->
<input type="text" name="email">

<!-- Not flagged — label references the id -->
<label for="email">Email</label>
<input type="text" name="email" id="email">

<!-- Not flagged — wrapping label -->
<label>Email <input type="text" name="email"></label>

<!-- Not flagged — explicit accessible name -->
<input type="text" name="email" aria-label="Email address">
```

Form controls that need no label are skipped: `hidden`, `submit`, `button`,
`reset`, and `image` `<input>` types. A control whose attributes are injected
dynamically (`{% ... %}` / `{{ ... }}`) is treated conservatively as *a label
may be present* and is **not** flagged, and a `data-type` attribute is not
mistaken for the input `type`.

### Y004 — positive `tabindex` value

**Severity:** Warning

Flags a `tabindex` attribute with a *positive* value (`tabindex="1"` or higher).
A positive `tabindex` overrides the natural DOM focus order, producing a
focus sequence that no longer matches the visual or reading order — a WCAG
2.4.3 (Level A) focus-order anti-pattern.

```html
<!-- Flagged by Y004 — positive value distorts focus order -->
<button tabindex="3">Save</button>

<!-- Not flagged — focusable in natural DOM order -->
<div tabindex="0" role="button">Custom control</div>

<!-- Not flagged — focusable only programmatically -->
<div tabindex="-1">Skip target</div>
```

Only `tabindex="0"` and `tabindex="-1"` are valid; both are not flagged. An
interpolated value (`tabindex="{{ ... }}"` / `{% ... %}`) is treated
conservatively and not flagged, and a `data-tabindex` attribute is not mistaken
for `tabindex`.

All four checks emit a `DjustWarning` (not an error) with the file path and line
number, so a stray false positive never fails `manage.py check`. Templates that
show literal HTML examples inside `{% verbatim %}` blocks are skipped, so docs
and marketing pages don't false-positive.

### Reading and suppressing the `Y` checks

The checks print like any other djust check:

```
WARNINGS:
?: (djust.Y001) templates/myapp/toolbar.html:14 -- <button> has no
   accessible name (icon-only content and no aria-label).
	HINT: Screen-reader users hear nothing for an icon-only control.
	Add aria-label="..." (or aria-labelledby / title) to the <button>
	element so its purpose is announced.
```

The right fix is almost always to *add the missing attribute* rather than
suppress the warning. When you do need to suppress — for an intentional
exception, or to silence a rare false positive — use
`DJUST_CONFIG['suppress_checks']` in `settings.py`:

```python
# settings.py
DJUST_CONFIG = {
    "suppress_checks": [
        "Y001",  # icon-only buttons in the legacy admin toolbar — tracked
        "Y002",
    ],
}
```

Suppressing `Y001` silences the icon-only-button scan; `Y002` silences the
`<img>` scan; `Y003` silences the form-label scan; `Y004` silences the
positive-`tabindex` scan. Prefer suppressing the most specific id, and leave a
comment explaining *why* the suppression is intentional.

> `DJUST_CONFIG['suppress_checks']` is djust's own suppression list and is what
> the `Y` checks honor. Django's built-in `SILENCED_SYSTEM_CHECKS` also works
> for the `djust.Y001` – `djust.Y004` ids if you prefer the standard Django
> mechanism.

## Theming color-contrast WCAG validation

Color contrast is the other half of accessibility djust validates — and it
shipped before this unit, as part of the theming system. `djust.theming` ships
an `AccessibilityValidator` (`python/djust/theming/accessibility.py`) that
checks every theme combination against **WCAG 2.1**:

- **Color contrast** — text/background contrast ratios against the WCAG AA
  (4.5:1 normal, 3.0:1 large) and AAA (7.0:1 normal, 4.5:1 large) thresholds.
- **Focus visibility** — whether the theme defines a visible focus indicator.
- **Motion safety** — whether the theme respects reduced-motion preferences.
- **Color independence** — whether information is conveyed by more than color
  alone.

```python
from djust.theming.accessibility import (
    validate_accessibility,
    validate_all_accessibility,
)

# Validate one theme combination
report = validate_accessibility(design_system="...", color_preset="...")
print(report.overall_score)       # 0–100
print(report.contrast_results)    # per-pair ContrastResult (ratio, AA/AAA)
print(report.issues)              # human-readable problems found
print(report.recommendations)     # how to fix them

# Validate every design-system × color-preset combination
all_reports = validate_all_accessibility()
```

This validation runs over *theme* color tokens — it does not look at your
component markup, which is what the `Y` checks and the built-in component ARIA
cover. The two are complementary: theming validation answers "is this theme's
contrast accessible?" and the `Y` checks answer "is this template's markup
accessible?".

## Known limitations / not yet covered

In keeping with djust's commitment to honest documentation, here is what 1.0
accessibility support does **not** yet cover. Each item is a deliberately
deferred follow-up — tracked, not forgotten — and none of them block the 1.0
accessibility gate, which is *interactive components are correct to assistive
technology*.

- **Keyboard interaction (client JS).** The 1.0 component ARIA pass ships
  *roles and states* — it makes components *correct* to assistive technology.
  It does **not** ship the client-side keyboard *operability* layer: a focus
  trap and `Esc`-to-close for `modal`/`sheet`, arrow-key roving `tabindex` for
  `tabs` and menus, and type-ahead for menus. This is client JavaScript work
  (new modules under `static/djust/src/`) and is deferred to a dedicated
  follow-up. The markup must land first regardless — roles and `aria-*` are the
  foundation the keyboard handlers build on. Until then, mouse and screen-reader
  users are well served; full keyboard-only operation of these specific
  widgets is in progress.
- **P2 / P3 component ARIA polish.** A second tier of components — `popover`,
  `collapsible`, `sheet`, `command_palette`, `context_menu`, `progress`, and
  `tooltip` — have the same kind of minor ARIA gaps as the interactive set but
  lower blast radius (no keyboard user is *blocked* by them). They are deferred
  to a "P2 a11y component pass" follow-up.
- **Long-tail decorative-icon sweep.** The ~115 display-only components (badge,
  card, avatar, timeline, breadcrumb, the `_advanced.py` exotic set) have
  scattered minor gaps — mostly decorative icons that should be
  `aria-hidden="true"`. This mechanical, low-severity sweep is a separate
  follow-up.
- **More `Y` checks.** The `Y` category ships with four checks. Heading-order
  validation, missing `lang` attribute, and redundant `role` detection are
  natural further additions, tracked for a future release.

If full keyboard operability or one of the deferred items is a hard requirement
for your app today, you can add the keyboard handling yourself with a
[client-side hook](hooks.md) — the ARIA markup djust emits is already correct,
so a hook only needs to wire up the key events.

## See also

- [System Checks Reference](../../system-checks.md) — every djust check id,
  including the `Y` category.
- [CSS Frameworks](css-frameworks.md) — styling components (djust has zero
  opinions on CSS; bring your own).
- [Hooks](hooks.md) — client-side JavaScript lifecycle hooks, the place to add
  custom keyboard handling until the built-in layer ships.
