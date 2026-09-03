# Project-local pipeline state templates

These files override the global pipeline-skills templates
(`~/.claude/skills/pipeline-ship/` and friends) when running
`/pipeline-ship`, `/pipeline-run`, or `/pipeline-next` from this project root.

The pipeline-next skill checks for these files first; if present, they
replace the skill's default `templates/<type>-state.json`. See the global
skill's §"State file templates" for the precedence logic.

## What's customized vs. the upstream templates

### PR target branch — `1.1` (not `main`)

All three templates set `"pr_target_branch": "1.1"`. After the 2026-05-23
release-branch cut (1.0 stabilization + 1.1 active dev, per ADR-019 and
ROADMAP §v1.1.0), pipeline-{run, next, ship} target `1.1` so feature/bugfix
PRs land on the active-dev line, not on `main`.

**Why not `main`?** With the release-branch model in place, `main` becomes
the trunk that next-major (v1.2+) work eventually targets. Until v1.2
planning starts, `main` carries no active dev — every feature/bugfix
intended for v1.1 belongs on `1.1`.

**When v1.1 ships** (final tag cut, post-soak re-strategize complete):
flip `pr_target_branch` to `"1.2"` and cut a `1.2` release branch from
main. Same template, same workflow — just the target moves forward one
version. (Suggested PR title: `chore(pipeline): flip pr_target_branch to 1.2`.)

**WebView-mode escape hatch.** If a bugfix MUST land on `1.0` (the
stabilization line — e.g., a P0 regression before GA cuts), override per
pipeline-run by passing `--target 1.0` (see pipeline-run §Arguments) or
edit the state file's `pr_target_branch` after `/pipeline-next` creates it.

### Stage 7 (feature-state.json) / Stage 3 (ship-state.json) Self-Review

Adds one extra mandatory checklist item (djust-specific):

> **DOWNSTREAM-APP NAME LEAK SCAN** — grep commit subject, commit body,
> PR title, PR body, and full diff for every identifier in `.customer-names`
> (project root, gitignored). Any match = REVIEW_FAILED; fix with
> `git commit --amend` + `gh pr edit` before proceeding.

Why: djust sits alongside multiple private downstream apps (a downstream consumer,
a downstream consumer, a downstream consumer, a downstream consumer, etc.). Patterns extracted upstream into the
public framework repo must not ship with private-project identifiers
in commit metadata or file contents. PR #836 nearly shipped "a downstream consumer"
in its commit subject + PR body — caught at Stage 3 during pipeline-ship,
but reviewer vigilance is not a durable gate. This mechanical grep is.

### Stage 5 (Implementation) / Stage 9 (Documentation) — two-commit shape gate

`feature-state.json` Stage 5 forbids CHANGELOG.md edits; Stage 9 is the
canonical CHANGELOG commit boundary. The implementation commit must
contain only code + tests; the docs commit must contain only docs +
CHANGELOG.

**Why**: v0.9.1 PRs #1163 + #1164 ran two implementer agents
concurrently on the same checkout. Both edited `[Unreleased]` while
their branches were alternately checked out via pre-commit
stash/restore. The first agent's commit captured the second agent's
CHANGELOG hunks — Stage 11 caught it as a 🔴 (CHANGELOG cross-
contamination). Three subsequent v0.9.1 PRs (#1166, #1168, #1170)
adopted the two-commit shape and shipped clean. Canonicalized in
v0.9.1 retro / Action Tracker #181 / GitHub #1173.

### Stage 6 (Test Execution) — 3-clean-runs gate for pollution-class fixes

The `bugfix-state.json` template (loaded by `pipeline-next` when the
task is a bugfix) has an unconditional Stage 6 mandatory item: when
the task description matches `/pollution|leak|flak|test isolation/i`,
run the full pytest suite **3 times consecutively** — all three runs
clean. Single-run pass is insufficient for pollution-class fixes by
definition (pollution shows up under specific orderings).

The gate lives in `bugfix-state.json` (not `feature-state.json`)
because feature pipelines hardcode `pipeline_type: "feature"` — a
"if pipeline_type == 'bugfix'" predicate in feature-state.json would
never evaluate true. Bugfix pipelines load the bugfix template, where
the predicate is implicit.

**Why**: v0.9.1 PR #1159 (the #1134 bisect) caught a hidden second
polluter (`sys.modules` rebind in `test_dev_server_watchdog_missing.py`)
on the third verification run — would have shipped silently otherwise,
and the next PR would have tripped the same flake. Canonicalized in
v0.9.1 retro / Action Tracker #182 / GitHub #1174.

### Symmetric ship-pipeline gates (`ship-state.json`)

The two-commit-shape rule applies to ship-pipelines too: even though
`/pipeline-ship` starts from existing implementation in the working
tree, the docs commit it produces (Stage 5 in ship-state, not Stage 9
as in feature-state) is the CANONICAL CHANGELOG COMMIT BOUNDARY for
that pipeline. The Stage 5 mandatory item enforces "docs + CHANGELOG
only" — no implementation code in this commit. Eliminates the same
class of cross-edit collision when two ship-pipelines run on adjacent
feature work.

### `.customer-names`

Sibling file in the project root (gitignored). Plaintext, one name per
line. `#` comments and blank lines are allowed **but must be stripped
before feeding to grep** — otherwise the literal `#` matches every
`#NNN` PR reference in commit bodies and every scan false-positives.

Correct recipe:

```bash
CN=$(grep -v '^#' .customer-names | grep -v '^$')
git log main..HEAD --format='%B' | grep -iF "$CN" && echo LEAK
```

Maintained per-operator — add every downstream app identifier you
might accidentally paste in.

## `conformance-state.json` — a row scored by a family of Django-suite cells

Use it for any ROADMAP row whose progress is measured by the Django template
suite (`make django-template-suite`, `.django-src/last-run.txt`): the v1.2.0-3
rows (#2549, #2556, #2547, #2558, #2557, #2563) and any later
compatibility family. Do NOT use it for a feature or a bugfix that the suite
does not score — those keep `feature-state.json` / `bugfix-state.json`.

Same stage names as `feature-state.json`, so `pipeline-run` matches them. The
differences, written into the stage `_note` / `checklist` / `subagent_prompt`:

- **Planning** names the FAMILY (a `--label` or test-id prefix of Django's
  `template_tests`), lists its cells with their current OK/FAIL/ERROR state
  from `.django-src/last-run.txt`, and names the Django reference
  implementation to lift (#1077).
- **Implementation** is a LOOP with one objective — *turn these cells green
  without any other cell moving*: re-run the family after each change
  (`scripts/run-django-template-suite.py run --label <family>`), compare
  against Django in-process, stop when the family is green or every remaining
  cell is blocked with a written reason. The whole-suite `compare` must show 0
  regressions; gate-off per #1468 stays.
- **Code Review** is one adversarial worktree review per family, not per tag.
- **Documentation** writes the new baseline (`--write-baseline`), the
  `docs/TEMPLATE_BACKEND.md` claim line, and the generated support lists
  (`scripts/generate-template-backend-lists.py --write`).

`pr_target_branch` is `main` (the 1.2 line is `main`; see the release-branch
note above for the older `1.1` value the other two templates still carry).

## Worktree environments — `make worktree-env` (#2526)

Every implementer/reviewer subagent that needs to *build or run* code gets its
own `git worktree`, and until now every worktree shared the main checkout's
`.venv`. Two costs followed: `make build` from a worktree repoints the shared
venv's `djust.pth` at that worktree (every other row now tests the wrong
tree), and the one checkout holding a usable build serialises every row
behind it (six hours on 2026-09-01).

From any checkout — the main clone or a worktree — run:

```bash
make worktree-env
.venv/bin/python -c "import djust; print(djust.__file__)"   # prints THIS tree
```

What it does, idempotently: creates `./.venv` with the interpreter named in
`.python-version` (`python3.12`), `pip install`s `requirements-dev.txt` plus
pyproject's runtime and `[dev]` dependencies (pip, **not** uv — `uv sync`
re-locks `uv.lock` on this machine), then `.venv/bin/maturin develop
--release` so the tree owns its extension and its `djust.pth`. Cargo's
`target/` is already per-checkout, so parallel worktree builds do not race.

`scripts/run-with-venv-python.sh` prefers a checkout's own `.venv` over the
main one, so `make test`, the pre-push hooks and every `scripts/*.py` run
against the worktree's build without `PYTHONPATH` juggling; the
`--worktree-pythonpath` shadow is skipped for a tree that owns a venv. A
worktree WITHOUT its own venv keeps the pre-#2526 behaviour (shared venv +
PYTHONPATH shadow + main-tree `.so` symlink).

## Updating

If you change these templates and want the changes to propagate to other
djust developers, commit them — `.pipeline-templates/` is tracked.
`.customer-names` stays local.

For truly-generic improvements (not specific to djust's
framework-plus-downstream-apps topology), contribute upstream to
`johnrtipton/pipeline-skills` instead.
