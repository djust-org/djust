# Changelog

All notable changes to djust will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Bootstrap 4 CSS framework adapter** — New `Bootstrap4Adapter` for projects using Bootstrap 4 (NYC Core Framework, government sites, legacy projects). Set `DJUST_CONFIG = {"css_framework": "bootstrap4"}`. Includes proper `custom-select`, `custom-control-*` classes for checkboxes/radios, and `form-group` wrappers.
- **Dedicated radio button classes** — Radio buttons now use `radio_class`, `radio_label_class`, and `radio_wrapper_class` config keys (with fallback to checkbox classes). Both Bootstrap 4 and 5 configs define radio-specific classes.
- **Select widget class support** — `ChoiceField` with `Select` widget uses `select_class` config key (e.g., `custom-select` for BS4, `form-select` for BS5) instead of the generic `field_class`.
- **Theme-to-framework CSS bridge** — New `{% theme_framework_css %}` template tag generates `<style>` overrides that map djust theme variables (`--primary`, `--border`, etc.) onto the active CSS framework's selectors (`.btn-primary`, `.form-control`, `.alert-*`, etc.). Switching themes now automatically re-styles Bootstrap 4/5 and Tailwind components.

### Fixed

- **Derived container context values now tracked by value equality ([#774](https://github.com/djust-org/djust/issues/774))** — The Rust state sync used `id()` comparison for all non-immutable context values, which is unreliable for containers (dict, list, tuple) due to CPython address reuse after GC. Derived values like `current_step = wizard_steps[step_index]` could be missed when the handler only changed `step_index`, causing Rust to render stale HTML. Fix: containers are now compared by value equality (like immutables already were), with previous values cached in `_prev_context_containers`. The optimization is preserved — unchanged containers are still skipped.

## [0.5.0rc1] - 2026-04-19

### Added

- **Package consolidation: all 5 runtime packages folded into djust** — One install, one version, one CHANGELOG. `pip install djust` stays lean; `pip install djust[all]` gets everything.
  - **Phase 1+2: `djust-auth` + `djust-tenants` → core** ([#770](https://github.com/djust-org/djust/pull/770)) — `djust-auth` (879 LOC) merged into `python/djust/auth/` package with lazy imports. `djust-tenants` missing modules (audit, middleware, managers, models, security) merged into existing `python/djust/tenants/`. Both are core — no extras needed. 27 new tests.
  - **Phase 3: `djust-admin` → `djust[admin]`** ([#771](https://github.com/djust-org/djust/pull/771)) — 3,878 LOC merged into `python/djust/admin_ext/` (avoids collision with `django.contrib.admin`). Views, forms, adapters, plugins, decorators, template tags, 7 HTML templates. 40 new tests.
  - **Phase 4: `djust-theming` → `djust[theming]`** ([#772](https://github.com/djust-org/djust/pull/772)) — 49,105 LOC merged into `python/djust/theming/`. CSS theming engine, design tokens, 96 HTML templates, 9 static files, management command (`djust_theme`), 4 template tag modules, gallery sub-package. 749+ tests.
  - **Phase 5: `djust-components` → `djust[components]`** ([#773](https://github.com/djust-org/djust/pull/773)) — ~100K LOC merged into `python/djust/components/`. 170+ UI component classes, 6 template tag modules, management command (`component_gallery`), descriptors, mixins, rust_handlers.py. Extra deps: `markdown>=3.0`, `nh3>=0.2`.

## [0.4.5rc2] - 2026-04-18

### Added

- **AI observability module: `djust.observability`** — DEBUG-gated, localhost-only HTTP endpoints that give external tooling (like the djust Python MCP and djust-browser-mcp) live visibility into framework state without in-process coupling. Ships as seven endpoints under `/_djust/observability/`: `health`, `view_assigns`, `last_traceback`, `log_tail`, `handler_timings`, `sql_queries`, `reset_view_state`, `eval_handler`. Each pairs with a matching MCP tool. Security model mirrors django-debug-toolbar (DEBUG=True + `LocalhostOnlyObservabilityMiddleware`). Requires `path("_djust/observability/", include("djust.observability.urls"))` in the project urls.py.
- **`get_view_assigns`** — Real server-side `self.*` state of the mounted LiveView for a given session. Complements browser-mcp's client-only `djust_state_diff` with the source of truth. Per-attr fallback tags non-serializable values with `{_repr, _type}` rather than an all-or-nothing blanket.
- **`get_last_traceback`** — Ring-buffered (50) exception log populated from `handle_exception()`. Replaces "can you paste the terminal?" for 80% of blind-debugging cases.
- **`tail_server_log`** — Ring-buffered (500) Django/djust log records with `since_ms` + `level` filters. `djust.*` captured at DEBUG+, `django.*` at WARNING+.
- **`get_handler_timings`** — Per-handler rolling 100-sample distribution (min/max/avg/p50/p90/p99). Reuses existing `timing["handler"]` measurements; no extra perf counters.
- **`get_sql_queries_since`** — Per-event SQL capture via `connection.execute_wrappers`. Queries are tagged with `(session_id, event_id, handler_name)` + `stack_top` filtered to skip framework frames.
- **`reset_view_state`** — Replay `view.mount()` on a registered instance. Clears public attrs, re-invokes `mount(stashed_request, **stashed_kwargs)`. Useful between fixture replays.
- **`eval_handler`** — Dry-run a handler against a live view's current state. Returns `{before_assigns, after_assigns, delta, result}`. v2 `dry_run=True` installs a `DryRunContext` that blocks `Model.save`/`delete`, `QuerySet.update`/`delete`/`bulk_create`/`bulk_update`, `send_mail`/`send_mass_mail`, `requests.*`, and `urllib.request.urlopen` — first attempt raises `DryRunViolation` and the response surfaces `{blocked_side_effect}`. `dry_run_block=False` records without blocking. Process-wide lock serializes dry-runs.
- **`find_handlers_for_template(template_path)` in djust MCP** — Cross-references a template file against every view that uses it, returning dj-* handlers wired in the template and the diff against view handler methods. Catches dead bindings at author time (complements djust-browser-mcp's runtime `find_dead_bindings`).
- **`seed_fixtures(fixture_paths)` in djust MCP** — Subprocess wrapper around `manage.py loaddata` for regression-fixture DB setup.

### Fixed

- **`hotreload`: suppress empty-patch broadcasts on unrelated file changes ([#763](https://github.com/djust-org/djust/issues/763))** — When a Python file changes that doesn't affect the currently-mounted view, re-render produces zero patches. The old code still broadcast ~14 KB (empty patches + full `_debug` state dump) to every connected session. Early-return when `hotreload=True AND patches==[]`. Non-hot-reload empty patches still sent (loading-state clear ack needed).
- **`client.js`: guard 38 unguarded `console.log` calls ([#761](https://github.com/djust-org/djust/issues/761))** — Per `djust/CLAUDE.md` rule, no `console.log` without `if (globalThis.djustDebug)` guard. Introduced a `djLog` helper in `00-namespace.js` and replaced bare `console.log` → `djLog` across 12 client modules. `console.warn`/`console.error` untouched (real problems stay visible in prod).
- **Observability `DryRunContext._uninstall` logs setattr failures ([#759](https://github.com/djust-org/djust/issues/759))** — Silent `except Exception: pass` meant the process could run indefinitely with a wrapped `Model.save` if uninstall partially failed — catastrophic for a dev server. Replaced with a `logger.warning` so the failure is observable.

### Changed

- **`djust.observability` + eval_handler v2** — Side-effect blocking now covers QuerySet bulk writes ([#758](https://github.com/djust-org/djust/issues/758)): `QuerySet.update`/`delete`/`bulk_create`/`bulk_update` are patched alongside `Model.save`/`delete`, so a handler that does `Model.objects.filter(...).update(...)` correctly raises `DryRunViolation` instead of silently committing.
- **Observability dry_run tests tightened ([#760](https://github.com/djust-org/djust/issues/760))** — Two tests claimed to verify the record-but-allow contract but only checked detection. Now use `unittest.mock` to assert the original callable was actually invoked (`call_count == 1`) alongside the violation-recorded assertion.

## [0.4.5rc1] - 2026-04-17

### Changed

- **Text-region fast path now fires for `{% extends %}` templates** — The scanner that builds the VDOM text-node position index used to process the full pre-hydration HTML, but the VDOM is rooted at `[dj-root]`. On templates extending a base (with `<title>`, meta tags, scripts outside dj-root), the scanner counted text runs in `<head>` and trailing `<footer>`/`<script>` siblings that the VDOM didn't have — the count mismatched, the index was discarded, and every event fell through to a full html5ever parse (~10ms on the djust.org /examples/ page). Now the scanner is restricted to the dj-root element's interior via a balanced-tag walker. Rust render drops from ~14ms → ~2.8ms on extends templates; browser E2E (production, DEBUG=False) drops from 30ms → ~25ms avg, 18ms min.

- **Text-region VDOM fast path** — Extends the existing text-fast-path to handle changes that differ only in a text span, even when the surrounding fragment contains tags. Computes byte-level common prefix/suffix on pre-hydration HTML; if the divergence is a single tag-free text run, locates the owning VDOM text node via a pre-built positional index (binary search on `(html_start, html_end, path, text, djust_id)` entries, built once per full-parse render and kept in sync through fast-path events by shifting downstream entries by the byte delta). Patches in place and skips html5ever entirely. For a counter click inside a `{% for %}` loop on a 309KB page, Rust render drops from ~12ms to ~2.7ms. UTF-8 safe (snaps to char boundaries), handles `<pre>`/`<code>`/`<textarea>` whitespace preservation and `<script>`/`<style>` raw-text element bodies correctly, bails to full parse on entity-offset mismatches.

- **`parse_html_fragment(html, context_tag)`** — New public entry point in `djust_vdom` that uses html5ever's `parse_fragment` with a parent-element context. Enables parsing isolated HTML fragments with correct tokenization for context-sensitive elements (`<tr>`, `<td>`, `<option>`), without resetting the dj-id counter. Scaffolding for future structural-fragment fast paths.

- **`collect_vdom_text_nodes` now skips comment nodes** — Previously collected `<!--dj-if-->` placeholders into the text-node list, shifting every subsequent ordinal by one and breaking any position-based patching. Text and comment VNodes both carry `text`, so an explicit `is_text()` filter was needed.

- **Partial template rendering ([#737](https://github.com/djust-org/djust/issues/737))** — Per-node dependency tracking at template parse time. On re-render, only template nodes whose context variable dependencies changed are re-rendered; unchanged nodes reuse cached HTML. For a single-variable change on a page with 50 template nodes, template render drops from ~1.4ms to ~0.1ms. Changed keys are passed from Python to Rust via `set_changed_keys()`, which merges across multiple sync calls. `{% include %}` and custom tags always re-render (wildcard dependency).

- **`{% extends %}` inheritance resolution caching** — Templates using `{% extends %}` now participate in partial rendering. Inheritance is resolved once via `OnceLock<ResolvedInheritance>` on the `Template` struct (shared via `TEMPLATE_CACHE`). Final merged nodes and their deps are cached, so subsequent renders skip both chain building and static parent nodes. Combined with partial rendering, extends templates go from full re-render (~14ms Rust) to partial render of changed nodes only (~0.02ms Rust).

- **Text-only VDOM fast path** — When all changed template fragments are plain text (no HTML tags), skip both html5ever parsing and VDOM diffing entirely. The old VDOM is mutated in-place via a fragment→text-node map built on first render, and SetText patches are produced directly. For counter-style updates: parse phase drops from ~12ms to ~0.001ms.

- **Block flattening for partial rendering** — `{% block %}` nodes left by Django's template engine are flattened to expose each child as a separate fragment. This enables the text fast path to activate on pages using `{% extends %}` where Django resolves blocks.

- **Faster change detection** — `_snapshot_assigns` uses identity + shallow fingerprints (id, length, content hash for list-of-dicts) instead of `copy.deepcopy`. Framework-internal keys (`csrf_token`, `kwargs`, `temporary_assigns`, `DATE_FORMAT`, `TIME_FORMAT`) and auto-generated `_count` keys are excluded from `set_changed_keys` to avoid spurious re-renders.

- **Optimized VNode parser** — Pre-sized attribute HashMap, eliminated redundant `to_lowercase()` call, removed form element debug output.

### Fixed

- **Derived immutable context values no longer go stale on partial re-render** — `_sync_state_to_rust` previously skipped id()-based change detection for immutable types (int/str/bool/bytes) to avoid false positives from Python's int cache, which meant derived values computed in `get_context_data` (e.g. `completed_count = sum(...)`, `total_count = len(...)`) were never synced to Rust when their sources changed. Partial rendering would then reuse the cached HTML for template nodes depending on those values, leaving counters stale after add/toggle/delete. Fixed by tracking previous VALUES for immutable keys and comparing by equality. Regression tests in `test_changed_tracking.py::TestDerivedImmutableSync`.

- **VDOM input value leak on name change** — When the patcher morphs an input into a different field (e.g., wizard step 1 name → step 2 email), the old field's typed value no longer leaks into the new field. Both `morphElement` and `SetAttr` patches now clear `.value` when the `name` attribute changes.

- **In-place dict mutation detection** — `_snapshot_assigns` now fingerprints list contents (id + dict values hash) to detect mutations like `todo['completed'] = True` that don't change the list's id or length. Falls back to id-only for unhashable values.

- **Derived context value detection** — When `_changed_keys` is set, the sync also checks non-immutable context values by id() to catch derived values (e.g., `products` from `_products_cache`) that change via private attributes.

## [0.4.4] - 2026-04-15

### Changed

- **Remove double `updateHooks()`/`bindModelElements()` scanning** — These were called in both `applyPatches()` and `reinitAfterDOMUpdate()`, scanning the full DOM twice per patch cycle. Removed from `applyPatches()`. Saves ~5ms per event.

- **Delegated scoped listeners (dj-window-*, dj-document-*)** — Replaced `querySelectorAll('*')` full DOM scan with a registry-based delegation pattern. Scoped elements are scanned once at mount time and registered in a Map. Event listeners on window/document dispatch to the registry. Handles dotted attribute variants (dj-window-keydown.escape).

- **Use `orjson.loads()` for patch JSON parsing** — 2-3x faster than stdlib `json.loads()` when orjson is installed. Falls back gracefully.

- **Gate debug payload behind panel open state** — `get_debug_update()` (dir + getattr + json.dumps per attribute) only runs when the debug panel is actually open, not on every event in DEBUG mode. Saves ~2-5ms per event. Panel sends `debug_panel_open`/`debug_panel_close` WS messages on toggle.

## [0.4.4rc1] - 2026-04-15

### Fixed

- **VDOM patch path traversal skips regular HTML comments ([#729](https://github.com/djust-org/djust/issues/729))** — The JS patcher was counting all HTML comment nodes during path traversal, but the Rust VDOM parser only preserves `<!--dj-if-->` placeholders. This caused every page with HTML comments in `dj-root` to fail VDOM patching and fall back to full HTML recovery.

- **Scroll to top on `dj-navigate` live_redirect** — `handleLiveRedirect()` now scrolls to the top of the page (or to anchor if URL has a hash) after `pushState`.

### Changed

- **Event delegation replaces per-element binding ([#730](https://github.com/djust-org/djust/issues/730))** — `bindLiveViewEvents()` no longer scans the DOM after every VDOM patch. Instead, one listener per event type is installed on the `dj-root` element via delegation (`e.target.closest('[dj-click]')`). This reduces client-side post-patch handling from ~56ms to ~30ms on large pages. Per-element rate limiting preserved via WeakMap.

### Added

- **Per-phase Rust timing in `render_with_diff()` ([#730](https://github.com/djust-org/djust/issues/730))** — Instrumentation measuring template render, html5ever parse, VDOM diff, and HTML serialization. Exposed to Python via `get_render_timing()` and propagated to WebSocket response performance metadata.

## [0.4.3] - 2026-04-14

### Fixed

- **`{% csrf_token %}` no longer renders poisoned `CSRF_TOKEN_NOT_PROVIDED` placeholder ([#696](https://github.com/djust-org/djust/issues/696))** — The Rust template engine now renders an empty string when no CSRF token is in context (instead of a placeholder that poisoned client.js's CSRF lookup). Python LiveView `_sync_state_to_rust()` now injects the real token from `get_token(request)`. Three-layer defense-in-depth fix merged as PR #708.

- **HTTP fallback POST no longer replaces page with logged-out render ([#705](https://github.com/djust-org/djust/issues/705))** — The POST handler now applies `_apply_context_processors()` before `render_with_diff()` so auth context (user, perms, messages) is available during re-render. Context processor cleanup uses `_processor_context()` context manager for guaranteed cleanup. Merged as PR #710 + #714 + #721.

- **Rust `|date` and `|time` filters honor Django `DATE_FORMAT`/`TIME_FORMAT` settings ([#713](https://github.com/djust-org/djust/issues/713))** — New `apply_filter_with_context()` checks the template context for format settings when no explicit format argument is given. Python injects Django settings into the Rust context during `_sync_state_to_rust()`. Merged as PR #714.

- **Rust `|date` filter now works on `DateField` values ([#719](https://github.com/djust-org/djust/issues/719))** — The `|date` filter previously only parsed RFC 3339 datetime strings. `DateField` values (bare dates like "2026-03-15") are now parsed via a `NaiveDate` fallback pinned to midnight UTC. Merged as PR #720.

- **CSRF token value HTML-escaped in Rust renderer ([#722](https://github.com/djust-org/djust/issues/722))** — The CSRF hidden input now uses the shared `filters::html_escape()` utility (escaping &, ", <, >, and single quotes) instead of a manual `.replace()` chain that missed single quotes. Defense-in-depth. Merged as PR #727.

- **Bare `except: pass` in CSRF injection now logs a warning ([#716](https://github.com/djust-org/djust/issues/716))** — The CSRF token injection in `_sync_state_to_rust()` previously swallowed all exceptions silently. Now logs via `djust.rust_bridge` logger with `exc_info=True`. Merged as PR #721.

### Changed

- **Context processor cleanup refactored to `_processor_context()` context manager ([#717](https://github.com/djust-org/djust/issues/717))** — Replaced the manual try/finally in the HTTP fallback POST handler with a reusable `@contextmanager` that guarantees cleanup of temporarily injected view attributes. Merged as PR #721 + #727.

- **Pre-existing test fixes** — `test_debug_state_sizes` corrected for `json.dumps(default=str)` behavior and `\uXXXX` escaping. `navigation.test.js` suppresses happy-dom/undici WebSocket mock `dispatchEvent` incompatibility.

### Added

- **Python integration tests for DATE_FORMAT settings injection ([#718](https://github.com/djust-org/djust/issues/718))** — 4 tests verifying `_sync_state_to_rust` injects DATE_FORMAT/TIME_FORMAT from Django settings. Merged as PR #721.

- **Negative tests for `|date` filter invalid input ([#725](https://github.com/djust-org/djust/issues/725))** — 4 Rust tests covering invalid dates, non-date strings, empty strings, and partial dates (filter returns original value per Django convention). Merged as PR #727.

- **`format_date()` doc comment documenting Django compatibility ([#726](https://github.com/djust-org/djust/issues/726))** — Documents supported input formats (RFC 3339, YYYY-MM-DD) and unsupported types (epoch ints, locale strings). Merged as PR #727.

## [0.4.2] - 2026-04-13

### Fixed

- **Derived context vars synced when parent instance attr mutated in-place ([#703](https://github.com/djust-org/djust/issues/703))** — `_sync_state_to_rust()` now collects `id()`s of all sub-objects reachable from changed instance attrs and includes any derived context var whose `id()` appears in that set. Previously, context vars computed in `get_context_data()` that returned sub-objects of a mutated dict (e.g., `wizard_step_data.get("person", {})`) were skipped because their `id()` was unchanged, causing templates to render stale data. Depth-capped at 8 with cycle detection. 9 new regression tests.

- **`as_live_field()` now respects `widget.input_type` override for `type` attribute ([#683](https://github.com/djust-org/djust/issues/683) re-open)** — The initial #683 fix merged `widget.attrs` but `type` was still ignored because Django moves `type=` from `attrs` into `widget.input_type` during widget `__init__`. `_get_field_type()` now checks `widget.input_type` against the widget class's default and uses the override when they differ (e.g. `TextInput(attrs={"type": "tel"})` sets `input_type="tel"`). 4 new regression tests covering `type="tel"`, `type="url"`, `type="search"`, and the default `type="text"` fallback.

### Added

- **LiveComponent events now propagate to parent LiveView waiters ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b/1c follow-up)** — Closes the "known limitation" documented in the v0.4.2 tutorials guide: `await self.wait_for_event("foo")` on a LiveView now resolves when the matching handler fires on an embedded `LiveComponent`, not just when it fires on the view itself. Without this, a `TutorialStep(wait_for="submit", ...)` where `submit` is a handler on a child `FormComponent` would silently stall forever — the parent view's waiter would never resolve and the tour would hang. The fix is in the WebSocket consumer's `handle_event` component-event branch: after the component handler runs, the consumer now calls `self.view_instance._notify_waiters(event_name, notify_kwargs)` with the handler's kwargs + an injected `component_id` key, mirroring the notification that already happened in the main LiveView branch from Phase 1b. The `component_id` injection means apps can use the waiter's `predicate` argument to disambiguate events fired from multiple component instances: `wait_for_event("submit", predicate=lambda kw: kw.get("component_id") == "project_form")`. A notification failure is caught and logged via the `djust.websocket` logger so a buggy waiter/predicate can't break the component handler's observable behavior — the component's state mutations always happen even if the waiter notification raises. 5 new regression tests in `python/tests/test_waiter_component_propagation.py` covering: component event resolves parent waiter, `component_id` is injected into notify kwargs so predicates can filter by source, multiple parent waiters for the same event all resolve (fan-out), the non-component branch still notifies parent waiters (regression guard for the Phase 1b path), and a raising `_notify_waiters` is logged-and-swallowed rather than propagating. `docs/website/guides/tutorials.md` Limitations section updated to document the new behavior with a `component_id` predicate example.

### Documentation

- **Tutorial bubble must be placed outside `dj-root` ([#699](https://github.com/djust-org/djust/issues/699))** — If the `{% tutorial_bubble %}` tag is placed inside the `dj-root` container, morphdom recovery (which replaces the entire `dj-root` content on patch failure) destroys the bubble mid-tour, causing it to silently disappear. The tutorials guide now has a dedicated "Bubble Placement" section explaining the requirement, why it exists, and correct/incorrect examples. The simplest-possible example at the top of the guide is updated to show the bubble outside `dj-root`. The `tutorial_bubble` template tag docstring is also updated with this requirement.

- **`data-*` attribute naming convention documented in Events guide ([#623](https://github.com/djust-org/djust/issues/623))** — How `data-foo-bar` on an HTML element maps to `foo_bar` in the event handler's kwargs was undocumented. The Events guide now has a dedicated "Data Attribute Naming Convention" section covering: the dash-to-underscore rule, client-side type-hint suffixes (`:int`, `:float`, `:bool`, `:json`, `:list`), server-side Python type-hint coercion, the `dj-value-*` alternative, which internal `data-*` attributes are excluded, and a quick-reference table.

### Changed

- **System checks T002, V008, C003 now suppressible via `DJUST_CONFIG` ([#603](https://github.com/djust-org/djust/issues/603))** — These three informational checks fire on every `manage.py` invocation and are noisy for projects that deliberately don't use the checked features (daphne, explicit `dj-root`, non-primitive mount state). A new `suppress_checks` config key in `DJUST_CONFIG` (or `LIVEVIEW_CONFIG`) accepts a list of check IDs to silence: `DJUST_CONFIG = {"suppress_checks": ["T002", "V008", "C003"]}`. Both short IDs (`"T002"`) and fully-qualified IDs (`"djust.T002"`) are accepted, case-insensitive. Only the informational/advisory variants are suppressed — the C003 *Warning* (daphne misordered) still fires because it indicates a real misconfiguration. 7 new tests for the suppression mechanism.

- **`release-drafter/release-drafter` v6 → v7 + drop `pull_request` trigger** — v7 validates `target_commitish` against the GitHub releases API and rejects `refs/pull/<n>/merge` refs, which is what `github.ref` resolves to under a `pull_request` trigger. v6 silently tolerated this; v7 does not, causing every PR to fail with `Validation Failed: target_commitish invalid`. The fix is to drop the `pull_request` trigger — release-drafter is designed to track changes that have *landed* on the release branch, not comment on in-flight PRs, so `push: branches: [main]` is the right fit. Aligns with how Phoenix, Elixir, GitHub CLI, and other major projects wire release-drafter. Resolves the v7 bump that was deferred out of the v0.4.2 dependabot batch (#680).


- **Dependency batch carry-over (v0.4.2)** — Drains the dependabot backlog that was held behind the v0.4.1 release. Single consolidated PR so one CI run catches any inter-dep interactions:
  - **npm**: `vitest` / `@vitest/ui` / `@vitest/coverage-v8` 4.0.18 → 4.1.4 (patches + new test runner features), `jsdom` 29.0.1 → 29.0.2, `happy-dom` 20.8.4 → 20.8.9. Full JS suite remains green (1111 tests).
  - **Cargo**: `tokio` 1.50 → 1.51 (workspace), `uuid` 1.22 → 1.23, `proptest` 1.10 → 1.11 (djust_vdom), `indexmap` 2.13.0 → 2.14.0 (transitive pickup via cargo update). `cargo check --workspace` clean; `cargo test -p djust_vdom` passes all 42 proptest-driven tests on the new 1.11 runtime.
  - **GitHub Actions**: `actions/github-script` v8 → v9 (two workflows), `astral-sh/setup-uv` v6 → v7 (test workflow). Workflow syntax unchanged.
  - **Intentionally deferred**: `html5ever` 0.36 → 0.39 is a 3-minor-version jump that requires a matching `markup5ever_rcdom` 0.39 release which has not yet been published to crates.io (only git snapshots exist in the html5ever workspace). Using git deps in our published workspace would break `cargo publish` and leak unreleased upstream state, so this stays deferred until upstream publishes. `release-drafter/release-drafter` v6 → v7 was also deferred out of this chore batch because of a `target_commitish` validation incompatibility — shipped as a separate follow-up PR alongside this one.

  Closes 13 open dependabot PRs as superseded (#581, #582, #604, #606, #607, #609, #615, #616, #644, #645, #646, #647, #648).

### Fixed

- **`@background` natively supports `async def` handlers ([#697](https://github.com/djust-org/djust/issues/697))** — The `@background` decorator now detects `asyncio.iscoroutinefunction` and creates a native async closure so `_run_async_work` can `await` it directly on the event loop instead of routing through `sync_to_async`. The fragile `inspect.iscoroutine(result)` workaround from #692 is kept as a legacy fallback. 5 new regression tests.

- **`flush_push_events()` resolves callback dynamically on WS reconnect ([#698](https://github.com/djust-org/djust/issues/698))** — `PushEventMixin.flush_push_events()` now resolves the flush callback via `self._ws_consumer._flush_push_events` at call time instead of relying on a stored `_push_events_flush_callback` that was only wired during initial mount. After a WebSocket reconnect the view instance is restored from session but the stored callback was stale. The dynamic lookup always finds the current consumer. Legacy stored callback kept as fallback. 7 new tests.

- **push_commands-only handlers auto-skip VDOM re-render ([#700](https://github.com/djust-org/djust/issues/700))** — Handlers that only call `push_commands()` / `push_event()` without changing public state no longer trigger a VDOM re-render. The `_snapshot_assigns` deep-copy comparison could report false positives for views with non-copyable public attributes (querysets, file handles) because sentinel objects never compare equal. A new identity-based check (`id()` comparison before/after) detects whether any public attribute was actually rebound and auto-sets `_skip_render = True` when push events are pending but no state changed. 5 new tests.


- **System check V010 detects wrong TutorialMixin MRO ordering at startup ([#691](https://github.com/djust-org/djust/issues/691))** — Django's `View.__init__` does not call `super().__init__()`, so writing `class MyView(LiveView, TutorialMixin)` silently skips TutorialMixin's initialisation. A new `djust.V010` system check scans all LiveView subclasses at startup and emits an Error with a clear fix hint when TutorialMixin appears after a View-derived base in the class declaration. Suppressible via `DJUST_CONFIG = {"suppress_checks": ["V010"]}`. 5 new tests. Tutorials guide updated with correct ordering.

- **`@background async def` handlers now execute correctly ([#692](https://github.com/djust-org/djust/issues/692))** — `@background` wraps handlers in a sync closure; when the handler is `async def`, the closure returned an unawaited coroutine and the handler body never ran. The fix in `_run_async_work` (already on main via workaround) detects coroutine returns and awaits them. 11 new regression tests in `test_background_async.py` verify both sync and async handlers execute their bodies.

- **`push_commands` in `@background` tasks now flush mid-execution ([#693](https://github.com/djust-org/djust/issues/693))** — Push events queued by `push_commands` inside a `@background` handler only reached the client when the entire task completed. The `_flush_pending_push_events` callback mechanism (already on main) lets TutorialMixin and other background handlers flush events immediately. A new public `await self.flush_push_events()` method on PushEventMixin provides the same capability to any `@background` handler. 7 new tests in `test_push_flush_background.py`.

- **`get_context_data` no longer includes non-serializable class attributes ([#694](https://github.com/djust-org/djust/issues/694))** — The MRO walker in `ContextMixin.get_context_data()` added class-level attributes (like `tutorial_steps`) to the template context. Non-JSON-serializable values were silently converted to their `str()` repr, corrupting state on subsequent events. The fix skips class-level attributes that fail a JSON serialisability probe. Additionally, `TutorialMixin` now stores steps as `_tutorial_steps` (private) with a read-only `tutorial_steps` property, so they are excluded by both the `_` prefix convention and the serialisability check. 14 new tests.

- **Debug panel SVG attributes no longer double-escaped ([#613](https://github.com/djust-org/djust/issues/613))** — SVG attributes like `viewBox` and `path d` in the debug toolbar were rendered garbled because the Rust VDOM's `to_html()` method HTML-escaped text content inside `<script>` and `<style>` elements. Per the HTML spec, these are "raw text elements" whose content must be emitted verbatim — escaping `&` to `&amp;` or `<` to `&lt;` corrupts JavaScript/CSS code and causes double-escaping when the HTML is round-tripped through the VDOM pipeline (parse with html5ever which decodes entities, then re-serialize with `to_html()` which re-encodes them). The fix adds an `in_raw_text` flag to the internal `_to_html()` serializer that propagates through `<script>`/`<style>` children, skipping `html_escape()` for their text nodes. SVG attribute values in templates (which don't contain HTML special characters) were already correct but now have explicit regression tests. 4 new Rust unit tests, 3 new Rust integration tests (script/style/SVG roundtrip), 3 new Python regression tests (JS source validation, JSON injection check, VDOM roundtrip), and 3 new JS tests (tab icon SVGs, path d attributes, header button SVGs all verified in DOM).

- **`form.cleaned_data` Python types no longer serialize to null ([#628](https://github.com/djust-org/djust/issues/628))** — `datetime.date`, `datetime.datetime`, `datetime.time`, `Decimal`, and `UUID` values in `form.cleaned_data` stored in public view state are now properly serialized to their JSON representations (ISO strings, floats, strings) instead of silently becoming `null`. Both the `DjangoJSONEncoder` and `normalize_django_value()` already handled these types; 10 new regression tests confirm the behavior.

- **`set()` is now JSON-serializable as public state ([#626](https://github.com/djust-org/djust/issues/626))** — Storing a Python `set()` or `frozenset()` in public view state no longer crashes `json.dumps`. Sets are serialized as sorted lists (falling back to unsorted when elements aren't comparable). Both `DjangoJSONEncoder.default()` and `normalize_django_value()` now handle `set`/`frozenset`. 11 new regression tests.

- **`dict` state no longer corrupted to `list` after Rust state sync ([#612](https://github.com/djust-org/djust/issues/612))** — Round-tripping state through the Rust MessagePack serialization boundary could corrupt `dict` values into `list` because `#[serde(untagged)]` on the `Value` enum let `rmp_serde` match a msgpack map against the `List` variant before trying `Object`. The fix replaces the derived `Deserialize` with a custom visitor-based implementation that uses the deserializer's type hints (`visit_map` vs `visit_seq`) to correctly distinguish maps from arrays. 4 new Rust regression tests + 1 Python end-to-end msgpack round-trip test.

- **`as_live_field()` now merges `widget.attrs` into rendered HTML ([#683](https://github.com/djust-org/djust/issues/683))** — The `as_live_field()` method (and `{% live_field %}` tag) dropped any attributes defined on a Django widget's `attrs` dict — `type="email"`, `placeholder`, `pattern`, `min`/`max`, custom `data-*`, and any other HTML attributes were silently lost. The fix adds `_merge_widget_attrs()` to `BaseAdapter`, called from `_render_input`, `_render_checkbox`, and `_render_radio`, which merges `field.widget.attrs` into the output attributes with djust-specific keys (`dj-change`, `name`, `class`, etc.) taking precedence over widget defaults. Boolean `False`/`None` values in widget attrs are filtered out to avoid rendering `disabled="False"`. 17 new regression tests in `python/tests/test_live_field_widget_attrs.py` covering: EmailInput placeholder/type, pattern/min/max/step/title, djust attrs override clashing widget attrs, empty widget attrs, textarea rows/cols, checkbox data-attrs, radio data-attrs on each option, select data-attrs, and boolean True/False handling.

- **VDOM patcher guards against text nodes for 5 patch types ([#622](https://github.com/djust-org/djust/issues/622))** — The VDOM diff patcher called `setAttribute()`, `removeAttribute()`, `appendChild()`, `removeChild()`, and `replaceChild()` on `#text` nodes, which don't implement these methods. This crashed conditional rendering whenever a text node sat where the patcher expected an element (common in `{% if %}` blocks that switch between text and element content). The fix adds an `isElement(node)` guard at the top of each of the five patch-type branches in `12-vdom-patch.js` — when the target is a non-element node (text, comment, CDATA), the patch is skipped gracefully instead of throwing. 4 new JS tests in `tests/js/vdom_patch_errors.test.js` covering setAttribute, removeAttribute, appendChild, and replaceChild on text nodes.

- **Autofocus handling on dynamically inserted elements ([#617](https://github.com/djust-org/djust/issues/617))** — Dynamically inserted `<input autofocus>` elements didn't receive focus after a VDOM patch because the browser only honours the `autofocus` attribute on initial page load. The patcher now detects `autofocus` on newly inserted elements after each patch cycle and calls `.focus()` explicitly. 4 new JS tests in `tests/js/vdom-autofocus.test.js` covering single autofocus, multiple elements (last wins), elements without autofocus ignored, and no-op when no autofocus elements are present.

- **Private `_` attributes preserved across events and reconnects ([#627](https://github.com/djust-org/djust/issues/627), [#611](https://github.com/djust-org/djust/issues/611))** — Two related state-management bugs caused any attribute starting with `_` (the documented convention for private/internal state) to be silently wiped. The root cause was that session save used the output of `get_context_data()`, which by design strips `_`-prefixed attributes. For #627, every WebSocket event round-trip lost private state because `_save_state_to_session()` persisted only public context. For #611, the pre-rendered WS reconnect path restored session state but never included private attributes set during the HTTP GET mount. The fix adds two helpers — `_get_private_state()` (collects all `_`-prefixed instance attrs that aren't dunder or in the base-class exclusion set) and `_restore_private_state(state_dict)` — and wires them into `_save_state_to_session()` (now persists private state under a `_private_state` session key) and `_load_state_from_session()` / the reconnect path in `RequestMixin._restore_session_state()` (restores private attrs before the view resumes). 20 new regression tests in `python/tests/test_private_attr_preservation.py` covering: private attrs survive event dispatch, survive reconnect, survive multiple sequential events, coexist with public attrs, handle None/complex/nested values, are excluded for dunder attrs, are excluded for base-class internals, and round-trip through session save/load.

- **Layout flash on pre-rendered mount: defer `reinitAfterDOMUpdate` via `requestAnimationFrame` ([#619](https://github.com/djust-org/djust/pull/619), fixes [#618](https://github.com/djust-org/djust/issues/618))** — Carry-over bugfix from v0.4.1. When a page is pre-rendered via HTTP GET, the WebSocket mount used to call `reinitAfterDOMUpdate()` synchronously right after stamping `dj-id` attributes onto the existing DOM. That synchronous call triggered a full DOM traversal for event binding, which forced the browser to recalculate layout mid-paint — and on pages with large pre-rendered elements (e.g. big dashboard stat values) the elements briefly rendered at the wrong size before settling, producing a visible layout-flash on every initial load. The fix moves the post-mount block (reinit + `_mountReady` flag + form recovery + auto-recover) into a `runPostMount` closure and schedules it via `requestAnimationFrame(runPostMount)` when available, falling back to a synchronous call when `requestAnimationFrame` is unavailable (JSDOM tests, exotic non-browser environments). Event binding now happens *after* the browser finishes its current paint, eliminating the flash entirely. The ordering invariant (reinit → `_mountReady` → form recovery) is preserved inside the closure so `dj-mounted` handlers and recovered form inputs still see bound event listeners. The non-prerendered `data.html` innerHTML-replace branch is unchanged — it already invalidates layout via the full DOM swap so there's no pre-paint to protect. 8 new regression tests in `tests/js/mount-deferred-reinit.test.js` asserting: the rAF wrapper is present, the synchronous fallback is preserved, the closure is named `runPostMount` for stable debugging, `reinitAfterDOMUpdate()` runs before `_mountReady` inside the closure, `_mountReady` is set inside the closure (not synchronously), form recovery runs only on reconnect inside the closure, the non-prerendered branch calls reinit synchronously, and exactly one call-site of `reinitAfterDOMUpdate()` exists in the skipMountHtml branch (so a refactor that reintroduces the sync call would immediately flip red). Closes #619 as superseded and closes the original #618 bug report.

- **Scaffolded projects now default `DEBUG=False` and generate `.env.example` ([#637](https://github.com/djust-org/djust/issues/637))** — Carry-over bugfix from v0.4.1. Previously, `python -m djust startproject mysite` and `python -m djust new mysite` both generated a `settings.py` with `DEBUG = True` and `ALLOWED_HOSTS = ["*"]` as hardcoded literals. A developer who deployed the scaffolded output without remembering to flip those values ran production with full stack traces, the `django-insecure-<random>` default SECRET_KEY, and a wildcard host allowlist — the exact footgun that A001 (`DEBUG` enabled) and A014 (`ALLOWED_HOSTS` too permissive) flag in `djust_audit`. Now both scaffold paths (`cli.py`'s `cmd_startproject` and the higher-level `djust.scaffolding.generator.generate_project`) emit `DEBUG = os.environ.get("DEBUG", "False").lower() in ("true", "1", "yes")` and `ALLOWED_HOSTS = [host.strip() for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host.strip()]` — unconfigured deployments fail safe. The scaffold also writes a `.env.example` template alongside `.gitignore` (which already ignores `.env`) so local development picks up developer-friendly values via `cp .env.example .env` + whatever `.env` loader the developer uses. The `.env.example` includes `DEBUG=True`, a freshly-generated `SECRET_KEY` token (via `secrets.token_urlsafe(50)`), and `ALLOWED_HOSTS=localhost,127.0.0.1` so the local experience hasn't changed. 4 new regression tests in `python/tests/test_cli_scaffold.py` asserting: `DEBUG = True` is no longer literal, `DEBUG` reads from env with `"False"` fallback, `ALLOWED_HOSTS = ["*"]` is no longer literal, narrow `localhost,127.0.0.1` env default, `.env.example` exists with the three documented vars and a real (not template-placeholder) secret key, `.env` remains in `.gitignore` while `.env.example` does not. Closes #637.

### Added

- **`TutorialMixin` + `TutorialStep` + `{% tutorial_bubble %}` — declarative guided tours ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c)** — Capstone of ADR-002 Phase 1: a one-import, zero-JavaScript way for any djust app to ship a real guided tour, onboarding flow, or wizard. Apps declare the tour as a list of `TutorialStep` dataclasses on a `LiveView` that mixes in `TutorialMixin`; the framework runs the state machine as a `@background` task, pushing a highlight + narrate + focus chain at each step's target via `push_commands` (Phase 1a), then either `asyncio.sleep`'ing for auto-advance steps or `await`ing `wait_for_event` (Phase 1b) until the user actually fires the matching `@event_handler`. Four event handlers come for free — `start_tutorial`, `skip_tutorial`, `cancel_tutorial`, `restart_tutorial` — along with three instance attributes (`tutorial_running`, `tutorial_current_step`, `tutorial_total_steps`) for progress display in the view state. `TutorialStep` supports per-step `target` (CSS selector, required), `message` (narration text), `position` (`top`/`bottom`/`left`/`right` bubble hint), `wait_for` (handler name to suspend on), `timeout` (seconds — pairs with `wait_for` for bounded waits or used alone for auto-advance), `on_enter`/`on_exit` (optional extra `JSChain` pushes for per-step setup/teardown beyond the default highlight + narrate + focus), and `highlight_class`/`narrate_event` (override per-step CSS class and CustomEvent name when you need different visual treatment). Skip and cancel signals are raced against the wait via `asyncio.wait(..., return_when=FIRST_COMPLETED)` so either unblocks the current step immediately; WebSocket disconnect cancels the background task automatically so there's no lingering work, no leaked waiters, no stuck highlights. A new `{% tutorial_bubble %}` template tag renders a floating narration bubble that listens for `tour:narrate` CustomEvents at `document` level (dispatched at the step's target with `bubbles: true`), positions itself next to the target per the step's `position` hint, displays `step N / total` progress, and includes "Skip" and "Close" buttons pre-bound to the mixin's event handlers — the default bubble is marked `dj-update="ignore"` so morphdom doesn't clobber it during VDOM patches. The new client-side `src/28-tutorial-bubble.js` module (~140 lines, brings `client.js` to 30 modules) registers its listeners unconditionally at IIFE time, reads `detail.text`/`target`/`position`/`step`/`total` from the event, and updates the bubble's text + progress + position + visibility. The framework ships no CSS — apps style the bubble and highlight class themselves (the guide includes a minimal starter block). 26 new Python tests for the mixin covering TutorialStep dataclass (minimal, custom position, invalid position, empty target, empty message, wait_for+timeout, on_enter/on_exit), lifecycle (initial state, empty-steps no-op, single step, setup+cleanup chain order, multi-step order, idempotent start-while-running), `wait_for_event` integration (step suspends on user action, timeout advances silently, indefinite wait), skip/cancel paths (advance past current, abort loop, no-op when not running), `on_enter`/`on_exit` pushes, per-step highlight class override, and per-step narrate event override. 9 new Python tests for the `tutorial_bubble` template tag covering defaults, custom `css_class`/`event`/`position`, invalid-position fallback to `"bottom"`, skip+cancel button bindings, text/progress element classes, and XSS escaping of hostile `css_class` and `event` kwargs. 12 new JS tests in `tests/js/tutorial-bubble.test.js` covering listener registration, text content updates, progress text updates, show/hide via `data-visible`, default/custom position application, missing-target graceful handling, missing-bubble graceful handling, `tour:hide` event, and repeated updates on subsequent events. Zero new runtime dependencies — stdlib `asyncio` + `dataclasses` + Django's `format_html`. Full documentation in the new `docs/website/guides/tutorials.md` guide with the simplest-possible example, state-machine description, `TutorialStep` reference, `wait_for`/`timeout` combinations table, `on_enter`/`on_exit` patterns, the bubble template tag docs, a starter CSS block, four usage patterns (auto-advance walkthrough, interactive onboarding, mixed, branching with custom handlers), skip/cancel UX, disconnect cleanup, debugging tips, and honest limitations (LiveComponent events don't propagate to parent waiters yet, actor-mode views bypass the dispatch hook, handler validation failures prevent the waiter from resolving except via timeout, single-user only — multi-user broadcast is Phase 4 in v0.5.x).

- **`await self.wait_for_event(name, timeout=None, predicate=None)` async primitive ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b)** — Second half of the backend-driven UI Phase 1 primitives. Adds a new `WaiterMixin` (automatically included in `LiveView`) that lets a `@background` handler suspend until a specific `@event_handler` is called by the user, optionally filtered by a predicate, optionally bounded by a timeout. The returned dict is the kwargs that were passed to the matching handler. This is the primitive that makes "highlight this button, wait for the user to actually click it, then advance to the next step" work declaratively — required by `TutorialMixin` (Phase 1c) and by any server-driven flow that needs to pause mid-plan until real user input arrives. Implementation: ~180 lines in `python/djust/mixins/waiters.py`, a ~15-line hook in `python/djust/websocket.py` that calls `_notify_waiters` after every successful handler invocation, a ~10-line cleanup hook in the WebSocket `disconnect` path that cancels all pending waiters when the view tears down (so `@background` tasks unblock with `CancelledError` instead of leaking), and proper integration into `LiveView`'s MRO via `python/djust/mixins/__init__.py`. The notify pass runs AFTER the handler completes so waiters created during a handler call aren't self-notified (prevents re-entrancy surprises where `wait_for_event("X")` inside an `X` handler would resolve against itself). Multiple concurrent waiters for the same event name all resolve with the same kwargs dict when that event fires — fan-out patterns work without manual coordination. Waiters for different event names are fully independent. A predicate that raises is treated as "no match" and logged via the `djust.waiters` logger, so a buggy predicate can't crash the event pipeline or deadlock a background task. 18 new Python tests covering: basic resolution, kwargs copy semantics, no-op on unmatched names, predicate filtering, predicate-that-raises treated as False with warning log, predicate=None matches any kwargs, timeout raises `asyncio.TimeoutError`, expired waiters removed from registry, indefinite waits without timeout, concurrent waiters for same event all resolve, waiters for different events are independent, partial resolution (some predicates match, others don't), `_cancel_all_waiters` unblocks pending futures with `CancelledError` and clears the registry, task cancellation removes the waiter, and stability under mid-iteration waiter-list mutation. Full documentation in the existing `docs/website/guides/server-driven-ui.md` guide with signature, predicate examples, concurrency semantics, timeouts and cleanup, composition with `push_commands`, and honest limitations (no component-event support yet, actor mode bypasses the hook, validation failures prevent handler execution which means waiters never resolve except via timeout).

- **`LiveView.push_commands(chain)` + `djust:exec` client-side auto-executor ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a)** — First half of the backend-driven UI primitives proposed in ADR-002. Adds a one-line server-side helper `self.push_commands(chain)` that takes a `djust.js.JSChain` (shipped in v0.4.1 as the JS Commands fluent API) and pushes it to the current session as a `djust:exec` push event carrying the chain's JSON-serialized `ops` list. The client half is a new framework-provided `src/27-exec-listener.js` module that listens for `djust:push_event` CustomEvents on `window`, filters for `event === 'djust:exec'`, and runs the ops via `window.djust.js._executeOps(ops, document.body)` — the same function that runs inline `dj-click="[[...]]"` JSON chains and fluent-API `.exec()` calls from hook code. No hook registration, no template markup, no user setup required: the auto-executor ships bound with `client.js` and is active on every djust page automatically. The server-side helper is type-safe — it rejects anything that isn't a `JSChain` with a clear `TypeError` pointing at the `JS.*` factory methods, preventing raw ops-list smuggling through the `push_event` path. `push_commands` and `push_event` share the same queue and preserve ordering, so handlers can interleave "push a flash message, add a CSS class, fire analytics, run an animation" in one deterministic sequence. 23 new Python tests covering single-op chains, multi-op ordering, empty chains, JSON round-trip, immutability of chains after push, type validation against strings/dicts/lists/None, queue composition with `push_event`, and per-op factory parity across all 11 JS Commands. 13 new JS tests in `tests/js/exec-listener.test.js` covering listener registration, single-op execution, multi-op ordering, multiple-class `add_class`, `focus`, `dispatch` with detail, filtering for non-`djust:exec` events, malformed-payload rejection (missing `ops`, non-array `ops`, missing detail), error resilience (one bad op doesn't break the chain), multiple independent exec fires, and end-to-end integration with the fluent `window.djust.js` chain factory. Zero new runtime dependencies. Full documentation in `docs/website/guides/server-driven-ui.md` with patterns, debugging tips, and pointers to Phase 1b (`wait_for_event`) and Phase 1c (`TutorialMixin`) still to come in v0.4.2.

## [0.4.1] - 2026-04-11

### Added

- **JS Commands — client-side DOM commands chainable from templates, views, hooks, and JavaScript** — Closes the single biggest DX gap vs Phoenix LiveView 1.0. Eleven commands (`show`, `hide`, `toggle`, `add_class`, `remove_class`, `transition`, `dispatch`, `focus`, `set_attr`, `remove_attr`, `push`) that run locally without a server round-trip, plus a `push` escape hatch that mixes in server events when needed. Four equivalent entry points: (1) **Python helper `djust.js.JS`** — fluent chain builder that stringifies to a JSON command list, wrapped in `SafeString` for safe template embedding (`<button dj-click="{{ JS.show('#modal').add_class('active', to='#overlay') }}">Open</button>`). (2) **Client-side `window.djust.js`** — mirror of the Python API with `camelCase` method names for direct JavaScript use (`window.djust.js.show('#modal').addClass('active', {to: '#overlay'}).exec()`). (3) **Hook API** — every `dj-hook` instance now has a `this.js()` method returning a chain bound to the hook element (Phoenix 1.0 parity for programmable JS Commands from hook lifecycle callbacks). (4) **Attribute dispatcher** — `dj-click` (and other event-binding attributes) detect whether the attribute value is a JSON command list (`[[...]]`) and execute it locally; plain handler names still fire server events as before (zero breaking changes). All commands support scoped targets: `to=<selector>` (absolute `document.querySelectorAll`), `inner=<selector>` (scoped to origin element's descendants), `closest=<selector>` (walk up the DOM from origin) — a single `<button dj-click="{{ JS.hide(closest='.modal') }}">Close</button>` works in every modal with no per-instance IDs. The `push` command accepts `page_loading=True` to show the navigation-level loading bar while the event round-trips. Chains are **immutable** — every chain method returns a new `JSChain`, so reusing a base chain across multiple call sites never cross-contaminates. **37 new Python tests** (every command + target validation + chain immutability + HTML/SafeString integration + template rendering) and **30 new JS tests** (every command executing against real DOM + target resolution + chain fluency + attribute dispatcher + backwards-compat for plain event names + `parseCommandValue` edge cases). Zero new dependencies — the Python helper is stdlib-only and the JS interpreter is ~350 lines in a new `src/26-js-commands.js` module. Full guide in `docs/website/guides/js-commands.md` with examples for templates, hooks, chaining, and the "when to reach for what" decision tree.

- **`dj-paste` — paste event handling** — New attribute that fires a server event when the user pastes content into a bound element (`<textarea dj-paste="handle_paste">`). The client extracts structured payload from the `ClipboardEvent` in one pass: `text` (`clipboardData.getData('text/plain')`), `html` (`getData('text/html')` for rich paste from Word/Google Docs/web pages), `has_files` (`bool`), and `files` (list of `{name, type, size}` metadata dicts for every file in `clipboardData.files`). When the element also carries a `dj-upload="<slot>"` attribute, the clipboard's `FileList` is routed through the existing upload pipeline — image-paste → chat, CSV-paste → table, etc. — via a new `window.djust.uploads.queueClipboardFiles(element, fileList)` export. Participates in the standard interaction pipeline (`dj-confirm`, `dj-lock`). By default the browser's native paste still happens so hybrid editors feel natural; add `dj-paste-suppress` to intercept fully (useful when routing image paste to an upload slot without dumping a data URL into a `<div contenteditable>`). Positional args in the attribute syntax (`dj-paste="handle_paste('chat', 42)"`) forward via `kwargs["_args"]`. 11 new JS tests covering text extraction, HTML extraction, file metadata, suppress flag, missing `clipboardData`, double-bind protection, positional args, upload routing with and without a `dj-upload` slot, and graceful degradation when `getData('text/html')` throws. ~80 lines JS. Full guide in `docs/website/guides/dj-paste.md`.

- **`djust_audit --ast` — AST security anti-pattern scanner ([#660](https://github.com/djust-org/djust/issues/660))** — Adds a new mode to `djust_audit` that walks the project's Python source and Django templates looking for five specific security anti-patterns, each motivated by a live vulnerability or near-miss in the 2026-04-10 NYC Claims penetration test. Seven stable finding codes `djust.X001`–`djust.X007`: **X001** (ERROR) — possible IDOR: `Model.objects.get(pk=...)` inside a DetailView / LiveView without a sibling `.filter(owner=request.user)` (or `user=`, `tenant=`, `organization=`, `team=`, `created_by=`, `author=`, `workspace=`) scoping the queryset. **X002** (WARN) — state-mutating `@event_handler` without any permission check (no class-level `login_required`/`permission_required`, no `@permission_required`/`@login_required`). **X003** (ERROR) — SQL string formatting: `.raw()` / `.extra()` / `cursor.execute()` passed an f-string, a `.format()` call, or a `"..." % ...` binary-op. **X004** (ERROR) — open redirect: `HttpResponseRedirect(request.GET[...])` / `redirect(...)` without an `url_has_allowed_host_and_scheme` or `is_safe_url` guard in the enclosing function. **X005** (ERROR) — unsafe `mark_safe` / `SafeString` wrapping an interpolated string (XSS risk). **X006** (WARN) — template uses `{{ var|safe }}` (regex scan of `.html` files). **X007** (WARN) — template uses `{% autoescape off %}`. Suppression via `# djust: noqa X001` on the offending line, or `{# djust: noqa X006 #}` inside templates. New CLI flags: `--ast`, `--ast-path <dir>`, `--ast-exclude <prefix> [...]`, `--ast-no-templates`. Supports `--json` and `--strict` (fail on warnings too). 52 new tests covering positive + negative cases for every checker, management-command integration, template scanning, and noqa suppression. Zero new runtime dependencies — stdlib `ast` + `re`. Full documentation in `docs/guides/djust-audit.md` and `docs/guides/error-codes.md#ast-anti-pattern-scanner-findings-x0xx`. Closes the v0.4.1 audit-enhancement batch (#657/#659/#660/#661 all shipped).

- **New consolidated `djust_audit` command guide** — `docs/guides/djust-audit.md` documents all five modes of the command (default introspection, `--permissions`, `--dump-permissions`, `--live`, `--ast`), every CLI flag, CI integration examples, and exit-code conventions. Cross-linked from `docs/guides/security.md`.

- **Error code reference expanded with 44 new codes** — `docs/guides/error-codes.md` now covers the A0xx static audit checks (7 codes: A001, A010, A011, A012, A014, A020, A030), the P0xx permissions-document findings (7 codes: P001–P007), and the L0xx runtime-probe findings (30 codes: L001–L091). Every code gets severity, cause, fix, and a reference to the related issue/PR.

- **`{% live_input %}` template tag — standalone state-bound form fields for non-Form views ([#650](https://github.com/djust-org/djust/issues/650))** — `FormMixin.as_live_field()` and `WizardMixin.as_live_field()` render form fields with proper CSS classes, `dj-input`/`dj-change` bindings, and framework-aware styling — but only for views backed by a Django `Form` class. This leaves non-form views (modals, inline panels, search boxes, settings pages, anywhere state lives directly on view attributes) without an equivalent helper. The new `{% live_input %}` tag fills this gap with a lightweight alternative that needs no `Form` class or `WizardMixin`. Supports 12 field types (`text`, `textarea`, `select`, `password`, `email`, `number`, `url`, `tel`, `search`, `hidden`, `checkbox`, `radio`), explicit `event=` override (defaults sensibly per type — `text` → `dj-input`, `select`/`radio`/`checkbox` → `dj-change`, `hidden` → none), `debounce=`/`throttle=` passthrough, framework CSS class resolution via `config.get_framework_class('field_class')`, HTML attribute passthrough with underscore-to-dash normalisation (`aria_label="Search"` → `aria-label="Search"`), and a tested XSS escape boundary via a new shared `djust._html.build_tag()` helper. Example: `{% live_input "text" handler="search" value=query debounce="300" placeholder="Search..." %}`. 56 new tests including an explicit XSS matrix across every field type and attribute. See `docs/guides/live-input.md` for the full setup guide.

- **`djust_audit --live <url>` — runtime security-header and CSWSH probe ([#661](https://github.com/djust-org/djust/issues/661))** — Adds a new mode to `djust_audit` that fetches a running deployment with stdlib `urllib` and validates security headers (HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, COOP, CORP), cookies (HttpOnly, Secure, SameSite on session/CSRF cookies), information-disclosure paths (`/.git/config`, `/.env`, `/__debug__/`, `/robots.txt`, `/.well-known/security.txt`), and optionally probes the WebSocket endpoint with `Origin: https://evil.example` to verify the CSWSH defense from [#653](https://github.com/djust-org/djust/issues/653) is actually enforced end-to-end. This catches the class of production issues where the setting is correctly configured in `settings.py` but the response is stripped, rewritten, or never emitted by the time it reaches the client — the NYC Claims pentest caught a critical `Content-Security-Policy missing` case this way (`django-csp` was configured but the header was absent from production responses, stripped by an nginx ingress). 30 new stable finding codes `djust.L001`–`djust.L091` cover every check class so CI configs can suppress specific codes by number. New CLI flags: `--live <url>`, `--paths` (multi-URL), `--no-websocket-probe`, `--header 'Name: Value'` (for staging auth), `--skip-path-probes` (for WAF-protected environments). Supports `--json` and `--strict` (fail on warnings too). Zero new runtime dependencies — stdlib `urllib` for HTTP, optional `websockets` package for the WebSocket probe (skipped with an INFO finding if not installed).

- **New static security checks in `djust_check` / `djust_audit` ([#659](https://github.com/djust-org/djust/issues/659))** — Seven new check IDs fire from `check_configuration` when Django runs `python manage.py check`: **A001** (ERROR) — WebSocket router not wrapped in `AllowedHostsOriginValidator` (static-analysis companion to #653 for existing apps built from older scaffolds). **A010** (ERROR) — `ALLOWED_HOSTS = ["*"]` in production. **A011** (ERROR) — `ALLOWED_HOSTS` mixes `"*"` with explicit hosts (the wildcard makes the explicit entries meaningless). **A012** (ERROR) — `USE_X_FORWARDED_HOST=True` combined with wildcard `ALLOWED_HOSTS` enables Host header injection. **A014** (ERROR) — `SECRET_KEY` starts with `django-insecure-` in production (scaffold default not overridden before deployment). **A020** (WARNING) — `LOGIN_REDIRECT_URL` is a single hardcoded path but the project has multiple auth groups/permissions (catches the "every role lands on the same dashboard" anti-pattern). **A030** (WARNING) — `django.contrib.admin` installed without a known brute-force protection package (`django-axes`, `django-defender`, etc.). Each check has essentially zero false-positive risk, has a `fix_hint` pointing at the remediation, and was motivated by the 2026-04-10 NYC Claims pentest report. **Out of scope for this PR:** manifest scanning (k8s/helm/docker-compose env blocks) — deferred to a follow-up. Python-level `settings.py` values cover the common case.

- **`djust_audit --permissions permissions.yaml` — declarative permissions document for CI-level RBAC drift detection ([#657](https://github.com/djust-org/djust/issues/657))** — Adds a new flag to `djust_audit` that validates every LiveView against a committed, human-readable YAML document describing the expected auth configuration for each view. CI fails on any deviation (view declared public but has auth in code, permission list mismatch, undeclared view in strict mode, stale declaration, etc.). This closes a structural gap the existing audit couldn't catch: `djust_audit` today can tell "no auth" from "some auth", but not that `login_required=True` should have been `permission_required=['claims.view_supervisor']`. The permissions document IS the ground truth. Seven stable error codes (`djust.P001` through `djust.P007`) cover every deviation class. Also adds `--dump-permissions` to bootstrap a starter YAML from existing code, and `--strict` to fail CI on any finding. Full documentation in `docs/guides/permissions-document.md`. Motivated by NYC Claims pentest finding 10/11 where every view had `login_required=True` set and djust_audit reported them all as protected, but the lowest-privilege authenticated user could ID-walk the entire database.

- **`WizardMixin` for multi-step LiveView form wizards** — General-purpose mixin managing step navigation, per-step validation, and data collection for guided form flows. Provides `next_step`, `prev_step`, `go_to_step`, `update_step_field`, `validate_field`, and `submit_wizard` event handlers. Template context includes step indicators, progress, form data/errors, and pre-rendered field HTML via `as_live_field()`. Re-validates all steps on submission to guard against tampered WebSocket replays. ([#632](https://github.com/djust-org/djust/pull/632))

### Security

- **LOW: Nonce-based CSP support — drop `'unsafe-inline'` from `script-src` / `style-src`** — djust's inline `<script>` and `<style>` emissions (handler metadata bootstrap in `TemplateMixin._inject_handler_metadata`, `live_session` route map in `routing.get_route_map_script`, and the PWA template tags `djust_sw_register`, `djust_offline_indicator`, `djust_offline_styles`) now read `request.csp_nonce` when available (set by [django-csp](https://django-csp.readthedocs.io/) when `CSP_INCLUDE_NONCE_IN` covers the relevant directive) and emit a `nonce="..."` attribute on the tag. When no nonce is available (django-csp not installed, or `CSP_INCLUDE_NONCE_IN` not set), the tags emit without a nonce attribute — fully backward compatible with apps still allowing `'unsafe-inline'`. Apps that want strict CSP can now set `CSP_INCLUDE_NONCE_IN = ("script-src", "script-src-elem", "style-src", "style-src-elem")` in `settings.py`, drop `'unsafe-inline'` from `CSP_SCRIPT_SRC` / `CSP_STYLE_SRC`, and get strict CSP XSS protection across all djust-generated inline content. The PWA tags `djust_sw_register`, `djust_offline_indicator`, and `djust_offline_styles` now use `takes_context=True` to read the request from the template context — they still work with the same template syntax (`{% djust_sw_register %}` etc.) as long as a `RequestContext` is used (Django's default for template rendering). See `docs/guides/security.md` for the full setup. Reported via external penetration test 2026-04-10 (FINDING-W06). Closes the v0.4.1 security hardening batch (#653 / #654 / #655). ([#655](https://github.com/djust-org/djust/issues/655))

- **MEDIUM: Gate VDOM patch timing/performance metadata behind `DEBUG` / `DJUST_EXPOSE_TIMING`** — `LiveViewConsumer` previously attached `timing` (handler/render/total ms) and `performance` (full nested timing tree with handler and phase names) to every VDOM patch response unconditionally, regardless of `settings.DEBUG`. Combined with CSWSH ([#653](https://github.com/djust-org/djust/issues/653)) this let cross-origin attackers observe server-side code-path timings, enabling timing-based code-path differentiation (DB hit vs cache miss, valid vs invalid CSRF), internal handler/phase name disclosure, and load-based DoS scheduling. Now gated on a new helper `_should_expose_timing()` which returns True only when `settings.DEBUG` or the new `settings.DJUST_EXPOSE_TIMING` is True. **Upgrade notes:** production behavior change — existing clients that consumed `response.timing` / `response.performance` in production will no longer see those fields; opt in via `DJUST_EXPOSE_TIMING = True` in settings for staging/profiling. The browser debug panel is unaffected (it receives timing via the existing `_attach_debug_payload` path, which is already gated on `DEBUG`). Reported via external penetration test 2026-04-10. References: CWE-203, CWE-215, OWASP A09:2021. ([#654](https://github.com/djust-org/djust/issues/654))

- **HIGH: Validate WebSocket Origin header to prevent Cross-Site WebSocket Hijacking (CSWSH)** — `LiveViewConsumer.connect()` previously accepted the WebSocket handshake without validating the `Origin` header, and `DjustMiddlewareStack` did not wrap the router in an origin validator. A cross-origin attacker could mount any LiveView and dispatch any event from a victim's browser. Now the consumer rejects disallowed origins with close code 4403 before accepting the handshake, and `DjustMiddlewareStack` wraps its inner application in `channels.security.websocket.AllowedHostsOriginValidator` by default (defense in depth). Missing Origin is still allowed so non-browser clients (curl, test `WebsocketCommunicator`) continue to work. **Upgrade notes:** ensure `settings.ALLOWED_HOSTS` does NOT contain `*` in production; if you need to opt out for a specific stack, use `DjustMiddlewareStack(inner, validate_origin=False)` (not recommended). Reported via external penetration test 2026-04-10. ([#653](https://github.com/djust-org/djust/issues/653))

- **Enforce `login_required` on HTTP GET path** — Views with `login_required = True` rendered full HTML to unauthenticated users on the initial HTTP GET. The WebSocket connection was correctly rejected, but the pre-rendered page content was already visible. Now calls `check_view_auth()` before `mount()` on HTTP GET and returns 302 to `LOGIN_URL`. Also calls `handle_params()` after `mount()` on HTTP GET to match the WebSocket path's behavior, preventing state flash on URL-param-dependent views. ([#636](https://github.com/djust-org/djust/pull/636), fixes [#633](https://github.com/djust-org/djust/issues/633), [#634](https://github.com/djust-org/djust/issues/634))

### Fixed

- **Prevent `SynchronousOnlyOperation` in `PerformanceTracker.track_context_size`** — The tracker called `sys.getsizeof(str(context))`, which triggered `QuerySet.__repr__()` on any unevaluated querysets in the context dict. `__repr__` calls `list(self[:21])`, evaluating the queryset against the database — raising `SynchronousOnlyOperation` in the async WebSocket path. Now uses a shallow per-value `getsizeof` sum that does not invoke `__repr__`/`__str__` on values, so lazy objects stay lazy. Size estimates are now slightly less precise (don't include recursive inner size) but safe in async contexts. ([#651](https://github.com/djust-org/djust/pull/651), fixes [#649](https://github.com/djust-org/djust/issues/649))

- **Apply RemoveChild patches before batched InsertChild in same parent group** — `applyPatches` in `client.js:1379-1440` was filtering `InsertChild` patches out of each parent group and applying them via `DocumentFragment` before iterating the group for the `RemoveChild` patches in that same parent, violating the top-level Remove → Insert phase order. This was latent for keyed content (monotonic dj-ids meant removes still found targets by ID), but fired for `<!--dj-if-->` placeholder comments — they have no dj-id (only elements get IDs), so their removes fall back to index-based lookup, and by the time the removes ran, the batched inserts had already prepended the new content and shifted indices. The removes then deleted the just-inserted content, leaving empty tab content on multi-tab views (symptom: NYC Claims tab switches showing blank content after the first switch). Fix: split each parent group into non-Insert vs Insert lists, apply all non-Insert patches first in their phase-sorted order, then batch the inserts. ([#643](https://github.com/djust-org/djust/pull/643), fixes [#641](https://github.com/djust-org/djust/issues/641), closes [#642](https://github.com/djust-org/djust/pull/642))

- **`dj-patch` on `<a>` tags uses href when attribute value is empty** — Boolean `dj-patch` on anchor elements (`<a href="?tab=docs" dj-patch>`) was resolving to the current URL instead of the href destination. Now falls back to `el.getAttribute('href')` when `dj-patch` is empty and the element is `<a>`. ([#640](https://github.com/djust-org/djust/pull/640))

- **Normalize Model instances in `render_full_template` before passing to Rust** — Django FK fields are class-level descriptors not present in `__dict__`. Rust's `FromPyObject` extracts `__dict__` which has `claimant_id=1` (raw FK int) instead of the related object. Now always calls `normalize_django_value()` on pre-serialized context so FK relationships are resolved via `getattr()` and traversable with dot notation (`{{ claim.claimant.first_name }}`). ([#639](https://github.com/djust-org/djust/pull/639))

- **Render Django Form/BoundField to SafeString HTML in template context** — `{{ form.field_name }}` rendered as empty string because the Rust renderer extracted `Form.__dict__` which doesn't contain computed `BoundField` attributes. Now pre-renders Form and BoundField objects to SafeString HTML via `widget.render()` in all four code paths (serialization, template serialization, template rendering, and LiveView state sync). ([#631](https://github.com/djust-org/djust/pull/631), fixes [#621](https://github.com/djust-org/djust/issues/621))

- **Correct `has_ids` attribute name in WebSocket mount response** — `websocket.py` checked for `"data-dj-id="` but the Rust renderer emits `"dj-id="` attributes. This caused `_stampDjIds()` to be skipped on pre-rendered pages, breaking VDOM patches for large content swaps (e.g. tab switching) while small patches still worked. The SSE path already had the correct check. ([#630](https://github.com/djust-org/djust/pull/630), fixes [#629](https://github.com/djust-org/djust/issues/629))

- **Sync input `.value` from attribute after innerHTML/VDOM patch** — When navigating backward in a multi-step wizard, text input values were not visually restored even though the server sent correct VDOM patches. `setAttribute('value', x)` only updates the HTML attribute (defaultValue), not the `.value` DOM property. Now syncs `.value` from the attribute in `preserveFormValues()`, broadcast patches, and `morphElement()`. Skips focused inputs, checkboxes, radios, and file inputs. ([#625](https://github.com/djust-org/djust/pull/625), fixes [#624](https://github.com/djust-org/djust/issues/624))

## [0.4.0] - 2026-03-27

### Security

- **Fix 25 CodeQL code-scanning alerts in client.js and debug-panel.js** — Added UNSAFE_KEYS guard to VDOM SetAttr/RemoveAttr patches (rejects `__proto__`, `constructor`, `prototype` keys), replaced direct property assignment with `Object.defineProperty()` in debug panel state cloning, converted template literal logs to format strings to prevent log injection, and added XSS suppression comments for trusted server-rendered HTML. ([#597](https://github.com/djust-org/djust/pull/597))

### Removed

- **`whitenoise` dependency** — djust's `ASGIStaticFilesHandler` in `djust.asgi.get_application()` already handles static file serving at the ASGI layer, making WhiteNoise middleware redundant. Removed `whitenoise` from dependencies, scaffolded projects, and the demo project. Removed system check `C006` (daphne without WhiteNoise). ([#584](https://github.com/djust-org/djust/issues/584))

### Added

- **`{% dj_flash %}` template tag in Rust renderer** — Registered `DjFlashTagHandler` so the flash container renders correctly when templates are processed by the Rust engine. Previously, the tag was only registered as a Django template tag and silently dropped by the Rust renderer. ([#590](https://github.com/djust-org/djust/pull/590))

- **Navigation lifecycle events and CSS class** — `djust:navigate-start` / `djust:navigate-end` CustomEvents and `.djust-navigating` CSS class on `[dj-root]` during `dj-navigate` transitions. Enables CSS-only page transitions without monkey-patching `pageLoading`. ([#585](https://github.com/djust-org/djust/issues/585))

- **`manage.py djust_doctor` diagnostic command** -- checks Rust extension, Python/Django versions, Channels, Redis, templates, static files, routing, and ASGI server in one command. Supports `--json`, `--quiet`, `--check NAME`, and `--verbose` flags.

- **Enhanced VDOM patch error messages** -- patch failures now include patch type, `dj-id`, parent element info, and suggested causes (third-party DOM modification, `{% if %}` block changes). In `DEBUG_MODE`, a console group with full patch detail is shown. Batch failure summaries include which patch indices failed.

- **DEBUG-mode enriched WebSocket errors** -- `send_error` includes `debug_detail` (unsanitized message), `traceback` (last 3 frames), and `hint` (actionable suggestion) when `settings.DEBUG=True`. `handle_mount` lists available LiveView classes when class lookup fails.

- **Debug panel warning interceptor** -- intercepts `console.warn` calls matching `[LiveView]` prefix and surfaces them as a warning badge on the debug button. Configurable auto-open via `LIVEVIEW_CONFIG.debug_auto_open_on_error`.

- **Latency simulator in debug panel** -- test loading states and optimistic updates with simulated network delay. Presets (Off/50/100/200/500ms), custom value, jitter control, localStorage persistence, and visual badge on the debug button. Latency is injected on both WebSocket send and receive for full round-trip simulation. Only active when `DEBUG_MODE=true`.

- **Form recovery on reconnect** — After WebSocket reconnects, form fields with `dj-change` or `dj-input` automatically fire change events to restore server state. Compares DOM values against server-rendered defaults and only fires for fields that differ. Use `dj-no-recover` to opt out individual fields. Fields inside `dj-auto-recover` containers are skipped (custom handler takes precedence). Works over both WebSocket and SSE transports.

- **Reconnection backoff with jitter** — Exponential backoff with random jitter (AWS full-jitter strategy) prevents thundering herd on server restart. Min delay 500ms, max delay 30s, increased from 5 to 10 max attempts. Attempt count shown in reconnection banner (`dj-reconnecting-banner` CSS class) and exposed via `data-dj-reconnect-attempt` attribute and `--dj-reconnect-attempt` CSS custom property on `<body>`. Banner and attributes cleared on successful reconnect or intentional disconnect.

- **`page_title` / `page_meta` dynamic document metadata** — Update `document.title` and `<meta>` tags from any LiveView handler via property setters (`self.page_title = "..."`, `self.page_meta = {"description": "..."}`). Uses side-channel WebSocket messages (no VDOM diff needed). Supports `og:` and `twitter:` meta tags with correct `property` attribute. Works over both WebSocket and SSE transports.

- **`dj-copy` enhancements** — Selector-based copy (`dj-copy="#code-block"` copies the element's `textContent`), configurable feedback text (`dj-copy-feedback="Done!"`), CSS class feedback (`dj-copy-class` adds a custom class for 2s, default `dj-copied`), and optional server event (`dj-copy-event="copied"` fires after successful copy for analytics). Backward compatible with existing literal copy behavior.

- **`dj-auto-recover` attribute for reconnection recovery** — After WebSocket reconnects, elements with `dj-auto-recover="handler_name"` automatically fire a server event with serialized DOM state (form field values and `data-*` attributes from the container). Enables the server to restore custom state lost during disconnection. Does not fire on initial page load. Supports multiple independent recovery elements per page.

- **`dj-debounce` / `dj-throttle` HTML attributes** — Apply debounce or throttle to any `dj-*` event attribute (`dj-click`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`) directly in HTML: `<button dj-click="search" dj-debounce="300">`. Takes precedence over `data-debounce`/`data-throttle`. Supports `dj-debounce="blur"` to defer until element loses focus (Phoenix parity). `dj-debounce="0"` disables default debounce on `dj-input`. Each element gets its own independent timer.

- **Connection state CSS classes** — `dj-connected` and `dj-disconnected` classes are automatically applied to `<body>` based on WebSocket/SSE transport state. Enables CSS-driven UI feedback for connection status (e.g., dimming content, showing offline banners). Both classes are removed on intentional disconnect (TurboNav). Phoenix LiveView's `phx-connected`/`phx-disconnected` equivalent.

- **`dj-cloak` attribute for FOUC prevention** — Elements with `dj-cloak` are hidden (`display: none !important`) until the WebSocket/SSE mount response is received, preventing flash of unconnected content. CSS is injected automatically by client.js — no user stylesheet changes needed. Phoenix LiveView's `phx-no-feedback` equivalent.

- **Page loading bar for navigation transitions** — NProgress-style thin loading bar at the top of the page during TurboNav and `live_redirect` navigation. Always active by default. Exposed as `window.djust.pageLoading` with `start()`, `finish()`, and `enabled` for manual control. Disable via `window.djust.pageLoading.enabled = false` or CSS override.

- **`dj-scroll-into-view` attribute for auto-scroll on render** — Elements with `dj-scroll-into-view` are automatically scrolled into view after DOM updates (mount, VDOM patch). Supports scroll behavior options: `""` (smooth/nearest, default), `"instant"`, `"center"`, `"start"`, `"end"`. One-shot per DOM node — uses WeakSet tracking so the same element isn't re-scrolled on every patch, but VDOM-replaced fresh nodes scroll correctly.

- **`dj-window-*` / `dj-document-*` event scoping** — Bind event listeners on `window` or `document` while using the declaring element for context extraction (component_id, dj-value-* params). Supports `dj-window-keydown`, `dj-window-keyup`, `dj-window-scroll`, `dj-window-click`, `dj-window-resize`, `dj-document-keydown`, `dj-document-keyup`, `dj-document-click`. Key modifier filtering (e.g., `dj-window-keydown.escape="close_modal"`) works the same as `dj-keydown`. Scroll and resize events default to 150ms throttle. Phoenix LiveView's `phx-window-*` equivalent, plus `dj-document-*` as a djust extension.

- **`dj-click-away` attribute** — Fire a server event when the user clicks outside an element: `<div dj-click-away="close_dropdown">`. Uses capture-phase document listener so `stopPropagation()` inside the element doesn't prevent detection. Supports `dj-confirm` for confirmation dialogs and `dj-value-*` params from the declaring element.

- **`dj-shortcut` attribute for declarative keyboard shortcuts** — Bind keyboard shortcuts on any element with modifier key support: `<div dj-shortcut="ctrl+k:open_search:prevent, escape:close_modal">`. Supports `ctrl`, `alt`, `shift`, `meta` modifiers, comma-separated multiple bindings, and `prevent` modifier to suppress browser defaults. Shortcuts are automatically skipped when the user is typing in form inputs (override with `dj-shortcut-in-input` attribute). Event params include `key`, `code`, and `shortcut` (the matched binding string).

- **`_target` param in form change/input events** — When multiple form fields share one `dj-change` or `dj-input` handler, the `_target` param now includes the triggering element's `name` (or `id`, or `null`), letting the server know which field changed. For `dj-submit`, includes the submitter button's name if available. Matches Phoenix LiveView's `_target` convention.

- **`dj-disable-with` attribute for submit buttons** — Automatically disable submit buttons during form submission and replace their text with a loading message: `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and gives instant visual feedback. Works with both `dj-submit` forms and `dj-click` buttons. Original text is restored after server response.

- **`dj-lock` attribute for concurrent event prevention** — Disable an element until its event handler response arrives from the server: `<button dj-click="save" dj-lock>Save</button>`. Prevents rapid double-clicks from triggering duplicate server events. For non-form elements (e.g., `<div>`), applies a `djust-locked` CSS class instead of the `disabled` property. All locked elements are unlocked on server response.

- **`dj-mounted` event for element lifecycle** — Fire a server event when an element with `dj-mounted="handler_name"` enters the DOM after a VDOM patch: `<div dj-mounted="on_chart_ready" dj-value-chart-type="bar">`. Does not fire on initial page load (only after subsequent patches). Includes `dj-value-*` params from the mounted element. Uses a WeakSet to prevent duplicate fires for the same DOM node.

- **Priority-aware event queue for broadcast and async updates** — Server-initiated broadcasts (`server_push`) and async completions (`_run_async_work`) are now tagged with `source="broadcast"` and `source="async"` respectively, and the client buffers them during pending user event round-trips (same as tick buffering from #560). `server_push` now acquires the render lock and yields to in-progress user events to prevent version interleaving. Client-side pending event tracking upgraded from single ref to `Set`-based tracking, supporting multiple concurrent pending events. Buffer flushes only when all pending events resolve.

- **`manage.py djust_gen_live` — Model-to-LiveView scaffolding generator** — Generate a complete CRUD LiveView scaffold from a model name and field definitions: `python manage.py djust_gen_live blog Post title:string body:text`. Creates views.py (with `@event_handler` CRUD operations), urls.py (using `live_session()` routing), HTML template (with `dj-*` directives), and tests.py. Supports `--dry-run`, `--force`, `--no-tests`, `--api` (JSON mode) options. Handles all Django field types including FK relationships. Search uses `Q` objects for OR logic across text fields.

- **`on_mount` hooks for cross-cutting mount logic** — Module-level hooks that run on every LiveView mount, declared via `@on_mount` decorator and `on_mount` class attribute. Use cases: authentication checks, telemetry, tenant resolution, feature flags. Hooks run after auth checks, before `mount()`. Return a redirect URL string to halt the mount pipeline. Hooks are inherited via MRO (parent-first, deduplicated). Includes V009 system check for validation. Phoenix `on_mount` v0.17+ parity.

- **`put_flash(level, message)` and `clear_flash()` for ephemeral flash notifications** — Phoenix `put_flash` parity. Queue transient messages (info, success, warning, error) from any event handler; they are flushed to the client over WebSocket/SSE after each response. Includes `{% dj_flash %}` template tag with auto-dismiss and ARIA `role="status"` / `role="alert"` support. ([#568](https://github.com/djust-org/djust/pull/568))

- **`handle_params` called on initial mount** — `handle_params(params, uri)` is now invoked after `mount()` on the initial WebSocket connect, not just on subsequent URL changes. This matches Phoenix LiveView's `handle_params/3` contract and eliminates the need to duplicate URL-parsing logic between `mount()` and `handle_params()`. Views that don't override `handle_params` are unaffected (default is a no-op).

- **`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs: `<button dj-click="delete" dj-value-id:int="{{ item.id }}" dj-value-type="soft">`. Supports type-hint suffixes (`:int`, `:float`, `:bool`, `:json`, `:list`), kebab-to-snake_case conversion, and prototype pollution prevention. Works with all event types: `dj-click`, `dj-submit`, `dj-change`, `dj-input`, `dj-keydown`, `dj-keyup`, `dj-blur`, `dj-focus`, `dj-poll`. Phoenix LiveView's `phx-value-*` equivalent.

### Fixed

- **`True`/`False`/`None` literals resolved as empty string in custom tag args** — `get_value()` didn't recognize Python boolean/None literals, so `{% tag show_labels=False %}` produced `show_labels=` (empty string) instead of `show_labels=False`. Now handles `True`/`true`, `False`/`false`, and `None`/`none` as literal values. ([#602](https://github.com/djust-org/djust/pull/602))
- **Flash and page_metadata not delivered over HTTP POST fallback** — `put_flash()` and `page_title`/`page_meta` side-channel commands were only flushed over WebSocket. HTTP POST responses now drain `_pending_flash` and `_pending_page_metadata` and include them as `_flash` and `_page_metadata` arrays in the JSON response. ([#590](https://github.com/djust-org/djust/pull/590))
- **Custom tag args containing lists/objects serialized as `[List]`/`[Object]`** — `Value::List` and `Value::Object` in custom tag arguments were stringified via the `Display` trait, destroying structured data before it reached Python handlers. Now serialized as JSON via `serde_json`. ([#589](https://github.com/djust-org/djust/issues/589))
- **Django filters not applied in custom tag arguments** — `{% tag key=var|length %}` rendered the literal string instead of the computed value because arg resolution used `context.get()` (plain lookup) instead of `get_value()` (filter-aware). ([#591](https://github.com/djust-org/djust/pull/591))
- **`{% if %}` inside HTML tag after `{{ variable }}` emits `<!--dj-if-->` comment** — `is_inside_html_tag()` only checked the immediately preceding token, missing tag context when `{{ variable }}` tokens appeared between the tag opening and `{% if %}`. Added `is_inside_html_tag_at()` that scans all preceding tokens. ([#580](https://github.com/djust-org/djust/issues/580))
- **Tick/event version mismatch silently drops user input** — Server-initiated ticks could collide with user events, causing VDOM version divergence that silently discarded patches. Added server-side `asyncio.Lock` to serialize tick and event render operations, priority yielding so ticks skip during user events, client-side tick patch buffering during pending event round-trips, and monotonic event ref tracking for request/response matching. ([#560](https://github.com/djust-org/djust/issues/560))

- **Focus lost during VDOM patches** — When the server pushed VDOM patches (e.g., updating a counter while the user was typing), the focused input/textarea lost focus, cursor position, selection range, and scroll position. Added `saveFocusState()` / `restoreFocusState()` around the `applyPatches()` cycle to capture and restore `activeElement`, `selectionStart`/`selectionEnd`, and `scrollTop`/`scrollLeft`. Element matching uses id → name → dj-id → positional index. Broadcast (remote) updates correctly skip focus restoration.

- **VDOM patching fails when `{% if %}` blocks add/remove DOM elements** — Comment node placeholders (`<!--dj-if-->`) emitted by the Rust template engine were excluded from client-side child index resolution (`getSignificantChildren` and `getNodeByPath`), causing path traversal errors and silent patch failures. Also added `#comment` handling to `createNodeFromVNode` so comment placeholders can be correctly created during `InsertChild` patches. ([#559](https://github.com/djust-org/djust/issues/559))

## [0.3.8] - 2026-03-19

### Fixed

- **Tick auto-refresh causes VDOM version mismatch, silently drops user events** — `_run_tick` always called `render_with_diff()` even when `handle_tick()` made no state changes, incrementing the VDOM version on every tick. When a user event interleaved with a tick, the client and server versions diverged, causing all subsequent patches to be silently discarded. Tick now uses `_snapshot_assigns` to skip render when no public assigns changed. ([#560](https://github.com/djust-org/djust/issues/560))
- **WS VDOM cache key collision across tabs** — All WebSocket LiveViews shared a single RustLiveView cache slot keyed by `/ws/live/`, causing multi-tab sessions to overwrite each other's compiled templates. Cache key now uses `request.path` (the actual page URL) so each view gets its own VDOM baseline. ([#561](https://github.com/djust-org/djust/pull/561))
- **Canvas `width`/`height` cleared during `html_update` morph** — `morphElement` removed attributes absent from server HTML, resetting canvas 2D contexts and blanking Chart.js charts. Canvas `width` and `height` are now preserved during attribute sync. ([#561](https://github.com/djust-org/djust/pull/561))
- **`_force_full_html` not checked in `handle_url_change`** — Views that set `_force_full_html = True` in `handle_params` (e.g., when `{% for %}` loop lengths change) still received VDOM patches instead of full HTML. The flag is now checked after `render_with_diff()` in both `handle_event` and `handle_url_change`. ([#559](https://github.com/djust-org/djust/issues/559), [#561](https://github.com/djust-org/djust/pull/561))

### Added

- **`dj-patch` on selects/inputs uses WS `url_change`** — Select and input elements with `dj-patch` now update via pushState + WebSocket `url_change` instead of full page reload. A delegated `document` change listener survives DOM replacement by morphdom. `dj-patch-reload` attribute remains as an opt-in escape hatch for full page navigation. ([#561](https://github.com/djust-org/djust/pull/561))

## [0.3.7] - 2026-03-16

### Fixed

- **FormMixin: serialization, event handling, and ModelForm support** — Fixed 6 issues blocking production use of `FormMixin` with `ModelForm` over WebSocket: added `@event_handler` to `submit_form()` and `validate_field()`; renamed `form_instance` to private `_form_instance` with backward-compatible property; store `model_pk`/`model_label` as public attributes for re-hydration after WS session restore; sync `form_data` from saved instance after `form_valid()`; use FK PK instead of related object; auto-populate `form_choices` with serializable tuples. ([#545](https://github.com/djust-org/djust/pull/545))
- **`dj-hook` elements not re-initialized after `html_update` or `html_recovery`** — When VDOM patches failed and djust fell back to full HTML replacement, `updateHooks()` was never called, leaving hook elements stale (charts showing old data, canvases empty). Added `updateHooks()` to all DOM replacement paths: `html_update`, `html_recovery`, TurboNav reinit, embedded view update, lazy hydration, and streaming updates. ([#548](https://github.com/djust-org/djust/pull/548))
- **`__version__` not updated by `make version`** — `make version` only updated `pyproject.toml` and `Cargo.toml` but not the hardcoded `__version__` in `__init__.py` files. `djust.__version__` now stays in sync with the package version. ([#547](https://github.com/djust-org/djust/issues/547))

### Changed

- **Extract `reinitAfterDOMUpdate()` to DRY up post-DOM-update calls** — The repeated pattern of `initReactCounters()` + `initTodoItems()` + `bindLiveViewEvents()` + `updateHooks()` across 10+ call sites is now a single function. New DOM replacement paths only need one call. ([#549](https://github.com/djust-org/djust/issues/549))
- **Extract `addEventContext()` to consolidate component/embedded view ID extraction** — The 8-line `getComponentId`/`getEmbeddedViewId` pattern appeared 4 times in event binding; now a single helper. ([#551](https://github.com/djust-org/djust/issues/551))
- **Extract `isWSConnected()` to replace WebSocket state guard chains** — The `liveViewWS && liveViewWS.ws && liveViewWS.ws.readyState === WebSocket.OPEN` pattern appeared across 4 files; now a single predicate. ([#552](https://github.com/djust-org/djust/issues/552))
- **Extract `clearOptimisticPending()` to consolidate CSS class cleanup** — The `querySelectorAll('.optimistic-pending')` removal loop appeared 4 times across 2 files; now a single function. ([#553](https://github.com/djust-org/djust/issues/553))
- **Standardize `DJUST_CONFIG` access via `get_djust_config()`** — Replaced 10+ inline `getattr(settings, "DJUST_CONFIG", {})` try/except blocks across tenants, PWA, and storage modules with a single `get_djust_config()` helper in `config.py`. ([#554](https://github.com/djust-org/djust/issues/554))
- **Extract generic `BackendRegistry` class** — The duplicated lazy-init / set / reset pattern in `state_backends/registry.py` and `backends/registry.py` now delegates to a shared `BackendRegistry` class in `utils.py`. ([#555](https://github.com/djust-org/djust/issues/555))
- **Extract `is_model_list()` helper** — The repeated `isinstance(value, list) and value and isinstance(value[0], models.Model)` check is now a single `is_model_list()` function in `utils.py`, used in `mixins/context.py` and `mixins/request.py`. ([#556](https://github.com/djust-org/djust/issues/556))

## [0.3.6] - 2026-03-14

### Breaking Changes

- **`model.id` now returns the native type, not a string** — `_serialize_model_safely()` previously wrapped `obj.pk` with `str()` when producing the `"id"` key, causing template comparisons like `{% if edit_id == todo.id %}` to fail silently when `edit_id` was an integer. `model.id` now matches `model.pk` and returns the native Python type (e.g. `int`, `UUID`). **Migration:** if your templates or event handlers compare `model.id` against string literals or string-typed variables, update them to use the native type. PR #262 fixed `.pk`; this PR (#472) completes the fix for `.id`.

### Fixed

- **Skip redundant `mount()` on WebSocket connect for pre-rendered pages** — When the client sends `has_prerendered=true` on WS connect and saved state exists in the session (written during the HTTP GET), the view's attributes are restored from session instead of re-running `mount()`. This eliminates the double page-load cost for views with expensive `mount()` implementations (e.g. directory scans, API calls). Falls back to calling `mount()` normally when no saved state is found. `_ensure_tenant()` is now called unconditionally before the restore/mount decision, fixing a regression where multi-tenant views had `self.tenant=None` on WS connect for pre-rendered pages. ([#542](https://github.com/djust-org/djust/pull/542))
- **`djust cache --all` now correctly clears all sessions on the Redis backend** — The CLI called `cleanup_expired(ttl=0)` to force-clear sessions, but the semantics of `ttl=0` changed in 0.3.5 to mean "never expire". The command now calls the explicit `delete_all()` method, which uses a Redis pipeline for an efficient single round-trip bulk delete. ([#409](https://github.com/djust-org/djust/pull/409))
- **`dj-params` attribute no longer silently dropped** — Between 0.3.2 and 0.3.6rc2, `dj-params` was removed from the client event-binding code. Templates using `dj-params='{"key": value}'` continued to fire click events but the server received `params: {}`. The attribute is now read and merged into the params object for backward compatibility. A `console.warn` is emitted in debug mode (`globalThis.djustDebug`) to notify developers to migrate. ([#469](https://github.com/djust-org/djust/pull/469))
- **Prefetch Set not cleared on SPA navigation** — The client-side `_prefetched` Set persisted across `live_redirect` navigations, preventing links on the new view from being prefetched. Added `clear()` to `window.djust._prefetch` and call it in `handleLiveRedirect()` so each SPA navigation starts with a fresh prefetch state. ([#402](https://github.com/djust-org/djust/pull/402))
- **Auto-reload on unrecoverable VDOM state** — When VDOM patch recovery fails because recovery HTML is unavailable (e.g. after server restart), the client now auto-reloads the page instead of showing a confusing error overlay. The server sends `recoverable: false` to signal the client. ([#421](https://github.com/djust-org/djust/pull/421))
- **`{% djust_pwa_head %}` and other custom tags with quoted arguments containing spaces now render correctly** — The Rust template lexer used `split_whitespace()` to tokenize tag arguments, which broke quoted values like `name="My App"` into separate tokens (`name="My` and `App"`). This caused the downstream Python handler to receive malformed arguments, silently returning empty output. Replaced with a quote-aware splitter (`split_tag_args`) that preserves quoted strings as single arguments. ([#419](https://github.com/djust-org/djust/pull/419))
- **`{% load %}` tags stripped during template inheritance, breaking inclusion tags** — The Rust parser treated `{% load %}` as `Node::Comment`, which `nodes_to_template_string()` discarded during inheritance reconstruction. When the resolved template was re-parsed, custom tags that relied on Django tag libraries (e.g. `{% djust_pwa_head %}`) could silently fail. Fixed by adding a dedicated `Node::Load` variant that preserves library names through reconstruction. Also improved `_render_django_tag()` error handling: failures now log a full traceback via `logger.exception()` and return a visible HTML comment instead of an empty string. ([#418](https://github.com/djust-org/djust/pull/418))
- **Checkbox/radio `checked` and `<option>` `selected` state not updated by VDOM patches** — `SetAttr` and `RemoveAttr` patches only called `setAttribute`/`removeAttribute`, which updates the HTML attribute but not the DOM property. After user interaction the browser separates the two, so server-driven state changes via `dj-click` had no visible effect on checkboxes, radios, or select options. Fixed by syncing the DOM property alongside the attribute. Also fixed `createNodeFromVNode` to set `.checked`/`.selected` when creating new elements. ([#422](https://github.com/djust-org/djust/pull/422))
- **`SESSION_TTL=0` breaks all event handling (no DOM patches)** — `cleanup_expired()` methods in both `InMemoryStateBackend` and `RedisStateBackend` now treat `TTL ≤ 0` as "never expire". Previously `SESSION_TTL=0` caused `cutoff = time.time() - 0`, making all sessions appear expired, deleting them immediately, and leaving no state for VDOM patches. ([#395](https://github.com/djust-org/djust/issues/395))
- **WebSocket session extraction crashes on Django Channels `LazyObject`** — Replaced `hasattr(scope_session, "session_key")` with `getattr(scope_session, "session_key", None)` in the consumer's request context builder. `hasattr()` on a Django Channels `LazyObject` can raise non-`AttributeError` exceptions during lazy evaluation, causing the consumer to crash silently. ([#396](https://github.com/djust-org/djust/issues/396))

### Deprecated

- **`dj-params` JSON blob attribute** — Use individual `data-*` attributes with optional type-coercion suffixes instead. `dj-params` will be removed in a future release.

  **Migration guide (0.3.2 → 0.3.6):**

  ```html
  <!-- Before (0.3.2) -->
  <button dj-click="start_edit" dj-params='{"todo_id": {{ todo.id }}}'>Edit</button>
  <button dj-click="set_filter" dj-params='{"filter_value": "all"}'>All</button>

  <!-- After (0.3.6+) -->
  <button dj-click="start_edit" data-todo-id:int="{{ todo.id }}">Edit</button>
  <button dj-click="set_filter" data-filter-value="all">All</button>
  ```

  Type-coercion suffixes: `:int`, `:float`, `:bool`, `:json`. Kebab-case attribute names are auto-converted to `snake_case` for server handler parameters.

### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Available via the `djust-deploy` entry point after installation. ([#437](https://github.com/djust-org/djust/pull/437))
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout
  - `--server` flag / `DJUST_SERVER` env var to override the default server URL (`https://djustlive.com`)
- **TypeScript type stubs updated** — `DjustStreamOp` now includes `"done"` and `"start"` operation types and an optional `mode` field (`"append" | "replace" | "prepend"`). `getActiveStreams()` return type changed from `Map` to `Record`.
- **`.flex-between` CSS utility class** — Added to demo project's `utilities.css` for laying out flex children horizontally with space-between. Use on card headers or any flex container that needs a title on the left and action widget on the right. ([#397](https://github.com/djust-org/djust/issues/397))
- **Debug toolbar state size visualization** — New "Size Breakdown" table in State tab shows per-variable memory and serialized byte sizes with human-readable formatting (B/KB/MB). Added `_debug_state_sizes()` method to `PostProcessingMixin` included in both mount and event debug payloads. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel TurboNav persistence** — Event, patch, network, and state history now persist across TurboNav navigation via sessionStorage (30s window). Panel state restores on next page if navigated within 30 seconds. ([#459](https://github.com/djust-org/djust/pull/459))
- **TurboNav integration guide** — Comprehensive guide covering setup, navigation lifecycle, inline script handling, known caveats, and design decisions: `docs/guides/turbonav-integration.md`. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel search extended to Network and State tabs** — The search bar in the debug panel now filters across all data tabs. The Network tab shows a `N / total` count label when a query narrows the message list (#530). The State tab filters history entries by trigger, event name, and serialized state content, with the same `N / total` count label (#520). Overlapping `nameFilter` and `searchQuery` on the Events tab now correctly apply AND semantics (#532). ([#541](https://github.com/djust-org/djust/pull/541))

## [0.3.6rc4] - 2026-03-13

### Fixed

- **Skip redundant `mount()` on WebSocket connect for pre-rendered pages** — When the client sends `has_prerendered=true` on WS connect and saved state exists in the session (written during the HTTP GET), the view's attributes are restored from session instead of re-running `mount()`. This eliminates the double page-load cost for views with expensive `mount()` implementations (e.g. directory scans, API calls). Falls back to calling `mount()` normally when no saved state is found. `_ensure_tenant()` is now called unconditionally before the restore/mount decision, fixing a regression where multi-tenant views had `self.tenant=None` on WS connect for pre-rendered pages. ([#542](https://github.com/djust-org/djust/pull/542))

## [0.3.6rc3] - 2026-03-13

### Breaking Changes

- **`model.id` now returns the native type, not a string** — `_serialize_model_safely()` previously wrapped `obj.pk` with `str()` when producing the `"id"` key, causing template comparisons like `{% if edit_id == todo.id %}` to fail silently when `edit_id` was an integer. `model.id` now matches `model.pk` and returns the native Python type (e.g. `int`, `UUID`). **Migration:** if your templates or event handlers compare `model.id` against string literals or string-typed variables, update them to use the native type. PR #262 fixed `.pk`; this PR (#472) completes the fix for `.id`.

### Fixed

- **`djust cache --all` now correctly clears all sessions on the Redis backend** — The CLI called `cleanup_expired(ttl=0)` to force-clear sessions, but the semantics of `ttl=0` changed in 0.3.5 to mean "never expire". The command now calls the explicit `delete_all()` method, which uses a Redis pipeline for an efficient single round-trip bulk delete. ([#409](https://github.com/djust-org/djust/pull/409))
- **`dj-params` attribute no longer silently dropped** — Between 0.3.2 and 0.3.6rc2, `dj-params` was removed from the client event-binding code. Templates using `dj-params='{"key": value}'` continued to fire click events but the server received `params: {}`. The attribute is now read and merged into the params object for backward compatibility. A `console.warn` is emitted in debug mode (`globalThis.djustDebug`) to notify developers to migrate. ([#469](https://github.com/djust-org/djust/pull/469))
- **Prefetch Set not cleared on SPA navigation** — The client-side `_prefetched` Set persisted across `live_redirect` navigations, preventing links on the new view from being prefetched. Added `clear()` to `window.djust._prefetch` and call it in `handleLiveRedirect()` so each SPA navigation starts with a fresh prefetch state. ([#402](https://github.com/djust-org/djust/pull/402))
- **Auto-reload on unrecoverable VDOM state** — When VDOM patch recovery fails because recovery HTML is unavailable (e.g. after server restart), the client now auto-reloads the page instead of showing a confusing error overlay. The server sends `recoverable: false` to signal the client. ([#421](https://github.com/djust-org/djust/pull/421))
- **`{% djust_pwa_head %}` and other custom tags with quoted arguments containing spaces now render correctly** — The Rust template lexer used `split_whitespace()` to tokenize tag arguments, which broke quoted values like `name="My App"` into separate tokens (`name="My` and `App"`). This caused the downstream Python handler to receive malformed arguments, silently returning empty output. Replaced with a quote-aware splitter (`split_tag_args`) that preserves quoted strings as single arguments. ([#419](https://github.com/djust-org/djust/pull/419))
- **`{% load %}` tags stripped during template inheritance, breaking inclusion tags** — The Rust parser treated `{% load %}` as `Node::Comment`, which `nodes_to_template_string()` discarded during inheritance reconstruction. When the resolved template was re-parsed, custom tags that relied on Django tag libraries (e.g. `{% djust_pwa_head %}`) could silently fail. Fixed by adding a dedicated `Node::Load` variant that preserves library names through reconstruction. Also improved `_render_django_tag()` error handling: failures now log a full traceback via `logger.exception()` and return a visible HTML comment instead of an empty string. ([#418](https://github.com/djust-org/djust/pull/418))
- **Checkbox/radio `checked` and `<option>` `selected` state not updated by VDOM patches** — `SetAttr` and `RemoveAttr` patches only called `setAttribute`/`removeAttribute`, which updates the HTML attribute but not the DOM property. After user interaction the browser separates the two, so server-driven state changes via `dj-click` had no visible effect on checkboxes, radios, or select options. Fixed by syncing the DOM property alongside the attribute. Also fixed `createNodeFromVNode` to set `.checked`/`.selected` when creating new elements. ([#422](https://github.com/djust-org/djust/pull/422))
- **`SESSION_TTL=0` breaks all event handling (no DOM patches)** — `cleanup_expired()` methods in both `InMemoryStateBackend` and `RedisStateBackend` now treat `TTL ≤ 0` as "never expire". Previously `SESSION_TTL=0` caused `cutoff = time.time() - 0`, making all sessions appear expired, deleting them immediately, and leaving no state for VDOM patches. ([#395](https://github.com/djust-org/djust/issues/395))
- **WebSocket session extraction crashes on Django Channels `LazyObject`** — Replaced `hasattr(scope_session, "session_key")` with `getattr(scope_session, "session_key", None)` in the consumer's request context builder. `hasattr()` on a Django Channels `LazyObject` can raise non-`AttributeError` exceptions during lazy evaluation, causing the consumer to crash silently. ([#396](https://github.com/djust-org/djust/issues/396))

### Deprecated

- **`dj-params` JSON blob attribute** — Use individual `data-*` attributes with optional type-coercion suffixes instead. `dj-params` will be removed in a future release.

  **Migration guide (0.3.2 → 0.3.6):**

  ```html
  <!-- Before (0.3.2) -->
  <button dj-click="start_edit" dj-params='{"todo_id": {{ todo.id }}}'>Edit</button>
  <button dj-click="set_filter" dj-params='{"filter_value": "all"}'>All</button>

  <!-- After (0.3.6+) -->
  <button dj-click="start_edit" data-todo-id:int="{{ todo.id }}">Edit</button>
  <button dj-click="set_filter" data-filter-value="all">All</button>
  ```

  Type-coercion suffixes: `:int`, `:float`, `:bool`, `:json`. Kebab-case attribute names are auto-converted to `snake_case` for server handler parameters.

### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Available via the `djust-deploy` entry point after installation. ([#437](https://github.com/djust-org/djust/pull/437))
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout
  - `--server` flag / `DJUST_SERVER` env var to override the default server URL (`https://djustlive.com`)
- **TypeScript type stubs updated** — `DjustStreamOp` now includes `"done"` and `"start"` operation types and an optional `mode` field (`"append" | "replace" | "prepend"`). `getActiveStreams()` return type changed from `Map` to `Record`.
- **`.flex-between` CSS utility class** — Added to demo project's `utilities.css` for laying out flex children horizontally with space-between. Use on card headers or any flex container that needs a title on the left and action widget on the right. ([#397](https://github.com/djust-org/djust/issues/397))
- **Debug toolbar state size visualization** — New "Size Breakdown" table in State tab shows per-variable memory and serialized byte sizes with human-readable formatting (B/KB/MB). Added `_debug_state_sizes()` method to `PostProcessingMixin` included in both mount and event debug payloads. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel TurboNav persistence** — Event, patch, network, and state history now persist across TurboNav navigation via sessionStorage (30s window). Panel state restores on next page if navigated within 30 seconds. ([#459](https://github.com/djust-org/djust/pull/459))
- **TurboNav integration guide** — Comprehensive guide covering setup, navigation lifecycle, inline script handling, known caveats, and design decisions: `docs/guides/turbonav-integration.md`. ([#459](https://github.com/djust-org/djust/pull/459))
- **Debug panel search extended to Network and State tabs** — The search bar in the debug panel now filters across all data tabs. The Network tab shows a `N / total` count label when a query narrows the message list (#530). The State tab filters history entries by trigger, event name, and serialized state content, with the same `N / total` count label (#520). Overlapping `nameFilter` and `searchQuery` on the Events tab now correctly apply AND semantics (#532). ([#541](https://github.com/djust-org/djust/pull/541))

## [0.3.5] - 2026-03-05


### Added

- **`djust-deploy` CLI** — new `python/djust/deploy_cli.py` module providing deployment commands for [djustlive.com](https://djustlive.com). Install with `pip install djust[deploy]`. Available via the `djust-deploy` entry point:
  - `djust-deploy login` — prompts for email/password, authenticates against djustlive.com, and stores the token in `~/.djustlive/credentials` (mode `0o600`)
  - `djust-deploy logout` — calls the server logout endpoint and removes the local credentials file
  - `djust-deploy status [project]` — fetches current deployment state; optionally filtered by project slug
  - `djust-deploy deploy <project-slug>` — validates the git working tree is clean, triggers a production deployment, and streams build logs to stdout

### Fixed

- **`dj-hook` elements now initialize after `dj-navigate` navigation** — `updateHooks()` is called after `live_redirect_mount` replaces DOM content via WebSocket and SSE mount handlers. Previously, hook lifecycle callbacks (`mounted()`, `destroyed()`) were skipped after client-side navigation, leaving hook-dependent elements (e.g., Chart.js canvases) uninitialized. ([#408](https://github.com/djust-org/djust/pull/408))
- **Event handler exceptions now logged with full traceback in production** — Previously, `handle_exception()` only logged the exception class name (e.g. `ValueError`) when `DEBUG=False`, hiding the error message and stack trace. Now logs type, message, and traceback at `ERROR` level regardless of `DEBUG` mode. Client responses remain generic in production. ([#415](https://github.com/djust-org/djust/pull/415))
- **DJE-053 no longer fires as a warning for idempotent event handlers** — When an `@event_handler` runs successfully but produces no DOM changes (e.g. toggle clicked in target state, debounced input with unchanged results, side-effect-only handlers), the empty diff is now silently dropped at `DEBUG` level rather than logged as a `WARNING`. This matches Phoenix LiveView behaviour. The `WARNING`-level DJE-053 is preserved for genuine VDOM failures (`patches=None`), which fall back to a full HTML update and risk losing event listeners. ([#415](https://github.com/djust-org/djust/pull/415))

## [0.3.5rc2] - 2026-03-04

### Fixed

- **VDOM patching with conditional `{% if %}` blocks** — `InsertChild` and `RemoveChild` patches now include `ref_d` and `child_d` fields for ID-based DOM resolution, preventing stale-index mis-targeting when `{% if %}` blocks add or remove elements that shift sibling positions. Falls back to index-based resolution for backwards compatibility. ([#410](https://github.com/djust-org/djust/issues/410))

## [0.3.5rc1] - 2026-02-26

### Added

- **Type stubs for Rust-injected LiveView methods** — `.pyi` stubs for `live_redirect`, `live_patch`, `push_event`, `stream`, and related methods so mypy/pyright catch typos at lint time. ([#390](https://github.com/djust-org/djust/pull/390))
- **Navigation Patterns guide** — Documents when to use `dj-navigate` vs `live_redirect` vs `live_patch`. ([#390](https://github.com/djust-org/djust/pull/390))
- **Testing guide** — Django testing best practices and pytest setup for djust applications. ([#390](https://github.com/djust-org/djust/pull/390))
- **System checks reference** — New `docs/system-checks.md` covering all 37 check IDs (C/V/S/T/Q) with severity, detection method, suppression patterns, and known false positives. ([#398](https://github.com/djust-org/djust/pull/398))

### Security

- **`mark_safe(f"...")` eliminated in core framework** — `components/base.py` now uses `format_html()` to avoid XSS risk in component rendering. ([#390](https://github.com/djust-org/djust/pull/390))
- **Exception details no longer exposed in production** — `render_template()` previously returned `f"<div>Error: {e}</div>"` unconditionally, leaking internal Rust template engine details. Now returns a generic message in production; error details are only shown when `settings.DEBUG = True`. ([#385](https://github.com/djust-org/djust/pull/385))
- **Playground XSS fixed** — Replaced `innerHTML` assignment with a sandboxed iframe for user-editable preview content. ([#384](https://github.com/djust-org/djust/pull/384))
- **Prototype pollution guard** — Added safeguards against prototype pollution in client-side JS. ([#384](https://github.com/djust-org/djust/pull/384))

### Fixed

- **`{% if %}` inside attribute values no longer shifts VDOM path indices** — Conditional attribute fragments were causing off-by-one errors in VDOM diffing. ([#390](https://github.com/djust-org/djust/pull/390))
- **`super().__init__()` added to component and backend subclasses** — `TenantAwareRedisBackend`, `TenantAwareMemoryBackend`, and several example components were missing `super().__init__()` calls, causing MRO issues. ([#386](https://github.com/djust-org/djust/pull/386))
- **Unused `escape` import removed from `data_table.py`** — CodeQL alert resolved. ([#387](https://github.com/djust-org/djust/pull/387))
- **`render_full_template` signature mismatch fixed** — `no_template_demo.py` override now correctly accepts `serialized_context`. ([#387](https://github.com/djust-org/djust/pull/387))
- **V004 false positives on lifecycle methods** — `handle_params()`, `handle_disconnect()`, `handle_connect()`, and `handle_event()` no longer incorrectly trigger the V004 system check. ([#398](https://github.com/djust-org/djust/pull/398))
- **T013 false positives for `{{ view_path }}`** — `dj-view="{{ view_path }}"` (Django template variable injection) is now correctly recognised as valid by T013. ([#398](https://github.com/djust-org/djust/pull/398))
- **V008 false positives for `-> str`-annotated functions** — Functions with primitive return-type annotations (e.g. `-> str`, `-> int`) no longer trigger V008 when their result is assigned in `mount()`. ([#398](https://github.com/djust-org/djust/pull/398))
- **Test isolation** — `test_checks.py` and `double_bind.test.js` no longer fail when run as part of the full suite. ([#390](https://github.com/djust-org/djust/pull/390))

## [0.3.4] - 2026-02-24

Stable release — promotes 0.3.3rc1 through 0.3.3rc3. All changes below were present in the RC series; this entry summarises them for the stable changelog.

### Added

- **6 new Django template tags in Rust renderer** — `{% widthratio %}`, `{% firstof %}`, `{% templatetag %}`, `{% spaceless %}`, `{% cycle %}`, `{% now %}`. ([#329](https://github.com/djust-org/djust/issues/329))
- **System checks `djust.T011` / `T012` / `T013`** — Warns at startup for unsupported Rust template tags, missing `dj-view`, and invalid `dj-view` paths. ([#293](https://github.com/djust-org/djust/issues/293), [#329](https://github.com/djust-org/djust/issues/329))
- **Deployment guides** — Railway, Render, and Fly.io. ([#247](https://github.com/djust-org/djust/issues/247))
- **Navigation and LiveView invariants documentation.** ([#304](https://github.com/djust-org/djust/issues/304), [#316](https://github.com/djust-org/djust/issues/316))

### Fixed

- **#380: `{% if %}` in HTML attribute values no longer emits `<!--dj-if-->` comment** — Produced malformed HTML (e.g. `class="btn <!--dj-if-->"`). Empty string is emitted instead; text-node VDOM anchor is unaffected. ([#381](https://github.com/djust-org/djust/pull/381))
- **#382: `{% elif %}` chains in attribute values propagate `in_tag_context`** — All elif nodes in a chain now inherit the outer `{% if %}`'s attribute context. ([#383](https://github.com/djust-org/djust/pull/383))
- **`{% if/else %}` branches miscounting div depth in template extraction.** ([#365](https://github.com/djust-org/djust/issues/365))
- **VDOM extraction used fully-merged `{% extends %}` document.** ([#366](https://github.com/djust-org/djust/issues/366))
- **`TypeError: Illegal invocation` in debug panel on Chrome/Edge.** ([#367](https://github.com/djust-org/djust/issues/367))
- **`dj-patch('/')` now correctly updates browser URL to root path.** ([#307](https://github.com/djust-org/djust/issues/307))
- **`live_patch` routing restored** — `handleNavigation` dispatch now fires correctly. ([#307](https://github.com/djust-org/djust/issues/307))
- **T003 false positives eliminated** — `{% include %}` check now examines the include path, not whole-file content. ([#331](https://github.com/djust-org/djust/issues/331))

## [0.3.3rc3] - 2026-02-24

### Fixed

- **#382: `{% elif %}` inside HTML attribute values propagates `in_tag_context`** — When `{% if a %}...{% elif b %}...{% endif %}` appears inside an attribute value and all conditions are false, the elif node previously emitted `<!--dj-if-->` (malformed HTML). Fixed by threading `in_tag_context` as a parameter into `parse_if_block()` so elif nodes inherit the outer if's attribute context. ([#382](https://github.com/djust-org/djust/issues/382))

## [0.3.3rc2] - 2026-02-24

### Fixed

- **`{% if/else %}` branches miscounting div depth in template extraction** — `_extract_liveview_root_with_wrapper` and the other extraction methods treated both branches of a `{% if/else %}` block as independent div opens, causing depth to never reach 0 when both branches opened a div sharing a single closing `</div>`. This caused the entire template to be returned as root, making the view non-reactive. Fixed with a shared `_find_closing_div_pos()` static method that uses a branch stack to restore depth at `{% else %}`/`{% elif %}` tags, so mutually-exclusive branches are counted as one open. ([#365](https://github.com/djust-org/djust/issues/365))
- **VDOM extraction used fully-merged `{% extends %}` document** — For inherited templates, `get_template()` extracted the VDOM root from the fully-resolved document (base HTML + inlined blocks), which contains surrounding HTML that the depth counter could trip over. Now prefers the child template source when it contains `dj-root`/`dj-view`, which holds exactly the block content needed. Also fixes the exception fallback path: the raw child source (containing `{% extends %}`) was incorrectly stored in `_full_template`, causing `render_full_template` to attempt rendering a non-standalone template. ([#366](https://github.com/djust-org/djust/issues/366))
- **`TypeError: Illegal invocation` in debug panel on Chrome/Edge** — `_hookExistingWebSocket` called native WebSocket getter/setter functions via `Function.prototype.call()` from external code, which fails V8's brand check on IDL-generated bindings. Fixed by using normal property access (`ws.onmessage`) and assignment (`ws.onmessage = handler`) instead of `desc.get/set.call(ws)`. ([#367](https://github.com/djust-org/djust/issues/367))

## [0.3.3rc1] - 2026-02-21

### Added

- **6 new Django template tags in Rust renderer** — Implemented `{% widthratio %}`, `{% firstof %}`, `{% templatetag %}`, `{% spaceless %}`, `{% cycle %}`, and `{% now %}` in the Rust template engine. These tags were previously rendered as HTML comments with warnings. ([#329](https://github.com/djust-org/djust/issues/329))
- **System check `djust.T011` for unsupported template tags** — Warns at startup when templates use Django tags not yet implemented in the Rust renderer (`ifchanged`, `regroup`, `resetcycle`, `lorem`, `debug`, `filter`, `autoescape`). Suppressible with `{# noqa: T011 #}`. ([#329](https://github.com/djust-org/djust/issues/329))
- **System check `djust.T012` for missing `dj-view`** — Detects templates that use `dj-*` event directives without a `dj-view` attribute, which would silently fail at runtime. ([#293](https://github.com/djust-org/djust/issues/293))
- **System check `djust.T013` for invalid `dj-view` paths** — Detects empty or malformed `dj-view` attribute values. ([#293](https://github.com/djust-org/djust/issues/293))
- **`{% now %}` supports 35+ Django date format specifiers** — Including `S` (ordinal suffix), `t` (days in month), `w`/`W` (weekday/week number), `L` (leap year), `c` (ISO 8601), `r` (RFC 2822), `U` (Unix timestamp), and Django's special `P` format (noon/midnight).
- **Deployment guides** — Added deployment documentation for Railway, Render, and Fly.io. ([#247](https://github.com/djust-org/djust/issues/247))
- **Navigation best practices documentation** — Documented `dj-patch` vs `dj-click` for client-side navigation, with `handle_params()` patterns. ([#304](https://github.com/djust-org/djust/issues/304))
- **LiveView invariants documentation** — Documented root container requirement and `**kwargs` convention for event handlers. ([#316](https://github.com/djust-org/djust/issues/316))

### Fixed

- **#380: `{% if %}` inside HTML attribute values no longer emits `<!--dj-if-->` comment** — When a `{% if %}` block with no else branch evaluates to false inside an HTML attribute value (e.g. `class="btn {% if active %}active{% endif %}"`), the Rust renderer now emits an empty string instead of the `<!--dj-if-->` VDOM placeholder. The placeholder is only meaningful as a DOM child node; inside an attribute it produced malformed HTML (e.g. `class="btn <!--dj-if-->"`). Text-node context is unaffected — the anchor comment is still emitted there for VDOM stability (fix for DJE-053 / #295).
- **False `{% if %}` blocks now emit `<!--dj-if-->` placeholder instead of empty string** — Gives the VDOM diffing engine a stable DOM anchor to target when the condition later becomes true, resolving DJE-053 / issue #295.
- **`dj-patch('/')` now correctly updates the browser URL to the root path** — Removed the `url.pathname !== '/'` guard in `bindNavigationDirectives` that prevented the browser URL from being updated when patching to `/`. The guard was silently ignoring root-path patches. ([#307](https://github.com/djust-org/djust/issues/307))
- **`live_patch` routing restored — `handleNavigation` dispatch now fires correctly** — Fixed dict merge order in `_flush_navigation` so `type: 'navigation'` is no longer overwritten by `**cmd`. Added an `action` field to carry the nav sub-type (`live_patch` / `live_redirect`); `handleNavigation` now dispatches on `data.action` instead of `data.type`. Previously the client `switch case 'navigation':` never matched because `type` was being overwritten with `"live_patch"`. **Note:** `data.action || data.type` fallback is kept for old JS clients that send messages without an `action` field — this fallback is planned for removal in the next minor release. ([#307](https://github.com/djust-org/djust/issues/307))
- **T003 false positives eliminated** — The `{% include %}` check now examines the include path instead of the whole file content, preventing false warnings on templates that include SVGs or modals alongside `dj-*` directives. ([#331](https://github.com/djust-org/djust/issues/331))

## [0.3.2] - 2026-02-18

### Added

- **TypeScript definitions (`djust.d.ts`)** — Comprehensive ambient TypeScript declaration file shipped with the Python package at `static/djust/djust.d.ts`. Covers: `window.djust` namespace, `LiveViewWebSocket` and `LiveViewSSE` transport classes, `DjustHook` lifecycle interface (`mounted`, `beforeUpdate`, `updated`, `destroyed`, `disconnected`, `reconnected`), `DjustHookContext` (`this.el`, `this.pushEvent`, `this.handleEvent`), `dj-model` binding types, streaming API types (`DjustStreamMessage`, `DjustStreamOp`), upload progress event types (`DjustUploadEntry`, `DjustUploadConfig`, `DjustUploadProgressEventDetail`), and the `djust:upload:progress` custom DOM event. Use via `/// <reference path="..." />` or add to `tsconfig.json`.
- **Python type stubs (`_rust.pyi`)** — PEP 561 compliant type stubs for the PyO3 Rust extension module (`djust._rust`). Covers all exported functions (`render_template`, `render_template_with_dirs`, `diff_html`, `resolve_template_inheritance`, `fast_json_dumps`, serialization helpers, tag handler registry) and classes (`RustLiveView`, `SessionActorHandle`, `SupervisorStatsPy`, and all 15 Rust UI components). Enables full IDE autocomplete and mypy type checking for the Rust extension.
- **SSE (Server-Sent Events) fallback transport** — djust now automatically falls back to SSE when WebSocket is unavailable (corporate proxies, enterprise firewalls). Architecture: `EventSource` for server→client push, HTTP POST for client→server events. Transport negotiation is automatic: WebSocket is tried first; SSE activates after all reconnect attempts fail. Register the endpoint with `path("djust/", include(djust.sse.sse_urlpatterns))` and include `03b-sse.js` in your template. Feature limitations: no binary file uploads, no presence tracking, no actor-based state. See `docs/sse-transport.md` for full setup guide.
- **Type stub files (.pyi) for LiveView and mixins** — Added PEP 561 compliant type stubs for `NavigationMixin`, `PushEventMixin`, `StreamsMixin`, `StreamingMixin`, and `LiveView` to enable IDE autocomplete and mypy type checking for runtime-injected methods like `live_redirect`, `live_patch`, `push_event`, `stream`, `stream_insert`, `stream_delete`, and `stream_to`. Includes `py.typed` marker file and comprehensive test suite.
- **`@background` decorator for async event handlers** — New decorator that automatically runs the entire event handler in a background thread via `start_async()`. Simplifies syntax for long-running operations (AI generation, API calls, file processing) without needing explicit callback splitting. Can be combined with other decorators like `@debounce`. Task name is automatically set to the handler's function name for cancellation tracking. ([#313](https://github.com/djust-org/djust/issues/313))
- **`start_async()` keeps loading state active during background work** — WebSocket responses include `async_pending` flag when a `start_async()` callback is running, preventing loading spinners from disappearing prematurely. Async completion responses include `event_name` so the client clears the correct loading state. Supports named tasks for tracking and cancellation via `cancel_async(name)`. Optional `handle_async_result(name, result, error)` callback for completion/error handling. ([#313](https://github.com/djust-org/djust/issues/313), [#314](https://github.com/djust-org/djust/pull/314))
- **`dj-loading.for` attribute** — Scope any `dj-loading.*` directive to a specific event name, regardless of DOM position. Allows spinners, disabled buttons, and other loading indicators anywhere in the page to react to a named event. ([#314](https://github.com/djust-org/djust/pull/314))
- **`AsyncWorkMixin` included in `LiveView` base class** — `start_async()` is now available on all LiveViews without explicit mixin import. ([#314](https://github.com/djust-org/djust/pull/314))
- **Loading state re-scan after DOM patches** — `scanAndRegister()` is called after every `bindLiveViewEvents()` so dynamically rendered elements (e.g., inside modals) get loading state registration. Stale entries for disconnected elements are cleaned up automatically. ([#314](https://github.com/djust-org/djust/pull/314))
- **System check `djust.T010` for dj-click navigation antipattern** — Detects elements using `dj-click` with navigation-related data attributes (`data-view`, `data-tab`, `data-page`, `data-section`). This pattern should use `dj-patch` instead for proper URL updates, browser history support, and bookmarkable views. Warning severity. ([#305](https://github.com/djust-org/djust/issues/305))
- **System check `djust.Q010` for navigation state in event handlers** — Heuristic INFO-level check that detects `@event_handler` methods setting navigation state variables (`self.active_view`, `self.current_tab`, etc.) without using `patch()` or `handle_params()`. Suggests converting to `dj-patch` pattern for URL updates and back-button support. Can be suppressed with `# noqa: Q010`. ([#305](https://github.com/djust-org/djust/issues/305))
- **Type stubs for Rust extension and LiveView** — Added `.pyi` type stub files for `_rust` module and `LiveView` class, enabling IDE autocomplete, mypy/pyright type checking, and catching typos like `live_navigate` (should be `live_patch`) at lint time. Includes `py.typed` marker for PEP 561 compliance and comprehensive documentation in `docs/TYPE_STUBS.md`.

### Deprecated

- **`data.type` fallback in `handleNavigation`** — The `data.action || data.type` fallback for pre-#307 clients (added for backwards compatibility in [#318](https://github.com/djust-org/djust/pull/318)) will be removed in the next minor release. Server now sends `data.action` on all navigation messages. Update any custom client code that sends navigation messages without an `action` field.

### Fixed

- **Silent `str()` coercion for non-serializable LiveView state** — Non-serializable objects stored in `self.*` during `mount()` (e.g., service instances, API clients) were silently converted to strings, causing confusing `AttributeError` on subsequent requests far from the root cause. `normalize_django_value()` now logs a warning before falling back with the type name, module, and guidance on how to fix. Opt-in strict mode (`DJUST_STRICT_SERIALIZATION = True`) raises `TypeError` instead of coercing, recommended for development. New static check `djust.V008` (AST-based) detects non-primitive assignments in `mount()` at development time. ([#292](https://github.com/djust-org/djust/issues/292))
- **System check S005 incorrectly warns on views with `login_required = False`** — The S005 security check now correctly distinguishes between intentionally public views (`login_required = False`) and views that haven't addressed authentication at all (`login_required = None`). Previously, views with `login_required = False` were incorrectly flagged as missing authentication due to a truthy test. The check now uses explicit `is not None` comparisons to distinguish intentional public access from unaddressed auth. ([#303](https://github.com/djust-org/djust/issues/303))
- **`|safe` filter rendering empty string for nested SafeString values** — When mark_safe() HTML was stored in lists of dicts or nested dicts, the |safe filter rendered an empty string instead of preserving the HTML. The _collect_safe_keys() function now recursively scans nested dicts and lists using dotted path notation (e.g., "items.0.content") to track all SafeString locations. Includes circular reference protection to prevent RecursionError on tree/graph structures. ([#317](https://github.com/djust-org/djust/issues/317))
- **VDOM diff incorrectly matching siblings when `{% if %}` removes nodes** — When `{% if %}` blocks evaluated to false and removed elements, siblings shifted left, causing `diff_indexed_children()` to incorrectly match unrelated nodes and generate wrong patches. The template engine now emits `<!--dj-if-->` placeholder comments when conditions are false (matching Phoenix LiveView's approach), maintaining consistent sibling positions. The VDOM diff detects placeholder-to-content transitions and generates `RemoveChild` + `InsertChild` patches instead of `Replace` patches for semantic consistency. Eliminates DJE-053 fallback to full HTML updates and removes need for `style='display:none'` workarounds. ([#295](https://github.com/djust-org/djust/issues/295))
- **Event listener leak causing duplicate WebSocket sends** — Single user actions were triggering the same event multiple times (e.g. `select_project` 5×, `mount` 3×) because listeners accumulated across VDOM patch/morph cycles without cleanup. Fixed four root causes: (1) `initReactCounters` now uses a `WeakSet` guard to skip already-initialized containers; (2) `createNodeFromVNode` no longer pre-marks elements as bound before `bindLiveViewEvents()` runs, eliminating a race where newly inserted elements were silently skipped; (3) `dj-click` handlers now read the attribute at fire-time rather than bind-time, so `morphElement` attribute updates take effect immediately; (4) three unguarded `console.log` calls in `12-vdom-patch.js` are now wrapped in `if (globalThis.djustDebug)`. The existing `WeakMap`-based deduplication in `bindLiveViewEvents()` (introduced in #312) correctly prevents re-binding when called repeatedly. ([#315](https://github.com/djust-org/djust/issues/315))
- **`dj-patch('/')` failed to update URL and `live_patch` routing broken** — Removed `url.pathname !== '/'` guard in `bindNavigationDirectives` so root-path navigation works. Fixed dict merge order in `_flush_navigation` so server sends `type='navigation'` instead of `type='live_patch'`. Updated `handleNavigation` to dispatch via `data.action` with `data.action || data.type` fallback for backwards compatibility. ([#318](https://github.com/djust-org/djust/pull/318))
- **52 unguarded `console.log` calls in client JS** — All `console.log` calls across 12 files in `static/djust/src/` (excluding the intentional debug panel in `src/debug/`) are now wrapped with `if (globalThis.djustDebug)`. Bare logging in production code leaks internal state to browser consoles and violates the `djust.Q003` system check. Files affected: `00-namespace.js`, `02-response-handler.js`, `03-websocket.js`, `04-cache.js`, `05-state-bus.js`, `06-draft-manager.js`, `07-form-data.js`, `09-event-binding.js`, `10-loading-states.js`, `11-event-handler.js`, `12-vdom-patch.js`, `13-lazy-hydration.js`.
- **dj-submit forms sent empty params when created by VDOM patches** — `createNodeFromVNode` now correctly collects `FormData` for submit events; replaced `data-liveview-*-bound` attribute tracking with `WeakMap` to prevent stale binding flags after DOM replacement ([#312](https://github.com/djust-org/djust/pull/312))

### Security

- **F-strings in logging calls** — Converted 9 logger calls to use %-style formatting (`logger.error("msg %s", val)`) instead of f-strings (`logger.error(f"msg {val}")`). F-strings defeat lazy evaluation, causing string interpolation before the log level check, potentially exposing sensitive data and wasting CPU. Affected files: `mixins/template.py`, `security/__init__.py`, `security/error_handling.py`, `template_tags/__init__.py`, `template_tags/static.py`, `template_tags/url.py`.

### Tests

- **Regression tests for `|safe` filter with nested dicts** — Added comprehensive tests verifying that `|safe` filter works correctly for HTML content in nested dict/list values, preventing issue [#317](https://github.com/djust-org/djust/issues/317) from recurring

## [0.3.2rc1] - 2026-02-15

### Fixed

- **Form data lost on `dj-submit`** — Client-only properties (`_targetElement`, `_optimisticUpdateId`, `_skipLoading`, `_djTargetSelector`) are now stripped from event params before serialization. Previously, `HTMLFormElement` references in params corrupted the JSON payload, overwriting form field data with the element's indexed children. ([#308](https://github.com/djust-org/djust/issues/308))
- **`@change` → `dj-change` in form adapters** — All three framework adapters (Bootstrap 5, Tailwind, Plain) rendered `@change="validate_field"` instead of `dj-change="validate_field"`, causing real-time field validation to silently fail. ([#310](https://github.com/djust-org/djust/pull/310))
- **`EmailField` rendered as `type="text"`** — `_get_field_type()` checked `CharField` before `EmailField` (which inherits from `CharField`), so email fields never got `type="email"`. Reordered the isinstance checks. ([#310](https://github.com/djust-org/djust/pull/310))

### Security

- **XSS in `FormMixin.render_field()`** — Removed `render_field()`, `_render_field_widget()`, and `_attrs_to_string()` from `FormMixin`. These methods used f-strings with no escaping to build HTML, allowing stored XSS via form field values. Use `as_live()` / `as_live_field()` (which delegate to framework adapters with proper `escape()`) instead. ([#310](https://github.com/djust-org/djust/pull/310))
- **Textarea content not escaped in adapters** — `_render_input()` passed raw textarea values to `_build_tag()` content without `escape()`. Added `escape(str(value))` for textarea content. ([#310](https://github.com/djust-org/djust/pull/310))

### Changed

- **Framework adapters deduplicated** — Created `BaseAdapter` with all shared rendering logic. `Bootstrap5Adapter`, `TailwindAdapter`, and `PlainAdapter` reduced from ~200 lines each to ~10 lines of class attributes. `frameworks.py` reduced from ~657 to ~349 lines. ([#310](https://github.com/djust-org/djust/pull/310))
- **`_model_instance` support for ModelForm editing** — `FormMixin.mount()` now reads field values from `_model_instance` if set and the form is a `ModelForm`. `_create_form()` passes `instance=` to the form constructor. ([#310](https://github.com/djust-org/djust/pull/310))

### Deprecated

- **`LiveViewForm`** — Emits `DeprecationWarning` on subclass. Adds no functionality over `django.forms.Form`. Will be removed in 0.4. ([#310](https://github.com/djust-org/djust/pull/310))

### Removed

- **`FormMixin.render_field()`** — Insecure (XSS via f-strings) and duplicated adapter logic. Use `as_live_field()` instead. ([#310](https://github.com/djust-org/djust/pull/310))
- **`form_field()` function** — Dead code, never called. Removed from `forms.py` and `__all__`. ([#310](https://github.com/djust-org/djust/pull/310))

## [0.3.1] - 2026-02-14

### Changed

- **3.8x faster rendering for large pages** — Optimized `get_context_data()` by replacing `dir(self)` iteration (~300 inherited Django View attributes, ~50ms) with targeted `__dict__` + MRO walk (<1ms). Added `dj-update="ignore"` optimization to Rust VDOM diff engine, skipping subtrees the client won't patch (240ms → 17ms). Combined with template-level optimizations, reduces event roundtrip from ~160ms to ~42ms on pages with large static content.

## [0.3.0] - 2026-02-14

### Added

- **`dj-confirm` attribute** — Declarative confirmation dialogs for event handlers. Add `dj-confirm="Are you sure?"` to any `dj-click` element to show a browser confirmation dialog before dispatching the event. ([#302](https://github.com/djust-org/djust/pull/302))

- **CSS Framework Support** — Comprehensive Tailwind CSS integration with three-part system: (1) System checks (`djust.C010`, `djust.C011`, `djust.C012`) automatically warn about Tailwind CDN in production, missing compiled CSS, and manual `client.js` loading. (2) Graceful fallback auto-injects Tailwind CDN in development mode when `output.css` is missing. (3) CLI helper command `python manage.py djust_setup_css tailwind` creates `input.css` with Tailwind v4 syntax, auto-detects template directories, finds Tailwind CLI, and builds CSS with optional `--watch` and `--minify` flags. Eliminates duplicate client.js race conditions and guides developers toward production-ready setup.

### Fixed

- **Server-side template processing now auto-infers dj-root from dj-view** — All template extraction methods (`_extract_liveview_content`, `_extract_liveview_root_with_wrapper`, `_extract_liveview_template_content`, `_strip_liveview_root_in_html`) now fall back to `[dj-view]` when `[dj-root]` is not present, matching the client-side `autoStampRootAttributes()` behavior introduced in PR #297. This fixes a bug where templates with only `dj-view` (no explicit `dj-root`) would fail to render correctly. ([#300](https://github.com/djust-org/djust/issues/300))
- **Client-side autoMount now correctly reads dj-view attribute** — Fixed `autoMount()` to use `getAttribute('dj-view')` instead of `container.dataset.djView`. The `dataset` API reads `data-*` attributes, but `dj-view` is not a data attribute, causing the attribute to be missed. ([#300](https://github.com/djust-org/djust/issues/300))
- **System check T002 downgraded from WARNING to INFO** — Since `dj-root` is now optional and auto-inferred from `dj-view` (per PR #297), the T002 check is now informational rather than a warning. The message now clarifies that auto-inference is working correctly. ([#300](https://github.com/djust-org/djust/issues/300))
- **Duplicate client.js loading race condition** — djust now automatically detects and warns (via `djust.C012` system check) when base or layout templates manually include `<script src="{% static 'djust/client.js' %}">`. Since the framework auto-injects `client.js`, manual loading causes double-initialization and console warnings. The check provides clear guidance to remove manual script tags.
- **Tailwind CDN in production** — New `djust.C010` system check warns when Tailwind CDN (`cdn.tailwindcss.com`) is detected in production templates (`DEBUG=False`). Provides actionable guidance to compile CSS with `djust_setup_css` command or Tailwind CLI. Prevents slow CDN performance and console warnings in production.

### Security

- **Pre-Release Security Audit Process** — Comprehensive security infrastructure to prevent vulnerabilities like the mount handler RCE (Issue #298) from reaching production. Includes 259 new security tests (Python + Rust) covering parameter injection, file upload attacks, URL injection, and XSS prevention across all contexts. Three GitHub workflows provide automated security scanning (bandit, safety, cargo-audit, npm audit, CodeQL), hot spot detection (auto-labels PRs touching security-sensitive code), and CI security test job requiring 85% coverage for security-sensitive modules. New pre-release security audit template with 7-phase checklist ensures comprehensive review before each release. Documentation updates establish mandatory security gates and review requirements for changes to hot spot files.

### Dependencies

- Bump happy-dom from 20.5.3 to 20.6.1 ([#289](https://github.com/djust-org/djust/pull/289))
- Bump tempfile from 3.24.0 to 3.25.0 ([#288](https://github.com/djust-org/djust/pull/288))

## [0.3.0rc5] - 2026-02-11

### Added

- **Automatic change tracking** — Phoenix-style render optimization. The framework automatically detects which context values changed between renders and only sends those to Rust's `update_state()`. Replaces the manual `static_assigns` API. Two-layer detection: snapshot comparison for instance attributes, `id()` reference comparison for computed values (e.g., `@lru_cache` results). Immutable types (`str`, `int`, `float`, `bool`, `None`, `bytes`, `tuple`, `frozenset`) skip `deepcopy` in snapshots.

### Removed

- **`static_assigns` class attribute** — Replaced by automatic change tracking. The framework now detects unchanged values automatically — no manual annotation needed.

## [0.3.0rc4] - 2026-02-11

### Added

- **All 57 Django template filters** — The Rust template engine now supports the complete set of Django built-in filters. Added 24 filters across two batches: `default_if_none`, `wordcount`, `wordwrap`, `striptags`, `addslashes`, `ljust`, `rjust`, `center`, `make_list`, `json_script`, `force_escape`, `escapejs`, `linenumbers`, `get_digit`, `iriencode`, `urlize`, `urlizetrunc`, `truncatechars_html`, `truncatewords_html`, `safeseq`, `escapeseq`, `unordered_list`, `phone2numeric`, `pprint`. ([#246](https://github.com/djust-org/djust/issues/246), [#254](https://github.com/djust-org/djust/issues/254))
- **Authentication & Authorization** — Opinionated, framework-enforced auth for LiveViews. View-level `login_required` and `permission_required` class attributes (plus `LoginRequiredMixin`/`PermissionRequiredMixin` for Django-familiar patterns). Custom auth logic via `check_permissions()` hook. Handler-level `@permission_required()` decorator for protecting individual event handlers. Auth checks run server-side before `mount()` and before handler dispatch — no client-side bypass possible. Integrates with `djust_audit` command (shows auth posture per view) and Django system checks (`djust.S005` warns on unprotected views with exposed state).
- **Navigation & URL State** — `live_patch()` updates URL query params without remount, `live_redirect()` navigates to a different view over the same WebSocket. Includes `handle_params()` callback, `live_session()` URL routing helper, and client-side `dj-patch`/`dj-navigate` directives with popstate handling. ([#236](https://github.com/djust-org/djust/pull/236))
- **Presence Tracking** — Real-time user presence with `PresenceMixin` and `PresenceManager`. Pluggable backends (in-memory and Redis). Includes `LiveCursorMixin` and `CursorTracker` for collaborative live cursor features. ([#236](https://github.com/djust-org/djust/pull/236))
- **Streaming** — `StreamingMixin` for real-time partial DOM updates (e.g., LLM token-by-token streaming). Provides `stream_to()`, `stream_insert()`, `stream_text()`, `stream_error()`, `stream_start()`/`stream_done()`, and `push_state()`. Batched at ~60fps to prevent flooding. ([#236](https://github.com/djust-org/djust/pull/236))
- **File Uploads** — `UploadMixin` with binary WebSocket frame protocol for chunked file uploads. Includes progress tracking, magic bytes validation, file size/extension/MIME checking, and client-side `dj-upload`/`dj-upload-drop` directives. ([#236](https://github.com/djust-org/djust/pull/236))
- **JS Hooks** — `dj-hook` attribute for client-side JavaScript lifecycle hooks (mounted, updated, destroyed, disconnected, reconnected). ([#236](https://github.com/djust-org/djust/pull/236))
- **Model Binding** — `dj-model` two-way data binding with `.lazy` and `.debounce-N` modifiers. Server-side `ModelBindingMixin` with security field blocklist and type coercion. ([#236](https://github.com/djust-org/djust/pull/236))
- **Client Directives** — `dj-confirm` confirmation dialogs, `dj-target` scoped updates, embedded view routing in event handlers. ([#236](https://github.com/djust-org/djust/pull/236))
- **Server-Push API** — Background tasks (Celery, management commands, cron jobs) can now push state updates to connected LiveView clients via `push_to_view()`. Includes per-view channel groups (auto-joined on mount), a sync/async public API (`push_to_view` / `apush_to_view`), and periodic `handle_tick()` for self-updating views. ([#230](https://github.com/djust-org/djust/issues/230))
- **Progressive Web App (PWA) Support** — Complete offline-first PWA implementation with service worker integration, IndexedDB/LocalStorage abstraction, optimistic UI updates, and offline-aware template directives. Includes comprehensive template tags (`{% djust_pwa_head %}`, `{% djust_pwa_manifest %}`), PWA mixins (`PWAMixin`, `OfflineMixin`, `SyncMixin`), and automatic synchronization when online. ([#235](https://github.com/djust-org/djust/pull/235))
- **Multi-Tenant SaaS Support** — Production-ready multi-tenant architecture with flexible tenant resolution strategies (subdomain, path, header, session, custom, chained), automatic data isolation, tenant-aware state backends, and comprehensive template context injection. Includes `TenantMixin` and `TenantScopedMixin` for views. ([#235](https://github.com/djust-org/djust/pull/235))
- **`dj-poll` attribute** — Declarative polling for LiveView elements. Add `dj-poll="handler_name"` to any element to trigger the handler at regular intervals. Configurable via `dj-poll-interval` (default: 5000ms). Automatically pauses when the page is hidden and resumes on visibility change. ([#269](https://github.com/djust-org/djust/issues/269))
- **`DjustMiddlewareStack`** — New ASGI middleware for apps that don't use `django.contrib.auth`. Wraps WebSocket routes with session middleware only (no auth required). Updated `C005` system check to recognize both `AuthMiddlewareStack` and `DjustMiddlewareStack`. ([#265](https://github.com/djust-org/djust/issues/265))
- **System check `C006`** — Warns when `daphne` is in `INSTALLED_APPS` but `whitenoise` middleware is missing. ([#259](https://github.com/djust-org/djust/issues/259))
- **`startproject` / `startapp` / `new` CLI commands** — `python -m djust new myapp` creates a full project with optional features (`--with-auth`, `--with-db`, `--with-presence`, `--with-streaming`, `--from-schema`). Legacy `startproject` and `startapp` commands also available. ([#266](https://github.com/djust-org/djust/issues/266))
- **`djust mcp install` CLI command** — Automates MCP server setup for Claude Code, Cursor, and Windsurf. Tries `claude mcp add` first (canonical for Claude Code), falls back to writing `.mcp.json` directly. Merges with existing config, backs up malformed files, idempotent.
- **Simplified root element** — `dj-view` is now the only required attribute on LiveView container elements. The client auto-stamps `dj-root` and `dj-liveview-root` at init time. Old three-attribute format still works. ([#258](https://github.com/djust-org/djust/issues/258))
- **Model `.pk` in templates** — `{{ model.pk }}` now works in Rust-rendered templates. Model serialization includes a `pk` key with the native primary key value. ([#262](https://github.com/djust-org/djust/issues/262))
- **Better Error Messages** — Improved error messages for common LiveView event handler mistakes (missing `@event_handler`, wrong method signature). ([#248](https://github.com/djust-org/djust/issues/248))
- **`LiveViewSmokeTest` mixin** — Automated smoke and fuzz testing for LiveView classes. ([#251](https://github.com/djust-org/djust/pull/251))
- **MCP server** — `python manage.py djust_mcp` starts a Model Context Protocol server for AI assistant integration. Provides framework introspection, system checks, scaffolding, and validation tools. Used by `djust mcp install` to configure Claude Code, Cursor, and Windsurf.
- **`djust_audit` management command** — Security audit showing auth posture, exposed state, and handler signatures per view.
- **`djust_check` management command** — Django system checks for project validation. Gains `--fix` flag for safe auto-fixes and `--format json` for enhanced output with fix hints.
- **`djust_schema` management command** — Extract and generate Django models from JSON schema files.
- **`djust_ai_context` management command** — Generate AI-focused context files for LLM integrations.
- **AI documentation** — `docs/ai/` with focused guides for events, forms, JIT, lifecycle, security, and templates. `docs/llms.txt` and `docs/llms-full.txt` for LLM context.
- **Auto-build client.js from src/ modules** — Pre-commit hook runs `build-client.sh` when `src/` files change. ([#211](https://github.com/djust-org/djust/issues/211))
- **Keyed-mutation fuzz test generator** — New proptest generator produces tree B by mutating tree A, exercising keyed diff paths more effectively. Proptest cases bumped from 500 to 1000. ([#216](https://github.com/djust-org/djust/issues/216), [#217](https://github.com/djust-org/djust/issues/217))

### Changed

- **BREAKING: `data-dj-*` prefix stripping** — Client-side `extractTypedParams()` now strips the `dj_` prefix from `data-dj-*` attributes. `data-dj-preset="dark"` sends `{preset: "dark"}` instead of `{dj_preset: "dark"}`. Update handler parameter names accordingly: `dj_foo` → `foo`.
- **State Backends** — Enhanced with tenant-aware isolation support (`TenantAwareRedisBackend`, `TenantAwareMemoryBackend`).

### Performance

- **Batched `sync_to_async` calls** — Event handler processing now uses 2 thread hops instead of 4, saving ~1-4ms per event. ([#277](https://github.com/djust-org/djust/issues/277))
- **Eliminated JSON encode/decode roundtrip** — Direct `normalize_django_value()` Python-to-Python type normalization replaces 17 `json.loads(json.dumps(...))` patterns. Saves 2-5ms per event for views with database objects. ([#279](https://github.com/djust-org/djust/issues/279))
- **Cached template variable extraction** — Rust `extract_template_variables()` results cached by content hash (SHA-256). Size-capped at 256 entries with automatic eviction. ([#280](https://github.com/djust-org/djust/issues/280))
- **Cached context processor resolution** — `resolve_context_processors()` results cached per settings configuration. Invalidated on `setting_changed` signal. ([#281](https://github.com/djust-org/djust/issues/281))
- **JIT short-circuit for non-DB views** — Views without QuerySets or Models in context skip the entire JIT serialization pipeline. Saves ~0.5ms per event for simple views. ([#278](https://github.com/djust-org/djust/issues/278))
- **Slimmer debug payload** — Event responses send only state variables; handler metadata moved to initial mount as static data. ~68% smaller debug payloads (~25KB → ~8KB per event).

### Fixed

- **Inline args on form events** — `dj-change`, `dj-input`, `dj-blur`, `dj-focus` now parse inline arguments (e.g., `dj-change="toggle(3)"`) before sending to server. Also fixed state change detection to use deep copy comparison, catching in-place mutations.
- **Error overlay on intentional disconnect** — Suppress "WebSocket Connection Failed" overlay during TurboNav navigation via `_intentionalDisconnect` flag.
- **VDOM patch failure recovery** — When VDOM patches fail, the client requests recovery HTML on demand instead of reloading the page. Uses DOM morphing to preserve event listeners and form state. ([#259](https://github.com/djust-org/djust/issues/259))
- **HTTP Fallback Protocol** — `post()` now accepts the HTTP fallback format where the event name is in the `X-Djust-Event` header and params are flat in the body JSON. ([#255](https://github.com/djust-org/djust/issues/255))
- **Debug panel HTTP-only mode** — POST responses include `_debug` payload when `DEBUG=True`, enabling the debug panel in HTTP-only mode. ([#267](https://github.com/djust-org/djust/issues/267))
- **Silent LiveView config failures** — Client JS now shows helpful `console.error` when no LiveView containers are found. Added system check `V005` for modules not in `LIVEVIEW_ALLOWED_MODULES`. ([#257](https://github.com/djust-org/djust/issues/257))
- **HTTP-only mode session state on GET** — `get()` now saves view state to the session immediately when `use_websocket: False`. ([#264](https://github.com/djust-org/djust/issues/264))
- **`use_websocket: False` client-side enforcement** — Setting now actually prevents WebSocket connections. ([#260](https://github.com/djust-org/djust/issues/260))
- **DOM morphing preserves event listeners** — `html_update` now uses morphdom-style DOM diffing instead of `innerHTML`. ([#236](https://github.com/djust-org/djust/pull/236))
- **Textarea newlines preserved** — Template whitespace stripping no longer collapses newlines inside `<textarea>` elements. ([#236](https://github.com/djust-org/djust/pull/236))
- **PresenceMixin crash without auth** — `track_presence()` now checks for `request.user` before accessing it. ([#236](https://github.com/djust-org/djust/pull/236))
- **`_skip_render` support in server_push** — `server_push()` now checks `_skip_render`, preventing phantom renders and VDOM version mismatches. ([#236](https://github.com/djust-org/djust/pull/236))
- **Client-side SetText mis-targets after keyed MoveChild** — MoveChild patches now include `child_d` for `data-dj-id` resolution. ([#225](https://github.com/djust-org/djust/issues/225))
- **VDOM diff/patch round-trip on keyed child reorder** — Patches now processed level-by-level (shallowest parent first). ([#212](https://github.com/djust-org/djust/issues/212))
- **apply_patches djust_id-based resolution** — Resolves parent nodes by `djust_id` instead of path-based traversal. ([#216](https://github.com/djust-org/djust/issues/216))
- **Diff engine keyed+unkeyed interleaving** — Emits `MoveChild` patches for unkeyed element children in keyed contexts. ([#219](https://github.com/djust-org/djust/issues/219))
- **Text node targeting after keyed moves** — `SetText` patches carry `djust_id` when available; `sync_ids` propagates IDs to text nodes. ([#221](https://github.com/djust-org/djust/issues/221))
- **Tag registry test pollution** — `clear_tag_handlers()` now restores built-in handlers in teardown. ([#261](https://github.com/djust-org/djust/issues/261))

### Security

- **HTTP POST handler dispatch gating** — `post()` now enforces the same security model as the WebSocket path: only `@event_handler`-decorated methods can be invoked. Validates event names with `is_safe_event_name()` to block dunders and private methods.
- **Auto-escaping in Rust template engine** — `SafeString` values propagated to Rust for proper auto-escaping.
- **HTML-escaped `urlize` and `unordered_list` filters** — Both filters now escape their output to prevent XSS. ([#254](https://github.com/djust-org/djust/issues/254))
- **Template tag XSS prevention** — All PWA template tags now use `format_html()` and `escape()` instead of `mark_safe()` with f-string interpolation.
- **Sync endpoint hardening** — Removed `@csrf_exempt` from `sync_endpoint_view`. Added authentication requirement, payload validation, and safe field extraction.
- **Silent exception elimination** — All `except: pass` patterns replaced with appropriate logging calls.
- **Production JS hardened** — All `console.log` calls guarded behind `djustDebug` flag.

### Removed

- **`_allowed_events` class attribute** — The backwards-compatibility escape hatch that allowed undecorated methods to be called via WebSocket or HTTP POST has been removed. All event handlers must now use the `@event_handler` decorator.

## [0.2.2] - 2026-02-01

### Fixed

- **Stale Closure Args on VDOM-Patched Elements** — After deleting a todo, the remaining button's click handler sent the wrong `_args` (stale closure from bind time) because `SetAttribute` patches updated the `dj-click` DOM attribute but not the listener closure. Event listeners now re-parse `dj-*` attributes from the DOM at event time. Also sets `dj-*` as DOM attributes in `createNodeFromVNode` and marks elements as bound to prevent duplicate listeners. ([#205](https://github.com/djust-org/djust/pull/205))
- **VDOM: Non-breaking Space Text Nodes Stripped** — Rust parser stripped `&nbsp;`-only text nodes (used in syntax highlighting) because `char::is_whitespace()` includes U+00A0. Now preserves `\u00A0` text nodes in parser, `to_html()`, and client-side path traversal. Also adds `sync_ids()` to prevent ID drift between server VDOM and client DOM after diffing, and 4-phase patch ordering matching Rust's `apply_patches()`. ([#199](https://github.com/djust-org/djust/pull/199))
- **CSRF Token Lookup on Formless Pages** — Pages without a `<form>` element failed to send CSRF tokens with WebSocket events. Token lookup now falls back to the `csrftoken` cookie. ([#210](https://github.com/djust-org/djust/pull/210))
- **Codegen Crash on Numeric Index Paths** — Template expressions like `{{ posts.0.url }}` produced paths starting with a numeric index (`0.url`), generating invalid Python (`obj.0`). Codegen now skips numeric-leading paths since list items are serialized individually.
- **JIT Serialization Pipeline** — Fixed multiple issues in JIT auto-serialization: ([#140](https://github.com/djust-org/djust/pull/140))
  - M2M `.all()` traversal now generates correct iteration code in codegen serializers
  - `@property` attributes are now serialized via Rust→Python codegen fallback when Rust can't access them
  - `list[Model]` context values (not just QuerySets) now receive full JIT optimization with `select_related`/`prefetch_related`
  - Nested dicts containing Model/QuerySet values are now deep-serialized recursively
  - `_djust_annotations` model class attribute for declaring computed annotations (e.g., `Count`) applied during query optimization
  - `{% include %}` templates are now inlined for variable extraction, so included template variables get JIT optimization
  - Rust template parser now correctly prefixes loop variable paths (e.g., `item.field` inside `{% for item in items %}`)
- **`{% include %}` After Cache Restore** — `template_dirs` was not included in msgpack serialization of `RustLiveView`. After a cache hit, the restored view had empty search paths, causing `{% include %}` tags to fail with "Template not found". Now calls `set_template_dirs()` on both WebSocket and HTTP cache-hit paths.
- **VDOM Replace Sibling Grouping** — Fixed `data-djust-replace` inserting children into wrong parent when the replace container has siblings. `groupPatchesByParent()` now uses the full path for child-operation patches, and `groupConsecutiveInserts()` checks parent identity before batching. ([#144](https://github.com/djust-org/djust/pull/144))
- **VDOM Replace Child Removal** — Fixed `data-djust-replace` not removing old children before inserting new ones, causing duplicate content on re-render. ([#142](https://github.com/djust-org/djust/pull/142), [#143](https://github.com/djust-org/djust/pull/143))
- **Context Processor Precedence** — View context now takes precedence over context processors. Previously, context processors could overwrite view-defined variables (e.g., Django's messages processor overwriting a view's `messages` variable).
- **VDOM Keyed Diff Insert Ordering** — Fixed `apply_patches` for keyed diff insert ordering where items were inserted in the wrong position. ([#154](https://github.com/djust-org/djust/pull/154))
- **VDOM MoveChild Resolution** — Fixed `MoveChild` in `apply_patch` by resolving children via `djust_id` instead of index. ([#150](https://github.com/djust-org/djust/pull/150))
- **Debug Toolbar: Received WebSocket Messages Not Captured** — Network tab now captures both sent and received WebSocket messages by intercepting the `onmessage` property setter (not just `addEventListener`). ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Toolbar: Events Tab Always Empty** — Events tab now populates by extracting event data from sent WebSocket messages and matching responses, replacing the broken `window.liveView` hook. ([#188](https://github.com/djust-org/djust/pull/188))
- **Debug Panel: Handler Discovery, Auto-loading, Tab Crashes** — Handler discovery now finds all public methods; `debug-panel.js` auto-loads; handler dict normalized to array; retroactive WebSocket hooking for late-loading panels. ([#191](https://github.com/djust-org/djust/pull/191), [#197](https://github.com/djust-org/djust/pull/197))

### Added

- **Debug Panel: Live Debug Payload** — When `DEBUG=True`, WebSocket event responses now include a `_debug` field with updated variables, handlers, patches, and performance metrics. ([#191](https://github.com/djust-org/djust/pull/191))
- **Debug Toolbar: Event Filtering** — Events tab filter controls to search by event/handler name and filter by status. ([#180](https://github.com/djust-org/djust/pull/180))
- **Debug Toolbar: Event Replay** — Replay button (⟳) that re-sends events through the WebSocket with original params. ([#181](https://github.com/djust-org/djust/pull/181))
- **Debug Toolbar: Scoped State Persistence** — Panel UI state scoped per view class via localStorage. ([#182](https://github.com/djust-org/djust/pull/182))
- **Debug Toolbar: Network Message Inspection** — Directional color coding and copy-to-clipboard for expanded payloads. ([#183](https://github.com/djust-org/djust/pull/183))
- **Debug Toolbar: Test Harness** — Integration tests against the actual `DjustDebugPanel` class. ([#185](https://github.com/djust-org/djust/pull/185))
- **VDOM Proptest/Fuzzing** — Property-based testing for the VDOM diff algorithm with `proptest`. ([#153](https://github.com/djust-org/djust/pull/153))
- **Duplicate Key Detection** — VDOM keyed diff now warns on duplicate keys. ([#149](https://github.com/djust-org/djust/pull/149))
- **Branding Assets** — Official logo variants (dark, light, icon, wordmark, transparent). ([#208](https://github.com/djust-org/djust/pull/208), [#213](https://github.com/djust-org/djust/pull/213))

### Deprecated

- **`@event` decorator alias** — The `@event` shorthand is deprecated in favor of `@event_handler`. `@event` will be removed in v0.3.0. A deprecation warning is emitted at import time. ([#141](https://github.com/djust-org/djust/pull/141))

### Changed

- **Internal: LiveView Mixin Extraction** — Refactored monolithic `live_view.py` into focused mixins: `RequestMixin`, `ContextMixin`, `JITMixin`, `TemplateMixin`, `RustBridgeMixin`, `ComponentMixin`, `LifecycleMixin`. No public API changes. ([#130](https://github.com/djust-org/djust/pull/130))
- **Internal: Module Splits** — Split `client.js` into source modules with concat build, extracted `websocket_utils.py`, `session_utils.py`, `serialization.py`, split `state_backend.py` into `state_backends` package, split `template_backend.py` into `template` package. ([#124](https://github.com/djust-org/djust/pull/124), [#125](https://github.com/djust-org/djust/pull/125), [#126](https://github.com/djust-org/djust/pull/126), [#128](https://github.com/djust-org/djust/pull/128), [#129](https://github.com/djust-org/djust/pull/129))
- **Dependencies** — Upgraded uuid 1.19→1.20, thiserror 1→2, bincode 1→2, happy-dom 20.3.7→20.4.0, actions/setup-python 5→6, actions/upload-artifact 4→6, actions/checkout 4→6, softprops/action-gh-release 1→2

## [0.2.1] - 2026-01-29

### Security

- **WebSocket Event Security Hardening** - Three-layer defense for WebSocket event dispatch: ([#104](https://github.com/djust-org/djust/pull/104))
  - **Event name guard** — regex pattern filter (`^[a-z][a-z0-9_]*$`) blocks private methods, dunders, and malformed names before `getattr()`
  - **`@event_handler` decorator allowlist** — only methods decorated with `@event_handler` (or listed in `_allowed_events`) are callable via WebSocket. Configurable via `event_security` setting (`"strict"` default, `"warn"`, `"open"`)
  - **Server-side rate limiting** — per-connection token bucket algorithm with configurable rate/burst. Per-handler `@rate_limit` decorator for expensive operations. Automatic disconnect after repeated violations (close code 4429)
  - **Per-IP connection limit** — process-level `IPConnectionTracker` enforces a maximum number of concurrent WebSocket connections per IP (default: 10) and a reconnection cooldown after rate-limit disconnects (default: 5 seconds). Configurable via `max_connections_per_ip` and `reconnect_cooldown` in `rate_limit` settings. Supports `X-Forwarded-For` header for proxied deployments. ([#108](https://github.com/djust-org/djust/issues/108), [#121](https://github.com/djust-org/djust/pull/121))
  - **Message size limit** — 64KB default (`max_message_size` setting)

### Documentation

- Added migration guide for `@event_handler` decorator requirement and strict mode upgrade path ([#105](https://github.com/djust-org/djust/issues/105), [#122](https://github.com/djust-org/djust/pull/122))
- Added `@event_handler` decorator to all example demo view handler methods

### Added

- `is_event_handler(func)` — check if a function is decorated with `@event_handler`
- `@rate_limit(rate, burst)` — per-handler server-side rate limiting decorator
- `_allowed_events` class attribute — escape hatch for bulk allowlisting without decorating each method
- `LIVEVIEW_CONFIG` settings: `event_security`, `rate_limit` (including `max_connections_per_ip`, `reconnect_cooldown`), `max_message_size`

## [0.2.0] - 2026-01-28

### Added

- **Template `and`/`or`/`in` Operators** - `{% if %}` conditions now support `and`, `or`, and `in` boolean/membership operators with correct precedence and chaining. ([#103](https://github.com/djust-org/djust/pull/103))

### Fixed

- **Pre-rendered DOM Whitespace Preservation** - WebSocket mount no longer replaces `innerHTML` when content was pre-rendered via HTTP GET. Instead, `data-dj-id` attributes are stamped onto existing DOM elements, preserving whitespace in code blocks and syntax-highlighted content. ([#99](https://github.com/djust-org/djust/pull/99))

- **VDOM Keyed Diffing** - Unkeyed children in keyed diffing contexts are now matched by relative position among unkeyed siblings, eliminating spurious insert+remove patch pairs when keyed children reorder. ([#95](https://github.com/djust-org/djust/pull/95), [#97](https://github.com/djust-org/djust/pull/97))

- **Event Handler Attributes Preserved** - `dj-*` event handler attributes are no longer removed during VDOM patching. ([#100](https://github.com/djust-org/djust/pull/100))

- **Model List Serialization** - Lists of Django Model instances are now properly serialized on GET requests. ([#103](https://github.com/djust-org/djust/pull/103))

- **Mount URL Path** - WebSocket mount requests now use the actual page URL instead of a hardcoded path. ([#95](https://github.com/djust-org/djust/pull/95))

### Changed

- **Dependencies** - Upgraded html5ever 0.27→0.36, markup5ever_rcdom 0.3→0.36, vitest 2.x→4.x, actions/download-artifact 4→7. ([#101](https://github.com/djust-org/djust/pull/101), [#102](https://github.com/djust-org/djust/pull/102), [#43](https://github.com/djust-org/djust/pull/43))

### Developer Experience

- **VDOM Debug Tracing** - `debug_vdom` Django config is now bridged to Rust VDOM tracing. Mixed keyed/unkeyed children emit developer warnings. ([#97](https://github.com/djust-org/djust/pull/97))

## [0.2.0a2] - 2026-01-27

### Changed

- **Internal: DRY Refactoring** - Reduced ~275 lines of duplicate code across the codebase through helper function extraction. These are internal improvements that don't affect the public API. ([#93](https://github.com/djust-org/djust/pull/93), [#94](https://github.com/djust-org/djust/pull/94))
  - `getComponentId()` - DOM traversal for component ID lookup (client.js)
  - `buildFormEventParams()` - Form event parameter building (client.js)
  - `send_error()` - WebSocket error response helper (websocket.py)
  - `_send_update()` - WebSocket patch/HTML response helper (websocket.py)
  - `_create_rust_instance()` - Rust component instantiation (base.py)
  - `_render_template_with_fallback()` - Template rendering with Rust→Django fallback (base.py)
  - `_make_metadata_decorator()` - Decorator factory for metadata-only decorators (decorators.py)

## [0.2.0a1] - 2026-01-26

### Changed

- **BREAKING: Event Binding Syntax** - Standardized all event bindings to use `dj-` prefix instead of `@` prefix. This affects all event attributes: `@click` → `dj-click`, `@input` → `dj-input`, `@change` → `dj-change`, `@submit` → `dj-submit`, `@blur` → `dj-blur`, `@focus` → `dj-focus`, `@keydown` → `dj-keydown`, `@keyup` → `dj-keyup`, `@loading.*` → `dj-loading.*`. Benefits: namespaced attributes, no conflicts with Vue/Alpine, no CSS selector escaping required. ([#68](https://github.com/djust-org/djust/issues/68))

- **BREAKING: Component Consolidation** - Removed legacy `python/djust/component.py`. Use `djust.Component` which now imports from `components/base.py`. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Method Rename** - `LiveComponent.get_context()` → `get_context_data()` for Django consistency. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Decorator Attributes Removed** - Deprecated decorator attributes removed: `_is_event_handler`, `_event_name`, `_debounce_seconds`, `_debounce_ms`, `_throttle_seconds`, `_throttle_ms`. Use `_djust_decorators` dict instead. ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: Data Attributes Renamed** - Standardized data attribute naming for consistency:
  - `dj-liveview-root` → `dj-root`
  - `data-live-view` → `dj-view`
  - `data-live-lazy` → `dj-lazy`
  - `data-dj` → `data-dj-id`
  ([#89](https://github.com/djust-org/djust/pull/89))

- **BREAKING: WebSocket Message Types** - Renamed message types for consistency:
  - `connected` → `connect`
  - `mounted` → `mount`
  - `hotreload.message` → `hotreload`
  ([#89](https://github.com/djust-org/djust/pull/89))

### Added

- **LiveComponent Methods** - Added missing methods to `LiveComponent`: `_set_parent_callback()`, `send_parent()`, `unmount()`. ([#89](https://github.com/djust-org/djust/pull/89))

- **Inline Template Support** - `LiveComponent` now supports inline `template` attribute for template strings, in addition to `template_name` for file-based templates. ([#89](https://github.com/djust-org/djust/pull/89))

- **Form Components Export** - `ForeignKeySelect` and `ManyToManySelect` are now exported from `djust.components`. ([#89](https://github.com/djust-org/djust/pull/89))

### Fixed

- **`{% elif %}` Tag Support**: Template parser now correctly handles `{% elif %}` conditionals. Previously, elif branches fell through to the unknown tag handler and rendered all branches instead of just the matching one. ([#80](https://github.com/djust-org/djust/pull/80))

- **Template Include Fallback** - Component `render()` methods now fall back to Django templates when Rust template engine fails (e.g., for `{% include %}` tags). ([#89](https://github.com/djust-org/djust/pull/89))

## [0.1.8] - 2026-01-25

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks. When a child template overrides a block that is nested inside another block in the parent (e.g., `content` inside `body`), the override is now correctly applied. ([#71](https://github.com/djust-org/djust/pull/71))

## [0.1.7] - 2026-01-25

### Added

- **Tag Handler Registry**: Extensible system for custom Django template tags in Rust. Register Python callbacks for tags like `{% url %}` and `{% static %}` with ~100-500ns overhead per call. Built-in tags (if, for, block) remain zero-overhead native Rust. Includes ADR documenting architecture decisions. ([#65](https://github.com/djust-org/djust/pull/65))
- **Comparison Operators**: Template conditions now support `>`, `<`, `>=`, `<=` operators in addition to `==` and `!=`. ([#65](https://github.com/djust-org/djust/pull/65))
- **Enhanced `{% include %}` Tag**: Full support for `with` clause (pass variables) and `only` keyword (isolate context). ([#65](https://github.com/djust-org/djust/pull/65))
- **Performance Testing Infrastructure**: Comprehensive benchmarking with Criterion (Rust) and pytest-benchmark (Python). New Makefile commands: `make benchmark`, `make benchmark-quick`, `make benchmark-e2e`. Enables tracking performance across releases and detecting regressions. ([#69](https://github.com/djust-org/djust/pull/69))
- **Inline Handler Arguments**: Event handlers now support function-call syntax with arguments directly in the template attribute. Use `dj-click="handler('arg')"` instead of `dj-click="handler" data-value="arg"`. Supports strings, numbers, booleans, null, and multiple arguments. ([#67](https://github.com/djust-org/djust/pull/67))

### Fixed

- **Async Event Handlers**: WebSocket consumer now properly supports `async def` event handlers. Previously only synchronous handlers worked correctly. ([#63](https://github.com/djust-org/djust/pull/63))

### Performance

- Dashboard render: ~37µs (27,000 renders/sec)
- Tag handler overhead: ~100-500ns per call
- Template variable substitution: ~970ns
- 50-row data table: ~188µs

## [0.1.6] - 2026-01-24

### Added

- **`{% url %}` Tag Support**: Django's `{% url %}` template tag is now fully supported with automatic Python-side URL resolution. Supports named URLs, namespaced URLs, and positional/keyword arguments. ([#55](https://github.com/djust-org/djust/pull/55))
- **`{% include %}` Tag Support**: Fixed template include functionality by passing template directories to the Rust engine. Included templates are now correctly resolved from configured template paths. ([#55](https://github.com/djust-org/djust/pull/55))
- **`urlencode` Filter**: Added the `urlencode` filter for URL-safe encoding of strings. Supports encoding all characters or preserving safe characters. ([#55](https://github.com/djust-org/djust/pull/55))
- **Comparison Operators in `{% if %}` Tags**: Added support for `>`, `<`, `>=`, `<=` comparison operators in conditional expressions. ([#55](https://github.com/djust-org/djust/pull/55))
- **Auto-serialization for Django Types**: Context variables with Django types (datetime, date, time, Decimal, UUID, FieldFile) are now automatically serialized for Rust rendering. No manual JSON conversion required. ([#55](https://github.com/djust-org/djust/pull/55))
- **Lazy Hydration**: LiveView elements can now defer WebSocket connections until they enter the viewport or receive user interaction. Use `dj-lazy` attribute with modes: `viewport` (default), `click`, `hover`, or `idle`. Reduces memory usage by 20-40% per page for below-fold content. ([#54](https://github.com/djust-org/djust/pull/54))
- **TurboNav Integration**: LiveView now works seamlessly with Turbo-style client-side navigation. WebSocket connections are properly disconnected on navigation and reinitialized when returning to a page. ([#54](https://github.com/djust-org/djust/pull/54))

### Changed

- **AST Optimization**: Template parser now merges adjacent Text nodes during AST optimization, reducing allocations and improving render time by 5-15%. Comment nodes are also removed during optimization as they produce no output. ([#54](https://github.com/djust-org/djust/pull/54))

### Fixed

- **Nested Block Inheritance**: Fixed template inheritance for nested blocks (e.g., `docs_content` inside `content`). Block overrides are now recursively applied to merged content, ensuring deeply nested blocks are correctly resolved. ([#57](https://github.com/djust-org/djust/pull/57))
- **Form Validation First-Click Issue**: Added `parse_html_continue()` function to maintain ID counter continuity across parsing operations. Prevents ID collisions when inserting dynamically generated elements (like validation error messages) that caused first-click validation issues. ([#54](https://github.com/djust-org/djust/pull/54))
- **Whitespace Preservation**: Whitespace is now preserved inside `<pre>`, `<code>`, `<textarea>`, `<script>`, and `<style>` elements during both Rust parsing and client-side DOM patching. ([#54](https://github.com/djust-org/djust/pull/54))

### Security

- **pyo3 Upgrade**: Upgraded pyo3 from 0.22 to 0.24 to address RUSTSEC-2025-0020 (buffer overflow vulnerability in `PyString::from_object`). ([#55](https://github.com/djust-org/djust/pull/55))

## [0.1.5] - 2026-01-23

### Added

- **Context Processor Support**: LiveView now automatically applies Django context processors configured in `DjustTemplateBackend`. Variables like `GOOGLE_ANALYTICS_ID`, `user`, `messages`, etc. are now available in LiveView templates without manual passing. ([#26](https://github.com/djust-org/djust/pull/26))

### Fixed

- **VDOM Cache Key Path Awareness**: Cache keys now include URL path and query string hash, preventing render corruption when navigating between views with different template structures (e.g., `/emails/` vs `/emails/?sender=1`). ([#24](https://github.com/djust-org/djust/pull/24))

## [0.1.4] - 2026-01-22

### Added

- Initial public release
- LiveView reactive server-side rendering
- Rust-powered VDOM engine (10-100x faster than Django templates)
- WebSocket support for real-time updates
- 40+ UI components (Bootstrap 5 and Tailwind CSS)
- State management decorators (`@state`, `@computed`, `@debounce`, `@optimistic`)
- Form handling with real-time validation
- Testing utilities (`LiveViewTestClient`, snapshot testing)

## [0.1.3] - 2026-01-22

### Fixed

- Bug fixes and stability improvements

[Unreleased]: https://github.com/djust-org/djust/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/djust-org/djust/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/djust-org/djust/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/djust-org/djust/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/djust-org/djust/compare/v0.2.0a2...v0.2.0
[0.2.0a2]: https://github.com/djust-org/djust/compare/v0.2.0a1...v0.2.0a2
[0.2.0a1]: https://github.com/djust-org/djust/compare/v0.1.8...v0.2.0a1
[0.1.8]: https://github.com/djust-org/djust/compare/v0.1.7...v0.1.8
[0.1.7]: https://github.com/djust-org/djust/compare/v0.1.6...v0.1.7
[0.1.6]: https://github.com/djust-org/djust/compare/v0.1.5...v0.1.6
[0.1.5]: https://github.com/djust-org/djust/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/djust-org/djust/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/djust-org/djust/releases/tag/v0.1.3
