- **The theming `tabs`, `dropdown` and `modal` components take their state from
  the server.** Each interactive element now dispatches an event — `set_tab`,
  `toggle_dropdown`, `toggle_modal` — and the markup renders from the host
  LiveView's descriptor state, so `{% theme_tabs active=tabs.active %}`,
  `{% theme_dropdown is_open=menu.is_open %}` and
  `{% theme_modal is_open=modal.is_open %}` drive them. The modal's visibility
  is an inline `display` because `.modal-backdrop` is `display: flex` in both
  `theming/css/components.css` and `scaffold.css`, and an author rule beats the
  UA's `[hidden]` rule. Adding `dj-click` to the close control also makes Escape
  close a dialog through the framework: `51-keyboard-nav.js` finds a dialog's
  close target with `[dj-click]`.
- **Fixed: a DEP-002 descriptor was unreachable on any page with more than one
  descriptor of its type.** `data-component-id` routing checked only
  `view._components` (LiveComponents), so a descriptor's id — the documented way
  to disambiguate — was reported as `Component not found` before the descriptor's
  own handler could see it. Routing now falls through when the id names a
  descriptor, which is what makes several modals or dropdowns on one page work.
- **`components.js` stands down on any page djust is driving.** When the markup
  carries a djust mount root the server owns these components, and binding
  client-side as well would put two writers on the same DOM. Plain pages keep
  the fallback: the theme gallery is still a plain Django view, because it
  renders all 25 `{% theme_* %}` tags and those are registered with Django's
  template engine only — as a LiveView it raises `Invalid block tag:
  'theme_button'`. Registering them with the Rust engine is a prerequisite for
  making that page server-driven.
