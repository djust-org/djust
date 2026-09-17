- Fix every component in the component gallery rendering as a red "Render error". The gallery compiled its example snippets with `django.template.base.Template`, which resolves through `Engine.get_default()` and therefore requires a `DjangoTemplates` entry in `TEMPLATES`. A project scaffolded by `djust new` — and djust's own demo — configures only `djust.template_backend.DjustTemplateBackend`, so every snippet raised `ImproperlyConfigured: No DjangoTemplates backend is configured`. Snippets are now compiled through the project's own configured backend, preferring `DjangoTemplates` when present. Restores 237 of the 245 affected render sites.
- Fix the component gallery rendering entirely unstyled, with a single-option theme dropdown. Its theme integration imported `djust_theming.manager` / `djust_theming.presets` / `djust_theming.theme_packs`, none of which exist — `djust_theming` is only the *static* namespace; the package is `djust.theming`. Each import raised `ModuleNotFoundError` into a bare `except`, so the gallery injected no theme CSS at all (no design tokens, so no component styling) and reported one preset. Those fallbacks now log instead of failing silently.
- Fix the theming gallery ignoring the project's configured preset. It opened on a hardcoded `preset="default"` regardless of `LIVEVIEW_CONFIG["theme"]["preset"]`, so a themed site saw its own component gallery in a palette that was not its own; a `?preset=` query parameter still wins.
- Fix `default_mode` being ignored on every page. Both the anti-FOUC script in `theme_head.html` and `theme.js` hardcoded `'system'` as their mode fallback, so a project configuring `default_mode: "dark"` came up light on any machine whose OS preferred light — and because `theme.js` loads `defer` it then overwrote the inline script's correct value. The server-resolved mode is now published once (`window.__djust_theme_default_mode`) and read by both.
- Fix the theme gallery's heading hierarchy, which was inverted: `.gallery__section h2` was pulled down to `1.25rem` while the theme's base rules left `h3` at `var(--text-2xl)` (~1.95rem), so "Variants" rendered half again larger than the "Button" section it belonged to.
- Fix gallery modal and tooltip trigger buttons rendering as browser defaults; they are now styled via a `.gallery-trigger` class.
- Fix the component gallery's routes raising `TemplateDoesNotExist` for anyone following its own documentation. The gallery needs `"djust.components"` in `INSTALLED_APPS` — Django only scans an app's `templates/` directory when the app is installed — and `components/gallery/urls.py` additionally told developers to include `djust_components.gallery.urls`, a module that has never existed. Both are now documented.
- Fix the theming gallery's topbar linking to a hardcoded `/components/`, a path belonging to whichever project hosted the gallery, which 404s everywhere else. The theming app now routes the component gallery at `/theme/components/` when `djust.components` is installed, and the topbar links to it via `components_gallery_url` — omitted entirely rather than dead when the app is absent.
- Fix the component gallery ignoring the project's theme: it hardcoded its
  preset to `default`, its design system to `material` and its mode to
  `light`, so a project configuring a preset or `default_mode: "dark"`
  still got a light, default-palette gallery. A selection made in the
  gallery's own toolbar still wins.
- Fix the component gallery's chrome rendering unthemed. Its layout CSS uses
  `--color-bg`, `--color-text`, `--color-border`, `--color-text-secondary`,
  `--color-bg-subtle` and `--color-primary`; none of those names exist in
  the theming system, which emits the semantic set plus `--color-brand-*`.
  Every declaration was therefore dropped — which looks correct in light
  mode by accident (the browser default is dark-on-light) and rendered the
  header and section headings white on white in dark mode. The six are now
  aliased to the theming tokens.
- Fix `{% split_pane %}` raising `Invalid block tag on line 1: 'pane', expected
  'endsplit_pane'` on the Rust engine, the last component in the gallery that
  could not render. It is a two-segment tag (`{% split_pane %}`…`{% pane %}`…
  `{% endsplit_pane %}`) and the native block path declares exactly one end
  tag, so `{% pane %}` was rejected. The handler registered on that path,
  `SplitPaneHandler`, also ignored the split — it wrapped the pre-rendered body
  in a single `<div>` — so it diverged from Django even when it parsed. It now
  registers on the raw path, where `LibraryRawBlockTagHandler` hands the
  un-rendered body to Django's own `do_split_pane`: the two panes, the drag
  handle and the inline script are Django's, byte for byte. The panes' contents
  are rendered by Django rather than Rust, so `dj-*` bindings inside a pane do
  not get Rust VDOM identity — the same trade every raw-path tag makes, and
  preferable to raising on every use.
