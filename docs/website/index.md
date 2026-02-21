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

|                                                |                                                           |
| ---------------------------------------------- | --------------------------------------------------------- |
| **[Streaming](guides/streaming.md)**           | Real-time partial DOM updates for LLM chat and live feeds |
| **[Presence](guides/presence.md)**             | Track online users, live cursors, typing indicators       |
| **[Uploads](guides/uploads.md)**               | Chunked binary file uploads via WebSocket                 |
| **[Loading States](guides/loading-states.md)** | Spinners, skeleton screens, disabled states               |

### Navigation

|                                              |                                                     |
| -------------------------------------------- | --------------------------------------------------- |
| **[Navigation](guides/navigation.md)**       | `live_patch`, `live_redirect`, URL state management |
| **[Hooks](guides/hooks.md)**                 | Client-side JavaScript lifecycle hooks              |
| **[Model Binding](guides/model-binding.md)** | Two-way `dj-model` data binding                     |

### Integration

|                                                |                                                           |
| ---------------------------------------------- | --------------------------------------------------------- |
| **[CSS Frameworks](guides/css-frameworks.md)** | Bootstrap, Tailwind, and custom styling                   |
| **[Authentication](guides/authentication.md)** | View-level and handler-level auth with Django permissions |
| **[Multi-Tenant](guides/multi-tenant.md)**     | SaaS architecture with tenant isolation                   |
| **[External Services](guides/services.md)**    | AWS, REST APIs, Redis integration patterns                |
| **[MCP Server](guides/mcp-server.md)**         | AI assistant integration via Model Context Protocol       |

### Operations

|                                          |                                               |
| ---------------------------------------- | --------------------------------------------- |
| **[Deployment](guides/deployment.md)**   | Deploy to production (uvicorn, nginx, Docker) |
| **[PWA](guides/pwa.md)**                 | Build offline-first Progressive Web Apps      |
| **[Error Codes](guides/error-codes.md)** | Complete error code reference with fixes      |

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
| **[LiveView](api-reference/liveview.md)**     | `mount()`, `get_context_data()`, `handle_params()`, `handle_info()`, navigation                         |
| **[Decorators](api-reference/decorators.md)** | `@event_handler`, `@debounce`, `@throttle`, `@loading`, `@cache`, `@optimistic`, `@permission_required` |
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

## Community

- [GitHub](https://github.com/johnrtipton/djust) — Source, issues, PRs
- [Discord](https://discord.gg/djust) — Chat and support
- [Changelog](../../CHANGELOG.md) — What's new
