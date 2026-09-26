# ADR-040: Third-party browser code is vendored by default and published as an SBOM

**Status**: Accepted
**Date**: 2026-09-25
**Deciders**: Project maintainers
**Spec**: [docs/superpowers/specs/2026-09-25-vendored-assets-sbom-design.md](../superpowers/specs/2026-09-25-vendored-assets-sbom-design.md)
**Related**:
- `djust.components.dependencies` (removed): the undocumented, unused `DEPENDENCY_REGISTRY` / `DependencyManager` that pointed at cdnjs
- `js/markdown-editor` (moved to `js/vendor/`): the one existing vendored bundle, now one of four
- GHSA context: reported by a downstream govcon app on 1.2.2, alongside #3093–#3109 — the 35 packages bundled into `markdown-visual.js` were invisible to every scanner

---

## Context

A survey of main (2026-09-25) found five places third-party code entered a
djust-served page, and none of them left a version a scanner could find:

1. `markdown-visual.js` (505 KB, 35 npm packages) — vendored, but its only
   record of what's inside was a 46 KB `.LICENSE.txt` with no versions.
2. `components/dependencies.py` — highlight.js from cdnjs, pinned to
   `11.9.0`, no SRI, no importer anywhere in djust or its docs.
3. `templatetags/djust_components.py` — a second, live highlight.js loader
   from jsdelivr, `@11` floating, injected by an inline script, no SRI.
4. `ttyd_terminal.js` — xterm from `esm.sh`, `xterm@5` / `addon-fit@0.10`
   floating, loaded by a runtime `import()`, no SRI.
5. `admin_ext` base/login/logout templates — `cdn.tailwindcss.com`, the
   runtime Tailwind compiler, no version, no SRI.

Trivy and Syft scan a container image for known-vulnerable packages by
reading lockfiles, package metadata and SBOMs — never by fingerprinting
minified JS. A package whose version exists only in prose (or not at all)
cannot be matched against an advisory database. This is invisible risk:
the framework and its components can carry a vulnerable browser dependency
indefinitely with no scanner ever flagging it.

## Decision

1. **Vendored by default.** Every third-party file djust, a component
   package, or an app serves to a browser is declared in a
   `djust_assets.json` manifest and loaded from the app's own origin.
   External origins require `DJUST_ALLOW_EXTERNAL_ASSETS = True`, an exact
   version, and Subresource Integrity — the same bar the vendored path
   already clears.
2. **One manifest schema.** `{"schema": 1, "assets": {...}}`: each asset
   lists its files (`path` or `url`, plus `integrity`) and its packages
   (`purl` with `@version`, plus an SPDX `license`). Manifests are
   discovered from `<app>/djust_assets.json` (via `apps.get_app_configs()`)
   and from `DJUST_ASSET_MANIFESTS` for project-level files. Resolution
   order matches Django's own: project manifests first, then
   `INSTALLED_APPS` order.
3. **One build pipeline.** `js/vendor/` (formerly `js/markdown-editor/`)
   builds every djust-shipped bundle — `markdown-visual`, `highlight.js`,
   `xterm`, `admin-css` — from one lockfile, and regenerates each owning
   app's manifest from the esbuild metafile. `make vendor` builds;
   `make vendor-check` fails on drift.
4. **Rendering carries integrity.** `{% djust_asset "name" %}` renders
   `<script>`/`<link>` tags with `integrity=`, computed from the *stored*
   static file so `ManifestStaticFilesStorage` rewrites don't break it.
   `Component.requires_assets` declares what a component needs; it
   renders nothing by itself. A component outputs its tags with
   `{{ <instance>.asset_tags }}` in the page template or
   `self.asset_tags()` in Python (for example from `get_context_data`).
   The call is per instance, so when several instances share an asset
   the tags go once in a page-level template. `LiveComponent`
   does not inherit `Component`, so it has neither the helper nor the
   `djust.B007` check; it uses `{% djust_asset %}` in its template.
5. **System checks catch drift, `djust.B001`–`B010`.** Unparseable
   manifests, missing versions or licenses, missing or tampered files,
   undeclared `requires_assets`, an SBOM that would be served as a static
   file, a shadowed declaration, and an undeclared `<script src="http…">`
   in a template all fail (or warn) at `manage.py check --tag djust`.
6. **Three SBOM outputs, one generator
   (`djust.assets.sbom.to_cyclonedx`).**
   - The djust distribution's own SBOM, `python/djust/djust.cdx.json` in
     djust's own source tree (written by `make vendor` and `make version`
     via `python -m djust.assets.sbom --distribution`), covering
     vendored JS and the Rust crates linked into the extension (from
     `cargo metadata`, not `cargo cyclonedx`). Once djust is installed, the
     same file ships as `djust/djust.cdx.json` inside the installed
     package, and again at `.dist-info/sboms/` per PEP 770, via
     `[tool.maturin.sbom].include` (`maturin>=1.12.1,<2.0`).
   - The app's SBOM, written by a `collectstatic` override to
     `DJUST_SBOM_PATH` (no default — nothing is written unless it's set).
     `djust.B011`–`B014` catch it being unset, pointed inside a served
     directory, blocked by app ordering, or stale.
   - `manage.py djust_sbom [-o PATH]`, the same document on demand, for CI
     pipelines that submit SBOMs (e.g. GitHub's dependency submission API).
7. **CI gates on advisories.** The `vendor` PR job runs `make vendor-check`
   and scans `python/djust/djust.cdx.json` with `osv-scanner`, failing on
   any match; exceptions are explicit, dated entries in `osv-scanner.toml`.
   A weekly workflow re-scans the latest tag of each supported line and
   opens one issue per advisory per line.
8. **Disclosure stays narrow.** No SBOM and no served file (in particular
   `*.LICENSE.txt`) ever lists a version. The SBOM itself is never
   collected as a static file (`djust.B008`/`B012` enforce this). B008
   lists every static file, so when djust's `collectstatic` override is
   active it runs under `check --deploy` and before that command collects
   anything; otherwise it runs on every check, as it did originally (#3144).

## Consequences

- **Positive.** A team scanning a djust app's image with Trivy or Syft now
  gets a real match on a vendored package's advisory. Every version that
  reaches a browser has one canonical, machine-readable record.
- **Overriding a bundled library no longer waits on a djust release.**
  Because manifests resolve in Django's app order and project manifests
  win outright, an app or a component package can ship its own
  `djust_assets.json` entry for `highlight.js` (say, for a security fix)
  and it shadows djust's. `djust.B009` warns, naming both sources and
  versions, so the override is visible rather than silent — it does not
  block the app from starting.
- **highlight.js themes are curated, not all ~250 upstream themes.** The
  vendored set is github, github-dark, atom-one-dark, atom-one-light,
  monokai, vs2015, nord, default, dark, a11y-dark, a11y-light,
  stackoverflow-light, stackoverflow-dark. `{% code_block theme="..." %}`
  with any other name raises `ImproperlyConfigured` naming the thirteen
  available themes — the same loud-failure posture as B005/B006, never a
  silently unhighlighted block. (Each djust release already publishes
  ~20 platform wheels against the PyPI 10 GB project cap; shipping all
  upstream themes would make that worse for no benefit most apps use.)
- **The flat SBOM shape is deliberate, not simpler-looking-but-wrong.**
  The Task 0 scanner probe found `trivy sbom` reports zero matches when a
  package is nested inside its owning asset's own `components[]` array,
  but an instant match when the same package is a top-level `components`
  entry linked to its asset through the top-level `dependencies` array
  (Syft, grype and osv-scanner read both shapes; trivy does not). Every
  SBOM this design emits — the distribution file, the app file, and the
  CLI output — is flat for this reason, recorded in the spec's "Scanner
  probe results" section.
- **tailwindcss appears in the SBOM as a `library` component named
  `tailwindcss`, under the `admin-css` asset, even though it is a
  build-time `devDependency`.** The
  static `admin.css` build embeds Tailwind's MIT-licensed preflight CSS
  in its output, so `tailwindcss@3.4.19` is recorded as a package of the
  `admin-css` asset. `admin.css` itself keeps exactly one upstream
  `/*! tailwindcss v3.4.19 | MIT License | ... */` banner comment — a
  single-library attribution, not a version list — which is a documented
  exception to the "no served file lists a version" disclosure rule
  because the banner ships unmodified from the upstream build and names
  only Tailwind's own version, not djust's dependency graph.
- **The `osv-scanner.toml` advisory allowlist follows the existing
  `.cargo/audit.toml` policy**, not a new one: unmaintained advisories are
  recorded and reviewed by a date, vulnerabilities always fail. The one
  current entry, `RUSTSEC-2025-0141` (bincode, unmaintained, not
  vulnerable), is ignored until 2026-12-31 for exactly the reason
  `.cargo/audit.toml` already treats it as a warning, not a block.
- **`{% djust_asset %}` needed a tag-library registration most
  contributors won't think to look for.** It only works inside a
  LiveView template because `djust.templatetags.djust_assets` was added
  to `djust.template_libraries._DJUST_TAGS_BRIDGED` — the Rust template
  engine only exposes tags from libraries listed there. Any future
  built-in tag library needs the same registration, or it renders fine
  in Django's own template engine but silently does nothing under a
  LiveView.
- **Negative.** Dependabot, `pip-audit` and Snyk still do not look inside
  wheels or bundled JS; they see none of this. Vendoring adds one more
  build pipeline (`js/vendor/`) whose output must stay byte-identical
  (`make vendor-check`) for the SBOM digest checks (`djust.B014`) to be
  meaningful. Fingerprinting a served bundle against a public build can
  still identify its version — this design stops djust from publishing
  the version list itself; it does not make the bundles unidentifiable.
- **Neutral.** 1.2.x is not backported. SPDX output and Python dependency
  SBOMs (covered by `cyclonedx-py`) are out of scope.

## Alternatives rejected

- **A. CDN with Subresource Integrity only, no vendoring.** Keeps the
  external-origin dependency (an outage or a compromised CDN still breaks
  or poisons the page) and still leaves nothing for Trivy/Syft to scan —
  SRI verifies the browser got the expected bytes, it records no purl or
  version anywhere a scanner reads. Rejected.
- **B. Python-side dependency declarations** (extending
  `djust.components.dependencies`'s `DEPENDENCY_REGISTRY` instead of
  replacing it). Keeps versions in Python, disconnected from the actual
  built JS/CSS; nothing catches the declaration and the shipped file
  drifting apart. `djust.B004`'s file-hash check is the reason a
  file-centric manifest was chosen instead. Rejected.
- **C. A hand-written, ad hoc JSON format instead of CycloneDX.** Would
  need djust to define its own advisory-matching semantics and its own
  purl-equivalent, and no existing scanner would read it. CycloneDX 1.6 is
  read natively by Trivy, Syft, grype and osv-scanner today. Rejected.
- **D. Per-bundle lockfiles** (one `package.json`/lockfile per vendored
  bundle, mirroring how `js/markdown-editor/` worked alone). Multiplies
  the number of places a version can drift and the number of `npm ci`
  runs in CI; one `js/vendor/` project with one lockfile is what the
  `bundles.mjs` table pattern assumes. Rejected.
