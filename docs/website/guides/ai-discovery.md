---
title: "AI Capability Discovery"
slug: ai-discovery
section: guides
order: 14.6
level: intermediate
description: "Discover installed capabilities, find bundled UI components by intent, and give AI agents a framework manifest."
---

# AI Capability Discovery

djust can tell an AI assistant what it already provides before the assistant writes views or templates. The inventory distinguishes installed extras from enabled apps, the catalog suggests reusable UI by intent, and the manifest supplies framework guidance. Use these to find native djust behavior before building custom markup or application plumbing.

## Commands and real output

The output below comes from a small example project with one routed
`CounterView` and theming not enabled. The version, installed extras and
project classes will differ in yours. `python -m djust ai` is the same as the
installed `djust ai` entry point.

### Inventory: what is available here?

```bash
python -m djust ai inventory
# Equivalent inside a configured Django project:
python manage.py djust_ai inventory
```

Beginning of actual output:

```text
djust 1.3.0rc7

Bundled apps
  djust.components: available, not enabled (add "djust.components" to INSTALLED_APPS) — ~190 template-tag UI components (tables, forms, overlays, feedback)
  djust.theming: available, not enabled (add "djust.theming" to INSTALLED_APPS) — design systems + color presets via CSS tokens; {% theme_head %}
  djust.auth: available, not enabled (add "djust.auth" to INSTALLED_APPS) — account backends + page kit (ADR-039)
  djust.admin_ext: available, not enabled (add "djust.admin_ext" to INSTALLED_APPS) — LiveView-powered admin extensions

Theme: material / default, mode system (enabled: False)

Components (188)
```

Project excerpt from the same output:

```text
Project
  views: 1 — CounterView
  components: 0

UI audit: none: run python manage.py djust_audit --ast
```

Inventory reports bundled app availability/enabling, theme configuration,
the component catalog, project views/components, UI audit cache state, and
discovery commands. It loads the root URLconf before collecting project
classes, so views imported only through routes appear. An unavailable
URLconf can fall back to already-loaded classes. It reads the audit summary;
it does not run a new audit.

### Suggest: find components by intent

```bash
python -m djust ai suggest "toast" --limit 1
# Django management command:
python manage.py djust_ai suggest "toast" --limit 1
```

Actual output:

```text
1. server_toast_container (Feedback) — Receive server toast notifications in a positioned live region
   {% load djust_components %}
   {% server_toast_container position="top-right" %}
   props: position (from example)
   related: toast_container, page_alert
```

Ranking is deterministic token matching against names, labels, keywords,
purposes, and categories, rather than a model call. `--limit` defaults to 5
and is clamped to 1–20. Intent is truncated to the first **512 characters**
before ranking and JSON output; an empty/tokenless intent is an error.
Props can come from a callable signature or an example snippet: example
props are not a required-prop contract. Missing optional rendering extras
can leave props unknown without hiding the catalog.

### Manifest: a framework reference

```bash
python -m djust ai manifest
# Django management command:
python manage.py djust_ai manifest
```

Beginning of actual output:

```text
# djust 1.3.0rc7 — capabilities manifest

Discover bundled UI components and framework capabilities before building.

## Discovery commands

  python manage.py djust_ai inventory --json     # what's installed/enabled here, component catalog, theme
  python manage.py djust_ai suggest "<intent>"   # intent -> components + tag + snippet
  python manage.py djust_ai manifest             # one llms.txt-style doc (directives, components, theming, best practices)
  python manage.py djust_audit --ast             # security + UI rules
  python manage.py djust_mcp                     # MCP server (list_ui_components / get_ui_component for the component catalog)
```

The remaining Markdown includes component names/purposes and child tags,
theming, directives, lifecycle methods, decorators, conventions, security,
state guidance, and common pitfalls. It describes framework capabilities,
not your project's installed configuration. There is no `manifest --json`.

## How `djust ai` resolves a project

1. If `DJANGO_SETTINGS_MODULE` is nonempty, add cwd to the import path,
   initialize Django, and call `djust_ai` **in-process**. A settings-load
   failure prints the reason; inventory exits 2, while suggest/manifest
   fall back to standalone discovery.
2. Without that setting, **inventory only** searches for `manage.py` in cwd,
   then walks upward. It checks the candidate before stopping at a directory
   containing `.git`, `pyproject.toml`, or `setup.cfg`, at the user's home,
   or at the filesystem root. It never searches beyond that boundary.
   An accepted file runs in a subprocess with the current Python interpreter
   and the file's directory as cwd: `python /path/manage.py djust_ai inventory`.
   Arguments such as `--json` are forwarded and the child exit code is returned.
3. Without an accepted entry point, inventory exits 2 with project setup
   guidance. **Suggest and manifest stay in-process** without settings and
   do not search for or execute nearby `manage.py` files; they work anywhere.

A discovered `manage.py` must be a regular file. On POSIX systems it must
also be owned by the current user and not be group/world-writable, and its
containing directory must not be world-writable; Windows has no equivalent
mode bits, so only the regular-file and project-boundary rules apply there. Stat failures also reject
it. Rejection exits **2**, prints the specific reason and direct-invocation
guidance, and does not continue searching for a different entry point.
Explicitly invoking `python manage.py djust_ai ...` remains your own choice
of project entry point.

Example rejection output for a group/world-writable `manage.py` (exit 2):

```text
djust ai: rejected /srv/shared/myproject/manage.py: entry point is group/world writable; run python manage.py djust_ai inventory directly.
```

## JSON contracts

Use `inventory --json` and `suggest "<intent>" --json` for structured output.
Both currently emit `version: 1`. Within a version, consumers should accept
additive keys and ignore unknown fields; breaking schema changes bump the
version. Do not parse the human-readable output.

| Payload | Top-level fields |
| --- | --- |
| Inventory | `version`, `kind: "inventory"`, `djust_version`, `apps`, `theming`, `components`, `project`, `ui_audit`, `discovery` |
| Suggest | `version`, `kind: "suggest"`, `intent` (truncated), `results` |

`apps` records contain `app`, `purpose`, `extra`, `available`, `enabled`,
and `missing_requirements`. `theming` has `enabled` plus `theme`, `preset`,
`default_mode`, `presets_available`, or an `error`. `components` contains
`count` and `categories`; categories contain `slug`, `label`, and component
summaries (`name`, `component_kind`, `label`, `purpose`). `project` has
`views` and `components` lists with `name`, `module`, and `template`.
`ui_audit` includes `status`, `path`, `reason`, `generated_at`, `counts`,
`total`, and `stale_paths`; discovery entries contain `invocation` and `purpose`.

Suggest results contain `name`, `component_kind` (`tag` or `class`), `label`,
`category`, `category_label`, `purpose`, `keywords`, `load`, `snippet`,
`props`, `props_source`, `children`, `related`, `required_props`, and `score`.
`props` entries contain `name`, `required`, and `default`; `props` and
`props_source` can be null. `required_props` is null unless signature-derived.

## MCP component discovery

The [MCP server](mcp-server.md#ui-component-catalog-no-django-required) exposes
`list_ui_components(query="")` and `get_ui_component(name)` without a Django
project. Empty-query listing returns `version: 1`, `kind: "catalog"`, `count`,
and `categories`. A query returns `kind: "suggest"`, `query`, and `results`
(up to 10), using the same 512-character cap and ranking. Component lookup
returns `kind: "component"`, the entry fields, and example `variants` with
`name`/`template`. Child tag names resolve to their parent. Unknown names
return `error` and `did_you_mean`, without a version/kind envelope.
`kind` identifies the payload; `component_kind` identifies a tag or class.

## Informational checks

All three are Django **INFO** checks registered under the `djust` tag:

- [I001](error-codes.md#i001-capability-discovery-for-ai-agents): command hints,
  only when `sys.argv[1] == "check"` and the shell identifies itself as an agent;
  emitted at most once per process. It does not emit for runserver/migrate.
- [I002](error-codes.md#i002-liveviews-without-theming): non-abstract user
  LiveViews exist but `djust.theming` is absent from `INSTALLED_APPS`.
  It can also print during runserver/migrate system checks.
- [I003](error-codes.md#i003-hand-rolled-ui-in-the-last-audit): a fresh UI
  summary contains X101–X104 findings, even if `djust.components` is installed
  and enabled. X105 alone does not trigger it; missing, invalid, or stale
  caches are silent. See [cache lookup and limits](djust-audit.md#ui-summary-cache).

I001 recognizes this exact environment-variable list:

```text
DJUST_AGENT
CLAUDECODE
CURSOR_AGENT
CODEX_SANDBOX
CODEX_SANDBOX_NETWORK_DISABLED
CODEX_THREAD_ID
```

Any listed value except empty, `0`, `false`, `no`, or `off` (case-insensitive)
identifies an agent. Editor detection is best effort; **`DJUST_AGENT=1` is
the explicit contract**.

Silence individual IDs with `SILENCED_SYSTEM_CHECKS = ["djust.I001"]` or
`DJUST_CONFIG = {"suppress_checks": ["I001"]}` (use I002/I003 as appropriate).
`DJUST_AI_HINTS=0` disables **I001 only**; `false`, `no`, and `off` also work.
A silenced I001 does not consume its once-per-process flag.

**Behavior change:** an agent-run `python manage.py check --fail-level INFO`
now fails unless I001 is silenced. Any unsilenced I002/I003 also reaches that
threshold; the default ERROR threshold is unaffected. Silencing I001 alone
therefore does not guarantee success. The discovery management command
itself skips system checks.

Actual output from `DJUST_AGENT=1 python manage.py check --tag djust`
(exit 0 in the throwaway project):

```text
System check identified some issues:

INFOS:
?: (djust.I001) Capability discovery for AI agents
	HINT: Before writing templates or views, see what djust already provides:
  python manage.py djust_ai inventory --json     # what's installed/enabled here, component catalog, theme
  python manage.py djust_ai suggest "<intent>"   # intent -> components + tag + snippet
  python manage.py djust_ai manifest             # one llms.txt-style doc (directives, components, theming, best practices)
  python manage.py djust_audit --ast             # security + UI rules
  python manage.py djust_mcp                     # MCP server (list_ui_components / get_ui_component for the component catalog)
?: (djust.I002) 1 LiveView(s) found, but djust.theming is not in INSTALLED_APPS.
	HINT: Add "djust.theming" to INSTALLED_APPS and {% load theme_tags %}{% theme_head %} to your base template for design tokens, light/dark mode and presets; see `python manage.py djust_ai inventory`. Silence with DJUST_CONFIG = {'suppress_checks': ['I002']}.

System check identified 2 issues (0 silenced).
```

## See also

- [AI Guardrails](ai-guardrails.md)
- [Error code reference](error-codes.md)
- [Audit and UI summary cache](djust-audit.md#ui-summary-cache)
