"""v1.2.1-9 — Rust template renderer gaps.

- #2890: ``{% verbatim %}`` inside a ``{% block %}`` of an extended template
  broke the LiveView full-template render with a spurious "Invalid block tag":
  inheritance flattening re-emitted the verbatim body (``{%``) as bare source.
- #2958: ``{% dj_activity %}``, ``{% colocated_hook %}``, ``{% live_form %}``,
  ``{% live_field %}`` and ``{% live_errors %}`` had no Rust handler, so a root
  LiveView template using them failed with "Invalid block tag". They now bridge
  through Django's own nodes; the output is compared with the Django engine's.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from django import forms
from django.template import engines
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.forms import FormMixin
from djust.utils import clear_template_dirs_cache


class _TemplateDir:
    def __init__(self, files: dict):
        self._tmpdir = Path(tempfile.mkdtemp())
        for name, src in files.items():
            (self._tmpdir / name).write_text(src)
        self._override = None

    def __enter__(self):
        from django.conf import settings

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(TEMPLATES=templates)
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        if self._override is not None:
            self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


def _get(view_cls) -> str:
    from django.contrib.sessions.backends.db import SessionStore

    request = RequestFactory().get("/v/")
    request.session = SessionStore()
    request.session.create()
    response = view_cls.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    assert response.status_code == 200, response.content[:500]
    return response.content.decode()


# ─────────────────────────────────────────────────────────────────────────────
# #2890 — verbatim inside an extended block
# ─────────────────────────────────────────────────────────────────────────────

_BASE_2890 = (
    "<html><body><div dj-root>{% block content %}{% endblock %}\n"
    "{% if flag %}<p>tail</p>{% else %}<p>no</p>{% endif %}</div></body></html>"
)
_CHILD_2890 = (
    '{% extends "base_2890.html" %}\n{% block content %}\n'
    "<pre>{% verbatim %}{%{% endverbatim %} load theme_components "
    "{% verbatim %}%}{% endverbatim %}</pre>\n"
    "{% if flag %}<p>yes</p>{% elif n %}<p>n</p>{% else %}<p>no</p>{% endif %}\n"
    '<pre>{% verbatim %}{% theme_card title="x" %}{{ raw }}{# c #}{% endverbatim %}</pre>\n'
    "<pre>{% templatetag openblock %} x {% templatetag closeblock %}</pre>\n"
    "<span>{{ n }}</span>\n{% endblock %}"
)


def _pre_blocks(html: str) -> list:
    import re

    return re.findall(r"<pre[^>]*>(.*?)</pre>", html, re.S)


@pytest.mark.django_db
class TestVerbatimInExtendedBlock2890:
    def _view(self):
        return type(
            "Verbatim2890",
            (LiveView,),
            {
                "template_name": "child_2890.html",
                "__module__": __name__,
                "mount": lambda self, request, **kw: (
                    setattr(self, "flag", True),
                    setattr(self, "n", 1),
                ),
            },
        )

    def test_liveview_get_renders_and_matches_django(self):
        with _TemplateDir({"base_2890.html": _BASE_2890, "child_2890.html": _CHILD_2890}):
            html = _get(self._view())
            django_html = (
                engines["django"].get_template("child_2890.html").render({"flag": True, "n": 1})
            )
        assert _pre_blocks(html) == _pre_blocks(django_html)
        assert _pre_blocks(html)[1] == '{% theme_card title="x" %}{{ raw }}{# c #}'
        assert "<p>yes</p>" in html and "<p>tail</p>" in html

    def test_ws_render_path_renders_the_same(self):
        with _TemplateDir({"base_2890.html": _BASE_2890, "child_2890.html": _CHILD_2890}):
            v = self._view()()
            v.mount(None)
            html, _patches, _version = v.render_with_diff(None)
            django_html = (
                engines["django"].get_template("child_2890.html").render({"flag": True, "n": 1})
            )
        assert _pre_blocks(html) == _pre_blocks(django_html)


# ─────────────────────────────────────────────────────────────────────────────
# #2958 — live_tags without a native handler bridge through Django's nodes
# ─────────────────────────────────────────────────────────────────────────────


class _NameForm(forms.Form):
    name = forms.CharField(max_length=10)


def _rust_and_django(body: str, context: dict):
    from djust._rust import RustLiveView

    src = "{% load live_tags %}<div dj-root>" + body + "</div>"
    rv = RustLiveView(src)
    rv.update_state(context)
    rust = rv.render()
    django = engines["django"].from_string(src).render(context)
    return rust, django


class _FormView(FormMixin, LiveView):
    form_class = _NameForm
    template = "<div dj-root></div>"


@pytest.mark.parametrize(
    "body",
    [
        '{% dj_activity "panel" visible=show %}<p>{{ n }}</p>{% enddj_activity %}',
        '{% dj_activity "panel" visible=True eager=True %}<b>x</b>{% enddj_activity %}',
        '{% colocated_hook "Chart" %}hook.mounted = () => { return {{ n }}; };{% endcolocated_hook %}',
        '{% colocated_hook "Chart" global %}a</script>b{% endcolocated_hook %}',
    ],
)
def test_block_tags_render_like_django(body):
    rust, django = _rust_and_django(body, {"show": False, "n": 3})
    assert rust == django


@pytest.mark.django_db
@pytest.mark.parametrize(
    "body",
    [
        "{% live_form view %}",
        '{% live_field view "name" %}',
        '{% live_errors view "name" %}',
        "{% live_errors view %}",
    ],
)
def test_form_tags_render_like_django(body):
    view = _FormView()
    view.mount(None)
    view.submit_form(name="far too long a name")  # populate field errors
    rust, django = _rust_and_django(body, {"view": view})
    assert rust == django


def test_dj_activity_registers_on_the_view_during_a_rust_render():
    """The Rust context cannot carry the view; the node falls back to the
    active-parent-view thread-local, as ``{% live_render %}`` does."""

    class V(LiveView):
        template = (
            "{% load live_tags %}<div dj-root>"
            '{% dj_activity "panel" visible=show %}<p>{{ n }}</p>{% enddj_activity %}</div>'
        )

        def mount(self, request, **kwargs):
            self.show = False
            self.n = 1

    v = V()
    v.mount(None)
    html, _patches, _version = v.render_with_diff(None)
    assert 'data-djust-activity="panel"' in html and "hidden" in html
    assert v._djust_activities == {"panel": {"visible": False, "eager": False}}


def test_only_the_named_live_tags_are_bridged():
    """The rest of djust's own library keeps its native handlers."""
    from django.template.library import import_library

    from djust import template_libraries as tl

    allowed = tl._DJUST_TAGS_BRIDGED["djust.templatetags.live_tags"]
    assert allowed == frozenset(
        {
            "dj_activity",
            "colocated_hook",
            "live_form",
            "live_field",
            "live_errors",
            # #3044
            "live_input",
            "djust_skeleton",
            "djust_track_static",
        }
    )
    library = import_library("djust.templatetags.live_tags")
    subset = tl._djust_subset("djust.templatetags.live_tags", library, allowed)
    assert set(subset.tags) == allowed and subset.filters == {}
    assert "live_render" in library.tags and "live_render" not in subset.tags
    # The same subset object comes back, so a repeated {% load %} is a no-op.
    assert tl._djust_subset("djust.templatetags.live_tags", library, allowed) is subset


@pytest.mark.django_db
def test_form_tags_and_filters_work_in_a_real_root_liveview():
    """Code Review on #3042: a root LiveView's Rust context has no ``view``,
    so the tags and filters fall back to the view being rendered."""

    class V(FormMixin, LiveView):
        form_class = _NameForm
        template = (
            "{% load live_tags %}<div dj-root>"
            "<form>{% live_form view %}</form>"
            '<i>{% live_field view "name" %}</i>'
            '<b>{% live_errors view "name" %}</b>'
            '<u>[{{ view|field_value:"name" }}]'
            '{% if view|has_errors:"name" %}bad{% endif %}</u>'
            "</div>"
        )

    import re

    v = V()
    v.mount(None)
    v.submit_form(name="far too long a name")
    html, _patches, _version = v.render_with_diff(None)
    assert "ERROR: View does not have" not in html
    assert "[far too long a name]" in html
    assert "bad</u>" in html
    assert "at most 10 characters" in html  # live_errors rendered the error

    # What the Django engine renders for the same view, as the page shows it
    # (entity spelling differs between the VDOM serialiser and Django's
    # ``escape``, so compare the decoded text). Since #3043 the form tags'
    # markup renders as markup on both engines (test_live_form_tags_safe_3043).
    import html as _html

    django_html = engines["django"].from_string(V.template).render({"view": v})

    def section(markup: str, tag: str) -> str:
        inner = re.search(r"<%s[^>]*>(.*?)</%s>" % (tag, tag), markup, re.S).group(1)
        return _html.unescape(re.sub(r"<[^>]+>", "", inner))

    for tag in ("form", "i", "b", "u"):
        assert section(html, tag) == section(django_html, tag), tag
