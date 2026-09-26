"""A page with no ``dj-root`` that embeds a ``{% live_render %}`` child (#3155).

With no ``dj-root`` in the shell, the page-shell render fell back to the FIRST
element carrying ``dj-view``. On a page that embeds a child, that element is
the child's ``<div dj-view data-djust-embedded=…>`` wrapper, so the whole page
(``<!DOCTYPE>``, sidebar, heading) was spliced into the child's slot: two
documents in one response. This is the generic form of the djust admin's
#3142, which #3153 fixed for the admin only by giving its pages a ``dj-root``.

The root locator now skips an embedded child's wrapper and everything inside
it, so the child is never mistaken for the page's root.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from django.test import override_settings

from djust import LiveView
from djust.mixins.template import _DJ_ROOT_RE, _DJ_VIEW_RE, _search_dj_root_open
from djust.utils import clear_template_dirs_cache

_MODULE = "djust.tests.test_embedded_child_root_fallback_3155"


class Widget3155(LiveView):
    template = '<p class="widget-body">Change summary</p>'

    def mount(self, request, **kwargs):
        pass


class RootedWidget3155(LiveView):
    """A child whose own template declares a ``dj-root``."""

    template = '<div dj-root><p class="widget-body">Rooted child</p></div>'

    def mount(self, request, **kwargs):
        pass


# The #3142 admin shape: a full page, chrome and content, and NO dj-root.
_PAGE = """<!DOCTYPE html>
<html>
<head><title>Admin</title></head>
<body>
  <aside class="sidebar">Sidebar</aside>
  <main>
    <h1>Change request</h1>
    <section class="card">
      {%% load live_tags %%}{%% live_render "%s.%s" %%}
    </section>
  </main>
</body>
</html>
"""


class _Harness:
    def __init__(self, source: str):
        self._tmpdir = Path(tempfile.mkdtemp())
        (self._tmpdir / "page_3155.html").write_text(source)

    def __enter__(self):
        from django.conf import settings

        templates = [dict(t) for t in settings.TEMPLATES]
        templates[0] = dict(templates[0])
        templates[0]["DIRS"] = [str(self._tmpdir), *templates[0].get("DIRS", [])]
        self._override = override_settings(
            TEMPLATES=templates, DJUST_LIVE_RENDER_ALLOWED_MODULES=[_MODULE]
        )
        self._override.enable()
        clear_template_dirs_cache()
        return self

    def __exit__(self, *exc):
        self._override.disable()
        clear_template_dirs_cache()
        shutil.rmtree(self._tmpdir, ignore_errors=True)
        return False


def _render_page(rf, child: str) -> str:
    view_cls = type(
        "Page3155",
        (LiveView,),
        {"template_name": "page_3155.html", "mount": lambda self, request, **kw: None},
    )
    with _Harness(_PAGE % (_MODULE, child)):
        request = rf.get("/admin/change/")
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
        view = view_cls()
        view.request = request
        view.mount(request)
        view.get_template()  # sets _full_template, as the GET path does
        return view.render_full_template(request)


@pytest.mark.parametrize("child", ["Widget3155", "RootedWidget3155"])
def test_page_without_dj_root_renders_one_document(rf, child):
    html = _render_page(rf, child)
    assert html.upper().count("<!DOCTYPE") == 1, html
    assert html.count('<aside class="sidebar">') == 1, html
    assert html.count("<h1>") == 1, html
    # The child's wrapper still holds only the child's markup.
    start = html.index("data-djust-embedded=")
    assert "<aside" not in html[start:], html
    assert "widget-body" in html[start:]


class TestLocatorSkipsEmbeddedChildren:
    def test_embedded_wrapper_is_not_the_root(self):
        html = '<main><div dj-view data-djust-embedded="c1"><p>x</p></div></main>'
        assert _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE) is None

    def test_a_dj_root_inside_an_embedded_child_is_not_the_root(self):
        html = (
            '<div dj-view data-djust-embedded="c1"><div dj-root><p>x</p></div></div>'
            '<section dj-view="app.Page"><p>page</p></section>'
        )
        m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
        assert m is not None
        assert html[m.start() : m.end()] == '<section dj-view="app.Page">'

    def test_sticky_wrapper_is_skipped(self):
        html = (
            '<div dj-view dj-sticky-view="p" dj-sticky-root data-djust-embedded="c1">'
            "<div><p>x</p></div></div>"
            "<div dj-root><p>page</p></div>"
        )
        m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
        assert html[m.start() : m.end()] == "<div dj-root>"

    def test_page_root_holding_an_embedded_child_is_still_found(self):
        html = (
            '<div dj-root dj-view="app.Page">'
            '<div dj-view data-djust-embedded="c1"><p>x</p></div></div>'
        )
        m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
        assert m.start() == 0

    def test_embedded_marker_as_attribute_text_is_not_a_wrapper(self):
        html = '<div dj-view title="data-djust-embedded=x"><p>x</p></div>'
        m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
        assert m is not None and m.start() == 0


class TestStampSkipsEmbeddedChildren:
    """``_stamp_dj_view`` (the GET's view-path stamp) is the parallel path:
    it must not stamp the PAGE's view path onto a child's own ``dj-root``."""

    def test_child_dj_root_is_not_stamped(self):
        html = (
            '<div dj-root><div dj-view data-djust-embedded="c1">'
            "<div dj-root><p>x</p></div></div></div>"
        )
        out = LiveView._stamp_dj_view(html, "app.Page")
        assert out.count('dj-view="app.Page"') == 1
        assert out.startswith('<div dj-root dj-view="app.Page">')
