# djust Documentation

djust is a hybrid Python/Rust framework that brings Phoenix LiveView-style reactive server-side rendering to Django. Build real-time UIs with Python — no JavaScript framework required.

---

## Getting Started

New to djust? Start here.

|                                                              |                                                                 |
| ------------------------------------------------------------ | --------------------------------------------------------------- |
| **[Installation](getting-started/installation.md)**          | Install djust and configure Django, Channels, and ASGI          |
| **[Your First LiveView](getting-started/first-liveview.md)** | Build a reactive counter in minutes                             |
| **[Core Concepts](getting-started/core-concepts.md)**        | Understand the lifecycle, state model, and when to use LiveView |

---

## Core Concepts

Deep dives into how djust works.

|                                               |                                                                   |
| --------------------------------------------- | ----------------------------------------------------------------- |
| **[LiveView](core-concepts/liveview.md)**     | The `LiveView` class — lifecycle hooks, state, navigation, auth   |
| **[Events](core-concepts/events.md)**         | Event binding (`dj-click`, `dj-input`, etc.) and handler patterns |
| **[Components](core-concepts/components.md)** | Stateless and stateful (LiveComponent) component systems          |
| **[Templates](core-concepts/templates.md)**   | Template directives, Django syntax, VDOM keying                   |

---

## Guides

How to build specific features.

### Real-Time Features

|                                                        |                                                           |
| ------------------------------------------------------ | --------------------------------------------------------- |
| **[Streaming](guides/streaming.md)**                   | Real-time partial DOM updates for LLM chat and live feeds |
| **[Virtual Lists (`dj-virtual`)](guides/virtual-lists.md)** | Render 1000s of items with only the visible window in the DOM (fixed + variable height) |
| **[Flash Messages](guides/flash-messages.md)**         | Transient notifications with `put_flash` (Phoenix-style)  |
| **[Presence](guides/presence.md)**                     | Track online users, live cursors, typing indicators       |
| **[Uploads](guides/uploads.md)**                       | Chunked binary file uploads via WebSocket                 |
| **[Paste Events](guides/dj-paste.md)**                 | `dj-paste` — structured clipboard payloads + upload routing |
| **[Declarative UX Attributes](guides/declarative-ux-attrs.md)** | `dj-mutation`, `dj-sticky-scroll`, `dj-track-static` — small attrs that replace custom hooks |
| **[Runtime Layout Switching](guides/layouts.md)** | `self.set_layout(path)` — swap outer layout without losing inner state |
| **[Sticky LiveViews](guides/sticky-liveviews.md)**     | `sticky=True` child LiveViews that survive `live_redirect` — audio players, sidebars, notification centers |
| **[Loading States](guides/loading-states.md)**         | Spinners, skeleton screens, disabled states               |

### Client-Side Commands

|                                                        |                                                           |
| ------------------------------------------------------ | --------------------------------------------------------- |
| **[JS Commands](guides/js-commands.md)**               | `show`, `hide`, `toggle`, `add_class`, `transition`, `push`, … — client-side DOM ops with Phoenix LV 1.0 parity |
| **[Server-Driven UI](guides/server-driven-ui.md)**     | `push_commands()` — the server pushes JS Command chains to the client for immediate execution (ADR-002 Phase 1a) |
| **[Guided Tours](guides/tutorials.md)**                | `TutorialMixin` + `{% tutorial_bubble %}` — declarative guided tours, onboarding flows, and wizards (ADR-002 Phase 1c) |

### Navigation

|                                              |                                                     |
| -------------------------------------------- | --------------------------------------------------- |
| **[Navigation](guides/navigation.md)**             | `live_patch`, `live_redirect`, URL state management           |
| **[Hooks](guides/hooks.md)**                       | Client-side JavaScript lifecycle hooks                        |
| **[on_mount Hooks](guides/on-mount-hooks.md)**     | Cross-cutting server-side mount hooks (auth, telemetry, etc.) |
| **[Model Binding](guides/model-binding.md)**       | Two-way `dj-model` data binding                               |

### Integration

|                                                |                                                           |
| ---------------------------------------------- | --------------------------------------------------------- |
| **[CSS Frameworks](guides/css-frameworks.md)** | Bootstrap, Tailwind, and custom styling                   |
| **[Authentication](guides/authentication.md)** | View-level and handler-level auth with Django permissions |
| **[Multi-Tenant](guides/multi-tenant.md)**     | SaaS architecture with tenant isolation                   |
| **[External Services](guides/services.md)**    | AWS, REST APIs, Redis integration patterns                |
| **[MCP Server](guides/mcp-server.md)**         | AI assistant integration via Model Context Protocol       |
| **[HTTP API](guides/http-api.md)**             | Auto-generated HTTP endpoints + OpenAPI from `@event_handler` (ADR-008) |

### Operations

|                                          |                                               |
| ---------------------------------------- | --------------------------------------------- |
| **[Deployment](guides/deployment.md)**                    | Deploy to production (uvicorn, nginx, Docker)      |
| **[djust-deploy CLI](guides/djust-deploy.md)**            | Deploy to djustlive.com from the command line      |
| **[Template Cheat Sheet](guides/template-cheatsheet.md)** | Quick reference for all directives and filters     |
| **[PWA](guides/pwa.md)**                                  | Build offline-first Progressive Web Apps           |
| **[Service Worker](guides/service-worker.md)**            | Instant page shell + WebSocket reconnection bridge |
| **[Error Codes](guides/error-codes.md)**                  | Complete error code reference with fixes           |
| **[Error Overlay (Dev Mode)](guides/error-overlay.md)**   | In-browser Python traceback panel, Next.js-style   |
| **[Type-Safe Template Validation](guides/typecheck.md)**  | `manage.py djust_typecheck` — catch template typos before prod |

---

## Forms

Real-time form validation and submission with Django Forms.

|                                      |                                                             |
| ------------------------------------ | ----------------------------------------------------------- |
| **[Forms Overview](forms/index.md)** | `dj-submit`, `FormMixin`, real-time validation              |
| **[Forms Guide](guides/forms.md)**   | Full guide with `as_live`, model forms, reset, confirmation |

---

## State Management

Replace JavaScript state patterns with Python decorators.

|                                                                  |                                                                                 |
| ---------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| **[State Management](state/index.md)**                           | `@debounce`, `@throttle`, `@loading`, `@cache`, `@optimistic`, `DraftModeMixin` |
| **[Tutorial](../state-management/STATE_MANAGEMENT_TUTORIAL.md)** | Step-by-step product search example                                             |
| **[Patterns](../state-management/STATE_MANAGEMENT_PATTERNS.md)** | Best practices and anti-patterns                                                |
| **[Examples](../state-management/STATE_MANAGEMENT_EXAMPLES.md)** | Copy-paste ready code                                                           |

---

## Testing

Test LiveViews without a browser.

|                                       |                                                                                     |
| ------------------------------------- | ----------------------------------------------------------------------------------- |
| **[Testing Guide](testing/index.md)** | `LiveViewTestClient`, `SnapshotTestMixin`, `LiveViewSmokeTest`, `@performance_test` |
| **[Testing LiveViews (v0.5.1 API)](guides/testing.md)** | Phoenix-parity assertions: `assert_push_event`, `assert_patch`, `render_async`, `follow_redirect`, `assert_stream_insert`, `trigger_info` |

---

## Advanced

Internal architecture and advanced patterns.

|                                                        |                                                           |
| ------------------------------------------------------ | --------------------------------------------------------- |
| **[VDOM Architecture](advanced/vdom-architecture.md)** | How the Rust virtual DOM and diffing works                |
| **[Server Push](advanced/server-push.md)**             | Push updates from background tasks and signals            |
| **[Debug Panel](advanced/debug-panel.md)**             | Built-in developer tooling                                |
| **[Security](advanced/security.md)**                   | XSS prevention, CSRF, permissions, multi-tenant isolation |

---

## API Reference

Complete API documentation.

|                                               |                                                                                                         |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| **[LiveView](api-reference/liveview.md)**     | `on_mount`, `mount()`, `get_context_data()`, `handle_params()`, `handle_info()`, `start_async()`, navigation             |
| **[Decorators](api-reference/decorators.md)** | `@event_handler`, `@debounce`, `@throttle`, `@loading`, `@cache`, `@optimistic`, `@background`, `@permission_required` |
| **[Components](api-reference/components.md)** | `Component`, `LiveComponent`, built-in components, registry                                             |
| **[Testing](api-reference/testing.md)**       | `LiveViewTestClient`, `SnapshotTestMixin`, `LiveViewSmokeTest`, `@performance_test`                     |

---

## Performance

Rust powers the core rendering engine:

| Operation              | Django | djust  | Speedup         |
| ---------------------- | ------ | ------ | --------------- |
| Template (100 items)   | 2.5ms  | 0.15ms | **16×**         |
| Large list (10k items) | 450ms  | 12ms   | **37×**         |
| VDOM diff              | N/A    | 0.08ms | sub-millisecond |

---

## Migrating

- **[From standalone djust packages](guides/migration-from-standalone-packages.md)** — moving from `pip install djust-auth` / `djust-tenants` / `djust-theming` / `djust-components` / `djust-admin`? Replace with `djust[auth]` / `djust[tenants]` / etc. extras. Mechanical sed script + FAQ included. ([ADR-007](../adr/007-package-taxonomy-and-consolidation.md) Phase 4 — sunset as of v0.6.0.)

---

## Community

- [GitHub](https://github.com/johnrtipton/djust) — Source, issues, PRs
- [Discord](https://discord.gg/djust) — Chat and support
- [Changelog](../../CHANGELOG.md) — What's new
