- **Publishing to PyPI is now gated on the pre-release security audit, and
  every scanner in it blocks.** The audit used to run alongside `release.yml`
  on the tag push, so a release published even when the audit failed — and
  only Bandit and ESLint could fail it anyway. It is now a reusable workflow
  that `release.yml` and `publish.yml` call as a job their GitHub Release and
  PyPI jobs `need`. pip-audit (over every package pinned in `uv.lock`),
  cargo-audit (vulnerabilities and unsound advisories), `npm audit`
  (high/critical), clippy's deny lints and CodeQL (open high/critical alerts on
  the tag) now fail the audit, each against a reviewed allowlist
  (`.github/security/pip-audit-ignore.txt`, `.cargo/audit.toml`, the CodeQL
  config); Safety is removed in favour of pip-audit. The locked dependencies
  are refreshed to clear what the gates found: anyio 4.14.2, autobahn 26.7.1
  (Python 3.11+), click 8.3.3 and Django 5.2.17 in `uv.lock`, anyhow 1.0.104
  in `Cargo.lock`, and brace-expansion 5.0.12 (dev-only) in
  `package-lock.json`. See `RELEASING.md`.
