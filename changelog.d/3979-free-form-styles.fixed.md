- **Free-form style values: checked, and an opt-in strict-CSP mode (#3979).**
  Every width, colour, height or grid placement a component writes from a
  caller's value now goes through one helper,
  `djust.components.utils.style_attrs` / `style_value` (lengths, colours,
  integers, ratios, grid lines and tracks, a few keywords; never `fixed` or
  `absolute` positioning, a `z-index` within ±9999; a hostile value is dropped
  with one warning per property). By default the result is the inline `style`
  each component has always written, byte for byte, so nothing changes for an
  app without a strict style policy. With
  `LIVEVIEW_CONFIG["csp_strict"] = True` (for a `style-src` without
  `'unsafe-inline'`) components write `data-dj-style` plus
  `data-dj-style-<property>="<value>"` instead, pages carry
  `<meta name="djust-styles" content="attrs">` in their head (the setting is
  project-wide), and a new client module (`54-dj-style.js`) applies the values
  with `element.style.setProperty` on such pages only (the meta is read from
  the server-rendered head, once), re-checking each value (allowlisted properties, no `url(`,
  `image`, `attr(`, `@`, `;`, braces, quotes or comments, custom properties by
  kind), after every patch, morph, navigation and lazy mount, and removing a
  property when its attribute goes away. In strict mode `{% djust_skeleton %}`
  and the animated Tailwind progress bar ask for their rules with
  `data-dj-css` (added through a constructed stylesheet) instead of an inline
  `<style>` block, and the native dropdown's popover placement is the
  `dj-dropdown-menu__content--popover` class. Percent fills of
  `djust.components.ui.Progress`, `Progress` and `RustProgress` carry the
  `dj-progress-w-N` class in both modes. Covered, on every render path (class,
  Django tag, Rust engine, native handler, Rust component): the ui progress
  bars, avatar, navbar logo, spinner, many-to-many checkboxes, dropdown
  popover, ttyd terminal, virtual list, table of contents, tree view, file
  tree, terminal colours, avatar group, presence avatars, expandable text,
  progress circle, masonry grid, data card grid, split pane, dashboard grid,
  sortable grid, resizable panel, scroll area, sticky header, rich text
  editor, map picker, data grid and data table widths and colour scale,
  kanban and meter colours, colour picker, calendar, cursors overlay, collab
  selection, aspect ratio, responsive image, `{% theme_skeleton %}` and
  `{% djust_skeleton %}`. The calendar event and collab-selection highlight
  rules in `components.css` set `background-color` (a longhand) from their
  custom properties. New cases in
  `python/djust/tests/test_csp_free_form_styles_3979.py` and
  `tests/js/dj_style.test.js`.
