# `djust_audit` — Security Audit Command

`djust_audit` is djust's all-in-one security and configuration audit tool.
It runs as a Django management command and operates in four modes:

1. **Default mode** — introspect every LiveView and LiveComponent in the
   project and report what they expose, what decorators protect them, and
   what their auth configuration is.
2. **`--permissions` mode** — validate every LiveView against a committed
   `permissions.yaml` declarative permissions document and report
   deviations ([P0xx codes](error-codes.md#permissions-document-findings-p0xx)).
3. **`--dump-permissions` mode** — bootstrap a starter `permissions.yaml`
   from the discovered views.
4. **`--live <url>` mode** — fetch a running deployment and verify
   security headers, cookies, information-disclosure paths, and WebSocket
   CSWSH defense ([L0xx codes](error-codes.md#live-runtime-probe-findings-l0xx)).

The configuration-level static checks (A0xx codes) also run via Django's
normal check pipeline (`manage.py check --tag djust`) — `djust_audit`
doesn't need a separate flag for those.

## Quick reference

```bash
# Default: report every LiveView/LiveComponent
python manage.py djust_audit

# JSON output (CI-friendly)
python manage.py djust_audit --json

# Filter to one Django app
python manage.py djust_audit --app myapp

# Include template variable sub-paths (requires Rust extension)
python manage.py djust_audit --verbose

# Validate against permissions.yaml
python manage.py djust_audit --permissions permissions.yaml

# Strict: fail CI on any deviation
python manage.py djust_audit --permissions permissions.yaml --strict --json > audit.json

# Bootstrap a starter permissions.yaml from current code
python manage.py djust_audit --dump-permissions > permissions.yaml

# Runtime probe
python manage.py djust_audit --live https://staging.example.com

# Runtime probe — CI mode, multiple paths, staging auth
python manage.py djust_audit --live https://staging.example.com \
    --paths /auth/login/ /api/ \
    --header 'Authorization: Basic dXNlcjpwYXNz' \
    --strict --json > runtime.json

# Runtime probe — environments behind a WAF
python manage.py djust_audit --live https://app.example.com \
    --skip-path-probes

# Runtime probe — environments without WebSocket support
python manage.py djust_audit --live https://app.example.com \
    --no-websocket-probe
```

## CLI flags

| Flag | Type | Description |
|------|------|-------------|
| `--json` | switch | Output results as JSON (for CI parsing). |
| `--app <label>` | str | Filter to a single Django app. |
| `--verbose` | switch | Include template variable sub-paths (requires Rust extension). |
| `--permissions <path>` | str | Validate against a YAML permissions document. |
| `--strict` | switch | Fail with non-zero exit on any finding (including warnings). |
| `--dump-permissions` | switch | Print a starter `permissions.yaml` and exit. |
| `--live <url>` | str | Switch to runtime probe mode. Fetches the URL. |
| `--paths <path> [...]` | list | Extra paths/URLs to inspect in `--live` mode. |
| `--header 'Name: Value'` | repeatable | Extra HTTP header for `--live` requests (e.g. staging basic auth). |
| `--no-websocket-probe` | switch | Skip the CSWSH handshake check. |
| `--skip-path-probes` | switch | Skip `/.git/`, `/.env`, `/__debug__/` probes (for WAF-protected environments). |

## Modes explained

### Default: LiveView introspection

Running `djust_audit` with no mode flags walks every `LiveView` and
`LiveComponent` subclass in the project and builds a report covering:

- Class name and template
- Authentication configuration (`login_required`, `permission_required`,
  custom `check_permissions()`, dispatch-based auth mixins)
- Active mixins (`PresenceMixin`, `TenantMixin`, `FormMixin`, etc.)
- Exposed public attributes (anything that doesn't start with `_`)
- Event handlers and their decorators (`@event_handler`, `@debounce`,
  `@throttle`, `@rate_limit`, `@cache`, `@optimistic`, etc.)
- Config flags (`tick_interval`, `temporary_assigns`, `use_actors`)

This is the "what does my app actually expose" report. Run it when you're
onboarding a new codebase, preparing for a security review, or diffing
before/after a refactor.

### `--permissions`: RBAC drift detection

The permissions document ([full guide](permissions-document.md)) is a
YAML file that declares the expected auth configuration for every
LiveView. `djust_audit --permissions permissions.yaml` validates the
code against the document and reports every deviation.

Example `permissions.yaml`:

```yaml
version: 1
strict: true

views:
  apps.public.views.HomeView:
    public: true

  apps.claims.views.ExaminerDashboardView:
    login_required: true
    permissions: ["claims.view_examiner_dashboard"]
    roles: ["Examiner", "Supervisor", "Director"]

  apps.claims.views.ClaimDetailView:
    login_required: true
    permissions: ["claims.view_claim"]
    object_scoping:
      fields: ["claimant.email", "assigned_examiner"]
```

See the [P0xx finding codes](error-codes.md#permissions-document-findings-p0xx)
for the full rule set.

### `--dump-permissions`: bootstrap a starter document

Running `djust_audit --dump-permissions > permissions.yaml` writes a
starter YAML file based on the current code. Each view gets:

- `login_required: true` + `permissions: [...]` if the code already
  has explicit permissions
- `login_required: true` + a `TODO` note if the code only sets
  `login_required=True`
- `public: true` + a `TODO` note if the code has no auth at all
  (asking the reviewer to confirm)

Review every `TODO` note before committing the file.

### `--live`: runtime security probe

Runtime checks catch the class of issues that static analysis cannot
see: middleware correctly configured in `settings.py` but the response
is stripped by nginx/ingress/proxy, or a firewall allows what settings
appear to deny. The NYC Claims pentest caught a critical "CSP header
missing in production" case this way (django-csp was configured but an
nginx ingress stripped the header).

The probe performs four classes of check:

1. **Security headers** — HSTS, CSP, X-Frame-Options, X-Content-Type-Options,
   Referrer-Policy, Permissions-Policy, COOP, CORP. See [L001–L015](error-codes.md#live-runtime-probe-findings-l0xx).
2. **Cookie attributes** — HttpOnly, Secure, SameSite on session and
   CSRF cookies. See [L020–L024](error-codes.md#l020-session-cookie-missing-httponly).
3. **Information-disclosure paths** — `/.git/config`, `/.env`,
   `/__debug__/`, `/robots.txt`, `/.well-known/security.txt`. See
   [L040–L044](error-codes.md#l040-gitconfig-publicly-accessible).
4. **WebSocket CSWSH probe** — attempts `wss://host/ws/live/` with
   `Origin: https://evil.example`. See [L060](error-codes.md#l060-websocket-accepted-cross-origin-handshake-cswsh).

#### Dependencies

`--live` uses stdlib `urllib` for HTTP — **no new runtime dependencies**.
The WebSocket probe lazy-imports the `websockets` package; if it's not
installed, the probe is skipped with an INFO-level finding (`L062`)
instead of failing the audit. Install with `pip install websockets` to
enable it.

#### Security of the tool itself

`fetch()` validates the URL scheme to `http`/`https` only and rejects
`file://`, `ftp://`, etc. with a `ValueError`. A security auditing tool
shouldn't exfiltrate local files or follow hostile redirects to non-HTTP
schemes.

## CI integration

A typical CI job runs all four modes:

```yaml
# .github/workflows/security.yml
name: Security audit
on: [push, pull_request]

jobs:
  static:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e .

      # Static checks — C0xx, V0xx, S0xx, T0xx, Q0xx, A0xx
      - name: Run system checks
        run: python manage.py check --tag djust --fail-level WARNING

      # RBAC drift — P0xx
      - name: Validate permissions document
        run: |
          python manage.py djust_audit \
            --permissions permissions.yaml \
            --strict --json > permissions-report.json

  runtime:
    needs: static
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'   # Only run on main, against staging
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e . websockets

      # Runtime probe — L0xx
      - name: Probe staging for security headers
        run: |
          python manage.py djust_audit \
            --live https://staging.example.com \
            --strict --json > runtime-report.json
```

## Exit codes

| Exit code | Meaning |
|-----------|---------|
| 0 | Success — no findings in strict mode, or no errors in non-strict mode. |
| 1 | At least one finding at error (or warning, in `--strict` mode). |
| 2 | Invalid input — missing permissions file, bad YAML, bad `--header` format. |

## See also

- [Error Code Reference](error-codes.md) — every check ID with fixes
- [Declarative Permissions Document](permissions-document.md) — permissions.yaml schema
- [Security Guide](security.md) — LiveView-level security best practices
- [Best Practices](BEST_PRACTICES.md) — architectural recommendations
