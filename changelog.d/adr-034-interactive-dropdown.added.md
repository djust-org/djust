- **`djust.components.interactive`: a dropdown that owns its behavior and
  reports typed outputs (ADR-034 C1).**
  - `DropdownMenu(label=..., items=[...])` opens, closes and validates a
    selection itself, then calls the view's
    `@menu.on.selected def ...(self, component, value: str)` callback.
  - Items are typed as `ActionItem` / `SeparatorItem`. `visibility="client"`
    hands open/close to the browser's native popover, and an optional
    `@menu.on.toggled` callback observes it.
  - Each view instance gets its own component state. Events route only
    through the server's registry. mypy and Pyright catch misspelled menus,
    unknown outputs and wrong callback signatures.
  - Two new checks: `djust.V020` (interactive components on `use_actors`
    views, which 1.3 does not support) and `djust.Q004` (a module importing
    both this and the legacy `DropdownMenu`).
