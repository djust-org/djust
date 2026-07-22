# Surface Manifest (#2064)

## Why this exists

djust.org's `/docs/directives/` reference catalog silently lacked the entire
JS-commands family (`djust.js.JS` / `window.djust.js`) and both new hook
attributes (`dj-hook-value-*`, `dj-hook-target`) for months — nobody noticed
until a manual audit (fixed by hand in djust.org PR #52). Nothing detected
the drift because nothing on the framework side published a single
machine-readable statement of "this is the current user-facing surface."

This is phase 1: the framework emits that statement. Phase 2 (tracked
separately, NOT part of this change) is djust.org CI diffing its reference
doc's test fixture against this manifest and failing the build on drift.

## What it is

`djust.schema.get_surface_manifest()` returns a JSON-serializable dict:

```python
{
    "manifest_version": 1,
    "djust_version": "1.1.0rc8",
    "directives": [...],      # every dj-* template directive (from DIRECTIVES)
    "js_commands": [...],     # every djust.js.JS chain command, DERIVED via introspection
    "view_api": {
        "lifecycle_methods": [...],
        "class_attributes": [...],
        "decorators": [...],
        "navigation_methods": [...],
        "stream_methods": [...],
        "push_event_methods": [...],
    },
}
```

Every list is sorted by name so repeat calls produce a byte-identical diff.

Three deliberate design choices:

1. **`directives` is hand-curated, not derived.** There is no reliable way
   to walk the Rust template parser and recover human-readable descriptions
   and examples for each `dj-*` attribute — so `DIRECTIVES` in
   `python/djust/schema.py` stays a hand-maintained list. What keeps it
   honest is the parity canaries (below), not automation.
2. **`js_commands` is DERIVED, not hand-listed.** It's
   `sorted(m for m in dir(djust.js.JS) if not m.startswith("_"))`. A new
   command added to `djust.js.JS` appears here automatically — there is no
   parallel hand-maintained list to forget to update.
3. **`view_api` is reused, not reinvented.** It's built from the same
   sections `get_framework_schema()` already extracts
   (`LIFECYCLE_METHODS`, `CLASS_ATTRIBUTES`, `DECORATORS`,
   `NAVIGATION_METHODS`, `STREAM_METHODS`, `PUSH_EVENT_METHODS`).

Does **not** require Django setup — same framework-only contract as
`get_framework_schema()` (see `djust.mcp.server.create_server`'s
`get_framework_schema` tool, which calls it with no `django.setup()`).

## How djust.org (or any downstream consumer) uses it

```python
from djust.schema import get_surface_manifest

manifest = get_surface_manifest()
```

Or from the command line (no Django project required beyond `settings.py`
pointing at an `INSTALLED_APPS` that includes `"djust"`):

```bash
python manage.py djust_surface_manifest --indent 2
```

A downstream CI job fetches the manifest for the pinned djust version it
ships against, diffs `directives` names / `js_commands` / `view_api` entry
names against its own reference-doc fixture, and fails the build on any
name present in the manifest but missing from the docs (or vice versa,
depending on how strict the consumer wants to be). That diff step is phase 2
and lives in djust.org's repo, not here.

## What enforces freshness on the framework side

`python/djust/tests/test_surface_manifest_2064.py` — the load-bearing part
of this change (a manifest that can silently drift is decorative, #1859):

- **`TestJSCommandTriParity`** — extracts the chain-method names from the
  `const factory = {...}` object literal in
  `static/djust/src/26-js-commands.js` (`window.djust.js`), and asserts
  `client == djust.js.JS == manifest["js_commands"]`. Catches: a command
  added to the client without a Python mirror, or either added without the
  manifest picking it up (structurally impossible today since the manifest
  derives from `dir(JS)`, but the test also guards the derivation itself).
- **`TestDirectiveBindingParity`** — extracts every `dj-*` attribute name
  the client actually binds/reads (across every `static/djust/src/*.js`
  module, via a set of regexes anchored to real DOM-binding call shapes:
  `closest`/`querySelector(All)`/`matches` selector arguments,
  `get/has/set/removeAttribute`, `attr.name.startsWith(...)`, two-arg
  attribute-reading helpers, and `const _XXX_ATTR = 'dj-...'` constants) and
  asserts every one is documented in `DIRECTIVES` — either as a literal
  `name`, inside some entry's `related_attributes` (including `-*` prefix
  families like `dj-hook-value-*`), or as a dotted modifier of a directive
  that declares it in `modifiers` (currently only `dj-model`). A client-side
  attribute that's genuinely internal (never authored by a user — e.g.
  `dj-id`, the sticky-child auto-emitted markers) goes in the explicit,
  commented `_STRUCTURAL_EXCLUSIONS` dict, not a silent skip.
- **`TestManifestShape`** — `manifest_version` pin, `djust_version` matches
  the package, JSON-serializable, every list sorted, `directives` is a
  lossless copy of `DIRECTIVES`.
- **`TestGateOffNonVacuous`** (#1468) — neuters each canary (removes a real
  `DIRECTIVES` entry / prefix family / Python method from the comparison)
  and asserts the coverage-check logic actually reports the gap, proving
  the canaries are load-bearing rather than tautological.

Every non-vacuous-extraction test (`test_extraction_is_not_vacuous` and
siblings) exists so a future restructure of the client source (renaming the
`factory` object, changing the binding call convention) fails LOUDLY with a
"the regex found almost nothing" message instead of the parity assertion
silently passing against an empty set.

## Adding a new directive or JS command

- New `dj-*` directive: add an entry to `DIRECTIVES` in
  `python/djust/schema.py` (name/category/description/value/example, plus
  `related_attributes` for any sibling modifier attributes). The parity
  canary will fail your PR if you bind a new attribute client-side and
  forget this step.
- New JS Command: add the method to both `djust.js.JSChain`/`_JSFactory`
  (Python) and the client `factory` object in
  `static/djust/src/26-js-commands.js`. Nothing else to update — the
  manifest and the canary both derive from introspection.
