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
- Fix every storybook page documenting an import that does not work. The USAGE
  snippet was spelled out in the template as
  `from djust_components.components.<name> import <Name>`, and both halves were
  wrong: `djust_components` is the *static* namespace, not a Python package, so
  it raised `ModuleNotFoundError` on all 150 pages; and the class name is not
  always the snake→CamelCase of the module (`qr_code` defines `QRCode`,
  `form_validation` defines two components, `server_event_toast` defines only a
  mixin). The line is now derived from the module and emitted over the public
  `djust.components` namespace, which resolves component classes lazily so
  `from djust.components import Accordion` works for all of them; a module with
  no component class documents no import instead of a wrong one. Also corrected
  the same wrong path in `server_event_toast`'s own docstring example.
- Make the storybook's component previews a real djust view. The detail page was a
  plain Django view, which renders a component's markup but cannot make it *work*:
  `dj-click` is a server event and a plain view ships no server to reach, so the
  accordion showed a chevron and did nothing when clicked. It is now a `LiveView`
  with the DEP-002 descriptor components attached — the same mechanism the component
  gallery's `/lv/` pages use — and the descriptor's state is merged into each
  example's kwargs, so a click updates it and the re-render carries the change.
  `dj-view` is declared on the content element, without which the page renders but
  nothing listens.
- Fix the storybook's LiveView patching by re-rendering the whole region on every
  click. `dj-view` sat on a `<main>`, and `_DJ_VIEW_RE` / `_DJ_ROOT_RE`
  (`mixins/template.py:32`, `:48`) both require a `<div>` — so the mount attribute
  matched nothing, the dj-root normalisation was skipped, and the initial-GET HTML
  kept the comments and as-authored whitespace the WS frame had already stripped.
  The two frames then described different trees, so one of three patches per click
  failed with `node not found at path=7/1/3/1/0` and the client re-morphed from
  recovery HTML (#1737). The mount is now a `<div>` inside the `<main>` landmark,
  and a test pins that the HTTP render and the WS mount describe the same tree.
- Fix the theme gallery's preset switcher applying only part of the preset. The
  gallery's override block was emitted as `{{ gallery_preset_css }}` without
  `|safe`, so autoescaping rewrote the quotes in its generated CSS: the selector
  `html[data-theme="dark"]` was emitted as `html[data-theme=&quot;dark&quot;]`,
  which no browser matches. The `:root` block has no quotes, so it survived — and
  the page therefore applied the preset's root values while every dark-mode
  override fell through to the configured theme. Choosing a preset changed the
  background and left the components in the previous theme's colours. With the
  selector fixed, `?preset=forest` renders green components rather than purple
  ones on a green background.
- Fix the theme gallery's preset choice not persisting, so it appeared to revert.
  The preset `<select>` submitted a GET form (`?preset=`), which changes the
  preset for that page load only — remove the parameter and the page fell back to
  whatever preset the visitor had stored, which reads as "I picked midnight, took
  the parameter off, and it reverted to candy". It now calls the framework's own
  `window.djustTheme.setPreset` (`theme.js:217`), which writes the cookie and
  localStorage and reloads, matching every other theme control on the site. The
  form is kept behind `<noscript>` as the no-JS fallback.
- Fix three components whose gallery preview rendered nothing at all. The preview
  helper returned an empty string on *any* failure and logged at DEBUG, so a
  component that could not render was indistinguishable from one that renders
  nothing by design. `markdown` passed `content=` to a constructor that takes
  `text=` (swallowed by `**kwargs`); `icon` and `qr_code` passed pixel `size`
  values to components whose `size` is a name (`xs/sm/md/lg`), so `html.escape`
  raised on the int; and `qr_code`'s class is `QRCode` while the lookup guessed
  `QrCode`. Failures now render a visible message carrying the reason, the class
  is resolved by reading the module rather than guessing, and a sweep test renders
  every component and fails if any produces nothing.
