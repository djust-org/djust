---
title: "Vendoring third-party JS"
slug: vendored-assets
section: guides
order: 34
level: intermediate
description: "Declare the third-party JS/CSS your app or component package serves, with Subresource Integrity and a startup check for every version."
---

# Vendoring third-party JS

Any Django app that serves someone else's JavaScript or CSS to a browser —
djust itself, a component package, or your own project — declares it in a
`djust_assets.json` manifest. djust renders it with Subresource Integrity
(SRI), checks it at startup, and can list it in a CycloneDX SBOM (see
[Scanning a djust app](scanning.md)). This is [ADR-040](../../adr/040-vendored-assets-and-sboms.md).

## Who it's for

- **Component packages** that bundle a JS library (a chart widget, a rich
  text editor, a terminal) and want it declared once, checked at every
  consumer's startup, and visible to a scanner.
- **Apps** that vendor their own third-party files, or that need to
  override a library djust already bundles — for example shipping a
  security fix for `highlight.js` before djust releases one.

If you never write `<script src="...">` or `<link href="...">` by hand for
third-party code, you don't need this guide — djust's own components already
declare their assets, and `{% code_block %}` and the ttyd terminal already
use them.

## The schema

A manifest is `<app>/djust_assets.json`, next to the app's `apps.py`:

```json
{
  "schema": 1,
  "assets": {
    "markdown-visual": {
      "files": [
        {
          "path": "djust_components/markdown-visual.js",
          "integrity": "sha384-…"
        }
      ],
      "license_file": "djust_components/markdown-visual.LICENSE.txt",
      "packages": [
        {"purl": "pkg:npm/%40tiptap/core@3.31.3", "license": "MIT"}
      ]
    },
    "chart.js": {
      "files": [
        {
          "url": "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
          "integrity": "sha384-…"
        }
      ],
      "packages": [{"purl": "pkg:npm/chart.js@4.4.0", "license": "MIT"}]
    }
  }
}
```

Rules:

- `"schema"` must be `1`.
- Each entry in `"files"` has exactly one of `"path"` (a staticfiles path —
  vendored) or `"url"` (external), plus `"integrity"` in SRI form
  (`sha256-`, `sha384-` or `sha512-`). A file may also carry `"variant"`
  (for example a highlight.js theme) and `"type"` (`script`, `module` or
  `style`); `"type"` is inferred from the extension when omitted (`.js` →
  `script`, `.mjs` → `module`, `.css` → `style`).
- `"packages"` must be non-empty. Each `"purl"` includes an exact
  `@version` — it's the only record of name and version djust keeps.
  Non-npm sources use `pkg:github/<owner>/<repo>@<tag>` or
  `pkg:generic/<name>@<version>?download_url=<url>`. `"license"` is an
  SPDX expression.
- `"license_file"` is optional, for a bundle whose build generates one.

An invalid asset is dropped, and its neighbours in the same manifest still
load — startup checks report each problem (`djust.B001`–`B002`) rather than
crashing.

## Where manifests live

- **App-owned:** `<app>/djust_assets.json`, discovered automatically from
  every installed app via `apps.get_app_configs()`. This is how djust's own
  `components` and `admin_ext` apps declare their bundles, and how a
  component package ships one.
- **Project-owned:** list extra manifest paths in `DJUST_ASSET_MANIFESTS`
  (default `[]`), for files under your `STATICFILES_DIRS` rather than an
  installed app.

```text
# settings.py
DJUST_ASSET_MANIFESTS = [
    BASE_DIR / "static" / "djust_assets.json",
]
```

Names are global. When two manifests declare the same asset name, project
manifests win over apps (in `DJUST_ASSET_MANIFESTS` list order), and apps
earlier in `INSTALLED_APPS` win over later ones — the same order Django
already uses for templates and static files.

## How to compute an integrity

```bash
openssl dgst -sha384 -binary path/to/file.js | openssl base64 -A
```

Prefix the result with `sha384-` (or use `-sha256`/`-sha512` and prefix
accordingly) for the manifest's `"integrity"` value.

## Using assets

`{% load djust_assets %}`, then:

```html
{% djust_asset "highlight.js" variant="github-dark" %}
{% djust_asset_url "xterm" file_type="module" %}
```

- `{% djust_asset "name" [variant="…"] %}` renders every tag the asset
  needs — `<script integrity="…">`, `<link rel="stylesheet" integrity="…">`
  or `<link rel="modulepreload" integrity="…">` — computed from the
  *stored* static file (so a post-`collectstatic` rewrite, like
  `ManifestStaticFilesStorage`'s hashed CSS `url()`s, doesn't invalidate the
  hash).
- `{% djust_asset_url "name" [variant="…"] [file_type="module"] %}` returns
  just the URL, for JavaScript that needs to `import()` a module itself —
  `import()` can't carry SRI, so the module is rendered as a
  `modulepreload` link and imported by its URL from a `data-` attribute
  instead.
- A component declares what it needs and djust checks it exists:

```python
from djust.components.base import Component


class ChartWidget(Component):
    requires_assets = ["chart.js"]
```

An undeclared name in `requires_assets` fails startup with `djust.B007`.

### Rendering a component's assets

`requires_assets` only declares and checks; it renders nothing by itself.
Something has to output the tags, and you choose where.

**In the page template**, through the variable holding the component
instance — for a component the view exposes as `chart`:

```html
{{ chart.asset_tags }}
```

**In the component's own `template`** (or `_render_custom`), from Python
with `self.asset_tags()`:

```python
class ChartWidget(Component):
    requires_assets = ["chart.js"]
    template = "<div>{{ asset_tags }}<canvas></canvas></div>"

    def get_context_data(self):
        return {"asset_tags": self.asset_tags()}
```

`asset_tags` is per instance: every instance that renders it emits its own
tags. When several instances on one page share an asset, render the tags
once in a page-level template (`{% djust_asset "chart.js" %}` in the base
template, or one instance's `asset_tags`) instead of in each instance.

`LiveComponent` does not inherit from `Component`, so it has neither the
`asset_tags` helper nor the `djust.B007` check for `requires_assets`. Put
`{% load djust_assets %}{% djust_asset "chart.js" %}` in its template
instead — an unknown name there raises `ImproperlyConfigured` when it
renders.

## Overriding a djust-bundled library for a security fix

Because resolution follows `INSTALLED_APPS`/`DJUST_ASSET_MANIFESTS` order
and the first declaration of a name wins, you can ship a fixed version of a
library djust bundles — for example `highlight.js` — before djust cuts a
release with the fix: add your own `djust_assets.json` entry for the same
asset name in a manifest that resolves before djust's.

An override replaces djust's declaration whole — djust does not merge the
two. Overriding `highlight.js` in particular means your entry must
re-declare all thirteen theme files as `"variant"` entries (see
[Supported highlight.js themes](#supported-highlightjs-themes)) alongside
the script, or `{% code_block theme="…" %}` raises `ImproperlyConfigured`
for every theme you left out.

Keep the override under its own path, not djust's: a copy of a djust
static file at djust's own path (for example an old
`djust_components/vendor/highlight/highlight.js` in `STATICFILES_DIRS`)
shadows the vendored file, no longer matches djust's manifest, and fails
startup with `djust.B004`. Delete such a copy.

The override is never silent: `djust.B009` (a warning, not an error) names
both sources and both versions, so the fact that something is shadowing
djust's own copy stays visible in `manage.py check` output. Silence it
deliberately with `DJUST_CONFIG = {"suppress_checks": ["B009"]}` once
you've confirmed it's intentional.

## External assets

Vendoring is the default; loading from another origin needs an explicit
opt-in:

```text
# settings.py
DJUST_ALLOW_EXTERNAL_ASSETS = True
```

Without it, any manifest entry using `"url"` fails startup with
`djust.B005`. With it, every external file still needs `"integrity"` —
`djust.B006` catches one that doesn't.

**If your `STATIC_URL` is absolute** (serving your own vendored files from
S3, a CDN, or any host other than the app itself), djust adds
`crossorigin="anonymous"` to the rendered tag automatically — the browser
requires it before it will apply an SRI check to a cross-origin resource.
Your static host must send an `Access-Control-Allow-Origin` header back for
that fetch, or the browser blocks the file outright rather than silently
skipping the integrity check. Check that against your CDN/S3 CORS
configuration before you switch `STATIC_URL` to an absolute host.

## Supported highlight.js themes

`{% code_block %}` renders one of a curated set of vendored highlight.js
themes, not the full ~250 upstream themes:

- `github`
- `github-dark`
- `atom-one-dark`
- `atom-one-light`
- `monokai`
- `vs2015`
- `nord`
- `default`
- `dark`
- `a11y-dark`
- `a11y-light`
- `stackoverflow-light`
- `stackoverflow-dark`

`{% code_block theme="dracula" %}` (or any other name outside this list)
raises `ImproperlyConfigured` naming the thirteen available themes — never
a silently unhighlighted block.

## See also

- [Scanning a djust app](scanning.md) — SBOM setup and scanner commands.
- [Error Codes](error-codes.md#vendored-assets-and-sboms-b0xx) — every
  `djust.B0xx` check.
- [ADR-040](../../adr/040-vendored-assets-and-sboms.md) — the design.
