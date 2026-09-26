"""#3187 — a LiveView page that ``{% extends %}`` a base template must render ONE
HTML document through the real GET path, not a second full document nested
inside the base's ``<main>``.

Shape from the issue (docs.djust.org/search/): a base template with a masthead
and a ``<main>`` holding ``{% block content %}``; the child extends it and puts
``<div dj-root ...>`` inside that block; the backend is the Rust
``DjustTemplateBackend``.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, override_settings

from djust import LiveView
from djust.utils import clear_template_dirs_cache

_BASE = """{% load live_tags %}<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{% block title %}Docs{% endblock %} · docs</title>
  {% comment %}
  Emits the client config. Required on any page containing a dj-root region.
  {% endcomment %}
  {% djust_client_config %}
</head>
<body>
  <header class="masthead"><a href="/">docs</a></header>
  <div class="layout">
    <nav class="sidebar">{{ sidebar_marker }}</nav>
    <main id="content" class="content">
      {% block content %}{% endblock %}
      {% if raw_url %}<footer class="page-footer"><a href="{{ raw_url }}">md</a></footer>{% endif %}
    </main>
  </div>
</body>
</html>
"""

_CHILD_TEMPLATE = """{% extends "docs_3187/base.html" %}

{% block title %}Search{% endblock %}

{% comment %}
Everything inside `dj-root` is patched from the server.

`dj-view` is written out because djust 1.2 fills it in only on a bare
`<div dj-root>` (a literal string match in RequestMixin.get).
{% endcomment %}
{% block content %}
<div dj-root__VIEW_ATTR__ class="search">
  <h1>Search</h1>
  <input type="search" name="q" value="{{ query }}" dj-input="update_query">
  <p class="count">{{ query|length }}</p>
</div>
{% endblock %}
"""


def _child(explicit_view: bool) -> str:
    attr = ' dj-view="djust.tests.test_extends_double_document_3187.SearchView3187"'
    return _CHILD_TEMPLATE.replace("__VIEW_ATTR__", attr if explicit_view else "")


class SearchView3187(LiveView):
    template_name = "docs_3187/search.html"

    def mount(self, request, **kwargs):
        self.query = "hello"

    def get_context_data(self, **kwargs):
        return {"query": self.query}


def _context_processor(request):
    return {"sidebar_marker": "SIDEBAR-FROM-CP", "raw_url": "/search.md"}


@pytest.fixture
def docs_templates():
    def _install(explicit_view: bool):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "docs_3187").mkdir()
        (tmp / "docs_3187" / "base.html").write_text(_BASE)
        (tmp / "docs_3187" / "search.html").write_text(_child(explicit_view))
        created.append(tmp)
        templates = [
            {
                "BACKEND": "djust.template_backend.DjustTemplateBackend",
                "DIRS": [str(tmp)],
                "APP_DIRS": True,
                "OPTIONS": {
                    "context_processors": [
                        "django.template.context_processors.request",
                        "djust.tests.test_extends_double_document_3187._context_processor",
                    ]
                },
            }
        ]
        ov = override_settings(TEMPLATES=templates)
        ov.enable()
        overrides.append(ov)
        clear_template_dirs_cache()

    created: list = []
    overrides: list = []
    yield _install
    for ov in reversed(overrides):
        ov.disable()
    clear_template_dirs_cache()
    for tmp in created:
        shutil.rmtree(tmp, ignore_errors=True)


def _get(path: str = "/search/") -> str:
    request = RequestFactory().get(path)
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    from django.contrib.auth.models import AnonymousUser

    request.user = AnonymousUser()
    response = SearchView3187.as_view()(request)
    if hasattr(response, "render"):
        response.render()
    return response.content.decode("utf-8")


@pytest.mark.django_db
@pytest.mark.parametrize("explicit_view", [True, False], ids=["dj-root+dj-view", "bare-dj-root"])
def test_extends_page_renders_one_document(docs_templates, explicit_view):
    docs_templates(explicit_view)
    html = _get()
    assert html.lower().count("<!doctype") == 1, html
    assert html.count('class="masthead"') == 1, html
    assert html.count("<main") == 1, html
    assert html.count("dj-root") == 1, html
    # The base's context-processor data renders once, and the dj-root region
    # sits inside the base's <main>.
    assert html.count("SIDEBAR-FROM-CP") == 1, html
    main_inner = html.split("<main", 1)[1].split("</main>", 1)[0]
    assert 'class="search"' in main_inner
    assert 'value="hello"' in main_inner


# ---------------------------------------------------------------------------
# The cause: a ``<div dj-root>`` inside a Django template comment was taken as
# the root when locating it in template SOURCE. Pinned at the extractor.
# ---------------------------------------------------------------------------

_REAL_ROOT = '<div dj-root class="r"><p>{{ x }}</p></div>'


@pytest.mark.parametrize(
    "comment",
    [
        "{% comment %}fills it in only on a bare `<div dj-root>`.{% endcomment %}",
        "{% comment 'note' %}\n<div dj-root>\n</div>\n{% endcomment %}",
        "{# a bare <div dj-root> #}",
        "{# <div dj-view='x.Y'> #}",
    ],
)
def test_template_comment_root_is_not_the_root(comment):
    view = SearchView3187()
    src = "<body>" + comment + "\n" + _REAL_ROOT + "</body>"
    assert view._extract_liveview_root_with_wrapper(src) == _REAL_ROOT
    assert view._extract_liveview_template_content(src) == "<p>{{ x }}</p>"


def test_comment_inside_the_root_is_kept_and_balanced():
    """A comment INSIDE the real root stays in the VDOM template (it renders
    nothing), and a ``</div>`` inside it does not close the root early."""
    view = SearchView3187()
    root = "<div dj-root><p>a</p>{% comment %}</div> stray{% endcomment %}<p>b</p></div>"
    assert view._extract_liveview_root_with_wrapper("<main>" + root + "</main>") == root


def test_rendered_html_search_is_unchanged():
    """Literal ``{# ... #}`` text in RENDERED HTML (a page showing template
    syntax) is content: the rendered-HTML root search does not mask it."""
    from djust.mixins.template import _DJ_ROOT_RE, _DJ_VIEW_RE, _search_dj_root_open

    html = "<p>{# </p><div dj-root>x</div><p> #}</p>"
    m = _search_dj_root_open(html, _DJ_ROOT_RE, _DJ_VIEW_RE)
    assert m is not None and html[m.start() :].startswith("<div dj-root>")
