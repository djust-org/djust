# Rust Components (removed in #4002)

Until #4002 fifteen of the stateless components in `djust.components.ui` rendered through
native classes from the `djust_components` crate (`RustAlert`, `RustAvatar`, `RustBadge`,
`RustButton`, `RustCard`, `RustDivider`, `RustIcon`, `RustModal`, `RustProgress`,
`RustRange`, `RustSpinner`, `RustSwitch`, `RustTextArea`, `RustToast`, `RustTooltip`,
registered in `djust._rust`). Each Python class also carried an inline `template` (used
when the crate constructor refused a value, such as a lazy string) and a `_render_custom()`
per CSS framework that no render reached: three renderers per component.

#4002 ("one renderer per component") kept one: each class's `_render_custom()`, a Python port
of the crate's markup. The crate (about 4k lines) and its 15 `add_class` registrations are
gone. You use the components as before:

```python
from djust.components.ui import Button

btn = Button("Save", variant="primary", size="lg")
html = btn.render()
```

## Why the crate went: the benchmark

`scripts/bench_ui_simple_components.py`, best of 5 x 3000 iterations, microseconds per call,
on the shapes listed in that script. "Build+render" is constructing the component and
rendering it once, the shape a view uses; "render" is `render()` on a built instance.

| class | crate render | crate build+render | template render | template build+render | Python render (now) | Python build+render (now) |
|---|---|---|---|---|---|---|
| Alert | 0.93 | 12.14 | 11.92 | 18.27 | 2.60 | 7.39 |
| Avatar | 1.10 | 28.70 | 21.36 | 35.55 | 5.16 | 12.29 |
| Badge | 1.03 | 14.72 | 14.96 | 22.67 | 2.74 | 7.74 |
| Button | 0.98 | 16.80 | 15.54 | 22.52 | 2.71 | 8.06 |
| Card | 0.85 | 13.76 | 15.92 | 22.62 | 1.67 | 6.80 |
| Divider | 0.72 | 11.02 | 10.54 | 16.10 | 0.83 | 5.02 |
| Icon | 0.81 | 16.93 | 14.26 | 22.41 | 1.71 | 7.24 |
| Modal | 15.62* | 30.69* | 15.65 | 26.27 | 2.26 | 10.13 |
| Progress | 1.25 | 42.92 | 23.72 | 39.29 | 6.26 | 14.73 |
| Range | 1.33 | 29.40 | 17.40 | 29.93 | 3.75 | 11.83 |
| Spinner | 1.03 | 13.83 | 14.28 | 21.12 | 3.46 | 8.41 |
| Switch | 1.12 | 22.74 | 17.14 | 26.81 | 4.59 | 11.65 |
| TextArea | 0.90 | 34.90 | 20.01 | 33.28 | 4.76 | 13.81 |
| Toast | 1.13 | 20.01 | 16.85 | 24.89 | 4.05 | 10.53 |
| Tooltip | 0.90 | 18.39 | 12.47 | 21.32 | 2.18 | 8.68 |

\* `Modal` never reached its crate class (its required `id` is the base class's own argument,
so construction always fell back to the template): both columns are the template path.

Reading it: the crate's `render()` alone was 1-5 us faster than the Python port, but a
component is built before it is rendered, and building the crate instance cost more than it
saved (each constructor call first tried a `framework=` keyword the classes did not take, and
every attribute the subclass set in `__init__` rebuilt the instance). Per construct-and-render
the Python renderer is 1.6x-2.9x faster than the crate path for every class. A benchmark did
not earn the crate, so it was removed.

## What the markup is

The same bytes the crate drew, pinned by `python/tests/test_ui_simple_golden_4002.py` against
284 renders captured from the crate before it was removed
(`python/tests/fixtures/ui_simple_golden_4002.json`), with three bug fixes:

- `Tooltip`: the crate wrote no space before `title=` (`data-bs-trigger="hover"title="..."`).
- `Range`, `Switch`, `TextArea`: an explicit `id=` never reached the crate, which used `name`
  for the element's `id` and the label's `for`. The explicit `id` is used now.

Every text slot is escaped whatever it is marked as, as the crate did; `Modal.body` /
`Modal.footer` and `Tooltip.content` are markup slots the constructor escapes itself unless
they are marked safe.

## Slice 3: the ui `*Component` markup wins

Eight of the simple classes have a twin in `djust.components.ui`: `Alert` (`AlertComponent`),
`Badge`, `Button`, `Card`, `Dropdown`, `Modal`, `Progress` and `Spinner`. Each now maps its
arguments onto its twin's and returns the twin's markup, so the widget has one renderer and
the output follows `css_framework` the way the twin's does. Each class docstring gives its
argument mapping; an argument the twin has no equivalent for still works and raises a
`DeprecationWarning`. What the markup change means for a page (the component `id`, the
dismiss buttons, the variant and size classes, the `Modal` layout) is under
"Markup that changed" in the [2.0 migration guide](../website/guides/migrating-to-2.0.md).
Text is still escaped whatever it is marked as: the simple class hands the twin plain strings.

Building the twin costs a construct per render. Build+render, same script and shapes:

| class | slice 1 (own markup) | slice 3 (twin) |
|---|---|---|
| Alert | 7.39 | 13.04 |
| Badge | 7.74 | 13.64 |
| Button | 8.06 | 15.96 |
| Card | 6.80 | 13.28 |
| Modal | 10.13 | 12.79 (measured hidden, before it drew the hidden markup) |
| Progress | 14.73 | 33.87 |
| Spinner | 8.41 | 12.94 |

Each is still at or below the retired crate's build+render. The other simple classes have no
twin in `djust.components.ui` and keep drawing their own markup.

`Component._rust_impl_class` remains for a project's own native renderer; no djust class
sets it.
