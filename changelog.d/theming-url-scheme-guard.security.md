- **Theming URL tags now refuse script-bearing schemes in every URL they render
  (nav, sidebar, breadcrumb, pagination, avatar, and the auth/error pages).**
  `theme_button` and the `**attrs` passthrough already validated a URL with the
  module's `_check_url`; the other tags that render a caller-supplied URL into an
  `href`/`src`/`action` did not, so a `javascript:` value an app filled from its
  own data (a CMS page, a user profile link) reached a live `<a href>`. 22 cases
  in `python/djust/tests/test_theming_url_scheme_guard.py`.
