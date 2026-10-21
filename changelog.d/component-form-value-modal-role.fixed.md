- **`combobox` / `rich_select` submit the chosen option's value, and
  `{% modal %}` takes a role, a description and an optional close button.**
  - The combobox's visible text input carried the field's name, so a form
    submitted whatever was typed, not the chosen option's value. The selected
    value is now submitted from a hidden `.combobox-value` input named after the
    field, which the client updates on a pick; the visible input has no `name`.
    (The legacy `_target` hint of its `dj-input` event is now the input's id.)
    Typed text that exactly matches an option label picks it; other text is
    discarded on change unless `allow_free_text=True`. `rich_select` also
    updates its hidden value and trigger on a pick, before the server round
    trip. The `Combobox` class gets the same hidden input and shows the
    selected option's label instead of its value.
  - With `field=`, both derive their ids from the bound field's `auto_id` (form
    prefix included) instead of the field name, so two forms rendering the
    same field on one page no longer produce duplicate ids. `id=` sets them
    explicitly. Without either, the combobox keeps its `<name>-input` ids.
  - `{% modal %}` (both the `<div>` and the `native=True` dialog) takes
    `role="alertdialog"`, `description=` (a paragraph named by
    `aria-describedby`), `describedby=` and `close_button=False`.
