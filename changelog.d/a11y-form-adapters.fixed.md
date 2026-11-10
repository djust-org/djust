- **Form adapter fields link their help text and errors to the control.** Every
  adapter gives the help text the id `<auto_id>-help` and the errors
  `<auto_id>-error` (`auto_id` is `id_<name>` unless passed), lists the rendered
  ones in the control's `aria-describedby` (after any the widget sets), marks a
  field with errors `aria-invalid="true"`, and hides the required `*` from
  assistive technology (the control is already `required`). An optional
  `ChoiceField` whose choices already include a blank option no longer gets a
  second empty `<option>`, grouped choices render as `<optgroup>`, a
  `BooleanField` no longer renders two labels, and radio ids no longer contain
  the choice value (a value with a space made an invalid id). Pinned in
  `python/tests/test_form_adapter_a11y.py`.
