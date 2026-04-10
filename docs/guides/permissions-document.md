# Declarative Permissions Document

The `djust_audit --permissions permissions.yaml` flag validates every LiveView
in your project against a committed, human-readable YAML document describing
the expected auth configuration for each view. CI fails on any deviation,
making the permission model an **auditable artifact** — security reviewers
can sign it off once, and no new view can merge without an explicit declaration.

This closes a structural gap in `djust_audit`: the tool can tell "no auth" from
"some auth", but it cannot tell that `login_required=True` should have been
`permission_required=['claims.view_supervisor_dashboard']`. The document IS
the ground truth for what each view SHOULD require.

## Why

In a recent penetration test of a djust-based application, every view in the
`claims/`, `settlement/`, `investigation/`, and `documents/` namespaces had
`login_required=True` set. `djust_audit` reported them all as "protected."
In reality, the lowest-privilege authenticated user could access the examiner
dashboard, the supervisor dashboard, and every claim detail page in the
database by ID walk.

The gap was structural: the framework's check could see that auth existed, but
it had no way to know that "some auth" should have been "supervisor role only"
for specific views. That's application knowledge the framework doesn't have —
unless the app tells the framework explicitly.

## Creating `permissions.yaml`

Bootstrap a starter document from your existing code:

```bash
python manage.py djust_audit --dump-permissions > permissions.yaml
```

Review the generated file carefully. Views with existing `permission_required`
attributes will be pre-populated; views with `login_required` only will get a
`TODO` note asking you to confirm the intended role model; views with no auth
will be marked `public: true` with a TODO asking you to confirm that's
intentional.

Then commit the file.

## Schema

```yaml
# permissions.yaml
# Expected permissions per LiveView. djust_audit --permissions permissions.yaml
# fails if the actual code deviates from this document.

version: 1
strict: true  # require every LiveView to be declared

views:
  # ---------- intentionally public ----------
  apps.public.views.Home:
    public: true
    notes: "Landing page — confirmed public after security review"

  apps.intake.views.IntakeWizardView:
    public: true
    notes: "Public intake wizard for anonymous users (RFP FR-I-a)"

  # ---------- authenticated, role-scoped ----------
  apps.claims.views.ExaminerDashboardView:
    login_required: true
    permissions: ["claims.view_examiner_dashboard"]
    roles: ["Examiner", "Supervisor", "Director"]

  apps.claims.views.ClaimDetailView:
    login_required: true
    permissions: ["claims.view_claim"]
    roles: ["Claimant", "Examiner", "Supervisor"]
    # Document the intended object-level scoping. djust can't verify role
    # membership or object ownership at static-analysis time, but listing
    # the fields makes the intent reviewable.
    object_scoping:
      fields: ["claimant.email", "assigned_examiner"]
```

### Per-view keys

| Key | Type | Description |
|-----|------|-------------|
| `public` | `bool` | View is intentionally accessible without auth. Mutually exclusive with `login_required` / `permissions`. |
| `login_required` | `bool` | Matches `cls.login_required = True` on the view class. |
| `permissions` | `[str]` | Matches `cls.permission_required` (list or single string, normalized to list). |
| `roles` | `[str]` | Documentation only — djust cannot verify Django group membership via static analysis. |
| `object_scoping.fields` | `[str]` | Documents which object-level fields the view checks for ownership. Currently informational; may be promoted to AST-verified in a later release. |
| `notes` | `str` | Free-form documentation shown in error/diff output. |

### Top-level keys

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `version` | `int` | (required) | Document schema version. Only `1` is supported. |
| `strict` | `bool` | `true` | When `true`, views found in code but not declared in the document fail the audit. |
| `views` | `mapping` | (required) | Mapping of dotted view path → per-view declaration. |

## Findings

Each deviation between the document and the code produces a finding with a
stable error code:

| Code | Severity | Meaning |
|------|----------|---------|
| `djust.P001` | error | View declared in `permissions.yaml` but not found in code (stale entry). |
| `djust.P002` | error | View found in code but not declared in `permissions.yaml` (strict mode only). |
| `djust.P003` | error | Document says `public: true` but code has auth configured. |
| `djust.P004` | error | Document says auth required but code has none (`login_required=False`, no `permission_required`). |
| `djust.P005` | error | Permission list in `permissions.yaml` does not match `cls.permission_required`. |
| `djust.P006` | warning | `object_scoping.fields` not referenced in the view (best-effort, currently informational). |
| `djust.P007` | info | `roles` declaration — djust cannot verify at static-analysis time, treated as documentation. |

## CLI

```bash
# Human-friendly terminal output
python manage.py djust_audit --permissions permissions.yaml

# Machine-readable, fails CI on any error/warning
python manage.py djust_audit --permissions permissions.yaml --strict --json > audit.json

# Bootstrap a starter permissions.yaml from current code
python manage.py djust_audit --dump-permissions > permissions.yaml
```

## CI integration

```yaml
# .github/workflows/ci.yml
- name: Audit permissions document
  run: |
    python manage.py djust_audit \
      --permissions permissions.yaml \
      --strict \
      --json > /tmp/audit.json
```

Any new view added without a corresponding `permissions.yaml` entry fails the
build. A developer can't "forget RBAC" — they can't even merge the code.

## What this catches

1. **"Forgot the decorator"** — new view added without updating `permissions.yaml`
   fails CI.
2. **"Decorator drift"** — `permission_required = ['claims.view_claim']` changed
   to something else fails CI unless the document is also updated.
3. **Stale declarations** — a view removed from the codebase but still in the
   document is flagged.
4. **Mismatched public/auth intent** — a view declared public but actually has
   auth, or vice versa.
5. **Auditable artifact** — a security reviewer can read `permissions.yaml` in
   five minutes and sign off on the entire app's permission model.

## What this does NOT catch

- **Role membership** — `roles: ["Examiner"]` is documentation. djust cannot
  verify Django group membership at static-analysis time; that still requires
  runtime enforcement in the view.
- **Object-level access control** — `object_scoping.fields` is currently
  informational. `Claim.objects.get(pk=...)` in `get_object()` without a
  `filter(assigned_examiner=request.user)` clause is not automatically detected.
  Use AST-based anti-pattern detection (see [#660](https://github.com/djust-org/djust/issues/660))
  for that class of check.
- **Business-logic authorization** — whether a user should be allowed to
  perform a specific action on a specific record remains application code.
  The document describes framework-level auth config, not app-level policies.

## References

- [#657](https://github.com/djust-org/djust/issues/657) — original feature issue
- OWASP A01:2021 Broken Access Control
- CWE-285 Improper Authorization
- NIST SP 800-53 AC-3 Access Enforcement
