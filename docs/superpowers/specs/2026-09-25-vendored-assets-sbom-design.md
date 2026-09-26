# Vendored third-party assets and SBOMs

Date: 2026-09-25
Status: approved design, pending spec review
Target: the next 1.3 pre-release after v1.3.0rc2 (no 1.2.x backport)

## Goal

Components, component packages, and apps can ship third-party browser code,
and every version that reaches a browser is recorded where downstream
security scanners find it. Nothing is loaded from an external origin unless
the app opts in, and every way this can drift fails loudly rather than
silently.

Success: a team scanning a container image of a djust app with Trivy or
Syft gets a match on a published advisory for a package djust or their app
vendors (for example a ProseMirror CVE), and djust's own CI fails when a
vendored package has a known advisory.

## Background

Reported by a downstream govcon app on 1.2.2 (alongside #3093–#3109): the
35 packages bundled into `markdown-visual.js` are invisible to every
scanner, because their versions exist only in a 46 KB
`markdown-visual.LICENSE.txt`; and `components/dependencies.py` points at
cdnjs, an external origin serving executable code.

A survey of main (2026-09-25) found five places third-party code enters a
page:

| # | Where | Delivery | Pinned | SRI |
|---|---|---|---|---|
| 1 | `components/static/djust_components/markdown-visual.js` (505 KB, 35 packages) | vendored, `js/markdown-editor/build.mjs` | lockfile | n/a |
| 2 | `components/dependencies.py:49` highlight.js | cdnjs | 11.9.0 | no |
| 3 | `components/templatetags/djust_components.py:1930` highlight.js | jsdelivr, injected by inline script | `@11` floating | no |
| 4 | `components/static/djust_components/ttyd/ttyd_terminal.js:12` xterm | esm.sh `import()` | `xterm@5`, `addon-fit@0.10` floating | no |
| 5 | `admin_ext/templates/djust_admin/{base,login,logout}.html` | `cdn.tailwindcss.com` (runtime compiler) | no | no |

No file in djust uses SRI. `djust.components.dependencies` (#2) has no
importer in djust or its docs; only demo apps, which carry their own copy.
The live highlight.js loader is #3.

### Scanner facts this design depends on (verified 2026-09-25)

- PEP 770 is final: SBOMs belong in `.dist-info/sboms/`, and may describe
  bundled non-Python components. maturin ≥ 1.12.1 copies user files there
  via `[tool.maturin.sbom].include`.
- Trivy deliberately skips `.dist-info/sboms/` (PR #10033, v0.69.0) but
  detects `*.cdx.json` / `*.spdx.json` elsewhere on the filesystem.
- Syft reads SBOM files (`*.cdx.*`, `*.bom.*`, `*.spdx.*`, …) only with
  `--select-catalogers +sbom-cataloger`; it walks nested CycloneDX
  components.
- pip-audit, Dependabot and Snyk do not look inside wheels.
- Scoped npm purls percent-encode `@`: `pkg:npm/%40tiptap/core@3.31.3`.

## Decisions

1. **Scope:** djust's built-in components, third-party component packages,
   and the app's own vendored files all declare through one mechanism.
2. **External origins:** vendored by default. External assets require
   `DJUST_ALLOW_EXTERNAL_ASSETS = True`, an exact version, and SRI. All of
   djust's own assets are vendored.
3. **Declaration:** a `djust_assets.json` manifest per Django app, plus
   project manifests listed in a setting. Components reference assets by
   name. `djust.components.dependencies` is deleted (no users).
4. **Advisories:** CI blocks on known advisories; a weekly job scans
   released lines.
5. **Build:** one `js/vendor/` npm project with one lockfile builds every
   djust-shipped asset.
6. **Disclosure:** no SBOM or version list is ever served. The app SBOM is
   written only to an explicitly configured path outside anything djust
   knows is served; served license files carry no version numbers.

## 1. Manifest format

### Location and discovery

- `<app>/djust_assets.json`, next to `apps.py`, discovered via
  `apps.get_app_configs()`. Covers djust's own apps and component packages.
- `DJUST_ASSET_MANIFESTS`: list of extra paths, for project files under
  `STATICFILES_DIRS`. Default `[]`.

### Schema (version 1)

```json
{
  "schema": 1,
  "assets": {
    "markdown-visual": {
      "files": [
        {"path": "djust_components/markdown-visual.js",
         "integrity": "sha384-…"}
      ],
      "license_file": "djust_components/markdown-visual.LICENSE.txt",
      "packages": [
        {"purl": "pkg:npm/%40tiptap/core@3.31.3", "license": "MIT"}
      ]
    },
    "chart.js": {
      "files": [
        {"url": "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
         "integrity": "sha384-…"}
      ],
      "packages": [{"purl": "pkg:npm/chart.js@4.4.0", "license": "MIT"}]
    }
  }
}
```

Rules:

- `schema` must be `1`.
- Each file has exactly one of `path` (a staticfiles path, vendored) or
  `url` (external), plus `integrity` in SRI form (`sha256-`, `sha384-` or
  `sha512-`). A file may carry `variant` (for example a highlight.js theme)
  and `type` (`script`, `module`, `style`); `type` defaults from the
  extension (`.js` → `script`, `.mjs` → `module`, `.css` → `style`).
- `packages` is non-empty. Each `purl` must include `@version`; the purl is
  the only record of name and version. Non-npm sources use
  `pkg:github/<owner>/<repo>@<tag>` or
  `pkg:generic/<name>@<version>?download_url=<url>`. `license` is an SPDX
  expression.
- `license_file` is optional (required when the build generates one).
- Excluded: transitive dependency graph, CPE, per-package hashes inside a
  bundle.

### Name resolution

Asset names are global. When two manifests declare the same name, project
manifests (`DJUST_ASSET_MANIFESTS`, in list order) win over apps, and apps
earlier in `INSTALLED_APPS` win over later ones, matching Django's template
and static-file order. Every shadowed declaration raises warning B009
naming both sources and both versions. This is how an app ships a
security fix for a djust-bundled library before djust releases one.

## 2. Build pipeline (`js/vendor/`)

`js/markdown-editor/` moves to `js/vendor/`; its `test/*.check.mjs` checks
move with it.

- One `package.json` and `package-lock.json`. Shipped libraries are
  `dependencies`; build tools (esbuild, Tailwind CLI, jsdom, prettier) are
  `devDependencies` and never appear in an SBOM.
- `bundles.mjs` lists each bundle: asset name, owning app (target
  manifest), entry point or copy source, output paths.

| Asset | Owning app | Build |
|---|---|---|
| `markdown-visual` | `djust.components` | esbuild entry (unchanged behaviour) |
| `highlight.js` | `djust.components` | esbuild bundle of `highlight.js` core; theme CSS copied from the package as `variant` files |
| `xterm` | `djust.components` | esbuild ESM bundle of `@xterm/xterm` + `@xterm/addon-fit` |
| `admin-css` | `djust.admin_ext` | Tailwind CLI over `admin_ext` templates |

`build.mjs`, per bundle:

1. Runs after `npm ci`, so versions come only from the lockfile.
2. Builds with `metafile: true`; for every input under `node_modules/`,
   walks up to the owning `package.json` and records name, version and
   `license` as a purl. A package without an SPDX `license` fails the build.
3. Writes the bundle; writes `<bundle>.LICENSE.txt` with package **names
   and license texts only, no versions**; computes the SRI hash.
4. Regenerates each owning app's `djust_assets.json` in full (never merged
   by hand).

After the JS build, `make vendor` runs `python -m djust.assets.sbom
--distribution`, which reads djust's own manifests directly (no Django
settings) and writes `python/djust/djust.cdx.json`.

Make targets:

- `make vendor` builds everything.
- `make vendor-check` builds into a temp directory and fails if any
  bundle, license file, manifest or `djust.cdx.json` differs from what is
  committed. esbuild output is deterministic for a fixed lockfile, so the
  comparison is byte-exact.

Admin CSS risk: the Tailwind runtime compiles any class, including ones
built in Python. The static build only sees classes present in scanned
files. Before switching, visually diff every admin page CDN vs static
build; add any missed classes to a Tailwind safelist.

## 3. Runtime and system checks

### `djust.assets`

- `DjustConfig.ready()` loads every manifest once into a process-lifetime
  registry. Manifest problems never raise at import; they surface as
  system-check errors.
- API: `get_asset(name)`, `asset_tags(name, variant=None)`, and the
  template tag `{% djust_asset "name" [variant="…"] %}` (tag library
  module chosen in the plan).
- Rendered tags:
  - vendored file: `static()` URL plus `integrity=`, computed from the
    **stored** file on first use and cached (so `ManifestStaticFilesStorage`
    rewriting `url()` in CSS does not break SRI);
  - external file: the manifest's `integrity=` plus
    `crossorigin="anonymous"`.
  A floating external URL with SRI fails loudly in the browser, so URLs are
  not parsed for floating versions.
- `Component.requires_assets = [...]`: validated by B007; the component's
  render calls `asset_tags()` for those names. No page-wide collector.

### Loader migration

- highlight.js: the `code_block` tag renders
  `asset_tags("highlight.js", variant=theme)` instead of the CDN injector.
  The MutationObserver that re-highlights WS-patched code (#1625) stays.
- xterm: `ttyd_terminal.js` imports a same-origin module URL read from a
  `data-` attribute rendered with `static()`.
- `markdown-visual`: existing hand-written `<script>` tags keep working
  (the file is declared regardless); docs move to `{% djust_asset %}`.

### System checks

| ID | Level | Condition |
|---|---|---|
| B001 | Error | Manifest unparseable or fails schema |
| B002 | Error | purl without `@version`, or package without `license` |
| B003 | Error | Vendored `path` not found by staticfiles finders |
| B004 | Error | Vendored file's hash ≠ manifest `integrity` |
| B005 | Error | External asset declared while `DJUST_ALLOW_EXTERNAL_ASSETS` is false |
| B006 | Error | External asset without `integrity` |
| B007 | Error | `requires_assets` names an undeclared asset |
| B008 | Error | Staticfiles finders find a `*.cdx.json`, `*.spdx.json` or `*.bom.json` (would be collected and served) |
| B009 | Warning | A declaration shadows another (names both sources and versions) |
| B010 | Warning | A template has `<script src="http…">` / `<link href="http…">` to an origin no manifest declares (heuristic; silenceable) |
| B011 | Warning (deploy) | `DJUST_SBOM_PATH` unset |
| B012 | Error | `DJUST_SBOM_PATH` resolves inside `STATIC_ROOT`, `MEDIA_ROOT` or any `STATICFILES_DIRS` entry |
| B013 | Error (deploy) | `djust` is after `django.contrib.staticfiles` in `INSTALLED_APPS` (the `collectstatic` override would not run) |
| B014 | Error (deploy) | SBOM at `DJUST_SBOM_PATH` is missing or its recorded manifest digest differs from the current registry |

B004 hashes about 1 MB of vendored files each time checks run (runserver,
migrate, check); never per request.

## 4. SBOM outputs

One generator, `djust.assets.sbom.to_cyclonedx(registry, root)`, emits
CycloneDX 1.6 JSON from the resolved registry:

- `metadata.component` is the root (djust, or the project);
- each asset is a component of `type: "file"` with `hashes` and
  `evidence.occurrences[].location` set to its static path;
- each package is nested in its asset's `components` (assembly), with
  `purl` and `licenses`;
- external assets carry property `djust:delivery=external` and an
  `externalReferences` entry with the URL;
- property `djust:manifest-digest` records a SHA-256 over the resolved
  registry, used by B014.
- `serialNumber` and `metadata.timestamp` (both optional in CycloneDX) are
  omitted and all arrays are sorted, so output is byte-identical for the
  same inputs; `vendor-check` depends on this.

### Output 1: the djust distribution

- `python/djust/djust.cdx.json`, committed and verified by `vendor-check`.
  It covers vendored JS and the Rust crates linked into the extension,
  derived from `cargo metadata --format-version 1` (normal dependencies
  reachable from the bindings crate, as `pkg:cargo/<name>@<version>`)
  rather than `cargo cyclonedx`, so no extra tool is needed and the output
  is deterministic. It lives in
  `site-packages` outside `dist-info`, where Trivy and Syft filesystem
  scans find it, and is never under a `static/` directory.
- The same file is included in `.dist-info/sboms/` via
  `[tool.maturin.sbom].include` for PEP 770 compliance. Build requirement
  becomes `maturin>=1.12.1,<2.0`.
- Syft with `+sbom-cataloger` may report packages twice; accepted.

### Output 2: the app

- A `collectstatic` override writes `djust-assets.cdx.json` covering every
  resolved asset (djust, packages, project) to `DJUST_SBOM_PATH`.
- `DJUST_SBOM_PATH` has **no default**. Unset: nothing is written and B011
  warns on `--deploy`. Inside a served directory djust knows about: B012
  errors and `collectstatic` refuses to write. djust cannot see the web
  server's document roots, so the guide tells users to keep the path out
  of anything their server serves.
- The file is never served by djust.

### Output 3: CLI

`manage.py djust_sbom [-o PATH]` prints the same document (for CI
pipelines that submit SBOMs, such as GitHub's dependency submission API).
CycloneDX only.

Python packages are out of scope; `cyclonedx-py` covers the environment.

### Disclosure limit

Served bundles can still be fingerprinted by hashing them against
published npm builds. The design stops djust from publishing its own
version list; it does not make bundles unidentifiable.

## 5. CI

- PR job `vendor`: `make vendor-check`, then `osv-scanner` against
  `python/djust/djust.cdx.json`, failing on any advisory. Exceptions live
  in `osv-scanner.toml` `[[IgnoredVulns]]` with `id`, `reason` and
  `ignoreUntil`; an expired entry fails again.
- Weekly scheduled workflow: for the latest tag of each supported line that
  contains `djust.cdx.json` (1.3 onward), scan it with the current advisory
  database and open one issue per advisory × line (deduplicated by title).

## 6. Rollout (the next 1.3 pre-release after v1.3.0rc2)

- `js/vendor/`, manifests, `djust.assets`, checks B001–B014, all three
  SBOM outputs, CI job and weekly workflow.
- All five external loads removed: highlight.js vendored and both loaders
  replaced; xterm vendored; admin static CSS (after the visual diff);
  `components/dependencies.py` deleted.
- `djust_theme` scaffold stops emitting `cdn.tailwindcss.com`; the hint in
  `checks_css_proposal.py` is updated.
- Docs: markdown-editor guide uses `{% djust_asset %}`; new guides
  "Vendoring third-party JS" (manifest format for packages and apps) and
  "Scanning a djust app" (`trivy fs`, `syft … --select-catalogers
  +sbom-cataloger`, `grype sbom:…`; states that pip-audit, Dependabot and
  Snyk will not see vendored JS); CHANGELOG security entry; ADR-040
  recording vendored-by-default, the manifest format and the SBOM outputs.
- Before merge: run checks in djust.org, djustlive and djust-docs
  (`make docs-verify`). CDN tags in their templates will raise B010; each
  is either declared or removed.

## Testing

- Manifest parsing and each check B001–B014: unit tests with fixture apps
  (valid, missing file, hash mismatch, floating purl, external without
  opt-in, shadowing, SBOM path inside `STATIC_ROOT`, app order).
- `asset_tags`: vendored integrity computed from stored file, including
  under `ManifestStaticFilesStorage` with a CSS `url()` rewrite; external
  tags carry `crossorigin`.
- SBOM generator: golden-file test; validate output against the CycloneDX
  1.6 JSON schema; assert nested purls for a scoped package.
- `collectstatic` override: writes to `DJUST_SBOM_PATH`, refuses inside
  `STATIC_ROOT`, writes nothing when unset.
- `vendor-check`: CI proves determinism by running it on a clean checkout.
- End-to-end (manual, recorded in the PR): build a wheel, install into a
  container image, confirm `trivy fs` and `syft --select-catalogers
  +sbom-cataloger` report `@tiptap/core` at the bundled version.
- Browser: code block highlighting (initial load and WS patch), ttyd
  terminal, markdown visual editor, admin pages visual diff.

## Out of scope

- 1.2.x backport.
- SPDX output.
- Python dependency SBOMs.
- #3107/#3108 (table actions, bubble menu); they change the bundle's
  package list, which this design then tracks automatically.

## Scanner probe results (Task 0)

Probe method: two minimal CycloneDX 1.6 SBOMs, differing only in where the
`lodash@4.17.20` (`pkg:npm/lodash@4.17.20`) component lives — nested inside
`probe-bundle`'s own `components[]` array vs. flat at the top level with a
`dependencies` edge from `probe-bundle` to it. Each scanner run was checked
for whether it surfaces `lodash@4.17.20`'s known vulnerability
CVE-2021-23337 (aliased as GHSA-35jh-r3h4-6jhm / GHSA-r5fr-rjxr-66jc in the
OSV/GHSA databases the scanners query).

Versions: `trivy 0.74.0`, `syft 1.52.0`, `osv-scanner 2.6.0`
(osv-scalibr 0.5.2), `grype 0.119.0` — all installed via `brew install
trivy syft osv-scanner grype` (approved by the controller).

| Scanner (version) | nested | flat |
|---|---|---|
| trivy sbom (0.74.0) | no — `trivy sbom --quiet nested.cdx.json` reports "Report Summary: Target - / Type - / Vulnerabilities -" (not scanned, 0 matches for CVE-2021-23337) | yes — same command on `flat.cdx.json` reports `Node.js (node-pkg)`, 5 vulnerabilities, including `CVE-2021-23337` (HIGH) |
| trivy fs (0.74.0) | no — `trivy fs --quiet fs-nested/` (dir containing only `nested.cdx.json`) logs `WARN Supported files for scanner(s) not found. scanners=[vuln]`, 0 matches | no — identical result on `fs-flat/`; `trivy fs` does not treat a loose `*.cdx.json` file as an SBOM to parse in this version (no `--sbom-sources` support for local files), so this row is uninformative rather than a shape signal, but note it is not a "yes" for either shape |
| syft +sbom-cataloger (1.52.0) | yes — `syft dir:fs-nested --select-catalogers +sbom-cataloger -o json \| grep -c '"pkg:npm/lodash@4.17.20"'` → `1` | yes — same command on `fs-flat` → `1` |
| grype (0.119.0) | yes — `grype sbom:nested.cdx.json` lists `lodash 4.17.20` with 5 GHSA advisories (incl. GHSA-35jh-r3h4-6jhm / CVE-2021-23337), exit 0 | yes — `grype sbom:flat.cdx.json` produces the identical 5-row table |
| osv-scanner (`osv-scanner scan source -L nested.cdx.json`) | yes — found 1 package, "Total 1 package affected by 3 known vulnerabilities", table includes `https://osv.dev/GHSA-35jh-r3h4-6jhm` (= CVE-2021-23337 per `--format json` aliases), exit=1 | yes — `osv-scanner scan source -L flat.cdx.json` produces the identical result, exit=1 |

The brief's suggested `-L`/`--lockfile` flag worked as-is against both
`.cdx.json` files in osv-scanner 2.6.0 (no need to fall back to the
deprecated `-S`/`--sbom` flag); no flag substitution was required.

Decision: **SBOM_SHAPE = flat**. `trivy sbom` — a scanner djust explicitly
targets in the "End-to-end" testing section above — gives a definite "no"
on the nested shape (0 findings, package invisible) while finding the same
component instantly when it is flattened to top-level `components[]` with
a `dependencies` edge. Per the decision rule (any "no" in the nested column
forces flat), the generator must emit vendored packages as top-level
`components` entries linked via `dependencies`, not nested inside the
`probe-bundle`/asset component's own `components[]`.

## Planning amendments

The implementation plan (`docs/superpowers/plans/2026-09-25-vendored-assets-sbom.md`,
"Global Constraints") made six amendments to this design while planning,
ahead of any implementation code:

1. **B013 is conditional.** It fires only when `DJUST_SBOM_PATH` is set.
   The demo project (and most apps) list `"djust"` after
   `"django.contrib.staticfiles"`; without an SBOM path the `collectstatic`
   override has nothing to do.
2. **Schema validation is replaced by scanner canaries.** Validating
   against the CycloneDX 1.6 JSON schema needs the schema plus its
   SPDX/JSF sub-schemas vendored into tests. Parsing by the real consumers
   (the Task 0 probe, the CI canary) is the stronger test.
3. **Nested vs flat is decided by Task 0.** If any of Trivy, Syft or
   OSV-Scanner ignores nested `components`, the generator emits packages
   top-level and records asset→package in `dependencies`. (Decided above:
   flat.)
4. **`vendor-check` uses `git diff` on a rebuilt tree**, like the earlier
   `markdown-editor.yml`, rather than a temp directory.
5. **highlight.js themes are a curated set** (thirteen, listed in the
   vendoring guide), not all ~250 upstream themes; an unknown theme is a
   loud `ImproperlyConfigured` naming the available ones. (Wheel size: each
   release publishes ~20 platform wheels against the PyPI 10 GB project
   cap.)
6. **Modules render as `<link rel="modulepreload" integrity>`** plus
   `{% djust_asset_url %}` for the importing code, because `import()`
   cannot carry SRI.

### Execution rulings

Facts established while implementing this design, recorded here because
they refine or correct the text above:

1. **Scanner commands.** Use `trivy rootfs /app` or `trivy image <image>`,
   never `trivy fs` — `trivy fs` (repository mode) does not read an
   embedded `*.cdx.json` the way `rootfs`/`image` do. The tested
   OSV-Scanner command is `osv-scanner scan source -L <path>.cdx.json`;
   the file must end in `.cdx.json` for OSV-Scanner to recognize it.
2. **The SBOM is flat, not nested** (see the Task 0 decision above):
   packages are top-level components, and each asset links to its
   packages through the top-level `dependencies` array, because `trivy
   sbom` ignores components nested inside another component.
3. **xterm is pinned to 5.5.0 with `@xterm/addon-fit` 0.10.0** — the ttyd
   hook targets the xterm 5 API; upgrading to xterm 6 is a separate
   change. highlight.js is 11.12.0 and tailwindcss is 3.4.19.
4. **tailwindcss appears in the SBOM as the `admin-css` package**, even
   though it is a `devDependency`, because its MIT preflight CSS is
   embedded in the built `admin.css` output. `admin.css` keeps exactly one
   upstream `tailwindcss v3.4.19` attribution banner — a single-library
   banner, not a version list, and a documented exception to the
   no-version-in-served-files rule.
5. **Advisory policy.** `osv-scanner.toml` ignores `RUSTSEC-2025-0141`
   (bincode, unmaintained) until 2026-12-31, following the existing
   `.cargo/audit.toml` policy: unmaintained advisories warn, vulnerabilities
   fail.
6. **Registering a tag library for the Rust engine.** `{% djust_asset %}`
   only renders inside a LiveView template because
   `djust.templatetags.djust_assets` was added to
   `djust.template_libraries._DJUST_TAGS_BRIDGED` — any future built-in tag
   library needs the same registration.
7. **Two B-check details confirmed against `checks/sbom.py`.** B013 is
   registered `deploy=True` and returns no messages when `DJUST_SBOM_PATH`
   is unset (amendment 1, above). B011 is a separate `deploy=True` check
   that warns whenever `DJUST_SBOM_PATH` is unset, independent of B013.
