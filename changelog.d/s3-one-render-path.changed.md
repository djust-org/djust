- **Component tags render through one path (S3, #4002).** Under the Rust engine every
  `djust_components` tag now renders the Python tag's own Django node through the `{% load %}`
  bridge (#2547), registered at startup so no `{% load %}` is needed; the ~190 hand-written native
  twins in `python/djust/components/rust_handlers.py` (about 11,000 lines) and their argument
  wrappers are gone. Only the four core layout tags (`app_shell`, `app_sidebar`, `app_header`,
  `app_content`) stay native, for the partial-root root-proof protocol, pinned against the Python
  tags. A LiveView page now renders the same markup as the Django engine: the template-library
  parity ratchet is `IDENTICAL` for every component tag but `app_shell` (whose native keeps body
  content outside the slot tags). Differences a page can see, all where the native twin was the
  one that diverged: class names, ARIA and `dj-*` attributes as the Python tags draw them;
  positional arguments, bare operands, unknown keywords, missing and invalid arguments accepted or
  refused as Django does (a parameter given by position and by keyword is Django's render-time
  `TypeError`); `as var` binds; the native-only context fallbacks are gone (`{% breadcrumb %}`
  reading `breadcrumb_items`, `{% stepper %}` reading `steps`, `{% autocomplete %}` /
  `{% dependent_select %}` reading `suggestions` / `options`, `dj_select options_var=`,
  `dj_radio current_value_var=`); an `inclusion_tag` (`badge`, `avatar`, `progress`, `pagination`,
  `data_table`, `toast_container`) needs a template loader under `DjustTemplateBackend` as under
  Django; `data_table` renders `table.html` only, so the 24 features only the native handler drew
  (`expandable`, `editable_columns`, `exportable`, `reorderable`, `column_visibility`,
  `density_toggle`, `group_by`, `copyable`, `importable`, `row_drag`, `editable_rows`,
  `resizable`, `frozen_left`/`frozen_right`, `responsive_cards`, `keyboard_nav`,
  `virtual_scroll`, `server_mode`, `persist_key`, `printable`, `footer_aggregations`,
  `column_groups`, `computed_columns`, `conditional_formatting`) are not rendered (#3869 holds the
  decision), a model-instance row is an empty row and a bare-name `columns=["a"]` list raises, as
  in the Python tag. `{% toolbar %}` and `{% filter_bar %}` without an `id` now default to the
  constant `toolbar` / `filter-bar` under both engines (the native default; a random one was a
  patch on every LiveView turn). A tag whose node reads its children (`accordion`, `sidebar`,
  `nav_menu`, `toolbar`, `filter_bar`, `form_array`, `page_header`, `tabs`, `split_pane`) has its
  body rendered by Django's engine. Cost, measured on a release build: an `inclusion_tag` renders
  its template per use, about 0.15 to 0.2 ms more per use under `DjustTemplateBackend`
  (`data_table` over 100 rows: about 2 ms to 12 ms per render and per turn); inline and wrapper
  tags are within noise or faster. Closes #3663 for `djust_components` (the `theme_tags` twins
  remain), #3768, #3849, #3873, #3878, #3882, #3883; their reproductions are in
  `python/tests/test_component_single_render_path.py`.
