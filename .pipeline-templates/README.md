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

Why: djust sits alongside multiple private downstream apps (NYC Claims,
Rezme, Ridgeview MHP, JBM, etc.). Patterns extracted upstream into the
public framework repo must not ship with private-project identifiers
in commit metadata or file contents. PR #836 nearly shipped "NYC Claims"
in its commit subject + PR body — caught at Stage 3 during pipeline-ship,
but reviewer vigilance is not a durable gate. This mechanical grep is.

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
