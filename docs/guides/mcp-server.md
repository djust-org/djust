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

> **Tip**: If you use a virtual environment, point `command` at the full path to your venv's Python binary to avoid activation issues:
> ```json
> "command": ".venv/bin/python"
> ```

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

### Other MCP clients

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

#### `get_framework_schema()`

Returns the complete djust framework schema — all directives, lifecycle methods, decorators, class attributes, mixins, data attribute types, and conventions.

**This is the first tool an AI should call.** It provides everything needed to write correct djust code.

#### `get_template_directives()`

Returns just the `dj-*` template directives with their parameters, DOM events, examples, and modifiers. Useful when you need a focused reference on template syntax without the full schema.

Covers all 28+ directives: `dj-click`, `dj-submit`, `dj-change`, `dj-input`, `dj-model`, `dj-update`, `dj-target`, `dj-loading.*`, `dj-hook`, `dj-patch`, `dj-navigate`, `dj-stream`, `dj-upload`, and more.

#### `get_decorators()`

Returns all djust decorators with import paths, parameters, and usage examples:

- `@event_handler` — mark methods as event handlers
- `@debounce` / `@throttle` — client-side rate control
- `@optimistic` — instant UI updates, server corrects
- `@cache` — client-side response caching
- `@client_state` — cross-component state sharing
- `@rate_limit` — server-side rate limiting
- `@permission_required` — handler-level auth
- `@reactive` / `@computed` / `state()` — reactive state primitives

#### `get_best_practices()`

Returns comprehensive guidance for writing correct djust code:

- Setup checklist (INSTALLED_APPS, ASGI, URLs)
- Lifecycle flow diagram
- Event handler rules and examples
- JIT serialization patterns
- Form integration
- Security rules
- Template directive examples
- The 8 most common pitfalls and how to avoid them

### Project Introspection (requires Django)

These tools inspect your live Django project. They only work when launched via `python manage.py djust_mcp`.

#### `list_views()`

Lists all `LiveView` classes found in your project. For each view, returns:

- Class name and module path
- Template name
- `mount()` parameters
- Event handlers (names, typed parameters, decorators)
- Exposed state variables (public attributes)
- Auth configuration (`login_required`, `permission_required`)
- Mixins (PresenceMixin, FormMixin, etc.)
- Configuration (`tick_interval`, `temporary_assigns`, `use_actors`)

#### `list_components()`

Lists all `LiveComponent` classes in the project with props, slots, event handlers, and template info.

#### `list_routes()`

Lists all URL routes mapped to LiveView classes — URL patterns, view class paths, and route names.

#### `get_view_schema(view_name)`

Returns the full schema for a specific view or component.

| Parameter | Type | Description |
|-----------|------|-------------|
| `view_name` | `str` | Class name (e.g., `CounterView`) or full path (e.g., `myapp.views.CounterView`) |

If the view isn't found, returns a list of available classes to help you find the right name.

### Validation & Checks (requires Django)

#### `run_system_checks(category="")`

Runs djust's Django system checks and returns structured results.

| Parameter | Type | Description |
|-----------|------|-------------|
| `category` | `str` | Optional filter: `config`, `liveview`, `security`, `templates`, or `quality`. Empty runs all. |

Returns JSON with:
- Each check's ID, severity (error/warning/info), message, and hint
- File path and line number (when available)
- Fix suggestions
- Summary counts

Use this after generating or modifying code to catch misconfigurations early.

#### `run_audit(app_label="")`

Runs a security audit across all LiveViews.

| Parameter | Type | Description |
|-----------|------|-------------|
| `app_label` | `str` | Optional Django app to audit (e.g., `myapp`). Empty audits all apps. |

Returns a comprehensive report per view: exposed state, auth config, handler signatures, decorator protections, and mixins.

#### `validate_view(code)`

Validates a LiveView class definition *without running it*. Pass in Python source code and get back a list of issues.

| Parameter | Type | Description |
|-----------|------|-------------|
| `code` | `str` | Python source code of a LiveView class |

Catches common issues:
- Missing `@event_handler` decorator on handler-like methods
- Missing `**kwargs` in handler signatures
- Missing `mount()` method or `request` parameter
- Missing `template_name`
- Security issues (`mark_safe` with f-strings)

This tool doesn't require Django — it uses AST parsing, so it works on code that hasn't been imported yet.

### Code Generation (no Django required)

#### `scaffold_view(name, features="")`

Generates a complete LiveView class with the requested features.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Class name (e.g., `ProductListView`) |
| `features` | `str` | Comma-separated: `search`, `crud`, `pagination`, `form`, `presence`, `streaming`, `auth` |

Example:

```
scaffold_view("ProductListView", "search,pagination,auth")
```

Generates a view with search (debounced), pagination, login protection, appropriate imports, `mount()`, `_refresh()`, event handlers, and `get_context_data()`.

#### `scaffold_component(name, props="")`

Generates a LiveComponent class.

| Parameter | Type | Description |
|-----------|------|-------------|
| `name` | `str` | Class name (e.g., `UserCard`) |
| `props` | `str` | Comma-separated prop names (e.g., `user,show_avatar,editable`) |

#### `add_event_handler(handler_name, params="", decorators="")`

Generates a single event handler method to paste into an existing view.

| Parameter | Type | Description |
|-----------|------|-------------|
| `handler_name` | `str` | Method name (e.g., `delete_item`) |
| `params` | `str` | Typed params (e.g., `item_id: int = 0, confirm: bool = False`) |
| `decorators` | `str` | Decorators to stack (e.g., `debounce(wait=0.3), rate_limit(rate=5)`) |

Example:

```
add_event_handler("delete_item", "item_id: int = 0", "rate_limit(rate=5)")
```

Generates:

```python
    @rate_limit(rate=5)
    @event_handler()
    def delete_item(self, item_id: int = 0, **kwargs):
        # TODO: implement
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
