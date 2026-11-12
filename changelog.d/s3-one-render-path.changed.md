- **Component tags render through one path where it costs nothing (S3 first slice, #4002).**
  Under the Rust engine a `djust_components` tag now renders the Python tag's own Django node
  through the `{% load %}` bridge (#2547), registered at startup so no `{% load %}` is needed; about
  160 hand-written native twins in `python/djust/components/rust_handlers.py` and their parity
  pins are gone. A native twin is kept only where a benchmark earns it or the bridge lacks a
  capability, each pinned against its Python tag with its divergences listed: the core layout tags
  (root-proof protocol), `badge` / `avatar` / `progress` / `pagination` / `data_table` (4 to 8x
  slower through the bridge; `data_table`'s native-only features stay, #3869), and `accordion`,
  `sidebar`, `nav_menu`, `toolbar`, `filter_bar`, `form_array`, `page_header` with their child
  tags (the bridge would hand their body to Django's engine, which cannot compile `{% call %}` /
  `{% component %}` / `{% slot %}` / `{% dj_suspense %}`). On a bridged tag a LiveView page now
  renders the Django engine's markup: class names, ARIA and `dj-*` attributes as the Python tag
  draws them; arguments accepted or refused as Django does (a parameter given by position and by
  keyword is Django's render-time `TypeError`); `as var` binds; the native-only context
  fallbacks are gone (`{% breadcrumb %}` reading `breadcrumb_items`, `{% stepper %}` reading
  `steps`, `{% autocomplete %}` / `{% dependent_select %}` reading `suggestions` / `options`,
  `dj_select options_var=`, `dj_radio current_value_var=`). `tooltip`, `otp_input` and
  `tag_input` derive their default id from their arguments plus the call's occurrence among
  identical calls in the same render (`-2`, `-3`, counted in the render's own context), instead
  of drawing a new one per render (a patch on every LiveView turn): stable across renders and
  unique within one. The inclusion templates (`badge`, `avatar`,
  `progress`, `pagination`, `table`, `toast`) escape every caller value written into an attribute
  with `force_escape`, so a marked-safe value cannot leave it (#4009). The twin-drift issues
  (#3663, #3768, #3849, #3882, #3883) no longer apply to the bridged tags (reproductions in
  `python/tests/test_component_single_render_path.py`) and stay open for the kept twins.
