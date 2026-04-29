# `djust_audit` — Security Audit Command

`djust_audit` is djust's all-in-one security and configuration audit tool.
It runs as a Django management command and operates in five modes:

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
5. **`--ast` mode** — walk your Python source and templates looking for
   five security anti-patterns (IDOR, unauthenticated mutation, SQL
   string formatting, open redirects, unsafe `mark_safe`/`|safe`). Emits
   stable [X0xx codes](error-codes.md#ast-anti-pattern-scanner-findings-x0xx).

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

# AST anti-pattern scan
python manage.py djust_audit --ast

# AST scan, specific root, exclude vendored paths, JSON for CI
python manage.py djust_audit --ast \
    --ast-path src/ \
    --ast-exclude vendor third_party legacy/ \
    --strict --json > ast-report.json

# AST scan — Python only (skip templates)
python manage.py djust_audit --ast --ast-no-templates
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
| `--ast` | switch | Run the AST anti-pattern scanner (#660). |
| `--ast-path <path>` | str | Root directory for `--ast` (default: current working directory). |
| `--ast-exclude <path> [...]` | list | Path prefixes (relative to `--ast-path`) to skip during `--ast` scanning. |
| `--ast-no-templates` | switch | Skip `.html` template files in `--ast` mode (Python only). |

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
appear to deny. A downstream consumer pentest caught a critical "CSP header
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

### `--ast`: static anti-pattern scanner

The AST scanner walks your project's Python source and Django templates
looking for five specific security anti-patterns. Every pattern it
looks for was either a live vulnerability or a near-miss in the
2026-04-10 pentest — so the checks are intentionally narrow rather
than trying to catch every possible bug. False positives are worse
than missed findings for a linter that runs on every push.

**What it checks** (full reference: [X0xx codes](error-codes.md#ast-anti-pattern-scanner-findings-x0xx)):

- **X001 — IDOR** — `Model.objects.get(pk=...)` inside a DetailView /
  LiveView without a sibling `.filter(owner=request.user)` or
  `check_permissions` override.
- **X002 — Unauthenticated state-mutating handler** — an
  `@event_handler` that writes to the DB (`create`, `update`,
  `delete`, `save`) without any permission check (class-level
  `login_required`, `permission_required`, `@permission_required`,
  etc.).
- **X003 — SQL string formatting** — `.raw()` / `.extra()` /
  `cursor.execute()` with an f-string, a `.format()` call, or a
  `"..." % ...` binary-op.
- **X004 — Open redirect** — `HttpResponseRedirect(...)` /
  `redirect(...)` fed directly from `request.GET` / `request.POST`
  without an `url_has_allowed_host_and_scheme` /
  `is_safe_url` guard in the same function.
- **X005 — Unsafe `mark_safe`** — `mark_safe(...)` / `SafeString(...)`
  wrapping an f-string, a `.format()` call, or a `%` binary-op.
- **X006 / X007** — a light regex scan of `.html` files that flags
  `{{ var|safe }}` and `{% autoescape off %}` blocks for review.

**Suppression**: add `# djust: noqa X001` (or the relevant code) on
the offending line. Bare `# djust: noqa` suppresses every `djust.X`
finding on the line. Templates use `{# djust: noqa X006 #}`.

**Dependencies**: zero new runtime deps. The scanner uses the
stdlib `ast` module for Python and a handful of regular
expressions for templates.

**Limitations**:

- The scanner cannot follow values across function calls. If the user
  input is sanitised in a helper two files away, that's invisible
  here — annotate with `# djust: noqa` and move on.
- P005 only catches `mark_safe`/`SafeString` on the immediate argument.
  Multi-step construction (build a string into a variable then pass
  it) slips through.
- P003 only scans the first positional argument; `.extra(where=[...])`
  with kwargs is not inspected.
- Templates are scanned by regex, not by Django's template compiler —
  inline tags and comments that look like `{{ var|safe }}` inside
  strings will be matched.

These are intentional scope limits; the goal is a 2-second CI check
that catches the common mistakes, not a full data-flow analyser.

#### Security of the tool itself

`fetch()` validates the URL scheme to `http`/`https` only and rejects
`file://`, `ftp://`, etc. with a `ValueError`. A security auditing tool
shouldn't exfiltrate local files or follow hostile redirects to non-HTTP
schemes.

## CI integration

A typical CI job runs all five modes:

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

      # Anti-pattern scan — X0xx
      - name: AST anti-pattern scan
        run: |
          python manage.py djust_audit \
            --ast --ast-path src/ \
            --ast-exclude vendor legacy/ \
            --strict --json > ast-report.json

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
