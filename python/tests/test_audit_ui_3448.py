"""Behavior contracts for ADR-043 D2 UI audit rules (#3448)."""

import json
import re
import textwrap
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.exceptions import ImproperlyConfigured

from djust.audit_ast import AST_FINDING_CODES, run_ast_audit


def _tree(tmp_path, files):
    for name, source in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")


def _codes(report):
    return [f.code for f in report.findings if f.code.startswith("X1")]


def _run(tmp_path, **kw):
    return run_ast_audit(root=str(tmp_path), **kw)


def _live(tmp_path, source, **kw):
    _tree(tmp_path, {"app/templates/page.html": '<div dj-click="noop"></div>\n' + source})
    return _run(tmp_path, **kw)


def _css(tmp_path, source, ref="css/app.css", **kw):
    _tree(tmp_path, {"app/static/css/app.css": source})
    return _live(tmp_path, "<link href=\"{% static '" + ref + "' %}\">", **kw)


def _command(tmp_path, *flags):
    out = StringIO()
    call_command("djust_audit", "--ast", "--ast-path", str(tmp_path), *flags, stdout=out)
    return out.getvalue()


LOOP_TABLE = "<table>{% for r in rows %}<tr><td>{{ r }}</td></tr>{% endfor %}</table>"
CACHE = ".djust/audit-ui.json"


class TestLiveTemplateDetection:
    def test_dj_attribute_marks_template_live(self, tmp_path):
        assert _codes(_live(tmp_path, LOOP_TABLE)) == ["X101"]

    def test_plain_django_template_is_not_scanned(self, tmp_path):
        _tree(tmp_path, {"app/templates/page.html": LOOP_TABLE})
        assert _codes(_run(tmp_path)) == []

    def test_load_live_tags_marks_template_live(self, tmp_path):
        _tree(tmp_path, {"app/templates/page.html": "{% load live_tags %}\n" + LOOP_TABLE})
        assert _codes(_run(tmp_path)) == ["X101"]

    def test_liveview_template_name_marks_template_live(self, tmp_path):
        _tree(
            tmp_path,
            {
                "views.py": 'class InvoiceListView(LiveView):\n    template_name: str = "page.html"',
                "app/templates/page.html": LOOP_TABLE,
            },
        )
        assert _codes(_run(tmp_path)) == ["X101"]

    def test_extended_base_of_live_template_is_scanned(self, tmp_path):
        _tree(tmp_path, {"app/templates/base.html": LOOP_TABLE})
        report = _live(tmp_path, '{% extends "base.html" %}')
        assert _codes(report) == ["X101"]
        assert report.findings[0].path.endswith("base.html")

    def test_included_partial_of_live_template_is_scanned(self, tmp_path):
        _tree(tmp_path, {"app/templates/partial.html": LOOP_TABLE})
        assert _codes(_live(tmp_path, '{% include "partial.html" %}')) == ["X101"]

    def test_variable_extends_is_ignored(self, tmp_path):
        _tree(tmp_path, {"app/templates/base.html": LOOP_TABLE})
        assert _codes(_live(tmp_path, "{% extends base %}")) == []


class TestX101DataTable:
    def test_table_with_for_loop_triggers(self, tmp_path):
        assert _codes(_live(tmp_path, LOOP_TABLE)) == ["X101"]

    def test_table_with_dj_stream_triggers(self, tmp_path):
        assert _codes(_live(tmp_path, '<TABLE><tbody dj-stream="rows"></tbody></TABLE>')) == [
            "X101"
        ]

    def test_static_table_ok(self, tmp_path):
        assert _codes(_live(tmp_path, "<table><tr><td>one</td></tr></table>")) == []

    def test_loop_table_inside_comment_block_ok(self, tmp_path):
        assert (
            _codes(_live(tmp_path, '{% comment "old" %}\n' + LOOP_TABLE + "{% endcomment %}")) == []
        )

    def test_noqa_same_line_suppresses(self, tmp_path):
        assert _codes(_live(tmp_path, LOOP_TABLE + "{# djust: noqa X101 #}")) == []

    def test_noqa_other_code_does_not_suppress(self, tmp_path):
        assert _codes(_live(tmp_path, LOOP_TABLE + "{# djust: noqa X102 #}")) == ["X101"]

    def test_bare_noqa_suppresses(self, tmp_path):
        assert _codes(_live(tmp_path, LOOP_TABLE + "{# djust: noqa #}")) == []

    def test_line_number_is_table_line(self, tmp_path):
        report = _live(tmp_path, "<!-- old\nmarkup -->\n  " + LOOP_TABLE)
        finding = report.findings[0]
        assert (finding.code, finding.lineno, finding.col) == ("X101", 4, 2)


class TestX102Select:
    def test_dj_change_select_with_generated_options_triggers(self, tmp_path):
        assert _codes(
            _live(
                tmp_path,
                '<select dj-change="pick">{% for o in options %}<option>{{ o }}</option>{% endfor %}</select>',
            )
        ) == ["X102"]

    def test_dj_change_select_over_threshold_triggers(self, tmp_path):
        assert _codes(
            _live(tmp_path, '<select dj-change="pick">' + "<option>x</option>" * 11 + "</select>")
        ) == ["X102"]

    def test_dj_change_select_at_threshold_ok(self, tmp_path):
        assert (
            _codes(
                _live(
                    tmp_path, '<select dj-change="pick">' + "<option>x</option>" * 10 + "</select>"
                )
            )
            == []
        )

    def test_select_without_dj_change_ok(self, tmp_path):
        assert (
            _codes(
                _live(
                    tmp_path,
                    "<select>{% for o in options %}<option>{{ o }}</option>{% endfor %}</select>",
                )
            )
            == []
        )


class TestX103Overlay:
    def test_modal_class_triggers(self, tmp_path):
        assert _codes(_live(tmp_path, '<div class="modal"></div>')) == ["X103"]

    def test_drawer_class_inside_template_branch_triggers(self, tmp_path):
        assert _codes(
            _live(tmp_path, '<div class="drawer {% if show %}open{% endif %}"></div>')
        ) == ["X103"]

    def test_fixed_four_edges_inline_style_triggers(self, tmp_path):
        assert _codes(
            _live(
                tmp_path,
                '<aside style="position: fixed; top: 0px; bottom: 0 !important; left: 0; right: 0"></aside>',
            )
        ) == ["X103"]

    @pytest.mark.parametrize("geometry", ["top:0;bottom:0", "height:100vh", "height:100%"])
    @pytest.mark.parametrize(
        "marker", ["conditional", "dialog", "modal", "click", "click-away", "backdrop"]
    )
    def test_fixed_full_height_drawer_with_overlay_marker(self, tmp_path, geometry, marker):
        attrs = {
            "dialog": ' role="dialog"',
            "modal": ' aria-modal="true"',
            "click": ' dj-click="close_panel"',
            "click-away": ' dj-click-away="close_panel"',
        }.get(marker, "")
        source = (
            '<aside style="position:fixed;right:0;width:24rem;'
            + geometry
            + '"'
            + attrs
            + "></aside>"
        )
        if marker == "conditional":
            source = "{% if open %}" + source + "{% endif %}"
        elif marker == "backdrop":
            source = '<div class="backdrop" dj-click="close_panel"></div>' + source
        assert _codes(_live(tmp_path, source)) == ["X103"]

    @pytest.mark.parametrize("geometry", ["top:0;bottom:0", "height:100vh", "height:100%"])
    def test_unconditional_full_height_sidebar_without_markers(self, tmp_path, geometry):
        source = '<nav style="position:fixed;left:0;width:24rem;' + geometry + '">nav</nav>'
        assert _codes(_live(tmp_path, source)) == []

    @pytest.mark.parametrize(
        "prefix",
        [
            "{% if open %}<p>Open</p>{% endif %}",
            '<div dj-click="close_panel"></div>',
            '<div class="panel {% if open %}active{% endif %}"></div>',
            '<section><div class="backdrop" dj-click="close_panel"></div></section>',
        ],
    )
    def test_unrelated_overlay_markers_do_not_flag_sidebar(self, tmp_path, prefix):
        source = prefix + '<nav style="position:fixed;top:0;bottom:0;left:0;width:24rem">nav</nav>'
        assert _codes(_live(tmp_path, source)) == []

    def test_fixed_inset_zero_triggers(self, tmp_path):
        assert _codes(_live(tmp_path, '<div style="position:fixed;inset:0"></div>')) == ["X103"]

    def test_fixed_header_ok(self, tmp_path):
        assert (
            _codes(
                _live(tmp_path, '<header style="position:fixed;top:0;left:0;right:0">nav</header>')
            )
            == []
        )

    def test_panel_class_alone_ok(self, tmp_path):
        assert _codes(_live(tmp_path, '<div class="panel card"></div>')) == []


class TestX104StatusMessage:
    def test_if_error_class_switch_triggers(self, tmp_path):
        assert _codes(
            _live(
                tmp_path,
                '<p class="{% if error %}msg-error{% else %}msg-success{% endif %}">{{ message }}</p>',
            )
        ) == ["X104"]

    def test_class_from_message_variable_triggers(self, tmp_path):
        assert _codes(_live(tmp_path, '<div class="{{ message_class }}">{{ message }}</div>')) == [
            "X104"
        ]

    def test_status_badge_ok(self, tmp_path):
        assert (
            _codes(
                _live(
                    tmp_path,
                    '<span class="{% if order.paid %}badge-success{% endif %}">{{ order.status }}</span>',
                )
            )
            == []
        )

    def test_static_class_message_ok(self, tmp_path):
        assert _codes(_live(tmp_path, '<p class="error">{{ error }}</p>')) == []


class TestX105Stylesheet:
    def test_linked_stylesheet_hex_color_triggers(self, tmp_path):
        report = _css(tmp_path, "body { color: #333; }")
        assert _codes(report) == ["X105"]
        assert report.files_scanned == 2

    def test_rgb_and_hsl_literals_counted_once_per_file(self, tmp_path):
        report = _css(tmp_path, "body { color: rgb(1,2,3); }\na { background: hsl(10 20% 30%); }")
        assert _codes(report) == ["X105"]
        assert report.findings[0].details.startswith("2 color literal(s)")
        assert report.findings[0].lineno == 1
        assert "lines 1, 2" in report.findings[0].details

    def test_theme_token_usage_ok(self, tmp_path):
        assert _codes(_css(tmp_path, "body { color: hsl(var(--foreground)); }")) == []

    def test_var_fallback_literal_ok(self, tmp_path):
        assert (
            _codes(_css(tmp_path, "body { background: var(--card, var(--background, #fff)); }"))
            == []
        )

    def test_overriding_theme_token_custom_property_ok(self, tmp_path):
        assert (
            _codes(
                _css(
                    tmp_path,
                    ":root { --primary: #fff; --primary-text: rgb(0,0,0); --sidebar-background: #333; --chart-6: #123; }",
                )
            )
            == []
        )

    def test_parallel_palette_custom_property_triggers(self, tmp_path):
        assert _codes(_css(tmp_path, ":root { --my-red: #f00; }")) == ["X105"]

    def test_unlinked_stylesheet_not_scanned(self, tmp_path):
        _tree(tmp_path, {"app/static/css/app.css": "body { color: #333; }"})
        assert _codes(_live(tmp_path, "hello")) == []

    def test_stylesheet_linked_only_from_non_live_template_not_scanned(self, tmp_path):
        _tree(
            tmp_path,
            {
                "app/templates/plain.html": "<link href=\"{% static 'css/app.css' %}\">",
                "app/static/css/app.css": "body { color: #333; }",
            },
        )
        assert _codes(_run(tmp_path)) == []

    def test_min_css_skipped(self, tmp_path):
        _tree(tmp_path, {"app/static/css/app.min.css": "body{color:#333}"})
        assert _codes(_live(tmp_path, "<link href=\"{% static 'css/app.min.css' %}\">")) == []

    def test_css_noqa_suppresses_line(self, tmp_path):
        report = _css(tmp_path, "a { color: #333; } /* djust: noqa X105 */\nb { color: #fff; }")
        assert _codes(report) == ["X105"]
        assert report.findings[0].lineno == 2
        assert report.findings[0].details.startswith("1 color literal(s)")

    def test_all_lines_suppressed_no_finding(self, tmp_path):
        assert _codes(_css(tmp_path, "a { color: #333; } /* djust: noqa */")) == []

    def test_css_comments_ignored(self, tmp_path):
        assert _codes(_css(tmp_path, "/* a { color: #333; }\nb { color: rgb(1,2,3); } */")) == []

    def test_staticfiles_dirs_prefix_tuple_resolved(self, tmp_path):
        _tree(tmp_path, {"assets/css/app.css": "a { color: #333; }"})
        report = _live(
            tmp_path,
            "<link href=\"{% static 'vendor/css/app.css' %}\">",
            static_dirs=[("vendor", str(tmp_path / "assets"))],
        )
        assert _codes(report) == ["X105"]

    def test_parent_dir_static_ref_rejected(self, tmp_path):
        assert _codes(_css(tmp_path, "a { color: #333; }", ref="../static/css/app.css")) == []


class TestUIRuleSwitches:
    def test_include_ui_false_skips_x1xx(self, tmp_path):
        report = _live(tmp_path, LOOP_TABLE, include_ui=False)
        assert _codes(report) == []
        assert not report.ui_ran

    def test_no_templates_skips_x1xx(self, tmp_path):
        report = _live(tmp_path, LOOP_TABLE, include_templates=False)
        assert _codes(report) == []
        assert not report.ui_ran

    def test_every_x1xx_code_is_a_warning(self):
        assert all(
            AST_FINDING_CODES[code][0] == "warning"
            for code in ("X101", "X102", "X103", "X104", "X105")
        )

    def test_existing_x006_scan_unchanged_on_non_live_template(self, tmp_path):
        _tree(tmp_path, {"plain.html": "{{ value|safe }}"})
        assert [f.code for f in _run(tmp_path).findings] == ["X006"]

    def test_report_to_dict_has_ui_block(self, tmp_path):
        report = _live(tmp_path, LOOP_TABLE)
        assert report.to_dict()["ui"] == {
            "ran": True,
            "templates_scanned": 1,
            "live_templates": 1,
            "stylesheets_scanned": 0,
        }
        assert not (tmp_path / CACHE).exists()


class TestSuggestedTagsExist:
    def test_every_tag_named_in_x1xx_text_is_registered(self):
        from djust import audit_ui
        from djust.components import __all__ as exports
        from djust.components.templatetags.djust_components import register

        for code in ("X101", "X102", "X103", "X104", "X105"):
            text = AST_FINDING_CODES[code][1] + getattr(audit_ui, f"_{code}_DETAILS")
            for name in re.findall(r"\{%\s*(\w+)", text):
                assert (name[3:] if name.startswith("end") else name) in register.tags
        assert "ServerEventToastMixin" in exports


class TestUISummaryCache:
    def _trigger(self, tmp_path):
        _tree(tmp_path, {"app/templates/page.html": '<div dj-click="noop"></div>\n' + LOOP_TABLE})

    def test_command_writes_summary_with_schema(self, tmp_path):
        self._trigger(tmp_path)
        _tree(tmp_path, {"app/templates/plain.html": "plain"})
        _command(tmp_path)
        payload = json.loads((tmp_path / CACHE).read_text())
        assert payload["version"] == 1
        assert payload["generator"] == "djust_audit --ast"
        assert payload["root"] == str(tmp_path)
        assert payload["rules"] == ["X101", "X102", "X103", "X104", "X105"]
        assert payload["counts"] == {"X101": 1, "X102": 0, "X103": 0, "X104": 0, "X105": 0}
        assert payload["total"] == 1
        assert payload["djust_version"]
        assert payload["generated_at"].endswith("+00:00")
        assert payload["templates"]["app/templates/plain.html"]["live"] is False
        assert payload["stylesheets"] == {}

    def test_summary_dir_is_self_gitignored(self, tmp_path):
        self._trigger(tmp_path)
        _command(tmp_path)
        assert (tmp_path / ".djust/.gitignore").read_text() == "*\n"

    def test_summary_template_mtimes_match_disk(self, tmp_path):
        self._trigger(tmp_path)
        _command(tmp_path)
        payload = json.loads((tmp_path / CACHE).read_text())
        assert (
            payload["templates"]["app/templates/page.html"]["mtime"]
            == (tmp_path / "app/templates/page.html").stat().st_mtime
        )

    def test_summary_counts_exclude_suppressed(self, tmp_path):
        _live(tmp_path, LOOP_TABLE + "{# djust: noqa X101 #}")
        _command(tmp_path)
        assert json.loads((tmp_path / CACHE).read_text())["total"] == 0

    def test_no_ui_cache_flag_skips_write(self, tmp_path):
        self._trigger(tmp_path)
        assert "X101" in _command(tmp_path, "--ast-no-ui-cache")
        assert not (tmp_path / CACHE).exists()

    def test_no_ui_flag_skips_findings_and_write(self, tmp_path):
        self._trigger(tmp_path)
        assert "X101" not in _command(tmp_path, "--ast-no-ui")
        assert not (tmp_path / CACHE).exists()

    def test_unwritable_cache_does_not_fail_audit(self, tmp_path, monkeypatch):
        from djust import audit_ui

        self._trigger(tmp_path)

        def deny(*args):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(audit_ui.os, "replace", deny)
        assert "UI summary not written: Permission denied" in _command(tmp_path)
        assert not list((tmp_path / ".djust").glob("*.tmp"))
        assert not (tmp_path / CACHE).exists()

    def test_write_ui_summary_returns_reason_on_oserror(self, tmp_path, monkeypatch):
        from djust import audit_ui

        report = _live(tmp_path, LOOP_TABLE)

        def deny(*args, **kwargs):
            raise PermissionError(13, "Permission denied")

        monkeypatch.setattr(audit_ui.os, "mkdir", deny)
        assert audit_ui.write_ui_summary(report, str(tmp_path)) == (None, "Permission denied")

    def test_json_output_reports_ui_cache_path(self, tmp_path):
        self._trigger(tmp_path)
        payload = json.loads(_command(tmp_path, "--json"))
        assert payload["ui_cache"] == str(tmp_path / CACHE)
        assert payload["ui_cache_error"] is None

    def test_x1xx_warning_exits_zero_without_strict(self, tmp_path):
        self._trigger(tmp_path)
        assert "X101" in _command(tmp_path)

    def test_x1xx_warning_fails_strict(self, tmp_path):
        self._trigger(tmp_path)
        with pytest.raises(SystemExit) as exc:
            _command(tmp_path, "--strict")
        assert exc.value.code == 1


class TestCombinedTrees:
    def test_trigger_tree_all_five_rules(self, tmp_path):
        _tree(
            tmp_path,
            {
                "views.py": 'class InvoiceListView(LiveView):\n    template_name = "invoices/list.html"',
                "app/templates/base.html": '<link href="{% static \'css/app.css\' %}">\n<div class="drawer"></div>',
                "app/templates/invoices/list.html": '{% extends "base.html" %}\n'
                + LOOP_TABLE
                + '\n<select dj-change="pick">{% for a in accounts %}<option>{{ a }}</option>{% endfor %}</select>\n<p class="{% if error %}msg-error{% else %}msg-success{% endif %}">{{ message }}</p>',
                "app/static/css/app.css": "body { color: #333; background: rgb(250,250,250); }\n.ok { color: hsl(var(--success)); }",
            },
        )
        report = _run(tmp_path)
        assert sorted(_codes(report)) == ["X101", "X102", "X103", "X104", "X105"]
        assert all(f.severity == "warning" for f in report.findings)
        assert next(f for f in report.findings if f.code == "X105").details.startswith(
            "2 color literal(s)"
        )

    def test_clean_tree_no_ui_findings(self, tmp_path):
        _tree(
            tmp_path,
            {
                "app/templates/admin_report.html": LOOP_TABLE,
                "app/static/css/themed.css": "body { color: hsl(var(--foreground)); background: var(--card, #fff); }\n:root { --primary: 220 90% 50%; }\n.legacy { color: #123456; } /* djust: noqa X105 */\n/* .old { color: #fff; } */",
                "app/static/css/vendor.min.css": "body{color:#000}",
            },
        )
        report = _live(
            tmp_path,
            '<table><tr><td>static</td></tr></table>\n<select dj-change="pick"><option>a</option><option>b</option></select>\n<header style="position:fixed;top:0;left:0;right:0">nav</header>\n<div class="panel card"></div>\n<span class="{% if order.paid %}badge-success{% endif %}">{{ order.status }}</span>\n'
            + LOOP_TABLE
            + "{# djust: noqa X101 #}\n{% comment %}"
            + LOOP_TABLE
            + "{% endcomment %}\n<link href=\"{% static 'css/themed.css' %}\">\n<link href=\"{% static 'css/vendor.min.css' %}\">",
        )
        assert _codes(report) == []


class TestDiscoveryBoundaries:
    @pytest.mark.parametrize("update", ["append", "prepend"])
    def test_table_update_triggers(self, tmp_path, update):
        assert _codes(_live(tmp_path, f'<table dj-update="{update}"><tr></tr></table>')) == ["X101"]

    def test_comment_markers_do_not_make_plain_template_live(self, tmp_path):
        _tree(
            tmp_path, {"app/templates/plain.html": '<!-- <div dj-click="noop"> -->\n' + LOOP_TABLE}
        )
        assert _codes(_run(tmp_path)) == []

    def test_transitive_ambiguous_template_closure(self, tmp_path):
        _tree(
            tmp_path,
            {
                "a/templates/base.html": '{% include "part.html" %}',
                "b/templates/base.html": '<div class="modal"></div>',
                "a/templates/part.html": LOOP_TABLE,
            },
        )
        assert sorted(_codes(_live(tmp_path, '{% extends "base.html" %}'))) == ["X101", "X103"]

    def test_no_basename_fallback(self, tmp_path):
        _tree(tmp_path, {"app/templates/sub/base.html": LOOP_TABLE})
        assert _codes(_live(tmp_path, '{% extends "base.html" %}')) == []

    def test_excluded_css_not_scanned(self, tmp_path):
        report = _css(tmp_path, "a { color: #fff; }", exclude=["app/static/css/app.css"])
        assert _codes(report) == []
        assert report.files_scanned == 1

    @pytest.mark.parametrize("kind", ["html", "css"])
    def test_large_files_skipped_for_ui(self, tmp_path, kind):
        if kind == "html":
            report = _live(tmp_path, LOOP_TABLE + " " * 1_048_576)
        else:
            report = _css(tmp_path, "a { color: #fff; }" + " " * 1_048_576)
        assert _codes(report) == []
        assert any(reason == "too large for UI rules" for _, reason in report.files_skipped)

    def test_css_deduplicated_across_links_and_roots(self, tmp_path):
        _tree(tmp_path, {"app/static/css/app.css": "a { color: #fff; }"})
        report = _live(
            tmp_path,
            "<link href=\"{% static 'css/app.css' %}\">\n<link href=\"{% static 'css/app.css' %}\">",
            static_dirs=[str(tmp_path / "app/static")],
        )
        assert _codes(report) == ["X105"]
        assert len(report.ui_stylesheets) == 1

    def test_command_no_templates_skips_cache(self, tmp_path):
        _live(tmp_path, LOOP_TABLE)
        assert "X101" not in _command(tmp_path, "--ast-no-templates")
        assert not (tmp_path / CACHE).exists()

    def test_command_resolves_configured_static_dirs(self, tmp_path, settings):
        _tree(tmp_path, {"assets/app.css": "a { color: #fff; }"})
        _live(tmp_path, "<link href=\"{% static 'vendor/app.css' %}\">")
        settings.STATICFILES_DIRS = [("vendor", tmp_path / "assets")]
        payload = json.loads(_command(tmp_path, "--json"))
        assert [f["code"] for f in payload["findings"]] == ["X105"]
        cache = json.loads((tmp_path / CACHE).read_text())
        assert cache["stylesheets"]["assets/app.css"]["findings"] == {"X105": 1}


class TestReviewRegressions:
    @pytest.mark.parametrize(
        "source",
        [
            '<div class="form-group {% if field.errors %}has-error{% endif %}">{{ field }}</div>',
            '<p class="{% if form.errors %}text-danger{% endif %}">{{ form.non_field_errors }}</p>',
        ],
    )
    def test_form_validation_is_not_status_message(self, tmp_path, source):
        assert _codes(_live(tmp_path, source)) == []

    @pytest.mark.parametrize("variable", ["message", "msg", "flash", "notice", "status_text"])
    def test_message_names_still_trigger(self, tmp_path, variable):
        assert _codes(
            _live(
                tmp_path,
                '<p class="{% if '
                + variable
                + ' %}text-success{% endif %}">{{ '
                + variable
                + " }}</p>",
            )
        ) == ["X104"]

    @pytest.mark.parametrize(
        "source",
        [
            '<div class="overlay">caption</div>',
            '<nav style="position:fixed;top:0;bottom:0;left:0;width:240px">nav</nav>',
        ],
    )
    def test_overlay_caption_and_sidebar_are_not_modal(self, tmp_path, source):
        assert _codes(_live(tmp_path, source)) == []

    @pytest.mark.parametrize(
        "style",
        [
            "position:fixed;top:0;bottom:0;left:0;right:0",
            "position:fixed;width:100vw;height:100vh",
        ],
    )
    def test_full_screen_fixed_overlay_triggers(self, tmp_path, style):
        assert _codes(_live(tmp_path, '<div style="' + style + '"></div>')) == ["X103"]

    def test_selector_is_not_css_declaration(self, tmp_path):
        assert _codes(_css(tmp_path, "a:hover, #bad { color: var(--foreground); }")) == []
        assert _codes(_css(tmp_path, "a:hover, #bad { color: #fff; }")) == ["X105"]

    def test_stylesheet_symlink_outside_static_base_skipped(self, tmp_path):
        _tree(tmp_path, {"outside.css": "a { color: #fff; }", "app/static/keep.txt": ""})
        (tmp_path / "app/static/app.css").symlink_to(tmp_path / "outside.css")
        report = _live(tmp_path, "<link href=\"{% static 'app.css' %}\">")
        assert not report.ui_stylesheets
        assert _codes(report) == []

    def test_stylesheet_symlink_inside_static_base_allowed(self, tmp_path):
        _tree(tmp_path, {"app/static/real.css": "a { color: #fff; }"})
        (tmp_path / "app/static/app.css").symlink_to(tmp_path / "app/static/real.css")
        assert _codes(_live(tmp_path, "<link href=\"{% static 'app.css' %}\">")) == ["X105"]

    def test_symlink_scan_root_exclusion_and_cache_keys(self, tmp_path):
        root = tmp_path / "project"
        _css(root, "a { color: #fff; }")
        link = tmp_path / "project-link"
        link.symlink_to(root, target_is_directory=True)
        assert _codes(_run(link, exclude=["app/static"])) == []
        _command(link)
        cache = json.loads((root / CACHE).read_text())
        assert cache["root"] == str(root.resolve())
        assert list(cache["templates"]) == ["app/templates/page.html"]
        assert list(cache["stylesheets"]) == ["app/static/css/app.css"]

    @pytest.mark.parametrize("kind", ["symlink", "dangling", "file"])
    def test_cache_directory_must_be_real_directory(self, tmp_path, kind):
        target = tmp_path / "outside"
        if kind == "symlink":
            target.mkdir()
        cache_dir = tmp_path / ".djust"
        if kind == "file":
            cache_dir.write_text("keep")
        else:
            cache_dir.symlink_to(target, target_is_directory=True)
        output = _command(tmp_path)
        assert "UI summary not written:" in output
        assert not (target / "audit-ui.json").exists()
        assert not (target / ".gitignore").exists()
        if kind == "dangling":
            assert not target.exists()

    def test_existing_cache_directory_does_not_get_gitignore(self, tmp_path):
        (tmp_path / ".djust").mkdir()
        _command(tmp_path)
        assert not (tmp_path / ".djust/.gitignore").exists()
        assert (tmp_path / CACHE).exists()

    def test_dangling_gitignore_symlink_is_not_followed(self, tmp_path):
        (tmp_path / ".djust").mkdir()
        target = tmp_path / "must-not-create"
        (tmp_path / ".djust/.gitignore").symlink_to(target)
        _command(tmp_path)
        assert not target.exists()
        assert (tmp_path / ".djust/.gitignore").is_symlink()

    def test_new_cache_gitignore_uses_exclusive_nofollow(self, tmp_path, monkeypatch):
        from djust import audit_ui

        real_open = audit_ui.os.open
        flags_seen = []

        def track(path, flags, *args, **kwargs):
            if str(path).endswith(".gitignore"):
                flags_seen.append(flags)
            return real_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(audit_ui.os, "open", track)
        _command(tmp_path)
        assert len(flags_seen) == 1
        assert flags_seen[0] & audit_ui.os.O_CREAT
        assert flags_seen[0] & audit_ui.os.O_EXCL
        assert flags_seen[0] & audit_ui.os.O_NOFOLLOW
        mode = (tmp_path / ".djust/.gitignore").stat().st_mode
        assert mode & 0o022 == 0
        assert mode & 0o400

    def test_cache_json_symlink_replaced_and_mode_is_readable(self, tmp_path):
        (tmp_path / ".djust").mkdir()
        target = tmp_path / "keep.json"
        target.write_text("keep")
        (tmp_path / CACHE).symlink_to(target)
        _command(tmp_path)
        assert target.read_text() == "keep"
        assert not (tmp_path / CACHE).is_symlink()
        mode = (tmp_path / CACHE).stat().st_mode
        assert mode & 0o022 == 0
        assert mode & 0o400

    def test_css_detail_is_terminal_safe_and_bounded(self, tmp_path):
        _css(tmp_path, "a { color: \x1b[2J\x1b[31m\x00\x7f\x85" + " " * 300 + "#abcdef; }")
        output = _command(tmp_path)
        assert "X105" in output
        assert not any(ord(c) < 32 and c not in "\n\t" or 127 <= ord(c) <= 159 for c in output)
        detail = next(line for line in output.splitlines() if "first `" in line)
        assert len(detail.split("first `", 1)[1].split("`", 1)[0]) <= 120

    def test_ui_crash_warns_without_exception_paths(self, tmp_path, monkeypatch):
        from djust import audit_ui

        def crash(*args, **kwargs):
            raise RuntimeError("secret/path\nsecret")

        monkeypatch.setattr(audit_ui, "scan_ui", crash)
        output = _command(tmp_path)
        assert any("WARNING: UI rules failed" in line for line in output.splitlines())
        assert "secret" not in output
        assert not (tmp_path / CACHE).exists()

    @pytest.mark.parametrize("error", [ImproperlyConfigured, TypeError, ValueError])
    def test_invalid_static_settings_warn(self, tmp_path, monkeypatch, error):
        from djust.management.commands import djust_audit

        class BadSettings:
            configured = True

            @property
            def STATICFILES_DIRS(self):
                raise error("secret/path\nsecret")

        monkeypatch.setattr("django.conf.settings", BadSettings())
        from io import StringIO

        out = StringIO()
        err = StringIO()
        command = djust_audit.Command(stdout=out, stderr=err)
        command._run_ast_audit({"ast_path": str(tmp_path)})
        assert "WARNING: STATICFILES_DIRS" in out.getvalue() + err.getvalue()
        assert "secret" not in out.getvalue() + err.getvalue()

    def test_unexpected_static_settings_error_propagates(self, monkeypatch):
        from djust.management.commands.djust_audit import Command

        class BadSettings:
            configured = True

            @property
            def STATICFILES_DIRS(self):
                raise RuntimeError("unexpected")

        monkeypatch.setattr("django.conf.settings", BadSettings())
        with pytest.raises(RuntimeError, match="unexpected"):
            Command()._static_dirs_from_settings()


@pytest.mark.parametrize(
    "case",
    [
        "div",
        "html-comment",
        "django-comment",
        "css-comment",
        "css-letters",
        "link",
        "table",
        "css-normal",
        "unclosed-tables",
        "css-letters-block",
        "css-noqa",
        "html-noqa",
        "unclosed-expressions",
    ],
)
def test_adversarial_scan_completes_under_two_seconds(case):
    import time
    from djust.audit_ui import scan_ui, _scan_stylesheet

    size = 524288 if case == "table" else 1048576
    units = {
        "div": "<div>",
        "html-comment": "<!-- ",
        "django-comment": "{% comment %}",
        "css-comment": "/* ",
        "css-letters": "a",
        "link": "<link href=",
        "table": '<tr><td class="cell">{{ r }}</td></tr>\n',
        "css-normal": ".btn{color:#fff;background-color:#123456;}\n",
        "unclosed-tables": "<table>",
        "css-letters-block": "a",
        "css-noqa": " ",
        "html-noqa": " ",
        "unclosed-expressions": "{{",
    }
    prefix = {
        "table": "<table>{% for r in rows %}",
        "css-letters-block": "a{",
        "css-noqa": "/* djust: noqa",
        "html-noqa": "{# djust: noqa",
    }.get(case, "")
    suffix = {"table": "{% endfor %}</table>", "css-letters-block": "}"}.get(case, "")
    remaining = size - len(prefix) - len(suffix)
    unit = units[case]
    source = prefix + unit * (remaining // len(unit)) + " " * (remaining % len(unit)) + suffix
    start = time.process_time()
    if case.startswith("css-"):
        findings = _scan_stylesheet("app.css", source)
    else:
        findings = scan_ui(".", {"app/templates/page.html": source}, {"page.html"}).findings
    elapsed = time.process_time() - start
    assert elapsed < 2, (case, elapsed)
    if case == "table":
        assert [f.code for f in findings] == ["X101"]
    elif case == "css-normal":
        assert [f.code for f in findings] == ["X105"]
    else:
        assert not findings


def test_pretty_finding_detail_sanitizes_controls():
    from djust.audit_ast import ASTFinding

    finding = ASTFinding.make("X101", "page.html", 1, details="bad\x1b[2J\n\x7f\x85detail")
    line = finding.format_line()
    assert not any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in line)
    assert finding.to_dict()["details"] == "bad\x1b[2J\n\x7f\x85detail"


def test_new_cache_directory_gitignore_symlink_race_is_not_followed(tmp_path, monkeypatch):
    from djust import audit_ui

    target = tmp_path / "must-not-create"
    original_mkdir = audit_ui.os.mkdir

    def create_with_symlink(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        if str(path).endswith(".djust"):
            (tmp_path / ".djust/.gitignore").symlink_to(target)

    monkeypatch.setattr(audit_ui.os, "mkdir", create_with_symlink)
    output = _command(tmp_path)
    assert "UI summary not written:" in output
    assert not target.exists()
    assert (tmp_path / ".djust/.gitignore").is_symlink()


def test_static_settings_warning_preserves_json_output(tmp_path, monkeypatch):
    from djust.management.commands.djust_audit import Command

    class BadSettings:
        configured = True
        STATICFILES_DIRS = 42

    monkeypatch.setattr("django.conf.settings", BadSettings())
    out, err = StringIO(), StringIO()
    Command(stdout=out, stderr=err)._run_ast_audit({"ast_path": str(tmp_path), "json_output": True})
    assert json.loads(out.getvalue())["mode"] == "ast"
    assert "WARNING: STATICFILES_DIRS" in err.getvalue()


@pytest.mark.parametrize("opener,closer", [("{#", "#}"), ("/*", "*/")])
@pytest.mark.parametrize(
    "body,expected",
    [
        ("djust: noqa", True),
        ("djust: noqa X103", True),
        ("DJUST : NOQA: x103, X105", True),
        ("djust: noqa X105", False),
        ("djust: noqa X1030", False),
        ("djust: noqaX103", False),
    ],
)
def test_shared_comment_suppression_syntax(opener, closer, body, expected):
    from djust.audit_ast import _comment_suppressed

    assert _comment_suppressed(opener + body + closer, "X103", opener, closer) is expected


@pytest.mark.parametrize("replace_fails", [False, True])
def test_summary_temp_cleanup_only_before_successful_replace(tmp_path, monkeypatch, replace_fails):
    from djust import audit_ui

    report = _live(tmp_path, LOOP_TABLE)
    original_replace = audit_ui.os.replace
    original_unlink = audit_ui.os.unlink
    unlinked = []

    def replace(source, target):
        if replace_fails:
            raise OSError("replace failed")
        return original_replace(source, target)

    def unlink(path, *args, **kwargs):
        unlinked.append(path)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(audit_ui.os, "replace", replace)
    monkeypatch.setattr(audit_ui.os, "unlink", unlink)
    path, error = audit_ui.write_ui_summary(report, str(tmp_path))
    assert bool(error) is replace_fails
    assert bool(path) is not replace_fails
    assert len(unlinked) == int(replace_fails)
    assert not list((tmp_path / ".djust").glob(".audit-ui.*.tmp"))


@pytest.mark.parametrize("case", ["div", "table"])
def test_adversarial_scan_scales_linearly(case):
    import time
    from djust.audit_ui import scan_ui

    unit = "<div>" if case == "div" else '<tr><td class="cell">{{ r }}</td></tr>\n'
    prefix = "" if case == "div" else "<table>{% for r in rows %}"
    suffix = "" if case == "div" else "{% endfor %}</table>"
    timings = []
    for size in (262144, 1048576):
        remaining = size - len(prefix) - len(suffix)
        source = prefix + unit * (remaining // len(unit)) + " " * (remaining % len(unit)) + suffix
        start = time.process_time()
        findings = scan_ui(".", {"app/templates/page.html": source}, {"page.html"}).findings
        timings.append(time.process_time() - start)
        assert [f.code for f in findings] == ([] if case == "div" else ["X101"])
    assert timings[1] <= 6 * timings[0], (case, timings)


@pytest.mark.parametrize(
    "before,after,expected",
    [
        ("{% if outer %}{% if inner %}{% endif %}", "{% endif %}", True),
        ("{% if outer %}{% if inner %}", "", True),
        ("{% if outer %}{% endif %}", "", False),
        ('<section class="{% if open %}active{% endif %}">', "</section>", False),
        ('<section><div class="backdrop" dj-click="close"></div>', "</section>", True),
        ("<section>", '<div class="backdrop" dj-click="close"></div></section>', True),
        (
            '<section><div class="backdrop" dj-click="close"></div><article>',
            "</article></section>",
            False,
        ),
        ('<section><article></section><div class="backdrop" dj-click="close"></div>', "", True),
        ('<section><div class="backdrop" dj-click="close"></div></unknown>', "</section>", True),
    ],
)
def test_drawer_context_boundaries(tmp_path, before, after, expected):
    drawer = '<aside style="position:fixed;top:0;bottom:0;right:0"></aside>'
    assert _codes(_live(tmp_path, before + drawer + after)) == (["X103"] if expected else [])


@pytest.mark.parametrize("missing", [True, False])
def test_summary_cleanup_errors_return_as_data(tmp_path, monkeypatch, missing):
    from djust import audit_ui

    report = _run(tmp_path)
    original_unlink = audit_ui.os.unlink

    def deny_replace(source, target):
        raise PermissionError(13, "Permission denied")

    def cleanup(path):
        # Remove the actual scratch file before simulating either cleanup race.
        original_unlink(path)
        if missing:
            raise FileNotFoundError(2, "No such file or directory")
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(audit_ui.os, "replace", deny_replace)
    monkeypatch.setattr(audit_ui.os, "unlink", cleanup)
    assert audit_ui.write_ui_summary(report, str(tmp_path)) == (None, "Permission denied")
    assert not list((tmp_path / ".djust").glob(".audit-ui.*.tmp"))


def test_summary_cleanup_does_not_suppress_unexpected_exception(tmp_path, monkeypatch):
    from djust import audit_ui

    report = _run(tmp_path)
    original_unlink = audit_ui.os.unlink

    def crash(*args, **kwargs):
        raise RuntimeError("unexpected encoder error")

    def deny_cleanup(path):
        original_unlink(path)
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(audit_ui.json, "dump", crash)
    monkeypatch.setattr(audit_ui.os, "unlink", deny_cleanup)
    with pytest.raises(RuntimeError, match="unexpected encoder error"):
        audit_ui.write_ui_summary(report, str(tmp_path))
