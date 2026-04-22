"""Unit tests for the {% djust_track_static %} template tag (v0.6.0)."""

from django.template import Context, Template


def _render(tmpl_source, context=None):
    full = "{% load live_tags %}" + tmpl_source
    return Template(full).render(Context(context or {}))


class TestDjustTrackStatic:
    def test_emits_bare_attribute_marker(self):
        out = _render("{% djust_track_static %}")
        assert out == "dj-track-static"

    def test_renders_inside_script_tag_as_attribute(self):
        out = _render('<script {% djust_track_static %} src="/static/app.abc.js"></script>')
        assert out == '<script dj-track-static src="/static/app.abc.js"></script>'

    def test_renders_inside_link_tag(self):
        out = _render('<link {% djust_track_static %} rel="stylesheet" href="/static/x.css">')
        assert "dj-track-static" in out
        assert 'rel="stylesheet"' in out

    def test_marker_is_not_html_escaped(self):
        """The tag returns mark_safe — the hyphen in `dj-track-static`
        must not get entity-encoded."""
        out = _render("{% djust_track_static %}")
        assert "&" not in out
