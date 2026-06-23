"""Tests for djust system check T017 (#1837).

T017 (WARNING) flags ``dj-view`` / ``dj-root`` placed on an HTML
table-section element (``<tbody>``, ``<thead>``, ``<tfoot>``, ``<tr>``,
``<td>``, ``<th>``, ``<caption>``, ``<col>``, ``<colgroup>``). Such a view
renders to silent garbage: html5ever foster-parents the table elements out of
the tree at render time, so ``<tbody dj-view="...">{% for %}<tr>...{% endfor %}
</tbody>`` renders as ``<html><head></head><body>text</body></html>`` (all rows
dropped) with NO error.

Empirical canary (#1459): the "fires" cases assert the check actually catches
the bug class it claims to catch; the "does NOT fire" case pins the
no-false-positive contract; the suppression cases pin the opt-out.
"""

from django.test import override_settings

from djust.checks import _DJ_TABLE_SECTION_ROOT_RE


class TestT017TableSectionRegex:
    """Regex unit cases (mirrors the TestS007ClientNameSafeRegex style)."""

    def test_matches_tbody_dj_view(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<tbody dj-view="x.V">') is not None

    def test_matches_tr_dj_root(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search("<tr dj-root>") is not None

    def test_matches_all_table_section_tags(self):
        for tag in (
            "tbody",
            "thead",
            "tfoot",
            "tr",
            "td",
            "th",
            "caption",
            "col",
            "colgroup",
        ):
            html = '<%s dj-view="x.V">' % tag
            assert _DJ_TABLE_SECTION_ROOT_RE.search(html) is not None, tag

    def test_tolerates_attribute_order(self):
        """dj-root after other attributes still matches."""
        assert (
            _DJ_TABLE_SECTION_ROOT_RE.search('<thead class="h" id="x" dj-root="x.V">') is not None
        )

    def test_tolerates_extra_whitespace(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<tbody    dj-view="x.V"  >') is not None

    def test_case_insensitive(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<TBODY DJ-VIEW="x.V">') is not None

    def test_word_boundary_rejects_trx(self):
        """`<trx` must NOT match `tr` (word-boundary on tag name)."""
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<trx dj-view="x.V">') is None

    def test_word_boundary_rejects_tablefoo(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search("<tablefoo dj-view>") is None

    def test_table_itself_not_flagged(self):
        """`<table dj-view>` is the recommended wrap target — never flagged."""
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<table dj-view="x.V">') is None

    def test_div_wrapping_table_not_flagged(self):
        """`dj-view` on a <div> wrapping a <table><tbody> must NOT match —
        the attribute is on the wrapper, not on any table-section tag."""
        html = (
            '<div dj-view="x.V"><table><tbody>'
            "{% for r in rows %}<tr><td>{{ r }}</td></tr>{% endfor %}"
            "</tbody></table></div>"
        )
        assert _DJ_TABLE_SECTION_ROOT_RE.search(html) is None

    def test_plain_table_section_without_attr_not_flagged(self):
        assert _DJ_TABLE_SECTION_ROOT_RE.search('<tbody class="x">') is None


def _scan(tmp_path, settings, body, fname="t.html"):
    """Drive the real ``check_templates`` against a temp template dir."""
    tpl_dir = tmp_path / "templates"
    tpl_dir.mkdir()
    (tpl_dir / fname).write_text(body)
    settings.TEMPLATES = [
        {
            "DIRS": [str(tpl_dir)],
            "BACKEND": "django.template.backends.django.DjangoTemplates",
            "APP_DIRS": False,
        }
    ]
    from djust.checks import check_templates

    errors = check_templates(None)
    return [e for e in errors if e.id == "djust.T017"]


class TestT017CheckIntegration:
    """Integration tests for T017 via the actual check function (#1837)."""

    def test_fires_on_tbody_dj_view(self, tmp_path, settings):
        """T017 fires for a <tbody dj-view="..."> root template."""
        t017 = _scan(
            tmp_path,
            settings,
            '<tbody dj-view="x.V">{% for r in rows %}<tr><td>{{ r }}</td></tr>{% endfor %}</tbody>',
        )
        assert len(t017) == 1
        assert "table-section" in t017[0].msg
        assert "tbody" in t017[0].msg
        assert "wrapping element" in t017[0].hint

    def test_fires_on_tr_dj_root(self, tmp_path, settings):
        """T017 fires for a <tr dj-root> root template (second variant)."""
        t017 = _scan(tmp_path, settings, "<tr dj-root><td>cell</td></tr>")
        assert len(t017) == 1
        assert "tr" in t017[0].msg

    def test_does_not_fire_on_div_wrapping_table(self, tmp_path, settings):
        """No false positive: dj-view on a <div> wrapping a <table><tbody>."""
        t017 = _scan(
            tmp_path,
            settings,
            '<div dj-view="x.V"><table><tbody>'
            "{% for r in rows %}<tr><td>{{ r }}</td></tr>{% endfor %}"
            "</tbody></table></div>",
        )
        assert t017 == []

    def test_does_not_fire_on_table_dj_view(self, tmp_path, settings):
        """No false positive: dj-view directly on the <table> element."""
        t017 = _scan(
            tmp_path,
            settings,
            '<table dj-view="x.V"><tbody><tr><td>x</td></tr></tbody></table>',
        )
        assert t017 == []

    @override_settings(DJUST_CONFIG={"suppress_checks": ["T017"]})
    def test_suppressed_short_id(self, tmp_path, settings):
        """T017 is suppressible via the short id form."""
        t017 = _scan(tmp_path, settings, '<tbody dj-view="x.V"><tr><td>x</td></tr></tbody>')
        assert t017 == []

    @override_settings(DJUST_CONFIG={"suppress_checks": ["djust.T017"]})
    def test_suppressed_qualified_id(self, tmp_path, settings):
        """T017 is suppressible via the fully-qualified id form."""
        t017 = _scan(tmp_path, settings, '<tbody dj-view="x.V"><tr><td>x</td></tr></tbody>')
        assert t017 == []
