"""
Declarative permissions document for ``djust_audit`` (#657).

A permissions document is a YAML file (conventionally ``permissions.yaml`` at
the project root) that declares the expected auth configuration for every
LiveView in a project. ``manage.py djust_audit --permissions permissions.yaml``
validates the actual code against the document and fails CI on any deviation.

This closes a structural gap in ``djust_audit``: the tool can tell "no auth"
from "some auth", but it cannot tell that ``login_required=True`` should have
been ``permission_required=['claims.view_supervisor_dashboard']``. Without an
explicit declaration, the tool has no ground truth to compare against. The
permissions document IS the ground truth.

Schema
======

.. code-block:: yaml

    version: 1
    strict: true  # require every view to be declared

    views:
      apps.intake.views.PublicIntakeView:
        public: true
        notes: "Public intake wizard for anonymous users"

      apps.claims.views.ExaminerDashboardView:
        login_required: true
        permissions: ["claims.view_examiner_dashboard"]
        roles: ["Examiner", "Supervisor"]

      apps.claims.views.ClaimDetailView:
        login_required: true
        permissions: ["claims.view_claim"]
        object_scoping:
          fields: ["claimant.email", "assigned_examiner"]

Per-view keys:

* ``public: bool`` — view is intentionally accessible without auth. Mutually
  exclusive with ``login_required`` / ``permissions``.
* ``login_required: bool`` — matches ``cls.login_required = True``.
* ``permissions: [str, ...]`` — matches ``cls.permission_required``.
* ``roles: [str, ...]`` — documentation only. djust cannot verify Django
  group membership via static analysis; this key exists so reviewers can
  see the intended role model in one place.
* ``object_scoping.fields: [str, ...]`` — documents which object-level fields
  the view checks for ownership. Stretch goal (warning-only): AST-walk
  ``get_object`` / ``mount`` and verify the fields are referenced.
* ``notes: str`` — free-form documentation shown in diff output.

Findings
========

Each comparison between the document and actual code produces zero or more
``PermissionsFinding`` objects with a severity, a stable error code, and a
human-readable message. Codes:

=====  ========  ==============================================================
Code   Severity  Meaning
=====  ========  ==============================================================
P001   ERROR     View in document but not in code (stale declaration)
P002   ERROR     View in code but not declared in document (strict mode only)
P003   ERROR     Document says ``public: true`` but code has auth configured
P004   ERROR     Document says auth required but code has none
P005   ERROR     Permission list mismatch between document and code
P006   WARN      ``object_scoping`` field not referenced in ``get_object``
                 (stretch — currently unimplemented, stored as informational)
P007   INFO      ``roles`` declaration (djust cannot verify at static-analysis
                 time — treated as documentation)
=====  ========  ==============================================================

See issue #657 (NYC Claims pentest, FINDING-10/11 motivation).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


FINDING_CODES = {
    "P001": ("error", "View declared in permissions.yaml but not found in code"),
    "P002": ("error", "View found in code but not declared in permissions.yaml"),
    "P003": ("error", "View declared public in permissions.yaml but has auth in code"),
    "P004": ("error", "View declared auth-required in permissions.yaml but has none in code"),
    "P005": ("error", "Permission list in permissions.yaml does not match code"),
    "P006": ("warning", "object_scoping field not referenced in view (best-effort)"),
    "P007": ("info", "roles declaration (cannot be verified by static analysis)"),
}


@dataclass
class PermissionsFinding:
    """A single deviation between the permissions document and the code."""

    code: str
    view: str  # dotted path, e.g. "apps.claims.views.ExaminerDashboardView"
    message: str
    severity: str = "error"  # "error" / "warning" / "info"
    details: Optional[str] = None

    @classmethod
    def make(cls, code: str, view: str, message: str, details: Optional[str] = None):
        """Build a finding using the canonical severity from FINDING_CODES."""
        severity = FINDING_CODES.get(code, ("error", ""))[0]
        return cls(code=code, view=view, message=message, severity=severity, details=details)

    def format_line(self) -> str:
        """Format as a single line for terminal output."""
        prefix = {"error": "ERROR", "warning": "WARN", "info": "INFO"}.get(
            self.severity, self.severity.upper()
        )
        line = f"{prefix} [djust.{self.code}] {self.view}: {self.message}"
        if self.details:
            line += f" ({self.details})"
        return line

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for JSON output."""
        return {
            "code": self.code,
            "severity": self.severity,
            "view": self.view,
            "message": self.message,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------


class PermissionsDocumentError(Exception):
    """Raised for schema/parse errors in a permissions document."""


@dataclass
class ViewDeclaration:
    """A single view entry from a permissions document."""

    view: str  # dotted path
    public: bool = False
    login_required: bool = False
    permissions: List[str] = field(default_factory=list)
    roles: List[str] = field(default_factory=list)
    object_scoping_fields: List[str] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class PermissionsDocument:
    """Parsed, validated representation of a permissions.yaml document."""

    version: int = 1
    strict: bool = True
    views: Dict[str, ViewDeclaration] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str) -> "PermissionsDocument":
        """Parse a YAML permissions document from disk.

        Args:
            path: Filesystem path to the YAML file.

        Raises:
            PermissionsDocumentError: on parse or schema error (message
                describes the problem; caller should surface it).
            FileNotFoundError: if the path doesn't exist.
        """
        try:
            import yaml  # PyYAML
        except ImportError as exc:
            raise PermissionsDocumentError(
                "--permissions requires PyYAML. Install it with: pip install pyyaml"
            ) from exc

        with open(path, encoding="utf-8") as fp:
            try:
                data = yaml.safe_load(fp)
            except yaml.YAMLError as exc:
                raise PermissionsDocumentError(f"{path}: YAML parse error — {exc}") from exc

        return cls._from_data(data, source=path)

    @classmethod
    def _from_data(cls, data: Any, source: str = "<input>") -> "PermissionsDocument":
        """Validate a pre-parsed dict and build a document."""
        if data is None:
            raise PermissionsDocumentError(f"{source}: empty document")
        if not isinstance(data, dict):
            raise PermissionsDocumentError(
                f"{source}: top-level must be a mapping, got {type(data).__name__}"
            )

        version = data.get("version", 1)
        if not isinstance(version, int):
            raise PermissionsDocumentError(
                f"{source}: 'version' must be an integer, got {type(version).__name__}"
            )
        if version != 1:
            raise PermissionsDocumentError(
                f"{source}: unsupported version {version} (only version 1 is supported)"
            )

        strict = bool(data.get("strict", True))

        views_raw = data.get("views")
        if views_raw is None:
            raise PermissionsDocumentError(f"{source}: missing required 'views' mapping")
        if not isinstance(views_raw, dict):
            raise PermissionsDocumentError(
                f"{source}: 'views' must be a mapping, got {type(views_raw).__name__}"
            )

        views: Dict[str, ViewDeclaration] = {}
        for dotted, entry in views_raw.items():
            if not isinstance(dotted, str) or "." not in dotted:
                raise PermissionsDocumentError(
                    f"{source}: view key '{dotted}' must be a dotted path like 'myapp.views.MyView'"
                )
            views[dotted] = _parse_view_entry(dotted, entry, source=source)

        return cls(version=version, strict=strict, views=views)

    # ---- comparator -------------------------------------------------------

    def compare_all(self, actual: Dict[str, Dict[str, Any]]) -> List[PermissionsFinding]:
        """Compare the document against a mapping of actual view auth info.

        Args:
            actual: Mapping of dotted view name → auth-info dict as produced
                by ``_extract_auth_info`` in the audit command. Example::

                    {
                        "apps.claims.views.ExaminerDashboard": {
                            "login_required": True,
                            "permission_required": ["claims.view_examiner"],
                        },
                        "apps.public.views.Home": {},
                    }

        Returns:
            List of ``PermissionsFinding`` objects covering every detected
            deviation. Empty list means the code matches the document.
        """
        findings: List[PermissionsFinding] = []
        declared = set(self.views.keys())
        found = set(actual.keys())

        # P001 — stale declarations
        for stale in sorted(declared - found):
            findings.append(
                PermissionsFinding.make(
                    "P001",
                    stale,
                    "declared in permissions.yaml but not found in code; "
                    "remove the entry or restore the view",
                )
            )

        # P002 — undeclared views (strict only)
        if self.strict:
            for undeclared in sorted(found - declared):
                findings.append(
                    PermissionsFinding.make(
                        "P002",
                        undeclared,
                        "found in code but not declared in permissions.yaml; "
                        "add it as 'public: true' or with auth config",
                    )
                )

        # Per-view comparisons
        for dotted in sorted(declared & found):
            findings.extend(self.compare_view(dotted, actual[dotted]))

        return findings

    def compare_view(self, dotted: str, actual_auth: Dict[str, Any]) -> List[PermissionsFinding]:
        """Compare a single view's declaration against its actual auth info.

        Args:
            dotted: The dotted path of the view.
            actual_auth: The auth-info dict from ``_extract_auth_info``.

        Returns:
            List of findings, possibly empty.
        """
        findings: List[PermissionsFinding] = []
        decl = self.views.get(dotted)
        if decl is None:
            # Caller should only invoke compare_view for declared views.
            return findings

        has_auth = bool(
            actual_auth.get("login_required")
            or actual_auth.get("permission_required")
            or actual_auth.get("custom_check")
            or actual_auth.get("dispatch_mixin")
        )

        # P003 — declared public but has auth
        if decl.public and has_auth:
            findings.append(
                PermissionsFinding.make(
                    "P003",
                    dotted,
                    "declared 'public: true' in permissions.yaml but code has auth",
                    details=_summarize_auth(actual_auth),
                )
            )

        # P004 — declared auth-required but code has none
        if not decl.public and (decl.login_required or decl.permissions) and not has_auth:
            findings.append(
                PermissionsFinding.make(
                    "P004",
                    dotted,
                    "declared auth-required in permissions.yaml but code has none",
                    details="expected: "
                    + (", ".join(decl.permissions) if decl.permissions else "login_required"),
                )
            )

        # P005 — permission list mismatch
        if decl.permissions:
            actual_perms = set(actual_auth.get("permission_required") or [])
            declared_perms = set(decl.permissions)
            if actual_perms != declared_perms:
                findings.append(
                    PermissionsFinding.make(
                        "P005",
                        dotted,
                        "permission list in permissions.yaml does not match code",
                        details=(
                            f"expected {sorted(declared_perms)}, "
                            f"found {sorted(actual_perms) or '[] (none)'}"
                        ),
                    )
                )

        # P007 — roles declaration (informational)
        if decl.roles:
            findings.append(
                PermissionsFinding.make(
                    "P007",
                    dotted,
                    f"roles declared: {', '.join(decl.roles)}",
                )
            )

        return findings


def _parse_view_entry(dotted: str, entry: Any, source: str) -> ViewDeclaration:
    """Validate a single view entry and return a ``ViewDeclaration``."""
    if entry is None or entry == "":
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' has no configuration; use 'public: true' "
            f"or specify 'login_required'/'permissions'"
        )
    if not isinstance(entry, dict):
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' must be a mapping, got {type(entry).__name__}"
        )

    public = bool(entry.get("public", False))
    login_required = bool(entry.get("login_required", False))
    permissions = entry.get("permissions", []) or []
    roles = entry.get("roles", []) or []
    notes = entry.get("notes")

    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' — 'permissions' must be a list of strings"
        )
    if not isinstance(roles, list) or not all(isinstance(r, str) for r in roles):
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' — 'roles' must be a list of strings"
        )

    object_scoping_fields: List[str] = []
    scoping = entry.get("object_scoping")
    if scoping is not None:
        if not isinstance(scoping, dict):
            raise PermissionsDocumentError(
                f"{source}: view '{dotted}' — 'object_scoping' must be a mapping"
            )
        fields = scoping.get("fields", []) or []
        if not isinstance(fields, list) or not all(isinstance(f, str) for f in fields):
            raise PermissionsDocumentError(
                f"{source}: view '{dotted}' — 'object_scoping.fields' must be a list of strings"
            )
        object_scoping_fields = fields

    # Mutual-exclusion sanity check
    if public and (login_required or permissions):
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' — 'public: true' is mutually exclusive with "
            f"'login_required' / 'permissions'"
        )
    if not public and not login_required and not permissions:
        raise PermissionsDocumentError(
            f"{source}: view '{dotted}' — must specify either 'public: true' or "
            f"'login_required: true' / 'permissions'"
        )

    return ViewDeclaration(
        view=dotted,
        public=public,
        login_required=login_required,
        permissions=list(permissions),
        roles=list(roles),
        object_scoping_fields=object_scoping_fields,
        notes=notes,
    )


def _summarize_auth(auth: Dict[str, Any]) -> str:
    """Render an auth-info dict as a short description for error messages."""
    parts = []
    if auth.get("login_required"):
        parts.append("login_required")
    if auth.get("permission_required"):
        parts.append(f"permissions={auth['permission_required']}")
    if auth.get("custom_check"):
        parts.append("custom check_permissions()")
    if auth.get("dispatch_mixin"):
        parts.append("auth mixin in MRO")
    return ", ".join(parts) if parts else "no auth"


# ---------------------------------------------------------------------------
# Dump helper (--dump-permissions)
# ---------------------------------------------------------------------------


def dump_starter_document(audits: List[Dict[str, Any]]) -> str:
    """Render a starter permissions.yaml from a list of audit dicts.

    Each audit is expected to have the shape produced by
    ``_audit_class`` in ``djust_audit``: a ``class`` dotted path and an
    ``auth`` dict.

    The output is a valid parseable YAML string that round-trips through
    ``PermissionsDocument.load``. Views with declared permissions are
    seeded with them; views with `login_required` only get that; views
    with no auth info are seeded as ``public: true`` with a TODO note
    instructing the reviewer to confirm.
    """
    try:
        import yaml  # PyYAML
    except ImportError as exc:
        raise PermissionsDocumentError(
            "--dump-permissions requires PyYAML. Install it with: pip install pyyaml"
        ) from exc

    views_out: Dict[str, Any] = {}
    for audit in sorted(audits, key=lambda a: a.get("class", "")):
        dotted = audit.get("class")
        if not dotted:
            continue
        auth = audit.get("auth") or {}
        entry: Dict[str, Any] = {}
        if auth.get("permission_required"):
            entry["login_required"] = True
            entry["permissions"] = list(auth["permission_required"])
        elif auth.get("login_required"):
            entry["login_required"] = True
            entry["notes"] = "TODO: confirm roles/permissions — login_required alone"
        elif auth.get("custom_check") or auth.get("dispatch_mixin"):
            entry["login_required"] = True
            entry["notes"] = "TODO: review custom auth check and declare explicit permissions"
        else:
            entry["public"] = True
            entry["notes"] = "TODO: confirm this view is intentionally public"
        views_out[dotted] = entry

    data = {
        "version": 1,
        "strict": True,
        "views": views_out,
    }
    header = (
        "# djust permissions document (generated by `djust_audit --dump-permissions`)\n"
        "#\n"
        "# Review each entry and replace any `TODO` notes with the intended\n"
        "# permission configuration. Then commit this file and run:\n"
        "#\n"
        "#     python manage.py djust_audit --permissions permissions.yaml --strict\n"
        "#\n"
        "# in CI to catch any future deviation.\n"
        "\n"
    )
    return header + yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
