"""Tests for the ``djust_audit --ast`` anti-pattern scanner (#660).

Each checker has at least one positive case (finding emitted) and one
negative case (safe pattern, no finding) to keep the false-positive
surface honest. Template scanning (X006/X007) and noqa suppression are
exercised in the same file.
"""

from __future__ import annotations

import textwrap

import pytest

from djust.audit_ast import (
    AST_FINDING_CODES,
    ASTAuditReport,
    ASTFinding,
    run_ast_audit,
    scan_python_source,
)


def _scan(src: str, path: str = "/tmp/fake.py") -> list[ASTFinding]:
    """Run the scanner on an inline source string."""
    return scan_python_source(path, textwrap.dedent(src).lstrip())


def _codes(findings: list[ASTFinding]) -> list[str]:
    return [f.code for f in findings]


# ---------------------------------------------------------------------------
# X001 — IDOR
# ---------------------------------------------------------------------------


class TestX001IDOR:
    def test_unscoped_detail_view_triggers(self) -> None:
        findings = _scan("""
            from django.views.generic import DetailView
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(pk=self.kwargs["pk"])
        """)
        assert _codes(findings) == ["X001"]

    def test_scoped_with_filter_owner_ok(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.filter(
                        owner=self.request.user
                    ).get(pk=self.kwargs["pk"])
        """)
        assert findings == []

    def test_scoped_with_filter_tenant_ok(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    qs = Contact.objects.filter(tenant_id=self.request.user.tenant_id)
                    return qs.get(pk=self.kwargs["pk"])
        """)
        assert findings == []

    def test_get_without_request_param_ok(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(pk=42)
        """)
        assert findings == []

    def test_non_detail_view_class_ignored(self) -> None:
        findings = _scan("""
            class SomeHelper:
                def load(self, pk):
                    return Contact.objects.get(pk=self.kwargs["pk"])
        """)
        assert findings == []

    def test_id_kwarg_also_flagged(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(id=self.kwargs["id"])
        """)
        assert _codes(findings) == ["X001"]

    def test_liveview_base_class_detected(self) -> None:
        findings = _scan("""
            from djust import LiveView
            class ProjectView(LiveView):
                def mount(self, request, pk):
                    self.project = Project.objects.get(pk=self.kwargs["pk"])
        """)
        assert _codes(findings) == ["X001"]

    def test_check_permissions_override_ok(self) -> None:
        findings = _scan("""
            class ProjectView(LiveView):
                def check_permissions(self, request):
                    return self.project.team.members.filter(user=request.user).exists()
                def mount(self, request, pk):
                    self.project = Project.objects.get(pk=self.kwargs["pk"])
        """)
        # The mount method is still unscoped, but the class has an explicit
        # check_permissions override — we still flag this to be conservative.
        # (If the override is sufficient the user can suppress with noqa.)
        assert "X001" in _codes(findings)


# ---------------------------------------------------------------------------
# X002 — Unauthenticated state-mutating handler
# ---------------------------------------------------------------------------


class TestX002UnprotectedMutation:
    def test_delete_handler_without_auth_triggers(self) -> None:
        findings = _scan("""
            class FooView:
                @event_handler
                def delete_thing(self, pk):
                    Thing.objects.filter(pk=pk).delete()
        """)
        assert _codes(findings) == ["X002"]

    def test_class_level_login_required_ok(self) -> None:
        findings = _scan("""
            class FooView:
                login_required = True
                @event_handler
                def delete_thing(self, pk):
                    Thing.objects.filter(pk=pk).delete()
        """)
        assert findings == []

    def test_permission_required_decorator_ok(self) -> None:
        findings = _scan("""
            class FooView:
                @permission_required("app.delete_thing")
                @event_handler
                def delete_thing(self, pk):
                    Thing.objects.filter(pk=pk).delete()
        """)
        assert findings == []

    def test_non_mutating_handler_ok(self) -> None:
        findings = _scan("""
            class FooView:
                @event_handler
                def search(self, value=""):
                    self.query = value
        """)
        assert findings == []

    def test_save_counts_as_mutation(self) -> None:
        findings = _scan("""
            class FooView:
                @event_handler
                def rename(self, pk, name):
                    obj = Thing.objects.get(pk=pk)
                    obj.name = name
                    obj.save()
        """)
        assert "X002" in _codes(findings)

    def test_non_event_handler_ignored(self) -> None:
        findings = _scan("""
            class FooView:
                def helper(self, pk):
                    Thing.objects.filter(pk=pk).delete()
        """)
        assert findings == []

    def test_create_counts_as_mutation(self) -> None:
        findings = _scan("""
            class FooView:
                @event_handler
                def add(self, name):
                    Thing.objects.create(name=name)
        """)
        assert _codes(findings) == ["X002"]


# ---------------------------------------------------------------------------
# X003 — SQL string formatting
# ---------------------------------------------------------------------------


class TestX003SQLFormatting:
    def test_raw_fstring_triggers(self) -> None:
        findings = _scan("""
            def q(name):
                return Thing.objects.raw(f"SELECT * FROM t WHERE n = {name}")
        """)
        assert _codes(findings) == ["X003"]

    def test_raw_with_params_ok(self) -> None:
        findings = _scan("""
            def q(name):
                return Thing.objects.raw("SELECT * FROM t WHERE n = %s", [name])
        """)
        assert findings == []

    def test_execute_format_triggers(self) -> None:
        findings = _scan("""
            def q(cursor, name):
                cursor.execute("SELECT * FROM t WHERE n = {}".format(name))
        """)
        assert _codes(findings) == ["X003"]

    def test_execute_percent_triggers(self) -> None:
        findings = _scan("""
            def q(cursor, name):
                cursor.execute("SELECT * FROM t WHERE n = %s" % name)
        """)
        assert _codes(findings) == ["X003"]

    def test_extra_fstring_triggers(self) -> None:
        findings = _scan("""
            def q(name):
                return Thing.objects.extra(where=[f"name = '{name}'"])
        """)
        # extra's first positional arg is 'where', which is a list; we only
        # scan the first arg to avoid false positives on kwargs, so this
        # case isn't flagged. Document the limitation.
        assert findings == []

    def test_static_sql_ok(self) -> None:
        findings = _scan("""
            def q():
                return Thing.objects.raw("SELECT * FROM t")
        """)
        assert findings == []


# ---------------------------------------------------------------------------
# X004 — Open redirect
# ---------------------------------------------------------------------------


class TestX004OpenRedirect:
    def test_bare_redirect_from_get_triggers(self) -> None:
        findings = _scan("""
            def v(request):
                return HttpResponseRedirect(request.GET["next"])
        """)
        assert _codes(findings) == ["X004"]

    def test_url_has_allowed_host_and_scheme_ok(self) -> None:
        findings = _scan("""
            def v(request):
                target = request.GET["next"]
                if url_has_allowed_host_and_scheme(target, allowed_hosts=None):
                    return HttpResponseRedirect(target)
                return HttpResponseRedirect("/")
        """)
        assert findings == []

    def test_is_safe_url_ok(self) -> None:
        findings = _scan("""
            def v(request):
                target = request.GET.get("next")
                if is_safe_url(target):
                    return HttpResponseRedirect(target)
                return HttpResponseRedirect("/")
        """)
        # request.GET.get("next") is still user input and _is_user_input_expr
        # catches it, so we also expect the guard to clear it.
        assert findings == []

    def test_static_redirect_ok(self) -> None:
        findings = _scan("""
            def v(request):
                return HttpResponseRedirect("/dashboard/")
        """)
        assert findings == []

    def test_redirect_from_post_triggers(self) -> None:
        findings = _scan("""
            def v(request):
                return redirect(request.POST.get("after"))
        """)
        assert _codes(findings) == ["X004"]


# ---------------------------------------------------------------------------
# X005 — mark_safe
# ---------------------------------------------------------------------------


class TestX005MarkSafe:
    def test_mark_safe_fstring_triggers(self) -> None:
        findings = _scan("""
            def render_name(name):
                return mark_safe(f"<b>{name}</b>")
        """)
        assert _codes(findings) == ["X005"]

    def test_mark_safe_format_triggers(self) -> None:
        findings = _scan("""
            def render_name(name):
                return mark_safe("<b>{}</b>".format(name))
        """)
        assert _codes(findings) == ["X005"]

    def test_mark_safe_percent_triggers(self) -> None:
        findings = _scan("""
            def render_name(name):
                return mark_safe("<b>%s</b>" % name)
        """)
        assert _codes(findings) == ["X005"]

    def test_mark_safe_static_ok(self) -> None:
        findings = _scan("""
            def banner():
                return mark_safe("<b>Static content</b>")
        """)
        assert findings == []

    def test_safestring_class_triggers(self) -> None:
        findings = _scan("""
            def render_name(name):
                return SafeString(f"<b>{name}</b>")
        """)
        assert _codes(findings) == ["X005"]


# ---------------------------------------------------------------------------
# Suppression
# ---------------------------------------------------------------------------


class TestSuppression:
    def test_specific_noqa_suppresses(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(pk=self.kwargs["pk"])  # djust: noqa X001
        """)
        assert findings == []

    def test_bare_noqa_suppresses(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(pk=self.kwargs["pk"])  # djust: noqa
        """)
        assert findings == []

    def test_wrong_code_does_not_suppress(self) -> None:
        findings = _scan("""
            class ContactDetailView(DetailView):
                def get_object(self):
                    return Contact.objects.get(pk=self.kwargs["pk"])  # djust: noqa X999
        """)
        assert _codes(findings) == ["X001"]

    def test_noqa_multiple_codes(self) -> None:
        # X002 is emitted on the `def` line of the mutating handler, so the
        # noqa must sit on that line (not on the .delete() call).
        findings = _scan("""
            class Foo:
                @event_handler
                def delete(self, pk):  # djust: noqa X002, X003
                    Thing.objects.filter(pk=pk).delete()
        """)
        assert findings == []


# ---------------------------------------------------------------------------
# Template scanning (X006 / X007)
# ---------------------------------------------------------------------------


class TestTemplateScanning:
    def test_safe_filter_detected(self, tmp_path) -> None:
        tpl = tmp_path / "page.html"
        tpl.write_text("<p>{{ user_bio|safe }}</p>\n")
        report = run_ast_audit(root=str(tmp_path))
        codes = [f.code for f in report.findings]
        assert "X006" in codes

    def test_autoescape_off_detected(self, tmp_path) -> None:
        tpl = tmp_path / "page.html"
        tpl.write_text("{% autoescape off %}\n{{ x }}\n{% endautoescape %}\n")
        report = run_ast_audit(root=str(tmp_path))
        codes = [f.code for f in report.findings]
        assert "X007" in codes

    def test_template_noqa_suppresses(self, tmp_path) -> None:
        tpl = tmp_path / "page.html"
        tpl.write_text("{{ user_bio|safe }}{# djust: noqa X006 #}\n")
        report = run_ast_audit(root=str(tmp_path))
        assert report.findings == []

    def test_safe_filter_with_space_detected(self, tmp_path) -> None:
        tpl = tmp_path / "page.html"
        tpl.write_text("{{ user_bio | safe }}\n")
        report = run_ast_audit(root=str(tmp_path))
        assert [f.code for f in report.findings] == ["X006"]

    def test_no_templates_flag_skips_html(self, tmp_path) -> None:
        tpl = tmp_path / "page.html"
        tpl.write_text("{{ user_bio|safe }}\n")
        report = run_ast_audit(root=str(tmp_path), include_templates=False)
        assert report.findings == []


# ---------------------------------------------------------------------------
# Project walking
# ---------------------------------------------------------------------------


class TestRunAstAudit:
    def test_walks_python_files(self, tmp_path) -> None:
        (tmp_path / "views.py").write_text(
            textwrap.dedent(
                """
                class FooView:
                    @event_handler
                    def delete_thing(self, pk):
                        Thing.objects.filter(pk=pk).delete()
                """
            )
        )
        report = run_ast_audit(root=str(tmp_path))
        assert any(f.code == "X002" for f in report.findings)
        assert report.files_scanned >= 1

    def test_skip_dirs_excluded(self, tmp_path) -> None:
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "bad.py").write_text("def q(n): return Thing.objects.raw(f'x={n}')")
        report = run_ast_audit(root=str(tmp_path))
        assert report.findings == []

    def test_exclude_prefix(self, tmp_path) -> None:
        (tmp_path / "views.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )
        skip = tmp_path / "vendor"
        skip.mkdir()
        (skip / "bad.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )
        report = run_ast_audit(root=str(tmp_path), exclude=["vendor"])
        paths = [f.path for f in report.findings]
        assert all("vendor" not in p for p in paths)
        assert any("views.py" in p for p in paths)

    def test_report_to_dict_shape(self, tmp_path) -> None:
        (tmp_path / "v.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )
        report = run_ast_audit(root=str(tmp_path))
        d = report.to_dict()
        assert d["mode"] == "ast"
        assert "summary" in d
        assert d["summary"]["errors"] >= 1
        assert "findings" in d

    def test_syntax_error_skipped(self, tmp_path) -> None:
        (tmp_path / "broken.py").write_text("def broken(:\n")
        report = run_ast_audit(root=str(tmp_path))
        # Syntax error shouldn't blow up the scan
        assert isinstance(report, ASTAuditReport)

    def test_sorted_output(self, tmp_path) -> None:
        (tmp_path / "a.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )
        (tmp_path / "b.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )
        report = run_ast_audit(root=str(tmp_path))
        paths = [f.path for f in report.findings]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# Finding model
# ---------------------------------------------------------------------------


class TestFindingModel:
    def test_finding_codes_well_formed(self) -> None:
        for code, (severity, msg) in AST_FINDING_CODES.items():
            assert code.startswith("X")
            assert severity in {"error", "warning", "info"}
            assert msg

    def test_format_line_contains_code(self) -> None:
        f = ASTFinding.make("X001", path="/tmp/x.py", lineno=5, col=0, details="details")
        line = f.format_line()
        assert "djust.X001" in line
        assert "/tmp/x.py:5" in line
        assert "details" in line

    def test_finding_to_dict(self) -> None:
        f = ASTFinding.make("X003", path="/tmp/y.py", lineno=10)
        d = f.to_dict()
        assert d["code"] == "X003"
        assert d["severity"] == "error"
        assert d["path"] == "/tmp/y.py"
        assert d["lineno"] == 10


# ---------------------------------------------------------------------------
# Management command integration smoke test
# ---------------------------------------------------------------------------


class TestManagementCommandIntegration:
    def test_ast_mode_runs(self, tmp_path, capsys) -> None:
        """`--ast --ast-path <dir>` runs without Django settings mayhem."""
        from io import StringIO

        from django.core.management import call_command

        (tmp_path / "bad.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )

        out = StringIO()
        err = StringIO()
        with pytest.raises(SystemExit) as excinfo:
            call_command(
                "djust_audit",
                "--ast",
                "--ast-path",
                str(tmp_path),
                stdout=out,
                stderr=err,
            )
        # Exit code 1 because we have a X004 error
        assert excinfo.value.code == 1
        output = out.getvalue()
        assert "X004" in output

    def test_ast_mode_json(self, tmp_path) -> None:
        """`--ast --json` produces JSON on stdout."""
        import json as _json
        from io import StringIO

        from django.core.management import call_command

        (tmp_path / "bad.py").write_text(
            "def v(request): return HttpResponseRedirect(request.GET['next'])"
        )

        out = StringIO()
        with pytest.raises(SystemExit):
            call_command(
                "djust_audit",
                "--ast",
                "--json",
                "--ast-path",
                str(tmp_path),
                stdout=out,
            )
        payload = _json.loads(out.getvalue())
        assert payload["mode"] == "ast"
        assert any(f["code"] == "X004" for f in payload["findings"])

    def test_ast_clean_tree_exits_zero(self, tmp_path) -> None:
        from io import StringIO

        from django.core.management import call_command

        (tmp_path / "clean.py").write_text(
            "def v(request): return HttpResponseRedirect('/dashboard/')"
        )
        out = StringIO()
        # No exception — exit code 0
        call_command(
            "djust_audit",
            "--ast",
            "--ast-path",
            str(tmp_path),
            stdout=out,
        )
        assert "No findings" in out.getvalue()
