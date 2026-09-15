# Pattern index

One row per failure class this repo guards against. Each row's **rule** is the line
the rule sheet carries — the corresponding rule lives in `CLAUDE.md` (its
`## Process canonicalizations …` sections are the rule sheet) and the page beside this
file holds the instance table, detection and rejected shapes.

`/pipeline-retro` **Stage 3.7** reads the `rule in force?` column of a page's instance
table to decide whether a rule should be **KEPT**, **HARDENED** into a mechanical gate,
or **DEMOTED**. Without a page here, Stage 3.7 reports `RULE_GATE_SKIPPED` and rules
accumulate with nothing ever retiring them — which is what happened in this repo until
the v1.2.0-6 retro added these.

Page format: [TEMPLATE.md](TEMPLATE.md). The upstream family wiki
(`pipeline-skills`, `docs/patterns/`) holds the same classes from the pipeline's side;
a class page here carries *this repo's* instances and pairs with *this repo's* rule.

| class | rule | status · gate | origin |
|---|---|---|---|
| `parallel-path-drift` | A per-path invariant implemented in several places must be fixed on every path, or expressed once. | skill · partial (`tests/js/tick_buffer_version_desync_2829.test.js` transport-source pin; `scripts/check-bundle-init-order.mjs`) | #1646 |
| `unverified-claim` | Open the file and cite the path (and line) before writing any claim about what the code or docs contain. | skill · partial (`check-doc-snippets`, `#2652` symbol check, `check-changelog-test-counts`) | v1.2.0-6 retro (#2838) |
| `fix-reproduces-own-bug` | Enumerate every caller's invariant of the shared state a fix touches before the first edit. | skill · none | v1.2.0-6 retro (#2147, #2838) |
| `retro-dropout` | A task is not complete until a retro artifact exists on the PR; a *bucket* is not complete until a `RETRO.md` entry exists. | skill + script · `scripts/check-retro-coverage.py`, `scripts/audit-pipeline-bypass.py` | #1212, #2140, #2848 |
