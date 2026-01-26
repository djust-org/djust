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

   Or manually update:
   - `pyproject.toml`: `version = "0.2.0a1"`
   - `Cargo.toml`: `version = "0.2.0-alpha.1"` (workspace.package)

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

2. **GitHub Actions will automatically**:
   - Build wheels for all platforms
   - Create a GitHub Release
   - Publish to PyPI

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
# Bump version (updates pyproject.toml and Cargo.toml)
make version VERSION=0.2.0a1

# Create and push a release tag
make release VERSION=0.2.0a1

# Check current version
make version-check
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

### PyPI Upload Failures

- Ensure trusted publishing is configured at pypi.org
- Check that the version doesn't already exist on PyPI
- Pre-release versions (a, b, rc) won't be installed by default

### Version Mismatch

If versions get out of sync:
```bash
# Check all version files
grep -r "version" pyproject.toml Cargo.toml | grep -E "[0-9]+\.[0-9]+"
```
