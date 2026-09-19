- **The component storybook is now the component catalogue, at
  `/theme/components/`.** It was `/theme/gallery/storybook/`, a fourth name for
  a family of pages that the documentation and the marketing site already call
  "components". The theme gallery moves alongside it from `/theme/gallery/` to
  `/theme/themes/`. Every old path redirects permanently and carries its
  captured component or category, so a bookmark lands on the page it named.
  The older `djust.components` gallery keeps its own pages, now under
  `/theme/components-legacy/`.
- **A host site can wrap the catalogue in its own chrome.** The pages render
  inside the template
  `theming/templates/djust_theming/catalogue/_document.html`; an app listed
  before `djust.theming` in `INSTALLED_APPS` that ships a template at the same
  name replaces the framework's document and topbar, which is how `djust.org` puts
  these pages inside its own navigation. Not a setting: djust's Rust engine
  resolves `{% extends %}` targets literally, so the template loader is the
  override mechanism that works on both engines. The contract a replacement
  meets is documented in the file and in `guides/components.md`.
- **Each component page says which djust it runs and links to its prose.** The
  topbar carries the running version, and every component links to its entry
  in the generated reference and to the components guide
  (`DJUST_THEMING_DOCS_URL`, default `https://docs.djust.org`) — the catalogue
  runs a release while the documentation site pins a checkout, and those can
  differ.
- **`describe_component(name)` is the registry's data contract.**
  `djust.theming.gallery.component_registry` exposes one call returning a
  component's description, import line, parameters, examples, events,
  accessibility rules, slots and style paths, with the keys pinned as
  `COMPONENT_DESCRIPTION_KEYS`. `docs.djust.org` generates its reference page
  from it, so the live catalogue and the written reference cannot describe a
  component differently.
  Renames, for anyone importing them: `theming.gallery.storybook` →
  `theming.gallery.catalogue`, `Storybook*View` → `Components*View`,
  `{% storybook_preview %}` / `{% storybook_thumbnail %}` →
  `{% component_preview %}` / `{% component_thumbnail %}`, the URL names
  `storybook*` → `components*`, and the pages' CSS prefix `sb-` → `dc-`.
- **`describe_component` carries what the class alone cannot.** Parameter
  descriptions now come from the component's own `Args:` block (the signature
  and the template contract record a name and a type, never a meaning, so
  every row read `—`); a contracted component also names its Python class and
  constructor under `python_class`, because `{% theme_alert %}` and `Alert`
  are two ways to render one component; a private sentinel default is
  reported as `NOT_SUPPLIED` rather than `<object object at 0x…>`, whose
  address changed on every run; and `required` / `kind` are read from the
  signature, so a `None` default is not a missing one and `**kwargs` is not a
  parameter named "kwargs". `docs.djust.org` generates its component
  reference from this call.
- **Documented when not to use `dj-navigate`.** It swaps the contents of
  `[dj-root]` and nothing else, so a target page that needs its own
  stylesheet or script in `<head>` — the component catalogue, or any page
  from another application — arrives as unstyled markup, with the previous
  page's `<title>` still in the tab. Nothing errors. `guides/navigation.md`
  now names the symptom and says to link such pages with a plain `href`, and
  `guides/components.md` says it for the catalogue specifically.
