---
title: "AI Guardrails"
slug: ai-guardrails
section: guides
order: 10.5
level: intermediate
description: "The checks and tooling that catch mistakes an AI coding assistant makes in a LiveView app: what to run, when it runs, and what it does not catch."
---

# AI Guardrails

djust ships checks and tooling that catch mistakes an AI coding assistant makes in a LiveView app. They cover four things: context that tells the assistant how djust works, tools the assistant can call to check code it just wrote, Django system checks that run at startup and in CI, and an audit command that reports what each view exposes. This page lists each one, the command that runs it, and what it catches.

Here, "guardrails" means checks on code and configuration. It does not mean content filtering of a language model, and it makes no claim that AI-written code is correct. Most of the checks are static and heuristic: they find some mistakes and miss others. [What this does not do](#what-this-does-not-do) lists the limits, and none of this replaces tests and code review.

Items marked **1.3** are not in djust 1.2.2. They are in the 1.3.0 release candidates. The claims on this page were checked against the `main` source tree (1.3.0rc6 plus the changes merged after it), and against the `v1.2.2` tag wherever a row says whether something is in 1.2.2. The check tables below are generated from the check messages in that source. Each ID and message lives in `python/djust/checks/`, so you can search for an ID there to see the code behind a row.

## The layers

| Layer | How to run it | When it runs | Needs |
| --- | --- | --- | --- |
| Context for the assistant | `manage.py djust_ai_context`, the `docs/ai/` pages | When you generate or refresh the files | Nothing extra |
| Checks on generated code | MCP server tools `validate_view`, `detect_common_issues`, `run_system_checks`, `run_audit` | When the assistant calls them | `pip install 'mcp[cli]'` |
| Startup and CI checks | `manage.py check` | With `runserver`, with most management commands, and in CI | Nothing extra |
| Audit | `manage.py djust_audit`, six modes | When you run it | Nothing extra |
| Framework defaults | No command | On every request | Nothing extra |

## An example used on this page

The examples below run against one small view. It has four mistakes of the kind an assistant can make: a client object stored on `self`, a handler without the decorator, a template variable that nothing sets, and a binding to a method that does not exist.

```python
import requests
from djust import LiveView
from djust.decorators import event_handler


class ShopView(LiveView):
    template_name = "shop/index.html"

    def mount(self, request, **kwargs):
        self.pings = 0
        self.client = requests.Session()

    @event_handler()
    def ping(self, **kwargs):
        self.pings += 1

    def toggle_cart(self, **kwargs):
        self.pings += 1

    def get_context_data(self, **kwargs):
        return {"pings": self.pings}
```

```html
<div dj-root dj-view="shop.views.ShopView">
  <p>Pings: {{ pings }}</p>
  <p>Total: {{ cart_total }}</p>
  <button dj-click="ping">Ping</button>
  <button dj-click="toggle_cart">Cart</button>
  <button dj-click="add_to_cart">Add</button>
</div>
```

## Context for the assistant

### `djust_ai_context`

`djust_ai_context` writes a file that describes djust and your project, in the format your assistant reads.

```bash
python manage.py djust_ai_context --format claude    # CLAUDE.md
python manage.py djust_ai_context --format cursor    # .cursorrules
python manage.py djust_ai_context --format copilot   # .github/copilot-instructions.md
python manage.py djust_ai_context --print            # stdout instead of a file
python manage.py djust_ai_context --output docs/djust-context.md
python manage.py djust_ai_context --force            # replace an existing file (1.3)
```

Those three formats are the only ones, and `claude` is the default. The file has two parts. The first is a framework reference: template directives, lifecycle methods, decorators, and conventions. The second is read from your project: each view's template, mixins, auth settings, handlers with their parameters and the variables its template can use, plus components and URL routes. For the example view, the project part is:

```text
## Project Views

### `shop.views.ShopView`

Template: `shop/index.html`

Handlers:
- `ping()`

Template variables: `client`, `pings`

## URL Routes

- `/` -> `shop.views.ShopView` (name='index')
```

Only methods decorated with `@event_handler` are listed as handlers. The example view also has an undecorated `toggle_cart` method. The file leaves it out, and the browser cannot call it either.

Between the two parts, **1.3** adds two short sections of advice. A Security section says to avoid `|safe`, `{% autoescape off %}` and `mark_safe()` on user content, to set `login_required` or `permission_required`, to re-check permissions in handlers that change data, to include `{% csrf_token %}`, not to call `login()` in an event handler (the session cookie cannot change over a WebSocket, so the browser stays anonymous), and to run `manage.py check` and `djust_audit --ast` before finishing. It opens by saying that these practices do not make generated code secure on their own. A State section says to set state in `mount()` and to call `super().get_context_data(**kwargs)` in an override, because the HTTP fallback rebuilds a view from the dict `get_context_data()` returned on the previous request. Both are text for the assistant to read. Nothing checks that it followed them.

**1.3** also refuses to overwrite a file that already exists. The command exits with an error that names the file and says so. Pass `--force` to replace it, `--output` to write elsewhere, or `--print` to read the text first. A forced replacement is written to a temporary file beside the real target and renamed over it, so a failure keeps the old file, and the file keeps its permission bits and any symlink. In 1.2.2 the command replaced an existing file without asking, so check `git diff` after running it there. Keep hand-written rules in a separate file, and re-run the command after you add or change views.

### The `docs/ai/` reference pages

`docs/ai/` in the repository holds short Markdown pages meant to be loaded into an assistant's context: [conventions](https://github.com/djust-org/djust/blob/main/docs/ai/conventions.md) (start here), lifecycle, events, templates, forms, components, JIT serialization, loading states and security patterns. The [`docs/ai/README.md`](https://github.com/djust-org/djust/blob/main/docs/ai/README.md) indexes them, and [`docs/llms-full.txt`](https://github.com/djust-org/djust/blob/main/docs/llms-full.txt) is a single-file reference. Use the copy that matches your installed release, because `main` can describe newer APIs.

`djust new` writes an `AGENTS.md` into the project that points at these pages. For an existing project, add the same pointer to whatever instructions file your assistant reads. An assistant does not read a dependency's docs unless something points it there.

These are instructions. Nothing verifies that an assistant read them or followed them, which is why the layers below check the result.

## Checks on generated code: the MCP server

The djust MCP server lets an assistant call djust's own checks on the code it just wrote. The `mcp` package is optional. It is not a djust dependency, so install it only where you want this:

```bash
pip install 'mcp[cli]'
claude mcp add --transport stdio djust -- python manage.py djust_mcp
```

`djust mcp install` does the second step for Claude Code from inside a project. The [MCP server guide](mcp-server.md) covers other editors and the full tool list. Four of its tools check code:

| Tool | Input | Needs Django |
| --- | --- | --- |
| `validate_view(code)` | Source of a LiveView class | No |
| `detect_common_issues(code)` | Source of a LiveView class | No |
| `run_system_checks(category="")` | Optional `config`, `liveview`, `security`, `templates` or `quality` | Yes |
| `run_audit(app_label="")` | Optional app label | Yes |

`validate_view` reports:

- a syntax error
- a `mount()` with no `request` parameter (error) or no `**kwargs` (warning)
- a class with neither `template_name` nor `template`
- a method with no `@event_handler` whose name starts with `handle_`, `on_`, `toggle_`, `select_`, `update_`, `delete_`, `create_`, `add_`, `remove_`, `save_`, `cancel_`, `submit_`, `close_` or `open_`
- `mark_safe()` called on an f-string
- a public attribute assigned, in `mount()` or a handler-like method, from a call that looks like a service or client: `Service(`, `.client(`, `Session(`, `boto3.`, `requests.` or `httpx.`

`detect_common_issues` reports:

- a syntax error
- the same handler-like names with no `@event_handler`
- a public attribute named `service`, `client`, `session`, `connection`, `conn`, `api` or `sdk`, or assigned from `boto3`, `requests`, `httpx`, `redis` or `paramiko`
- a public attribute assigned a QuerySet, as in `self.items = Item.objects.all()`

`run_system_checks` runs every djust system check and returns the id, severity, message and hint of each, with the file and line when the check has them. The category names filter by ID prefix, and without a category you get every check, including families the names do not cover. `run_audit` returns the default `djust_audit` report as JSON: per view, the exposed state, auth configuration, handlers and their decorators.

The two tools that take source text read one string. They do not see the rest of the project, and they match handler names by the prefixes above, so an undecorated method called `increment` is not reported. `manage.py check` and `djust_audit` have the project-wide view. On the example view, `validate_view` returns:

```json
[
  {
    "severity": "warning",
    "message": "Method 'toggle_cart' looks like an event handler but lacks @event_handler decorator",
    "line": 17,
    "fix_hint": "Add @event_handler() above the method"
  },
  {
    "severity": "error",
    "message": "Service instance stored in state: self.client (session instantiation). Non-serializable objects cannot be stored as view attributes.",
    "line": 11,
    "fix_hint": "Use helper method pattern: define def _get_client(self) that creates the instance on demand. See: docs/guides/services.md"
  }
]
```

An assistant calls these tools only if it is told to. Add a line to `AGENTS.md` or `CLAUDE.md` that names them, for example "after changing a view, call `validate_view` and `run_system_checks`". The MCP guide's recommended workflow uses that order.

## Startup and CI checks

`python manage.py check` runs Django's system checks, and every djust check is registered with it. Django also runs them for `runserver` and before most other management commands. An ASGI server started directly, such as `uvicorn shop.asgi:application`, does not run them. So put the command in CI:

```bash
python manage.py check --tag djust --fail-level WARNING
```

Without `--fail-level`, `check` exits non-zero only for errors. The hints after each finding say how to fix it, and `manage.py djust_check` prints the same findings grouped by category, with `--json` and `--format json` for machine-readable output.

`--tag djust` selects the checks registered under that tag, which is nearly all of them. The theming checks, whose IDs start with `djust_theming.`, are registered under Django's `compatibility` tag. A plain `manage.py check` runs them, and `--tag djust` and the MCP `run_system_checks` tool do not. One of them, `djust_theming.W003` (**1.3**), warns when `LIVEVIEW_CONFIG["theme"]["selectable_presets"]` is not a list of preset names or names a preset that is not registered. At render time such a value is logged and ignored.

Four checks read `DEBUG = False` as "production": `A010`, `A011`, `A012` and `A014` (the `ALLOWED_HOSTS` wildcard and `SECRET_KEY` checks). From **1.3** they stay quiet during `manage.py test`, because Django's test runner sets `DEBUG = False` before it runs the checks. They still run under `manage.py check` and `check --deploy`. A CI job that only runs the test suite therefore does not cover them. The `check` step above does, when the settings CI uses have `DEBUG = False`.

Template checks skip `{# ... #}` comments and `{% comment %}` blocks (**1.3**), so a binding or tag mentioned in a comment is not reported.

The checks are heuristic. They read class definitions, settings and templates. The binding checks, for example, construct no view, run no handler and evaluate no queryset. A check reports what it can see in the source and says nothing about the rest.

### Families

Each check has an ID such as `djust.V004`: a family letter and a number. The theming package adds `djust_theming.`-prefixed IDs (`E001`, `W001`, `W003` and others) that this page does not tabulate. The [error code reference](error-codes.md) explains every ID and its fix. This table is generated from the checks package:

| Prefix | Family | IDs first seen after 1.2.2 (marked 1.3) |
| --- | --- | --- |
| `A` | Audit / static security checks | A100 to A107 |
| `B` | Vendored assets and SBOMs | B001 to B014 |
| `C` | Configuration | C020 to C025 |
| `D` | Database notifications | none |
| `Q` | Code quality | Q004 |
| `S` | Security | S013 |
| `T` | Templates | T019 to T025 |
| `U` | Update notice | none |
| `V` | Validation of LiveView classes and handlers | V016 to V020 |
| `Y` | Accessibility | none |

### Checks that matter for generated code

These checks flag slips that still look like ordinary Django or ordinary Python, so they are easy to write without noticing. They were chosen by reading the messages. Each table is generated from the message text in the source. A `<name>` in angle brackets stands for a value filled in when the check runs. Some messages start with a file location or a view name followed by ` -- `, and the tables leave that part out. Where a check has several wordings, the table shows one. "Yes, wording differs" means the ID exists in 1.2.2 with different message text.

Views and handlers:

| ID | Level | What it reports | In 1.2.2 |
| --- | --- | --- | --- |
| `djust.V001` | Warning | `<view>: missing 'template_name' attribute.` | Yes |
| `djust.V003` | Error | `<view>: mount() should accept (self, request, **kwargs).` | Yes |
| `djust.V004` | Info | `<view>.<name>() looks like an event handler but is missing @event_handler.` | Yes |
| `djust.V005` | Warning | `<view> is not in LIVEVIEW_ALLOWED_MODULES. WebSocket mount will silently fail.` | Yes |
| `djust.V006` | Warning | `Service instance '<attr>' assigned in mount(). Service instances cannot be serialized.` | Yes |
| `djust.V008` | Info | `Non-primitive type '<call_name>' assigned to self.<attr> in mount(). Ensure this type is JSON-serializable.` | Yes |
| `djust.V013` | Warning | `<view>: <base class> overrides <methods>, which will NOT run on a WebSocket mount (the WS path calls mount() directly, never dispatch()/get()/post()).` | Yes |

Templates:

| ID | Level | What it reports | In 1.2.2 |
| --- | --- | --- | --- |
| `djust.T011` | Warning | `unsupported template tag '{% <tag_name> %}' will be silently ignored by Rust renderer.` | Yes |
| `djust.T012` | Warning | `template uses dj-* directives that need a connected LiveView but has no dj-root or dj-view attribute, so the page never connects.` | Yes, wording differs |
| `djust.T015` | Warning | `legacy '<attribute>' attribute detected.` | Yes |
| `djust.T018` | Warning | `template references undefined variable '<name>' at line <line> (<template>) -- it resolves to nothing and renders as empty string, with no error.` | Yes |
| `djust.T024` | Warning | `` `<path>` is always empty in a LiveView template: djust never serializes `<field>`. `` | No (1.3) |
| `djust.T025` | Warning | `'<attribute>' is on <<tag>>. The HTTP render is complete, but the WebSocket mount keeps only the first element inside <body>, so the live page silently loses the rest.` | No (1.3) |

Event bindings compare every literal `dj-*` binding in a template with the handler it names:

| ID | Level | What it reports | In 1.2.2 |
| --- | --- | --- | --- |
| `djust.T019` | Warning | `<name> is not a handler on <class>.` | No (1.3) |
| `djust.T019` | Warning | `<name> on <class> is not an @event_handler, so the server refuses the event.` | No (1.3) |
| `djust.T020` | Warning | `<directive> sends <names>, which <name>() does not accept.` | No (1.3) |
| `djust.T020` | Warning | `<name>() requires <names>, which this binding never sends.` | No (1.3) |
| `djust.T021` | Warning | `<attribute>=<value> is not a valid <hint> literal; the browser rejects the event.` | No (1.3) |
| `djust.T022` | Warning | `<attribute> supplies <key>, which the server reads as routing context, not an argument.` | No (1.3) |
| `djust.T023` | Info | `T019-T022 were skipped: no usable template engine to scan templates with.` | No (1.3) |

Authentication and exposure:

| ID | Level | What it reports | In 1.2.2 |
| --- | --- | --- | --- |
| `djust.S005` | Warning | `<view> exposes state without authentication.` | Yes |
| `djust.S009` | Warning | `LiveView <name> declares view-level auth but exposes the public @event_handler <name> with no per-handler authorization gate. A user who passes the view's mount auth can call this handler.` | Yes |
| `djust.S012` | Error | `LiveView <name> gates auth via @method_decorator(..., name='dispatch'); this is NOT enforced over WebSocket (only on the HTTP GET).` | Yes |
| `djust.S013` | Warning | `<view> lets any user who can open the view edit any <model> by its id.` | No (1.3) |

`T024` reports `request.user.is_staff`, `is_superuser` and `password` (and a `{% with %}` alias of a user) in a template a LiveView renders. djust never serializes those three fields, so the expression is always empty and a `{% if request.user.is_staff %}` block never shows. It looks only at paths through a `user` variable, so a form field named `password` is not reported. `T025` reports `dj-root` or `dj-view` on `<html>`, `<head>` or `<body>`. The HTTP render of such a page is complete, but over the WebSocket only the first element inside `<body>` mounts.

The binding checks (`T019` to `T022`) read templates through a Django template engine. When these checks first shipped, a project whose `TEMPLATES` setting listed only `DjustTemplateBackend` (the layout `djust new` writes) got no binding findings. From **1.3** the scan builds a compile-only engine from the Djust backend's directories, libraries and builtins, so that layout is checked too. If neither a `DjangoTemplates` nor a Djust backend gives an engine, or the engine cannot be built, the checks do not run and `djust.T023` says so at info level. What the checks cannot decide from the source is reported as dynamic or unsupported in the coverage object of `manage.py djust_check --format json`, not guessed. A single finding can be silenced with a `{# noqa: T019 -- reason #}` comment, and the comment needs a reason. `T024` and `T025` take the same comment.

### A run on the example view

`python manage.py check` reports the following for the example view. Project paths are shortened and each hint is cut after its first sentence.

```text
System check identified some issues:

WARNINGS:
?: (djust.S005) shop.views.ShopView exposes state without authentication.
	HINT: Add login_required = True or permission_required to protect this view, or set login_required = False to acknowledge public access.
?: (djust.T018) shop.views.ShopView -- template references undefined variable 'cart_total' at line 3 (shop/index.html) -- it resolves to nothing and renders as empty string, with no error.
	HINT: 'cart_total' is never set via a class attribute, a `self.cart_total = ...` assignment, or a literal `get_context_data()` return key on shop.views.ShopView, and it isn't one of the framework/Django-injected names (csrf_token, request, user, messages, forloop, ...).
?: (djust.T019) shop/templates/shop/index.html:5: 'toggle_cart' on shop.views.ShopView is not an @event_handler, so the server refuses the event.
	HINT: Decorate ShopView.toggle_cart with @event_handler.
?: (djust.T019) shop/templates/shop/index.html:6: 'add_to_cart' is not a handler on shop.views.ShopView.
	HINT: Add an @event_handler method named 'add_to_cart' to ShopView, or correct the binding.
?: (djust.V006) shop/views.py:11 -- Service instance 'client' assigned in mount(). Service instances cannot be serialized.
	HINT: Use a helper method pattern instead.

INFOS:
?: (djust.V004) shop.views.ShopView.toggle_cart() looks like an event handler but is missing @event_handler.
	HINT: Add @event_handler decorator or prefix with _ if it is private.

System check identified 6 issues (0 silenced).
```

## `djust_audit`

`djust_audit` reports what your views expose and, in its other modes, compares the code with a document you commit, probes a deployed site, or scans source for patterns. Run it as a management command. The `djust` console script has no `audit` subcommand.

| Mode | Command | What it reports |
| --- | --- | --- |
| Default | `python manage.py djust_audit` | Every LiveView and LiveComponent: template, auth configuration, mixins, public attributes it exposes, handlers and their decorators. It also lists each handler exposed over the HTTP API and flags those without `@permission_required`. |
| Permissions | `python manage.py djust_audit --permissions permissions.yaml --strict` | Deviations between the code and a committed `permissions.yaml`, as `P0xx` findings. With `--strict`, any error or warning finding makes the command exit non-zero, which is how CI uses it. |
| Dump permissions | `python manage.py djust_audit --dump-permissions` | A starter `permissions.yaml` written from the code. A view with no auth is written as public with a `TODO` note to confirm. |
| Live | `python manage.py djust_audit --live https://staging.example.com` | A probe of a running deployment: response headers, cookie attributes, paths that should not be served, and whether the WebSocket accepts a cross-origin handshake, as `L0xx` findings. The WebSocket probe needs the `websockets` package and is skipped without it. |
| AST | `python manage.py djust_audit --ast` | A scan of Python source and templates for a small set of patterns, as `X0xx` findings. |
| Accessibility | `python manage.py djust_audit --a11y` | Template accessibility findings `Y001` to `Y004`. They are warnings, and the scan fails only under `--strict`. |

On the example view the default mode prints:

```text
App: shop (1 view)
--------------------------------------------------

  LiveView: shop.views.ShopView
    Template:   shop/index.html
    Auth:       (none)  ⚠ exposes state without auth
    Mixins:     (none)
    Exposed state:
      client                   (mount)
      pings                    (mount)
    Handlers:
      * ping(**kwargs)

--------------------------------------------------
  Summary: 1 view, 0 components, 1 handler
```

The `--ast` mode looks for five patterns in Python code: an object looked up by a URL parameter with no scoping to the user (`X001`), a state-changing `@event_handler` with no permission check (`X002`), SQL built with string formatting (`X003`), a redirect built from request data (`X004`), and `mark_safe` applied to an interpolated string (`X005`). The same run also reports `X006` and `X007`, from a regular-expression scan of templates for `|safe` and `{% autoescape off %}`, and `X008`, which flags a detail view that has no object-permission override.

The [`djust_audit` guide](djust-audit.md) documents every flag, the exit codes and a CI example, so this page does not repeat them. The [declarative permissions guide](permissions-document.md) explains the `permissions.yaml` format.

## Framework defaults and opt-ins

Some behavior needs no command. These are the defaults when you write nothing, and the settings that change them.

| Behavior | Default | Opt-in or change | In 1.2.2 |
| --- | --- | --- | --- |
| Which handler methods the browser can call | Only methods decorated with `@event_handler`. The event name must be lowercase letters, digits and underscores and start with a letter, so names starting with `_` are refused. An undecorated method gets an error frame and does not run. | `LIVEVIEW_CONFIG["event_security"]` is `"strict"` by default. `"warn"` and `"open"` relax the decorator check ([modes](../advanced/security.md)). | Yes |
| Login requirement | `login_required` is `None`: no login check. `S005` and `djust_audit` flag a view that exposes state without one. | Set `login_required = True`, `permission_required = "app.perm"`, or `login_required = False` to mark the view public on purpose ([authentication guide](authentication.md)). | Yes |
| Per-object access | `get_object()` returns `None` and `has_object_permission()` returns `True`, so there is no object-level check. | Override both. The check then runs at mount and again before each event ([authorization guide](authorization.md)). | Yes |
| HTTP API | A handler is not reachable over HTTP. | `@event_handler(expose_api=True)`, plus the `djust.api` URLs in your URLconf ([HTTP API guide](http-api.md)). `djust_audit` lists exposed handlers. | Yes |
| State exposure | `exposure_policy = "legacy"`: public attributes on `self` become template context, and much of it also reaches session state and the client state mirror. | `exposure_policy = "explicit"` on a view: a value reaches the template, session or browser only when you declare it ([explicit exposure](../state/explicit-exposure.md)). The default stays `legacy`. | No (1.3) |
| Refusal codes | A refused `@permission_required` handler or object-level check sends an error frame with `code: "permission_denied"`, and the HTTP fallback answers `403` with the same code. A page can listen for `djust:error` (`detail.code`) and, for a 4401/4403 close, `djust:auth-refused` (`detail.error_code`). | Match the code in client code, not the error text ([refusal codes](authentication.md#refusal-codes)). | Partly: the uniform code and `detail.error_code` are new in 1.3 |
| djust warnings | WARNING and ERROR records from the `djust` logger reach standard error even with no `LOGGING` setting. INFO stays quiet. | Configure `LOGGING` to send them elsewhere. A record that another handler already receives is not printed a second time. | No (1.3) |
| `reauth_on_event` | Off. | Opt in with `LIVEVIEW_CONFIG = {"reauth_on_event": True}`. See the Known Limitations section of the [authentication guide](authentication.md). | Yes |

## What this does not do

- Heuristics miss things. A check that finds nothing has found nothing it knows how to look for. A method such as `increment` with no decorator matches none of the handler-name prefixes, so `validate_view` and `V004` stay silent about it. Under the default `strict` mode the browser is still refused, and from 1.3 `T019` reports a template binding to it.
- Checks run at startup and in CI, not at request time. They read the project when the command runs, nothing re-checks a request as it arrives, and a server started without `manage.py` runs no checks at all. `--live` probes a deployment once, when you run it.
- The `--ast` scan covers five Python patterns plus the three related findings above. It cannot follow a value across function calls. `X005` looks only at the argument passed to `mark_safe`, and building the string in a variable first slips through. `X003` reads only the first argument of the SQL call. Templates are read with regular expressions, not Django's template compiler.
- The binding checks (`T019` to `T022`) need a template engine to compile with: a `DjangoTemplates` backend, or from 1.3 a `DjustTemplateBackend`. Without either they report nothing, and `T023` says that they were skipped. A binding it cannot resolve from the source (an owner that resolves attributes at runtime, say) is counted as dynamic in the coverage object, not checked.
- `T024` looks for three fields (`is_staff`, `is_superuser`, `password`), and only on a `user` or `*_user` variable or a `{% with %}` alias of one. A user held under another name is not reported.
- `T025` reads the template source. It does not check what the WebSocket mount actually found.
- `T018` skips views whose template uses `{% extends %}` and says so at info level. `manage.py djust_typecheck` analyses extending templates for views that set `template_name`.
- The MCP server is optional, and nothing makes an assistant call it. `validate_view` and `detect_common_issues` see one piece of source, not the project.
- Checks can be silenced with `DJUST_CONFIG = {"suppress_checks": [...]}` or a `noqa` comment. An assistant can add one instead of fixing the finding, so read suppressions in review.
- `LiveViewTestClient.send_event` runs the permission and object-level gates, but it does not check for `@event_handler`: a test can pass for an undecorated handler that the browser is refused on. `T019` and `V004` cover that gap, and only for bindings and names they can see.
- `djust_ai_context` and the `docs/ai/` pages give an assistant information. They do not constrain what it writes, and the Security section in the generated file is advice, not a check.
- `exposure_policy = "explicit"` and `reauth_on_event` are opt-in. A project that has not set them runs with the defaults in the table above.

Write tests for the behavior you care about, and review generated code before you merge it. The [testing guide](../testing/index.md) covers `LiveViewTestClient` and the smoke test.

## A workflow that uses all of it

Once per project, and again when views change:

```bash
# Context for the assistant (1.3 refuses to overwrite an existing CLAUDE.md; 1.2.2 replaces it)
python manage.py djust_ai_context --format claude

# Optional: let the assistant check its own code
pip install 'mcp[cli]'
claude mcp add --transport stdio djust -- python manage.py djust_mcp

# Record what each view is meant to require, then review every TODO
python manage.py djust_audit --dump-permissions > permissions.yaml
```

After each change:

```bash
python manage.py check --tag djust
python manage.py djust_audit
```

In CI:

```bash
python manage.py check --tag djust --fail-level WARNING
python manage.py djust_audit --permissions permissions.yaml --strict
python manage.py djust_audit --ast --strict
```

Against staging, after a deploy:

```bash
python manage.py djust_audit --live https://staging.example.com --strict
```

With `DEBUG = True` and `watchdog` installed, djust 1.2.2 printed a `[HotReload]` line to standard output, so the line landed at the top of any file you redirected the output into, and `--dump-permissions >` produced a file that did not parse. From **1.3** the line goes to standard error, or to the `djust` logger when a logging handler is listening. On 1.2.2, run `--dump-permissions >` and `--json >` with `DEBUG` off.

## See also

- [MCP server](mcp-server.md): setup for each editor and the full tool list
- [`djust_audit`](djust-audit.md): every flag, exit code and a CI example
- [Error code reference](error-codes.md): every check ID with its cause and fix
- [Declarative permissions document](permissions-document.md): the `permissions.yaml` format
- [Explicit exposure](../state/explicit-exposure.md): opt-in control over what reaches the template, session and browser
- [Authentication](authentication.md) and [authorization](authorization.md): the view-level and object-level hooks
