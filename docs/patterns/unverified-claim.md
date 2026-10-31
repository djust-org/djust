# `unverified-claim`

**Rule** (the line the rule sheet carries): Open the file and cite the path (and line) before
writing any claim about what the code or docs contain; a claim that cannot be checked must
not be written.
→ this page

**Status**: `skill` · **gate**: partial — `scripts/check-doc-snippets.py` (doc figures),
the #2652 symbol check (documented methods), `scripts/check-changelog-test-counts.py` (JS test
counts) · **candidate for mechanical gate**: yes
**Origin**: the v1.2.0-6 retro (#2838) — the first instance of this class in this repo to
cause a *code* defect rather than a documentation one.

## Instances

| PR | what drifted / what was missed | cure | rule in force? |
|---|---|---|---|
| #2838 | the changelog asserted `dj-key="Enter"` is "the documented way to restrict an undotted handler". `dj-key` is the VNode **list-identity** attribute (`docs/website/advanced/vdom-architecture.md:187`). The false claim became the *premise* for the code change, which silenced handlers on keyed list rows — a regression against `main` | change reverted; `dj-key` deliberately not read as a key; rule written (v1.2.0-6 retro arc, rule 2) | no (origin) |
| #2838 | the changelog joined a quote spanning **two different docs files** with an ellipsis and credited it to one | citation split into the two real sources | no |
| #2838 | a commit message implied the change moved the client module count; the generated `client-sizes.json` had said 56 all along | message corrected | no |
| #2546 | shipped "a false categorical claim of exactly the class this row exists to kill" | corrected in review | no |
| #2534 | PR body: a test class has "14 shapes"; the case list has 19 | corrected | no |
| #2554 | CHANGELOG cites "two new cases in `TestCustomFilters` of the #1121 file" — no such class | corrected | no |
| #2573 | CHANGELOG + docstring assert an import-ordering fact that does not hold | corrected | no |
| #2607 | `docs/TEMPLATE_BACKEND.md:260` still reports `47.09% (493 of 1047)` after a behaviour change moved it | corrected | no |
| #2843 | PR body claimed a test "needs the compiled Rust extension, unavailable in this worktree env" — it runs (24 tests pass) and `import djust._rust` works | body corrected before merge | no |
| #2147 | the issue's own premise — that a fix was in place — was false for the error arm | the arm was fixed | no |
| #3463 | the first-draft `changelog.d/3454.security.md` and ROADMAP row presented a project-marker heuristic as Windows "security hardening"; it is not an ownership check (anyone who can plant `manage.py` can plant a marker) | reworded to state what it stops and what it doesn't; ownership check split to #3462 (independent review) | yes, fired |
| #3472 | first PR body said the new gates were "not PR gating"; the JavaScript job's lint step does run on PRs | review caught it before merge; body and fragment corrected | yes, fired |
| #3473 | the issue asserted "no test assertion is involved" and a lost runner, from a `gh run view --log` copy truncated at ~8,000 of 24,882 lines; the real cause was a Python 3.14 test failure exposing GHSA-4h5m-xcmg-vvmw | raw job log via the API; CLAUDE.md rule (v1.3.0-11/12 arc) | yes, missed |
| #3474 | the implementer's CodeQL report called #3004 a false positive by citing a different file, and #3042 a false positive when CodeQL was right | reviewer re-verified every dismissal against the source before filing it | yes, fired |
| #3692 | the issue's title and body said CI shards "ended silently"; the full logs showed real failures (read through a truncated view) | issue corrected, premise named in the retro | yes, missed |
| #3704 | the renderer "reads HTML comments and script bodies as text" was written into code, three tests, docs and a changelog; the lexer is HTML-blind (`crates/djust_templates/src/lexer.rs:404-407`). Round 2: docs said "False positives: None known" | fresh-context review; a differential test against `djust._rust.render_template` | yes, fired |
| #3708 | first PR body carried a placeholder count and pointed at comments that did not exist yet | reviewer caught it before merge | yes, fired |

## Detection

- **The three checkable shapes**: a path, a symbol, a count. All three are mechanically
  verifiable today for `docs/` and `README.md`; `changelog.d/*.md` and PR bodies are the
  uncovered surface, which is where seven of the ten above live (#2849 proposes closing it).
- **Cite the line.** `path:line` makes the claim falsifiable by a reader in one grep; a bare
  claim makes it falsifiable only by re-deriving the whole analysis.
- **For a count, count.** `check-changelog-test-counts` exists precisely because hand-counted
  test claims drift; note its own regex has a prose false-positive (#2839) — share one parser
  rather than adding a third.

## Rejected shapes

- **"Add a line to the PR template asking authors to verify claims."** Five of the ten shipped
  *past* reviewers reading them; a template line adds no reader.
- **Relying on review.** True in nine of ten cases here — but the tenth became a regression,
  which is an expensive way to have a claim checked.
- **Marking the claim `unverified:` instead of deleting it.** Leaves the false assertion in the
  reader's path. Delete or verify.
