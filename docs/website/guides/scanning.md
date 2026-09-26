---
title: "Scanning a djust app"
slug: scanning
section: guides
order: 34.5
level: advanced
description: "Publish a CycloneDX SBOM of your app's vendored assets and Python-linked Rust crates, and scan it with Trivy, Syft, grype and OSV-Scanner."
---

# Scanning a djust app

djust can write a CycloneDX SBOM of every third-party asset it and your app
declare (see [Vendoring third-party JS](vendored-assets.md)), so container
scanners can match a vendored JS package against a published advisory the
same way they already do for Python and OS packages. This is
[ADR-040](../../adr/040-vendored-assets-and-sboms.md).

## Setting up the app SBOM

1. Set `DJUST_SBOM_PATH` to a full file path **outside every directory your
   web server serves** — outside `STATIC_ROOT`, `MEDIA_ROOT`, every
   `STATICFILES_DIRS` entry, and anything your reverse proxy or object
   storage exposes directly. djust only knows about the Django-side
   directories; check the rest against your own web server/CDN
   configuration. Name the file so it ends in `.cdx.json` — `osv-scanner`
   only recognizes a CycloneDX file with that extension:

   ```text
   # settings.py
   DJUST_SBOM_PATH = "/var/lib/myapp/sbom/djust-assets.cdx.json"
   ```

   Unset (the default), nothing is written and `manage.py check --deploy`
   warns with `djust.B011`. Pointed inside a served directory, startup
   fails with `djust.B012` and `collectstatic` refuses to write it.

2. Make sure `"djust"` is listed **above** `"django.contrib.staticfiles"`
   in `INSTALLED_APPS`. The SBOM is written by a `collectstatic` command
   override, and Django only lets an earlier app override a later app's
   management command. With `DJUST_SBOM_PATH` set and the order wrong,
   `manage.py check --deploy` fails with `djust.B013`; with no
   `DJUST_SBOM_PATH` set, an app in the old order sees no new check output
   at all — `djust.B013` only fires once there's an SBOM to write.

3. Run `collectstatic` as part of your build or deploy step. It writes the
   SBOM after collecting static files. `manage.py check --deploy` also
   verifies the file on disk still matches the currently declared assets
   (`djust.B014`) — useful as a build-time guard against a stale SBOM
   shipped in an image after a dependency bump.

You can also print or write the same document on demand, without running
`collectstatic`:

```bash
manage.py djust_sbom -o /var/lib/myapp/sbom/djust-assets.cdx.json
```

The app SBOM's root component is named after `DJUST_SBOM_NAME` when it's
set, or the first component of `ROOT_URLCONF` otherwise (for example
`myapp`, for `myapp.urls`).

## Exact commands

Use these exactly — some scanner subcommands silently ignore an embedded
SBOM in a way that looks like "nothing to report" rather than an error.

```bash
# Trivy: scan a filesystem root or a built image, never `trivy fs` (repo
# mode) — it does not read an embedded *.cdx.json the way `rootfs`/`image` do.
trivy rootfs /app
trivy image myapp:latest

# Syft: needs the SBOM cataloger explicitly enabled to read *.cdx.json files.
syft dir:/app --select-catalogers +sbom-cataloger

# grype: scan an SBOM file directly.
grype sbom:/var/lib/myapp/sbom/djust-assets.cdx.json

# OSV-Scanner: the file must end in .cdx.json (see setup step 1).
osv-scanner scan source -L /var/lib/myapp/sbom/djust-assets.cdx.json
```

djust's own CI uses the same OSV-Scanner command against
`python/djust/djust.cdx.json`, djust's own distribution SBOM (see
`.github/workflows/vendor.yml`). That `python/` prefix is only djust's own
source layout — it's where the file is generated and committed in the
djust repository. Once you `pip install djust`, the same file ships in two
places: inside the installed package directory (`<site-packages>/djust/djust.cdx.json`)
and under the wheel's PEP 770 metadata
(`<site-packages>/djust-<version>.dist-info/sboms/djust.cdx.json`). Find
the installed copy without knowing your `site-packages` path:

```bash
python -c "import djust, pathlib; print(pathlib.Path(djust.__file__).with_name('djust.cdx.json'))"
```

Known advisories that are accepted for
now (unmaintained, not vulnerable) are listed in `osv-scanner.toml` with a
`reason` and an `ignoreUntil` date — the same policy `.cargo/audit.toml`
already applies to Rust advisories: unmaintained warns, a real
vulnerability fails. An expired entry starts failing CI again until it's
re-reviewed. A weekly scheduled workflow reruns the same scan against the
latest tag of each supported release line and files one issue per
advisory per line.

## Coverage limits

- **`pip-audit`, Dependabot and Snyk do not see vendored JS.** They read
  Python package metadata and lockfiles, not bundled browser code. The
  SBOM this guide describes exists specifically to close that gap for
  scanners that *do* read CycloneDX (Trivy, Syft, grype, OSV-Scanner).
- **Dependabot can still be fed the SBOM**, through
  [GitHub's dependency submission API](https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/using-the-dependency-submission-api) —
  submit the document `manage.py djust_sbom` prints as part of your CI
  workflow.

## Python dependencies

This SBOM covers browser assets (and, for djust's own distribution SBOM,
the Rust crates linked into the extension) — it does not cover your
Python environment. Use [`cyclonedx-py`](https://cyclonedx-python.readthedocs.io/)
to generate an SBOM of your installed Python packages; run both tools in
CI if you want full coverage.

## See also

- [Vendoring third-party JS](vendored-assets.md) — manifests, integrity,
  and `{% djust_asset %}`.
- [Error Codes](error-codes.md#vendored-assets-and-sboms-b0xx) — every
  `djust.B0xx` check, including B011–B014 (the app SBOM).
- [ADR-040](../../adr/040-vendored-assets-and-sboms.md) — the design.
