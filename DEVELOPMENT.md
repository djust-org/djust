# Development Guidelines

Working guidelines for all agents and contributors to the djust project. These apply to the core framework repo; see project-specific docs for djust.org and djustlive.

---

## Branch Strategy

- **No direct commits to `main`.** All work goes through feature branches.
- Branch naming:
  - `feat/<description>` — new functionality
  - `fix/<description>` — bug fixes
  - `refactor/<description>` — code restructuring, no behavior change
  - `docs/<description>` — documentation only
  - `test/<description>` — test additions or changes
  - `chore/<description>` — maintenance (deps, tooling)
- Branches are short-lived. Merge within 1–2 heartbeats when possible.
- Delete the branch after merge.

---

## Commit Conventions

- Use [Conventional Commits](https://www.conventionalcommits.org/) prefixes: `feat:`, `fix:`, `refactor:`, `docs:`, `test:`, `chore:`, `security:`
- One logical change per commit. Keep commits atomic.
- Include the Paperclip co-author line on every commit:
  ```
  Co-Authored-By: Paperclip <noreply@paperclip.ing>
  ```
- Reference issue identifiers in commit messages when applicable (e.g., `fix: correct textarea newline stripping (DJU-42)`).

---

## PR Workflow

1. **Create a branch** from `main` (see Branch Strategy above).
2. **Make changes**, commit locally.
3. **Run quality gates** (see below) before pushing.
4. **Push the branch** and open a PR against `main`.
5. **PR description** must include: purpose, what changed, and how it was tested.
6. **For djust core**: QA Engineer reviews before merge. Self-review is not sufficient.
7. **CI must be green** before requesting review.
8. **Do not merge your own PR** unless explicitly authorized.

See [`docs/PULL_REQUEST_CHECKLIST.md`](docs/PULL_REQUEST_CHECKLIST.md) for the full review checklist.

---

## Quality Gates (Pre-Merge)

All of these must pass before a PR is considered ready for review:

| Gate | How | Tool |
|------|-----|------|
| Python linting | `ruff check` | pre-commit |
| Python formatting | `ruff format` | pre-commit |
| Security scan | `bandit` | pre-commit |
| Secret detection | `detect-secrets` | pre-commit |
| Rust formatting | `cargo fmt --check` | pre-commit |
| Rust linting | `cargo clippy -- -D warnings` | pre-commit |
| Trailing whitespace / YAML / TOML | file checks | pre-commit |
| Python tests | `pytest` | pre-push hook |
| Rust tests | `cargo test` | pre-push hook |
| Security audit | `cargo audit` | pre-push hook |
| JS tests | `npm test` | pre-commit |

Run all pre-commit hooks manually: `pre-commit run --all-files`

Run the full test suite: `make test`

**Never bypass hooks** with `--no-verify`. If a hook fails, fix the underlying issue.

---

## Cross-Project Testing

The djust core powers both `djust.org` and `djustlive`. Any PR that touches:
- Rendering pipeline (Rust templates, VDOM)
- WebSocket protocol / consumer lifecycle
- Template tags or filters
- Public Python API surface

...must be smoke-tested in at least one dependent project before requesting review.

```bash
# Install local djust into djust.org
cd djust.org && uv pip install -e ../djust

# Run the dev server and verify the homepage renders
make dev
```

The QA Engineer owns cross-project integration verification for critical PRs.

---

## Review Expectations

### Reviewer checklist (summary)
- Tests exist for all new behavior (Python + JS where applicable)
- No `print()` statements — use `logging.getLogger(__name__)`
- No `console.log` without `if (globalThis.djustDebug)` guard
- No `mark_safe(f'...')` with user-controlled values
- No `@csrf_exempt` without documented justification
- CHANGELOG.md updated for `feat:` and `fix:` PRs
- No placeholder/stub code shipped as production behavior

### Auto-reject triggers
- Silent exception handling: `except: pass`
- F-string formatting in logger calls (use `%s` style)
- New features without tests
- New JS files in `static/djust/src/` without matching `tests/js/` test file
- Security hot spot files changed without security-qualified reviewer approval

Full list: [`docs/PULL_REQUEST_CHECKLIST.md`](docs/PULL_REQUEST_CHECKLIST.md) — Common Rejection Reasons section.

---

## CHANGELOG

Update `CHANGELOG.md` for every `feat:` and `fix:` PR. Add under the `[Unreleased]` heading:

```markdown
## [Unreleased]

### Added
- Brief description of the new feature

### Fixed
- Brief description of the bug fix
```

Do not update CHANGELOG for `docs:`, `chore:`, `refactor:`, or `test:` PRs unless there is a user-visible behavior change.

---

## Security

- Follow [`docs/SECURITY_GUIDELINES.md`](docs/SECURITY_GUIDELINES.md) for all security-sensitive changes.
- Changes to [security hot spot files](docs/SECURITY_GUIDELINES.md#security-hot-spot-files) require a security-qualified reviewer and targeted security tests.
- Never commit secrets, tokens, or credentials. `detect-secrets` runs on every commit.

---

## Agent-Specific Notes

- Agents must follow these guidelines exactly — the same rules apply regardless of whether the author is human or AI.
- Agents should not merge their own PRs. Open the PR and assign for review.
- For blocking issues (CI failure, merge conflict, ambiguous requirements), update the Paperclip issue to `blocked` and leave a comment explaining what is needed.
