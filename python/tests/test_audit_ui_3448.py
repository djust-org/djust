"""Behavior contracts for ADR-043 D2 UI audit rules (#3448)."""

import json
import os
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
            for code in ("X101", "X102", "X103", "X104", "X105", "X106")
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

        for code in ("X101", "X102", "X103", "X104", "X105", "X106"):
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
        assert payload["rules"] == ["X101", "X102", "X103", "X104", "X105", "X106"]
        assert payload["counts"] == {
            "X101": 1,
            "X102": 0,
            "X103": 0,
            "X104": 0,
            "X105": 0,
            "X106": 0,
        }
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
        "css-font-face-nesting",
    ],
)
def test_adversarial_scan_completes_under_two_seconds(case):
    import gc
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
        "css-font-face-nesting": "@font-face{",
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

    def scan():
        if case.startswith("css-"):
            return _scan_stylesheet("app.css", source)
        return scan_ui(".", {"app/templates/page.html": source}, {"page.html"}).findings

    # A loaded CI runner can push one sample over the bound (a GC pass, a
    # preempted core). A slow sample is retried, up to three in all, with the
    # collector off, and the best one counts: noise only adds time, while a
    # pathological (super-linear) scan of 1 MB stays far over 2 s every time.
    elapsed = float("inf")
    for _ in range(3):
        gc.collect()
        gc.disable()
        try:
            start = time.process_time()
            findings = scan()
            elapsed = min(elapsed, time.process_time() - start)
        finally:
            gc.enable()
        if elapsed < 2:
            break
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


@pytest.mark.parametrize("case", ["div", "table", "style-attrs", "css-named-colors"])
def test_adversarial_scan_scales_linearly(case):
    import gc
    import time
    from djust.audit_ui import _scan_stylesheet, scan_ui

    # (unit, prefix, suffix, expected finding codes). The heavy new corpora are judged
    # by growth, not by a wall-clock bound: a 1 MB scan costs ~0.5 s here and several
    # times that on a loaded CI runner, but a super-linear scan still grows >8x per 4x.
    unit, prefix, suffix, expected = {
        "div": ("<div>", "", "", []),
        "table": (
            '<tr><td class="cell">{{ r }}</td></tr>\n',
            "<table>{% for r in rows %}",
            "{% endfor %}</table>",
            ["X101"],
        ),
        "style-attrs": (
            '<p style="color:red;font-family:Arial">{{ r }}</p>\n',
            "",
            "",
            ["X105", "X106"],
        ),
        "css-named-colors": (
            ".btn{border:1px solid red;font:12px Arial}\n",
            "",
            "",
            ["X105", "X106"],
        ),
    }[case]

    def scan(source):
        if case.startswith("css-"):
            return _scan_stylesheet("app.css", source)
        return scan_ui(".", {"app/templates/page.html": source}, {"page.html"}).findings

    timings = []
    for size in (262144, 1048576):
        remaining = size - len(prefix) - len(suffix)
        source = prefix + unit * (remaining // len(unit)) + " " * (remaining % len(unit)) + suffix
        # One wall-clock or CPU sample is noisy on a loaded CI runner (a GC
        # pass or a preempted core inflates it). Take the best of three with
        # the collector off: noise only ever adds time, so the minimum is the
        # closest to the real cost.
        samples = []
        for _ in range(3):
            gc.collect()
            gc.disable()
            try:
                start = time.process_time()
                findings = scan(source)
                samples.append(time.process_time() - start)
            finally:
                gc.enable()
            assert [f.code for f in findings] == expected
        timings.append(min(samples))
    # 4x the input: a linear scan costs ~4x and a quadratic one ~16x. 8x is the
    # geometric midpoint, so it tolerates noise and still fails a quadratic.
    assert timings[1] <= 8 * timings[0], (case, timings)


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


# ---------------------------------------------------------------------------
# #3687 -- X105 named colours, X106 font-family literals, style= attributes
# ---------------------------------------------------------------------------

# The stylesheet and template from the #3687 report.
ISSUE_3687_CSS = """\
.swatch { background: hsl(var(--primary)); }
.note   { color: white; font-family: "Comic Sans MS", cursive; }
.bars span { background: #3b82f6; }
.menu { box-shadow: var(--shadow, 0 1px 2px rgb(0 0 0 / .2)); color: transparent; }
"""
ISSUE_3687_TEMPLATE = """\
{% load static %}<link rel="stylesheet" href="{% static 'demo/app.css' %}">
<div dj-root>
  <p style="color: #9a9a9a; font-family: 'Comic Sans MS'">Note</p>
</div>
"""


def _by_code(report, code):
    return [f for f in report.findings if f.code == code]


def _x105(tmp_path, css):
    return _by_code(_css(tmp_path, css), "X105")


def _x106(tmp_path, css):
    return _by_code(_css(tmp_path, css), "X106")


class TestIssue3687Repro:
    def _report(self, tmp_path):
        _tree(
            tmp_path,
            {
                "demo/static/demo/app.css": ISSUE_3687_CSS,
                "demo/templates/demo/styled.html": ISSUE_3687_TEMPLATE,
            },
        )
        return _run(tmp_path)

    def test_stylesheet_gives_x105_for_lines_2_and_3_and_x106_for_line_2(self, tmp_path):
        report = self._report(tmp_path)
        (x105,) = [f for f in _by_code(report, "X105") if f.path.endswith("app.css")]
        assert x105.lineno == 2
        assert x105.details.startswith("2 color literal(s), first `color: white` at line 2")
        assert "(lines 2, 3)" in x105.details
        (x106,) = [f for f in _by_code(report, "X106") if f.path.endswith("app.css")]
        assert x106.lineno == 2
        assert x106.details.startswith("1 font-family literal(s), first `font-family:")

    def test_template_style_attribute_gives_x105_and_x106(self, tmp_path):
        report = self._report(tmp_path)
        (x105,) = [f for f in _by_code(report, "X105") if f.path.endswith("styled.html")]
        (x106,) = [f for f in _by_code(report, "X106") if f.path.endswith("styled.html")]
        assert (x105.lineno, x106.lineno) == (3, 3)
        assert x105.details.startswith(
            "1 color literal(s) in style attributes, first `color: #9a9a9a`"
        )
        assert x106.details.startswith("1 font-family literal(s) in style attributes")

    def test_transparent_and_var_fallback_stay_clean(self, tmp_path):
        css = ".menu { box-shadow: var(--shadow, 0 1px 2px rgb(0 0 0 / .2)); color: transparent; }"
        assert _x105(tmp_path, css) == []
        assert _x106(tmp_path, css) == []

    def test_only_the_expected_findings(self, tmp_path):
        assert sorted(
            (f.code, os.path.basename(f.path)) for f in self._report(tmp_path).findings
        ) == [
            ("X105", "app.css"),
            ("X105", "styled.html"),
            ("X106", "app.css"),
            ("X106", "styled.html"),
        ]


class TestX105NamedColors:
    @pytest.mark.parametrize("name", ["white", "red", "black", "rebeccapurple", "Tomato", "GREY"])
    def test_named_colour_triggers(self, tmp_path, name):
        assert len(_x105(tmp_path, "a { color: %s; }" % name)) == 1

    @pytest.mark.parametrize(
        "value",
        ["transparent", "currentColor", "currentcolor", "inherit", "initial", "unset", "revert"],
    )
    def test_keywords_are_allowed(self, tmp_path, value):
        for prop in ("color", "background", "border-color", "fill"):
            assert _x105(tmp_path, "a { %s: %s; }" % (prop, value)) == [], (prop, value)

    @pytest.mark.parametrize(
        "decl",
        [
            "border: 1px solid red",
            "outline: 2px dashed navy",
            "box-shadow: 0 0 4px gold",
            "text-shadow: 1px 1px teal",
            "background: linear-gradient(to right, red, blue)",
            "background: url(a.png) no-repeat white",
            "border-bottom-color: crimson",
            "fill: tomato",
            "stroke: black",
            "caret-color: lime",
            "text-decoration: underline wavy red",
            "background: red !important",
            "COLOR: Red",
            "--my-accent: orange",
        ],
    )
    def test_named_colour_in_colour_bearing_properties(self, tmp_path, decl):
        assert len(_x105(tmp_path, "a { %s; }" % decl)) == 1, decl

    @pytest.mark.parametrize(
        "decl",
        [
            "animation-name: orange",
            "grid-area: tan",
            "font-family: Tomato, serif",
            "content: 'red'",
            "transition: color 0.2s, background-color 0.2s",
            "transition-property: color",
            "will-change: color",
            "counter-reset: snow",
            "background: url(red.png)",
            "background: url('/img/white.png')",
            "background: var(--card, white)",
            "color: var(--foreground, black)",
            "color: hsl(var(--foreground))",
            "background-image: none",
            "border: 1px solid var(--border)",
            "border-style: dotted",
            "width: calc(100% - 4px)",
            "margin: 0 auto",
            "background: tan(45deg)",
            "--my-shadow: 0 1px 2px red",
            "--font-stack: red-hat-display",
            "color: redish",
            "color: red-ish",
            "color: x-red",
            "color: 10red",
            "background-position: top left",
        ],
    )
    def test_not_a_named_colour_literal(self, tmp_path, decl):
        assert _x105(tmp_path, "a { %s; }" % decl) == [], decl

    def test_selectors_that_look_like_colours_are_not_declarations(self, tmp_path):
        css = ".red, #white, a:hover.black { color: hsl(var(--foreground)); }"
        assert _x105(tmp_path, css) == []

    def test_theme_token_definition_with_a_named_colour_is_allowed(self, tmp_path):
        assert _x105(tmp_path, ":root { --primary: red; --chart-1: blue; }") == []

    def test_named_colour_counts_alongside_hex_in_one_finding(self, tmp_path):
        (finding,) = _x105(tmp_path, "a { color: white; }\nb { color: #123; }\nc { fill: red; }")
        assert finding.details.startswith("3 color literal(s), first `color: white` at line 1")
        assert "(lines 1, 2, 3)" in finding.details

    def test_named_colour_noqa_suppresses_the_line(self, tmp_path):
        css = "a { color: white; } /* djust: noqa X105 */\nb { color: red; }"
        (finding,) = _x105(tmp_path, css)
        assert finding.lineno == 2
        assert finding.details.startswith("1 color literal(s)")

    def test_css_comment_with_named_colour_ignored(self, tmp_path):
        assert _x105(tmp_path, "/* a { color: white; } */ b { color: var(--foreground); }") == []

    def test_named_colour_inside_nested_media_block(self, tmp_path):
        css = "@media (min-width: 1px) { a { color: white; } }"
        assert len(_x105(tmp_path, css)) == 1

    def test_named_colour_set_is_well_formed(self):
        from djust.audit_ui import _CSS_NAMED_COLORS

        assert {
            "red",
            "white",
            "black",
            "rebeccapurple",
            "lightgoldenrodyellow",
        } <= _CSS_NAMED_COLORS
        assert all(n.isalpha() and n.islower() for n in _CSS_NAMED_COLORS)
        assert not {"transparent", "currentcolor", "inherit", "initial", "unset", "revert"} & (
            _CSS_NAMED_COLORS
        )


class TestX106FontFamily:
    @pytest.mark.parametrize(
        "decl",
        [
            'font-family: "Comic Sans MS", cursive',
            "font-family: Arial",
            "font-family: sans-serif",
            "font-family: system-ui, -apple-system, 'Segoe UI', sans-serif",
            "font-family: var(--brand-font)",
            "font-family: var(--sans, Arial)",
            "font-family: Arial !important",
            "FONT-FAMILY: Georgia, serif",
            "font: 12px/1.5 Arial, sans-serif",
            "font: italic bold 1rem 'Inter'",
            "font: 700 14px Helvetica",
        ],
    )
    def test_literal_family_triggers(self, tmp_path, decl):
        assert len(_x106(tmp_path, "a { %s; }" % decl)) == 1, decl

    @pytest.mark.parametrize(
        "decl",
        [
            "font-family: var(--font-sans)",
            "font-family: var(--font-mono, ui-monospace, monospace)",
            "font-family: var( --font-sans )",
            "font-family: var(--font-display, var(--font-sans))",
            "font-family: var(--font-sans) !important",
            "font-family: inherit",
            "font-family: initial",
            "font-family: unset",
            "font-family: revert",
            "font-family: revert-layer",
            "font: inherit",
            "font: 12px/1.5 var(--font-sans)",
            "font: var(--text-sm) var(--font-sans)",
            "font: caption",
            "font: status-bar",
            "font-size: 12px",
            "font-weight: bold",
            "font-style: italic",
            "font-variant: small-caps",
            "line-height: 1.5",
            "--font-sans: Arial, sans-serif",
            "--font-body: 'Inter', sans-serif",
            "color: hsl(var(--foreground))",
        ],
    )
    def test_not_a_literal_family(self, tmp_path, decl):
        assert _x106(tmp_path, "a { %s; }" % decl) == [], decl

    def test_font_face_declarations_are_the_definition_not_a_use(self, tmp_path):
        css = (
            "@font-face { font-family: 'Inter'; src: url(inter.woff2); font-weight: 400; }\n"
            "@font-face{font-family:Inter;src:url(i.woff2)}\n"
        )
        assert _x106(tmp_path, css) == []

    def test_font_face_exemption_does_not_leak_to_the_next_rule(self, tmp_path):
        css = "@font-face { font-family: Inter; }\na { font-family: Arial; }"
        (finding,) = _x106(tmp_path, css)
        assert finding.lineno == 2

    def test_font_face_inside_a_media_block(self, tmp_path):
        css = "@media print { @font-face { font-family: Inter; } a { font-family: Arial; } }"
        (finding,) = _x106(tmp_path, css)
        assert "Arial" in finding.details

    def test_one_finding_per_stylesheet_with_count_and_lines(self, tmp_path):
        css = "a { font-family: Arial; }\nb { color: hsl(var(--foreground)); }\nc { font: 1rem Georgia; }"
        (finding,) = _x106(tmp_path, css)
        assert finding.lineno == 1
        assert finding.details.startswith("2 font-family literal(s), first `font-family: Arial`")
        assert "(lines 1, 3)" in finding.details
        assert "var(--font-sans)" in finding.details

    def test_x106_is_independent_of_x105(self, tmp_path):
        css = "a { font-family: Arial; }"
        assert _x105(tmp_path, css) == []
        assert len(_x106(tmp_path, css)) == 1
        report = _css(tmp_path, "a { color: #fff; }")
        assert _by_code(report, "X105") and not _by_code(report, "X106")

    def test_noqa_x106_suppresses_only_x106(self, tmp_path):
        css = "a { color: #fff; font-family: Arial; } /* djust: noqa X106 */"
        report = _css(tmp_path, css)
        assert _by_code(report, "X106") == []
        assert len(_by_code(report, "X105")) == 1

    def test_noqa_x105_does_not_suppress_x106(self, tmp_path):
        css = "a { color: #fff; font-family: Arial; } /* djust: noqa X105 */"
        report = _css(tmp_path, css)
        assert _by_code(report, "X105") == []
        assert len(_by_code(report, "X106")) == 1

    def test_bare_noqa_suppresses_both(self, tmp_path):
        report = _css(tmp_path, "a { color: #fff; font-family: Arial; } /* djust: noqa */")
        assert [f.code for f in report.findings if f.code.startswith("X1")] == []

    def test_x106_is_a_warning_in_the_code_table(self):
        assert AST_FINDING_CODES["X106"][0] == "warning"
        assert "font" in AST_FINDING_CODES["X106"][1].lower()


class TestStyleAttributes:
    def _tpl(self, tmp_path, body, name="page.html"):
        _tree(tmp_path, {"app/templates/" + name: "<div dj-root>\n" + body + "\n</div>\n"})
        return _run(tmp_path)

    def _codes_of(self, report):
        return sorted(f.code for f in report.findings if f.code in ("X105", "X106"))

    @pytest.mark.parametrize(
        "style, expected",
        [
            ("color: #9a9a9a", ["X105"]),
            ("color: white", ["X105"]),
            ("background:red;color:#fff", ["X105"]),
            ("font-family: Arial", ["X106"]),
            ("color: white; font-family: Arial", ["X105", "X106"]),
            ("color: hsl(var(--foreground))", []),
            ("color: var(--foreground, #fff)", []),
            ("color: transparent", []),
            ("color: currentColor", []),
            ("font-family: var(--font-sans)", []),
            ("height: 40%", []),
            ("width: 50px; margin: 0 auto", []),
            ("--bar-height: 40%", []),
            ("", []),
        ],
    )
    def test_style_attribute_declarations(self, tmp_path, style, expected):
        report = self._tpl(tmp_path, '<p style="%s">x</p>' % style)
        assert self._codes_of(report) == expected

    def test_single_quoted_style_attribute(self, tmp_path):
        assert self._codes_of(self._tpl(tmp_path, "<p style='color: red'>x</p>")) == ["X105"]

    def test_template_expressions_are_masked(self, tmp_path):
        for body in (
            '<i style="color: {{ series.color }}">x</i>',
            '<i style="background: {{ c|default:"#fff" }}">x</i>',
            '<i style="font-family: {{ font }}">x</i>',
            '<i style="height: {{ pct }}%">x</i>',
            '<i style="color: rgb({{ r }}, 0, 0)">x</i>',
            '<i style="{% if hot %}height: 70%{% endif %}">x</i>',
        ):
            assert self._codes_of(self._tpl(tmp_path, body)) == [], body

    def test_literal_next_to_an_expression_still_counts(self, tmp_path):
        body = '<i style="color: {{ c }}; background: #fff">x</i>'
        assert self._codes_of(self._tpl(tmp_path, body)) == ["X105"]

    def test_finding_text_shows_the_expression_not_the_stand_in(self, tmp_path):
        body = '<i style="background: {{ c }} white">x</i>'
        (finding,) = _by_code(self._tpl(tmp_path, body), "X105")
        assert "`background: {{ … }} white`" in finding.details
        assert "djust-template" not in finding.details

    def test_literal_in_a_template_branch_counts(self, tmp_path):
        body = '<i style="color: {% if hot %}red{% else %}blue{% endif %}">x</i>'
        assert self._codes_of(self._tpl(tmp_path, body)) == ["X105"]

    def test_data_style_attribute_is_not_a_style_attribute(self, tmp_path):
        assert self._codes_of(self._tpl(tmp_path, '<p data-style="color: red">x</p>')) == []

    def test_one_finding_per_template_with_count_and_lines(self, tmp_path):
        body = (
            '<p style="color: red">a</p>\n'
            '<p style="height: 3px">b</p>\n'
            '<p style="background: #fff; border: 1px solid navy">c</p>'
        )
        report = self._tpl(tmp_path, body)
        (finding,) = _by_code(report, "X105")
        assert finding.lineno == 2
        assert finding.details.startswith(
            "3 color literal(s) in style attributes, first `color: red`"
        )
        assert "(lines 2, 4, 4)" in finding.details  # one entry per offending declaration

    def test_multiline_style_attribute_reports_the_declaration_line(self, tmp_path):
        body = '<p style="\n  height: 3px;\n  color: red;\n">x</p>'
        (finding,) = _by_code(self._tpl(tmp_path, body), "X105")
        assert finding.lineno == 4

    def test_noqa_on_the_same_line_suppresses(self, tmp_path):
        body = '<p style="color: red">a</p> {# djust: noqa X105 #}\n<p style="color: blue">b</p>'
        (finding,) = _by_code(self._tpl(tmp_path, body), "X105")
        assert finding.lineno == 3
        assert finding.details.startswith("1 color literal(s)")

    def test_noqa_x105_leaves_x106_and_vice_versa(self, tmp_path):
        body = '<p style="color: red; font-family: Arial">a</p> {# djust: noqa X105 #}'
        assert self._codes_of(self._tpl(tmp_path, body)) == ["X106"]
        body = '<p style="color: red; font-family: Arial">a</p> {# djust: noqa X106 #}'
        assert self._codes_of(self._tpl(tmp_path, body)) == ["X105"]

    def test_bare_noqa_suppresses_both(self, tmp_path):
        body = '<p style="color: red; font-family: Arial">a</p> {# djust: noqa #}'
        assert self._codes_of(self._tpl(tmp_path, body)) == []

    def test_style_in_a_commented_out_tag_is_ignored(self, tmp_path):
        body = '{# <p style="color: red">x</p> #}<!-- <p style="color: blue">y</p> -->'
        assert self._codes_of(self._tpl(tmp_path, body)) == []

    def test_non_live_template_is_not_scanned(self, tmp_path):
        _tree(tmp_path, {"app/templates/plain.html": '<p style="color: red">x</p>'})
        assert self._codes_of(_run(tmp_path)) == []

    def test_included_and_extended_templates_are_scanned(self, tmp_path):
        _tree(tmp_path, {"app/templates/partial.html": '<p style="color: red">x</p>'})
        report = self._tpl(tmp_path, '{% include "partial.html" %}')
        assert [os.path.basename(f.path) for f in _by_code(report, "X105")] == ["partial.html"]

    def test_x103_overlay_detection_is_unchanged_by_style_scan(self, tmp_path):
        body = '<div style="position:fixed;top:0;bottom:0;left:0;right:0;background:red"></div>'
        assert sorted(f.code for f in self._tpl(tmp_path, body).findings) == ["X103", "X105"]

    def test_include_ui_false_skips_style_findings(self, tmp_path):
        _tree(
            tmp_path, {"app/templates/page.html": '<div dj-root><p style="color: red">x</p></div>'}
        )
        assert self._codes_of(_run(tmp_path, include_ui=False)) == []


class TestX106Registration:
    def test_summary_cache_lists_x106(self, tmp_path):
        _tree(
            tmp_path,
            {"app/templates/page.html": '<div dj-root><p style="font-family: Arial">x</p></div>'},
        )
        _command(tmp_path)
        payload = json.loads((tmp_path / CACHE).read_text())
        assert payload["rules"][-2:] == ["X105", "X106"]
        assert payload["counts"]["X106"] == 1
        assert payload["counts"]["X105"] == 0
        assert payload["total"] == 1

    def test_x106_alone_does_not_trigger_the_component_hint(self):
        from djust.checks.ai_discovery import _UI_REPLACEMENTS

        assert "X106" not in _UI_REPLACEMENTS and "X105" not in _UI_REPLACEMENTS

    def test_x106_details_name_only_registered_theme_tokens(self):
        from djust import audit_ui

        for token in re.findall(r"var\((--[\w-]+)\)", audit_ui._X106_DETAILS):
            assert token in {"--font-sans", "--font-mono"}

    def test_command_pretty_output_reports_x106(self, tmp_path):
        _tree(
            tmp_path,
            {"app/templates/page.html": '<div dj-root><p style="font-family: Arial">x</p></div>'},
        )
        assert "[djust.X106]" in _command(tmp_path)


class TestReviewFixes3687:
    """Findings from the PR #3702 review."""

    def test_many_style_attributes_on_one_tag_scan_in_linear_time(self):
        import time

        from djust.audit_ui import scan_ui

        # `attrs.count("\n", 0, ...)` per attribute made this quadratic (21 s at 80k).
        source = "<div dj-root " + 'style="a" ' * 40000 + ">x</div>"
        start = time.process_time()
        scan_ui(".", {"app/templates/page.html": source}, {"page.html"})
        assert time.process_time() - start < 2.0

    def test_style_attributes_on_different_lines_of_one_tag_report_their_own_lines(self, tmp_path):
        _tree(
            tmp_path,
            {
                "app/templates/page.html": '<div dj-root\n  style="color: red"\n  data-x="1"\n'
                '  style="background: blue">x</div>\n'
            },
        )
        (finding,) = _by_code(_run(tmp_path), "X105")
        assert finding.lineno == 2
        assert "(lines 2, 4)" in finding.details

    @pytest.mark.parametrize(
        "decl",
        [
            "font-family: var(--font-sans), sans-serif",
            "font-family: var(--font-sans),system-ui,sans-serif",
            "font-family: var(--font-mono), ui-monospace, monospace",
            "font-family: sans-serif, var(--font-sans)",
            "font-family: var(--font-sans) , SERIF !important",
        ],
    )
    def test_theme_token_with_generic_fallback_is_clean(self, tmp_path, decl):
        assert _x106(tmp_path, "a { %s; }" % decl) == [], decl

    @pytest.mark.parametrize(
        "decl",
        [
            'font-family: var(--font-sans), "Inter"',
            "font-family: var(--font-sans), Arial, sans-serif",
            "font-family: sans-serif",
            "font-family: system-ui, serif",
            "font-family: var(--my-font), sans-serif",
        ],
    )
    def test_named_family_or_missing_token_is_still_flagged(self, tmp_path, decl):
        assert len(_x106(tmp_path, "a { %s; }" % decl)) == 1, decl

    def test_template_expression_with_generic_fallback_is_clean(self, tmp_path):
        _tree(
            tmp_path,
            {
                "app/templates/p.html": '<div dj-root><i style="font-family: {{ f }}, sans-serif">x</i></div>'
            },
        )
        assert _by_code(_run(tmp_path), "X106") == []

    @pytest.mark.parametrize(
        "attr",
        [
            ":style=\"{ color: 'red' }\"",
            "x-bind:style=\"{ color: 'red' }\"",
            '@style="color: red"',
        ],
    )
    def test_framework_bound_style_expressions_are_not_css(self, tmp_path, attr):
        _tree(tmp_path, {"app/templates/p.html": "<div dj-root><i %s>x</i></div>" % attr})
        assert [f for f in _run(tmp_path).findings if f.code in ("X105", "X106")] == []

    def test_font_palette_values_family_is_a_definition(self, tmp_path):
        css = "@font-palette-values --brand { font-family: Bixa; override-colors: 0 #fff; }"
        assert _x106(tmp_path, css) == []

    def test_text_emphasis_named_colour_is_flagged(self, tmp_path):
        assert len(_x105(tmp_path, "a { text-emphasis: filled red; }")) == 1
