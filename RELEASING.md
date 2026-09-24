# Release Process

This document describes how to create releases for djust.

## Version Numbering

djust follows [Semantic Versioning](https://semver.org/) (SemVer) with [PEP 440](https://peps.python.org/pep-0440/) compatible pre-release suffixes.

### Version Format

```
MAJOR.MINOR.PATCH[{a|b|rc}N]
```

- **MAJOR**: Breaking changes
- **MINOR**: New features (backwards compatible)
- **PATCH**: Bug fixes (backwards compatible)
- **a**: Alpha (early testing)
- **b**: Beta (feature complete, testing)
- **rc**: Release candidate (final testing)

### Examples

```
0.1.8      # Stable patch release
0.2.0a1    # Alpha 1 (early testing of 0.2.0 features)
0.2.0a2    # Alpha 2 (more changes based on feedback)
0.2.0b1    # Beta 1 (feature complete, testing)
0.2.0rc1   # Release candidate 1 (final testing)
0.2.0      # Stable release
```

### Installation

```bash
pip install djust           # Latest stable (e.g., 0.1.8)
pip install djust --pre     # Latest including pre-releases (e.g., 0.2.0b1)
pip install djust==0.2.0a1  # Specific pre-release
```

## Release Workflow

### 1. Prepare the Release

1. **Create a release branch** (for major/minor releases):
   ```bash
   git checkout main
   git pull
   git checkout -b release/0.2.0
   ```

2. **Update version numbers**:
   ```bash
   make version VERSION=0.2.0a1
   ```

   `make version` also refreshes the lockfile self-entries — it runs
   `uv lock` (updates the editable `djust` entry in `uv.lock`) and
   `cargo update --workspace` (updates the workspace-crate entries in
   `Cargo.lock`). **Commit the resulting `uv.lock` and `Cargo.lock`
   changes alongside the manifest bump** — a release tag cut from a tree
   with a stale lockfile self-entry will fail the `make release`
   verification gate (see #1498).

   Or manually update:
   - `pyproject.toml`: `version = "0.2.0a1"`
   - `Cargo.toml`: `version = "0.2.0-alpha.1"` (workspace.package)
   - then run `uv lock` and `cargo update --workspace` to refresh the
     lockfile self-entries, and verify with `make check-lockfile-versions`.

3. **Update CHANGELOG.md**:
   ```markdown
   ## [0.2.0-alpha.1] - 2026-01-28

   ### Added
   - New feature X

   ### Changed
   - **BREAKING**: Changed Y to Z
   ```

4. **Commit and push**:
   ```bash
   git add -A
   git commit -m "chore: bump version to 0.2.0a1"
   git push origin release/0.2.0
   ```

5. **Create PR and merge to main**

### 2. Create the Release

1. **Tag the release**:
   ```bash
   git checkout main
   git pull
   git tag -a v0.2.0a1 -m "Release v0.2.0a1"
   git push origin v0.2.0a1
   ```

2. **GitHub Actions will automatically** (`.github/workflows/release.yml`):
   - Run the pre-release security audit (see below) alongside the build
   - Build wheels for all platforms
   - Create a GitHub Release — **only if the audit passed**
   - Publish to PyPI — **only if the audit passed**

### Publishing is gated on the security audit

`.github/workflows/pre-release-security-audit.yml` is a reusable workflow.
`release.yml` (tag push) and `publish.yml` (release published / manual
dispatch) call it as a job that their GitHub Release and PyPI jobs `need`, so
a failing scanner stops the release before anything is published. It has no
tag trigger of its own; you can still dispatch it by hand on any ref
(Actions → Pre-Release Security Audit → Run workflow) to check a branch before
tagging — do that before cutting a release.

Every scanner blocks, and each has one reviewed allowlist:

| Scanner | Fails on | Allowlist |
|---------|----------|-----------|
| Bandit | any high-severity finding | the `-s`/`-x` skip set in the workflow (same as the pre-commit hook) |
| pip-audit | any known vulnerability in a package pinned in `uv.lock` (all extras, every Python/platform fork; djust itself excluded), or an allowlist entry past its review-by date | `.github/security/pip-audit-ignore.txt` — `ID  # reason; review-by YYYY-MM-DD` |
| cargo-audit | any vulnerability, and any `unsound` advisory (unmaintained/yanked are warnings) | `.cargo/audit.toml` `[advisories] ignore`, each with a reason comment |
| npm audit | any high or critical advisory in `package-lock.json` | none — fix with `npm audit fix` or a `package.json` `overrides` entry |
| clippy | `clippy::correctness` / `clippy::suspicious` lints | fix, or `#[allow(...)]` with a justification |
| ESLint | errors (warnings are reported) | the ESLint config |
| CodeQL | open high/critical alerts on the analysed ref (the tag) | `.github/codeql/codeql-config.yml` exclusions + alerts dismissed in the Security tab with a reason |

Run the dependency gates locally before tagging:

```bash
make security-audit-deps                  # pip-audit gate + cargo audit + npm audit
scripts/codeql-alert-gate.sh refs/heads/main
```

If the audit fails on a tag, fix the finding (or add a reviewed allowlist
entry) on `main`, bump to a new version and tag again — the failed tag's
release never reached PyPI. A tracking issue is created only on a manual
dispatch with `create_issue` checked.

### 3. Post-Release

1. **Verify the release**:
   ```bash
   pip install djust==0.2.0a1
   python -c "import djust; print(djust.__version__)"
   ```

2. **Announce** (for stable releases):
   - Update documentation site
   - Post on social media
   - Notify Discord/community

## Pre-Release Workflow

For major changes (like breaking changes), use pre-releases to gather feedback:

```
main ─── v0.1.8 (stable)
  │
  ├─── release/0.2.0
  │      │
  │      ├─── v0.2.0a1 (alpha - early testing)
  │      │      └─── gather feedback, fix issues
  │      │
  │      ├─── v0.2.0a2 (alpha - more fixes)
  │      │      └─── gather feedback
  │      │
  │      ├─── v0.2.0b1 (beta - feature complete)
  │      │      └─── wider testing
  │      │
  │      ├─── v0.2.0rc1 (release candidate)
  │      │      └─── final testing
  │      │
  │      └─── v0.2.0 (stable)
  │
  └─── merge back to main
```

## Makefile Commands

```bash
# Bump version (updates pyproject.toml, Cargo.toml, __init__.py files,
# and refreshes the uv.lock + Cargo.lock self-entries)
make version VERSION=0.2.0a1

# Create and push a release tag (verifies lockfile self-entries are in sync)
make release VERSION=0.2.0a1

# Check current version (includes the uv.lock + Cargo.lock self-entries)
make version-check

# Verify lockfile self-entries match the manifests (#1498)
make check-lockfile-versions
```

## Hotfix Releases

For urgent fixes to stable releases:

1. Branch from the release tag:
   ```bash
   git checkout -b hotfix/0.1.9 v0.1.8
   ```

2. Apply fix, update version to `0.1.9`

3. Tag and release:
   ```bash
   git tag -a v0.1.9 -m "Hotfix: description"
   git push origin v0.1.9
   ```

4. Merge fix back to main and any active release branches

## Troubleshooting

### Build Failures

If the GitHub Actions build fails:
1. Check the workflow logs
2. Ensure all Cargo.toml versions match
3. Run `make build` locally to verify

### Security Audit Failures

The release run's **Pre-release security audit / Audit Summary** job lists
which scanner failed; each scanner's report is in the run's
`*-security-reports` artifacts and `security-audit-report`. Upgrade the
dependency (targeted: `uv lock --upgrade-package <name>`,
`cargo update -p <crate>`, `npm audit fix`), or — when no fix exists — add an
allowlist entry with the reason and a review-by date (see the table above).

### PyPI Upload Failures

- Ensure trusted publishing is configured at pypi.org
- Check that the version doesn't already exist on PyPI
- Pre-release versions (a, b, rc) won't be installed by default

### Version Mismatch

If versions get out of sync:
```bash
# Check all version files (manifests + lockfile self-entries)
make version-check

# Verify the lockfile self-entries specifically (#1498)
make check-lockfile-versions

# Or grep the manifests + lockfiles directly
grep -r "version" pyproject.toml Cargo.toml uv.lock Cargo.lock | grep -E "[0-9]+\.[0-9]+"
```

If `make check-lockfile-versions` reports drift, re-run
`make version VERSION=<current>` (it refreshes `uv.lock` + `Cargo.lock`)
and commit the lockfile changes.
