- **Client APIs that djust 2.0.0 removes now warn once per page when used.**
  The JSON `dj-params` attribute (use `dj-value-*` attributes; the warning used
  to appear only with `djustDebug`), the pre-1.0 `[data-djust-root]` marker in
  `dj-layout` swaps (use `dj-root`), and the `window.LiveViewWebSocket`,
  `window.setCacheConfig` and `window.globalLoadingManager` aliases (use the
  same names on `window.djust`). Each warning names its replacement, and each
  API keeps working until 2.0.0. `client-dev.js`, the demo loading test page
  and the Playwright loading check now read `window.djust.*`, so they don't
  trigger the warning. Covered by `tests/js/deprecation_warnings_2_0.test.js`.
