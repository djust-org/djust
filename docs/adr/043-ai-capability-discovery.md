# ADR-043: djust tells AI agents what it already provides — checks point at discovery, discovery covers components and theming

**Status**: Proposed
**Date**: 2026-10-10
**Citations**: paths pinned to `main` at `edda6475b`.
**Deciders**: Project maintainers
**Related**:
- `python/djust/management/commands/djust_ai_context.py` — generates CLAUDE.md / .cursorrules / copilot instructions
- `python/djust/mcp/server.py` — MCP tools (`get_framework_schema`, `list_components`, ...)
- `python/djust/management/commands/djust_check.py`, `djust_audit.py` — the checks agents already run
- `python/djust/components/` (~170 components, `registry.py`, `component_gallery`), `python/djust/theming/` (40+ presets, `theme_context`)
- `docs/llms.txt`, `docs/llms-full.txt`, `docs/BEST_PRACTICES_AI.md`, the `djust` / `djust-dev` agent skills

---

## Summary (plain language first)

An AI agent built a complete djust app ("Ledger", 18 tasks, every task reviewed, `manage.py check` + `djust_audit --ast` run after each one) and shipped it with hand-rolled HTML tables, native `<select>`s and a 40-line custom stylesheet. djust ships `data_table`, `filter_bar`, `combobox`, `sheet`, `toast`, `app_shell` and a theming system that would have replaced all of it. The agent never learned they existed.

It did everything the framework asks of an agent. The gap is that **none of djust's AI-facing surfaces mention the component library or theming**:

| Surface | Mentions `djust.components` / theming |
|---|---|
| `djust_ai_context` (752-line CLAUDE.md) | no |
| `docs/llms.txt`, `docs/llms-full.txt`, `docs/BEST_PRACTICES_AI.md` | 0 / 0 / 0 |
| `djust` agent skill (SKILL.md, index.md, catalog/*) | 0 |
| MCP `list_components` | lists the *project's* LiveComponents only, not the bundled library |
| `component_gallery` | visual, served on a port — for humans |
| `manage.py check` / `djust_audit` | no UI rules, no pointers |

Decision: make the checks agents already run **flag hand-rolled UI** and **point at the discovery commands**, add a small `djust ai` command family for machine-readable capability discovery, and close the documentation gaps.

## Context

Agents discover a framework through four channels, in this order of reliability:

1. **Output of commands they already run** — `manage.py check`, test runs, linters. Seen every task, no initiative needed.
2. **Context files loaded automatically** — CLAUDE.md / AGENTS.md / skills.
3. **Tools they can call** — MCP, CLI `--help`.
4. **Docs they must think to search.**

djust invests in 2–4, but only for the *directive* surface (events, decorators, lifecycle, streams). The UI layer — the part most visible to the user — exists only in channel 4 (component docs) and for humans (gallery). Channel 1 says nothing at all, and it is the only channel an agent cannot skip.

## Decision

### D1. `manage.py check` points agents at discovery (channel 1)

Registered as **Django system checks** (not only in `djust_check`): `manage.py check` is the one command every agent runs on any Django project without knowing djust; `djust_check` is only reached by agents that already know djust. In the Ledger build the agent ran `manage.py check` out of Django habit and `djust_audit --ast` because the djust-dev skill showed it — it never ran `djust_check`. When `manage.py check` runs and the caller looks like an agent (`CLAUDECODE`, `CURSOR_AGENT`, `CODEX_*`, `DJUST_AGENT=1`, or non-TTY stdout), append one INFO block — never an error, suppressible with `DJUST_AI_HINTS=0`:

```
djust.I001: Capability discovery for AI agents
  Before writing templates or views, see what djust already provides:
    python manage.py djust_ai inventory --json   # what's installed/enabled here, component catalog, theme
    python manage.py djust_ai suggest "<intent>" # intent -> components + tag + snippet
    python manage.py djust_ai manifest           # one llms.txt-style doc (directives, components, theming, best practices)
    python manage.py djust_audit --ast           # security + UI rules
    python manage.py djust_mcp                   # MCP server (list_ui_components, get_ui_component, ...)
```

Plus targeted INFO checks that fire on project state, not on every run:

- `djust.I002` — LiveViews exist but `djust.theming` is not in `INSTALLED_APPS` / `theme_context` not configured.
- `djust.I003` — `djust.components` available but unused while templates contain hand-rolled equivalents (see D2's X1xx findings).

### D2. `djust_audit` UI rules (channel 1, catches it regardless of the agent)

New `X1xx` rules (the `X0xx` series is the existing AST audit; `djust.U001` is taken by the update check) over LiveView templates (warnings, `# djust: allow X101` escape):

| Rule | Detects | Suggests |
|---|---|---|
| X101 | raw `<table>` rendering a stream / loop in a live template | `{% data_table %}` / `data_grid` (+ `infinite_scroll`) |
| X102 | native `<select>` with `dj-change` over > N options | `combobox` / `rich_select` |
| X103 | hand-built overlay (`position: fixed` panel, `.modal`, `.panel`) | `sheet` / `modal` |
| X104 | message `<p>` styled by class switching on message text | `toast` / `page_alert` |
| X105 | app stylesheet defining colors outside theme tokens | theming tokens / preset |

Each finding names the component and the one-line tag to use. This rule set alone would have caught Ledger at its first UI task.

### D3. `djust_ai` command family (channel 3, machine-readable)

`python manage.py djust_ai <subcommand>` (and `djust ai` on the CLI):

- **`inventory [--json]`** — djust version; bundled apps available vs enabled (`components: available, not enabled`); active theme preset; component catalog grouped by category with one-line purpose; project views/components; X1xx findings summary. The "what do I have to work with?" call.
- **`suggest "<intent>" [--json]`** — ranks components from `components/registry.py` metadata against an intent ("table with filters, bulk select, infinite scroll") → tag, required props, minimal snippet, related components. Pure local search; no network.
- **`manifest`** — a single llms.txt-style document generated from the registries: directives, decorators, lifecycle, **components, theming**, best practices, and the discovery commands. `djust_ai_context` becomes a thin wrapper around it so the two can't drift.

MCP gains `list_ui_components(query?)` and `get_ui_component(name)` (or `list_components(scope="library")`), backed by the same registry.

### D4. Documentation and scaffolding (channels 2 and 4)

- `djust_ai_context`, `llms.txt`, `llms-full.txt`, `BEST_PRACTICES_AI.md`, and the `djust` skill each gain a short **"UI building blocks — use these before writing markup"** section near the top: use case → component table, theming setup, and the D3 commands.
- Fix the djust-dev skill's naming mismatch: its prose says "Run `djust check`" but its example runs `python manage.py djust_audit --ast`; name the commands exactly and list `manage.py check`, `djust_check`, `djust_audit`, and the D3 commands with one line each on when to use which.
- `djust new` / `startapp` scaffold a themed project by default (`djust.theming` + `djust.components` installed, `app_shell` base template), and the skill tells agents to start projects with `djust new`.

## Consequences

- Agents learn about components and theming from output they cannot avoid (D1/D2), not from docs they must think to read.
- One source of truth (component registry) feeds `suggest`, `manifest`, MCP, and the docs sections, so new components become discoverable without doc edits.
- Cost: X1xx heuristics will have false positives; they are warnings with an allow-comment escape, and I002/I003 are INFO. D1's hint block is gated to agent-like callers to keep human `check` output clean.
- Rollout order by leverage: D2 → D1 → D4 (docs/skill) → D3 → D4 (scaffolding).

## Evidence

Ledger (sub-project 1, 2026-10-10): 18 tasks, 30+ per-task reviews, `djust_audit --ast` clean, 177 tests green — and a UI the owner immediately flagged as not following djust themes or components. The retrospective traced it to the table in §Summary: every surface the agent consulted was silent about the UI layer.
