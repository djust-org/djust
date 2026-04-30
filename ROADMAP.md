ca# djust Roadmap

> Current version: **0.3.8rc1** (Alpha) — Last updated: March 18, 2026 (roadmap refresh: Phoenix LV 1.1 parity audit, React 19.2 patterns, v0.5.0 scope split into v0.5.0/v0.5.1, new features: `handle_async`, declarative assigns, `used_input?`, programmable JS hook commands, server actions, keep-alive/activity, keyed for-loop change tracking, type-safe template validation, streaming markdown renderer, `self.defer()`, document metadata, temporary assigns, `djust_gen_live` scaffolding, transition/priority updates, suspense boundaries, named slots with attributes, static asset tracking, database change notifications, WebSocket compression, `dj-scroll-into-view`, multi-step wizard, virtual/windowed lists, `dj-paste`, runtime layout switching, i18n live switching)

This roadmap outlines what has been built, what is actively being worked on, and where djust is headed. Priorities are shaped by real-world usage across [djust.org](https://djust.org) and [djustlive](https://djustlive.com), and by feature parity goals with Phoenix LiveView 1.0 and React 19-level interactivity.

### Priority Matrix — What Moves the Needle Most

| Priority | Feature | Why | Milestone |
|----------|---------|-----|-----------|
| ~~**P0**~~ | ~~VDOM structural patching (#559)~~ ✅ | ~~Blocks all conditional rendering — every new user hits this~~ | v0.4.0 |
| ~~**P0**~~ | ~~Focus preservation across re-renders~~ ✅ | ~~Forms feel broken without it — table-stakes for any interactive framework~~ | v0.4.0 |
| ~~**P0**~~ | ~~Event sequencing (#560)~~ ✅ | ~~User events silently dropped during ticks — trust-destroying~~ | v0.4.0 |
| ~~**P0**~~ | ~~`dj-value-*` static event params~~ ✅ | ~~Most underrated Phoenix feature; used on virtually every event binding~~ | v0.4.0 |
| ~~**P0**~~ | ~~`handle_params` callback (complete)~~ ✅ | ~~`live_patch` is half-implemented without it — partial impl exists, needs finish~~ | v0.4.0 |
| ~~**P1**~~ | ~~JS Commands (`dj.push`, `dj.show`, etc.)~~ ✅ Shipped — `static/djust/src/26-js-commands.js` (fluent chain API) + `27-exec-listener.js` | ~~Biggest DX gap vs Phoenix; eliminates server round-trip for UI interactions~~ | ~~v0.4.1~~ |
| ~~**P1**~~ | ~~Flash messages (`put_flash`)~~ ✅ Shipped — `FlashMixin` (live_view.py:41,142) + `static/djust/src/23-flash.js` | ~~Every app reinvents this; 40 lines to eliminate universal boilerplate~~ | ~~v0.4.0~~ |
| ~~**P1**~~ | ~~`on_mount` hooks~~ ✅ Shipped — `python/djust/hooks.py` + `live_view.py` integration | ~~Cross-cutting auth/telemetry without copy-pasting into every mount()~~ | ~~v0.4.0~~ |
| ~~**P1**~~ | ~~Function Components (stateless)~~ ✅ Shipped — `python/djust/components/function_component.py` (`@component` decorator + `{% call %}` tag) | ~~Cheap render-only components without WS overhead — Phoenix.Component parity~~ | ~~v0.5.0~~ |
| ~~**P1**~~ | ~~`assign_async` / AsyncResult~~ ✅ Shipped — `python/djust/async_result.py` + `mixins/async_work.py` (`assign_async()` method) | ~~Foundation for responsive dashboards — independent loading boundaries~~ | ~~v0.5.0~~ |
| ~~**P1**~~ | ~~Template fragments (static subtree)~~ ✅ Shipped — `crates/djust_live/src/lib.rs` `clear_fragment_cache` + `build_fragment_text_map` (Rust-side static subtree fingerprinting) | ~~Biggest wire-size optimization; how Phoenix achieves sub-ms updates~~ | ~~v0.5.0~~ |
| ~~**P1**~~ | ~~LiveView testing utilities~~ ✅ Shipped in v0.5.1 (7 methods + 21 tests) | ~~`assert_push_event()`, `assert_patch()`, `render_async()` — test DX is adoption-critical~~ | ~~v0.5.0~~ |
| ~~**P1**~~ | ~~Keyed for-loop change tracking~~ ✅ Shipped — `crates/djust_vdom/src/parser.rs` (per-item change detection in `{% for %}` loops via `dj-key`) | ~~O(changed) not O(total) for list re-renders — foundation for large-list performance~~ | ~~v0.5.0~~ |
| ~~**P1**~~ | ~~Temporary assigns~~ ✅ Shipped — `LiveView.temporary_assigns` dict (live_view.py:120,272) + `_reset_temporary_assigns` (live_view.py:818) | ~~Phoenix's #1 memory optimization — without it, large lists (chat, feeds) leak memory unboundedly~~ | ~~v0.5.0~~ |
| **P1** | ✅ `manage.py djust_gen_live` scaffolding | Phoenix's generators are the #1 onboarding DX feature; scaffold views/templates/tests from a model | v0.4.0 |
| **P1** | ✅ Transition/priority updates | React 18/19 `startTransition` concept — mark re-renders as low-priority so user events always win | v0.4.0 |
| ~~**P1**~~ | ~~Suspense boundaries (`{% dj_suspense %}`)~~ ✅ Shipped — `python/djust/components/suspense.py` (`{% dj_suspense await=… %}…{% enddj_suspense %}` with fallback + skeleton support) | ~~Template-level loading boundaries wrapping `assign_async` — React Suspense parity~~ | ~~v0.5.0~~ |
| ~~**P2**~~ | ~~Named slots with attributes~~ ✅ Shipped — `components/function_component.py` + `components/assigns.py` (slot attrs in function components) | ~~Phoenix's `<:slot>` with slot attrs — foundation for composable component libraries~~ | ~~v0.5.0~~ |
| ~~**P2**~~ | ~~Server Actions (`@action` decorator)~~ ✅ Shipped — `python/djust/decorators.py:233` (`@action` with auto-tracked `_action_state[name] = {pending, error, result}`) | ~~React 19 parity; standardized pending/error/success for mutations~~ | ~~v0.8.0~~ |
| ~~**P2**~~ | ~~Async Streams~~ ✅ Shipped — `python/djust/streaming.py` `StreamingMixin` (token-by-token DOM updates via `stream_to(...)` + LLM streaming primitives) | ~~Phoenix 1.0 parity; infinite scroll and real-time feeds at scale~~ | ~~v0.8.0~~ |
| **P2** | Connection multiplexing | Pages with 5+ live sections need this to not waste connections | v0.6.0 |
| **P2** | Dead View / Progressive Enhancement | 1.0 requirement for government/accessibility projects | v1.0.0 |
| **P2** | Accessibility (ARIA/WCAG) | 1.0 requirement; Phoenix was criticized for shipping without this | v1.0.0 |
| ~~**P2**~~ | ~~Type-safe template validation~~ ✅ Shipped in v0.5.1 (`manage.py djust_typecheck`) | ~~Catch template variable typos at CI — unique differentiator vs all competitors~~ | ~~v0.5.1~~ |
| ~~**P2**~~ | ~~Keep-Alive / `dj-activity`~~ ✅ Shipped — `static/djust/src/49-activity.js` + `templatetags/live_tags.py` `{% dj_activity %}` (React 19.2 `<Activity>` parity, server-canonical visibility) | ~~Pre-render hidden routes, preserve state — React 19.2 parity~~ | ~~v0.7.0~~ |
| ~~**P2**~~ | ~~Streaming markdown renderer~~ ✅ Shipped in v0.7.0 (`{% djust_markdown %}` + `djust.render_markdown`, pulldown-cmark backend, provisional-line splitter) | ~~Incremental markdown for LLM output — strongest AI vertical signal~~ | ~~v0.7.0~~ |
| ~~**P1**~~ | ~~Database change notifications (pg_notify)~~ ✅ | ~~PostgreSQL LISTEN/NOTIFY → LiveView push — killer feature for reactive dashboards~~ | v0.5.0 |
| ~~**P1**~~ | ~~Virtual/windowed lists (`dj-virtual`)~~ ✅ | ~~DOM virtualization for 100K+ rows at 60fps — mandatory for data-heavy apps~~ | v0.5.0 |
| ~~**P2**~~ | ~~Multi-step wizard (`WizardMixin`)~~ ✅ Shipped in PR #632 (`python/djust/wizard.py`) | ~~#2 most common UI pattern after CRUD — no framework has this natively~~ | ~~v0.5.1~~ |
| ~~**P2**~~ | ~~Error overlay (dev mode)~~ ✅ Shipped in v0.5.1 (`36-error-overlay.js`) | ~~In-browser error display like Next.js/Vite — faster debugging loop~~ | ~~v0.5.1~~ |
| ~~**P2**~~ | ~~WebSocket compression~~ ✅ Shipped — `config.py:65` `websocket_compression: True` default + `mixins/post_processing.py:245` propagation (`window.DJUST_WS_COMPRESSION` + ASGI server permessage-deflate negotiation) | ~~`permessage-deflate` for 60-80% bandwidth reduction — cheapest optimization available~~ | ~~v0.6.0~~ |
| ~~**P2**~~ | ~~Static asset tracking (`dj-track-static`)~~ ✅ Shipped — `static/djust/src/39-dj-track-static.js` (Phoenix `phx-track-static` parity, stale-on-reconnect prompt) | ~~Detect stale JS/CSS on reconnect, prompt reload — Phoenix `phx-track-static` parity~~ | ~~v0.6.0~~ |
| **P3** | View Transitions API | Cheapest way to make navigation feel native | v0.5.0 |
| **P3** | Islands of interactivity | Content-heavy sites with small interactive zones | v0.7.1 |
| **P3** | Offline mutation queue | Mobile/spotty-connection differentiator | v0.6.0 |
| ~~**P3**~~ | ~~Native `<dialog>` integration~~ ✅ Shipped in v0.5.1 (`dj-dialog="open|close"`, 8 tests) | ~~Browser-native modals with better a11y than custom implementations~~ | ~~v0.5.0~~ |
| ~~**P0**~~ | ~~`push_commands` + `djust:exec` auto-executor~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a) | ~~Foundation primitive for every backend-driven UI feature in ADRs 002-006~~ | v0.4.2 |
| ~~**P0**~~ | ~~`wait_for_event` async primitive~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b) | ~~Lets background handlers pause until real user actions — required for TutorialMixin~~ | v0.4.2 |
| ~~**P0**~~ | ~~`TutorialMixin` + `{% tutorial_bubble %}`~~ ✅ ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c) | ~~Declarative guided tours with zero custom JS — v0.4.2 headline feature~~ | v0.4.2 |
| ~~**P1**~~ | ~~Scaffold `DEBUG=False` default + `.env.example`~~ ✅ (#637) | ~~Security-adjacent carry-over; fails-safe default complements A014 static check~~ | v0.4.2 |
| ~~**P1**~~ | ~~Defer `reinitAfterDOMUpdate` for pre-rendered mount~~ ✅ (#619) | ~~Visible layout-flash bugfix carried over from v0.4.1~~ | v0.4.2 |
| ~~**P3**~~ | ~~Dependabot batch carry-over (v0.4.2)~~ ✅ | ~~Vitest/jsdom/tokio/indexmap/etc. — single "ci: bump deps" PR~~ | v0.4.2 |
| ~~**P1**~~ | ~~Private `_` attributes wiped between WebSocket events (#627)~~ ✅ | ~~Core state management broken — any `_private` attr is lost after each event~~ | v0.4.2 |
| ~~**P1**~~ | ~~Pre-rendered WS reconnect drops `_private` attributes (#611)~~ ✅ | ~~State loss on reconnect after HTTP GET pre-render — related to #627~~ | v0.4.2 |
| ~~**P1**~~ | ~~VDOM patcher calls element methods on text nodes (#622)~~ ✅ | ~~`setAttribute`/`appendChild` crash on `#text` nodes — breaks conditional rendering~~ | v0.4.2 |
| ~~**P1**~~ | ~~`as_live_field()` ignores `widget.attrs` (#683)~~ ✅ | ~~Form fields lose `type`, `placeholder`, `pattern` — forms DX broken~~ | v0.4.2 |
| ~~**P2**~~ | ~~`form.cleaned_data` Python types serialized to null (#628)~~ ✅ | ~~`date`, `Decimal`, `UUID` in cleaned_data become `null` in public state~~ | v0.4.2 |
| ~~**P0**~~ | ~~`{% csrf_token %}` renders `CSRF_TOKEN_NOT_PROVIDED` in Rust engine (#696)~~ ✅ | ~~Poisons client.js CSRF lookup — HTTP fallback always 403~~ | v0.4.3 |
| ~~**P0**~~ | ~~HTTP fallback replaces page with logged-out render (#705)~~ ✅ | ~~`dj-submit`/`dj-click` POST loses session context — page goes blank~~ | v0.4.3 |
| ~~**P1**~~ | ~~WebSocket 404 with django-tenants (#706)~~ | ~~Nginx config issue, not framework bug — closed~~ | v0.4.3 |
| ~~**P1**~~ | ~~Rust engine HTML-escapes content in `<script>` tags (#707)~~ | ~~By design — `\|safe` and `\|json_script` handle this — closed~~ | v0.4.3 |
| ~~**P2**~~ | ~~Wrap HTTP fallback context cleanup in try/finally (#711)~~ ✅ | ~~Tech-debt from PR #710 review — exception safety~~ | v0.4.3 |
| ~~**P2**~~ | ~~Add regression test for HTTP fallback auth (#712)~~ ✅ | ~~Tech-debt from PR #710 review — missing test coverage~~ | v0.4.3 |
| ~~**P2**~~ | ~~Rust renderer: honor Django DATE_FORMAT settings (#713)~~ ✅ | ~~`\|date` filter ignores Django settings~~ | v0.4.3 |
| ~~**P1**~~ | ~~Incremental Rust state sync skips derived context vars (#703)~~ ✅ | ~~Already fixed in 94d37692 + 97f7b7aa — `_collect_sub_ids` cascades detection~~ | v0.4.3 |
| ~~**P1**~~ | ~~Rust `\|date` filter doesn't work on DateField (#719)~~ ✅ | ~~Only works on DateTimeField — NaiveDate fallback added~~ | v0.4.3 |
| ~~**P2**~~ | ~~HTML-escape CSRF token value in renderer.rs (#715)~~ ✅ | ~~Manual escape chain added in PR #721~~ | v0.4.3 |
| ~~**P2**~~ | ~~Log warning for bare `except` in rust_bridge.py (#716)~~ ✅ | ~~Logging with exc_info added in PR #721~~ | v0.4.3 |
| ~~**P2**~~ | ~~Unify GET/POST context processor pattern (#717)~~ ✅ | ~~`_processor_context` context manager in PR #721~~ | v0.4.3 |
| ~~**P2**~~ | ~~Python integration test for DATE_FORMAT injection (#718)~~ ✅ | ~~4 tests in PR #721~~ | v0.4.3 |
| ~~**P3**~~ | ~~Use `filters::html_escape()` for CSRF token (#722)~~ ✅ | ~~Deduplicated in PR #727~~ | v0.4.3 |
| ~~**P3**~~ | ~~Move contextmanager import to module level (#723)~~ ✅ | ~~Fixed in PR #727~~ | v0.4.3 |
| ~~**P3**~~ | ~~Wire `_processor_context` into GET path or fix docstring (#724)~~ ✅ | ~~Docstring fixed in PR #727~~ | v0.4.3 |
| ~~**P3**~~ | ~~Add negative test for `\|date` filter (#725)~~ ✅ | ~~4 negative tests in PR #727~~ | v0.4.3 |
| ~~**P2**~~ | ~~Document `\|date` filter Django compatibility gaps (#726)~~ ✅ | ~~Doc comment added in PR #727~~ | v0.4.3 |
| ~~**P1**~~ | ~~Cache VDOM subtrees for `dj-update="ignore"` sections~~ ✅ | ~~Rust serialize 5.8ms→0.7ms, PR #735~~ | v0.4.5 |
| ~~**P2**~~ | ~~Skip `to_html()` for unchanged VDOM subtrees~~ ✅ | ~~Solved by cached_html in PR #735~~ | v0.4.5 |
| ~~**P2**~~ | ~~Reduce Python→Rust serialization overhead~~ ✅ | ~~Fast path for primitives, PR #736~~ | v0.4.5 |
| ~~**P3**~~ | ~~WebSocket close race on TurboNav (#732)~~ ✅ | ~~Fixed in PR #734~~ | v0.4.5 |
| ~~**P1**~~ | ~~Per-node template dependency map (#737 phase 1)~~ ✅ | ~~Foundation for partial render — compute which context vars each template node uses, PR #738~~ | v0.4.5 |
| ~~**P1**~~ | ~~Changed keys bridge Python→Rust (#737 phase 2)~~ ✅ | ~~Pass _changed_keys to Rust so it knows which context vars changed, PR #738~~ | v0.4.5 |
| ~~**P0**~~ | ~~Partial template render + VDOM splice (#737 phase 3)~~ ✅ | ~~Skip unchanged nodes, parse only changed fragments — template render 1.4ms→0.1ms, PR #738~~ | v0.4.5 |
| ~~**P2**~~ | ~~Lazy context via dependency map (#737 phase 4)~~ ✅ | ~~Investigation: already optimized — incremental sync only sends changed keys, SafeString scan skips unchanged~~ | v0.4.5 |
| ~~**P0**~~ | ~~Extends inheritance resolution caching (#737 phase 3b)~~ ✅ | ~~OnceLock on Template for resolved nodes — extends templates use partial render, Rust 14ms→0.02ms~~ | v0.4.5 |
| ~~**P1**~~ | ~~Text-only VDOM fast path (#737 phase 3b)~~ ✅ | ~~Skip html5ever + diff for text changes — parse 12ms→0.001ms, in-place VDOM mutation~~ | v0.4.5 |
| ~~**P2**~~ | ~~`set()` not JSON-serializable as public state (#626)~~ ✅ | ~~`set` in view state crashes serialization — common Python type~~ | v0.4.2 |
| ~~**P2**~~ | ~~`dict` state deserialized as `list` after Rust sync (#612)~~ ✅ | ~~Round-trip through Rust state sync corrupts dict → list~~ | v0.4.2 |
| ~~**P2**~~ | ~~VDOM patcher should handle `autofocus` on inserted elements (#617)~~ ✅ | ~~Dynamically inserted inputs don't receive focus even with `autofocus` attr~~ | v0.4.2 |
| ~~**P2**~~ | ~~Debug panel SVG attributes double-escaped (#613)~~ ✅ | ~~`viewBox`, `path d` attributes rendered garbled in the debug toolbar~~ | v0.4.2 |
| ~~**P3**~~ | ~~docs: `data-*` attribute naming convention undocumented (#623)~~ ✅ | ~~How `data-foo-bar` maps to `foo_bar` event params — every new user asks~~ | v0.4.2 |
| ~~**P3**~~ | ~~chore: reduce system check noise — T002, V008, C003 (#603)~~ ✅ | ~~Noisy checks on every `manage.py` invocation annoy developers~~ | v0.4.2 |
| ~~**P1**~~ | ~~TutorialMixin `__init__` not called when listed after LiveView (#691)~~ ✅ | ~~Django's `View.__init__` breaks `super()` chain — mixin silently uninitialised~~ | v0.4.2 |
| ~~**P1**~~ | ~~`@background` silently drops `async def` handlers (#692)~~ ✅ | ~~Coroutine returned but never awaited — any async background handler is dead~~ | v0.4.2 |
| ~~**P1**~~ | ~~`push_commands` in `@background` tasks never flush until task ends (#693)~~ ✅ | ~~Push events queue up but don't reach client mid-task — tours show nothing~~ | v0.4.2 |
| ~~**P1**~~ | ~~`get_context_data` includes non-serializable class attrs, corrupting state (#694)~~ ✅ | ~~MRO walker adds class attrs to context; serializer converts to strings~~ | v0.4.2 |
| ~~**P1**~~ | ~~`@background` should natively support `async def` handlers (#697)~~ ✅ | ~~Coroutine detection is a fragile workaround — decorator should handle it properly~~ | v0.4.2 |
| ~~**P2**~~ | ~~`_flush_pending_push_events` callback not wired on WS reconnect (#698)~~ ✅ | ~~Push commands in background tasks may silently queue after reconnect~~ | v0.4.2 |
| ~~**P3**~~ | ~~docs: tutorial bubble must be outside `dj-root` (#699)~~ ✅ | ~~Morphdom recovery wipes bubble if inside LiveView container — undocumented~~ | v0.4.2 |
| ~~**P2**~~ | ~~push_commands-only handlers should auto-skip VDOM re-render (#700)~~ ✅ | ~~Unnecessary re-renders cause patch failures + morphdom recovery during tours~~ | v0.4.2 |
| ~~**P1**~~ | ~~Derived context vars stale under incremental Rust sync (#703)~~ ✅ | ~~`id()` optimization skips sub-objects of mutated dicts — templates render stale data~~ | v0.4.2 |
| **P2** | Fold `djust-auth` + `djust-tenants` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 1) | Eliminate theoretical-audience package fragmentation; extras pattern + compat shim | v0.5.0 |
| ~~**P2**~~ | ~~Fold `djust-theming` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 2)~~ ✅ Shipped in v0.5.0 (PR #772) | ~~Unified CSS/theming story with core; compat shim for plain-Django users~~ | ~~v0.5.1~~ |
| **P2** | Fold `djust-components` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 3) | Largest fold — 64K LOC — dedicated release window in v0.5.2 | v0.5.2 |
| **P3** | Strip `examples/demo_project` to a test harness (move to `tests/test_project/`) | Stops pretending the repo has a demo; real starter is `djust-scaffold`. See `docs/plans/strip-demo-project-to-test-harness.md` | v0.5.2 |
| ~~**P2**~~ | ~~Consolidation sunset — remove compat shims ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 4)~~ ✅ **Shipped v0.6.0 (PR #971)** — Path A (tag-only sunset). All 5 sibling repos tagged `v99.0.0`. djust core ships `djust[auth]` / `djust[tenants]` / `djust[theming]` / `djust[components]` / `djust[admin]` extras. Migration guide at `docs/website/guides/migration-from-standalone-packages.md`. | ~~v0.6.0~~ |
| **P1** | `broadcast_commands` + multi-user sync ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 4) | Instructor → students UI sync in a single primitive; novel for Python frameworks | v0.5.x |
| **P1** | Consent envelope for remote control ([ADR-005](docs/adr/005-consent-envelope-for-remote-control.md)) | Security-critical primitive for support handoffs, accessibility caregivers, AI assist | v0.5.x |
| **P0** | `AssistantMixin` + LLM provider abstraction ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 5, [ADR-003](docs/adr/003-llm-provider-abstraction.md), [ADR-004](docs/adr/004-undo-for-llm-driven-actions.md)) | Voice/chat-driven djust apps; market window is ~12 months; largest revenue angle | v0.5.x |
| **P0** | AI-generated UIs with capture-and-promote ([ADR-006](docs/adr/006-ai-generated-uis-with-capture-and-promote.md)) | "User builds an app with an LLM" — v0.6.0 headline feature; lossless export to Python | **v0.6.1** (deferred from v0.6.0rc1) |
| **P1 ⭐** | **Auto-generated HTTP API from `@event_handler`** ([ADR-008](docs/adr/008-auto-generated-http-api-from-event-handlers.md)) | **v0.5.1 headline feature (pulled forward from v0.7.0).** Opt-in `expose_api=True` turns handlers into `POST /djust/api/<view>/<handler>/` endpoints with OpenAPI schema — unlocks mobile, S2S, CLI, and AI-agent callers without duplicating logic. Transport adapter over the existing handler stack (same coercion, permissions, rate limiter) → manifesto principle #4 preserved. | v0.5.1 |
| **P1** | 3 pre-existing main test failures (#935) | `test_api_response`, `test_observability_eval_handler`, `test_observability_reset_view` — failing on main, surfaced during PR #924 | v0.5.2 |
| **P1** | FormArrayNode drops inner template content (#930) | Latent bug — `{% form_array %}inner{% endform_array %}` silently loses markup | v0.5.2 |
| **P1** | tag_input missing `name=` attribute (#932) | Form submissions silently drop field values | v0.5.2 |
| **P1** | Audit all HttpResponseRedirect sites (#921) | `url_has_allowed_host_and_scheme` coverage — close open-redirect category | v0.5.2 |
| **P2** | Drop redundant `ch == ' '` in sanitize_for_log (#914) | 1-line simplification; ASCII space is printable | v0.5.2 |
| **P2** | gallery/registry.py dead discover_* path (#933) | `get_gallery_data` never consumes discovery results | v0.5.2 |
| **P2** | add javascript: + HTTPS-downgrade + path-traversal edge tests (#922) | Test coverage gaps flagged in PR #920 review | v0.5.2 |
| **P2** | 10 py-format-drift files (#915) | Pre-existing ruff-format drift; bulk reformat | v0.5.2 |
| **P2** | dj-remove teardown dedupe via _teardownState (#900) | Code-quality refactor; Stage 11 nit from PR #898 | v0.5.2 |
| **P2** | dj-remove 2-token-form debug warn (#901) | Silent fall-through on malformed spec; debug-only warn | v0.5.2 |
| **P2** | dj-transition-group reduce 700ms test wallclock (#905) | Override `dj-remove-duration=50` in the integration test | v0.5.2 |
| **P2** | dj-transition-group nested-group regression test (#906) | Verify inner groups install independently | v0.5.2 |
| **P2** | dj-transition parser reject comma/paren separators (#886) | Input validation; avoid silent coercion | v0.5.2 |
| **P2** | dj-transition fallback timer vs detached element (#887) | Timer fires against node already removed from DOM | v0.5.2 |
| **P2** | dj-transition stabilize transitionend-dispatch tests (#888) | 2 tests skipped in PR #885 — fix under vitest parallel load | v0.5.2 |
| **P2** | dj-mutation test for pre-debounce removal (#882) | Assert no CustomEvent fires when element removed before debounce | v0.5.2 |
| **P2** | dj-mutation/sticky-scroll observer misses attr removal (#879) | Root observer doesn't re-scan when attribute removed on kept element | v0.5.2 |
| **P2** | dj-sticky-scroll document scroll-to-bottom install behavior (#881) | Unconditional on install — explicit doc | v0.5.2 |
| **P2** | dj-track-static document Map-vs-WeakMap choice (#880) | `39-dj-track-static.js` — explain non-weak reference | v0.5.2 |
| **P2** | UploadMixin schema-changed saved-configs replay (#892) | Defensive replay when allow_upload kwargs shift between versions | v0.5.2 |
| **P2** | _restore_listen_channels vs _assert_same_loop (#896) | Cross-loop restore interaction — verify no AssertionError | v0.5.2 |
| **P2** | ADR for mixin-side-effect replay pattern (#897) | Document the `_restore_*` pattern formally | v0.5.2 |
| **P2** | CodeQL MaD model for sanitize_for_log (#934) | Teach CodeQL the custom sanitizer — close FP class | v0.5.2 |
| **P2** | Automate CHANGELOG test-count validation (#908) | Pre-commit hook or make target; 3 retros flagged drift | v0.5.2 |
| **P2** | codeql-triage.sh script (#916) | Dump alerts as markdown triage table | v0.5.2 |
| **P2** | Audit open-ended dep ceilings (#910) | `requests>=2.28`, `markdown>=3.0` etc. — add upper bounds | v0.5.2 |
| **P3** | Variable-height virtual-list items via ResizeObserver (#797) | ~200 LOC; extends virtual-list to variable row heights | v0.5.x |
| **P3** | Ship final standalone package compat shims (#778) | djust-auth/tenants/theming/components final PyPI releases | v0.6.0 |
| **P1** | `djust.A010` check recognize proxy-trusted deployments (#890) | AWS ALB / L7-LB deployments need `ALLOWED_HOSTS=['*']`; current check forces silencing workaround | v0.5.7 |
| **P1** | `LiveView.get_state()` filter framework-internal attrs (#762) | ~30 framework attrs leak into state_sizes + reactive-state debug payloads | v0.5.7 |
| **P2** | Pre-signed S3 PUT URLs — client-direct upload (#820) | Bypass djust for large uploads; djust only signs URL + observes completion | v0.5.7 |
| **P2** | Resumable uploads across WS disconnects (#821) | Client-side byte tracking + Redis MPU state; Phoenix 1.0 pattern | v0.5.7 |
| **P2** | First-class GCS + Azure Blob UploadWriter subclasses (#822) | `djust.contrib.uploads.gcs` / `azure`; optional extras | v0.5.7 |
| **P1** | NameError on module load — `DjustFileChangeHandler` references undefined `FileSystemEventHandler` when `watchdog` is not installed (#994) | Breaks `manage.py check` in any production install without the `[dev]` extra — latent since ≥v0.5.4rc1, surfaced v0.7.0rc1 | v0.7.2 |
| **P1** | Rust renderer ignores `__str__` key in serialized model dicts — renders literal `[Object]` (#968) | Asymmetry with Django template semantics: `{{ obj }}` should call `__str__`, the dict already carries `"__str__"` from `_serialize_model_safely`, Rust just doesn't consume it | v0.7.2 |
| **P2** | docs: prominent `key_template` convention for `s3_events` UUID extraction (#964) | Silent `upload_id` fallback when key doesn't match UUID-prefix shape; doc + debug-warn | v0.7.2 |
| **P2** | tooling: weekly real-cloud CI matrix job for S3 / GCS / Azure upload writers (#963) | All v0.5.7 writer tests mock SDKs; weekly happy-path integration run | v0.7.2 |
| **P2** | feat: inline radio buttons in forms (#991) | Segmented controls / filter pills / Yes-No — common LiveView UX; API TBD (form-level flag vs widget attr vs template variant) | v0.7.2 |
| ~~**P2**~~ | ~~policy: decide breaking rename of framework-internal attrs to `_*` prefix (#962)~~ ✅ **Closed without code in v0.7.2** — [ADR-012](docs/adr/012-framework-internal-attrs-filter-vs-rename.md) documents the decision: keep the `_FRAMEWORK_INTERNAL_ATTRS` filter (shipped #762), do NOT rename. Rename would break every user view reading `self.login_required` / `self.template_name` without net defense-in-depth benefit. | ~~v0.7.2~~ |
| **P1** | `djust.C011` doesn't catch stale/placeholder `output.css` (#1003) | `_check_missing_compiled_css` only tests `os.path.exists` — a committed placeholder passes; site serves without Tailwind utilities silently | v0.7.3 |
| **P1** | `djust.A070` false positive on `{% verbatim %}`-wrapped `dj_activity` examples (#1004) | A070 scans template source as raw text and fires on docs/marketing examples wrapped in `{% verbatim %}` | v0.7.3 |
| **P2** | `djust_theming.W001` should only contrast-check the active pack (#1005) | 65+ built-in packs produce hundreds of warnings on every `manage.py check` — bad S/N ratio means real warnings get ignored | v0.7.3 |
| **P2** | py3.14 timing-sensitive CI flake class (#1016) | `test_hotreload_slow_patch_warning` + `test_broadcast_latency_scales[10]` flake on py3.14 only — pick per-runner tolerance / `@flaky(reruns=2)` / non-required matrix slot | v0.7.4 |
| **P2** | docs: `_FRAMEWORK_INTERNAL_ATTRS` PR-checklist reminder (#1017) | ADR-012 mitigation — one bullet in `PULL_REQUEST_CHECKLIST.md` | v0.7.4 |
| **P2** | docs: "misleading existing tests" pattern note (#1018) | One paragraph in `PULL_REQUEST_CHECKLIST.md` — when fixing a check, audit existing tests whose fixtures exemplify the broken behavior | v0.7.4 |
| **P2** | docs: whitespace-preserving redaction pattern in check-authoring guide (#1019) | New section documenting the `_strip_verbatim_blocks` pattern as canonical reference for line-number-aware regex scanners | v0.7.4 |
| **P2** | docs: scope-decision helper extraction pattern in check-authoring guide (#1020) | New section documenting `_contrast_check_scope` / `_presets_to_check` as canonical reference for config-driven check scope | v0.7.4 |
| **P1** | Bisect 6 flaky tests that fail in full pytest run, pass in isolation (#1134) | Every PR pays a ~30s skip-marker tax on full-suite runs; root cause is a polluting test mutating global state (Django settings / Channels registry / Redis mock). Bisect first, fix the polluter — unblocks the pre-push hook for every future PR. | v0.9.1 |
| **P1** | Rust template renderer rejects project-defined `register.filter` (#1121) | Real bug, surfaced post-v0.9.0 — projects that register custom filters via the Django registry don't see them in the Rust path. Asymmetry with the Python engine; same shape as the v0.7.2 `__str__` fix (#968). | v0.9.1 |
| **P2** | A075 system check — sticky+lazy template scan (#1146) | ADR-015 §"Deferred from PR-B". Catch `{% live_render sticky=True lazy=True %}` collision at startup, not template-render time. ~80 LoC + tests. | v0.9.1 |
| **P2** | CSP-nonce-aware activator script for `<dj-lazy-slot>` fills (#1147) | ADR-015 §"Deferred from PR-B". Sites with strict CSP need the framework to thread the request CSP nonce through `live_tags.py` + `50-lazy-fill.js` so inline activators match the document policy. | v0.9.1 |
| **P2** | Rust template engine `{% live_render %}` lazy=True parity (#1145) | Surfaced in PR #1138 integration tests — production users on the Rust path can't use `lazy=True`. Port the Django implementation to a Rust tag handler in `crates/djust_templates/`. | v0.9.1 |
| **P2** | Replay handler argument validation — defense-in-depth (#1148) | PR #1142 follow-up. Augment `replay_event` to validate `event_name` against `view._djust_event_handlers` registry rather than the bare underscore-prefix guard, limiting replay to actual handlers. | v0.9.1 |
| **P2** | Theming cookie namespace to prevent cross-project bleed on localhost (#1158) | Follow-up to closed-as-workaround #1013. Cookies are domain-scoped, not port-scoped — multiple djust projects on `localhost:80xx` share `djust_theme*` cookies and overwrite each other. Add `LIVEVIEW_CONFIG['theme']['cookie_namespace']` setting; namespaced reads/writes with fallback to legacy unprefixed names. | v0.9.1 |
| **P3** | Descriptor-pattern component time-travel verification test (#1150) | PR #1141 Stage 11 deferral. End-to-end test that constructs a view with a class-level `LiveComponent.descriptor()` and asserts capture+restore preserves the component's state. Locks in the `_COMPONENT_INTERNAL_ATTRS` defense layer. | v0.9.1 |
| **P3** | `markdown` package missing from default test env (#1149) | Carryover from v0.8.7 retro. Add to dev-dependencies or mark dependent tests with `pytest.importorskip("markdown")`. | v0.9.1 |
| **P3** | data_table row-level navigation — `row_click_event` / `row_url` (#1111) | Feat slot — common UX pattern for click-to-detail. Decide: handler attribute on `<tr>` vs URL builder, accessibility (Enter/Space, role=button), default-prevent for nested controls. | v0.9.1 |
| **P2** | Pipeline template canonicalization (#1173 + #1174) | Add two-commit shape (impl+tests / docs+CHANGELOG) as a Stage 9 boundary in `.pipeline-templates/feature-state.json` + `.pipeline-templates/bugfix-state.json`; add "3 clean full-suite runs" verification gate in Stage 6 for pollution-class fixes. | v0.9.2 |
| **P2** | CSP-strict defaults canonicalization (#1175) | CLAUDE.md + `docs/PULL_REQUEST_CHECKLIST.md` + `docs/website/guides/security.md` addition documenting "external static JS module + auto-bind on marker class" as the canonical CSP-friendly pattern for new client-side framework code. v1.0 readiness. | v0.9.2 |
| **P2** | Custom filter bridge polish (#1162) | 6 sub-items from PR #1161 Stage 11 review: hot-path Mutex perf via `AtomicBool` short-circuit, hardcoded autoescape consultation, weak negative-case test tightening, drop unused `custom_filter_exists`, fixture isolation, silent async filter handling. All in `crates/djust_templates/`. | v0.9.2 |
| **P3** | Test/dev-env hygiene group (#1160 + #1165) | Tighten `test_redis_serialization_performance` perf bound or soften docstring (#1160). Add `caplog` assertions for #1148 replay rejection logging + descriptor auto-promotion gap doc + `scripts/check-dev-env-imports.py` (#1165). | v0.9.2 |
| **P3** | Tag registry test isolation + sidecar bridge extension (#1167) | Pre-existing test-isolation flake in `tests/unit/test_assign_tag.py` (after `test_tag_registry.py` leaks a `broken` handler) — tighten teardown with autouse fixture. Plus extend `call_handler_with_py_sidecar` pattern to block-tag and assign-tag handlers for symmetry with custom-tag handlers (mechanical follow-up to PR #1166). | v0.9.2 |
| **P3** | Cookie namespace polish (#1169) | 4 sub-items from PR #1168 Stage 11 review: empty-namespaced-cookie defeats fallback (`_read('') or None` masks empty case), no validation on namespace value (whitespace/`=`/`;` produces malformed cookies), no JSDOM test for the WRITE side of `theme.js`, legacy unprefixed cookie persists indefinitely after migration. | v0.9.2 |
| **P3** | data_table row navigation polish (#1171) | 3 sub-items from PR #1170 Stage 11 review: missing `<details>`/`<summary>`/`<option>` from nested-control selector, refactor `window.__djustRowClickNavigate` test-hook into the namespaced exports, add Python-side allowlist regression test. | v0.9.2 |
| **P1** | happy-dom + undici WebSocket unhandled errors in `tests/js/sw_advanced.test.js` (#1186) | Blocks `/djust-release 0.9.0rc3` pre-flight: `make test` exits non-zero with 3 unhandled `WebSocket.dispatchEvent` errors. CI's vitest config silently swallows these; local `make test` surfaces them. All actual tests pass. Filter in vitest.config OR stub WebSocket constructor in test setup. | v0.9.3 |
| **P2** | Vitest unhandled-rejection in `tests/js/view-transitions.test.js` (#1152) | Sibling issue to #1186: non-deterministic `EnvironmentTeardownError` during the test's own teardown phase. Same class (test-environment WebSocket / async-callback interop). v0.9.0 retro Action Tracker #178. | v0.9.3 |
| **P2** | `asyncio.as_completed._wait_for_one` warning suppression in `tests/integration/test_chunks_overlap.py` (#1153) | Python-side analog to #1186/#1152: `DeprecationWarning: There is no current event loop` under teardown. Filter locally OR fix `_cancel_pending` lifecycle in `arender_chunks`. v0.9.0 retro Action Tracker #179. | v0.9.3 |

---

## Completed

### Core Framework (Stable)

- Rust-powered template engine (10-100x faster than Django's Python engine)
- Sub-millisecond VDOM diffing with DOM morphing
- WebSocket real-time communication via Django Channels
- HTTP fallback for environments without WebSocket support
- Django Forms integration with real-time field validation
- Two-tier component system (Component + LiveComponent)
- Redis state backend for horizontal scaling
- Hot reload for development (file watcher + WS broadcast)
- Debug panel (`Ctrl+Shift+D`) with event history, VDOM patches, state inspection, network tab
- System checks (`manage.py djust_check`) and security audit (`manage.py djust_audit`)
- All 57 Django built-in template filters supported in Rust engine
- `{% url %}` tag with arguments (including inside `{% for %}` loops)
- MCP server for AI-assisted development
- TurboNav integration with documented contract and guards
- WebSocket security hardening (rate limiting, per-IP connection limits, message size checks, error disclosure prevention)
- Keyed VDOM diff with LIS optimization (`dj-key` / `data-key`), proptest/fuzzing coverage
- `dj-confirm` attribute — browser confirmation dialog before event execution
- JIT serialization for M2M, nested dicts, `@property`
- File modularization: `client.js`, `live_view.py`, `websocket.py`, `state_backend.py`, `template_backend.py` all split into focused modules

### State Management Decorators (Phases 1-5)

All state management features are production-ready:

- `@debounce` — Reduce server requests by waiting for input to settle
- `@throttle` — Rate-limit event handlers with leading/trailing edge control
- `@loading` — Automatic loading states with configurable UI feedback
- `@cache` — Client-side LRU caching with TTL for idempotent operations
- `@client_state` — Reactive state bus for cross-component state sharing
- `@optimistic` — Optimistic UI updates with automatic rollback on error
- `DraftModeMixin` — Draft/discard/publish flow with localStorage persistence

**Result**: 87% code reduction compared to equivalent manual JavaScript.

### Real-Time Collaboration (Phase 6, partial)

- Presence tracking (who's online, idle detection)
- Broadcasting (pub/sub messaging across LiveView instances)
- Live indicators (typing, user count)
- Collaborative notepad example app

### v0.3.0 "Phoenix Rising"

- Progressive Web App support with offline-first implementation, service worker integration, 8 PWA template tags
- Multi-tenant architecture with flexible tenant resolution, automatic data isolation, tenant-aware state backends
- 114 new tests (53 PWA, 61 multi-tenant)

### v0.3.6–v0.3.8rc1

- File uploads over binary WebSocket frames with chunked transfer, drag-and-drop zones, client-side image preview, progress tracking, magic-byte MIME validation, auto-upload, extension/MIME filtering (`UploadMixin`, `allow_upload()`, `consume_uploaded_entries()`)
- Server-Sent Events (SSE) fallback transport — same message interface as WebSocket, works in environments that block WS
- `live_session()` URL routing — groups URL patterns into shared WebSocket connections with route map injection for client-side `live_redirect`
- `StreamingMixin` for token-by-token partial DOM updates (LLM response streaming) with 60fps batching
- `dj-patch` on `<select>` and `<input>` elements — WebSocket `url_change` instead of full page reload
- FormMixin serialization fix for `ModelForm` over WebSocket, `model_pk`/`model_label` for re-hydration
- Debug toolbar: state size breakdown (memory + serialized bytes), TurboNav persistence, search in Network/State tabs
- `dj-hook` re-initialization after VDOM patching
- VDOM version sync improvements, multi-tab cache key fix (`request.path`), canvas `width`/`height` preservation during morph
- `djust-deploy` CLI for deployment automation
- `model.id` returns native type (not string) — breaking change in v0.3.6

---

## In Progress

### Stability & Correctness

Active bugs being fixed before expanding feature scope:

| Issue | Description | Status |
|-------|-------------|--------|
| [#560](https://github.com/johnrtipton/djust/issues/560) | Tick auto-refresh causes VDOM version mismatch, dropping user events | Open |
| [#559](https://github.com/johnrtipton/djust/issues/559) | VDOM patching fails when `{% if %}` blocks add/remove DOM elements | Open |
| [#561](https://github.com/johnrtipton/djust/pull/561) | WS cache key collision, canvas morph clear, dj-patch navigation | PR open |

### Rust Template Engine Parity

Closing the remaining gaps between the Rust engine and Django's Python engine:

| Gap | Impact | Workaround |
|-----|--------|------------|
| Model attribute access (`.field_name`) | High | Convert models to dicts in context |
| `"` not escaped to `&quot;` in attributes | Medium | Use hidden `<pre>` + JS `.textContent` |
| Custom `{% load %}` template tags | Medium | Write raw HTML with correct CSS classes |
| `request.path` not available | Low | Inject via context processor |

---

## Next Up

### Milestone: v0.4.0 — Stability & Core DX (Scope Trimmed)

*Goal:* Make djust reliable enough that developers don't hit surprising breakage in normal use. Fix the sharp edges that make new users bounce. *Scope intentionally trimmed from the previous 28-feature v0.4.0 — ship the must-haves, then iterate. JS Commands moved to v0.4.1.*

#### Critical Bug Fixes

**VDOM structural patching** (#559) ✅ — Fixed in PR #563. Comment node placeholders (`<!--dj-if-->`) are now included in client-side child index resolution (`getSignificantChildren`, `getNodeByPath`, `createNodeFromVNode`), matching the Rust VDOM's index computation. Conditional `{% if %}` blocks no longer break surrounding element patches.

**Event sequencing during ticks** (#560) ✅ — Fixed in PR #566. Render lock serializes tick/event operations; ticks yield to user events; client buffers tick patches during pending event round-trips; monotonic event ref for request/response matching.

**Focus preservation across re-renders** ✅ PR #564 (2026-03-18) — When the VDOM patches the DOM, focused elements lose focus and cursor position. This makes typing in forms feel broken when other parts of the page update. Fix: capture `document.activeElement`, selection range, and scroll position before patching; restore after. *Phoenix preserves focus automatically via `phx-update="ignore"` and morph internals; React preserves it via reconciliation. This is table-stakes for feeling like a real app.*

#### JS Commands (Biggest DX Gap)

**JS Commands (`dj.push`, `dj.show`, `dj.hide`, `dj.toggle`, `dj.addClass`, `dj.removeClass`, `dj.transition`, `dj.dispatch`, `dj.focus`, `dj.set_attr`, `dj.remove_attr`)** — This is the single biggest DX gap vs Phoenix LiveView. Phoenix's `JS` module lets developers chain client-side DOM manipulations that execute instantly without a server round-trip: show/hide modals, toggle classes, add transitions, dispatch custom events — all from template attributes. Currently, djust requires a server round-trip for every UI change, creating perceptible latency for simple interactions like opening a dropdown. Implementation: a `DJ` command builder (Python-side) that serializes to a JSON instruction set executed by the client JS. Commands must survive DOM patches (classes added by `dj.addClass` persist across re-renders).

```python
# Target API (Python-side, used in templates)
from djust import DJ

# In template:
# <button dj-click="{{ DJ.push('toggle_sidebar') | DJ.toggle('#sidebar') | DJ.toggle_class('active', '#btn') }}">
# Executes client-side instantly, THEN pushes event to server
```

#### Quick Wins (High impact, low effort)

**Connection state CSS classes** ✅ — Auto-apply `dj-connected` / `dj-disconnected` CSS classes to the body element based on WebSocket/SSE state. Phoenix does this with `phx-connected`/`phx-disconnected` — trivial to implement, big DX win for showing connection status without custom JS.

**`dj-confirm` attribute** — ✅ Already implemented in `09-event-binding.js`. Native browser confirmation dialog before executing an event.

**`dj-disable-with` attribute** ✅ — Automatically disable submit buttons and replace text during form submission. `<button type="submit" dj-disable-with="Saving...">Save</button>`. Prevents double-submit and provides instant visual feedback. Phoenix's `phx-disable-with` is one of its most-loved small features.

**`dj-key` attribute** — ✅ Already implemented. Keyed VDOM diff with LIS optimization.

**Window/document event scoping** ✅ — `dj-window-keydown`, `dj-window-scroll`, `dj-document-click` attributes for binding events to `window` or `document` rather than the element itself. Phoenix has `phx-window-*`. Essential for keyboard shortcuts, infinite scroll triggers, and click-outside-to-close patterns.

**`dj-debounce` / `dj-throttle` as HTML attributes** ✅ — Currently debounce/throttle only works as Python decorators on event handlers, applying the same delay to every caller. Phoenix allows per-element control: `<input dj-change="search" dj-debounce="300">` vs `<select dj-change="filter" dj-debounce="0">`. This is strictly more flexible — the Python decorator becomes the default, the attribute becomes the override. Implementation: client-side timer per element+event pair, ~50 lines of JS.

**`live_title` & document metadata** ✅ — Update `<title>` and `<meta>` tags from the server without a page reload. Phoenix's `live_title_tag` is trivial but surprisingly impactful — it enables unread counts, status indicators, and notification badges in browser tabs. React 19 went further with native document metadata support (title, link, meta hoisted to `<head>` automatically). API: `self.page_title = "Chat (3 unread)"` and `self.page_meta = {"description": "...", "og:image": "..."}` in any event handler, sent as a lightweight WS message that updates `document.title` and `<meta>` tags without a VDOM diff. The meta tag support is especially valuable for SPAs that need dynamic Open Graph tags for link previews. ~50 lines total.

**`dj-mounted` event** ✅ — Fire a server event when an element enters the DOM (after VDOM patch inserts it). Use cases: scroll-into-view for new chat messages, trigger data loading when a tab becomes active, animate elements on appearance. Phoenix has `phx-mounted`. Pairs naturally with `dj-remove` (exit event). Uses a WeakSet in `bindLiveViewEvents()` to detect newly-added elements after VDOM patches (not initial page load).

**`dj-click-away`** ✅ — Fire an event when the user clicks outside an element. `<div dj-click-away="close_dropdown">`. This is the single most common pattern developers manually implement in every interactive app (dropdowns, modals, popovers). Currently requires `dj-window-click` + manual coordinate checking or a JS hook. One attribute, ~20 lines of JS, eliminates boilerplate in every project.

**`dj-lock` — Prevent concurrent event execution** ✅ — Disable an element until its event handler completes. `<button dj-click="save" dj-lock>Save</button>` prevents double-clicks and concurrent submissions. Different from `dj-disable-with` (which is cosmetic) — `dj-lock` actually blocks the event from firing again until the server acknowledges completion. Phoenix handles this implicitly via its event acknowledgment protocol. Uses `data-djust-locked` marker attribute and `disabled` for form elements or `djust-locked` CSS class for non-form elements. All locked elements unlocked on server response.

**`dj-auto-recover` — Custom reconnection recovery** ✅ — Fires a custom server event on WebSocket reconnect instead of the default form-value replay. `<div dj-auto-recover="restore_state">`. Use case: views with complex state (drag positions, canvas state, multi-step wizard progress) that can't be recovered from form values alone. The handler receives `params` with whatever the client can serialize from the DOM. Phoenix's `phx-auto-recover` solves the same problem — not every reconnection fits the "replay form values" pattern.

**`dj-value-*` — Static event parameters** — Pass static values alongside events without `data-*` attributes or hidden inputs. `<button dj-click="delete" dj-value-id="{{ item.id }}" dj-value-type="soft">Delete</button>` sends `{"id": "42", "type": "soft"}` as params. Phoenix's `phx-value-*` is used everywhere — it's the standard way to pass context with events. Currently djust requires either `data-*` attributes (which the client must extract) or hidden form fields. This is ~20 lines of JS (collect `dj-value-*` attributes on the trigger element and merge into event params) but eliminates boilerplate in every template. *This is arguably the single most underrated Phoenix feature — once developers have it, they use it on every event.*

**`handle_params` callback** ✅ PR #567 (2026-03-18) — Invoked when URL parameters change via `live_patch` or browser navigation. Phoenix's `handle_params/3` is the standard pattern for URL-driven state (pagination, filters, search, tab selection). Currently, `live_patch` updates the URL but there's no server-side callback to react to the change — developers must manually parse `request.GET` in event handlers. API: `def handle_params(self, params, url, **kwargs)` called after `mount()` on initial render and on every subsequent URL change. This enables bookmark-friendly state: users can share URLs like `/dashboard?tab=metrics&range=7d` and the view reconstructs itself from params. ~50 lines Python. *Without this, `live_patch` is only half-implemented — you can push URLs but can't react to them.*

**`dj-shortcut` — Keyboard shortcut binding** ✅ — Declarative keyboard shortcuts on any element. `<div dj-shortcut="ctrl+k:open_search, escape:close_modal">`. Use cases: command palettes (`Ctrl+K`), close modals (`Escape`), save (`Ctrl+S`), undo (`Ctrl+Z`), navigation (`j`/`k` for list items). Currently requires `dj-window-keydown` + manual key checking in Python event handlers — a round-trip for every keypress. `dj-shortcut` handles matching client-side and only fires the event on match. Supports modifier keys (`ctrl`, `shift`, `alt`, `meta`), key combos, and `prevent` modifier to suppress browser defaults (`dj-shortcut="ctrl+s:save" dj-shortcut-prevent`). ~60 lines of JS. *Every productivity app needs keyboard shortcuts. React developers use `react-hotkeys-hook`; this is the built-in equivalent.*

**`dj-copy` — Copy to clipboard** ✅ — Copy text content to clipboard on click without a server round-trip. `<button dj-copy="#code-block">Copy</button>` copies the text content of `#code-block`. `<button dj-copy="literal text here">Copy</button>` copies the literal string. Optionally fires a server event for analytics: `dj-copy="#code-block" dj-copy-event="copied"`. Shows visual feedback (configurable CSS class, default: `dj-copied` for 2s). Use cases: code snippets, share links, API keys, referral codes. Currently requires a `dj-hook` for every copy button. ~30 lines of JS. *This is the kind of small built-in that makes developers think "this framework gets it" — every documentation site, every admin panel needs copy buttons.*

**`dj-cloak` — Prevent flash of unstyled content** ✅ — Elements with `dj-cloak` are hidden (`display: none !important`) until the WebSocket/SSE mount response is received. CSS is injected automatically by client.js. Vue has `v-cloak`, Alpine has `x-cloak` — this is expected in any framework that enhances server-rendered HTML.

**`on_mount` hooks (promoted from v0.6.0)** — Module-level hooks that run on every LiveView mount, declared via `@on_mount` decorator or class attribute. Use cases: authentication checks, telemetry, tenant resolution, feature flags. Phoenix added this in v0.17 and it's now the standard pattern for cross-cutting concerns. Replaces repetitive auth checks in individual `mount()` methods. *Promoted to v0.4.0 because every real app needs cross-cutting mount logic from day one — auth, tenant resolution, telemetry. Without this, developers copy-paste the same 5 lines into every view's `mount()`. Simple to implement (~100 lines Python), massive DX win.*

```python
# Target API
from djust import LiveView, on_mount

@on_mount
def require_auth(view, request, **kwargs):
    if not request.user.is_authenticated:
        return view.redirect('/login/')

class DashboardView(LiveView):
    on_mount = [require_auth]
```

**`_target` param in form change events** ✅ — When multiple fields share one `dj-change="validate"` handler, the `_target` parameter identifies which field triggered the change. Essential for efficient per-field validation without needing separate handlers per field. The client includes the triggering element's `name` (or `id`, or `null`) as `_target` in the event params for `dj-change`, `dj-input`, and `dj-submit` (submitter button name). Matches Phoenix LiveView's `_target` convention.

**`dj-scroll-into-view` — Auto-scroll to element on render** ✅ — Elements with `dj-scroll-into-view` are automatically scrolled into view after DOM updates (mount, VDOM patch). Supports scroll behavior via attribute value: `""` (smooth/nearest), `"instant"`, `"center"`, `"start"`, `"end"`. One-shot per DOM node via WeakSet tracking. VDOM-replaced fresh nodes scroll again correctly. Use cases: chat messages, form validation errors, notification toasts.

**`dj-page-loading` — Navigation loading bar** ✅ — NProgress-style thin loading bar at the top of the page during TurboNav and `live_redirect` navigation. Always active by default. Exposed as `window.djust.pageLoading` with `start()`, `finish()`, and `enabled` for manual control. Disable via `window.djust.pageLoading.enabled = false` or CSS override.

**Flash messages (promoted from v0.5.0)** — Built-in ephemeral notification pattern with `self.put_flash(level, message)` and auto-dismissing client-side rendering. Phoenix's `put_flash` is used in virtually every app. *Promoted to v0.4.0 because this is the #1 pattern developers reinvent in every project. A `FlashMixin` with `put_flash('info', 'Saved!')`, a `{% dj_flash %}` template tag, and ~40 lines of client JS for appear/auto-dismiss animations. Flash messages survive `live_patch` but clear on `live_redirect`. Without this, every djust app ships with a slightly different homegrown toast system.*

#### Transition / Priority Updates (React 18/19 `startTransition` concept)

**✅ Priority-aware event queue** *(completed v0.4.0)* — Server-initiated broadcasts (`server_push`) and async completions (`_run_async_work`) are now tagged with `source="broadcast"` and `source="async"` respectively, and the client buffers them during pending user event round-trips (same as tick buffering from #560). `server_push` acquires the render lock and yields to in-progress user events to prevent version interleaving. Client-side pending event tracking upgraded from single ref to `Set`-based tracking, supporting multiple concurrent pending events. Buffer flushes only when all pending events resolve.

#### Scaffolding

**✅ `manage.py djust_gen_live` — Model-to-LiveView scaffolding** *(completed v0.4.0)* — Phoenix's `mix phx.gen.live` is the #1 onboarding accelerator: give it a model and it generates a LiveView, templates, and tests for CRUD operations in seconds. djust has the MCP server for AI-assisted scaffolding, but a CLI command is essential for developers who aren't using AI tools. `manage.py djust_gen_live posts Post title:string body:text published:boolean` generates: (1) a LiveView class with `mount()`, `handle_event()` for create/edit/delete, (2) index/show/form templates with `dj-model` bindings and `dj-submit`, (3) URL patterns via `live_session()`, (4) test file with `LiveViewTestClient` smoke tests. Respects the project's existing patterns (detects whether the project uses function-based or class-based views, which CSS framework, etc.). Optional `--no-tests`, `--api` (JSON responses), `--belongs-to=User` flags. ~400 lines Python management command + Jinja2 templates. *Every framework with fast adoption has a generator: Rails scaffold, Phoenix gen.live, Laravel make:livewire. This is how new developers go from "installed" to "productive" in under 5 minutes. The MCP server is great for AI-assisted dev, but the CLI command is the universal onramp.*

#### Developer Tooling

~~**Error message quality**~~ ✅ — VDOM patch errors now include patch type, `dj-id`, parent element info, and suggested causes. WebSocket `send_error` includes `debug_detail`, `traceback`, and `hint` in DEBUG mode. Debug panel intercepts `[LiveView]` warnings and shows a badge.

~~**`manage.py djust_doctor`**~~ ✅ — Single diagnostic command checking 12 items: djust/Python/Django versions, Rust extension, Channels, ASGI, channel layers, Redis, template dirs, Rust render, static files, ASGI server. Supports `--json`, `--quiet`, `--check NAME`, `--verbose`.

~~**Latency simulator**~~ ✅ — Debug panel latency controls with presets (Off/50/100/200/500ms), custom value, jitter, localStorage persistence. Injected on both WebSocket send and receive. Badge on debug button shows active latency.

**Profile & improve performance** — *Moved forward to v0.6.0* — Full-request-path profiling with explicit targets was parked while v0.4.5 delivered the concrete Rust-side render-perf wins (phases 1-4 of #737). Revisit now that there's a stable baseline to measure against.

#### Reconnection Resilience

~~**Form recovery on reconnect**~~ ✅ — After WebSocket reconnects, the client auto-fires `dj-change` with current DOM form values to restore server state. Compares DOM values against server-rendered defaults, skips unchanged fields. Supports `dj-no-recover` opt-out and defers to `dj-auto-recover` containers.

~~**Reconnection backoff with jitter**~~ ✅ — Exponential backoff with random jitter (AWS full-jitter strategy). Min 500ms, max 30s, 10 attempts. Reconnection banner with attempt count, `data-dj-reconnect-attempt` attribute and `--dj-reconnect-attempt` CSS custom property on `<body>`.

### Milestone: v0.4.1 — Security Hardening, JS Commands & Interaction Polish

*Goal:* Close the biggest DX gap vs Phoenix (JS Commands), ship the remaining quick wins that didn't fit in v0.4.0's bug-fix focus, and fix security findings from the 2026-04-10 penetration test (#653, #654, #655, plus the #657/#659/#660/#661 audit-enhancement batch).

*Status (2026-04-11):* Security hardening batch complete (#653/#654/#655 shipped). Audit-enhancement batch complete (#657/#659/#660/#661 all shipped). `{% live_input %}` (#650) shipped. `dj-paste` shipped (PR #671). JS Commands (P1) shipped (PR #672) — full 11-command suite, template/hook/JS/attribute entry points, scoped targets (`to`/`inner`/`closest`), immutable chains, `push` with `page_loading`. v0.4.1 is feature-complete; only any v0.4.0 leftover quick wins remain before release.

#### Security hardening (from pentest findings 2026-04-10)

**✅ Reject cross-origin WebSocket connections by default (#653, CSWSH)** — Shipped as PR #658 (merged 2026-04-10). ⚠️ **High priority.** `djust.websocket.LiveViewConsumer.connect()` calls `self.accept()` without validating the `Origin` header, and no djust helper (`DjustMiddlewareStack`, `live_session`, the scaffold `asgi.py` template) wraps the router in `channels.security.websocket.AllowedHostsOriginValidator`. Every djust application is vulnerable to Cross-Site WebSocket Hijacking by default — any page on the internet can mount a LiveView in a victim's browser, dispatch events, and read VDOM patches back. Demonstrated against a live deployment via `websockets.connect(TARGET, origin="https://evil.example")`. Three complementary fixes: (1) Add an Origin check to `LiveViewConsumer.connect()` that rejects with `close(code=4403)` when the Origin host is not in `settings.ALLOWED_HOSTS` (empty Origin is allowed to keep curl/test scripts working). (2) Update `DjustMiddlewareStack` in `djust/routing.py` to wrap in `AllowedHostsOriginValidator` by default, with an opt-out kwarg for apps that truly need cross-origin access. (3) Update the `ASGI_PY` template in `djust/scaffolding/templates.py` to include origin validation in generated `asgi.py` files. Release notes must call out the interaction with `ALLOWED_HOSTS = ["*"]` (the validator respects `*` as explicit opt-out). ~40 lines Python. *CWE-346, CWE-942, OWASP WSTG-INPV-11. This is the single highest-impact security fix in v0.4.1.*

**✅ Gate VDOM patch timing/performance metadata behind `DEBUG` (#654)** — Shipped as PR #663 (merged 2026-04-10). Every `patch` response from `LiveViewConsumer` previously included `timing` (handler/render/total ms) and `performance` (full nested timing tree with handler names, phase names, durations, and warnings) — unconditionally, regardless of `settings.DEBUG`. This leaks server-side code path structure to any client including unauthenticated cross-origin attackers (see #653). Enables timing-based code path differentiation (DB hit vs cache miss, valid vs invalid CSRF), internal structure disclosure, and load-based DoS timing. Fix: gate both emissions on `settings.DEBUG or getattr(settings, "DJUST_EXPOSE_TIMING", False)` in `djust/websocket.py` around lines 629-640 and line 719. Keep the existing behavior in debug mode so the browser debug panel still gets its data. Add a test asserting that `timing`/`performance` keys are absent from patch responses when `DEBUG=False`. ~15 lines Python + test. *Medium severity alone, but paired with #653 this becomes a real reconnaissance primitive.*

**✅ Nonce-based CSP support — drop `'unsafe-inline'` from `script-src` / `style-src` (#655)** — Shipped as PR #664 (merged 2026-04-10). Low priority enhancement. djust apps currently must allow `'unsafe-inline'` in `CSP_SCRIPT_SRC` and `CSP_STYLE_SRC` because djust's client runtime bootstrap and `djust-theming`'s dynamic `<style>` injection don't carry CSP nonces. This negates most of CSP's XSS defense. Three changes: (1) `djust-theming` — inline `<style>` tags emitted for theme variables accept and render a nonce from `request.csp_nonce` when `django-csp` middleware is active. (2) djust client runtime — wherever the bootstrap `<script>` is emitted (likely a `{% djust_client %}` template tag or the theme head), apply the same nonce. (3) Scaffold `settings.py` defaults — once nonces are supported, update the generated CSP settings to use `CSP_INCLUDE_NONCE_IN = ("script-src", "script-src-elem", "style-src", "style-src-elem")` and drop `'unsafe-inline'`. Requires `django-csp>=4.0`. Document the upgrade path for existing apps in release notes. ~30 lines Python across djust + djust-theming + scaffold templates. *Hardening, not a live vulnerability — but closes the biggest remaining CSP gap for djust apps handling sensitive data.*

#### `djust_audit` enhancements (pentest follow-ups)

The same 2026-04-10 pentest that surfaced #653/#654/#655 also surfaced a broader observation: several of the 17 findings would have been catchable at CI time by an enhanced audit tool. Four follow-up issues extend `djust_audit` with new checkers and modes, each filed separately so they can land incrementally. All four share context (pentest source analysis) but no implementation complexity, so they're grouped here but scoped individually.

**✅ Declarative permissions document for `djust_audit` (#657)** — Shipped as PR #665 (merged 2026-04-11). Adds a `--permissions <file>` flag that validates every LiveView against a committed YAML/TOML permissions document. `djust_audit` today can tell "no auth at all" from "some auth is set," but it cannot tell whether `login_required=True` should have been `permission_required("claims.view_supervisor_dashboard")`. The pentest found that every claim detail view in a downstream consumer's app had `login_required=True` and djust_audit reported them all as "protected," but the lowest-privilege authenticated user could still read every claim by ID walk. The fix is to make the expected permission model an **auditable artifact**: a `permissions.yaml` at the project root that lists every view with its expected `public: true` / `roles: [...]` / `permissions: [...]` config, and `djust_audit --permissions permissions.yaml --strict` fails CI on any deviation (undeclared view, mismatched config, or code-level auth that contradicts the document). ~200 lines Python for the parser + validator + diff reporter. *The missing RBAC audit primitive — lets security reviewers sign off on the permission model once and have CI enforce it forever.*

**✅ `djust_audit` — ASGI stack, config, and misc static security checks (#659)** — Shipped as PR #666 (merged 2026-04-11). Seven static check IDs added: A001 (ASGI origin validator), A010/A011/A012 (ALLOWED_HOSTS footguns), A014 (insecure SECRET_KEY), A020 (hardcoded login redirect + multi-group), A030 (admin without brute-force protection). Manifest scanning (k8s/helm/docker-compose) remains out of scope and will land in a follow-up. Four cheap, high-signal static checks added as a batch: (A) ASGI stack validator — parses `asgi.py` to check that the `"websocket"` entry is wrapped in `AllowedHostsOriginValidator` (static-analysis companion to #653 for existing apps not yet rebuilt from the new scaffold). (B) Configuration audit — catches `ALLOWED_HOSTS` footguns, missing `SECURE_PROXY_SSL_HEADER` behind proxies, `DEBUG=True` shipped to prod via `os.environ.get("DEBUG", "True")`, unbounded `CSRF_TRUSTED_ORIGINS`. (C) Misc middleware ordering checks — `SecurityMiddleware` before `CommonMiddleware`, `csp.middleware.CSPMiddleware` present when `CSP_*` settings exist. (D) Recognize djust helper signatures (`djust.routing.live_session`, `DjustMiddlewareStack`) so the ASGI validator handles indirect ASGI app construction. Each check ~15-100 lines Python with essentially zero false-positive risk. *Catches the subset of pentest findings that live in config, not user code.*

**✅ `djust_audit` — AST-based security anti-pattern scanner (#660)** — Shipped as PR #670 (merged 2026-04-10). Seven stable finding codes added under a new `X0xx` prefix so they coexist with the existing `P0xx` permissions-document codes from #657. X001 (IDOR), X002 (unauthenticated state-mutating handler), X003 (SQL string formatting), X004 (open redirect), X005 (unsafe `mark_safe`), X006 (template `|safe`), X007 (template `{% autoescape off %}`). Suppression via `# djust: noqa XNNN` on the offending Python line or `{# djust: noqa XNNN #}` inside templates. New CLI flags: `--ast`, `--ast-path`, `--ast-exclude`, `--ast-no-templates`. Supports `--json` and `--strict`. 52 new tests covering positive + negative cases for every checker, noqa suppression, and management-command integration. ~720 lines Python in `python/djust/audit_ast.py`. Closes the v0.4.1 audit-enhancement batch.

**✅ `djust_audit --live <url>` — runtime security header and WebSocket probe (#661)** — Shipped as PR #667 (merged 2026-04-11). 30 stable finding codes djust.L001–L091 for headers, cookies, path probes, WebSocket CSWSH probe, and connectivity. Zero new runtime dependencies (stdlib urllib + optional websockets package). Add a `--live <url>` mode (or a separate `djust_live_audit` command) that fetches an actual HTTP response from a running deployment and verifies security headers, plus opens a WebSocket handshake with a bogus Origin to verify CSWSH defense end-to-end. This catches the class of issues that **static analysis cannot see** — middleware correctly configured in `settings.py` but the response is stripped, rewritten, or never emitted by the time it reaches the client. The source pentest caught a critical CSP misconfiguration where `django-csp` was correctly configured but the `Content-Security-Policy` header was completely absent from production responses (stripped by an nginx ingress annotation). Validates `Strict-Transport-Security`, `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, and probes `wss://<host>/ws/` with `Origin: https://evil.example` to confirm the server closes the handshake. Modes: basic, `--json --strict` (CI-friendly), `--paths` (multi-URL), `--no-websocket-probe`. ~250 lines Python. *The only way to catch config-drift between source and production. Two-second feedback loop vs waiting for a pentest.*

#### Feature / polish work

**✅ JS Commands (`dj.push`, `dj.show`, `dj.hide`, `dj.toggle`, `dj.addClass`, `dj.removeClass`, `dj.transition`, `dj.dispatch`, `dj.focus`, `dj.set_attr`, `dj.remove_attr`)** — Shipped as PR #672 (merged 2026-04-11). All 11 commands available from four entry points: (1) Python helper `djust.js.JS` fluent chain builder that stringifies to a JSON command list wrapped in `SafeString`; (2) client-side `window.djust.js` mirror with `camelCase` method names; (3) hook API `this.js()` returning a chain bound to the hook element; (4) attribute dispatcher — `dj-click` detects JSON command lists and executes them locally without a server round-trip. 37 Python tests + 30 JS tests. Full guide in `docs/website/guides/js-commands.md`.

**✅ Programmable JS Commands from hooks (Phoenix 1.0 parity)** — Shipped as part of PR #672. Every `dj-hook` instance has a `this.js()` method that returns a fresh `JSChain`; call `.exec(this.el)` to run it against the hook's element.

**✅ `to: {:inner, selector}` and `to: {:closest, selector}` JS Command targets (Phoenix 1.0 parity)** — Shipped as part of PR #672. Every command accepts at most one of `to=` (absolute selector), `inner=` (scoped to origin descendants), `closest=` (walk up from origin). A single `<button dj-click="{{ JS.hide(closest='.modal') }}">Close</button>` works in every modal with no per-instance IDs.

**✅ `page_loading` option on `dj.push` (Phoenix 1.0 parity)** — Shipped as part of PR #672. `JS.push('generate_report', page_loading=True)` triggers `dj-page-loading` elements during the server round-trip.

**✅ `dj-paste` — Paste event handling** — Shipped as PR #671 (merged 2026-04-11). Fires a server event when the user pastes content (text, images, files) into an element. `<textarea dj-paste="handle_paste">`. The client extracts paste payload: plain text via `clipboardData.getData('text/plain')`, rich HTML via `getData('text/html')`, and file metadata via `clipboardData.files`. Sends structured params: `{"text": "...", "html": "...", "has_files": true, "files": [{name, type, size}, ...]}`. When combined with `dj-upload="<slot>"`, clipboard files are auto-routed through the upload pipeline via a new `window.djust.uploads.queueClipboardFiles(element, fileList)` export. Native paste still happens by default; add `dj-paste-suppress` to intercept fully. Participates in `dj-confirm` / `dj-lock`. 11 JS tests. ~80 lines JS. Docs: `docs/website/guides/dj-paste.md`.

**✅ Standalone `{% live_input %}` template tag for non-form state (#650)** — Shipped as PR #668 (merged 2026-04-11). All 10 design points from the PR #652 review delivered: dedicated tag name, explicit `event=` kwarg, single HTML builder path via new `djust._html.build_tag`, field-type registry, `name=` default from handler, CSS class via `config.get_framework_class('field_class')`, full XSS test matrix, `docs/guides/live-input.md` guide, `debounce=`/`throttle=` forwarding, no `data-field_name` (one handler per field). 12 supported field types. `FormMixin.as_live_field()` and `WizardMixin.as_live_field()` render form fields with proper CSS classes and `dj-input`/`dj-change` bindings for views backed by a Django `Form` class. But non-form views — modals, inline panels, settings pages, search boxes, filter bars, toggles, anywhere state lives directly on view attributes — have no equivalent ergonomic helper. Developers write raw `<input class="form-input" dj-input="set_x" value="{{ x }}">` by hand, forget the class, or use inconsistent event bindings. This is the 80% of UI state that doesn't need a full `forms.Form`. *(GitHub issue #650 tracks the user-facing feature request — claim notes panel, reclassification modal, settlement offer modal, and every other inline form in a downstream consumer's app currently uses raw HTML. #650 and the `{% live_input %}` plan below are the same feature from two sides: the user ask and the implementation design.)*

*PR #652 explored an initial implementation by overloading the existing `{% live_field %}` tag with a field-type string as its first argument, dispatching to a standalone path when the first arg is a known type. On review we decided that design has several problems worth fixing before shipping, so that PR is closed and the work will restart cleanly for v0.4.1.*

**Design notes for the clean-slate implementation** (captured from the PR #652 review so we don't re-discover them):

1. **New tag, not an overload.** Use a dedicated `{% live_input %}` (or `{% live_state %}`) tag instead of overloading `{% live_field %}`. The existing `{% live_field %}` stays as the Form-based path that expects `(view, field_name)`. A new tag name makes the call site visually unambiguous at the template and decouples the supported field-type set from argument-dispatch logic — adding a new type is a dict entry, not a change to parsing heuristics.

2. **Explicit event override.** Accept an `event=` kwarg so the caller can opt into `dj-input` (per-keystroke), `dj-change` (blur/selection), or `dj-blur`. Default sensibly per type (`text/textarea` → `dj-input`, `select` → `dj-change`), but never force the caller to bail out of the tag just because they want debounced text or a validate-on-blur select. Pairs naturally with `debounce=`/`throttle=` kwargs that forward to `dj-debounce`/`dj-throttle` attributes already supported in 0.4.0.

3. **Single source of HTML building.** Don't reimplement the escape-by-hand attribute builder. `frameworks.py` has `_build_tag(tag, attrs, content)` which centralises attribute escaping via `django.utils.html.escape`. Either import it directly or promote it to a shared `djust._html` module. Two escape paths is how XSS regressions happen.

4. **Field-type registry, not a hardcoded set.** Define field types as a dict of `{name: render_fn}` so adding a new type (`checkbox`, `number`, `date`, `datetime-local`, `hidden`, `radio`, `range`, `color`) is a one-line registration. Each render function takes `(handler, value, css_class, **kwargs)` and returns an HTML string. First-class types at launch: `text`, `textarea`, `select`, `password`, `email`, `number`, `url`, `tel`, `checkbox`, `radio`, `hidden`. Use cases mapped to types documented in the guide.

5. **Emit `name` attribute by default.** Derive from the handler name or accept an explicit `name=` kwarg. Without a `name`, no-JS form submission doesn't work as a fallback, which is a hidden degradation for users on slow connections / JS failures.

6. **CSS class resolution.** Use `config.get_framework_class("field_class")` (already used by the Form-based path) so Bootstrap/Tailwind/Plain configs are honoured. Fall back to `"form-input"` only if config lookup fails. Narrow the exception catch in the fallback — `except (ImportError, AttributeError)` not bare `Exception`.

7. **XSS test matrix.** Every field type needs a test that injects `<script>alert(1)</script>` into (a) the value, (b) custom kwargs (placeholder, aria-label, title), and (c) choice labels for `select`/`radio`. This is cheap and catches 99% of future regressions.

8. **User-facing documentation.** `docs/website/guides/forms.md` (or a new `guides/state-bound-fields.md`) with a full example showing: a modal with a `{% live_input %}` subject + body + type-select, the corresponding event handlers (`set_subject`, `set_body`, `set_type`), and when to reach for `{% live_input %}` vs `FormMixin` vs `WizardMixin`.

9. **Integration with `dj-debounce`/`dj-throttle` shipped in 0.4.0.** `{% live_input "text" handler="search" debounce="300" %}` should just work by passing `dj-debounce="300"` through.

10. **Conservative decision on `data-field_name`.** The Form-based path emits `data-field_name="..."` so a single validate handler can serve many fields. The standalone path has one handler per field, so `data-field_name` is not strictly needed — but worth documenting the omission so users migrating from `FormMixin` know what changes.

*Ships the ergonomic primitive developers actually want for the 80% of UI state that doesn't need a Django Form — toggles, search inputs, inline editors, modal fields.*

**Remaining v0.4.0 quick wins** — Any items from the v0.4.0 quick wins list that didn't ship in the initial release ship here. (`dj-lock`, `dj-mounted`, `dj-shortcut`, `dj-click-away`, window/document event scoping, connection CSS, `dj-cloak`, `dj-page-loading`, `dj-scroll-into-view`, `dj-copy`, `dj-auto-recover`, `dj-debounce`/`dj-throttle`, and `live_title`/document metadata shipped in v0.4.0.)

### Milestone: v0.4.2 — Backend-Driven UI (Phase 1) & Carry-Over Fixes

*Goal:* Land the MVP of backend-driven UI automation (`push_commands`, `wait_for_event`, `TutorialMixin`) so server-side Python can declaratively drive the browser through guided flows. Fix the open bug backlog: state management bugs (#627, #611), VDOM patcher issues (#622, #617), serialization hardening (#628, #626, #612), forms (#683), debug panel (#613). Clean up docs (#623) and noisy system checks (#603). Ship the dependabot batch and carry-over fixes from v0.4.1.

*Execution order:* The BDUI features shipped first as 1a → 1b → 1c (dependency chain). The remaining bug fixes, docs, and chores are independent — pipeline runners can use `--all --group` to batch related issues (e.g. the serialization cluster #628/#626/#612 ships as one PR, the state management pair #627/#611 ships as one PR).

**✅ `push_commands(chain)` + client-side `djust:exec` auto-executor ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1a)** — Shipped. The foundation primitive for every backend-driven UI feature in ADRs 002-006. Adds `self.push_commands(chain)` as a one-line server-side helper that pushes a `JSChain` (v0.4.1) to the current session for immediate execution. Paired with a new ~40-line `djust:exec` auto-executor (framework-provided, no hook needed) registered automatically on every page — users don't write any client code to consume it. In single-user mode the chain is sent via `push_event("djust:exec", {"ops": chain.ops})` over the current WebSocket; presence-group broadcasting lands later in Phase 4. The client auto-executor calls `window.djust.js._executeOps(ops, null)` from v0.4.1's JS Commands module on every payload. Includes: 1 Python module (`djust/server_driven/mixin.py`), 1 JS module (`python/djust/static/djust/src/27-exec-listener.js`), test harness for push-commands round-trip, and one worked example on djust.org's counter demo ("drive it from the server" button that runs a 5-step narration + highlight tour). Branch: `feat/push-commands-server-driven`. *~20 lines Python + 15 lines JS + ~50 lines tests + 1 docs page. Tiny feature, biggest leverage — unblocks everything else.*

**✅ `wait_for_event` async primitive ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1b)** — Shipped. Depends on Phase 1a. Adds `await self.wait_for_event(name, timeout, predicate)` as an async primitive for pausing a `@background` handler until the user performs a specific action. The handler suspends on an `asyncio.Event` latch registered in the LiveView's event dispatch layer; when an `@event_handler`-decorated method with the matching name is called, the normal handler runs AND the latch resolves with the handler's kwargs. Optional `predicate(kwargs) -> bool` filter lets callers wait for "the user clicks *this specific* button." Optional `timeout` raises `asyncio.TimeoutError` on elapsed. Required by `TutorialMixin` (Phase 1c) for "wait for the user to actually click Next" without polling. Implementation: ~40 lines in `djust/server_driven/waiters.py` + integration with `djust/live_view.py`'s existing event dispatch + ~80 lines tests (happy path, timeout, predicate, cancellation, concurrent waits). Branch: `feat/wait-for-event-async-primitive`. *~40 lines implementation + tests. Small feature but structurally tricky because it touches the event dispatch path.*

**✅ `TutorialMixin` + `TutorialStep` + `{% tutorial_bubble %}` template tag ([ADR-002](docs/adr/002-backend-driven-ui-automation.md) Phase 1c)** — Shipped. Depends on Phases 1a and 1b. The headline feature of v0.4.2. Ships a declarative state machine for guided tours: apps describe the tutorial as a list of `TutorialStep` dataclasses (target selector, message, position, wait_for event name, optional on_enter/on_exit chains, optional auto-advance timeout), mix in `TutorialMixin`, and call `start_tutorial()` from any event handler. The mixin runs the steps in order: for each step it pushes a "highlight + narrate" chain (add class, show bubble, position it, set message text, focus for accessibility), awaits the `wait_for` event (via `wait_for_event`) or sleeps for the step's timeout, then cleans up the highlight and advances. Ships a default `{% tutorial_bubble %}` template tag so users don't have to style their own overlay unless they want to — honours `config.get_framework_class()` for Bootstrap/Tailwind/Plain apps. Includes: `djust/tutorials/mixin.py`, `djust/tutorials/step.py`, `djust/templatetags/djust_tutorials.py`, `~150 lines tests (happy path, skip, cancel, timeout per step, on_enter/on_exit chains), and a djust.org homepage tour demo (7 steps showing features, captured as the first example app). Branch: `feat/tutorial-mixin`. *~200 lines Python + template tag + tests + demo. The v0.4.2 headline — user-facing, marketable, demoable.*

**✅ #637 — Scaffold defaults `DEBUG=False` and generates `.env.example`** — Shipped. Carry-over bugfix from v0.4.1. Independent of the BDUI track; can ship in parallel. Rebased and cleaned up: the original PR bundled scaffold changes with stale `client.js` edits that would regress #625 and stale `debug-panel.js` edits that duplicate #633. Ship only the scaffold slice: `python/djust/scaffolding/generator.py` + `python/djust/scaffolding/templates.py` + new `.env.example` template. Close the original PR #637 as superseded. Fails-safe default (`DEBUG = os.environ.get("DEBUG", "False")...`, `ALLOWED_HOSTS` from env) complements the A014/A001 static checks from #666. Branch: `fix/scaffold-debug-default-637`. *~30 lines Python. 1-2 days.*

**✅ #619 — Defer `reinitAfterDOMUpdate` via `requestAnimationFrame` on pre-rendered mount** — Shipped. Carry-over bugfix from v0.4.1. Independent of the BDUI track; can ship in parallel. Rebase onto current main, edit `python/djust/static/djust/src/03-websocket.js` to wrap the post-mount block in a `requestAnimationFrame` callback (with synchronous fallback when rAF is unavailable for JSDOM tests), preserve the ordering invariant so form recovery still runs after event binding is complete, rebuild `client.js`. Includes 148-line regression test file `tests/js/mount-deferred-reinit.test.js`. Fixes visible layout-flash on pre-rendered HTTP GET content. Branch: `fix/defer-reinit-after-dom-update-619`. *~30 lines JS (source) + rebuild + 148 lines tests. 1-2 days.*

**✅ Dependabot batch carry-over** — Shipped. Independent chore work that was held behind the v0.4.1 release. Ship as a single "ci: bump deps" PR: Vitest 4.1.0 → 4.1.4, `@vitest/ui` + `@vitest/coverage-v8` to match, jsdom 29.0.1 → 29.0.2, happy-dom, tokio 1.50 → 1.51, indexmap 2.13.0 → 2.13.1, proptest, uuid, html5ever, release-drafter, github-script, astral-sh/setup-uv. Full test suite gates the merge. Branch: `chore/dependabot-batch-v042`. *15 deps in one PR. 1 day.*

#### Open issues added to v0.4.2

**✅ #627 — Private `_` attributes wiped between WebSocket events** — Shipped. Root cause: session save used `get_context_data()` output which strips `_`-prefixed attrs. Fix adds `_get_private_state()`/`_restore_private_state()` helpers and wires them into session persistence. 20 new regression tests. Branch: `fix/private-attr-preservation`.

**✅ #611 — Pre-rendered WS reconnect drops `_private` attributes, skipping `mount()`** — Shipped (same PR as #627 — shared root cause). The reconnect path in `RequestMixin._restore_session_state()` now restores private attrs from the `_private_state` session key before the view resumes. Branch: `fix/private-attr-preservation`.

**✅ #622 — VDOM patcher calls element methods on text nodes** — Shipped. The patcher now guards all 5 affected patch types (setAttribute, removeAttribute, appendChild, removeChild, replaceChild) with an `isElement()` check, skipping gracefully on text/comment nodes. Branch: `fix/vdom-patcher-text-nodes-autofocus`.

**✅ #683 — `as_live_field()` ignores `widget.attrs` (type, placeholder, pattern)** — Shipped. `BaseAdapter._merge_widget_attrs()` now merges `field.widget.attrs` into the rendered HTML for all field types (input, textarea, select, checkbox, radio), with djust-specific attributes taking precedence. Branch: `fix/as-live-field-widget-attrs-683`.

~~**#628 — `form.cleaned_data` Python types (date, Decimal) serialized to null**~~ ✅ — Fixed: `DjangoJSONEncoder` and `normalize_django_value()` already handled these types; added 10 regression tests to lock in the behavior. Branch: `fix/serialization-hardening`.

~~**#626 — `set()` not JSON-serializable as public LiveView state**~~ ✅ — Fixed: extended both `DjangoJSONEncoder.default()` and `normalize_django_value()` to serialize `set`/`frozenset` as sorted lists. 11 regression tests. Branch: `fix/serialization-hardening`.

~~**#612 — `dict` state attributes deserialized as `list` after Rust state sync**~~ ✅ — Fixed: replaced `#[serde(untagged)]` derived `Deserialize` on `Value` with a custom visitor-based implementation that uses `visit_map`/`visit_seq` to correctly distinguish maps from arrays in MessagePack. 4 Rust + 1 Python regression tests. Branch: `fix/serialization-hardening`.

**✅ #617 — VDOM patcher should handle `autofocus` on inserted elements** — Shipped. The patcher now detects `autofocus` on newly inserted elements after each patch cycle and calls `.focus()` explicitly. Branch: `fix/vdom-patcher-text-nodes-autofocus`.

**✅ #613 — Debug panel SVG attributes double-escaped** — Shipped. The Rust VDOM's `to_html()` was HTML-escaping text inside `<script>`/`<style>` raw text elements, corrupting JS/CSS code on roundtrip. Fix: `_to_html(in_raw_text)` skips escaping for raw text element children. Branch: `fix/debug-svg-escape-613`.

~~**#623 — docs: `data-*` attribute naming convention for event handler params not documented**~~ ✅ — Documented in Events guide: dash-to-underscore rule, type-hint suffixes, `dj-value-*` alternative, quick-reference table. Shipped in `chore/docs-and-checks-cleanup`.

~~**#603 — chore: reduce system check noise — T002, V008, C003**~~ ✅ — Added `suppress_checks` config key to `DJUST_CONFIG`/`LIVEVIEW_CONFIG`. Accepts short (`"T002"`) or qualified (`"djust.T002"`) IDs, case-insensitive. Only Info-level variants are suppressible. 7 new tests. Shipped in `chore/docs-and-checks-cleanup`.

#### TutorialMixin integration bugs (found during live testing)

~~**#691 — TutorialMixin `__init__` not called when listed after LiveView in MRO**~~ ✅ — Added system check `djust.V010` that detects wrong MRO ordering at startup and emits an Error with a fix hint. Tutorials guide updated with correct ordering. 5 new tests. Shipped in `fix/tutorial-integration-bugs`.

~~**#692 — `@background` decorator silently drops `async def` handlers**~~ ✅ — The coroutine detection in `_run_async_work` (workaround already on main) is the proper fix. 11 new regression tests verify both sync and async handlers execute. Shipped in `fix/tutorial-integration-bugs`.

~~**#693 — `push_commands` inside `@background` tasks never flush until task completes**~~ ✅ — The `_flush_pending_push_events` callback mechanism (workaround already on main) is the proper fix. Added public `await self.flush_push_events()` API on PushEventMixin. 7 new tests. Shipped in `fix/tutorial-integration-bugs`.

~~**#694 — `get_context_data` includes non-serializable class attributes, corrupting state**~~ ✅ — `ContextMixin.get_context_data()` now skips class-level attributes that fail a JSON serialisability probe. `TutorialMixin` stores steps as `_tutorial_steps` with a read-only property. 14 new tests. Shipped in `fix/tutorial-integration-bugs`.

### Milestone: v0.4.3 — HTTP Fallback & Template Engine Fixes

*Goal:* Fix critical bugs found during djustlive.com production deployment that make djust unusable without WebSocket. These are all P0/P1 blockers for any real-world deployment behind proxies, with django-tenants, or where WebSocket connectivity is unreliable.

~~**#696 — `{% csrf_token %}` renders as literal `CSRF_TOKEN_NOT_PROVIDED`**~~ ✅ — Rust engine now renders empty when no token in context; Python injects real token in `_sync_state_to_rust()`; client.js falls through to cookie. Merged as PR #708.

~~**#705 — HTTP fallback POST replaces page with logged-out render**~~ ✅ — Apply `_apply_context_processors()` before `render_with_diff()` in the POST handler so auth context (user, perms, messages) is available during re-render. Merged as PR #710.

~~**#706 — WebSocket 404 with django-tenants**~~ — Closed as nginx configuration issue (not a framework bug). Upgrade headers must be explicitly forwarded by the ingress. Documented in issue comments.

~~**#707 — Rust engine HTML-escapes `<script>` tag content**~~ — Closed as by-design. `|safe` and `|json_script` filters already handle this. Documented in issue comments.

~~**#711 — tech-debt: wrap HTTP fallback context processor cleanup in try/finally**~~ ✅ — Wrapped render_with_diff() in try/finally so cleanup always runs. Merged as PR #714.

~~**#712 — tech-debt: add regression test for authenticated HTTP fallback render**~~ ✅ — 4 new tests: auth POST, anonymous POST, attr cleanup, cleanup-on-error. Merged as PR #714.

~~**#713 — Rust renderer: honor Django DATE_FORMAT/DATETIME_FORMAT settings**~~ ✅ — New `apply_filter_with_context()` checks context for format settings. Python injects Django settings into Rust context. Merged as PR #714.

~~**#703 — Incremental Rust state sync silently skips derived context vars**~~ ✅ — Already fixed in commits `94d37692` and `97f7b7aa` (same day as issue filing). `_collect_sub_ids()` cascades change detection to nested sub-objects. Verified with reproduction script.

~~**#719 — Rust `|date` filter doesn't work on DateField**~~ ✅ — Added NaiveDate fallback parsing in `format_date()` for bare date strings like "2026-03-15". Falls back to midnight UTC. Merged as PR #720.

~~**#715 — HTML-escape CSRF token value in renderer.rs**~~ ✅ — Manual `.replace()` chain for &, ", <, > on the token value. Merged as PR #721.

~~**#716 — Log warning for bare `except` in rust_bridge.py**~~ ✅ — Changed to `logging.warning()` with `exc_info=True`. Merged as PR #721.

~~**#717 — Unify GET/POST context processor application pattern**~~ ✅ — New `_processor_context()` context manager replaces manual try/finally. Merged as PR #721.

~~**#718 — Python integration test for DATE_FORMAT settings injection**~~ ✅ — 4 tests in `test_date_format_injection.py`: injection, TIME_FORMAT, explicit override, no-op without Rust view. Merged as PR #721.

~~**#722 — tech-debt: use `filters::html_escape()` for CSRF token**~~ ✅ — Replaced manual `.replace()` chain with shared `filters::html_escape()`. Merged as PR #727.

~~**#723 — tech-debt: move contextmanager import to module level**~~ ✅ — Moved from class body to module-level import. Merged as PR #727.

~~**#724 — tech-debt: wire `_processor_context` into GET path or fix docstring**~~ ✅ — Fixed docstring to say "POST (HTTP fallback) path". Merged as PR #727.

~~**#725 — tech-debt: add negative test for `|date` filter**~~ ✅ — 4 tests: invalid date, non-date string, empty string, partial date. Merged as PR #727.

~~**#726 — tech-debt: document `|date` filter Django compatibility gaps**~~ ✅ — Doc comment on `format_date()` listing supported vs unsupported input types. Merged as PR #727.

### Milestone: v0.4.5 — Server-Side Render Performance

*Goal:* Reduce server-side render overhead from ~45ms to ~25ms for large pages (304KB HTML, 17 sections). The client side is now optimized (5ms) — the remaining bottleneck is the Rust html5ever parse (19ms), HTML serialization (6ms), and Python overhead (17ms).

~~**Cache VDOM subtrees for `dj-update="ignore"` sections**~~ ✅ — `splice_ignore_subtrees()` reuses old VDOM children for ignored nodes; `cache_ignore_subtree_html()` caches HTML for `to_html()` skip. Rust serialize: 5.8ms → 0.7ms. Merged as PR #735.

~~**Skip `to_html()` serialization for unchanged VDOM subtrees**~~ ✅ — Solved by the `cached_html` field on VNode, populated by `cache_ignore_subtree_html()`. Merged as PR #735.

~~**Reduce Python→Rust serialization overhead**~~ ✅ — Fast path for primitives: skip `_collect_safe_keys()` recursion and `normalize_django_value()` traversal for int/float/bool/None/str. Direct SafeString check for strings. Merged as PR #736.

~~**WebSocket close race on TurboNav (#732)**~~ ✅ — Suppress onerror when `_intentionalDisconnect` is true; don't call `close()` on CONNECTING websockets. Merged as PR #734.

~~**Per-node template dependency map (#737 phase 1)**~~ ✅ — `extract_per_node_deps()` in parser.rs computes `HashSet<String>` per top-level node. Merged as PR #738.

~~**Changed keys bridge Python→Rust (#737 phase 2)**~~ ✅ — `set_changed_keys()` on RustLiveViewBackend, called from `_sync_state_to_rust()`. Merges across multiple calls. Merged as PR #738.

~~**Partial template render + VDOM splice (#737 phase 3)**~~ ✅ — `render_nodes_partial()` skips unchanged nodes, `render_nodes_collecting()` populates cache on first render. Template render 1.4ms→0.1ms. Merged as PR #738.

~~**Lazy context via dependency map (#737 phase 4)**~~ ✅ — Investigation complete: the incremental sync in `_sync_state_to_rust()` already only sends changed keys to Rust (3-layer detection at lines 299-330), and SafeString/normalization scanning only runs on the changed subset. `get_context_data()` is user code that can't be lazily evaluated without API changes. The 20ms Python overhead is dominated by `get_context_data()`, `sync_to_async`, and Django session access — none of which benefit from the dep map. Closed as already optimized.

~~**#758 — eval_handler dry_run misses bulk ORM writes**~~ ✅ **Shipped in v0.4.5 (PR #769)** — `DryRunContext` now patches `QuerySet.update` / `QuerySet.delete` / `bulk_create` / `bulk_update` in addition to `Model.save` / `Model.delete`.

~~**#759 — DryRunContext._uninstall swallows setattr errors**~~ ✅ **Shipped in v0.4.5 (PR #765)** — Restore failures now log at warning level instead of silently continuing with a wrapped `Model.save`.

~~**#760 — observability dry_run tests over-claim what they verify**~~ ✅ **Shipped in v0.4.5 (PR #766)** — Test assertions tightened with explicit mock verification.

~~**#761 — client.js unguarded console.log violates project rule**~~ ✅ **Shipped in v0.4.5 (PR #768)** — All client-side logs now gated on `globalThis.djustDebug` or the `djLog()` helper.

~~**#763 — hot-reload sends 14KB empty-patch message on unrelated file changes**~~ ✅ **Shipped in v0.4.5 (PR #767)** — Empty-patch early-return when the trigger was a file-watch event.

### Milestone: v0.5.0 — Full Package Consolidation

*Goal:* Fold all five runtime packages into `djust` core as optional extras. One install, one version, one CHANGELOG. `pip install djust` stays lean; `pip install djust[all]` gets everything. Revised 2026-04-18 to include all packages in a single milestone (previously split across v0.5.0/v0.5.1/v0.5.2).

**Package consolidation: fold all 5 runtime packages into djust core as extras ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md))** — Move each package's source into `python/djust/<name>/`, add `[project.optional-dependencies]` entries in pyproject.toml, update all internal imports, ship final standalone versions as thin compat shims with `DeprecationWarning`, update downstream consumers (djust.org, djustlive, demo_project). Tests merged into djust's suite with pytest markers.

Execute in order (smallest → largest to amortize risk):

1. **`djust-auth` → `djust[auth]`** (879 LOC, 13 files) — Django-generic auth mixins. Move to `python/djust/auth/`. Extra deps: none beyond Django. Shim: final `djust-auth` release re-exports from `djust.auth` with DeprecationWarning.

2. **`djust-tenants` → `djust[tenants]`** (3,277 LOC, 21 files) — Multi-tenant schema isolation. Move to `python/djust/tenants/`. Sub-extras: `tenants-redis`, `tenants-postgres` for backend-specific deps. Currently has optional djust dep → becomes unconditional once inside core.

3. **`djust-admin` → `djust[admin]`** (3,878 LOC, 23 files) — Admin UI extensions. Move to `python/djust/admin_ext/` (avoid collision with `django.contrib.admin`). Already depends on djust ≥0.3.0rc5.

4. **`djust-theming` → `djust[theming]`** (49,105 LOC, 176 files) — CSS theming engine + design tokens. Move to `python/djust/theming/`. Currently Django-generic (no djust dep) → will gain implicit djust dep once inside core. Extra deps: any theming-specific packages (Sass, etc.).

5. **`djust-components` → `djust[components]`** (99,681 LOC, 371 files) — Pre-built UI component library. Move to `python/djust/components/`. Largest fold (~100K LOC). Already depends on djust ≥0.3.0rc5. May have its own template tags, static assets, management commands — merge carefully. Check for Cargo.toml (Rust components?).

Per-package checklist:
- [ ] Create `python/djust/<name>/` in djust repo
- [ ] Move source files preserving directory structure
- [ ] Update all imports (`from djust_<name>` → `from djust.<name>`)
- [ ] Add `[project.optional-dependencies] <name> = [...]` to pyproject.toml
- [ ] Add `__all__` exports for backward compat
- [ ] Ship final standalone version as compat shim with DeprecationWarning
- [ ] Update downstream: djust.org, djustlive, demo_project
- [ ] Merge test suite (pytest markers: `pytest -m auth`, `pytest -m components`, etc.)
- [ ] CHANGELOG entry
- [ ] Close open issues on old repo

~~**Rust VDOM diff does not detect attribute changes inside `|safe` HTML blobs ([#783](https://github.com/djust-org/djust/issues/783))**~~ ✅ — Originally attributed to PR #779 (container value equality tracking), but the downstream-consumer regression test stayed red after that — PR #779 only fixed the Python-side `id()` comparison. True root cause found 2026-04-20: `crates/djust_templates/src/parser.rs::extract_from_nodes` had no arm for nested `Include` / `CustomTag` / `BlockCustomTag` / `InlineIf` Nodes, so their variable refs (or `"*"` wildcard) never bubbled up to the enclosing `{% if %}` / `{% for %}` / `{% with %}`'s dep set. In a deep wrapper chain (`{% extends %} → {% block %} → {% if %} → {% include %} → {{ field_html.x|safe }}`), changing only a key referenced inside the innermost layer left the wrapper's dep set unintersected with `changed_keys` — partial render reused the cached fragment, text-region fast-path found identical old/new HTML, returned `patches=[]` with `diff_ms: 0`. Fix: propagate `"*"` from nested `Include`/`CustomTag` and extract non-literal vars from `InlineIf`'s three expressions. 7 new regression tests in `tests/test_rust_vdom_safe_diff_783.py`. Fixed a latent sibling bug (`{{ x if cond else y }}` inside `{% for %}`) in the same commit.

**Dep-extractor hardening ([#783](https://github.com/djust-org/djust/issues/783) follow-up — P0)** — The #783 class of bug is structural: `extract_from_nodes` is a ~200-line `match` with a silent `_ => {}` default arm. Any new `Node` variant added to `parser.rs` is automatically dep-less until someone adds an arm, and nothing fails loudly. Three-part hardening pass to turn this silent failure mode into an explicit opt-in:

1. **Unit tests for `extract_per_node_deps`** — new `mod tests` inside `parser.rs` with table-driven assertions: one row per AST shape (`{{ a|f:b }}`, `{% if c %}{% include "x" %}{% endif %}`, `{% for k,v in d.items %}{{ v|safe }}{% endfor %}`, `{% extends %} → {% block %} → {% with %} → {% custom_tag %}`, inline-if inside inline-if, etc.). Asserts expected deps / `"*"` membership. ~80 lines Rust.

2. **Exhaustiveness check across `Node` variants** — a test that instantiates a dummy of every `Node` variant, calls `extract_per_node_deps` on it, and fails if the result is empty UNLESS the variant appears in an explicit `NO_VARS` allow-list (`Text`, `Comment`, `CsrfToken`, `Static`, `TemplateTag`, `Now`, `Extends`, `Load`). Breaks compilation (or the test) the moment someone adds a new variant without touching the extractor. ~40 lines Rust.

3. **Partial-render correctness harness** — pytest helper that renders a template twice (baseline + mutation), then re-runs the mutation path with `node_html_cache` cleared as a control, and asserts the two HTMLs are byte-identical. Catches any dep miss end-to-end regardless of Node type or wrapper depth. Added to `tests/test_rust_vdom_safe_diff_783.py` as a parametrized helper; applied to a matrix of nesting patterns (no wrapper, `if`, `for`, `with`, `block`, nested `extends`, `include`, custom tag). ~120 lines Python.

Rationale: #783 is the *second* time a text-region-fast-path + dep-tracking bug has silently dropped correctness (first was #774, fixed by #779). The fast-path returns `patches=[]` with `diff_ms: 0` when reality is "changes were missed" — indistinguishable from "nothing changed" without a correctness oracle. This harness is the oracle.

~~**`assign_async` / `AsyncResult` (promoted from v0.7.0)**~~ ✅ **Shipped in PR feat/async-rendering-v050** — High-level async data loading inspired by Phoenix's `assign_async` and React's Suspense. Wrap a function in `assign_async()` — the template receives an `AsyncResult` with `.loading`, `.ok`, `.failed` states and renders accordingly. Multiple async assigns load concurrently. Auto-cancels on navigation via `cancel_async("assign_async:<name>")`. Nested async loading within components enables independent loading boundaries (one slow query doesn't block the entire page). *Promoted from v0.7.0 because this is the #1 pattern for building responsive dashboards — every panel loads independently with its own skeleton state. Without this, developers either block the entire mount on the slowest query or manually wire up `start_async` + loading flags for every data source. Phoenix added this in 0.19 and it immediately became the default pattern for all data loading.*

```python
# Target API
class DashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.assign_async('metrics', self._load_metrics)
        self.assign_async('notifications', self._load_notifications)
        # Template renders loading states independently:
        # {% if metrics.loading %}<div class="skeleton">{% endif %}
        # {% if metrics.ok %}{{ metrics.result }}{% endif %}
        # {% if metrics.failed %}Error: {{ metrics.error }}{% endif %}

    async def _load_metrics(self):
        return await expensive_query()
```

~~**Temporary assigns**~~ ✅ **Already shipped in an earlier release; ROADMAP entry was laggy.** Feature lives at `LiveView._initialize_temporary_assigns` / `_reset_temporary_assigns` with exclusion from change tracking in `mixins/rust_bridge.py:519-522`. A dedicated regression test (`tests/unit/test_temporary_assigns.py`) was added in PR feat/async-rendering-v050 — prior coverage was indirect (context processor, on_mount, testing-utils suites). Original ROADMAP description (kept for posterity): Phoenix's most critical memory optimization, completely absent from djust today. `temporary_assigns` resets specified attributes to a default value *after every render*, so the server doesn't hold large collections in memory between events. Without this, a chat app with 10,000 messages keeps all 10,000 in server memory for every connected user — even though only the last 50 are visible. With temporary assigns, the server renders the full list once, sends the diff, then resets `self.messages = []` — the client already has the DOM, the server doesn't need the data anymore. New messages append via streams. API: `temporary_assigns = {'messages': [], 'search_results': []}` class attribute, or `self.temporary_assign('messages', [])` in `mount()`. The render pipeline checks `temporary_assigns` after each render cycle and resets the values. ~60 lines Python. *This is not optional for production apps with large lists. Phoenix has had this since 0.4.0 (2019) and it's used in virtually every app that displays collections. Without it, djust apps will hit memory limits at modest scale. A chat room with 100 concurrent users × 10,000 messages × ~1KB per message = ~1GB of memory just for message state. With temporary assigns: ~0. This is the single highest-ROI feature for production readiness.*

```python
# Target API
class ChatView(LiveView):
    temporary_assigns = {'messages': []}

    def mount(self, request, **kwargs):
        self.messages = Message.objects.order_by('-created')[:50]
        # After first render, self.messages resets to []
        # Client already has the DOM — server doesn't need the data

    def handle_info(self, message):
        if message['type'] == 'new_message':
            self.messages = [message['data']]  # Append one, reset after render
```

~~**Suspense boundaries (`{% dj_suspense %}`)**~~ ✅ **Shipped in PR feat/async-rendering-v050** — Explicit `await="var1,var2"` syntax; fallback renders via Django template loader (or a built-in default skeleton); failed-state renders an error div with an HTML-escaped message; nesting composes naturally. Block handler registered in `python/djust/components/suspense.py`, no Rust changes. Template-level loading boundaries that wrap sections dependent on `assign_async` data. When the async data is loading, the suspense boundary renders a fallback (skeleton, spinner, or custom template). When data arrives, the boundary swaps to the real content with an optional transition. React's `<Suspense>` transformed how developers think about loading states — instead of `{% if data.loading %}` conditionals scattered through templates, you wrap sections declaratively. API: `{% dj_suspense fallback="skeleton.html" %}{{ metrics }}{% enddj_suspense %}` or inline: `{% dj_suspense %}<div class="skeleton h-20">{% enddj_suspense %}...{% enddj_suspense_content %}{{ metrics }}{% enddj_suspense_content %}`. Multiple suspense boundaries on one page load independently — a slow query in one section doesn't block the others. Nested suspense boundaries cascade (inner resolves independently of outer). Implementation: the Rust template engine emits placeholder markers for unresolved `AsyncResult` values; the client swaps them when the server pushes resolved data. ~80 lines Python + ~40 lines JS + Rust template tag. *This is the declarative counterpart to `assign_async` — without it, every async section needs manual `{% if x.loading %}` / `{% if x.ok %}` conditionals, which is verbose and error-prone. React proved that Suspense boundaries are the right abstraction for async rendering.*

```html
<!-- Target API in templates -->
<div class="dashboard">
  {% dj_suspense fallback="components/metric_skeleton.html" %}
    <div class="metric-card">{{ metrics.total_users }}</div>
  {% enddj_suspense %}

  {% dj_suspense fallback="components/chart_skeleton.html" %}
    <canvas dj-hook="Chart" data-values="{{ chart_data }}"></canvas>
  {% enddj_suspense %}
  <!-- Each section loads independently — fast data shows instantly -->
</div>
```

**Named slots with attributes (Phoenix `<:slot>` parity)** — Phoenix's slot system lets parent templates pass named content blocks *with attributes* into components. This is strictly more powerful than Django's `{% block %}` (which has no attributes) or basic `children` passing. Named slots enable composable patterns like tables where the parent defines columns with headers and cell renderers. API: `{% slot header label="Name" sortable=True %}{{ item.name }}{% endslot %}` in the parent, `{% render_slot header %}` in the component template with access to slot attributes via `{{ slot.label }}`. Multiple slots of the same name create a list (essential for table columns, tab panels, accordion sections). Implementation: slots are collected during template parsing and passed as structured data to the component's context. ~120 lines Python + Rust template support. *This is the missing piece for building real component libraries. Without named slots with attributes, components can't express patterns like "here are my columns, each with a header label and a cell renderer" — which is the foundation of every table, tab, and accordion component. Phoenix's slot system is what made HEEx components genuinely composable.*

```python
# Parent template usage:
# {% component "data_table" rows=users %}
#   {% slot col label="Name" sortable=True %}{{ row.name }}{% endslot %}
#   {% slot col label="Email" %}{{ row.email }}{% endslot %}
#   {% slot col label="Role" %}{{ row.get_role_display }}{% endslot %}
#   {% slot empty %}No users found.{% endslot %}
# {% endcomponent %}

# Component template (data_table.html):
# <table>
#   <thead><tr>
#     {% for col in slots.col %}
#       <th {% if col.attrs.sortable %}dj-click="sort" dj-value-field="{{ col.attrs.label }}"{% endif %}>
#         {{ col.attrs.label }}
#       </th>
#     {% endfor %}
#   </tr></thead>
#   <tbody>
#     {% for row in rows %}
#       <tr>{% for col in slots.col %}<td>{% render_slot col %}</td>{% endfor %}</tr>
#     {% empty %}
#       <tr><td colspan="{{ slots.col|length }}">{% render_slot slots.empty.0 %}</td></tr>
#     {% endfor %}
#   </tbody>
# </table>
```


```python
# Component usage in parent template:
{% component "button" variant="primary" class="mt-4" aria-label="Save" data-testid="save-btn" %}
# Component template renders: <button class="btn btn-primary mt-4" aria-label="Save" data-testid="save-btn">
```

**Function Components (stateless render functions)** — Lightweight components that are just a Python function returning HTML, with no WebSocket connection, no state, and no lifecycle. Phoenix's `Phoenix.Component` module (added in 0.18) transformed how people write UIs — most "components" are stateless and don't need the overhead of a LiveComponent. API: `@component` decorator on a function that takes a dict of assigns and returns a string. Callable from templates via `{% call button variant="primary" %}Click me{% endcall %}`. The Rust engine resolves these at render time with zero overhead. ~150 lines Python + Rust template support.

```python
from djust import component

@component
def button(assigns):
    variant = assigns.get('variant', 'default')
    children = assigns['children']
    return f'<button class="btn btn-{variant}">{children}</button>'
```

*This is the missing middle ground between "write raw HTML" and "create a full LiveComponent." 80% of reusable UI pieces (buttons, cards, badges, icons, alert boxes) are stateless. Forcing developers to create a LiveComponent class for a styled button is the kind of friction that makes people reach for React instead. Phoenix learned this lesson and added function components — djust should too.*

**Declarative component assigns (Phoenix 1.0 parity)** — Declare expected assigns with types, defaults, and required/optional status on LiveComponents and function components. Phoenix's `attr :name, :string, required: true` and `slot :inner_block, required: true` macros catch misconfiguration at compile time. djust equivalent: class-level `assigns` declaration validated at mount time, with clear error messages in DEBUG mode. This enables: auto-generated component documentation, IDE autocomplete for component attributes, runtime validation that catches typos early, and automatic type coercion (string → int for numeric assigns). ~120 lines Python.

```python
from djust import LiveComponent, Assign

class Button(LiveComponent):
    assigns = [
        Assign('variant', type=str, default='default', values=['default', 'primary', 'danger']),
        Assign('size', type=str, default='md'),
        Assign('disabled', type=bool, default=False),
    ]
    slots = ['inner_block']  # Required slot (children)
    template_name = 'components/button.html'
```

~~**`JS.ignore_attributes` equivalent (Phoenix 1.1 parity)**~~ ✅ **Shipped in v0.5.0** — `<dialog dj-ignore-attrs="open">` / `<div dj-ignore-attrs="data-lib-state, aria-expanded">`. Comma-separated opt-out list; VDOM `SetAttr` patches for listed keys are skipped. See `python/djust/static/djust/src/31-ignore-attrs.js` + the guard in `12-vdom-patch.js::applySinglePatch` (`case 'SetAttr'`).

~~**Colocated JS hooks with namespacing (Phoenix 1.1 parity)**~~ ✅ **Shipped in v0.5.0** — `{% colocated_hook "Chart" %}...{% endcolocated_hook %}` emits a `<script type="djust/hook" data-hook="Chart">` tag with a `/* COLOCATED HOOK: Chart */` auditor banner; client runtime walks `script[type="djust/hook"]` on init and after each VDOM morph and registers each body as `window.djust.hooks[name]`. Namespacing is opt-in via `DJUST_CONFIG = {"hook_namespacing": "strict"}` (prefixes hook name with `<view_module>.<view_qualname>`); per-tag opt-out with `{% colocated_hook "X" global %}`. See `python/djust/static/djust/src/32-colocated-hooks.js`, `python/djust/templatetags/live_tags.py::ColocatedHookNode`, `docs/website/guides/hooks.md`.

~~**`UploadWriter` — Raw upload byte stream access (Phoenix 1.0 parity)**~~ ✅ **Shipped in v0.5.0** — `djust.uploads.UploadWriter` base class + `BufferedUploadWriter` helper. `allow_upload('avatar', writer=S3Writer)` bypasses disk buffering entirely: writer instance is created lazily on the first chunk with `(upload_id, filename, content_type, expected_size)`, `open()` is called once, `write_chunk(bytes)` for each client chunk, `close() -> Any` on completion (return value stored on `entry.writer_result` and templated as `{{ entry.writer_result }}`), `abort(error: BaseException)` on any failure path (open/write raised, client cancelled, size-limit hit, WS disconnect via `UploadManager.cleanup()`). `BufferedUploadWriter` accumulates raw 64 KB client chunks until `buffer_threshold` (default 5 MB = S3 MPU minimum) then calls `on_part(bytes, part_num)` + `on_complete()`. Legacy disk path untouched when `writer=` is omitted. Documented in `docs/website/guides/uploads.md`.

~~**Rust template engine parity**~~ ✅ **(v0.5.0)** — ~~Close the remaining gaps: model attribute access via PyO3 `getattr` fallback, `&quot;` escaping in attribute context, broader custom tag handler support.~~ Shipped as a single PR: PyO3 `getattr` fallback with PyObject sidecar on `Context` (templates now reference Django models directly), dedicated `html_escape_attr` split with parse-time `in_attr` classification on every `Node::Variable`, and `register_assign_tag_handler()` for context-mutating tags (returns `dict[str, Any]` merged into context). Known limitations left as future work: loader access for block handlers (2b) and parent-tag propagation for nested handlers (2c).

~~**Database change notifications (PostgreSQL LISTEN/NOTIFY → LiveView push)**~~ ✅ **Shipped in v0.5.0** — `python/djust/db/decorators.py`, `python/djust/db/notifications.py`, `python/djust/mixins/notifications.py`. `@notify_on_save` decorator hooks `post_save` / `post_delete` → `pg_notify`; `self.listen(channel)` in `mount()` joins the `djust_db_notify_<channel>` Channels group; `handle_info(message)` receives `{"type": "db_notify", "channel": ..., "payload": ...}`. Process-wide `PostgresNotifyListener` on a dedicated `psycopg.AsyncConnection` (outside Django's pool, auto-reconnect on drop). Channel names strictly validated (`^[a-z_][a-z0-9_]{0,62}$`) — load-bearing because Postgres NOTIFY takes no bind parameters for the channel. `send_pg_notify()` helper for Celery tasks / management commands. See `docs/website/guides/database-notifications.md`.

```python
# Target API
from djust import LiveView
from djust.db import notify_on_save

@notify_on_save  # Auto-sends pg_notify on Order.save()
class Order(models.Model):
    status = models.CharField(max_length=20)

class OrderDashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.orders = list(Order.objects.filter(status='pending'))
        self.listen('orders')  # Subscribe to pg_notify channel

    def handle_info(self, message):
        if message['type'] == 'db_notify':
            self.orders = list(Order.objects.filter(status='pending'))
```

~~**Virtual/windowed lists (`dj-virtual`)**~~ ✅ **Shipped in v0.5.0** — `python/djust/static/djust/src/29-virtual-list.js`. Render only the visible portion of large lists, recycling DOM elements as the user scrolls. `<div dj-virtual="items" dj-virtual-item-height="48" dj-virtual-overscan="5">` renders ~20-30 visible items plus overscan, even if `items` has 10,000 entries. Fixed-height model via `dj-virtual-item-height`; variable-height deferred to v0.5.1. See `docs/website/guides/large-lists.md`.

~~**`dj-viewport-top` / `dj-viewport-bottom` — Bidirectional infinite scroll**~~ ✅ **Shipped in v0.5.0** — `python/djust/static/djust/src/30-infinite-scroll.js` and `stream()` `limit=` kwarg on `StreamsMixin`. Once-per-entry firing semantics matches Phoenix; re-arm via `djust.resetViewport(container)` or by replacing the sentinel child. `stream_prune` op trims children from the opposite edge so chat / feed / log patterns cap DOM growth. See `docs/website/guides/large-lists.md`.

~~**Service worker core improvements**~~ ✅ **Shipped in v0.5.0** — Opt-in SW at `python/djust/static/djust/service-worker.js` registered via `djust.registerServiceWorker({ instantShell: true, reconnectionBridge: true })`. Instant page shell (SW caches first-navigate response split into shell + main; subsequent navigates serve shell immediately and swap `<main>` innerHTML via `X-Djust-Main-Only: 1` header handled by `djust.middleware.DjustMainOnlyMiddleware`). WebSocket reconnection bridge (client wraps `sendMessage` to `postMessage` buffered payloads to SW during disconnect, capped at 50/connection; replays via `DJUST_DRAIN` on reconnect). 17 tests (10 JS + 7 Python). See `docs/website/guides/service-worker.md`.

### Milestone: v0.5.1 — HTTP API Headline + Developer Experience, Testing & Form Patterns

*Goal:* Ship the **auto-generated HTTP API from `@event_handler`** as the headline feature (unlocks mobile, S2S, CLI, and AI-agent callers). On the developer-experience side: ship the testing utilities, error overlay, form patterns, and computed state that transform the daily development experience. The DX items were split from v0.5.0 to ship the core async/component primitives faster; the API work was pulled forward from v0.7.0 because its strategic cost — every non-browser consumer of djust apps — is paid on every day it ships late.

**Auto-generated HTTP API from `@event_handler` — P1 HEADLINE ([ADR-008](docs/adr/008-auto-generated-http-api-from-event-handlers.md))** — Opt-in `@event_handler(expose_api=True)` exposes a handler at `POST /djust/api/<view_slug>/<handler_name>/` with an auto-generated OpenAPI schema entry. The handler itself is unchanged — same signature, same `validate_handler_params` coercion, same `@permission_required` / `@rate_limit` stack, same assigns-diff response. This is a **transport adapter**, not a new framework surface: everything security-relevant lives in the existing decorator stack and runs identically regardless of transport. Unlocks four caller classes that cannot reach djust today: (1) **mobile/native clients** that don't hold WebSockets, (2) **server-to-server integrations** and CLI scripts, (3) **cron jobs** firing one-shot actions, and (4) **AI agents** that consume OpenAPI-described tools — direct plug-in for ADR-002/003 AssistantMixin work. Manifesto principle #4 ("One Stack, One Truth") is preserved: no parallel serializer hierarchy, no DRF view classes, no "validation runs in two places" drift. Implementation is ~600-800 LOC Python: a dispatch view (`djust_api_dispatch(request, view_slug, handler_name)`), URL wiring via `djust.urls.api_patterns()`, a pluggable auth hook (default honors existing `login_required` / `permission_required` / `check_view_auth`), an OpenAPI 3.1 generator that walks all `@event_handler(expose_api=True)` sites via the existing `get_handler_signature_info()`, and the `expose_api=True` kwarg plumbing in `@event_handler`. Rate limiting shares the same token bucket as the WS path so a handler cannot be abused by switching transports. Response shape mirrors the WS assigns-diff format — clients with a local state cache can apply patches without a full refetch. Tests include: handler accessible via HTTP with correct permissions, handler NOT accessible when `expose_api=False`, coercion parity between WS and HTTP, rate limit shared, OpenAPI schema validates against the 3.1 spec, and a regression that a handler change only needs to happen in one place to affect both transports.

~~**Transport-conditional API returns (`_api_request` flag + `@api_returns` decorator) — P2 follow-up to ADR-008**~~ ✅ **Shipped in v0.5.1** as `api_response()` convention + `@event_handler(expose_api=True, serialize=...)` override — simpler than the originally-scoped two-decorator form. Three-tier resolution on the HTTP path (zero overhead on WS): per-handler `serialize=` wins when set; otherwise the view's `api_response(self)` runs (DRY convention — one method, many handlers); otherwise the handler return value passes through. `serialize=` accepts a callable (arity-detected) or a method-name string. Async-safe. `self._api_request = True` flag kept as an escape hatch. 22 tests. See `docs/website/guides/http-api.md` under "Transport-conditional returns". (`python/djust/decorators.py`, `python/djust/api/dispatch.py`)

*Why HEADLINE for v0.5.1 (pulled forward from v0.7.0):* ADR-008 is a strategic inflection point. Every LLM-agent platform consumes OpenAPI; every mobile team avoids WebSocket-first frameworks; every S2S integration wants plain HTTP. Shipping this in v0.5.1 makes djust a credible back-end choice for those workloads — not just a reactive-UI framework — a full two minor releases earlier than originally scoped. Cost is low because all security-relevant pieces already exist (decorator metadata, `validate_handler_params`, `@permission_required`, rate-limit buckets, `get_handler_signature_info`). Scoped per ADR-008 §"Decision" + §"Design sketch"; does NOT include streaming responses (HTTP/2 SSE deferred) or the GraphQL-style batching mentioned in ADR-008 §"Out of scope". Server functions (in-browser RPC) stay in v0.7.0 — they reuse the dispatch-view router landing here.

~~**Package consolidation: fold `djust-theming` into core** ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md))~~ ✅ **Shipped in v0.5.0 (PR #772)** — Phase 2 of the three-phase consolidation landed as part of the v0.5.0 "Full Package Consolidation" milestone rather than slipping to v0.5.1. `djust_theming/` (~37.6K LOC) was moved to `python/djust/theming/` with `djust-theming 0.5.0` shipping as a compat shim. Retained in the ROADMAP for historical context; sunset tracked under v0.6.0 Phase 4.

~~**LiveView testing utilities**~~ ✅ **Shipped in v0.5.1** (7 methods + 21 tests in `LiveViewTestClient`). `assert_push_event`, `assert_patch`, `assert_redirect`, `render_async`, `follow_redirect`, `assert_stream_insert`, `trigger_info` all match the v0.5.1 roadmap spec. Full user guide at `docs/website/guides/testing.md`. See also the priority matrix row above.

```python
# Target API
from djust.testing import LiveViewTestClient

async def test_search_with_debounce(self):
    view = await LiveViewTestClient.mount(SearchView, user=self.user)
    await view.type('#search-input', 'django')  # simulates dj-model input
    await view.assert_has_element('.search-results')
    await view.assert_push_event('highlight', {'query': 'django'})
```

~~**Error overlay (development mode)**~~ ✅ **Shipped in v0.5.1** — `36-error-overlay.js` renders a dev-only full-screen panel on `djust:error`. Shows the error message, triggering event, traceback, hint, and validation details. Gated on `window.DEBUG_MODE` so production ships nothing. 10 JSDOM tests. See `docs/website/guides/error-overlay.md`.

~~**`@computed` decorator for derived state**~~ ✅ **Shipped in v0.5.1rc1** (State & computation primitives batch) — `@computed("dep1", "dep2")` memoizes derived values keyed on shallow-fingerprint of listed deps; plain `@computed` retains property semantics. See `python/djust/decorators.py`.

```python
from djust.decorators import computed

class ProductView(LiveView):
    @computed('items', 'tax_rate')
    def total_price(self):
        subtotal = sum(i['price'] * i['qty'] for i in self.items)
        return subtotal * (1 + self.tax_rate)
```

~~**`dj-lazy` — Lazy component loading**~~ ✅ **Lazy LiveView hydration shipped in PR #54** (`python/djust/static/djust/src/13-lazy-hydration.js`). `<div dj-view="..." dj-lazy>` (and `dj-lazy="click|hover|idle"`) defers WebSocket connection + LiveView mount until the element enters the viewport (or the named trigger fires). Note: this covers full LiveView hydration — deferred rendering of *individual LiveComponent instances* within an already-mounted view is a narrower variant that remains unshipped and can be picked up if a user actually needs it. Retained in ROADMAP for completeness.

~~**Component context sharing**~~ ✅ **Shipped in v0.5.1rc1** (State & computation primitives batch) — `self.provide_context(key, value)` / `self.consume_context(key, default)` walk the `_djust_context_parent` chain. Scoped per render tree. See `python/djust/live_view.py`.

~~**`dj-trigger-action` — Bridge live validation to standard form POST**~~ ✅ **Shipped in v0.5.1rc1** (Form & submit polish batch) — `self.trigger_submit("#form-id")` pushes an event that submits the target form's native `.submit()` after validation. Form must opt in via `dj-trigger-action`. See `python/djust/mixins/push_events.py` and `python/djust/static/djust/src/34-form-polish.js`.

~~**Scoped loading states (`dj-loading`)**~~ ✅ **Shipped in v0.5.1rc1** (Form & submit polish batch) — `<div dj-loading="search">` shorthand auto-hides on register and shows only during in-flight `search` events. Coexists with existing `dj-loading.*` modifiers. See `python/djust/static/djust/src/10-loading-states.js`.

~~**Error boundaries**~~ ✅ **Shipped via the v0.5.0 components consolidation (PR #773)** — `python/djust/components/components/error_boundary.py` provides a style-agnostic error boundary for catching rendering errors within a LiveComponent subtree. See the components reference docs for usage.

~~**Nested form handling (`inputs_for`)**~~ ✅ **Shipped in v0.5.1** — `{% inputs_for formset as form %}` block tag in `djust.templatetags.djust_formsets` pairs with `djust.formsets.FormSetHelpersMixin` (and the direct `add_row` / `remove_row` helpers) for add/remove event handlers. Respects `max_num` / `absolute_max` caps; uses Django's standard `DELETE=on` protocol on remove. 16 tests in `python/djust/tests/test_formsets.py`. (commit 335cce26)

~~**Stable component IDs (React 19 `useId` equivalent)**~~ ✅ **Shipped in v0.5.1rc1** (State & computation primitives batch) — `self.unique_id(suffix="")` returns `djust-<viewslug>-<n>[-<suffix>]`, deterministic per logical position, reset at render boundaries. See `python/djust/live_view.py`.

~~**Native `<dialog>` element integration**~~ ✅ **Shipped in v0.5.1** — `dj-dialog="open|close"` attribute, MutationObserver-driven sync, 8 JSDOM tests. See `python/djust/static/djust/src/35-dj-dialog.js`.

~~**Automatic dirty tracking**~~ ✅ **Shipped in v0.5.1rc1** (State & computation primitives batch) — `self.is_dirty` / `self.changed_fields` / `self.mark_clean()` track which public view attrs differ from the post-mount baseline. Respects `static_assigns` and skips private attrs. See `python/djust/live_view.py`.

~~**Type-safe template validation (`manage.py djust_typecheck`)**~~ ✅ **Shipped in v0.5.1** — Python-side static analysis (walks LiveView subclasses, resolves each `template_name`, extracts referenced names via regex + AST extraction of class attrs / `self.x =` assigns / properties / literal `get_context_data` returns). Supports `{# djust_typecheck: noqa name #}` pragma, `strict_context = True` per-view opt-in, `DJUST_TEMPLATE_GLOBALS` setting. Flags: `--json`, `--strict`, `--app`, `--view`. 14 tests. See `docs/website/guides/typecheck.md`. *Chose pure Python regex+AST instead of Rust AST extraction — simpler to iterate and the perf headroom isn't needed for a CI check.*

~~**Multi-step form wizard primitive (`WizardMixin`)**~~ ✅ **Shipped in PR #632** (`python/djust/wizard.py`). Built-in support for multi-step forms (onboarding, checkout, surveys, registration) with step index management, per-step validation, back/forward navigation with state preservation, URL sync via `live_patch`, and `on_wizard_complete(step_data)` callback. API matches the original spec: `current_step`, `step_data`, `next_step()`, `prev_step()`. Retained in ROADMAP for historical context.

```python
# Target API
from djust import LiveView
from djust.wizard import WizardMixin, Step

class OnboardingView(WizardMixin, LiveView):
    steps = [
        Step('account', AccountForm, template='onboarding/account.html'),
        Step('profile', ProfileForm, template='onboarding/profile.html'),
        Step('preferences', PrefsForm, template='onboarding/prefs.html'),
    ]

    def wizard_complete(self, data):
        user = User.objects.create(**data['account'])
        Profile.objects.create(user=user, **data['profile'])
        self.put_flash('success', 'Welcome!')
        self.live_redirect(f'/dashboard/')
```

~~**`dj-no-submit` — Prevent enter-key form submission**~~ ✅ **Shipped in v0.5.1rc1** (Form & submit polish batch) — `<form dj-submit="save" dj-no-submit="enter">`. Document-level keydown listener; textareas, submit buttons, and modified Enter (Shift/Ctrl) unaffected. See `python/djust/static/djust/src/34-form-polish.js`.

### Milestone: v0.5.2 — Demo Harness Cleanup

*Goal:* Originally scoped around the `djust-components` fold, which actually shipped in v0.5.0 alongside auth / tenants / admin / theming (confirmed in the v0.5.0 retrospective). With the headline item retired, v0.5.2 becomes a narrow-scope cleanup release — the demo-project split into test harness + scaffold pointer.

~~**Package consolidation: fold `djust-components` into core ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 3)**~~ ✅ **Shipped in v0.5.0** as part of the "Full Package Consolidation" milestone. All 272 Python files already live under `python/djust/components/` (4.3 MB). The standalone `djust-components` repo continues to exist as a compat shim; its sunset is tracked under v0.6.0 Phase 4 along with auth/tenants/theming.

**Tech-debt drain (28 open issues, P1–P3)** — Overnight drain batch 2026-04-23. Process through pipeline-run grouping where related. Grouped as:

- **Real bugs** (P1): #930 FormArrayNode drops inner content, #932 tag_input missing `name=`, #935 3 pre-existing main test failures.
- **Security audit** (P1–P2): #921 redirect site audit for `url_has_allowed_host_and_scheme`, #922 javascript:/HTTPS-downgrade/path-traversal edge tests.
- **dj-remove / dj-transition / dj-transition-group follow-ups** (P2): #900 teardown dedupe, #901 2-token warn, #886 parser, #887 detached timer, #888 stabilize skipped tests, #905 reduce 700ms wallclock, #906 nested-group test.
- **Other JS observer fixes** (P2): #879 attr-removal miss, #882 dj-mutation pre-debounce, #880/#881 docs.
- **Mixin-replay** (P2): #892 UploadMixin schema change, #896 _restore_listen_channels cross-loop, #897 ADR for replay pattern.
- **Tooling** (P2): #908 CHANGELOG test-count check, #916 codeql-triage.sh, #934 CodeQL MaD for sanitize_for_log.
- **Mechanical cleanup** (P2): #914 redundant char check, #915 10 py-format-drift files, #933 gallery/registry dead path, #910 audit dep ceilings.
- **Larger / deferred** (P3): #797 variable-height virtual-list, #778 standalone package compat shims.

**Strip `examples/demo_project` down to a test harness — P3 (opportunistic)** — The directory currently plays two roles: (1) the pytest/playwright test-harness (settings.py, urls.py, asgi.py — maintained) and (2) ~12 pseudo-demo apps (`demo_app`, `djust_homepage`, `djust_demos`, `djust_forms`, `djust_tests`, `djust_docs`, `djust_rentals`, `djust_shared` — unmaintained, bit-rotting). The real user-facing starter template is the sibling `djust-scaffold` repo. Split the two: move the test-harness to `tests/test_project/`, delete the 12 demo apps, and point users at `djust-scaffold`. Critical-path effort is ~2 hours (dependency audit already done) — 5 real couplings require ports (`test_query_optimizer*.py` needs `djust_rentals` models → move to `tests/test_project/test_rentals/`; `test_demo_views.py` needs inline tenant view; playwright tests need `/tests/loading/`, `/cache/`, `/draft-mode/` routes ported into a minimal `test_playwright_views` app). Also touches `pyproject.toml` `DJANGO_SETTINGS_MODULE`, `Makefile` 8 targets, `.github/workflows/test.yml` playwright job, `tests/conftest.py` sys.path. Full plan with file-by-file audit in `docs/plans/strip-demo-project-to-test-harness.md`. *Benefit is non-mechanical: stops the public repo from shipping a pretend-maintained demo that contradicts the real starter (djust-scaffold). Smaller repo, faster CI checkout, clearer story. One purpose per tree.*

### Milestone: v0.5.7 — Deployment Ergonomics & Upload Feature Family

*Goal:* Clear the narrow-scope feature + bugfix queue that accumulated during the v0.5.6 security arc. Two framework cleanups (ALB-deployment friction, `get_state()` internal-attr leak) plus the three upload-transport features that branched off PR #819's `UploadWriter` work.

**`djust.A010` check — recognize proxy-trusted deployments (#890) — P1** — Current behavior: `A010` raises a hard error whenever `ALLOWED_HOSTS = ['*']` in production. Blocks every AWS ALB / Cloudflare / Fly.io / L7-load-balancer deployment where task private IPs rotate per redeploy/autoscale. The deployer has no enumeration option — the ALB target IP changes constantly. Current user workaround is `SILENCED_SYSTEM_CHECKS = ['djust.A010', 'djust.A011']` in `prod.py`, which works but defeats the check's intent. **Fix**: allow `'*'` in `ALLOWED_HOSTS` when `SECURE_PROXY_SSL_HEADER` is set AND a new `DJUST_TRUSTED_PROXIES` setting is non-empty — the deployer is explicitly asserting a trusted proxy terminates the request. Add a matching hint to A010's message. ~40 LOC in `python/djust/checks.py` + 3 tests (proxy-trusted path, untrusted path still errors, hint text). Real-world evidence: a downstream consumer AWS Fargate + ALB deployment. *v0.5.7 P1 because every production deployer hits this; the silencing workaround is a footgun.*

**`LiveView.get_state()` internal-attr filter (#762) — P1** — ~30 framework-internal attrs (`sync_safe`, `login_required`, `template_name`, `http_method_names`, `on_mount_count`, `page_meta`, `static_assigns_count`, ...) leak into `get_state()` and the `_debug.state_sizes` observability payload. Three consequences: state reasoning is noisier (user's real reactive state is swamped by framework config), `_snapshot_assigns` hashes all of this on every event (minor perf), and the observability debug endpoint payload balloons. **Fix**: non-breaking filter via `_FRAMEWORK_INTERNAL_ATTRS: frozenset[str]` set in `live_view.py`; `get_state()` + `_snapshot_assigns` skip matching keys. Covers Django `View`-inherited attrs too (`http_method_names`, `args`, `kwargs`). No user rename required. ~60 LOC + regression test that `get_state()` on an unmodified LiveView returns `{}` (or just the user's explicit assigns). Defer the breaking-rename to `_*` prefix to v0.7.0 if still wanted. *v0.5.7 P1 because the observability-debug noise directly hurts the MCP browser-tools UX shipping to developers.*

**Pre-signed S3 PUT URLs (#820) — P2** — Complement to PR #819's `UploadWriter`. Instead of `client → djust → S3` (bytes flow through the djust server), sign a pre-signed PUT URL on the server and let the client upload directly to S3. djust's role is only to sign the URL (fast) and observe completion via an S3 event notification. Different threat model — client bytes never touch djust's process — useful when bandwidth to djust is constrained or uploads are >100MB. Deliverables: `djust.contrib.uploads.s3_presigned.PresignedS3Upload` class + `dj-upload-mode="presigned"` client attribute + `on_upload_complete` hook triggered by the S3 event webhook. ~300 LOC + `boto3` as an optional extra (`djust[s3]`). ADR-adjacent to ADR-008. *v0.5.7 P2 because the existing `UploadWriter` covers most use cases; the presigned path is opt-in for high-volume flows.*

**Resumable uploads across WS disconnects (#821) — P2** — Current upload system (including `UploadWriter`) aborts mid-transfer if the WebSocket drops. Add a resumable-upload protocol matching the Phoenix 1.0 pattern: client tracks `bytes_sent`, server stores multipart-upload (MPU) state in Redis or session storage, reconnect resumes from the last completed chunk. Deliverables: new `ResumableUploadWriter` subclass, Redis-backed state store (new `djust.uploads.storage.RedisUploadState`), client-side resume protocol in `client.js`, reconnect handler that queries `GET /djust/uploads/<upload_id>/status` before re-sending. ~500+ LOC. Needs ADR for the wire protocol. *v0.5.7 P2 because long-running mobile uploads hit this constantly; desktop browsers rarely enough.*

**GCS and Azure Blob UploadWriter subclasses (#822) — P2** — Users can already subclass `UploadWriter` for GCS/Azure today, but every user reimplements the same credential-wiring, multipart-upload-state, error-taxonomy boilerplate. Ship `djust.contrib.uploads.gcs.GCSMultipartWriter` + `djust.contrib.uploads.azure.AzureBlockBlobWriter` as first-class subclasses. ~400 LOC total. Optional deps via extras: `djust[gcs]` pulls `google-cloud-storage`; `djust[azure]` pulls `azure-storage-blob`. Consistent error patterns with existing `S3UploadWriter`. *v0.5.7 P2 because GCS + Azure are the 2nd + 3rd most-requested upload backends after S3.*


### Milestone: v0.6.0 — Production Hardening, Interactivity & Generative UIs

*Goal:* Make djust production-ready for teams deploying real apps, close the remaining interactivity gap with client-side frameworks, and ship the capture-and-promote generative UI story as the headline feature.

**Profile & improve performance — P2 (moved from v0.4.0)** — Use existing benchmarks in `tests/benchmarks/` (`test_e2e.py`, `test_serialization.py`, `test_tag_registry.py`, `test_template_render.py`) as baselines. Profile the full request path end-to-end: HTTP render, WebSocket mount, event dispatch, VDOM diff, patch application. Targets: **<2ms per patch**, **<5ms for list updates**. v0.4.5's Rust-side render-partial work (`extract_per_node_deps`, `render_nodes_partial`) gives a stable floor to measure against — but there has been no systematic profile since the WS consumer, streaming, and VDOM features shipped. Deliverables: (1) a reproducible profiling harness (py-spy / cProfile wiring), (2) a written record of current timings for each path segment, (3) a punch-list of hot spots ranked by time saved vs. engineering cost, (4) fixes for anything over the target bounds. Scope does NOT include optimizing paths already within target.

~~**Pre-minified `client.js` distribution — P1**~~ ✅ **Shipped (first v0.6.0 PR)** — `scripts/build-client.sh` now runs terser after the concat step, producing `client.min.js` (~146 KB from 410 KB raw) plus `.gz` (39 KB) and `.br` (33 KB when brotli is installed) pre-compressed siblings. `post_processing.py` serves `client.min.js` by default in production and `client.js` in DEBUG mode for debuggability; an explicit `DJUST_CLIENT_JS_MINIFIED` setting overrides the DEBUG heuristic. Same artifact layout for `debug-panel.js`. Source map emitted alongside. **Wire-size reduction achieved: 88 KB gzipped concat → 33 KB brotli minified (~62%).** Added `terser` as an npm dev-dependency. 6 tests in `tests/unit/test_client_minified.py`. Does NOT include code-splitting / feature toggles (deferred to v0.6.x) or ESM refactor (deferred indefinitely).

~~**Package consolidation sunset ([ADR-007](docs/adr/007-package-taxonomy-and-consolidation.md) Phase 4)**~~ ✅ **Shipped v0.6.0 (PR #971)** — Path A closure. All five sibling repos (`djust-auth`, `djust-tenants`, `djust-theming`, `djust-components`, `djust-admin`) tagged `v99.0.0` as the frozen final release; each ships a shim-only `__init__.py` that re-exports from `djust.<name>` with a `DeprecationWarning`. djust core now exposes the consolidation via `[project.optional-dependencies]`: `djust[auth]`, `djust[tenants]` (with `djust[tenants-redis]` / `djust[tenants-postgres]` backend-specific sub-extras), `djust[theming]`, `djust[components]`, `djust[admin]`. Existing PyPI versions remain installable indefinitely for legacy projects; no new PyPI releases planned. Migration guide at `docs/website/guides/migration-from-standalone-packages.md`. ADR-007 status updated from "Proposed" → "Accepted + Phase 4 complete". Cosmetic tech-debt deferred: the sibling repos retain dead `src/djust_<name>/{mixins,views,urls,...}.py` files next to the shim — cleanup is tracked but not user-facing.

**AI-generated UIs with capture-and-promote ([ADR-006](docs/adr/006-ai-generated-uis-with-capture-and-promote.md))** — **Deferred to v0.6.1.** v0.6.0's scope turned out dominated by animations, sticky LiveViews, service-worker advanced features, and package consolidation closure; the AI-generated UIs headline didn't fit the v0.6.0rc1 cut. See the v0.6.1 milestone entry below for the full description and phased deliverables. "User builds an app with an LLM" remains the natural v0.6.x story and begins with Phase A (`@ai_composable` + `CompositionDocument` + `GenerativeMixin` with ephemeral generation) as a standalone PR.


**Animations & transitions** — *(phases 1 + 2a + 2c + 2d shipped in v0.6.0; milestone complete.)* ~~Declarative `dj-transition` attribute for enter/leave CSS transitions with three-phase class application (start → active → end), matching Phoenix's `JS.transition`.~~ ✅ **Shipped (v0.6.0)** — `41-dj-transition.js`, 7 JSDOM tests, guide updated. ~~`dj-remove` (exit animations before element removal).~~ ✅ **Shipped (v0.6.0)** — `42-dj-remove.js`, hooks into 5 VDOM-patch removal sites, 10 JSDOM tests. ~~`dj-transition-group` (React `<TransitionGroup>` / Vue `<transition-group>` equivalent).~~ ✅ **Shipped (v0.6.0)** — `43-dj-transition-group.js`, 11 JSDOM tests, guide updated. ~~FLIP technique for list reordering, Skeleton/shimmer loading-state components.~~ ✅ **Shipped (v0.6.0)** — `44-dj-flip.js` (FLIP list reorder, `dj-flip` / `dj-flip-duration` / `dj-flip-easing`, reduced-motion bypass, `Number`-based duration parsing, CSS-property-breakout guard on easing, author-transform restoration, overlapping-reorder cache-stomp guard, 12 JSDOM tests in `tests/js/dj_flip.test.js`) + `{% djust_skeleton %}` template tag (shape=line|circle|rect, width/height regex-whitelisted, count clamped to `[1,100]`, XSS-escaped via `build_tag()`, shimmer `@keyframes` deduped via `render_context`, 21 Python tests in `tests/unit/test_djust_skeleton_tag.py`). *(View Transitions API integration was promoted to v0.5.0.)*

~~**Sticky LiveViews**~~ ✅ **Shipped (v0.6.0)** — three PRs: #966 (embedding primitive), #967 (preservation), #969 (ADR + guide + demo). `sticky = True` class attr + `{% live_render 'X' sticky=True %}` tag + `[dj-sticky-slot]` markers. Audit: 32 Python + 20 JSDOM + 6 integration tests. ADR-011 documents wire protocol + security model + failure modes.

~~**`dj-mutation` — DOM mutation events**~~ ✅ **Shipped (v0.6.0)** — Fires a `dj-mutation-fire` CustomEvent when the marked element's attributes or children change via MutationObserver. `<div dj-mutation="handle_change" dj-mutation-attr="class,style">` filters attribute changes; omitting `dj-mutation-attr` observes childList instead. `dj-mutation-debounce="N"` (default 150 ms) coalesces bursts. Lands in `static/djust/src/37-dj-mutation.js`. 5 JSDOM tests in `tests/js/dj_mutation.test.js`.

~~**`dj-sticky-scroll` — Auto-scroll preservation**~~ ✅ **Shipped (v0.6.0)** — Keeps a scrollable container pinned to the bottom when children are appended, backs off when the user scrolls up, resumes when they return to the bottom (1 px sub-pixel tolerance). `static/djust/src/38-dj-sticky-scroll.js`. 5 JSDOM tests in `tests/js/dj_sticky_scroll.test.js`.

~~**`dj-track-static` — Static asset change detection (Phoenix `phx-track-static` parity)**~~ ✅ **Shipped (v0.6.0)** — Snapshots `[dj-track-static]` element `src`/`href` on page load; on every subsequent `djust:ws-reconnected` event, diffs against the snapshot. Dispatches `dj:stale-assets` CustomEvent on changed URLs; calls `window.location.reload()` when the changed element carried `dj-track-static="reload"`. Supporting change in `03-websocket.js` dispatches `djust:ws-reconnected` on every reconnect. Convenience `{% djust_track_static %}` template tag in `live_tags.py`. `static/djust/src/39-dj-track-static.js`. 5 JSDOM tests in `tests/js/dj_track_static.test.js` + 4 Python tests in `tests/unit/test_djust_track_static_tag.py`.

~~**WebSocket per-message compression (permessage-deflate)**~~ ✅ **Shipped (v0.6.0)** — Uvicorn and Daphne both negotiate `permessage-deflate` with browsers out of the box, so the actual wire-level compression (60-80 % reduction for VDOM patches) was already free. Shipped the declarative config toggle (`DJUST_WS_COMPRESSION`, default `True`) + `websocket_compression` config key + `window.DJUST_WS_COMPRESSION` client bootstrap, plus a deployment-guide section on the ~64 KB/connection zlib context cost, the CDN double-compression footgun, and Uvicorn/Daphne flags to enforce the decision at server level. 6 tests in `tests/unit/test_ws_compression_config.py`.

~~**Runtime layout switching**~~ ✅ **Shipped (v0.6.0)** — `self.set_layout(path)` queues a layout swap; the WS consumer renders the layout with the view's current context and emits a `layout` frame; the client splices the live `[dj-root]` into the new layout and swaps `<body>`, preserving form state / scroll / focus. Fires `djust:layout-changed` CustomEvent. 18 tests (12 Python + 6 JSDOM). User guide at `docs/website/guides/layouts.md`. Known limitation: `<head>` merging is out of scope for v1 — add dynamic stylesheets to the initial layout's `<head>`.

~~**Advanced service worker features**~~ ✅ **Shipped (v0.6.0)** — VDOM patch caching (per-URL HTML snapshots served on popstate, TTL-enforced, LRU-capped). LiveView state snapshots (opt-in per view via `enable_state_snapshot = True`; JSON-only, restored before `mount()` on back-nav). Mount batching (N lazy-hydration mounts collapsed into one `mount_batch` frame; per-view failures isolated). 4 new system checks (`djust.C301`-`C304`). 25 Python unit + 9 JSDOM + 2 integration tests. Client bundle +1 KB gzipped. Activation: `djust.registerServiceWorker({vdomCache: true, stateSnapshot: true})`.

### Milestone: v0.6.1 — Remaining v0.6.0 scope (deferred)

*Goal:* Complete the v0.6.0 feature items that didn't fit the v0.6.0rc1 cut. Each is substantial enough to deserve its own design session rather than batching. Scope: AI-generated UIs (headline), streaming initial render, time-travel debugging, Hot View Replacement.

**AI-generated UIs with capture-and-promote ([ADR-006](docs/adr/006-ai-generated-uis-with-capture-and-promote.md))** — v0.6.0 headline feature, deferred from v0.6.0rc1 due to scope. Users can chat with an assistant to compose UIs from a vetted component library, iterate through conversation, save drafts, publish them as real routed djust views, and optionally export them to idiomatic Python source for developer customization. Four phased deliverables: (A) `@ai_composable` decorator + `CompositionDocument` schema + `GenerativeMixin` with ephemeral generation; (B) `GeneratedView` model + draft capture lifecycle + drafts panel; (C) publish-and-version flow with URL routing, version history, diff/rollback/fork; (D) Python export generator producing idiomatic LiveView code with zero runtime dependency on the generative layer. The feature is deliberately structured as "LLM composes validated documents" not "LLM writes code" — the composition document is a strict recursive JSON that the framework renders through the same VDOM pipeline as every other djust view. All twelve captured-view threats (prompt injection, data exfiltration, storage quota, cost exploitation, stale bindings, tampering, DoS, accessibility regression, IP ambiguity, cross-tenant leakage, pathological compositions, poisoned component dependencies) have documented mitigations. Eight new A060-A067 system checks. Integrates with `AssistantMixin` from v0.5.x so the generative tool is just another entry in the LLM's tool schema. *~9 weeks total across four subphases; each phase is independently shippable and useful. Phase A is the natural starting point (standalone, no DB persistence).*

~~**Streaming initial render**~~ ✅ Shipped v0.6.1 (Phase 1, PR #TBD) — Chunked HTTP page shell + progressive content. Django's `StreamingHttpResponse` + djust's template engine emit the `<head>` + `<body>` wrapper immediately in a shell-open chunk, then stream the `<div dj-root>` main content and `</body></html>` close as separate chunks. Faster perceived load than full-page wait; competitive with Next.js `renderToPipeableStream` for first-paint. Opt-in via `streaming_render = True` on the LiveView class. See `docs/website/guides/streaming-render.md`. Phase 2 (lazy-child out-of-order streaming via `{% live_render lazy=True %}`) is tracked for v0.6.2.

~~**Time-travel debugging**~~ ✅ Shipped v0.6.1 (PR #TBD) — Per-view bounded ring buffer of `EventSnapshot` entries captured around every `@event_handler` dispatch (reusing `_capture_snapshot_state` from the v0.6.0 state-snapshot work). New `Time Travel` tab in the debug panel renders the timeline; clicking an entry dispatches a `time_travel_jump` WS frame and the server restores state via `safe_setattr` + re-renders through the VDOM patch pipeline. Dev-only — `DEBUG=True` gate at the consumer layer + per-view opt-in via `time_travel_enabled = True`. Beyond Redux DevTools (server-side, no client store) and beyond Phoenix's debug tools (telemetry-only). See `docs/website/guides/time-travel-debugging.md`.

~~**Hot View Replacement**~~ ✅ Shipped v0.6.1 (PR #TBD) — State-preserving Python code reload in dev mode. When a LiveView module changes on disk, the dev server `importlib.reload()`s it and swaps `__class__` in place on every live instance, preserving form input, counter values, and scroll position. React Fast Refresh parity for djust. See `docs/website/guides/hot-view-replacement.md`.

**CSS `@starting-style`** — ~~✅ Documented in v0.6.0 (PR #973)~~ — browser-native feature, no framework work needed. See `docs/website/guides/declarative-ux-attrs.md`.

### Milestone: v0.7.0 — Navigation, Smart Rendering & AI Patterns

*Goal:* Make navigation feel like a SPA and establish djust as the best framework for AI-powered applications. (Auto-generated HTTP API from `@event_handler` was pulled forward to **v0.5.1** — see [ADR-008](docs/adr/008-auto-generated-http-api-from-event-handlers.md).)

**Keep-Alive / `dj-activity` (React 19.2 `<Activity>` parity)** — React 19.2's `<Activity>` component is one of the most significant additions to any framework in 2025: it pre-renders hidden routes in the background and maintains their state when navigating away. Map this to djust: `{% dj_activity "settings-panel" visible=show_settings %}...{% enddj_activity %}` wraps a section that stays mounted (WebSocket alive, state preserved) even when hidden. Hidden activities pause effects and defer updates until visible. Use cases: tab panels where switching tabs preserves form input and scroll position, dashboard widgets that pre-load data before the user clicks, multi-step wizards where going "back" doesn't lose state. Different from `sticky=True` (which keeps a LiveView alive during navigation) — Activity is about *within-page* show/hide with preserved state and background pre-rendering. Implementation: server-side activity registry tracks hidden views, client sends visibility changes, hidden activities skip VDOM patches until shown. ~150 lines Python + ~60 lines JS. *This is how React makes navigations feel instant — the destination is already rendered. Combined with `live_session` shared connections, djust can pre-render the next likely page while the user reads the current one. No other server-rendered framework has this.*

```python
# Target API
class DashboardView(LiveView):
    def mount(self, request, **kwargs):
        self.active_tab = 'overview'

    @event_handler
    def switch_tab(self, tab: str = "", **kwargs):
        self.active_tab = tab
        # Settings panel stays mounted, form state preserved
        # Charts panel pre-renders data in background
```

~~**Django admin LiveView widgets**~~ ✅ **Shipped in v0.7.0** — Per-page widget slots (`change_form_widgets`, `change_list_widgets`) on `DjustModelAdmin` + `@admin_action_with_progress` decorator + `BulkActionProgressWidget` LiveView with cancel / log / progress bar + system checks A072 (non-LiveView slot) and A073 (multi-worker note). Shipped as extensions to the existing `DjustAdminSite` (ADR-007 Phase 4 adoption path) rather than a `DjustAdminMixin` on stock `admin.ModelAdmin` — avoids duplicating 60% of admin_ext infrastructure. See [docs/website/guides/admin-widgets.md](docs/website/guides/admin-widgets.md). Channel-layer backend for multi-worker `_JOBS` deferred to v0.7.1.

**Prefetch on hover/intent** — Pre-load the next page's data when the user hovers over a link or shows navigation intent (mouse movement toward link, touch start). `<a dj-prefetch href="/dashboard">Dashboard</a>` triggers a lightweight prefetch request on hover, so the page loads instantly on click. Different from existing `22-prefetch.js` (which pre-fetches all visible links) — this is intent-based and targeted. Remix, Next.js, and Astro all use hover-prefetch as their primary strategy for fast navigation. Implementation: `mouseenter` listener with 65ms delay (avoids prefetch on fly-over), prefetch via `<link rel="prefetch">` or fetch API with abort on `mouseleave`. ~50 lines JS. *Combined with View Transitions API, this makes navigation feel literally instant — the page is already loaded before the user clicks.*

**Server functions (RPC-style calls, promoted from post-v0.7.0 consideration)** — Call server-side Python functions from client JS and get structured results back, without defining an event handler or managing state. `const result = await djust.call('search_users', {query: 'john'})` invokes a decorated Python function and returns JSON. Different from event handlers (which trigger re-renders) — server functions are pure request/response, ideal for typeahead suggestions, autocomplete, validation checks, and any pattern where you need data but don't want a full re-render. React Server Actions and tRPC popularized this pattern. API: `@server_function` decorator on view methods, client-side `djust.call()` with promise return. ~100 lines Python + ~30 lines JS. **Relationship to the ADR-008 HTTP API (now shipping in v0.5.1):** the two are complementary — server functions target in-browser-to-server RPC for no-re-render use cases, the ADR-008 API targets external consumers (mobile / S2S / AI agents) of the same handler pool. A handler can be either or both (`@server_function @event_handler(expose_api=True)`). The ADR-008 dispatch-view router lands first in v0.5.1; server functions reuse that router plumbing here in v0.7.0.

### Milestone: v0.7.1 — Deployment ergonomics & deferred v0.7.0 items

*Goal:* Ship the smaller-but-compound follow-ups from the v0.7.0 retro
— deployment-ergonomic fixes that unblock sub-path / mounted-app users,
plus the Islands of interactivity scope that slipped from v0.7.0.

| Priority | Item | Status |
| --- | --- | --- |
| **P1** | ~~`FORCE_SCRIPT_NAME` / mounted sub-path support for the in-browser HTTP API client (#987, Action Tracker #123)~~ | ~~Shipped in v0.7.1~~ ✅ |
| **P2** | Islands of interactivity (deferred from v0.7.0) | Not started |

**~~`FORCE_SCRIPT_NAME` / sub-path mount support (#987)~~** ✅ Shipped
in v0.7.1 — new `{% djust_client_config %}` template tag emits
`<meta name="djust-api-prefix" content="...">` whose content is
resolved via Django's `reverse()`, so it automatically honors
`FORCE_SCRIPT_NAME` and any custom `api_patterns(prefix=...)` mount.
The client reads the meta tag at bootstrap and exposes
`window.djust.apiPrefix` + `window.djust.apiUrl(path)`; `djust.call()`
routes through the helper so the last remaining hardcoded
`/djust/api/` reference in the client bundle is gone. Priority:
explicit `window.djust.apiPrefix` > meta tag > compile-time default
`/djust/api/`. 12 new tests (5 Python + 6 JS + 1 regression).
Bundle delta: +148 B gzipped. Docs: "Sub-path deploys" section added
to `docs/website/guides/server-functions.md` +
`docs/website/guides/http-api.md`. Follow-up issue #992 filed for the same
class of bug in `03b-sse.js:44` (SSE fallback transport, v0.7.2
target). Closes Action Tracker #123.

**Islands of interactivity (deferred from v0.7.0 retro)** —
content-heavy sites with small, scattered interactive zones. Lets a
page use `{% live_island %}` to mark a region that upgrades to a
LiveView on hydration while the rest of the page stays fully static.
Deferred from v0.7.0 because the markdown/admin/activity work already
saturated that milestone's scope; reopens here.

### Milestone: v0.7.2 — Production Fixes & DX Polish

*Goal:* Drain the open issue queue after v0.7.1rc1 — two real bugs
(one critical install-time NameError, one Rust renderer semantics
gap), docs + infra tech-debt from the v0.5.7 upload-writer retro,
one small UX feature, and one policy decision to close out the
consolidation arc.

| Priority | Item | Status |
| --- | --- | --- |
| **P1** | NameError on module load — `djust.dev_server` references undefined `FileSystemEventHandler` when `watchdog` is absent (#994) | Not started |
| **P1** | Rust renderer ignores `__str__` key in serialized model dicts (#968) | Not started |
| **P2** | docs: `key_template` UUID-prefix convention for `s3_events` (#964) | Not started |
| **P2** | tooling: weekly real-cloud CI matrix for S3 / GCS / Azure (#963) | Not started |
| **P2** | feat: inline radio buttons (#991) | Not started |
| ~~**P2**~~ | ~~policy: `_*` prefix rename decision (#962)~~ | ~~Closed without code — ADR-012~~ ✅ |

**#994 — NameError on module load when watchdog is not installed.**
`djust/dev_server.py` wraps the `watchdog` import in try/except
`ImportError`, setting `WATCHDOG_AVAILABLE = False`, but the class
`DjustFileChangeHandler(FileSystemEventHandler)` on line 25 references
the symbol unconditionally — crashing the module at import when
watchdog is absent. Since `djust/checks.py::check_hot_view_replacement`
imports from `djust.dev_server`, this breaks `python manage.py check`
in any production install without the `[dev]` extra. Latent since
≥v0.5.4rc1; only surfaces when the env omits watchdog. Fix: guard the
class definition behind `WATCHDOG_AVAILABLE` or define a stub base
class in the except branch. Reporter offered a PR.

**#968 — Rust renderer ignores `__str__` key in serialized model
dicts.** `djust/serialization.py:157` (`_serialize_model_safely`)
sets `"__str__": str(obj)` on every serialized model dict so
`{{ obj }}` can render the instance's string representation. The
Rust renderer doesn't consume the key — instead it emits the literal
`[Object]` placeholder. Asymmetry: Django's template engine calls
`__str__` on any object by default; a plain Python object with a
custom `__str__` renders correctly even through the Rust engine
(`{{ x }}` where `x = Obj()` works). The mismatch breaks FK display
in LiveView templates whenever a view returns a model or a dict with
nested model data. Fix: in the Rust renderer's variable resolution
path, when the value is a dict containing `"__str__"`, emit that
value; fall through to `[Object]` for non-model dicts. Reporter
provided a clear repro.

**#964 — docs: prominent `key_template` convention for `s3_events`
UUID extraction.** From PR #958 retro. `s3_events.parse_s3_event`
extracts `upload_id` via regex match on the first UUID-shaped path
segment; apps must follow the documented `key_template` convention.
If they don't, extraction silently falls back to the full key. Fix:
document the UUID-prefix requirement in the upload-writers guide +
on-page docstring, emit a debug-level warning when a key doesn't
match the expected shape.

**#963 — tooling: weekly real-cloud CI matrix.** From PR #958 retro.
All v0.5.7 upload-writer tests mock the SDKs. Missing: happy-path
integration run against real AWS / GCP / Azure. Add a weekly GitHub
Actions workflow that uploads a 1 MB file, verifies presence, and
deletes it. Credentials via GitHub encrypted secrets. ~30 LOC
workflow + ~50 LOC test.

**#991 — feat: inline radio buttons.** Django's default `RadioSelect`
renders vertically; segmented controls / filter pills / short Yes-No
choices want a horizontal layout. API TBD (form-level `inline_radios`
list vs widget attr vs `{% dj_field field inline=True %}` template
variant). Must: render each choice as inline-block `<label>` with its
`<input type=radio>` inline, preserve a11y + focus ring, be
CSS-framework-agnostic, work with existing form-validation error
styling + `dj-bind`. Phoenix LiveView form helpers support inline
radios out of the box — keeps parity.

**~~#962 — policy: decide breaking rename of framework-internal attrs
to `_*` prefix.~~** ✅ **Closed without code in v0.7.2** — see
[ADR-012](docs/adr/012-framework-internal-attrs-filter-vs-rename.md).
Decision: keep the `_FRAMEWORK_INTERNAL_ATTRS` filter shipped in
#762; do NOT rename. Rename would break every user view that reads
`self.login_required` / `self.template_name` / `self.sync_safe`
(all documented first-class view attributes in our guides, and
`template_name` is Django public API) without a meaningful
defense-in-depth benefit. The filter is the single canonical gate on
the exact point where leakage matters (`get_state()` + downstream
serializers); distributing the "this attr is internal" signal
across 25 attribute sites would not catch new classes of bugs.
Mitigation: the PR review checklist now reminds authors to add new
framework-set attrs to `_FRAMEWORK_INTERNAL_ATTRS` at introduction
time.

### Milestone: v0.7.3 — Check Refinements

*Goal:* Triage the three checks-area issues filed during the v0.7.2
drain. All three are check-refinement bugs / enhancements — drift
between what a check claims to test and what it actually tests, or
signal-to-noise issues. Small-to-medium PRs each.

| Priority | Item | Status |
| --- | --- | --- |
| **P1** | `djust.C011` doesn't catch stale/placeholder `output.css` (#1003) | Not started |
| **P1** | `djust.A070` false positive on `{% verbatim %}`-wrapped `dj_activity` (#1004) | Not started |
| **P2** | `djust_theming.W001` should only contrast-check active pack (#1005) | Not started |

**#1003 — `djust.C011` doesn't catch stale/placeholder `output.css`.**
`djust._check_missing_compiled_css` at `python/djust/checks.py:185`
tests only `os.path.exists(...)` for `static/css/output.css`. A
committed-but-stale `output.css` (e.g. a placeholder
`/* Run tailwindcss ... */`) passes the check — the file "exists" —
so no C011 is emitted. The site then serves with no Tailwind
utilities. Fix: extend the check to detect placeholder content or
suspiciously-small files. Consider a sentinel comment at the top of
generated `output.css` that the check can verify.

**#1004 — `djust.A070` false positive on `{% verbatim %}`-wrapped
examples.** A070 (`dj_activity` missing `name=` argument) scans
template source as raw text. Templates that contain literal examples
of `{% dj_activity %}` inside `{% verbatim %}...{% endverbatim %}`
blocks — common pattern on docs / marketing pages that document the
tag — get flagged as real uninstrumented `dj_activity` calls. Fix:
strip `{% verbatim %}...{% endverbatim %}` regions before scanning
for `{% dj_activity %}` literals.

**#1005 — `djust_theming.W001` only contrast-checks active pack.**
`djust_theming.W001` runs WCAG AA contrast checks on every
registered theme pack × color-preset × mode. With 65+ built-in
packs, this produces hundreds of warnings on every `manage.py
check` / pod start. Most of those packs are never used by the
installing project — they're discovered purely because they ship
with djust. Fix: scope contrast checks to the active pack (per
`DJUST_THEMING_ACTIVE_PACK` setting) instead of iterating all
discovered packs.

### Milestone: v0.7.4 — Retro Follow-ups (process & docs)

*Goal:* Land the five tech-debt items filed by the v0.7.2 + v0.7.3
milestone retros. All five are small (one is test-infra; four are
docs-only). Likely shippable as 2 PRs: one test-infra (#1016) +
one bundled docs PR covering the four checklist/guide additions
(#1017 + #1018 + #1019 + #1020 — all touch
`docs/PULL_REQUEST_CHECKLIST.md` or `docs/dev/check-authoring.md`).

| Priority | Item | Status |
| --- | --- | --- |
| **P2** | py3.14 timing-sensitive CI flake class (#1016) | Not started |
| **P2** | docs: `_FRAMEWORK_INTERNAL_ATTRS` PR-checklist reminder (#1017) | Not started |
| **P2** | docs: "misleading existing tests" pattern note (#1018) | Not started |
| **P2** | docs: whitespace-preserving redaction pattern in check-authoring guide (#1019) | Not started |
| **P2** | docs: scope-decision helper extraction pattern in check-authoring guide (#1020) | Not started |

**#1016 — py3.14 timing-sensitive CI flake class.** From Action
Tracker #133. `test_hotreload_slow_patch_warning` (PR #1001) and
`test_broadcast_latency_scales[10]` (PR #990) both flake on py3.14
only — wall-clock threshold assertions and warning-debounce
timeouts hit the threshold occasionally on the py3.14 CI runner.
Pick one: per-runner tolerance / `@pytest.mark.flaky(reruns=2)` /
move py3.14 to non-required check.

**#1017 — `_FRAMEWORK_INTERNAL_ATTRS` PR-checklist reminder.** From
ADR-012 / Action Tracker #134. One bullet in
`docs/PULL_REQUEST_CHECKLIST.md` reminding reviewers to verify any
new framework-set attribute on `LiveView` / `LiveComponent` was also
added to `_FRAMEWORK_INTERNAL_ATTRS`. Mitigation for ADR-012's
accepted maintenance burden.

**#1018 — "misleading existing tests" pattern note.** From PR #1008
/ Action Tracker #135. One paragraph in
`docs/PULL_REQUEST_CHECKLIST.md` documenting that when fixing a
check or invariant, existing tests whose fixtures exemplify the
broken behavior must be UPDATED, not just augmented with new tests.
A test that passes for the wrong reason is worse than no test.

**#1019 — whitespace-preserving redaction pattern in check-authoring
guide.** From PR #1014 / Action Tracker #136. One section in
`docs/dev/check-authoring.md` (or `docs/CONTRIBUTING.md`) titled
"Ignoring template regions in regex scanners" documenting the
pattern (replace body with whitespace, keep newlines) with
`_strip_verbatim_blocks` as canonical example.

**#1020 — scope-decision helper extraction pattern in check-authoring
guide.** From PR #1015 / Action Tracker #137. One section titled
"Config-driven check scope" documenting the pattern (extract scope
decision into a named helper, safe-default contract) with
`_contrast_check_scope` / `_presets_to_check` as canonical examples.

### Milestone: v0.8.0 — Server Actions, Async Streams & Form Patterns (NEW)

*Goal:* Bridge the gap between Phoenix 1.0's async primitives and React 19's server actions model. Make djust the most ergonomic framework for forms, data mutation, and async data flows.

**Async Streams (Phoenix 1.0 parity)** — Phoenix 1.0 introduced `stream/3` with `:reset` and async enumeration. djust's `StreamsMixin` covers basic append/replace but lacks: async stream sources (wrap an async generator and stream items as they arrive), `:reset` to clear and replace all items in a stream, bulk insert/delete operations, and stream-level error handling. This is the foundation for infinite scroll, real-time feeds, and large dataset rendering without loading everything into memory. Implementation: extend `StreamsMixin` with `stream_async(name, async_generator)`, `stream_reset(name, items)`, `stream_delete(name, item_id)`, `stream_insert_at(name, index, item)`. ~200 lines Python + Rust VDOM support for stream containers.

```python
class FeedView(LiveView):
    def mount(self, request, **kwargs):
        self.assign_async('posts', self._load_posts)

    async def _load_posts(self):
        async for batch in Post.objects.filter(published=True).aiter(chunk_size=50):
            self.stream_insert('feed', batch)
```

**Server Actions (React 19 pattern)** — React 19's `useActionState` and form actions provide a pattern where form submissions automatically handle pending states, error states, and optimistic updates. Map this to djust: `@action` decorator on methods that receive form data, automatically set `action.pending`, `action.error`, `action.result` states accessible in templates. Combined with `@optimistic`, this gives React 19-level form ergonomics without any client JS. Different from `@event_handler` — actions are specifically for mutations that should have standardized pending/error/success states.

```python
from djust.decorators import action

class TodoView(LiveView):
    @action
    def create_todo(self, title: str = "", **kwargs):
        # action.pending is True in template while this runs
        todo = Todo.objects.create(title=title, user=self.request.user)
        self.todos.append(todo)
        return {"created": todo.id}  # Sets action.result in template
        # If this raises, action.error is set automatically
```

**Form status awareness (React 19 `useFormStatus` equivalent)** — Child components inside a form should be able to read whether the parent form is currently submitting, without prop drilling. React 19's `useFormStatus` lets any nested component access `{ pending, data, method, action }` from the nearest `<form>`. djust equivalent: any element with `dj-form-pending` attribute auto-toggles visibility/class based on whether its ancestor form's `dj-submit` event is in-flight. Template: `<button type="submit"><span dj-form-pending="hide">Save</span><span dj-form-pending="show">Saving...</span></button>`. Works with the existing `dj-lock` and `dj-disable-with` but provides a more general-purpose pattern for any element — not just the submit button. ~30 lines JS. *This is how React 19 handles loading states in forms, and it's more composable than per-button solutions.*

| ~~**Streaming initial render**~~ ✅ | Chunked HTTP page shell + progressive content — faster perceived load than full-page wait | ~~**v0.6.1**~~ ✅ Shipped v0.6.1 (Phase 1); lazy-child = v0.6.2 |
| ~~**Time-travel debugging**~~ ✅ | State snapshot recording + replay in debug panel — beyond Phoenix's debug tools | ~~**v0.6.1**~~ ✅ Shipped v0.6.1 |

### Milestone: v0.8.1 — Reconcile drain (15 issues from 2026-04-25 reconcile)

*Goal:* Process the curated djust-repo subset of the 39 tech-debt issues filed during `/pipeline-retro --reconcile` on 2026-04-25. Skill-level work is tracked separately under the `out-of-scope-for-djust-drain` GitHub label and lives in the pipeline-skill repo, not here.

**Quick wins (P2)** — small, focused PRs eligible for `/pipeline-drain --milestone v0.8.1`:

- **#1026** — `dispatch.py:295` vs `observability.py:399` JSON-parse error message consistency. Pure style alignment.
- **#1027** — Replace `inspect.getsource + substring` test with behavior-level test. Test-quality refactor.
- **#1028** — Shared `conftest.py` staff-user fixture for auth-gated view tests. Test-infra DRY.
- **#1029** — `docs/internal/codeql-patterns.md` taint-flow cheat sheet. Internal docs.
- **#1030** — Silent cache-write failures in `03-websocket.js:386` should log under `djustDebug`. Small JS fix.
- **#1033** — `djust[admin]` extra vs `djust.admin_ext` module name divergence. Rename one or the other.
- **#1034** — `TARGET_LIST_UPDATE_S * 20` → named `TARGET_WS_MOUNT_S` constant in perf tests.
- **#1035** — cProfile single-run "not canonical" disclaimer in `docs/performance/v0.6.0-profile.md`.
- **#1036** — `_assert_benchmark_under` move to `tests/benchmarks/conftest.py` for shared scope.
- **#1045** — Shared `_SCRIPT_CLOSE_TOLERANT_RE` constant for HTML5-tolerant `</script>` matching.
- **#1048** — Flaky perf test triage — `test_broadcast_latency_scales[10]` py3.13 budget (paired-class with PR #1021's py3.14 fix).
- **#1057** — `make roadmap-lint` Makefile target — automate ROADMAP-vs-codebase grep. ~30 LOC.
- **#1061** — Pre-push hook for `noqa: F822` in `__all__` patterns. ~15 LOC.

**Medium (P2)** — larger but still v0.8.1-eligible:

- ~~**#1031**~~ ✅ — Version-probe fallback for `mount_batch` — older servers produce generic "unknown msg type"; client should fall back gracefully. **Shipped in PR #1068.**
- ~~**#1032**~~ — Dashboard→Dashboard re-mount limitation in sticky LiveView demo. **Closed as deferred to v0.9.0+ feature item** — requires server-side template-tag intelligence + client preserved-sticky tracking; non-trivial. See v0.9.0 backlog below.

### Milestone: v0.8.2 — Theming Polish & Docs Cleanup (5 issues from docs.djust.org)

*Goal:* Process 5 in-scope GitHub issues surfaced by docs.djust.org's link crawl + theming testing. 4 of 5 are theming-cluster (`djust_theming/` package); 1 is pure docs cleanup. All originally filed as `bug` or `enhancement` (not `tech-debt`) and outside the v0.8.1 reconcile drain scope.

**Group T — Theming polish (P1 bug + P1 bug + P2 enh + P2 enh)** — bundle, single PR:

- **#1011** (bug, P1) — `.card` / `.alert` in `djust_theming/static/djust_theming/css/components.css` should set `overflow: hidden` to keep child borders inside the rounded corners. ~2 LOC.
- **#1012** (bug, P1) — `theme_css_view` `Cache-Control` insufficient: Chrome ignores `Vary: Cookie` and serves stale per-pack CSS. Add `{% theme_css_link %}` helper tag that emits cache-busting URL params (`?p=djust&m=dark`) so different pack/mode = different URL. ~30 LOC.
- **#1009** (enhancement, P2) — Ship `djust_theming/static/djust_theming/css/prose.css` so sites using `@tailwindcss/typography` don't have to re-invent the `--tw-prose-*` ↔ pack bridge. Opt-in via `prose-djust` class. ~95 LOC.
- **#1013** (enhancement, P2) — `ThemeManager.get_state()` cookie priority overrides `LIVEVIEW_CONFIG['theme']`, causing localhost cross-project bleed. Add `enable_client_override` setting (default `True` for back-compat) so sites without a user-facing switcher can opt out of cookie reads. ~20 LOC.

**Solo — Docs link cleanup (P1 bug)**:

- **#1010** (bug, P1) — `docs/components/RUST_COMPONENTS.md` references 3 nonexistent files (`LIVEVIEW.md`, `TEMPLATES.md`, `PYTHONIC_FORMS_IMPLEMENTATION.md`) and 8 dead anchors. Surfaced by docs.djust.org's `scripts/link_check.py`. Pure docs cleanup.

---

### Milestone: v0.8.3 — Docs Sweep + Pre-push Lint (1 issue)

*Goal:* Process #1075, the broader stale-MD ref sweep filed during v0.8.2's #1010 investigation. Solo issue, no `--group` mode needed.

**Solo (P2 tech-debt)**:

- **#1075** — broader stale .md ref sweep across 17 files (~50 broken refs) + new `make docs-lint` Makefile target wrapping a python sweep script (mirrors `make roadmap-lint` from Action #142). Pre-push hook prevents regression. Filed during v0.8.2 PR #1076 follow-up.

**Out of scope for v0.8.3** (deferred for explicit attention):

- ~~**#1081**~~ — `|date` filter on model DateField produces JSON-quoted output in Rust-rendered templates. Real production bug (a downstream consumer). Requires deep Rust template-engine investigation; root cause hypothesis (JIT serializer wraps in `json.dumps`) needs verification before fix. Not a 1-PR drain item — will get a focused session.

---

### Milestone: v0.8.6 — View Transitions PR-B + Open-Issue Drain (13 issues)

*Goal:* Convert the v0.8.5rc1 async-foundation work (PR-A) into a shipped user-facing feature, sweep up the remaining downstream-consumer-arc retro tech-debt, and roll the 7 process-canonicalization tickets into one CLAUDE.md/PR-checklist update. Without v0.8.6, PR-A is a breaking signature change for nothing.

**P0 — View Transitions arc (must finish what v0.8.5rc1 started)**:

- **#1098** — `handleMessage` interleaving across `await` boundaries. Stage 8 security finding from PR #1099. Two adjacent inbound WS frames can interleave their `await handleServerResponse` calls; `_pendingEventRefs.size` check at `03-websocket.js:561-568` is read AFTER an `await`, so an in-flight second message could mutate the set between the check and the flush — buffered tick draining out-of-order or applied twice. **Latent today, made worse by PR-B's wrap.** Suggested fix: per-transport message queue (`await this._inflight` chain). Solo. **PR-B blocker.**

- **PR-B** — View Transitions wrap (ADR-013 Option A complete). On top of PR-A's async `applyPatches` foundation, wrap the patch loop in `document.startViewTransition()` opt-in via `<body dj-view-transitions>`. Honors `prefers-reduced-motion: reduce`. Browser-support gate (Chrome/Edge 111+, Safari 18+; Firefox graceful degrade — no animation). 12 vitest cases per ADR-013 §"Test rewrite", real-browser smoke via MCP `djust-browser`. CHANGELOG `### Added: View Transitions API integration`. Solo. Blocked by #1098.

**P2 — Framework gaps (drain group, ~1 PR)**:

- **#1088** — Django system check for stale `collectstatic` `client.min.js`. When `client.min.js` in `STATIC_ROOT` is older than `python/djust/static/djust/client.min.js`, emit a diagnostic so deployers don't ship stale client code.
- **#1089** — Expand release wheel matrix to cp313 + cp314 explicitly. Currently the GitHub Actions release matrix builds for cp310/311/12; cp313/14 fall through to source build. Closes the source-build trap that surfaced in #1081.
- **#1090** — Debug-log when `|date` / `|time` filter parse fails. Today silent fallback; should debug-log at WARN with the offending value + format string so template authors can diagnose without instrumentation.
- **#1093** — SSE-side test for legacy-view `hasattr` guard in `_flush_deferred_to_sse`. Test gap from PR #1091 Stage 13 review — landed without a test that exercises the SSE-transport drain path with a view that lacks `_pending_deferred`.

**P3 — Process canonicalization (single docs PR)**:

Roll all 7 retro-tracker conventions into one CLAUDE.md / PR-checklist / pipeline-run subagent-prompt update. Each is a 1-3 line addition; bundling avoids 7 trivial PRs.

- **#1100** — completeness-grep for async-migration regex passes. After bulk regex pass adding `await` to migrated functions, run a follow-up grep + visual scan for hits inside `async` test bodies that don't have `await`.
- **#1101** — ADR scope-estimation for async-style migrations. Test-file scope is typically 2-3× production scope; count via `grep -lr` upfront.
- **#1103** — prefer `is None` coalescing over `kwargs.setdefault()` for forwarding mixins. `setdefault` doesn't overwrite caller-passed `None`.
- **#1104** — N similar sites need N tests, not "a representative few". Mechanical-replacement PRs should test all replacement sites.
- **#1106** — CHANGELOG conventions for additions to existing test files. Reference the test CLASS, not "N regression cases in <file>", to avoid the test-count drift hook.
- **#1108** — `Iterable[T]` over `list[T]` for membership-check filter parameters; test at least one non-list shape (tuple OR set).
- **#1109** — dynamic test fixture pattern: `type(name, bases, dict)` over class-level mutation in `__init__`.

**v0.8.6 extension — added 2026-04-26 after the original 4 PRs merged**:

Three downstream-consumer issues filed during the v0.8.6 session, plus async-enabled enhancements that finally cash in PR-A's async refactor beyond View Transitions:

- **#1114 (HIGH severity, P1)** — `DataTableMixin` is incompatible with LiveView JIT serialization + BUG-06 pre-mount lifecycle. Three compounding root causes: (1) `get_context_data()` runs before `mount()`, so `self.table_rows` doesn't exist and `get_table_context()` raises silently → empty VDOM; (2) `table_rows` serializes as a large list, JIT-broken dot-notation access; (3) `on_table_*` methods aren't `@event_handler()`-decorated → unusable under default `event_security=strict`. Downstream blocker: downstream-consumer PR #189 reverted to native handlers. Fixes: class-level `table_rows = []` default, `@event_handler()` decoration on the 5 `on_table_*` methods, doc the LiveView vs Component API boundary explicitly.

- **#1110 (P2)** — `{% data_table %}` link column type. New `link` and `link_class` keys in column dicts render the cell as `<a href="{{ row[link_key] }}">{{ row[col.key] }}</a>` instead of plain text. Currently consumers must render `<tbody>` manually (forfeit the component) or store pre-escaped HTML (fragile). Affects every admin/dashboard use case. ~30 LOC.

- **#1111 (P2)** — `{% data_table %}` row-level navigation. `row_url="key"` and/or `row_click_event="handler"` make the entire `<tr>` clickable. Option B (LiveView event) preferred — integrates with djust's event system without raw JS. ~30 LOC.

- **NEW: Async `dj-mounted` / `dj-updated` hook callbacks (P2, async-enabled)** — currently sync; user hooks can't `await fetch(...)`. Now that the patch path is async-aware (PR-A) and message-ordered (#1098), djust can `await` hook callbacks before continuing. Small API change (~40 LOC in `19-hooks.js` + `09-event-binding.js` + tests). Cashes in PR-A's async refactor beyond View Transitions. Bundle: `await window.djust.applyPatches(...)` documentation as public API + per-element `view-transition-name` example patterns (pure docs add, leverages PR-B).

**Out of scope for v0.8.6** (parked):

- The 26 issues labeled `out-of-scope-for-djust-drain` — pipeline-skill / process improvements; need their own batch session against `~/.claude/skills/`, not the djust repo.
- The 5 v0.9.0 backlog candidates (component time-travel, Redux-DevTools parity, Phase 2 streaming, ADR-006, live_render sticky auto-detect) — feature-scale, deferred.
- **Streaming patches with `scheduler.yield()` between chunks** — speculative; would need an ADR for the patch-loop's 4-phase ordering invariant.
- **`await fetch()` inside a patch (new `FetchAndApply` patch type)** — speculative; needs design surface (cache, retry, error handling).

---

### Milestone: v0.8.7 — v0.8.6 retro followup polish (5 issues)

*Goal:* Close out the 5 followup items from the v0.8.6 milestone retro before they age. Single PR, mostly docs (CLAUDE.md additions) plus one 1-line code fix. Fastest-path-to-1.0-testing logic — sweep loose ends, cut release, then v0.9.0.

**Items (single PR)**:

- **#1118 (P2 bugfix)** — `DataTableMixin.get_table_context()` missing `show_stats` post-mount. Pre-existing inconsistency surfaced by PR #1117's pre-mount/post-mount keyset comparison test. One-line fix: `"show_stats": self.table_show_stats` in the post-mount return dict. New regression test asserts both default + class-override flow.
- **#1122 (P3 docs)** — Split-foundation pattern for high-blast-radius features → CLAUDE.md. Validated 3× across the View Transitions arc.
- **#1123 (P3 docs)** — Pre-mount/post-mount keyset invariant test pattern → CLAUDE.md (testing patterns).
- **#1124 (P3 docs)** — CodeQL `js/tainted-format-string` self-review checkpoint → CLAUDE.md (JS-side patterns + Stage 7 grep target).
- **#1125 (P3 docs)** — Bulk dispatch-site refactor + count-test pattern → CLAUDE.md.

**Out of scope for v0.8.7**:
- All v0.9.0 feature work — deferred to v0.9.0 (shape C: ships all 4 — #1032 + #1041 + #1042 + #1043).
- ADR-006 AI-generated UIs (#1044) — pushed down the road (post-1.0 candidate).

---

### Milestone: v0.9.0 — Full feature wave before 1.0 testing (shape C, ~6 PRs)

*Goal:* Ship all 4 v0.9.0 backlog candidates so 1.0 testing starts from a feature-complete base. ADR-006 #1044 (AI-generated UIs) is the only deferred candidate — pushed down the road to post-1.0 because it needs the AssistantMixin/LLM-provider design work first.

**Status (live):** 1 of 6 PRs shipped. PR #1128 closed #1032; remaining work below is broken into pipeline-runnable units.

#### Shipped

- ✅ **#1032 — `{% live_render %}` auto-detect preserved stickies** (PR #1128, ADR-014, merged 2026-04-26). 1.0-blocker P1 cleared. Dashboard→Dashboard re-mount limitation closed.

#### In flight: #1043 split into 3 PRs (ADR-015 draft at `.pipeline-state/feat-streaming-phase2-1043-adr-draft.md`)

The Plan-stage pre-flight pass discovered that Phase 1 streaming (v0.6.1) was a regex-split-after-render — TTFB unchanged, retro #116 already documented this as doc overclaim. So #1043 is **introducing real streaming for the first time**, not "completing" Phase 1. Per retro #1122 split-foundation rule, this needs to ship as 3 PRs:

- [ ] **#1043 PR-A — async render path foundation** (P2, ~600 LoC core + 250 tests, ~1.5 days). Branch: `feat/streaming-phase2-1043-pr-a`. Pipeline state already exists at `.pipeline-state/feat-streaming-phase2-1043.json` (Stages 1-4 passed; ready to resume at Stage 5). Add `async def aget()` parallel to `RequestMixin.get()`; new `python/djust/http_streaming.py` with `ChunkEmitter`; `arender_chunks()` async generator in `mixins/template.py`. No new user-facing API. `streaming_render = True` flag actually shell-flushes for the first time. Rewrite `docs/website/guides/streaming-render.md` to close retro #116 doc-claim debt. Standalone ship value: TTFB win for slow `get_context_data()` views; releasable as v0.9.0rc1.

- [ ] **#1043 PR-B — `{% live_render lazy=True %}` capability** (P2, ~500 LoC + 550 tests, ~2 days, depends on PR-A). Branch: `feat/streaming-phase2-1043-pr-b`. Tag `live_render` `lazy=` kwarg branch; emit `<dj-lazy-slot>` placeholder + register thunk on `parent._chunk_emitter`; new `static/djust/src/16-lazy-fill.js` for `<template id="djl-fill-X">` + inline-script slot replacement; system check A075 to flag `lazy=True + sticky=True` collision (`TemplateSyntaxError` at tag eval). `lazy="visible"` opts into IntersectionObserver-triggered fill (composes with `dj-lazy` from `13-lazy-hydration.js`). Demo: extend `examples/demo_project` with a `lazy_demo` view exercising 3 children at different render times.

- [ ] **#1043 PR-C — `asyncio.as_completed()` parallel render** (P2, ~80 LoC + 200 tests, ~0.5 days, depends on PR-A; can ship before PR-B if scheduling demands). Branch: `feat/streaming-phase2-1043-pr-c`. Replace sequential `await` over thunks with `asyncio.as_completed()`; per-task timeout; sentinel-based cancellation propagates via `request_token` from emitter on ASGI scope `disconnected`. Children render in parallel; chunks emerge in completion order. Closes #1043 (umbrella) on merge.

#### Remaining P3 features (DevTools polish)

- [ ] **#1041 — Component-level time-travel** (P3, ~2-3 days). v0.6.1's time-travel ring-buffer records against the parent LiveView. Phase 2 captures component-level state too, so multi-component pages get per-component scrubbing in the debug panel. **Stage-4 first-principles guideline** (canonicalized from #1032 retro): the Plan stage should grep for existing `time_travel`, `state_snapshot`, `ring_buffer` symbols before locking architecture; reuse the existing parent-level recorder if at all possible.

- [ ] **#1042 — Forward-replay through branched timeline (Redux DevTools parity)** (P3, ~2 days, depends on #1041). Currently the time-travel debug panel only scrubs back through linear history. Forward-replay through alternative timelines (replay from state X with new event Y) closes the React DevTools / Redux DevTools UX parity gap. Smaller than #1041 but builds on its data model.

#### Deferred to post-1.0

- ~~ADR-006 AI-generated UIs (#1044)~~ — needs AssistantMixin/LLM-provider design first. Reconsider after 1.0 ships.

#### After v0.9.0

- Enter 1.0 testing phase.
- v1.0.0 ships after the bake.

#### Sequencing strategy (locked)

Each item ships as its own PR. Within v0.9.0:

1. ✅ **#1032** (smallest, real 1.0-blocker) — DONE, PR #1128.
2. **#1043 PR-A** (foundation; standalone-shippable, releasable as v0.9.0rc1) — in flight, plan complete.
3. **#1043 PR-B** (lazy capability; rides PR-A foundation) — blocked by PR-A.
4. **#1043 PR-C** (overlap; rides PR-A foundation; can ship before PR-B if cleaner). Closes #1043 umbrella.
5. **#1041** (component time-travel) — independent of streaming work; can ship in parallel with PR-B/PR-C if a fresh session picks it up.
6. **#1042** (forward-replay) — blocked by #1041.

v0.9.0 release cuts after all 6 PRs merge. Earlier rc cuts are fine after each foundation PR (PR-A, #1041) lands.

#### Pipeline runner notes

- `/pipeline-run --milestone v0.9.0` picks the next available unit by priority + dependency.
- `/pipeline-next --milestone v0.9.0 --feature "streaming-phase2-1043-pr-a"` to resume the in-flight PR-A pipeline (state file already exists; Stages 1-4 passed).
- The Plan-stage ADR draft at `.pipeline-state/feat-streaming-phase2-1043-adr-draft.md` is the canonical design for ALL three #1043 PRs.
- Apply Stage-4 first-principles rule: every Plan pass should grep the codebase before committing to architecture (canon from #1032 retro — what looked like "needs new transport" was actually "use the WS pipeline that already carries the data"; analogous traps may lurk in #1041/#1042).

---

### Milestone: v0.9.1 — v0.9.0 follow-up drain (10 issues)

*Goal:* Land the user-reported real bug (#1121), unblock the pre-push hook (#1134), and clear the v0.9.0 retro deferrals (ADR-015 gates + replay defense-in-depth + Rust template parity for `lazy=True`). Bake v0.9.0rc2 → v0.9.0 stable on the back of this drain — no new headline features; the soak window closes the v0.9.0 arc cleanly.

**Status:** v0.9.0rc2 released 2026-04-27. v0.9.1 candidates filed during the v0.9.0 retro + post-rc2 user reports.

#### High-priority unblockers (P1)

- [ ] **#1134 — Bisect 6 flaky tests that fail in full pytest run, pass in isolation** (P1, ~1 day). Pollution comes from another test mutating Django settings / Channels consumer registry / Redis mock state. Bisect first, fix the polluter. Every PR pays a flat 30s skip-marker tax until this is done — biggest ROI item in the milestone. Likely closes the 6 `@pytest.mark.skip(reason='flaky, see #1134')` markers added during v0.9.0.
- [ ] **#1121 — Rust template renderer rejects project-defined `register.filter`** (P1, ~0.5–1 day). User-reported real bug. Custom filters registered via Django's `template.Library().filter` work in the Python engine but not the Rust engine. Same shape as v0.7.2 `__str__` fix (#968) — the Rust path needs to consult the Django filter registry (or be told about user filters at startup). Investigate scope of the registry bridge first.

#### ADR-015 deferred follow-ups (P2)

- [ ] **#1146 — A075 system check (sticky+lazy template scan)** (P2, ~80 LoC + tests, ~0.5 day). Walk template loader's known templates; emit warning on `{% live_render sticky=True lazy=True %}` collision at startup rather than template-render time.
- [ ] **#1147 — CSP-nonce-aware activator for `<dj-lazy-slot>` fills** (P2, ~50 LoC + tests, ~0.5 day). Thread the request CSP nonce through `live_tags.py` + `50-lazy-fill.js` so inline activators match a strict CSP. Required for sites that disallow `unsafe-inline`.
- [ ] **#1145 — Rust template engine `{% live_render %}` lazy=True parity** (P2, ~150 LoC Rust + ~50 LoC tests, ~1.5 days). Port the Django `lazy=True` branch (~210 LoC at `templatetags/live_tags.py:live_render`) into a Rust tag handler in `crates/djust_templates/`. Production users on the Rust path are blocked from streaming today.

#### Server-side polish (P2)

- [ ] **#1148 — Replay handler argument validation (defense-in-depth)** (P2, ~5 LoC + 2 tests, ~0.25 day). Augment `replay_event` (PR #1142) to validate `snapshot.event_name` against `view._djust_event_handlers` rather than the bare underscore-prefix guard. Limits forward-replay to actual handlers.
- [ ] **#1158 — Theming cookie namespace for cross-project isolation on localhost** (P2, ~10–15 LoC + tests, ~0.5 day). Follow-up to closed-as-workaround #1013. Cookies are domain-scoped, not port-scoped, so multiple djust projects on `localhost:80xx` share `djust_theme*` cookies. Add `LIVEVIEW_CONFIG['theme']['cookie_namespace']` (string); read namespaced first, fall back to legacy unprefixed names; write only the namespaced name when set. Touches `manager.py` + `build_themes.py` + theming docs.

#### Test/env hygiene (P3)

- [ ] **#1150 — Descriptor-pattern component time-travel verification test** (P3, ~30 LoC, ~0.25 day). PR #1141 Stage 11 deferral. End-to-end test exercising class-level `LiveComponent.descriptor()` capture+restore. Locks in the `_COMPONENT_INTERNAL_ATTRS` defense layer.
- [ ] **#1149 — `markdown` package missing from default test env** (P3, ~10 LoC, ~0.1 day). Carryover from v0.8.7 retro. Add to dev-deps OR mark dependent tests with `pytest.importorskip("markdown")`.

#### Feat slot (P3)

- [ ] **#1111 — data_table row-level navigation (`row_click_event` / `row_url`)** (P3, ~150 LoC + tests, ~1 day). Common click-to-detail UX. Design choices to lock in the Plan stage: handler attribute on `<tr>` vs URL builder, keyboard support (Enter/Space, role=button), default-prevent for nested controls (links/buttons inside the row). Slips out of v0.9.1 if the unblocker work runs long.

#### Out of scope for v0.9.1

- **#1151 — Debug panel UI for per-component scrubbing + forward-replay** — bigger feature (~300 LoC JS + tests). Build on PRs #1141/#1142 primitives. Park for v0.10.0 or a dedicated devtools milestone.
- **#1152 — Vitest unhandled-rejection in `view-transitions.test.js`** — non-deterministic teardown error; investigate when it next surfaces in CI rather than chasing it speculatively.
- **#1153 — `asyncio.as_completed._wait_for_one` warning suppression** — cosmetic warning under teardown; locally filter or fix `_cancel_pending` lifecycle when it actually blocks something.
- **#1143/#1144 — Stage-4 first-principles canonicalization + branch-name verify check** — skill/CLAUDE.md updates, not framework code. Apply directly to `~/.claude/skills/pipeline-run/SKILL.md` and `CLAUDE.md` independent of any release cycle.

#### Sequencing strategy

1. **#1134 first** — every other PR is faster once the flaky-test tax is gone. Single-session bisect → polluter fix → unskip all 6 markers in one PR.
2. **#1121** in parallel (independent codepath) — can ride a fresh session if a contributor picks it up.
3. **#1158** + **#1148** + **#1149** + **#1150** as a small drain group (each ~0.25–0.5 day) — single autonomous `pipeline-run --milestone v0.9.1 --group --all` pass.
4. **#1146** + **#1147** + **#1145** as the ADR-015 cleanup group — these depend on the streaming code shipped in v0.9.0 PR-B (#1138) and naturally cluster.
5. **#1111** last — feat with a non-trivial API decision; better to ship this on its own with a Stage 4 design pass.

#### After v0.9.1

- v0.9.0 stable promotion (rc2 → final) once v0.9.1 has soaked for one cycle without regressions.
- Then enter the v1.0.0 testing arc — the deferred 1.0-blockers are Dead View / Progressive Enhancement and Accessibility (ARIA/WCAG), per the Priority Matrix.

#### Pipeline runner notes

- `/pipeline-drain --milestone v0.9.1` to triage all 10 candidates into an `--all`-mode run.
- `/pipeline-run --milestone v0.9.1 --priority P1 --all` to ship #1134 + #1121 first.
- `/pipeline-run --milestone v0.9.1 --group --all` to bundle the small P2/P3 drain items per the sequencing strategy above.

---

### Milestone: v0.9.2 — v0.9.1 retro follow-up drain (~7 PRs + 1 skill update)

*Goal:* Land the 10 follow-up issues filed during v0.9.1 Stage 11 reviews. Mostly polish on top of working implementations — no real bugs, no headline features. Locks in the process canonicalizations from v0.9.1's lessons learned (parallel-agent serialization, two-commit shape, "3 clean runs" gate, CSP-strict defaults). Bake v0.9.0 stable on the back of this drain.

**Status (planning):** 0 of 7 PRs shipped. All 10 candidate issues open and triaged into 7 work units (4 grouped + 3 solo) plus 1 skill-only update (#1172) that lands directly without a PR.

#### Process / canonicalization (P2)

- [ ] **Skill update — Serialize implementer agents per checkout (#1172)** — applied directly to `~/.claude/skills/pipeline-run/SKILL.md`, NOT a djust-repo PR. ~20 LoC doc addition. Land first; encodes the lesson that benefits the rest of this drain.
- [ ] **Pipeline template canonicalization (#1173 + #1174)** — `.pipeline-templates/feature-state.json` + `.pipeline-templates/bugfix-state.json` updates: enforce two-commit shape (impl+tests / docs+CHANGELOG, Stage 9 boundary) and add "3 clean full-suite runs" mandatory gate at Stage 6 for pollution-class fixes. ~30 LoC across templates + ~15 LoC skill text. Branch `chore/v0.9.2-pipeline-template-canon`.
- [ ] **CSP-strict defaults canonicalization (#1175)** — CLAUDE.md addition + `docs/PULL_REQUEST_CHECKLIST.md` + `docs/website/guides/security.md` addition. Pattern: external static JS module + auto-bind on marker class as the canonical CSP-friendly shape for new client-side framework code. ~50 LoC docs. Branch `docs/v0.9.2-csp-strict-canon`.

#### Custom filter bridge polish (P2, #1162 → 1 PR, 6 sub-items)

- [ ] **PR: `crates/djust_templates/` polish** — closes #1162. (a) Hot-path Mutex perf: `AtomicBool ANY_CUSTOM_FILTERS_REGISTERED` short-circuit so apps with zero custom filters skip the lock entirely. (b) Hardcoded `autoescape=true` for `needs_autoescape` filters → consult renderer state. (c) Tighten unknown-filter test to assert the specific error message shape. (d) Drop unused `pub fn custom_filter_exists` (or wire to a parser-time use). (e) Test fixture autouse-scope `filter_registry::clear()`. (f) Raise clear error on async filters instead of silently calling them. Branch `fix/1162-custom-filter-bridge-polish`. ~50 LoC core + 4-6 tests.

#### Test / dev-env hygiene (P3, grouped)

- [ ] **PR: hygiene group #1160 + #1165** — closes #1160 (Redis perf bound — tighten via median-based assertion or soften docstring) and #1165 (3 sub-items: caplog assertions for replay rejection logging, document the descriptor auto-promotion gap, optional `scripts/check-dev-env-imports.py` for `markdown`/`nh3` regression coverage). Branch `chore/v0.9.2-hygiene-group`. ~30 LoC core + ~8 tests.

#### Tag-registry isolation + sidecar extension (P3, #1167 → 1 PR, 2 sub-items)

- [ ] **PR: `tag_registry` test isolation + `call_handler_with_py_sidecar` parity** — closes #1167. (a) Tighten `tests/unit/test_tag_registry.py` teardown so the leaked `"broken"` handler doesn't break `tests/unit/test_assign_tag.py` under specific test orderings. (b) Extend `call_handler_with_py_sidecar` (PR #1166) to block-tag and assign-tag handlers for symmetry — currently only `Node::CustomTag` gets the sidecar. ~30 LoC across `crates/djust_templates/src/registry.rs` + `renderer.rs` + tests. Branch `fix/1167-tag-isolation-sidecar`.

#### Cookie namespace polish (P3, #1169 → 1 PR, 4 sub-items)

- [ ] **PR: `python/djust/theming/` polish** — closes #1169. (a) `_read('djust_theme_<ns>')` `or None` defeats the migration fallback when the namespaced cookie is empty-string — switch to explicit `None` check. (b) Validate `cookie_namespace` config value: reject characters illegal in cookie names (whitespace, `=`, `;`). (c) Add JSDOM test asserting `document.cookie` after a theme switch contains the prefixed name. (d) Clean up legacy unprefixed cookie on first namespaced write to avoid indefinite jar persistence. Branch `fix/1169-cookie-namespace-polish`. ~30 LoC + 1-2 JSDOM tests.

#### data_table row navigation polish (P3, #1171 → 1 PR, 3 sub-items)

- [ ] **PR: `python/djust/components/static/djust_components/data-table-row-click.js` polish** — closes #1171. (a) Add `<details>`/`<summary>`/`<option>` to `NESTED_CONTROL_SELECTOR` (currently 6 tags; misses 3 common interactive elements). (b) Refactor the test-hook (`window.__djustRowClickNavigate`) into the existing `window.djustDataTableRowClick` namespace export so tests can `vi.spyOn(djustDataTableRowClick, 'navigate')` without the magic underscored global. (c) Add a Python-side allowlist regression test (cell-rendered HTML doesn't navigate; the JS guard is the actual defense, but a Python test documents the allowed shapes). Branch `fix/1171-data-table-row-nav-polish`. ~30 LoC core + 4-5 tests.

#### Out of scope for v0.9.2

- **#1170 deferred 🟡 R3-R5** — covered by #1171 above.
- **#1166 self-flag #3 (asymmetric sidecar)** — covered by #1167 above.
- **Anything from v0.9.0 retro that wasn't already drained in v0.9.1** — those are now blocked by deeper design work (e.g., #1151 debug panel UI is its own milestone).

#### Sequencing strategy (locked)

1. **#1172 first** (skill file update, no PR) — encodes the parallel-agent serialization rule that the rest of this drain benefits from.
2. **#1173 + #1174 (template PR)** + **#1175 (CSP docs PR)** can run in parallel since they touch disjoint files.
3. **#1162 (custom filter polish)** — sole heavy Rust task; runs solo to avoid Cargo.lock churn collisions.
4. **#1160 + #1165 (hygiene group)** + **#1169 (cookie polish)** + **#1171 (data_table polish)** + **#1167 (tag-registry + sidecar)** — 4 small PRs, can run in any order. Touch disjoint files: `tests/unit/`, `python/djust/theming/`, `python/djust/components/static/djust_components/`, `crates/djust_templates/`.
5. After all 7 PRs land + #1172 skill update applied, **promote v0.9.0rc2 → v0.9.0 stable** as the bake closes.

#### After v0.9.2

- **v0.9.3 test-infra cleanup** (see milestone below) — REQUIRED before `/djust-release 0.9.0rc3` because `make test` exits non-zero with happy-dom + undici unhandled errors (CI is green, but local make-test pre-flight is the canonical release gate).
- v0.9.0 stable promotion (rc3 → final) once v0.9.3 fixes land + soak.
- Then enter the v1.0.0 testing arc — deferred 1.0-blockers are Dead View / Progressive Enhancement and Accessibility (ARIA/WCAG) per the Priority Matrix.

#### Pipeline runner notes

- `/pipeline-drain --milestone v0.9.2` to triage all 7 PR candidates into an `--all`-mode run.
- `/pipeline-run --milestone v0.9.2 --group --all` to bundle the small P3 drain items per the sequencing strategy above.
- Apply the v0.9.1 retro lessons proactively: serial agents (#1172/#180), two-commit shape (#1173/#181), 3-clean-runs gate (#1174/#182). The drain is the right place to dogfood these rules.

---

### Milestone: v0.9.3 — Test-infra cleanup (release-blocker for v0.9.0rc3)

*Goal:* Get `make test` exiting clean so `/djust-release 0.9.0rc3` can proceed. Three sibling unhandled-error / warning issues from JS + Python test environments — same class (test-runtime cross-pollination between real Web-platform implementations and emulated test environments). All three are pre-existing (not introduced by v0.9.1 or v0.9.2 work) but only surfaced as a release-blocker at v0.9.0rc3 pre-flight when CI's vitest config silently swallows them while `make test` doesn't.

**Status (planning):** 0 of 3 PRs shipped. All 3 issues open. Single drain — small, mechanical, no design work.

#### The 3 issues (all P1/P2, test-infra only)

- [ ] **#1186 — happy-dom + undici WebSocket unhandled errors in `tests/js/sw_advanced.test.js`** (P1, release-blocker). 3× `TypeError: Failed to execute 'dispatchEvent' on 'EventTarget'` — undici constructs an Event that happy-dom's `instanceof` check rejects. All actual tests pass; only the unhandled-error count makes vitest exit non-zero. Filed during v0.9.0rc3 pre-flight 2026-04-28. Three fix paths:
  - **(1)** Filter in `vitest.config.ts` `onUnhandledRejection` hook (cheapest, ~5 LoC).
  - **(2)** Stub the WebSocket constructor in `sw_advanced.test.js` setup using happy-dom's Event class (mirrors v0.8.5 retro #1113 microtask-yield-stub pattern).
  - **(3)** Pin happy-dom + undici versions to a known-good combination.
  - Path 1 + a TODO comment is recommended.

- [ ] **#1152 — Vitest unhandled-rejection in `tests/js/view-transitions.test.js`** (P2, sibling). v0.9.0 retro Action Tracker #178. Non-deterministic `EnvironmentTeardownError: Closing rpc while "onUserConsoleLog" was pending` during teardown. Same root-cause class as #1186 — JS test runtime async-callback interop. Audit per CLAUDE.md retro #1113 microtask-yield rule.

- [ ] **#1153 — `asyncio.as_completed._wait_for_one` warning suppression** (P2, Python-side analog). v0.9.0 retro Action Tracker #179. `DeprecationWarning: There is no current event loop` under teardown in `tests/integration/test_chunks_overlap.py`. Filter locally OR fix `_cancel_pending` lifecycle in `arender_chunks` (the latter is a real bug if the cancellation isn't awaited cleanly).

#### Acceptance

- `make test` exits 0 on a clean checkout. All three issues closed (or downgraded to filtered-suppression) before tag.
- No actual test logic regresses (the 1463 JS + ~6729 Python tests still pass).
- `/djust-release 0.9.0rc3` pre-flight `make test` passes, unblocking the release.

#### Sequencing strategy

1. **#1186 first** — release-blocker. Path 1 (vitest.config filter) is the cheapest unblock; Path 2 (stub) is the cleaner fix. Pick by judgment during the Plan stage.
2. **#1152 next** — same class; the fix-pattern from #1186 likely applies.
3. **#1153 last** — Python-side; small. Determine whether it's a real \`_cancel_pending\` lifecycle bug (fix forward) or a benign teardown warning (filter).

All three can ship as ONE PR titled `chore(test-infra): suppress unhandled errors in JS + Python test runtimes` if the fixes align (likely cheapest path); OR as 3 small PRs if the diagnoses diverge. Plan stage decides.

#### After v0.9.3

- `/djust-release 0.9.0rc3` retry. Soak. Promote rc3 → v0.9.0 stable.
- Then v1.0.0 testing arc.

#### Pipeline runner notes

- `/pipeline-drain --milestone v0.9.3` to triage. Likely results in 1-PR drain (combine all 3 fixes) since the issues are mechanically similar and all touch test-infrastructure files.
- v0.9.1 retro lessons still apply: single-agent-per-checkout, two-commit shape, 3-clean-runs gate (the latter relevant if any of the 3 turns out to be pollution-class rather than runtime-interop).

---

### Milestone: v0.9.4 — Debug Panel UI + post-rc3 polish

*Goal:* Build the user-facing **Debug Panel UI** on top of the v0.9.0 time-travel + forward-replay primitives (#1041 + #1042), plus a small batch of test-infra polish and process canon items that have been accumulating in the Action Tracker.

Headlined by #1151 (real user-visible feature). Test-infra polish bundles cleanly alongside since both touch the dev-experience surface. Process canon items batch into a single ROADMAP/CLAUDE.md PR at the end.

**Status (planning):** 0 of ~3 PRs shipped. 8 issues identified.

#### Headliner — Debug Panel UI for time-travel + forward-replay

- [ ] **#1151 — Debug panel UI for per-component scrubbing + forward-replay** (P1, feature). The v0.9.0 milestone shipped the *capability*: per-component time-travel (#1041) and Redux-DevTools-parity forward-replay through branched timelines (#1042). The Python/JS plumbing exists. What's missing: the user-facing UI in the existing debug panel. Concrete asks:
  - Per-component scrubber widget (timeline slider per LiveComponent, not just the whole-view ring buffer).
  - Forward-replay button — "fast-forward through this branched timeline" — once you've rewound and you want to re-run from the current state.
  - Branch indicator — visualize that the current cursor is on a branch (not the main timeline) so users don't lose work.
  - Wire the existing `time_travel_max_events` config knob into the panel as a settings dropdown.
  - Stage 4 plan should grep `python/djust/static/djust/src/14-debug-panel.js` for the existing panel scaffold; this is an additive feature inside an existing module, not a new one.
  - Test plan: vitest cases for the new UI components; one Playwright case that scrubs back N steps + forward-replays and asserts state recovery; add a `tests/js/debug-panel-time-travel.test.js`.
  - Likely a single PR; bigger if the Python-side `branch_id` exposure needs work.

#### Test-infra polish (P3 batch)

- [ ] **#1189 — `test_large_template` wall-clock perf bound flakes under heavy suite load** (P3). Same class as the v0.9.0 wall-clock flake noted at v0.9.0rc3 retry. Two fix paths:
  - **(1)** Bump the perf bound + add explanatory comment (cheapest).
  - **(2)** Mark with `@pytest.mark.benchmark` so it only runs in dedicated benchmark sessions, not the regular suite.
  - Path 1 is fine if the wall-clock variance is bounded; Path 2 is correct if the test is fundamentally a benchmark masquerading as a regression.
- [ ] **#1188 — PR #1187 follow-ups** (P3). Vitest filter narrowing (the `onUnhandledError` hook from v0.9.3 currently matches a broad message+stack pattern; tighten to the specific undici/happy-dom shape) + regression test using `gc.collect()` to verify no resource leaks across the filtered errors.

#### Process canon (P3 batch)

Single PR titled `docs(process): canonicalize 4 retro patterns from v0.8.x + v0.9.x arc`. Each adds a section to CLAUDE.md and (if applicable) the PR-checklist. No code changes.

- [ ] **#1185 — PR-checklist canon: each `Closes #N` on its own body line** (P3). Parenthesized comma-list form silently fails GitHub's auto-close parser. v0.9.2 retro tracker #184.
- [ ] **#1144 — Branch-name verify check in pipeline-run skill** (P3). Twice in v0.9.0 a commit landed on the wrong branch. Add a pre-commit `git symbolic-ref --short HEAD` match against the active state file's `branch_name`. v0.9.0 retro tracker #169. (Skill change, not a framework change — bundle here for atomicity.)
- [ ] **#1143 — Stage-4 first-principles canonicalization in CLAUDE.md** (P3). Plan stage's grep-before-architecting pass paid off in #1128, #1041, #1135. v0.9.0 retro tracker #168.
- [ ] **#1180 — PR #1179 follow-ups: filter polish + test strength** (P3). Lightweight, mechanical.

Three v0.8.6 retro patterns (#1125, #1124, #1123) are also still open as canon items. Optional addition to the same canon PR if scope allows; otherwise defer to v0.9.5.

#### Acceptance

- #1151 ships with vitest + at least one Playwright case; debug panel scrubbing + forward-replay demonstrably work in a browser.
- `make test` still exits 0 after #1189 + #1188 land (no perf regressions, no over-eager filter swallowing real errors).
- The 4 process canon items become CLAUDE.md / PR-checklist rules anyone can grep for.

#### Sequencing strategy

1. **#1151 first** — biggest feature, most uncertainty in Stage 4 design (panel module surgery). Land it standalone with full Stage 11.
2. **#1189 + #1188 together** — sibling test-infra polish. One PR titled `chore(test-infra): tighten v0.9.3 vitest filter + suppress test_large_template flake`.
3. **Process canon PR last** — `docs(process): canonicalize 4 retro patterns from v0.8.x + v0.9.x arc`. Closes #1185, #1144, #1143, #1180.

#### After v0.9.4

- v0.9.5 candidates (post-release-tag):
  - **docs.djust.org Makefile migration** — drop `watchfiles` wrapper, use plain `uvicorn`. Needs djust submodule bumped to a release containing PR #1190 (HVR auto-enable). Filed as PR #1190 retro follow-up.
  - **docs.djust.org green-theming experiment** — apply djust.org's green accent palette to docs.djust.org. User-flagged 2026-04-28; needs a brief design pass first.
  - Remaining canon items: #1125, #1124, #1123 (v0.8.6 patterns) if not bundled into v0.9.4 canon PR.

#### Pipeline runner notes

- `/pipeline-drain --milestone v0.9.4` to triage. Likely 3 PRs (feature + test-infra + canon).
- v0.9.x retro lessons all apply: single-agent-per-checkout (#1172), two-commit shape (#1173), 3-clean-runs gate for any pollution-class fix (#1174), CSP-strict defaults for new client-side code (#1175 — relevant for #1151 since the debug panel UI emits HTML).

---

### ~~Milestone: v0.9.0 — Backlog (deferred features from v0.8.1 reconcile)~~ — superseded

*Superseded by the shape C v0.9.0 milestone above (4 features ship; ADR-006 #1044 deferred post-1.0). Original block kept here for audit-trail only.*

~~Five tech-debt issues from the 2026-04-25 reconcile pass were closed-as-relocated because they're real feature work, not 1-PR drain items. Filing them as v0.9.0+ planning candidates so they aren't lost:~~

- ~~**Component-level time-travel** (was #1041)~~ — promoted into v0.9.0 shape C
- ~~**Forward-replay through branched timeline** (was #1042)~~ — promoted into v0.9.0 shape C
- ~~**Phase 2 streaming** (was #1043)~~ — promoted into v0.9.0 shape C
- ~~**ADR-006 AI-generated UIs** (was #1044)~~ — still deferred (post-1.0)
- ~~**`{% live_render %}` auto-detect preserved stickies** (was #1032)~~ — promoted into v0.9.0 shape C as P1

---

### Milestone: v0.9.5 — Process polish wave from v0.9.5 retro

*Goal:* Ship the small process-improvement issues surfaced by the v0.9.5 milestone retrospective. All quick wins; each unblocks future-PR efficiency or future-investigator clarity. No framework code changes — only CLAUDE.md, pipeline templates, skill files, and test strengthening. The heavier issues from the same retro (#1207 list[Model] shape coverage, #1212 retro-gate audit, #1214 CodeQL sanitizer model) deferred to a later milestone where their design choices warrant their own planning passes.

**Status (planning):** 0 of 5 PRs shipped. 5 issues identified.

#### Process canon (P2 batch)

- [ ] **#1210 — plan-template Stage 4 must require reproducer/artifact before plan finalization** (P2, tech-debt). v0.9.5 retro Action Tracker #191. Add a leading mandatory checklist item to `.pipeline-templates/feature-state.json` and `bugfix-state.json` Stage 4: bug plans require a failing reproducer test; security plans require reading actual code at the alert-cited location. Caught by PR #1206's ~10 min Stage 4 waste chasing dead code AND PR #1201's 8 alerts that all turned out to be FPs after reading the actual lines.
- [ ] **#1211 — reviewer-prompt budget guidelines for pipeline-run Stage 11** (P2, tech-debt). v0.9.5 retro Action Tracker #192. Update `~/.claude/skills/pipeline-run/SKILL.md` Stage 11 prompt template to cap security PR review at 200 words, feature at 350, bugfix at 250. Forbid "edge-case spelunking" beyond the documented attack-shape list. PR #1201 reviewer stalled at the 10-min watchdog mid-tangent on backslash-injection; the right phrasing prevents this.
- [ ] **#1213 — Bug-report triage section in CLAUDE.md citing PR #1206 as case study** (P2, docs). v0.9.5 retro Action Tracker #194. Add a "Bug-report triage" section to `CLAUDE.md` near the existing "Personality" section. Generalizes the "issue-reporter analysis ≠ root cause" lesson from PR #1206. Trace from observable symptom to actual code path; don't trust path-down hypotheses. ~20 min.

#### Tooling (P2)

- [ ] **#1209 — vulture-based pre-push check for unused private methods** (P2, tooling). v0.9.5 retro Action Tracker #197. Filed during PR #1206 cleanup. Add `scripts/check-dead-private-methods.py` (or a vulture wrapper) plus pre-push hook entry. Whitelist framework-hook patterns (`__init__`, `_meta`, descriptor protocols) and reflection-called methods. Would have caught `_lazy_serialize_context` months before PR #1206 if it had been in place.

#### Test strengthening (P3)

- [ ] **#1208 — strengthen idempotency test for normalize pass with explicit zero-patch assertion** (P3, test). v0.9.5 retro Action Tracker #196. Filed during PR #1206 cleanup. `test_normalize_idempotent_on_already_serialized` currently asserts no exception. Should also assert `dom_changes` count is 0 on noop event. ~15 min effort. May need to add a thin patch-count accessor to `LiveViewTestClient`.

#### Acceptance

- All 5 issues close via merged PRs.
- `.pipeline-templates/{feature,bugfix}-state.json` Stage 4 has the reproducer-first mandatory item.
- `CLAUDE.md` contains a "Bug-report triage" section.
- Vulture (or equivalent) runs in pre-push and flags unused private methods.
- Idempotency test asserts both no-exception AND zero-patches.

#### Deferred to later milestone

- **#1207** — heterogeneous + nested `list[Model]` shapes in normalize pass. Needs a design pass on whether to scan-full-list, recurse-bounded, or document-as-unsupported.
- **#1212** — audit pipeline-bypass merges + harden retro-gate. Larger effort: audit script + scheduled CI check + tune false-positive thresholds.
- **#1214** — CodeQL sanitizer model for `sanitize_for_log`. Requires investigation into CodeQL custom-query authoring; potentially 1-2 hours just to determine tractability.
- **#1215** — `.pxd` line-ending cleanup. Small chore; can ship anytime, no blocker.

#### Sequencing strategy

1. **#1213 first** — pure docs, no code dependencies, ~20 min.
2. **#1208 + #1210 + #1211 in parallel** — small standalone changes, no shared files. Could ship as 3 PRs or batched as one "v0.9.5 process polish" PR.
3. **#1209 last** — needs the most investigation (vulture configuration, whitelist tuning). May expose pre-existing dead methods to triage.

#### Pipeline runner notes

- `/pipeline-drain --milestone v0.9.5 --label tech-debt` to triage. Will pick up issues already added to this milestone.
- v0.9.5 retro lessons apply: reproducer-first (the issues being shipped here MAKE this discipline structural — meta-applicable), reviewer-prompt budget, two-commit shape per v0.9.1 retro #181.

---

### Milestone: v0.10.0 — Rust Polish (next minor after v0.9.0 stable)

*Goal:* Three sub-week, low-risk Rust additions that compound the existing Rust-side wins (template engine, VDOM, fragment cache). Each is Django-compatible — no surface change for user code; just faster + safer plumbing underneath.

**Status (planning):** 0 of 3 PRs shipped. Targeted to ship as the next minor after v0.9.0 stable cuts. If 1.0 has cut by then, this becomes v1.1.

**Why ship as v0.10 not v1.0:** the 1.0 quality gates (accessibility / WCAG, Dead View / progressive enhancement, soak) are the 1.0 blocker. These three Rust items are pure-perf / pure-safety wins — they don't move the 1.0 release-readiness needle. Better to land them in a focused minor where soak is bounded.

#### Items

- [ ] **#1 — WebSocket payload validation in Rust** (P1, security + perf double-win, ~1 week). Every inbound WS frame today goes through Python: type whitelist, `ref` is int, `params` is dict, `event` name is allowed. djust_core can pre-validate before any Python touches it. Drops malformed/malicious frames before Python overhead; tightens the security surface (rate-limit, message-size cap, schema validation in one hot Rust path). First-PR shape: `crates/djust_core/src/wire.rs` — `validate_inbound_frame(json: &str) -> Result<ValidatedFrame, FrameError>`. `LiveViewConsumer.receive()` calls it, dispatches based on the validated shape.

- [ ] **#2 — Patch coalescing buffer** (P1, smoother high-frequency UIs, ~1 week). When multiple events fire within ~16ms (cursor moves, slider drags, animations), djust currently sends N patch frames; the browser applies them sequentially — wasted work. Rust adds a 16ms windowed buffer that merges patches targeting the same node before flushing. Cursor-tracking demos that fire 60 events/sec become ~6 frames/sec on the wire with no UI difference. First-PR shape: `crates/djust_vdom/src/coalesce.rs`. Activated by config: `LIVEVIEW_CONFIG = {"patch_coalesce_window_ms": 16}`. Default off until v0.10 soak.

- [ ] **#3 — Settings/config validator at startup** (P2, catches prod misconfig early, ~3 days). `LIVEVIEW_CONFIG` is a Python dict. Mistypes (`"hot_reload_auto_enable": "True"` instead of `True`) only surface at the first relevant code path. Rust-side validator at `django.setup()` time can check shape + types up front. Misconfig → loud error at boot, not silent broken behavior in prod. First-PR shape: `crates/djust_core/src/config_schema.rs` with serde-style schema. Reuses existing `LIVEVIEW_CONFIG` defaults from `python/djust/config.py`. Wired into `DjustConfig.ready()` (alongside the existing observability handler + HVR auto-enable blocks).

#### Acceptance

- All 3 PRs ship without breaking the existing wire protocol (additive only).
- Bench: WS payload validation in Rust beats the Python equivalent by ≥3× on the standard event-dispatch benchmark.
- Bench: patch coalescing reduces the 60-event/sec cursor demo's wire frames by ≥80%.
- Misconfig validator catches the 5 most common `LIVEVIEW_CONFIG` typos with a clear actionable error message.
- No regression in `make test` (4047 Python + 1486+ JS).

#### Out of scope (post-1.0)

- **Form validation hybrid** (Rust does mechanical validators; Python does `clean_*`) — needs careful soak because every Django form touches it. Logged in "Investigate & Decide" / Rust gap-closing.
- **Pre-render cache for static-ish routes** — bigger surface area, deserves its own milestone.
- **Rust + WASM client patcher** — biggest architectural Rust play but ~2-3 months and high risk; v1.x or v2.x.

---

## Investigate & Decide

Open questions that inform future direction:

- **Session/state storage** — Can template context be reconstructed from DB rather than stored in memory/Redis? Can any state move client-side (signed cookies, JWT)? What is typical session size at scale?
- **Debug toolbar completeness** — State size visualization is done (v0.3.7). Remaining: panel state persistence across TurboNav navigation (30s sessionStorage window implemented but edge cases remain).
- **VDOM edge cases** — Investigate remaining edge cases surfaced by proptest fuzzing.
- **Rust-side WASM compilation** — Could the VDOM diffing run client-side via WASM for even faster patches? Tradeoffs: larger JS bundle vs eliminating server round-trip for pure UI changes. Investigate feasibility and performance impact.
- **Django Ninja / DRF interop** — Some teams use djust for UI but need REST/GraphQL APIs alongside. Document recommended patterns; evaluate whether djust views can expose API endpoints without duplication.
- **Navigation API** — The Navigation API (`navigation.navigate()`) is the modern replacement for `pushState`/`popstate`, with better interception, transition tracking, and abort support. Chrome ships it; Safari/Firefox are implementing. Evaluate whether djust's `live_patch`/`live_redirect` should use it where available for cleaner SPA navigation and better integration with View Transitions API.
- **WebTransport** — Next-generation transport after WebSocket: lower latency, supports unreliable delivery (useful for cursor positions where dropped updates are fine), and multiplexing built-in. Chrome supports it. Evaluate as a third transport option alongside WebSocket and SSE for low-latency use cases.
- **Content-Visibility CSS property** — `content-visibility: auto` lets the browser skip rendering of off-screen content entirely. For djust pages with long lists or many components, this is free performance — the browser handles "virtual scrolling" natively. Evaluate documenting this pattern and ensuring VDOM patching doesn't interfere with content-visibility optimizations.
- **Popover API** — The HTML `popover` attribute provides browser-native popovers with light dismiss, top-layer rendering, and accessibility built-in. Evaluate integrating with `djust-components` dropdown/tooltip components as a progressive enhancement.
- **React Compiler-style auto-memoization (post-1.0)** — React 19's compiler automatically inserts `useMemo`/`useCallback` equivalents. Concrete plan: extend the Rust template parser (`crates/djust_templates`) with a dataflow pass that, for every subtree, computes the set of template variables it depends on (`Map<NodeId, Set<VarPath>>`). At render time, compare each subtree's depended-on vars against the previous render's values; unchanged → return cached HTML, skip re-rendering. Default ON; opt-out via `{% no_cache %}` for subtrees with hidden side effects. Closes the gap to React Compiler **structurally** — Rust at parse time has more AST visibility than React Compiler's JS-level inference. Profile target: a 1000-row table benchmark on a single-row mutation; measure render % skipped. ~2 weeks. Subsumes the existing manual `{% fragment %}` cache by making it default.

- **React Server Components analog: client-side islands story (post-1.0)** — RSC's user-facing value is "opt INTO client-side stateful islands inside an otherwise server-rendered page." djust already has the primitive — `react_components` / `register_react_component` / `ReactMixin` (see `python/djust/react.py`) — but undocumented as the RSC story and missing Astro-style hydration directives. Concrete plan, ~1 week:
  - Audit + harden the existing React component host. Make props serialization symmetric with the LiveView wire protocol so the same Python view can pass values into either a server-rendered subtree OR a client-side React island.
  - Add hydration directives: `{% react_component "Chart" hydrate="visible" %}` (Astro `client:visible`/`idle`/`load`/`media` equivalents). Browser doesn't load the React bundle until the island scrolls into view / browser idles / media query matches.
  - Generic component-host abstraction so the same surface works for Vue/Solid/Preact.
  - Document with 3 patterns: animation-heavy island (Framer Motion), third-party-React-only library (react-pdf, react-flow), gradual-migration island (existing React app embedded in a djust shell).
  - Closes the RSC gap **fully** for the user-visible pattern. The thing it doesn't give — RSC's `'use client'` import-graph parser — isn't useful in a Python framework; the boundary is at the template tag, not the import statement.
- **Speculation Rules API** — Chrome's Speculation Rules API (`<script type="speculationrules">`) enables browser-native prefetching and prerendering of likely navigation targets. More powerful than `<link rel="prefetch">` — the browser actually pre-renders the entire page in a hidden tab. Evaluate generating speculation rules from `live_session` route maps so the browser pre-renders likely next pages automatically.
- **Cross-document View Transitions (Level 2)** — View Transitions API Level 2 supports cross-document transitions (MPA, not just SPA). This means djust's full-HTTP navigations (not just `live_redirect`) can animate smoothly. Evaluate whether djust should inject `@view-transition` CSS and `pagereveal`/`pageswap` event handlers automatically for `live_redirect` targets.
- **Shared Element Transitions** — Chrome's shared element transitions allow specific elements (images, cards, headers) to animate smoothly between pages/states. Combined with View Transitions API, this creates native-app-quality navigation. Evaluate generating `view-transition-name` from `dj-key` attributes so keyed elements animate between renders automatically.
- **WebGPU compute for VDOM diffing** — WebGPU is shipping in all major browsers. Evaluate whether large VDOM tree diffs (1000+ nodes) could benefit from GPU-accelerated parallel comparison. Speculative — the overhead of GPU dispatch may exceed the diff cost for typical tree sizes.
- **Django async views integration** — Django 4.1+ supports `async def` views natively. Evaluate deeper integration: `async def mount()`, `async def handle_event()`, native `await` in event handlers without `start_async` wrapper. Could simplify the async story significantly for Django 5.0+ projects.
- **Trusted Types API** — Chrome enforces Trusted Types to prevent DOM XSS. Evaluate ensuring all djust client-side DOM writes (`innerHTML` in morph, streaming HTML injection) go through Trusted Types policies. This would make djust the first LiveView framework with Trusted Types compliance — a selling point for enterprise/security-conscious teams.
- **Federated LiveView (cross-origin embedding)** — Evaluate a protocol for embedding a LiveView from one Django app inside another app's page, with cross-origin WebSocket communication. Use case: microservices architecture where each team owns a LiveView widget. Related to the WebComponent export idea but more dynamic.

### Phoenix LiveView gap-closing (post-1.0)

Items below close concrete Phoenix LiveView features that djust doesn't have. Ordered by leverage.

- **`djust-native` (mobile, post-1.0)** — Phoenix LiveView Native renders the same LiveView class via SwiftUI / Jetpack Compose / Web. Biggest single Phoenix advantage today; teams building web + mobile from one codebase currently have to choose Phoenix. Concrete plan, ~2-3 month project as a separate `djust-native` package:
  - Same `LiveView` Python class, same WebSocket transport, but native renderer emits SwiftUI/Compose widget commands instead of HTML patches.
  - Reuses the existing wire protocol (mount/event/patch frames). New: a `render_native()` method that emits widget JSON instead of HTML.
  - Templates need a native-equivalent format. Phoenix uses `.heex` for HTML and `.swiftui.heex` for native; djust would use `.html` + `.native.json` (or DSL).
  - Worth a dedicated maintainer or contributor since it's its own platform-team-ish effort. Doesn't block 1.0 of djust core.

- **`used_input?` server-side input-touched tracking (post-1.0)** — Phoenix's `Phoenix.Component.used_input?/2` tracks whether a form input was edited so error messages don't show on un-touched fields. djust doesn't have this; the matrix below at "**`used_input?` (server-side)**" is currently "Not started." Concrete plan, ~1 week:
  - Track per-input dirty state in the form mixin (`python/djust/forms.py` / `mixins/form.py`).
  - Expose `used_input(field_name) -> bool` for templates.
  - Wire automatically into `LiveViewForm` so existing form-validation views opt in for free.

- **OpenTelemetry event taxonomy (post-1.0)** — Phoenix emits standardized `[:phoenix, :live_view, :mount]` Telemetry events that DataDog / New Relic / Honeycomb integrations key off. djust has observability but with djust-specific event shapes, so off-the-shelf APM dashboards don't work. Concrete plan, ~2 weeks:
  - Emit OpenTelemetry spans with conventional names: `djust.live_view.mount`, `djust.live_view.event`, `djust.live_view.render`, `djust.live_view.patch`, `djust.streaming.chunk`.
  - Span attributes follow OTel semantic conventions (`http.route`, `user.id`, `code.namespace`).
  - Subsumes the existing observability log handler; the OTel-aware backends (Honeycomb, DataDog) get rich traces out of the box.

- **`djust.pubsub.broadcast` first-class abstraction (post-1.0)** — djust has Channel groups + `push_to_view` + PostgreSQL NOTIFY but it's not as composable as `Phoenix.PubSub.broadcast(MyApp.PubSub, "topic", msg)`. Concrete plan, ~1 week:
  - Wrap the existing primitives in `djust.pubsub.broadcast(topic: str, payload: dict, *, backend: str = "channels")`.
  - Backends: `channels` (default), `redis`, `pg_notify`. Pluggable.
  - Subscribe via `@subscribe_to("topic")` decorator on `handle_info` methods.
  - The composition gain: handlers fan out via the same primitive regardless of backend; tests substitute in-memory.

### Rust expansion (post-1.0)

Items below expand Rust's footprint beyond the existing template-engine / VDOM / fragment-cache surface, while preserving Django compatibility (no user-code surface change). Smaller / lower-risk Rust polish items live in the v0.10.0 milestone above; items here are bigger surface area or higher risk and want post-1.0 soak.

- **Form validation hybrid (post-1.0)** — Django form validation is all Python; Rust can handle the mechanical layer (type coercion, regex match, length/range bounds) without touching user-defined `clean_X` methods. Concrete plan, ~2 weeks:
  - User-written `clean_email()` / `clean()` Python methods unchanged. Rust runs FIRST for built-in validators (`MinLengthValidator`, `EmailValidator`, `RegexValidator`, `URLValidator`); Python `clean_*` runs on the already-coerced output.
  - `EmailValidator` regex in Rust is ~30× faster than Django's Python-regex equivalent; aggregate matters for big admin forms.
  - First-PR shape: `crates/djust_forms/src/validators.rs`. `LiveViewForm` opt-in via `class Meta: rust_validators = True`.
  - Why post-1.0: every Django form touches it. Needs careful soak.

- **Pre-render cache for static-ish routes (post-1.0)** — Marketing pages (homepage, docs landing) re-render on every WebSocket mount. They almost never change. A Rust process at deploy-time pre-renders initial HTML for top-N routes into a CDN-friendly cache; the WebSocket only sends patches if state diverges. Concrete plan, ~3 weeks:
  - Reuses the existing Rust template engine (already shipped) to bake initial HTML into static files at deploy time.
  - `manage.py djust_prerender` command emits `staticfiles/djust-prerender/<route_hash>.html`. Middleware serves from cache when present.
  - WebSocket subscription pulls from the cache, then takes over for live patches.
  - Win: TTI on marketing pages drops from "WebSocket connect + initial render" to "static HTML + WebSocket upgrade." Real meaningful for SEO / first-paint perception.

- **Rust + WASM client patcher (post-1.0, v1.x or v2.x ambitious bet)** — The biggest single Rust opportunity djust hasn't taken yet: replace `client.js` (~87 KB gzipped raw / ~37 KB minified target) with a Rust-compiled WASM patcher. Wire protocol unchanged — just a different patcher implementation on the client side. ~2-3 month project. Wins:
  - **Bundle size**: ~50% reduction (target ~30-40 KB gzipped).
  - **Apply perf**: VDOM apply in Rust > VDOM apply in JS, especially for large diffs (1000+ node tables).
  - **Code sharing**: client and server share the same VDOM types from `crates/djust_vdom` — eliminates "the JS patcher and Rust differ on edge case X" failure class.
  - Risks: WASM has different lazy-loading semantics; some browsers throttle WASM compile. Need careful soak. Bench target: VDOM apply on 1000-row table change in <2ms.
  - Why post-1.0 (v1.x or v2.x): too risky for the 1.0 stability bar. Subsumes the existing "Rust-side WASM compilation" entry above.

### JS/React ecosystem strategy (post-1.0)

Three ship items + one explicit non-goal. Together they define how djust relates to the JS ecosystem without becoming JS-flavored.

- **`@djust/react` bridge — TanStack Query replacement (post-1.0, biggest external lever)** — Separate package; doesn't touch djust core. Ships a `useDjust(path)` React hook that subscribes to a LiveView's state stream over WebSocket and exposes it as React state. Concrete plan, ~2-3 months for v1:
  - **Server-side**: a "state-mode" WebSocket variant emits JSON state diffs instead of DOM patches. Additive — existing morph mode untouched. Reuses the wire protocol's existing assigns-on-patch shape; only the client interpreter differs.
  - **Client-side**: `@djust/react` npm package with hooks: `useDjust(path)` (subscribe + read state), `useDjustEvent(name)` (send events), `useDjustOptimistic()` (matches React 19 `useOptimistic` shape), `useDjustAction()` (matches `useActionState`).
  - **TypeScript types** generated from view classes (similar to existing `djust_typecheck`); `useDjust<TodoListView>("/todos/")` is fully typed.
  - **Marketing positioning: "TanStack Query, but real-time. And free, because djust does it server-side."** TanStack Query's 5M weekly downloads solve four problems (cache-fetched-data, auto-revalidate, mutate-with-rollback, cross-tab-sync); djust's bridge solves all four BETTER, structurally — server pushes when state changes, no polling, no manual cache invalidation, optimistic updates first-class, cross-tab sync free via shared WebSocket. The 5-line `useDjust()` example beats the 20-line TanStack Query equivalent on every metric.
  - **Why this matters**: React has the largest dev mind-share. djust is invisible to that audience today. The bridge is the lever. Far bigger top-of-funnel than the Django community by itself.
  - **Lives in a separate repo / npm package** (`djust-org/djust-react`); doesn't pollute djust core. Reuses the React-islands hosting infrastructure from the RSC analog item above (compounding value).

- **"djust hooks starter" — reference patterns for `dj-hook` (post-1.0, sharpens existing primitive)** — djust already has the JS-component primitive: `{% colocated_hook %}` + `dj-hook` element bindings. What's missing is curation. Concrete plan, ~2 weeks of focused docs work, no new infrastructure:
  - 10 reference patterns in `docs.djust.org/content/website/guides/`: chart wrapper (Chart.js), autocomplete dropdown, file dropzone with preview, drag/drop reorder, infinite scroll, keyboard shortcut handler, modal manager, copy-to-clipboard, virtualized list, observable scroll position. Each ~50 LoC.
  - Each pattern is plain JS — no React, no build pipeline, no node_modules. Just `dj-hook` + the colocated_hook tag.
  - Closes the gap users hit when they need <16ms response time (animation, drag/drop) or third-party DOM-API libraries.
  - Frames the existing primitive as the canonical answer to "how do I write a custom widget in djust?" so users stop reaching for React/Vue/Stimulus by default.

- **RSC-style islands (cross-reference)** — already captured above as a separate entry. Note: the React bridge above and the islands story compound — same React-hosting infrastructure serves both "embed a React island for animation" (islands) and "drive a React tree from a djust LiveView" (bridge). One implementation, two product narratives.

- **Explicit NON-GOAL: our own React component library** — djust will NOT ship `@djust/components-react` competing with shadcn/ui, Radix, MUI, Chakra, Mantine, NextUI. Reasons:
  - Saturated market. Each competitor has years of polish, design-system consistency, accessibility audits, dark-mode/RTL/i18n support.
  - Wrong shape. djust's value is "Python framework + reactivity"; React component libraries are a 5-year, 10-person-company effort with zero defensible moat.
  - Better strategy: make existing React libraries embeddable via the islands story. djust serves the data; React serves the components. Drop in shadcn/ui, drop in Recharts, drop in TanStack Table — all work because of the bridge + islands infrastructure.
  - The existing `djust-components` package ships *server-rendered* components — that's the right shape for djust core. React component libraries belong in the React ecosystem.

### Moonshots — post-1.0 candidates

Bare technical sketches. Each item lists what it does and which files/crates it touches; sequencing, effort estimates, and competitive framing are tracked privately.

- **M1. Time-travel-driven test generation.** Export the time-travel ring buffer as a runnable pytest file with state-shape assertions. Touches: `python/djust/time_travel.py`, `python/djust/testing.py`, new `python/djust/management/commands/djust_gentest.py`.
- **M2. Production time-travel debugging.** Field-level redaction (`@redact` decorator) + safe encrypted export + admin import-into-sandbox tool, on top of the existing dev-only ring buffer. Touches: `python/djust/time_travel.py`, new `python/djust/admin/replay.py`.
- **M3. Native CRDT primitives `assign_crdt()`.** Conflict-free real-time collaborative state, server-side fan-out + client-side ops applier. Touches: new crate `crates/djust_crdt/` (port Automerge or Yrs), new `python/djust/mixins/crdt.py`, new `python/djust/static/djust/src/30-crdt-applier.js`.
- **M4. AI-native components `{% chat %}` / `{% rag_search %}` / `{% agent_workflow %}`.** Template tags wiring up streaming LLM, tool dispatch, conversation state, retry, error UI. Touches: new `python/djust/contrib/ai/templatetags/ai.py`, new optional `djust-ai` extras package + provider plugins.
- **M5. Declarative real-time queryset subscriptions `subscribe(queryset)`.** ORM dependency-tracking + filter-aware fan-out + coalescing on top of the shipped `@notify_on_save` LISTEN/NOTIFY bridge. Touches: `python/djust/db.py`, new `python/djust/mixins/realtime_query.py`, Rust dataflow integration with v0.10 auto-memoization.
- **M6. Schema-first scaffolding.** `djust generate <Model>` emits model + LiveView (CRUD) + template + form + admin + REST + GraphQL + OpenAPI + tests. Every output respects existing canon (CSP-strict, CSRF, ARIA).
- **M7. Live state migration across versions.** Extend HVR (currently dev-only) to PROD via `__migrate_state__` classmethod that maps v1 state shape → v2 shape; WebSocket stays connected through the deploy.
- **M8. Generative UI from natural language.** `djust generate "<description>"` calls a configurable LLM with introspected schema + auth context, emits LiveView + template + queries + tests. Depends on M4 + M6.
- **M9. Built-in observability dashboard.** Self-hosted dashboard at `/admin/djust/observability/`: active sessions, P50/P95/P99 per handler, error rates, time-travel buffer occupancy, slow-query/N+1. Builds on v0.10 OTel taxonomy.
- **M10. Edge runtime target.** `djust deploy edge --provider cloudflare`. Compiles djust runtime to Pyodide/RustPython on Cloudflare Workers / Deno Deploy. Subsumes the existing "Rust-side WASM compilation" entry. Feasibility prototype required before committing.
- **M11. Component marketplace ("djust hub") with state-aware live previews.** Browse community-built `LiveComponent`s; click "preview" → component runs in a sandbox iframe with real interactive state (not screenshots).

(Sequencing, effort estimates, competitive framing, and the strategic-bets analysis are tracked in a private strategy doc.)

---

### Phoenix LiveView gaps that are NOT closable

Documented for honesty — these are architectural impossibilities given Python, not roadmap items:

- **BEAM/GenServer crash isolation** — Phoenix LiveViews are supervised processes; a crash restarts cleanly without affecting siblings. Python uses async tasks. Mitigation is best-effort try/except wrappers (already in djust); no equivalent to "the supervisor restarts your view."
- **Distribution** — Phoenix can run LiveViews on different BEAM nodes via Erlang Distribution; clients don't know. djust's cross-process story is Channel-layer Redis fan-out, which works but is nowhere near `:rpc.call` ergonomics.
- **Hot code upgrade in production** — Phoenix can swap GenServer modules at runtime, preserving in-flight state across deploys. djust HVR is dev-only and view-class-only; production deploys spin up new workers. Probably not closable in any practical Python way.
| ~~**Lock (prevent double-fire)**~~ | ~~**Event ack protocol**~~ | — | ✅ **Shipped** — `dj-lock` (event-binding.js, response-handler.js) | **v0.4.0** |
| ~~**Auto-recover (custom)**~~ | ~~**`phx-auto-recover`**~~ | — | ✅ **Shipped** — `dj-auto-recover` reconnect handler (event-binding.js:1414, websocket.js:126,358,421) | **v0.4.0** |
| ~~**Cloak (FOUC prevention)**~~ | — | ~~**`v-cloak` (Vue)**~~ | ✅ **Shipped** — `dj-cloak` (websocket.js + namespace.js) | **v0.4.0** |
| ~~**`on_mount` hooks**~~ | ~~**`on_mount/1`**~~ | — | ✅ **Shipped** — `python/djust/hooks.py` + `live_view.py` | **v0.4.0** |
| ~~**Flash messages**~~ | ~~**`put_flash/3`**~~ | ~~**Toast libraries**~~ | ✅ **Shipped** — `FlashMixin` + `static/djust/src/23-flash.js` | **v0.4.0** |
| ~~Latency simulator~~ | Built-in | — | ✅ **Done** | v0.4.0 |
| ~~Keyboard shortcuts~~ | — | ~~`react-hotkeys-hook`~~ | ✅ **Done** | v0.4.0 |
| ~~Copy to clipboard~~ | — | ~~`navigator.clipboard`~~ | ✅ **Shipped** — `dj-copy` (event-binding.js) | **v0.4.0** |
| ~~**JS Commands from hooks**~~ | ~~**Programmable JS API**~~ | — | ✅ **Shipped** — `static/djust/src/26-js-commands.js` (fluent chain API) + `python/djust/js.py` Python builder | **v0.4.1** |
| ~~**Scoped JS selectors**~~ | ~~**`to: {:closest}`**~~ | — | ✅ **Shipped** — `python/djust/js.py` + client.js (closest/scoped selector support) | **v0.4.1** |
| ~~**`page_loading` on push**~~ | ~~**`page_loading: true`**~~ | — | ✅ **Shipped** — `static/djust/src/24-page-loading.js` | **v0.4.1** |
| ~~`assign_async` / `AsyncResult`~~ | ~~`assign_async/3`~~ | ~~`<Suspense>`~~ | ✅ **Shipped** — `python/djust/async_result.py` + `mixins/async_work.py:121` + `components/suspense.py` | **v0.5.0** |
| ~~**`handle_async` callback**~~ | ~~**`handle_async/3`**~~ | — | ✅ **Shipped** — `LiveView.handle_async_result(name, result, error)` (live_view.py:236) dispatched from `websocket.py:819,869` | **v0.5.0** |
| ~~Component `update` callback~~ | ~~`update/2`~~ | ~~`getDerivedStateFromProps`~~ | ✅ **Shipped** — `Component.update(**kwargs)` (components/base.py:206) | v0.5.0 |
| View Transitions API | — | View Transitions | **Not started** *(no `startViewTransition` / `viewTransition` references in JS modules)* | v0.5.0 |
| ~~Nested components~~ | ~~`LiveComponent`~~ | ~~Component tree~~ | ✅ **Shipped** — `LiveComponent` class (components/base.py) + registry | v0.5.0 |
| ~~Targeted events (`@myself`)~~ | ~~`phx-target`~~ | — | ✅ **Shipped** — `dj-target` attribute (event-binding.js:527,668,886; schema.py:141) for scoped updates | v0.5.0 |
| ~~Named slots~~ | ~~`slot/3` macro~~ | ~~`children` / slots~~ | ✅ **Shipped** — function components with declarative `Assign` slot attrs (`components/function_component.py` + `assigns.py`) | v0.5.0 |
| ~~Direct-to-S3 uploads~~ | ~~`presign_upload`~~ | — | ✅ **Shipped** — `python/djust/contrib/uploads/s3_presigned.py` + `s3_events.py` (v0.5.7 — closes #820) | v0.5.0 |
| ~~Stream limits + viewport~~ ✅ | ~~`:limit`, viewport events~~ | ~~Virtualization~~ | ~~Not started~~ **Shipped** | v0.5.0 |
| ~~**Viewport top/bottom (streams)**~~ ✅ | ~~**`phx-viewport-top/bottom`**~~ | — | ~~**Not started**~~ **Shipped** | **v0.5.0** |
| ~~`handle_info`~~ | ~~`handle_info/2`~~ | — | ✅ **Shipped** — `handle_info` (mixins/activity.py + mixins/notifications.py + websocket.py dispatch) | v0.5.0 |
| ~~Template fragments~~ | ~~HEEx static tracking~~ | — | ✅ **Shipped** — Rust-side static-subtree fingerprinting (`crates/djust_live` `clear_fragment_cache` + `build_fragment_text_map`) | v0.5.0 |
| **`used_input?` (server-side)** | **`used_input?/2`** | — | **Not started** *(no `used_input` / `_used_inputs` references in tree)* | **v0.5.0** |
| ~~**Declarative assigns**~~ | ~~**`attr/3`, `slot/3`**~~ | ~~**PropTypes/TS**~~ | ✅ **Shipped** — `components/assigns.py` `Assign` class (type-checked attrs + defaults + validation) used by `function_component.py` | **v0.5.0** |
| ~~**Function components**~~ | ~~**`Phoenix.Component`**~~ | ~~**Function components**~~ | ✅ **Shipped** — `python/djust/components/function_component.py` (`@component` decorator + `{% call %}` tag) | **v0.5.0** |
| Selective re-rendering | Per-component diff | Reconciliation | ✅ **Shipped** — VDOM partial render path (`crates/djust_templates` `render_nodes_partial`) re-renders only nodes whose deps intersect changed keys | v0.5.0 |
| Attribute spread (`@rest`) | `{@rest}` | `...props` | **Not started** *(no `rest_attrs` / `attr_spread` references in components/)* | v0.5.0 |
| ~~**Ignore attributes (client-owned)**~~ ✅ | `JS.ignore_attributes` | — | **Shipped v0.5.0** | v0.5.0 |
| ~~**Colocated JS hooks + namespacing**~~ ✅ | `ColocatedHook` | — | **Shipped v0.5.0** | v0.5.0 |
| ~~**`UploadWriter` (stream upload)**~~ | ~~**`UploadWriter`**~~ | — | ✅ **Shipped in v0.5.0** | v0.5.0 |
| ~~**Keyed for-loop change tracking**~~ | ~~**Auto in comprehensions**~~ | — | ✅ **Shipped** — `crates/djust_vdom/src/parser.rs` per-item change detection in `{% for %}` loops (via `dj-key`) | **v0.5.0** |
| ~~**`self.defer()` (post-render)**~~ | ~~**`send(self(), ...)`**~~ | ~~`useEffect` (post-render)~~ | ✅ **Shipped (v0.8.5)** — `python/djust/mixins/async_work.py` `defer()` + `_drain_deferred()` + `LiveViewConsumer._flush_deferred()` (10 post-render-flush sites in `websocket.py`) — Phoenix-parity post-render callback scheduling | **v0.5.0** |
| **Testing utilities** | **`LiveViewTest`** | **Testing Library** | **Basic** (`LiveViewTestClient`) | **v0.5.1** |
| **Error overlay (dev)** | Error page | **Next.js overlay** | ✅ Shipped (v0.5.1) | v0.5.1 |
| Computed/derived state | — | `useMemo` | ✅ Shipped (v0.5.1) | v0.5.1 |
| Lazy component loading | — | `React.lazy()` | ✅ Shipped (LiveView-level, PR #54) | v0.5.1 |
| Component context sharing | — | `useContext` | ✅ Shipped (v0.5.1) | v0.5.1 |
| Trigger form action | `phx-trigger-action` | — | ✅ Shipped (v0.5.1) | v0.5.1 |
| Nested forms | `inputs_for/4` | Formik nested | ✅ Shipped (v0.5.1) | v0.5.1 |
| Scoped loading states | `phx-loading` | Suspense per-query | ✅ Shipped (v0.5.1) | v0.5.1 |
| Error boundaries | — | `<ErrorBoundary>` | ✅ Shipped (PR #773) | v0.5.1 |
| **Native `<dialog>`** | — | — | ✅ Shipped (v0.5.1) | v0.5.1 |
| **Stable component IDs** | — | **`useId`** | ✅ Shipped (v0.5.1) | v0.5.1 |
| **Form status awareness** | — | **`useFormStatus`** | **Partial** — `@action` decorator (decorators.py:233) provides `_action_state[name] = {pending, error, result}` for mutation handlers; `useFormStatus`-style template-level read of "any in-flight action on this form" not specifically wired | **v0.8.0** |
| **Dirty tracking** | — | — | ✅ Shipped (v0.5.1) | v0.5.1 |
| ~~Animations / transitions~~ | ~~`JS.transition`~~ | ~~`<AnimatePresence>`~~ | ✅ **Shipped** — `dj-transition` attribute (parsing + transitionend + fallback timer) | v0.6.0 |
| ~~Transition groups (lists)~~ | — | ~~`<TransitionGroup>`~~ | ✅ **Shipped** — `dj-transition-group` (FLIP-style list transitions) | v0.6.0 |
| ~~Exit animations~~ | ~~`phx-remove`~~ | ~~`<AnimatePresence>`~~ | ✅ **Shipped** — `dj-remove` (`static/djust/src/42-dj-remove.js` + `12-vdom-patch.js` integration) | v0.6.0 |
| ~~Streaming initial render~~ ✅ | — | `renderToPipeableStream` | ✅ Shipped v0.6.1 (Phase 1); lazy-child Phase 2 v0.6.2 | **v0.6.1** |
| ~~Time-travel debugging~~ ✅ | — | Redux DevTools | ✅ Shipped v0.6.1 | **v0.6.1** |
| ~~Sticky LiveViews~~ ✅ | `sticky: true` | — | Shipped v0.6.0 | v0.6.0 |
| ~~DOM mutation events~~ | — | ~~MutationObserver~~ | ✅ **Shipped** — `dj-mutation` (`static/djust/src/37-dj-mutation.js`) + observer drain follow-ups #879/#880/#881/#882 | v0.6.0 |
| ~~Sticky scroll~~ | — | ~~Chat/log UX~~ | ✅ **Shipped** — `dj-sticky-scroll` (`static/djust/src/38-dj-sticky-scroll.js`) | v0.6.0 |
| ~~CSP nonce~~ | ~~Built-in~~ | — | ✅ **Shipped** — `python/djust/utils.py` `get_csp_nonce` (django-csp integration; nonce attribute on injected scripts — see #655) | v0.6.0 |
| ~~Viewport events~~ | — | ~~`IntersectionObserver`~~ | ✅ **Shipped** — `dj-viewport-top/bottom` (`30-infinite-scroll.js`) + lazy hydration (`13-lazy-hydration.js`) | v0.6.0 |
| Multi-tab sync | — | BroadcastChannel | **Not started** *(no `BroadcastChannel` / `multi_tab` references in tree)* | v0.6.0 |
| Offline mutation queue | — | Service Worker | **Not started** *(`pwa/service_worker.py` ships SW registration but no offline-mutation-queue replay pattern)* | v0.6.0 |
| Element resize events | — | ResizeObserver | **Partial** — `ResizeObserver` is used internally by `29-virtual-list.js` for variable-row-height tracking; a public `dj-resize` user-facing event-binding is not exposed | v0.6.0 |
| State undo/redo | — | `use-undo` | **Not started** *(no `UndoMixin` / undo-redo ring-buffer pattern in tree)* | v0.6.0 |
| Connection multiplexing | Channel multiplexer | — | **Not started** *(verified: no `multiplex` / `MultiplexedSocket` references in tree)* | v0.6.0 |
| ~~**CSS `@starting-style`**~~ ✅ | — | Framer Motion | ~~**Not started**~~ **Documented v0.6.0 (PR #973)** — browser-native enter animations work unmodified with djust's VDOM insert path; docs/website/guides/declarative-ux-attrs.md has a comparison section vs `dj-transition`. | **v0.6.0** |
| ~~**Hot View Replacement**~~ ✅ | Code reloading | Fast Refresh | ~~**Not started**~~ **✅ Shipped v0.6.1** — state-preserving `__class__` swap + VDOM re-render on .py save; see `docs/website/guides/hot-view-replacement.md`. | **v0.6.1** |
| Stale-while-revalidate | — | SWR / React Query | **Partial** — service-worker uses SWR cache strategy (`pwa/service_worker.py`); LiveView-level stale-while-revalidate (`assign_async`-style with cached-then-fresh) not specifically implemented | v0.7.0 |
| `live_session` enhancements | `live_session/3` | — | Basic done | v0.7.0 |
| ~~Push navigate (SPA nav)~~ | ~~`push_navigate`~~ | — | ✅ **Shipped** — `live_view.py` + `routing.py` (`live_redirect` / `push_navigate` SPA nav with `live_session`) | v0.7.0 |
| Portal rendering | **`<.portal>`** (1.1) | `createPortal` | **Not started** *(no `dj-portal` / `live_portal` references in tree)* | v0.7.0 |
| ~~Back/forward restoration~~ | ~~`push_patch` state~~ | ~~Loader cache~~ | ✅ **Shipped** — `static/djust/src/18-navigation.js` (history.pushState + popstate with state-snapshot lookup, line 135,189-202) | v0.7.0 |
| Server-only components | — | Server Components | **Not started** *(no `ServerComponent` / `@server_component` references in tree)* | v0.7.0 |
| Islands of interactivity | — | Astro islands | Not started (deferred from v0.7.0 retro) | v0.7.1 |
| ~~AI streaming primitives~~ | — | — | ✅ **Shipped** — `python/djust/streaming.py` `StreamingMixin` (token-by-token DOM updates via `stream_to(...)`) | v0.7.0 |
| ~~Server functions (RPC)~~ | — | ~~Server Actions~~ | ✅ **Shipped** — `@server_function` decorator (`python/djust/decorators.py:401`) | v0.7.0 |
| ~~Django admin LiveView widgets~~ | — | — | ✅ Shipped (v0.7.0) | v0.7.0 |
| ~~Prefetch on hover/intent~~ | — | ~~Remix prefetch~~ | ✅ **Shipped** — `static/djust/src/22-prefetch.js` + `dj-prefetch` template tag | v0.7.0 |
| ~~**Keep-Alive / Activity**~~ | — | ~~**`<Activity>`** (19.2)~~ | ✅ **Shipped** — `static/djust/src/49-activity.js` + `templatetags/live_tags.py` `{% dj_activity %}` (server-canonical visibility) | **v0.7.0** |
| ~~**Document metadata**~~ | ~~`live_title`~~ | ~~**Native** (React 19)~~ | ✅ **Done** | v0.4.0 |
| **Type-safe template validation** | — | TypeScript | ✅ Shipped (v0.5.1) | v0.5.1 |
| ~~**Streaming markdown renderer**~~ | — | — | ✅ **Shipped (v0.7.0)** | **v0.7.0** |
| ~~**DB change notifications**~~ ✅ | ~~**PubSub + Ecto**~~ | — | **Shipped** | **v0.5.0** |
| ~~**Virtual/windowed lists**~~ ✅ | — | ~~**`react-window`**~~ | ~~**Not started**~~ **Shipped** | **v0.5.0** |
| **Multi-step wizard** | — | **`react-hook-form`** | ✅ **Shipped (PR #632)** | **v0.5.1** |
| ~~**Paste event handling**~~ | — | ~~**`onPaste`**~~ | ✅ **Shipped** — `dj-paste` (event-binding.js:760 `pasteHandler` + uploads.js:750 clipboard upload pipeline) | **v0.4.1** |
| ~~**Standalone `{% live_input %}` template tag**~~ | — | — | ✅ **Shipped (#650, PR #668)** | v0.4.1 |
| ~~**WebSocket Origin validation (CSWSH fix)**~~ | ~~`check_origin/2`~~ | — | ✅ **Shipped (#653, PR #658)** | v0.4.1 |
| ~~**Gate `timing`/`performance` on DEBUG**~~ | — | — | ✅ **Shipped (#654, PR #663)** | v0.4.1 |
| ~~**Nonce-based CSP support**~~ | — | ~~React nonce~~ | ✅ **Shipped (#655, PR #664)** | v0.4.1 |
| ~~**`djust_audit` declarative permissions (`--permissions`)**~~ | — | — | ✅ **Shipped (#657, PR #665)** | v0.4.1 |
| ~~**`djust_audit` ASGI stack + config static checks**~~ | — | — | ✅ **Shipped (#659, PR #666)** | v0.4.1 |
| ~~**`djust_audit` AST-based anti-pattern scanner**~~ | — | — | ✅ **Shipped (#660, PR #670)** | v0.4.1 |
| ~~**`djust_audit --live` runtime header probe**~~ | — | — | ✅ **Shipped (#661, PR #667)** | v0.4.1 |
| ~~**Scroll into view**~~ | — | ~~**`scrollIntoView`**~~ | ✅ **Shipped** — `dj-scroll-into-view` (Quick Wins #14a) | **v0.4.0** |
| ~~**WS compression**~~ | ~~**Built-in (Cowboy)**~~ | — | ✅ **Shipped** — `config.py:65` `websocket_compression: True` default + `mixins/post_processing.py:245` propagation (`window.DJUST_WS_COMPRESSION` + ASGI server permessage-deflate) | **v0.6.0** |
| ~~**Runtime layout switching**~~ ✅ | Runtime layouts (1.1) | — | **Shipped v0.6.0** | **v0.6.0** |
| **i18n live switching** | — | — | **Not started** *(no `set_language` / `live_translation` references in tree)* | **v0.7.0** |

---

## Contributing

Want to help? See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

High-impact areas for contributions:

#### Quick Wins (< 1 day, great first contributions)
1. ~~**`dj-value-*` static params**~~ ✅
2. ~~**`_target` param in change events**~~ ✅
3. ~~**`dj-disable-with`**~~ ✅
4. ~~**Connection state CSS classes**~~ ✅
5. ~~**`dj-copy`**~~ ✅
6. ~~**`dj-cloak`**~~ ✅
7. ~~**`live_title`**~~ ✅
8. ~~**`dj-click-away`**~~ ✅
9. ~~**`dj-lock`**~~ ✅
10. ~~**`dj-page-loading`**~~ ✅
11. ~~**Native `<dialog>` integration**~~ ✅ **Shipped in v0.5.1** — `dj-dialog="open|close"` with MutationObserver sync.
12. ~~**`dj-no-submit`**~~ ✅ Shipped — `static/djust/src/34-form-polish.js` (Enter-key swallow with mode parsing)
13. ~~**`page_loading` on `dj.push`**~~ ✅ Shipped — `static/djust/src/24-page-loading.js` (loading bar during heavy events)
14. ~~**`dj-scroll-into-view`**~~ ✅

#### Medium Effort (1-3 days)
14. ~~**`self.defer(callback)`**~~ ✅ **Shipped (v0.8.5)** — `mixins/async_work.py` `defer()` + `_drain_deferred()` (Phoenix-parity post-render scheduling)
15. ~~**`dj-shortcut`**~~ ✅
15. ~~**`dj-debounce`/`dj-throttle` HTML attributes**~~ ✅
16. ~~**`on_mount` hooks**~~ ✅ Shipped — `python/djust/hooks.py` + `live_view.py` integration
17. ~~**Flash messages**~~ ✅ Shipped — `FlashMixin` (live_view.py:41,142) + `static/djust/src/23-flash.js` auto-dismiss
18. ~~**`handle_params` callback**~~ ✅ Shipped — `LiveView.handle_params(params, uri)` (live_view.pyi:60, schema-tracked)
19. ~~**`dj-mounted`**~~ ✅
20. ~~**`dj-sticky-scroll`**~~ ✅ Shipped — `static/djust/src/38-dj-sticky-scroll.js` (auto-scroll chat/log containers)
21. ~~**`dj-lazy` viewport loading**~~ ✅ **Shipped (PR #54)** — lazy LiveView hydration (viewport/click/hover/idle) in `13-lazy-hydration.js`
22. **Multi-tab sync** — BroadcastChannel API integration, ~60 lines JS *(genuinely pending — no `BroadcastChannel` / `multi_tab` references in tree)*
23. **View Transitions API** — Animated page transitions, ~60 lines JS *(genuinely pending — no `startViewTransition` / `viewTransition` references in JS modules)*
24a. ~~**`dj-paste`**~~ ✅ Shipped — `static/djust/src/09-event-binding.js:760` (`pasteHandler`) + `15-uploads.js:750` (clipboard upload pipeline)
24. ~~**`dj-viewport-top`/`dj-viewport-bottom`**~~ ✅ Shipped in v0.5.0 — Bidirectional infinite scroll (`30-infinite-scroll.js` + stream `limit` kwarg)
25. **`used_input?` (server-side feedback)** — Server-side field touched tracking, ~40 lines Python + ~10 lines JS *(genuinely pending — no `used_input` / `_used_inputs` references in tree)*
26. **Programmable JS Commands from hooks** — Expose DJ command API to dj-hook callbacks *(JS Commands core shipped via `26-js-commands.js`; "expose to hook callbacks" surface unverified — leave open until specifically audited)*
27. ~~**Stable component IDs**~~ ✅ Shipped (v0.5.1) — see Phoenix LiveView Parity Tracker row "Stable component IDs"
28. ~~**Dirty tracking**~~ ✅ Shipped (v0.5.1) — see Phoenix LiveView Parity Tracker row "Dirty tracking"
29. ~~**`dj-ignore-attrs`**~~ ✅ Shipped — `static/djust/src/31-ignore-attrs.js` + `12-vdom-patch.js` integration

#### Major Features
30. ~~**JS Commands**~~ ✅ Shipped — `static/djust/src/26-js-commands.js` (fluent chain API: `dj.push`, `dj.show`, `dj.hide`, `dj.add_class`, etc.) + `27-exec-listener.js` + `python/djust/js.py` Python builder
30. ~~**VDOM structural patching** (#559)~~ ✅ Fixed in PR #563
31. ~~**Function components**~~ ✅ Shipped — `python/djust/components/function_component.py` (`@component` decorator + `{% call %}` tag) + `components/rust_handlers.py` Rust engine integration
32. ~~**`assign_async`/`AsyncResult`**~~ ✅ Shipped — `python/djust/async_result.py` (`AsyncResult` class) + `mixins/async_work.py:121` (`assign_async()` method)
33. ~~**`handle_async` callback**~~ ✅ Shipped — `LiveView.handle_async_result(name, result, error)` (live_view.py:236) dispatched from `websocket.py:819,869` on success+error paths
34. ~~**Declarative component assigns**~~ ✅ Shipped — `components/assigns.py` (`Assign` class with type-checked attrs/defaults/validation) used by `function_component.py`
35. ~~**LiveView testing utilities**~~ ✅ **Shipped in v0.5.1** — 7 methods + 21 tests; see guide at `docs/website/guides/testing.md`.
36. ~~**Error overlay (dev mode)**~~ ✅ **Shipped in v0.5.1** — `36-error-overlay.js` dev panel + `docs/website/guides/error-overlay.md` guide + 10 JSDOM tests.
37. ~~**Template fragments**~~ ✅ Shipped — `crates/djust_live/src/lib.rs` `clear_fragment_cache` + `build_fragment_text_map` (Rust-side static subtree fingerprinting)
38. **Connection multiplexing** — Share one WS across multiple LiveViews, ~200 lines JS + Python *(genuinely pending — no `multiplex` / `MultiplexedSocket` references in tree)*
39. ~~**Rust template engine parity**~~ ✅ — Closed in v0.5.0: getattr fallback, attr-context escape, assign-tag handler
40. ~~**AI streaming primitives**~~ ✅ Shipped — `python/djust/streaming.py` `StreamingMixin` (token-by-token DOM updates via `stream_to(...)`, ~16ms throttle, LLM-friendly async iteration pattern)
41. **Streaming initial render** — Chunked HTTP response with progressive content loading
42. ~~**Django admin LiveView widgets**~~ ✅ **Shipped in v0.7.0** — `change_form_widgets`/`change_list_widgets` slots + `@admin_action_with_progress` + `BulkActionProgressWidget` + A072/A073 checks. See `docs/website/guides/admin-widgets.md`.
43. ~~**Hot View Replacement**~~ ✅ Shipped (v0.6.1) — see Phoenix LiveView Parity Tracker; state-preserving `__class__` swap + VDOM re-render on .py save; `docs/website/guides/hot-view-replacement.md`
44. ~~**Server Actions (`@action`)**~~ ✅ Shipped (v0.8.0) — `python/djust/decorators.py:233` (`@action` with auto-tracked `_action_state[name] = {pending, error, result}`)
45. ~~**Keyed for-loop change tracking**~~ ✅ Shipped — `crates/djust_vdom/src/parser.rs` (per-item change detection in `{% for %}` loops via `dj-key`)
46. ~~**Type-safe template validation**~~ ✅ **Shipped in v0.5.1** — `manage.py djust_typecheck` static analysis + `docs/website/guides/typecheck.md` guide + 14 tests.
47. ~~**Streaming markdown renderer**~~ ✅ **Shipped in v0.7.0** — `{% djust_markdown %}` + `djust.render_markdown` backed by pulldown-cmark 0.12, raw-HTML escaping enforced in the event-filter layer, `javascript:` URLs neutralised, provisional-line splitter for flicker-free streaming. See `docs/website/guides/streaming-markdown.md`.
48. ~~**Keep-Alive / `dj-activity`**~~ ✅ Shipped (v0.7.0) — `static/djust/src/49-activity.js` + `templatetags/live_tags.py` `{% dj_activity %}` (server-canonical visibility tracking; React 19.2 `<Activity>` parity)
49. ~~**Database change notifications**~~ ✅ Shipped in v0.5.0 — PostgreSQL LISTEN/NOTIFY → LiveView push (`@notify_on_save`, `self.listen`, `handle_info`). See `docs/website/guides/database-notifications.md`.
50. ~~**Virtual/windowed lists**~~ ✅ Shipped in v0.5.0 — DOM virtualization for large lists (`29-virtual-list.js`, fixed-height v0.5.0; variable-height v0.5.1)
51. ~~**Multi-step wizard (`WizardMixin`)**~~ ✅ **Shipped (PR #632)** — per-step validation, URL sync, progress (`python/djust/wizard.py`)
52. **i18n live language switching** — Switch locale without page reload, ~60 lines Python *(genuinely pending — no `set_language` / `live_translation` references in tree)*

#### Always Welcome
45. **Starter templates** — Build example apps that showcase djust patterns
46. **Documentation** — Improve guides, fix gaps, add cookbook recipes
47. **Test coverage** — Edge cases in VDOM diffing, WebSocket reconnection, state backends

Open an issue or discussion to propose features or ask questions.
