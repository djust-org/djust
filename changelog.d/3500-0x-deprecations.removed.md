- **Removed the three 0.x deprecations: `@event`, `LiveViewForm` and the
  legacy theme module (#3500).** Their removal floor was 1.1.0.
  - `@event` (`djust.event`, `djust.decorators.event`): use `@event_handler`,
    which behaves the same.
  - `LiveViewForm` (`djust.LiveViewForm`, `djust.forms.LiveViewForm`):
    subclass `django.forms.Form`. Form reactivity comes from `FormMixin` on
    the view. Replace `get_field_errors_json()` with `json.dumps(form.errors)`
    and `get_field_value(name)` with `form.cleaned_data` / `form.data`.
  - `djust.theming.themes._legacy` and the names `djust.theming.themes`
    re-exported from it (`THEMES`, `get_theme`, `list_themes`, `Theme`,
    `Typography`, `Spacing`, `BorderRadius`, `Shadows`, `Animations`,
    `ComponentStyles`): use `get_design_system` / `get_all_design_systems` /
    `DESIGN_SYSTEMS` from `djust.theming.theme_packs`.
