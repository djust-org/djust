- **Component tags work under a strict nonce CSP, with stable ids and dialog
  semantics.** A Content-Security-Policy without `'unsafe-inline'` refuses
  inline event handlers, and several component tags depended on them; others
  rendered markup a socket render could not diff.
  - `{% theme_toggle %}` rendered a new random id on every render, so every
    re-render patched it. It now renders no id (pass `id=` for one). Its
    buttons are `role="radio"` with `aria-checked` and carry `data-theme-mode`,
    so djust.theming's `theme.js` switches the mode with `{% theme_head %}`
    alone; without `theme.js` the client bundle does it. The choice is stored
    under `djust-theme-mode` (the old `djust-theme` key is migrated by the
    legacy `theme-toggle.js`, which is no longer needed). With `event=`, each
    button sends it with `data-value` set to its mode; the buttons no longer
    carry `data-theme`, which matched `[data-theme="dark"]` theme selectors.
  - `{% rich_select %}` (tag, Rust handler and `RichSelect`) and
    `{% combobox %}` open, close, filter and keep focus through delegated
    listeners in the client bundle instead of `onclick`/`onkeydown`/`oninput`/
    `onmousedown`. `rich_select`'s id is `rs-<name>` (or `id=`), not random;
    its trigger names its listbox with `aria-controls`. The combobox input is
    `role="combobox"` with `aria-controls`/`aria-expanded`, its list
    `role="listbox"`, its options `role="option"` with `aria-selected`.
  - `{% modal %}` rendered by the Rust engine (every socket render) had no
    `role="dialog"`/`aria-modal` and put its size class (`modal-md`) on the
    full-screen backdrop, where djust.theming's `.modal-md { max-width }`
    squeezed it. The Rust handler now renders the same markup as the Django
    tag (`dj-modal-backdrop` / `dj-modal dj-modal--md`).
  - `{% copy_button %}` uses the client's `dj-copy` directive instead of an
    inline handler (and the Rust handler now passes `copied_label`).
  - `critical_css`'s deferred stylesheet is swapped in by a script carrying the
    request nonce, not by an inline `onload=`.
  - `{% colocated_hook %}` carries the request nonce; the client registers a
    nonce-bearing hook through a script element with that nonce instead of
    `new Function`, so a strict CSP needs no `'unsafe-eval'`.
  - `{% confirm_dialog %}` ids derive from `id=` (default `dj-confirm`) instead
    of a random suffix.
