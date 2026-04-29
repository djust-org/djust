# Project-local pipeline state templates

These files override the global pipeline-skills templates
(`~/.claude/skills/pipeline-ship/` and friends) when running
`/pipeline-ship`, `/pipeline-run`, or `/pipeline-next` from this project root.

The pipeline-next skill checks for these files first; if present, they
replace the skill's default `templates/<type>-state.json`. See the global
skill's §"State file templates" for the precedence logic.

## What's customized vs. the upstream templates

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

## Updating

If you change these templates and want the changes to propagate to other
djust developers, commit them — `.pipeline-templates/` is tracked.
`.customer-names` stays local.

For truly-generic improvements (not specific to djust's
framework-plus-downstream-apps topology), contribute upstream to
`johnrtipton/pipeline-skills` instead.
