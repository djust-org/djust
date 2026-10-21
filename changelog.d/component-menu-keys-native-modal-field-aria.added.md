- **`{% dropdown_menu %}` keyboard support, `{% modal native=True %}`, and
  form-field ARIA for `{% combobox %}` / `{% rich_select %}`.**
  - The client's keyboard handler only knew `{% dropdown %}`
    (`.dj-dropdown`), so ArrowDown on a `{% dropdown_menu %}` trigger left focus
    there and Escape did nothing. Both menus now get the WAI-ARIA menu pattern:
    arrows rove over `menuitem` / `menuitemradio` / `menuitemcheckbox` items,
    skipping disabled ones; Home / End jump to the ends; type-ahead works;
    Escape sends the toggle event and returns focus to the trigger.
  - `{% modal native=True %}` renders a `<dialog dj-dialog="open">`, opened with
    `showModal()`: top layer, inert page, native Escape. A browser-side close
    (Escape, a backdrop click) sends `close_event` back through
    `dj-dialog-close-event`. The focus trap and return focus still apply, and
    focus now returns to the opener even though `showModal()` has already moved
    focus into the dialog. The default markup is unchanged.
  - `combobox` and `rich_select` take `error=`, `helper=`, `describedby=`,
    `invalid=` and `field=` (a Django bound field). Their help text and error
    are named by `aria-describedby`, and an error sets `aria-invalid="true"`.
    The Rust handler for `combobox` now also passes `required`, `error` and
    `helper` through, which it used to drop.
