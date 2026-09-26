# ADR-037 D3: staged delivery of the 1.3 documentation

Date: 2026-09-25. Owner decision: delivery is verified on a staged djust-docs build
pinned to a 1.3 release candidate. Production docs.djust.org stays on stable 1.2.x
until 1.3.0 final, and bumping it with 1.3.0 is the production gate.

## Build

- djust-docs at `main` (`a44f0f6`, which includes djust-docs#12), in a separate
  worktree, served locally on `http://localhost:18463` by uvicorn.
- `DJUST_VERSION=1.3.0rc3`. djust imported from tag `v1.3.0rc3` (`PYTHONPATH`).
- `DJUST_DOCS_EXTRA` = `docs.djust.org/content`, the tutorials and theming pages
  the Dockerfile ships.
- Two docs sources:
  1. tag `v1.3.0rc3`'s `docs/`, which is what 1.3.0rc3 published;
  2. this branch's `docs/` (`feat/adr-037-d3`), which is what the next 1.3
     candidate will publish.

## Results

**v1.3.0rc3 docs.** `make docs-verify`: nav **FAIL** (1 finding), links ok (143),
symbols ok* (718 checked, 10 advisory, none for `@<menu>.on.<output>`), versions ok,
a11y ok (143). The nav finding:

> unreachable page: guides/accounts.md is not listed in _config.yaml (served at
> /guides/accounts/ via fallback grouping)

ADR-039's accounts guide (1.3.0rc1) was never added to djust's
`docs/website/_config.yaml`. This branch adds it under Guides, after
Authentication.

**This branch's docs.** `make docs-verify` **passes**: nav, links, symbols,
versions and a11y are all ok (143 pages). The page check:

| Page | Status | Code blocks (`<pre>`) | Linked in nav | Content present |
|---|---|---|---|---|
| `/guides/interactive-components/` | 200 | 10 | yes | "DropdownMenu", "keyed collections" |
| `/guides/forms/` | 200 | 26 | yes | "Editing one record with `ModelFormMixin`" |
| `/core-concepts/components/` | 200 | 17 | yes | "Choose the owner" |
| `/core-concepts/events/` | 200 | 44 | yes | "Typed event parameters (strict policy)" |
| `/guides/error-codes/` | 200 | 79 | yes | T019–T022, C022 |
| `/guides/mcp-server/` | 200 | 8 | yes | `scaffold_view` |
| `/api-reference/components/` | 200 | 20 | yes | generated `DropdownMenu` tables, `selected` output |
| `/guides/accounts/` | 200 | 11 | yes (after this branch's `_config.yaml` fix) | "Accounts" |

**Second finding: authoring markers rendered as text.** djust's Rust Markdown
renderer escapes raw HTML, so whole-line authoring comments showed on the page. The
existing `<!-- doc-snippet-check: skip -->` and D2's 11 `<!-- djust-example -->`
markers appeared as literal text on the interactive-components, forms and events
pages. djust-docs#13 drops whole-line comments outside fenced code before
rendering. With it, all 8 pages show **0** escaped markers, and their code-block
counts are unchanged.

Production is unchanged. The remaining gate is to bump docs.djust.org (djust-docs'
`DJUST_VERSION`) with 1.3.0, after djust-docs#13 merges.
