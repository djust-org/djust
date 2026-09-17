- **Six storybook previews that rendered something, and nothing like the
  component.** Each was reported from a page whose job is to show a component
  working, and each rendered plausibly enough that nothing looked broken —
  which is why they survived an audit that checked for raises, empty output and
  unhandled events.

  - **`progress` printed `style="width: %"`.** The storybook handed each
    template component's *template* the example's raw kwargs, bypassing the
    `{% theme_* %}` tag that computes the derived context (`percentage`,
    `is_indeterminate`, `css_prefix`, `attrs`, `slot_*`). An unfilled name is
    an empty string rather than an error, so the hole was invisible; the same
    tag rendered `width: 25.0%` for any real caller. Previews now render
    through the component's own tag, so a preview cannot be wrong in a way the
    component is not.
  - **`dropdown`'s menu was an empty div.** `theme_dropdown` — and `alert`,
    `badge`, `button`, `input`, `modal`, `pagination` and `table` — read
    `slot_*` in their templates without ever calling `_extract_slots`, so a
    caller-supplied slot stayed in `attrs`, which templates only read as
    `attrs.class` / `attrs.id`. All eight now lift their slots into context.
  - **`switch` could not toggle.** Its slider is drawn from the server-rendered
    `.dj-switch-checked`, and the example named no `action`, so the input
    carried no `dj-change` — the box flipped its own hidden checkbox and
    nothing else moved. It also needed the demo handler to accept a `bool`: a
    checkbox's `dj-change` is not a string, and `value: str` made the framework
    reject the event with "expected str, got bool (True)".
  - **`segmented_progress` had nothing to click.** Its steps are now `<button>`s
    carrying their 1-based number to an `event` (default `set_segment`), the
    way its sibling `stepper` already dispatched. With `event=""` it renders
    plain `<div>`s, so an indicator wired to nothing is not focusable.
  - **`loading_overlay` was an empty wrapper.** The example passed no `content`
    and no `active`, so there was nothing to overlay.
  - **`model_selector` could not open.** The option list carried
    `display: none` and no rule anywhere turned it back on, and the trigger
    dispatched nothing. It now takes `is_open` / `toggle_event`, emits
    `data-open` and a `dj-click`, and `components-classes.css` reveals the list
    on that marker.

- **Every example rendered the first example's values.** `_render_examples`
  merges the demo state into *every* example, so seeding that state from
  `examples[0]` made a two-state switch show two switches both on and four
  progress bars all read 25%. The state now starts empty and a transform reads
  its starting value from the example on first use.

- **`card`'s preview had an empty body, and `djust_theme` scaffolded a template
  that raised.** `theme_card` funnels every keyword it does not declare into
  `attrs`, so the example's `content` was accepted and dropped — and the tag
  itself was a `simple_tag` whose documented usage was a
  `{% theme_card %}…{% end_theme_card %}` block that no `end_theme_card`
  registers, so `djust_theme`'s generated page failed on first render with
  `TemplateSyntaxError: Invalid block tag: 'end_theme_card'`. The tag now takes
  a `body` argument, and the scaffold, gallery and editor pass one.
