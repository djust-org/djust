- **Free-form style values draw under a strict CSP (#3979).** A `style-src`
  without `'unsafe-inline'` refuses every `style="..."` attribute and inline
  `<style>` block, so column widths, custom colours, item heights and the like
  were dropped. Components now write such a value as
  `data-dj-style-<property>="<value>"`, checked on the server by one helper
  (`djust.components.utils.style_attrs` / `style_value`: lengths, colours,
  integers, ratios, grid lines and tracks, a few keywords; a hostile value is
  dropped with one warning per property), and a new client module
  (`54-dj-style.js`, under 1 KB gzipped) applies it with
  `element.style.setProperty` on first paint and after every patch, morph,
  navigation and lazy mount, removing a property when its attribute goes away.
  Only an allowlist of properties is applied. Converted, on every render path
  (class, Django tag, Rust engine, native handler, Rust component):
  `djust.components.ui.Progress`, `Progress` / `RustProgress`,
  `Avatar` / `RustAvatar`, the navbar logo, spinner sizes and dot delays, the
  many-to-many checkbox list, the native dropdown's popover placement, the
  ttyd terminal, `{% virtual_list %}`, `{% table_of_contents %}`, tree view,
  file tree, terminal ANSI colours, avatar group, presence avatars, expandable
  text, progress circle, masonry grid, data card grid, split pane, dashboard
  grid, sortable grid, resizable panel, scroll area, sticky header, rich text
  editor, map picker, data grid and data table column widths (and the data
  table's colour scale), kanban column colours, meter segment colours, colour
  picker swatches, calendar events, cursors overlay, collab selection, aspect
  ratio, responsive image and `{% theme_skeleton %}`. `{% djust_skeleton %}`
  and the animated Tailwind progress bar no longer emit a `<style>` block: the
  client adds their rules through a constructed stylesheet when an element
  carries `data-dj-css="skeleton"` / `"shimmer"`. The cursors overlay,
  dashboard grid and collab selection hooks read the data attributes. These
  values need JavaScript; without it the element keeps its stylesheet's size
  and colour. New cases in `python/djust/tests/test_csp_free_form_styles_3979.py`
  and `tests/js/dj_style.test.js`.
