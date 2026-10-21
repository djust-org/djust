- **The remaining component tags work under a strict CSP.** `{% popover %}`,
  `{% popconfirm %}`, `{% collapsible %}`, `{% context_menu %}`,
  `{% file_dropzone %}`, `{% kanban_board %}`, `{% rich_text_editor %}`,
  `{% multi_select %}`, `{% split_pane %}`, `{% code_block %}` and
  `{% tutorial_bubble %}` used inline event handlers (`onclick`, `ondrop`,
  `oncontextmenu`, `oninput`, `onmousedown` ...) or an inline `<script>`, which a
  Content-Security-Policy without `'unsafe-inline'` refuses. Their behaviour now
  runs from delegated listeners in the client bundle. `{% code_block %}`
  highlights through the static `python/djust/components/static/djust_components/code-block-highlight.js`,
  still loading highlight.js once per page. Also fixed along the way:
  - popover, popconfirm, collapsible and context_menu render the same markup
    under both engines. The Rust popover and popconfirm toggled a class the CSS
    never styled (it keys on `data-open`), so they only opened through
    `:focus-within`.
  - The random ids on popover, collapsible, context_menu, popconfirm,
    split_pane, rich_text_editor and multi_select are gone: ids come from `id=`,
    or from `name` for the editor (`rte-<name>`) and multi-select (`ms-<name>`).
  - The triggers carry `aria-expanded`. The popover is `role="dialog"` (it was
    `role="tooltip"` around interactive content), and the popconfirm
    `role="alertdialog"`.
  - kanban's move event is a `data-move-event` attribute on the board.
