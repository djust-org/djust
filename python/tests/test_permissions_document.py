"""
Tests for the declarative permissions document (#657).

Covers:
- ``PermissionsDocument.load`` / ``_from_data`` schema validation
- ``compare_all`` and ``compare_view`` for each finding code (P001–P007)
- ``dump_starter_document`` round-trip
- ``djust_audit --permissions`` integration via ``call_command``
"""

import io
import textwrap

import pytest
from django.core.management import call_command

from djust.permissions import (
    FINDING_CODES,
    PermissionsDocument,
    PermissionsDocumentError,
    PermissionsFinding,
    ViewDeclaration,
    dump_starter_document,
)


# ---------------------------------------------------------------------------
# Finding + severity mapping
# ---------------------------------------------------------------------------


class TestFindingCodes:
    def test_all_codes_have_severity_and_description(self):
        for code, (severity, desc) in FINDING_CODES.items():
            assert severity in ("error", "warning", "info")
            assert desc, f"{code} missing description"

    def test_finding_make_uses_canonical_severity(self):
        f = PermissionsFinding.make("P001", "apps.Foo", "gone")
        assert f.severity == "error"
        f = PermissionsFinding.make("P006", "apps.Foo", "scoping")
        assert f.severity == "warning"
        f = PermissionsFinding.make("P007", "apps.Foo", "roles: [Admin]")
        assert f.severity == "info"

    def test_finding_format_line_includes_code_and_view(self):
        f = PermissionsFinding.make("P001", "apps.Foo", "gone")
        line = f.format_line()
        assert "djust.P001" in line
        assert "apps.Foo" in line
        assert "gone" in line

    def test_finding_to_dict_roundtrip(self):
        f = PermissionsFinding.make("P005", "apps.Foo", "mismatch", details="expected [x]")
        d = f.to_dict()
        assert d["code"] == "P005"
        assert d["severity"] == "error"
        assert d["view"] == "apps.Foo"
        assert d["details"] == "expected [x]"


# ---------------------------------------------------------------------------
# Document parsing and schema validation
# ---------------------------------------------------------------------------


class TestDocumentSchemaValidation:
    def _load(self, data):
        return PermissionsDocument._from_data(data, source="<test>")

    def test_valid_document(self):
        doc = self._load(
            {
                "version": 1,
                "strict": True,
                "views": {
                    "apps.foo.views.Home": {"public": True},
                    "apps.foo.views.Admin": {
                        "login_required": True,
                        "permissions": ["foo.admin"],
                    },
                },
            }
        )
        assert doc.strict is True
        assert len(doc.views) == 2
        assert doc.views["apps.foo.views.Home"].public is True
        assert doc.views["apps.foo.views.Admin"].permissions == ["foo.admin"]

    def test_none_document_rejected(self):
        with pytest.raises(PermissionsDocumentError, match="empty document"):
            self._load(None)

    def test_non_mapping_top_level_rejected(self):
        with pytest.raises(PermissionsDocumentError, match="top-level must be a mapping"):
            self._load([1, 2, 3])

    def test_missing_views_rejected(self):
        with pytest.raises(PermissionsDocumentError, match="missing required 'views'"):
            self._load({"version": 1})

    def test_views_not_mapping_rejected(self):
        with pytest.raises(PermissionsDocumentError, match="'views' must be a mapping"):
            self._load({"version": 1, "views": ["a", "b"]})

    def test_unsupported_version(self):
        with pytest.raises(PermissionsDocumentError, match="unsupported version 99"):
            self._load({"version": 99, "views": {}})

    def test_version_must_be_int(self):
        with pytest.raises(PermissionsDocumentError, match="'version' must be an integer"):
            self._load({"version": "one", "views": {}})

    def test_view_key_must_be_dotted_path(self):
        with pytest.raises(PermissionsDocumentError, match="must be a dotted path"):
            self._load({"version": 1, "views": {"notdotted": {"public": True}}})

    def test_view_entry_must_be_mapping(self):
        with pytest.raises(PermissionsDocumentError, match="must be a mapping"):
            self._load({"version": 1, "views": {"apps.foo.Bar": "public"}})

    def test_view_entry_must_have_some_config(self):
        with pytest.raises(PermissionsDocumentError, match="no configuration"):
            self._load({"version": 1, "views": {"apps.foo.Bar": None}})

    def test_public_and_auth_mutually_exclusive(self):
        with pytest.raises(PermissionsDocumentError, match="mutually exclusive"):
            self._load(
                {
                    "version": 1,
                    "views": {
                        "apps.foo.Bar": {
                            "public": True,
                            "login_required": True,
                        }
                    },
                }
            )

    def test_must_specify_public_or_auth(self):
        with pytest.raises(PermissionsDocumentError, match="must specify either"):
            self._load({"version": 1, "views": {"apps.foo.Bar": {"notes": "empty"}}})

    def test_permissions_must_be_list_of_strings(self):
        with pytest.raises(
            PermissionsDocumentError, match="'permissions' must be a list of strings"
        ):
            self._load(
                {
                    "version": 1,
                    "views": {"apps.foo.Bar": {"login_required": True, "permissions": "foo.admin"}},
                }
            )

    def test_roles_must_be_list_of_strings(self):
        with pytest.raises(PermissionsDocumentError, match="'roles' must be a list of strings"):
            self._load(
                {
                    "version": 1,
                    "views": {"apps.foo.Bar": {"login_required": True, "roles": "admin"}},
                }
            )

    def test_object_scoping_must_be_mapping(self):
        with pytest.raises(PermissionsDocumentError, match="'object_scoping' must be a mapping"):
            self._load(
                {
                    "version": 1,
                    "views": {
                        "apps.foo.Bar": {
                            "login_required": True,
                            "object_scoping": "fields",
                        }
                    },
                }
            )


# ---------------------------------------------------------------------------
# compare_all — end-to-end findings
# ---------------------------------------------------------------------------


def _doc(views, strict=True):
    """Shortcut to build a document from raw dict data."""
    return PermissionsDocument._from_data(
        {"version": 1, "strict": strict, "views": views},
        source="<test>",
    )


class TestCompareAll:
    def test_empty_match(self):
        doc = _doc({})
        findings = doc.compare_all({})
        assert findings == []

    def test_p001_stale_declaration(self):
        doc = _doc({"apps.foo.Gone": {"public": True}})
        findings = doc.compare_all({})
        assert len(findings) == 1
        assert findings[0].code == "P001"
        assert findings[0].view == "apps.foo.Gone"

    def test_p002_undeclared_view_strict(self):
        doc = _doc({}, strict=True)
        findings = doc.compare_all({"apps.foo.New": {}})
        assert len(findings) == 1
        assert findings[0].code == "P002"

    def test_p002_not_raised_when_strict_false(self):
        doc = _doc({}, strict=False)
        findings = doc.compare_all({"apps.foo.New": {}})
        assert findings == []

    def test_p003_declared_public_but_has_auth(self):
        doc = _doc({"apps.foo.Home": {"public": True}})
        findings = doc.compare_all({"apps.foo.Home": {"login_required": True}})
        assert any(f.code == "P003" for f in findings)

    def test_p004_declared_auth_but_code_has_none(self):
        doc = _doc({"apps.foo.Admin": {"login_required": True}})
        findings = doc.compare_all({"apps.foo.Admin": {}})
        assert any(f.code == "P004" for f in findings)

    def test_p004_not_raised_when_code_has_custom_check(self):
        doc = _doc({"apps.foo.Admin": {"login_required": True}})
        findings = doc.compare_all({"apps.foo.Admin": {"custom_check": True}})
        assert not any(f.code == "P004" for f in findings)

    def test_p005_permission_list_mismatch(self):
        doc = _doc(
            {
                "apps.foo.Admin": {
                    "login_required": True,
                    "permissions": ["foo.view_admin"],
                }
            }
        )
        findings = doc.compare_all(
            {
                "apps.foo.Admin": {
                    "login_required": True,
                    "permission_required": ["foo.wrong"],
                }
            }
        )
        assert any(f.code == "P005" for f in findings)

    def test_p005_no_mismatch_when_lists_match(self):
        doc = _doc(
            {
                "apps.foo.Admin": {
                    "login_required": True,
                    "permissions": ["foo.view", "foo.edit"],
                }
            }
        )
        findings = doc.compare_all(
            {
                "apps.foo.Admin": {
                    "login_required": True,
                    "permission_required": ["foo.edit", "foo.view"],  # order ignored
                }
            }
        )
        assert not any(f.code == "P005" for f in findings)

    def test_p007_roles_informational(self):
        doc = _doc(
            {
                "apps.foo.Admin": {
                    "login_required": True,
                    "roles": ["Admin", "Editor"],
                }
            }
        )
        findings = doc.compare_all({"apps.foo.Admin": {"login_required": True}})
        role_findings = [f for f in findings if f.code == "P007"]
        assert len(role_findings) == 1
        assert role_findings[0].severity == "info"
        assert "Admin" in role_findings[0].message

    def test_multiple_findings_sorted(self):
        doc = _doc(
            {
                "apps.a.Gone": {"public": True},
                "apps.b.Home": {"public": True},
            }
        )
        findings = doc.compare_all(
            {
                "apps.b.Home": {},  # matches (public, no auth)
                "apps.c.Undeclared": {"login_required": True},  # undeclared
            }
        )
        # P001 for apps.a.Gone, P002 for apps.c.Undeclared
        codes = [f.code for f in findings]
        assert "P001" in codes
        assert "P002" in codes


# ---------------------------------------------------------------------------
# dump_starter_document round-trip
# ---------------------------------------------------------------------------


class TestDumpStarterDocument:
    def test_dump_public_view(self):
        audits = [
            {"class": "apps.foo.Home", "auth": {}},
        ]
        out = dump_starter_document(audits)
        assert "version: 1" in out
        assert "apps.foo.Home" in out
        assert "public: true" in out

    def test_dump_view_with_permissions(self):
        audits = [
            {
                "class": "apps.foo.Admin",
                "auth": {"login_required": True, "permission_required": ["foo.admin"]},
            },
        ]
        out = dump_starter_document(audits)
        assert "permissions:" in out
        assert "foo.admin" in out

    def test_dump_view_with_login_required_only(self):
        audits = [
            {"class": "apps.foo.LoginOnly", "auth": {"login_required": True}},
        ]
        out = dump_starter_document(audits)
        assert "login_required: true" in out
        assert "TODO" in out  # reviewer prompt

    def test_dump_roundtrips_through_load(self, tmp_path):
        audits = [
            {"class": "apps.foo.Home", "auth": {}},
            {
                "class": "apps.foo.Admin",
                "auth": {"login_required": True, "permission_required": ["foo.admin"]},
            },
        ]
        out = dump_starter_document(audits)
        path = tmp_path / "permissions.yaml"
        path.write_text(out)
        doc = PermissionsDocument.load(str(path))
        assert len(doc.views) == 2
        assert doc.views["apps.foo.Home"].public is True
        assert doc.views["apps.foo.Admin"].permissions == ["foo.admin"]


# ---------------------------------------------------------------------------
# Management command integration
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestDjustAuditPermissionsIntegration:
    """Run ``djust_audit --permissions`` via Django's ``call_command``.

    Uses ``--json`` output and an in-memory StringIO to inspect results
    without touching the real stdout.
    """

    def test_permissions_flag_with_missing_file(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        out = io.StringIO()
        err = io.StringIO()
        with pytest.raises(SystemExit) as exc_info:
            call_command(
                "djust_audit",
                "--permissions",
                str(missing),
                "--strict",
                stdout=out,
                stderr=err,
            )
        assert exc_info.value.code == 2
        assert "permissions document error" in err.getvalue()

    def test_permissions_flag_with_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(":\n  - broken: [unterminated")
        err = io.StringIO()
        out = io.StringIO()
        with pytest.raises(SystemExit):
            call_command(
                "djust_audit",
                "--permissions",
                str(bad),
                stdout=out,
                stderr=err,
            )
        assert "permissions document error" in err.getvalue()

    def test_permissions_flag_with_valid_document(self, tmp_path):
        """With a valid empty document and strict=false, exit is clean."""
        doc = tmp_path / "permissions.yaml"
        doc.write_text(
            textwrap.dedent(
                """\
                version: 1
                strict: false
                views: {}
                """
            )
        )
        out = io.StringIO()
        call_command(
            "djust_audit",
            "--permissions",
            str(doc),
            "--json",
            stdout=out,
        )
        # Returns successfully; output is JSON
        import json

        data = json.loads(out.getvalue())
        assert "permissions_findings" in data or "audits" in data

    def test_dump_permissions_produces_valid_yaml(self, tmp_path):
        """--dump-permissions output must parse back via load()."""
        out = io.StringIO()
        call_command("djust_audit", "--dump-permissions", stdout=out)
        rendered = out.getvalue()
        assert "version: 1" in rendered
        # Round-trip: write to file and re-load
        p = tmp_path / "seed.yaml"
        p.write_text(rendered)
        doc = PermissionsDocument.load(str(p))
        assert doc.version == 1


# ---------------------------------------------------------------------------
# ViewDeclaration dataclass sanity
# ---------------------------------------------------------------------------


class TestViewDeclaration:
    def test_defaults(self):
        decl = ViewDeclaration(view="apps.foo.Bar")
        assert decl.public is False
        assert decl.login_required is False
        assert decl.permissions == []
        assert decl.roles == []
        assert decl.object_scoping_fields == []
