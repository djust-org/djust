---
title: "MCP Server"
slug: mcp-server
section: guides
order: 10
level: beginner
description: "Give AI assistants structured access to your djust project via the Model Context Protocol"
---

# MCP Server

djust ships with an [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that gives AI assistants structured access to your djust project. Instead of the AI guessing at framework conventions, it can query the server for the exact directives, decorators, lifecycle methods, and even inspect your live project's views and routes.

## Prerequisites

Install the MCP Python package (it's not a djust dependency — install it only where you need AI integration):

```bash
pip install 'mcp[cli]'
```

## Configuration

### Claude Code

From your project directory, run:

```bash
claude mcp add --transport stdio djust -- python manage.py djust_mcp
```

Or create a `.mcp.json` file in your project root:

```json
{
  "mcpServers": {
    "djust": {
      "type": "stdio",
      "command": "python",
      "args": ["manage.py", "djust_mcp"]
    }
  }
}
```

!!! tip
    If you use a virtual environment, point `command` at the full path to your venv's Python binary (e.g., `.venv/bin/python`) to avoid activation issues.

### VS Code / Cursor

Add to your `.vscode/mcp.json` (or Cursor equivalent):

```json
{
  "servers": {
    "djust": {
      "type": "stdio",
      "command": "python",
      "args": ["manage.py", "djust_mcp"]
    }
  }
}
```

### Other MCP Clients

Any MCP client that supports stdio transport works. The server reads from stdin and writes to stdout — just point it at:

```
python manage.py djust_mcp
```

## Two Modes

The server operates in two modes depending on how it's launched:

| Mode | Command | What's available |
|------|---------|-----------------|
| **Full mode** | `python manage.py djust_mcp` | Everything — framework schema, project introspection, system checks, code generation |
| **Framework-only** | `python -m djust.mcp` | Framework schema, decorators, directives, best practices, code generation. No project introspection. |

Use full mode when working inside a Django project. Use framework-only mode when you just need djust API reference without a running project (e.g., starting a new project from scratch).

## Tools Reference

The server exposes 14 tools organized into four categories.

### Framework Schema (no Django required)

These tools return static framework metadata. They work in both modes.

**`get_framework_schema()`** — Returns the complete djust framework schema: all directives, lifecycle methods, decorators, class attributes, mixins, data attribute types, and conventions. **This is the first tool an AI should call.**

**`get_template_directives()`** — Returns just the `dj-*` template directives with their parameters, DOM events, examples, and modifiers. Covers all 28+ directives: `dj-click`, `dj-submit`, `dj-change`, `dj-input`, `dj-model`, `dj-update`, `dj-target`, `dj-loading.*`, `dj-hook`, `dj-patch`, `dj-navigate`, `dj-stream`, `dj-upload`, and more.

**`get_decorators()`** — Returns all djust decorators with import paths, parameters, and usage examples: `@event_handler`, `@debounce`, `@throttle`, `@optimistic`, `@cache`, `@client_state`, `@rate_limit`, `@permission_required`, `@reactive`, `@computed`, and `state()`.

**`get_best_practices()`** — Returns comprehensive guidance: setup checklist, lifecycle flow diagram, event handler rules, JIT serialization patterns, form integration, security rules, template directive examples, and the 8 most common pitfalls.

### Project Introspection (requires Django)

These tools inspect your live Django project. They only work when launched via `python manage.py djust_mcp`.

**`list_views()`** — Lists all `LiveView` classes found in your project with templates, event handlers (names, typed parameters, decorators), exposed state variables, auth configuration, and mixins.

**`list_components()`** — Lists all `LiveComponent` classes with props, slots, event handlers, and template info.

**`list_routes()`** — Lists all URL routes mapped to LiveView classes — URL patterns, view class paths, and route names.

**`get_view_schema(view_name)`** — Returns the full schema for a specific view or component. Accepts a class name (e.g., `CounterView`) or full path (e.g., `myapp.views.CounterView`). Returns state variables, handlers, decorators, auth config, and mixins.

### Validation & Checks

**`run_system_checks(category="")`** — Runs djust's Django system checks. Optionally filter by category: `config`, `liveview`, `security`, `templates`, or `quality`. Returns structured results with severity, message, hint, file path, and line number. *(Requires Django.)*

**`run_audit(app_label="")`** — Runs a security audit across all LiveViews. Returns exposed state, auth config, handler signatures, decorator protections, and mixins per view. *(Requires Django.)*

**`validate_view(code)`** — Validates a LiveView class definition without running it. Pass in Python source code and get back a list of issues: missing `@event_handler` decorators, missing `**kwargs`, missing `mount()`, security problems. Uses AST parsing, so it works without Django.

### Code Generation (no Django required)

**`scaffold_view(name, features="")`** — Generates a complete LiveView class. Features: `search`, `crud`, `pagination`, `form`, `presence`, `streaming`, `auth`.

**`scaffold_component(name, props="")`** — Generates a LiveComponent class with the specified props.

**`add_event_handler(handler_name, params="", decorators="")`** — Generates a single event handler method to paste into an existing view. Supports typed parameters and decorator stacking.

### Live Observability (DEBUG-only, runtime introspection)

These tools (added in v0.4.5) expose the running app's state to an AI
agent the same way django-debug-toolbar exposes it to a human. They
require:

- `DEBUG = True`
- `path("_djust/observability/", include("djust.observability.urls"))`
  in the project's `urls.py`
- `LocalhostOnlyObservabilityMiddleware` in `MIDDLEWARE` (rejects any
  non-loopback caller)

Each tool corresponds 1:1 to an HTTP endpoint under
`/_djust/observability/` — the MCP wrapper just calls it.

**`get_view_assigns(session_id)`** — Real server-side `self.*` state of
the mounted LiveView for a given session. Complements
djust-browser-mcp's client-only `djust_state_diff` with the source of
truth. Per-attr fallback tags non-serializable values as
`{"_repr": "...", "_type": "..."}` rather than blanking the whole
response.

**`get_last_traceback(n=1)`** — Ring-buffered (50) exception log
populated from `handle_exception()`. Replaces "can you paste the
terminal?" for ~80% of blind-debugging cases.

**`tail_server_log(since_ms=0, level="INFO")`** — Ring-buffered (500)
Django + djust log records with `since_ms` and `level` filters.
`djust.*` is captured at DEBUG and above; `django.*` at WARNING and
above.

**`get_handler_timings(handler_name="")`** — Per-handler rolling
100-sample distribution: `min`, `max`, `avg`, `p50`, `p90`, `p99`.
Reuses the existing `timing["handler"]` measurement; no extra perf
counters in the request path.

**`get_sql_queries_since(since_ms)`** — Per-event SQL capture via
`connection.execute_wrappers`. Each query is tagged with
`(session_id, event_id, handler_name)` plus a `stack_top` that skips
framework frames so you see your application's call site directly.

**`reset_view_state(session_id)`** — Replay `view.mount()` on the
registered instance. Clears public attrs and re-invokes
`mount(stashed_request, **stashed_kwargs)`. Useful between fixture
replays.

**`eval_handler(session_id, handler_name, params={}, dry_run=True)`** —
Dry-run a handler against the live view's current state. Returns
`{before_assigns, after_assigns, delta, result}`. With `dry_run=True`
(default) a `DryRunContext` blocks side effects:

- `Model.save` / `delete`
- `QuerySet.update` / `delete` / `bulk_create` / `bulk_update`
- `send_mail` / `send_mass_mail`
- `requests.*` and `urllib.request.urlopen`

The first attempt raises `DryRunViolation`; the response surfaces
`{"blocked_side_effect": "..."}` so the caller knows what was
attempted. Pass `dry_run_block=False` to record violations without
blocking. A process-wide lock serializes dry-runs.

**`find_handlers_for_template(template_path)`** — Cross-references a
template file against every view that uses it. Returns the `dj-*`
handlers wired in the template AND the diff against the view's
handler methods, so you can catch dead bindings (template uses
`dj-click="missing"`) at author time.

**`seed_fixtures(fixture_paths)`** — Subprocess wrapper around
`manage.py loaddata` for regression-fixture DB setup before a
`reset_view_state` + `eval_handler` cycle.

> **Security model.** Mirrors django-debug-toolbar: only fires when
> `DEBUG=True` AND the request originates from `127.0.0.1` / `::1`.
> Production deployments should leave the `urls.py` include
> commented-out as a defense-in-depth check beyond `DEBUG=False`.

## Example

Scaffolding a view with search and pagination:

```python
# AI calls: scaffold_view("ProductListView", "search,pagination,auth")
# Generates:

from djust import LiveView
from djust.decorators import event_handler, debounce

class ProductListView(LiveView):
    template_name = 'myapp/product_list.html'
    login_required = True

    def mount(self, request, **kwargs):
        self.search_query = ''
        self.page = 1
        self.per_page = 20
        self._refresh()

    def _refresh(self):
        qs = Product.objects.all()
        if self.search_query:
            qs = qs.filter(name__icontains=self.search_query)
        start = (self.page - 1) * self.per_page
        self._total_count = qs.count()
        self._items = qs[start:start + self.per_page]

    @event_handler()
    @debounce(wait=0.3)
    def search(self, value: str = '', **kwargs):
        self.search_query = value
        self.page = 1
        self._refresh()

    @event_handler()
    def go_to_page(self, page: int = 1, **kwargs):
        self.page = page
        self._refresh()
```

## Recommended Workflow

When an AI assistant connects to the djust MCP server, the most effective workflow is:

1. **Learn the framework** — call `get_framework_schema()` to understand directives, lifecycle, and decorators
2. **Learn best practices** — call `get_best_practices()` for patterns, security rules, and common pitfalls
3. **Understand the project** — call `list_views()` and `list_routes()` to see what already exists
4. **Generate code** — use `scaffold_view()` / `scaffold_component()` / `add_event_handler()` for boilerplate
5. **Validate** — call `validate_view()` on generated code to catch issues before saving
6. **Run checks** — call `run_system_checks()` to verify the code integrates correctly
7. **Audit security** — call `run_audit()` to review exposed state and auth configuration

## Troubleshooting

### "mcp package not installed"

```bash
pip install 'mcp[cli]'
```

If using a virtual environment, make sure you install it in the same environment your `manage.py` uses.

### "Django not configured"

Project introspection tools (`list_views`, `list_routes`, `run_system_checks`, etc.) require Django. Make sure you're launching with `python manage.py djust_mcp`, not `python -m djust.mcp`.

If you only need framework-level information (directives, decorators, scaffolding), those tools work without Django.

### Server not showing up in Claude Code

1. Check that `.mcp.json` is in your project root (the directory where you run `claude`)
2. Verify the Python path is correct — if using a venv, use the full path
3. Test manually: `python manage.py djust_mcp` should print "Starting djust MCP server..." to stderr and then wait for input
4. Restart Claude Code after adding or modifying `.mcp.json`

### Tools returning empty views/components

The introspection tools discover classes by walking Python subclasses at runtime. If your views aren't showing up:

- Make sure your app is in `INSTALLED_APPS`
- Make sure the module containing your views is importable (no import errors)
- Framework-internal classes are filtered out — only your project's classes are returned
