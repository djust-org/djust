"""Unit tests for the rendered-page pass of ``djust_audit --a11y`` (#3700).

No browser: the axe run is replaced by a canned report. The real-browser
check (a known contrast/landmark violation caught, a clean page passing) is
``tests/playwright/test_a11y_rendered_3700.py``.
"""

import json
import subprocess
import sys
import types
from io import StringIO

import pytest
from django.core.management import call_command
from django.urls import path

from djust import audit_a11y
from djust.audit_a11y import (
    A11yExtraMissing,
    A11yRouteError,
    PageResult,
    RenderedA11yReport,
    RenderedViolation,
    exit_code,
    is_failing_impact,
    resolve_urls,
    routes_from_permissions,
)


def _violation(impact, rule="color-contrast", url="http://x/"):
    return RenderedViolation.from_axe(
        url,
        {
            "id": rule,
            "impact": impact,
            "help": "Elements must meet minimum color contrast ratio thresholds",
            "helpUrl": "https://dequeuniversity.com/rules/axe/4.14/%s" % rule,
            "tags": ["wcag2aa", "wcag143"],
            "nodes": [{"target": ["#low"], "html": "<p id=low>", "failureSummary": "fix"}],
        },
    )


def _report(*impacts, error=""):
    page = PageResult(url="http://x/", final_url="http://x/", status=200, error=error)
    page.violations = [_violation(i, rule="rule-%d" % n) for n, i in enumerate(impacts)]
    return RenderedA11yReport(pages=[page], axe_version="4.14.0")


# ---------------------------------------------------------------------------
# Impact -> exit mapping
# ---------------------------------------------------------------------------


class TestExitMapping:
    @pytest.mark.parametrize(
        "impact,failing",
        [
            ("critical", True),
            ("serious", True),
            ("moderate", False),
            ("minor", False),
            (None, False),
        ],
    )
    def test_failing_impacts(self, impact, failing):
        assert is_failing_impact(impact) is failing

    def test_normal_mode_is_warning_only(self):
        assert exit_code(_report("critical", "serious", "minor"), strict=False) == 0

    def test_strict_fails_on_serious_or_critical(self):
        assert exit_code(_report("serious"), strict=True) == 1
        assert exit_code(_report("critical"), strict=True) == 1

    def test_strict_passes_moderate_and_minor(self):
        assert exit_code(_report("moderate", "minor"), strict=True) == 0

    def test_page_not_audited_fails_in_both_modes(self):
        assert exit_code(_report(error="HTTP 500"), strict=False) == 1
        assert exit_code(_report(error="HTTP 500"), strict=True) == 1

    def test_report_json_summary(self):
        data = _report("critical", "minor").to_dict()
        assert data["axe_version"] == "4.14.0"
        assert data["summary"]["violations"] == 2
        assert data["summary"]["failing"] == 1
        assert data["summary"]["critical"] == 1
        assert data["summary"]["minor"] == 1
        first = data["a11y_rendered_findings"][0]
        assert first["nodes"][0]["target"] == ["#low"]
        assert first["url"] == "http://x/"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_absolute_urls_kept_and_deduplicated(self):
        assert resolve_urls(["http://a/x", "http://a/x", "https://b/"], None) == [
            "http://a/x",
            "https://b/",
        ]

    def test_relative_url_joins_base(self):
        assert resolve_urls(["/about/"], "http://localhost:8000") == [
            "http://localhost:8000/about/"
        ]

    def test_relative_url_without_base_is_an_error(self):
        with pytest.raises(A11yRouteError):
            resolve_urls(["/about/"], None)

    def test_first_argument_free_route_per_declared_view(self):
        route_map = {
            "/": "app.views.Home",
            "/home/": "app.views.Home",
            "/items/:id/": "app.views.Item",
            "/other/": "app.views.Undeclared",
        }
        paths, notes = routes_from_permissions(
            ["app.views.Home", "app.views.Item", "app.views.Gone"], route_map
        )
        assert paths == ["/"]
        assert len(notes) == 2
        assert notes[0].startswith("app.views.Item: every route needs URL arguments")
        assert notes[1] == "app.views.Gone: no route in the URLconf"

    def test_urls_from_permissions_file_walks_the_urlconf(self, tmp_path):
        from djust.live_view import LiveView
        from djust.routing import _reset_route_map_cache

        class A11yHomeView(LiveView):
            template = "<div dj-root>home</div>"

        class A11yItemView(LiveView):
            template = "<div dj-root>item</div>"

        urlconf = types.ModuleType("a11y_rendered_urls_3700")
        urlconf.urlpatterns = [
            path("start/", A11yHomeView.as_view()),
            path("items/<int:pk>/", A11yItemView.as_view()),
        ]
        home = "%s.%s" % (A11yHomeView.__module__, A11yHomeView.__qualname__)
        item = "%s.%s" % (A11yItemView.__module__, A11yItemView.__qualname__)
        doc = tmp_path / "permissions.yaml"
        doc.write_text(
            "version: 1\nviews:\n  %s:\n    public: true\n  %s:\n    public: true\n" % (home, item)
        )
        _reset_route_map_cache()
        try:
            urls, notes = audit_a11y.urls_from_permissions_file(
                str(doc), "http://localhost:8000", urlconf=urlconf
            )
        finally:
            _reset_route_map_cache()
        assert urls == ["http://localhost:8000/start/"]
        assert len(notes) == 1 and "URL arguments" in notes[0]

    def test_missing_permissions_file_is_an_error(self, tmp_path):
        with pytest.raises(A11yRouteError, match="not found"):
            audit_a11y.urls_from_permissions_file(str(tmp_path / "nope.yaml"), "http://x")


# ---------------------------------------------------------------------------
# Missing extra
# ---------------------------------------------------------------------------


class TestMissingExtra:
    def test_no_playwright_names_the_extra(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        with pytest.raises(A11yExtraMissing, match=r"djust\[a11y\]"):
            audit_a11y.load_tooling()

    def test_no_axe_package_names_the_extra(self, monkeypatch):
        pytest.importorskip("playwright.sync_api")
        monkeypatch.setitem(sys.modules, audit_a11y.AXE_PACKAGE, None)
        with pytest.raises(A11yExtraMissing, match=r"axe-core is not installed.*djust\[a11y\]"):
            audit_a11y.load_tooling()

    def test_command_exits_2_with_the_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "playwright", None)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", None)
        err = StringIO()
        with pytest.raises(SystemExit) as exc:
            call_command(
                "djust_audit", "--a11y", "--url", "http://x/", stdout=StringIO(), stderr=err
            )
        assert exc.value.code == 2
        assert "pip install 'djust[a11y]'" in err.getvalue()

    def test_importing_djust_and_the_audit_does_not_import_playwright(self):
        code = (
            "import sys, djust, djust.audit_a11y, djust.management.commands.djust_audit;"
            "print('playwright' in sys.modules)"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout
        assert out.strip() == "False"


# ---------------------------------------------------------------------------
# Command options
# ---------------------------------------------------------------------------


class TestCommand:
    @pytest.fixture
    def fake_run(self, monkeypatch):
        calls = {}

        def run(urls, extra_headers=None, timeout=30.0):
            calls["urls"] = urls
            calls["headers"] = extra_headers
            return calls.get("report", _report("serious", "minor"))

        monkeypatch.setattr(audit_a11y, "run_rendered_audit", run)
        return calls

    def test_repeatable_url_and_header(self, fake_run):
        out = StringIO()
        call_command(
            "djust_audit",
            "--a11y",
            "--url",
            "http://localhost:8000/",
            "--url",
            "/about/",
            "--base-url",
            "http://localhost:8000",
            "--header",
            "Authorization: Basic abc",
            stdout=out,
        )
        assert fake_run["urls"] == ["http://localhost:8000/", "http://localhost:8000/about/"]
        assert fake_run["headers"] == {"Authorization": "Basic abc"}
        text = out.getvalue()
        assert "rendered-page report (axe-core 4.14.0)" in text
        assert "[serious] rule-0" in text
        assert "1 serious/critical violation(s) would fail --strict" in text

    def test_strict_exits_1_on_serious(self, fake_run):
        with pytest.raises(SystemExit) as exc:
            call_command(
                "djust_audit", "--a11y", "--strict", "--url", "http://x/", stdout=StringIO()
            )
        assert exc.value.code == 1

    def test_strict_passes_minor_only(self, fake_run):
        fake_run["report"] = _report("minor", "moderate")
        call_command("djust_audit", "--a11y", "--strict", "--url", "http://x/", stdout=StringIO())

    def test_json_mode(self, fake_run):
        out = StringIO()
        call_command("djust_audit", "--a11y", "--json", "--url", "http://x/", stdout=out)
        data = json.loads(out.getvalue())
        assert data["summary"]["failing"] == 1
        assert [f["id"] for f in data["a11y_rendered_findings"]] == ["rule-0", "rule-1"]
        assert data["skipped_routes"] == []

    def test_base_url_reads_permissions(self, fake_run, monkeypatch):
        monkeypatch.setattr(
            audit_a11y,
            "urls_from_permissions_file",
            lambda p, base, urlconf=None: ([base + "/from-yaml/"], ["v: no route"]),
        )
        out = StringIO()
        call_command(
            "djust_audit",
            "--a11y",
            "--base-url",
            "http://h",
            "--permissions",
            "p.yaml",
            stdout=out,
        )
        assert fake_run["urls"] == ["http://h/from-yaml/"]
        assert "v: no route" in out.getvalue()

    def test_url_without_a11y_is_refused(self):
        err = StringIO()
        with pytest.raises(SystemExit) as exc:
            call_command("djust_audit", "--url", "http://x/", stdout=StringIO(), stderr=err)
        assert exc.value.code == 2
        assert "--a11y" in err.getvalue()

    def test_bad_header_exits_2(self, fake_run):
        with pytest.raises(SystemExit) as exc:
            call_command(
                "djust_audit",
                "--a11y",
                "--url",
                "http://x/",
                "--header",
                "nocolon",
                stdout=StringIO(),
                stderr=StringIO(),
            )
        assert exc.value.code == 2
