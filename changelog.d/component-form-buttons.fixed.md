- **`toggle_group` and `rating` no longer submit the form they sit in, and can post
  their value.** Their buttons had no `type`, so inside a `<form>` each click was a
  submit. Every renderer (the template tag, the Rust engine's inline handler and the
  `ToggleGroup` / `Rating` component classes) now writes `<button type="button">`.
  `{% toggle_group ... form_field=True %}` adds hidden inputs that post the selected
  value(s) under `name`, and `{% rating ... name="score" %}` posts the current
  rating; both are opt-in, so a submit handler that does not take the field is
  unchanged. `{% dj_radio %}` builds its id from `<name>_<value>` with characters
  other than letters, digits, `_` and `-` replaced by `-` (a value with a space
  made an invalid id), and takes an explicit `id=`. Pinned in
  `python/djust/tests/test_component_form_buttons.py`.
