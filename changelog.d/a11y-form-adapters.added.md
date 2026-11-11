- **Theme classes for the form adapters, and accessible choice groups.** The
  classes `as_live_field` / `as_live` / `{% live_field %}` use are set by slot
  (`input`, `textarea`, `select`, `label`, `help`, `error`, `wrapper`, `legend`,
  `radio_group`, `radio_option`, `radio_input`, `radio_label`, ...; the list is
  `djust.frameworks.FORM_CLASS_SLOTS`) in `LIVEVIEW_CONFIG["form_classes"]`, or
  in `css_classes` on an adapter subclass, without copying adapter code or a
  framework's whole class map. `css_classes` wins over `form_classes`, which
  wins over the framework's map; an unset slot keeps the framework's class.
  A `RadioSelect` renders as `<fieldset role="radiogroup">` named by its
  `<legend>`, with `aria-required`, the group's `aria-describedby` /
  `aria-invalid`, and one `<input type="radio" dj-change="validate_field">` per
  choice with its own `<label for>` and an id `<auto_id>_<index>`; the hook
  classes `dj-radio-group` / `dj-radio-option`, plus
  `dj-radio-group--segmented` from `group_variant="segmented"`, let a theme draw
  segmented buttons. A `CheckboxSelectMultiple` renders as a
  `<fieldset class="dj-checkbox-group">` of checkboxes instead of a single
  `<select>`. A built-in adapter now reads its own framework's classes
  (`framework="tailwind"` gets Tailwind's even when `css_framework` is
  `bootstrap5`). Documented in `docs/ai/forms.md` and the CSS frameworks guide.
