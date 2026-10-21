- **`{% dropdown_menu %}` item kinds and `{% confirm_dialog_block %}`.**
  Menu items (`items=` entries and `{% menu_item %}`) accept `href` (a link),
  `type="checkbox"`/`type="radio"` with `checked` (`menuitemcheckbox` /
  `menuitemradio` with `aria-checked`; `checked` alone implies a checkbox),
  `value` (sent with the item's event) and `heading` (a group label); icons are
  `aria-hidden`. `{% menu_item %}` / `{% menu_divider %}` children were a
  template error under the Rust engine and now render, and other markup inside
  `{% dropdown_menu %}` is kept instead of dropped. Both engines render the same
  markup (`dj-dropdown-menu--open` and `data-open` when open); items inherit the
  menu's colour and font. `{% confirm_dialog_block %}...{% endconfirm_dialog_block %}`
  is `{% confirm_dialog %}` with a body slot (a note field, a pending line, an
  error) under the optional message.
